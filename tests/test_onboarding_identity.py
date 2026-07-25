"""Onboarding + Telegram/Google identity reconciliation tests.

These extend the Phase 0 security suite (tests/test_security_phase0.py) with
coverage for the single canonical onboarding flow (/cabinet/onboarding) and
the immutable-Telegram-id identity invariants, after merging the local
onboarding UX work on top of the security branch.

No real database or network is used: DB-touching flows are exercised with the
same fake-connection technique as test_security_phase0.py, and the rest are
asserted at the source/route-map level. APP_ENV=testing is enforced so a
stray connection can never reach a real Postgres.
"""
import hashlib
import hmac
import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("SESSION_SECRET", "onboarding-test-secret")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "phase-0-telegram-token")
# Empty values prevent python-dotenv from inheriting a parent workspace's .env.
os.environ["DATABASE_URL"] = ""
os.environ["POSTGRES_URL"] = ""

import app as app_module
import auth
import cabinet


TG_TOKEN = "phase-0-telegram-token"


def _signed_telegram_payload(tg_id, username="someuser", first_name="New"):
    payload = {
        "id": tg_id,
        "username": username,
        "first_name": first_name,
        "auth_date": int(time.time()),
    }
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(payload.items()))
    secret = hashlib.sha256(TG_TOKEN.encode()).digest()
    payload["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return payload


class FakeCursor:
    """Cursor whose telegram_id lookup returns ``existing_row`` (or None for a
    brand-new account). Everything else returns a sensible default so the
    login flow (INSERT ... RETURNING, visit_count UPDATE, chat_id ALTER) runs
    without a database."""

    def __init__(self, existing_row=None):
        self.executed = []
        self._existing_row = existing_row
        self._row = None

    def execute(self, query, params=None):
        normalized = " ".join(query.split())
        self.executed.append((normalized, params))
        if "FROM users WHERE telegram_id = %s" in normalized:
            self._row = self._existing_row
        elif "RETURNING id, username, email" in normalized:
            self._row = (99, "tg_555", "telegram_555@auth.local")
        else:
            self._row = None

    def fetchone(self):
        return self._row

    def close(self):
        return None


class FakeConnection:
    def __init__(self, existing_row=None):
        self.cursor_instance = FakeCursor(existing_row)
        self.commits = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        return None

    def close(self):
        return None


class OnboardingIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.flask_app = app_module.app
        cls.flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)

    def setUp(self):
        self.client = self.flask_app.test_client()
        auth.TELEGRAM_BOT_TOKEN = TG_TOKEN

    def _cabinet_session(self, user_id=1):
        with self.client.session_transaction() as sess:
            sess["cabinet_user_id"] = user_id

    # ── Telegram identity (immutable id) ─────────────────────────────────

    def test_new_telegram_user_lands_on_canonical_onboarding(self):
        payload = _signed_telegram_payload(555, username="someuser")
        conn = FakeConnection(existing_row=None)
        with patch.object(auth, "get_connection", return_value=conn):
            resp = self.client.post("/login/telegram", json=payload)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["redirect"], "/cabinet/onboarding")

    def test_returning_telegram_user_matched_by_immutable_id_skips_onboarding(self):
        # Existing account already carries this telegram_id → matched directly,
        # no new INSERT, and NOT sent back through onboarding.
        existing = (7, "existing_scholar", "telegram_555@auth.local")
        payload = _signed_telegram_payload(555, username="renamed_since")
        conn = FakeConnection(existing_row=existing)
        with patch.object(auth, "get_connection", return_value=conn):
            resp = self.client.post("/login/telegram", json=payload)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["success"])
        self.assertNotEqual(body["redirect"], "/cabinet/onboarding")
        statements = conn.cursor_instance.executed
        # Identity is bound only by telegram_id; a changed @username must never
        # trigger a username lookup, and no new row is inserted for a match.
        self.assertFalse(any("WHERE username" in sql for sql, _ in statements))
        self.assertFalse(any(sql.startswith("INSERT INTO users") for sql, _ in statements))

    def test_telegram_login_source_has_no_username_or_email_fallback(self):
        # Source-level guard against reintroducing the takeover-prone fallback:
        # the login flow must select users only by telegram_id.
        import inspect
        src = inspect.getsource(auth.telegram_login)
        self.assertIn("WHERE telegram_id = %s", src)
        self.assertNotIn("WHERE email = %s", src)
        self.assertNotIn("WHERE username = %s", src)

    # ── Onboarding flow ──────────────────────────────────────────────────

    def test_welcome_route_is_compat_redirect_to_onboarding(self):
        self._cabinet_session()
        resp = self.client.get("/cabinet/xush-kelibsiz", follow_redirects=False)
        self.assertIn(resp.status_code, (301, 302, 303))
        self.assertTrue(resp.headers["Location"].endswith("/cabinet/onboarding"))

    def test_old_set_user_type_route_is_gone(self):
        rules = {r.rule for r in self.flask_app.url_map.iter_rules()}
        endpoints = {r.endpoint for r in self.flask_app.url_map.iter_rules()}
        self.assertNotIn("/cabinet/xush-kelibsiz/set-type", rules)
        self.assertNotIn("cabinet.set_user_type", endpoints)
        # The single canonical onboarding route and the user-type API both exist.
        self.assertIn("/cabinet/onboarding", rules)
        self.assertIn("/cabinet/api/user-type", rules)

    def test_user_type_form_requires_csrf(self):
        # Browser write route must reject a POST with no CSRF token.
        self._cabinet_session()
        resp = self.client.post(
            "/cabinet/api/user-type",
            data={"user_type": "researcher"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_no_template_references_removed_endpoint(self):
        # A dangling url_for('cabinet.set_user_type') would 500 at render time.
        for tmpl in (ROOT / "templates").rglob("*.html"):
            self.assertNotIn(
                "set_user_type", tmpl.read_text(encoding="utf-8"),
                msg=f"stale set_user_type reference in {tmpl.name}",
            )

    def test_onboarding_template_has_type_tabs_and_csrf_safe_fetch(self):
        html = (ROOT / "templates" / "cabinet_onboarding.html").read_text(encoding="utf-8")
        # UX brought over from the local branch:
        self.assertIn("olz-type-tabs", html)
        self.assertIn("/cabinet/api/user-type", html)
        # Security wrap preserved on the profile-save fetch helper:
        self.assertIn("oakCsrfHeaders", html)

    def test_auth_and_cabinet_new_user_redirects_are_canonical(self):
        # Every "new account" branch points at the single onboarding route,
        # and the old welcome path is never a *destination* any more.
        import inspect
        auth_google = inspect.getsource(auth.google_callback)
        self.assertIn("/cabinet/onboarding", auth_google)
        cab_src = inspect.getsource(cabinet)
        self.assertIn('"/cabinet/onboarding"', cab_src)
        # The only surviving 'xush-kelibsiz' literal is the compat *route*,
        # not a redirect target.
        self.assertNotIn("redirect('/cabinet/xush-kelibsiz'", cab_src)
        self.assertNotIn('"/cabinet/xush-kelibsiz" if', cab_src)


if __name__ == "__main__":
    unittest.main()
