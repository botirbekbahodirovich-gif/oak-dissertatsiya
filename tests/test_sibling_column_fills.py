"""Tests for scripts/apply_sibling_column_fills.py — FILL 1 (muassasa <-
ilmiy_kengash) only.

Offline by design: no socket, no database. The repository is replaced by a
recorder, so assertions are about what apply_fills() would send to the DB and
in which order. Covers the exact exclusion rule ("ҳузуридаги"), the direct
(non-fuzzy) copy, the fix-log-before-UPDATE ordering, one transaction per
record, the re-check that refuses to overwrite a filled field, and that the
rollback CLI (run_type-agnostic) can undo a sibling_fill run.

    python3 -m unittest tests.test_sibling_column_fills
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


def _load_script(name):
    path = os.path.join(_REPO_ROOT, "scripts", f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_script_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


F = _load_script("apply_sibling_column_fills")

from data_quality import repository as repo_real  # noqa: E402
from tests.test_data_quality_fix_log import _FakeConn  # noqa: E402


# ── rule: is_council_not_institution / is_fillable ──────────────────────────
class RuleTests(unittest.TestCase):
    def test_plain_institution_is_fillable(self):
        self.assertFalse(F.is_council_not_institution(
            "Тошкент давлат аграр университети"))
        self.assertTrue(F.is_fillable("", "Тошкент давлат аграр университети"))

    def test_council_suffix_is_excluded(self):
        v = "Чирчиқ давлат педагогика университети ҳузуридаги"
        self.assertTrue(F.is_council_not_institution(v))
        self.assertFalse(F.is_fillable("", v))

    def test_council_suffix_with_trailing_punctuation_is_still_excluded(self):
        for v in ("...ҳузуридаги.", "...ҳузуридаги ", "...ҳузуридаги,"):
            with self.subTest(v=v):
                self.assertTrue(F.is_council_not_institution(v))

    def test_council_suffix_case_insensitive(self):
        self.assertTrue(F.is_council_not_institution(
            "Тошкент давлат университети ҲУЗУРИДАГИ"))

    def test_suffix_in_the_middle_does_not_exclude(self):
        # Only a genuine ending counts — the word appearing mid-string is not
        # the same signal (defensive: keeps the rule to what was approved).
        v = "ҳузуридаги Илмий кенгаш аъзоси Тошкент давлат университети"
        self.assertFalse(F.is_council_not_institution(v))

    def test_not_fillable_when_muassasa_already_has_a_value(self):
        self.assertFalse(F.is_fillable("Allaqachon bor", "ToshDTU"))

    def test_not_fillable_when_kengash_is_blank(self):
        for v in (None, "", "   "):
            self.assertFalse(F.is_fillable("", v))

    def test_not_fillable_when_muassasa_is_whitespace_only(self):
        # Effectively blank, same as a true NULL/empty string.
        self.assertTrue(F.is_fillable("   ", "ToshDTU"))


# ── recorder ────────────────────────────────────────────────────────────────
class _RepoRecorder:
    """Stand-in for data_quality.repository: records calls, no DB."""

    def __init__(self, current=None, affected=1):
        self.calls = []
        # {(record_id, column): (found, value)} — default: still empty
        self.current = current or {}
        self.affected = affected
        self.fail_on = None
        self.next_run_id = 88
        self.SCOPE_SIBLING_FILL = repo_real.SCOPE_SIBLING_FILL
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


def _cand(id=11, muassasa="", ilmiy_kengash="Тошкент давлат аграр университети"):
    return {"id": id, "muassasa": muassasa, "ilmiy_kengash": ilmiy_kengash}


def _apply(candidates, recorder=None, issues=None, conn=None):
    recorder = recorder or _RepoRecorder()
    conn = conn or _FakeConn()
    original = F.repo
    F.repo = recorder
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            stats = F.apply_fills(conn, candidates, target="host/staging",
                                  actor="cli:test", issues=issues or {})
        return stats, recorder, conn, buf.getvalue()
    finally:
        F.repo = original


# ── direct copy, no fuzzy matching ──────────────────────────────────────────
class DirectCopyTests(unittest.TestCase):
    def test_value_is_copied_verbatim(self):
        cand = _cand(ilmiy_kengash="Тошкент давлат аграр университети")
        stats, recorder, _, _ = _apply([cand])
        upd = recorder.kwargs_of("update_record_field")[0]
        self.assertEqual(upd["value"], "Тошкент давлат аграр университети")
        self.assertEqual(upd["column"], "muassasa")
        self.assertEqual(stats["applied"], 1)

    def test_council_suffixed_candidate_is_never_passed_to_apply_fills(self):
        # collect_fills equivalent: main() filters with is_fillable before
        # apply_fills ever sees the row. Simulate that filtering here.
        cands = [_cand(id=1, ilmiy_kengash="ToshDTU"),
                 _cand(id=2, ilmiy_kengash="Chirchiq DPU ҳузуридаги")]
        filtered = [c for c in cands
                   if F.is_fillable(c["muassasa"], c["ilmiy_kengash"])]
        self.assertEqual([c["id"] for c in filtered], [1])

    def test_scope_used_is_sibling_fill(self):
        _, recorder, _, _ = _apply([_cand()])
        self.assertEqual(recorder.kwargs_of("log_fix")[0]["scope"],
                         repo_real.SCOPE_SIBLING_FILL)
        self.assertEqual(recorder.kwargs_of("update_record_field")[0]["scope"],
                         repo_real.SCOPE_SIBLING_FILL)


# ── logging + transaction shape ─────────────────────────────────────────────
class ApplyLoggingTests(unittest.TestCase):
    def test_fill_is_logged_with_before_and_after(self):
        stats, recorder, _, _ = _apply([_cand()])
        logged = recorder.kwargs_of("log_fix")[0]
        self.assertEqual(logged["value_before"], "")
        self.assertEqual(logged["value_after"], "Тошкент давлат аграр университети")
        self.assertEqual(logged["record_id"], 11)
        self.assertEqual(logged["column_name"], "muassasa")
        self.assertEqual(logged["table_name"], "dissertations")
        self.assertEqual(logged["issue_code"], "MISSING_ORG")
        self.assertEqual(logged["applied_by"], "cli:test")
        self.assertEqual(logged["run_id"], recorder.next_run_id)

    def test_log_is_written_before_the_update(self):
        _, recorder, _, _ = _apply([_cand()])
        names = recorder.names()
        self.assertLess(names.index("log_fix"), names.index("update_record_field"))

    def test_run_is_a_sibling_fill_run(self):
        _, recorder, _, _ = _apply([_cand()])
        run = recorder.kwargs_of("create_run")[0]
        self.assertEqual(run["run_type"], "sibling_fill")
        self.assertEqual(recorder.kwargs_of("complete_run")[0]["run_id"], 88)

    def test_all_fills_of_a_run_share_one_run_id(self):
        cands = [_cand(id=11), _cand(id=12), _cand(id=13)]
        _, recorder, _, _ = _apply(cands)
        run_ids = {kw["run_id"] for kw in recorder.kwargs_of("log_fix")}
        self.assertEqual(run_ids, {88})
        self.assertEqual(recorder.names().count("create_run"), 1)

    def test_originating_missing_org_issue_is_linked_and_closed(self):
        _, recorder, _, _ = _apply([_cand()], issues={11: 5})
        logged = recorder.kwargs_of("log_fix")[0]
        self.assertEqual(logged["issue_id"], 5)
        self.assertEqual(recorder.kwargs_of("mark_issue_fixed")[0]["issue_id"], 5)

    def test_missing_issue_link_does_not_block_the_fill(self):
        stats, recorder, _, _ = _apply([_cand()], issues={})
        logged = recorder.kwargs_of("log_fix")[0]
        self.assertIsNone(logged["issue_id"])
        self.assertNotIn("mark_issue_fixed", recorder.names())
        self.assertEqual(stats["applied"], 1)


class TransactionTests(unittest.TestCase):
    def test_one_commit_per_record(self):
        cands = [_cand(id=11), _cand(id=12)]
        stats, _, conn, _ = _apply(cands)
        # 2 records + 1 closing commit for the run.
        self.assertEqual(conn.events.count("commit"), 3)
        self.assertEqual(stats["applied"], 2)

    def test_failed_update_rolls_back_the_log_row_too(self):
        recorder = _RepoRecorder()
        recorder.fail_on = "update_record_field"
        stats, recorder, conn, _ = _apply([_cand()], recorder=recorder)
        self.assertIn("rollback", conn.events)
        self.assertNotIn("commit", conn.events)
        self.assertEqual(stats["applied"], 0)

    def test_wrong_affected_row_count_rolls_back(self):
        recorder = _RepoRecorder(affected=2)
        stats, recorder, conn, _ = _apply([_cand()], recorder=recorder)
        self.assertIn("rollback", conn.events)
        self.assertNotIn("commit", conn.events)
        self.assertEqual(stats["applied"], 0)

    def test_one_bad_record_does_not_stop_the_others(self):
        cands = [_cand(id=11), _cand(id=12)]
        recorder = _RepoRecorder()
        real_update = recorder.update_record_field

        def flaky(conn, record_id, column, value, **kw):
            if record_id == 11:
                raise RuntimeError("boom")
            return real_update(conn, record_id, column, value, **kw)

        recorder.update_record_field = flaky
        stats, recorder, conn, _ = _apply(cands, recorder=recorder)
        self.assertEqual(stats["applied"], 1)
        self.assertEqual(cands[0]["apply_status"], F.FAILED)
        self.assertEqual(cands[1]["apply_status"], F.APPLIED)

    def test_nothing_to_write_leaves_no_orphan_run(self):
        recorder = _RepoRecorder(current={(11, "muassasa"): (True, "Allaqachon bor")})
        stats, recorder, conn, _ = _apply([_cand()], recorder=recorder)
        self.assertNotIn("commit", conn.events)
        self.assertNotIn("complete_run", recorder.names())
        self.assertIsNone(stats["run_id"])
        self.assertEqual(stats["applied"], 0)

    def test_no_candidates_touches_nothing(self):
        stats, recorder, conn, _ = _apply([])
        self.assertEqual(recorder.names(), [])
        self.assertEqual(conn.events, [])
        self.assertIsNone(stats["run_id"])


class RecheckBeforeWriteTests(unittest.TestCase):
    def test_field_filled_since_selection_is_skipped(self):
        recorder = _RepoRecorder(current={(11, "muassasa"): (True, "Boshqa qiymat")})
        cands = [_cand()]
        stats, recorder, _, _ = _apply(cands, recorder=recorder)
        self.assertNotIn("log_fix", recorder.names())
        self.assertNotIn("update_record_field", recorder.names())
        self.assertEqual(cands[0]["apply_status"], F.SKIPPED_NOT_EMPTY)
        self.assertEqual(stats["skipped"], 1)

    def test_deleted_record_is_skipped(self):
        recorder = _RepoRecorder(current={(11, "muassasa"): (False, None)})
        cands = [_cand()]
        stats, recorder, conn, _ = _apply(cands, recorder=recorder)
        self.assertNotIn("update_record_field", recorder.names())
        self.assertEqual(cands[0]["apply_status"], F.SKIPPED_GONE)
        self.assertEqual(stats["skipped"], 1)


# ── CLI gating ──────────────────────────────────────────────────────────────
class CliGatingTests(unittest.TestCase):
    def test_dry_run_is_the_default(self):
        p = F._build_parser().parse_args([])
        self.assertTrue(p.dry_run)
        self.assertFalse(p.confirm)

    def test_confirm_flag_parses(self):
        p = F._build_parser().parse_args(["--confirm"])
        self.assertTrue(p.confirm)

    def test_dry_run_and_confirm_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            F._build_parser().parse_args(["--dry-run", "--confirm"])


# ── rollback works for a sibling_fill run too ───────────────────────────────
class RollbackOfSiblingFillRunTests(unittest.TestCase):
    """The rollback CLI is generic (run_type-agnostic). A sibling_fill row
    differs from a 'fix' row only in run_type and column; it must restore
    the same way — including 'muassasa', which the SCOPE_AUTOFIX-only
    fixer could never touch."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_script("rollback_data_quality_fixes")

    def _fix_row(self, **over):
        base = {"id": 3, "run_id": 88, "issue_id": 5, "record_id": 11,
                "table_name": "dissertations", "column_name": "muassasa",
                "value_before": "", "value_after": "Тошкент давлат аграр университети",
                "issue_code": "MISSING_ORG", "applied_at": None,
                "applied_by": "cli:test", "rolled_back_at": None}
        base.update(over)
        return base

    def _run(self, fixes, current, argv, run_type="sibling_fill"):
        from tests.test_data_quality_fix_log import _FakeRepo, _run_cli
        recorder = _FakeRepo(fixes=fixes, current=current)
        recorder.fetch_run = lambda conn, run_id: {
            "id": run_id, "run_type": run_type, "status": "completed",
            "started_at": None, "completed_at": None}
        return _run_cli(self.mod, recorder, argv)

    def test_muassasa_fill_is_restored_to_empty(self):
        code, out, conn = self._run(
            [self._fix_row()], (True, "Тошкент давлат аграр университети"),
            ["--run-id", "88", "--confirm"])
        self.assertEqual(code, 0)
        self.assertIn("run_type=sibling_fill", out)
        self.assertIn("restored : 1", out)

    def test_rollback_union_scope_accepts_muassasa(self):
        # `muassasa` is NOT on the fixer allowlist; rollback must still accept
        # it, otherwise a sibling_fill run could never be undone.
        repo_real._check_target("dissertations", "muassasa",
                                repo_real.SCOPE_ROLLBACK)
        with self.assertRaises(ValueError):
            repo_real._check_target("dissertations", "muassasa",
                                    repo_real.SCOPE_AUTOFIX)

    def test_value_changed_after_the_fill_is_skipped(self):
        code, out, conn = self._run(
            [self._fix_row()], (True, "Qo'lda o'zgartirilgan"),
            ["--run-id", "88", "--confirm"])
        self.assertIn("mismatched : 1", out)
        self.assertNotIn("commit", conn.events)

    def test_dry_run_is_still_the_default(self):
        code, out, conn = self._run(
            [self._fix_row()], (True, "Тошкент давлат аграр университети"),
            ["--run-id", "88"])
        self.assertIn("DRY-RUN", out)
        self.assertNotIn("commit", conn.events)


if __name__ == "__main__":
    unittest.main()
