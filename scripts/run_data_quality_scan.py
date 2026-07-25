#!/usr/bin/env python3
"""Data Quality scanner.

    python scripts/run_data_quality_scan.py --mode report
    python scripts/run_data_quality_scan.py --mode persist
    python scripts/run_data_quality_scan.py --mode normalize-preview
    python scripts/run_data_quality_scan.py --limit 1000
    python scripts/run_data_quality_scan.py --record-id 123
    python scripts/run_data_quality_scan.py --duplicates-only

Modes
    report            aggregate results to the terminal; writes NOTHING to the DB.
    persist           writes a data_quality_runs row + data_quality_issues +
                      duplicate_candidates (requires migration 004).
    normalize-preview shows original vs normalized values only; no DB writes.

Safety
    * default mode is ``report`` (non-destructive).
    * batched + server-side cursor -> bounded memory on 28k+ / 100k+ rows.
    * any exception marks a persisted run 'failed' instead of crashing.
    * no Telegram id / e-mail / secret is ever printed; values are truncated.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_quality.cli_common import resolve_connection, truncate  # noqa: E402
from data_quality.config import get_config  # noqa: E402
from data_quality.quality_service import (  # noqa: E402
    MODE_PERSIST, MODE_PREVIEW, MODE_REPORT, DataQualityService)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Olimlar.uz data-quality scanner")
    p.add_argument("--mode", choices=[MODE_REPORT, MODE_PERSIST, MODE_PREVIEW],
                   default=MODE_REPORT)
    p.add_argument("--limit", type=int, default=None,
                   help="Scan at most N records (by id order)")
    p.add_argument("--record-id", type=int, default=None,
                   help="Scan a single dissertation id")
    p.add_argument("--duplicates-only", action="store_true",
                   help="Skip field validation; only detect duplicates")
    p.add_argument("--sample", type=int, default=15,
                   help="Max sample issues to print")
    p.add_argument("--database-url", default=None,
                   help="Override DB URL (else DATA_QUALITY_/TEST_/DATABASE_URL)")
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = get_config()

    if args.mode == MODE_PERSIST and not config.scan_enabled:
        print("Scan is disabled by DATA_QUALITY_SCAN_ENABLED=0 — refusing persist.")
        return 2

    conn, target = resolve_connection(args.database_url)
    print(f"→ Target database: {target}   mode={args.mode}")
    if args.mode != MODE_PERSIST:
        print("  (read-only: this mode writes NOTHING to the database)")

    try:
        service = DataQualityService(conn, config)
        result = service.scan(
            mode=args.mode, limit=args.limit, record_id=args.record_id,
            duplicates_only=args.duplicates_only, sample_cap=max(args.sample, 50),
            progress=lambda n: print(f"  … checked {n} records", flush=True))
    finally:
        try:
            conn.close()
        except Exception:
            pass

    _print_report(result, args)
    return 0 if result.status == "completed" else 1


def _print_report(result, args) -> None:
    s = result.summary
    print("\n" + "=" * 60)
    print(f"RUN status : {result.status}"
          + (f"   run_id={result.run_id}" if result.run_id else ""))
    if result.error_message:
        print(f"ERROR      : {result.error_message}")
    print(f"records    : total={s.total_records} checked={s.checked_records}")
    print(f"issues     : {s.issue_count}")
    print(f"duplicates : {s.duplicate_count}")
    print(f"quality    : {s.quality_score} / 100")

    if s.issues_by_severity:
        print("\nby severity:")
        for sev, cnt in sorted(s.issues_by_severity.items(), key=lambda kv: -kv[1]):
            print(f"  {sev:<9} {cnt}")
    if s.issues_by_code:
        print("\nby issue code:")
        for code, cnt in sorted(s.issues_by_code.items(), key=lambda kv: -kv[1]):
            print(f"  {code:<26} {cnt}")

    if s.sample_issues:
        head = "normalization preview" if args.mode == MODE_PREVIEW else "sample issues"
        print(f"\n{head} (max {args.sample}):")
        for iss in s.sample_issues[: args.sample]:
            if args.mode == MODE_PREVIEW:
                print(f"  [{iss.record_id}] {iss.field_name}: "
                      f"{truncate(iss.original_value)!r} -> {truncate(iss.normalized_value)!r}")
            else:
                print(f"  [{iss.record_id}] {iss.severity:<8} {iss.issue_code:<24} "
                      f"{iss.field_name}: {truncate(iss.description)}")

    if args.mode != MODE_PERSIST:
        print("\n(no database changes were made — use --mode persist to record results)")


if __name__ == "__main__":
    raise SystemExit(main())
