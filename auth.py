import os
import hmac
import hashlib
import time
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, session
from flask_login import current_user, login_user, logout_user, login_required
import bcrypt
from dotenv import load_dotenv
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


def _safe_next(val):
    """Faqat xavfsiz nisbiy yo'l (ochiq-redirectni oldini oladi)."""
    if val and val.startswith('/') and not val.startswith('//'):
        return val
    return None


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


@auth_bp.route('/login/password', methods=['POST'])
def password_login():
    from app import User
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').encode()
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, email, password_hash, COALESCE(is_admin, FALSE) "
                "FROM users WHERE username = %s",
                (username,))
            row = cur.fetchone()
        conn.close()
        if row and row[3]:
            # password_hash may come back as str (text) or bytes/memoryview (bytea).
            stored = row[3]
            hash_bytes = bytes(stored) if isinstance(stored, (bytes, bytearray, memoryview)) else stored.encode()
            if bcrypt.checkpw(password, hash_bytes):
                session.permanent = True
                login_user(User(row[0], row[1], row[2], row[4]), remember=True)
                session.modified = True
                return redirect(_post_login_dest(url_for('index')))
        return render_template('login.html', error='Login yoki parol xato')
    except Exception:
        return render_template('login.html', error='Xatolik yuz berdi')


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
os.environ.setdefault('OAUTHLIB_INSECURE_TRANSPORT', '1')


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
    except Exception as e:
        print("Main Google OAuth error: " + str(e))
        return render_template('login.html',
                               error="Google bilan kirishda xatolik yuz berdi.", registered=None)

    email = (info.get('email') or '').strip().lower()
    name = (info.get('name') or '').strip()
    if not email:
        return render_template('login.html',
                               error="Google hisobidan email olinmadi.", registered=None)

    try:
        user, created = _find_or_create_google_user(email, name)
    except Exception as e:
        print("Main Google user upsert error: " + str(e))
        return render_template('login.html',
                               error="Google bilan kirishda xatolik yuz berdi.", registered=None)

    session.permanent = True
    login_user(user, remember=True)
    session.modified = True
    nxt = session.pop('main_google_next', '/') or '/'
    session.pop('main_google_state', None)
    # New accounts complete their scholar profile first (cabinet onboarding
    # bridges the main-site session in via _bridge_from_main).
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


@auth_bp.route('/login/telegram', methods=['POST'])
def telegram_login():
    from app import User
    raw = request.get_json(silent=True) or {}

    if not TELEGRAM_BOT_TOKEN:
        return jsonify({'success': False, 'error': 'TELEGRAM_BOT_TOKEN sozlanmagan'}), 200

    try:
        # Work on a copy so we don't mutate the original dict
        data = dict(raw)
        check_hash = data.pop('hash', '')

        if not check_hash:
            return jsonify({'success': False, 'error': 'Hash mavjud emas'}), 200

        data_check = '\n'.join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hashlib.sha256(TELEGRAM_BOT_TOKEN.encode()).digest()
        computed = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed, check_hash):
            return jsonify({
                'success': False,
                'error': "Bot domain sozlanmagan: @BotFather da /setdomain buyrug'ini bering"
            }), 200

        if time.time() - int(data.get('auth_date', 0)) > 86400:
            return jsonify({'success': False, 'error': "Tasdiqlash muddati o'tgan, qayta urinib ko'ring"}), 200

        tg_id    = str(data.get('id', ''))
        username = (data.get('username') or f"tg_{tg_id}").strip()
        email    = f"{tg_id}@telegram.uz"

        if not tg_id:
            return jsonify({'success': False, 'error': 'Telegram ID topilmadi'}), 200

        is_new = False
        conn = get_connection()
        try:
            cur = conn.cursor()
            # Look up by telegram email to handle username changes
            cur.execute("SELECT id, username, email FROM users WHERE email = %s", (email,))
            user_row = cur.fetchone()

            if not user_row:
                # Also check by username in case email lookup misses
                cur.execute("SELECT id, username, email FROM users WHERE username = %s", (username,))
                user_row = cur.fetchone()

            if not user_row:
                cur.execute(
                    "INSERT INTO users (username, email, password_hash, is_admin) "
                    "VALUES (%s, %s, %s, %s) RETURNING id",
                    (username, email, 'telegram_auth', False)
                )
                user_id = cur.fetchone()[0]
                conn.commit()
                is_new = True
            else:
                user_id = user_row[0]
                username = user_row[1]
                email    = user_row[2] or email

            # Har login bo'lganda tashriflar sonini oshiramiz (so'rovnoma gate'i uchun).
            cur.execute(
                "UPDATE users SET visit_count = COALESCE(visit_count, 0) + 1 WHERE id = %s",
                (user_id,))
            conn.commit()
            cur.close()
        finally:
            conn.close()

    except Exception as e:
        return jsonify({'success': False, 'error': f"Xatolik: {str(e)}"}), 200

    session.permanent = True
    login_user(User(user_id, username, email), remember=True)
    session.modified = True   # sessiya cookie brauzerga aniq yozilishi uchun
    dest = _post_login_dest('/')
    return jsonify({'success': True, 'redirect': '/cabinet/onboarding' if is_new else dest})
