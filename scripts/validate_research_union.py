#!/usr/bin/env python3
"""Validate and compose the source-specific H-index researcher union offline."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


FILES = {
    "organizations": "organizations_cleaned.csv",
    "org_researchers": "org_researchers_cleaned.csv",
    "scopus_top": "scopus_top1000_cleaned.csv",
    "scholar_top": "scholar_top1000_cleaned.csv",
    "candidates": "canonical_match_candidates.csv",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def profile_key(row: dict[str, str]) -> tuple[str, str]:
    source = row["source"].strip().lower()
    author_id = (
        row.get("scopus_author_id", "").strip()
        if source == "scopus"
        else row.get("scholar_author_id", "").strip()
    )
    if source not in {"scopus", "scholar"} or not author_id:
        raise ValueError(f"Invalid source profile locator: {source!r} / {author_id!r}")
    return source, author_id


def compose(data_dir: Path) -> dict:
    paths = {key: data_dir / name for key, name in FILES.items()}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing files: " + ", ".join(missing))

    organizations = read_csv(paths["organizations"])
    org_rows = read_csv(paths["org_researchers"])
    scopus_top = read_csv(paths["scopus_top"])
    scholar_top = read_csv(paths["scholar_top"])
    candidates = read_csv(paths["candidates"])

    errors: list[str] = []
    org_slugs = {row["slug"].strip() for row in organizations}
    if len(organizations) != 144 or len(org_slugs) != 144:
        errors.append(f"organizations expected 144 unique slugs, got {len(organizations)}/{len(org_slugs)}")

    profiles: dict[tuple[str, str], dict] = {}
    for row in org_rows:
        try:
            key = profile_key(row)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if key in profiles:
            errors.append(f"duplicate org profile {key}")
            continue
        if row["org_slug"].strip() not in org_slugs:
            errors.append(f"unknown organization slug in org researchers: {row['org_slug']}")
        profiles[key] = {
            "source": key[0],
            "source_author_id": key[1],
            "org_slug": row["org_slug"].strip(),
            "organization_rank": row["organization_rank"].strip() or None,
            "country_rank": None,
            "raw_name": row["raw_name"].strip(),
            "display_name": row["display_name"].strip(),
            "normalized_name": row["normalized_name"].strip(),
            "orcid": row["orcid"].strip() or None,
            "hindex": row["hindex"].strip() or None,
            "hindex_status": row["hindex_status"].strip(),
            "internal_profile_url": row["internal_profile_url"].strip() or None,
            "external_profile_url": row["external_profile_url"].strip() or None,
            "reporting_period": row["reporting_period"].strip(),
            "scraped_at": row["scraped_at"].strip() or None,
            "origin": "org_researchers",
        }

    overlap = Counter()
    top_only = Counter()
    metric_conflicts: list[dict] = []
    name_conflicts: list[dict] = []
    for top_source, rows in (("scopus", scopus_top), ("scholar", scholar_top)):
        seen_top: set[tuple[str, str]] = set()
        for row in rows:
            key = profile_key(row)
            if key in seen_top:
                errors.append(f"duplicate TOP profile {key}")
                continue
            seen_top.add(key)
            if row["org_slug"].strip() not in org_slugs:
                errors.append(f"unknown organization slug in TOP: {row['org_slug']}")
            if key in profiles:
                overlap[top_source] += 1
                existing = profiles[key]
                if existing["hindex"] != (row["hindex"].strip() or None):
                    metric_conflicts.append({
                        "key": key,
                        "org_hindex": existing["hindex"],
                        "top_hindex": row["hindex"].strip() or None,
                    })
                if existing["normalized_name"] != row["normalized_name"].strip():
                    name_conflicts.append({
                        "key": key,
                        "org_name": existing["normalized_name"],
                        "top_name": row["normalized_name"].strip(),
                    })
                existing["country_rank"] = row["rank"].strip()
                existing["top_origin"] = f"{top_source}_top"
            else:
                top_only[top_source] += 1
                profiles[key] = {
                    "source": key[0],
                    "source_author_id": key[1],
                    "org_slug": row["org_slug"].strip(),
                    "organization_rank": None,
                    "country_rank": row["rank"].strip(),
                    "raw_name": row["raw_name"].strip(),
                    "display_name": row["display_name"].strip(),
                    "normalized_name": row["normalized_name"].strip(),
                    "orcid": row["orcid"].strip() or None,
                    "hindex": row["hindex"].strip() or None,
                    "hindex_status": row["hindex_status"].strip(),
                    "internal_profile_url": row["internal_profile_url"].strip() or None,
                    "external_profile_url": row["external_profile_url"].strip() or None,
                    "reporting_period": row["reporting_period"].strip(),
                    "scraped_at": None,
                    "origin": f"{top_source}_top_only",
                }

    candidate_keys: set[tuple[tuple[str, str], tuple[str, str]]] = set()
    candidate_missing_profiles = 0
    candidate_missing_orgs = 0
    for row in candidates:
        pair = (("scopus", row["scopus_author_id"].strip()), ("scholar", row["scholar_author_id"].strip()))
        if pair in candidate_keys:
            errors.append(f"duplicate candidate pair {pair}")
        candidate_keys.add(pair)
        if pair[0] not in profiles or pair[1] not in profiles:
            candidate_missing_profiles += 1
        if row["org_slug"].strip() not in org_slugs:
            candidate_missing_orgs += 1

    source_counts = Counter(source for source, _ in profiles)
    hindex_status_counts = Counter(profile["hindex_status"] for profile in profiles.values())
    orcids = [profile["orcid"] for profile in profiles.values() if profile["orcid"]]
    duplicate_orcid_values = sorted(value for value, count in Counter(orcids).items() if count > 1)

    expected = {
        "profiles": 10574,
        "scopus": 5415,
        "scholar": 5159,
        "scopus_overlap": 851,
        "scholar_overlap": 926,
        "scopus_top_only": 149,
        "scholar_top_only": 74,
        "candidates": 540,
    }
    actual = {
        "profiles": len(profiles),
        "scopus": source_counts["scopus"],
        "scholar": source_counts["scholar"],
        "scopus_overlap": overlap["scopus"],
        "scholar_overlap": overlap["scholar"],
        "scopus_top_only": top_only["scopus"],
        "scholar_top_only": top_only["scholar"],
        "candidates": len(candidates),
    }
    if actual != expected:
        errors.append(f"unexpected union counts: expected={expected}, actual={actual}")
    if candidate_missing_profiles or candidate_missing_orgs:
        errors.append(
            f"candidate lookup failures: profiles={candidate_missing_profiles}, orgs={candidate_missing_orgs}"
        )

    file_hashes = {key: file_sha256(path) for key, path in paths.items()}
    manifest_json = json.dumps(file_hashes, sort_keys=True, separators=(",", ":"))
    manifest_sha256 = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()

    return {
        "ok": not errors,
        "errors": errors,
        "expected": expected,
        "actual": actual,
        "source_counts": dict(source_counts),
        "hindex_status_counts": dict(hindex_status_counts),
        "metric_conflict_count": len(metric_conflicts),
        "metric_conflict_sample": metric_conflicts[:10],
        "name_conflict_count": len(name_conflicts),
        "name_conflict_sample": name_conflicts[:10],
        "orcid_nonblank": len(orcids),
        "duplicate_orcid_value_count": len(duplicate_orcid_values),
        "duplicate_orcid_value_sample": duplicate_orcid_values[:20],
        "candidate_missing_profiles": candidate_missing_profiles,
        "candidate_missing_orgs": candidate_missing_orgs,
        "candidate_decisions": dict(Counter(row["decision"] for row in candidates)),
        "file_hashes": file_hashes,
        "manifest_sha256": manifest_sha256,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = compose(args.data_dir)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
