#!/usr/bin/env python3
"""Undo a batch of Data Quality auto-fixes from the fix log.

    python scripts/rollback_data_quality_fixes.py --run-id 42            # dry-run
    python scripts/rollback_data_quality_fixes.py --run-id 42 --confirm  # revert

``--run-id`` is the FIX run printed by apply_data_quality_fixes.py (the
data_quality_runs row with run_type='fix'), i.e. one batch of applied fixes.

Guarantees
    * DRY-RUN IS THE DEFAULT. Without --confirm nothing is written; the run only
      prints what it would restore.
    * Fixes are replayed NEWEST FIRST (reverse application order), so a field
      fixed more than once ends up back at its original value.
    * SAFETY CHECK per row: the field's CURRENT value must still equal the
      logged value_after. If it does not, somebody (or another job) changed the
      field after the fix — that row is SKIPPED with a warning and left for a
      human, never blindly overwritten.
    * Each restore is one transaction: the UPDATE, the rolled_back_at stamp and
      the reopening of the originating issue commit together or not at all.
    * Already-reverted rows are ignored, so re-running is safe and idempotent.
    * Rollback is deliberately NOT gated by DATA_QUALITY_AUTO_FIX_ENABLED: you
      must be able to undo fixes after switching auto-fix back off.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_quality import repository as repo  # noqa: E402
from data_quality.cli_common import resolve_connection, truncate  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Roll back the fixes applied by one Data Quality fix run")
    p.add_argument("--run-id", type=int, required=True,
                   help="Fix run id printed by apply_data_quality_fixes.py")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True,
                   help="Show what would be restored, write nothing (default)")
    g.add_argument("--confirm", action="store_true",
                   help="Actually restore the previous values")
    p.add_argument("--limit", type=int, default=None,
                   help="Only consider the N most recent fixes of the run")
    p.add_argument("--database-url", default=None)
    return p


def _as_text(value):
    """Compare against the TEXT snapshots in the log, keeping NULL distinct
    from the empty string and from the literal 'None'."""
    return None if value is None else str(value)


def _display(value) -> str:
    """Terminal rendering that does not make NULL look like an empty string."""
    return "NULL" if value is None else repr(truncate(value))


def _require_manual_transactions(conn) -> None:
    if getattr(conn, "autocommit", False):
        try:
            conn.autocommit = False
        except Exception:
            raise SystemExit("Refusing to restore: connection is in autocommit "
                             "mode (restore + rolled_back_at could not be atomic).")


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    confirm = bool(args.confirm)
    conn, target = resolve_connection(args.database_url)
    print(f"→ Target database: {target}   "
          f"mode={'RESTORE' if confirm else 'DRY-RUN'}   run_id={args.run_id}")
    if not confirm:
        print("  (dry-run: this run writes NOTHING to the database)")
    else:
        _require_manual_transactions(conn)

    restored = skipped = mismatched = considered = eligible = 0
    try:
        fixes = repo.fetch_fixes_by_run(conn, args.run_id, limit=args.limit)
        if not fixes:
            print("  nothing to roll back (no un-reverted fixes for this run)")

        for fix in fixes:
            considered += 1
            column, table = fix["column_name"], fix["table_name"]
            try:
                found, current = repo.fetch_current_value(
                    conn, fix["record_id"], column, table_name=table)
            except ValueError as exc:  # not on the fixer allowlist any more
                logging.warning("fix %s skipped: %s", fix["id"], exc)
                skipped += 1
                continue

            if not found:
                logging.warning("fix %s skipped: %s record %s no longer exists",
                                fix["id"], table, fix["record_id"])
                skipped += 1
                continue

            if _as_text(current) != _as_text(fix["value_after"]):
                # Changed by hand (or by another job) since the fix — the logged
                # "before" is no longer the right thing to restore.
                logging.warning(
                    "fix %s skipped: %s.%s of record %s is now %s, expected %s "
                    "— changed outside the fixer, restore it manually",
                    fix["id"], table, column, fix["record_id"],
                    _display(current), _display(fix["value_after"]))
                mismatched += 1
                continue

            eligible += 1
            print(f"  [{fix['record_id']}] {column}: "
                  f"{_display(fix['value_after'])} -> {_display(fix['value_before'])}"
                  f"   (fix #{fix['id']}, {fix['issue_code'] or '-'})")

            if confirm:
                try:
                    affected = repo.update_record_field(
                        conn, fix["record_id"], column, fix["value_before"],
                        table_name=table)
                    if affected != 1:
                        raise RuntimeError(f"affected {affected} rows, expected 1")
                    if repo.mark_rolled_back(conn, [fix["id"]]) != 1:
                        raise RuntimeError("fix was already marked rolled back")
                    if fix["issue_id"] is not None:
                        # The issue is no longer 'fixed' — put it back in the queue.
                        repo.resolve_issue(conn, fix["issue_id"], None, status="open")
                    conn.commit()
                    restored += 1
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    logging.error("rollback failed for fix %s (record %s): %s",
                                  fix["id"], fix["record_id"], exc.__class__.__name__)
                    skipped += 1
    finally:
        try:
            conn.close()
        except Exception:
            pass

    print("\n" + "=" * 50)
    print(f"considered : {considered}")
    print(f"{'restored' if confirm else 'would restore'} : "
          f"{restored if confirm else eligible}")
    print(f"mismatched : {mismatched}   (changed since the fix — left untouched)")
    print(f"skipped    : {skipped}")
    if not confirm:
        print("\n(no changes written — re-run with --confirm to restore)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
