"""Researcher cabinet / portfolio system.

Self-contained auth via Flask `session` (separate from the main-site Flask-Login
session), so it never interferes with the existing Telegram login on the main site.
"""
import os
import re
import hmac
import hashlib
import time
from functools import wraps

from flask import (Blueprint, render_template, request, redirect, url_for,
                   jsonify, session)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

from data import get_connection

load_dotenv()
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_BOT_USERNAME = os.environ.get('TELEGRAM_BOT_USERNAME', 'send_kod_bot')

cabinet_bp = Blueprint('cabinet', __name__)


# ── helpers ────────────────────────────────────────────────────────────────
def current_cabinet_user():
    uid = session.get('cabinet_user_id')
    if not uid:
        return None
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, email, telegram_username, telegram_first_name, olim_name "
                    "FROM cabinet_users WHERE id = %s", (uid,))
                r = cur.fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    if not r:
        return None
    return {"id": r[0], "email": r[1], "telegram_username": r[2],
            "telegram_first_name": r[3], "olim_name": r[4]}


def cabinet_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('cabinet_user_id') and not _bridge_from_main():
            if request.path.startswith('/cabinet/api/'):
                return jsonify({"ok": False, "error": "auth"}), 401
            return redirect(url_for('cabinet.login', next=request.full_path
                                    if request.query_string else request.path))
        return view(*args, **kwargs)
    return wrapped


def _bridge_from_main():
    """Bridge the main-site Flask-Login session into the cabinet session.

    If the visitor is authenticated on the main site (users table) but has no
    cabinet session, find-or-create a cabinet_users row by email and attach the
    cabinet session. This lets main-site OAuth/registration flow straight into
    the cabinet + onboarding without a separate cabinet login."""
    if session.get('cabinet_user_id'):
        return True
    try:
        from flask_login import current_user
        if not getattr(current_user, 'is_authenticated', False):
            return False
        email = (getattr(current_user, 'email', '') or '').strip().lower()
    except Exception:
        return False
    if not email:
        return False
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, olim_name FROM cabinet_users WHERE LOWER(email) = LOWER(%s)", (email,))
                r = cur.fetchone()
                if r:
                    _set_session(r[0], r[1])
                else:
                    name = (getattr(current_user, 'username', '') or '').strip()
                    cur.execute(
                        "INSERT INTO cabinet_users (email, telegram_first_name, created_at, last_login) "
                        "VALUES (%s, %s, NOW(), NOW()) RETURNING id", (email, name or None))
                    _set_session(cur.fetchone()[0], None)
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception:
        return False


def _set_session(user_id, olim_name=None):
    # Commit a durable (permanent) session so a page refresh re-mounts the
    # authenticated cabinet profile instead of dropping into a guest loop.
    session.permanent = True
    session['cabinet_user_id'] = user_id
    session['cabinet_olim_name'] = olim_name or ''


def _touch_login(cur, user_id):
    cur.execute("UPDATE cabinet_users SET last_login = CURRENT_TIMESTAMP WHERE id = %s", (user_id,))


def _olim_name():
    """Primary claimed name for the current cabinet user (or '')."""
    u = current_cabinet_user()
    return (u or {}).get('olim_name') or ''


# ── pages ──────────────────────────────────────────────────────────────────
@cabinet_bp.route('/cabinet')
@cabinet_login_required
def cabinet():
    user = current_cabinet_user()
    olim_name = (user or {}).get('olim_name') or ''
    profile = None
    maqolalar = konferensiyalar = ish_faoliyati = rasmlar = shogirdlar = []
    claimed = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM olim_profiles WHERE cabinet_user_id = %s LIMIT 1", (user['id'],))
                cols = [c[0] for c in cur.description]
                row = cur.fetchone()
                if row:
                    profile = dict(zip(cols, row))
                # all names claimed by this user
                cur.execute("SELECT olim_name FROM olim_profiles WHERE cabinet_user_id = %s", (user['id'],))
                claimed = [r[0] for r in cur.fetchall() if r[0]]
                if olim_name:
                    def _f(sql, order):
                        cur.execute(sql + " ORDER BY " + order, (olim_name,))
                        cn = [c[0] for c in cur.description]
                        return [dict(zip(cn, rr)) for rr in cur.fetchall()]
                    maqolalar = _f("SELECT * FROM olim_maqolalar WHERE LOWER(TRIM(olim_name))=LOWER(TRIM(%s))",
                                   "year DESC NULLS LAST, id DESC")
                    konferensiyalar = _f("SELECT * FROM olim_konferensiyalar WHERE LOWER(TRIM(olim_name))=LOWER(TRIM(%s))",
                                         "date DESC NULLS LAST, id DESC")
                    ish_faoliyati = _f("SELECT * FROM olim_ish_faoliyati WHERE LOWER(TRIM(olim_name))=LOWER(TRIM(%s))",
                                       "start_date DESC NULLS LAST, id DESC")
                    rasmlar = _f("SELECT * FROM olim_rasmlar WHERE LOWER(TRIM(olim_name))=LOWER(TRIM(%s))",
                                 "created_at DESC, id DESC")
                    shogirdlar = _f("SELECT * FROM olim_shogirdlar WHERE LOWER(TRIM(olim_name))=LOWER(TRIM(%s))",
                                    "year DESC NULLS LAST, id DESC")
        finally:
            conn.close()
    except Exception:
        pass
    return render_template('cabinet.html', user=user, profile=profile or {},
                           olim_name=olim_name, claimed=claimed,
                           maqolalar=maqolalar, konferensiyalar=konferensiyalar,
                           ish_faoliyati=ish_faoliyati, rasmlar=rasmlar,
                           shogirdlar=shogirdlar,
                           telegram_bot_username=TELEGRAM_BOT_USERNAME)


@cabinet_bp.route('/cabinet/onboarding')
@cabinet_login_required
def onboarding():
    """Post-registration 'complete your profile' flow. Uses the existing
    search-olim / claim / profile-save endpoints; on finish sets
    profile_completed and lands the user in the full cabinet."""
    user = current_cabinet_user()
    olim_name = (user or {}).get('olim_name') or ''
    profile = {}
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM olim_profiles WHERE cabinet_user_id = %s LIMIT 1", (user['id'],))
                row = cur.fetchone()
                if row:
                    profile = dict(zip([c[0] for c in cur.description], row))
        finally:
            conn.close()
    except Exception:
        pass
    return render_template('cabinet_onboarding.html', user=user, profile=profile,
                           olim_name=olim_name, degree_choices=_DEGREE_CHOICES,
                           telegram_bot_username=TELEGRAM_BOT_USERNAME)


def _safe_next(target):
    """Return a safe local redirect path, or None."""
    if not target:
        return None
    if target.startswith('/') and not target.startswith('//') and '\\' not in target:
        return target
    return None


@cabinet_bp.route('/cabinet/login', methods=['GET', 'POST'])
def login():
    nxt = _safe_next(request.values.get('next'))
    if session.get('cabinet_user_id'):
        return redirect(nxt or url_for('cabinet.cabinet'))
    error = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        if not email or not password:
            error = "Email va parolni kiriting."
        else:
            try:
                conn = get_connection()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id, password_hash, olim_name FROM cabinet_users WHERE email = %s", (email,))
                        r = cur.fetchone()
                        if r and r[1] and check_password_hash(r[1], password):
                            _touch_login(cur, r[0])
                            conn.commit()
                            _set_session(r[0], r[2])
                            return redirect(nxt or url_for('cabinet.cabinet'))
                        error = "Email yoki parol noto'g'ri."
                finally:
                    conn.close()
            except Exception:
                error = "Kirishda xatolik yuz berdi."
    return render_template('cabinet_login.html', error=error, next=nxt or '',
                           telegram_bot_username=TELEGRAM_BOT_USERNAME)


@cabinet_bp.route('/cabinet/register', methods=['GET', 'POST'])
def register():
    if session.get('cabinet_user_id'):
        return redirect(url_for('cabinet.cabinet'))
    error = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        if not email or not password:
            error = "Barcha maydonlarni to'ldiring."
        elif len(password) < 6:
            error = "Parol kamida 6 ta belgi bo'lishi kerak."
        elif password != confirm:
            error = "Parollar mos kelmadi."
        else:
            pw_hash = generate_password_hash(password)
            try:
                conn = get_connection()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id FROM cabinet_users WHERE email = %s", (email,))
                        if cur.fetchone():
                            error = "Bu email allaqachon ro'yxatdan o'tgan."
                        else:
                            cur.execute(
                                "INSERT INTO cabinet_users (email, password_hash) VALUES (%s, %s) RETURNING id",
                                (email, pw_hash))
                            new_id = cur.fetchone()[0]
                            conn.commit()
                            _set_session(new_id, None)
                            return redirect(url_for('cabinet.onboarding'))
                finally:
                    conn.close()
            except Exception:
                error = "Ro'yxatdan o'tishda xatolik yuz berdi."
    return render_template('cabinet_register.html', error=error)


@cabinet_bp.route('/cabinet/telegram', methods=['POST'])
def telegram():
    """Telegram Login Widget callback for the cabinet (separate from main-site login)."""
    raw = request.get_json(silent=True) or {}
    if not TELEGRAM_BOT_TOKEN:
        return jsonify({"success": False, "error": "TELEGRAM_BOT_TOKEN sozlanmagan"}), 200
    try:
        data = dict(raw)
        check_hash = data.pop('hash', '')
        if not check_hash:
            return jsonify({"success": False, "error": "Hash mavjud emas"}), 200
        data_check = '\n'.join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hashlib.sha256(TELEGRAM_BOT_TOKEN.encode()).digest()
        computed = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed, check_hash):
            return jsonify({"success": False, "error": "Tasdiqlash amalga oshmadi"}), 200
        if time.time() - int(data.get('auth_date', 0)) > 86400:
            return jsonify({"success": False, "error": "Muddati o'tgan"}), 200
        tg_id = int(data.get('id', 0))
        if not tg_id:
            return jsonify({"success": False, "error": "Telegram ID topilmadi"}), 200
        username = (data.get('username') or '').strip()
        first_name = (data.get('first_name') or '').strip()
        is_new = False
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, olim_name FROM cabinet_users WHERE telegram_id = %s", (tg_id,))
                r = cur.fetchone()
                if r:
                    _touch_login(cur, r[0])
                    conn.commit()
                    _set_session(r[0], r[1])
                else:
                    cur.execute(
                        "INSERT INTO cabinet_users (telegram_id, telegram_username, telegram_first_name) "
                        "VALUES (%s, %s, %s) RETURNING id", (tg_id, username, first_name))
                    new_id = cur.fetchone()[0]
                    conn.commit()
                    _set_session(new_id, None)
                    is_new = True
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"success": False, "error": f"Xatolik: {e}"}), 200
    return jsonify({"success": True, "redirect": "/cabinet/onboarding" if is_new else "/cabinet"})


# ── Google OAuth ─────────────────────────────────────────────────────────────
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
GOOGLE_USERINFO_URL = 'https://www.googleapis.com/oauth2/v3/userinfo'
GOOGLE_REDIRECT_URI = os.environ.get(
    'GOOGLE_REDIRECT_URI', 'https://www.olimlar.uz/auth/google/callback')
GOOGLE_SCOPES = ['openid',
                 'https://www.googleapis.com/auth/userinfo.email',
                 'https://www.googleapis.com/auth/userinfo.profile']
# Allow HTTP for local dev (Railway/Cloudflare terminates TLS in production).
os.environ.setdefault('OAUTHLIB_INSECURE_TRANSPORT', '1')


@cabinet_bp.route('/auth/google/login')
def google_login():
    if not GOOGLE_CLIENT_ID:
        return redirect('/cabinet/login?error=google')
    try:
        from requests_oauthlib import OAuth2Session
    except Exception:
        return redirect('/cabinet/login?error=google')
    google = OAuth2Session(GOOGLE_CLIENT_ID, scope=GOOGLE_SCOPES,
                           redirect_uri=GOOGLE_REDIRECT_URI)
    authorization_url, state = google.authorization_url(
        GOOGLE_AUTH_URL, access_type='offline', prompt='select_account')
    session['google_oauth_state'] = state
    nxt = request.args.get('next', '/')
    # Only allow safe relative redirects to avoid open-redirect.
    if not (nxt.startswith('/') and not nxt.startswith('//')):
        nxt = '/'
    session['google_next'] = nxt
    return redirect(authorization_url)


@cabinet_bp.route('/auth/google/callback')
def google_callback():
    try:
        from requests_oauthlib import OAuth2Session
        google = OAuth2Session(GOOGLE_CLIENT_ID,
                               state=session.get('google_oauth_state'),
                               redirect_uri=GOOGLE_REDIRECT_URI)
        google.fetch_token(GOOGLE_TOKEN_URL, client_secret=GOOGLE_CLIENT_SECRET,
                           authorization_response=request.url)
        user_info = google.get(GOOGLE_USERINFO_URL).json()

        email = (user_info.get('email') or '').strip()
        name = (user_info.get('name') or '').strip()
        picture = user_info.get('picture') or ''
        google_id = user_info.get('sub') or ''

        if not email:
            return redirect('/cabinet/login?error=google')

        dest = '/'
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, olim_name FROM cabinet_users WHERE email = %s", (email,))
                row = cur.fetchone()
                if row:
                    # Existing account — refresh login, link Google id + photo.
                    cur.execute(
                        "UPDATE cabinet_users SET last_login = NOW(), google_id = %s, "
                        "photo_url = COALESCE(photo_url, %s) WHERE id = %s",
                        (google_id, picture or None, row[0]))
                    conn.commit()
                    _set_session(row[0], row[1])
                else:
                    # New account.
                    cur.execute(
                        "INSERT INTO cabinet_users "
                        "(email, google_id, telegram_first_name, photo_url, created_at, last_login) "
                        "VALUES (%s, %s, %s, %s, NOW(), NOW()) RETURNING id",
                        (email, google_id, name, picture or None))
                    new_id = cur.fetchone()[0]
                    conn.commit()
                    _set_session(new_id, None)
                    dest = '/cabinet/onboarding'
                    # Seed an olim_profiles row (olim_name is NOT NULL UNIQUE).
                    try:
                        parts = name.split(' ', 1)
                        first_name = parts[0] if parts else name
                        last_name = parts[1] if len(parts) > 1 else ''
                        cur.execute(
                            "INSERT INTO olim_profiles "
                            "(olim_name, first_name, last_name, photo_url, cabinet_user_id) "
                            "VALUES (%s, %s, %s, %s, %s)",
                            (f"cabinet_{new_id}", first_name, last_name,
                             picture or None, new_id))
                        conn.commit()
                    except Exception:
                        conn.rollback()
        finally:
            conn.close()

        # Asosiy sayt (Flask-Login / users jadvali) ga ham kiritamiz — Google
        # bilan kirgan foydalanuvchi bosh sahifada avtorizatsiyalangan bo'ladi.
        _login_main_user(email, name)

        session.pop('google_next', None)
        return redirect(dest)
    except Exception as e:
        print(f"Google OAuth error: {e}")
        return redirect('/cabinet/login?error=google')


def _login_main_user(email, name=None):
    """users jadvalida email bo'yicha topadi/yaratadi va Flask-Login qiladi."""
    from flask_login import login_user
    from app import User
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, username, email FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
            if not row:
                cur.execute(
                    "INSERT INTO users (username, email, password_hash) "
                    "VALUES (%s, %s, %s) RETURNING id, username, email",
                    (email, email, 'google_auth'))
                row = cur.fetchone()
                conn.commit()
    finally:
        conn.close()
    session.permanent = True
    login_user(User(row[0], row[1], row[2]), remember=True)


@cabinet_bp.route('/cabinet/api/logout', methods=['POST', 'GET'])
def logout():
    session.pop('cabinet_user_id', None)
    session.pop('cabinet_olim_name', None)
    return redirect(url_for('home'))


# ── profile + claim ────────────────────────────────────────────────────────
_PROFILE_FIELDS = [
    'first_name', 'last_name', 'patronymic', 'title', 'position', 'institution',
    'bio', 'birth_year', 'photo_url', 'region',
    'academic_degree', 'academic_rank',
    'magistratura_mavzu', 'magistratura_institution', 'magistratura_year',
    # Degree-specific dissertation fields (PhD / DSc / magistr).
    'ixtisoslik', 'dissertatsiya_mavzu', 'advisor_name', 'consultant_name',
    'phd_advisor_name', 'opponents', 'defense_year', 'yonalish',
    'profile_completed',
    # Academic links only — Pinterest/Facebook/YouTube/Instagram intentionally dropped.
    'scopus_url', 'wos_url', 'scholar_url', 'orcid_url', 'website_url',
    'telegram_url',
]

# Canonical academic-degree values stored in olim_profiles.academic_degree.
# The onboarding form offers 6 human labels; these map onto the 3 base degrees
# plus an "in progress" flag captured separately via academic_rank-free logic.
_DEGREE_CHOICES = [
    'Magistrant', 'Magistr',
    'Izlanuvchi PhD', 'PhD',
    'Izlanuvchi DSc', 'DSc',
]


@cabinet_bp.route('/cabinet/api/profile/save', methods=['POST'])
@cabinet_login_required
def profile_save():
    user = current_cabinet_user()
    data = request.get_json(silent=True) or request.form
    # Only touch fields actually present in the payload so a single-field save
    # (inline edit) never wipes the rest of the profile.
    vals = {}
    for f in _PROFILE_FIELDS:
        if f not in data:
            continue
        raw = data.get(f)
        v = raw.strip() if isinstance(raw, str) else raw
        vals[f] = v if v not in ('', None) else None
    if not vals:
        return jsonify({"ok": True})
    for _yf in ('birth_year', 'magistratura_year', 'defense_year'):
        if vals.get(_yf):
            try:
                vals[_yf] = int(vals[_yf])
            except (TypeError, ValueError):
                vals[_yf] = None
    if 'profile_completed' in vals:
        vals['profile_completed'] = str(data.get('profile_completed')).lower() in ('1', 'true', 'yes', 'on')
    # Google Scholar havolasi — bo'sh yoki rasmiy domen bilan boshlanishi shart
    # (client'dagi saveLinksForm tekshiruvining server tomondagi jufti).
    if vals.get('scholar_url') and not vals['scholar_url'].startswith('https://scholar.google.com/'):
        return jsonify({"ok": False, "error": "Google Scholar havolasi "
                        "https://scholar.google.com/ bilan boshlanishi kerak"}), 200
    olim_name = user.get('olim_name') or (vals.get('last_name') or vals.get('first_name') or '').strip()
    if not olim_name:
        olim_name = f"cabinet_{user['id']}"
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM olim_profiles WHERE cabinet_user_id = %s LIMIT 1", (user['id'],))
                existing = cur.fetchone()
                cols = list(vals.keys())
                if existing:
                    set_clause = ", ".join(f"{c} = %s" for c in cols) + ", updated_at = CURRENT_TIMESTAMP"
                    cur.execute(f"UPDATE olim_profiles SET {set_clause} WHERE id = %s",
                                [vals[c] for c in cols] + [existing[0]])
                else:
                    all_cols = ['olim_name', 'cabinet_user_id'] + cols
                    placeholders = ", ".join(["%s"] * len(all_cols))
                    cur.execute(
                        f"INSERT INTO olim_profiles ({', '.join(all_cols)}) VALUES ({placeholders})",
                        [olim_name, user['id']] + [vals[c] for c in cols])
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200
    return jsonify({"ok": True})


_AVATAR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'static', 'uploads', 'avatars')


def _delete_local_avatar(photo_url):
    """Remove a previously-uploaded avatar file from disk (ignore external URLs)."""
    if not photo_url or not photo_url.startswith('/static/uploads/avatars/'):
        return
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            photo_url.lstrip('/'))
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


def _set_profile_photo(cur, user_id, photo_url):
    """Set photo_url on the user's olim_profiles row (create it if missing),
    and mirror it onto cabinet_users so the avatar is consistent everywhere."""
    cur.execute("SELECT id FROM olim_profiles WHERE cabinet_user_id = %s LIMIT 1", (user_id,))
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE olim_profiles SET photo_url = %s, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = %s", (photo_url, row[0]))
    else:
        cur.execute(
            "INSERT INTO olim_profiles (olim_name, cabinet_user_id, photo_url) "
            "VALUES (%s, %s, %s)", (f"cabinet_{user_id}", user_id, photo_url))
    cur.execute("UPDATE cabinet_users SET photo_url = %s WHERE id = %s", (photo_url, user_id))


@cabinet_bp.route('/cabinet/api/avatar/upload', methods=['POST'])
@cabinet_login_required
def avatar_upload():
    user_id = session.get('cabinet_user_id')
    file = request.files.get('photo')
    if not file or not file.filename:
        return jsonify({"success": False, "error": "Fayl tanlanmagan"}), 400
    from werkzeug.utils import secure_filename
    ext = os.path.splitext(secure_filename(file.filename))[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.webp'):
        return jsonify({"success": False, "error": "Faqat JPG, PNG, WEBP qabul qilinadi"}), 400
    try:
        os.makedirs(_AVATAR_DIR, exist_ok=True)
        filename = f"avatar_{user_id}_{int(time.time())}{ext}"
        file.save(os.path.join(_AVATAR_DIR, filename))
        photo_url = f"/static/uploads/avatars/{filename}"
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                # Drop the previous locally-stored avatar, if any.
                cur.execute("SELECT photo_url FROM olim_profiles WHERE cabinet_user_id = %s LIMIT 1",
                            (user_id,))
                old = cur.fetchone()
                if old and old[0]:
                    _delete_local_avatar(old[0])
                _set_profile_photo(cur, user_id, photo_url)
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    return jsonify({"success": True, "photo_url": photo_url})


@cabinet_bp.route('/cabinet/api/avatar/remove', methods=['POST'])
@cabinet_login_required
def avatar_remove():
    user_id = session.get('cabinet_user_id')
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT photo_url FROM olim_profiles WHERE cabinet_user_id = %s LIMIT 1",
                            (user_id,))
                row = cur.fetchone()
                if row and row[0]:
                    _delete_local_avatar(row[0])
                _set_profile_photo(cur, user_id, None)
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    return jsonify({"success": True})


@cabinet_bp.route('/cabinet/api/search-olim')
@cabinet_login_required
def search_olim():
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify({"results": []})
    like = f"%{q.lower()}%"
    results = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT TRIM(olim), COUNT(*) AS cnt, MIN(mavzu) AS sample_mavzu, "
                    "MIN(daraja) AS sample_daraja FROM dissertations "
                    "WHERE olim IS NOT NULL AND TRIM(olim) <> '' AND LOWER(TRIM(olim)) LIKE %s "
                    "GROUP BY TRIM(olim) ORDER BY cnt DESC LIMIT 25", (like,))
                results = [{"name": r[0], "count": r[1],
                            "mavzu": r[2] or "", "daraja": (r[3] or "").upper()}
                           for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        results = []
    return jsonify({"results": results})


def _autofill_from_dissertations(cur, name):
    """Populate empty degree fields on the claimed scholar's olim_profiles row
    from the dissertations base (DSc row preferred). Never overwrites data the
    user already entered — only fills columns that are currently empty."""
    cur.execute(
        "SELECT daraja, muassasa, ilmiy_rahbar, mavzu, ixtisoslik, "
        "opponent_1, opponent_2, opponent_3, sana FROM dissertations "
        "WHERE LOWER(TRIM(olim)) = LOWER(TRIM(%s)) "
        "ORDER BY (CASE WHEN UPPER(COALESCE(daraja,'')) LIKE '%%DSC%%' "
        "OR LOWER(COALESCE(daraja,'')) LIKE '%%док%%' THEN 0 ELSE 1 END), id DESC",
        (name,))
    rows = cur.fetchall()
    if not rows:
        return
    daraja, muassasa, rahbar, mavzu, ixt, o1, o2, o3, sana = rows[0]
    up, low = (daraja or '').upper(), (daraja or '').lower()
    if 'DSC' in up or 'док' in low:
        degree = 'DSc'
    elif 'PHD' in up or 'phd' in low or 'фан' in low:
        degree = 'PhD'
    else:
        degree = None
    yrs = re.findall(r'(?:19|20)\d{2}', sana or '')
    year = int(yrs[-1]) if yrs else None
    codes = re.findall(r'\d{2}\.\d{2}\.\d{2}', ixt or '')
    ixt_code = codes[0] if codes else ((ixt or '').strip() or None)
    opps = '; '.join(o.strip() for o in (o1, o2, o3) if o and o.strip()) or None
    fills = {
        'academic_degree': degree,
        'institution': (muassasa or '').strip() or None,
        'dissertatsiya_mavzu': (mavzu or '').strip() or None,
        'ixtisoslik': ixt_code,
        'opponents': opps,
        'defense_year': year,
    }
    # For a DSc defence the supervisor slot is the scientific consultant.
    if degree == 'DSc':
        fills['consultant_name'] = (rahbar or '').strip() or None
    else:
        fills['advisor_name'] = (rahbar or '').strip() or None
    sets, params = [], []
    for col, val in fills.items():
        if val is None:
            continue
        if col == 'defense_year':
            sets.append(f"{col} = COALESCE({col}, %s)")
        else:
            sets.append(f"{col} = COALESCE(NULLIF(TRIM({col}), ''), %s)")
        params.append(val)
    if not sets:
        return
    params.append(name)
    cur.execute("UPDATE olim_profiles SET " + ", ".join(sets) +
                " WHERE LOWER(TRIM(olim_name)) = LOWER(TRIM(%s))", params)


@cabinet_bp.route('/cabinet/api/search-advisor')
@cabinet_login_required
def search_advisor():
    """Autocomplete over dissertations.ilmiy_rahbar (supervisor / consultant)."""
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify({"results": []})
    like = f"%{q.lower()}%"
    results = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT TRIM(ilmiy_rahbar), COUNT(*) AS cnt FROM dissertations "
                    "WHERE ilmiy_rahbar IS NOT NULL AND TRIM(ilmiy_rahbar) <> '' "
                    "AND LOWER(TRIM(ilmiy_rahbar)) LIKE %s "
                    "GROUP BY TRIM(ilmiy_rahbar) ORDER BY cnt DESC LIMIT 15", (like,))
                results = [{"name": r[0], "count": r[1]} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        results = []
    return jsonify({"results": results})


@cabinet_bp.route('/cabinet/api/search-institution')
@cabinet_login_required
def search_institution():
    """Autocomplete over institution_map (deduped; matches Cyrillic AND Latin,
    shows the Latin name). Falls back to raw dissertations.muassasa if the map
    is not populated yet."""
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify({"results": []})
    like = f"%{q.lower()}%"
    # Apostrophe-insensitive Latin match: users type o'zbekiston / o`zbekiston,
    # stored latin_name may have none (transliteration) or a different mark.
    _apos = "'`’‘ʼ"
    q_lat = q.lower()
    for ch in _apos:
        q_lat = q_lat.replace(ch, '')
    like_lat = f"%{q_lat}%"
    results = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(canonical_name, cyrillic_name) AS canon, "
                    "       COUNT(*) AS variants "
                    "FROM institution_map "
                    "WHERE is_active = TRUE "
                    "AND (LOWER(cyrillic_name) LIKE %s "
                    "     OR TRANSLATE(LOWER(COALESCE(latin_name, '')), %s, '') LIKE %s) "
                    "GROUP BY canon ORDER BY variants DESC, canon "
                    "LIMIT 15", (like, _apos, like_lat))
                from institutions import transliterate_display
                results = [{"name": transliterate_display(r[0]), "cyrillic": r[0],
                            "count": r[1]} for r in cur.fetchall()]
                if not results:
                    cur.execute(
                        "SELECT TRIM(muassasa), COUNT(*) AS cnt FROM dissertations "
                        "WHERE muassasa IS NOT NULL AND TRIM(muassasa) <> '' "
                        "AND LOWER(TRIM(muassasa)) LIKE %s "
                        "GROUP BY TRIM(muassasa) ORDER BY cnt DESC LIMIT 15", (like,))
                    results = [{"name": r[0], "count": r[1]} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        results = []
    return jsonify({"results": results})


@cabinet_bp.route('/cabinet/api/claim', methods=['POST'])
@cabinet_login_required
def claim():
    user = current_cabinet_user()
    data = request.get_json(silent=True) or {}
    name = (data.get('olim_name') or '').strip()
    if not name:
        return jsonify({"ok": False, "error": "Ism kiritilmagan"}), 200
    pr = None
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                # upsert an olim_profiles row for this name linked to the user
                cur.execute("SELECT id, cabinet_user_id FROM olim_profiles WHERE LOWER(TRIM(olim_name))=LOWER(TRIM(%s))", (name,))
                row = cur.fetchone()
                if row and row[1] and row[1] != user['id']:
                    return jsonify({"ok": False, "error": "Bu profil boshqa foydalanuvchi tomonidan band qilingan."}), 200
                if row:
                    cur.execute("UPDATE olim_profiles SET cabinet_user_id = %s WHERE id = %s", (user['id'], row[0]))
                else:
                    cur.execute("INSERT INTO olim_profiles (olim_name, cabinet_user_id) VALUES (%s, %s)", (name, user['id']))
                cur.execute("UPDATE cabinet_users SET olim_name = %s WHERE id = %s", (name, user['id']))
                # Auto-fill degree/institution/advisor/topic/opponents from the base.
                _autofill_from_dissertations(cur, name)
                cur.execute(
                    "SELECT academic_degree, institution, advisor_name, consultant_name, "
                    "dissertatsiya_mavzu, ixtisoslik, opponents, defense_year "
                    "FROM olim_profiles WHERE LOWER(TRIM(olim_name)) = LOWER(TRIM(%s))", (name,))
                pr = cur.fetchone()
            conn.commit()
            session['cabinet_olim_name'] = name
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200
    profile = {}
    if pr:
        profile = dict(zip(
            ('academic_degree', 'institution', 'advisor_name', 'consultant_name',
             'dissertatsiya_mavzu', 'ixtisoslik', 'opponents', 'defense_year'), pr))
    return jsonify({"ok": True, "olim_name": name, "profile": profile})


@cabinet_bp.route('/cabinet/api/unclaim', methods=['POST'])
@cabinet_login_required
def unclaim():
    user = current_cabinet_user()
    data = request.get_json(silent=True) or {}
    name = (data.get('olim_name') or '').strip()
    if not name:
        return jsonify({"success": False, "error": "Ism kiritilmagan"}), 400
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                # the claimed profile row for this name must belong to this user
                cur.execute(
                    "SELECT id FROM olim_profiles "
                    "WHERE cabinet_user_id = %s AND LOWER(TRIM(olim_name)) = LOWER(TRIM(%s)) LIMIT 1",
                    (user['id'], name))
                row = cur.fetchone()
                if not row:
                    return jsonify({"success": False, "error": "Bu dissertatsiya sizga biriktirilmagan"}), 400
                # release the claim on this profile
                cur.execute("UPDATE olim_profiles SET cabinet_user_id = NULL WHERE id = %s", (row[0],))
                # if it was the user's primary name, repoint to another remaining claim (or clear)
                new_primary = None
                if (user.get('olim_name') or '').strip().lower() == name.lower():
                    cur.execute(
                        "SELECT olim_name FROM olim_profiles "
                        "WHERE cabinet_user_id = %s AND olim_name IS NOT NULL ORDER BY id LIMIT 1",
                        (user['id'],))
                    rem = cur.fetchone()
                    new_primary = rem[0] if rem else None
                    cur.execute("UPDATE cabinet_users SET olim_name = %s WHERE id = %s",
                                (new_primary, user['id']))
            conn.commit()
            if (user.get('olim_name') or '').strip().lower() == name.lower():
                session['cabinet_olim_name'] = new_primary or ''
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 200
    return jsonify({"success": True, "message": "Dissertatsiya ajratildi"})


# ── generic portfolio item CRUD ────────────────────────────────────────────
def _insert_item(table, fields, form):
    name = _olim_name()
    if not name:
        return jsonify({"ok": False, "error": "Avval dissertatsiyangizni biriktiring."}), 200
    vals = [name] + [(_clean(form.get(f))) for f in fields]
    cols = ['olim_name'] + fields
    placeholders = ", ".join(["%s"] * len(cols))
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) RETURNING id", vals)
                new_id = cur.fetchone()[0]
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200
    return jsonify({"ok": True, "id": new_id})


def _edit_item(table, fields, item_id, form):
    name = _olim_name()
    set_clause = ", ".join(f"{f} = %s" for f in fields)
    vals = [_clean(form.get(f)) for f in fields] + [item_id, name]
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {table} SET {set_clause} WHERE id = %s AND LOWER(TRIM(olim_name))=LOWER(TRIM(%s))", vals)
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200
    return jsonify({"ok": True})


def _delete_item(table, item_id):
    name = _olim_name()
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {table} WHERE id = %s AND LOWER(TRIM(olim_name))=LOWER(TRIM(%s))", (item_id, name))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200
    return jsonify({"ok": True})


def _clean(v):
    if v is None:
        return None
    v = str(v).strip()
    return v if v else None


def _form():
    return request.get_json(silent=True) or request.form


# maqolalar — journal_flag: jurnal tekshiruvi natijasi ('trusted'/'unknown'/
# 'suspect'), tavsiya xarakterida — saqlashni hech qachon bloklamaydi.
_MAQOLA = ['title', 'authors', 'journal', 'year', 'citations', 'url', 'journal_flag']


@cabinet_bp.route('/cabinet/api/maqola/add', methods=['POST'])
@cabinet_login_required
def maqola_add():
    return _insert_item('olim_maqolalar', _MAQOLA, _form())


@cabinet_bp.route('/cabinet/api/maqola/edit/<int:id>', methods=['POST'])
@cabinet_login_required
def maqola_edit(id):
    return _edit_item('olim_maqolalar', _MAQOLA, id, _form())


@cabinet_bp.route('/cabinet/api/maqola/delete/<int:id>', methods=['POST'])
@cabinet_login_required
def maqola_delete(id):
    return _delete_item('olim_maqolalar', id)


# konferensiyalar
_KONF = ['title', 'conference_name', 'location', 'date', 'url']


@cabinet_bp.route('/cabinet/api/konferensiya/add', methods=['POST'])
@cabinet_login_required
def konf_add():
    return _insert_item('olim_konferensiyalar', _KONF, _form())


@cabinet_bp.route('/cabinet/api/konferensiya/edit/<int:id>', methods=['POST'])
@cabinet_login_required
def konf_edit(id):
    return _edit_item('olim_konferensiyalar', _KONF, id, _form())


@cabinet_bp.route('/cabinet/api/konferensiya/delete/<int:id>', methods=['POST'])
@cabinet_login_required
def konf_delete(id):
    return _delete_item('olim_konferensiyalar', id)


# ish faoliyati
_ISH = ['position', 'organization', 'start_date', 'end_date', 'is_current']


@cabinet_bp.route('/cabinet/api/ish/add', methods=['POST'])
@cabinet_login_required
def ish_add():
    f = _form()
    name = _olim_name()
    if not name:
        return jsonify({"ok": False, "error": "Avval dissertatsiyangizni biriktiring."}), 200
    is_current = bool(f.get('is_current'))
    end_date = None if is_current else _clean(f.get('end_date'))
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO olim_ish_faoliyati (olim_name, position, organization, start_date, end_date, is_current) "
                    "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (name, _clean(f.get('position')), _clean(f.get('organization')),
                     _clean(f.get('start_date')), end_date, is_current))
                new_id = cur.fetchone()[0]
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200
    return jsonify({"ok": True, "id": new_id})


@cabinet_bp.route('/cabinet/api/ish/edit/<int:id>', methods=['POST'])
@cabinet_login_required
def ish_edit(id):
    f = _form()
    name = _olim_name()
    is_current = bool(f.get('is_current'))
    end_date = None if is_current else _clean(f.get('end_date'))
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE olim_ish_faoliyati SET position=%s, organization=%s, start_date=%s, "
                    "end_date=%s, is_current=%s WHERE id=%s AND LOWER(TRIM(olim_name))=LOWER(TRIM(%s))",
                    (_clean(f.get('position')), _clean(f.get('organization')),
                     _clean(f.get('start_date')), end_date, is_current, id, name))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200
    return jsonify({"ok": True})


@cabinet_bp.route('/cabinet/api/ish/delete/<int:id>', methods=['POST'])
@cabinet_login_required
def ish_delete(id):
    return _delete_item('olim_ish_faoliyati', id)


# rasmlar
_RASM = ['image_url', 'caption']


@cabinet_bp.route('/cabinet/api/rasm/add', methods=['POST'])
@cabinet_login_required
def rasm_add():
    return _insert_item('olim_rasmlar', _RASM, _form())


@cabinet_bp.route('/cabinet/api/rasm/delete/<int:id>', methods=['POST'])
@cabinet_login_required
def rasm_delete(id):
    return _delete_item('olim_rasmlar', id)


# shogirdlar (students) — PhD/DSc supervise students; magistr advisors too.
_SHOGIRD = ['student_name', 'degree', 'year']


@cabinet_bp.route('/cabinet/api/shogird/add', methods=['POST'])
@cabinet_login_required
def shogird_add():
    return _insert_item('olim_shogirdlar', _SHOGIRD, _form())


@cabinet_bp.route('/cabinet/api/shogird/edit/<int:id>', methods=['POST'])
@cabinet_login_required
def shogird_edit(id):
    return _edit_item('olim_shogirdlar', _SHOGIRD, id, _form())


@cabinet_bp.route('/cabinet/api/shogird/delete/<int:id>', methods=['POST'])
@cabinet_login_required
def shogird_delete(id):
    return _delete_item('olim_shogirdlar', id)
