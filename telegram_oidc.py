"""Telegram Login via OpenID Connect (Authorization Code Flow + PKCE).

Telegram archived the old iframe login widget and now runs a standard OIDC
provider. Reference: https://core.telegram.org/bots/telegram-login

    Authorization: https://oauth.telegram.org/auth
    Token:         https://oauth.telegram.org/token
    JWKS:          https://oauth.telegram.org/.well-known/jwks.json
    Issuer:        https://oauth.telegram.org

client_id / client_secret come from @BotFather -> pick the bot -> Login
Widget -> register the Allowed URL (origin + this callback), which reveals
the Client ID and Client Secret to save as TELEGRAM_LOGIN_CLIENT_ID /
TELEGRAM_LOGIN_CLIENT_SECRET.

This module only builds requests and verifies the returned id_token; it has
no Flask/session/DB dependency so it can be unit tested and reused.
"""
import base64
import hashlib
import hmac
import os
import secrets
from urllib.parse import urlencode

import jwt
import requests

TELEGRAM_LOGIN_CLIENT_ID = os.environ.get('TELEGRAM_LOGIN_CLIENT_ID', '')
TELEGRAM_LOGIN_CLIENT_SECRET = os.environ.get('TELEGRAM_LOGIN_CLIENT_SECRET', '')

AUTHORIZATION_ENDPOINT = 'https://oauth.telegram.org/auth'
TOKEN_ENDPOINT = 'https://oauth.telegram.org/token'
JWKS_URI = 'https://oauth.telegram.org/.well-known/jwks.json'
ISSUER = 'https://oauth.telegram.org'

# `profile` is required (not just `openid`) to get the `id` claim -- the
# immutable numeric Telegram user id that matches the legacy widget's `id`
# field and users.telegram_id. `phone` is not requested: we don't need it
# and it needs extra user consent.
SCOPE = 'openid profile'

# BotFather's Login Widget > Advanced lets an operator switch the id_token
# signing algorithm (default RS256; ES256/EdDSA/ES256K also offered). We
# have not changed that setting, so only RS256 is accepted here. If the
# algorithm is ever changed in BotFather, this allowlist must be updated to
# match -- do not widen it speculatively.
ALLOWED_ALGORITHMS = ('RS256',)

_jwks_client = None


def _get_jwks_client():
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(JWKS_URI, cache_keys=True)
    return _jwks_client


class TelegramOIDCError(Exception):
    """Any failure in the Telegram OIDC exchange or id_token verification."""


def is_configured():
    return bool(TELEGRAM_LOGIN_CLIENT_ID and TELEGRAM_LOGIN_CLIENT_SECRET)


def generate_state():
    return secrets.token_urlsafe(32)


def generate_pkce_verifier():
    return secrets.token_urlsafe(64)[:128]


def pkce_challenge(verifier):
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')


def build_authorization_url(redirect_uri, state, code_challenge, nonce):
    params = {
        'client_id': TELEGRAM_LOGIN_CLIENT_ID,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': SCOPE,
        'state': state,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
        'nonce': nonce,
    }
    return f"{AUTHORIZATION_ENDPOINT}?{urlencode(params)}"


def exchange_code_for_id_token(code, redirect_uri, code_verifier):
    """POST the authorization code to Telegram's token endpoint.

    Returns the raw (unverified) id_token string. Caller must verify it
    with verify_id_token() before trusting any claim.
    """
    credentials = f"{TELEGRAM_LOGIN_CLIENT_ID}:{TELEGRAM_LOGIN_CLIENT_SECRET}".encode('utf-8')
    basic_auth = base64.b64encode(credentials).decode('ascii')
    try:
        response = requests.post(
            TOKEN_ENDPOINT,
            data={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': redirect_uri,
                'client_id': TELEGRAM_LOGIN_CLIENT_ID,
                'code_verifier': code_verifier,
            },
            headers={'Authorization': f'Basic {basic_auth}'},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise TelegramOIDCError('token_exchange_network_error') from exc

    if response.status_code != 200:
        raise TelegramOIDCError('token_exchange_failed')

    try:
        payload = response.json()
    except ValueError as exc:
        raise TelegramOIDCError('token_exchange_bad_response') from exc

    id_token = payload.get('id_token')
    if not id_token:
        raise TelegramOIDCError('missing_id_token')
    return id_token


def verify_id_token(id_token, nonce=None):
    """Verify signature + iss/aud/exp, return the decoded claims dict.

    Signature is checked against Telegram's live JWKS (fetched by kid, not
    a hardcoded key). Never call this and trust the token without it
    raising -- callers must not decode id_token any other way.
    """
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(id_token)
    except jwt.PyJWKClientError as exc:
        raise TelegramOIDCError('jwks_lookup_failed') from exc

    try:
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=list(ALLOWED_ALGORITHMS),
            issuer=ISSUER,
            audience=TELEGRAM_LOGIN_CLIENT_ID,
            options={'require': ['exp', 'iat', 'iss', 'aud', 'sub']},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TelegramOIDCError('token_expired') from exc
    except jwt.InvalidAudienceError as exc:
        raise TelegramOIDCError('invalid_audience') from exc
    except jwt.InvalidIssuerError as exc:
        raise TelegramOIDCError('invalid_issuer') from exc
    except jwt.InvalidTokenError as exc:
        raise TelegramOIDCError('invalid_token') from exc

    # Best-effort replay check: only enforced if Telegram actually echoes
    # the nonce back (the manual/server-side flow docs don't guarantee it,
    # unlike the JS widget flow) -- state + PKCE already bind this exchange
    # to our own request, so a missing nonce claim is not itself an error.
    if nonce and claims.get('nonce') and not hmac.compare_digest(str(claims['nonce']), str(nonce)):
        raise TelegramOIDCError('nonce_mismatch')

    # `sub` is an opaque OIDC subject identifier (verified above to be
    # present, but NOT the same value as the legacy widget's numeric `id` --
    # it must never be used to look up users.telegram_id, or every existing
    # Telegram-linked account would appear to be a new user).
    raw_id = claims.get('id')
    try:
        telegram_user_id = int(raw_id)
    except (TypeError, ValueError):
        raise TelegramOIDCError('missing_telegram_id_claim')
    if telegram_user_id <= 0:
        raise TelegramOIDCError('missing_telegram_id_claim')
    claims['id'] = telegram_user_id

    return claims
