import os
from dotenv import load_dotenv
from flask_wtf.csrf import CSRFProtect
import bcrypt
from flask import Flask, render_template, redirect, url_for, jsonify, request, abort, flash
from urllib.parse import urlparse
from flask_login import (LoginManager, UserMixin, logout_user,
                         login_required, current_user)

app = Flask(__name__)
# Trust one level of proxy headers (Cloudflare/Railway) so request.remote_addr
# and the X-Forwarded-* family resolve to the real client.
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
load_dotenv()
session_secret = os.environ.get("SESSION_SECRET")
if not session_secret:
    raise RuntimeError(
        "SESSION_SECRET is not set. Add it to a .env file or set the environment variable."
    )
app.secret_key = session_secret
csrf = CSRFProtect(app)

# Ensure the news image upload directory exists.
try:
    os.makedirs(os.path.join(app.static_folder, "uploads", "news"), exist_ok=True)
except Exception:
    pass

from extensions import cache
cache.init_app(app, config={
    'CACHE_TYPE': 'SimpleCache',
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


class User(UserMixin):
    def __init__(self, id, username, email):
        self.id = id
        self.username = username
        self.email = email


@login_manager.user_loader
def load_user(user_id):
    from dotenv import load_dotenv
    load_dotenv()
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        return None
    try:
        import psycopg2
        conn = psycopg2.connect(database_url)
        cur = conn.cursor()
        cur.execute("SELECT id, username, email FROM users WHERE id = %s", (int(user_id),))
        row = cur.fetchone()
        cur.close()
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

app.register_blueprint(auth_bp)
app.register_blueprint(data_bp)
app.register_blueprint(analytics_bp)
app.register_blueprint(upload_bp)
app.register_blueprint(cabinet_bp)

# Telegram login uses HMAC hash verification — no CSRF token needed
csrf.exempt(app.view_functions['auth.telegram_login'])


# ── University seed data + detection ────────────────────────────────────────
# (name, university_type) — city/region are detected from the name.
_UNIVERSITY_SEED = [
    # Davlat universitetlari
    ('Abu Ali Ibn Sino Nomidagi Buxoro Davlat Tibbiyot Instituti', 'davlat'),
    ('Abu Rayhon Beruniy nomidagi Urganch davlat universiteti', 'davlat'),
    ('Ajiniyoz nomidagi Nukus davlat pedagogika instituti', 'davlat'),
    ('Andijon davlat chet tillari instituti', 'davlat'),
    ('Andijon davlat pedagogika instituti', 'davlat'),
    ('Andijon davlat texnika instituti', 'davlat'),
    ('Andijon davlat tibbiyot instituti', 'davlat'),
    ('Andijon davlat universiteti', 'davlat'),
    ('Andijon mashinasozlik instituti', 'davlat'),
    ('Andijon qishloq xojaligi va agrotexnologiyalar instituti', 'davlat'),
    ('Berdaq nomidagi Qoraqalpoq davlat universiteti', 'davlat'),
    ('Botir Zokirov nomidagi Milliy estrada sanati instituti', 'davlat'),
    ('Buxoro davlat pedagogika instituti', 'davlat'),
    ('Buxoro davlat texnika universiteti', 'davlat'),
    ('Buxoro davlat universiteti', 'davlat'),
    ('Buxoro muhandislik-texnologiya instituti', 'davlat'),
    ('Chirchiq davlat pedagogika universiteti', 'davlat'),
    ('Fargona davlat texnika universiteti', 'davlat'),
    ('Fargona davlat universiteti', 'davlat'),
    ('Fargona jamoat salomatligi tibbiyot instituti', 'davlat'),
    ('Fargona politexnika instituti', 'davlat'),
    ('Geologiya fanlari universiteti', 'davlat'),
    ('Guliston davlat pedagogika instituti', 'davlat'),
    ('Guliston davlat universiteti', 'davlat'),
    ('Jahon iqtisodiyoti va diplomatiya universiteti', 'davlat'),
    ('Jizzax davlat pedagogika universiteti', 'davlat'),
    ('Jizzax politexnika instituti', 'davlat'),
    ('Mirzo Ulugbek nomidagi Ozbekiston Milliy universiteti', 'davlat'),
    ('Muhammad al-Xorazmiy nomidagi Toshkent axborot texnologiyalari universiteti', 'davlat'),
    ('Namangan davlat chet tillari instituti', 'davlat'),
    ('Namangan davlat pedagogika instituti', 'davlat'),
    ('Namangan davlat texnika universiteti', 'davlat'),
    ('Namangan davlat universiteti', 'davlat'),
    ('Navoiy davlat konchilik va texnologiyalar universiteti', 'davlat'),
    ('Navoiy davlat universiteti', 'davlat'),
    ('Ozbekiston davlat jismoniy tarbiya va sport universiteti', 'davlat'),
    ('Ozbekiston davlat konservatoriyasi', 'davlat'),
    ('Ozbekiston davlat jahon tillari universiteti', 'davlat'),
    ('Ozbekiston milliy pedagogika universiteti', 'davlat'),
    ('Qarshi davlat universiteti', 'davlat'),
    ('Qoqon davlat universiteti', 'davlat'),
    ('Samarqand davlat chet tillar instituti', 'davlat'),
    ('Samarqand davlat tibbiyot universiteti', 'davlat'),
    ('Sharof Rashidov nomidagi Samarqand davlat universiteti', 'davlat'),
    ('Termiz davlat universiteti', 'davlat'),
    ('Toshkent davlat agrar universiteti', 'davlat'),
    ('Toshkent davlat iqtisodiyot universiteti', 'davlat'),
    ('Toshkent davlat texnika universiteti', 'davlat'),
    ('Toshkent davlat tibbiyot universiteti', 'davlat'),
    ('Toshkent davlat transport universiteti', 'davlat'),
    ('Toshkent kimyo-texnologiya instituti', 'davlat'),
    # Xususiy / xalqaro universitetlar
    ('Akfa universiteti', 'xususiy'),
    ('Alfraganus University', 'xususiy'),
    ('Binary international university', 'xususiy'),
    ('British Management University', 'xususiy'),
    ('Cambridge International University', 'xususiy'),
    ('Digital University', 'xususiy'),
    ('IT-Park University', 'xususiy'),
    ('Japan Digital University', 'xususiy'),
    ('Kokand university', 'xususiy'),
    ('PDP University', 'xususiy'),
    ('Perfect University', 'xususiy'),
    ('Renessans talim universiteti', 'xususiy'),
    ('Sharda universiteti', 'xususiy'),
    ('Stars International University', 'xususiy'),
    ('TEAM University', 'xususiy'),
    ('Toshkent shahridagi Inha universiteti', 'xalqaro'),
    ('Toshkent shahridagi Turin politexnika universiteti', 'xalqaro'),
    ('Toshkent shahrida Vebster universiteti', 'xalqaro'),
    ('Xalqaro innovatsion universiteti', 'xususiy'),
    ('Yangi asr universiteti', 'xususiy'),
]


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


def _seed_universities(cur):
    """Insert the seed universities once (no-op if the table already has rows)."""
    cur.execute("SELECT COUNT(*) FROM universities")
    if (cur.fetchone()[0] or 0) > 0:
        return
    for name, utype in _UNIVERSITY_SEED:
        city, region = detect_uni_city_region(name)
        cur.execute(
            "INSERT INTO universities (name, university_type, city, region) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (name) DO NOTHING",
            (name, utype, city, region))


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
            _seed_universities(cur)
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

    return render_template("home.html", recent=recent, news=news,
                           top_supervisors=top_supervisors,
                           top_supervisors_random=top_supervisors_random,
                           top_marquee=top_marquee, total_stats=total_stats,
                           gender_pct=gender_pct, latest_blog=latest_blog,
                           active_vacancy_count=active_vacancy_count,
                           top_universities=top_universities)


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


@app.route('/admin/analytics')
@login_required
def admin_analytics():
    if current_user.username != 'admin':
        abort(403)
    from data import get_connection
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Today's visits
            cur.execute("SELECT COUNT(*) FROM page_visits WHERE visited_at::date = CURRENT_DATE")
            today_visits = cur.fetchone()[0]

            # Today's unique visitors
            cur.execute("""SELECT COUNT(DISTINCT COALESCE(user_id::text, ip_address))
                FROM page_visits WHERE visited_at::date = CURRENT_DATE""")
            today_unique = cur.fetchone()[0]

            # Online now (last 5 min)
            cur.execute("""SELECT COUNT(DISTINCT COALESCE(user_id::text, ip_address))
                FROM page_visits WHERE visited_at > NOW() - INTERVAL '5 minutes'""")
            online_now = cur.fetchone()[0]

            # Identity = registered username (case-folded) for real accounts,
            # else the IP. Guests are logged as 'Mehmon'/'Anonim' so they group by IP.
            _ID = ("CASE WHEN username IS NOT NULL AND TRIM(username) <> '' "
                   "AND username NOT IN ('Mehmon', 'Anonim') "
                   "THEN LOWER(TRIM(username)) ELSE ip_address END")

            # Recent visitors — one row per identity, latest activity first (last 24h)
            cur.execute(f"""
                SELECT {_ID} AS identity,
                       MAX(username) AS username,
                       MAX(user_id) AS user_id,
                       (array_agg(page ORDER BY visited_at DESC))[1] AS last_page,
                       MAX(visited_at) AS last_visit,
                       COUNT(*) AS total_visits,
                       COUNT(DISTINCT page) AS unique_pages,
                       MAX(ip_address) AS ip,
                       MAX(country) AS country
                FROM page_visits
                WHERE visited_at >= NOW() - INTERVAL '24 hours'
                GROUP BY {_ID}
                ORDER BY last_visit DESC
                LIMIT 50
            """)
            recent = [{
                "username": r[1] or "", "user_id": r[2],
                "page": r[3] or "/", "visited_at": r[4],
                "total_visits": r[5] or 0, "unique_pages": r[6] or 0,
                "ip": r[7] or "—", "country": r[8] or "",
            } for r in cur.fetchall()]

            # Daily visits last 7 days
            cur.execute("""SELECT visited_at::date AS d, COUNT(*) AS cnt FROM page_visits
                WHERE visited_at > NOW() - INTERVAL '7 days'
                GROUP BY d ORDER BY d""")
            weekly = cur.fetchall()

            # Top registered visitors — grouped by username (one row per account)
            registered_visitors = []
            try:
                cur.execute("""
                    SELECT LOWER(TRIM(username)) AS identity,
                           MAX(username) AS username,
                           MAX(user_id) AS user_id,
                           COUNT(*) AS visit_count,
                           COUNT(DISTINCT ip_address) AS ip_count,
                           COUNT(DISTINCT page) AS unique_pages,
                           MIN(visited_at) AS first_visit,
                           MAX(visited_at) AS last_visit,
                           MAX(country) AS country,
                           MAX(ip_address) AS ip
                    FROM page_visits
                    WHERE username IS NOT NULL AND TRIM(username) <> ''
                          AND username NOT IN ('Mehmon', 'Anonim')
                    GROUP BY LOWER(TRIM(username))
                    ORDER BY visit_count DESC
                    LIMIT 20
                """)
                registered_visitors = [{
                    "username": r[1] or "", "user_id": r[2], "visit_count": r[3] or 0,
                    "ip_count": r[4] or 0, "unique_pages": r[5] or 0,
                    "first_visit": str(r[6])[:16] if r[6] else "",
                    "last_visit": str(r[7])[:16] if r[7] else "",
                    "country": r[8] or "", "ip": r[9] or "—",
                } for r in cur.fetchall()]
            except Exception:
                registered_visitors = []

            # Top guest visitors — grouped by IP (no real account)
            guest_visitors = []
            try:
                cur.execute("""
                    SELECT ip_address,
                           COUNT(*) AS visit_count,
                           COUNT(DISTINCT page) AS unique_pages,
                           MIN(visited_at) AS first_visit,
                           MAX(visited_at) AS last_visit,
                           MAX(country) AS country
                    FROM page_visits
                    WHERE username IS NULL OR TRIM(username) = ''
                          OR username IN ('Mehmon', 'Anonim')
                    GROUP BY ip_address
                    ORDER BY visit_count DESC
                    LIMIT 20
                """)
                guest_visitors = [{
                    "ip": r[0] or "—", "visit_count": r[1] or 0, "unique_pages": r[2] or 0,
                    "first_visit": str(r[3])[:16] if r[3] else "",
                    "last_visit": str(r[4])[:16] if r[4] else "",
                    "country": r[5] or "",
                } for r in cur.fetchall()]
            except Exception:
                guest_visitors = []

            # Registered cabinet users (if any)
            registered_users = []
            try:
                cur.execute("""
                    SELECT id, email, telegram_username, telegram_first_name,
                           olim_name, created_at, last_login
                    FROM cabinet_users ORDER BY created_at DESC LIMIT 50
                """)
                registered_users = [{
                    "id": r[0], "email": r[1] or "", "telegram_username": r[2] or "",
                    "telegram_first_name": r[3] or "", "olim_name": r[4] or "",
                    "created_at": r[5], "last_login": r[6],
                } for r in cur.fetchall()]
            except Exception:
                registered_users = []

            # ── Summary: registered users / today's new sign-ups / guests ──
            registered_count = 0
            new_today_count = 0
            guest_count = 0
            try:
                cur.execute("SELECT COUNT(*) FROM cabinet_users")
                registered_count = cur.fetchone()[0] or 0
            except Exception:
                registered_count = 0
            try:
                cur.execute("SELECT COUNT(*) FROM cabinet_users WHERE created_at >= CURRENT_DATE")
                new_today_count = cur.fetchone()[0] or 0
            except Exception:
                new_today_count = 0
            try:
                # Distinct guest visitors (no account → logged as "Mehmon"), by IP
                cur.execute("""SELECT COUNT(DISTINCT ip_address) FROM page_visits
                    WHERE user_id IS NULL AND (username = 'Mehmon' OR username IS NULL)""")
                guest_count = cur.fetchone()[0] or 0
            except Exception:
                guest_count = 0

            # Totals for the summary row
            total_dissertations = 0
            total_news = 0
            try:
                cur.execute("SELECT COUNT(*) FROM dissertations")
                total_dissertations = cur.fetchone()[0] or 0
            except Exception:
                total_dissertations = 0
            try:
                cur.execute("SELECT COUNT(*) FROM yangiliklar WHERE is_published = TRUE")
                total_news = cur.fetchone()[0] or 0
            except Exception:
                total_news = 0

            # Live online users (last 5 min) — one row per distinct visitor,
            # showing the page they are currently on.
            online_users = []
            try:
                cur.execute(f"""
                    SELECT {_ID} AS identity,
                           MAX(username) AS username,
                           MAX(user_id) AS user_id,
                           (array_agg(page ORDER BY visited_at DESC))[1] AS current_page,
                           MAX(visited_at) AS last_activity,
                           COUNT(DISTINCT ip_address) AS ip_count,
                           MAX(ip_address) AS ip,
                           MAX(country) AS country
                    FROM page_visits
                    WHERE visited_at > NOW() - INTERVAL '5 minutes'
                    GROUP BY {_ID}
                    ORDER BY last_activity DESC
                """)
                online_users = [{
                    "ip": r[6] or "—", "username": r[1] or "", "user_id": r[2],
                    "page": r[3] or "/", "visited_at": r[4], "country": r[7] or "",
                } for r in cur.fetchall()]
            except Exception:
                online_users = []

            # Currently active blocks
            active_blocks = []
            try:
                cur.execute("""
                    SELECT id, ip_address, reason, blocked_by, blocked_until,
                           is_permanent, created_at, duration_text
                    FROM blocked_users
                    WHERE is_active = TRUE AND (is_permanent = TRUE OR blocked_until > NOW())
                    ORDER BY created_at DESC
                """)
                active_blocks = [{
                    "id": r[0], "ip_address": r[1] or "—", "reason": r[2] or "",
                    "blocked_by": r[3] or "admin", "blocked_until": r[4],
                    "is_permanent": r[5], "created_at": r[6], "duration_text": r[7] or "",
                } for r in cur.fetchall()]
            except Exception:
                active_blocks = []

            # Block history (inactive / expired / unblocked)
            block_history = []
            try:
                cur.execute("""
                    SELECT id, ip_address, reason, blocked_by, blocked_until,
                           is_permanent, created_at, duration_text, unblocked_at, unblocked_by
                    FROM blocked_users
                    WHERE is_active = FALSE
                    ORDER BY created_at DESC LIMIT 50
                """)
                block_history = [{
                    "id": r[0], "ip_address": r[1] or "—", "reason": r[2] or "",
                    "blocked_by": r[3] or "admin", "blocked_until": r[4],
                    "is_permanent": r[5], "created_at": r[6], "duration_text": r[7] or "",
                    "unblocked_at": r[8], "unblocked_by": r[9] or "",
                } for r in cur.fetchall()]
            except Exception:
                block_history = []

            # Active broadcasts list (for admin management)
            broadcasts = []
            try:
                cur.execute("""
                    SELECT id, message, message_type, is_active, show_to,
                           created_at, expires_at
                    FROM admin_broadcasts
                    WHERE is_active = TRUE
                    ORDER BY created_at DESC
                """)
                broadcasts = [{
                    "id": r[0], "message": r[1] or "", "message_type": r[2] or "info",
                    "is_active": r[3], "show_to": r[4] or "all",
                    "created_at": r[5], "expires_at": r[6],
                } for r in cur.fetchall()]
            except Exception:
                broadcasts = []
    finally:
        conn.close()

    return render_template('admin_analytics.html',
        today_visits=today_visits, today_unique=today_unique, online_now=online_now,
        recent=recent, weekly=weekly,
        registered_visitors=registered_visitors, guest_visitors=guest_visitors,
        registered_users=registered_users,
        registered_count=registered_count, new_today_count=new_today_count,
        guest_count=guest_count, total_dissertations=total_dissertations,
        total_news=total_news, online_users=online_users,
        active_blocks=active_blocks, block_history=block_history,
        now=datetime.utcnow(), broadcasts=broadcasts)


# Duration string → Postgres interval literal (expiry computed server-side via NOW()).
_DURATION_INTERVALS = {
    "10m": "10 minutes", "30m": "30 minutes",
    "1h": "1 hour", "24h": "24 hours", "7d": "7 days", "1d": "1 day",
}


@app.route('/admin/api/block-user', methods=['POST'])
@csrf.exempt
@login_required
def admin_block_user():
    _require_admin()
    from data import get_connection
    data = request.get_json(silent=True) or {}
    ip = (data.get('ip_address') or '').strip()
    if not ip:
        return jsonify({"success": False, "error": "ip_address required"}), 400
    reason = (data.get('reason') or '').strip()[:500] or None
    duration = data.get('duration') or '30m'
    if duration != "permanent" and duration not in _DURATION_INTERVALS:
        duration = '30m'
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                # Keep history — supersede any existing active block for this IP
                # rather than deleting it.
                cur.execute(
                    "UPDATE blocked_users SET is_active = FALSE, unblocked_at = NOW(), "
                    "unblocked_by = 'admin (qayta bloklash)' "
                    "WHERE ip_address = %s AND is_active = TRUE", (ip,))
                if duration == "permanent":
                    cur.execute("""
                        INSERT INTO blocked_users
                            (ip_address, reason, blocked_by, blocked_until,
                             is_permanent, is_active, duration_text)
                        VALUES (%s, %s, 'admin', NULL, TRUE, TRUE, 'permanent')
                    """, (ip, reason))
                else:
                    interval = _DURATION_INTERVALS.get(duration, "30 minutes")
                    cur.execute("""
                        INSERT INTO blocked_users
                            (ip_address, reason, blocked_by, blocked_until,
                             is_permanent, is_active, duration_text)
                        VALUES (%s, %s, 'admin', NOW() + INTERVAL %s, FALSE, TRUE, %s)
                    """, (ip, reason, interval, duration))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/admin/api/unblock-user', methods=['POST'])
@csrf.exempt
@login_required
def admin_unblock_user():
    _require_admin()
    from data import get_connection
    data = request.get_json(silent=True) or {}
    ip = (data.get('ip_address') or '').strip()
    if not ip:
        return jsonify({"success": False, "error": "ip_address required"}), 400
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE blocked_users SET is_active = FALSE, unblocked_at = NOW(), "
                    "unblocked_by = 'admin' WHERE ip_address = %s AND is_active = TRUE", (ip,))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/admin/api/broadcast', methods=['POST'])
@csrf.exempt
@login_required
def admin_broadcast():
    _require_admin()
    from data import get_connection
    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({"success": False, "error": "message required"}), 400
    mtype = data.get('type') or 'info'
    if mtype not in ('info', 'warning', 'success'):
        mtype = 'info'
    show_to = data.get('show_to') or 'all'
    if show_to not in ('all', 'guests', 'registered'):
        show_to = 'all'
    duration = data.get('duration') or '24h'
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                if duration == "permanent":
                    cur.execute("""
                        INSERT INTO admin_broadcasts
                            (message, message_type, show_to, expires_at, is_active)
                        VALUES (%s, %s, %s, NULL, TRUE)
                    """, (message, mtype, show_to))
                else:
                    interval = _DURATION_INTERVALS.get(duration, "24 hours")
                    cur.execute("""
                        INSERT INTO admin_broadcasts
                            (message, message_type, show_to, expires_at, is_active)
                        VALUES (%s, %s, %s, NOW() + INTERVAL %s, TRUE)
                    """, (message, mtype, show_to, interval))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/admin/api/broadcast/delete/<int:id>', methods=['POST'])
@csrf.exempt
@login_required
def admin_broadcast_delete(id):
    _require_admin()
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE admin_broadcasts SET is_active = FALSE WHERE id = %s", (id,))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/admin/user-activity/<identifier>')
@login_required
def admin_user_activity(identifier):
    if current_user.username != 'admin':
        abort(403)
    from collections import OrderedDict, Counter
    from data import get_connection

    def _to_uzt(dt):
        if not isinstance(dt, datetime):
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(UZT)

    is_ip = identifier.replace('.', '').isdigit() and '.' in identifier
    user_info = {'type': 'ip' if is_ip else 'user', 'identifier': identifier,
                 'username': '', 'email': '', 'telegram_username': '',
                 'olim_name': '', 'registered': False, 'user_id': None}
    rows = []
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if is_ip:
                cur.execute("""
                    SELECT page, visited_at, user_agent, referrer, username, user_id
                    FROM page_visits WHERE ip_address = %s
                    ORDER BY visited_at DESC LIMIT 200
                """, (identifier,))
                rows = cur.fetchall()
                for r in rows:
                    if r[4]:
                        user_info['username'] = r[4]
                        user_info['user_id'] = r[5]
                        break
            else:
                uid = None
                try:
                    uid = int(identifier)
                except Exception:
                    uid = None
                cur.execute("""
                    SELECT page, visited_at, user_agent, referrer, username, user_id
                    FROM page_visits WHERE user_id = %s OR username = %s
                    ORDER BY visited_at DESC LIMIT 200
                """, (uid, identifier))
                rows = cur.fetchall()
                user_info['username'] = (rows[0][4] if rows else '') or identifier
                user_info['user_id'] = uid if uid is not None else (rows[0][5] if rows else None)
                # Enrich from cabinet_users if registered
                try:
                    if uid is not None:
                        cur.execute("""SELECT id, email, telegram_username, olim_name
                                       FROM cabinet_users WHERE id = %s""", (uid,))
                    else:
                        cur.execute("""SELECT id, email, telegram_username, olim_name
                                       FROM cabinet_users
                                       WHERE email = %s OR telegram_username = %s OR olim_name = %s""",
                                    (identifier, identifier, identifier))
                    cu = cur.fetchone()
                    if cu:
                        user_info['registered'] = True
                        user_info['user_id'] = cu[0]
                        user_info['email'] = cu[1] or ''
                        user_info['telegram_username'] = cu[2] or ''
                        user_info['olim_name'] = cu[3] or ''
                except Exception:
                    pass
    finally:
        conn.close()

    # Build per-visit dicts (converted to Tashkent time)
    activities = []
    for page, visited_at, ua, ref, _uname, _uid in rows:
        uz = _to_uzt(visited_at)
        activities.append({
            'page': page or '/', 'visited_at': visited_at, 'uz': uz,
            'user_agent': ua or '', 'referrer': ref or '',
        })

    # Stats
    pages = [a['page'] for a in activities]
    uz_dates = [a['uz'] for a in activities if a['uz']]
    page_counter = Counter(pages)
    most_visited_page = page_counter.most_common(1)[0][0] if page_counter else '—'
    distinct_days = len({d.date() for d in uz_dates}) or 1
    devices = len({a['user_agent'] for a in activities if a['user_agent']})
    last_dt = max(uz_dates) if uz_dates else None
    first_dt = min(uz_dates) if uz_dates else None

    stats = {
        'total_visits': len(activities),
        'unique_pages': len(page_counter),
        'first_visit': first_dt.strftime('%d.%m.%Y %H:%M') if first_dt else '—',
        'last_visit': last_dt.strftime('%d.%m.%Y %H:%M') if last_dt else '—',
        'most_visited_page': most_visited_page,
        'avg_visits_per_day': round(len(activities) / distinct_days, 1),
        'devices': devices,
    }

    # Most common device / referrer
    dev_counter = Counter(parse_device(a['user_agent']) for a in activities if a['user_agent'])
    ref_counter = Counter(parse_referrer(a['referrer']) for a in activities)
    stats['device_info'] = dev_counter.most_common(1)[0][0] if dev_counter else '—'
    stats['referrer_info'] = ref_counter.most_common(1)[0][0] if ref_counter else '—'

    # Timeline grouped by date (already DESC by visited_at)
    visits_by_date = OrderedDict()
    for a in activities:
        if not a['uz']:
            continue
        key = a['uz'].strftime('%d.%m.%Y')
        visits_by_date.setdefault(key, []).append({
            'time': a['uz'].strftime('%H:%M'), 'page': a['page'],
        })

    # Page frequency (top 10) for CSS bar chart
    top_pages_freq = page_counter.most_common(10)
    max_page_count = top_pages_freq[0][1] if top_pages_freq else 1

    # Hour-of-day heatmap (Tashkent hours)
    hour_counts = [0] * 24
    for d in uz_dates:
        hour_counts[d.hour] += 1
    max_hour_count = max(hour_counts) if any(hour_counts) else 1

    # Block status + history for this IP (block actions are IP-based)
    block_ip = identifier if is_ip else None
    current_block = None
    user_block_history = []
    if block_ip:
        try:
            bconn = get_connection()
            try:
                with bconn.cursor() as bcur:
                    bcur.execute("""
                        SELECT reason, blocked_until, is_permanent, duration_text
                        FROM blocked_users
                        WHERE ip_address = %s AND is_active = TRUE
                        AND (is_permanent = TRUE OR blocked_until > NOW())
                        ORDER BY created_at DESC LIMIT 1
                    """, (block_ip,))
                    br = bcur.fetchone()
                    if br:
                        current_block = {
                            "reason": br[0] or "", "blocked_until": br[1],
                            "is_permanent": br[2], "duration_text": br[3] or "",
                        }
                    bcur.execute("""
                        SELECT reason, blocked_until, is_permanent, created_at,
                               duration_text, is_active, unblocked_at, unblocked_by
                        FROM blocked_users WHERE ip_address = %s
                        ORDER BY created_at DESC
                    """, (block_ip,))
                    user_block_history = [{
                        "reason": r[0] or "", "blocked_until": r[1], "is_permanent": r[2],
                        "created_at": r[3], "duration_text": r[4] or "",
                        "is_active": r[5], "unblocked_at": r[6], "unblocked_by": r[7] or "",
                    } for r in bcur.fetchall()]
            finally:
                bconn.close()
        except Exception:
            current_block = None
            user_block_history = []

    return render_template('admin_user_activity.html',
        user_info=user_info, activities=activities, stats=stats,
        visits_by_date=visits_by_date, top_pages_freq=top_pages_freq,
        max_page_count=max_page_count, hour_counts=hour_counts,
        max_hour_count=max_hour_count,
        block_ip=block_ip, current_block=current_block,
        user_block_history=user_block_history)


def _require_admin():
    if not current_user.is_authenticated or current_user.username != 'admin':
        abort(403)


@app.route("/admin/yangiliklar")
@login_required
def admin_yangiliklar():
    _require_admin()
    from data import get_connection
    items = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, title, summary, created_at, is_published, image_url "
                    "FROM yangiliklar ORDER BY created_at DESC, id DESC"
                )
                items = [{
                    "id": r[0], "title": r[1] or "", "summary": r[2] or "",
                    "created_at": str(r[3])[:16] if r[3] else "", "is_published": r[4],
                    "image_url": r[5] or "",
                } for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        items = []
    return render_template("admin_yangiliklar.html", items=items)


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


@app.route("/admin/yangiliklar/add", methods=["GET", "POST"])
@login_required
def admin_yangilik_add():
    _require_admin()
    from data import get_connection
    if request.method == "POST":
        v = _yangilik_form_values()
        if not v["title"] or not v["summary"]:
            flash("Sarlavha va qisqa matn majburiy.", "error")
            return render_template("admin_yangilik_form.html", item=v, edit_mode=False)
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO yangiliklar (title, summary, content, image_url, source_url, is_published) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (v["title"], v["summary"], v["content"], v["image_url"], v["source_url"], v["is_published"])
                    )
                conn.commit()
            finally:
                conn.close()
            flash("Yangilik muvaffaqiyatli qo'shildi!", "success")
        except Exception:
            flash("Yangilik qo'shishda xatolik yuz berdi.", "error")
        return redirect(url_for("admin_yangiliklar"))
    return render_template("admin_yangilik_form.html", item=None, edit_mode=False)


@app.route("/admin/yangiliklar/edit/<int:id>", methods=["GET", "POST"])
@login_required
def admin_yangilik_edit(id):
    _require_admin()
    from data import get_connection

    def _load():
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, title, summary, content, image_url, source_url, is_published "
                        "FROM yangiliklar WHERE id = %s", (id,))
                    r = cur.fetchone()
                    if r:
                        return {
                            "id": r[0], "title": r[1] or "", "summary": r[2] or "",
                            "content": r[3] or "", "image_url": r[4] or "",
                            "source_url": r[5] or "", "is_published": r[6],
                        }
            finally:
                conn.close()
        except Exception:
            return None
        return None

    current = _load()
    if not current:
        abort(404)

    if request.method == "POST":
        v = _yangilik_form_values(existing_image=current.get("image_url") or None)
        if not v["title"] or not v["summary"]:
            flash("Sarlavha va qisqa matn majburiy.", "error")
            v["id"] = id
            return render_template("admin_yangilik_form.html", item=v, edit_mode=True)
        # if the stored image changed/removed and it was a local file, drop it from disk
        old_img = current.get("image_url") or ""
        if old_img and old_img != (v["image_url"] or ""):
            _delete_local_news_image(old_img)
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE yangiliklar SET title=%s, summary=%s, content=%s, "
                        "image_url=%s, source_url=%s, is_published=%s, updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=%s",
                        (v["title"], v["summary"], v["content"], v["image_url"],
                         v["source_url"], v["is_published"], id))
                conn.commit()
            finally:
                conn.close()
            flash("Yangilik yangilandi!", "success")
        except Exception:
            flash("Yangilikni yangilashda xatolik yuz berdi.", "error")
        return redirect(url_for("admin_yangiliklar"))

    return render_template("admin_yangilik_form.html", item=current, edit_mode=True)


@app.route("/admin/yangiliklar/delete/<int:id>", methods=["POST"])
@login_required
def admin_yangilik_delete(id):
    _require_admin()
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT image_url FROM yangiliklar WHERE id = %s", (id,))
                row = cur.fetchone()
                cur.execute("DELETE FROM yangiliklar WHERE id = %s", (id,))
            conn.commit()
            if row and row[0]:
                _delete_local_news_image(row[0])
        finally:
            conn.close()
        flash("Yangilik o'chirildi.", "success")
    except Exception:
        flash("O'chirishda xatolik yuz berdi.", "error")
    return redirect(url_for("admin_yangiliklar"))


@app.route("/yangiliklar")
def yangiliklar():
    from data import get_connection
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1
    per_page = 20
    offset = (page - 1) * per_page
    items = []
    total = 0
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM yangiliklar WHERE is_published = TRUE")
                total = cur.fetchone()[0] or 0
                cur.execute(
                    "SELECT id, title, summary, created_at, image_url, image_data FROM yangiliklar "
                    "WHERE is_published = TRUE ORDER BY created_at DESC "
                    "LIMIT %s OFFSET %s",
                    (per_page, offset)
                )
                items = [{
                    "id": r[0], "title": r[1] or "", "summary": r[2] or "",
                    "created_at": str(r[3])[:10] if r[3] else "",
                    "image": r[4] or r[5] or "", "is_placeholder": False,
                } for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        items, total = [], 0

    if not items and page == 1:
        items = _placeholder_news()
        total = len(items)

    total_pages = max(1, (total + per_page - 1) // per_page)
    return render_template("yangiliklar.html", items=items, page=page,
                           total_pages=total_pages, total=total)


@app.route("/yangiliklar/<int:id>")
def yangilik_detail(id):
    from data import get_connection
    item = None
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, title, content, summary, source_url, created_at, image_url, image_data "
                    "FROM yangiliklar WHERE id = %s AND is_published = TRUE",
                    (id,)
                )
                r = cur.fetchone()
                if r:
                    item = {
                        "id": r[0], "title": r[1] or "", "content": r[2] or "",
                        "summary": r[3] or "", "source_url": r[4] or "",
                        "created_at": str(r[5])[:16] if r[5] else "",
                        "image": r[6] or r[7] or "",
                    }
        finally:
            conn.close()
    except Exception:
        item = None
    if not item:
        abort(404)
    return render_template("yangilik_detail.html", item=item)


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


@app.route("/vacancies")
def vacancies():
    from data import get_connection
    items = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM vacancies WHERE is_published = TRUE "
                    "ORDER BY created_at DESC"
                )
                cols = [d[0] for d in cur.description]
                items = [_vacancy_from_row(cols, r) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        items = []
    return render_template("vacancies.html", items=items, vacancy_types=VACANCY_TYPES)


@app.route("/vacancies/<int:id>")
def vacancy_detail(id):
    from data import get_connection
    item = None
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM vacancies WHERE id = %s AND is_published = TRUE", (id,))
                row = cur.fetchone()
                if row:
                    cols = [d[0] for d in cur.description]
                    item = _vacancy_from_row(cols, row)
        finally:
            conn.close()
    except Exception:
        item = None
    if not item:
        abort(404)
    return render_template("vacancy_detail.html", item=item)


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


@app.route("/admin/vacancies")
@login_required
def admin_vacancies():
    _require_admin()
    from data import get_connection
    items = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, title, organization, vacancy_type, deadline, is_published "
                    "FROM vacancies ORDER BY created_at DESC, id DESC"
                )
                items = [{
                    "id": r[0], "title": r[1] or "", "organization": r[2] or "",
                    "vacancy_type": r[3] or "", "type_label": VACANCY_TYPE_LABELS.get(r[3], ""),
                    "deadline": str(r[4])[:10] if r[4] else "", "is_published": r[5],
                } for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        items = []
    return render_template("admin_vacancies.html", items=items)


@app.route("/admin/vacancies/add", methods=["GET", "POST"])
@login_required
def admin_vacancy_add():
    _require_admin()
    from data import get_connection
    if request.method == "POST":
        v = _vacancy_form_values()
        if not v["title"] or not v["organization"]:
            flash("Sarlavha va tashkilot majburiy.", "error")
            return render_template("admin_vacancy_form.html", item=v, edit_mode=False,
                                   vacancy_types=VACANCY_TYPES)
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO vacancies (title, organization, location, specialty, "
                        "requirements, description, salary, contact_info, contact_url, "
                        "vacancy_type, deadline, is_published) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (v["title"], v["organization"], v["location"], v["specialty"],
                         v["requirements"], v["description"], v["salary"], v["contact_info"],
                         v["contact_url"], v["vacancy_type"], v["deadline"], v["is_published"])
                    )
                conn.commit()
            finally:
                conn.close()
            flash("Vakansiya qo'shildi!", "success")
        except Exception:
            flash("Vakansiya qo'shishda xatolik yuz berdi.", "error")
        return redirect(url_for("admin_vacancies"))
    return render_template("admin_vacancy_form.html", item=None, edit_mode=False,
                           vacancy_types=VACANCY_TYPES)


@app.route("/admin/vacancies/edit/<int:id>", methods=["GET", "POST"])
@login_required
def admin_vacancy_edit(id):
    _require_admin()
    from data import get_connection

    def _load():
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM vacancies WHERE id = %s", (id,))
                    row = cur.fetchone()
                    if row:
                        return _vacancy_from_row([d[0] for d in cur.description], row)
            finally:
                conn.close()
        except Exception:
            return None
        return None

    current = _load()
    if not current:
        abort(404)

    if request.method == "POST":
        v = _vacancy_form_values()
        if not v["title"] or not v["organization"]:
            flash("Sarlavha va tashkilot majburiy.", "error")
            v["id"] = id
            return render_template("admin_vacancy_form.html", item=v, edit_mode=True,
                                   vacancy_types=VACANCY_TYPES)
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE vacancies SET title=%s, organization=%s, location=%s, "
                        "specialty=%s, requirements=%s, description=%s, salary=%s, "
                        "contact_info=%s, contact_url=%s, vacancy_type=%s, deadline=%s, "
                        "is_published=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                        (v["title"], v["organization"], v["location"], v["specialty"],
                         v["requirements"], v["description"], v["salary"], v["contact_info"],
                         v["contact_url"], v["vacancy_type"], v["deadline"], v["is_published"], id)
                    )
                conn.commit()
            finally:
                conn.close()
            flash("Vakansiya yangilandi!", "success")
        except Exception:
            flash("Vakansiyani yangilashda xatolik yuz berdi.", "error")
        return redirect(url_for("admin_vacancies"))

    return render_template("admin_vacancy_form.html", item=current, edit_mode=True,
                           vacancy_types=VACANCY_TYPES)


@app.route("/admin/vacancies/delete/<int:id>", methods=["POST"])
@login_required
def admin_vacancy_delete(id):
    _require_admin()
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM vacancies WHERE id = %s", (id,))
            conn.commit()
        finally:
            conn.close()
        flash("Vakansiya o'chirildi", "success")
    except Exception:
        flash("O'chirishda xatolik yuz berdi.", "error")
    return redirect(url_for("admin_vacancies"))


@app.route("/contact")
def contact():
    return render_template("contact.html")


BLOG_CATEGORIES = {
    "maslahat": "Maslahat", "qollanma": "Qo'llanma",
    "texnologiya": "Texnologiya", "yangilik": "Yangilik",
}


@app.route("/blog")
def blog():
    from data import get_connection
    category = (request.args.get("category") or "").strip()
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1
    per_page = 12
    offset = (page - 1) * per_page
    posts, total = [], 0
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                where = "WHERE is_published = TRUE"
                params = []
                if category in BLOG_CATEGORIES:
                    where += " AND category = %s"
                    params.append(category)
                cur.execute(f"SELECT COUNT(*) FROM blog_posts {where}", params)
                total = cur.fetchone()[0] or 0
                cur.execute(
                    f"SELECT id, title, slug, summary, category, image_url, author, views, created_at "
                    f"FROM blog_posts {where} ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
                    params + [per_page, offset])
                posts = [{
                    "id": r[0], "title": r[1] or "", "slug": r[2] or "",
                    "summary": r[3] or "", "category": r[4] or "",
                    "category_label": BLOG_CATEGORIES.get(r[4] or "", r[4] or ""),
                    "image_url": r[5] or "", "author": r[6] or "", "views": r[7] or 0,
                    "created_at": str(r[8])[:10] if r[8] else "",
                } for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        posts, total = [], 0
    total_pages = max(1, (total + per_page - 1) // per_page)
    return render_template("blog.html", posts=posts, page=page, total_pages=total_pages,
                           total=total, category=category, categories=BLOG_CATEGORIES)


@app.route("/blog/<slug>")
def blog_post(slug):
    from data import get_connection
    post = None
    related = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, title, slug, summary, content, category, image_url, author, "
                    "views, created_at FROM blog_posts WHERE slug = %s AND is_published = TRUE", (slug,))
                r = cur.fetchone()
                if r:
                    post = {
                        "id": r[0], "title": r[1] or "", "slug": r[2] or "",
                        "summary": r[3] or "", "content": r[4] or "", "category": r[5] or "",
                        "category_label": BLOG_CATEGORIES.get(r[5] or "", r[5] or ""),
                        "image_url": r[6] or "", "author": r[7] or "", "views": (r[8] or 0) + 1,
                        "created_at": str(r[9])[:10] if r[9] else "",
                    }
                    cur.execute("UPDATE blog_posts SET views = views + 1 WHERE id = %s", (r[0],))
                    conn.commit()
                    cur.execute(
                        "SELECT title, slug, summary, category FROM blog_posts "
                        "WHERE is_published = TRUE AND category = %s AND id <> %s "
                        "ORDER BY created_at DESC LIMIT 3", (post["category"], post["id"]))
                    related = [{
                        "title": rr[0], "slug": rr[1], "summary": rr[2] or "",
                        "category_label": BLOG_CATEGORIES.get(rr[3] or "", rr[3] or ""),
                    } for rr in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        post = None
    if not post:
        abort(404)
    return render_template("blog_detail.html", post=post, related=related)


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


@app.route("/admin/blog")
@login_required
def admin_blog():
    _require_admin()
    from data import get_connection
    items = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, title, slug, category, views, is_published, created_at "
                    "FROM blog_posts ORDER BY created_at DESC, id DESC")
                items = [{
                    "id": r[0], "title": r[1] or "", "slug": r[2] or "", "category": r[3] or "",
                    "category_label": BLOG_CATEGORIES.get(r[3] or "", r[3] or ""),
                    "views": r[4] or 0, "is_published": r[5],
                    "created_at": str(r[6])[:16] if r[6] else "",
                } for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        items = []
    return render_template("admin_blog.html", items=items)


@app.route("/admin/blog/add", methods=["GET", "POST"])
@login_required
def admin_blog_add():
    _require_admin()
    from data import get_connection
    if request.method == "POST":
        v = _blog_form_values()
        if not v["title"] or not v["content"]:
            flash("Sarlavha va to'liq matn majburiy.", "error")
            return render_template("admin_blog_form.html", item=v, edit_mode=False, categories=BLOG_CATEGORIES)
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO blog_posts (title, slug, summary, content, category, image_url, is_published) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (v["title"], v["slug"], v["summary"], v["content"], v["category"],
                         v["image_url"], v["is_published"]))
                conn.commit()
                flash("Maqola qo'shildi!", "success")
            finally:
                conn.close()
        except Exception:
            flash("Saqlashda xatolik (slug takrorlangan bo'lishi mumkin).", "error")
            return render_template("admin_blog_form.html", item=v, edit_mode=False, categories=BLOG_CATEGORIES)
        return redirect(url_for("admin_blog"))
    return render_template("admin_blog_form.html", item=None, edit_mode=False, categories=BLOG_CATEGORIES)


@app.route("/admin/blog/edit/<int:id>", methods=["GET", "POST"])
@login_required
def admin_blog_edit(id):
    _require_admin()
    from data import get_connection

    def _load():
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, title, slug, summary, content, category, image_url, is_published "
                        "FROM blog_posts WHERE id = %s", (id,))
                    r = cur.fetchone()
                    if r:
                        return {"id": r[0], "title": r[1] or "", "slug": r[2] or "",
                                "summary": r[3] or "", "content": r[4] or "", "category": r[5] or "",
                                "image_url": r[6] or "", "is_published": r[7]}
            finally:
                conn.close()
        except Exception:
            return None
        return None

    current = _load()
    if not current:
        abort(404)
    if request.method == "POST":
        v = _blog_form_values()
        if not v["title"] or not v["content"]:
            flash("Sarlavha va to'liq matn majburiy.", "error")
            v["id"] = id
            return render_template("admin_blog_form.html", item=v, edit_mode=True, categories=BLOG_CATEGORIES)
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE blog_posts SET title=%s, slug=%s, summary=%s, content=%s, category=%s, "
                        "image_url=%s, is_published=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                        (v["title"], v["slug"], v["summary"], v["content"], v["category"],
                         v["image_url"], v["is_published"], id))
                conn.commit()
                flash("Maqola yangilandi!", "success")
            finally:
                conn.close()
        except Exception:
            flash("Yangilashda xatolik (slug takrorlangan bo'lishi mumkin).", "error")
            v["id"] = id
            return render_template("admin_blog_form.html", item=v, edit_mode=True, categories=BLOG_CATEGORIES)
        return redirect(url_for("admin_blog"))
    return render_template("admin_blog_form.html", item=current, edit_mode=True, categories=BLOG_CATEGORIES)


@app.route("/admin/blog/delete/<int:id>", methods=["POST"])
@login_required
def admin_blog_delete(id):
    _require_admin()
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM blog_posts WHERE id = %s", (id,))
            conn.commit()
            flash("Maqola o'chirildi.", "success")
        finally:
            conn.close()
    except Exception:
        flash("O'chirishda xatolik.", "error")
    return redirect(url_for("admin_blog"))


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


@app.route('/admin/survey')
@login_required
def admin_survey():
    _require_admin()
    from data import get_connection
    total_responses = 0
    total_participants = 0
    yes_pct = 0
    questions = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM survey_responses")
                total_responses = cur.fetchone()[0] or 0
                cur.execute("SELECT COUNT(DISTINCT ip_address) FROM survey_responses")
                total_participants = cur.fetchone()[0] or 0
                cur.execute("SELECT COUNT(*) FROM survey_responses WHERE answer = 'ha'")
                yes_total = cur.fetchone()[0] or 0
                yes_pct = round(yes_total / total_responses * 100, 1) if total_responses else 0

                cur.execute("""
                    SELECT q.id, q.question_text, q.question_group, q.question_order,
                           COUNT(r.id) AS total_responses,
                           SUM(CASE WHEN r.answer = 'ha' THEN 1 ELSE 0 END) AS yes_count,
                           SUM(CASE WHEN r.answer = 'yoq' THEN 1 ELSE 0 END) AS no_count,
                           SUM(CASE WHEN r.answer = 'custom' THEN 1 ELSE 0 END) AS custom_count
                    FROM survey_questions q
                    LEFT JOIN survey_responses r ON q.id = r.question_id
                    GROUP BY q.id, q.question_text, q.question_group, q.question_order
                    ORDER BY q.question_group, q.question_order
                """)
                rows = cur.fetchall()
                for r in rows:
                    qid, qtext, qgroup = r[0], r[1], r[2]
                    total = r[4] or 0
                    yc, nc, cc = r[5] or 0, r[6] or 0, r[7] or 0
                    custom_answers = []
                    if cc:
                        cur.execute("""
                            SELECT custom_text, username, ip_address, created_at
                            FROM survey_responses
                            WHERE question_id = %s AND answer = 'custom'
                            AND custom_text IS NOT NULL AND custom_text <> ''
                            ORDER BY created_at DESC
                        """, (qid,))
                        custom_answers = [{
                            "custom_text": cr[0], "username": cr[1] or "",
                            "ip_address": cr[2] or "", "created_at": cr[3],
                        } for cr in cur.fetchall()]
                    questions.append({
                        "id": qid, "question_text": qtext, "question_group": qgroup,
                        "total_responses": total,
                        "yes_count": yc, "no_count": nc, "custom_count": cc,
                        "yes_pct": round(yc / total * 100, 1) if total else 0,
                        "no_pct": round(nc / total * 100, 1) if total else 0,
                        "custom_pct": round(cc / total * 100, 1) if total else 0,
                        "custom_answers": custom_answers,
                    })
        finally:
            conn.close()
    except Exception:
        questions = []
    # Group questions for the template
    grouped = {}
    for q in questions:
        grouped.setdefault(q["question_group"], []).append(q)
    grouped = dict(sorted(grouped.items()))
    return render_template('admin_survey.html',
        total_responses=total_responses, total_participants=total_participants,
        yes_pct=yes_pct, grouped_questions=grouped)


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


@app.route('/universities')
def universities():
    from data import get_connection
    items = []
    regions = set()
    try:
        stats = get_university_dissertation_stats()
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM universities WHERE is_active = TRUE")
                cols = [c[0] for c in cur.description]
                for r in cur.fetchall():
                    d = _uni_row_to_dict(cols, r)
                    s = stats.get(d['id'], {})
                    d['diss_count'] = s.get('total', 0)
                    d['olim_count'] = s.get('olimlar', 0)
                    if d.get('region'):
                        regions.add(d['region'])
                    items.append(d)
        finally:
            conn.close()
    except Exception:
        items = []
    items.sort(key=lambda x: -x.get('diss_count', 0))
    return render_template('universities.html', items=items,
                           regions=sorted(regions), type_labels=_UNI_TYPE_LABELS)


@app.route('/university/<path:name>')
def university_profile(name):
    from data import get_connection, clean_olim_name
    term = (name or '').strip()
    where, params = _uni_where(term)
    uni = None
    stats = {'total': 0, 'phd': 0, 'dsc': 0, 'olimlar': 0, 'ixtisosliklar': 0, 'rahbarlar': 0}
    top_olimlar, top_rahbarlar, recent, by_year, top_ixtisos = [], [], [], [], []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                uni = _find_university(cur, term)
                cur.execute(f"""
                    SELECT COUNT(*),
                           SUM(CASE WHEN daraja ILIKE '%%PhD%%' OR daraja ILIKE '%%фан%%' THEN 1 ELSE 0 END),
                           SUM(CASE WHEN daraja ILIKE '%%DSc%%' OR daraja ILIKE '%%док%%' THEN 1 ELSE 0 END),
                           COUNT(DISTINCT olim), COUNT(DISTINCT ixtisoslik), COUNT(DISTINCT ilmiy_rahbar)
                    FROM dissertations WHERE {where}
                """, params)
                r = cur.fetchone()
                if r:
                    stats = {'total': r[0] or 0, 'phd': r[1] or 0, 'dsc': r[2] or 0,
                             'olimlar': r[3] or 0, 'ixtisosliklar': r[4] or 0, 'rahbarlar': r[5] or 0}
                cur.execute(f"""
                    SELECT TRIM(olim), COUNT(*) cnt, MAX(daraja), MAX(photo_url)
                    FROM dissertations WHERE {where} AND olim IS NOT NULL AND TRIM(olim) <> ''
                    GROUP BY TRIM(olim) ORDER BY cnt DESC LIMIT 10
                """, params)
                top_olimlar = [{'name': x[0], 'display': clean_olim_name(x[0]), 'count': x[1],
                                'daraja': x[2] or '', 'photo_url': x[3] or ''} for x in cur.fetchall()]
                cur.execute(f"""
                    SELECT TRIM(ilmiy_rahbar), COUNT(*) cnt, MAX(ilmiy_rahbar_photo_url)
                    FROM dissertations WHERE {where} AND ilmiy_rahbar IS NOT NULL AND TRIM(ilmiy_rahbar) <> ''
                    GROUP BY TRIM(ilmiy_rahbar) ORDER BY cnt DESC LIMIT 10
                """, params)
                top_rahbarlar = [{'name': x[0], 'display': clean_olim_name(x[0]), 'count': x[1],
                                  'photo_url': x[2] or ''} for x in cur.fetchall()]
                cur.execute(f"""
                    SELECT id, olim, mavzu, daraja, sana FROM dissertations WHERE {where}
                    ORDER BY id DESC LIMIT 10
                """, params)
                recent = [{'id': x[0], 'olim': x[1] or '', 'display': clean_olim_name(x[1] or ''),
                           'mavzu': x[2] or '', 'daraja': x[3] or '', 'sana': x[4] or ''}
                          for x in cur.fetchall()]
                cur.execute(f"""
                    SELECT substring(sana from '(19|20)[0-9][0-9]') AS yr, COUNT(*)
                    FROM dissertations WHERE {where} AND sana ~ '(19|20)[0-9][0-9]'
                    GROUP BY yr ORDER BY yr
                """, params)
                by_year = [{'year': x[0], 'count': x[1]} for x in cur.fetchall() if x[0]]
                cur.execute(f"""
                    SELECT TRIM(ixtisoslik), COUNT(*) cnt FROM dissertations
                    WHERE {where} AND ixtisoslik IS NOT NULL AND TRIM(ixtisoslik) <> ''
                    GROUP BY TRIM(ixtisoslik) ORDER BY cnt DESC LIMIT 5
                """, params)
                top_ixtisos = [{'name': x[0], 'count': x[1]} for x in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        pass

    if not uni and stats['total'] == 0:
        abort(404)
    if not uni:
        city, region = detect_uni_city_region(term)
        uni = {'id': None, 'name': term, 'short_name': '', 'logo_url': '', 'website': '',
               'city': city, 'region': region, 'university_type': '', 'type_label': '',
               'description': '', 'founded_year': None, 'rector': '', 'address': '',
               'phone': '', 'email': '', 'telegram': ''}

    is_admin = (current_user.is_authenticated and current_user.username == 'admin')
    return render_template('university_profile.html', uni=uni, stats=stats,
                           top_olimlar=top_olimlar, top_rahbarlar=top_rahbarlar,
                           recent=recent, by_year=by_year, top_ixtisos=top_ixtisos,
                           is_admin=is_admin)


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
                    'telegram', 'student_count', 'teacher_count']


@app.route('/admin/universities')
@login_required
def admin_universities():
    _require_admin()
    from data import get_connection
    items = []
    try:
        stats = get_university_dissertation_stats()
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, university_type, city, region, logo_url, is_active "
                            "FROM universities ORDER BY name")
                for r in cur.fetchall():
                    items.append({"id": r[0], "name": r[1] or "", "university_type": r[2] or "",
                                  "type_label": _UNI_TYPE_LABELS.get(r[2], r[2] or ""),
                                  "city": r[3] or "", "region": r[4] or "", "logo_url": r[5] or "",
                                  "is_active": r[6], "diss_count": stats.get(r[0], {}).get('total', 0)})
        finally:
            conn.close()
    except Exception:
        items = []
    return render_template('admin_universities.html', items=items)


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


@app.route('/admin/university/add', methods=['GET', 'POST'])
@login_required
def admin_university_add():
    _require_admin()
    from data import get_connection
    if request.method == 'POST':
        vals = _uni_form_values()
        if not vals.get('name'):
            flash("Nomi majburiy.", "error")
            return render_template('admin_university_form.html', item=vals, edit_mode=False)
        logo = _save_university_logo()
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cols = _UNI_EDIT_FIELDS + (['logo_url'] if logo else [])
                    placeholders = ", ".join(["%s"] * len(cols))
                    args = [vals[f] for f in _UNI_EDIT_FIELDS] + ([logo] if logo else [])
                    cur.execute(
                        f"INSERT INTO universities ({', '.join(cols)}) VALUES ({placeholders}) "
                        f"ON CONFLICT (name) DO NOTHING", args)
                conn.commit()
            finally:
                conn.close()
            cache.delete('university_stats')
            flash("Universitet qo'shildi!", "success")
        except Exception:
            flash("Qo'shishda xatolik yuz berdi.", "error")
        return redirect(url_for('admin_universities'))
    return render_template('admin_university_form.html', item=None, edit_mode=False)


@app.route('/admin/university/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def admin_university_edit(id):
    _require_admin()
    from data import get_connection

    def _load():
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM universities WHERE id = %s", (id,))
                    row = cur.fetchone()
                    if row:
                        return _uni_row_to_dict([c[0] for c in cur.description], row)
            finally:
                conn.close()
        except Exception:
            return None
        return None

    current = _load()
    if not current:
        abort(404)
    if request.method == 'POST':
        vals = _uni_form_values()
        if not vals.get('name'):
            flash("Nomi majburiy.", "error")
            vals['id'] = id
            return render_template('admin_university_form.html', item=vals, edit_mode=True)
        logo = _save_university_logo()
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cols = list(_UNI_EDIT_FIELDS)
                    args = [vals[f] for f in _UNI_EDIT_FIELDS]
                    if logo:
                        cols.append('logo_url')
                        args.append(logo)
                    set_clause = ", ".join(f"{c} = %s" for c in cols) + ", updated_at = NOW()"
                    cur.execute(f"UPDATE universities SET {set_clause} WHERE id = %s", args + [id])
                conn.commit()
            finally:
                conn.close()
            cache.delete('university_stats')
            flash("Universitet yangilandi!", "success")
        except Exception:
            flash("Yangilashda xatolik yuz berdi.", "error")
        return redirect(url_for('admin_universities'))
    return render_template('admin_university_form.html', item=current, edit_mode=True)


@app.route('/admin/university/logo/<int:id>', methods=['POST'])
@login_required
def admin_university_logo(id):
    _require_admin()
    from data import get_connection
    logo = _save_university_logo()
    if not logo:
        flash("Rasm yuklanmadi (JPG/PNG/WEBP/SVG).", "error")
        return redirect(request.referrer or url_for('admin_universities'))
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE universities SET logo_url = %s, updated_at = NOW() WHERE id = %s",
                            (logo, id))
            conn.commit()
        finally:
            conn.close()
        flash("Logo yuklandi!", "success")
    except Exception:
        flash("Logo saqlashda xatolik.", "error")
    return redirect(request.referrer or url_for('admin_universities'))


@app.route('/admin/university/delete/<int:id>', methods=['POST'])
@login_required
def admin_university_delete(id):
    _require_admin()
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM universities WHERE id = %s", (id,))
            conn.commit()
        finally:
            conn.close()
        cache.delete('university_stats')
        flash("Universitet o'chirildi.", "success")
    except Exception:
        flash("O'chirishda xatolik.", "error")
    return redirect(url_for('admin_universities'))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
