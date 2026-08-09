import base64
import os
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("SESSION_SECRET", "tg-oidc-test-secret")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "tg-oidc-test-legacy-token")
# Empty values prevent python-dotenv from inheriting a parent workspace's .env.
os.environ["DATABASE_URL"] = ""
os.environ["POSTGRES_URL"] = ""

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

import app as app_module
import auth
import telegram_oidc

TEST_CLIENT_ID = "555000111"
TEST_CLIENT_SECRET = "test-client-secret-do-not-leak"
TEST_KID = "test-kid-1"
TEST_TELEGRAM_ID = 987654321

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()
_OTHER_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _base_claims(**overrides):
    now = int(time.time())
    claims = {
        "iss": telegram_oidc.ISSUER,
        "aud": TEST_CLIENT_ID,
        "sub": "1234123412341234123",
        "iat": now,
        "exp": now + 3600,
        "id": TEST_TELEGRAM_ID,
        "given_name": "John",
        "family_name": "Doe",
        "preferred_username": "johndoe",
    }
    claims.update(overrides)
    return claims


def _make_id_token(claims, private_key=None, alg="RS256", kid=TEST_KID):
    return jwt.encode(claims, private_key or _PRIVATE_KEY, algorithm=alg, headers={"kid": kid})


class _FakeSigningKey:
    def __init__(self, key):
        self.key = key


class _FakeJWKClient:
    def __init__(self, public_key=_PUBLIC_KEY):
        self._public_key = public_key

    def get_signing_key_from_jwt(self, token):
        return _FakeSigningKey(self._public_key)


class _FakeAuthCursor:
    """Mimics enough of a DB-API cursor for _login_or_register_telegram_user."""

    def __init__(self, existing_row=None):
        self.executed = []
        self._existing_row = existing_row
        self._next_result = None

    def execute(self, query, params=None):
        normalized = " ".join(query.split())
        self.executed.append((normalized, params))
        if normalized.startswith("SELECT id, username, email FROM users WHERE telegram_id"):
            self._next_result = self._existing_row
        elif normalized.startswith("INSERT INTO users") and "RETURNING id, username, email" in normalized:
            new_row = (42, params[0], params[1])
            self._existing_row = new_row  # a later SELECT on the same tg_id must now find it
            self._next_result = new_row
        else:
            self._next_result = None

    def fetchone(self):
        return self._next_result

    def close(self):
        return None


class _FakeAuthConnection:
    def __init__(self, existing_row=None):
        self.cursor_instance = _FakeAuthCursor(existing_row)

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        return None


class TelegramOIDCVerificationTests(unittest.TestCase):
    """Unit tests for telegram_oidc.verify_id_token: signature/claims only,
    no Flask/DB involved."""

    def setUp(self):
        patcher = patch.object(telegram_oidc, "_get_jwks_client", return_value=_FakeJWKClient())
        self.addCleanup(patcher.stop)
        patcher.start()
        self._orig_client_id = telegram_oidc.TELEGRAM_LOGIN_CLIENT_ID
        telegram_oidc.TELEGRAM_LOGIN_CLIENT_ID = TEST_CLIENT_ID
        self.addCleanup(setattr, telegram_oidc, "TELEGRAM_LOGIN_CLIENT_ID", self._orig_client_id)

    def test_valid_token_is_accepted_and_id_normalized_to_int(self):
        claims = telegram_oidc.verify_id_token(_make_id_token(_base_claims()))
        self.assertEqual(claims["id"], TEST_TELEGRAM_ID)
        self.assertIsInstance(claims["id"], int)

    def test_invalid_issuer_rejected(self):
        token = _make_id_token(_base_claims(iss="https://evil.example"))
        with self.assertRaises(telegram_oidc.TelegramOIDCError):
            telegram_oidc.verify_id_token(token)

    def test_invalid_audience_rejected(self):
        token = _make_id_token(_base_claims(aud="some-other-bot-id"))
        with self.assertRaises(telegram_oidc.TelegramOIDCError):
            telegram_oidc.verify_id_token(token)

    def test_expired_token_rejected(self):
        now = int(time.time())
        token = _make_id_token(_base_claims(iat=now - 7200, exp=now - 3600))
        with self.assertRaises(telegram_oidc.TelegramOIDCError):
            telegram_oidc.verify_id_token(token)

    def test_tampered_signature_rejected(self):
        # Signed by a different key than the one the (patched) JWKS lookup
        # returns -- the signature must not verify.
        token = _make_id_token(_base_claims(), private_key=_OTHER_PRIVATE_KEY)
        with self.assertRaises(telegram_oidc.TelegramOIDCError):
            telegram_oidc.verify_id_token(token)

    def test_hs256_alg_confusion_rejected(self):
        # Classic alg-confusion attack: sign with HS256 using a guessed
        # secret. Must be rejected purely because RS256 is the only allowed
        # algorithm -- the HMAC secret is never even evaluated.
        token = jwt.encode(_base_claims(), "attacker-guessed-secret", algorithm="HS256")
        with self.assertRaises(telegram_oidc.TelegramOIDCError):
            telegram_oidc.verify_id_token(token)

    def test_missing_telegram_id_claim_rejected(self):
        claims = _base_claims()
        del claims["id"]
        with self.assertRaises(telegram_oidc.TelegramOIDCError):
            telegram_oidc.verify_id_token(_make_id_token(claims))

    def test_nonce_mismatch_rejected_when_provider_echoes_nonce(self):
        token = _make_id_token(_base_claims(nonce="expected-nonce"))
        with self.assertRaises(telegram_oidc.TelegramOIDCError):
            telegram_oidc.verify_id_token(token, nonce="different-nonce")

    def test_nonce_absent_from_token_is_not_an_error(self):
        # The manual/server-side flow docs don't guarantee nonce is echoed.
        token = _make_id_token(_base_claims())
        claims = telegram_oidc.verify_id_token(token, nonce="expected-nonce")
        self.assertEqual(claims["id"], TEST_TELEGRAM_ID)


class TelegramOIDCTokenExchangeTests(unittest.TestCase):
    def setUp(self):
        self._orig_id = telegram_oidc.TELEGRAM_LOGIN_CLIENT_ID
        self._orig_secret = telegram_oidc.TELEGRAM_LOGIN_CLIENT_SECRET
        telegram_oidc.TELEGRAM_LOGIN_CLIENT_ID = TEST_CLIENT_ID
        telegram_oidc.TELEGRAM_LOGIN_CLIENT_SECRET = TEST_CLIENT_SECRET

    def tearDown(self):
        telegram_oidc.TELEGRAM_LOGIN_CLIENT_ID = self._orig_id
        telegram_oidc.TELEGRAM_LOGIN_CLIENT_SECRET = self._orig_secret

    def test_successful_exchange_sends_correct_request_and_returns_id_token(self):
        fake_response = MagicMock(status_code=200)
        fake_response.json.return_value = {"id_token": "the-id-token", "access_token": "x"}
        with patch.object(telegram_oidc.requests, "post", return_value=fake_response) as mock_post:
            token = telegram_oidc.exchange_code_for_id_token(
                "auth-code", "https://www.olimlar.uz/login/telegram/callback", "verifier-value")
        self.assertEqual(token, "the-id-token")

        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["data"]["grant_type"], "authorization_code")
        self.assertEqual(kwargs["data"]["code"], "auth-code")
        self.assertEqual(kwargs["data"]["code_verifier"], "verifier-value")
        self.assertEqual(kwargs["data"]["client_id"], TEST_CLIENT_ID)
        expected_auth = "Basic " + base64.b64encode(
            f"{TEST_CLIENT_ID}:{TEST_CLIENT_SECRET}".encode()).decode()
        self.assertEqual(kwargs["headers"]["Authorization"], expected_auth)
        # The client secret must travel only in this server-side header, never
        # as a URL/query parameter that could end up in logs.
        self.assertNotIn(TEST_CLIENT_SECRET, str(kwargs.get("data", "")))

    def test_non_200_response_raises(self):
        fake_response = MagicMock(status_code=400)
        with patch.object(telegram_oidc.requests, "post", return_value=fake_response):
            with self.assertRaises(telegram_oidc.TelegramOIDCError):
                telegram_oidc.exchange_code_for_id_token("code", "uri", "verifier")

    def test_missing_id_token_in_response_raises(self):
        fake_response = MagicMock(status_code=200)
        fake_response.json.return_value = {"access_token": "x"}
        with patch.object(telegram_oidc.requests, "post", return_value=fake_response):
            with self.assertRaises(telegram_oidc.TelegramOIDCError):
                telegram_oidc.exchange_code_for_id_token("code", "uri", "verifier")


class TelegramLoginHelperTests(unittest.TestCase):
    def test_duplicate_telegram_user_not_created_on_second_login(self):
        connection = _FakeAuthConnection(existing_row=None)
        with patch.object(auth, "get_connection", return_value=connection):
            user1, is_new1 = auth._login_or_register_telegram_user(TEST_TELEGRAM_ID)
            user2, is_new2 = auth._login_or_register_telegram_user(TEST_TELEGRAM_ID)
        self.assertTrue(is_new1)
        self.assertFalse(is_new2)
        self.assertEqual(user1.id, user2.id)
        inserts = [row for row in connection.cursor_instance.executed
                   if row[0].startswith("INSERT INTO users")]
        self.assertEqual(len(inserts), 1)


class TelegramOIDCRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.flask_app = app_module.app
        cls.flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)

    def setUp(self):
        self.client = self.flask_app.test_client()
        self._orig_id = telegram_oidc.TELEGRAM_LOGIN_CLIENT_ID
        self._orig_secret = telegram_oidc.TELEGRAM_LOGIN_CLIENT_SECRET
        telegram_oidc.TELEGRAM_LOGIN_CLIENT_ID = TEST_CLIENT_ID
        telegram_oidc.TELEGRAM_LOGIN_CLIENT_SECRET = TEST_CLIENT_SECRET

    def tearDown(self):
        telegram_oidc.TELEGRAM_LOGIN_CLIENT_ID = self._orig_id
        telegram_oidc.TELEGRAM_LOGIN_CLIENT_SECRET = self._orig_secret

    def _start_and_capture_session(self, next_url=None):
        url = "/login/telegram/start"
        if next_url:
            url += "?next=" + next_url
        response = self.client.get(url, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        self.assertTrue(location.startswith(telegram_oidc.AUTHORIZATION_ENDPOINT))
        with self.client.session_transaction() as sess:
            state = sess.get("tg_oidc_state")
            verifier = sess.get("tg_oidc_verifier")
        return location, state, verifier

    def test_start_creates_state_and_pkce_and_redirects_to_telegram(self):
        location, state, verifier = self._start_and_capture_session()
        self.assertIsNotNone(state)
        self.assertIsNotNone(verifier)
        self.assertIn(f"client_id={TEST_CLIENT_ID}", location)
        self.assertIn("response_type=code", location)
        self.assertIn("code_challenge_method=S256", location)
        self.assertIn(f"state={state}", location)
        expected_challenge = telegram_oidc.pkce_challenge(verifier)
        self.assertIn(f"code_challenge={expected_challenge}", location)

    def test_callback_rejects_wrong_state(self):
        self._start_and_capture_session()
        response = self.client.get("/login/telegram/callback?state=wrong-state&code=abc")
        self.assertIn("Xavfsizlik tekshiruvi", response.get_data(as_text=True))

    def test_callback_rejects_missing_code(self):
        _, state, _ = self._start_and_capture_session()
        response = self.client.get(f"/login/telegram/callback?state={state}")
        self.assertIn("tasdiqlash kodi topilmadi", response.get_data(as_text=True))

    def test_callback_with_valid_token_logs_in_existing_user(self):
        _, state, _ = self._start_and_capture_session()
        id_token = _make_id_token(_base_claims())
        connection = _FakeAuthConnection(
            existing_row=(7, "tg_987654321", "telegram_987654321@auth.local"))
        with patch.object(telegram_oidc, "exchange_code_for_id_token", return_value=id_token), \
             patch.object(telegram_oidc, "_get_jwks_client", return_value=_FakeJWKClient()), \
             patch.object(auth, "get_connection", return_value=connection):
            response = self.client.get(
                f"/login/telegram/callback?state={state}&code=abc", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(any(row[0].startswith("INSERT INTO users")
                              for row in connection.cursor_instance.executed))
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get("_user_id"), "7")

    def test_callback_with_valid_token_creates_new_user(self):
        _, state, _ = self._start_and_capture_session()
        id_token = _make_id_token(_base_claims())
        connection = _FakeAuthConnection(existing_row=None)
        with patch.object(telegram_oidc, "exchange_code_for_id_token", return_value=id_token), \
             patch.object(telegram_oidc, "_get_jwks_client", return_value=_FakeJWKClient()), \
             patch.object(auth, "get_connection", return_value=connection):
            response = self.client.get(
                f"/login/telegram/callback?state={state}&code=abc", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/cabinet/onboarding")
        insert = next(row for row in connection.cursor_instance.executed
                      if row[0].startswith("INSERT INTO users"))
        self.assertEqual(insert[1][4], TEST_TELEGRAM_ID)

    def test_callback_state_is_single_use(self):
        _, state, _ = self._start_and_capture_session()
        id_token = _make_id_token(_base_claims())
        connection = _FakeAuthConnection(existing_row=(7, "tg_987654321", "e@x"))
        with patch.object(telegram_oidc, "exchange_code_for_id_token", return_value=id_token), \
             patch.object(telegram_oidc, "_get_jwks_client", return_value=_FakeJWKClient()), \
             patch.object(auth, "get_connection", return_value=connection):
            first = self.client.get(f"/login/telegram/callback?state={state}&code=abc")
            self.assertEqual(first.status_code, 302)
            second = self.client.get(f"/login/telegram/callback?state={state}&code=abc")
        self.assertIn("Kirish seansi muddati tugagan", second.get_data(as_text=True))

    def test_client_secret_never_exposed_to_browser(self):
        login_html = self.client.get("/login").get_data(as_text=True)
        self.assertNotIn(TEST_CLIENT_SECRET, login_html)

        location, _state, _verifier = self._start_and_capture_session()
        self.assertNotIn(TEST_CLIENT_SECRET, location)

        error_html = self.client.get(
            "/login/telegram/callback?state=wrong&code=abc").get_data(as_text=True)
        self.assertNotIn(TEST_CLIENT_SECRET, error_html)

    def test_password_and_google_login_unchanged(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('action="/login/password"', html)
        self.assertIn('href="/login/google"', html)


class LoginPageMarkupTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "templates" / "login.html").read_text()

    def test_legacy_widget_removed_from_login_page(self):
        self.assertNotIn("telegram-widget.js", self.html)
        self.assertNotIn("data-telegram-login", self.html)

    def test_visible_telegram_login_button_present(self):
        self.assertIn('/login/telegram/start', self.html)
        self.assertIn("Telegram bilan kirish", self.html)


if __name__ == "__main__":
    unittest.main()
