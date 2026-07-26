#!/usr/bin/env python3
"""Benchmark the duplicate-detection stage of a data-quality scan.

    # staging, default ladder (1000 / 5000 / 10000 / 29551)
    TEST_DATABASE_URL=postgresql://…/olimlar_staging_db \
        python3 scripts/benchmark_duplicate_detection.py

    # one size, and write the table to a file
    python3 scripts/benchmark_duplicate_detection.py --limits 10000 \
        --out docs/benchmarks/duplicates.md

    # how much each optimisation contributes (keep `legacy` to small limits —
    # it is the pre-hotfix algorithm and is quadratic inside every block)
    python3 scripts/benchmark_duplicate_detection.py --limits 1000,5000 \
        --variants full,no-rapidfuzz,legacy

Variants
    full          current detector: cheap bounds + rapidfuzz pre-filter.
    no-rapidfuzz  cheap length/token bounds only (what a host without the
                  rapidfuzz wheel gets).
    legacy        the pre-hotfix algorithm — full pairwise SequenceMatcher
                  inside every block, `seen` populated only on a hit, 8-char
                  title prefix, oversized blocks skipped. For reference only.

Safety
    * READ-ONLY. Opens one connection, streams rows, writes NOTHING.
    * Reads TEST_DATABASE_URL first (see data_quality.cli_common), so pointing
      it at staging is the default path. Never run it against production.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Dict, List, Mapping, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_quality import duplicate_detector as D  # noqa: E402
from data_quality import repository as repo  # noqa: E402
from data_quality.cli_common import resolve_connection  # noqa: E402
from data_quality.config import DataQualityConfig, get_config  # noqa: E402
from data_quality.models import DuplicateCandidate  # noqa: E402
from data_quality.validators import validate_record  # noqa: E402

DEFAULT_LIMITS = "1000,5000,10000,29551"
VARIANTS = ("full", "no-rapidfuzz", "legacy")


# ── the pre-hotfix algorithm, kept here purely as a baseline ────────────────
def legacy_detect(records: List[Mapping], config: DataQualityConfig,
                  max_block_size: int = 400) -> Tuple[List[DuplicateCandidate], int]:
    """Reproduces the duplicate pass as it behaved before this hotfix.

    Differences from the current detector: no pre-filter of any kind, and a pair
    is added to ``seen`` only when it turns out to be a candidate — so a
    non-candidate pair sharing several block keys is re-scored once per block.
    Returns (candidates, pairs_compared).
    """
    sigs = [D._Sig(r) for r in records]
    blocks = D._build_blocks(sigs, 8)
    seen: set = set()
    out: List[DuplicateCandidate] = []
    pairs = 0
    for _key, idxs in blocks.items():
        if len(idxs) < 2 or len(idxs) > max_block_size:
            continue
        for i in range(len(idxs)):
            for j in range(i + 1, len(idxs)):
                a, b = sigs[idxs[i]], sigs[idxs[j]]
                if a.id is None or b.id is None or a.id == b.id:
                    continue
                pair = (a.id, b.id) if a.id <= b.id else (b.id, a.id)
                if pair in seen:
                    continue
                pairs += 1
                cand = D._classify(a, b, config)
                if cand is not None:
                    seen.add(pair)
                    out.append(cand.ordered())
    out.sort(key=lambda c: (-c.score, c.record_id_1, c.record_id_2))
    return out, pairs


# ── measurement ─────────────────────────────────────────────────────────────
def fetch_records(conn, limit: Optional[int], batch_size: int) -> Tuple[List[Dict], float]:
    started = time.monotonic()
    records = list(repo.iter_dissertations(conn, limit=limit, batch_size=batch_size))
    return records, time.monotonic() - started


def validate_all(records: List[Dict]) -> Tuple[int, float]:
    started = time.monotonic()
    issues = 0
    for rec in records:
        issues += sum(1 for _ in validate_record(rec))
    return issues, time.monotonic() - started


def run_variant(variant: str, records: List[Dict],
                config: DataQualityConfig) -> Dict:
    """Time one duplicate pass. Returns a row for the report table."""
    if variant == "legacy":
        started = time.monotonic()
        candidates, pairs = legacy_detect(records, config)
        elapsed = time.monotonic() - started
        return {"variant": variant, "seconds": elapsed, "pairs": pairs,
                "classified": pairs, "candidates": len(candidates),
                "blocks": None, "skipped": None}

    original = D.RAPIDFUZZ_AVAILABLE
    if variant == "no-rapidfuzz":
        D.RAPIDFUZZ_AVAILABLE = False
    try:
        started = time.monotonic()
        candidates, stats = D.detect_duplicates_with_stats(records, config)
        elapsed = time.monotonic() - started
    finally:
        D.RAPIDFUZZ_AVAILABLE = original

    return {"variant": variant, "seconds": elapsed, "pairs": stats.pairs_seen,
            "classified": stats.pairs_classified, "candidates": len(candidates),
            "blocks": stats.blocks_planned, "skipped": len(stats.blocks_skipped),
            "cheap_skipped": stats.pairs_cheap_skipped,
            "rf_skipped": stats.pairs_rf_skipped,
            "repeated": stats.pairs_repeated,
            "largest_blocks": stats.largest_blocks}


def benchmark(conn, limits: List[Optional[int]], variants: List[str],
              config: DataQualityConfig, skip_validate: bool) -> List[Dict]:
    rows: List[Dict] = []
    for limit in limits:
        label = str(limit) if limit else "all"
        print(f"\n── limit={label} " + "─" * 40, flush=True)

        records, read_s = fetch_records(conn, limit, config.batch_size)
        print(f"   read      {len(records):>7,} records in {read_s:6.1f}s", flush=True)

        issues, validate_s = (0, 0.0)
        if not skip_validate:
            issues, validate_s = validate_all(records)
            print(f"   validate  {issues:>7,} issues  in {validate_s:6.1f}s", flush=True)

        for variant in variants:
            print(f"   duplicates [{variant}] …", flush=True)
            result = run_variant(variant, records, config)
            result.update({"limit": label, "records": len(records),
                           "read_seconds": read_s, "validate_seconds": validate_s,
                           "issues": issues,
                           "total_seconds": read_s + validate_s + result["seconds"]})
            rows.append(result)
            print(f"   duplicates [{variant}] {result['candidates']:,} candidates, "
                  f"{result['pairs']:,} pairs compared in {result['seconds']:.1f}s",
                  flush=True)
    return rows


# ── reporting ───────────────────────────────────────────────────────────────
def _fmt(n) -> str:
    return "—" if n is None else f"{n:,}"


def markdown_report(rows: List[Dict], target: str,
                    config: DataQualityConfig) -> str:
    lines = [
        "# Duplicate detection benchmark",
        "",
        f"* target: `{target}` (read-only)",
        f"* rapidfuzz available: `{D.RAPIDFUZZ_AVAILABLE}`",
        f"* thresholds: strong `{config.strong_threshold}`, "
        f"possible `{config.possible_threshold}`",
        f"* max block size: `{D._MAX_BLOCK_SIZE}`, hard cap `{D._HARD_BLOCK_CAP}`",
        "",
        "| limit | records | variant | read | validate | duplicates | total | "
        "pairs compared | classified | candidates | work units | skipped blocks |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['limit']} | {r['records']:,} | `{r['variant']}` | "
            f"{r['read_seconds']:.1f}s | {r['validate_seconds']:.1f}s | "
            f"**{r['seconds']:.1f}s** | {r['total_seconds']:.1f}s | "
            f"{_fmt(r['pairs'])} | {_fmt(r['classified'])} | "
            f"{r['candidates']:,} | {_fmt(r.get('blocks'))} | "
            f"{_fmt(r.get('skipped'))} |")

    lines += ["", "## Pre-filter breakdown", "",
              "| limit | variant | dropped by cheap bound | dropped by rapidfuzz | "
              "already-seen pairs | reached SequenceMatcher |",
              "|---|---|---|---|---|---|"]
    for r in rows:
        if r["variant"] == "legacy":
            continue
        lines.append(
            f"| {r['limit']} | `{r['variant']}` | {_fmt(r.get('cheap_skipped'))} | "
            f"{_fmt(r.get('rf_skipped'))} | {_fmt(r.get('repeated'))} | "
            f"{_fmt(r['classified'])} |")

    last = next((r for r in reversed(rows) if r.get("largest_blocks")), None)
    if last:
        lines += ["", f"## Largest blocks (limit={last['limit']}, "
                      f"variant=`{last['variant']}`)", "",
                  "| block key | records | pairs |", "|---|---|---|"]
        for label, size in last["largest_blocks"]:
            lines.append(f"| `{label}` | {size:,} | {size * (size - 1) // 2:,} |")
    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Benchmark duplicate detection (read-only)")
    p.add_argument("--limits", default=DEFAULT_LIMITS,
                   help=f"comma-separated record limits, 'all' for the whole "
                        f"table (default: {DEFAULT_LIMITS})")
    p.add_argument("--variants", default="full",
                   help=f"comma-separated subset of {VARIANTS} (default: full)")
    p.add_argument("--skip-validate", action="store_true",
                   help="time only read + duplicates")
    p.add_argument("--out", default=None, help="also write the markdown report here")
    p.add_argument("--database-url", default=None,
                   help="override DB URL (else DATA_QUALITY_/TEST_/DATABASE_URL)")
    p.add_argument("--quiet", action="store_true",
                   help="suppress the detector's per-block progress logging")
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s")

    limits: List[Optional[int]] = []
    for raw in args.limits.split(","):
        raw = raw.strip()
        if not raw:
            continue
        limits.append(None if raw.lower() == "all" else int(raw))

    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    unknown = [v for v in variants if v not in VARIANTS]
    if unknown:
        print(f"unknown variant(s): {unknown}; choose from {VARIANTS}")
        return 2

    config = get_config()
    conn, target = resolve_connection(args.database_url)
    print(f"→ Target database: {target}   (READ-ONLY benchmark)")
    if "staging" not in target and "stag" not in target and "test" not in target:
        print("  ⚠ target does not look like a staging database — Ctrl+C now "
              "if this is production.", flush=True)

    try:
        rows = benchmark(conn, limits, variants, config, args.skip_validate)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    report = markdown_report(rows, target, config)
    print("\n" + report)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(report)
        print(f"(report written to {args.out})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
