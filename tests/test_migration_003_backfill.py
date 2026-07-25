"""Static safety tests for migrations/003_backfill_users_telegram_id.sql.

No database is used. These assert, at the SQL-source level, that the backfill
migration keeps the invariants the hotfix depends on:

  * telegram_id is sourced ONLY from telegram_chat_id (no username / email
    fallback anywhere);
  * only NULL telegram_id rows are touched (idempotent WHERE clause);
  * the run is transactional (BEGIN/COMMIT) and Postgres-guarded (DO $$ /
    RAISE EXCEPTION) against duplicate telegram_id;
  * the dangerous blanket "SET telegram_id = NULL WHERE
    telegram_id = telegram_chat_id" is NOT recommended as a rollback.

A separate executed logic test (SQLite) covers runtime behaviour; this file is
the cheap regression guard that ships with the migration.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "migrations" / "003_backfill_users_telegram_id.sql"


class Migration003SafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = SQL_PATH.read_text(encoding="utf-8")
        # Executable statements only (strip -- line comments) so assertions
        # about the actual DML never match explanatory prose.
        cls.code = "\n".join(
            line for line in cls.sql.splitlines()
            if not line.lstrip().startswith("--")
        )
        cls.code_norm = " ".join(cls.code.split())

    def test_file_exists_and_nonempty(self):
        self.assertTrue(SQL_PATH.is_file())
        self.assertGreater(len(self.sql.strip()), 0)

    def test_transactional(self):
        self.assertIn("BEGIN;", self.code)
        self.assertIn("COMMIT;", self.code)

    def test_has_do_guard_and_raise_exception(self):
        self.assertIn("DO $$", self.code)
        self.assertIn("RAISE EXCEPTION", self.code)

    def test_backfill_copies_only_from_chat_id(self):
        # The single UPDATE must set telegram_id = telegram_chat_id.
        self.assertRegex(
            self.code_norm,
            r"UPDATE users SET telegram_id\s*=\s*telegram_chat_id",
        )

    def test_only_null_rows_touched_idempotent(self):
        self.assertIn("telegram_id IS NULL", self.code_norm)
        self.assertIn("telegram_chat_id IS NOT NULL", self.code_norm)

    def test_no_username_or_email_fallback(self):
        # telegram_id must never be assigned from username/email columns.
        forbidden = [
            r"telegram_id\s*=\s*username",
            r"telegram_id\s*=\s*email",
            r"COALESCE\([^)]*username",
            r"COALESCE\([^)]*email",
        ]
        for pat in forbidden:
            self.assertNotRegex(self.code_norm, pat, msg=f"forbidden fallback: {pat}")

    def test_verifies_unique_index(self):
        self.assertIn("uq_users_telegram_id", self.code)

    def test_does_not_recommend_blanket_null_rollback(self):
        # The blanket column-comparison rollback must not appear as executable
        # SQL. It may only be mentioned inside comments as an anti-pattern.
        self.assertNotRegex(
            self.code_norm,
            r"UPDATE users SET telegram_id\s*=\s*NULL\s+WHERE\s+telegram_id\s*=\s*telegram_chat_id",
        )

    def test_rollback_points_to_backup_or_explicit_ids(self):
        # Rollback guidance (in comments) must steer to backup restore or an
        # explicit id list, not a broad predicate.
        lowered = self.sql.lower()
        self.assertTrue(
            "restore from backup" in lowered or "pg_restore" in lowered,
            "rollback should reference backup restore",
        )
        self.assertIn("id in (", lowered)


if __name__ == "__main__":
    unittest.main()
