import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("SESSION_SECRET", "phase-0-test-secret")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "phase-0-telegram-token")
# Empty values prevent python-dotenv from inheriting a parent workspace's .env.
os.environ["DATABASE_URL"] = ""
os.environ["POSTGRES_URL"] = ""

import app as app_module
import auth
import cabinet
import data as data_module


class FakeTelegramCursor:
    def __init__(self):
        self.executed = []
        self._row = None

    def execute(self, query, params=None):
        normalized = " ".join(query.split())
        self.executed.append((normalized, params))
        if "FROM users WHERE telegram_id = %s" in normalized:
            self._row = None
        elif "RETURNING id, username, email" in normalized:
            self._row = (99, "tg_123456", "telegram_123456@auth.local")
        else:
            self._row = None

    def fetchone(self):
        return self._row

    def close(self):
        return None


class FakeTelegramConnection:
    def __init__(self):
        self.cursor_instance = FakeTelegramCursor()
        self.commits = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def close(self):
        return None


class SecurityPhase0Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.flask_app = app_module.app
        cls.flask_app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=True,
        )
        cls.original_user_loader = app_module.login_manager._user_callback
        app_module.login_manager._user_callback = cls._load_test_user

    @classmethod
    def tearDownClass(cls):
        app_module.login_manager._user_callback = cls.original_user_loader

    @staticmethod
    def _load_test_user(user_id):
        is_admin = str(user_id) == "2"
        return app_module.User(
            int(user_id),
            "admin" if is_admin else "regular",
            "admin@example.test" if is_admin else "regular@example.test",
            is_admin=is_admin,
        )

    def setUp(self):
        self.client = self.flask_app.test_client()

    def csrf_token(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        match = re.search(
            rb'<meta name="csrf-token" content="([^"]+)"', response.data
        )
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True

    def test_anonymous_upload_redirects_and_regular_user_gets_403(self):
        anonymous = self.client.get("/upload", follow_redirects=False)
        self.assertIn(anonymous.status_code, (302, 303))

        self.login(1)
        forbidden = self.client.get("/upload")
        self.assertEqual(forbidden.status_code, 403)

    def test_admin_can_open_upload_page(self):
        self.login(2)
        response = self.client.get("/upload")
        self.assertEqual(response.status_code, 200)

    def test_telegram_username_collision_does_not_select_admin(self):
        token = "phase-0-telegram-token"
        auth.TELEGRAM_BOT_TOKEN = token
        payload = {
            "id": 123456,
            "username": "admin",
            "first_name": "Collision",
            "auth_date": int(time.time()),
        }
        check_string = "\n".join(f"{key}={value}" for key, value in sorted(payload.items()))
        secret = hashlib.sha256(token.encode()).digest()
        payload["hash"] = hmac.new(
            secret, check_string.encode(), hashlib.sha256
        ).hexdigest()
        connection = FakeTelegramConnection()

        with patch.object(auth, "get_connection", return_value=connection):
            response = self.client.post("/login/telegram", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        statements = connection.cursor_instance.executed
        self.assertFalse(any("WHERE username" in sql for sql, _ in statements))
        insert = next(item for item in statements if item[0].startswith("INSERT INTO users"))
        self.assertEqual(insert[1][0], "tg_123456")
        self.assertFalse(insert[1][3])
        self.assertEqual(insert[1][4], 123456)

    def test_telegram_id_has_partial_unique_index(self):
        migration = (ROOT / "migrations" / "001_add_users_telegram_id.sql").read_text()
        self.assertIn("CREATE UNIQUE INDEX", migration)
        self.assertIn("ON users (telegram_id)", migration)
        self.assertIn("WHERE telegram_id IS NOT NULL", migration)

    def test_phase0_migrations_are_transactional_and_document_rollback(self):
        for migration_name in (
            "001_add_users_telegram_id.sql",
            "002_create_import_audit_log.sql",
        ):
            migration = (ROOT / "migrations" / migration_name).read_text()
            self.assertTrue(migration.lstrip().startswith("BEGIN;"))
            self.assertIn("\nCOMMIT;", migration)
            self.assertIn("-- Rollback", migration)
            self.assertIn("-- BEGIN;", migration)
            self.assertIn("-- COMMIT;", migration)

    def test_deploy_script_has_no_embedded_database_credentials(self):
        deploy = (ROOT / "deploy" / "deploy.sh").read_text()
        self.assertNotRegex(
            deploy,
            r"postgres(?:ql)?://[^\s:@]+:[^\s@]+@",
        )
        self.assertIn('DATABASE_URL="${DATABASE_URL:-}"', deploy)

    def test_cabinet_post_without_csrf_is_rejected(self):
        with self.client.session_transaction() as session:
            session["cabinet_user_id"] = 10
        response = self.client.post("/cabinet/api/profile/save", json={})
        self.assertEqual(response.status_code, 400)

    def test_cabinet_post_with_csrf_succeeds(self):
        token = self.csrf_token()
        with self.client.session_transaction() as session:
            session["cabinet_user_id"] = 10
        with patch.object(
            cabinet,
            "current_cabinet_user",
            return_value={"id": 10, "olim_name": "Test Olim"},
        ):
            response = self.client.post(
                "/cabinet/api/profile/save",
                json={},
                headers={"X-CSRFToken": token},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])

    def test_admin_write_without_csrf_is_rejected(self):
        self.login(2)
        response = self.client.post(
            "/admin/api/block-user",
            json={"ip_address": "203.0.113.10", "reason": "test"},
        )
        self.assertEqual(response.status_code, 400)

    def test_heatmap_uses_text_nodes_for_database_labels(self):
        source = (ROOT / "static" / "js" / "heatmap.js").read_text()
        self.assertNotIn("innerHTML", source)
        self.assertNotIn("insertAdjacentHTML", source)
        self.assertIn("textContent", source)
        self.assertIn("replaceChildren", source)
        malicious = '<img src=x onerror="window.pwned=true"><script>alert(1)</script>'
        self.assertNotIn(malicious, source)

    def test_oversized_csv_is_rejected(self):
        token = self.csrf_token()
        self.login(2)
        oversized = io.BytesIO(b"a" * (15 * 1024 * 1024 + 1))
        response = self.client.post(
            "/upload",
            data={"file": (oversized, "large.csv", "text/csv")},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(response.status_code, 413)

    def test_fake_image_is_rejected(self):
        token = self.csrf_token()
        with self.client.session_transaction() as session:
            session["cabinet_user_id"] = 10
        response = self.client.post(
            "/cabinet/api/avatar/upload",
            data={"photo": (io.BytesIO(b"not a real image"), "avatar.png", "image/png")},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(response.status_code, 422)

    def test_production_cookie_and_headers(self):
        script = r"""
import json
from flask import session
from app import app

@app.route('/_phase0_cookie')
def phase0_cookie():
    session['phase0'] = True
    return 'ok'

response = app.test_client().get('/_phase0_cookie', base_url='https://localhost')
print(json.dumps({
    'cookie': response.headers.get('Set-Cookie', ''),
    'hsts': response.headers.get('Strict-Transport-Security', ''),
    'csp': response.headers.get('Content-Security-Policy', ''),
    'nosniff': response.headers.get('X-Content-Type-Options', ''),
    'http_status': app.test_client().get('/login', base_url='http://localhost').status_code,
}))
"""
        env = os.environ.copy()
        env.update(
            APP_ENV="production",
            SESSION_SECRET="phase-0-production-test-secret",
            DATABASE_URL="postgresql://127.0.0.1:1/phase0",
        )
        env["POSTGRES_URL"] = ""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        details = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertIn("Secure", details["cookie"])
        self.assertIn("HttpOnly", details["cookie"])
        self.assertIn("SameSite=Lax", details["cookie"])
        self.assertTrue(details["hsts"])
        self.assertTrue(details["csp"])
        self.assertEqual(details["nosniff"], "nosniff")
        self.assertEqual(details["http_status"], 400)

    def test_production_refuses_missing_session_secret(self):
        env = os.environ.copy()
        env["APP_ENV"] = "production"
        # Empty existing values prevent python-dotenv from inheriting a parent
        # workspace's ignored .env while exercising the missing-secret path.
        env["SESSION_SECRET"] = ""
        env["SECRET_KEY"] = ""
        env["DATABASE_URL"] = "postgresql://127.0.0.1:1/phase0"
        result = subprocess.run(
            [sys.executable, "-c", "import app"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("production uchun majburiy", result.stderr)

    def test_production_refuses_missing_database_url(self):
        env = os.environ.copy()
        env.update(
            APP_ENV="production",
            SESSION_SECRET="phase-0-production-test-secret",
            SECRET_KEY="",
            DATABASE_URL="",
            POSTGRES_URL="",
        )
        result = subprocess.run(
            [sys.executable, "-c", "import app"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DATABASE_URL production uchun majburiy", result.stderr)

    def test_production_startup_does_not_connect_or_run_migrations(self):
        script = r"""
import json
import psycopg2
from psycopg2 import pool

calls = []
def forbidden_connection(*args, **kwargs):
    calls.append([args, kwargs])
    raise RuntimeError('database connection attempted during production startup')

psycopg2.connect = forbidden_connection
pool.ThreadedConnectionPool = forbidden_connection
import app
print(json.dumps({'connection_attempts': len(calls)}))
"""
        env = os.environ.copy()
        env.update(
            APP_ENV="production",
            SESSION_SECRET="phase-0-production-test-secret",
            DATABASE_URL="postgresql://127.0.0.1:1/phase0",
            POSTGRES_URL="",
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        details = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(details["connection_attempts"], 0)

    def test_testing_environment_blocks_external_database_connections(self):
        external_dsn = "postgresql://external.example.invalid/phase0"
        with patch.dict(
            os.environ,
            {"APP_ENV": "testing", "DATABASE_URL": external_dsn},
        ):
            with self.assertRaisesRegex(RuntimeError, "disabled in testing"):
                data_module.get_connection()

    def test_database_exception_is_not_returned_to_client(self):
        marker = "secret_table constraint db_password"
        with patch.object(
            app_module,
            "_collab_full",
            side_effect=RuntimeError(marker),
        ):
            response = self.client.get("/api/collaboration?mode=full")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(marker, response.get_data(as_text=True))
        self.assertTrue(response.headers.get("X-Request-ID"))


if __name__ == "__main__":
    unittest.main()
