#!/usr/bin/env python3
"""Read-only data-quality dry-run audit (Phase 1 — audit/data-quality-phase-1).

Scans dissertation/institution/scholar data for the issues described in
docs/data-quality/architecture.md and writes a quarantine report to
output/data_quality_report.{json,txt}. NEVER writes to any database table
and NEVER modifies dissertations/olim_profiles/institution_map — this is a
detector only; nothing here merges or auto-fixes anything.

Data source, in priority order:
  1. DATABASE_URL (if set and reachable) — reads a bounded, read-only sample
     of `dissertations` (SELECT ... LIMIT --limit, no writes, connection is
     rolled back / closed without committing anything).
  2. data/dissertatsiyalar.csv — the small legacy sample CSV bundled in the
     repo, used automatically when no DB is reachable (e.g. local dev
     environments without production credentials).

Usage:
    python3 scripts/data_quality_audit.py [--limit 5000] [--source auto|db|csv]
"""
import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from utils.data_quality.names import is_broken_name, normalize_person_name
from utils.data_quality.specialties import (all_specialty_codes,
                                             validate_code_format)
from utils.data_quality.location import (flag_location_mismatch,
                                          detect_filial_region_conflict,
                                          detect_region_scored)
from utils.data_quality.gender import detect_gender_scored
from utils.data_quality.confidence import score_candidate_pair, confidence_band
from utils.data_quality.stopwords import COMBINED_STOPWORDS

CSV_FALLBACK = REPO_ROOT / "data" / "dissertatsiyalar.csv"
OUTPUT_DIR = REPO_ROOT / "output"

# Old (app.py STOP_WORDS-equivalent) list, reconstructed for the demo
# comparison only — Uzbek-only, no Russian/English. See stopwords.py docstring.
_OLD_STOPWORDS_DEMO = frozenset({
    "va", "ham", "yoki", "lekin", "uchun", "bilan", "bu", "shu", "u",
    "ва", "ҳам", "ёки", "лекин", "учун", "билан", "бу", "шу", "у",
})


def load_rows(source, limit):
    """Returns (rows, actual_source) where rows is a list of dicts with keys
    sana, daraja, olim, mavzu, ixtisoslik, muassasa, ilmiy_rahbar."""
    if source in ("auto", "db"):
        rows = _load_from_db(limit)
        if rows is not None:
            return rows, "db"
        if source == "db":
            print("DATABASE_URL not set/reachable and --source db was forced; aborting.",
                  file=sys.stderr)
            sys.exit(1)
    return _load_from_csv(limit), "csv"


def _load_from_db(limit):
    url = os.environ.get("DATABASE_URL")
    if not url:
        return None
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        return None
    try:
        conn = psycopg2.connect(url, connect_timeout=5)
    except Exception as exc:  # noqa: BLE001 — any connection failure -> fall back
        print(f"(DB unreachable, falling back to CSV sample: {exc})", file=sys.stderr)
        return None
    try:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT sana, daraja, olim, mavzu, ixtisoslik, muassasa, "
                "ilmiy_rahbar FROM dissertations ORDER BY id DESC LIMIT %s",
                (limit,))
            rows = [dict(r) for r in cur.fetchall()]
        conn.rollback()  # belt and suspenders — read-only session, nothing to commit
        return rows
    finally:
        conn.close()


def _load_from_csv(limit):
    if not CSV_FALLBACK.exists():
        print(f"No CSV fallback found at {CSV_FALLBACK}", file=sys.stderr)
        return []
    rows = []
    with open(CSV_FALLBACK, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i >= limit:
                break
            rows.append({
                "sana": row.get("Sana"),
                "daraja": row.get("Daraja"),
                "olim": row.get("Olim"),
                "mavzu": row.get("Mavzu"),
                "ixtisoslik": row.get("Ixtisoslik"),
                "muassasa": row.get("Muassasa"),
                "ilmiy_rahbar": row.get("Ilmiy_rahbar"),
            })
    return rows


def _year_from_sana(sana):
    if not sana:
        return None
    for token in str(sana).replace("-", ".").replace("/", ".").split("."):
        if len(token) == 4 and token.isdigit():
            return int(token)
    return None


def audit_broken_names(rows):
    issues = []
    seen = set()
    for row in rows:
        for field in ("olim", "ilmiy_rahbar"):
            name = row.get(field)
            key = (field, name)
            if key in seen:
                continue
            seen.add(key)
            broken, reason = is_broken_name(name)
            if broken:
                issues.append({"field": field, "raw_value": name, "reason": reason})
    return issues


def audit_specialty_codes(rows):
    issues = []
    seen = set()
    for row in rows:
        raw = row.get("ixtisoslik")
        if not raw or raw in seen:
            continue
        seen.add(raw)
        codes = all_specialty_codes(raw)
        if not codes:
            if raw.strip():
                issues.append({"raw_value": raw, "issue": "no_code_found"})
            continue
        if len(codes) > 1:
            issues.append({"raw_value": raw, "issue": "multiple_codes_present",
                            "codes": codes,
                            "note": "data.py's live _extract_ixtisoslik keeps only the first"})
        if not validate_code_format(codes[0]) or raw.strip() != codes[0]:
            issues.append({"raw_value": raw, "issue": "code_embedded_in_text",
                            "extracted_code": codes[0]})
    return issues


def audit_locations(rows):
    issues = []
    seen = set()
    for row in rows:
        name = row.get("muassasa")
        if not name or name in seen:
            continue
        seen.add(name)
        region, confidence, _ = detect_region_scored(name)
        if region is None:
            issues.append({"institution_name": name, "issue": "region_undetectable",
                            "note": "would silently become Toshkent under app.py's "
                                    "detect_uni_city_region()/detect_region() fallback"})
        conflict = detect_filial_region_conflict(name)
        if conflict:
            issues.append({"institution_name": name, "issue": "filial_region_conflict",
                            **conflict})
    return issues


def audit_gender(rows):
    seen = set()
    tally = Counter()
    confidence_sum = defaultdict(float)
    for row in rows:
        name = row.get("olim")
        if not name or name in seen:
            continue
        seen.add(name)
        result = detect_gender_scored(name)
        tally[result["gender"]] += 1
        confidence_sum[result["gender"]] += result["confidence"]
    summary = {}
    for gender, count in tally.items():
        summary[gender] = {
            "count": count,
            "avg_confidence": round(confidence_sum[gender] / count, 3) if count else 0.0,
        }
    return summary


def audit_duplicate_candidates(rows):
    """Group distinct olim names by normalized_key (order/apostrophe/case
    tolerant); within each group of >1 distinct raw spelling, score every
    pair. Nothing here merges anything — output only."""
    by_key = defaultdict(set)
    meta = {}
    for row in rows:
        name = row.get("olim")
        if not name:
            continue
        key = normalize_person_name(name)
        if not key:
            continue
        by_key[key].add(name)
        meta[name] = {
            "name": name,
            "institution_id": row.get("muassasa"),
            "specialty_code": (all_specialty_codes(row.get("ixtisoslik") or "") or [None])[0],
            "year": _year_from_sana(row.get("sana")),
        }

    candidates = []
    for key, names in by_key.items():
        names = list(names)
        if len(names) < 2:
            continue
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = meta[names[i]], meta[names[j]]
                score = score_candidate_pair(a, b)
                candidates.append({
                    "normalized_key": key,
                    "name_a": names[i],
                    "name_b": names[j],
                    "score": round(score, 3),
                    "band": confidence_band(score),
                })
    candidates.sort(key=lambda c: -c["score"])
    return candidates


def audit_stopword_improvement(rows):
    """Demonstrates the combined stop-word list's effect vs. the current
    Uzbek-only list, on whatever topic text is available in this sample."""
    old_pass, new_blocked = Counter(), Counter()
    for row in rows:
        text = (row.get("mavzu") or "")
        for word in text.lower().split():
            w = "".join(ch for ch in word if ch.isalpha())
            if len(w) < 4:
                continue
            if w not in _OLD_STOPWORDS_DEMO:
                old_pass[w] += 1
                if w in COMBINED_STOPWORDS:
                    new_blocked[w] += 1
    return {
        "would_now_be_filtered": dict(new_blocked.most_common(20)),
        "note": "words that survive app.py's current Uzbek-only STOP_WORDS "
                "filter but are caught by the combined uz/ru/en list",
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=5000,
                     help="max rows to scan (DB or CSV)")
    ap.add_argument("--source", choices=["auto", "db", "csv"], default="auto")
    args = ap.parse_args()

    started_at = datetime.now(timezone.utc)
    rows, actual_source = load_rows(args.source, args.limit)

    report = {
        "run_type": "dry_run_audit",
        "started_at": started_at.isoformat(),
        "data_source": actual_source,
        "records_scanned": len(rows),
        "broken_names": audit_broken_names(rows),
        "specialty_issues": audit_specialty_codes(rows),
        "location_issues": audit_locations(rows),
        "gender_confidence_summary": audit_gender(rows),
        "duplicate_candidates": audit_duplicate_candidates(rows),
        "stopword_improvement": audit_stopword_improvement(rows),
    }
    report["issues_found"] = (len(report["broken_names"]) + len(report["specialty_issues"])
                               + len(report["location_issues"]))
    report["candidates_found"] = len(report["duplicate_candidates"])
    report["finished_at"] = datetime.now(timezone.utc).isoformat()

    OUTPUT_DIR.mkdir(exist_ok=True)
    json_path = OUTPUT_DIR / "data_quality_report.json"
    txt_path = OUTPUT_DIR / "data_quality_report.txt"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"Data quality dry-run — source={actual_source}, rows scanned={len(rows)}",
        f"Broken names:         {len(report['broken_names'])}",
        f"Specialty issues:     {len(report['specialty_issues'])}",
        f"Location issues:      {len(report['location_issues'])}",
        f"Duplicate candidates: {len(report['duplicate_candidates'])}",
        f"Gender summary:       {report['gender_confidence_summary']}",
        "",
        "(nothing was written to any database; see "
        f"{json_path.relative_to(REPO_ROOT)} for full detail)",
    ]
    txt_path.write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines))
    print(f"\nFull report: {json_path}")


if __name__ == "__main__":
    main()
