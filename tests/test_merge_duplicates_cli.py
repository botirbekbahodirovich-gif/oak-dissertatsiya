"""Tests for scripts/merge_duplicates.py's report/CSV/argument logic — pure,
no DB. The transactional merge itself (repo.merge_dissertations) is covered by
tests/test_merge_duplicates_dbgated.py, which needs a real staging schema.

The load-bearing case here is the three-way outcome classification: a
candidate refused by the guard (never touched), a candidate that merged, and a
candidate that PASSED planning but failed mid-write — that third case must
never be silently reported as either "merged" or merely "planned".
"""
import csv
import io
import os
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))

import merge_duplicates as MD  # noqa: E402
from data_quality import merge_review as R  # noqa: E402
from data_quality import repository as repo  # noqa: E402


def merged_outcome(candidate_id=1, winner=10, loser=20, fields=("mavzu",),
                   **result_kw):
    plan = R.MergePlan(candidate_id=candidate_id, winner_id=winner, loser_id=loser,
                       reason="more complete",
                       fills=[R.FieldFill(f, None, f"v-{f}") for f in fields])
    o = MD.Outcome(candidate_id)
    o.plan = plan
    o.decision = R.MergeDecision(True)
    o.result = repo.MergeResult(candidate_id=candidate_id, winner_id=winner,
                                loser_id=loser, fields_filled=len(fields), **result_kw)
    return o


def skipped_outcome(candidate_id=2, reason="candidate status is pending"):
    o = MD.Outcome(candidate_id)
    o.decision = R.MergeDecision(False, reason)
    return o


def failed_outcome(candidate_id=3, winner=30, loser=40, error="IntegrityError: boom"):
    plan = R.MergePlan(candidate_id=candidate_id, winner_id=winner, loser_id=loser,
                       reason="tie — smaller id", fills=[])
    o = MD.Outcome(candidate_id)
    o.plan = plan
    o.decision = R.MergeDecision(True)
    o.error = error
    return o


def planned_outcome(candidate_id=4, winner=50, loser=60):
    plan = R.MergePlan(candidate_id=candidate_id, winner_id=winner, loser_id=loser,
                       reason="more complete", fills=[])
    o = MD.Outcome(candidate_id)
    o.plan = plan
    o.decision = R.MergeDecision(True)
    return o


class OutcomeTests(unittest.TestCase):
    def test_merged_outcome_reports_no_skip_reason(self):
        self.assertTrue(merged_outcome().merged)
        self.assertIsNone(merged_outcome().skipped_reason)

    def test_guard_skip_reports_decision_reason(self):
        o = skipped_outcome(reason="already merged elsewhere")
        self.assertFalse(o.merged)
        self.assertEqual(o.skipped_reason, "already merged elsewhere")

    def test_execution_error_takes_priority_over_decision(self):
        """A candidate can have BOTH a passing decision and a later error —
        the error is what the operator needs to see."""
        o = failed_outcome(error="boom")
        self.assertFalse(o.merged)
        self.assertEqual(o.skipped_reason, "boom")


class ReportClassificationTests(unittest.TestCase):
    """The bug this guards against: a candidate that PASSED planning but
    FAILED while writing must show up as failed, not silently as "merged"
    (blank counts) or as "planned" (implying it never even tried)."""

    def test_confirm_mode_merged_and_failed_and_skipped_are_distinct(self):
        merged = merged_outcome(1)
        skipped = skipped_outcome(2)
        failed = failed_outcome(3)
        md = MD.build_report([merged, skipped, failed], dry_run=False,
                             target="host/db", run_id=5)

        self.assertIn("birlashtirildi: 1", md)
        self.assertIn("o‘tkazib yuborildi: 2", md)
        # The failed candidate's real error must be visible, not swallowed.
        self.assertIn("IntegrityError: boom", md)
        # And it must NOT appear as a row in the merged table (which would
        # imply zero-effect success rather than failure).
        merged_section = md.split("## O‘tkazib yuborilganlar")[0]
        self.assertNotIn("| 3 |", merged_section)

    def test_dry_run_counts_plannable_candidates_not_result(self):
        """Regression: dry-run never sets .result, so the summary line must
        not be derived from o.merged (which would always read zero)."""
        planned = planned_outcome(1)
        skipped = skipped_outcome(2)
        md = MD.build_report([planned, skipped], dry_run=True,
                             target="host/db", run_id=None)
        self.assertIn("birlashtirilishi mumkin: 1", md)
        self.assertIn("o‘tkazib yuborildi: 1", md)

    def test_dry_run_report_never_claims_data_was_written(self):
        md = MD.build_report([planned_outcome()], dry_run=True,
                             target="host/db", run_id=None)
        self.assertIn("DRY RUN", md)
        self.assertIn("hech narsa yozilmadi", md)

    def test_confirm_report_does_not_say_dry_run(self):
        md = MD.build_report([merged_outcome()], dry_run=False,
                             target="host/db", run_id=1)
        self.assertNotIn("DRY RUN", md)

    def test_empty_outcome_list_does_not_crash(self):
        md = MD.build_report([], dry_run=True, target="host/db", run_id=None)
        self.assertIn("candidates ko‘rilgan: 0", md)

    def test_run_id_only_shown_when_present(self):
        md_none = MD.build_report([], dry_run=True, target="host/db", run_id=None)
        md_set = MD.build_report([], dry_run=False, target="host/db", run_id=42)
        self.assertNotIn("data_quality_runs.id", md_none)
        self.assertIn("data_quality_runs.id` = 42", md_set)

    def test_pipe_in_error_cannot_break_the_markdown_table(self):
        md = MD.build_report([failed_outcome(error="bad | value")], dry_run=False,
                             target="host/db", run_id=None)
        # truncate() escapes '|' the same way the rescrape report does.
        self.assertNotIn("| bad | value |", md)


class CsvTests(unittest.TestCase):
    def _rows(self, outcomes):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.csv")
            MD.write_csv(path, outcomes)
            with open(path, encoding="utf-8") as f:
                return list(csv.DictReader(f))

    def test_one_row_per_outcome_type(self):
        rows = self._rows([merged_outcome(1), skipped_outcome(2), failed_outcome(3),
                           planned_outcome(4)])
        statuses = {r["candidate_id"]: r["status"] for r in rows}
        self.assertEqual(statuses, {"1": "merged", "2": "skipped",
                                    "3": "failed", "4": "planned"})

    def test_failed_row_carries_the_error_not_the_plan_reason(self):
        rows = self._rows([failed_outcome(3, error="IntegrityError: boom")])
        self.assertEqual(rows[0]["reason"], "IntegrityError: boom")

    def test_merged_row_carries_fk_counts(self):
        o = merged_outcome(1, bookmarks_repointed=2, bookmarks_dropped=1,
                           subscriptions_repointed=3, duplicate_rows_repointed=1,
                           duplicate_rows_dropped=0)
        rows = self._rows([o])
        self.assertEqual(rows[0]["bookmarks_repointed"], "2")
        self.assertEqual(rows[0]["bookmarks_dropped"], "1")


class ArgumentValidationTests(unittest.TestCase):
    """These never reach resolve_connection() — no DB is touched."""

    def test_winner_without_candidate_id_is_rejected(self):
        buf = io.StringIO()
        real_stdout = sys.stdout
        sys.stdout = buf
        try:
            code = MD.main(["--winner", "5"])
        finally:
            sys.stdout = real_stdout
        self.assertEqual(code, 2)
        self.assertIn("--candidate-id", buf.getvalue())

    def test_winner_with_candidate_id_passes_argument_parsing(self):
        """Cannot go further without a DB, but must not fail at the arg-parse
        stage the way the bare --winner case does."""
        parser = MD._build_parser()
        args = parser.parse_args(["--candidate-id", "9", "--winner", "5"])
        self.assertEqual(args.candidate_id, 9)
        self.assertEqual(args.winner, 5)

    def test_confirm_defaults_to_false(self):
        args = MD._build_parser().parse_args([])
        self.assertFalse(args.confirm)


if __name__ == "__main__":
    unittest.main()
