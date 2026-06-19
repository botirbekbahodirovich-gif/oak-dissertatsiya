import os
import re
import csv
import io
import html as html_module
import threading
from dotenv import load_dotenv
load_dotenv()
import openpyxl
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')
OAK_API_KEY  = os.environ.get('OAK_API_KEY', '')
from flask import Blueprint, jsonify, request, send_file, render_template, abort
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


# ── Connection pool ───────────────────────────────────────────────────────────

_db_pool: 'pg_pool.ThreadedConnectionPool | None' = None
_db_pool_lock = threading.Lock()


def _get_pool():
    global _db_pool
    if _db_pool is None:
        with _db_pool_lock:
            if _db_pool is None and pg_pool is not None:
                url = get_database_url()
                if url:
                    _db_pool = pg_pool.ThreadedConnectionPool(2, 10, url)
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
        return _PooledConn(p.getconn(), p)
    return psycopg2.connect(get_database_url())


def get_supervisor_counts() -> dict:
    """Returns {trimmed_name: count} for all supervisors. Cached 5 min."""
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
    cache.set(key, result, timeout=300)
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


def _build_filter_clause(search, daraja, muassasa, ixtisoslik,
                         fan_tarmoqi='', ilmiy_kengash='', sana_yil='', scope='all'):
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
        clauses.append("TRIM(ixtisoslik) ILIKE %s")
        params.append(ixtisoslik)
    if fan_tarmoqi:
        clauses.append("TRIM(COALESCE(fan_tarmoqi,'')) = %s")
        params.append(fan_tarmoqi)
    if ilmiy_kengash:
        clauses.append("TRIM(COALESCE(ilmiy_kengash,'')) ILIKE %s")
        params.append(ilmiy_kengash)
    if sana_yil:
        clauses.append("sana LIKE %s")
        params.append(f"%{sana_yil}%")
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
                        fan_tarmoqi='', ilmiy_kengash='', sana_yil='', scope='all'):
    clause, params = _build_filter_clause(
        search, daraja, muassasa, ixtisoslik, fan_tarmoqi, ilmiy_kengash, sana_yil, scope)
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
                        fan_tarmoqi='', ilmiy_kengash='', sana_yil='', scope='all'):
    clause, params = _build_filter_clause(
        search, daraja, muassasa, ixtisoslik, fan_tarmoqi, ilmiy_kengash, sana_yil, scope)
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


_DISTINCT_LIMITS = {
    "daraja": 20, "ixtisoslik": 500, "fan_tarmoqi": 100,
    "muassasa": 500, "ilmiy_kengash": 200,
}

def _distinct_values(column, limit=None):
    if column not in _DISTINCT_LIMITS:
        return []
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


def _data_cache_key():
    a = request.args
    return (
        f"data_{a.get('page',1)}_{a.get('per_page',25)}"
        f"_{a.get('search','')}_{a.get('daraja','')}_{a.get('muassasa','')}_{a.get('ixtisoslik','')}"
        f"_{a.get('fan_tarmoqi','')}_{a.get('ilmiy_kengash','')}_{a.get('sana_yil','')}"
        f"_{a.get('scope','all')}"
    )


@data_bp.route('/data')
@login_required
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
    total = count_dissertations(search, daraja, muassasa, ixtisoslik,
                                fan_tarmoqi, ilmiy_kengash, sana_yil, scope)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    rows = query_dissertations(
        search, daraja, muassasa, ixtisoslik,
        page=page, per_page=per_page,
        fan_tarmoqi=fan_tarmoqi, ilmiy_kengash=ilmiy_kengash,
        sana_yil=sana_yil, scope=scope
    )

    return jsonify({
        "records": rows,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages
    })


@data_bp.route('/filters')
@login_required
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


@data_bp.route('/search-stats')
@login_required
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
@login_required
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
@login_required
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


@data_bp.route('/olim/<path:name>')
@login_required
def olim_profile(name):
    term = name.strip()
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
    return render_template('olim_profile.html', olim_name=term, dissertations=own, is_owner=is_owner,
                           as_supervisor=as_supervisor, as_opponent=as_opponent,
                           shogirdlar=as_supervisor, opponent_works=as_opponent,
                           stats=stats, profile=profile, maqolalar=maqolalar,
                           konferensiyalar=konferensiyalar, ish_faoliyati=ish_faoliyati, rasmlar=rasmlar)


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
            "SELECT COUNT(*) FROM dissertations WHERE TRIM(ixtisoslik) = TRIM(%s)",
            (code,)
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
@login_required
def dissertation(id):
    row = get_dissertation_detail_by_id(id)
    if not row:
        abort(404)
    row['Olim_short'] = clean_olim_name(row.get('Olim', ''))
    saturation = get_ixtisoslik_saturation(row.get('Ixtisoslik', ''))
    return render_template('dissertation.html', row=row, id=id, saturation=saturation)


@data_bp.route('/author/<path:name>')
@login_required
def author(name):
    rows = get_dissertations_by_field('Olim', name)
    if not rows:
        abort(404)
    return render_template('author.html', name=name, rows=rows, stats=_summary_stats(rows))


@data_bp.route('/supervisor/<path:name>')
@login_required
def supervisor(name):
    rows = get_dissertations_by_field('Ilmiy_rahbar', name)
    if not rows:
        abort(404)
    return render_template('supervisor.html', name=name, rows=rows, stats=_summary_stats(rows))


@data_bp.route('/university/<path:name>')
@login_required
def university(name):
    rows = get_dissertations_by_field('Muassasa', name)
    if not rows:
        abort(404)
    return render_template('university.html', name=name, rows=rows, stats=_summary_stats(rows))


@data_bp.route('/specialization/<path:code>')
@login_required
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
# Admin: fix existing data quality
# ---------------------------------------------------------------------------

@data_bp.route('/admin/fix-existing-data', methods=['POST'])
@login_required
def fix_existing_data():
    from flask_login import current_user
    if not getattr(current_user, 'username', None) or current_user.username != 'admin':
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
