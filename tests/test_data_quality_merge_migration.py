"""Static safety checks for migrations/006_dissertation_merge_support.sql.

No database is touched — same approach as test_data_quality_migration.py for
migration 004. DB-level application is verified separately on staging via
tests/test_merge_duplicates_dbgated.py.
"""
import os
import re
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(_REPO_ROOT, "migrations",
                         "006_dissertation_merge_support.sql")


def _strip_comments(sql: str) -> str:
    out = []
    for line in sql.splitlines():
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

    def test_expected_table(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS DATA_QUALITY_FIX_LOG",
                      self.upper)

    def test_expected_columns_on_dissertations(self):
        for col in ("MERGED_INTO_ID", "MERGED_AT"):
            self.assertIn(col, self.upper)

    def test_all_creates_are_idempotent(self):
        creates = re.findall(r"CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS)?", self.upper)
        self.assertTrue(creates)
        for guard in creates:
            self.assertTrue(guard, "every CREATE TABLE must be IF NOT EXISTS")
        idx_guards = re.findall(r"CREATE\s+INDEX\s+(IF\s+NOT\s+EXISTS)?", self.upper)
        self.assertTrue(idx_guards)
        for guard in idx_guards:
            self.assertTrue(guard, "every CREATE INDEX must be IF NOT EXISTS")

    def test_every_alter_only_adds_a_nullable_column(self):
        """The only touch to an existing table must be ADD COLUMN IF NOT
        EXISTS — no DROP/ALTER of an existing column, no NOT NULL added."""
        alters = re.findall(r"ALTER TABLE .+?;", self.upper, re.DOTALL)
        self.assertTrue(alters)
        for line in alters:
            self.assertIn("ADD COLUMN IF NOT EXISTS", line, line)
            self.assertNotIn("NOT NULL", line, line)
            self.assertNotIn("DROP", line, line)

    def test_no_destructive_statements(self):
        for bad in ("DROP TABLE", "DROP INDEX", "DROP COLUMN", "TRUNCATE",
                    "DELETE FROM", "UPDATE "):
            self.assertNotIn(bad, self.upper, f"unexpected {bad!r} in executable SQL")

    def test_no_extension_creation(self):
        self.assertNotIn("CREATE EXTENSION", self.upper)

    def test_no_not_null_constraint_added_to_dissertations(self):
        # Both new columns must stay optional — merging never becomes
        # mandatory metadata for a row that predates this migration.
        for col in ("merged_into_id", "merged_at"):
            self.assertNotIn(f"{col.upper()} INTEGER NOT NULL", self.upper)
            self.assertNotIn(f"{col.upper()} TIMESTAMPTZ NOT NULL", self.upper)


if __name__ == "__main__":
    unittest.main()
