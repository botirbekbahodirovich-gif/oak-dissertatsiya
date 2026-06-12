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
        "supervisor_count": int(row.get("supervisor_count") or 1),
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


def _build_filter_clause(search, daraja, muassasa, ixtisoslik,
                         fan_tarmoqi='', ilmiy_kengash='', sana_yil=''):
    search       = (search       or '').strip()
    daraja       = (daraja       or '').strip()
    muassasa     = (muassasa     or '').strip()
    ixtisoslik   = (ixtisoslik   or '').strip()
    fan_tarmoqi  = (fan_tarmoqi  or '').strip()
    ilmiy_kengash= (ilmiy_kengash or '').strip()
    sana_yil     = (sana_yil     or '').strip()
    clauses = []
    params  = []
    if search:
        text = f"%{search}%"
        clauses.append(
            "(TRIM(olim) ILIKE %s OR TRIM(mavzu) ILIKE %s OR "
            "TRIM(ilmiy_rahbar) ILIKE %s OR TRIM(muassasa) ILIKE %s OR "
            "TRIM(ixtisoslik) ILIKE %s OR TRIM(COALESCE(ixtisoslik_nomi,'')) ILIKE %s)"
        )
        params.extend([text] * 6)
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
        params.append(f"{sana_yil}%")
    clause = " WHERE " + " AND ".join(clauses) if clauses else ""
    return clause, params


def _map_sort_column(sort_by):
    mapping = {
        "Sana": "sana",
        "Daraja": "daraja",
        "Olim": "olim",
        "Mavzu": "mavzu",
        "Ixtisoslik": "ixtisoslik",
        "Muassasa": "muassasa",
        "Ilmiy_rahbar": "ilmiy_rahbar",
        "Link": "link",
        "id": "id"
    }
    return mapping.get(sort_by, "id")


def load_data():
    sql = (
        'SELECT id, oak_id, sana AS "Sana", daraja AS "Daraja", olim AS "Olim", '
        'mavzu AS "Mavzu", ixtisoslik AS "Ixtisoslik", muassasa AS "Muassasa", '
        'ilmiy_rahbar AS "Ilmiy_rahbar", link AS "Link" '
        'FROM dissertations ORDER BY id'
    )
    return _query_rows(sql)


def count_dissertations(search, daraja, muassasa, ixtisoslik,
                        fan_tarmoqi='', ilmiy_kengash='', sana_yil=''):
    clause, params = _build_filter_clause(
        search, daraja, muassasa, ixtisoslik, fan_tarmoqi, ilmiy_kengash, sana_yil)
    sql = 'SELECT COUNT(*) FROM dissertations' + clause
    return _query_scalar(sql, params) or 0


# Default sort direction per column: Sana/id start DESC, text columns start ASC
_COL_DEFAULT_DIR = {
    "id":           "desc",
    "sana":         "desc",
    "olim":         "asc",
    "mavzu":        "asc",
    "daraja":       "asc",
    "ixtisoslik":   "asc",
    "muassasa":     "asc",
    "ilmiy_rahbar": "asc",
}


def query_dissertations(search, daraja, muassasa, ixtisoslik, sort_by, sort_dir,
                        page=None, per_page=None,
                        fan_tarmoqi='', ilmiy_kengash='', sana_yil=''):
    clause, params = _build_filter_clause(
        search, daraja, muassasa, ixtisoslik, fan_tarmoqi, ilmiy_kengash, sana_yil)
    sort_col = _map_sort_column(sort_by)
    # Honour explicit direction; fall back to per-column default; ultimate default is desc (id DESC)
    if sort_dir and str(sort_dir).lower() in ('asc', 'desc'):
        effective_dir = str(sort_dir).lower()
    else:
        effective_dir = _COL_DEFAULT_DIR.get(sort_col, 'desc')
    pagination_clause = ''
    if page is not None and per_page is not None:
        try:
            page = max(1, int(page))
        except (TypeError, ValueError):
            page = 1
        try:
            per_page = max(1, int(per_page))
        except (TypeError, ValueError):
            per_page = 50
        pagination_clause = ' LIMIT %s OFFSET %s'
        params = params + [per_page, (page - 1) * per_page]
    sql = (
        'SELECT d.id, d.oak_id, d.sana AS "Sana", d.daraja AS "Daraja", d.olim AS "Olim", '
        'd.mavzu AS "Mavzu", d.ixtisoslik AS "Ixtisoslik", d.muassasa AS "Muassasa", '
        'd.ilmiy_rahbar AS "Ilmiy_rahbar", d.link AS "Link", '
        'COUNT(*) OVER (PARTITION BY TRIM(d.ilmiy_rahbar)) AS supervisor_count '
        f'FROM dissertations d{clause} ORDER BY {sort_col} {effective_dir}' + pagination_clause
    )
    return _query_rows(sql, params)


_DISTINCT_LIMITS = {
    "daraja": 20, "ixtisoslik": 500, "fan_tarmoqi": 100,
    "muassasa": 500, "ilmiy_kengash": 200,
}

def _distinct_values(column, limit=None):
    if column not in _DISTINCT_LIMITS:
        return []
    lim = limit or _DISTINCT_LIMITS[column]
    sql = (
        f"SELECT DISTINCT TRIM({column}) AS val FROM dissertations "
        f"WHERE {column} IS NOT NULL AND TRIM({column}) <> '' "
        f"ORDER BY val LIMIT {int(lim)}"
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [row[0] for row in cur.fetchall() if row[0] is not None]
    finally:
        conn.close()


def _distinct_years():
    sql = """
        SELECT DISTINCT SUBSTRING(TRIM(sana), 1, 4) AS yr
        FROM dissertations
        WHERE sana IS NOT NULL AND TRIM(sana) ~ '^[0-9]{4}'
        ORDER BY yr DESC
        LIMIT 50
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
        f"data_{a.get('page',1)}_{a.get('per_page',50)}"
        f"_{a.get('search','')}_{a.get('daraja','')}_{a.get('muassasa','')}_{a.get('ixtisoslik','')}"
        f"_{a.get('fan_tarmoqi','')}_{a.get('ilmiy_kengash','')}_{a.get('sana_yil','')}"
        f"_{a.get('sort_by','id')}_{a.get('sort_dir','desc')}"
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
        per_page = int(a.get("per_page", 50))
    except ValueError:
        per_page = 50

    sort_by  = a.get("sort_by",  "id")
    sort_dir = a.get("sort_dir", "desc")
    total = count_dissertations(search, daraja, muassasa, ixtisoslik,
                                fan_tarmoqi, ilmiy_kengash, sana_yil)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    rows = query_dissertations(
        search, daraja, muassasa, ixtisoslik,
        sort_by, sort_dir,
        page, per_page,
        fan_tarmoqi, ilmiy_kengash, sana_yil
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


@data_bp.route('/export')
@login_required
def export():
    rows = query_dissertations(
        request.args.get("search", "").strip(),
        request.args.get("daraja", "").strip(),
        request.args.get("muassasa", "").strip(),
        request.args.get("ixtisoslik", "").strip(),
        request.args.get("sort_by", "id"),
        request.args.get("sort_dir", "desc")
    )
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["id", "Sana", "Daraja", "Olim", "Mavzu", "Ixtisoslik", "Muassasa", "Ilmiy_rahbar", "Link"])
    writer.writeheader()
    writer.writerows(rows)
    data = buf.getvalue().encode("utf-8-sig")
    return send_file(io.BytesIO(data), mimetype="text/csv", as_attachment=True,
                     download_name="dissertatsiyalar_filtrlangan.csv")


@data_bp.route('/export-xlsx')
@login_required
def export_xlsx():
    rows = query_dissertations(
        request.args.get("search", "").strip(),
        request.args.get("daraja", "").strip(),
        request.args.get("muassasa", "").strip(),
        request.args.get("ixtisoslik", "").strip(),
        request.args.get("sort_by", "id"),
        request.args.get("sort_dir", "desc")
    )
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["id", "Sana", "Daraja", "Olim", "Mavzu", "Ixtisoslik", "Muassasa", "Ilmiy_rahbar", "Link"])
    for row in rows:
        ws.append([
            row.get("id"), row.get("Sana"), row.get("Daraja"), row.get("Olim"),
            row.get("Mavzu"), row.get("Ixtisoslik"), row.get("Muassasa"),
            row.get("Ilmiy_rahbar"), row.get("Link")
        ])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True,
                     download_name="dissertatsiyalar_filtrlangan.xlsx")


def _summary_stats(rows):
    return {
        'total': len(rows),
        'phd': sum(1 for row in rows if str(row.get('Daraja', '')).strip().upper() == 'PHD'),
        'dsc': sum(1 for row in rows if str(row.get('Daraja', '')).strip().upper() == 'DSC')
    }


@data_bp.route('/dissertation/<int:id>')
@login_required
def dissertation(id):
    row = get_dissertation_detail_by_id(id)
    if not row:
        abort(404)
    row['Olim_short'] = clean_olim_name(row.get('Olim', ''))
    return render_template('dissertation.html', row=row, id=id)


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
