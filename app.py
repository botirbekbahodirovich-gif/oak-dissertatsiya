import os
from dotenv import load_dotenv
from flask_wtf.csrf import CSRFProtect
import bcrypt
from flask import Flask, render_template, redirect, url_for, jsonify, request, abort, flash
from urllib.parse import urlparse, quote
from flask_login import (LoginManager, UserMixin, logout_user,
                         login_required, current_user)

app = Flask(__name__)
# Trust one level of proxy headers (Cloudflare/Railway) so request.remote_addr
# and the X-Forwarded-* family resolve to the real client.
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
load_dotenv()
# Session secret: prefer SESSION_SECRET, then SECRET_KEY (common alias). If
# neither is configured, fall back to an ephemeral key so the app still boots
# (e.g. before env vars are wired on a fresh host) — but warn loudly, because an
# ephemeral key breaks sessions/CSRF across restarts and across Gunicorn workers.
import secrets as _secrets
session_secret = os.environ.get("SESSION_SECRET") or os.environ.get("SECRET_KEY")
if not session_secret:
    session_secret = _secrets.token_hex(32)
    print("WARNING: SESSION_SECRET/SECRET_KEY not set — using an EPHEMERAL key. "
          "Set SESSION_SECRET in the environment for stable sessions/CSRF in production.",
          flush=True)
app.secret_key = session_secret
csrf = CSRFProtect(app)

# Ensure the news image upload directory exists.
try:
    os.makedirs(os.path.join(app.static_folder, "uploads", "news"), exist_ok=True)
except Exception:
    pass

from extensions import cache
# FileSystemCache keeps cached data on disk (the 'flask_cache' folder) instead of
# in RAM — important on a small free-tier VPS where memory is scarce.
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'flask_cache')
try:
    os.makedirs(_CACHE_DIR, exist_ok=True)
except Exception:
    pass
cache.init_app(app, config={
    'CACHE_TYPE': 'FileSystemCache',
    'CACHE_DIR': _CACHE_DIR,
    'CACHE_DEFAULT_TIMEOUT': 300,
})

from flask_wtf.csrf import generate_csrf

# ── Timezone: Uzbekistan / Tashkent (UTC+5) ─────────────────────────────────
import time as _time
from datetime import datetime, timezone, timedelta
os.environ['TZ'] = 'Asia/Tashkent'
try:
    _time.tzset()
except Exception:
    pass  # tzset is unavailable on some platforms (e.g. Windows)
UZT = timezone(timedelta(hours=5))


def uz_now():
    """Current time in Uzbekistan (UTC+5)."""
    return datetime.now(UZT)


@app.template_filter('uztime')
def uz_time_filter(dt):
    """Format a timestamp in Tashkent time. Naive datetimes are assumed UTC
    (timestamps are stored in UTC by the database)."""
    if dt is None or dt == '':
        return ''
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except Exception:
            return dt
    if not isinstance(dt, datetime):
        return str(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(UZT).strftime('%d.%m.%Y %H:%M')


def parse_device(ua):
    """Human-readable device + browser from a User-Agent string."""
    ua = (ua or '').lower()
    if 'mobile' in ua or 'android' in ua or 'iphone' in ua or 'ipad' in ua:
        device = '📱 Mobil'
    else:
        device = '💻 Kompyuter'
    if 'edg' in ua:
        browser = 'Edge'
    elif 'chrome' in ua:
        browser = 'Chrome'
    elif 'firefox' in ua:
        browser = 'Firefox'
    elif 'safari' in ua:
        browser = 'Safari'
    else:
        browser = 'Boshqa'
    return f"{device} — {browser}"


def parse_referrer(ref):
    """Human-readable traffic source from a Referer header."""
    if not ref:
        return "🔗 To'g'ridan-to'g'ri"
    r = ref.lower()
    if 'google' in r:
        return '🔍 Google'
    if 't.me' in r or 'telegram' in r:
        return '📱 Telegram'
    if 'facebook' in r:
        return '📘 Facebook'
    if 'instagram' in r:
        return '📷 Instagram'
    if 'olimlar.uz' in r:
        return '🏠 Olimlar.uz'
    return '🌐 ' + ref[:50]

@app.context_processor
def _inject_csrf_token():
    return dict(csrf_token=lambda: '<input type="hidden" name="csrf_token" value="%s">' % generate_csrf())


_FEMALE_NAMES = {
    'гулнора', 'дилноза', 'малика', 'мадина', 'нигора', 'наргиза', 'зулфия', 'феруза',
    'шахло', 'барно', 'мунира', 'дилбар', 'нафиса', 'хилола', 'сарвиноз', 'камола',
    'юлдуз', 'лола', 'севара', 'нодира', 'зиёда', 'мухаббат', 'гавхар', 'дурдона',
    'матлуба', 'хуршида', 'азиза',
}


def detect_gender(full_name):
    """Detect gender from Uzbek/Russian name patterns. Returns 'female', 'male', or 'unknown'."""
    if not full_name or not full_name.strip():
        return 'unknown'
    name = full_name.strip().lower()
    parts = name.split()
    # Patronymic (otasining ismi) — most reliable
    for part in parts:
        if part.endswith(('овна', 'евна', 'ёвна', 'қизи', 'qizi')):
            return 'female'
        if part.endswith(('ович', 'евич', 'ёвич', 'ўғли', "o'g'li", 'угли', 'уғли')):
            return 'male'
    # Surname endings
    for part in parts:
        if part.endswith(('ова', 'ева', 'ёва', 'ская', 'цкая', 'ная', 'яна')):
            return 'female'
        if part.endswith(('ов', 'ев', 'ёв', 'ский', 'цкий', 'ной', 'ян')):
            if len(part) > 3:
                return 'male'
    # Common Uzbek female first names
    for part in parts:
        if part in _FEMALE_NAMES:
            return 'female'
    return 'unknown'


@app.context_processor
def inject_gender_detector():
    return dict(detect_gender=detect_gender)


# Uzbekistan regions — used by the cabinet profile form and the mandatory
# region-selection popup (base.html).
UZ_REGIONS = [
    "Toshkent shahri", "Toshkent viloyati", "Andijon", "Buxoro", "Farg'ona",
    "Jizzax", "Xorazm", "Namangan", "Navoiy", "Qashqadaryo", "Qoraqalpog'iston",
    "Samarqand", "Sirdaryo", "Surxondaryo",
]


@app.context_processor
def inject_uz_regions():
    return dict(uz_regions=UZ_REGIONS)


@app.template_filter('uni_link')
def uni_link(name):
    """Render a university name as a clickable link to its profile page.

    Used so any occurrence of an institution name across the site (tables, olim
    profiles, dissertations) navigates to /university/<name>."""
    from markupsafe import Markup, escape
    n = (name or '').strip()
    if not n:
        return ''
    return Markup('<a href="/university/{0}" class="uni-link">{1}</a>').format(
        quote(n, safe=''), escape(n))


@app.context_processor
def _inject_cabinet():
    """Expose cabinet (portfolio) session state to all templates."""
    from flask import session
    return dict(
        cabinet_logged_in=bool(session.get('cabinet_user_id')),
        cabinet_olim_name=session.get('cabinet_olim_name') or '',
    )


@app.context_processor
def _inject_supervisor_counts():
    """Expose cached supervisor → student-count lookup to every template."""
    def supervisor_count(name):
        if not name:
            return 0
        try:
            from data import get_supervisor_counts
            return get_supervisor_counts().get(str(name).strip(), 0)
        except Exception:
            return 0
    return dict(supervisor_count=supervisor_count)


@app.context_processor
def inject_auth_status():
    from flask import session
    is_logged_in = False
    current_username = None
    try:
        if hasattr(current_user, 'is_authenticated') and current_user.is_authenticated:
            is_logged_in = True
            current_username = getattr(current_user, 'username', None)
        elif 'cabinet_user_id' in session:
            is_logged_in = True
    except Exception:
        pass
    return dict(is_logged_in=is_logged_in, current_username=current_username)


@app.context_processor
def inject_broadcasts():
    """Expose active admin broadcasts to every template, filtered by audience."""
    from flask import session
    try:
        is_logged_in = False
        if hasattr(current_user, 'is_authenticated') and current_user.is_authenticated:
            is_logged_in = True
        elif 'cabinet_user_id' in session:
            is_logged_in = True
        from data import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, message, message_type, show_to
                    FROM admin_broadcasts
                    WHERE is_active = TRUE
                    AND (expires_at IS NULL OR expires_at > NOW())
                    ORDER BY created_at DESC LIMIT 5
                """)
                rows = cur.fetchall()
        finally:
            conn.close()
        filtered = []
        for r in rows:
            show_to = r[3] or 'all'
            if (show_to == 'all'
                    or (show_to == 'guests' and not is_logged_in)
                    or (show_to == 'registered' and is_logged_in)):
                filtered.append({"id": r[0], "message": r[1] or "",
                                 "message_type": r[2] or "info", "show_to": show_to})
        return dict(active_broadcasts=filtered)
    except Exception:
        return dict(active_broadcasts=[])


@app.context_processor
def inject_region_status():
    """True when the current logged-in user has not yet recorded a region, so
    base.html can fire the mandatory region popup 20s after page load."""
    from flask import session
    needs = False
    try:
        from data import get_connection
        if hasattr(current_user, 'is_authenticated') and current_user.is_authenticated:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT region FROM users WHERE id = %s",
                                (int(current_user.get_id()),))
                    row = cur.fetchone()
                    needs = not (row and row[0])
            finally:
                conn.close()
        elif session.get('cabinet_user_id'):
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT region FROM olim_profiles WHERE cabinet_user_id = %s "
                                "LIMIT 1", (session['cabinet_user_id'],))
                    row = cur.fetchone()
                    needs = not (row and row[0])
            finally:
                conn.close()
    except Exception:
        needs = False
    return dict(needs_region=needs)


@app.context_processor
def inject_visit_count():
    """Logged-in foydalanuvchining tashriflar soni — so'rovnoma faqat 10+ tashrifda."""
    vc = 0
    try:
        if hasattr(current_user, 'is_authenticated') and current_user.is_authenticated:
            from data import get_connection
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT COALESCE(visit_count, 0) FROM users WHERE id = %s",
                                (int(current_user.get_id()),))
                    row = cur.fetchone()
                    vc = row[0] if row else 0
            finally:
                conn.close()
    except Exception:
        vc = 0
    return dict(visit_count=vc)


@app.route('/api/profile/set-region', methods=['POST'])
@csrf.exempt
def set_region():
    """Persist the region chosen in the mandatory popup, to whichever profile
    (main user / cabinet user) the visitor is logged into."""
    from flask import session
    data = request.get_json(silent=True) or request.form
    region = (data.get('region') or '').strip()
    if not region or region not in UZ_REGIONS:
        return jsonify({"ok": False, "error": "invalid_region"}), 400
    try:
        from data import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                if hasattr(current_user, 'is_authenticated') and current_user.is_authenticated:
                    cur.execute("UPDATE users SET region = %s WHERE id = %s",
                                (region, int(current_user.get_id())))
                elif session.get('cabinet_user_id'):
                    uid = session['cabinet_user_id']
                    cur.execute("SELECT id FROM olim_profiles WHERE cabinet_user_id = %s LIMIT 1",
                                (uid,))
                    row = cur.fetchone()
                    if row:
                        cur.execute("UPDATE olim_profiles SET region = %s WHERE id = %s",
                                    (region, row[0]))
                    else:
                        cur.execute("INSERT INTO olim_profiles (olim_name, cabinet_user_id, region) "
                                    "VALUES (%s, %s, %s)", (f"cabinet_{uid}", uid, region))
                else:
                    return jsonify({"ok": False, "error": "not_authenticated"}), 401
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200
    return jsonify({"ok": True})



def is_safe_relative_url(target: str) -> bool:
    if not target:
        return False
    parsed = urlparse(target)
    return parsed.scheme == "" and parsed.netloc == "" and target.startswith("/")


def get_real_ip():
    """Get real client IP behind Cloudflare/Railway proxy."""
    cf_ip = request.headers.get('CF-Connecting-IP')
    if cf_ip:
        return cf_ip.strip()
    real_ip = request.headers.get('X-Real-IP')
    if real_ip:
        return real_ip.strip()
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        # "client, proxy1, proxy2" — the first hop is the real client.
        return forwarded.split(',')[0].strip()
    return request.remote_addr


def get_country():
    """Two-letter country code from Cloudflare's CF-IPCountry header."""
    return request.headers.get('CF-IPCountry', 'XX')

login_manager = LoginManager(app)
login_manager.login_view = "auth.login"
login_manager.login_message = "Iltimos, tizimga kiring."

# ── Fix 5.2: durable session/token lifecycle ────────────────────────────────
app.config.update(
    SESSION_PERMANENT=True,
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),   # login 7 kun saqlanadi
    REMEMBER_COOKIE_DURATION=timedelta(days=7),
    REMEMBER_COOKIE_HTTPONLY=True,
    REMEMBER_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# ── Fix 5.3: sanitized Supabase avatar path + patronymic normalization ──────
import re as _re_util

AVATAR_BUCKET = ("https://qzbgmfbpryneyacrcdfh.supabase.co/storage/v1/"
                 "object/public/avatars/")
DEFAULT_AVATAR = "/static/images/default-avatar.svg"
_AVATAR_STRIP = "'\"’‘ʻʼ`´"

# Uzbek Cyrillic → Latin map (Supabase avatar filenames are lowercase Latin).
KIRILL_TO_LATIN = {
    'А': 'a', 'Б': 'b', 'В': 'v', 'Г': 'g', 'Д': 'd', 'Е': 'e', 'Ё': 'yo',
    'Ж': 'j', 'З': 'z', 'И': 'i', 'Й': 'y', 'К': 'k', 'Л': 'l', 'М': 'm',
    'Н': 'n', 'О': 'o', 'П': 'p', 'Р': 'r', 'С': 's', 'Т': 't', 'У': 'u',
    'Ф': 'f', 'Х': 'x', 'Ц': 'ts', 'Ч': 'ch', 'Ш': 'sh', 'Щ': 'shch',
    'Ъ': '', 'Ы': 'i', 'Ь': '', 'Э': 'e', 'Ю': 'yu', 'Я': 'ya',
    'Ў': 'o', 'Қ': 'q', 'Ғ': 'g', 'Ҳ': 'h',
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
    'ж': 'j', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'x', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
    'ъ': '', 'ы': 'i', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
    'ў': 'o', 'қ': 'q', 'ғ': 'g', 'ҳ': 'h',
}


def transliterate(text):
    """Uzbek Cyrillic → Latin transliteration (unknown chars pass through)."""
    return "".join(KIRILL_TO_LATIN.get(ch, ch) for ch in (text or ""))


def avatar_url(full_name):
    """Map a scholar name to its sanitized Supabase avatar URL
    ({last}_{first}_{patronymic}.jpg): Cyrillic→Latin, lowercased, spaces→'_',
    quotes/ticks stripped, consecutive underscores collapsed to one."""
    s = (full_name or "").strip()
    if not s:
        return DEFAULT_AVATAR
    s = transliterate(s).lower()
    s = _re_util.sub(r"\s+", "_", s)
    for ch in _AVATAR_STRIP:
        s = s.replace(ch, "")
    s = _re_util.sub(r"_+", "_", s).strip("_")
    if not s:
        return DEFAULT_AVATAR
    return AVATAR_BUCKET + s + ".jpg"


def normalize_patronymic(name):
    """Standardize Uzbek patronymic suffixes (o'g'li / qizi variants) to a uniform form."""
    if not name:
        return name
    s = _re_util.sub(r"[’‘ʻʼ`´]", "'", name)
    s = _re_util.sub(r"\bo'?\s*g'?\s*li\b", "o'g'li", s, flags=_re_util.IGNORECASE)
    s = _re_util.sub(r"\bqiz[iy]\b", "qizi", s, flags=_re_util.IGNORECASE)
    return s


app.jinja_env.globals["avatar_url"] = avatar_url
app.jinja_env.filters["avatar_url"] = avatar_url
app.jinja_env.globals["DEFAULT_AVATAR"] = DEFAULT_AVATAR
app.jinja_env.filters["normalize_patronymic"] = normalize_patronymic


# ── Fix 5.1 (soft / public-friendly): protected-route gate ──────────────────
# Guests browse freely — home, universities list, journals list, scholar profiles
# and the genealogy tree stay public (SEO-critical). Only private, user-specific
# areas require login and redirect guests to /register.
_PROTECTED_PREFIXES = (
    "/dashboard",                 # personal dashboard
    "/cabinet/profile",           # personal profile editor
    "/cabinet/edit",              # profile editor
    "/my-network",                # "My Network"
    "/ego",                       # ego / personal network
    "/api/v1/notifications",      # internal notification triggers
    "/api/v1/grants/track",       # personal grant tracking
    "/api/v1/grants/reminders",   # personal deadline reminders
)


@app.before_request
def require_registration():
    from flask import session as _sess
    if request.method == "OPTIONS":
        return None
    p = request.path
    if not any(p == pre or p.startswith(pre) for pre in _PROTECTED_PREFIXES):
        return None  # public route — guests allowed
    try:
        if current_user.is_authenticated or _sess.get("cabinet_user_id"):
            return None
    except Exception:
        pass
    return redirect("/register")


class User(UserMixin):
    def __init__(self, id, username, email, is_admin=False):
        self.id = id
        self.username = username
        self.email = email
        self.is_admin = bool(is_admin)


@login_manager.user_loader
def load_user(user_id):
    # Use the hardened, pooled connection (SSL enforced + timeouts) so sessions
    # resolve correctly on managed Postgres after the host migration.
    # is_admin is loaded here so admin authorization works for every login flow
    # (Flask-Login rebuilds current_user via this loader on each request).
    try:
        from data import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, username, email, COALESCE(is_admin, FALSE) "
                    "FROM users WHERE id = %s",
                    (int(user_id),))
                row = cur.fetchone()
        finally:
            conn.close()
        return User(*row) if row else None
    except Exception:
        return None

from auth import auth_bp
from data import data_bp, query_dissertations
csrf.exempt(data_bp)
from analytics import analytics_bp
from upload import upload_bp
from cabinet import cabinet_bp
csrf.exempt(cabinet_bp)
from blueprints.admin import admin_bp
from blueprints.content import content_bp
from blueprints.notifications import notifications_bp
from blueprints.grants import grants_bp

app.register_blueprint(auth_bp)
app.register_blueprint(data_bp)
app.register_blueprint(analytics_bp)
app.register_blueprint(upload_bp)
app.register_blueprint(cabinet_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(content_bp)
app.register_blueprint(notifications_bp)
app.register_blueprint(grants_bp)

# Telegram login uses HMAC hash verification — no CSRF token needed
csrf.exempt(app.view_functions['auth.telegram_login'])


# ── University seed data + detection ────────────────────────────────────────
# (name, university_type) — city/region are detected from the name.
from seed_data import UNIVERSITY_SEED as _UNIVERSITY_SEED  # full OAK OTM list


def detect_uni_city_region(name):
    """Detect (city, region) for a university from keywords in its name."""
    n = (name or '').lower()
    rules = [
        (('buxoro', 'bukhara'),                 ('Buxoro', 'Buxoro')),
        (('andijon', 'andijan'),                ('Andijon', 'Andijon')),
        (('farg', 'fargona', 'qoqon', 'kokand'), ('Fargona', 'Fargona')),
        (('samarqand', 'samarkand'),            ('Samarqand', 'Samarqand')),
        (('namangan',),                          ('Namangan', 'Namangan')),
        (('nukus', 'qoraqalpoq', 'ajiniyoz', 'berdaq'), ('Nukus', 'Qoraqalpogiston')),
        (('termiz', 'surxon'),                  ('Termiz', 'Surxondaryo')),
        (('qarshi', 'shahrisabz'),              ('Qarshi', 'Qashqadaryo')),
        (('jizzax',),                            ('Jizzax', 'Jizzax')),
        (('navoiy',),                            ('Navoiy', 'Navoiy')),
        (('urganch', 'xorazm'),                 ('Urganch', 'Xorazm')),
        (('guliston', 'sirdaryo'),              ('Guliston', 'Sirdaryo')),
        (('chirchiq',),                          ('Chirchiq', 'Toshkent')),
    ]
    for keys, (city, region) in rules:
        if any(k in n for k in keys):
            return city, region
    return 'Toshkent', 'Toshkent'


def _seed_norm(name):
    """Normalized key for de-duplication (case/apostrophe/spacing-insensitive)."""
    s = (name or '').lower()
    for ch in ("'", "'", "`", "?", "ʼ", "‘", "’"):
        s = s.replace(ch, '')
    return ' '.join(s.split())


def _seed_universities(cur):
    """Additively seed the full OAK university list. De-duplicates against
    existing rows by a normalized key, so re-runs and apostrophe variants never
    create duplicates."""
    cur.execute("SELECT name FROM universities")
    existing = {_seed_norm(r[0]) for r in cur.fetchall()}
    for name, utype in _UNIVERSITY_SEED:
        key = _seed_norm(name)
        if key in existing:
            continue
        city, region = detect_uni_city_region(name)
        cur.execute(
            "INSERT INTO universities (name, university_type, city, region) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (name) DO NOTHING",
            (name, utype, city, region))
        existing.add(key)


# ── Journal seed data (OAK journals by specialty code) ──────────────────────
SPECIALTY_NAMES = {
    '01.00.00': 'Fizika-matematika fanlari',
    '02.00.00': 'Kimyo fanlari',
    '03.00.00': 'Biologiya fanlari',
    '04.00.00': 'Geologiya-mineralogiya fanlari',
    '05.00.00': 'Texnika fanlari',
    '06.00.00': "Qishloq xo'jaligi fanlari",
    '07.00.00': 'Tarix fanlari',
    '08.00.00': 'Iqtisodiyot fanlari',
    '09.00.00': 'Falsafa fanlari',
    '10.00.00': 'Filologiya fanlari',
    '11.00.00': 'Geografiya fanlari',
    '12.00.00': 'Yuridik fanlar',
    '13.00.00': 'Pedagogika fanlari',
    '14.00.00': 'Tibbiyot fanlari',
    '15.00.00': 'Farmatsevtika fanlari',
    '16.00.00': 'Veterinariya fanlari',
    '17.00.00': "San'atshunoslik fanlari",
    '18.00.00': 'Arxitektura fanlari',
    '19.00.00': 'Psixologiya fanlari',
    '22.00.00': 'Sotsiologiya fanlari',
    '23.00.00': 'Siyosiy fanlar',
    '24.00.00': 'Islomshunoslik fanlari',
}

from seed_data import JOURNALS_BY_SPECIALTY  # full OAK journal list


def _seed_journals(cur):
    """Additively seed OAK journals and their specialty links. New journals are
    inserted only if a normalized-name match doesn't already exist; specialty
    links are upserted idempotently (unique journal_id+specialty_code)."""
    cur.execute("SELECT id, name FROM journals")
    rows = cur.fetchall()
    norm_to_id = {_seed_norm(n): i for i, n in rows}
    all_names = set()
    for lst in JOURNALS_BY_SPECIALTY.values():
        all_names.update(lst)
    for nm in sorted(all_names):
        if _seed_norm(nm) in norm_to_id:
            continue
        cur.execute(
            "INSERT INTO journals (name, country, indexing, oak_approved) "
            "VALUES (%s, %s, %s, TRUE) ON CONFLICT (name) DO NOTHING RETURNING id",
            (nm, "O'zbekiston", 'OAK'))
        r = cur.fetchone()
        if r:
            norm_to_id[_seed_norm(nm)] = r[0]
    for code, lst in JOURNALS_BY_SPECIALTY.items():
        sname = SPECIALTY_NAMES.get(code, '')
        for nm in lst:
            jid = norm_to_id.get(_seed_norm(nm))
            if jid:
                cur.execute(
                    "INSERT INTO journal_specialties (journal_id, specialty_code, specialty_name) "
                    "VALUES (%s, %s, %s) ON CONFLICT (journal_id, specialty_code) DO NOTHING",
                    (jid, code, sname))


def _run_startup_migrations():
    try:
        from data import get_connection
        conn = get_connection()
        cols = [
            ('fan_tarmoqi',          'TEXT'),
            ('ixtisoslik_nomi',      'TEXT'),
            ('mavzu_raqami',         'TEXT'),
            ('ilmiy_kengash',        'TEXT'),
            ('ilmiy_kengash_raqami', 'TEXT'),
            ('opponent_1',           'TEXT'),
            ('opponent_2',           'TEXT'),
            ('opponent_3',           'TEXT'),
            ('yetakchi_tashkilot',   'TEXT'),
            ('ilmiy_rahbar_daraja',  'TEXT'),
            ('yonalish',             'TEXT'),
        ]
        with conn.cursor() as cur:
            # Base tables — created here so a brand-new (empty) database is
            # self-sufficient. Without this, the ALTER/seed statements below would
            # fail on the very first run and roll back the whole transaction,
            # leaving the universities/journals tables uncreated (blank pages).
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_admin BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS dissertations (
                    id SERIAL PRIMARY KEY,
                    sana TEXT,
                    daraja TEXT,
                    olim TEXT,
                    mavzu TEXT,
                    ixtisoslik TEXT,
                    muassasa TEXT,
                    ilmiy_rahbar TEXT,
                    link TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            for col, typ in cols:
                cur.execute(
                    f"ALTER TABLE dissertations ADD COLUMN IF NOT EXISTS {col} {typ}"
                )
            cur.execute("ALTER TABLE dissertations ADD COLUMN IF NOT EXISTS photo_url TEXT")
            cur.execute("ALTER TABLE dissertations ADD COLUMN IF NOT EXISTS ilmiy_rahbar_photo_url TEXT")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS notifications (
                    id SERIAL PRIMARY KEY,
                    message TEXT,
                    count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW(),
                    is_read BOOLEAN DEFAULT FALSE
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS page_visits (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER,
                    username TEXT,
                    page TEXT,
                    ip_address TEXT,
                    user_agent TEXT,
                    visited_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_visits_time ON page_visits(visited_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_visits_user ON page_visits(user_id)")
            cur.execute("ALTER TABLE page_visits ADD COLUMN IF NOT EXISTS user_id INTEGER")
            cur.execute("ALTER TABLE page_visits ADD COLUMN IF NOT EXISTS username VARCHAR(200)")
            cur.execute("ALTER TABLE page_visits ADD COLUMN IF NOT EXISTS user_agent TEXT")
            cur.execute("ALTER TABLE page_visits ADD COLUMN IF NOT EXISTS referrer VARCHAR(500)")
            cur.execute("ALTER TABLE page_visits ADD COLUMN IF NOT EXISTS session_id VARCHAR(100)")
            cur.execute("ALTER TABLE page_visits ADD COLUMN IF NOT EXISTS country VARCHAR(10)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS blocked_users (
                    id SERIAL PRIMARY KEY,
                    ip_address VARCHAR(50),
                    user_id INTEGER,
                    reason VARCHAR(500),
                    blocked_by VARCHAR(100) DEFAULT 'admin',
                    blocked_until TIMESTAMP,
                    is_permanent BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_blocked_ip ON blocked_users(ip_address)")
            cur.execute("ALTER TABLE blocked_users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")
            cur.execute("ALTER TABLE blocked_users ADD COLUMN IF NOT EXISTS duration_text VARCHAR(50)")
            cur.execute("ALTER TABLE blocked_users ADD COLUMN IF NOT EXISTS unblocked_at TIMESTAMP")
            cur.execute("ALTER TABLE blocked_users ADD COLUMN IF NOT EXISTS unblocked_by VARCHAR(100)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS admin_broadcasts (
                    id SERIAL PRIMARY KEY,
                    message TEXT NOT NULL,
                    message_type VARCHAR(50) DEFAULT 'info',
                    is_active BOOLEAN DEFAULT TRUE,
                    show_to VARCHAR(50) DEFAULT 'all',
                    created_at TIMESTAMP DEFAULT NOW(),
                    expires_at TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS survey_questions (
                    id SERIAL PRIMARY KEY,
                    question_text VARCHAR(500) NOT NULL,
                    question_group INTEGER DEFAULT 1,
                    question_order INTEGER DEFAULT 0,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS survey_responses (
                    id SERIAL PRIMARY KEY,
                    question_id INTEGER REFERENCES survey_questions(id),
                    ip_address VARCHAR(50),
                    user_id INTEGER,
                    username VARCHAR(200),
                    answer VARCHAR(20) NOT NULL,
                    custom_text TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_survey_responses_question ON survey_responses(question_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_survey_responses_ip ON survey_responses(ip_address)")
            # Seed survey questions (only if the table is empty)
            cur.execute("SELECT COUNT(*) FROM survey_questions")
            if (cur.fetchone()[0] or 0) == 0:
                _survey_seed = [
                    ('Platforma sizga maqul kelayaptimi?', 1, 1),
                    ('Aniq izlaganingizni topa oldingizmi?', 1, 2),
                    ('Sayt interfeysi qulaymi?', 1, 3),
                    ('Kurslar bo\'limi sizga maqulmi?', 2, 1),
                    ('Yangiliklar bo\'limini kuzatib borasizmi?', 2, 2),
                    ('Portfoliyoingizni shakllantirdingizmi?', 2, 3),
                    ('AI mavzu tahlili foydali bo\'ldimi?', 3, 1),
                    ('Ilmiy shajara funksiyasini ko\'rdingizmi?', 3, 2),
                    ('Platformani boshqalarga tavsiya qilarmidingiz?', 3, 3),
                    ('Qidiruv natijalaridan qoniqasizmi?', 4, 1),
                    ('Olim profil sahifasi sizga foydali bo\'ldimi?', 4, 2),
                    ('Saytda yana nima bo\'lishini xohlaysiz?', 4, 3),
                    ('Statistika sahifasi tushunarli ekanmi?', 5, 1),
                    ('Blog maqolalari foydali ekanmi?', 5, 2),
                    ('Telegram login qulaymi yoki boshqa usul ham kerakmi?', 5, 3),
                ]
                for _qt, _qg, _qo in _survey_seed:
                    cur.execute(
                        "INSERT INTO survey_questions (question_text, question_group, question_order) "
                        "VALUES (%s, %s, %s)", (_qt, _qg, _qo))
            cur.execute("""
                CREATE TABLE IF NOT EXISTS universities (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(500) NOT NULL UNIQUE,
                    short_name VARCHAR(200),
                    logo_url VARCHAR(500),
                    website VARCHAR(500),
                    city VARCHAR(200),
                    region VARCHAR(200),
                    university_type VARCHAR(100),
                    description TEXT,
                    founded_year INTEGER,
                    rector VARCHAR(300),
                    address VARCHAR(500),
                    phone VARCHAR(100),
                    email VARCHAR(200),
                    telegram VARCHAR(200),
                    student_count INTEGER,
                    teacher_count INTEGER,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_universities_name ON universities (LOWER(name))")
            for _uc, _ut in (
                ('instagram', 'VARCHAR(200)'), ('facebook', 'VARCHAR(200)'),
                ('youtube', 'VARCHAR(200)'), ('rector_photo_url', 'VARCHAR(500)'),
            ):
                cur.execute(f"ALTER TABLE universities ADD COLUMN IF NOT EXISTS {_uc} {_ut}")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS university_images (
                    id SERIAL PRIMARY KEY,
                    university_id INTEGER REFERENCES universities(id) ON DELETE CASCADE,
                    image_url VARCHAR(500) NOT NULL,
                    caption VARCHAR(300),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_university_images_uid "
                        "ON university_images (university_id)")
            _seed_universities(cur)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS journals (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(500) NOT NULL,
                    name_en VARCHAR(500),
                    issn VARCHAR(20),
                    eissn VARCHAR(20),
                    publisher VARCHAR(500),
                    country VARCHAR(100),
                    language VARCHAR(100),
                    category VARCHAR(200),
                    specialty_codes VARCHAR(500),
                    indexing VARCHAR(200),
                    website VARCHAR(500),
                    description TEXT,
                    impact_factor DECIMAL(5,3),
                    publish_fee VARCHAR(200),
                    review_period VARCHAR(100),
                    frequency VARCHAR(100),
                    is_predatory BOOLEAN DEFAULT FALSE,
                    is_active BOOLEAN DEFAULT TRUE,
                    logo_url VARCHAR(500),
                    oak_approved BOOLEAN DEFAULT FALSE,
                    scopus_indexed BOOLEAN DEFAULT FALSE,
                    wos_indexed BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)
            # Unique index on name enables ON CONFLICT (name) for seeding/admin add.
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_journals_name ON journals (name)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_journals_name ON journals (LOWER(name))")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_journals_category ON journals (category)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_journals_specialty ON journals (specialty_codes)")
            for _jc, _jt in (
                ('languages', 'VARCHAR(200)'), ('requirements', 'TEXT'),
                ('registered_number', 'VARCHAR(100)'), ('registered_date', 'VARCHAR(100)'),
                ('article_requirements', 'TEXT'), ('accepts_languages', 'VARCHAR(200)'),
                ('publish_format', 'VARCHAR(200)'),
                ('scholar_indexed', 'BOOLEAN DEFAULT FALSE'),
            ):
                cur.execute(f"ALTER TABLE journals ADD COLUMN IF NOT EXISTS {_jc} {_jt}")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS journal_specialties (
                    id SERIAL PRIMARY KEY,
                    journal_id INTEGER REFERENCES journals(id) ON DELETE CASCADE,
                    specialty_code VARCHAR(20) NOT NULL,
                    specialty_name VARCHAR(200)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_journal_spec_code ON journal_specialties (specialty_code)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_journal_spec_jid ON journal_specialties (journal_id)")
            # De-duplicate any pre-existing pairs, then enforce uniqueness so the
            # additive seeder can ON CONFLICT DO NOTHING on (journal_id, specialty_code).
            cur.execute("""
                DELETE FROM journal_specialties a USING journal_specialties b
                WHERE a.id > b.id AND a.journal_id = b.journal_id
                  AND a.specialty_code = b.specialty_code
            """)
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_journal_spec "
                        "ON journal_specialties (journal_id, specialty_code)")
            _seed_journals(cur)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS yangiliklar (
                    id SERIAL PRIMARY KEY,
                    title VARCHAR(500) NOT NULL,
                    content TEXT,
                    summary VARCHAR(1000),
                    image_url VARCHAR(500),
                    source_url VARCHAR(500),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_published BOOLEAN DEFAULT TRUE
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_yangiliklar_created ON yangiliklar(created_at)")
            cur.execute("ALTER TABLE yangiliklar ADD COLUMN IF NOT EXISTS image_data TEXT")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vacancies (
                    id SERIAL PRIMARY KEY,
                    title VARCHAR(500) NOT NULL,
                    organization VARCHAR(500) NOT NULL,
                    location VARCHAR(300),
                    specialty VARCHAR(300),
                    requirements TEXT,
                    description TEXT,
                    salary VARCHAR(200),
                    contact_info VARCHAR(500),
                    contact_url VARCHAR(500),
                    vacancy_type VARCHAR(100) DEFAULT 'full_time',
                    is_published BOOLEAN DEFAULT TRUE,
                    deadline DATE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_vacancies_created ON vacancies(created_at)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS olim_profiles (
                    id SERIAL PRIMARY KEY,
                    olim_name VARCHAR(500) NOT NULL UNIQUE,
                    photo_url VARCHAR(500),
                    bio TEXT,
                    scopus_url VARCHAR(500),
                    wos_url VARCHAR(500),
                    scholar_url VARCHAR(500),
                    youtube_url VARCHAR(500),
                    facebook_url VARCHAR(500),
                    twitter_url VARCHAR(500),
                    instagram_url VARCHAR(500),
                    telegram_url VARCHAR(500),
                    pinterest_url VARCHAR(500),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS olim_maqolalar (
                    id SERIAL PRIMARY KEY,
                    olim_name VARCHAR(500) NOT NULL,
                    title VARCHAR(1000) NOT NULL,
                    authors TEXT,
                    journal VARCHAR(500),
                    year INTEGER,
                    citations INTEGER DEFAULT 0,
                    url VARCHAR(500),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS olim_konferensiyalar (
                    id SERIAL PRIMARY KEY,
                    olim_name VARCHAR(500) NOT NULL,
                    title VARCHAR(1000) NOT NULL,
                    conference_name VARCHAR(500),
                    location VARCHAR(500),
                    date DATE,
                    url VARCHAR(500),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS olim_ish_faoliyati (
                    id SERIAL PRIMARY KEY,
                    olim_name VARCHAR(500) NOT NULL,
                    position VARCHAR(500) NOT NULL,
                    organization VARCHAR(500),
                    start_date DATE,
                    end_date DATE,
                    is_current BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS olim_rasmlar (
                    id SERIAL PRIMARY KEY,
                    olim_name VARCHAR(500) NOT NULL,
                    image_url VARCHAR(500) NOT NULL,
                    caption VARCHAR(500),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            for _t in ("olim_maqolalar", "olim_konferensiyalar", "olim_ish_faoliyati", "olim_rasmlar"):
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{_t}_name ON {_t}(olim_name)"
                )
            # ── Cabinet (researcher portfolio) ──
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cabinet_users (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) UNIQUE,
                    password_hash VARCHAR(255),
                    telegram_id BIGINT UNIQUE,
                    telegram_username VARCHAR(100),
                    telegram_first_name VARCHAR(100),
                    olim_name VARCHAR(500),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("ALTER TABLE cabinet_users ADD COLUMN IF NOT EXISTS google_id VARCHAR(100)")
            cur.execute("ALTER TABLE cabinet_users ADD COLUMN IF NOT EXISTS photo_url VARCHAR(500)")
            for _col, _typ in (
                ('first_name', 'VARCHAR(200)'), ('last_name', 'VARCHAR(200)'),
                ('patronymic', 'VARCHAR(200)'), ('title', 'VARCHAR(200)'),
                ('position', 'VARCHAR(300)'), ('institution', 'VARCHAR(500)'),
                ('birth_year', 'INTEGER'), ('orcid_url', 'VARCHAR(500)'),
                ('website_url', 'VARCHAR(500)'), ('cabinet_user_id', 'INTEGER'),
                ('academic_degree', 'VARCHAR(50)'), ('academic_rank', 'VARCHAR(100)'),
                ('magistratura_mavzu', 'VARCHAR(1000)'),
                ('magistratura_institution', 'VARCHAR(500)'),
                ('magistratura_year', 'INTEGER'), ('region', 'VARCHAR(100)'),
            ):
                cur.execute(f"ALTER TABLE olim_profiles ADD COLUMN IF NOT EXISTS {_col} {_typ}")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_olim_profiles_olim_name_lower "
                        "ON olim_profiles (LOWER(TRIM(olim_name)))")
            indexes = [
                ("idx_dissertations_olim",         "olim"),
                ("idx_dissertations_ixtisoslik",    "ixtisoslik"),
                ("idx_dissertations_ilmiy_rahbar",  "ilmiy_rahbar"),
                ("idx_dissertations_daraja",        "daraja"),
                ("idx_dissertations_oak_id",        "oak_id"),
                ("idx_dissertations_sana",          "sana"),
            ]
            for idx_name, col in indexes:
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON dissertations({col})"
                )
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_fts ON dissertations
                USING gin(to_tsvector('simple',
                    coalesce(olim,'') || ' ' ||
                    coalesce(mavzu,'') || ' ' ||
                    coalesce(ilmiy_rahbar,'') || ' ' ||
                    coalesce(muassasa,'')
                ))
            """)
            # pg_trgm for fast ILIKE / LIKE search
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            trgm_indexes = [
                ("idx_trgm_olim",     "LOWER(TRIM(olim))"),
                ("idx_trgm_mavzu",    "LOWER(TRIM(mavzu))"),
                ("idx_trgm_rahbar",   "LOWER(TRIM(ilmiy_rahbar))"),
                ("idx_trgm_muassasa", "LOWER(TRIM(muassasa))"),
                ("idx_trgm_opp1",     "LOWER(TRIM(COALESCE(opponent_1,'')))"),
                ("idx_trgm_opp2",     "LOWER(TRIM(COALESCE(opponent_2,'')))"),
                ("idx_trgm_opp3",     "LOWER(TRIM(COALESCE(opponent_3,'')))"),
            ]
            for idx_name, expr in trgm_indexes:
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} "
                    f"ON dissertations USING gin(({expr}) gin_trgm_ops)"
                )
            cur.execute("""
                CREATE TABLE IF NOT EXISTS blog_posts (
                    id SERIAL PRIMARY KEY,
                    title VARCHAR(500) NOT NULL,
                    slug VARCHAR(500) UNIQUE,
                    summary VARCHAR(1000),
                    content TEXT NOT NULL,
                    category VARCHAR(100),
                    image_url VARCHAR(500),
                    author VARCHAR(200) DEFAULT 'Olimlar.uz jamoasi',
                    views INTEGER DEFAULT 0,
                    is_published BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_blog_created ON blog_posts(created_at)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS course_subscribers (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) NOT NULL UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Region (viloyat) for registered main-site users — collected via the
            # mandatory post-registration popup for geography analytics.
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS region VARCHAR(100)")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS visit_count INTEGER DEFAULT 0")
        conn.commit()
        conn.close()
    except Exception:
        pass


def _seed_blog_posts():
    """Insert starter blog posts once (only if the table is empty)."""
    try:
        from data import get_connection
        from blog_seed import SEED_POSTS
    except Exception:
        return
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM blog_posts")
                if (cur.fetchone()[0] or 0) > 0:
                    return
                for p in SEED_POSTS:
                    cur.execute(
                        "INSERT INTO blog_posts (title, slug, summary, content, category) "
                        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (slug) DO NOTHING",
                        (p["title"], p["slug"], p["summary"], p["content"], p["category"]))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


_run_startup_migrations()
_seed_blog_posts()


def _placeholder_news():
    """Fallback news cards shown when the yangiliklar table is empty."""
    import datetime
    today = datetime.date.today().strftime("%Y-%m-%d")
    items = [
        ("Olimlar.uz platformasi ishga tushirildi",
         "Olimlar.uz — O'zbekistondagi eng katta ilmiy-tadqiqot ma'lumotlar bazasi rasman ishga tushdi."),
        ("OAK tizimida yangiliklar",
         "Oliy Attestatsiya Komissiyasi tizimidagi so'nggi o'zgarishlar va e'lonlar haqida ma'lumot."),
        ("AI tadqiqot yordamchisi qo'shildi",
         "Endi sun'iy intellekt yordamida mavzu tanlash va adabiyotlar tahlilini amalga oshirish mumkin."),
        ("27,000+ dissertatsiya bazaga yuklandi",
         "Platformaga 27 mingdan ortiq dissertatsiya himoyasi haqida to'liq ma'lumot qo'shildi."),
    ]
    return [{
        "id": 0, "title": t, "summary": s,
        "created_at": today, "is_placeholder": True,
    } for t, s in items]


@app.route("/")
def home():
    from data import clean_olim_name
    rows = []
    top_rows = []
    news = []
    total_stats = {"dissertations": 0, "researchers": 0, "institutions": 0, "specialties": 0}
    top_random_rows = []
    active_vacancy_count = 0
    try:
        import datetime
        # `sana` is free-form DD.MM.YYYY text — convert to YYYYMMDD for chronological compare/sort.
        sana_key = r"NULLIF(regexp_replace(TRIM(sana), '^(\d{2})\.(\d{2})\.(\d{4})$', '\3\2\1'), TRIM(sana))"
        threshold = (datetime.date.today() - datetime.timedelta(days=3)).strftime("%Y%m%d")
        from data import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                # Recent dissertations — last 3 days, fallback to latest 9 (newest first)
                cur.execute(
                    "SELECT id, olim, mavzu, daraja, sana, muassasa, ixtisoslik, photo_url "
                    "FROM dissertations "
                    f"WHERE mavzu IS NOT NULL AND TRIM(mavzu) != '' AND {sana_key} >= %s "
                    f"ORDER BY {sana_key} DESC NULLS LAST, id DESC LIMIT 30",
                    (threshold,)
                )
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, row)) for row in cur.fetchall()]
                if not rows:
                    cur.execute(
                        "SELECT id, olim, mavzu, daraja, sana, muassasa, ixtisoslik, photo_url "
                        "FROM dissertations "
                        "WHERE mavzu IS NOT NULL AND TRIM(mavzu) != '' "
                        f"ORDER BY {sana_key} DESC NULLS LAST, id DESC LIMIT 9"
                    )
                    cols = [d[0] for d in cur.description]
                    rows = [dict(zip(cols, row)) for row in cur.fetchall()]

                # Most active supervisors (top 20 by student count)
                cur.execute(
                    "SELECT TRIM(ilmiy_rahbar) AS rahbar, COUNT(*) AS cnt, "
                    "MAX(ilmiy_rahbar_photo_url) AS photo_url "
                    "FROM dissertations "
                    "WHERE ilmiy_rahbar IS NOT NULL AND TRIM(ilmiy_rahbar) != '' "
                    "GROUP BY TRIM(ilmiy_rahbar) ORDER BY cnt DESC LIMIT 20"
                )
                top_rows = cur.fetchall()

                # Random 20 supervisors for the marquee
                cur.execute(
                    "SELECT TRIM(ilmiy_rahbar) AS rahbar, COUNT(*) AS cnt, "
                    "MAX(ilmiy_rahbar_photo_url) AS photo_url "
                    "FROM dissertations "
                    "WHERE ilmiy_rahbar IS NOT NULL AND TRIM(ilmiy_rahbar) != '' "
                    "GROUP BY TRIM(ilmiy_rahbar) ORDER BY RANDOM() LIMIT 20"
                )
                top_random_rows = cur.fetchall()

                # Aggregate totals
                cur.execute(
                    "SELECT COUNT(*), "
                    "COUNT(DISTINCT NULLIF(TRIM(olim), '')), "
                    "COUNT(DISTINCT NULLIF(TRIM(muassasa), '')), "
                    "COUNT(DISTINCT NULLIF(TRIM(ixtisoslik), '')) "
                    "FROM dissertations"
                )
                srow = cur.fetchone()
                if srow:
                    total_stats = {
                        "dissertations": srow[0] or 0,
                        "researchers": srow[1] or 0,
                        "institutions": srow[2] or 0,
                        "specialties": srow[3] or 0,
                    }

                # Published news for the carousel
                try:
                    cur.execute(
                        "SELECT id, title, summary, created_at, image_url, image_data FROM yangiliklar "
                        "WHERE is_published = TRUE ORDER BY created_at DESC LIMIT 8"
                    )
                    news = [{
                        "id": r[0], "title": r[1] or "", "summary": r[2] or "",
                        "created_at": str(r[3])[:10] if r[3] else "",
                        "image": r[4] or r[5] or "", "is_placeholder": False,
                    } for r in cur.fetchall()]
                except Exception:
                    news = []

                # Active (published, not expired) vacancy count for the home banner
                try:
                    cur.execute(
                        "SELECT COUNT(*) FROM vacancies WHERE is_published = TRUE "
                        "AND (deadline IS NULL OR deadline >= CURRENT_DATE)"
                    )
                    active_vacancy_count = cur.fetchone()[0] or 0
                except Exception:
                    active_vacancy_count = 0
        finally:
            conn.close()
    except Exception:
        rows, top_rows, top_random_rows = [], [], []

    if not news:
        news = _placeholder_news()

    recent = [{
        "id": row.get("id"),
        "Olim": row.get("olim", "") or "",
        "Olim_display": clean_olim_name(row.get("olim", "") or ""),
        "Mavzu": row.get("mavzu", "") or "",
        "Daraja": row.get("daraja", "") or "",
        "Sana": row.get("sana", "") or "",
        "Muassasa": row.get("muassasa", "") or "",
        "Ixtisoslik": row.get("ixtisoslik", "") or "",
        "photo_url": row.get("photo_url") or "",
    } for row in rows]

    def _sup_list(src):
        return [{
            "name": r[0] or "",
            "display": clean_olim_name(r[0] or ""),
            "count": r[1] or 0,
            "photo_url": r[2] or "",
        } for r in src]

    top_supervisors = _sup_list(top_rows)
    top_supervisors_random = _sup_list(top_random_rows)
    # Combined list for the seamless marquee (top 20 + random 20)
    top_marquee = top_supervisors + top_supervisors_random

    # Specialties: split combined codes ("01.01.01 05.01.07" = 2 specialties), cached.
    try:
        from data import count_distinct_ixtisosliklar
        total_stats["specialties"] = count_distinct_ixtisosliklar() or total_stats.get("specialties", 0)
    except Exception:
        pass

    # Gender split (cached) for the Tadqiqotchilar stat card
    gender_pct = {"male": 0, "female": 0}
    try:
        gs = compute_gender_stats()["gender_stats"]
        gtot = (gs.get("male", 0) + gs.get("female", 0) + gs.get("unknown", 0)) or 1
        gender_pct = {"male": round(gs.get("male", 0) / gtot * 100),
                      "female": round(gs.get("female", 0) / gtot * 100)}
    except Exception:
        pass

    # Latest 3 blog posts
    latest_blog = []
    try:
        from data import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT title, slug, summary, created_at FROM blog_posts "
                    "WHERE is_published = TRUE ORDER BY created_at DESC, id DESC LIMIT 3")
                latest_blog = [{
                    "title": r[0] or "", "slug": r[1] or "", "summary": r[2] or "",
                    "created_at": str(r[3])[:10] if r[3] else "",
                } for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        latest_blog = []

    # Top 6 universities by dissertation count for the home section
    top_universities = []
    try:
        uni_stats = get_university_dissertation_stats()
        from data import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, logo_url, university_type, city "
                            "FROM universities WHERE is_active = TRUE")
                for r in cur.fetchall():
                    s = uni_stats.get(r[0], {})
                    top_universities.append({
                        "name": r[1] or "", "logo_url": r[2] or "",
                        "university_type": r[3] or "", "city": r[4] or "",
                        "diss_count": s.get('total', 0)})
        finally:
            conn.close()
        top_universities = sorted(top_universities, key=lambda x: -x['diss_count'])[:6]
    except Exception:
        top_universities = []

    # Featured journals (OAK-approved) for the home section
    featured_journals = []
    try:
        from data import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, name_en, category, logo_url, oak_approved, "
                    "scopus_indexed, wos_indexed FROM journals "
                    "WHERE is_active = TRUE AND oak_approved = TRUE "
                    "ORDER BY impact_factor DESC NULLS LAST, LOWER(name) LIMIT 4")
                featured_journals = [{
                    "id": r[0], "name": r[1] or "", "name_en": r[2] or "",
                    "category": r[3] or "", "logo_url": r[4] or "", "oak_approved": r[5],
                    "scopus_indexed": r[6], "wos_indexed": r[7]} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        featured_journals = []

    return render_template("home.html", recent=recent, news=news,
                           top_supervisors=top_supervisors,
                           top_supervisors_random=top_supervisors_random,
                           top_marquee=top_marquee, total_stats=total_stats,
                           gender_pct=gender_pct, latest_blog=latest_blog,
                           active_vacancy_count=active_vacancy_count,
                           top_universities=top_universities,
                           featured_journals=featured_journals)


@app.route("/dashboard")
def index():
    return render_template("dashboard.html")


def compute_gender_stats():
    """Gender breakdown over all dissertations. Cached 30 min (processes all records)."""
    cached = cache.get("gender_stats_v1")
    if cached is not None:
        return cached
    import re as _re
    gender_stats = {"male": 0, "female": 0, "unknown": 0}
    gender_by_degree = {"phd_male": 0, "phd_female": 0, "dsc_male": 0, "dsc_female": 0}
    gender_by_year = {}
    name_gender = {}
    try:
        from data import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT olim, daraja, sana FROM dissertations WHERE olim IS NOT NULL AND TRIM(olim) <> ''")
                for olim, daraja, sana in cur.fetchall():
                    g = name_gender.get(olim)
                    if g is None:
                        g = detect_gender(olim)
                        name_gender[olim] = g
                    dl = (daraja or "")
                    deg = "phd" if ("PHD" in dl.upper() or "фан" in dl.lower()) else "dsc"
                    if g in ("male", "female"):
                        key = f"{deg}_{g}"
                        if key in gender_by_degree:
                            gender_by_degree[key] += 1
                        m = _re.search(r"(19|20)\d{2}", sana or "")
                        if m:
                            yr = m.group(0)
                            slot = gender_by_year.setdefault(yr, {"male": 0, "female": 0})
                            slot[g] += 1
        finally:
            conn.close()
        # distinct-researcher gender counts
        for g in name_gender.values():
            gender_stats[g] = gender_stats.get(g, 0) + 1
    except Exception:
        pass
    weekly_years = sorted(gender_by_year.keys())[-12:]
    result = {
        "gender_stats": gender_stats,
        "gender_by_degree": gender_by_degree,
        "gender_by_year": {y: gender_by_year[y] for y in weekly_years},
    }
    cache.set("gender_stats_v1", result, timeout=1800)
    return result


@app.route("/stats")
def stats_page():
    g = compute_gender_stats()
    return render_template("stats.html",
                           gender_stats=g["gender_stats"],
                           gender_by_degree=g["gender_by_degree"],
                           gender_by_year=g["gender_by_year"])


def _trends_data():
    """One-pass aggregation of all trend metrics over the dissertations table. Cached 30 min."""
    cached = cache.get("trends_data_v1")
    if cached is not None:
        return cached
    import re
    from data import get_connection, split_ixtisoslik
    full = re.compile(r'^(\d{2})\.(\d{2})\.(\d{4})$')
    anyyear = re.compile(r'(19|20)\d{2}')

    yearly = {}        # year -> {cnt, phd, dsc}
    spec_year = {}     # code -> {year: cnt}
    spec_total = {}    # code -> cnt
    uni_year = {}      # uni -> {year: cnt}
    uni_total = {}
    monthly = {}       # 'YYYY-MM' -> cnt
    gender_year = {}   # year -> {male, female}
    adv_year = {}      # advisor -> {year: cnt}
    adv_students = {}  # advisor -> set(students)

    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT daraja, sana, ixtisoslik, muassasa, ilmiy_rahbar, olim FROM dissertations")
                for daraja, sana, ixt, muas, rahbar, olim in cur.fetchall():
                    s = (sana or '').strip()
                    mon = None
                    m = full.match(s)
                    if m:
                        mon, yr = m.group(2), m.group(3)
                    else:
                        ym = anyyear.search(s)
                        yr = ym.group(0) if ym else None
                    if not yr:
                        continue
                    year = int(yr)
                    dl = (daraja or '')
                    yslot = yearly.setdefault(year, {"cnt": 0, "phd": 0, "dsc": 0})
                    yslot["cnt"] += 1
                    if 'PHD' in dl.upper() or 'фан' in dl.lower():
                        yslot["phd"] += 1
                    if 'DSC' in dl.upper() or 'док' in dl.lower():
                        yslot["dsc"] += 1
                    for code in split_ixtisoslik(ixt):
                        spec_total[code] = spec_total.get(code, 0) + 1
                        spec_year.setdefault(code, {})
                        spec_year[code][year] = spec_year[code].get(year, 0) + 1
                    u = (muas or '').strip()
                    if u:
                        uni_total[u] = uni_total.get(u, 0) + 1
                        uni_year.setdefault(u, {})
                        uni_year[u][year] = uni_year[u].get(year, 0) + 1
                    if mon:
                        key = f"{year:04d}-{mon}"
                        monthly[key] = monthly.get(key, 0) + 1
                    gd = detect_gender(olim)
                    if gd in ('male', 'female'):
                        gslot = gender_year.setdefault(year, {"male": 0, "female": 0})
                        gslot[gd] += 1
                    rb = (rahbar or '').strip()
                    if rb:
                        adv_year.setdefault(rb, {})
                        adv_year[rb][year] = adv_year[rb].get(year, 0) + 1
                        ol = (olim or '').strip()
                        if ol:
                            adv_students.setdefault(rb, set()).add(ol.lower())
        finally:
            conn.close()
    except Exception:
        pass

    years_all = sorted(yearly.keys())
    if not years_all:
        empty = {"yearly": [], "key_stats": {}, "spec_trend": {"years": [], "codes": [], "data": {}},
                 "growing": [], "declining": [], "uni_trend": {"years": [], "unis": [], "data": {}},
                 "monthly": [], "gender": [], "advisors": {"years": [], "list": []}}
        cache.set("trends_data_v1", empty, 1800)
        return empty

    max_year = years_all[-1]
    last5 = list(range(max_year - 4, max_year + 1))

    yearly_list = [{"year": y, "cnt": yearly[y]["cnt"], "phd": yearly[y]["phd"], "dsc": yearly[y]["dsc"]}
                   for y in years_all]
    peak = max(years_all, key=lambda y: yearly[y]["cnt"])
    low = min(years_all, key=lambda y: yearly[y]["cnt"])
    avg = round(sum(yearly[y]["cnt"] for y in years_all) / len(years_all))
    key_stats = {"peak_year": peak, "peak_cnt": yearly[peak]["cnt"],
                 "low_year": low, "low_cnt": yearly[low]["cnt"], "avg": avg}

    top15 = [c for c, _ in sorted(spec_total.items(), key=lambda kv: -kv[1])[:15]]
    spec_trend = {"years": last5, "codes": top15,
                  "data": {c: [spec_year.get(c, {}).get(y, 0) for y in last5] for c in top15}}

    y_this, y_prev = max_year, max_year - 1
    growth = []
    for code, _ in spec_total.items():
        prev = spec_year.get(code, {}).get(y_prev, 0)
        cur_ = spec_year.get(code, {}).get(y_this, 0)
        if prev >= 3:
            growth.append({"code": code, "pct": round((cur_ - prev) / prev * 100),
                           "this": cur_, "prev": prev})
    growing = sorted(growth, key=lambda x: -x["pct"])[:10]
    declining = sorted([g for g in growth if g["pct"] < 0], key=lambda x: x["pct"])[:10]

    uni_years = years_all[-8:]
    top_unis = [u for u, _ in sorted(uni_total.items(), key=lambda kv: -kv[1])[:10]]
    uni_trend = {"years": uni_years, "unis": top_unis,
                 "data": {u: [uni_year.get(u, {}).get(y, 0) for y in uni_years] for u in top_unis}}

    months_sorted = sorted(monthly.keys())[-24:]
    monthly_list = [{"key": k, "cnt": monthly[k]} for k in months_sorted]

    gender_list = [{"year": y, "male": gender_year.get(y, {}).get("male", 0),
                    "female": gender_year.get(y, {}).get("female", 0)} for y in years_all]

    adv_top = sorted(adv_students.items(), key=lambda kv: -len(kv[1]))[:10]
    advisors = {"years": last5, "list": [
        {"name": name, "total": len(studs),
         "spark": [adv_year.get(name, {}).get(y, 0) for y in last5]}
        for name, studs in adv_top]}

    result = {"yearly": yearly_list, "key_stats": key_stats, "spec_trend": spec_trend,
              "growing": growing, "declining": declining, "uni_trend": uni_trend,
              "monthly": monthly_list, "gender": gender_list, "advisors": advisors}
    cache.set("trends_data_v1", result, 1800)
    return result


@app.route("/trends")
def trends():
    return render_template("trends.html", t=_trends_data())


# ── Collaboration graph (advisor / student / opponent network) ─────────────
def _collab_index():
    """Build the collaboration adjacency index once. Cached 30 min (in-memory)."""
    cached = cache.get("collab_index_v1")
    if cached is not None:
        return cached
    from data import get_connection
    from collections import Counter, defaultdict
    adj = defaultdict(set)            # name -> set of connected names (advisor + opponent)
    adv_w = Counter()                 # (advisor, student) -> count
    opp_w = Counter()                 # (opponent, defender) -> count
    students_by_advisor = defaultdict(set)
    advisor_of = defaultdict(set)     # student -> set advisors
    opp_count = Counter()             # name -> times they were an opponent
    deg_map = {}                      # name -> 'PhD'/'DSc'
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT olim, daraja, ilmiy_rahbar, opponent_1, opponent_2, opponent_3 "
                    "FROM dissertations")
                def _ok(n):
                    n = (n or '').strip()
                    if len(n) < 4:
                        return ''
                    low = n.lower()
                    if 'ф.и.ш' in low or 'f.i.sh' in low or low in ('номаълум', "noma'lum"):
                        return ''
                    return n
                for olim, daraja, rahbar, o1, o2, o3 in cur.fetchall():
                    o = _ok(olim)
                    if o and daraja:
                        up = daraja.upper()
                        cur_d = deg_map.get(o)
                        if 'DSC' in up or 'док' in (daraja or '').lower():
                            deg_map[o] = 'DSc'
                        elif ('PHD' in up or 'фан' in (daraja or '').lower()) and cur_d != 'DSc':
                            deg_map[o] = 'PhD'
                    r = _ok(rahbar)
                    if r and o:
                        adv_w[(r, o)] += 1
                        students_by_advisor[r].add(o)
                        advisor_of[o].add(r)
                        adj[r].add(o); adj[o].add(r)
                    for opp in (o1, o2, o3):
                        opp = _ok(opp)
                        if opp and o and opp != o:
                            opp_w[(opp, o)] += 1
                            opp_count[opp] += 1
                            adj[opp].add(o); adj[o].add(opp)
        finally:
            conn.close()
    except Exception:
        pass
    idx = {
        "adj": adj, "adv_w": adv_w, "opp_w": opp_w,
        "students_by_advisor": students_by_advisor, "advisor_of": advisor_of,
        "opp_count": opp_count, "deg_map": deg_map,
        "lower_map": {n.lower(): n for n in adj.keys()},
        "by_conn": sorted(adj.keys(), key=lambda n: -len(adj[n])),
    }
    cache.set("collab_index_v1", idx, 1800)
    return idx


def _collab_role(idx, name):
    students = len(idx["students_by_advisor"].get(name, ()))
    opponents = int(idx["opp_count"].get(name, 0))
    advisors = len(idx["advisor_of"].get(name, ()))
    if students >= 3 and students >= opponents:
        role = "advisor"
    elif opponents > 0 and opponents >= students and opponents >= advisors:
        role = "opponent"
    elif students > 0:
        role = "mixed"
    else:
        role = "student"
    return role, students, opponents, advisors


def _collab_nodes_edges(idx, node_set, center=None, with_siblings=False, sibling_for=None):
    adj = idx["adj"]
    group_map = {"advisor": 1, "mixed": 2, "student": 3, "opponent": 4}
    nodes = []
    for n in node_set:
        role, students, opponents, advisors = _collab_role(idx, n)
        nodes.append({
            "id": n, "connections": len(adj.get(n, ())),
            "degree": idx["deg_map"].get(n), "role": role, "group": group_map[role],
            "students": students, "opponents": opponents, "advisors": advisors,
            "center": (n == center),
        })
    edges = []
    seen = set()
    for (a, b), w in idx["adv_w"].items():
        if a in node_set and b in node_set:
            edges.append({"source": a, "target": b, "type": "advisor", "weight": int(w)})
            seen.add((a, b))
    for (a, b), w in idx["opp_w"].items():
        if a in node_set and b in node_set and (a, b) not in seen:
            edges.append({"source": a, "target": b, "type": "opponent", "weight": int(w)})
    if with_siblings and sibling_for:
        sibs = sibling_for & node_set
        for s in sibs:
            edges.append({"source": center, "target": s, "type": "sibling", "weight": 1})
    return nodes, edges


def _collab_search(name, max_nodes=120):
    idx = _collab_index()
    center = idx["lower_map"].get((name or "").strip().lower())
    if not center:
        return {"nodes": [], "edges": [], "stats": {"total_nodes": 0, "total_edges": 0, "most_connected": ""}}
    adj = idx["adj"]
    S = {center}
    neigh = sorted(adj.get(center, ()), key=lambda n: -len(adj[n]))[:60]
    S.update(neigh)
    # academic siblings (other students of the center's advisors)
    sibs = set()
    for a in idx["advisor_of"].get(center, ()):
        sibs |= idx["students_by_advisor"].get(a, set())
    sibs.discard(center)
    for s in list(sibs)[:30]:
        if len(S) >= max_nodes:
            break
        S.add(s)
    # level 2
    for n in neigh[:20]:
        if len(S) >= max_nodes:
            break
        for m in sorted(adj.get(n, ()), key=lambda x: -len(adj[x]))[:6]:
            if len(S) >= max_nodes:
                break
            S.add(m)
    nodes, edges = _collab_nodes_edges(idx, S, center=center, with_siblings=True, sibling_for=sibs)
    most = max(nodes, key=lambda x: x["connections"])["id"] if nodes else ""
    return {"nodes": nodes, "edges": edges, "center": center,
            "stats": {"total_nodes": len(nodes), "total_edges": len(edges), "most_connected": most}}


def _collab_full(node_limit=150):
    """Edge-driven selection — walk the strongest connections so the map is dense, not a cloud of disconnected hubs."""
    idx = _collab_index()
    ranked = [(-w, a, b) for (a, b), w in idx["adv_w"].items()]
    ranked += [(-w, a, b) for (a, b), w in idx["opp_w"].items()]
    ranked.sort()
    S = set()
    for _negw, a, b in ranked:
        new = {a, b} - S
        if not new:
            continue
        if len(S) + len(new) > node_limit:
            if len(S) >= node_limit:
                break
            continue
        S |= new
    nodes, edges = _collab_nodes_edges(idx, S)
    most = max(nodes, key=lambda x: x["connections"])["id"] if nodes else ""
    return {"nodes": nodes, "edges": edges,
            "stats": {"total_nodes": len(nodes), "total_edges": len(edges), "most_connected": most}}


@app.route("/collaboration")
def collaboration():
    return render_template("collaboration.html")


@app.route("/api/collaboration")
def api_collaboration():
    name = (request.args.get("name") or "").strip()
    mode = (request.args.get("mode") or "").strip()
    try:
        if mode == "full":
            return jsonify(_collab_full())
        if name:
            return jsonify(_collab_search(name))
    except Exception as e:
        return jsonify({"nodes": [], "edges": [], "stats": {}, "error": str(e)})
    return jsonify({"nodes": [], "edges": [], "stats": {"total_nodes": 0, "total_edges": 0, "most_connected": ""}})


@app.route("/api/collaboration/search")
def api_collaboration_search():
    q = (request.args.get("q") or "").strip().lower()
    if len(q) < 2:
        return jsonify({"results": []})
    idx = _collab_index()
    adj = idx["adj"]
    out = []
    for n in idx["by_conn"]:
        if q in n.lower():
            out.append({"name": n, "connections": len(adj[n])})
            if len(out) >= 15:
                break
    return jsonify({"results": out})


# ── Topic clustering (keyword-based grouping of dissertation topics) ────────
_IXT_GROUPS = {  # ixtisoslik prefix -> (group key, color)
    "13": ("education", "#059669"), "05": ("technical", "#3b82f6"),
    "14": ("medical", "#ef4444"), "08": ("economics", "#f59e0b"),
    "12": ("law", "#8b5cf6"),
}


def _ixt_group(code):
    pre = (code or "")[:2]
    return _IXT_GROUPS.get(pre, ("other", "#64748b"))


def _cluster_trend(year_counts):
    yrs = sorted(year_counts.keys())
    if len(yrs) < 4:
        return "stable"
    recent = sum(year_counts[y] for y in yrs[-2:])
    prev = sum(year_counts[y] for y in yrs[-4:-2]) or 0
    if recent > prev * 1.2:
        return "growing"
    if recent < prev * 0.8:
        return "declining"
    return "stable"


def _clustering_build():
    """Keyword-based clustering of dissertation topics. Cached 1 hour."""
    cached = cache.get("clustering_v1")
    if cached is not None:
        return cached
    import re
    from collections import Counter, defaultdict
    from itertools import combinations
    from data import get_connection

    yre = re.compile(r'(19|20)\d{2}')
    docs = []
    kw_docs = defaultdict(set)
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, mavzu, ixtisoslik, daraja, olim, sana, ilmiy_rahbar "
                    "FROM dissertations WHERE mavzu IS NOT NULL AND TRIM(mavzu) <> ''")
                for did, mavzu, ixt, daraja, olim, sana, rahbar in cur.fetchall():
                    kws = set(_extract_keywords(mavzu or ""))
                    ym = yre.search(sana or "")
                    docs.append({
                        "id": did, "mavzu": (mavzu or "").strip(), "ixtisoslik": (ixt or "").strip(),
                        "daraja": (daraja or "").strip(), "olim": (olim or "").strip(),
                        "sana": (sana or "").strip(), "rahbar": (rahbar or "").strip(),
                        "year": int(ym.group(0)) if ym else None, "kws": kws,
                    })
                    i = len(docs) - 1
                    for kw in kws:
                        kw_docs[kw].add(i)
        finally:
            conn.close()
    except Exception:
        pass

    if not docs:
        empty = {"clusters": [], "total_clusters": 0, "clustered": 0, "unclustered": 0, "total": 0}
        cache.set("clustering_v1", empty, 3600)
        return empty

    sig = {kw for kw, s in kw_docs.items() if len(s) >= 15}
    pair_co = Counter()
    for d in docs:
        sk = sorted(kw for kw in d["kws"] if kw in sig)
        for a, b in combinations(sk, 2):
            pair_co[(a, b)] += 1

    assigned = [False] * len(docs)
    clusters = []
    for (k1, k2), _co in pair_co.most_common(200):
        if len(clusters) >= 30:
            break
        members = [i for i in (kw_docs[k1] & kw_docs[k2]) if not assigned[i]]
        if len(members) < 8:
            continue
        for i in members:
            assigned[i] = True
        members.sort(key=lambda i: -(docs[i]["year"] or 0))
        kc = Counter()
        adv = Counter()
        grp = Counter()
        yc = Counter()
        phd = dsc = 0
        for i in members:
            d = docs[i]
            for kw in d["kws"]:
                if kw in sig:
                    kc[kw] += 1
            if d["rahbar"]:
                adv[d["rahbar"]] += 1
            for code in (d["ixtisoslik"] or "").replace(",", " ").split():
                grp[_ixt_group(code)[0]] += 1
            if d["year"]:
                yc[d["year"]] += 1
            up = d["daraja"].upper()
            if "DSC" in up:
                dsc += 1
            elif "PHD" in up:
                phd += 1
        top_kw = [k for k, _ in kc.most_common(4)]
        title = " · ".join(w.capitalize() for w in top_kw[:3]) or "Klaster"
        group = grp.most_common(1)[0][0] if grp else "other"
        color = next((c for g, c in _IXT_GROUPS.values() if g == group), "#64748b")
        years = sorted(y for y in yc)
        yr_range = f"{years[0]}-{years[-1]}" if years else "—"
        full_members = [{
            "id": docs[i]["id"], "mavzu": docs[i]["mavzu"], "olim": docs[i]["olim"],
            "daraja": docs[i]["daraja"], "sana": docs[i]["sana"],
            "ixtisoslik": docs[i]["ixtisoslik"], "rahbar": docs[i]["rahbar"],
        } for i in members]
        clusters.append({
            "id": len(clusters) + 1, "title": title, "keywords": top_kw,
            "count": len(members), "dissertations": full_members[:50],
            "_members": full_members, "top_advisors": [a for a, _ in adv.most_common(2)],
            "year_range": yr_range, "trend": _cluster_trend(yc),
            "group": group, "color": color, "phd": phd, "dsc": dsc,
            "year_counts": {str(y): yc[y] for y in years},
        })

    clusters.sort(key=lambda c: -c["count"])
    for n, c in enumerate(clusters, 1):
        c["id"] = n
    clustered = sum(1 for a in assigned if a)
    result = {
        "clusters": clusters, "total_clusters": len(clusters),
        "clustered": clustered, "unclustered": len(docs) - clustered, "total": len(docs),
    }
    cache.set("clustering_v1", result, 3600)
    return result


def _clustering_public(data):
    """Strip heavy `_members` for the page/API payload."""
    out = {k: v for k, v in data.items() if k != "clusters"}
    out["clusters"] = [{k: v for k, v in c.items() if k != "_members"} for c in data["clusters"]]
    return out


@app.route("/clustering")
def topic_clustering():
    data = _clustering_build()
    biggest = data["clusters"][0] if data["clusters"] else None
    return render_template("clustering.html", summary={
        "total_clusters": data["total_clusters"], "clustered": data["clustered"],
        "unclustered": data["unclustered"], "total": data["total"],
        "biggest_title": biggest["title"] if biggest else "—",
        "biggest_count": biggest["count"] if biggest else 0,
    })


@app.route("/api/clustering")
def api_clustering():
    try:
        return jsonify(_clustering_public(_clustering_build()))
    except Exception as e:
        return jsonify({"clusters": [], "total_clusters": 0, "clustered": 0,
                        "unclustered": 0, "total": 0, "error": str(e)})


@app.route("/clustering/<int:cluster_id>")
def cluster_detail(cluster_id):
    data = _clustering_build()
    cluster = next((c for c in data["clusters"] if c["id"] == cluster_id), None)
    if not cluster:
        abort(404)
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1
    per_page = 25
    members = cluster.get("_members", [])
    total = len(members)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    rows = members[(page - 1) * per_page: page * per_page]
    kwset = set(cluster["keywords"])
    related = sorted(
        ((len(kwset & set(c["keywords"])), c) for c in data["clusters"] if c["id"] != cluster_id),
        key=lambda t: -t[0])
    related = [c for n, c in related if n > 0][:3]
    related = [{"id": c["id"], "title": c["title"], "count": c["count"], "keywords": c["keywords"]} for c in related]
    return render_template("cluster_detail.html",
                           cluster={k: v for k, v in cluster.items() if k != "_members"},
                           rows=rows, page=page, total_pages=total_pages, total=total,
                           related=related)


def _compare_university(cur, name):
    """Aggregate comparison data for one university."""
    name = (name or "").strip()
    if not name:
        return None
    cur.execute(
        """SELECT
               COUNT(*),
               COUNT(*) FILTER (WHERE UPPER(TRIM(daraja)) = 'PHD'),
               COUNT(*) FILTER (WHERE UPPER(TRIM(daraja)) = 'DSC')
           FROM dissertations WHERE TRIM(muassasa) = TRIM(%s)""",
        (name,)
    )
    total, phd, dsc = cur.fetchone()
    if not total:
        return {"name": name, "total": 0, "phd": 0, "dsc": 0,
                "top_supervisors": [], "top_ixtisosliklar": [], "years": []}
    cur.execute(
        """SELECT TRIM(ilmiy_rahbar), COUNT(*) AS cnt
           FROM dissertations
           WHERE TRIM(muassasa) = TRIM(%s) AND ilmiy_rahbar IS NOT NULL AND TRIM(ilmiy_rahbar) <> ''
           GROUP BY TRIM(ilmiy_rahbar) ORDER BY cnt DESC LIMIT 5""",
        (name,)
    )
    top_supervisors = [{"name": r[0], "count": r[1]} for r in cur.fetchall()]
    cur.execute(
        """SELECT TRIM(ixtisoslik), COUNT(*) AS cnt
           FROM dissertations
           WHERE TRIM(muassasa) = TRIM(%s) AND ixtisoslik IS NOT NULL AND TRIM(ixtisoslik) <> ''
           GROUP BY TRIM(ixtisoslik) ORDER BY cnt DESC LIMIT 5""",
        (name,)
    )
    top_ixtisosliklar = [{"code": r[0], "count": r[1]} for r in cur.fetchall()]
    cur.execute(
        r"""SELECT substring(TRIM(sana) from '\d{4}') AS yr, COUNT(*) AS cnt
            FROM dissertations
            WHERE TRIM(muassasa) = TRIM(%s) AND sana ~ '\d{4}'
            GROUP BY yr ORDER BY yr""",
        (name,)
    )
    years = [{"year": r[0], "count": r[1]} for r in cur.fetchall() if r[0]]
    return {"name": name, "total": total or 0, "phd": phd or 0, "dsc": dsc or 0,
            "top_supervisors": top_supervisors, "top_ixtisosliklar": top_ixtisosliklar,
            "years": years}


# ── Topic similarity analyzer (replaces the old university comparison) ──
STOP_WORDS = {
    # Uzbek (latin)
    'va', 'uchun', 'ning', 'da', 'ni', 'ga', 'dan', 'bilan', "bo'lgan", 'ham', 'bu',
    'shu', 'bir', 'har', "o'z", 'esa', 'yoki', 'lekin', 'chunki', 'haqida', 'orqali',
    'asosida', "bo'yicha", "to'g'risida", 'rivojlantirish', 'takomillashtirish',
    'shakllantirish', 'metodikasi', 'metodlari', 'asoslari', 'tahlili', 'masalalari',
    # Cyrillic
    'ва', 'учун', 'нинг', 'билан', 'ҳам', 'бу', 'шу', 'бир', 'ҳар', 'ёки', 'лекин',
    'орқали', 'асосида', 'бўйича', 'ривожлантириш', 'такомиллаштириш', 'шакллантириш',
}


def _extract_keywords(text, limit=10):
    import re
    words = re.findall(r"[\w'’ʻ]+", (text or '').lower(), flags=re.UNICODE)
    seen, out = set(), []
    for w in words:
        w = w.strip("'’ʻ")
        if len(w) < 4 or w in STOP_WORDS or w in seen:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= limit:
            break
    return out


@app.route("/compare")
@app.route("/mavzu-tahlili")
def compare():
    from flask import session
    is_logged_in = ('user_id' in session or 'cabinet_user_id' in session
                    or current_user.is_authenticated)
    return render_template("compare.html", ixtisosliklar=_compare_ixtisosliklar(),
                           is_logged_in=is_logged_in)


def _compare_ixtisosliklar():
    from data import list_individual_ixtisosliklar
    try:
        return list_individual_ixtisosliklar()
    except Exception:
        return []


@app.route("/api/mavzu-tahlili", methods=["POST"])
@csrf.exempt
def api_mavzu_tahlili():
    from data import get_connection, latin_to_cyrillic, clean_olim_name
    body = request.get_json(silent=True) or {}
    mavzu = (body.get("mavzu") or "").strip()
    ixtisoslik = (body.get("ixtisoslik") or "").strip()
    keywords = body.get("keywords") or _extract_keywords(mavzu)
    keywords = [k.strip().lower() for k in keywords if k and len(k.strip()) >= 3][:10]
    if not keywords:
        return jsonify({"results": [], "total": 0, "keywords_used": []})

    # build keyword variants (latin + cyrillic transliteration)
    variants = []
    for k in keywords:
        variants.append(k)
        try:
            cyr = latin_to_cyrillic(k).lower()
            if cyr and cyr != k:
                variants.append(cyr)
        except Exception:
            pass
    score_terms = " + ".join(["CASE WHEN LOWER(mavzu) LIKE %s THEN 1 ELSE 0 END"] * len(variants))
    where_terms = " OR ".join(["LOWER(mavzu) LIKE %s"] * len(variants))
    like_params = [f"%{v}%" for v in variants]
    sql = (
        f"SELECT id, olim, mavzu, daraja, sana, ixtisoslik, ixtisoslik_nomi, ilmiy_rahbar, "
        f"({score_terms}) AS match_score FROM dissertations WHERE ({where_terms})"
    )
    params = list(like_params) + list(like_params)
    if ixtisoslik:
        sql += " AND ixtisoslik ILIKE %s"
        params.append(f"%{ixtisoslik}%")
    sql += " ORDER BY match_score DESC, id DESC LIMIT 50"
    results = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                for r in cur.fetchall():
                    results.append({
                        "id": r[0], "olim": r[1] or "", "olim_short": clean_olim_name(r[1] or ""),
                        "mavzu": r[2] or "", "daraja": (r[3] or "").upper(), "sana": r[4] or "",
                        "ixtisoslik": r[5] or "", "ixtisoslik_nomi": r[6] or "",
                        "ilmiy_rahbar": r[7] or "", "match_score": r[8] or 0,
                    })
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"results": [], "total": 0, "keywords_used": keywords, "error": str(e)})
    return jsonify({"results": results, "total": len(results), "keywords_used": keywords})


@app.route("/api/mavzu-tahlili/ai", methods=["POST"])
@csrf.exempt
def api_mavzu_tahlili_ai():
    from data import GROQ_API_KEY
    body = request.get_json(silent=True) or {}
    mavzu = (body.get("mavzu") or "").strip()
    similar = body.get("similar_topics") or []
    if not mavzu:
        return jsonify({"error": "Mavzu kiritilmagan"}), 200
    if not GROQ_API_KEY:
        return jsonify({
            "uniqueness_score": 5,
            "analysis": "AI tahlil hozircha mavjud emas (Groq API kaliti sozlanmagan). "
                        "Quyidagi o'xshash mavzular ro'yxatini ko'rib chiqing.",
            "suggestions": [], "angles": [],
        })
    similar_list = "\n".join(f"- {t}" for t in similar[:10])
    user_prompt = (
        f"Taklif qilinayotgan mavzu: {mavzu}\n\n"
        f"Topilgan o'xshash mavjud mavzular:\n{similar_list}\n\n"
        "Tahlil qil: 1) Bu mavzu qanchalik noyob? (1-10 ball), "
        "2) Qaysi mavjud mavzular eng o'xshash va nega? "
        "3) Mavzuni yanada noyob qilish uchun 3 ta taklif. "
        "4) O'rganilmagan yondashuvlarni taklif et."
    )
    try:
        from groq import Groq
        import json as _json
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": (
                    "You are a dissertation topic analyzer for Uzbekistan. Given a proposed topic, "
                    "analyze it and suggest improvements. Respond in Uzbek. Respond ONLY with a JSON "
                    "object: {\"uniqueness_score\": <1-10 int>, \"analysis\": \"<text>\", "
                    "\"suggestions\": [\"...\"], \"angles\": [\"...\"]}.")},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=900,
            response_format={"type": "json_object"},
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = _json.loads(raw)
        try:
            data["uniqueness_score"] = int(data.get("uniqueness_score", 5))
        except (TypeError, ValueError):
            data["uniqueness_score"] = 5
        data.setdefault("analysis", "")
        data.setdefault("suggestions", [])
        data.setdefault("angles", [])
        return jsonify(data)
    except Exception as e:
        return jsonify({"uniqueness_score": 5, "analysis": f"AI tahlilda xatolik: {e}",
                        "suggestions": [], "angles": []}), 200


@app.route("/api/notifications/count")
@login_required
def notifications_count():
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM notifications WHERE is_read = FALSE")
                count = cur.fetchone()[0]
        finally:
            conn.close()
    except Exception:
        count = 0
    return jsonify({'count': count})


@app.route("/notifications")
@login_required
def notifications_page():
    from data import get_connection
    notifs = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, message, count, created_at FROM notifications "
                    "ORDER BY created_at DESC LIMIT 50"
                )
                notifs = [
                    {'id': r[0], 'message': r[1], 'count': r[2], 'created_at': str(r[3])}
                    for r in cur.fetchall()
                ]
                cur.execute("UPDATE notifications SET is_read = TRUE")
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass
    return render_template("notifications.html", notifications=notifs)


@app.route("/profile")
@login_required
def profile():
    return redirect('/cabinet')


@app.before_request
def check_blocked():
    # Admin pages and static files are never blocked; admin user always passes.
    if request.path.startswith('/admin') or request.path.startswith('/static'):
        return None
    try:
        if (hasattr(current_user, 'is_authenticated') and current_user.is_authenticated
                and getattr(current_user, 'username', None) == 'admin'):
            return None
    except Exception:
        pass
    ip = get_real_ip()
    if not ip:
        return None
    try:
        from data import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                # Most recent active block row for this IP (if any).
                cur.execute("""
                    SELECT id, reason, blocked_until, is_permanent,
                           (is_permanent OR blocked_until > NOW()) AS still_active
                    FROM blocked_users
                    WHERE ip_address = %s AND is_active = TRUE
                    ORDER BY created_at DESC LIMIT 1
                """, (ip,))
                row = cur.fetchone()
                if row and not row[4]:
                    # Block has expired naturally — deactivate for history, then allow.
                    cur.execute(
                        "UPDATE blocked_users SET is_active = FALSE WHERE id = %s", (row[0],))
                    conn.commit()
                    row = None
        finally:
            conn.close()
    except Exception:
        return None
    if row:
        return render_template('errors/blocked.html', reason=row[1], until=row[2]), 403
    return None


def get_visitor_info():
    """Detect the logged-in user from any supported auth method.

    Returns (user_id, username), or (None, None) for true guests. Covers:
      1. Flask-Login (main site / admin login)
      2. Session-based admin keys (defensive fallback)
      3. Cabinet login (cabinet_user_id)
      4. Telegram data stored in session (defensive fallback)
    """
    from flask import session

    # Method 1: Flask-Login (main site — admin login)
    try:
        if hasattr(current_user, 'is_authenticated') and current_user.is_authenticated:
            user_id = getattr(current_user, 'id', None)
            username = (getattr(current_user, 'username', None)
                        or getattr(current_user, 'email', None))
            if username:
                return user_id, username
    except Exception:
        pass

    # Method 2: Session-based admin keys (in case a future flow sets them directly)
    try:
        if session.get('user_id'):
            user_id = session['user_id']
            username = session.get('username') or None
            if username:
                return user_id, username
            try:
                from data import get_connection
                conn = get_connection()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT username, email FROM users WHERE id = %s", (user_id,))
                        u = cur.fetchone()
                finally:
                    conn.close()
                if u:
                    return user_id, (u[0] or u[1] or f'user_{user_id}')
            except Exception:
                return user_id, f'admin_{user_id}'
    except Exception:
        pass

    # Method 3: Cabinet login
    try:
        cab_id = session.get('cabinet_user_id')
        if cab_id:
            username = session.get('cabinet_olim_name') or None
            if not username:
                try:
                    from data import get_connection
                    conn = get_connection()
                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                "SELECT olim_name, telegram_username, telegram_first_name, email "
                                "FROM cabinet_users WHERE id = %s", (cab_id,))
                            cab = cur.fetchone()
                    finally:
                        conn.close()
                    if cab:
                        username = cab[0] or cab[1] or cab[2] or cab[3]
                except Exception:
                    username = None
            return cab_id, (username or f'cabinet_{cab_id}')
    except Exception:
        pass

    # Method 4: Telegram data stored in session (defensive fallback)
    try:
        tg_user = session.get('telegram_user')
        if tg_user:
            username = tg_user.get('first_name', '') or tg_user.get('username', '')
            user_id = tg_user.get('id')
            if username:
                return user_id, username
    except Exception:
        pass

    return None, None


@app.before_request
def track_visit():
    if request.endpoint and not request.path.startswith('/static') and not request.path.startswith('/api/'):
        try:
            from data import get_connection
            from flask import session
            conn = get_connection()
            cur = conn.cursor()
            user_id, username = get_visitor_info()
            # Guests (no account) are logged as "Mehmon" so analytics can
            # distinguish registered users from anonymous visitors.
            if not username:
                username = "Mehmon"
            # Stable per-session id so visits can be grouped into sessions.
            sid = session.get('visit_sid')
            if not sid:
                import uuid as _uuid
                sid = _uuid.uuid4().hex
                session['visit_sid'] = sid
            cur.execute("""
                INSERT INTO page_visits
                    (user_id, username, page, ip_address, user_agent, referrer, session_id, country)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (user_id, username, request.path, get_real_ip(),
                  request.headers.get('User-Agent', '')[:500],
                  request.headers.get('Referer', '')[:500], sid, get_country()))
            conn.commit()
            cur.close()
            conn.close()
        except Exception:
            pass  # never break the app if tracking fails


@app.before_request
def api_protection():
    # Protect data API endpoints from scraping
    protected_paths = ['/data', '/api/']

    if any(request.path.startswith(p) for p in protected_paths):
        # Rate limit: max 60 requests per minute per IP
        ip = get_real_ip()
        cache_key = f'rate_limit:{ip}'
        current = cache.get(cache_key) or 0

        if current > 60:
            return jsonify({'error': 'Rate limit exceeded. Iltimos, biroz kuting.'}), 429

        cache.set(cache_key, current + 1, timeout=60)

        # Block obvious scraping (automated user agents)
        ua = request.headers.get('User-Agent', '').lower()
        scrapy_agents = ['scrapy', 'wget', 'curl', 'python-requests', 'httpclient', 'bot', 'spider', 'crawl']

        if any(agent in ua for agent in scrapy_agents):
            if not request.path.startswith('/api/oak/'):  # Allow our own scraper
                return jsonify({'error': 'Automated access blocked'}), 403


@app.after_request
def add_cache_headers(response):
    # Long cache for static assets
    if request.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'public, max-age=86400'  # 1 day
        return response
    # Never cache user-specific or admin pages, or POST requests
    if (request.method == 'POST'
            or request.path.startswith('/cabinet')
            or request.path.startswith('/admin')
            or request.path.startswith('/api/oak/')):
        response.headers['Cache-Control'] = 'no-store'
        return response
    # Short private client cache for the data table API
    if request.path.startswith('/data'):
        response.headers['Cache-Control'] = 'private, max-age=60'  # 1 min client cache
    return response


@app.route('/offline')
def offline():
    return render_template('offline.html')


@app.route('/sw.js')
def service_worker():
    # Serve the service worker from root so its scope can be "/"
    return app.send_static_file('sw.js'), 200, {
        'Content-Type': 'application/javascript',
        'Service-Worker-Allowed': '/'
    }


@app.errorhandler(404)
def page_not_found(e):
    return render_template('errors/404.html'), 404


@app.errorhandler(500)
def server_error(e):
    return render_template('errors/500.html'), 500


@app.errorhandler(403)
def forbidden(e):
    return render_template('errors/403.html'), 403


@app.errorhandler(429)
def rate_limited(e):
    return render_template('errors/429.html'), 429


@app.route('/api/online-count')
def online_count():
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(DISTINCT COALESCE(user_id::text, ip_address))
                    FROM page_visits
                    WHERE visited_at > NOW() - INTERVAL '5 minutes'
                """)
                count = cur.fetchone()[0]
        finally:
            conn.close()
    except Exception:
        count = 0
    return jsonify({'online': count})


@app.route('/api/user-count')
def user_count():
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM users")
                count = cur.fetchone()[0]
        finally:
            conn.close()
    except Exception:
        count = 0
    return jsonify({'count': count})


# ── Academic genealogy (ilmiy shajara) ─────────────────────────────────────
def _gen_degree(darajalar):
    up = [str(d or '').upper() for d in darajalar]
    if any('DSC' in d for d in up):
        return 'DSc'
    if any('PHD' in d for d in up):
        return 'PhD'
    return None


def _genealogy_data(name, depth=2, child_cap=40, sibling_cap=30):
    """Build the genealogy tree (parents↑, children↓, siblings↔) for a researcher."""
    from data import get_connection, get_supervisor_counts
    name = (name or '').strip()
    res = {
        "center": {"name": name, "degree": None, "dissertation_count": 0},
        "parents": [], "children": [], "siblings": [],
    }
    if not name:
        return res
    try:
        sup_counts = get_supervisor_counts()
    except Exception:
        sup_counts = {}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            def degree_of(person):
                cur.execute("SELECT daraja FROM dissertations WHERE LOWER(TRIM(olim)) = LOWER(TRIM(%s))", (person,))
                return _gen_degree([r[0] for r in cur.fetchall()])

            # center
            cur.execute("SELECT daraja FROM dissertations WHERE LOWER(TRIM(olim)) = LOWER(TRIM(%s))", (name,))
            crows = [r[0] for r in cur.fetchall()]
            res["center"]["dissertation_count"] = len(crows)
            res["center"]["degree"] = _gen_degree(crows)

            # parents (this person's advisors) + their advisors (grandparents)
            cur.execute(
                "SELECT DISTINCT TRIM(ilmiy_rahbar) FROM dissertations "
                "WHERE LOWER(TRIM(olim)) = LOWER(TRIM(%s)) "
                "AND ilmiy_rahbar IS NOT NULL AND TRIM(ilmiy_rahbar) <> ''", (name,))
            parent_names = [r[0] for r in cur.fetchall()]
            for pn in parent_names:
                grand = []
                if depth >= 2:
                    cur.execute(
                        "SELECT DISTINCT TRIM(ilmiy_rahbar) FROM dissertations "
                        "WHERE LOWER(TRIM(olim)) = LOWER(TRIM(%s)) "
                        "AND ilmiy_rahbar IS NOT NULL AND TRIM(ilmiy_rahbar) <> ''", (pn,))
                    gnames = [r[0] for r in cur.fetchall()]
                    for gpn in gnames:
                        grand.append({"name": gpn, "degree": degree_of(gpn)})
                res["parents"].append({"name": pn, "degree": degree_of(pn), "parents": grand})

            # children (students), with how many students each of them has
            cur.execute(
                "SELECT TRIM(olim), daraja FROM dissertations "
                "WHERE LOWER(TRIM(ilmiy_rahbar)) = LOWER(TRIM(%s)) "
                "AND olim IS NOT NULL AND TRIM(olim) <> ''", (name,))
            childmap = {}
            for o, d in cur.fetchall():
                childmap.setdefault(o, []).append(d)
            children = [{
                "name": cn, "degree": _gen_degree(drs),
                "children_count": int(sup_counts.get(cn, 0)),
            } for cn, drs in childmap.items()]
            children.sort(key=lambda x: (-x["children_count"], x["name"]))
            res["children"] = children[:child_cap]

            # siblings (other students of the same advisors)
            sib = {}
            for pn in parent_names:
                cur.execute(
                    "SELECT TRIM(olim), daraja FROM dissertations "
                    "WHERE LOWER(TRIM(ilmiy_rahbar)) = LOWER(TRIM(%s)) "
                    "AND LOWER(TRIM(olim)) <> LOWER(TRIM(%s)) "
                    "AND olim IS NOT NULL AND TRIM(olim) <> ''", (pn, name))
                for o, d in cur.fetchall():
                    sib.setdefault(o, []).append(d)
            res["siblings"] = [{"name": sn, "degree": _gen_degree(drs)}
                               for sn, drs in list(sib.items())[:sibling_cap]]
    finally:
        conn.close()
    return res


@app.route('/api/genealogy/<path:name>')
@cache.cached(timeout=900)
def api_genealogy(name):
    try:
        return jsonify(_genealogy_data(name, depth=2))
    except Exception as e:
        return jsonify({"center": {"name": name, "degree": None, "dissertation_count": 0},
                        "parents": [], "children": [], "siblings": [], "error": str(e)})


@app.route('/api/genealogy/expand/<path:name>')
def api_genealogy_expand(name):
    """Immediate parents + children only (1 level) for live tree expansion."""
    try:
        d = _genealogy_data(name, depth=1)
        return jsonify({
            "name": name,
            "parents": [{"name": p["name"], "degree": p["degree"]} for p in d["parents"]],
            "children": d["children"],
        })
    except Exception as e:
        return jsonify({"name": name, "parents": [], "children": [], "error": str(e)})


@app.route('/genealogy/<path:name>')
def genealogy_page(name):
    return render_template('genealogy.html', olim_name=name.strip())
















def _require_admin():
    # Authorize by the is_admin flag (not a hardcoded username) so every admin
    # account — admin, botir365, Botir_Bakhodirovich — has dashboard access.
    if not current_user.is_authenticated or not getattr(current_user, 'is_admin', False):
        abort(403)


@app.route('/admin/fix-names', methods=['GET', 'POST'])
@login_required
def admin_fix_names():
    """dissertations.olim ustunidagi 'o'g'li'/'qizi' variantlarini standartlaydi.

    Faqat admin uchun. DISTINCT olim ismlarini olib, har xil yozilishlarni
    (oʻgʻli, o`g`li, ogli, o'gli, ... / қизи, qizy) yagona formatga keltiradi.
    """
    _require_admin()
    import re
    from data import get_connection

    def _norm(name):
        if not name:
            return name
        # Barcha apostrof/tik variantlarini oddiy ' ga keltiramiz.
        s = re.sub(r"[ʻʼ’‘`´]", "'", name)
        # Kirill variantlari
        s = re.sub(r"қизи", "qizi", s, flags=re.IGNORECASE)
        s = re.sub(r"ў\s*ғ\s*ли", "o'g'li", s, flags=re.IGNORECASE)
        # Lotin variantlari: oʻgʻli / o'g'li / ogli / o'gli / o`g`li ...
        s = re.sub(r"\bo'?\s*g'?\s*li\b", "o'g'li", s, flags=re.IGNORECASE)
        s = re.sub(r"\bqiz[iy]\b", "qizi", s, flags=re.IGNORECASE)
        # Ortiqcha bo'shliqlarni tozalaymiz.
        s = re.sub(r"\s+", " ", s).strip()
        return s

    fixed_rows = 0
    fixed_names = 0
    samples = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT olim FROM dissertations "
                            "WHERE olim IS NOT NULL AND TRIM(olim) <> ''")
                names = [r[0] for r in cur.fetchall()]
                for old in names:
                    new = _norm(old)
                    if new and new != old:
                        cur.execute("UPDATE dissertations SET olim = %s WHERE olim = %s",
                                    (new, old))
                        fixed_rows += cur.rowcount
                        fixed_names += 1
                        if len(samples) < 30:
                            samples.append({"old": old, "new": new})
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True, "distinct_names_fixed": fixed_names,
                    "rows_updated": fixed_rows, "samples": samples})


def _save_news_image():
    """Save an uploaded news image to static/uploads/news/ and return its web path, or None."""
    f = request.files.get("image_file")
    if not f or not f.filename:
        return None
    from werkzeug.utils import secure_filename
    import time as _time
    fname = secure_filename(f.filename)
    if not fname:
        return None
    ext = os.path.splitext(fname)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        return None
    upload_dir = os.path.join(app.static_folder, "uploads", "news")
    os.makedirs(upload_dir, exist_ok=True)
    saved = f"{int(_time.time())}_{fname}"
    try:
        f.save(os.path.join(upload_dir, saved))
    except Exception:
        return None
    return f"/static/uploads/news/{saved}"


def _yangilik_form_values(existing_image=None):
    uploaded = _save_news_image()
    if request.form.get("remove_image"):
        image_url = None
    elif uploaded:
        image_url = uploaded
    else:
        url_in = (request.form.get("image_url_input")
                  or request.form.get("image_url") or "").strip()
        image_url = url_in or existing_image or None
    return {
        "title": request.form.get("title", "").strip(),
        "summary": request.form.get("summary", "").strip()[:500],
        "content": request.form.get("content", "").strip(),
        "image_url": image_url,
        "source_url": request.form.get("source_url", "").strip() or None,
        "is_published": bool(request.form.get("is_published")),
    }


def _delete_local_news_image(image_url):
    """Remove a locally-stored news image file from disk (ignore external URLs)."""
    if not image_url or not image_url.startswith("/static/uploads/"):
        return
    try:
        rel = image_url[len("/static/"):]  # e.g. uploads/news/123_x.jpg
        path = os.path.join(app.static_folder, rel)
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass








@app.route("/top-olimlar")
def top_olimlar():
    from data import get_connection, clean_olim_name
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1
    per_page = 50
    offset = (page - 1) * per_page
    items = []
    total = 0
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM (SELECT 1 FROM dissertations "
                    "WHERE ilmiy_rahbar IS NOT NULL AND TRIM(ilmiy_rahbar) != '' "
                    "GROUP BY TRIM(ilmiy_rahbar)) t"
                )
                total = cur.fetchone()[0] or 0
                cur.execute(
                    "SELECT TRIM(ilmiy_rahbar) AS rahbar, COUNT(*) AS cnt, "
                    "MAX(ilmiy_rahbar_photo_url) AS photo_url "
                    "FROM dissertations "
                    "WHERE ilmiy_rahbar IS NOT NULL AND TRIM(ilmiy_rahbar) != '' "
                    "GROUP BY TRIM(ilmiy_rahbar) ORDER BY cnt DESC, rahbar "
                    "LIMIT %s OFFSET %s",
                    (per_page, offset)
                )
                items = [{
                    "rank": offset + i + 1,
                    "name": r[0] or "",
                    "display": clean_olim_name(r[0] or ""),
                    "count": r[1] or 0,
                    "photo_url": r[2] or "",
                } for i, r in enumerate(cur.fetchall())]
        finally:
            conn.close()
    except Exception:
        items, total = [], 0

    total_pages = max(1, (total + per_page - 1) // per_page)
    return render_template("top_olimlar.html", items=items, page=page,
                           total_pages=total_pages, total=total)


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/team")
def team():
    return render_template("team.html")


VACANCY_TYPES = [
    ("full_time", "To'liq stavka"),
    ("part_time", "Yarim stavka"),
    ("project", "Loyiha"),
    ("internship", "Stajirovka"),
]
VACANCY_TYPE_LABELS = dict(VACANCY_TYPES)


def _vacancy_from_row(cols, row):
    v = dict(zip(cols, row))
    v["type_label"] = VACANCY_TYPE_LABELS.get(v.get("vacancy_type"), "")
    if v.get("deadline"):
        v["deadline"] = str(v["deadline"])[:10]
    return v


def _vacancy_form_values():
    vtype = request.form.get("vacancy_type", "full_time").strip()
    if vtype not in VACANCY_TYPE_LABELS:
        vtype = "full_time"
    return {
        "title": request.form.get("title", "").strip()[:500],
        "organization": request.form.get("organization", "").strip()[:500],
        "location": request.form.get("location", "").strip()[:300] or None,
        "specialty": request.form.get("specialty", "").strip()[:300] or None,
        "requirements": request.form.get("requirements", "").strip() or None,
        "description": request.form.get("description", "").strip() or None,
        "salary": request.form.get("salary", "").strip()[:200] or None,
        "contact_info": request.form.get("contact_info", "").strip()[:500] or None,
        "contact_url": request.form.get("contact_url", "").strip()[:500] or None,
        "vacancy_type": vtype,
        "deadline": request.form.get("deadline", "").strip() or None,
        "is_published": bool(request.form.get("is_published")),
    }










@app.route("/contact")
def contact():
    return render_template("contact.html")


BLOG_CATEGORIES = {
    "maslahat": "Maslahat", "qollanma": "Qo'llanma",
    "texnologiya": "Texnologiya", "yangilik": "Yangilik",
}


def _slugify(text):
    import re
    s = (text or "").strip().lower()
    s = s.replace("'", "").replace("'", "").replace("`", "")
    s = re.sub(r"[^a-z0-9Ѐ-ӿ]+", "-", s).strip("-")
    return s[:200] or "post"


def _blog_form_values():
    title = request.form.get("title", "").strip()
    slug = (request.form.get("slug", "").strip() or _slugify(title))
    return {
        "title": title,
        "slug": _slugify(slug),
        "summary": request.form.get("summary", "").strip()[:1000],
        "content": request.form.get("content", "").strip(),
        "category": request.form.get("category", "").strip(),
        "image_url": request.form.get("image_url", "").strip() or None,
        "is_published": bool(request.form.get("is_published")),
    }










@app.route("/api/course-subscribe", methods=["POST"])
@csrf.exempt
def course_subscribe():
    from data import get_connection
    data = request.get_json(silent=True) or request.form
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email or len(email) > 255:
        return jsonify({"success": False, "error": "Email noto'g'ri"}), 200
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO course_subscribers (email) VALUES (%s) ON CONFLICT (email) DO NOTHING",
                    (email,))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        return jsonify({"success": False, "error": "Xatolik yuz berdi"}), 200
    return jsonify({"success": True, "message": "Rahmat! Sizga xabar beramiz."})


@app.route("/preparation")
def preparation():
    return render_template("preparation.html")


@app.route("/courses")
def courses():
    return render_template("courses.html")


# ════════════════════════════════════════════════════════════════════
#  User feedback survey — rotating question groups, popup, analytics
# ════════════════════════════════════════════════════════════════════
@app.route('/api/survey/questions')
@csrf.exempt
def survey_questions_api():
    from data import get_connection
    answered = request.args.get('answered', '')
    answered_groups = [int(x) for x in answered.split(',') if x.strip().isdigit()]
    groups = {}
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, question_text, question_group FROM survey_questions "
                    "WHERE is_active = TRUE ORDER BY question_group, question_order")
                for r in cur.fetchall():
                    groups.setdefault(r[2], []).append({"id": r[0], "question_text": r[1]})
        finally:
            conn.close()
    except Exception:
        groups = {}
    total_groups = len(groups)
    for group_num in sorted(groups.keys()):
        if group_num not in answered_groups:
            return jsonify({
                "questions": groups[group_num],
                "group_number": group_num,
                "total_groups": total_groups,
            })
    return jsonify({"questions": [], "group_number": 0, "total_groups": total_groups})


@app.route('/api/survey/submit', methods=['POST'])
@csrf.exempt
def survey_submit_api():
    from flask import session
    from data import get_connection
    data = request.get_json(silent=True) or {}
    responses = data.get('responses', []) or []
    if not responses:
        return jsonify({"success": False, "error": "no responses"}), 400

    ip = get_real_ip()
    user_id = None
    username = None
    try:
        if hasattr(current_user, 'is_authenticated') and current_user.is_authenticated:
            user_id = current_user.id
            username = getattr(current_user, 'username', None)
        elif session.get('cabinet_user_id'):
            user_id = session['cabinet_user_id']
            username = session.get('cabinet_olim_name') or None
    except Exception:
        pass

    group_number = 1
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                first_qid = responses[0].get('question_id')
                if first_qid is not None:
                    cur.execute("SELECT question_group FROM survey_questions WHERE id = %s",
                                (first_qid,))
                    row = cur.fetchone()
                    if row:
                        group_number = row[0]
                for resp in responses:
                    qid = resp.get('question_id')
                    answer = (resp.get('answer') or '')[:20]
                    custom = (resp.get('custom_text') or '').strip() or None
                    if qid is None or not answer:
                        continue
                    cur.execute(
                        "INSERT INTO survey_responses "
                        "(question_id, ip_address, user_id, username, answer, custom_text) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (qid, ip, user_id, username, answer, custom))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    return jsonify({"success": True, "group_number": group_number})




# ════════════════════════════════════════════════════════════════════
#  SEO: sitemap.xml, robots.txt, OG default image
# ════════════════════════════════════════════════════════════════════
SITE_BASE = "https://www.olimlar.uz"


def _build_sitemap_xml():
    """Build the sitemap XML string. Cached 6 hours."""
    cached = cache.get("sitemap_xml_v1")
    if cached is not None:
        return cached
    from xml.sax.saxutils import escape
    from urllib.parse import quote
    static_pages = [
        ("/", "daily", "1.0"), ("/data", "daily", "0.9"),
        ("/compare", "weekly", "0.8"), ("/stats", "weekly", "0.7"),
        ("/trends", "weekly", "0.7"), ("/clustering", "weekly", "0.7"),
        ("/collaboration", "weekly", "0.7"), ("/top-olimlar", "weekly", "0.7"),
        ("/blog", "weekly", "0.6"), ("/courses", "monthly", "0.5"),
        ("/vacancies", "weekly", "0.5"), ("/yangiliklar", "daily", "0.6"),
        ("/about", "monthly", "0.4"), ("/heatmap", "weekly", "0.6"),
    ]
    top_olimlar, blog_posts = [], []
    try:
        from data import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT olim FROM dissertations "
                    "WHERE olim IS NOT NULL AND TRIM(olim) <> '' ORDER BY olim LIMIT 500")
                top_olimlar = [r[0] for r in cur.fetchall()]
                try:
                    cur.execute("SELECT slug FROM blog_posts WHERE is_published = TRUE")
                    blog_posts = [r[0] for r in cur.fetchall() if r[0]]
                except Exception:
                    blog_posts = []
        finally:
            conn.close()
    except Exception:
        top_olimlar, blog_posts = [], []

    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, freq, pri in static_pages:
        parts.append(
            f"  <url><loc>{SITE_BASE}{loc}</loc>"
            f"<changefreq>{freq}</changefreq><priority>{pri}</priority></url>")
    for name in top_olimlar:
        loc = f"{SITE_BASE}/olim/{quote(str(name), safe='')}"
        parts.append(
            f"  <url><loc>{escape(loc)}</loc>"
            f"<changefreq>monthly</changefreq><priority>0.5</priority></url>")
    for slug in blog_posts:
        loc = f"{SITE_BASE}/blog/{quote(str(slug), safe='')}"
        parts.append(
            f"  <url><loc>{escape(loc)}</loc>"
            f"<changefreq>monthly</changefreq><priority>0.5</priority></url>")
    parts.append("</urlset>")
    xml = "\n".join(parts)
    cache.set("sitemap_xml_v1", xml, timeout=21600)
    return xml


@app.route("/sitemap.xml")
def sitemap_xml():
    from flask import Response
    return Response(_build_sitemap_xml(), mimetype="application/xml")


@app.route("/robots.txt")
def robots_txt():
    from flask import Response
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin/\n"
        "Disallow: /cabinet/\n"
        "Disallow: /api/\n"
        "Disallow: /login\n"
        "Disallow: /register\n"
        "Disallow: /notifications\n"
        "\n"
        f"Sitemap: {SITE_BASE}/sitemap.xml\n"
    )
    return Response(body, mimetype="text/plain")


@app.route("/static/og-default.png")
def og_default_image():
    """Branded placeholder OG image (SVG). Replace with a real PNG later."""
    from flask import Response
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#0f172a"/><stop offset="1" stop-color="#1e3a8a"/>'
        '</linearGradient></defs>'
        '<rect width="1200" height="630" fill="url(#g)"/>'
        '<text x="600" y="300" fill="#ffffff" font-family="Inter,Arial,sans-serif" '
        'font-size="92" font-weight="800" text-anchor="middle">Olimlar.uz</text>'
        '<text x="600" y="380" fill="#93c5fd" font-family="Inter,Arial,sans-serif" '
        'font-size="38" text-anchor="middle">Ilmiy-tadqiqot ma\'lumotlar bazasi</text>'
        '</svg>'
    )
    return Response(svg, mimetype="image/svg+xml")


# ════════════════════════════════════════════════════════════════════
#  Research Heatmap — regional research activity map
# ════════════════════════════════════════════════════════════════════
UZ_REGIONS = {
    'Toshkent': ['Toshkent', 'Ташкент', 'ТАТУ', 'ТДЮУ', 'ТошДТУ', 'ТошДУ', 'ТГПУ', 'ТГЭУ', 'ТДШИ', 'ЎзМУ', 'НУУз'],
    'Samarqand': ['Самарканд', 'Самарқанд', 'Samarqand', 'СамДУ', 'СамГУ', 'СамМИ'],
    'Buxoro': ['Бухар', 'Бухоро', 'Buxoro', 'БухДУ', 'БухГУ'],
    "Farg'ona": ['Фарган', 'Фарғона', 'Fargona', 'ФарДУ', 'ФарГУ', 'ФарПИ'],
    'Andijon': ['Андижан', 'Андижон', 'Andijon', 'АндДУ', 'АндГУ', 'АндМИ'],
    'Namangan': ['Наманган', 'Namangan', 'НамДУ', 'НамМИ'],
    'Navoiy': ['Навои', 'Навоий', 'Navoiy', 'НавДУ'],
    'Qashqadaryo': ['Қашқадарё', 'Кашкадар', 'Qashqadaryo', 'Карши', 'Қарши', 'Qarshi'],
    'Surxondaryo': ['Сурхандар', 'Сурхондар', 'Surxondaryo', 'Термез', 'Термиз', 'Termiz'],
    'Jizzax': ['Жиззах', 'Jizzax', 'ЖизДПИ'],
    'Sirdaryo': ['Сырдар', 'Сирдар', 'Sirdaryo', 'Гулистан', 'Гулистон', 'Guliston'],
    'Xorazm': ['Хорезм', 'Хоразм', 'Xorazm', 'Урганч', 'Ургенч', 'Urgench'],
    "Qoraqalpog'iston": ['Каракалпак', 'Қорақалпоғ', 'Qoraqalpog', 'Нукус', 'Nukus'],
}


def detect_region(muassasa):
    if not muassasa:
        return 'Toshkent'
    ml = muassasa.lower()
    for region, keywords in UZ_REGIONS.items():
        for kw in keywords:
            if kw.lower() in ml:
                return region
    return 'Toshkent'


def _compute_heatmap_data():
    """Aggregate dissertations by detected region. Cached 6 hours."""
    cached = cache.get("heatmap_data_v1")
    if cached is not None:
        return cached
    import re as _re
    region_names = list(UZ_REGIONS.keys())
    regions = {r: {"total": 0, "phd": 0, "dsc": 0, "specialties": {}, "years": {}}
               for r in region_names}
    uni_stats = {}
    total = 0
    try:
        from data import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT muassasa, daraja, ixtisoslik, sana FROM dissertations "
                    "WHERE muassasa IS NOT NULL AND TRIM(muassasa) <> ''")
                for muassasa, daraja, ixtisoslik, sana in cur.fetchall():
                    region = detect_region(muassasa)
                    R = regions[region]
                    R["total"] += 1
                    total += 1
                    dl = (daraja or "").upper()
                    dlow = (daraja or "").lower()
                    is_phd = ("PHD" in dl or "фан" in dlow)
                    is_dsc = ("DSC" in dl or "док" in dlow)
                    if is_phd:
                        R["phd"] += 1
                    elif is_dsc:
                        R["dsc"] += 1
                    uni = (muassasa or "").strip()
                    us = uni_stats.setdefault(
                        uni, {"name": uni, "count": 0, "phd": 0, "dsc": 0, "region": region})
                    us["count"] += 1
                    if is_phd:
                        us["phd"] += 1
                    elif is_dsc:
                        us["dsc"] += 1
                    ix = (ixtisoslik or "").strip()
                    if ix:
                        R["specialties"][ix] = R["specialties"].get(ix, 0) + 1
                    m = _re.search(r"(19|20)\d{2}", sana or "")
                    if m:
                        yr = m.group(0)
                        R["years"][yr] = R["years"].get(yr, 0) + 1
        finally:
            conn.close()
    except Exception:
        pass

    region_unis = {r: [] for r in region_names}
    for us in uni_stats.values():
        region_unis.get(us["region"], []).append(us)

    out_regions = []
    for r in region_names:
        R = regions[r]
        unis = sorted(region_unis[r], key=lambda x: -x["count"])
        specs = sorted(R["specialties"].items(), key=lambda x: -x[1])
        years = sorted(R["years"].items())
        out_regions.append({
            "name": r, "total": R["total"], "phd": R["phd"], "dsc": R["dsc"],
            "uni_count": len(unis),
            "top_universities": [{"name": u["name"], "count": u["count"]} for u in unis[:5]],
            "top_specialties": [{"name": n, "count": c} for n, c in specs[:5]],
            "years": [{"year": y, "count": c} for y, c in years],
        })
    out_regions.sort(key=lambda x: -x["total"])
    top_universities = sorted(uni_stats.values(), key=lambda x: -x["count"])[:20]
    result = {"regions": out_regions, "total": total, "top_universities": top_universities}
    cache.set("heatmap_data_v1", result, timeout=21600)
    return result


@app.route("/heatmap")
def heatmap():
    data = _compute_heatmap_data()
    return render_template("heatmap.html", regions=data["regions"],
                           total=data["total"], top_universities=data["top_universities"])


# ════════════════════════════════════════════════════════════════════
#  University portfolio system
# ════════════════════════════════════════════════════════════════════
_UNI_TYPE_LABELS = {'davlat': 'Davlat', 'xususiy': 'Xususiy', 'xalqaro': 'Xalqaro'}
_UNI_STOPWORDS = {'nomidagi', 'davlat', 'universiteti', 'instituti', 'milliy',
                  'xalqaro', 'xususiy', 'va', 'xojaligi', 'fanlari', 'shahridagi',
                  'shahrida', 'university', 'international'}


def _uni_keywords(name):
    words = [w.strip(".,'’\"").lower() for w in (name or '').split()]
    kw = [w for w in words if len(w) > 3 and w not in _UNI_STOPWORDS]
    if not kw:
        kw = [w for w in words if len(w) > 3]
    return kw[:3]


def _uni_where(term):
    """WHERE clause + params matching dissertations to an institution text."""
    like = f"%{(term or '').strip().lower()}%"
    return ("(LOWER(muassasa) LIKE %s OR LOWER(COALESCE(ilmiy_kengash,'')) LIKE %s)",
            [like, like])


@cache.cached(timeout=3600, key_prefix='university_stats')
def get_university_dissertation_stats():
    """Map university id → {total, olimlar} dissertation counts. Cached 1h."""
    from data import get_connection
    stats = {}
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name FROM universities WHERE is_active = TRUE")
                unis = cur.fetchall()
                for uid, name in unis:
                    kws = _uni_keywords(name)
                    if not kws:
                        stats[uid] = {'total': 0, 'olimlar': 0}
                        continue
                    clause = " AND ".join(["LOWER(muassasa) LIKE %s"] * len(kws))
                    params = [f"%{k}%" for k in kws]
                    cur.execute(
                        f"SELECT COUNT(*), COUNT(DISTINCT olim) FROM dissertations WHERE {clause}",
                        params)
                    r = cur.fetchone()
                    stats[uid] = {'total': r[0] or 0, 'olimlar': r[1] or 0}
        finally:
            conn.close()
    except Exception:
        stats = {}
    return stats


def _uni_row_to_dict(cols, row):
    d = dict(zip(cols, row))
    d['type_label'] = _UNI_TYPE_LABELS.get(d.get('university_type'), d.get('university_type') or '')
    return d


def _find_university(cur, term):
    """Find a universities row matching the institution text (exact, else fuzzy)."""
    cur.execute("SELECT * FROM universities WHERE LOWER(name) = LOWER(%s) LIMIT 1", (term,))
    row = cur.fetchone()
    cols = [c[0] for c in cur.description]
    if row:
        return _uni_row_to_dict(cols, row)
    # Fuzzy: a university whose keywords all appear in the institution text.
    t = (term or '').lower()
    cur.execute("SELECT * FROM universities")
    cols = [c[0] for c in cur.description]
    best, best_score = None, 0
    for r in cur.fetchall():
        d = _uni_row_to_dict(cols, r)
        kws = _uni_keywords(d['name'])
        if kws and all(k in t for k in kws) and len(kws) > best_score:
            best, best_score = d, len(kws)
    return best


def _save_university_logo():
    """Save an uploaded university logo and return its web path, or None."""
    f = request.files.get("logo")
    if not f or not f.filename:
        return None
    from werkzeug.utils import secure_filename
    import time as _time
    fname = secure_filename(f.filename)
    ext = os.path.splitext(fname)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"):
        return None
    upload_dir = os.path.join(app.static_folder, "uploads", "university_logos")
    os.makedirs(upload_dir, exist_ok=True)
    saved = f"{int(_time.time())}_{fname}"
    try:
        f.save(os.path.join(upload_dir, saved))
    except Exception:
        return None
    return f"/static/uploads/university_logos/{saved}"


_UNI_EDIT_FIELDS = ['name', 'short_name', 'website', 'city', 'region', 'university_type',
                    'description', 'founded_year', 'rector', 'address', 'phone', 'email',
                    'telegram', 'student_count', 'teacher_count',
                    'instagram', 'facebook', 'youtube', 'rector_photo_url']




def _uni_form_values():
    vals = {}
    for f in _UNI_EDIT_FIELDS:
        v = (request.form.get(f) or '').strip()
        vals[f] = v or None
    for intf in ('founded_year', 'student_count', 'teacher_count'):
        if vals.get(intf):
            try:
                vals[intf] = int(vals[intf])
            except (TypeError, ValueError):
                vals[intf] = None
    return vals














# ════════════════════════════════════════════════════════════════════
#  Scientific journals system
# ════════════════════════════════════════════════════════════════════
_JOURNAL_COLS = [
    'name', 'name_en', 'issn', 'eissn', 'publisher', 'country', 'languages',
    'indexing', 'website', 'description', 'requirements', 'publish_fee',
    'review_period', 'frequency', 'registered_number', 'registered_date',
    'article_requirements', 'accepts_languages', 'publish_format', 'impact_factor',
    'is_predatory', 'is_active', 'oak_approved', 'scopus_indexed', 'wos_indexed',
    'scholar_indexed',
]
_JOURNAL_BOOLS = {'is_predatory', 'is_active', 'oak_approved', 'scopus_indexed',
                  'wos_indexed', 'scholar_indexed'}


def _journal_row(cols, row):
    return dict(zip(cols, row))


def _save_journal_logo():
    f = request.files.get("logo")
    if not f or not f.filename:
        return None
    from werkzeug.utils import secure_filename
    import time as _time
    fname = secure_filename(f.filename)
    ext = os.path.splitext(fname)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"):
        return None
    upload_dir = os.path.join(app.static_folder, "uploads", "journal_logos")
    os.makedirs(upload_dir, exist_ok=True)
    saved = f"{int(_time.time())}_{fname}"
    try:
        f.save(os.path.join(upload_dir, saved))
    except Exception:
        return None
    return f"/static/uploads/journal_logos/{saved}"


def _journal_form_values():
    vals = {}
    for f in _JOURNAL_COLS:
        if f in _JOURNAL_BOOLS:
            vals[f] = bool(request.form.get(f))
        else:
            v = (request.form.get(f) or '').strip()
            vals[f] = v or None
    if vals.get('impact_factor'):
        try:
            vals['impact_factor'] = float(vals['impact_factor'])
        except (TypeError, ValueError):
            vals['impact_factor'] = None
    return vals


def _save_journal_specialties(cur, journal_id):
    """Replace this journal's specialty links from the submitted checkboxes."""
    codes = request.form.getlist('specialty_codes')
    cur.execute("DELETE FROM journal_specialties WHERE journal_id = %s", (journal_id,))
    for code in codes:
        code = (code or '').strip()
        if code in SPECIALTY_NAMES:
            cur.execute(
                "INSERT INTO journal_specialties (journal_id, specialty_code, specialty_name) "
                "VALUES (%s, %s, %s)", (journal_id, code, SPECIALTY_NAMES[code]))












if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
