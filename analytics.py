from flask import Blueprint, jsonify
from flask_login import login_required
from collections import Counter, defaultdict
from datetime import datetime
from data import get_connection, query_dissertations

analytics_bp = Blueprint('analytics', __name__)


def _normalize_text(value):
    return str(value or "").strip()


def _parse_month(date_text):
    if not date_text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_text, fmt).strftime("%Y-%m")
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(date_text).strftime("%Y-%m")
    except Exception:
        return None


@analytics_bp.route('/stats-json')
def stats_json():
    sql = '''
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE UPPER(TRIM(daraja)) = 'PHD') AS phd,
            COUNT(*) FILTER (WHERE UPPER(TRIM(daraja)) = 'DSC') AS dsc,
            COUNT(DISTINCT NULLIF(TRIM(muassasa), '')) AS muassasalar,
            COUNT(DISTINCT NULLIF(TRIM(olim), '')) AS olim
        FROM dissertations
    '''
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
            return jsonify({
                "total": row[0] or 0,
                "phd": row[1] or 0,
                "dsc": row[2] or 0,
                "muassasalar": row[3] or 0,
                "olim": row[4] or 0
            })
    finally:
        conn.close()


@analytics_bp.route('/analytics-data')
@login_required
def analytics_data():
    rows = query_dissertations("", "", "", "", "id", "asc")
    muassasa_counter = Counter(_normalize_text(row.get("Muassasa")) for row in rows if row.get("Muassasa"))
    daraja_counter = Counter(_normalize_text(row.get("Daraja")) for row in rows if row.get("Daraja"))
    top_muassasalar = [
        {"muassasa": name, "count": count}
        for name, count in muassasa_counter.most_common(20)
    ]
    daraja_counts = [
        {"daraja": name, "count": count}
        for name, count in daraja_counter.most_common()
    ]

    trend_counter = Counter()
    for row in rows:
        month = _parse_month(_normalize_text(row.get("Sana")))
        if month:
            trend_counter[month] += 1
    trend_data = [
        {"period": period, "count": trend_counter[period]}
        for period in sorted(trend_counter)
    ]

    ixtisoslik_counter = Counter(_normalize_text(row.get("Ixtisoslik")) for row in rows if row.get("Ixtisoslik"))
    top_ixtisosliklar = [
        {"ixtisoslik": name, "count": count}
        for name, count in ixtisoslik_counter.most_common(15)
    ]

    top15_unis = [name for name, _ in muassasa_counter.most_common(15)]
    heatmap_counts = defaultdict(lambda: defaultdict(int))
    heatmap_darajalar = []
    for row in rows:
        muassasa = _normalize_text(row.get("Muassasa"))
        daraja = _normalize_text(row.get("Daraja"))
        if muassasa in top15_unis and daraja:
            heatmap_counts[muassasa][daraja] += 1
            if daraja not in heatmap_darajalar:
                heatmap_darajalar.append(daraja)

    heatmap = {
        "muassasalar": top15_unis,
        "darajalar": heatmap_darajalar,
        "data": [
            [heatmap_counts[muassasa].get(daraja, 0) for daraja in heatmap_darajalar]
            for muassasa in top15_unis
        ]
    }

    return jsonify({
        "top_muassasalar": top_muassasalar,
        "daraja_ratio": daraja_counts,
        "trend": trend_data,
        "top_ixtisosliklar": top_ixtisosliklar,
        "heatmap": heatmap
    })
