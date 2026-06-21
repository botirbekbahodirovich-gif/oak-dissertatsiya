import os
from dotenv import load_dotenv
from flask_wtf.csrf import CSRFProtect
import bcrypt
from flask import Flask, render_template, redirect, url_for, jsonify, request, abort, flash
from urllib.parse import urlparse
from flask_login import (LoginManager, UserMixin, logout_user,
                         login_required, current_user)

app = Flask(__name__)
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



def is_safe_relative_url(target: str) -> bool:
    if not target:
        return False
    parsed = urlparse(target)
    return parsed.scheme == "" and parsed.netloc == "" and target.startswith("/")

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
            for _col, _typ in (
                ('first_name', 'VARCHAR(200)'), ('last_name', 'VARCHAR(200)'),
                ('patronymic', 'VARCHAR(200)'), ('title', 'VARCHAR(200)'),
                ('position', 'VARCHAR(300)'), ('institution', 'VARCHAR(500)'),
                ('birth_year', 'INTEGER'), ('orcid_url', 'VARCHAR(500)'),
                ('website_url', 'VARCHAR(500)'), ('cabinet_user_id', 'INTEGER'),
            ):
                cur.execute(f"ALTER TABLE olim_profiles ADD COLUMN IF NOT EXISTS {_col} {_typ}")
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

    return render_template("home.html", recent=recent, news=news,
                           top_supervisors=top_supervisors,
                           top_supervisors_random=top_supervisors_random,
                           top_marquee=top_marquee, total_stats=total_stats,
                           gender_pct=gender_pct,
                           active_vacancy_count=active_vacancy_count)


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
def track_visit():
    if request.endpoint and not request.path.startswith('/static') and not request.path.startswith('/api/'):
        try:
            from data import get_connection
            from flask import session
            conn = get_connection()
            cur = conn.cursor()
            user_id = None
            username = None
            try:
                if current_user.is_authenticated:
                    user_id = current_user.id
                    username = (getattr(current_user, 'username', None)
                                or getattr(current_user, 'email', None) or str(current_user.id))
                elif session.get('cabinet_user_id'):
                    user_id = session['cabinet_user_id']
                    username = session.get('cabinet_olim_name') or None
                    if not username:
                        cur.execute("SELECT olim_name, telegram_username, email "
                                    "FROM cabinet_users WHERE id = %s", (user_id,))
                        cab = cur.fetchone()
                        if cab:
                            username = cab[0] or cab[1] or cab[2]
            except Exception:
                pass
            cur.execute("""
                INSERT INTO page_visits (user_id, username, page, ip_address, user_agent)
                VALUES (%s, %s, %s, %s, %s)
            """, (user_id, username, request.path, request.remote_addr,
                  request.headers.get('User-Agent', '')[:200]))
            conn.commit()
            cur.close()
            conn.close()
        except Exception:
            pass  # never break the app if tracking fails


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

            # Most visited pages today
            cur.execute("""SELECT page, COUNT(*) AS cnt FROM page_visits
                WHERE visited_at::date = CURRENT_DATE
                GROUP BY page ORDER BY cnt DESC LIMIT 10""")
            top_pages = cur.fetchall()

            # Recent visitors (last 50)
            cur.execute("""SELECT username, page, ip_address, visited_at FROM page_visits
                ORDER BY visited_at DESC LIMIT 50""")
            recent = cur.fetchall()

            # Daily visits last 7 days
            cur.execute("""SELECT visited_at::date AS d, COUNT(*) AS cnt FROM page_visits
                WHERE visited_at > NOW() - INTERVAL '7 days'
                GROUP BY d ORDER BY d""")
            weekly = cur.fetchall()

            # Top visitors by IP
            cur.execute("""
                SELECT ip_address,
                       MAX(username) AS username,
                       MAX(user_id) AS user_id,
                       COUNT(*) AS visit_count,
                       MAX(visited_at) AS last_visit,
                       MIN(visited_at) AS first_visit,
                       COUNT(DISTINCT page) AS unique_pages
                FROM page_visits
                GROUP BY ip_address
                ORDER BY visit_count DESC
                LIMIT 20
            """)
            top_visitors = [{
                "ip": r[0] or "—",
                "username": r[1] or "", "user_id": r[2],
                "visit_count": r[3] or 0,
                "last_visit": str(r[4])[:16] if r[4] else "",
                "first_visit": str(r[5])[:16] if r[5] else "",
                "unique_pages": r[6] or 0,
            } for r in cur.fetchall()]

            # Top visitors grouped by username
            top_users = []
            try:
                cur.execute("""
                    SELECT username, MAX(user_id) AS user_id,
                           COUNT(*) AS visit_count,
                           COUNT(DISTINCT ip_address) AS device_count,
                           COUNT(DISTINCT page) AS unique_pages,
                           MAX(visited_at) AS last_visit
                    FROM page_visits
                    WHERE username IS NOT NULL AND username <> '' AND username <> 'Anonim'
                    GROUP BY username
                    ORDER BY visit_count DESC
                    LIMIT 20
                """)
                top_users = [{
                    "username": r[0] or "", "user_id": r[1], "visit_count": r[2] or 0,
                    "device_count": r[3] or 0, "unique_pages": r[4] or 0,
                    "last_visit": str(r[5])[:16] if r[5] else "",
                } for r in cur.fetchall()]
            except Exception:
                top_users = []

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
                    "created_at": str(r[5])[:16] if r[5] else "",
                    "last_login": str(r[6])[:16] if r[6] else "",
                } for r in cur.fetchall()]
            except Exception:
                registered_users = []
    finally:
        conn.close()

    return render_template('admin_analytics.html',
        today_visits=today_visits, today_unique=today_unique, online_now=online_now,
        top_pages=top_pages, recent=recent, weekly=weekly,
        top_visitors=top_visitors, top_users=top_users, registered_users=registered_users)


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
    return render_template("blog_post.html", post=post, related=related)


@app.route("/preparation")
def preparation():
    return render_template("preparation.html")


@app.route("/courses")
def courses():
    return render_template("courses.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
