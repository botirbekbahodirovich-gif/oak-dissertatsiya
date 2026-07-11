"""Ilmiy kengashlar katalogi (councils_bp).

Olim ixtisoslik shifrini kiritadi → shu shifrni qabul qiladigan barcha ilmiy
kengashlar chiqadi. Manba — dissertations jadvalidan agregatsiya
(GROUP BY ilmiy_kengash, ilmiy_kengash_raqami). Qimmat so'rov bir marta
bajarilib, extensions.cache da 10 daqiqa keshlanadi (`_council_index`); API
keshdan Python'da filtrlaydi (advisors.py bilan bir xil naqsh), shuning uchun
debounce-fetch DB'ni urmaydi.

Routes:
  GET /api/v1/councils        — JSON ro'yxat (spec / daraja / search filtrlari).
  GET /councils               — katalog sahifasi (jadval JS orqali yuklanadi).
  GET /councils/<raqam>       — bitta kengash: olimlar, ixtisosliklar, yillik
                                 himoyalar dinamikasi. raqam '/' saqlaydi →
                                 <path:> konverter.
"""
import re

from flask import Blueprint, render_template, request, jsonify, abort

from extensions import cache
from data import get_connection

try:
    import psycopg2.extras as psycopg2_extras
except Exception:  # pragma: no cover — psycopg2 always present in prod
    psycopg2_extras = None

councils_bp = Blueprint('councils', __name__)

INDEX_CACHE_TTL = 600           # 10 daqiqa — spec bo'yicha qimmat agregatsiya keshi
API_LIMIT = 300                 # katalog javobida qaytariladigan maksimum qator

# daraja ILIKE naqshlari (advisors.py bilan bir xil — isbotlangan)
_PHD_SQL = "(daraja ILIKE '%%PhD%%' OR daraja ILIKE '%%falsafa%%')"
_DSC_SQL = "(daraja ILIKE '%%DSc%%' OR daraja ILIKE '%%fan doktori%%')"

# sana erkin matn (asosan DD.MM.YYYY, ba'zan YYYY-MM-DD) → saralanuvchi YYYYMMDD
_SANA_KEY = (
    "CASE "
    "WHEN TRIM(sana) ~ '^[0-9]{2}[.][0-9]{2}[.][0-9]{4}$' "
    "THEN regexp_replace(TRIM(sana), '^([0-9]{2})[.]([0-9]{2})[.]([0-9]{4})$', '\\3\\2\\1') "
    "WHEN TRIM(sana) ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' "
    "THEN replace(substring(TRIM(sana) FROM 1 FOR 10), '-', '') "
    "ELSE NULL END"
)

_CODE_RE = re.compile(r'\d{2}\.\d{2}\.\d{2}')
_YEAR_RE = re.compile(r'(19|20)\d{2}')


def _fmt_date(key):
    """'20241227' → '27.12.2024' (ko'rsatish uchun). Noto'g'ri kalitda ''."""
    if key and len(key) == 8 and key.isdigit():
        return f"{key[6:8]}.{key[4:6]}.{key[0:4]}"
    return ''


def _fetch_council_rows():
    """GROUP BY agregatsiya — har bir kengash bo'yicha statistika (bitta so'rov)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2_extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT TRIM(ilmiy_kengash) AS muassasa,
                       COALESCE(TRIM(ilmiy_kengash_raqami), '') AS raqam,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE {_PHD_SQL}) AS phd_count,
                       COUNT(*) FILTER (WHERE {_DSC_SQL}) AS dsc_count,
                       MAX({_SANA_KEY}) AS oxirgi_key,
                       array_agg(DISTINCT TRIM(ixtisoslik))
                           FILTER (WHERE TRIM(COALESCE(ixtisoslik, '')) <> '') AS shifrlar,
                       array_agg(DISTINCT TRIM(ixtisoslik_nomi))
                           FILTER (WHERE TRIM(COALESCE(ixtisoslik_nomi, '')) <> '') AS nomlar
                FROM dissertations
                WHERE ilmiy_kengash IS NOT NULL AND TRIM(ilmiy_kengash) <> ''
                GROUP BY TRIM(ilmiy_kengash), COALESCE(TRIM(ilmiy_kengash_raqami), '')
            """)
            return cur.fetchall()
    finally:
        conn.close()


def _council_index():
    """Kengashlar ro'yxati (himoyalar soni bo'yicha kamayuvchi), 10 daqiqa kesh."""
    cached = cache.get('council_index')
    if cached is not None:
        return cached
    try:
        rows = _fetch_council_rows()
    except Exception:
        rows = []
    councils = []
    for r in rows:
        shifrlar = sorted(r.get('shifrlar') or [])
        oxirgi_key = r.get('oxirgi_key') or ''
        councils.append({
            'muassasa': r['muassasa'],
            'raqam': r['raqam'] or '',
            'shifrlar': shifrlar,
            'shifrlar_text': ', '.join(shifrlar),
            'nomlar': sorted(r.get('nomlar') or []),
            'total': int(r.get('total') or 0),
            'phd_count': int(r.get('phd_count') or 0),
            'dsc_count': int(r.get('dsc_count') or 0),
            'oxirgi_key': oxirgi_key,
            'oxirgi': _fmt_date(oxirgi_key),
            # filtrlash uchun kichik harfli qidiruv maydonlari
            '_muassasa_low': r['muassasa'].lower(),
            '_shifr_low': ' '.join(shifrlar).lower(),
        })
    councils.sort(key=lambda c: c['total'], reverse=True)
    cache.set('council_index', councils, timeout=INDEX_CACHE_TTL)
    return councils


# ── API ────────────────────────────────────────────────────────────────────

@councils_bp.route('/api/v1/councils')
def api_councils():
    spec = (request.args.get('spec') or '').strip().lower()
    daraja = (request.args.get('daraja') or '').strip().lower()
    search = (request.args.get('search') or '').strip().lower()

    items = _council_index()
    if spec:
        items = [c for c in items if spec in c['_shifr_low']]
    if search:
        items = [c for c in items if search in c['_muassasa_low']]
    if daraja == 'phd':
        items = [c for c in items if c['phd_count'] > 0]
    elif daraja == 'dsc':
        items = [c for c in items if c['dsc_count'] > 0]

    total = len(items)
    payload = [{
        'muassasa': c['muassasa'],
        'raqam': c['raqam'],
        'shifrlar': c['shifrlar'],
        'shifrlar_text': c['shifrlar_text'],
        'total': c['total'],
        'phd_count': c['phd_count'],
        'dsc_count': c['dsc_count'],
        'oxirgi': c['oxirgi'],
    } for c in items[:API_LIMIT]]
    return jsonify({'ok': True, 'councils': payload, 'count': total,
                    'shown': len(payload)})


# ── Sahifalar ──────────────────────────────────────────────────────────────

@councils_bp.route('/councils')
def councils_page():
    # Filtr uchun eng ko'p uchraydigan ixtisoslik shifrlari (top 24)
    code_counts = {}
    for c in _council_index():
        for code in c['shifrlar']:
            m = _CODE_RE.search(code)
            if m:
                code_counts[m.group(0)] = code_counts.get(m.group(0), 0) + 1
    top_codes = sorted(code_counts, key=lambda k: -code_counts[k])[:24]
    return render_template('councils.html',
                           top_codes=top_codes,
                           council_count=len(_council_index()))


@councils_bp.route('/councils/<path:raqam>')
def council_detail(raqam):
    raqam = (raqam or '').strip()
    if not raqam:
        abort(404)

    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2_extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, sana, daraja, olim, mavzu, ixtisoslik, ixtisoslik_nomi,
                       ilmiy_rahbar, ilmiy_kengash, link
                FROM dissertations
                WHERE TRIM(COALESCE(ilmiy_kengash_raqami, '')) = %s
                ORDER BY sana DESC
            """, (raqam,))
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        abort(404)

    muassasa = ''
    for r in rows:
        if (r.get('ilmiy_kengash') or '').strip():
            muassasa = r['ilmiy_kengash'].strip()
            break

    students = []
    spec_counts, spec_names = {}, {}
    by_year = {}
    phd = dsc = 0
    for r in rows:
        daraja = (r.get('daraja') or '').strip()
        du = daraja.upper()
        if 'PHD' in du or 'FALSAFA' in du:
            phd += 1
        elif 'DSC' in du or 'DOKTORI' in du:
            dsc += 1
        code = (r.get('ixtisoslik') or '').strip()
        if code:
            spec_counts[code] = spec_counts.get(code, 0) + 1
            nom = (r.get('ixtisoslik_nomi') or '').strip()
            if nom and code not in spec_names:
                spec_names[code] = nom
        m = _YEAR_RE.search(r.get('sana') or '')
        if m:
            by_year[int(m.group(0))] = by_year.get(int(m.group(0)), 0) + 1
        students.append({
            'olim': (r.get('olim') or '').strip(),
            'mavzu': (r.get('mavzu') or '').strip(),
            'daraja': daraja,
            'sana': (r.get('sana') or '').strip(),
            'rahbar': (r.get('ilmiy_rahbar') or '').strip(),
            'ixtisoslik': code,
            'link': (r.get('link') or '').strip(),
        })

    specialties = sorted(
        ({'code': c, 'name': spec_names.get(c, ''), 'count': n}
         for c, n in spec_counts.items()),
        key=lambda s: -s['count'])

    # Yillik dinamika (o'suvchi tartibda), bar chart uchun max bilan
    timeline = [{'year': y, 'count': by_year[y]} for y in sorted(by_year)]
    max_year_count = max((t['count'] for t in timeline), default=0)

    meta_description = (
        f"{muassasa} ilmiy kengashi ({raqam}) — {len(students)} ta himoya, "
        f"{len(specialties)} ixtisoslik. Olimlar, yo'nalishlar va yillik "
        f"dinamika — Olimlar.uz")

    return render_template('council_detail.html',
                           raqam=raqam, muassasa=muassasa,
                           students=students, specialties=specialties,
                           timeline=timeline, max_year_count=max_year_count,
                           total=len(students), phd=phd, dsc=dsc,
                           meta_description=meta_description)
