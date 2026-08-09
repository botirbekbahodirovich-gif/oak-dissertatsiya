"""Post-login destination hardening.

Production incident: templates/base.html's checkNotifs() fires a background
fetch('/api/notifications/count') on every page, including for guests. When
unauthenticated, that request used to 302 through /login?next=/api/notific
ations/count, and the old permissive _safe_next() (only "starts with / and
not //") stored that API path in session['login_next']. The *next* real
login (Telegram OIDC, legacy widget, or Google) then redirected the browser
straight to a raw JSON response.

Covers:
  - auth._safe_next() rejects API/static/auth-flow destinations and open
    redirects, accepts ordinary navigational pages.
  - GET /api/notifications/count while unauthenticated returns 401 JSON and
    never touches session['login_next'] (root-cause fix).
  - Ordinary @login_required HTML routes still redirect to /login?next=...
    (no regression).
  - Telegram OIDC / legacy widget / Google callbacks all fall back to '/'
    when their stored next is poisoned, and honor a legitimate one.
  - New-user branches still land on /cabinet/onboarding regardless of next.

No real database or network is used (same fake-connection technique as
tests/test_telegram_oidc_login.py); APP_ENV=testing keeps a stray connection
from ever reaching real Postgres.
"""
import hashlib
import hmac
import os
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("SESSION_SECRET", "post-login-redirect-test-secret")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "post-login-redirect-test-token")
# Empty values prevent python-dotenv from inheriting a parent workspace's .env.
os.environ["DATABASE_URL"] = ""
os.environ["POSTGRES_URL"] = ""

import app as app_module
import auth

TG_TOKEN = "post-login-redirect-test-token"
TEST_TELEGRAM_ID = 424242424


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


class _FakeCursor:
    """Enough of a DB-API cursor for _login_or_register_telegram_user."""

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
            new_row = (99, params[0], params[1])
            self._existing_row = new_row
            self._row = new_row
        else:
            self._row = None

    def fetchone(self):
        return self._row

    def close(self):
        return None


class _FakeConnection:
    def __init__(self, existing_row=None):
        self.cursor_instance = _FakeCursor(existing_row)

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        return None


# ── auth._safe_next() unit tests ─────────────────────────────────────────

class SafeNextUnitTests(unittest.TestCase):
    def test_rejects_notifications_count_api_endpoint(self):
        self.assertIsNone(auth._safe_next('/api/notifications/count'))

    def test_rejects_any_api_path(self):
        self.assertIsNone(auth._safe_next('/api/xarita/data'))
        self.assertIsNone(auth._safe_next('/api/messages/unread-count'))

    def test_accepts_maqolalar(self):
        self.assertEqual(auth._safe_next('/maqolalar'), '/maqolalar')

    def test_accepts_admin_articles_discovery(self):
        self.assertEqual(auth._safe_next('/admin/articles/discovery'),
                          '/admin/articles/discovery')

    def test_accepts_invite_token(self):
        self.assertEqual(auth._safe_next('/invite/abc123'), '/invite/abc123')

    def test_accepts_root(self):
        self.assertEqual(auth._safe_next('/'), '/')

    def test_rejects_scheme_relative_open_redirect(self):
        self.assertIsNone(auth._safe_next('//evil.com'))
        self.assertIsNone(auth._safe_next('//evil.com/phish'))

    def test_rejects_absolute_external_url(self):
        self.assertIsNone(auth._safe_next('https://evil.com'))
        self.assertIsNone(auth._safe_next('http://evil.com/x'))

    def test_rejects_malformed_destinations(self):
        self.assertIsNone(auth._safe_next(None))
        self.assertIsNone(auth._safe_next(''))
        self.assertIsNone(auth._safe_next('not-a-path'))
        self.assertIsNone(auth._safe_next('/\\evil.com'))
        self.assertIsNone(auth._safe_next('/api/notifications/count\r\nX-Injected: 1'))

    def test_rejects_static_assets(self):
        self.assertIsNone(auth._safe_next('/static/js/app.js'))

    def test_rejects_auth_flow_endpoints(self):
        self.assertIsNone(auth._safe_next('/login'))
        self.assertIsNone(auth._safe_next('/login/telegram/start'))
        self.assertIsNone(auth._safe_next('/login/telegram/callback'))
        self.assertIsNone(auth._safe_next('/login/google'))
        self.assertIsNone(auth._safe_next('/login/google/callback'))
        self.assertIsNone(auth._safe_next('/logout'))
        self.assertIsNone(auth._safe_next('/register'))

    def test_query_string_preserved_on_accepted_destination(self):
        self.assertEqual(auth._safe_next('/maqolalar?tab=trend'),
                          '/maqolalar?tab=trend')

    def test_percent_encoded_api_prefix_still_rejected(self):
        # %61pi decodes to "api" -- must not slip past the prefix check.
        self.assertIsNone(auth._safe_next('/%61pi/notifications/count'))


# ── Route-level: unauthenticated API access must not poison login_next ───

class UnauthenticatedApiRedirectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.flask_app = app_module.app
        cls.flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)

    def setUp(self):
        self.client = self.flask_app.test_client()

    def test_unauthenticated_api_request_returns_401_json(self):
        resp = self.client.get('/api/notifications/count', follow_redirects=False)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.content_type, 'application/json')
        self.assertIn('error', resp.get_json())

    def test_unauthenticated_api_request_does_not_poison_login_next(self):
        resp = self.client.get('/api/notifications/count')
        self.assertEqual(resp.status_code, 401)
        with self.client.session_transaction() as sess:
            self.assertNotIn('login_next', sess)

    def test_unauthenticated_html_route_still_redirects_to_login(self):
        # No regression: a real navigational page must still bounce an
        # unauthenticated visitor to /login with next= set.
        resp = self.client.get('/profile', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers['Location'].startswith('/login'))
        self.assertIn('next=/profile', resp.headers['Location'])

    def test_unauthenticated_html_route_sets_valid_login_next(self):
        # session['login_next'] is populated by GET /login itself (the
        # redirect target), so follow the redirect once to confirm the round
        # trip stores a safe value.
        resp = self.client.get('/notifications', follow_redirects=False)
        location = resp.headers['Location']
        self.client.get(location, follow_redirects=False)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get('login_next'), '/notifications')


# ── Telegram OIDC callback ────────────────────────────────────────────────

class TelegramOidcRedirectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.flask_app = app_module.app
        cls.flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)

    def setUp(self):
        import telegram_oidc
        self.client = self.flask_app.test_client()
        self._orig_id = telegram_oidc.TELEGRAM_LOGIN_CLIENT_ID
        self._orig_secret = telegram_oidc.TELEGRAM_LOGIN_CLIENT_SECRET
        telegram_oidc.TELEGRAM_LOGIN_CLIENT_ID = 'test-client-id'
        telegram_oidc.TELEGRAM_LOGIN_CLIENT_SECRET = 'test-client-secret'

    def tearDown(self):
        import telegram_oidc
        telegram_oidc.TELEGRAM_LOGIN_CLIENT_ID = self._orig_id
        telegram_oidc.TELEGRAM_LOGIN_CLIENT_SECRET = self._orig_secret

    def _seed_oidc_session(self, nxt):
        with self.client.session_transaction() as sess:
            sess['tg_oidc_state'] = 'state-abc'
            sess['tg_oidc_verifier'] = 'verifier-abc'
            sess['tg_oidc_nonce'] = 'nonce-abc'
            sess['tg_oidc_next'] = nxt
            sess['tg_oidc_expires'] = time.time() + 600

    def _complete_callback(self, existing_row):
        import telegram_oidc
        conn = _FakeConnection(existing_row=existing_row)
        with patch.object(telegram_oidc, 'exchange_code_for_id_token',
                           return_value='fake-id-token'), \
             patch.object(telegram_oidc, 'verify_id_token',
                           return_value={'id': TEST_TELEGRAM_ID,
                                         'given_name': 'X', 'family_name': 'Y'}), \
             patch.object(auth, 'get_connection', return_value=conn):
            return self.client.get(
                '/login/telegram/callback?state=state-abc&code=abc',
                follow_redirects=False)

    def test_existing_user_poisoned_api_next_lands_on_root(self):
        self._seed_oidc_session('/api/notifications/count')
        resp = self._complete_callback(existing_row=(7, 'tg_x', 'e@x'))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers['Location'], '/')

    def test_existing_user_legitimate_next_is_honored(self):
        self._seed_oidc_session('/maqolalar')
        resp = self._complete_callback(existing_row=(7, 'tg_x', 'e@x'))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers['Location'], '/maqolalar')

    def test_new_user_still_lands_on_onboarding_regardless_of_next(self):
        self._seed_oidc_session('/api/notifications/count')
        resp = self._complete_callback(existing_row=None)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers['Location'], '/cabinet/onboarding')

    def test_start_rejects_api_next_falls_back_to_login_next_or_root(self):
        resp = self.client.get(
            '/login/telegram/start?next=/api/notifications/count',
            follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get('tg_oidc_next'), '/')

    def test_start_accepts_legitimate_next(self):
        resp = self.client.get(
            '/login/telegram/start?next=/admin/articles/discovery',
            follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get('tg_oidc_next'), '/admin/articles/discovery')


# ── Legacy Telegram widget (POST/HMAC) ────────────────────────────────────

class LegacyTelegramWidgetRedirectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.flask_app = app_module.app
        cls.flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)

    def setUp(self):
        self.client = self.flask_app.test_client()
        auth.TELEGRAM_BOT_TOKEN = TG_TOKEN

    def test_poisoned_login_next_falls_back_to_root(self):
        with self.client.session_transaction() as sess:
            sess['login_next'] = '/api/notifications/count'
        payload = _signed_telegram_payload(TEST_TELEGRAM_ID)
        conn = _FakeConnection(existing_row=(7, 'tg_x', 'e@x'))
        with patch.object(auth, 'get_connection', return_value=conn):
            resp = self.client.post('/login/telegram', json=payload)
        self.assertEqual(resp.get_json()['redirect'], '/')

    def test_legitimate_login_next_is_honored(self):
        with self.client.session_transaction() as sess:
            sess['login_next'] = '/invite/tok123'
        payload = _signed_telegram_payload(TEST_TELEGRAM_ID)
        conn = _FakeConnection(existing_row=(7, 'tg_x', 'e@x'))
        with patch.object(auth, 'get_connection', return_value=conn):
            resp = self.client.post('/login/telegram', json=payload)
        self.assertEqual(resp.get_json()['redirect'], '/invite/tok123')


# ── Google login/callback ─────────────────────────────────────────────────

class GoogleLoginRedirectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.flask_app = app_module.app
        cls.flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)

    def setUp(self):
        self.client = self.flask_app.test_client()
        self._orig_id = auth.GOOGLE_CLIENT_ID
        self._orig_secret = auth.GOOGLE_CLIENT_SECRET
        auth.GOOGLE_CLIENT_ID = 'test-google-client-id'
        auth.GOOGLE_CLIENT_SECRET = 'test-google-client-secret'

    def tearDown(self):
        auth.GOOGLE_CLIENT_ID = self._orig_id
        auth.GOOGLE_CLIENT_SECRET = self._orig_secret

    def test_start_rejects_api_next(self):
        mock_session = MagicMock()
        mock_session.authorization_url.return_value = (
            'https://accounts.google.com/o/oauth2/v2/auth?x=1', 'state123')
        with patch('requests_oauthlib.OAuth2Session', return_value=mock_session):
            resp = self.client.get(
                '/login/google?next=/api/notifications/count', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get('main_google_next'), '/')

    def test_callback_rejects_poisoned_next_lands_on_root(self):
        with self.client.session_transaction() as sess:
            sess['main_google_state'] = 'state123'
            sess['main_google_next'] = '/api/notifications/count'
        fake_user = app_module.User(21, 'googler', 'g@x.com', False)
        mock_session = MagicMock()
        mock_session.get.return_value.json.return_value = {
            'email': 'g@x.com', 'name': 'Googler'}
        with patch('requests_oauthlib.OAuth2Session', return_value=mock_session), \
             patch.object(auth, '_find_or_create_google_user',
                           return_value=(fake_user, False)):
            resp = self.client.get(
                '/login/google/callback?state=state123&code=abc',
                follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers['Location'], '/')

    def test_callback_honors_legitimate_next(self):
        with self.client.session_transaction() as sess:
            sess['main_google_state'] = 'state456'
            sess['main_google_next'] = '/maqolalar'
        fake_user = app_module.User(22, 'googler2', 'g2@x.com', False)
        mock_session = MagicMock()
        mock_session.get.return_value.json.return_value = {
            'email': 'g2@x.com', 'name': 'Googler2'}
        with patch('requests_oauthlib.OAuth2Session', return_value=mock_session), \
             patch.object(auth, '_find_or_create_google_user',
                           return_value=(fake_user, False)):
            resp = self.client.get(
                '/login/google/callback?state=state456&code=abc',
                follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers['Location'], '/maqolalar')

    def test_callback_new_user_lands_on_onboarding(self):
        with self.client.session_transaction() as sess:
            sess['main_google_state'] = 'state789'
            sess['main_google_next'] = '/api/notifications/count'
        fake_user = app_module.User(23, 'googler3', 'g3@x.com', False)
        mock_session = MagicMock()
        mock_session.get.return_value.json.return_value = {
            'email': 'g3@x.com', 'name': 'Googler3'}
        with patch('requests_oauthlib.OAuth2Session', return_value=mock_session), \
             patch.object(auth, '_find_or_create_google_user',
                           return_value=(fake_user, True)):
            resp = self.client.get(
                '/login/google/callback?state=state789&code=abc',
                follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers['Location'], '/cabinet/onboarding')


if __name__ == '__main__':
    unittest.main()
