"""Tests for the rescrape WRITE path — scripts/rescrape_broken_records.py --apply.

Offline by design: no socket, no database. The repository is replaced by a
recorder, so every assertion is about what the apply path *would* send to the
DB and in which order.

Covered: only FILL is ever written, CONFLICT is never written, the fix-log row
precedes its UPDATE, one transaction per record (no half-filled record), the
re-check that refuses to overwrite a field somebody filled meanwhile, and that
rollback handles a rescrape run through the same code path as a fixer run.

    python3 -m unittest tests.test_rescrape_apply
"""
from __future__ import annotations

import importlib.util
import io
import os
import sys
import unittest
from contextlib import redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))

import rescrape_broken_records as R  # noqa: E402

from data_quality import models as M  # noqa: E402
from data_quality import repository as repo_real  # noqa: E402
from tests.test_data_quality_fix_log import _FakeConn  # noqa: E402


def _load_script(name):
    path = os.path.join(_REPO_ROOT, "scripts", f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_script_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── recorder ────────────────────────────────────────────────────────────────
class _RepoRecorder:
    """Stand-in for data_quality.repository: records calls, enforces nothing."""

    def __init__(self, current=None, affected=1):
        self.calls = []
        # {(record_id, column): (found, value)} — default: still empty
        self.current = current or {}
        self.affected = affected
        self.fail_on = None
        self.next_run_id = 77
        self.SCOPE_RESCRAPE = repo_real.SCOPE_RESCRAPE
        self.SCOPE_ROLLBACK = repo_real.SCOPE_ROLLBACK

    def _note(self, name, **kw):
        self.calls.append((name, kw))
        if self.fail_on == name:
            raise RuntimeError("boom")

    def create_run(self, conn, run_type, total_records=0, metadata=None):
        self._note("create_run", run_type=run_type, metadata=metadata or {})
        return self.next_run_id

    def complete_run(self, conn, run_id, **kw):
        self._note("complete_run", run_id=run_id, **kw)

    def fetch_current_value(self, conn, record_id, column,
                            table_name="dissertations", scope=None):
        self._note("fetch_current_value", record_id=record_id, column=column,
                   scope=scope)
        return self.current.get((record_id, column), (True, ""))

    def log_fix(self, conn, **kw):
        self._note("log_fix", **kw)
        return len([c for c, _ in self.calls if c == "log_fix"])

    def update_record_field(self, conn, record_id, column, value,
                            table_name="dissertations", scope=None):
        self._note("update_record_field", record_id=record_id, column=column,
                   value=value, scope=scope)
        return self.affected

    def record_issue_values(self, conn, issue_id, original_value, normalized_value):
        self._note("record_issue_values", issue_id=issue_id)

    def mark_issue_fixed(self, conn, issue_id, resolved_by=None):
        self._note("mark_issue_fixed", issue_id=issue_id)

    def names(self):
        return [c for c, _ in self.calls]

    def kwargs_of(self, name):
        return [kw for c, kw in self.calls if c == name]


def _finding(record_id=11, field="olim", status=None, page_value="Каримов Карим",
             db_value="", **kw):
    out = {"record_id": record_id, "field": field, "label": field.title(),
           "status": status or R.FILL, "note": "", "db_value": db_value,
           "page_value": page_value, "page_id": "4321"}
    out.update(kw)
    return out


def _apply(findings, recorder=None, issues=None, conn=None):
    recorder = recorder or _RepoRecorder()
    conn = conn or _FakeConn()
    original = R.repo
    R.repo = recorder
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            stats = R.apply_fills(conn, R.collect_fills(findings), scan_run_id=12,
                                  target="host/staging", actor="cli:test",
                                  issues=issues or {})
        return stats, recorder, conn, buf.getvalue()
    finally:
        R.repo = original


# ── what may be written at all ──────────────────────────────────────────────
class FillSelectionTests(unittest.TestCase):
    def test_only_fill_passes_the_gate(self):
        findings = [
            _finding(record_id=1, status=R.FILL),
            _finding(record_id=2, status=R.CONFLICT, db_value="Boshqa"),
            _finding(record_id=3, status=R.MATCH, db_value="Каримов Карим"),
            _finding(record_id=4, status=R.MISSING_ON_PAGE, page_value=""),
            _finding(record_id=5, status=R.EMPTY_BOTH, page_value=""),
        ]
        picked = R.collect_fills(findings)
        self.assertEqual([f["record_id"] for f in picked], [1])

    def test_fill_without_a_page_value_is_not_written(self):
        # Defensive: a FILL whose page value is blank/whitespace writes nothing.
        for blank in ("", "   ", "\n"):
            with self.subTest(blank=repr(blank)):
                self.assertEqual(R.collect_fills([_finding(page_value=blank)]), [])

    def test_conflict_never_reaches_the_database(self):
        conflict = _finding(record_id=2, status=R.CONFLICT, db_value="Eski qiymat")
        stats, recorder, conn, _ = _apply([conflict])
        self.assertEqual(recorder.names(), [])
        self.assertNotIn("commit", conn.events)
        self.assertEqual(stats["applied"], 0)

    def test_rescrape_columns_match_the_repository_allowlist(self):
        # The two lists are kept in step deliberately (defence in depth).
        self.assertEqual(M.RESCRAPE_FILL_COLUMNS, repo_real._RESCRAPE_COLUMNS)
        # Every field the comparison can produce must be writable.
        for column, _key, _norm, _label in R.FIELDS:
            self.assertIn(column, M.RESCRAPE_FILL_COLUMNS, column)
        # `link` is an input to the rescrape, never an output.
        self.assertNotIn("link", M.RESCRAPE_FILL_COLUMNS)


# ── logging + transaction shape ─────────────────────────────────────────────
class ApplyLoggingTests(unittest.TestCase):
    def test_fill_is_logged_with_before_and_after(self):
        stats, recorder, _, _ = _apply([_finding()])
        logged = recorder.kwargs_of("log_fix")
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged[0]["value_before"], "")
        self.assertEqual(logged[0]["value_after"], "Каримов Карим")
        self.assertEqual(logged[0]["record_id"], 11)
        self.assertEqual(logged[0]["column_name"], "olim")
        self.assertEqual(logged[0]["table_name"], "dissertations")
        self.assertEqual(logged[0]["applied_by"], "cli:test")
        self.assertEqual(logged[0]["run_id"], recorder.next_run_id)
        self.assertEqual(logged[0]["scope"], repo_real.SCOPE_RESCRAPE)
        self.assertEqual(stats["applied"], 1)

    def test_log_is_written_before_the_update(self):
        _, recorder, _, _ = _apply([_finding()])
        names = recorder.names()
        self.assertLess(names.index("log_fix"), names.index("update_record_field"))

    def test_run_is_a_rescrape_run_and_is_completed(self):
        _, recorder, _, _ = _apply([_finding()])
        run = recorder.kwargs_of("create_run")[0]
        self.assertEqual(run["run_type"], "rescrape")
        self.assertEqual(run["metadata"]["scan_run_id"], 12)
        self.assertEqual(recorder.kwargs_of("complete_run")[0]["run_id"], 77)

    def test_all_fills_of_a_run_share_one_run_id(self):
        findings = [_finding(record_id=11), _finding(record_id=12),
                    _finding(record_id=13, field="mavzu", page_value="Mavzu")]
        _, recorder, _, _ = _apply(findings)
        run_ids = {kw["run_id"] for kw in recorder.kwargs_of("log_fix")}
        self.assertEqual(run_ids, {77})
        self.assertEqual(recorder.names().count("create_run"), 1)

    def test_originating_issue_is_linked_and_closed(self):
        issues = {(11, "olim"): {"id": 5, "code": "MISSING_AUTHOR"}}
        _, recorder, _, _ = _apply([_finding()], issues=issues)
        logged = recorder.kwargs_of("log_fix")[0]
        self.assertEqual(logged["issue_id"], 5)
        self.assertEqual(logged["issue_code"], "MISSING_AUTHOR")
        self.assertEqual(recorder.kwargs_of("mark_issue_fixed")[0]["issue_id"], 5)

    def test_fill_without_a_matching_issue_still_logs(self):
        _, recorder, _, _ = _apply([_finding()], issues={})
        logged = recorder.kwargs_of("log_fix")[0]
        self.assertIsNone(logged["issue_id"])
        self.assertEqual(logged["issue_code"], R.GENERIC_FILL_CODE)
        self.assertNotIn("mark_issue_fixed", recorder.names())

    def test_page_value_is_written_verbatim(self):
        # What the report shows is exactly what lands in the column.
        page = "  Каримов   Карим  "
        stats, recorder, _, _ = _apply([_finding(page_value=page)])
        self.assertEqual(recorder.kwargs_of("update_record_field")[0]["value"], page)
        self.assertEqual(recorder.kwargs_of("log_fix")[0]["value_after"], page)


class TransactionTests(unittest.TestCase):
    def test_one_commit_per_record(self):
        findings = [_finding(record_id=11), _finding(record_id=11, field="mavzu",
                                                     page_value="Mavzu"),
                    _finding(record_id=12)]
        stats, _, conn, _ = _apply(findings)
        # 2 records + 1 closing commit for the run.
        self.assertEqual(conn.events.count("commit"), 3)
        self.assertEqual(stats["applied"], 3)
        self.assertEqual(stats["records"], 2)

    def test_a_failed_field_rolls_back_the_whole_record(self):
        findings = [_finding(record_id=11),
                    _finding(record_id=11, field="mavzu", page_value="Mavzu")]
        recorder = _RepoRecorder()
        recorder.fail_on = "update_record_field"
        stats, recorder, conn, _ = _apply(findings, recorder=recorder)
        self.assertIn("rollback", conn.events)
        self.assertNotIn("commit", conn.events, "no half-filled record may commit")
        self.assertEqual(stats["applied"], 0)
        self.assertEqual(stats["failed_records"], 1)
        for f in findings:
            self.assertEqual(f["apply_status"], R.FAILED)

    def test_one_bad_record_does_not_stop_the_others(self):
        findings = [_finding(record_id=11), _finding(record_id=12)]
        recorder = _RepoRecorder()

        real_update = recorder.update_record_field

        def flaky(conn, record_id, column, value, **kw):
            if record_id == 11:
                raise RuntimeError("boom")
            return real_update(conn, record_id, column, value, **kw)

        recorder.update_record_field = flaky
        stats, recorder, conn, _ = _apply(findings, recorder=recorder)
        self.assertEqual(stats["applied"], 1)
        self.assertEqual(stats["failed_records"], 1)
        self.assertEqual(findings[0]["apply_status"], R.FAILED)
        self.assertEqual(findings[1]["apply_status"], R.APPLIED)

    def test_wrong_affected_row_count_rolls_back(self):
        recorder = _RepoRecorder(affected=2)
        stats, recorder, conn, _ = _apply([_finding()], recorder=recorder)
        self.assertIn("rollback", conn.events)
        self.assertNotIn("commit", conn.events)
        self.assertEqual(stats["applied"], 0)

    def test_nothing_to_write_leaves_no_orphan_run(self):
        # Every field turned out to be non-empty: the run row is rolled back
        # with the transaction that created it, and no run is completed.
        recorder = _RepoRecorder(current={(11, "olim"): (True, "Allaqachon bor")})
        stats, recorder, conn, _ = _apply([_finding()], recorder=recorder)
        self.assertNotIn("commit", conn.events)
        self.assertNotIn("complete_run", recorder.names())
        self.assertIsNone(stats["run_id"])
        self.assertEqual(stats["applied"], 0)

    def test_no_fills_at_all_touches_nothing(self):
        stats, recorder, conn, _ = _apply([])
        self.assertEqual(recorder.names(), [])
        self.assertEqual(conn.events, [])
        self.assertIsNone(stats["run_id"])


class RecheckBeforeWriteTests(unittest.TestCase):
    def test_field_filled_since_the_report_is_skipped(self):
        recorder = _RepoRecorder(current={(11, "olim"): (True, "Boshqa odam")})
        findings = [_finding()]
        stats, recorder, conn, out = _apply(findings, recorder=recorder)
        self.assertNotIn("log_fix", recorder.names())
        self.assertNotIn("update_record_field", recorder.names())
        self.assertEqual(findings[0]["apply_status"], R.SKIPPED_NOT_EMPTY)
        self.assertEqual(stats["skipped"], 1)

    def test_deleted_record_is_skipped(self):
        recorder = _RepoRecorder(current={(11, "olim"): (False, None)})
        findings = [_finding()]
        stats, recorder, conn, _ = _apply(findings, recorder=recorder)
        self.assertNotIn("update_record_field", recorder.names())
        self.assertEqual(findings[0]["apply_status"], R.SKIPPED_GONE)
        self.assertEqual(stats["skipped"], 1)

    def test_placeholder_supervisor_still_counts_as_empty(self):
        # PLACEHOLDER_SUPERVISOR records are the point of the exercise: the
        # column holds a template, not a name, so filling it is a FILL.
        recorder = _RepoRecorder(current={(11, "ilmiy_rahbar"): (True, "Ф.И.Ш.")})
        findings = [_finding(field="ilmiy_rahbar", page_value="Юсупов Равшан")]
        stats, recorder, _, _ = _apply(findings, recorder=recorder)
        self.assertEqual(stats["applied"], 1)
        self.assertEqual(recorder.kwargs_of("log_fix")[0]["value_before"], "Ф.И.Ш.")

    def test_recheck_uses_the_rescrape_scope(self):
        _, recorder, _, _ = _apply([_finding()])
        for kw in recorder.kwargs_of("fetch_current_value"):
            self.assertEqual(kw["scope"], repo_real.SCOPE_RESCRAPE)
        for kw in recorder.kwargs_of("update_record_field"):
            self.assertEqual(kw["scope"], repo_real.SCOPE_RESCRAPE)


# ── CLI gating ──────────────────────────────────────────────────────────────
class CliGatingTests(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = R.main(argv)
        return code, buf.getvalue()

    def test_apply_without_confirm_is_refused(self):
        code, out = self._run(["--run-id", "12", "--apply"])
        self.assertEqual(code, 2)
        self.assertIn("--confirm", out)

    def test_confirm_without_apply_is_refused(self):
        code, out = self._run(["--run-id", "12", "--confirm"])
        self.assertEqual(code, 2)
        self.assertIn("--apply", out)

    def test_run_id_is_still_required(self):
        code, out = self._run(["--apply", "--confirm"])
        self.assertEqual(code, 2)
        self.assertIn("--run-id", out)


# ── report ──────────────────────────────────────────────────────────────────
class ApplyReportTests(unittest.TestCase):
    STATS = {"selected": 1, "with_page": 1, "ok": 1, "from_cache": 1,
             "downloaded": 0, "codes": ["MISSING_AUTHOR"]}

    def test_report_mode_still_says_nothing_was_written(self):
        md = R.build_report([_finding()], [], self.STATS, run_id=12,
                            target="host/staging")
        self.assertIn("1-FAZA", md)
        self.assertIn("hech narsa yozilmadi", md)
        self.assertNotIn("Qo'llash natijasi", md)

    def test_apply_mode_reports_the_run_and_the_rollback_command(self):
        findings = [_finding(), _finding(record_id=12)]
        applied = {"records": 2, "applied": 1, "skipped": 1,
                   "failed_records": 0, "run_id": 77}
        findings[1]["apply_status"] = R.SKIPPED_NOT_EMPTY
        md = R.build_report(findings, [], self.STATS, run_id=12,
                            target="host/staging", applied=applied)
        self.assertIn("2-FAZA", md)
        self.assertIn("Qo'llash natijasi", md)
        self.assertIn("rollback_data_quality_fixes.py --run-id 77 --confirm", md)
        self.assertIn(R.SKIPPED_NOT_EMPTY, md)

    def test_csv_carries_the_apply_status(self):
        import csv
        import tempfile
        findings = [_finding()]
        findings[0]["apply_status"] = R.APPLIED
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.csv")
            R.write_csv(path, findings, [])
            with open(path, encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["apply_status"], R.APPLIED)


# ── rollback works for a rescrape run too ───────────────────────────────────
class RollbackOfRescrapeRunTests(unittest.TestCase):
    """Point of the exercise: one rollback CLI, any writer. A rescrape fix-log
    row differs only in run_type and column, and must restore the same way."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_script("rollback_data_quality_fixes")

    def _fix(self, **over):
        base = {"id": 3, "run_id": 77, "issue_id": 5, "record_id": 11,
                "table_name": "dissertations", "column_name": "olim",
                "value_before": "", "value_after": "Каримов Карим",
                "issue_code": "MISSING_AUTHOR", "applied_at": None,
                "applied_by": "cli:test", "rolled_back_at": None}
        base.update(over)
        return base

    def _run(self, fixes, current, argv, run_type="rescrape"):
        from tests.test_data_quality_fix_log import _FakeRepo, _run_cli
        recorder = _FakeRepo(fixes=fixes, current=current)
        recorder.fetch_run = lambda conn, run_id: {
            "id": run_id, "run_type": run_type, "status": "completed",
            "started_at": None, "completed_at": None}
        return _run_cli(self.mod, recorder, argv)

    def test_rescrape_fill_is_restored_to_empty(self):
        code, out, conn = self._run([self._fix()], (True, "Каримов Карим"),
                                    ["--run-id", "77", "--confirm"])
        self.assertEqual(code, 0)
        self.assertIn("run_type=rescrape", out)
        self.assertIn("restored : 1", out)

    def test_rollback_uses_the_union_scope_so_free_text_columns_work(self):
        # `olim` is NOT on the fixer allowlist; the rollback scope must still
        # accept it, otherwise a rescrape run could never be undone.
        for column in sorted(M.RESCRAPE_FILL_COLUMNS):
            with self.subTest(column=column):
                repo_real._check_target("dissertations", column,
                                        repo_real.SCOPE_ROLLBACK)
        with self.assertRaises(ValueError):
            repo_real._check_target("dissertations", "olim",
                                    repo_real.SCOPE_AUTOFIX)

    def test_value_changed_after_the_fill_is_skipped(self):
        code, out, conn = self._run([self._fix()], (True, "Qo'lda yozilgan"),
                                    ["--run-id", "77", "--confirm"])
        self.assertIn("mismatched : 1", out)
        self.assertNotIn("commit", conn.events)

    def test_dry_run_is_still_the_default(self):
        code, out, conn = self._run([self._fix()], (True, "Каримов Карим"),
                                    ["--run-id", "77"])
        self.assertIn("DRY-RUN", out)
        self.assertNotIn("commit", conn.events)


if __name__ == "__main__":
    unittest.main()
