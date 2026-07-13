"""H-index reyting moduli (ranking_bp).

O'zbekiston olimlari va tashkilotlarining H-index (Scopus, Web of Science,
Google Scholar) reytingi. Ma'lumot admin tomonidan Excel (.xlsx) orqali
yuklanadi; har yuklash "snapshot" sanasi bilan belgilanadi (3-6 oyda
yangilanadi), shuning uchun dinamikani solishtirish mumkin.

Manba: uz.h-index.com (attribution har sahifada majburiy ko'rsatiladi).

Jadvallar (lazy schema, grants.py _ensure_schema naqshi):
  h_index_snapshots     — yuklash sanasi + manba
  h_index_scholars      — olim qatorlari (snapshot_id FK, CASCADE)
  h_index_institutions  — tashkilot qatorlari (snapshot_id FK, CASCADE)

Ism matching (yuklash paytida): dissertations.olim (kirill) → lotinga
o'giriladi, normallashtiriladi (kichik harf, apostrofsiz, tokenlar) va
scholar_name (lotin) bilan solishtiriladi. Mos kelsa olim_match ustuniga
ASL kirill olim ismi yoziladi → profil /olim/<olim_match> ga bog'lanadi.
Tashkilotlar universities.name bilan token-overlap fuzzy matching qilinadi.

Routes:
  GET  /reyting                       — H-index olimlar (asosiy, tab)
  GET  /reyting/tashkilotlar          — H-index tashkilotlar (tab)
  GET  /admin/reyting                 — admin yuklash paneli (GET+POST)
  POST /admin/reyting/delete/<int:id> — snapshot o'chirish (CASCADE)
  GET  /api/v1/ranking/scholars       — JSON (snapshot/search/institution/min_h)
  GET  /api/v1/ranking/institutions   — JSON (snapshot)

Integratsiya helper'lari (data.py, content.py, app.py chaqiradi):
  get_scholar_h_index(olim_name)      — olim profili badge'i uchun
  get_institution_h_index(uni_name)   — universitet profili bloki uchun
  get_top_scholars(limit)             — bosh sahifa vidjeti uchun
"""
import re

from flask import (Blueprint, jsonify, request, render_template,
                   redirect, abort, flash)
from flask_login import login_required, current_user

from extensions import cache
from data import get_connection

ranking_bp = Blueprint('ranking', __name__)

SOURCE_NAME = 'uz.h-index.com'
_CACHE_TTL = 600          # 10 daqiqa (councils.py naqshi)
_schema_ready = False


# ── Schema (lazy, idempotent) ───────────────────────────────────────────────

def _ensure_schema(cur):
    global _schema_ready
    if _schema_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS h_index_snapshots (
            id SERIAL PRIMARY KEY,
            snapshot_date DATE NOT NULL,
            source TEXT DEFAULT 'uz.h-index.com',
            notes TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS h_index_scholars (
            id SERIAL PRIMARY KEY,
            snapshot_id INTEGER REFERENCES h_index_snapshots(id) ON DELETE CASCADE,
            rank INTEGER,
            scholar_name TEXT NOT NULL,
            scholar_name_cyrillic TEXT,
            institution TEXT,
            h_scopus INTEGER,
            h_wos INTEGER,
            h_scholar INTEGER,
            orcid TEXT,
            olim_match TEXT,
            UNIQUE (snapshot_id, scholar_name)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS h_index_institutions (
            id SERIAL PRIMARY KEY,
            snapshot_id INTEGER REFERENCES h_index_snapshots(id) ON DELETE CASCADE,
            rank INTEGER,
            institution_name TEXT NOT NULL,
            category TEXT,
            h_scopus INTEGER,
            h_wos INTEGER,
            h_scholar INTEGER,
            national_h_index INTEGER,
            university_match TEXT,
            UNIQUE (snapshot_id, institution_name)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_hidx_sch_snap "
                "ON h_index_scholars(snapshot_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_hidx_sch_match "
                "ON h_index_scholars(LOWER(olim_match))")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_hidx_inst_snap "
                "ON h_index_institutions(snapshot_id)")
    _schema_ready = True


# ── Normalizatsiya / matching yordamchilari ─────────────────────────────────

_APOS = "'ʻ`’ʼ"


def _to_int(v):
    """Excel katakcha → int yoki None (bo'sh/xato → None)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            return int(v)
        except (ValueError, OverflowError):
            return None
    s = str(v).strip()
    if not s:
        return None
    m = re.search(r'-?\d+', s.replace(' ', ''))
    return int(m.group(0)) if m else None


def _norm(s):
    """Kichik harf, apostrofsiz, faqat [a-z0-9] va probel; tokenlarga bo'linadi."""
    s = (s or '')
    for a in _APOS:
        s = s.replace(a, '')
    s = s.lower()
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return s.strip()


def _key(s):
    """Tartib saqlangan kalit: 'Aziz Karimov' → 'azizkarimov'."""
    return _norm(s).replace(' ', '')


def _skey(s):
    """Tartibga befarq kalit (tokenlar saralanadi): ism/familiya joyi muhim emas."""
    return ''.join(sorted(_norm(s).split()))


# tashkilot nomidagi umumiy (ajratmaydigan) so'zlar — matchingda tashlanadi
_INST_STOP = {
    'universiteti', 'universitet', 'university', 'institut', 'instituti',
    'institute', 'nomidagi', 'nomli', 'davlat', 'davlati', 'milliy',
    'national', 'akademiyasi', 'akademiya', 'academy', 'markaz', 'markazi',
    'center', 'centre', 'oliy', 'texnika', 'texnologiya', 'ilmiy', 'tadqiqot',
    'respublika', 'respublikasi',
}


def _inst_tokens(s):
    """Tashkilot nomining ajratuvchi tokenlari (transliteratsiya + stop-filtr)."""
    from institutions import transliterate
    toks = _norm(transliterate(s or '')).split()
    return {t for t in toks if len(t) >= 4 and t not in _INST_STOP}


def _build_olim_maps():
    """dissertations.olim (kirill) → lotin normal kalit → asl kirill ism."""
    from institutions import transliterate
    by_key, by_skey = {}, {}
    rows = _safe_rows(
        "SELECT DISTINCT TRIM(olim) FROM dissertations "
        "WHERE olim IS NOT NULL AND TRIM(olim) <> ''")
    for (olim,) in rows:
        lat = transliterate(olim)
        k, sk = _key(lat), _skey(lat)
        if k:
            by_key.setdefault(k, olim)
        if sk:
            by_skey.setdefault(sk, olim)
    return by_key, by_skey


def _build_uni_list():
    """(universities.name, ajratuvchi tokenlar) ro'yxati — tashkilot matchingi uchun."""
    unis = []
    for (name,) in _safe_rows(
            "SELECT name FROM universities WHERE is_active = TRUE "
            "AND name IS NOT NULL AND TRIM(name) <> ''"):
        toks = _inst_tokens(name)
        if toks:
            unis.append((name, toks))
    return unis


def _match_olim(name, by_key, by_skey):
    """scholar_name (lotin) → asl kirill olim ismi yoki None."""
    return by_key.get(_key(name)) or by_skey.get(_skey(name))


def _match_uni(inst_name, unis):
    """institution_name → eng mos universities.name (token-overlap ≥ 0.6) yoki None."""
    itoks = _inst_tokens(inst_name)
    if not itoks:
        return None
    best, best_ratio = None, 0.0
    for name, utoks in unis:
        shared = itoks & utoks
        if not shared:
            continue
        ratio = len(shared) / len(utoks)     # universitet tokenlari qanchalik qoplangan
        if ratio > best_ratio:
            best, best_ratio = name, ratio
    return best if best_ratio >= 0.6 else None


def _safe_rows(sql, params=None):
    """DB so'rovi — xatoda bo'sh ro'yxat (public sahifa hech qachon 500 bermaydi).
    _ensure_schema DDL'ni commit qiladi (aks holda close paytida rollback bo'lib,
    _schema_ready True qolgani uchun jadvallar hech qachon yaratilmasdi)."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                conn.commit()          # yangi yaratilgan jadvallarni saqlab qolamiz
                cur.execute(sql, params)
                return cur.fetchall()
        finally:
            conn.close()
    except Exception:
        return []


# ── Snapshot / qatorlarni o'qish (keshli) ───────────────────────────────────

def _latest_snapshot(kind):
    """Berilgan turdagi (>=1 qator) eng so'nggi snapshot (id, 'YYYY-MM-DD') yoki None."""
    key = f'ranking_latest_{kind}_v1'
    cached = cache.get(key)
    if cached is not None:
        return cached or None      # bo'sh tuple → None, lekin kesh saqlanadi
    table = 'h_index_scholars' if kind == 'scholars' else 'h_index_institutions'
    rows = _safe_rows(
        f"SELECT s.id, s.snapshot_date FROM h_index_snapshots s "
        f"WHERE EXISTS (SELECT 1 FROM {table} t WHERE t.snapshot_id = s.id) "
        f"ORDER BY s.snapshot_date DESC, s.id DESC LIMIT 1")
    result = (rows[0][0], str(rows[0][1])) if rows else ()
    cache.set(key, result, timeout=_CACHE_TTL)
    return result or None


def _snapshot_list(kind):
    """Dropdown uchun: shu turdagi qatorlari bor snapshotlar [(id, 'YYYY-MM-DD')]."""
    table = 'h_index_scholars' if kind == 'scholars' else 'h_index_institutions'
    rows = _safe_rows(
        f"SELECT s.id, s.snapshot_date FROM h_index_snapshots s "
        f"WHERE EXISTS (SELECT 1 FROM {table} t WHERE t.snapshot_id = s.id) "
        f"ORDER BY s.snapshot_date DESC, s.id DESC")
    return [{'id': r[0], 'date': str(r[1])} for r in rows]


def _scholars_of(snap_id):
    """Snapshotning barcha olim qatorlari (kesh 10 daqiqa; filtr Python'da)."""
    key = f'ranking_scholars_{snap_id}_v1'
    cached = cache.get(key)
    if cached is not None:
        return cached
    rows = _safe_rows(
        "SELECT rank, scholar_name, scholar_name_cyrillic, institution, "
        "h_scopus, h_wos, h_scholar, orcid, olim_match "
        "FROM h_index_scholars WHERE snapshot_id = %s "
        "ORDER BY rank NULLS LAST, COALESCE(h_scopus,0) DESC, id", (snap_id,))
    items = [{
        'rank': r[0], 'scholar_name': r[1] or '', 'cyrillic': r[2] or '',
        'institution': r[3] or '', 'h_scopus': r[4], 'h_wos': r[5],
        'h_scholar': r[6], 'orcid': r[7] or '', 'olim_match': r[8] or '',
    } for r in rows]
    cache.set(key, items, timeout=_CACHE_TTL)
    return items


def _institutions_of(snap_id):
    key = f'ranking_institutions_{snap_id}_v1'
    cached = cache.get(key)
    if cached is not None:
        return cached
    rows = _safe_rows(
        "SELECT rank, institution_name, category, h_scopus, h_wos, h_scholar, "
        "national_h_index, university_match FROM h_index_institutions "
        "WHERE snapshot_id = %s "
        "ORDER BY rank NULLS LAST, COALESCE(national_h_index,0) DESC, id", (snap_id,))
    items = [{
        'rank': r[0], 'institution_name': r[1] or '', 'category': r[2] or '',
        'h_scopus': r[3], 'h_wos': r[4], 'h_scholar': r[5],
        'national_h_index': r[6], 'university_match': r[7] or '',
    } for r in rows]
    cache.set(key, items, timeout=_CACHE_TTL)
    return items


def _clear_ranking_cache():
    """Yuklash/o'chirishdan keyin barcha reyting keshlarini tozalash."""
    for k in ('ranking_latest_scholars_v1', 'ranking_latest_institutions_v1',
              'ranking_lookup_scholars_v1', 'ranking_lookup_institutions_v1',
              'ranking_top_scholars_v1'):
        cache.delete(k)
    # snapshot qatorlari keshlarini keng oraliqda tozalaymiz (id'lar noma'lum)
    try:
        rows = _safe_rows("SELECT id FROM h_index_snapshots")
        for (sid,) in rows:
            cache.delete(f'ranking_scholars_{sid}_v1')
            cache.delete(f'ranking_institutions_{sid}_v1')
    except Exception:
        pass


# ── Integratsiya helper'lari (profil badge'lari, bosh sahifa) ───────────────

def get_scholar_h_index(olim_name):
    """olim_profile badge'i uchun: {rank, h_scopus, h_wos, h_scholar, orcid,
    snapshot_date} yoki None. Eng so'nggi snapshotda olim_match bo'yicha qidiradi."""
    term = (olim_name or '').strip().lower()
    if not term:
        return None
    lookup = cache.get('ranking_lookup_scholars_v1')
    if lookup is None:
        snap = _latest_snapshot('scholars')
        lookup = {}
        if snap:
            sid, sdate = snap
            for s in _scholars_of(sid):
                if s['olim_match']:
                    lookup.setdefault(s['olim_match'].strip().lower(), {
                        'rank': s['rank'], 'h_scopus': s['h_scopus'],
                        'h_wos': s['h_wos'], 'h_scholar': s['h_scholar'],
                        'orcid': s['orcid'], 'snapshot_date': sdate,
                    })
        cache.set('ranking_lookup_scholars_v1', lookup, timeout=_CACHE_TTL)
    return lookup.get(term)


def get_institution_h_index(*uni_names):
    """university_profile bloki uchun: {rank, category, national_h_index, h_scopus,
    h_wos, h_scholar, snapshot_date} yoki None. Profil sahifasi kanonik (kirill)
    nomni beradi, H-index ro'yxati esa lotin bo'lishi mumkin — shuning uchun
    token-overlap fuzzy matching (transliteratsiya bilan) ishlatiladi. Bir nechta
    nom variantini (kanonik, lotin) berish mumkin."""
    entries = cache.get('ranking_lookup_institutions_v1')
    if entries is None:
        snap = _latest_snapshot('institutions')
        entries = []
        if snap:
            sid, sdate = snap
            for it in _institutions_of(sid):
                toks = _inst_tokens(it['institution_name'])
                if toks:
                    entries.append({'tokens': list(toks), 'data': {
                        'rank': it['rank'], 'category': it['category'],
                        'national_h_index': it['national_h_index'],
                        'h_scopus': it['h_scopus'], 'h_wos': it['h_wos'],
                        'h_scholar': it['h_scholar'], 'snapshot_date': sdate,
                    }})
        cache.set('ranking_lookup_institutions_v1', entries, timeout=_CACHE_TTL)
    best, best_ratio = None, 0.0
    for uni_name in uni_names:
        itoks = _inst_tokens(uni_name)
        if not itoks:
            continue
        for e in entries:
            etoks = set(e['tokens'])
            shared = itoks & etoks
            if not shared:
                continue
            # ikkala yo'nalishda ham qoplanishni talab qilamiz (noto'g'ri moslikni kamaytiradi)
            ratio = len(shared) / max(len(etoks), len(itoks))
            if ratio > best_ratio:
                best, best_ratio = e['data'], ratio
    return best if best_ratio >= 0.6 else None


def get_top_scholars(limit=10):
    """Bosh sahifa vidjeti: eng so'nggi snapshotdan top N olim (rank bo'yicha)."""
    cached = cache.get('ranking_top_scholars_v1')
    if cached is None:
        snap = _latest_snapshot('scholars')
        cached = {'date': '', 'items': []}
        if snap:
            sid, sdate = snap
            cached = {'date': sdate, 'items': _scholars_of(sid)[:20]}
        cache.set('ranking_top_scholars_v1', cached, timeout=_CACHE_TTL)
    return {'date': cached['date'], 'items': cached['items'][:limit]}


# ── Public API (councils.py naqshi: keshdan Python'da filtr) ─────────────────

def _resolve_snapshot(kind):
    """?snapshot=latest|<id> → (id, date) yoki None."""
    raw = (request.args.get('snapshot') or 'latest').strip()
    if raw and raw != 'latest' and raw.isdigit():
        rows = _safe_rows(
            "SELECT id, snapshot_date FROM h_index_snapshots WHERE id = %s",
            (int(raw),))
        if rows:
            return (rows[0][0], str(rows[0][1]))
    return _latest_snapshot(kind)


@ranking_bp.route('/api/v1/ranking/scholars')
def api_scholars():
    snap = _resolve_snapshot('scholars')
    if not snap:
        return jsonify({'ok': True, 'snapshot_date': None, 'count': 0, 'items': []})
    sid, sdate = snap
    items = _scholars_of(sid)
    inst = (request.args.get('institution') or '').strip().lower()
    search = (request.args.get('search') or '').strip().lower()
    min_h = _to_int(request.args.get('min_h'))
    if inst:
        items = [s for s in items if inst in s['institution'].lower()]
    if search:
        items = [s for s in items
                 if search in s['scholar_name'].lower()
                 or search in s['cyrillic'].lower()
                 or search in s['institution'].lower()]
    if min_h:
        items = [s for s in items
                 if max(s['h_scopus'] or 0, s['h_wos'] or 0, s['h_scholar'] or 0) >= min_h]
    total = len(items)
    return jsonify({'ok': True, 'snapshot_date': sdate, 'count': total,
                    'items': items[:1000]})


@ranking_bp.route('/api/v1/ranking/institutions')
def api_institutions():
    snap = _resolve_snapshot('institutions')
    if not snap:
        return jsonify({'ok': True, 'snapshot_date': None, 'count': 0, 'items': []})
    sid, sdate = snap
    items = _institutions_of(sid)
    search = (request.args.get('search') or '').strip().lower()
    category = (request.args.get('category') or '').strip().lower()
    if search:
        items = [i for i in items if search in i['institution_name'].lower()]
    if category:
        items = [i for i in items if category in i['category'].lower()]
    return jsonify({'ok': True, 'snapshot_date': sdate, 'count': len(items),
                    'items': items[:1000]})


# ── Public sahifalar ────────────────────────────────────────────────────────

def _render_ranking(active_tab):
    sch_snap = _latest_snapshot('scholars')
    inst_snap = _latest_snapshot('institutions')
    latest_date = (sch_snap[1] if sch_snap else
                   (inst_snap[1] if inst_snap else None))
    # institutions tab'i uchun toifalar ro'yxati (filtr)
    categories = []
    if inst_snap:
        seen = set()
        for it in _institutions_of(inst_snap[0]):
            c = (it['category'] or '').strip()
            if c and c.lower() not in seen:
                seen.add(c.lower())
                categories.append(c)
    return render_template(
        'ranking.html',
        active_tab=active_tab,
        source_name=SOURCE_NAME,
        latest_date=latest_date,
        scholar_snapshots=_snapshot_list('scholars'),
        institution_snapshots=_snapshot_list('institutions'),
        categories=sorted(categories),
        has_data=bool(sch_snap or inst_snap))


@ranking_bp.route('/reyting')
def ranking_page():
    return _render_ranking('scholars')


@ranking_bp.route('/reyting/tashkilotlar')
def ranking_institutions_page():
    return _render_ranking('institutions')


# ── Admin: Excel yuklash paneli ─────────────────────────────────────────────

# kutilayotgan ustun → qabul qilinadigan sarlavha variantlari (normallashtirilgan)
_SCHOLAR_COLS = {
    'rank': ('rank', 'orin', 'orni', 'no', 'tartib', 'raqam'),
    'scholar_name': ('scholarname', 'name', 'ism', 'fio', 'olim',
                     'ismfamiliya', 'fish'),
    'institution': ('institution', 'tashkilot', 'muassasa', 'ishjoyi'),
    'h_scopus': ('hscopus', 'scopus', 'hindexscopus'),
    'h_wos': ('hwos', 'wos', 'webofscience', 'hindexwos'),
    'h_scholar': ('hscholar', 'scholar', 'googlescholar', 'hindexscholar'),
    'orcid': ('orcid', 'orcidid'),
}
_INSTITUTION_COLS = {
    'rank': ('rank', 'orin', 'orni', 'no', 'tartib', 'raqam'),
    'institution_name': ('institutionname', 'institution', 'tashkilot',
                         'muassasa', 'name', 'nom', 'tashkilotnomi'),
    'category': ('category', 'kategoriya', 'toifa'),
    'h_scopus': ('hscopus', 'scopus'),
    'h_wos': ('hwos', 'wos', 'webofscience'),
    'h_scholar': ('hscholar', 'scholar', 'googlescholar'),
    'national_h_index': ('nationalhindex', 'national', 'milliy', 'milliyhindex',
                         'nationalh', 'milliyh'),
}


def _map_headers(header_row, spec):
    """Excel sarlavha qatori → {mantiqiy_ustun: indeks}. Topilmagani yo'q."""
    norm = {}
    for i, h in enumerate(header_row):
        key = re.sub(r'[^a-z0-9]', '', str(h or '').lower())
        if key and key not in norm:
            norm[key] = i
    out = {}
    for col, aliases in spec.items():
        for a in aliases:
            if a in norm:
                out[col] = norm[a]
                break
    return out


def _admin_guard():
    if not getattr(current_user, 'is_admin', False):
        abort(403)


@ranking_bp.route('/admin/reyting', methods=['GET', 'POST'])
@login_required
def admin_ranking():
    _admin_guard()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            conn.commit()
            if request.method == 'POST':
                _handle_upload(cur, conn)
        # snapshotlar ro'yxati (o'chirish + statistikasi bilan)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.id, s.snapshot_date, s.source, s.notes, s.created_at,
                    (SELECT COUNT(*) FROM h_index_scholars WHERE snapshot_id = s.id),
                    (SELECT COUNT(*) FROM h_index_scholars
                        WHERE snapshot_id = s.id AND olim_match IS NOT NULL AND olim_match <> ''),
                    (SELECT COUNT(*) FROM h_index_institutions WHERE snapshot_id = s.id),
                    (SELECT COUNT(*) FROM h_index_institutions
                        WHERE snapshot_id = s.id AND university_match IS NOT NULL AND university_match <> '')
                FROM h_index_snapshots s
                ORDER BY s.snapshot_date DESC, s.id DESC
            """)
            snapshots = [{
                'id': r[0], 'date': str(r[1]), 'source': r[2] or '',
                'notes': r[3] or '', 'created_at': str(r[4])[:16] if r[4] else '',
                'n_scholars': r[5] or 0, 'n_scholars_matched': r[6] or 0,
                'n_institutions': r[7] or 0, 'n_institutions_matched': r[8] or 0,
            } for r in cur.fetchall()]
    finally:
        conn.close()
    return render_template('admin_ranking.html', snapshots=snapshots)


def _handle_upload(cur, conn):
    """POST: Excel o'qib, yangi snapshot yaratib, qatorlarni matching bilan yozadi.
    Xatoli qatorlar skip qilinadi, xato ro'yxati flash orqali ko'rsatiladi."""
    kind = (request.form.get('kind') or '').strip()          # 'scholars' | 'institutions'
    snapshot_date = (request.form.get('snapshot_date') or '').strip()
    notes = (request.form.get('notes') or '').strip() or None
    source = (request.form.get('source') or SOURCE_NAME).strip() or SOURCE_NAME
    file = request.files.get('file')

    if kind not in ('scholars', 'institutions'):
        flash("Xato: yuklash turi tanlanmadi (Olimlar / Tashkilotlar).", 'error')
        return
    if not snapshot_date or not re.match(r'^\d{4}-\d{2}-\d{2}$', snapshot_date):
        flash("Xato: snapshot sanasi noto'g'ri (YYYY-MM-DD kutiladi).", 'error')
        return
    if not file or not file.filename:
        flash("Xato: Excel fayl tanlanmadi.", 'error')
        return
    if not file.filename.lower().endswith(('.xlsx', '.xlsm')):
        flash("Xato: faqat .xlsx fayl qabul qilinadi.", 'error')
        return

    try:
        import openpyxl
        wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        header = next(rows_iter, None)
    except Exception as e:
        flash(f"Xato: Excel o'qib bo'lmadi — {e}", 'error')
        return
    if not header:
        flash("Xato: fayl bo'sh (sarlavha qatori yo'q).", 'error')
        return

    spec = _SCHOLAR_COLS if kind == 'scholars' else _INSTITUTION_COLS
    cols = _map_headers(header, spec)
    name_col = 'scholar_name' if kind == 'scholars' else 'institution_name'
    if name_col not in cols:
        need = 'scholar_name' if kind == 'scholars' else 'institution_name'
        flash(f"Xato: majburiy '{need}' ustuni topilmadi. Topilgan sarlavhalar: "
              f"{', '.join(str(h) for h in header if h)}", 'error')
        return

    # matching manbalarini bir marta tayyorlaymiz
    if kind == 'scholars':
        by_key, by_skey = _build_olim_maps()
    else:
        unis = _build_uni_list()

    # snapshot yaratamiz
    cur.execute("INSERT INTO h_index_snapshots (snapshot_date, source, notes) "
                "VALUES (%s, %s, %s) RETURNING id", (snapshot_date, source, notes))
    snap_id = cur.fetchone()[0]

    added = skipped = matched = 0
    errors = []
    seen_names = set()

    def cell(row, col):
        idx = cols.get(col)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    for rn, row in enumerate(rows_iter, start=2):
        if row is None or all(c is None or str(c).strip() == '' for c in row):
            continue
        name = (str(cell(row, name_col) or '')).strip()
        if not name:
            errors.append(f"{rn}-qator: ism/nom bo'sh — o'tkazib yuborildi")
            skipped += 1
            continue
        dedup = name.lower()
        if dedup in seen_names:
            skipped += 1
            continue
        seen_names.add(dedup)
        # Har qator alohida SAVEPOINT ichida — bitta xatoli qator butun yuklashni
        # (snapshot + oldingi qatorlarni) bekor qilmaydi.
        cur.execute("SAVEPOINT rk_row")
        try:
            if kind == 'scholars':
                olim_match = _match_olim(name, by_key, by_skey)
                cur.execute(
                    "INSERT INTO h_index_scholars (snapshot_id, rank, scholar_name, "
                    "scholar_name_cyrillic, institution, h_scopus, h_wos, h_scholar, "
                    "orcid, olim_match) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (snapshot_id, scholar_name) DO NOTHING",
                    (snap_id, _to_int(cell(row, 'rank')), name, None,
                     (str(cell(row, 'institution') or '').strip() or None),
                     _to_int(cell(row, 'h_scopus')), _to_int(cell(row, 'h_wos')),
                     _to_int(cell(row, 'h_scholar')),
                     (str(cell(row, 'orcid') or '').strip() or None), olim_match))
            else:
                uni_match = _match_uni(name, unis)
                olim_match = uni_match     # umumiy "matched" hisoblagichi uchun
                cur.execute(
                    "INSERT INTO h_index_institutions (snapshot_id, rank, "
                    "institution_name, category, h_scopus, h_wos, h_scholar, "
                    "national_h_index, university_match) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (snapshot_id, institution_name) DO NOTHING",
                    (snap_id, _to_int(cell(row, 'rank')), name,
                     (str(cell(row, 'category') or '').strip() or None),
                     _to_int(cell(row, 'h_scopus')), _to_int(cell(row, 'h_wos')),
                     _to_int(cell(row, 'h_scholar')),
                     _to_int(cell(row, 'national_h_index')), uni_match))
            inserted = cur.rowcount
            cur.execute("RELEASE SAVEPOINT rk_row")
            if inserted:
                added += 1
                if olim_match:
                    matched += 1     # faqat haqiqatan yozilgan (dublikat bo'lmagan) qatorni sanaymiz
            else:
                skipped += 1
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT rk_row")
            errors.append(f"{rn}-qator ({name[:40]}): {e}")
            skipped += 1
            continue
    conn.commit()
    _clear_ranking_cache()

    msg = (f"Yuklandi: {added} ta qator qo'shildi, {matched} tasi profilga "
           f"bog'landi, {skipped} ta o'tkazib yuborildi. Snapshot: {snapshot_date}.")
    flash(msg, 'success')
    for e in errors[:15]:
        flash(e, 'warning')
    if len(errors) > 15:
        flash(f"...va yana {len(errors) - 15} ta xato qator.", 'warning')


@ranking_bp.route('/admin/reyting/delete/<int:id>', methods=['POST'])
@login_required
def admin_ranking_delete(id):
    _admin_guard()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute("DELETE FROM h_index_snapshots WHERE id = %s", (id,))
        conn.commit()
    finally:
        conn.close()
    _clear_ranking_cache()
    flash("Snapshot va unga bog'liq barcha qatorlar o'chirildi.", 'success')
    return redirect('/admin/reyting')
