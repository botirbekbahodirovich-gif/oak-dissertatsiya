import os
import hmac
import hashlib
import time
from urllib.parse import urlsplit, unquote
from flask import (Blueprint, current_app, render_template, request, redirect,
                   url_for, jsonify, session)
from flask_login import current_user, login_user, logout_user, login_required
from dotenv import load_dotenv
from security import log_exception
import telegram_oidc
load_dotenv()

TELEGRAM_BOT_USERNAME = os.environ.get('TELEGRAM_BOT_USERNAME', 'send_kod_bot')
TELEGRAM_BOT_TOKEN    = os.environ.get('TELEGRAM_BOT_TOKEN', '')
try:
    import psycopg2
    from psycopg2 import errors as psycopg2_errors
except Exception:
    psycopg2 = None
    psycopg2_errors = None

auth_bp = Blueprint('auth', __name__)


def get_database_url():
    from data import get_normalized_db_url
    return get_normalized_db_url()


def get_connection():
    # Delegate to the hardened, pooled connection in data.py (SSL enforced,
    # connect timeout + keepalives) so login works on managed Postgres.
    from data import get_connection as _get_connection
    return _get_connection()


# Post-login destination must be a normal navigational HTML page — never an
# API/XHR-only endpoint, a static asset, or the auth flow's own routes.
# Production incident: templates/base.html's checkNotifs() background
# fetch('/api/notifications/count') 302-redirected an unauthenticated visitor
# through /login?next=/api/notifications/count, which the old permissive
# _safe_next() (only "starts with / and not //") happily accepted and stored
# in session['login_next'] — poisoning the *next* real login's destination
# with a JSON endpoint. See also _unauthorized() in app.py, which stops the
# redirect chain at its source for API paths.
_UNSAFE_NEXT_PREFIXES = ('/api/', '/static/', '/login/')
_UNSAFE_NEXT_EXACT = ('/login', '/logout', '/register')


def _safe_next(val):
    """Faqat xavfsiz, navigatsion (HTML) nisbiy yo'lni qaytaradi.

    Ochiq-redirectni (//evil.com, https://... , orqa-slesh bilan yashiringan
    manzillar) va API/static/auth-oqim manzillarini (post-login destination
    bo'lishi mumkin emas) rad etadi.
    """
    if not val or not isinstance(val, str):
        return None
    val = val.strip()
    if not val.startswith('/') or val.startswith('//'):
        return None
    if any(c in val for c in ('\\', '\x00', '\t', '\n', '\r')):
        return None
    path = unquote(urlsplit(val).path)
    if path in _UNSAFE_NEXT_EXACT or any(path.startswith(p) for p in _UNSAFE_NEXT_PREFIXES):
        return None
    return val


def _post_login_dest(default='/'):
    """Login tugagach yo'naltiriladigan manzil (sessiyadagi next)."""
    return _safe_next(session.pop('login_next', None)) or default


@auth_bp.route('/login')
def login():
    # Parol/username formasi olib tashlandi — faqat Telegram va Google orqali kirish.
    if current_user.is_authenticated:
        return redirect(_post_login_dest(url_for('index')))
    registered = request.args.get('registered')
    # ?next= ni sessiyaga saqlaymiz — barcha login usullari (Google/Telegram/parol)
    # tugagach shu manzilga qaytaradi (masalan /invite/<token>).
    nxt = _safe_next(request.args.get('next'))
    if nxt:
        session['login_next'] = nxt
    return render_template('login.html', error=None, registered=registered)


@auth_bp.route('/register')
def register():
    # Ro'yxatdan o'tish formasi olib tashlandi — faqat Telegram / Google tugmalari.
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    return render_template('register.html', error=None)


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))


# ── Google OAuth — asosiy sayt (users jadvali + Flask-Login) ─────────────────
# Cabinet uchun alohida Google oqimi cabinet.py da (cabinet_users jadvali). Bu
# oqim esa foydalanuvchini asosiy saytga — users jadvaliga — kiritadi.
#
# MUHIM: callback foydalanuvchi boshlagan HOST da bo'lishi shart. Sessiya cookie
# host ga bog'langan (host-scoped), shuning uchun agar oqim olimlar.uz da
# boshlanib, callback www.olimlar.uz ga tushsa — google_oauth_state cookie
# callback ga yuborilmaydi va state tekshiruvi buziladi ("xatolik yuz berdi").
# redirect_uri ni so'rovdan dinamik quramiz => olimlar.uz ham, www.olimlar.uz
# ham to'g'ri ishlaydi. Google Cloud Console da IKKALA callback URL ham
# ro'yxatdan o'tkazilishi kerak (pastdagi izohga qarang).
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
GOOGLE_USERINFO_URL = 'https://www.googleapis.com/oauth2/v3/userinfo'
GOOGLE_SCOPES = ['openid',
                 'https://www.googleapis.com/auth/userinfo.email',
                 'https://www.googleapis.com/auth/userinfo.profile']
# Local dev (http) uchun ruxsat — production da nginx/TLS terminatsiya qiladi.
# OAuth transport policy is configured centrally in app.py.


def _google_redirect_uri():
    # ProxyFix (x_proto/x_host) tufayli request.url_root to'g'ri sxema+host
    # (https://olimlar.uz/ yoki https://www.olimlar.uz/) beradi.
    return request.url_root.rstrip('/') + '/login/google/callback'


@auth_bp.route('/login/google')
def google_login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        return render_template('login.html', error="Google login sozlanmagan.", registered=None)
    try:
        from requests_oauthlib import OAuth2Session
    except Exception:
        return render_template('login.html', error="Google login mavjud emas.", registered=None)

    google = OAuth2Session(GOOGLE_CLIENT_ID, scope=GOOGLE_SCOPES,
                           redirect_uri=_google_redirect_uri())
    authorization_url, state = google.authorization_url(
        GOOGLE_AUTH_URL, access_type='offline', prompt='select_account')
    session['main_google_state'] = state
    # ?next= to'g'ridan-to'g'ri, bo'lmasa /login da saqlangan sessiya next'i.
    nxt = _safe_next(request.args.get('next')) or _safe_next(session.get('login_next')) or '/'
    session['main_google_next'] = nxt
    return redirect(authorization_url)


@auth_bp.route('/login/google/callback')
def google_callback():
    try:
        from requests_oauthlib import OAuth2Session
        google = OAuth2Session(GOOGLE_CLIENT_ID,
                               state=session.get('main_google_state'),
                               redirect_uri=_google_redirect_uri())
        google.fetch_token(GOOGLE_TOKEN_URL, client_secret=GOOGLE_CLIENT_SECRET,
                           authorization_response=request.url)
        info = google.get(GOOGLE_USERINFO_URL).json()
    except Exception:
        log_exception(current_app.logger, "Main Google OAuth callback bajarilmadi")
        return render_template('login.html',
                               error="Google bilan kirishda xatolik yuz berdi.", registered=None)

    email = (info.get('email') or '').strip().lower()
    name = (info.get('name') or '').strip()
    if not email:
        return render_template('login.html',
                               error="Google hisobidan email olinmadi.", registered=None)

    try:
        user, created = _find_or_create_google_user(email, name)
    except Exception:
        log_exception(current_app.logger, "Google foydalanuvchisini saqlab bo'lmadi")
        return render_template('login.html',
                               error="Google bilan kirishda xatolik yuz berdi.", registered=None)

    session.permanent = True
    login_user(user, remember=True)
    session.modified = True
    # Re-validate even though main_google_next was already checked at
    # google_login() time — keeps this callback safe on its own regardless
    # of how the session value was populated (defense in depth, matches
    # tg_oidc_next below).
    nxt = _safe_next(session.pop('main_google_next', None)) or '/'
    session.pop('main_google_state', None)
    session['just_registered' if created else 'just_logged_in'] = True
    if created:
        # Onboarding formasidagi ism-familiya maydonlarini oldindan to'ldirish
        # uchun (faqat cabinet.py o'z Google oqimi orqali olim_profiles ni
        # allaqachon to'ldirmagan hollarda ishlatiladi — bir marta, session.pop).
        session['prefill_ism'] = (info.get('given_name') or '').strip().title()
        session['prefill_familiya'] = (info.get('family_name') or '').strip().title()
    # New accounts complete their scholar profile first via the single
    # canonical onboarding flow (user-type is chosen by the tab bar at the top
    # of that page). The cabinet onboarding gate bridges the main-site session
    # in via _bridge_from_main.
    return redirect('/cabinet/onboarding' if created else nxt)


def _unique_username(cur, base):
    """users.username UNIQUE — Google ismi band bo'lsa oxiriga raqam qo'shamiz."""
    base = (base or '').strip()[:60] or 'user'
    cur.execute("SELECT 1 FROM users WHERE username = %s", (base,))
    if not cur.fetchone():
        return base
    i = 1
    while True:
        cand = base[:55] + str(i)
        cur.execute("SELECT 1 FROM users WHERE username = %s", (cand,))
        if not cur.fetchone():
            return cand
        i += 1


def _find_or_create_google_user(email, name):
    """users jadvalida email bo'yicha topadi yoki yaratadi. (User, created) qaytaradi."""
    from app import User
    conn = get_connection()
    created = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, email, COALESCE(is_admin, FALSE) "
                "FROM users WHERE LOWER(email) = LOWER(%s)", (email,))
            row = cur.fetchone()
            if not row:
                username = _unique_username(cur, name or email.split('@')[0])
                # password_hash NOT NULL — Google foydalanuvchisi uchun placeholder
                # (parol bilan kira olmaydi, faqat Google orqali).
                cur.execute(
                    "INSERT INTO users (username, email, password_hash) "
                    "VALUES (%s, %s, %s) "
                    "RETURNING id, username, email, COALESCE(is_admin, FALSE)",
                    (username, email, 'google_oauth'))
                row = cur.fetchone()
                conn.commit()
                created = True
            return User(row[0], row[1], row[2], row[3]), created
    finally:
        conn.close()


def _login_or_register_telegram_user(tg_id):
    """users jadvalida telegram_id bo'yicha topadi yoki yaratadi.

    Bitta manba — eski widget (HMAC) va yangi OIDC oqimlari bir xil
    hisob-yaratish/bog'lash mantig'idan foydalanishi uchun (ikkalasi ham
    faqat Telegram'ning o'zgarmas raqamli ID'siga bog'laydi, hech qachon
    mutable username'ga emas). (User, is_new) qaytaradi.
    """
    from app import User
    is_new = False
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, username, email FROM users WHERE telegram_id = %s",
            (tg_id,))
        user_row = cur.fetchone()

        if not user_row:
            username = f"tg_{tg_id}"
            email = f"telegram_{tg_id}@auth.local"
            cur.execute(
                "INSERT INTO users "
                "(username, email, password_hash, is_admin, telegram_id) "
                "VALUES (%s, %s, %s, %s, %s) "
                "RETURNING id, username, email",
                (username, email, 'telegram_auth', False, tg_id)
            )
            user_row = cur.fetchone()
            conn.commit()
            is_new = True
        user_id = user_row[0]
        username = user_row[1]
        email = user_row[2]

        # Har login bo'lganda tashriflar sonini oshiramiz (so'rovnoma gate'i uchun).
        cur.execute(
            "UPDATE users SET visit_count = COALESCE(visit_count, 0) + 1 WHERE id = %s",
            (user_id,))
        conn.commit()
        # Telegram chat id — obuna/eslatma xabarlarini bot orqali yuborish
        # uchun (blueprints/subscriptions.py o'qiydi). Xato loginni buzmaydi.
        try:
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_chat_id BIGINT")
            cur.execute("UPDATE users SET telegram_chat_id = %s WHERE id = %s",
                        (int(tg_id), user_id))
            conn.commit()
        except Exception:
            conn.rollback()
        cur.close()
    finally:
        conn.close()
    return User(user_id, username, email), is_new


@auth_bp.route('/login/telegram', methods=['POST'])
def telegram_login():
    """Legacy widget callback (HMAC payload). Kept for backward compatibility
    while any page still embeds telegram-widget.js; the main /login page no
    longer does (see /login/telegram/start for the current OIDC flow)."""
    raw = request.get_json(silent=True) or {}

    if not TELEGRAM_BOT_TOKEN:
        return jsonify({'success': False, 'error': 'Telegram login vaqtincha mavjud emas.'}), 503

    try:
        # Work on a copy so we don't mutate the original dict
        data = dict(raw)
        check_hash = data.pop('hash', '')

        if not check_hash:
            return jsonify({"success": False, "error": "Tasdiqlash ma'lumoti to'liq emas."}), 400

        data_check = '\n'.join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hashlib.sha256(TELEGRAM_BOT_TOKEN.encode()).digest()
        computed = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed, check_hash):
            return jsonify({
                'success': False,
                'error': "Telegram orqali tasdiqlash amalga oshmadi."
            }), 401

        if time.time() - int(data.get('auth_date', 0)) > 86400:
            return jsonify({'success': False, 'error': "Tasdiqlash muddati o'tgan, qayta urinib ko'ring"}), 401

        tg_id = int(data.get('id', 0))

        if not tg_id:
            return jsonify({'success': False, 'error': 'Telegram ID topilmadi'}), 400

        user, is_new = _login_or_register_telegram_user(tg_id)

    except Exception:
        log_exception(current_app.logger, "Telegram login bajarilmadi")
        return jsonify({'success': False, 'error': "Tizimga kirib bo'lmadi."}), 500

    session.permanent = True
    login_user(user, remember=True)
    session['just_registered' if is_new else 'just_logged_in'] = True
    if is_new:
        session['prefill_ism'] = (data.get('first_name') or '').strip().title()
        session['prefill_familiya'] = (data.get('last_name') or '').strip().title()
    session.modified = True   # sessiya cookie brauzerga aniq yozilishi uchun
    dest = _post_login_dest('/')
    return jsonify({'success': True, 'redirect': '/cabinet/onboarding' if is_new else dest})


# ── Telegram Login — OpenID Connect (current) ────────────────────────────────
# Legacy iframe widget above is kept only for other pages that still embed
# it; the /login page itself now uses this server-side Authorization Code +
# PKCE flow. See telegram_oidc.py for the endpoint/verification details.
_TG_OIDC_SESSION_KEYS = (
    'tg_oidc_state', 'tg_oidc_verifier', 'tg_oidc_nonce',
    'tg_oidc_next', 'tg_oidc_expires',
)
_TG_OIDC_FLOW_TTL_SECONDS = 600


def _telegram_oidc_redirect_uri():
    # request.url_root already resolves to the correct scheme+host via
    # ProxyFix, matching the same pattern as _google_redirect_uri().
    return request.url_root.rstrip('/') + '/login/telegram/callback'


def _clear_telegram_oidc_session():
    for key in _TG_OIDC_SESSION_KEYS:
        session.pop(key, None)


@auth_bp.route('/login/telegram/start')
def telegram_login_start():
    if current_user.is_authenticated:
        return redirect(_post_login_dest(url_for('index')))
    if not telegram_oidc.is_configured():
        return render_template('login.html', error="Telegram login sozlanmagan.", registered=None)

    state = telegram_oidc.generate_state()
    verifier = telegram_oidc.generate_pkce_verifier()
    challenge = telegram_oidc.pkce_challenge(verifier)
    nonce = telegram_oidc.generate_state()
    nxt = _safe_next(request.args.get('next')) or _safe_next(session.get('login_next')) or '/'

    session['tg_oidc_state'] = state
    session['tg_oidc_verifier'] = verifier
    session['tg_oidc_nonce'] = nonce
    session['tg_oidc_next'] = nxt
    session['tg_oidc_expires'] = time.time() + _TG_OIDC_FLOW_TTL_SECONDS
    session.modified = True

    auth_url = telegram_oidc.build_authorization_url(
        _telegram_oidc_redirect_uri(), state, challenge, nonce)
    return redirect(auth_url)


@auth_bp.route('/login/telegram/callback')
def telegram_login_callback():
    if request.args.get('error'):
        _clear_telegram_oidc_session()
        return render_template(
            'login.html', error="Telegram orqali kirish bekor qilindi.", registered=None)

    expected_state = session.get('tg_oidc_state')
    verifier = session.get('tg_oidc_verifier')
    nonce = session.get('tg_oidc_nonce')
    expires = session.get('tg_oidc_expires') or 0
    # Re-validate (see _safe_next docstring) rather than trusting the stored
    # value outright: an invalid/API destination here must fall back to '/',
    # never surface raw JSON to the user after a successful login.
    nxt = _safe_next(session.get('tg_oidc_next')) or '/'
    # Single-use regardless of outcome: a replayed callback URL must fail
    # closed (no verifier/state left to complete the exchange).
    _clear_telegram_oidc_session()

    if not expected_state or not verifier or time.time() > expires:
        return render_template(
            'login.html',
            error="Kirish seansi muddati tugagan. Qaytadan urinib ko'ring.",
            registered=None)

    got_state = request.args.get('state', '')
    if not got_state or not hmac.compare_digest(got_state, expected_state):
        return render_template(
            'login.html',
            error="Xavfsizlik tekshiruvi muvaffaqiyatsiz o'tmadi. Qaytadan urinib ko'ring.",
            registered=None)

    code = request.args.get('code')
    if not code:
        return render_template(
            'login.html', error="Telegram tasdiqlash kodi topilmadi.", registered=None)

    try:
        id_token = telegram_oidc.exchange_code_for_id_token(
            code, _telegram_oidc_redirect_uri(), verifier)
        claims = telegram_oidc.verify_id_token(id_token, nonce=nonce)
    except telegram_oidc.TelegramOIDCError:
        log_exception(current_app.logger, "Telegram OIDC almashinuvi bajarilmadi")
        return render_template(
            'login.html', error="Telegram bilan bog'lanishda xatolik yuz berdi.", registered=None)

    tg_id = claims['id']
    first_name = (claims.get('given_name') or '').strip()
    last_name = (claims.get('family_name') or '').strip()

    try:
        user, is_new = _login_or_register_telegram_user(tg_id)
    except Exception:
        log_exception(current_app.logger, "Telegram foydalanuvchisini saqlab bo'lmadi")
        return render_template('login.html', error="Tizimga kirib bo'lmadi.", registered=None)

    session.permanent = True
    login_user(user, remember=True)
    session['just_registered' if is_new else 'just_logged_in'] = True
    if is_new:
        session['prefill_ism'] = first_name.title()
        session['prefill_familiya'] = last_name.title()
    session.modified = True
    return redirect('/cabinet/onboarding' if is_new else nxt)
