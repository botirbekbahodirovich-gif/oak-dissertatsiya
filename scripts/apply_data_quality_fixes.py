#!/usr/bin/env python3
"""Safe, two-stage Data Quality fixer.

    python scripts/apply_data_quality_fixes.py --preview        # or --dry-run
    python scripts/apply_data_quality_fixes.py --apply --issue-code NORMALIZE_DEGREE
    python scripts/apply_data_quality_fixes.py --apply --run-id 12
    python scripts/apply_data_quality_fixes.py --apply --record-id 123

Guarantees
    * DEFAULT IS PREVIEW (``--dry-run`` is an alias). Without --apply nothing is
      ever written — no fix, no fix-log row, no run row.
    * Only issue codes on data_quality.models.AUTO_FIX_ALLOWLIST are touched, and
      only if the target column is on AUTO_FIX_SAFE_COLUMNS (never title/author/
      org/supervisor — those stay human-review only).
    * The new value is RECOMPUTED from the current DB value at apply time
      (idempotent + concurrency-safe), not blindly taken from an old scan.
    * Every fix runs in ONE transaction that contains, in this order:
          1. the data_quality_fix_log row (before/after snapshot, written FIRST)
          2. the UPDATE itself, with the affected-row count verified (== 1)
          3. the issue audit (old + new value) and its 'fixed' status
      Any failure rolls the whole thing back, so there is never a fix without
      its log row, nor a log row without its fix.
    * All fixes of one invocation share a 'fix' run in data_quality_runs; that
      run id is the unit of rollback:
          python scripts/rollback_data_quality_fixes.py --run-id <id>
    * Duplicates are NEVER auto-deleted or merged here.

The public issue-code aliases accepted by --issue-code map to the internal
INCONSISTENT_* codes:
    NORMALIZE_DEGREE     -> INCONSISTENT_DEGREE
    NORMALIZE_WHITESPACE -> INCONSISTENT_WHITESPACE
    NORMALIZE_APOSTROPHE -> INCONSISTENT_APOSTROPHE
    NORMALIZE_URL        -> INCONSISTENT_URL
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_quality import normalizers as N  # noqa: E402
from data_quality import repository as repo  # noqa: E402
from data_quality.cli_common import (  # noqa: E402
    require_manual_transactions, resolve_connection, truncate)
from data_quality.config import get_config  # noqa: E402
from data_quality.models import (  # noqa: E402
    AUTO_FIX_ALLOWLIST, AUTO_FIX_SAFE_COLUMNS, FIX_TARGETS,
    INCONSISTENT_APOSTROPHE, INCONSISTENT_DEGREE, INCONSISTENT_URL,
    INCONSISTENT_WHITESPACE)

# normalizer-name (from FIX_TARGETS) -> callable
_NORMALIZERS = {
    "normalize_degree": N.normalize_degree,
    "normalize_whitespace": N.normalize_whitespace,
    "normalize_apostrophes": N.normalize_apostrophes,
    "normalize_url": N.normalize_url,
}

# operator-facing alias -> internal issue code
_ALIAS = {
    "NORMALIZE_DEGREE": INCONSISTENT_DEGREE,
    "NORMALIZE_WHITESPACE": INCONSISTENT_WHITESPACE,
    "NORMALIZE_APOSTROPHE": INCONSISTENT_APOSTROPHE,
    "NORMALIZE_URL": INCONSISTENT_URL,
}


FIX_TABLE = "dissertations"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Apply safe, allowlisted DQ fixes")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--preview", action="store_true", help="Show fixes, write nothing (default)")
    g.add_argument("--dry-run", action="store_true", dest="preview",
                   help="Alias for --preview")
    g.add_argument("--apply", action="store_true", help="Actually write the fixes")
    p.add_argument("--issue-code", default=None,
                   help="Alias (NORMALIZE_*) or internal INCONSISTENT_* code")
    p.add_argument("--run-id", type=int, default=None,
                   help="Only fix issues found by this SCAN run")
    p.add_argument("--record-id", type=int, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--applied-by", default=None,
                   help="Actor label stored in the fix log (default: cli:<user>)")
    p.add_argument("--database-url", default=None)
    return p


def _default_actor() -> str:
    try:
        return f"cli:{getpass.getuser()}"
    except Exception:  # no login name available (cron/container)
        return "cli"




def _resolve_code(raw: str | None) -> str | None:
    if not raw:
        return None
    return _ALIAS.get(raw.upper(), raw)


def _target_column(issue: dict) -> str | None:
    code = issue["issue_code"]
    if code not in FIX_TARGETS:
        return None
    column, _ = FIX_TARGETS[code]
    if column == "__field__":
        column = issue["field_name"]
    return column


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = get_config()

    apply = bool(args.apply)
    if apply and not config.auto_fix_enabled:
        print("Auto-fix is disabled (DATA_QUALITY_AUTO_FIX_ENABLED=0). Refusing --apply.")
        return 2

    issue_code = _resolve_code(args.issue_code)
    actor = (args.applied_by or _default_actor())
    conn, target = resolve_connection(args.database_url)
    print(f"→ Target database: {target}   mode={'APPLY' if apply else 'PREVIEW'}")
    if not apply:
        print("  (preview: this run writes NOTHING to the database)")
    else:
        require_manual_transactions(conn, "apply")

    fixed = skipped = considered = 0
    # Lazily created on the first real fix, inside that fix's transaction, so a
    # run with nothing to do leaves no empty run row behind.
    fix_run_id = None
    run_uncommitted = False
    try:
        issues = repo.iter_fixable_issues(
            conn, sorted(AUTO_FIX_ALLOWLIST), issue_code=issue_code,
            run_id=args.run_id, record_id=args.record_id, limit=args.limit)

        for issue in issues:
            considered += 1
            column = _target_column(issue)
            if (issue["issue_code"] not in AUTO_FIX_ALLOWLIST
                    or column is None
                    or column not in AUTO_FIX_SAFE_COLUMNS):
                skipped += 1
                continue

            record = repo.fetch_record(conn, issue["record_id"])
            if not record or column not in record:
                skipped += 1
                continue

            old_value = record[column]
            normalizer = _NORMALIZERS[FIX_TARGETS[issue["issue_code"]][1]]
            new_value = normalizer(old_value)
            if new_value == old_value or new_value is None:
                # Already normalized (or nothing to do) -> close the issue, no
                # write to the record, so nothing to log or roll back either.
                if apply:
                    repo.resolve_issue(conn, issue["id"], None, status="resolved")
                    conn.commit()
                skipped += 1
                continue

            print(f"  [{issue['record_id']}] {column}: "
                  f"{truncate(old_value)!r} -> {truncate(new_value)!r}")

            if apply:
                try:
                    if fix_run_id is None:
                        fix_run_id = repo.create_run(
                            conn, "fix", total_records=len(issues),
                            metadata={"mode": "apply", "target": target,
                                      "issue_code": issue_code,
                                      "scan_run_id": args.run_id,
                                      "record_id": args.record_id,
                                      "applied_by": actor})
                        run_uncommitted = True
                    # Audit FIRST, in the same transaction as the UPDATE: if the
                    # UPDATE fails, the log row dies with it (no half state).
                    repo.log_fix(conn, run_id=fix_run_id, issue_id=issue["id"],
                                 record_id=issue["record_id"],
                                 table_name=FIX_TABLE, column_name=column,
                                 value_before=old_value, value_after=new_value,
                                 issue_code=issue["issue_code"], applied_by=actor)
                    affected = repo.update_record_field(
                        conn, issue["record_id"], column, new_value,
                        table_name=FIX_TABLE)
                    if affected != 1:
                        raise RuntimeError(f"affected {affected} rows, expected 1")
                    # Pin the exact before/after onto the issue row too.
                    repo.record_issue_values(conn, issue["id"], old_value, new_value)
                    repo.mark_issue_fixed(conn, issue["id"])
                    conn.commit()
                    run_uncommitted = False
                    fixed += 1
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    if run_uncommitted:
                        # The run row died with the rolled-back transaction.
                        fix_run_id, run_uncommitted = None, False
                    logging.error("fix failed for record %s: %s",
                                  issue["record_id"], exc.__class__.__name__)
                    skipped += 1

        if apply and fix_run_id is not None:
            repo.complete_run(conn, fix_run_id, status="completed",
                              checked_records=considered, issue_count=fixed,
                              duplicate_count=0)
            conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    print("\n" + "=" * 50)
    print(f"considered : {considered}")
    print(f"{'applied' if apply else 'would apply'} : {fixed}")
    print(f"skipped    : {skipped}")
    if not apply:
        print("\n(no changes written — re-run with --apply to persist)")
    elif fix_run_id is not None:
        print(f"fix run id : {fix_run_id}")
        print(f"\nto undo:  python scripts/rollback_data_quality_fixes.py "
              f"--run-id {fix_run_id} --confirm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
