import os
import unittest
from unittest.mock import patch

from flask import Flask

os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("SESSION_SECRET", "staging-login-test-secret")
os.environ["DATABASE_URL"] = ""
os.environ["POSTGRES_URL"] = ""

import staging_login

STAGING_DB = "postgresql://u:p@localhost:5432/olimlar_staging_2610"
PROD_DB = "postgresql://u:p@localhost:5432/olimlar_db"


def _routes(app):
    return [r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/_staging")]


class RegistrationGateTests(unittest.TestCase):
    def _app(self, env):
        app = Flask(__name__)
        return app, staging_login.register_staging_login(app, env)

    def test_no_flag_registers_nothing(self):
        for env in ({}, {"DATABASE_URL": STAGING_DB},
                    {"STAGING_TEST_LOGIN": "0", "DATABASE_URL": STAGING_DB},
                    {"STAGING_TEST_LOGIN": "true", "DATABASE_URL": STAGING_DB}):
            app, registered = self._app(env)
            self.assertFalse(registered)
            self.assertEqual(_routes(app), [])

    def test_flag_on_production_database_registers_nothing(self):
        for url in (PROD_DB, "", "postgresql://u:p@staging.example.com/olimlar_db"):
            app, registered = self._app({"STAGING_TEST_LOGIN": "1", "DATABASE_URL": url})
            self.assertFalse(registered)
            self.assertEqual(_routes(app), [])

    def test_flag_on_staging_database_registers_routes(self):
        app, registered = self._app({"STAGING_TEST_LOGIN": "1", "DATABASE_URL": STAGING_DB})
        self.assertTrue(registered)
        self.assertIn("/_staging/test-login/<role>", _routes(app))

    def test_old_staging_database_name_is_also_accepted_by_marker_only(self):
        self.assertTrue(staging_login.staging_login_enabled(
            {"STAGING_TEST_LOGIN": "1", "DATABASE_URL": "postgresql://u@h/olimlar_staging_db"}))


class RequestGuardTests(unittest.TestCase):
    def setUp(self):
        from werkzeug.middleware.proxy_fix import ProxyFix
        self.app = Flask(__name__)
        self.app.secret_key = "x"
        # Same ProxyFix settings as app.py: it trusts one X-Forwarded-For hop.
        self.app.wsgi_app = ProxyFix(self.app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
        staging_login.register_staging_login(
            self.app, {"STAGING_TEST_LOGIN": "1", "DATABASE_URL": STAGING_DB})
        patcher = patch.object(staging_login, "_current_database",
                               return_value="olimlar_staging_2610")
        self.current_db = patcher.start()
        self.addCleanup(patcher.stop)

    def _get(self, remote, headers=None):
        return self.app.test_client().get("/_staging/test-login", headers=headers or {},
                                          environ_base={"REMOTE_ADDR": remote})

    def test_loopback_peer_is_allowed(self):
        self.assertEqual(self._get("127.0.0.1").status_code, 200)

    def test_non_loopback_is_404(self):
        self.assertEqual(self._get("203.0.113.9").status_code, 404)

    def test_spoofed_x_forwarded_for_cannot_fake_loopback(self):
        # ProxyFix would turn request.remote_addr into 127.0.0.1 here.
        resp = self._get("203.0.113.9", {"X-Forwarded-For": "127.0.0.1"})
        self.assertEqual(resp.status_code, 404)

    def test_any_proxy_header_is_404_even_from_loopback(self):
        for header in ("X-Forwarded-For", "X-Real-IP", "Forwarded", "CF-Connecting-IP"):
            resp = self._get("127.0.0.1", {header: "203.0.113.9"})
            self.assertEqual(resp.status_code, 404, header)

    def test_database_name_comes_from_live_connection_not_url(self):
        # DATABASE_URL says staging, but the connection is really production.
        self.current_db.return_value = "olimlar_db"
        self.assertEqual(self._get("127.0.0.1").status_code, 404)

    def test_database_check_fails_closed_on_error(self):
        self.current_db.side_effect = RuntimeError("no database")
        self.assertEqual(self._get("127.0.0.1").status_code, 404)

    def test_unknown_role_is_404(self):
        resp = self.app.test_client().post(
            "/_staging/test-login/root", environ_base={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(resp.status_code, 404)

    def test_accounts_are_fixed_test_invalid(self):
        self.assertEqual(set(staging_login.ACCOUNTS), {"user", "premium", "admin"})
        for username, email, _admin, _premium in staging_login.ACCOUNTS.values():
            self.assertTrue(email.endswith("@test.invalid"))
            self.assertTrue(username.startswith("test_"))

    def test_loopback_login_is_logged(self):
        import flask_login
        flask_login.LoginManager(self.app)
        with patch.object(staging_login, "_ensure_account",
                          return_value=(7, "test_admin", "test_admin@test.invalid", True)),              patch("app.User", create=True),              patch("flask_login.login_user") as login_user,              self.assertLogs(self.app.logger, level="WARNING") as logs:
            self.app.config["WTF_CSRF_ENABLED"] = False
            resp = self.app.test_client().post(
                "/_staging/test-login/admin", environ_base={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(resp.status_code, 302)
        login_user.assert_called_once()
        self.assertTrue(any("STAGING TEST LOGIN role=admin" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
