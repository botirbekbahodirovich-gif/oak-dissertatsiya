import os
import csv
import io
from dotenv import load_dotenv
load_dotenv()
import openpyxl
from flask import Blueprint, jsonify, request, send_file, render_template, abort
from flask_login import login_required
try:
    import psycopg2
    import psycopg2.extras as psycopg2_extras
except Exception:
    psycopg2 = None
    psycopg2_extras = None

REQUIRED_COLUMNS = {
    "Sana", "Daraja", "Olim", "Mavzu",
    "Ixtisoslik", "Muassasa", "Ilmiy_rahbar", "Link"
}

SORTABLE_COLUMNS = {"Sana", "Daraja", "Olim", "Mavzu", "Ixtisoslik", "Muassasa", "Ilmiy_rahbar", "id"}


def get_database_url():
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise RuntimeError('DATABASE_URL is not configured.')
    return url


def get_connection():
    if not psycopg2:
        raise RuntimeError('psycopg2 is required for PostgreSQL support.')
    return psycopg2.connect(get_database_url(), cursor_factory=psycopg2_extras.RealDictCursor)


def normalize_row(row):
    if row is None:
        return None
    return {
        "id": row.get("id"),
        "Sana": str(row.get("Sana") or "").strip(),
        "Daraja": str(row.get("Daraja") or "").strip(),
        "Olim": str(row.get("Olim") or "").strip(),
        "Mavzu": str(row.get("Mavzu") or "").strip(),
        "Ixtisoslik": str(row.get("Ixtisoslik") or "").strip(),
        "Muassasa": str(row.get("Muassasa") or "").strip(),
        "Ilmiy_rahbar": str(row.get("Ilmiy_rahbar") or "").strip(),
        "Link": str(row.get("Link") or "").strip(),
    }


def _query_rows(sql, params=None):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return [normalize_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _build_filter_clause(search, daraja, muassasa, ixtisoslik):
    clauses = []
    params = []
    if search:
        text = f"%{search}%"
        columns = [
            "sana", "daraja", "olim", "mavzu",
            "ixtisoslik", "muassasa", "ilmiy_rahbar", "link"
        ]
        clauses.append("(" + " OR ".join(f"{col} ILIKE %s" for col in columns) + ")")
        params.extend([text] * len(columns))
    if daraja:
        clauses.append("daraja ILIKE %s")
        params.append(daraja)
    if muassasa:
        clauses.append("muassasa ILIKE %s")
        params.append(muassasa)
    if ixtisoslik:
        clauses.append("ixtisoslik ILIKE %s")
        params.append(ixtisoslik)
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
        'SELECT id, sana AS "Sana", daraja AS "Daraja", olim AS "Olim", '
        'mavzu AS "Mavzu", ixtisoslik AS "Ixtisoslik", muassasa AS "Muassasa", '
        'ilmiy_rahbar AS "Ilmiy_rahbar", link AS "Link" '
        'FROM dissertations ORDER BY id'
    )
    return _query_rows(sql)


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


@data_bp.route('/data')
@login_required
def data():
    rows = load_data()
    search = request.args.get("search", "").strip()
    daraja = request.args.get("daraja", "").strip()
    muassasa = request.args.get("muassasa", "").strip()
    ixtisoslik = request.args.get("ixtisoslik", "").strip()
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    try:
        per_page = int(request.args.get("per_page", 50))
    except ValueError:
        per_page = 50

    sort_by = request.args.get("sort_by", "Sana")
    sort_dir = request.args.get("sort_dir", "asc")
    rows = apply_filters(rows, search, daraja, muassasa, ixtisoslik)
    rows = apply_sort(rows, sort_by, sort_dir)
    total = len(rows)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start, end = (page - 1) * per_page, page * per_page

    return jsonify({
        "records": rows[start:end],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages
    })


@data_bp.route('/filters')
@login_required
def filters():
    rows = load_data()
    darajalar = sorted({row.get("Daraja", "") for row in rows if row.get("Daraja", "")})
    muassasalar = sorted({row.get("Muassasa", "") for row in rows if row.get("Muassasa", "")})
    ixtisosliklar = sorted({row.get("Ixtisoslik", "") for row in rows if row.get("Ixtisoslik", "")})
    return jsonify({
        "darajalar": darajalar,
        "muassasalar": muassasalar,
        "ixtisosliklar": ixtisosliklar
    })


@data_bp.route('/export')
@login_required
def export():
    rows = load_data()
    rows = apply_filters(
        rows,
        request.args.get("search", "").strip(),
        request.args.get("daraja", "").strip(),
        request.args.get("muassasa", "").strip(),
        request.args.get("ixtisoslik", "").strip()
    )
    rows = apply_sort(rows, request.args.get("sort_by", "Sana"), request.args.get("sort_dir", "asc"))
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
    rows = load_data()
    rows = apply_filters(
        rows,
        request.args.get("search", "").strip(),
        request.args.get("daraja", "").strip(),
        request.args.get("muassasa", "").strip(),
        request.args.get("ixtisoslik", "").strip()
    )
    rows = apply_sort(rows, request.args.get("sort_by", "Sana"), request.args.get("sort_dir", "asc"))
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
    rows = load_data()
    row = next((item for item in rows if item.get('id') == id), None)
    if not row:
        abort(404)
    return render_template('dissertation.html', row=row, id=id)


@data_bp.route('/author/<path:name>')
@login_required
def author(name):
    rows = [row for row in load_data() if row.get('Olim') == name]
    if not rows:
        abort(404)
    return render_template('author.html', name=name, rows=rows, stats=_summary_stats(rows))


@data_bp.route('/supervisor/<path:name>')
@login_required
def supervisor(name):
    rows = [row for row in load_data() if row.get('Ilmiy_rahbar') == name]
    if not rows:
        abort(404)
    return render_template('supervisor.html', name=name, rows=rows, stats=_summary_stats(rows))


@data_bp.route('/university/<path:name>')
@login_required
def university(name):
    rows = [row for row in load_data() if row.get('Muassasa') == name]
    if not rows:
        abort(404)
    return render_template('university.html', name=name, rows=rows, stats=_summary_stats(rows))


@data_bp.route('/specialization/<path:code>')
@login_required
def specialization(code):
    rows = [row for row in load_data() if row.get('Ixtisoslik') == code]
    if not rows:
        abort(404)
    return render_template('specialization.html', code=code, rows=rows, stats=_summary_stats(rows))
