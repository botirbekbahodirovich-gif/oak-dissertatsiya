#!/usr/bin/env python3
"""Atomically import source-specific H-index researcher profiles.

Scopus and Scholar profiles remain separate provisional canonical researchers.
Match candidates are inserted as pending review; no candidate is auto-merged.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import date
from pathlib import Path

from validate_research_union import FILES, compose, profile_key, read_csv


DATASET_TYPE = "hindex_research_union"
SOURCE_NAME = "uz.h-index.com"


def optional(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def integer(value: str | None) -> int | None:
    value = optional(value)
    return int(value) if value is not None else None


def build_union(data_dir: Path):
    organizations = read_csv(data_dir / FILES["organizations"])
    org_rows = read_csv(data_dir / FILES["org_researchers"])
    scopus_top = read_csv(data_dir / FILES["scopus_top"])
    scholar_top = read_csv(data_dir / FILES["scholar_top"])
    candidates = read_csv(data_dir / FILES["candidates"])

    profiles: dict[tuple[str, str], dict] = {}
    for row in org_rows:
        key = profile_key(row)
        profiles[key] = {
            "source": key[0],
            "source_author_id": key[1],
            "org_slug": row["org_slug"].strip(),
            "organization_rank": integer(row["organization_rank"]),
            "country_rank": None,
            "display_name": row["display_name"].strip(),
            "normalized_name": row["normalized_name"].strip(),
            "orcid": optional(row["orcid"]),
            "hindex": integer(row["hindex"]),
            "hindex_status": row["hindex_status"].strip(),
            "internal_profile_url": optional(row["internal_profile_url"]),
            "external_profile_url": optional(row["external_profile_url"]),
            "reporting_period": row["reporting_period"].strip(),
            "scraped_at": optional(row["scraped_at"]),
            "origin": "org_researchers",
        }

    for top_source, rows in (("scopus", scopus_top), ("scholar", scholar_top)):
        for row in rows:
            key = profile_key(row)
            if key in profiles:
                profiles[key]["country_rank"] = int(row["rank"])
                profiles[key]["top_origin"] = f"{top_source}_top"
                continue
            profiles[key] = {
                "source": key[0],
                "source_author_id": key[1],
                "org_slug": row["org_slug"].strip(),
                "organization_rank": None,
                "country_rank": int(row["rank"]),
                "display_name": row["display_name"].strip(),
                "normalized_name": row["normalized_name"].strip(),
                "orcid": optional(row["orcid"]),
                "hindex": integer(row["hindex"]),
                "hindex_status": row["hindex_status"].strip(),
                "internal_profile_url": optional(row["internal_profile_url"]),
                "external_profile_url": optional(row["external_profile_url"]),
                "reporting_period": row["reporting_period"].strip(),
                "scraped_at": None,
                "origin": f"{top_source}_top_only",
            }
    return organizations, profiles, candidates


def db_connection():
    from dotenv import load_dotenv

    load_dotenv("/opt/olimlar-uz/.env")
    from data import get_connection

    return get_connection()


def validate_production_lookups(conn, organizations, snapshot_date: date):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id
            FROM h_index_snapshots s
            WHERE s.snapshot_date = %s AND s.source = %s
              AND EXISTS (SELECT 1 FROM h_index_institutions i WHERE i.snapshot_id = s.id)
            ORDER BY s.id
            """,
            (snapshot_date, SOURCE_NAME),
        )
        snapshot_ids = [row[0] for row in cur.fetchall()]
        if len(snapshot_ids) != 1:
            raise RuntimeError(f"Expected one institution snapshot, found {snapshot_ids}")
        snapshot_id = snapshot_ids[0]
        cur.execute(
            """
            SELECT i.id, i.institution_name, i.category, i.university_match, u.id
            FROM h_index_institutions i
            LEFT JOIN universities u ON u.name = i.university_match
            WHERE i.snapshot_id = %s
            """,
            (snapshot_id,),
        )
        ranking_rows = {
            row[1]: {
                "h_index_id": row[0],
                "category": row[2],
                "university_match": row[3],
                "university_id": row[4],
            }
            for row in cur.fetchall()
        }

    source_names = {row["name"].strip() for row in organizations}
    missing = sorted(source_names - set(ranking_rows))
    extra = sorted(set(ranking_rows) - source_names)
    if missing or extra or len(ranking_rows) != 144:
        raise RuntimeError(
            f"Organization lookup mismatch: missing={missing[:5]}, extra={extra[:5]}, rows={len(ranking_rows)}"
        )
    broken_university_links = sorted(
        name
        for name, row in ranking_rows.items()
        if row["university_match"] and row["university_id"] is None
    )
    if broken_university_links:
        raise RuntimeError(f"Broken university links: {broken_university_links[:10]}")
    return snapshot_id, ranking_rows


def ensure_target_tables(conn):
    required = [
        "research_import_batches",
        "research_organizations",
        "canonical_researchers",
        "researcher_external_profiles",
        "researcher_affiliations",
        "researcher_metric_snapshots",
        "researcher_match_candidates",
    ]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name = ANY(%s)",
            (required,),
        )
        found = {row[0] for row in cur.fetchall()}
    missing = sorted(set(required) - found)
    if missing:
        raise RuntimeError(f"Research schema is not applied; missing tables: {missing}")


def apply_import(
    conn,
    organizations,
    profiles,
    candidates,
    validation,
    snapshot_date: date,
    snapshot_id: int,
    ranking_rows: dict,
):
    reporting_periods = {profile["reporting_period"] for profile in profiles.values()}
    if reporting_periods != {"2026-Q2"}:
        raise RuntimeError(f"Unexpected reporting periods: {reporting_periods}")
    reporting_period = reporting_periods.pop()
    manifest_sha = validation["manifest_sha256"]

    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"research-union:{reporting_period}:{manifest_sha}",))
        cur.execute(
            """
            SELECT id, status FROM research_import_batches
            WHERE dataset_type=%s AND source=%s AND reporting_period=%s
              AND snapshot_date=%s AND lower(file_sha256)=lower(%s)
            """,
            (DATASET_TYPE, SOURCE_NAME, reporting_period, snapshot_date, manifest_sha),
        )
        existing = cur.fetchone()
        if existing:
            if existing[1] == "completed":
                conn.rollback()
                return {"skipped": True, "batch_id": existing[0]}
            raise RuntimeError(f"Existing non-completed batch: {existing}")

        cur.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM research_import_batches),
              (SELECT COUNT(*) FROM research_organizations),
              (SELECT COUNT(*) FROM canonical_researchers),
              (SELECT COUNT(*) FROM researcher_external_profiles),
              (SELECT COUNT(*) FROM researcher_metric_snapshots),
              (SELECT COUNT(*) FROM researcher_match_candidates)
            """
        )
        existing_counts = cur.fetchone()
        if any(existing_counts):
            raise RuntimeError(f"Research tables are not empty: {existing_counts}")

        notes = json.dumps(
            {
                "file_hashes": validation["file_hashes"],
                "profiles": len(profiles),
                "organizations": len(organizations),
                "candidates": len(candidates),
                "merge_policy": "no_auto_merge",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        total_source_rows = len(organizations) + len(profiles) + len(candidates)
        cur.execute(
            """
            INSERT INTO research_import_batches
              (dataset_type, source, reporting_period, snapshot_date, file_name,
               file_sha256, status, total_rows, notes)
            VALUES (%s,%s,%s,%s,%s,%s,'importing',%s,%s)
            RETURNING id
            """,
            (
                DATASET_TYPE,
                SOURCE_NAME,
                reporting_period,
                snapshot_date,
                "hindex_research_union_manifest.json",
                manifest_sha,
                total_source_rows,
                notes,
            ),
        )
        batch_id = cur.fetchone()[0]

        organization_ids: dict[str, int] = {}
        for row in organizations:
            name = row["name"].strip()
            lookup = ranking_rows[name]
            cur.execute(
                """
                INSERT INTO research_organizations
                  (slug,name,normalized_name,category,university_id,source,source_url)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
                """,
                (
                    row["slug"].strip(),
                    name,
                    row["normalized_name"].strip(),
                    lookup["category"],
                    lookup["university_id"],
                    SOURCE_NAME,
                    optional(row["source_url"]),
                ),
            )
            organization_id = cur.fetchone()[0]
            organization_ids[row["slug"].strip()] = organization_id
            cur.execute(
                "UPDATE h_index_institutions SET research_organization_id=%s WHERE id=%s",
                (organization_id, lookup["h_index_id"]),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"Ranking organization link failed for {name}")

        profile_ids: dict[tuple[str, str], tuple[int, int]] = {}
        for key in sorted(profiles):
            profile = profiles[key]
            organization_id = organization_ids[profile["org_slug"]]
            cur.execute(
                """
                INSERT INTO canonical_researchers
                  (display_name, normalized_name, orcid, canonical_status)
                VALUES (%s,%s,NULL,'unreviewed')
                RETURNING id
                """,
                (profile["display_name"], profile["normalized_name"]),
            )
            researcher_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO researcher_external_profiles
                  (researcher_id,source,source_author_id,internal_profile_url,
                   external_profile_url,orcid)
                VALUES (%s,%s,%s,%s,%s,%s)
                RETURNING id
                """,
                (
                    researcher_id,
                    profile["source"],
                    profile["source_author_id"],
                    profile["internal_profile_url"],
                    profile["external_profile_url"],
                    profile["orcid"],
                ),
            )
            external_profile_id = cur.fetchone()[0]
            profile_ids[key] = (researcher_id, external_profile_id)
            cur.execute(
                """
                INSERT INTO researcher_affiliations
                  (researcher_id,organization_id,source,organization_rank,
                   reporting_period,is_primary)
                VALUES (%s,%s,%s,%s,%s,TRUE)
                """,
                (
                    researcher_id,
                    organization_id,
                    profile["source"],
                    profile["organization_rank"],
                    reporting_period,
                ),
            )
            cur.execute(
                """
                INSERT INTO researcher_metric_snapshots
                  (researcher_id,external_profile_id,import_batch_id,source,h_index,
                   hindex_status,rank,reporting_period,snapshot_date,source_url,scraped_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    researcher_id,
                    external_profile_id,
                    batch_id,
                    profile["source"],
                    profile["hindex"],
                    profile["hindex_status"],
                    profile["country_rank"],
                    reporting_period,
                    snapshot_date,
                    profile["internal_profile_url"],
                    profile["scraped_at"],
                ),
            )

        for row in candidates:
            scopus_key = ("scopus", row["scopus_author_id"].strip())
            scholar_key = ("scholar", row["scholar_author_id"].strip())
            signals = {
                "signals": [part for part in row["signals"].split(";") if part],
                "scopus_name": row["scopus_name"].strip(),
                "scholar_name": row["scholar_name"].strip(),
            }
            cur.execute(
                """
                INSERT INTO researcher_match_candidates
                  (organization_id,scopus_profile_id,scholar_profile_id,name_similarity,
                   signals,decision,review_status)
                VALUES (%s,%s,%s,%s,%s::jsonb,%s,'pending')
                """,
                (
                    organization_ids[row["org_slug"].strip()],
                    profile_ids[scopus_key][1],
                    profile_ids[scholar_key][1],
                    row["name_similarity"].strip(),
                    json.dumps(signals, ensure_ascii=False),
                    row["decision"].strip(),
                ),
            )

        cur.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM research_organizations),
              (SELECT COUNT(*) FROM canonical_researchers),
              (SELECT COUNT(*) FROM researcher_external_profiles),
              (SELECT COUNT(*) FROM researcher_affiliations),
              (SELECT COUNT(*) FROM researcher_metric_snapshots),
              (SELECT COUNT(*) FROM researcher_match_candidates),
              (SELECT COUNT(*) FROM researcher_metric_snapshots WHERE rank IS NOT NULL),
              (SELECT COUNT(*) FROM researcher_metric_snapshots WHERE hindex_status='source_na')
            """
        )
        final_counts = cur.fetchone()
        expected_counts = (144, 10574, 10574, 10574, 10574, 540, 2000, 930)
        if final_counts != expected_counts:
            raise RuntimeError(f"Final count mismatch: expected={expected_counts}, actual={final_counts}")

        cur.execute(
            """
            UPDATE research_import_batches
            SET status='completed', inserted_rows=%s, skipped_rows=0, error_rows=0,
                completed_at=NOW()
            WHERE id=%s
            """,
            (total_source_rows, batch_id),
        )
    conn.commit()
    return {"skipped": False, "batch_id": batch_id, "counts": final_counts}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--snapshot-date", default="2026-07-31")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    validation = compose(args.data_dir)
    if not validation["ok"]:
        raise SystemExit(json.dumps(validation, ensure_ascii=False, indent=2))
    organizations, profiles, candidates = build_union(args.data_dir)
    snapshot_date = date.fromisoformat(args.snapshot_date)

    conn = db_connection()
    try:
        snapshot_id, ranking_rows = validate_production_lookups(conn, organizations, snapshot_date)
        dry_summary = {
            "ok": True,
            "mode": "apply" if args.apply else "dry-run",
            "snapshot_id": snapshot_id,
            "organizations": len(organizations),
            "profiles": len(profiles),
            "source_counts": dict(Counter(profile["source"] for profile in profiles.values())),
            "candidates": len(candidates),
            "country_ranks": sum(profile["country_rank"] is not None for profile in profiles.values()),
            "source_na": sum(profile["hindex_status"] == "source_na" for profile in profiles.values()),
            "manifest_sha256": validation["manifest_sha256"],
        }
        if not args.apply:
            conn.rollback()
            print(json.dumps(dry_summary, ensure_ascii=False, indent=2))
            return 0
        ensure_target_tables(conn)
        result = apply_import(
            conn,
            organizations,
            profiles,
            candidates,
            validation,
            snapshot_date,
            snapshot_id,
            ranking_rows,
        )
        dry_summary["result"] = result
        print(json.dumps(dry_summary, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
