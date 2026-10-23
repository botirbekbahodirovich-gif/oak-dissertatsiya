#!/usr/bin/env python3
"""Dry-run audit for values used by the public scholars catalogue.

This command never writes to the database or source CSV.  Use ``--json`` to
save a reviewable quarantine log.
"""
from __future__ import annotations

import argparse
import csv
import sys
import json
from collections import Counter
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from utils.olimlar_catalog_quality import (
    canonical_specialty,
    normalize_scholar_name,
    scholar_name_issue,
)


def audit_csv(path, canonical_names=None):
    issues = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), start=2):
            raw_name = row.get("Ilmiy_rahbar") or row.get("ilmiy_rahbar") or ""
            reason = scholar_name_issue(raw_name)
            if reason:
                issues.append({
                    "row": row_number,
                    "field": "ilmiy_rahbar",
                    "value": raw_name,
                    "normalized": normalize_scholar_name(raw_name),
                    "reason": reason,
                })

            code = row.get("Ixtisoslik") or row.get("ixtisoslik") or ""
            source_name = row.get("Ixtisoslik_nomi") or row.get("ixtisoslik_nomi") or ""
            clean_code, display_name, reasons = canonical_specialty(
                code, source_name, canonical_names or {}
            )
            for item in reasons:
                issues.append({
                    "row": row_number,
                    "field": "ixtisoslik",
                    "value": f"{code} | {source_name}",
                    "normalized": f"{clean_code} | {display_name}",
                    "reason": item,
                })
    return issues


def main():
    parser = argparse.ArgumentParser(
        description="Olimlar katalogi ma'lumotlarini o'zgartirmasdan tekshiradi."
    )
    parser.add_argument("--csv", default="data/dissertatsiyalar.csv")
    parser.add_argument("--json", help="Audit yozuvlarini JSON faylga saqlash")
    args = parser.parse_args()

    issues = audit_csv(args.csv)
    counts = Counter(item["reason"] for item in issues)
    print("DRY RUN: manba ma'lumotlari o'zgartirilmadi.")
    print(f"Audit yozuvlari: {len(issues)}")
    for reason, count in sorted(counts.items()):
        print(f"  {reason}: {count}")

    if args.json:
        target = Path(args.json)
        target.write_text(
            json.dumps(issues, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Audit logi: {target}")


if __name__ == "__main__":
    main()
