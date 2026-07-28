"""Tests for the Data Quality fix log (migration 005), its repository helpers,
the transactional apply path and the rollback CLI.

No database is required for the default suite: the migration is checked
statically (same approach as test_data_quality_migration.py) and the CLI logic
is driven through a recording fake connection / fake repository. The DB-gated
class at the bottom is skipped unless TEST_DATABASE_URL points at STAGING.

    python3 -m unittest tests.test_data_quality_fix_log
"""
import importlib.util
import io
import os
import re
import unittest
from contextlib import redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(_REPO_ROOT, "migrations",
                         "005_create_data_quality_fix_log.sql")

TEST_DB = os.environ.get("TEST_DATABASE_URL")


def _load_script(name):
    """Import a scripts/*.py module by path (scripts/ is not a package)."""
    path = os.path.join(_REPO_ROOT, "scripts", f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_script_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _strip_comments(sql: str) -> str:
    out = []
    for line in sql.splitlines():
        idx = line.find("--")
        out.append(line if idx == -1 else line[:idx])
    return "\n".join(out)


# ── 1. Migration safety (static) ────────────────────────────────────────────
class FixLogMigrationSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(MIGRATION, encoding="utf-8") as f:
            cls.raw = f.read()
        cls.code = _strip_comments(cls.raw)
        cls.upper = cls.code.upper()

    def test_single_transaction(self):
        self.assertIn("BEGIN;", self.upper)
        self.assertIn("COMMIT;", self.upper)

    def test_creates_fix_log_table(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS DATA_QUALITY_FIX_LOG", self.upper)

    def test_expected_columns(self):
        for col in ("id", "run_id", "record_id", "table_name", "column_name",
                    "value_before", "value_after", "issue_code", "applied_at",
                    "applied_by", "rolled_back_at"):
            self.assertRegex(self.code, rf"\n\s+{col}\s+\w", col)

    def test_run_id_references_runs(self):
        self.assertRegex(self.upper, r"RUN_ID\s+BIGINT\s+NOT NULL\s+REFERENCES DATA_QUALITY_RUNS")
        # The audit trail must survive run cleanup, so no CASCADE on run_id.
        self.assertNotRegex(self.upper, r"REFERENCES DATA_QUALITY_RUNS \(ID\) ON DELETE CASCADE")

    def test_rolled_back_at_is_nullable(self):
        self.assertRegex(self.upper, r"ROLLED_BACK_AT\s+TIMESTAMPTZ\s*\n")

    def test_required_indexes(self):
        self.assertRegex(self.upper, r"CREATE INDEX IF NOT EXISTS \S+\s+ON DATA_QUALITY_FIX_LOG \(RUN_ID\)")
        self.assertRegex(self.upper, r"CREATE INDEX IF NOT EXISTS \S+\s+ON DATA_QUALITY_FIX_LOG \(RECORD_ID\)")

    def test_all_creates_are_idempotent(self):
        creates = re.findall(r"CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS)?", self.upper)
        self.assertTrue(creates)
        for guard in creates:
            self.assertTrue(guard, "every CREATE TABLE must be IF NOT EXISTS")
        for guard in re.findall(r"CREATE\s+INDEX\s+(IF\s+NOT\s+EXISTS)?", self.upper):
            self.assertTrue(guard, "every CREATE INDEX must be IF NOT EXISTS")

    def test_no_destructive_statements(self):
        for bad in ("DROP TABLE", "DROP INDEX", "TRUNCATE", "DELETE FROM", "UPDATE "):
            self.assertNotIn(bad, self.upper, f"unexpected {bad!r} in executable SQL")

    def test_does_not_touch_production_tables(self):
        for prod in ("ALTER TABLE DISSERTATIONS", "ALTER TABLE USERS",
                     "ALTER TABLE DATA_QUALITY_ISSUES", "ALTER TABLE DATA_QUALITY_RUNS"):
            self.assertNotIn(prod, self.upper)


# ── Recording fakes ─────────────────────────────────────────────────────────
class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        self._conn.queries.append((" ".join(query.split()), params))
        self.rowcount = self._conn.rowcount

    def fetchone(self):
        return self._conn.fetchone_result

    def fetchall(self):
        return self._conn.fetchall_result

    def close(self):
        pass


class _FakeConn:
    """Records every statement plus the commit/rollback sequence."""

    def __init__(self, fetchone_result=(1,), fetchall_result=()):
        self.queries = []
        self.events = []
        self.fetchone_result = fetchone_result
        self.fetchall_result = fetchall_result
        self.rowcount = 1
        self.autocommit = False

    def cursor(self, name=None):
        return _FakeCursor(self)

    def commit(self):
        self.events.append("commit")

    def rollback(self):
        self.events.append("rollback")

    def close(self):
        self.events.append("close")

    def sql(self):
        return [q for q, _ in self.queries]


# ── 2. Repository helpers ───────────────────────────────────────────────────
class RepositoryFixLogTests(unittest.TestCase):
    def setUp(self):
        from data_quality import repository as repo
        self.repo = repo

    def test_log_fix_inserts_row_and_returns_id(self):
        conn = _FakeConn(fetchone_result=(77,))
        fix_id = self.repo.log_fix(
            conn, run_id=5, issue_id=9, record_id=123, column_name="daraja",
            value_before="phd", value_after="PhD", issue_code="INCONSISTENT_DEGREE",
            applied_by="cli:tester")
        self.assertEqual(fix_id, 77)
        sql, params = conn.queries[0]
        self.assertIn("INSERT INTO data_quality_fix_log", sql)
        self.assertEqual(
            params,
            (5, 9, 123, "dissertations", "daraja", "phd", "PhD",
             "INCONSISTENT_DEGREE", "cli:tester"))

    def test_log_fix_keeps_null_distinct_from_the_string_none(self):
        conn = _FakeConn(fetchone_result=(1,))
        self.repo.log_fix(conn, run_id=1, record_id=2, column_name="link",
                          value_before=None, value_after="http://x")
        params = conn.queries[0][1]
        self.assertIsNone(params[5])
        self.assertEqual(params[6], "http://x")

    def test_log_fix_refuses_unlisted_column_and_table(self):
        conn = _FakeConn()
        with self.assertRaises(ValueError):
            self.repo.log_fix(conn, run_id=1, record_id=2, column_name="olim",
                              value_before="a", value_after="b")
        with self.assertRaises(ValueError):
            self.repo.log_fix(conn, run_id=1, record_id=2, column_name="daraja",
                              table_name="users", value_before="a", value_after="b")
        self.assertEqual(conn.queries, [], "nothing may be written for a bad target")

    def test_fetch_fixes_by_run_excludes_reverted_and_is_newest_first(self):
        conn = _FakeConn(fetchall_result=[
            (2, 5, None, 123, "dissertations", "daraja", "phd", "PhD",
             "INCONSISTENT_DEGREE", None, "cli", None)])
        rows = self.repo.fetch_fixes_by_run(conn, 5)
        sql, params = conn.queries[0]
        self.assertIn("FROM data_quality_fix_log WHERE run_id = %s", sql)
        self.assertIn("rolled_back_at IS NULL", sql)
        self.assertIn("ORDER BY id DESC", sql)
        self.assertEqual(params, [5])
        self.assertEqual(rows[0]["value_before"], "phd")
        self.assertEqual(rows[0]["column_name"], "daraja")

    def test_fetch_fixes_by_run_can_include_reverted(self):
        conn = _FakeConn(fetchall_result=[])
        self.repo.fetch_fixes_by_run(conn, 5, include_rolled_back=True)
        self.assertNotIn("rolled_back_at IS NULL", conn.queries[0][0])

    def test_mark_rolled_back_skips_already_reverted(self):
        conn = _FakeConn()
        conn.rowcount = 1
        self.assertEqual(self.repo.mark_rolled_back(conn, [3]), 1)
        sql, params = conn.queries[0]
        self.assertIn("SET rolled_back_at = NOW()", sql)
        self.assertIn("rolled_back_at IS NULL", sql)
        self.assertEqual(params, ([3],))
        # A second pass matches no rows -> reports 0, not a phantom rollback.
        conn.rowcount = 0
        self.assertEqual(self.repo.mark_rolled_back(conn, [3]), 0)

    def test_mark_rolled_back_no_ids_is_a_noop(self):
        conn = _FakeConn()
        self.assertEqual(self.repo.mark_rolled_back(conn, []), 0)
        self.assertEqual(conn.queries, [])

    def test_fetch_current_value_separates_missing_record_from_null(self):
        conn = _FakeConn(fetchone_result=None)
        self.assertEqual(self.repo.fetch_current_value(conn, 1, "daraja"), (False, None))
        conn = _FakeConn(fetchone_result=(None,))
        self.assertEqual(self.repo.fetch_current_value(conn, 1, "daraja"), (True, None))
        with self.assertRaises(ValueError):
            self.repo.fetch_current_value(conn, 1, "olim")

    def test_update_record_field_still_guards_table_and_column(self):
        conn = _FakeConn()
        with self.assertRaises(ValueError):
            self.repo.update_record_field(conn, 1, "daraja", "x", table_name="users")
        with self.assertRaises(ValueError):
            self.repo.update_record_field(conn, 1, "mavzu", "x")


# ── Fake repository shared by both CLI test classes ─────────────────────────
class _FakeRepo:
    """Stand-in for data_quality.repository that records calls in order."""

    def __init__(self, issues=(), record=None, fixes=(), current=None):
        self.calls = []
        self._issues = list(issues)
        self._record = record or {}
        self._fixes = list(fixes)
        self._current = current
        self.affected = 1
        self.rolled_back_rows = 1
        self.fail_on = None          # call name that should raise
        self.next_run_id = 42

    def _note(self, name, **kw):
        self.calls.append((name, kw))
        if self.fail_on == name:
            raise RuntimeError("boom")

    # -- apply path
    def iter_fixable_issues(self, conn, codes, **kw):
        self._note("iter_fixable_issues", **kw)
        return list(self._issues)

    def fetch_record(self, conn, record_id):
        self._note("fetch_record", record_id=record_id)
        return dict(self._record)

    def create_run(self, conn, run_type, total_records=0, metadata=None):
        self._note("create_run", run_type=run_type)
        return self.next_run_id

    def complete_run(self, conn, run_id, **kw):
        self._note("complete_run", run_id=run_id, **kw)

    def log_fix(self, conn, **kw):
        self._note("log_fix", **kw)
        return 1

    def update_record_field(self, conn, record_id, column, value,
                            table_name="dissertations", scope=None):
        self._note("update_record_field", record_id=record_id, column=column,
                   value=value, table_name=table_name, scope=scope)
        return self.affected

    def record_issue_values(self, conn, issue_id, original_value, normalized_value):
        self._note("record_issue_values", issue_id=issue_id)

    def mark_issue_fixed(self, conn, issue_id, resolved_by=None):
        self._note("mark_issue_fixed", issue_id=issue_id)

    def resolve_issue(self, conn, issue_id, resolved_by, status="resolved"):
        self._note("resolve_issue", issue_id=issue_id, status=status)
        return 1

    # -- rollback path
    def fetch_fixes_by_run(self, conn, run_id, include_rolled_back=False, limit=None):
        self._note("fetch_fixes_by_run", run_id=run_id)
        return [dict(f) for f in self._fixes]

    def fetch_current_value(self, conn, record_id, column,
                            table_name="dissertations", scope=None):
        self._note("fetch_current_value", record_id=record_id, column=column,
                   scope=scope)
        return self._current

    def fetch_run(self, conn, run_id):
        self._note("fetch_run", run_id=run_id)
        return {"id": run_id, "run_type": "fix", "status": "completed",
                "started_at": None, "completed_at": None}

    def mark_rolled_back(self, conn, fix_ids):
        self._note("mark_rolled_back", fix_ids=list(fix_ids))
        return self.rolled_back_rows

    def names(self):
        return [c for c, _ in self.calls]

    def kwargs_of(self, name):
        return [kw for c, kw in self.calls if c == name]


def _run_cli(mod, repo, argv, conn=None):
    conn = conn or _FakeConn()
    original_repo, original_conn = mod.repo, mod.resolve_connection
    mod.repo = repo
    mod.resolve_connection = lambda url=None: (conn, "staging/db")
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = mod.main(argv)
        return code, buf.getvalue(), conn
    finally:
        mod.repo, mod.resolve_connection = original_repo, original_conn


_ISSUE = {"id": 9, "record_id": 123, "field_name": "daraja",
          "issue_code": "INCONSISTENT_DEGREE", "original_value": "phd",
          "normalized_value": "PhD"}


# ── 3. Apply: logging + transaction atomicity ───────────────────────────────
class ApplyFixLoggingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_script("apply_data_quality_fixes")

    def setUp(self):
        self._prev = os.environ.get("DATA_QUALITY_AUTO_FIX_ENABLED")
        os.environ["DATA_QUALITY_AUTO_FIX_ENABLED"] = "1"

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("DATA_QUALITY_AUTO_FIX_ENABLED", None)
        else:
            os.environ["DATA_QUALITY_AUTO_FIX_ENABLED"] = self._prev

    def _repo(self, daraja="phd"):
        return _FakeRepo(issues=[dict(_ISSUE)], record={"id": 123, "daraja": daraja})

    def test_fix_is_logged_with_before_and_after(self):
        repo = self._repo()
        code, out, conn = _run_cli(self.mod, repo, ["--apply"])
        self.assertEqual(code, 0)
        logged = repo.kwargs_of("log_fix")
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged[0]["value_before"], "phd")
        self.assertEqual(logged[0]["value_after"], "PhD")
        self.assertEqual(logged[0]["record_id"], 123)
        self.assertEqual(logged[0]["column_name"], "daraja")
        self.assertEqual(logged[0]["table_name"], "dissertations")
        self.assertEqual(logged[0]["issue_code"], "INCONSISTENT_DEGREE")
        self.assertEqual(logged[0]["issue_id"], 9)
        self.assertEqual(logged[0]["run_id"], repo.next_run_id)
        self.assertIn("rollback_data_quality_fixes.py --run-id 42", out)

    def test_log_is_written_before_the_update(self):
        repo = self._repo()
        _run_cli(self.mod, repo, ["--apply"])
        names = repo.names()
        self.assertLess(names.index("log_fix"), names.index("update_record_field"))

    def test_fix_and_log_share_one_transaction(self):
        repo = self._repo()
        _, _, conn = _run_cli(self.mod, repo, ["--apply"])
        # Exactly one commit for the fix itself, plus one closing the fix run.
        self.assertNotIn("rollback", conn.events)
        self.assertEqual(conn.events.count("commit"), 2)
        names = repo.names()
        self.assertLess(names.index("log_fix"), names.index("complete_run"))

    def test_failed_update_rolls_back_the_log_row_too(self):
        repo = self._repo()
        repo.fail_on = "update_record_field"
        _, out, conn = _run_cli(self.mod, repo, ["--apply"])
        self.assertIn("rollback", conn.events)
        self.assertNotIn("commit", conn.events, "a failed fix must not commit")
        self.assertNotIn("mark_issue_fixed", repo.names())
        self.assertIn("skipped    : 1", out)

    def test_wrong_affected_row_count_rolls_back(self):
        repo = self._repo()
        repo.affected = 2          # would have hit more than one record
        _, out, conn = _run_cli(self.mod, repo, ["--apply"])
        self.assertIn("rollback", conn.events)
        self.assertNotIn("commit", conn.events)
        self.assertIn("skipped    : 1", out)

    def test_failed_first_fix_leaves_no_orphan_run(self):
        # The run row is created inside the first fix's transaction, so a
        # rollback must discard it and the next attempt must create a new one.
        repo = _FakeRepo(issues=[dict(_ISSUE), dict(_ISSUE, id=10, record_id=124)],
                         record={"id": 123, "daraja": "phd"})
        repo.fail_on = "update_record_field"
        _, _, conn = _run_cli(self.mod, repo, ["--apply"])
        self.assertEqual(repo.names().count("create_run"), 2)
        self.assertEqual(conn.events.count("commit"), 0)
        self.assertNotIn("complete_run", repo.names())

    def test_dry_run_writes_nothing(self):
        for flag in (["--dry-run"], ["--preview"], []):
            with self.subTest(flag=flag):
                repo = self._repo()
                _, out, conn = _run_cli(self.mod, repo, flag)
                for forbidden in ("log_fix", "create_run", "update_record_field",
                                  "mark_issue_fixed", "record_issue_values"):
                    self.assertNotIn(forbidden, repo.names())
                self.assertNotIn("commit", conn.events)
                self.assertIn("[123] daraja:", out)
                self.assertIn("no changes written", out)

    def test_no_op_fix_is_not_logged(self):
        # Value already normalized: the issue is closed but nothing is written
        # to the record, so there is nothing to roll back and nothing to log.
        repo = self._repo(daraja="PhD")
        _, out, _ = _run_cli(self.mod, repo, ["--apply"])
        self.assertNotIn("log_fix", repo.names())
        self.assertIn("resolve_issue", repo.names())
        self.assertIn("skipped    : 1", out)

    def test_apply_refused_when_auto_fix_disabled(self):
        os.environ["DATA_QUALITY_AUTO_FIX_ENABLED"] = "0"
        repo = self._repo()
        code, _, _ = _run_cli(self.mod, repo, ["--apply"])
        self.assertEqual(code, 2)
        self.assertEqual(repo.names(), [])


# ── 4. Rollback CLI ─────────────────────────────────────────────────────────
def _fix(**over):
    base = {"id": 7, "run_id": 42, "issue_id": 9, "record_id": 123,
            "table_name": "dissertations", "column_name": "daraja",
            "value_before": "phd", "value_after": "PhD",
            "issue_code": "INCONSISTENT_DEGREE", "applied_at": None,
            "applied_by": "cli", "rolled_back_at": None}
    base.update(over)
    return base


class RollbackCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_script("rollback_data_quality_fixes")

    def test_run_id_is_required(self):
        with self.assertRaises(SystemExit):
            self.mod.main([])

    def test_dry_run_is_the_default(self):
        repo = _FakeRepo(fixes=[_fix()], current=(True, "PhD"))
        code, out, conn = _run_cli(self.mod, repo, ["--run-id", "42"])
        self.assertEqual(code, 0)
        self.assertNotIn("update_record_field", repo.names())
        self.assertNotIn("mark_rolled_back", repo.names())
        self.assertNotIn("commit", conn.events)
        self.assertIn("DRY-RUN", out)
        self.assertIn("would restore : 1", out)
        self.assertIn("no changes written", out)

    def test_confirm_restores_previous_value(self):
        repo = _FakeRepo(fixes=[_fix()], current=(True, "PhD"))
        _, out, conn = _run_cli(self.mod, repo, ["--run-id", "42", "--confirm"])
        upd = repo.kwargs_of("update_record_field")[0]
        self.assertEqual(upd["value"], "phd")        # back to value_before
        self.assertEqual(upd["record_id"], 123)
        self.assertEqual(upd["column"], "daraja")
        self.assertEqual(repo.kwargs_of("mark_rolled_back")[0]["fix_ids"], [7])
        # The issue is no longer 'fixed' — it goes back into the queue.
        self.assertEqual(repo.kwargs_of("resolve_issue")[0]["status"], "open")
        self.assertEqual(conn.events.count("commit"), 1)
        self.assertIn("restored : 1", out)

    def test_restore_and_stamp_share_one_transaction(self):
        repo = _FakeRepo(fixes=[_fix()], current=(True, "PhD"))
        repo.fail_on = "mark_rolled_back"
        _, out, conn = _run_cli(self.mod, repo, ["--run-id", "42", "--confirm"])
        self.assertIn("rollback", conn.events)
        self.assertNotIn("commit", conn.events)
        self.assertIn("restored : 0", out)
        self.assertIn("skipped    : 1", out)

    def test_value_changed_since_the_fix_is_skipped(self):
        # Someone edited the field by hand after the fix -> never overwrite it.
        repo = _FakeRepo(fixes=[_fix()], current=(True, "Doktor"))
        _, out, conn = _run_cli(self.mod, repo, ["--run-id", "42", "--confirm"])
        self.assertNotIn("update_record_field", repo.names())
        self.assertNotIn("mark_rolled_back", repo.names())
        self.assertNotIn("commit", conn.events)
        self.assertIn("mismatched : 1", out)

    def test_null_after_value_matches_null_column(self):
        repo = _FakeRepo(fixes=[_fix(value_after=None, value_before="x")],
                         current=(True, None))
        _, out, _ = _run_cli(self.mod, repo, ["--run-id", "42", "--confirm"])
        self.assertIn("restored : 1", out)
        self.assertEqual(repo.kwargs_of("update_record_field")[0]["value"], "x")

    def test_null_column_is_not_confused_with_the_string_none(self):
        repo = _FakeRepo(fixes=[_fix(value_after="None")], current=(True, None))
        _, out, _ = _run_cli(self.mod, repo, ["--run-id", "42", "--confirm"])
        self.assertIn("mismatched : 1", out)

    def test_deleted_record_is_skipped(self):
        repo = _FakeRepo(fixes=[_fix()], current=(False, None))
        _, out, conn = _run_cli(self.mod, repo, ["--run-id", "42", "--confirm"])
        self.assertNotIn("update_record_field", repo.names())
        self.assertIn("skipped    : 1", out)
        self.assertNotIn("commit", conn.events)

    def test_already_reverted_fixes_are_not_fetched(self):
        repo = _FakeRepo(fixes=[], current=(True, "PhD"))
        _, out, _ = _run_cli(self.mod, repo, ["--run-id", "42", "--confirm"])
        self.assertIn("nothing to roll back", out)
        self.assertIn("restored : 0", out)

    def test_double_rollback_of_one_fix_does_not_double_count(self):
        repo = _FakeRepo(fixes=[_fix()], current=(True, "PhD"))
        repo.rolled_back_rows = 0     # row was stamped by a concurrent run
        _, out, conn = _run_cli(self.mod, repo, ["--run-id", "42", "--confirm"])
        self.assertIn("rollback", conn.events)
        self.assertIn("restored : 0", out)

    def test_mixed_batch_reverts_only_matching_rows(self):
        fixes = [_fix(id=8, record_id=124, value_after="PhD"), _fix(id=7)]
        repo = _FakeRepo(fixes=fixes, current=(True, "PhD"))
        _, out, _ = _run_cli(self.mod, repo, ["--run-id", "42", "--confirm"])
        self.assertIn("restored : 2", out)
        self.assertEqual([kw["fix_ids"] for kw in repo.kwargs_of("mark_rolled_back")],
                         [[8], [7]])

    def test_dry_run_and_confirm_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            self.mod.main(["--run-id", "1", "--dry-run", "--confirm"])


# ── 5. DB-gated round trip (staging only) ───────────────────────────────────
@unittest.skipUnless(TEST_DB, "TEST_DATABASE_URL not set (staging-only test)")
class FixLogDbGatedTests(unittest.TestCase):
    """Applies migrations 004 + 005 and round-trips the fix log. Cleans up the
    synthetic run it created (the fix rows are removed first — run_id is
    ON DELETE RESTRICT by design)."""

    @classmethod
    def setUpClass(cls):
        import psycopg2
        cls.conn = psycopg2.connect(TEST_DB)
        cls.conn.autocommit = False
        for name in ("004_create_data_quality_tables.sql",
                     "005_create_data_quality_fix_log.sql"):
            with open(os.path.join(_REPO_ROOT, "migrations", name), encoding="utf-8") as f:
                sql = f.read()
            with cls.conn.cursor() as cur:
                cur.execute(sql)
            cls.conn.commit()
        cls._run_ids = []

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "_run_ids", None):
            with cls.conn.cursor() as cur:
                cur.execute("DELETE FROM data_quality_fix_log WHERE run_id = ANY(%s)",
                            (cls._run_ids,))
                cur.execute("DELETE FROM data_quality_runs WHERE id = ANY(%s)",
                            (cls._run_ids,))
            cls.conn.commit()
        cls.conn.close()

    def test_migration_is_idempotent(self):
        with open(MIGRATION, encoding="utf-8") as f:
            sql = f.read()
        with self.conn.cursor() as cur:
            cur.execute(sql)
        self.conn.commit()
        from data_quality import repository as repo
        self.assertTrue(repo.fix_log_exists(self.conn))

    def test_log_fetch_and_mark_rolled_back(self):
        from data_quality import repository as repo
        run_id = repo.create_run(self.conn, "fix", total_records=1,
                                 metadata={"mode": "test"})
        self.__class__._run_ids.append(run_id)
        fix_id = repo.log_fix(self.conn, run_id=run_id, record_id=999001,
                              column_name="daraja", value_before="phd",
                              value_after="PhD", issue_code="INCONSISTENT_DEGREE",
                              applied_by="cli:test")
        self.conn.commit()

        rows = repo.fetch_fixes_by_run(self.conn, run_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], fix_id)
        self.assertEqual(rows[0]["value_before"], "phd")
        self.assertIsNone(rows[0]["rolled_back_at"])

        self.assertEqual(repo.mark_rolled_back(self.conn, [fix_id]), 1)
        self.conn.commit()
        # Second pass stamps nothing, and the row drops out of the default read.
        self.assertEqual(repo.mark_rolled_back(self.conn, [fix_id]), 0)
        self.assertEqual(repo.fetch_fixes_by_run(self.conn, run_id), [])
        self.assertEqual(
            len(repo.fetch_fixes_by_run(self.conn, run_id, include_rolled_back=True)), 1)
        self.conn.commit()

    def test_null_before_value_round_trips_as_null(self):
        from data_quality import repository as repo
        run_id = repo.create_run(self.conn, "fix", total_records=1)
        self.__class__._run_ids.append(run_id)
        repo.log_fix(self.conn, run_id=run_id, record_id=999002,
                     column_name="link", value_before=None, value_after="http://x")
        self.conn.commit()
        row = repo.fetch_fixes_by_run(self.conn, run_id)[0]
        self.assertIsNone(row["value_before"])
        self.assertEqual(row["value_after"], "http://x")


if __name__ == "__main__":
    unittest.main()
