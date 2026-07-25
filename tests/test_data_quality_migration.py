"""Static safety checks for migrations/004_create_data_quality_tables.sql.

No database is touched — we parse the SQL text and assert the safety invariants
the spec requires (transaction, idempotent guards, no destructive statements,
expected tables). DB-level application is verified separately on staging via the
runbook (test_data_quality_dbgated.py, skipped without TEST_DATABASE_URL).
"""
import os
import re
import unittest

# This test lives in tests/, so the repo root is one level up.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(_REPO_ROOT, "migrations",
                         "004_create_data_quality_tables.sql")


def _strip_comments(sql: str) -> str:
    out = []
    for line in sql.splitlines():
        # drop full-line and trailing -- comments
        idx = line.find("--")
        out.append(line if idx == -1 else line[:idx])
    return "\n".join(out)


class MigrationSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(MIGRATION, encoding="utf-8") as f:
            cls.raw = f.read()
        cls.code = _strip_comments(cls.raw)
        cls.upper = cls.code.upper()

    def test_single_transaction(self):
        self.assertIn("BEGIN;", self.upper)
        self.assertIn("COMMIT;", self.upper)

    def test_expected_tables(self):
        for t in ("data_quality_runs", "data_quality_issues", "duplicate_candidates"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {t}".upper(), self.upper, t)

    def test_all_creates_are_idempotent(self):
        creates = re.findall(r"CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS)?", self.upper)
        self.assertTrue(creates)
        for guard in creates:
            self.assertTrue(guard, "every CREATE TABLE must be IF NOT EXISTS")
        idx_guards = re.findall(r"CREATE\s+INDEX\s+(IF\s+NOT\s+EXISTS)?", self.upper)
        for guard in idx_guards:
            self.assertTrue(guard, "every CREATE INDEX must be IF NOT EXISTS")

    def test_no_destructive_statements(self):
        # No DROP / TRUNCATE / DELETE, and no UPDATE of existing data — the
        # rollback DROP lives only inside a comment (stripped above).
        for bad in ("DROP TABLE", "DROP INDEX", "TRUNCATE", "DELETE FROM", "UPDATE "):
            self.assertNotIn(bad, self.upper, f"unexpected {bad!r} in executable SQL")

    def test_does_not_touch_production_tables(self):
        for prod in ("ALTER TABLE DISSERTATIONS", "DROP TABLE DISSERTATIONS",
                     "ALTER TABLE USERS", "ALTER TABLE IMPORT_AUDIT_LOG"):
            self.assertNotIn(prod, self.upper)

    def test_no_extension_creation(self):
        self.assertNotIn("CREATE EXTENSION", self.upper)


if __name__ == "__main__":
    unittest.main()
