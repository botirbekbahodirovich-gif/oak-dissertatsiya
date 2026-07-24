"""Release-integration guards for the data-quality + security/onboarding merge.

Asserts the invariants that this integration must not regress:
  - the security APP_ENV=testing DB guard in data.py is preserved;
  - the data-quality audit script stays read-only / dry-run by default;
  - the DRAFT canonical-entity migration is never presented as applied;
  - the unittest suite still discovers every tests/test_*.py module after
    tests/__init__.py was added.

Pure stdlib unittest, no network, no real database.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("SESSION_SECRET", "integration-test-secret")
os.environ["DATABASE_URL"] = ""
os.environ["POSTGRES_URL"] = ""

import data as data_module


class SecurityGuardPreservedTests(unittest.TestCase):
    def test_data_py_testing_guard_blocks_connection(self):
        # The security branch's APP_ENV=testing guard must survive the
        # data-quality integration untouched.
        os.environ["APP_ENV"] = "testing"
        with self.assertRaises(RuntimeError):
            data_module.get_connection()

    def test_get_connection_source_still_guards_testing(self):
        import inspect
        src = inspect.getsource(data_module.get_connection)
        self.assertIn("testing", src.lower())


class AuditScriptReadOnlyTests(unittest.TestCase):
    SCRIPT = ROOT / "scripts" / "data_quality_audit.py"

    def test_no_write_flag_or_write_sql(self):
        src = self.SCRIPT.read_text(encoding="utf-8")
        # No opt-in write mode of any kind.
        for needle in ("--apply", "--write", "--commit"):
            self.assertNotIn(needle, src, msg=f"audit script must not offer {needle}")
        # No mutating SQL / commit in a read-only detector.
        upper = src.upper()
        for sql in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
            self.assertNotIn(sql, upper, msg=f"audit script must not run {sql!r}")
        self.assertNotIn(".COMMIT()", upper)
        # It does roll back its read-only session.
        self.assertIn("rollback", src)

    def test_help_runs_without_db(self):
        env = dict(os.environ, APP_ENV="testing", DATABASE_URL="", POSTGRES_URL="")
        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), "--help"],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("usage", (proc.stdout + proc.stderr).lower())


class DraftMigrationTests(unittest.TestCase):
    def test_canonical_schema_is_draft_only(self):
        draft = ROOT / "migrations" / "drafts" / "2026_phase1_canonical_entities.sql"
        self.assertTrue(draft.exists(), "canonical-entity SQL must live under migrations/drafts/")
        text = draft.read_text(encoding="utf-8")
        self.assertIn("DRAFT — DO NOT APPLY", text)
        # It documents the review preconditions required before promotion.
        self.assertIn("HUMAN REVIEW REQUIRED", text)
        self.assertIn("Rollback", text)

    def test_no_canonical_tables_in_applied_migrations(self):
        # A DRAFT table must never leak into a top-level (auto-applyable) migration.
        applied = list((ROOT / "migrations").glob("*.sql"))
        for f in applied:
            body = f.read_text(encoding="utf-8").lower()
            self.assertNotIn(
                "create table if not exists canonical_researchers", body,
                msg=f"draft schema leaked into applied migration {f.name}",
            )


class TestDiscoveryTests(unittest.TestCase):
    def test_all_test_modules_are_discoverable(self):
        loader = unittest.TestLoader()
        suite = loader.discover(str(ROOT / "tests"), pattern="test_*.py")
        names = {tc.__class__.__module__ for tc in _iter_cases(suite)}
        for expected in (
            "test_security_phase0",
            "test_onboarding_identity",
            "test_data_quality",
            "test_create_admin",
            "test_staging_infrastructure",
            "test_integration_release",
        ):
            self.assertTrue(
                any(n.endswith(expected) for n in names),
                msg=f"discovery did not find {expected}; found {sorted(names)}",
            )


def _iter_cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_cases(item)
        else:
            yield item


if __name__ == "__main__":
    unittest.main()
