#!/usr/bin/env python3
"""Execute confirmed duplicate merges — THE MOST DANGEROUS operation in the
Data Quality subsystem. One bad merge on a 29 551-row production table is not
recoverable by re-running a scan.

    python scripts/merge_duplicates.py                    # dry run (default)
    python scripts/merge_duplicates.py --confirm           # actually writes
    python scripts/merge_duplicates.py --candidate-id 123 --confirm
    python scripts/merge_duplicates.py --candidate-id 123 --winner 456 --confirm

Safety
    * --dry-run is the DEFAULT. Nothing is written unless --confirm is passed.
      Dry-run computes and reports the exact same plan a --confirm run would
      execute — it is a preview, not an approximation.
    * Only status='confirmed' candidates are eligible (or a specific
      --candidate-id, which still enforces the same status check so a
      forgotten --confirm-in-review click cannot be merged by accident).
    * Every foreign key into dissertations(id) is repointed — see
      docs/data_quality_dissertation_fk_inventory.md for the full inventory —
      and a LIVE information_schema check refuses the whole run if the schema
      has grown a foreign key this script does not know about.
    * The losing record is NEVER deleted. It is marked merged_into_id, so it
      stays resolvable (and reversible) for anyone still holding a reference.
    * Each merge (field copy + every FK repoint + the retirement marker + the
      candidate's own status) is one database transaction: it all lands, or
      none of it does. One candidate failing does not affect the others in
      the same run — see the code comment at the top of the merge loop.
    * Every write is logged to data_quality_fix_log (record id, column,
      before, after) grouped under one data_quality_runs row (run_type='merge')
      for this invocation, so the run is auditable and a future rollback tool
      has a precise source to work from.
    * A record that already has merged_into_id set (on either side) refuses to
      merge again — this also rules out merge chains/cycles by construction.

Requires migration 004 (duplicate_candidates) AND migration 006
(dissertations.merged_into_id, data_quality_fix_log) already applied.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_quality import merge_review as review  # noqa: E402
from data_quality import repository as repo  # noqa: E402
from data_quality.cli_common import resolve_connection, truncate  # noqa: E402

DOCS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")

log = logging.getLogger("data_quality.merge")


def log_line(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _md_cell(value, limit: int = 200) -> str:
    """Markdown table cell — a bare `|` in a psycopg2 error message or a free-
    text field would otherwise split the row. truncate() only neutralises
    newlines/tabs, not pipes, so that escape happens here first."""
    s = truncate((value or "").replace("|", "\\|"), limit)
    return s or "—"


# ── preconditions ────────────────────────────────────────────────────────────
def _merge_support_ready(conn) -> bool:
    """True when migration 006 has been applied (merged_into_id + fix_log)."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT
                 (SELECT COUNT(*) FROM information_schema.columns
                   WHERE table_name = 'dissertations'
                     AND column_name = 'merged_into_id') = 1
                 AND
                 (SELECT COUNT(*) FROM information_schema.tables
                   WHERE table_name = 'data_quality_fix_log') = 1""")
        return bool(cur.fetchone()[0])


# ── one candidate, planned and (optionally) executed ────────────────────────
class Outcome:
    def __init__(self, candidate_id: int):
        self.candidate_id = candidate_id
        self.plan: Optional[review.MergePlan] = None
        self.decision: Optional[review.MergeDecision] = None
        self.result: Optional[repo.MergeResult] = None
        self.error: Optional[str] = None

    @property
    def merged(self) -> bool:
        return self.result is not None

    @property
    def skipped_reason(self) -> Optional[str]:
        if self.error:
            return self.error
        if self.decision is not None and not self.decision.ok:
            return self.decision.reason
        return None


def plan_one(conn, candidate: Dict, forced_winner: Optional[int]) -> Outcome:
    outcome = Outcome(candidate["id"])
    rows = repo.fetch_dissertation_rows(
        conn, (candidate["record_id_1"], candidate["record_id_2"]))
    a = rows.get(candidate["record_id_1"])
    b = rows.get(candidate["record_id_2"])
    if a is None or b is None:
        outcome.error = "one or both records are missing from dissertations"
        return outcome
    outcome.plan, outcome.decision = review.plan_merge(
        candidate, a, b, forced_winner_id=forced_winner)
    return outcome


def execute_one(conn, run_id: int, candidate_id: int,
                forced_winner: Optional[int], applied_by: str) -> Outcome:
    """Re-fetches the candidate AND both records fresh, under a row lock, right
    before writing. Earlier candidates in this same run may have repointed or
    retired one of this pair's records (see the merge loop's docstring) — a
    plan built from a batch snapshot taken at the start of the run could be
    stale by the time we get here, so nothing about this merge is decided
    until the data is locked."""
    fresh = repo.fetch_confirmed_candidates(conn, candidate_id=candidate_id)
    if not fresh:
        outcome = Outcome(candidate_id)
        outcome.error = "candidate no longer exists"
        return outcome
    candidate = fresh[0]

    rows = repo.fetch_dissertation_rows(
        conn, (candidate["record_id_1"], candidate["record_id_2"]),
        for_update=True)
    a = rows.get(candidate["record_id_1"])
    b = rows.get(candidate["record_id_2"])
    outcome = Outcome(candidate_id)
    if a is None or b is None:
        outcome.error = "one or both records are missing from dissertations"
        return outcome

    outcome.plan, outcome.decision = review.plan_merge(
        candidate, a, b, forced_winner_id=forced_winner)
    if not outcome.decision.ok:
        return outcome

    try:
        outcome.result = repo.merge_dissertations(
            conn, run_id, outcome.plan, applied_by)
    except Exception as exc:  # noqa: BLE001 — one candidate must not crash the run
        outcome.error = f"{exc.__class__.__name__}: {truncate(str(exc), 200)}"
    return outcome


# ── reporting ────────────────────────────────────────────────────────────────
def build_report(outcomes: List[Outcome], *, dry_run: bool, target: str,
                 run_id: Optional[int]) -> str:
    # Dry-run never sets .result (nothing was executed), so "would merge" is
    # counted from the plan itself; confirm counts what actually landed. A
    # candidate that PASSED planning but failed while writing (a mid-merge
    # exception, o.plan set but o.result not) belongs in "skipped" with its
    # error, never silently in the merged table with blank counts — that is
    # why this filters on o.merged / not o.merged rather than on o.plan.
    actionable = ([o for o in outcomes if o.plan is not None] if dry_run
                  else [o for o in outcomes if o.merged])
    skipped = [o for o in outcomes if o not in actionable and o.skipped_reason]
    lines = [
        f"# Duplicate merge {'preview (DRY RUN)' if dry_run else 'run'} — "
        f"{datetime.now():%Y-%m-%d %H:%M}",
        "",
        ("**DRY RUN — hech narsa yozilmadi.** Quyidagi reja `--confirm` bilan "
         "ishga tushirilganda aynan shu tarzda bajariladi."
         if dry_run else
         "**Yozildi.** Har bir yozuv `data_quality_fix_log` da qayd etildi."),
        "",
        f"* baza: `{target}`",
        f"* candidates ko‘rilgan: {len(outcomes)}",
        f"* {'birlashtirilishi mumkin' if dry_run else 'birlashtirildi'}: {len(actionable)}",
        f"* o‘tkazib yuborildi: {len(skipped)}",
    ]
    if run_id is not None:
        lines.append(f"* `data_quality_runs.id` = {run_id}")
    lines += ["", "## Birlashtirilganlar" if not dry_run else "## Reja", ""]

    if actionable:
        lines += ["| candidate | winner | loser | sabab | to‘ldirilgan maydonlar | "
                  "bookmark | obuna logi | boshqa juftliklar |",
                  "|---|---|---|---|---|---|---|---|"]
        for o in actionable:
            r = o.result
            fields = ", ".join(f.field for f in o.plan.fills) or "—"
            bm = f"{r.bookmarks_repointed} (+{r.bookmarks_dropped} tashlandi)" if r else "—"
            sub = str(r.subscriptions_repointed) if r else "—"
            dup = (f"{r.duplicate_rows_repointed} "
                  f"(+{r.duplicate_rows_dropped} tashlandi)") if r else "—"
            lines.append(
                f"| {o.candidate_id} | #{o.plan.winner_id} | #{o.plan.loser_id} | "
                f"{_md_cell(o.plan.reason)} | {_md_cell(fields)} | {bm} | {sub} | {dup} |")
    else:
        lines.append("Yo‘q.")

    lines += ["", "## O‘tkazib yuborilganlar", ""]
    if skipped:
        lines += ["| candidate | sabab |", "|---|---|"]
        for o in skipped:
            lines.append(f"| {o.candidate_id} | {_md_cell(o.skipped_reason)} |")
    else:
        lines.append("Yo‘q.")
    lines.append("")
    return "\n".join(lines)


def write_csv(path: str, outcomes: List[Outcome]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["candidate_id", "status", "winner_id", "loser_id", "reason",
                    "fields_filled", "bookmarks_repointed", "bookmarks_dropped",
                    "subscriptions_repointed", "duplicate_rows_repointed",
                    "duplicate_rows_dropped"])
        for o in outcomes:
            if o.merged:
                r = o.result
                w.writerow([o.candidate_id, "merged", o.plan.winner_id,
                           o.plan.loser_id, o.plan.reason, r.fields_filled,
                           r.bookmarks_repointed, r.bookmarks_dropped,
                           r.subscriptions_repointed, r.duplicate_rows_repointed,
                           r.duplicate_rows_dropped])
            elif o.error:
                # Passed planning, failed while writing (a mid-merge
                # exception) — never mislabel this as merely "planned"; the
                # whole point of this column is to catch exactly this.
                w.writerow([o.candidate_id, "failed",
                           o.plan.winner_id if o.plan else "",
                           o.plan.loser_id if o.plan else "", o.error,
                           len(o.plan.fills) if o.plan else "",
                           "", "", "", "", ""])
            elif o.plan is not None:   # dry-run: planned but not executed
                w.writerow([o.candidate_id, "planned", o.plan.winner_id,
                           o.plan.loser_id, o.plan.reason, len(o.plan.fills),
                           "", "", "", "", ""])
            else:
                w.writerow([o.candidate_id, "skipped", "", "",
                           o.skipped_reason or "", "", "", "", "", "", ""])


# ── CLI ─────────────────────────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Execute confirmed duplicate merges (dry-run by default)")
    p.add_argument("--confirm", action="store_true",
                   help="actually write; without this flag nothing is saved")
    p.add_argument("--candidate-id", type=int, default=None,
                   help="merge only this duplicate_candidates.id")
    p.add_argument("--winner", type=int, default=None,
                   help="force which record survives (requires --candidate-id)")
    p.add_argument("--limit", type=int, default=None,
                   help="cap how many confirmed candidates to attempt")
    p.add_argument("--applied-by", default=None,
                   help="actor label stored in data_quality_fix_log "
                        "(default: cli:<OS user>)")
    p.add_argument("--out-dir", default=DOCS_DIR, help="report output folder")
    p.add_argument("--database-url", default=None,
                   help="override DB URL (else DATA_QUALITY_/TEST_/DATABASE_URL)")
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.winner is not None and args.candidate_id is None:
        print("--winner requires --candidate-id (it overrides one specific pair).")
        return 2

    applied_by = args.applied_by or f"cli:{os.environ.get('USER') or os.environ.get('USERNAME') or 'merge_duplicates'}"
    conn, target = resolve_connection(args.database_url)
    mode = "CONFIRM — WRITES" if args.confirm else "DRY RUN — no writes"
    print(f"→ Baza: {target}   ({mode})")

    try:
        if not repo.dq_tables_exist(conn):
            print("Migration 004 qo'llanmagan (duplicate_candidates yo'q). To'xtatildi.")
            return 2
        if not _merge_support_ready(conn):
            print("Migration 006 qo'llanmagan (merged_into_id / data_quality_fix_log "
                 "yo'q). To'xtatildi: psql \"$DATABASE_URL\" -f "
                 "migrations/006_dissertation_merge_support.sql")
            return 2

        # Fatal in BOTH modes: a dry-run plan that could never be safely
        # applied is not a useful preview.
        try:
            repo.assert_no_unknown_fk_references(conn)
        except RuntimeError as exc:
            print(str(exc))
            return 3
        finally:
            conn.rollback()   # information_schema reads only; close the tx

        candidates = repo.fetch_confirmed_candidates(
            conn, candidate_id=args.candidate_id, limit=args.limit)
        conn.rollback()
        if not candidates:
            print("Hech qanday 'confirmed' nomzod topilmadi.")
            return 0
        log_line(f"{len(candidates)} ta nomzod ko'rib chiqiladi")

        run_id = None
        if args.confirm:
            run_id = repo.create_run(
                conn, "merge", len(candidates),
                metadata={"candidate_id": args.candidate_id, "winner": args.winner,
                          "limit": args.limit, "applied_by": applied_by})
            conn.commit()

        outcomes: List[Outcome] = []
        merged_count = 0
        for i, candidate in enumerate(candidates, 1):
            if args.confirm:
                # Each candidate is its OWN transaction: on success it commits
                # here immediately, so a later candidate in this same run can
                # never see (or corrupt) a half-applied earlier merge; on
                # failure it rolls back and the run continues — one bad pair
                # must not block every other confirmed pair behind it.
                outcome = execute_one(conn, run_id, candidate["id"],
                                      args.winner if args.candidate_id else None,
                                      applied_by)
                if outcome.merged:
                    conn.commit()
                    merged_count += 1
                    log_line(f"  [{i}/{len(candidates)}] #{candidate['id']} "
                             f"merged: winner=#{outcome.plan.winner_id} "
                             f"loser=#{outcome.plan.loser_id} "
                             f"({len(outcome.plan.fills)} maydon to'ldirildi)")
                else:
                    conn.rollback()
                    log_line(f"  [{i}/{len(candidates)}] #{candidate['id']} "
                             f"o'tkazib yuborildi: {outcome.skipped_reason}")
            else:
                outcome = plan_one(conn, candidate,
                                   args.winner if args.candidate_id else None)
                conn.rollback()
                if outcome.decision and outcome.decision.ok:
                    log_line(f"  [{i}/{len(candidates)}] #{candidate['id']} reja: "
                             f"winner=#{outcome.plan.winner_id} "
                             f"loser=#{outcome.plan.loser_id} "
                             f"({len(outcome.plan.fills)} maydon)")
                else:
                    reason = outcome.skipped_reason or "?"
                    log_line(f"  [{i}/{len(candidates)}] #{candidate['id']} "
                             f"o'tkazib yuborildi: {reason}")
            outcomes.append(outcome)

        if args.confirm and run_id is not None:
            repo.complete_run(conn, run_id, status="completed",
                              checked_records=len(candidates),
                              issue_count=0, duplicate_count=merged_count)
            conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    os.makedirs(args.out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    md_path = os.path.join(args.out_dir, f"merge_report_{stamp}.md")
    csv_path = os.path.join(args.out_dir, f"merge_report_{stamp}.csv")
    with open(md_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(build_report(outcomes, dry_run=not args.confirm, target=target,
                             run_id=run_id))
    write_csv(csv_path, outcomes)

    log_line(f"Tayyor: {merged_count if args.confirm else sum(1 for o in outcomes if o.decision and o.decision.ok)} "
             f"ta {'birlashtirildi' if args.confirm else 'birlashtirilishi mumkin'}, "
             f"{sum(1 for o in outcomes if o.skipped_reason)} ta o'tkazib yuborildi")
    print(f"\n  {md_path}\n  {csv_path}")
    if not args.confirm:
        print("\n(DRY RUN — hech narsa yozilmadi. Yozish uchun --confirm qo'shing.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
