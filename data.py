import io
import os
import pandas as pd
import openpyxl

BASE_DIR = os.path.dirname(__file__)
CSV_PATH = os.path.join(BASE_DIR, "data", "dissertatsiyalar.csv")

SORTABLE_COLUMNS = {"Sana", "Daraja", "Olim", "Mavzu", "Ixtisoslik", "Muassasa", "Ilmiy_rahbar", "id"}

# Simple in-memory cache for CSV data
_csv_cache_df = None
_csv_cache_mtime = None

REQUIRED_COLUMNS = {
    "Sana", "Daraja", "Olim", "Mavzu",
    "Ixtisoslik", "Muassasa", "Ilmiy_rahbar", "Link"
}


def load_data():
    """Load CSV into a cached DataFrame and reload only when the file changes."""
    global _csv_cache_df, _csv_cache_mtime
    try:
        mtime = os.path.getmtime(CSV_PATH)
    except FileNotFoundError:
        # If file doesn't exist, clear cache and return empty df
        _csv_cache_df = pd.DataFrame(columns=[
            "Sana", "Daraja", "Olim", "Mavzu",
            "Ixtisoslik", "Muassasa", "Ilmiy_rahbar", "Link"
        ])
        _csv_cache_mtime = None
        return _csv_cache_df

    # Reload if cache is empty or file modified since last load
    if _csv_cache_df is None or _csv_cache_mtime != mtime:
        df = pd.read_csv(CSV_PATH, dtype=str).fillna("")
        for col in df.columns:
            df[col] = df[col].astype(str).str.strip()
        _csv_cache_df = df
        _csv_cache_mtime = mtime

    return _csv_cache_df


def apply_filters(df, search, daraja, muassasa, ixtisoslik):
    if search:
        lo = search.lower()
        df = df[df.apply(lambda r: r.astype(str).str.lower().str.contains(lo).any(), axis=1)]
    if daraja:
        df = df[df["Daraja"].str.upper() == daraja.upper()]
    if muassasa:
        df = df[df["Muassasa"] == muassasa]
    if ixtisoslik:
        df = df[df["Ixtisoslik"] == ixtisoslik]
    return df


def apply_sort(df, sort_by, sort_dir):
    if not sort_by or sort_by not in SORTABLE_COLUMNS:
        return df
    asc = (sort_dir or "asc").lower() != "desc"
    if sort_by == "id":
        return df.sort_values(by=sort_by, ascending=asc, kind="mergesort")

    if df[sort_by].dtype == object:
        return df.sort_values(by=sort_by, ascending=asc, kind="mergesort", key=lambda col: col.str.lower())
    return df.sort_values(by=sort_by, ascending=asc, kind="mergesort")


from flask import Blueprint, jsonify, request, send_file, render_template, abort
from flask_login import login_required
import io

data_bp = Blueprint('data', __name__)


@data_bp.route('/data')
@login_required
def data():
    df = load_data()
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
    df = apply_filters(df, search, daraja, muassasa, ixtisoslik)
    df = df.reset_index(drop=False).rename(columns={"index": "id"})
    df["id"] = df["id"] + 1
    df = apply_sort(df, sort_by, sort_dir)
    total = len(df)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start, end = (page - 1) * per_page, page * per_page

    return jsonify({
        "records":     df.iloc[start:end].to_dict(orient="records"),
        "total":       total,
        "page":        page,
        "per_page":    per_page,
        "total_pages": total_pages
    })


@data_bp.route('/filters')
@login_required
def filters():
    df = load_data()
    return jsonify({
        "darajalar":    [d for d in sorted(df["Daraja"].unique()) if d],
        "muassasalar":  [m for m in sorted(df["Muassasa"].unique()) if m],
        "ixtisosliklar":[i for i in sorted(df["Ixtisoslik"].unique()) if i]
    })


@data_bp.route('/export')
@login_required
def export():
    df = load_data()
    df = apply_filters(
        df,
        request.args.get("search", "").strip(),
        request.args.get("daraja", "").strip(),
        request.args.get("muassasa", "").strip(),
        request.args.get("ixtisoslik", "").strip()
    )
    sort_by = request.args.get("sort_by", "Sana")
    sort_dir = request.args.get("sort_dir", "asc")
    df = df.reset_index(drop=False).rename(columns={"index": "id"})
    df["id"] = df["id"] + 1
    df = apply_sort(df, sort_by, sort_dir)
    buf = io.BytesIO(df.to_csv(index=False).encode("utf-8-sig"))
    buf.seek(0)
    return send_file(buf, mimetype="text/csv", as_attachment=True,
                     download_name="dissertatsiyalar_filtrlangan.csv")


@data_bp.route('/export-xlsx')
@login_required
def export_xlsx():
    df = load_data()
    df = apply_filters(
        df,
        request.args.get("search", "").strip(),
        request.args.get("daraja", "").strip(),
        request.args.get("muassasa", "").strip(),
        request.args.get("ixtisoslik", "").strip()
    )
    sort_by = request.args.get("sort_by", "Sana")
    sort_dir = request.args.get("sort_dir", "asc")
    df = df.reset_index(drop=False).rename(columns={"index": "id"})
    df["id"] = df["id"] + 1
    df = apply_sort(df, sort_by, sort_dir)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
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
    df = load_data()
    if id < 1 or id > len(df):
        abort(404)
    row = df.iloc[id - 1].to_dict()
    # Provide row id for back links
    return render_template('dissertation.html', row=row, id=id)


@data_bp.route('/author/<path:name>')
@login_required
def author(name):
    df = load_data()
    rows = df[df['Olim'] == name].to_dict(orient="records")
    if not rows:
        abort(404)
    return render_template('author.html', name=name, rows=rows, stats=_summary_stats(rows))


@data_bp.route('/supervisor/<path:name>')
@login_required
def supervisor(name):
    df = load_data()
    rows = df[df['Ilmiy_rahbar'] == name].to_dict(orient="records")
    if not rows:
        abort(404)
    return render_template('supervisor.html', name=name, rows=rows, stats=_summary_stats(rows))


@data_bp.route('/university/<path:name>')
@login_required
def university(name):
    df = load_data()
    rows = df[df['Muassasa'] == name].to_dict(orient="records")
    if not rows:
        abort(404)
    return render_template('university.html', name=name, rows=rows, stats=_summary_stats(rows))


@data_bp.route('/specialization/<path:code>')
@login_required
def specialization(code):
    df = load_data()
    rows = df[df['Ixtisoslik'] == code].to_dict(orient="records")
    if not rows:
        abort(404)
    return render_template('specialization.html', code=code, rows=rows, stats=_summary_stats(rows))
