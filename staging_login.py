"""Staging-only test sign-in (default OFF).

OAuth does not work on staging and the password form is gone on purpose, so
this gives testers three synthetic accounts (user / premium / admin, all
``@test.invalid``). It is never available on production:

* nothing is registered unless ``STAGING_TEST_LOGIN=1``;
* nothing is registered unless the ``DATABASE_URL`` database name carries a
  staging marker (``staging`` / ``stage`` / ``stg``) — a leaked flag on a
  production database does nothing;
* every request must come straight from loopback (reach it through
  ``ssh -L``): the TCP peer address as seen *before* ProxyFix (which trusts
  X-Forwarded-For) must be loopback, and no proxy header may be present;
* every request re-checks ``current_database()`` on the live connection, so a
  mismatch between ``DATABASE_URL`` and the database actually used fails closed;
* only the three fixed ``@test.invalid`` accounts can be signed in to, never
  a real user;
* every use is written to the application log.
"""
import ipaddress
import re
import secrets
from urllib.parse import urlsplit

import bcrypt
from flask import Blueprint, abort, redirect, render_template_string, request

STAGING_MARKER = re.compile(r'(^|[._-])(staging|stage|stg)([._-]|$)', re.I)

ACCOUNTS = {
    'user': ('test_user', 'test_user@test.invalid', False, False),
    'premium': ('test_premium', 'test_premium@test.invalid', False, True),
    'admin': ('test_admin', 'test_admin@test.invalid', True, False),
}

staging_login_bp = Blueprint('staging_login', __name__)

_PAGE = """<!doctype html><meta charset="utf-8"><title>Staging test login</title>
<h1>Staging test login</h1>
{% for role in roles %}
<form method="POST" action="/_staging/test-login/{{ role }}" style="margin:8px 0">
  {{ csrf_token()|safe }}<button type="submit">{{ role }}</button>
</form>
{% endfor %}"""


def _database_name(database_url):
    return urlsplit((database_url or '').strip()).path.lstrip('/')


def staging_login_enabled(environ):
    """True only for STAGING_TEST_LOGIN=1 on a staging-marked database."""
    if (environ.get('STAGING_TEST_LOGIN') or '').strip() != '1':
        return False
    return bool(STAGING_MARKER.search(_database_name(environ.get('DATABASE_URL'))))


_PROXY_HEADERS = ('X-Forwarded-For', 'X-Real-IP', 'Forwarded', 'CF-Connecting-IP')


def _peer_address():
    """TCP peer address. ProxyFix rewrites REMOTE_ADDR from X-Forwarded-For, so a
    client could spoof loopback; its original value is kept under
    ``werkzeug.proxy_fix.orig``."""
    orig = request.environ.get('werkzeug.proxy_fix.orig') or {}
    return orig.get('REMOTE_ADDR') or request.environ.get('REMOTE_ADDR') or ''


def _is_loopback_direct():
    if any(request.headers.get(h) for h in _PROXY_HEADERS):
        return False
    try:
        return ipaddress.ip_address(_peer_address()).is_loopback
    except ValueError:
        return False


def _current_database():
    """Name of the database the application is really connected to."""
    from data import get_connection
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT current_database()')
            return cur.fetchone()[0]
    finally:
        conn.close()


def _connected_to_staging():
    try:
        name = _current_database()
    except Exception:
        return False
    return bool(STAGING_MARKER.search(name or ''))


@staging_login_bp.before_request
def _guard():
    if not _is_loopback_direct() or not _connected_to_staging():
        abort(404)


def _ensure_account(role):
    """Create the fixed test account if missing; returns (id, username, email, is_admin)."""
    from data import get_connection
    username, email, is_admin, is_premium = ACCOUNTS[role]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM users WHERE username = %s AND email = %s',
                        (username, email))
            row = cur.fetchone()
            if row:
                user_id = row[0]
                cur.execute('UPDATE users SET is_admin = %s, is_premium = %s WHERE id = %s',
                            (is_admin, is_premium, user_id))
            else:
                # Unusable password: nobody can derive it, there is no password route.
                pw = bcrypt.hashpw(secrets.token_hex(32).encode(), bcrypt.gensalt()).decode()
                cur.execute(
                    'INSERT INTO users (username, email, password_hash, is_admin, is_premium) '
                    'VALUES (%s, %s, %s, %s, %s) RETURNING id',
                    (username, email, pw, is_admin, is_premium))
                user_id = cur.fetchone()[0]
        conn.commit()
        return user_id, username, email, is_admin
    finally:
        conn.close()


@staging_login_bp.route('/_staging/test-login')
def index():
    from flask_wtf.csrf import generate_csrf
    return render_template_string(_PAGE, roles=list(ACCOUNTS),
                                  csrf_token=lambda: '<input type="hidden" name="csrf_token" '
                                                     f'value="{generate_csrf()}">')


@staging_login_bp.route('/_staging/test-login/<role>', methods=['POST'])
def sign_in(role):
    from flask import current_app
    from flask_login import login_user
    if role not in ACCOUNTS:
        abort(404)
    user_id, username, email, is_admin = _ensure_account(role)
    from app import User
    login_user(User(user_id, username, email, is_admin))
    current_app.logger.warning(
        'STAGING TEST LOGIN role=%s user_id=%s remote=%s ua=%r',
        role, user_id, _peer_address(), (request.user_agent.string or '')[:120])
    return redirect('/')


def register_staging_login(app, environ=None):
    """Register the blueprint only when staging_login_enabled(); returns bool."""
    import os
    env = os.environ if environ is None else environ
    if not staging_login_enabled(env):
        if (env.get('STAGING_TEST_LOGIN') or '').strip() == '1':
            app.logger.error('STAGING_TEST_LOGIN=1 ignored: database is not staging-marked')
        return False
    app.register_blueprint(staging_login_bp)
    app.logger.warning('STAGING TEST LOGIN enabled (loopback only)')
    return True
