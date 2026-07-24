import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT / "deploy" / "deploy-staging.sh.example"
CHECK_SCRIPT = ROOT / "scripts" / "check_staging_environment.py"
FULL_SHA = "2d5ef29b1f1e28a7d8fd379165e93752fc3088bb"
STAGING_DSN = (
    "postgresql://staging_user:unique-test-password@"
    "db-staging.example.invalid/olimlar_staging"
)
STAGING_SECRET = "unique-staging-session-secret-value-1234567890"


def _load_checker():
    spec = importlib.util.spec_from_file_location("staging_checker", CHECK_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CHECKER = _load_checker()


class _FakeCursor:
    def __init__(self):
        self.queries = []
        self._query_number = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        self.queries.append((query, params))
        self._query_number += 1

    def fetchone(self):
        return ("olimlar_staging", "staging_user")

    def fetchall(self):
        return [(name,) for name in sorted(CHECKER.REQUIRED_TABLES)]


class _FakeConnection:
    def __init__(self):
        self.cursor_instance = _FakeCursor()
        self.readonly = None
        self.autocommit = None
        self.closed = False

    def set_session(self, *, readonly, autocommit):
        self.readonly = readonly
        self.autocommit = autocommit

    def cursor(self):
        return self.cursor_instance

    def close(self):
        self.closed = True


class StagingInfrastructureTests(unittest.TestCase):
    def _safe_env(self, **updates):
        env = os.environ.copy()
        env.update(
            {
                "APP_ENV": "production",
                "DATABASE_URL": STAGING_DSN,
                "SESSION_SECRET": STAGING_SECRET,
            }
        )
        env.update(updates)
        return env

    def _run_deploy(self, *args, env=None):
        return subprocess.run(
            ["bash", str(DEPLOY_SCRIPT), *args],
            cwd=ROOT,
            env=env or self._safe_env(),
            text=True,
            capture_output=True,
            check=False,
        )

    def test_staging_path_is_accepted_in_dry_run(self):
        result = self._run_deploy("--sha", FULL_SHA)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("target_path=/opt/olimlar-staging", result.stdout)
        self.assertIn("execution=disabled", result.stdout)

    def test_production_path_is_rejected(self):
        result = self._run_deploy(
            "--sha",
            FULL_SHA,
            env=self._safe_env(STAGING_APP_DIR="/opt/olimlar-uz"),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("production app path rejected", result.stderr)

    def test_production_service_is_rejected(self):
        result = self._run_deploy(
            "--sha",
            FULL_SHA,
            env=self._safe_env(STAGING_SERVICE="olimlar"),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("production service rejected", result.stderr)

    def test_secret_values_are_never_logged(self):
        result = self._run_deploy("--sha", FULL_SHA)
        combined = result.stdout + result.stderr
        self.assertNotIn(STAGING_DSN, combined)
        self.assertNotIn("unique-test-password", combined)
        self.assertNotIn(STAGING_SECRET, combined)
        self.assertIn("DATABASE_URL=set", combined)
        self.assertIn("SESSION_SECRET=set", combined)

    def test_sha_is_required(self):
        result = self._run_deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reviewed SHA is required", result.stderr)

    def test_migrations_are_manual_only(self):
        source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("migrations/001_add_users_telegram_id.sql", source)
        self.assertNotIn("migrations/002_create_import_audit_log.sql", source)
        self.assertNotRegex(source, r"\bpsql\b")
        result = self._run_deploy("--sha", FULL_SHA)
        self.assertIn("migration_mode=manual-only", result.stdout)

    def test_restart_requires_explicit_flags(self):
        result = self._run_deploy("--sha", FULL_SHA)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("restart=disabled", result.stdout)

        rejected = self._run_deploy("--sha", FULL_SHA, "--restart")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("--restart requires --execute", rejected.stderr)

    def test_production_like_database_url_is_rejected(self):
        env = self._safe_env(
            DATABASE_URL=(
                "postgresql://app:never-print-this@"
                "db.olimlar.uz/olimlar"
            )
        )
        result = subprocess.run(
            ["python3", str(CHECK_SCRIPT), "--config-only"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertNotIn(env["DATABASE_URL"], combined)
        self.assertNotIn("never-print-this", combined)
        self.assertIn("production", combined.lower())

    def test_database_checker_executes_selects_only(self):
        connection = _FakeConnection()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = CHECKER.main(
                [],
                environ={
                    "APP_ENV": "production",
                    "DATABASE_URL": STAGING_DSN,
                    "SESSION_SECRET": STAGING_SECRET,
                },
                connect_factory=lambda _dsn: connection,
            )
        self.assertEqual(result, 0, stderr.getvalue())
        self.assertTrue(connection.readonly)
        self.assertTrue(connection.autocommit)
        self.assertTrue(connection.closed)
        for query, _params in connection.cursor_instance.queries:
            self.assertTrue(query.lstrip().upper().startswith("SELECT"), query)
        combined = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(STAGING_DSN, combined)
        self.assertNotIn(STAGING_SECRET, combined)

    def test_service_and_nginx_are_isolated(self):
        service = (
            ROOT / "deploy" / "olimlar-staging.service.example"
        ).read_text(encoding="utf-8")
        nginx = (
            ROOT / "deploy" / "nginx-staging.conf.example"
        ).read_text(encoding="utf-8")
        self.assertIn("WorkingDirectory=/opt/olimlar-staging", service)
        self.assertIn("127.0.0.1:8001", service)
        self.assertIn("SyslogIdentifier=olimlar-staging", service)
        self.assertIn("server_name staging.olimlar.uz;", nginx)
        self.assertIn("proxy_pass http://127.0.0.1:8001;", nginx)
        self.assertNotIn("proxy_pass http://127.0.0.1:8000;", nginx)


if __name__ == "__main__":
    unittest.main()
