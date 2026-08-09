import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path

from dotenv import load_dotenv

load_dotenv("/opt/olimlar-uz/.env")

from data import get_connection


def normalize_name(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", " ", text.casefold())
    return " ".join(text.split())


def scalar(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchone()[0]


def counts(cur):
    cur.execute("""
        SELECT
          (SELECT COUNT(*) FROM canonical_researchers),
          (SELECT COUNT(*) FROM researcher_external_profiles),
          (SELECT COUNT(*) FROM researcher_external_profiles WHERE source='scopus'),
          (SELECT COUNT(*) FROM researcher_external_profiles WHERE source='scholar'),
          (SELECT COUNT(*) FROM researcher_metric_snapshots),
          (SELECT COUNT(*) FROM researcher_affiliations),
          (SELECT COUNT(*) FROM researcher_match_candidates WHERE review_status='approved'),
          (SELECT COUNT(*) FROM researcher_match_candidates WHERE review_status='pending')
    """)
    keys = (
        "canonical_researchers", "external_profiles", "scopus_profiles",
        "scholar_profiles", "metric_snapshots", "affiliations",
        "approved_candidates", "pending_candidates",
    )
    return dict(zip(keys, cur.fetchone()))


def candidate_state(cur, candidate_id):
    cur.execute("""
        SELECT
          mc.review_status, mc.decision, mc.name_similarity,
          mc.organization_id, o.slug,
          sp.id, sp.researcher_id, sp.source_author_id,
          sc.display_name, sc.canonical_status, sc.orcid, sc.olim_profile_id,
          hp.id, hp.researcher_id, hp.source_author_id,
          hc.display_name, hc.canonical_status, hc.orcid, hc.olim_profile_id,
          sm.h_index, hm.h_index
        FROM researcher_match_candidates mc
        JOIN research_organizations o ON o.id=mc.organization_id
        JOIN researcher_external_profiles sp
          ON sp.id=mc.scopus_profile_id AND sp.source='scopus'
        JOIN canonical_researchers sc ON sc.id=sp.researcher_id
        JOIN researcher_external_profiles hp
          ON hp.id=mc.scholar_profile_id AND hp.source='scholar'
        JOIN canonical_researchers hc ON hc.id=hp.researcher_id
        LEFT JOIN LATERAL (
          SELECT m.h_index FROM researcher_metric_snapshots m
          WHERE m.external_profile_id=sp.id
          ORDER BY m.snapshot_date DESC, m.id DESC LIMIT 1
        ) sm ON TRUE
        LEFT JOIN LATERAL (
          SELECT m.h_index FROM researcher_metric_snapshots m
          WHERE m.external_profile_id=hp.id
          ORDER BY m.snapshot_date DESC, m.id DESC LIMIT 1
        ) hm ON TRUE
        WHERE mc.id=%s
        FOR UPDATE OF mc, sp, hp, sc, hc
    """, (candidate_id,))
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"Candidate {candidate_id} not found")
    keys = (
        "review_status", "decision", "name_similarity", "organization_id", "org_slug",
        "scopus_profile_id", "scopus_researcher_id", "scopus_author_id",
        "scopus_name", "scopus_canonical_status", "scopus_canonical_orcid", "scopus_olim_profile_id",
        "scholar_profile_id", "scholar_researcher_id", "scholar_author_id",
        "scholar_name", "scholar_canonical_status", "scholar_canonical_orcid", "scholar_olim_profile_id",
        "scopus_hindex", "scholar_hindex",
    )
    return dict(zip(keys, row))


def validate_pending(cur, expected, current):
    candidate_id = expected["candidate_id"]
    checks = {
        "review_status": current["review_status"] == "pending",
        "decision": current["decision"] == "high_confidence_candidate",
        "name_similarity": float(current["name_similarity"]) == 1.0,
        "org_slug": current["org_slug"] == expected["org_slug"],
        "scopus_profile_id": current["scopus_profile_id"] == expected["scopus_profile_id"],
        "scholar_profile_id": current["scholar_profile_id"] == expected["scholar_profile_id"],
        "scopus_author_id": str(current["scopus_author_id"]) == str(expected["scopus_author_id"]),
        "scholar_author_id": str(current["scholar_author_id"]) == str(expected["scholar_author_id"]),
        "canonical_pair": {
            current["scopus_researcher_id"], current["scholar_researcher_id"]
        } == {expected["survivor_researcher_id"], expected["retired_researcher_id"]},
        "canonical_distinct": current["scopus_researcher_id"] != current["scholar_researcher_id"],
        "canonical_status": (
            current["scopus_canonical_status"] == "unreviewed"
            and current["scholar_canonical_status"] == "unreviewed"
        ),
        "scopus_name": current["scopus_name"] == expected["scopus_name"],
        "scholar_name": current["scholar_name"] == expected["scholar_name"],
        "scopus_hindex": current["scopus_hindex"] == expected["scopus_hindex"],
        "scholar_hindex": current["scholar_hindex"] == expected["scholar_hindex"],
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise RuntimeError(f"Candidate {candidate_id} precondition failed: {failed}")

    scopus_id = current["scopus_researcher_id"]
    scholar_id = current["scholar_researcher_id"]
    cur.execute("""
        SELECT
          (SELECT COUNT(*) FROM researcher_external_profiles WHERE researcher_id=%s),
          (SELECT COUNT(*) FROM researcher_external_profiles WHERE researcher_id=%s),
          (SELECT COUNT(*) FROM researcher_metric_snapshots WHERE researcher_id=%s),
          (SELECT COUNT(*) FROM researcher_metric_snapshots WHERE researcher_id=%s),
          (SELECT COUNT(*) FROM researcher_affiliations WHERE researcher_id=%s),
          (SELECT COUNT(*) FROM researcher_affiliations WHERE researcher_id=%s),
          (SELECT COUNT(*) FROM researcher_match_candidates
            WHERE scopus_profile_id=%s OR scholar_profile_id=%s),
          (SELECT COUNT(*) FROM researcher_match_candidates
            WHERE scopus_profile_id=%s OR scholar_profile_id=%s),
          (SELECT COUNT(*) FROM researcher_affiliations
            WHERE researcher_id=%s AND organization_id=%s AND source='scopus'),
          (SELECT COUNT(*) FROM researcher_affiliations
            WHERE researcher_id=%s AND organization_id=%s AND source='scholar')
    """, (
        scopus_id, scholar_id, scopus_id, scholar_id, scopus_id, scholar_id,
        current["scopus_profile_id"], current["scopus_profile_id"],
        current["scholar_profile_id"], current["scholar_profile_id"],
        scopus_id, current["organization_id"], scholar_id, current["organization_id"],
    ))
    aggregate = cur.fetchone()
    if aggregate != (1, 1, 1, 1, 1, 1, 1, 1, 1, 1):
        raise RuntimeError(f"Candidate {candidate_id} child-count invariant failed: {aggregate}")

    proposed_orcid = expected.get("proposed_orcid") or None
    if proposed_orcid:
        external_count = scalar(
            cur, "SELECT COUNT(*) FROM researcher_external_profiles WHERE orcid=%s", (proposed_orcid,)
        )
        canonical_count = scalar(
            cur, "SELECT COUNT(*) FROM canonical_researchers WHERE orcid=%s", (proposed_orcid,)
        )
        if external_count != 1 or canonical_count != 0:
            raise RuntimeError(
                f"Candidate {candidate_id} ORCID invariant failed: external={external_count}, canonical={canonical_count}"
            )


def merge_one(cur, row, manifest_sha):
    survivor = int(row["survivor_researcher_id"])
    retired = int(row["retired_researcher_id"])
    display_name = row["canonical_display_name"].strip()
    normalized_display = normalize_name(display_name)
    proposed_orcid = row.get("proposed_orcid") or None

    cur.execute("""
        INSERT INTO researcher_aliases
          (researcher_id, alias, normalized_alias, language, source)
        SELECT %s, alias, normalized_alias, language, source
        FROM researcher_aliases
        WHERE researcher_id=%s
        ON CONFLICT (researcher_id, normalized_alias) DO NOTHING
    """, (survivor, retired))
    cur.execute("DELETE FROM researcher_aliases WHERE researcher_id=%s", (retired,))

    for alias in (row["scopus_name"], row["scholar_name"], display_name):
        alias = alias.strip()
        cur.execute("""
            INSERT INTO researcher_aliases
              (researcher_id, alias, normalized_alias, language, source)
            VALUES (%s,%s,%s,NULL,'canonical_merge')
            ON CONFLICT (researcher_id, normalized_alias) DO NOTHING
        """, (survivor, alias, normalize_name(alias)))

    cur.execute(
        "UPDATE researcher_external_profiles SET researcher_id=%s, updated_at=NOW() WHERE researcher_id=%s",
        (survivor, retired),
    )
    cur.execute(
        "UPDATE researcher_metric_snapshots SET researcher_id=%s WHERE researcher_id=%s",
        (survivor, retired),
    )
    cur.execute(
        "UPDATE researcher_affiliations SET researcher_id=%s WHERE researcher_id=%s",
        (survivor, retired),
    )
    if scalar(cur, "SELECT to_regclass('public.h_index_scholars')") is not None:
        cur.execute(
            "UPDATE h_index_scholars SET canonical_researcher_id=%s WHERE canonical_researcher_id=%s",
            (survivor, retired),
        )

    cur.execute("""
        UPDATE canonical_researchers
        SET display_name=%s,
            normalized_name=%s,
            orcid=COALESCE(%s, orcid),
            canonical_status='reviewed',
            updated_at=NOW()
        WHERE id=%s
    """, (display_name, normalized_display, proposed_orcid, survivor))
    if cur.rowcount != 1:
        raise RuntimeError(f"Survivor canonical {survivor} update failed")

    merge_audit = {
        "manifest_sha256": manifest_sha,
        "survivor_researcher_id": survivor,
        "retired_researcher_id": retired,
        "canonical_display_name": display_name,
        "orcid_applied": proposed_orcid,
        "approval_source": "user_confirmation_in_codex_task",
    }
    cur.execute("""
        UPDATE researcher_match_candidates
        SET review_status='approved',
            reviewed_at=NOW(),
            signals=signals || jsonb_build_object('canonical_merge', %s::jsonb)
        WHERE id=%s AND review_status='pending'
    """, (json.dumps(merge_audit, ensure_ascii=False), row["candidate_id"]))
    if cur.rowcount != 1:
        raise RuntimeError(f"Candidate {row['candidate_id']} approval update failed")

    cur.execute("DELETE FROM canonical_researchers WHERE id=%s", (retired,))
    if cur.rowcount != 1:
        raise RuntimeError(f"Retired canonical {retired} delete failed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    manifest_bytes = args.manifest.read_bytes()
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    manifest = json.loads(manifest_bytes)
    rows = manifest["rows"]
    if manifest.get("approval_scope") != "tier_a_batch_review_exact":
        raise SystemExit("Unexpected approval scope")
    if len(rows) != 324 or any(row.get("reviewer_decision") != "approve_merge" for row in rows):
        raise SystemExit("Manifest must contain exactly 324 approved rows")
    candidate_ids = [int(row["candidate_id"]) for row in rows]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise SystemExit("Duplicate candidate IDs in manifest")

    conn = get_connection()
    report = None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"tier-a-canonical:{manifest_sha}",))
            before = counts(cur)
            states = [(row, candidate_state(cur, row["candidate_id"])) for row in rows]
            already_merged = [
                row for row, state in states
                if state["review_status"] == "approved"
                and state["scopus_researcher_id"] == state["scholar_researcher_id"] == row["survivor_researcher_id"]
            ]
            if already_merged and len(already_merged) != 324:
                raise RuntimeError(f"Mixed merge state detected: {len(already_merged)} of 324 already merged")
            if len(already_merged) == 324:
                report = {
                    "ok": True, "skipped": True, "applied": False,
                    "manifest_sha256": manifest_sha, "merged_candidates": 324,
                    "before": before, "after": before,
                }
                conn.rollback()
            else:
                expected_before = manifest["expected"]
                baseline_checks = {
                    "canonical_researchers": expected_before["canonical_count_before"],
                    "external_profiles": expected_before["external_profile_count_before"],
                    "metric_snapshots": expected_before["metric_count_before"],
                    "affiliations": expected_before["affiliation_count_before"],
                    "approved_candidates": 0,
                    "pending_candidates": 540,
                }
                for key, expected_value in baseline_checks.items():
                    if before[key] != expected_value:
                        raise RuntimeError(f"Baseline changed for {key}: expected={expected_value}, actual={before[key]}")
                for row, state in states:
                    validate_pending(cur, row, state)

                if not args.apply:
                    report = {
                        "ok": True, "skipped": False, "applied": False,
                        "manifest_sha256": manifest_sha, "validated_candidates": 324,
                        "before": before, "database_writes": 0,
                    }
                    conn.rollback()
                else:
                    for row, _state in states:
                        merge_one(cur, row, manifest_sha)
                    after = counts(cur)
                    expected_after = {
                        "canonical_researchers": expected_before["canonical_count_after"],
                        "external_profiles": expected_before["external_profile_count_after"],
                        "scopus_profiles": before["scopus_profiles"],
                        "scholar_profiles": before["scholar_profiles"],
                        "metric_snapshots": expected_before["metric_count_after"],
                        "affiliations": expected_before["affiliation_count_after"],
                        "approved_candidates": expected_before["approved_candidates_after"],
                        "pending_candidates": expected_before["pending_candidates_after"],
                    }
                    if after != expected_after:
                        raise RuntimeError(f"Final count mismatch: expected={expected_after}, actual={after}")
                    merged_pairs = scalar(cur, """
                        SELECT COUNT(*)
                        FROM researcher_match_candidates mc
                        JOIN researcher_external_profiles sp ON sp.id=mc.scopus_profile_id
                        JOIN researcher_external_profiles hp ON hp.id=mc.scholar_profile_id
                        WHERE mc.id=ANY(%s) AND mc.review_status='approved'
                          AND sp.researcher_id=hp.researcher_id
                    """, (candidate_ids,))
                    if merged_pairs != 324:
                        raise RuntimeError(f"Merged pair verification failed: {merged_pairs}")
                    orphan_metrics = scalar(cur, """
                        SELECT COUNT(*) FROM researcher_metric_snapshots m
                        LEFT JOIN canonical_researchers c ON c.id=m.researcher_id
                        LEFT JOIN researcher_external_profiles p ON p.id=m.external_profile_id
                        WHERE c.id IS NULL OR p.id IS NULL
                    """)
                    orphan_affiliations = scalar(cur, """
                        SELECT COUNT(*) FROM researcher_affiliations a
                        LEFT JOIN canonical_researchers c ON c.id=a.researcher_id
                        LEFT JOIN research_organizations o ON o.id=a.organization_id
                        WHERE c.id IS NULL OR o.id IS NULL
                    """)
                    if orphan_metrics or orphan_affiliations:
                        raise RuntimeError(
                            f"Orphan verification failed: metrics={orphan_metrics}, affiliations={orphan_affiliations}"
                        )
                    report = {
                        "ok": True, "skipped": False, "applied": True,
                        "manifest_sha256": manifest_sha, "merged_candidates": 324,
                        "orphan_metrics": orphan_metrics,
                        "orphan_affiliations": orphan_affiliations,
                        "before": before, "after": after,
                    }
                    conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
