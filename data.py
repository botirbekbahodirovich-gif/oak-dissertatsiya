import os
import re
import csv
import io
import html as html_module
import threading
from urllib.parse import urlparse
from dotenv import load_dotenv
load_dotenv()
import openpyxl
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')
OAK_API_KEY  = os.environ.get('OAK_API_KEY', '')
# Shared secret for the scraper → VPS import endpoint (/api/v1/import-oak).
SITE_API_KEY = os.environ.get('SITE_API_KEY', '')
from flask import (Blueprint, jsonify, request, send_file, render_template,
                   abort, redirect, Response)
from flask_login import login_required
from extensions import cache
try:
    import psycopg2
    import psycopg2.extras as psycopg2_extras
    from psycopg2 import pool as pg_pool
except Exception:
    psycopg2 = None
    psycopg2_extras = None
    pg_pool = None

REQUIRED_COLUMNS = {
    "Sana", "Daraja", "Olim", "Mavzu",
    "Ixtisoslik", "Muassasa", "Ilmiy_rahbar", "Link"
}

SORTABLE_COLUMNS = {"Sana", "Daraja", "Olim", "Mavzu", "Ixtisoslik", "Muassasa", "Ilmiy_rahbar", "id"}


def get_database_url():
    url = os.environ.get('DATABASE_URL', '')
    if not url or url.startswith('sqlite'):
        url = os.environ.get('POSTGRES_URL', '')
    return url


def _is_local_or_socket(url):
    """True for localhost or Cloud SQL unix-socket DSNs, which must NOT get SSL."""
    if '/cloudsql/' in url or 'host=/' in url:
        return True
    try:
        host = urlparse(url).hostname or ''
    except Exception:
        host = ''
    return host in ('', 'localhost', '127.0.0.1', '::1')


def get_normalized_db_url():
    """DATABASE_URL with sslmode=require enforced for remote managed Postgres
    (Neon/Supabase/Cloud SQL public IP) when the DSN omits it — a common cause of
    'pages not loading' after a host migration."""
    url = get_database_url()
    if url and 'sslmode=' not in url and not _is_local_or_socket(url):
        url += ('&' if '?' in url else '?') + 'sslmode=require'
    return url


# Resilience against idle-connection drops by managed Postgres / Neon pooler.
_CONNECT_KWARGS = dict(
    connect_timeout=10,
    keepalives=1,
    keepalives_idle=30,
    keepalives_interval=10,
    keepalives_count=5,
)


# ── Connection pool ───────────────────────────────────────────────────────────

_db_pool: 'pg_pool.ThreadedConnectionPool | None' = None
_db_pool_lock = threading.Lock()


def _get_pool():
    global _db_pool
    if _db_pool is None:
        with _db_pool_lock:
            if _db_pool is None and pg_pool is not None:
                url = get_normalized_db_url()
                if url:
                    try:
                        _db_pool = pg_pool.ThreadedConnectionPool(
                            2, 10, url, **_CONNECT_KWARGS)
                    except Exception:
                        # Fall back to per-request direct connections so a transient
                        # pool-init failure doesn't permanently break the app.
                        _db_pool = None
    return _db_pool


class _PooledConn:
    """Wraps a psycopg2 connection so that close() returns it to the pool."""
    __slots__ = ('_conn', '_pool')

    def __init__(self, conn, pool):
        object.__setattr__(self, '_conn', conn)
        object.__setattr__(self, '_pool', pool)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, '_conn'), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, '_conn'), name, value)

    def cursor(self, *args, **kwargs):
        return object.__getattribute__(self, '_conn').cursor(*args, **kwargs)

    def commit(self):
        return object.__getattribute__(self, '_conn').commit()

    def rollback(self):
        return object.__getattribute__(self, '_conn').rollback()

    def close(self):
        pool = object.__getattribute__(self, '_pool')
        conn = object.__getattribute__(self, '_conn')
        pool.putconn(conn)


def get_connection():
    if not psycopg2:
        raise RuntimeError('psycopg2 is required for PostgreSQL support.')
    p = _get_pool()
    if p:
        conn = p.getconn()
        # Drop a connection the server already closed (idle timeout) and retry once.
        if getattr(conn, 'closed', 0):
            try:
                p.putconn(conn, close=True)
            except Exception:
                pass
            conn = p.getconn()
        return _PooledConn(conn, p)
    return psycopg2.connect(get_normalized_db_url(), **_CONNECT_KWARGS)


def get_supervisor_counts() -> dict:
    """Returns {trimmed_name: count} for all supervisors. Cached 15 min."""
    key = 'supervisor_counts'
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT TRIM(ilmiy_rahbar), COUNT(*)
                    FROM dissertations
                    WHERE ilmiy_rahbar IS NOT NULL AND TRIM(ilmiy_rahbar) != ''
                    GROUP BY TRIM(ilmiy_rahbar)
                """)
                result = {row[0]: row[1] for row in cur.fetchall()}
        finally:
            conn.close()
    except Exception:
        result = {}
    cache.set(key, result, timeout=900)
    return result


def clean_olim_name(name: str) -> str:
    """Normalize whitespace/newlines; keep up to 3 words; truncate at 35 chars."""
    if not name:
        return ''
    clean = ' '.join(name.split(',')[0].split())
    words = clean.split()
    result = ' '.join(words[:3])
    return result[:35] + '…' if len(result) > 35 else result


def normalize_row(row):
    if row is None:
        return None
    oak_id = str(row.get("oak_id") or "").strip()
    link = str(row.get("Link") or "").strip()
    if not link and oak_id:
        link = f"https://oak.uz/pages/{oak_id}"
    olim = str(row.get("Olim") or "").strip()
    return {
        "id": row.get("id"),
        "oak_id": oak_id,
        "Sana": str(row.get("Sana") or "").strip(),
        "Daraja": str(row.get("Daraja") or "").strip(),
        "Olim": olim,
        "Olim_short": clean_olim_name(olim),
        "Mavzu": str(row.get("Mavzu") or "").strip(),
        "Ixtisoslik": str(row.get("Ixtisoslik") or "").strip(),
        "Muassasa": str(row.get("Muassasa") or "").strip(),
        "Ilmiy_rahbar": ' '.join(str(row.get("Ilmiy_rahbar") or "").split()),
        "Ilmiy_rahbar_short": clean_olim_name(' '.join(str(row.get("Ilmiy_rahbar") or "").split())),
        "Link": link,
        "supervisor_count": get_supervisor_counts().get(
            ' '.join(str(row.get("Ilmiy_rahbar") or "").split()), 1),
    }


def _query_rows(sql, params=None):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2_extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params or ()))
            return [normalize_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _query_scalar(sql, params=None):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params or ()))
            value = cur.fetchone()
            return value[0] if value else None
    finally:
        conn.close()


def latin_to_cyrillic(text):
    if not text:
        return text
    result = text
    multi = [
        ("o'", "ў"), ("O'", "Ў"), ("g'", "ғ"), ("G'", "Ғ"),
        ("ch", "ч"), ("Ch", "Ч"), ("CH", "Ч"),
        ("sh", "ш"), ("Sh", "Ш"), ("SH", "Ш"),
        ("ng", "нг"), ("Ng", "Нг"), ("NG", "Нг"),
    ]
    for lat, cyr in multi:
        result = result.replace(lat, cyr)
    single = {
        'a':'а','b':'б','d':'д','e':'е','f':'ф','g':'г','h':'ҳ',
        'i':'и','j':'ж','k':'к','l':'л','m':'м','n':'н','o':'о',
        'p':'п','q':'қ','r':'р','s':'с','t':'т','u':'у','v':'в',
        'w':'в','x':'х','y':'й','z':'з',
        'A':'А','B':'Б','D':'Д','E':'Е','F':'Ф','G':'Г','H':'Ҳ',
        'I':'И','J':'Ж','K':'К','L':'Л','M':'М','N':'Н','O':'О',
        'P':'П','Q':'Қ','R':'Р','S':'С','T':'Т','U':'У','V':'В',
        'W':'В','X':'Х','Y':'Й','Z':'З',
    }
    for lat, cyr in single.items():
        result = result.replace(lat, cyr)
    return result


# Name-ending patterns for the (approximate) gender filter on the free-form `olim` field.
_FEMALE_LIKE = ['%овна', '%евна', '%ёвна', '%қизи', '%qizi',
                '%ова', '%ева', '%ёва', '%ова %', '%ева %', '%ёва %',
                '%ская', '%цкая', '%ская %', '%цкая %']
_MALE_LIKE = ['%ович', '%евич', '%ёвич', "%ўғли", "%o'g'li", '%угли', '%уғли',
              '%ов', '%ев', '%ёв', '%ов %', '%ев %', '%ёв %',
              '%ский', '%цкий', '%ский %', '%цкий %']


def _build_filter_clause(search, daraja, muassasa, ixtisoslik,
                         fan_tarmoqi='', ilmiy_kengash='', sana_yil='', scope='all', gender=''):
    search       = (search       or '').strip()
    daraja       = (daraja       or '').strip()
    muassasa     = (muassasa     or '').strip()
    ixtisoslik   = (ixtisoslik   or '').strip()
    fan_tarmoqi  = (fan_tarmoqi  or '').strip()
    ilmiy_kengash= (ilmiy_kengash or '').strip()
    sana_yil     = (sana_yil     or '').strip()
    scope        = (scope        or 'all').strip()
    clauses = []
    params  = []
    if search and len(search) >= 2:
        # Pre-lowercase both variants so GIN trigram index on LOWER(TRIM(field)) is used
        sl = f"%{search.lower()}%"
        sc = f"%{latin_to_cyrillic(search).lower()}%"
        if scope == 'olim':
            clauses.append(
                "(LOWER(TRIM(olim)) LIKE %s OR LOWER(TRIM(olim)) LIKE %s)"
            )
            params.extend([sl, sc])
        elif scope == 'rahbar':
            clauses.append(
                "(LOWER(TRIM(ilmiy_rahbar)) LIKE %s OR LOWER(TRIM(ilmiy_rahbar)) LIKE %s)"
            )
            params.extend([sl, sc])
        elif scope == 'opponent':
            clauses.append(
                "(LOWER(TRIM(COALESCE(opponent_1,''))) LIKE %s OR LOWER(TRIM(COALESCE(opponent_1,''))) LIKE %s OR "
                "LOWER(TRIM(COALESCE(opponent_2,''))) LIKE %s OR LOWER(TRIM(COALESCE(opponent_2,''))) LIKE %s OR "
                "LOWER(TRIM(COALESCE(opponent_3,''))) LIKE %s OR LOWER(TRIM(COALESCE(opponent_3,''))) LIKE %s)"
            )
            params.extend([sl, sc, sl, sc, sl, sc])
        elif scope == 'mavzu':
            clauses.append(
                "(LOWER(TRIM(mavzu)) LIKE %s OR LOWER(TRIM(mavzu)) LIKE %s OR "
                "LOWER(TRIM(ixtisoslik)) LIKE %s OR LOWER(TRIM(ixtisoslik)) LIKE %s OR "
                "LOWER(TRIM(COALESCE(ixtisoslik_nomi,''))) LIKE %s OR LOWER(TRIM(COALESCE(ixtisoslik_nomi,''))) LIKE %s)"
            )
            params.extend([sl, sc, sl, sc, sl, sc])
        else:  # all
            clauses.append(
                "(LOWER(TRIM(olim)) LIKE %s OR LOWER(TRIM(olim)) LIKE %s OR "
                "LOWER(TRIM(mavzu)) LIKE %s OR LOWER(TRIM(mavzu)) LIKE %s OR "
                "LOWER(TRIM(ilmiy_rahbar)) LIKE %s OR LOWER(TRIM(ilmiy_rahbar)) LIKE %s OR "
                "LOWER(TRIM(muassasa)) LIKE %s OR LOWER(TRIM(muassasa)) LIKE %s OR "
                "LOWER(TRIM(ixtisoslik)) LIKE %s OR LOWER(TRIM(ixtisoslik)) LIKE %s OR "
                "LOWER(TRIM(COALESCE(ixtisoslik_nomi,''))) LIKE %s OR LOWER(TRIM(COALESCE(ixtisoslik_nomi,''))) LIKE %s OR "
                "LOWER(TRIM(COALESCE(opponent_1,''))) LIKE %s OR LOWER(TRIM(COALESCE(opponent_1,''))) LIKE %s OR "
                "LOWER(TRIM(COALESCE(opponent_2,''))) LIKE %s OR LOWER(TRIM(COALESCE(opponent_2,''))) LIKE %s OR "
                "LOWER(TRIM(COALESCE(opponent_3,''))) LIKE %s OR LOWER(TRIM(COALESCE(opponent_3,''))) LIKE %s)"
            )
            params.extend([sl, sc, sl, sc, sl, sc, sl, sc, sl, sc, sl, sc, sl, sc, sl, sc, sl, sc])
    elif search:
        clauses.append("FALSE")
    if daraja:
        clauses.append("UPPER(TRIM(daraja)) = UPPER(%s)")
        params.append(daraja)
    if muassasa:
        clauses.append("TRIM(muassasa) ILIKE %s")
        params.append(muassasa)
    if ixtisoslik:
        # CONTAINS match — the field may hold combined codes ("01.01.01 05.01.07")
        clauses.append("ixtisoslik ILIKE %s")
        params.append(f"%{ixtisoslik}%")
    if fan_tarmoqi:
        clauses.append("TRIM(COALESCE(fan_tarmoqi,'')) = %s")
        params.append(fan_tarmoqi)
    if ilmiy_kengash:
        clauses.append("TRIM(COALESCE(ilmiy_kengash,'')) ILIKE %s")
        params.append(ilmiy_kengash)
    if sana_yil:
        clauses.append("sana LIKE %s")
        params.append(f"%{sana_yil}%")
    gender = (gender or '').strip().lower()
    if gender in ('male', 'female'):
        pats = _MALE_LIKE if gender == 'male' else _FEMALE_LIKE
        clauses.append("(" + " OR ".join(["LOWER(TRIM(olim)) LIKE %s"] * len(pats)) + ")")
        params.extend(pats)
    clause = " WHERE " + " AND ".join(clauses) if clauses else ""
    return clause, params


def load_data():
    sql = (
        'SELECT id, oak_id, sana AS "Sana", daraja AS "Daraja", olim AS "Olim", '
        'mavzu AS "Mavzu", ixtisoslik AS "Ixtisoslik", muassasa AS "Muassasa", '
        'ilmiy_rahbar AS "Ilmiy_rahbar", link AS "Link" '
        'FROM dissertations ORDER BY id'
    )
    return _query_rows(sql)


def count_dissertations(search, daraja, muassasa, ixtisoslik,
                        fan_tarmoqi='', ilmiy_kengash='', sana_yil='', scope='all', gender=''):
    clause, params = _build_filter_clause(
        search, daraja, muassasa, ixtisoslik, fan_tarmoqi, ilmiy_kengash, sana_yil, scope, gender)
    sql = 'SELECT COUNT(*) FROM dissertations' + clause
    return _query_scalar(sql, params) or 0


# Chronological newest→oldest ordering for the free-form DD.MM.YYYY `sana` text column.
# Rewrites "DD.MM.YYYY" → "YYYYMMDD" (sortable); unparseable values sort last.
_SANA_ORDER_DESC = (
    r"NULLIF(regexp_replace(TRIM(d.sana), '^(\d{2})\.(\d{2})\.(\d{4})$', '\3\2\1'), TRIM(d.sana)) "
    "DESC NULLS LAST, d.id DESC"
)


def query_dissertations(search, daraja, muassasa, ixtisoslik, sort_by=None, sort_dir=None,
                        page=None, per_page=None,
                        fan_tarmoqi='', ilmiy_kengash='', sana_yil='', scope='all', gender=''):
    clause, params = _build_filter_clause(
        search, daraja, muassasa, ixtisoslik, fan_tarmoqi, ilmiy_kengash, sana_yil, scope, gender)
    # Default sort: newest → oldest. `sana` is free-form text in DD.MM.YYYY form, so a plain
    # string sort is NOT chronological — convert DD.MM.YYYY → YYYYMMDD before ordering.
    pagination_clause = ''
    if page is not None and per_page is not None:
        try:
            page = max(1, int(page))
        except (TypeError, ValueError):
            page = 1
        try:
            per_page = max(1, int(per_page))
        except (TypeError, ValueError):
            per_page = 25
        pagination_clause = ' LIMIT %s OFFSET %s'
        params = params + [per_page, (page - 1) * per_page]
    sql = (
        'SELECT d.id, d.oak_id, d.sana AS "Sana", d.daraja AS "Daraja", d.olim AS "Olim", '
        'd.mavzu AS "Mavzu", d.ixtisoslik AS "Ixtisoslik", d.muassasa AS "Muassasa", '
        'd.ilmiy_rahbar AS "Ilmiy_rahbar", d.link AS "Link" '
        f'FROM dissertations d{clause} ORDER BY {_SANA_ORDER_DESC}' + pagination_clause
    )
    return _query_rows(sql, params)


def split_ixtisoslik(ixtisoslik_str):
    """Split a combined specialty field into individual codes.
    "01.01.01 05.01.07" / "13.00.02, 13.00.01" / "05.01.01;05.06.01" -> ["01.01.01", ...]"""
    if not ixtisoslik_str or not str(ixtisoslik_str).strip():
        return []
    codes = re.findall(r'\d{2}\.\d{2}\.\d{2}', str(ixtisoslik_str))
    seen, unique = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique if unique else [str(ixtisoslik_str).strip()]


def _all_individual_ixtisoslik(cache_key):
    """Set of every individual specialty code across all dissertations."""
    codes = set()
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT ixtisoslik FROM dissertations "
                    "WHERE ixtisoslik IS NOT NULL AND TRIM(ixtisoslik) <> ''")
                for (val,) in cur.fetchall():
                    for c in split_ixtisoslik(val):
                        codes.add(c)
        finally:
            conn.close()
    except Exception:
        pass
    return codes


def list_individual_ixtisosliklar():
    """Sorted list of individual specialty codes (cached 10 min). For filter dropdowns."""
    key = 'ixtisoslik_individual_list'
    cached = cache.get(key)
    if cached is not None:
        return cached
    result = sorted(_all_individual_ixtisoslik(key))
    cache.set(key, result, timeout=600)
    return result


def count_distinct_ixtisosliklar():
    """Count of distinct individual specialty codes (cached 10 min)."""
    key = 'ixtisoslik_individual_count'
    cached = cache.get(key)
    if cached is not None:
        return cached
    result = len(_all_individual_ixtisoslik(key))
    cache.set(key, result, timeout=600)
    return result


_DISTINCT_LIMITS = {
    "daraja": 20, "ixtisoslik": 500, "fan_tarmoqi": 100,
    "muassasa": 500, "ilmiy_kengash": 200,
}

def _distinct_values(column, limit=None):
    if column not in _DISTINCT_LIMITS:
        return []
    if column == "ixtisoslik":
        # split combined codes so the dropdown lists each specialty individually
        return list_individual_ixtisosliklar()
    lim = limit if limit is not None else _DISTINCT_LIMITS[column]
    # ixtisoslik must list every unique code — no limit.
    limit_clause = "" if (column == "ixtisoslik" or not lim) else f" LIMIT {int(lim)}"
    sql = (
        f"SELECT DISTINCT TRIM({column}) AS val FROM dissertations "
        f"WHERE {column} IS NOT NULL AND TRIM({column}) <> '' "
        f"ORDER BY val{limit_clause}"
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [row[0] for row in cur.fetchall() if row[0] is not None]
    finally:
        conn.close()


def _distinct_years():
    # Extract a 4-digit year from anywhere in the (free-form text) sana value.
    sql = r"""
        SELECT DISTINCT (regexp_match(TRIM(sana), '(19|20)\d{2}'))[1] AS yr
        FROM dissertations
        WHERE sana IS NOT NULL AND TRIM(sana) ~ '(19|20)\d{2}'
        ORDER BY yr DESC
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [row[0] for row in cur.fetchall() if row[0]]
    finally:
        conn.close()


def get_dissertation_by_id(dissertation_id):
    sql = (
        'SELECT id, oak_id, sana AS "Sana", daraja AS "Daraja", olim AS "Olim", '
        'mavzu AS "Mavzu", ixtisoslik AS "Ixtisoslik", muassasa AS "Muassasa", '
        'ilmiy_rahbar AS "Ilmiy_rahbar", link AS "Link" '
        'FROM dissertations WHERE id = %s'
    )
    rows = _query_rows(sql, (dissertation_id,))
    return rows[0] if rows else None


def get_dissertation_detail_by_id(dissertation_id):
    sql = '''
        SELECT
            id,
            sana AS "Sana", daraja AS "Daraja", olim AS "Olim",
            mavzu AS "Mavzu", ixtisoslik AS "Ixtisoslik", muassasa AS "Muassasa",
            ilmiy_rahbar AS "Ilmiy_rahbar", link AS "Link",
            oak_id AS "Oak_id",
            COALESCE(ixtisoslik_nomi, '') AS "Ixtisoslik_nomi",
            COALESCE(mavzu_raqami, '') AS "Mavzu_raqami",
            COALESCE(ilmiy_rahbar_daraja, '') AS "Ilmiy_rahbar_daraja",
            COALESCE(ilmiy_kengash, '') AS "Ilmiy_kengash",
            COALESCE(ilmiy_kengash_raqami, '') AS "Ilmiy_kengash_raqami",
            COALESCE(opponent_1, '') AS "Opponent_1",
            COALESCE(opponent_2, '') AS "Opponent_2",
            COALESCE(opponent_3, '') AS "Opponent_3",
            COALESCE(yetakchi_tashkilot, '') AS "Yetakchi_tashkilot",
            COALESCE(fan_tarmoqi, '') AS "Fan_tarmoqi",
            COALESCE(yonalish, '') AS "Yonalish"
        FROM dissertations WHERE id = %s
    '''
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2_extras.RealDictCursor) as cur:
            cur.execute(sql, (dissertation_id,))
            row = cur.fetchone()
            if row is None:
                return None
            return {k: (str(v).strip() if v is not None else '') for k, v in row.items()}
    finally:
        conn.close()


def get_dissertations_by_field(field_name, field_value):
    valid_columns = {
        "Olim": "olim",
        "Ilmiy_rahbar": "ilmiy_rahbar",
        "Muassasa": "muassasa",
        "Ixtisoslik": "ixtisoslik"
    }
    column = valid_columns.get(field_name)
    if not column:
        return []
    sql = (
        'SELECT id, oak_id, sana AS "Sana", daraja AS "Daraja", olim AS "Olim", '
        'mavzu AS "Mavzu", ixtisoslik AS "Ixtisoslik", muassasa AS "Muassasa", '
        'ilmiy_rahbar AS "Ilmiy_rahbar", link AS "Link" '
        f'FROM dissertations WHERE TRIM({column}) = TRIM(%s) ORDER BY id DESC'
    )
    return _query_rows(sql, (field_value,))


def apply_filters(rows, search, daraja, muassasa, ixtisoslik):
    if search:
        lo = search.lower()
        rows = [
            row for row in rows
            if any(lo in str(row.get(col, "")).lower() for col in [
                "Sana", "Daraja", "Olim", "Mavzu",
                "Ixtisoslik", "Muassasa", "Ilmiy_rahbar", "Link"
            ])
        ]
    if daraja:
        rows = [row for row in rows if str(row.get("Daraja", "")).lower() == daraja.lower()]
    if muassasa:
        rows = [row for row in rows if str(row.get("Muassasa", "")).lower() == muassasa.lower()]
    if ixtisoslik:
        rows = [row for row in rows if str(row.get("Ixtisoslik", "")).lower() == ixtisoslik.lower()]
    return rows


def apply_sort(rows, sort_by, sort_dir):
    if not sort_by or sort_by not in SORTABLE_COLUMNS:
        return rows
    reverse = (sort_dir or "asc").lower() == "desc"
    if sort_by == "id":
        return sorted(rows, key=lambda row: int(row.get("id") or 0), reverse=reverse)
    return sorted(rows, key=lambda row: str(row.get(sort_by, "")).lower(), reverse=reverse)


def _prepare_rows(rows):
    return [row for row in rows]


data_bp = Blueprint('data', __name__)


# ── Freemium gate ──────────────────────────────────────────────────────────
# Mehmonlar (guest) faqat filtrsiz BIRINCHI sahifani ko'radi. Sahifa > 1 yoki
# istalgan filter/sort (yoki 25 dan katta per_page) bo'lsa — 401 login_required.
GATE_MESSAGE = "Ko'proq natijalarni ko'rish uchun ro'yxatdan o'ting"


def _is_guest():
    from flask_login import current_user
    return not getattr(current_user, 'is_authenticated', False)


def _guest_gate_response():
    """401 JSON — acquisition overlay uchun haqiqiy (global) jami sonni beradi."""
    try:
        total = count_dissertations('', '', '', '')
    except Exception:
        total = 0
    resp = jsonify({'error': 'login_required', 'total': total, 'message': GATE_MESSAGE})
    resp.status_code = 401
    return resp


def _data_cache_key():
    # Auth holatini kalitga qo'shamiz — aks holda mehmonga qaytgan 401 javob
    # keshdan ro'yxatdan o'tgan foydalanuvchiga (yoki teskarisi) sizib chiqardi.
    from flask_login import current_user
    auth = 'u' if getattr(current_user, 'is_authenticated', False) else 'g'
    a = request.args
    return (
        f"data_{auth}_{a.get('page',1)}_{a.get('per_page',25)}"
        f"_{a.get('search','')}_{a.get('daraja','')}_{a.get('muassasa','')}_{a.get('ixtisoslik','')}"
        f"_{a.get('fan_tarmoqi','')}_{a.get('ilmiy_kengash','')}_{a.get('sana_yil','')}"
        f"_{a.get('scope','all')}_{a.get('gender','')}"
    )


@data_bp.route('/data')
@cache.cached(timeout=120, make_cache_key=_data_cache_key)
def data():
    a = request.args
    search        = a.get("search",        "").strip()
    daraja        = a.get("daraja",        "").strip()
    muassasa      = a.get("muassasa",      "").strip()
    ixtisoslik    = a.get("ixtisoslik",    "").strip()
    fan_tarmoqi   = a.get("fan_tarmoqi",   "").strip()
    ilmiy_kengash = a.get("ilmiy_kengash", "").strip()
    sana_yil      = a.get("sana_yil",      "").strip()
    gender        = a.get("gender",        "").strip().lower()
    if gender not in ('male', 'female'):
        gender = ''
    try:
        page = int(a.get("page", 1))
    except ValueError:
        page = 1
    try:
        per_page = int(a.get("per_page", 25))
    except ValueError:
        per_page = 25

    scope    = a.get("scope",    "all").strip()
    if scope not in ('all', 'olim', 'rahbar', 'opponent', 'mavzu'):
        scope = 'all'

    # Freemium gate — mehmonlarga faqat filtrsiz 1-sahifa ochiq.
    if _is_guest():
        has_filter = bool(search or daraja or muassasa or ixtisoslik or fan_tarmoqi
                          or ilmiy_kengash or sana_yil or gender) or scope != 'all' \
            or bool(a.get('sort_by') or a.get('sort_dir') or a.get('sort'))
        if page > 1 or has_filter or per_page > 25:
            return _guest_gate_response()

    total = count_dissertations(search, daraja, muassasa, ixtisoslik,
                                fan_tarmoqi, ilmiy_kengash, sana_yil, scope, gender)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    rows = query_dissertations(
        search, daraja, muassasa, ixtisoslik,
        page=page, per_page=per_page,
        fan_tarmoqi=fan_tarmoqi, ilmiy_kengash=ilmiy_kengash,
        sana_yil=sana_yil, scope=scope, gender=gender
    )

    return jsonify({
        "records": rows,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages
    })


@data_bp.route('/dissertatsiyalar')
def dissertatsiyalar_page():
    """Mehmonlar (guest) uchun OCHIQ dissertatsiyalar ro'yxati sahifasi.
    /dashboard himoyalangan (app.py require_registration mehmonni /register ga
    yo'naltiradi), shuning uchun aynan shu UI ochiq manzilda beriladi. Freemium
    gate (dashboard.html + /api/dashboard/search) mehmonga faqat filtrsiz
    1-sahifani ko'rsatadi, qolganiga modal chiqaradi."""
    return render_template('dashboard.html')


@data_bp.route('/filters')
@cache.cached(timeout=600, key_prefix='filters')
def filters():
    return jsonify({
        "daraja":        ['PhD', 'DSc'],
        "ixtisoslik":    _distinct_values("ixtisoslik"),
        "fan_tarmoqi":   _distinct_values("fan_tarmoqi"),
        "muassasa":      _distinct_values("muassasa"),
        "ilmiy_kengash": _distinct_values("ilmiy_kengash"),
        "yillar":        _distinct_years(),
    })


# ═════════════════════════════════════════════════════════════════════════
#  Dashboard v2 — faceted search (ProQuest/Scopus pattern)
#  Bitta so'rov: rows + facet counts + yil histogrammasi. Facetlar ham,
#  qatorlar ham BITTA where-quruvchidan o'tadi (drift yo'q). Qidiruv qismi
#  mavjud _build_filter_clause ga delegatsiya qilinadi (translit saqlangan).
# ═════════════════════════════════════════════════════════════════════════

_bookmarks_ready = False
_export_last = {}   # user_id -> ts (per-worker rate limit, 30s)

_YEAR_EXPR = r"(regexp_match(TRIM(d.sana), '(19|20)\d{2}'))[1]"


def _ensure_dashboard_schema(cur):
    global _bookmarks_ready
    if _bookmarks_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_bookmarks (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            dissertation_id INTEGER NOT NULL REFERENCES dissertations(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, dissertation_id)
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_bookmarks_user "
                "ON user_bookmarks(user_id, created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_dissertations_fan_tarmoqi "
                "ON dissertations(fan_tarmoqi)")
    _bookmarks_ready = True


def _csvlist(s):
    return [x.strip() for x in (s or '').split(',') if x.strip()]


def _dashboard_filters(a):
    """URL query → filter dict. Eski nomlar (search/daraja/ixtisoslik/sana_yil)
    HAM qabul qilinadi — mavjud havolalar buzilmaydi."""
    from flask_login import current_user
    scope = a.get('scope', 'all').strip()
    if scope not in ('all', 'olim', 'rahbar', 'opponent', 'mavzu'):
        scope = 'all'
    gender = a.get('gender', '').strip().lower()
    if gender not in ('male', 'female'):
        gender = ''
    daraja = [d for d in _csvlist(a.get('daraja')) if d.upper() in ('PHD', 'DSC')]
    # yil: "2020-2024" birlashgan yoki yil_min/yil_max; legacy sana_yil (bitta yil)
    yil_min = yil_max = None
    yil = (a.get('yil') or '').strip()
    m = re.match(r'^(\d{4})\s*-\s*(\d{4})$', yil)
    if m:
        yil_min, yil_max = int(m.group(1)), int(m.group(2))
    elif re.match(r'^\d{4}$', yil):
        yil_min = yil_max = int(yil)
    for key, var in (('yil_min', 'yil_min'), ('yil_max', 'yil_max')):
        v = (a.get(key) or '').strip()
        if re.match(r'^\d{4}$', v):
            if key == 'yil_min':
                yil_min = int(v)
            else:
                yil_max = int(v)
    sana_yil = (a.get('sana_yil') or '').strip()
    if not yil_min and re.match(r'^\d{4}$', sana_yil):
        yil_min = yil_max = int(sana_yil)
    if yil_min and yil_max and yil_min > yil_max:
        yil_min, yil_max = yil_max, yil_min
    saved = a.get('saved') == '1' and getattr(current_user, 'is_authenticated', False)
    return {
        'search': (a.get('q') or a.get('search') or '').strip(),
        'scope': scope, 'gender': gender,
        'daraja': daraja,
        'fan': _csvlist(a.get('fan') or a.get('fan_tarmoqi'))[:10],
        'muassasa': _csvlist(a.get('muassasa'))[:10],
        'ixt': [c for c in _csvlist(a.get('ixt') or a.get('ixtisoslik'))][:10],
        'yil_min': yil_min, 'yil_max': yil_max,
        'saved': saved,
        'sort': a.get('sort') if a.get('sort') in ('sana_desc', 'sana_asc', 'olim_az') else 'sana_desc',
    }


def _dashboard_where(f, exclude=None, user_id=None):
    """WHERE quruvchi — rows, count, har bir facet va histogram uchun yagona
    manba. `exclude` — facet o'z tanlovini hisobga olmasligi uchun (standart
    faceting xulqi)."""
    base_clause, params = _build_filter_clause(
        f['search'], '', '', '', scope=f['scope'], gender=f['gender'])
    clauses = []
    if base_clause:
        clauses.append(base_clause[len(' WHERE '):])
    if exclude != 'daraja' and f['daraja']:
        clauses.append("UPPER(TRIM(d.daraja)) = ANY(%s)")
        params.append([d.upper() for d in f['daraja']])
    if exclude != 'fan' and f['fan']:
        clauses.append("TRIM(COALESCE(d.fan_tarmoqi, '')) = ANY(%s)")
        params.append(f['fan'])
    if exclude != 'muassasa' and f['muassasa']:
        # Facet values are canonical institution names (grouped via
        # institution_map). Match a raw variant directly OR any raw variant that
        # maps to a selected canonical, so merges/renames filter as one unit.
        # The direct match keeps old links (raw muassasa values) working.
        clauses.append(
            "(TRIM(d.muassasa) = ANY(%s) OR TRIM(d.muassasa) IN ("
            "SELECT cyrillic_name FROM institution_map "
            "WHERE COALESCE(canonical_name, cyrillic_name) = ANY(%s) AND is_active = TRUE))")
        params.append(f['muassasa'])
        params.append(f['muassasa'])
    if exclude != 'ixt' and f['ixt']:
        ors = []
        for c in f['ixt']:
            ors.append("d.ixtisoslik ILIKE %s")
            params.append(f"%{c}%")
        clauses.append("(" + " OR ".join(ors) + ")")
    if exclude != 'yil' and (f['yil_min'] or f['yil_max']):
        # sana erkin matn (DD.MM.YYYY) — yil regexp bilan ajratiladi. 27.7k
        # qatordagi filtrlangan to'plamda bu arzon (btree LIKE '%..%' ni
        # baribir ishlatmaydi — joriy sana_yil filtri bilan bir xil xulq).
        lo = f['yil_min'] or 1900
        hi = f['yil_max'] or 2100
        clauses.append(r"d.sana ~ '(19|20)\d{2}' AND "
                       f"({_YEAR_EXPR})::int BETWEEN %s AND %s")
        params.extend([lo, hi])
    if f['saved'] and user_id:
        clauses.append("d.id IN (SELECT dissertation_id FROM user_bookmarks WHERE user_id = %s)")
        params.append(user_id)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


_DASH_SORTS = {
    'sana_desc': _SANA_ORDER_DESC,           # joriy standart (yangidan eskiga)
    'sana_asc': (r"NULLIF(regexp_replace(TRIM(d.sana), "
                 r"'^(\d{2})\.(\d{2})\.(\d{4})$', '\3\2\1'), TRIM(d.sana)) "
                 "ASC NULLS LAST, d.id ASC"),
    'olim_az': "LOWER(TRIM(d.olim)) ASC, d.id ASC",
}


def _dashboard_facets(cur, f, user_id):
    """3 ta GROUP BY + 1 histogram — har biri o'z faceti chiqarilgan WHERE bilan."""
    facets = {}
    w, p = _dashboard_where(f, exclude='daraja', user_id=user_id)
    cur.execute(f"SELECT UPPER(TRIM(d.daraja)), COUNT(*) FROM dissertations d{w} "
                f"GROUP BY 1 ORDER BY 2 DESC LIMIT 10", p)
    facets['daraja'] = [{'value': r[0], 'count': r[1]} for r in cur.fetchall()
                        if r[0] in ('PHD', 'DSC')]
    w, p = _dashboard_where(f, exclude='fan', user_id=user_id)
    cur.execute(f"SELECT TRIM(d.fan_tarmoqi), COUNT(*) FROM dissertations d{w} "
                f"AND d.fan_tarmoqi IS NOT NULL AND TRIM(d.fan_tarmoqi) <> '' "
                f"GROUP BY 1 ORDER BY 2 DESC LIMIT 30"
                if w else
                f"SELECT TRIM(d.fan_tarmoqi), COUNT(*) FROM dissertations d "
                f"WHERE d.fan_tarmoqi IS NOT NULL AND TRIM(d.fan_tarmoqi) <> '' "
                f"GROUP BY 1 ORDER BY 2 DESC LIMIT 30", p)
    facets['fan'] = [{'value': r[0], 'count': r[1]} for r in cur.fetchall()]
    # Group the muassasa facet by canonical institution (institution_map) so
    # merged/renamed institutions collapse into one row with summed counts.
    # Unmapped values fall back to the raw muassasa string. cyrillic_name is
    # UNIQUE, so the LEFT JOIN never fans out a dissertation row.
    w, p = _dashboard_where(f, exclude='muassasa', user_id=user_id)
    _mj = ("LEFT JOIN institution_map im "
           "ON im.cyrillic_name = TRIM(d.muassasa) AND im.is_active = TRUE")
    _mcol = "COALESCE(im.canonical_name, TRIM(d.muassasa))"
    cur.execute(f"SELECT {_mcol} AS canon, COUNT(*) FROM dissertations d {_mj}{w} "
                f"AND d.muassasa IS NOT NULL AND TRIM(d.muassasa) <> '' "
                f"GROUP BY canon ORDER BY 2 DESC LIMIT 30"
                if w else
                f"SELECT {_mcol} AS canon, COUNT(*) FROM dissertations d {_mj} "
                f"WHERE d.muassasa IS NOT NULL AND TRIM(d.muassasa) <> '' "
                f"GROUP BY canon ORDER BY 2 DESC LIMIT 30", p)
    facets['muassasa'] = [{'value': r[0], 'count': r[1]} for r in cur.fetchall()]
    w, p = _dashboard_where(f, exclude='yil', user_id=user_id)
    year_where = (w + " AND " if w else " WHERE ") + r"d.sana ~ '(19|20)\d{2}'"
    cur.execute(f"SELECT ({_YEAR_EXPR})::int AS yr, COUNT(*) "
                f"FROM dissertations d{year_where} GROUP BY 1 ORDER BY 1", p)
    histogram = [{'year': r[0], 'count': r[1]} for r in cur.fetchall() if r[0]]
    return facets, histogram


def _dashboard_is_default(f):
    return not (f['search'] or f['daraja'] or f['fan'] or f['muassasa']
                or f['ixt'] or f['yil_min'] or f['yil_max'] or f['saved']
                or f['gender'])


@data_bp.route('/api/dashboard/search')
def dashboard_search():
    from flask_login import current_user
    a = request.args
    f = _dashboard_filters(a)
    uid = current_user.id if getattr(current_user, 'is_authenticated', False) else None
    try:
        page = max(1, int(a.get('page', 1)))
    except ValueError:
        page = 1
    # Freemium gate — mehmon faqat filtrsiz 1-sahifani ko'radi.
    if uid is None and (page > 1 or not _dashboard_is_default(f)
                        or f['scope'] != 'all' or f['sort'] != 'sana_desc'):
        return _guest_gate_response()
    per_page = 25
    order = _DASH_SORTS[f['sort']]
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_dashboard_schema(cur)
                w, p = _dashboard_where(f, user_id=uid)
                cur.execute(f"SELECT COUNT(*) FROM dissertations d{w}", p)
                total = cur.fetchone()[0] or 0
                total_pages = max(1, (total + per_page - 1) // per_page)
                page = min(page, total_pages)
                # facets + histogram: standart (filtrsiz) holat 10 daqiqa kesh
                cache_key = 'dashboard_facets_default_v1'
                cached = cache.get(cache_key) if _dashboard_is_default(f) else None
                if cached:
                    facets, histogram = cached
                else:
                    facets, histogram = _dashboard_facets(cur, f, uid)
                    if _dashboard_is_default(f):
                        cache.set(cache_key, (facets, histogram), timeout=600)
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({'records': [], 'total': 0, 'page': 1, 'per_page': per_page,
                        'total_pages': 1, 'facets': {}, 'histogram': [],
                        'error': str(e)}), 500
    try:
        w2, p2 = _dashboard_where(f, user_id=uid)
        rows = _query_rows(
            'SELECT d.id, d.oak_id, d.sana AS "Sana", d.daraja AS "Daraja", d.olim AS "Olim", '
            'd.mavzu AS "Mavzu", d.ixtisoslik AS "Ixtisoslik", d.muassasa AS "Muassasa", '
            'd.ilmiy_rahbar AS "Ilmiy_rahbar", d.link AS "Link" '
            f'FROM dissertations d{w2} '
            f'ORDER BY {order} LIMIT %s OFFSET %s',
            p2 + [per_page, (page - 1) * per_page])
        # bookmark holati — bitta so'rov (N+1 yo'q)
        if uid and rows:
            ids = [r['id'] for r in rows if r.get('id')]
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT dissertation_id FROM user_bookmarks "
                                "WHERE user_id = %s AND dissertation_id = ANY(%s)", (uid, ids))
                    marked = {r[0] for r in cur.fetchall()}
            finally:
                conn.close()
            for r in rows:
                r['bookmarked'] = r.get('id') in marked
    except Exception:
        rows = []
    return jsonify({'records': rows, 'total': total, 'page': page,
                    'per_page': per_page, 'total_pages': total_pages,
                    'facets': facets, 'histogram': histogram,
                    'logged_in': bool(uid)})


@data_bp.route('/api/bookmarks/toggle', methods=['POST'])
def bookmark_toggle():
    from flask_login import current_user
    if not getattr(current_user, 'is_authenticated', False):
        return jsonify({'success': False, 'error': 'auth'}), 401
    data = request.get_json(silent=True) or {}
    try:
        did = int(data.get('dissertation_id'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': "Noto'g'ri so'rov"}), 400
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_dashboard_schema(cur)
            cur.execute("DELETE FROM user_bookmarks "
                        "WHERE user_id = %s AND dissertation_id = %s",
                        (current_user.id, did))
            if cur.rowcount:
                bookmarked = False
            else:
                cur.execute("INSERT INTO user_bookmarks (user_id, dissertation_id) "
                            "VALUES (%s, %s) ON CONFLICT DO NOTHING", (current_user.id, did))
                bookmarked = True
        conn.commit()
        return jsonify({'success': True, 'bookmarked': bookmarked})
    finally:
        conn.close()


@data_bp.route('/api/bookmarks')
def bookmarks_list():
    from flask_login import current_user
    if not getattr(current_user, 'is_authenticated', False):
        return jsonify({'success': False, 'error': 'auth'}), 401
    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1
    rows = _query_rows(
        'SELECT d.id, d.oak_id, d.sana AS "Sana", d.daraja AS "Daraja", d.olim AS "Olim", '
        'd.mavzu AS "Mavzu", d.ixtisoslik AS "Ixtisoslik", d.muassasa AS "Muassasa", '
        'd.ilmiy_rahbar AS "Ilmiy_rahbar", d.link AS "Link" '
        'FROM user_bookmarks b JOIN dissertations d ON d.id = b.dissertation_id '
        'WHERE b.user_id = %s ORDER BY b.created_at DESC LIMIT 25 OFFSET %s',
        (current_user.id, (page - 1) * 25))
    return jsonify({'success': True, 'records': rows, 'page': page})


@data_bp.route('/api/dashboard/export.csv')
def dashboard_export():
    """CSV eksport — joriy filtrlangan to'plam. Bepul: 50 qator + premium
    eslatmasi; premium (users.is_premium): 5000 qator. UTF-8 BOM (Excel)."""
    import csv
    import io
    import time as _time
    from flask_login import current_user
    if not getattr(current_user, 'is_authenticated', False):
        return redirect('/login')
    now = _time.time()
    if now - _export_last.get(current_user.id, 0) < 30:
        return Response("Eksport tayyorlanmoqda, biroz kuting", status=429,
                        mimetype='text/plain; charset=utf-8')
    _export_last[current_user.id] = now
    f = _dashboard_filters(request.args)
    uid = current_user.id
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_dashboard_schema(cur)
    finally:
        conn.close()
    w, p = _dashboard_where(f, user_id=uid)
    order = _DASH_SORTS[f['sort']]
    # To'liq eksport: premium yoki csv_export_credit (kredit faqat natija
    # 50 qatordan KO'P bo'lganda sarflanadi — kichik to'plamga isrof qilinmaydi).
    from blueprints.payments import user_has_premium, consume_credit
    premium = user_has_premium(uid)
    full = premium
    if not full:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM dissertations d{w}", p)
                total = int((cur.fetchone() or [0])[0] or 0)
        finally:
            conn.close()
        if total > 50:
            full = consume_credit(uid, 'csv_export_credit')
    limit = 5000 if full else 50

    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        yield '﻿'  # UTF-8 BOM — Excel o'zbek matnini to'g'ri ochadi
        writer.writerow(['sana', 'olim', 'mavzu', 'daraja', 'ixtisoslik',
                         'fan_tarmoqi', 'ilmiy_rahbar', 'muassasa'])
        yield buf.getvalue()
        buf.seek(0); buf.truncate(0)
        conn2 = get_connection()
        try:
            with conn2.cursor() as cur:
                cur.execute(
                    "SELECT d.sana, d.olim, d.mavzu, d.daraja, d.ixtisoslik, "
                    "COALESCE(d.fan_tarmoqi, ''), d.ilmiy_rahbar, d.muassasa "
                    f"FROM dissertations d{w} ORDER BY {order} LIMIT %s",
                    p + [limit])
                for row in cur.fetchall():
                    writer.writerow([(x or '').strip() if isinstance(x, str) else (x or '')
                                     for x in row])
                    yield buf.getvalue()
                    buf.seek(0); buf.truncate(0)
        finally:
            conn2.close()
        if not full:
            writer.writerow(["Ko'proq eksport qilish uchun Premium (olimlar.uz/premium) "
                             "yoki bir martalik to'liq eksport (10,000 so'm) kerak"])
            yield buf.getvalue()
    return Response(generate(), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition':
                             'attachment; filename="olimlar-dissertatsiyalar.csv"'})


@data_bp.route('/search-stats')
def search_stats():
    search = request.args.get('search', '').strip()
    if not search or len(search) < 2:
        return jsonify({'total': 0, 'olim': 0, 'mavzu': 0, 'rahbar': 0})
    cache_key = f'search_stats:{search.lower()}'
    cached = cache.get(cache_key)
    if cached:
        return jsonify(cached)
    sl = f'%{search.lower()}%'
    sc = f'%{latin_to_cyrillic(search).lower()}%'
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(DISTINCT LOWER(TRIM(olim))) FROM dissertations "
                    "WHERE LOWER(TRIM(olim)) LIKE %s OR LOWER(TRIM(olim)) LIKE %s", (sl, sc))
                olim_count = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM dissertations "
                    "WHERE LOWER(TRIM(mavzu)) LIKE %s OR LOWER(TRIM(mavzu)) LIKE %s", (sl, sc))
                mavzu_count = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(DISTINCT LOWER(TRIM(ilmiy_rahbar))) FROM dissertations "
                    "WHERE LOWER(TRIM(ilmiy_rahbar)) LIKE %s OR LOWER(TRIM(ilmiy_rahbar)) LIKE %s", (sl, sc))
                rahbar_count = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM dissertations WHERE "
                    "LOWER(TRIM(olim)) LIKE %s OR LOWER(TRIM(olim)) LIKE %s OR "
                    "LOWER(TRIM(mavzu)) LIKE %s OR LOWER(TRIM(mavzu)) LIKE %s OR "
                    "LOWER(TRIM(ilmiy_rahbar)) LIKE %s OR LOWER(TRIM(ilmiy_rahbar)) LIKE %s OR "
                    "LOWER(TRIM(muassasa)) LIKE %s OR LOWER(TRIM(muassasa)) LIKE %s OR "
                    "LOWER(TRIM(ixtisoslik)) LIKE %s OR LOWER(TRIM(ixtisoslik)) LIKE %s OR "
                    "LOWER(TRIM(COALESCE(ixtisoslik_nomi,''))) LIKE %s OR LOWER(TRIM(COALESCE(ixtisoslik_nomi,''))) LIKE %s",
                    (sl, sc, sl, sc, sl, sc, sl, sc, sl, sc, sl, sc))
                total = cur.fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return jsonify({'total': 0, 'olim': 0, 'mavzu': 0, 'rahbar': 0})
    result = {'total': total, 'olim': olim_count, 'mavzu': mavzu_count, 'rahbar': rahbar_count}
    cache.set(cache_key, result, timeout=120)
    return jsonify(result)


@data_bp.route('/search-summary')
def search_summary():
    search = request.args.get('search', '').strip()
    if not search or len(search) < 2:
        return jsonify({})
    cache_key = f'search_summary:{search.lower()}'
    cached = cache.get(cache_key)
    if cached:
        return jsonify(cached)
    sl = f'%{search.lower()}%'
    sc = f'%{latin_to_cyrillic(search).lower()}%'
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT daraja, mavzu FROM dissertations "
                    "WHERE LOWER(TRIM(olim)) LIKE %s OR LOWER(TRIM(olim)) LIKE %s ORDER BY sana",
                    (sl, sc))
                olim_rows = cur.fetchall()
                cur.execute(
                    "SELECT COUNT(*) FROM dissertations "
                    "WHERE LOWER(TRIM(ilmiy_rahbar)) LIKE %s OR LOWER(TRIM(ilmiy_rahbar)) LIKE %s",
                    (sl, sc))
                rahbar_count = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM dissertations WHERE "
                    "LOWER(TRIM(COALESCE(opponent_1,''))) LIKE %s OR LOWER(TRIM(COALESCE(opponent_1,''))) LIKE %s OR "
                    "LOWER(TRIM(COALESCE(opponent_2,''))) LIKE %s OR LOWER(TRIM(COALESCE(opponent_2,''))) LIKE %s OR "
                    "LOWER(TRIM(COALESCE(opponent_3,''))) LIKE %s OR LOWER(TRIM(COALESCE(opponent_3,''))) LIKE %s",
                    (sl, sc, sl, sc, sl, sc))
                opponent_count = cur.fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return jsonify({})
    phd_mavzu = [r[1] for r in olim_rows if (r[0] or '').strip().upper() == 'PHD']
    dsc_mavzu = [r[1] for r in olim_rows if (r[0] or '').strip().upper() == 'DSC']
    result = {
        'olim_count':     len(olim_rows),
        'rahbar_count':   rahbar_count,
        'opponent_count': opponent_count,
        'phd_mavzu':      phd_mavzu[:3],
        'dsc_mavzu':      dsc_mavzu[:3],
    }
    cache.set(cache_key, result, timeout=120)
    return jsonify(result)


@data_bp.route('/search-as-opponent')
def search_as_opponent():
    search = request.args.get('search', '').strip()
    if not search:
        return jsonify({'dissertations': [], 'total': 0})
    sl = f'%{search}%'
    sc = f'%{latin_to_cyrillic(search)}%'
    try:
        rows = _query_rows(
            "SELECT * FROM dissertations WHERE "
            "TRIM(COALESCE(opponent_1,'')) ILIKE %s OR TRIM(COALESCE(opponent_1,'')) ILIKE %s OR "
            "TRIM(COALESCE(opponent_2,'')) ILIKE %s OR TRIM(COALESCE(opponent_2,'')) ILIKE %s OR "
            "TRIM(COALESCE(opponent_3,'')) ILIKE %s OR TRIM(COALESCE(opponent_3,'')) ILIKE %s "
            "ORDER BY sana DESC",
            (sl, sc, sl, sc, sl, sc)
        )
    except Exception:
        return jsonify({'dissertations': [], 'total': 0})
    result = [
        {
            'id':           r.get('id'),
            'olim':         r.get('Olim', ''),
            'mavzu':        r.get('Mavzu', ''),
            'daraja':       r.get('Daraja', ''),
            'sana':         r.get('Sana', ''),
            'muassasa':     r.get('Muassasa', ''),
            'ilmiy_rahbar': r.get('Ilmiy_rahbar', ''),
            'opponent_1':   r.get('Opponent_1', ''),
            'opponent_2':   r.get('Opponent_2', ''),
            'opponent_3':   r.get('Opponent_3', ''),
        }
        for r in rows
    ]
    return jsonify({'dissertations': result, 'total': len(result)})


_FULL_DISS_COLUMNS = '''
    id,
    sana AS "Sana", daraja AS "Daraja", olim AS "Olim",
    mavzu AS "Mavzu", ixtisoslik AS "Ixtisoslik", muassasa AS "Muassasa",
    ilmiy_rahbar AS "Ilmiy_rahbar", link AS "Link", oak_id AS "Oak_id",
    COALESCE(ixtisoslik_nomi, '') AS "Ixtisoslik_nomi",
    COALESCE(mavzu_raqami, '') AS "Mavzu_raqami",
    COALESCE(ilmiy_rahbar_daraja, '') AS "Ilmiy_rahbar_daraja",
    COALESCE(ilmiy_kengash, '') AS "Ilmiy_kengash",
    COALESCE(ilmiy_kengash_raqami, '') AS "Ilmiy_kengash_raqami",
    COALESCE(opponent_1, '') AS "Opponent_1",
    COALESCE(opponent_2, '') AS "Opponent_2",
    COALESCE(opponent_3, '') AS "Opponent_3",
    COALESCE(yetakchi_tashkilot, '') AS "Yetakchi_tashkilot",
    COALESCE(fan_tarmoqi, '') AS "Fan_tarmoqi",
    COALESCE(yonalish, '') AS "Yonalish",
    COALESCE(photo_url, '') AS "photo_url"
'''


def _query_full_diss(where_sql, params, order='sana DESC'):
    """Return dissertation rows with all detail fields (no normalize_row stripping)."""
    sql = f"SELECT {_FULL_DISS_COLUMNS} FROM dissertations WHERE {where_sql} ORDER BY {order}"
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2_extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params or ()))
            out = []
            for row in cur.fetchall():
                d = {k: (str(v).strip() if v is not None else '') for k, v in row.items()}
                d['id'] = row.get('id')
                d['Olim_short'] = clean_olim_name(d.get('Olim', ''))
                out.append(d)
            return out
    finally:
        conn.close()


# Newest→oldest ordering for the DD.MM.YYYY text `sana` column (no table alias).
_SANA_ORDER_PLAIN = (
    r"NULLIF(regexp_replace(TRIM(sana), '^(\d{2})\.(\d{2})\.(\d{4})$', '\3\2\1'), TRIM(sana)) "
    "DESC NULLS LAST, id DESC"
)


def _slug_for_olim(term):
    """olim_name uchun tasdiqlangan slug (olim_profiles.slug) yoki None."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT slug FROM olim_profiles "
                            "WHERE LOWER(TRIM(olim_name)) = LOWER(TRIM(%s)) "
                            "AND slug IS NOT NULL LIMIT 1", (term,))
                r = cur.fetchone()
                return r[0] if r else None
        finally:
            conn.close()
    except Exception:
        return None


@data_bp.route('/olim/<path:name>')
def olim_profile(name):
    """Eski (indekslangan) URL. Slug bo'lsa — kanonik /@slug ga 301 (SEO)."""
    from flask import redirect
    term = (name or '').strip()
    slug = _slug_for_olim(term)
    if slug:
        return redirect('/@' + slug, code=301)
    return _render_olim_profile(term)


@data_bp.route('/@<slug>')
def profile_by_username(slug):
    """Vanity URL. slug → olim_name; topilmasa username_history'da eski slugni
    qidiradi (301 yangi slugga); hech joyda bo'lmasa 404."""
    from flask import redirect
    slug = (slug or '').strip().lower()
    olim_name = None
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT olim_name FROM olim_profiles WHERE slug = %s LIMIT 1",
                            (slug,))
                r = cur.fetchone()
                if r:
                    olim_name = r[0]
                else:
                    cur.execute("""
                        SELECT p.slug FROM username_history h
                        JOIN olim_profiles p ON p.id = h.profile_id
                        WHERE h.old_slug = %s AND p.slug IS NOT NULL
                        ORDER BY h.changed_at DESC LIMIT 1""", (slug,))
                    hr = cur.fetchone()
                    if hr and hr[0]:
                        return redirect('/@' + hr[0], code=301)
        finally:
            conn.close()
    except Exception:
        olim_name = None
    if not olim_name:
        abort(404)
    return _render_olim_profile(olim_name)


def _render_olim_profile(term):
    term = (term or '').strip()
    # Exact (case-insensitive) match so links resolve to the correct person in each role.
    own = _query_full_diss('LOWER(TRIM(olim)) = LOWER(TRIM(%s))', (term,), order=_SANA_ORDER_PLAIN)
    as_supervisor = _query_full_diss(
        "LOWER(TRIM(COALESCE(ilmiy_rahbar,''))) = LOWER(TRIM(%s))", (term,), order=_SANA_ORDER_PLAIN)
    as_opponent = _query_full_diss(
        "LOWER(TRIM(COALESCE(opponent_1,''))) = LOWER(TRIM(%s)) "
        "OR LOWER(TRIM(COALESCE(opponent_2,''))) = LOWER(TRIM(%s)) "
        "OR LOWER(TRIM(COALESCE(opponent_3,''))) = LOWER(TRIM(%s))",
        (term, term, term), order=_SANA_ORDER_PLAIN)
    # A person may exist purely as a supervisor or opponent — only 404 if they have no role at all.
    if not own and not as_supervisor and not as_opponent:
        abort(404)
    fields = {d.get('Ixtisoslik', '').strip() for d in own if d.get('Ixtisoslik', '').strip()}

    # Portfolio data (new profile tables) — empty if not yet filled in
    profile = None
    maqolalar = konferensiyalar = ish_faoliyati = rasmlar = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                def _fetch(sql, order=''):
                    cur.execute(sql + (' ORDER BY ' + order if order else ''), (term,))
                    cnames = [c[0] for c in cur.description]
                    return [dict(zip(cnames, row)) for row in cur.fetchall()]

                rows_p = _fetch("SELECT * FROM olim_profiles WHERE LOWER(TRIM(olim_name)) = LOWER(TRIM(%s))")
                profile = rows_p[0] if rows_p else None
                maqolalar = _fetch("SELECT * FROM olim_maqolalar WHERE LOWER(TRIM(olim_name)) = LOWER(TRIM(%s))",
                                   "year DESC NULLS LAST, id DESC")
                konferensiyalar = _fetch("SELECT * FROM olim_konferensiyalar WHERE LOWER(TRIM(olim_name)) = LOWER(TRIM(%s))",
                                         "date DESC NULLS LAST, id DESC")
                ish_faoliyati = _fetch("SELECT * FROM olim_ish_faoliyati WHERE LOWER(TRIM(olim_name)) = LOWER(TRIM(%s))",
                                       "start_date DESC NULLS LAST, id DESC")
                rasmlar = _fetch("SELECT * FROM olim_rasmlar WHERE LOWER(TRIM(olim_name)) = LOWER(TRIM(%s))",
                                 "created_at DESC, id DESC")
        finally:
            conn.close()
    except Exception:
        profile = None
        maqolalar = konferensiyalar = ish_faoliyati = rasmlar = []

    stats = {
        'total': len(own),
        'phd': sum(1 for d in own if str(d.get('Daraja', '')).strip().upper() == 'PHD'),
        'dsc': sum(1 for d in own if str(d.get('Daraja', '')).strip().upper() == 'DSC'),
        'fields': len(fields),
        'supervisor_count': len(as_supervisor),
        'opponent_count': len(as_opponent),
        'maqola_count': len(maqolalar),
    }
    # Is the logged-in cabinet user the owner of this profile?
    is_owner = False
    try:
        from flask import session
        cab_uid = session.get('cabinet_user_id')
        if cab_uid:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM olim_profiles WHERE cabinet_user_id = %s "
                        "AND LOWER(TRIM(olim_name)) = LOWER(TRIM(%s)) LIMIT 1",
                        (cab_uid, term))
                    is_owner = cur.fetchone() is not None
            finally:
                conn.close()
    except Exception:
        is_owner = False

    # NOTE: do not pass `supervisor_count`/`opponent_count` as ints — `supervisor_count` would
    # shadow the global context-processor function of the same name used inside the template.
    # Counts are available via stats.supervisor_count / stats.opponent_count and *_works|length.
    # Genealogy preview counts (from already-fetched rows — no extra queries):
    tree_parents = len({d.get('Ilmiy_rahbar', '').strip()
                        for d in own if d.get('Ilmiy_rahbar', '').strip()})
    tree_children = len({d.get('Olim', '').strip()
                         for d in as_supervisor if d.get('Olim', '').strip()})

    # Journal name → id map so articles can link to the journal page when matched.
    journal_map = {}
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, LOWER(TRIM(name)) FROM journals WHERE is_active = TRUE")
                journal_map = {r[1]: r[0] for r in cur.fetchall() if r[1]}
        finally:
            conn.close()
    except Exception:
        journal_map = {}

    # "Xabar yuborish" target: this scholar's claimed cabinet account, bridged
    # to the main users row by e-mail (messaging is keyed to users.id).
    message_target_id = None
    try:
        from flask_login import current_user as _cu
        if getattr(_cu, 'is_authenticated', False):
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT u.id FROM cabinet_users cu
                        JOIN users u ON LOWER(u.email) = LOWER(cu.email)
                        WHERE LOWER(TRIM(cu.olim_name)) = LOWER(TRIM(%s))
                          AND u.id <> %s LIMIT 1
                    """, (term, _cu.id))
                    r = cur.fetchone()
                    message_target_id = r[0] if r else None
            finally:
                conn.close()
    except Exception:
        message_target_id = None

    # H-index reyting badge'i (uz.h-index.com) — mos kelsa, aks holda None
    h_index = None
    try:
        from blueprints.ranking import get_scholar_h_index
        h_index = get_scholar_h_index(term)
    except Exception:
        h_index = None

    # Obro' metrikalari (Olimlar katalogi 2.0): avlod, nashr, sparkline, o'xshash
    reputation = None
    try:
        from blueprints.olimlar_catalog import get_scholar_reputation
        reputation = get_scholar_reputation(term)
    except Exception:
        reputation = None

    return render_template('olim_profile.html', olim_name=term, dissertations=own, is_owner=is_owner,
                           message_target_id=message_target_id,
                           as_supervisor=as_supervisor, as_opponent=as_opponent,
                           shogirdlar=as_supervisor, opponent_works=as_opponent,
                           stats=stats, profile=profile, maqolalar=maqolalar,
                           konferensiyalar=konferensiyalar, ish_faoliyati=ish_faoliyati, rasmlar=rasmlar,
                           tree_parents=tree_parents, tree_children=tree_children,
                           journal_map=journal_map, h_index=h_index,
                           reputation=reputation)


def _summary_stats(rows):
    return {
        'total': len(rows),
        'phd': sum(1 for row in rows if str(row.get('Daraja', '')).strip().upper() == 'PHD'),
        'dsc': sum(1 for row in rows if str(row.get('Daraja', '')).strip().upper() == 'DSC')
    }


def get_ixtisoslik_saturation(ixtisoslik_code):
    """How many dissertations share this ixtisoslik code → saturation level."""
    code = (ixtisoslik_code or '').strip()
    if not code:
        return None
    cache_key = f'ixtisoslik_sat:{code.lower()}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        count = _query_scalar(
            "SELECT COUNT(*) FROM dissertations WHERE ixtisoslik ILIKE %s",
            (f"%{code}%",)
        ) or 0
    except Exception:
        return None
    if count < 50:
        result = {'level': 'low', 'label': "Kam o'rganilgan", 'count': count}
    elif count < 200:
        result = {'level': 'medium', 'label': "O'rtacha o'rganilgan", 'count': count}
    else:
        result = {'level': 'high', 'label': "Ko'p o'rganilgan", 'count': count}
    cache.set(cache_key, result, timeout=300)
    return result


@data_bp.route('/dissertation/<int:id>')
def dissertation(id):
    row = get_dissertation_detail_by_id(id)
    if not row:
        abort(404)
    row['Olim_short'] = clean_olim_name(row.get('Olim', ''))
    saturation = get_ixtisoslik_saturation(row.get('Ixtisoslik', ''))
    return render_template('dissertation.html', row=row, id=id, saturation=saturation)


@data_bp.route('/author/<path:name>')
def author(name):
    rows = get_dissertations_by_field('Olim', name)
    if not rows:
        abort(404)
    return render_template('author.html', name=name, rows=rows, stats=_summary_stats(rows))


@data_bp.route('/supervisor/<path:name>')
def supervisor(name):
    rows = get_dissertations_by_field('Ilmiy_rahbar', name)
    if not rows:
        abort(404)
    return render_template('supervisor.html', name=name, rows=rows, stats=_summary_stats(rows))


# NOTE: /university/<path:name> is served by app.py (university_profile) — the
# rich university portfolio page. Kept out of this blueprint to avoid a duplicate rule.


@data_bp.route('/specialization/<path:code>')
def specialization(code):
    rows = get_dissertations_by_field('Ixtisoslik', code)
    if not rows:
        abort(404)
    return render_template('specialization.html', code=code, rows=rows, stats=_summary_stats(rows))


@data_bp.route('/api/chat', methods=['POST'])
def chat():
    """AI chat endpoint that queries dissertations based on user message."""
    try:
        data = request.get_json() or {}
        message = (data.get('message') or '').strip().lower()
        
        if not message:
            return jsonify({"response": "Iltimos, savolingizni yozing."})
        
        # Handle specific queries
        if 'eng faol rahbar' in message:
            sql = '''
                SELECT ilmiy_rahbar, COUNT(*) as count
                FROM dissertations
                WHERE ilmiy_rahbar IS NOT NULL AND TRIM(ilmiy_rahbar) <> ''
                GROUP BY ilmiy_rahbar
                ORDER BY count DESC LIMIT 5
            '''
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    results = cur.fetchall()
                    if results:
                        html = '<div style="line-height:1.6;"><strong>Eng faol ilmiy rahbarlar:</strong><ol style="margin:8px 0 0 0;">'
                        for name, count in results:
                            html += f'<li>{name or "—"} ({count} ta)</li>'
                        html += '</ol></div>'
                    else:
                        html = 'Ilmiy rahbarlar haqida ma\'lumot topilmadi.'
            finally:
                conn.close()
            return jsonify({"response": html})
        
        elif 'phd' in message and 'statistik' in message:
            sql = '''
                SELECT
                    COUNT(*) FILTER (WHERE UPPER(TRIM(daraja)) = 'PHD') AS phd_count,
                    COUNT(*) FILTER (WHERE UPPER(TRIM(daraja)) = 'DSC') AS dsc_count
                FROM dissertations
            '''
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    phd, dsc = cur.fetchone()
                    html = f'''<div style="line-height:1.6;">
                        <strong>Dissertatsiya statistikasi:</strong>
                        <div style="margin:8px 0;">PhD: <strong>{phd or 0}</strong> ta</div>
                        <div>DSc: <strong>{dsc or 0}</strong> ta</div>
                    </div>'''
            finally:
                conn.close()
            return jsonify({"response": html})
        
        elif 'mavzu tavsiya' in message:
            sql = '''
                SELECT mavzu FROM dissertations
                WHERE mavzu IS NOT NULL AND TRIM(mavzu) <> ''
                ORDER BY RANDOM() LIMIT 5
            '''
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    topics = [row[0] for row in cur.fetchall()]
                    if topics:
                        html = '<div style="line-height:1.6;"><strong>Tavsiya etilgan mavzular:</strong><ul style="margin:8px 0 0 0;">'
                        for topic in topics:
                            html += f'<li>{topic}</li>'
                        html += '</ul></div>'
                    else:
                        html = 'Mavzular haqida ma\'lumot topilmadi.'
            finally:
                conn.close()
            return jsonify({"response": html})
        
        else:
            # Search PostgreSQL for relevant dissertations as context for Groq
            search_term = f"%{message}%"
            sql = '''
                SELECT olim, mavzu, daraja, muassasa, ilmiy_rahbar, ixtisoslik, sana
                FROM dissertations
                WHERE
                    olim ILIKE %s OR
                    mavzu ILIKE %s OR
                    muassasa ILIKE %s OR
                    ilmiy_rahbar ILIKE %s OR
                    ixtisoslik ILIKE %s
                LIMIT 10
            '''
            conn = get_connection()
            try:
                with conn.cursor(cursor_factory=psycopg2_extras.RealDictCursor) as cur:
                    cur.execute(sql, [search_term] * 5)
                    found_rows = cur.fetchall()
            finally:
                conn.close()

            if found_rows:
                context_lines = [
                    f"- Olim: {r.get('olim','')}, Mavzu: {r.get('mavzu','')}, "
                    f"Daraja: {r.get('daraja','')}, Muassasa: {r.get('muassasa','')}, "
                    f"Ilmiy rahbar: {r.get('ilmiy_rahbar','')}, "
                    f"Ixtisoslik: {r.get('ixtisoslik','')}, Sana: {r.get('sana','')}"
                    for r in found_rows
                ]
                context = "Topilgan dissertatsiyalar:\n" + "\n".join(context_lines)
            else:
                context = "Ushbu so'rov bo'yicha dissertatsiya topilmadi."

            if not GROQ_API_KEY:
                return jsonify({"response": "Groq API kaliti sozlanmagan."})

            try:
                from groq import Groq
                client = Groq(api_key=GROQ_API_KEY)
                response = client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Sen IlmNet platformasining AI yordamchisisan. "
                                "Faqat berilgan dissertatsiya malumotlariga asoslanib javob ber. "
                                "Javobni ozbekcha, qisqa va aniq ber."
                            )
                        },
                        {"role": "user", "content": message + "\n\n" + context}
                    ],
                    max_tokens=500
                )
                answer = (response.choices[0].message.content or "Javob olinmadi.").strip()
                safe_answer = html_module.escape(answer).replace("\n", "<br>")
                return jsonify({"response": f'<div style="line-height:1.6;">{safe_answer}</div>'})
            except Exception:
                return jsonify({"response": "AI xizmati hozirda mavjud emas. Iltimos, keyinroq urinib ko'ring."})
    
    except Exception as e:
        return jsonify({"response": "Xatolik yuz berdi. Iltimos, qayta urinib ko'ring."}), 200


# ---------------------------------------------------------------------------
# Ingest helpers
# ---------------------------------------------------------------------------

def _extract_olim(title: str) -> str:
    if not title:
        return ''
    t = title.strip()
    # "нинг фалсафа/фан доктори" — most reliable
    m = re.search(
        r'^([А-ЯЎҚҒҲа-яўқғҳёЁ][А-ЯЎҚҒҲа-яўқғҳёЁa-z\s\'\-\.]+?)нинг\s+(?:фалсафа|фан)\s+доктори',
        t
    )
    if m:
        return m.group(1).strip()
    # Latin transliteration
    m = re.search(
        r'^([A-Za-zА-ЯЎҚҒҲа-яўқғҳёЁ][A-Za-zА-ЯЎҚҒҲа-яўқғҳёЁ\s\'\-\.]+?)ning\s+(?:falsafa|fan)\s+doktori',
        t, re.IGNORECASE
    )
    if m:
        return m.group(1).strip()
    # Fallback: everything before "нинг"
    m = re.search(r'^(.+?)нинг', t)
    if m:
        return m.group(1).strip()
    return t[:80].strip()


def _extract_daraja(title: str, mavzu: str) -> str:
    text = (title or '') + ' ' + (mavzu or '')
    if any(x in text for x in ['фалсафа доктори', 'falsafa doktori', '(PhD)', 'PhD/']):
        return 'PhD'
    if any(x in text for x in ['фан доктори', 'fan doktori', '(DSc)', 'DSc/']):
        return 'DSc'
    if 'PhD' in text:
        return 'PhD'
    if 'DSc' in text:
        return 'DSc'
    return ''


def _extract_mavzu(mavzu_full: str) -> str:
    if not mavzu_full:
        return ''
    # Extract text inside quotes first
    m = re.search(r'[«"“«]([^»"”»]{20,})[»"”»]', mavzu_full)
    if m:
        return m.group(1).strip()
    # Up to first specialization code like "08.00.07"
    m = re.search(r'^(.+?)\s+\d{2}\.\d{2}\.\d{2}', mavzu_full)
    if m:
        return m.group(1).strip(' –—-')
    return mavzu_full[:500].strip()


def _extract_ixtisoslik(shifr_field: str, mavzu_full: str) -> str:
    # Prefer dedicated field — take only the first code
    src = shifr_field or mavzu_full or ''
    m = re.search(r'\b(\d{2}\.\d{2}\.\d{2})\b', src)
    return m.group(1) if m else ''


def _validate_ingest_record(oak_id: str, olim: str, daraja: str,
                             mavzu: str, muassasa: str) -> str | None:
    """Return a skip reason string, or None if the record is valid."""
    if not oak_id or not oak_id.isdigit() or int(oak_id) <= 0:
        return 'bad oak_id'
    if 'нинг' in olim:
        return 'olim not cleaned'
    if daraja not in ('PhD', 'DSc', ''):
        return f'bad daraja: {daraja}'
    if not mavzu or not (20 <= len(mavzu) <= 500):
        return f'mavzu length {len(mavzu)}'
    if mavzu == muassasa:
        return 'mavzu == muassasa'
    if 'attestatsiya komissiyasi' in mavzu.lower():
        return 'attestatsiya noise'
    if 'Fanlar akademiyasi' in mavzu:
        return 'Fanlar akademiyasi noise'
    return None


# ---------------------------------------------------------------------------
# Ingest endpoint
# ---------------------------------------------------------------------------

@data_bp.route('/api/oak/ingest', methods=['POST'])
def oak_ingest():
    auth = request.headers.get('Authorization', '')
    if not OAK_API_KEY or auth != f'Bearer {OAK_API_KEY}':
        return jsonify({'error': 'Unauthorized'}), 401

    payload = request.get_json(silent=True) or {}
    items = payload.get('items', [])
    if not isinstance(items, list):
        return jsonify({'error': 'items must be a list'}), 400

    added = 0
    skipped = 0
    skip_reasons: dict = {}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for item in items:
                oak_id     = str(item.get('ID', '') or '').strip()
                title      = str(item.get('Sarlavha', '') or '').strip()
                mavzu_full = str(item.get('Mavzu va ixtisoslik', '') or '').strip()
                muassasa   = str(item.get('Bajarilgan muassasa', '') or '').strip()[:300]

                # Extract & clean fields
                olim     = str(item.get('Olim', '') or '').strip() or _extract_olim(title)
                mavzu    = _extract_mavzu(mavzu_full)
                daraja   = _extract_daraja(title, mavzu_full) or str(item.get('Daraja', '') or '').strip()
                ixtisoslik = _extract_ixtisoslik(
                    str(item.get('Ixtisoslik shifrlari', '') or ''),
                    mavzu_full
                )

                # Validate
                reason = _validate_ingest_record(oak_id, olim, daraja, mavzu, muassasa)
                if reason:
                    skipped += 1
                    skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                    continue

                cur.execute("SELECT 1 FROM dissertations WHERE oak_id = %s", (oak_id,))
                if cur.fetchone():
                    skipped += 1
                    skip_reasons['duplicate'] = skip_reasons.get('duplicate', 0) + 1
                    continue

                cur.execute(
                    """
                    INSERT INTO dissertations
                        (oak_id, sana, daraja, olim, mavzu, ixtisoslik,
                         mavzu_raqami, ilmiy_rahbar, muassasa,
                         ilmiy_kengash_raqami, opponent_1, opponent_2,
                         yetakchi_tashkilot, link)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (oak_id) DO NOTHING
                    """,
                    (
                        oak_id,
                        str(item.get('Sana', '') or '')[:50],
                        daraja,
                        olim[:200],
                        mavzu[:500],
                        ixtisoslik[:50],
                        str(item.get('Royxat raqami', '') or ''),
                        str(item.get('Ilmiy rahbar', '') or '')[:200],
                        muassasa,
                        str(item.get('IK raqami', '') or ''),
                        str(item.get('Opponent 1', '') or '')[:200],
                        str(item.get('Opponent 2', '') or '')[:200],
                        str(item.get('Yetakchi tashkilot', '') or '')[:200],
                        str(item.get('Havola', '') or ''),
                    )
                )
                added += 1

        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

    return jsonify({'added': added, 'skipped': skipped,
                    'total': len(items), 'skip_reasons': skip_reasons})


# ---------------------------------------------------------------------------
# Secure import endpoint for the daily scraper (VPS-direct, X-API-KEY auth).
# Unlike /api/oak/ingest this UPSERTs: existing records (matched on oak_id) are
# updated in place instead of skipped, so re-runs refresh changed data.
#
# Exposed at BOTH paths, same handler:
#   /api/oak/import   — preferred; under the /api/oak/ prefix that api_protection
#                       whitelists, so the scraper's python-requests User-Agent
#                       is not blocked (403) by the anti-scraping middleware.
#   /api/v1/import-oak — legacy alias kept for backwards compatibility.
# Point the SITE_API_URL secret at https://olimlar.uz/api/oak/import.
# ---------------------------------------------------------------------------

@data_bp.route('/api/oak/import', methods=['POST'])
@data_bp.route('/api/v1/import-oak', methods=['POST'])
def import_oak():
    api_key = request.headers.get('X-API-KEY', '')
    if not SITE_API_KEY or api_key != SITE_API_KEY:
        return jsonify({'error': 'Unauthorized'}), 401

    payload = request.get_json(silent=True) or {}
    # Accept either a bare JSON array or {"items": [...]}.
    items = payload.get('items', payload) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return jsonify({'error': 'items must be a list'}), 400

    inserted = 0
    updated = 0
    skipped = 0
    skip_reasons: dict = {}
    new_defenses = []  # freshly inserted announcements → himoya_elon matching

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for item in items:
                oak_id     = str(item.get('ID', '') or '').strip()
                title      = str(item.get('Sarlavha', '') or '').strip()
                mavzu_full = str(item.get('Mavzu va ixtisoslik', '') or '').strip()
                muassasa   = str(item.get('Bajarilgan muassasa', '') or '').strip()[:300]

                olim     = str(item.get('Olim', '') or '').strip() or _extract_olim(title)
                mavzu    = _extract_mavzu(mavzu_full)
                daraja   = _extract_daraja(title, mavzu_full) or str(item.get('Daraja', '') or '').strip()
                ixtisoslik = _extract_ixtisoslik(
                    str(item.get('Ixtisoslik shifrlari', '') or ''),
                    mavzu_full
                )

                reason = _validate_ingest_record(oak_id, olim, daraja, mavzu, muassasa)
                if reason:
                    skipped += 1
                    skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                    continue

                cur.execute("SELECT 1 FROM dissertations WHERE oak_id = %s", (oak_id,))
                exists = cur.fetchone() is not None

                cur.execute(
                    """
                    INSERT INTO dissertations
                        (oak_id, sana, daraja, olim, mavzu, ixtisoslik,
                         mavzu_raqami, ilmiy_rahbar, muassasa,
                         ilmiy_kengash_raqami, opponent_1, opponent_2,
                         yetakchi_tashkilot, link)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (oak_id) DO UPDATE SET
                        sana = EXCLUDED.sana,
                        daraja = EXCLUDED.daraja,
                        olim = EXCLUDED.olim,
                        mavzu = EXCLUDED.mavzu,
                        ixtisoslik = EXCLUDED.ixtisoslik,
                        mavzu_raqami = EXCLUDED.mavzu_raqami,
                        ilmiy_rahbar = EXCLUDED.ilmiy_rahbar,
                        muassasa = EXCLUDED.muassasa,
                        ilmiy_kengash_raqami = EXCLUDED.ilmiy_kengash_raqami,
                        opponent_1 = EXCLUDED.opponent_1,
                        opponent_2 = EXCLUDED.opponent_2,
                        yetakchi_tashkilot = EXCLUDED.yetakchi_tashkilot,
                        link = EXCLUDED.link
                    RETURNING id
                    """,
                    (
                        oak_id,
                        str(item.get('Sana', '') or '')[:50],
                        daraja,
                        olim[:200],
                        mavzu[:500],
                        ixtisoslik[:50],
                        str(item.get('Royxat raqami', '') or ''),
                        str(item.get('Ilmiy rahbar', '') or '')[:200],
                        muassasa,
                        str(item.get('IK raqami', '') or ''),
                        str(item.get('Opponent 1', '') or '')[:200],
                        str(item.get('Opponent 2', '') or '')[:200],
                        str(item.get('Yetakchi tashkilot', '') or '')[:200],
                        str(item.get('Havola', '') or ''),
                    )
                )
                row = cur.fetchone()
                if exists:
                    updated += 1
                else:
                    inserted += 1
                    new_defenses.append({
                        'id': row[0] if row else None,
                        'olim': olim, 'mavzu': mavzu, 'ixtisoslik': ixtisoslik,
                        'ilmiy_rahbar': str(item.get('Ilmiy rahbar', '') or '').strip(),
                        'link': str(item.get('Havola', '') or ''),
                    })

        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

    # Himoya e'lonlari smart matching: notify scholars whose ixtisoslik matches
    # a newly imported announcement (opt-out via himoya_elon pref, 3/day cap).
    # Runs after the import commit on its own connection — never fails the import.
    notified = 0
    subs_notified = 0
    if new_defenses:
        try:
            from blueprints.reminders import notify_himoya_matches
            notified = notify_himoya_matches(new_defenses)
        except Exception:
            notified = 0
        # Ixtisoslik obunachilari (specialty_subscriptions) — sayt + Telegram.
        try:
            from blueprints.subscriptions import notify_specialty_subscribers
            subs_notified = notify_specialty_subscribers(new_defenses)
        except Exception:
            subs_notified = 0
        # Kuzatilayotgan olimlar (scholar_follows): yangi shogird → kuzatuvchilarga
        # sayt bildirishnomasi (bell). Import'ni hech qachon yiqitmaydi.
        try:
            from blueprints.olimlar_catalog import notify_scholar_follows
            notify_scholar_follows(new_defenses)
        except Exception:
            pass

    return jsonify({'success': True, 'inserted': inserted, 'updated': updated,
                    'skipped': skipped, 'total': len(items),
                    'skip_reasons': skip_reasons, 'himoya_notified': notified,
                    'subscribers_notified': subs_notified})


# ---------------------------------------------------------------------------
# Academic genealogy tree — recursive mentor→mentee traversal (DocUzBase).
# Scholars are identified by olim_profiles.id; generations are linked by name
# (dissertations.ilmiy_rahbar = mentor, dissertations.olim = student). A path
# array guards against cycles so the WITH RECURSIVE walk terminates.
# ---------------------------------------------------------------------------

def _build_scholar_tree(root_name):
    """Run the WITH RECURSIVE mentor→mentee walk and return a nested tree dict.

    Shared by the id-based and name-based tree endpoints so both serve the
    identical collapsible/recursive structure the frontend expects.
    """
    root_name = (root_name or '').strip()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH RECURSIVE tree AS (
                    SELECT TRIM(%s)::text AS name,
                           NULL::text     AS mentor,
                           0              AS depth,
                           ARRAY[LOWER(TRIM(%s))] AS path
                    UNION ALL
                    SELECT DISTINCT TRIM(d.olim)::text AS name,
                           t.name                       AS mentor,
                           t.depth + 1                  AS depth,
                           t.path || LOWER(TRIM(d.olim))
                    FROM tree t
                    JOIN dissertations d
                      ON LOWER(TRIM(d.ilmiy_rahbar)) = LOWER(TRIM(t.name))
                    WHERE d.olim IS NOT NULL AND TRIM(d.olim) <> ''
                      AND NOT (LOWER(TRIM(d.olim)) = ANY(t.path))
                      AND t.depth < 20
                )
                SELECT t.name, t.mentor, t.depth,
                       (SELECT COUNT(DISTINCT TRIM(d2.olim))
                          FROM dissertations d2
                         WHERE LOWER(TRIM(d2.ilmiy_rahbar)) = LOWER(TRIM(t.name))
                           AND d2.olim IS NOT NULL AND TRIM(d2.olim) <> ''
                       )::int AS direct_students
                FROM tree t
                ORDER BY t.depth, t.name
                """,
                (root_name, root_name)
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    # Build a nested tree from the flat (name, mentor, depth, count) rows.
    nodes = {}
    for name, mentor, depth, direct_students in rows:
        nodes.setdefault(name, {
            'name': name, 'direct_students': int(direct_students or 0),
            'depth': int(depth), 'children': [],
        })
    root_node = None
    for name, mentor, depth, _ in rows:
        node = nodes[name]
        if mentor is None or mentor not in nodes or mentor == name:
            if root_node is None:
                root_node = node
        else:
            nodes[mentor]['children'].append(node)

    return root_node or {
        'name': root_name, 'direct_students': 0, 'depth': 0, 'children': []
    }


@data_bp.route('/api/v1/scholar/<int:scholar_id>/tree', methods=['GET'])
def scholar_tree(scholar_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT olim_name FROM olim_profiles WHERE id = %s", (scholar_id,))
            root = cur.fetchone()
    finally:
        conn.close()
    if not root:
        return jsonify({'error': 'scholar not found'}), 404
    try:
        return jsonify(_build_scholar_tree(root[0]))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@data_bp.route('/api/v1/scholar/tree/by-name/<path:name>', methods=['GET'])
def scholar_tree_by_name(name):
    """Name-based recursive genealogy tree (used by the /genealogy/<name> page)."""
    try:
        return jsonify(_build_scholar_tree(name))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Network visualization graph API — dual-mode (map / force-graph).
# Nodes: institutions + scholars. Edges are semantically typed:
#   green = advisor→student mentorship (ilmiy_rahbar → olim)  [real data]
#   blue  = institutional collaboration proxy (scholars sharing muassasa)
#   red   = research co-authorship  [reserved — no source data yet]
# Filters: ?spec=05.01.01 (code prefix), ?year_from & ?year_to, ?ego=<scholar_id>.
# ---------------------------------------------------------------------------

# Approximate city/region centroids for the geographic (map) mode. Institutions
# are geocoded by keyword match on their name (Latin + Cyrillic forms) so the
# Leaflet map can drop a marker per institution. Coarse but good enough to
# cluster institutions by their host city.
_UZ_CITY_COORDS = {
    'toshkent': (41.311, 69.240), 'тошкент': (41.311, 69.240), 'ташкент': (41.311, 69.240),
    'samarqand': (39.654, 66.960), 'самарқанд': (39.654, 66.960), 'самарканд': (39.654, 66.960),
    'buxoro': (39.767, 64.421), 'бухоро': (39.767, 64.421), 'бухара': (39.767, 64.421),
    'andijon': (40.783, 72.344), 'андижон': (40.783, 72.344), 'андижан': (40.783, 72.344),
    "farg'ona": (40.389, 71.783), 'фарғона': (40.389, 71.783), 'фергана': (40.389, 71.783),
    'fargona': (40.389, 71.783),
    'namangan': (40.998, 71.672), 'наманган': (40.998, 71.672),
    'qarshi': (38.860, 65.799), 'қарши': (38.860, 65.799), 'карши': (38.860, 65.799),
    'nukus': (42.460, 59.617), 'нукус': (42.460, 59.617),
    'urganch': (41.550, 60.631), 'урганч': (41.550, 60.631), 'ургенч': (41.550, 60.631),
    'termiz': (37.224, 67.278), 'термиз': (37.224, 67.278), 'термез': (37.224, 67.278),
    'navoiy': (40.084, 65.379), 'навоий': (40.084, 65.379), 'навои': (40.084, 65.379),
    'jizzax': (40.116, 67.842), 'жиззах': (40.116, 67.842), 'джизак': (40.116, 67.842),
    'guliston': (40.489, 68.783), 'гулистон': (40.489, 68.783),
    'xiva': (41.378, 60.364), 'хива': (41.378, 60.364), 'хорезм': (41.378, 60.364),
    'nurafshon': (41.028, 69.348), 'нурафшон': (41.028, 69.348),
}


def _geocode_institution(name):
    """Best-effort (lat, lng) for an institution name via city keyword match."""
    low = (name or '').lower()
    for key, coord in _UZ_CITY_COORDS.items():
        if key in low:
            return coord
    return None


@data_bp.route('/api/v1/network', methods=['GET'])
def network_graph():
    spec = (request.args.get('spec') or '').strip()
    try:
        year_from = int(request.args.get('year_from') or 2022)
        year_to = int(request.args.get('year_to') or 2026)
    except (TypeError, ValueError):
        year_from, year_to = 2022, 2026
    ego_id = request.args.get('ego', type=int)
    limit = min(int(request.args.get('limit') or 2000), 5000)

    where = ["olim IS NOT NULL AND TRIM(olim) <> ''",
             "ilmiy_rahbar IS NOT NULL AND TRIM(ilmiy_rahbar) <> ''",
             "substring(sana from '(19|20)[0-9][0-9]') IS NOT NULL",
             "substring(sana from '(19|20)[0-9][0-9]')::int BETWEEN %s AND %s"]
    params = [year_from, year_to]
    if spec:
        where.append("ixtisoslik LIKE %s")
        params.append(spec + '%')

    ego_name = None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if ego_id:
                cur.execute("SELECT olim_name FROM olim_profiles WHERE id = %s", (ego_id,))
                row = cur.fetchone()
                if row:
                    ego_name = row[0]
                    where.append(
                        "(LOWER(TRIM(olim)) = LOWER(TRIM(%s)) "
                        "OR LOWER(TRIM(ilmiy_rahbar)) = LOWER(TRIM(%s)))")
                    params.extend([ego_name, ego_name])

            cur.execute(
                "SELECT TRIM(olim), TRIM(ilmiy_rahbar), TRIM(COALESCE(muassasa,'')), "
                "substring(sana from '(19|20)[0-9][0-9]')::int AS yr "
                "FROM dissertations WHERE " + " AND ".join(where) +
                " LIMIT %s", tuple(params) + (limit,))
            rows = cur.fetchall()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

    nodes = {}
    edges = []

    def add_node(nid, label, ntype, region=''):
        n = nodes.get(nid)
        if not n:
            nodes[nid] = {'id': nid, 'label': label, 'type': ntype,
                          'region': region, 'weight': 1}
        else:
            n['weight'] += 1

    inst_members = {}
    for olim, rahbar, muassasa, yr in rows:
        s_id = 'p:' + olim.lower()
        m_id = 'p:' + rahbar.lower()
        add_node(s_id, olim, 'scholar')
        add_node(m_id, rahbar, 'scholar')
        # green: mentorship lineage
        edges.append({'source': m_id, 'target': s_id, 'type': 'green',
                      'rel': 'mentorship', 'weight': 1, 'year': yr})
        if muassasa:
            i_id = 'i:' + muassasa.lower()
            add_node(i_id, muassasa, 'institution')
            coord = _geocode_institution(muassasa)
            if coord and 'lat' not in nodes[i_id]:
                nodes[i_id]['lat'], nodes[i_id]['lng'] = coord
            inst_members.setdefault(i_id, set()).add(s_id)

    # blue: institutional collaboration proxy — connect the institution hub to
    # its scholars (drill-down edges), capped to keep the graph readable.
    for i_id, members in inst_members.items():
        for s_id in list(members)[:80]:
            edges.append({'source': i_id, 'target': s_id, 'type': 'blue',
                          'rel': 'institutional', 'weight': 1, 'year': year_to})

    return jsonify({
        'nodes': list(nodes.values()),
        'edges': edges,
        'meta': {'spec': spec, 'year_from': year_from, 'year_to': year_to,
                 'ego': ego_name, 'node_count': len(nodes), 'edge_count': len(edges)},
    })


# ---------------------------------------------------------------------------
# Admin: fix existing data quality
# ---------------------------------------------------------------------------

@data_bp.route('/admin/fix-existing-data', methods=['POST'])
@login_required
def fix_existing_data():
    from flask_login import current_user
    if not getattr(current_user, 'is_admin', False):
        return jsonify({'error': 'Admin only'}), 403

    fixed = {'daraja_phd': 0, 'daraja_dsc': 0, 'olim_cleaned': 0}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Fix PhD daraja
            cur.execute("""
                UPDATE dissertations
                SET daraja = 'PhD'
                WHERE (mavzu ILIKE '%фалсафа доктори%'
                       OR mavzu ILIKE '%(PhD)%'
                       OR link   ILIKE '%PhD%'
                       OR olim   ILIKE '%нинг фалсафа%')
                  AND UPPER(TRIM(daraja)) != 'PHD'
            """)
            fixed['daraja_phd'] = cur.rowcount

            # Fix DSc daraja
            cur.execute("""
                UPDATE dissertations
                SET daraja = 'DSc'
                WHERE (mavzu ILIKE '%фан доктори%'
                       OR mavzu ILIKE '%(DSc)%'
                       OR link   ILIKE '%DSc%')
                  AND UPPER(TRIM(daraja)) != 'DSC'
            """)
            fixed['daraja_dsc'] = cur.rowcount

            # Fix olim names containing "нинг" — fetch then update
            cur.execute(
                "SELECT id, olim FROM dissertations WHERE olim LIKE '%нинг%'"
            )
            dirty = cur.fetchall()
            for row_id, raw_olim in dirty:
                clean = _extract_olim(raw_olim)
                if clean and clean != raw_olim and 'нинг' not in clean:
                    cur.execute(
                        "UPDATE dissertations SET olim = %s WHERE id = %s",
                        (clean[:200], row_id)
                    )
                    fixed['olim_cleaned'] += 1

        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

    return jsonify(fixed)
