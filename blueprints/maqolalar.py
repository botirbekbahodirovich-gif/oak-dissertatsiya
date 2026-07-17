"""Ilmiy maqolalar tahlili — OpenAlex integratsiyasi (maqolalar_bp).

Foydalanuvchi mavzu/kalit so'z kiritadi → OpenAlex (250 mln+ maqola) dan global
va O'zbekiston kesimida natijalar olinadi → research gap (bo'shliq) bahosi,
yillik dinamika, maqola kartalari (GOST/APA iqtibos, saqlash), top jurnallar va
bog'liq konseptlar ko'rsatiladi. Mavzu tahlili (/mavzu/tahlil) sahifasiga
preview blok ham shu API dan oziqlanadi (preview=1 — limitga kirmaydi).

Konvensiyalar (topic_analysis/saved blueprint'lari kabi):
  * Sxema lazy + idempotent (_ensure_schema) — server birinchi so'rovda
    self-migrate. user_id ga FK yo'q (users legacy SQLite'da — saved.py kabi).
  * DB — data.get_connection() (PostgreSQL).
  * Kunlik limit DB'da (article_search_log, sessiya emas): bepul 5 qidiruv/kun,
    premium amalda cheksiz (suiiste'molga qarshi qalqon bilan). Sahifalash va
    preview limitga kirmaydi.
  * OpenAlex chaqiruvlari doim timeout + try/except — API yotsa sahifa
    bloklanmaydi (503 JSON, frontend blokni yashiradi).
  * Kesh: modul darajasidagi dict — works 30 daqiqa, statistika 1 soat.

Routes:
  GET  /maqolalar-tahlili           — qidiruv sahifasi (?q= prefill).
  POST /api/maqolalar/search        — qidiruv (JSON; login talab, 401 JSON).
  POST /api/maqolalar/bookmark      — saqlash/olib tashlash (toggle).
  GET  /api/maqolalar/bookmark/ids  — saqlangan OpenAlex ID'lar (tugma holati).
  GET  /cabinet/maqolalar           — saqlangan maqolalar ro'yxati (20/sahifa).
"""
import os
import re
import time
from datetime import date

import requests
from flask import Blueprint, jsonify, request, render_template
from flask_login import login_required, current_user

from app import csrf
from data import get_connection
from blueprints.payments import user_has_premium

maqolalar_bp = Blueprint('maqolalar', __name__)

OPENALEX_WORKS = 'https://api.openalex.org/works'
# API kalit KERAK EMAS — mailto bilan "polite pool" (tezroq, barqaror) ishlaydi.
OPENALEX_MAILTO = os.environ.get('OPENALEX_MAILTO', 'info@olimlar.uz')
OPENALEX_TIMEOUT = 10          # soniya, har bir chaqiruv uchun

PER_PAGE = 10                  # maqola kartalari/sahifa
PREVIEW_LIMIT = 3              # mavzu tahlili blokidagi top-maqolalar
MAX_QUERY_LEN = 300
MIN_QUERY_LEN = 3
MAX_PAGE = 50                  # OpenAlex oddiy sahifalash chegarasi ichida

FREE_DAILY_LIMIT = 5           # bepul: kuniga 5 qidiruv (yangi so'rov, sahifalash emas)
PREMIUM_DAILY_SHIELD = 300     # premium: amalda cheksiz; qalqon suiiste'molga qarshi

WORKS_TTL = 30 * 60            # works keshi
STATS_TTL = 60 * 60            # group_by (statistika) keshi
_CACHE_MAX = 400

# Research gap etaloni: O'zbekiston jahon ilmiy maqolalarining ~0.05% ini beradi.
# expected = global * BASE_UZ_SHARE; gap = 1 - uz/expected (0..1, 1 = to'liq bo'shliq).
BASE_UZ_SHARE = 0.0005
GAP_STRONG = 0.66              # >= — kuchli bo'shliq (qizil)
GAP_MEDIUM = 0.33              # >= — o'rtacha (sariq); pasti — yaxshi qamrov (yashil)

YEARS_WINDOW = 10              # yillik dinamika: oxirgi 10 yil

_OPENALEX_ID_RE = re.compile(r'^W\d{4,15}$')
_CONCEPT_ID_RE = re.compile(r'^C\d{3,12}$')

_schema_ready = False
_cache = {}                    # key -> (expires_at, data)

_PAYWALL_INFO = {
    'feature_name': 'Maqolalar tahlili',
    'benefits': [
        'Cheksiz maqola qidiruvi va research gap tahlili',
        "250 mln+ maqola (OpenAlex) bo'yicha filtrlar",
        "GOST/APA iqtibos va saqlash — cheklovsiz",
    ],
}


# ── Sxema ─────────────────────────────────────────────────────────────────────

def _ensure_schema(cur):
    """user_article_bookmarks + article_search_log (idempotent). user_id ga FK
    ataylab yo'q — users legacy SQLite'da (saved.py bilan bir xil qaror)."""
    global _schema_ready
    if _schema_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_article_bookmarks (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            openalex_id VARCHAR(50) NOT NULL,
            title VARCHAR(600),
            authors TEXT,
            journal VARCHAR(300),
            year INTEGER,
            doi VARCHAR(200),
            oa_url VARCHAR(500),
            saved_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, openalex_id)
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_bm_user "
                "ON user_article_bookmarks(user_id, saved_at DESC)")
    # Kunlik limit (5/kun) + analitika/trending so'rovlar shu jadvaldan.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS article_search_log (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            query VARCHAR(300),
            total_results INTEGER,
            uzbekistan_results INTEGER,
            gap_score FLOAT,
            searched_at TIMESTAMP DEFAULT NOW()
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_log_user "
                "ON article_search_log(user_id, searched_at)")
    _schema_ready = True


def _searches_today(cur, user_id):
    cur.execute("SELECT COUNT(*) FROM article_search_log "
                "WHERE user_id = %s AND searched_at >= CURRENT_DATE", (user_id,))
    row = cur.fetchone()
    return int((row[0] if row else 0) or 0)


# ── Kesh ──────────────────────────────────────────────────────────────────────

def _cache_get(key):
    item = _cache.get(key)
    if not item:
        return None
    expires_at, data = item
    if expires_at < time.time():
        _cache.pop(key, None)
        return None
    return data


def _cache_set(key, data, ttl):
    if len(_cache) >= _CACHE_MAX:
        now = time.time()
        for k in [k for k, (e, _) in list(_cache.items()) if e < now]:
            _cache.pop(k, None)
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
    _cache[key] = (time.time() + ttl, data)


# ── OpenAlex mijoz ────────────────────────────────────────────────────────────

def _oa_get(params):
    """OpenAlex /works so'rovi — har qanday xatoda None (sahifa bloklanmaydi)."""
    try:
        p = dict(params)
        p['mailto'] = OPENALEX_MAILTO
        r = requests.get(OPENALEX_WORKS, params=p, timeout=OPENALEX_TIMEOUT,
                         headers={'User-Agent': 'olimlar.uz (maqolalar-tahlili)'})
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _norm_filters(raw):
    """Frontend filtrlarini xavfsiz normallashtiradi."""
    def _int(v, lo, hi):
        try:
            n = int(v)
        except (TypeError, ValueError):
            return None
        return n if lo <= n <= hi else None

    concept = (raw.get('concept') or '').strip().upper()
    return {
        'year_from': _int(raw.get('year_from'), 1900, 2035),
        'year_to': _int(raw.get('year_to'), 1900, 2035),
        'oa_only': bool(raw.get('oa_only')),
        'uz_only': bool(raw.get('uz_only')),
        'min_cited': _int(raw.get('min_cited'), 1, 1000000),
        'concept': concept if _CONCEPT_ID_RE.match(concept) else None,
    }


def _oa_filter(f, uz=None):
    """OpenAlex filter= qatori. uz=True — majburiy UZ, uz=False — UZ'siz
    (global taqqoslash), uz=None — foydalanuvchi tanlovi (uz_only)."""
    parts = []
    if f.get('year_from'):
        parts.append('from_publication_date:%d-01-01' % f['year_from'])
    if f.get('year_to'):
        parts.append('to_publication_date:%d-12-31' % f['year_to'])
    if f.get('oa_only'):
        parts.append('is_oa:true')
    if f.get('min_cited'):
        parts.append('cited_by_count:>%d' % (f['min_cited'] - 1))
    if f.get('concept'):
        parts.append('concepts.id:%s' % f['concept'])
    if uz is True or (uz is None and f.get('uz_only')):
        parts.append('authorships.countries:UZ')
    return ','.join(parts)


def _slim_work(w):
    """OpenAlex work → frontend karta uchun ixcham obyekt."""
    src = ((w.get('primary_location') or {}).get('source') or {})
    oa = (w.get('open_access') or {})
    biblio = (w.get('biblio') or {})
    authors = []
    for a in (w.get('authorships') or [])[:8]:
        name = ((a.get('author') or {}).get('display_name') or '').strip()
        if name:
            authors.append(name)
    return {
        'id': (w.get('id') or '').rsplit('/', 1)[-1],
        'title': (w.get('display_name') or '').strip() or '(nomsiz maqola)',
        'authors': authors,
        'authors_total': len(w.get('authorships') or []),
        'year': w.get('publication_year'),
        'journal': (src.get('display_name') or '').strip(),
        'doi': w.get('doi') or '',
        'oa_url': oa.get('oa_url') or '',
        'is_oa': bool(oa.get('is_oa')),
        'cited_by': int(w.get('cited_by_count') or 0),
        'volume': biblio.get('volume') or '',
        'issue': biblio.get('issue') or '',
        'first_page': biblio.get('first_page') or '',
        'last_page': biblio.get('last_page') or '',
    }


def _works_page(query, f, page, per_page, sort=None):
    key = ('works', query, _oa_filter(f), page, per_page, sort or '')
    hit = _cache_get(key)
    if hit is not None:
        return hit
    params = {'search': query, 'per-page': per_page, 'page': page}
    filt = _oa_filter(f)
    if filt:
        params['filter'] = filt
    if sort:
        params['sort'] = sort
    data = _oa_get(params)
    if data is None:
        return None
    out = {
        'total': int((data.get('meta') or {}).get('count') or 0),
        'works': [_slim_work(w) for w in (data.get('results') or [])],
    }
    _cache_set(key, out, WORKS_TTL)
    return out


def _group_by(query, f, group_key, uz=None):
    """group_by so'rovi: meta.count (jami) + guruhlar. 1 soat keshlanadi."""
    filt = _oa_filter(f, uz=uz)
    key = ('grp', query, filt, group_key)
    hit = _cache_get(key)
    if hit is not None:
        return hit
    params = {'search': query, 'group_by': group_key}
    if filt:
        params['filter'] = filt
    data = _oa_get(params)
    if data is None:
        return None
    out = {
        'total': int((data.get('meta') or {}).get('count') or 0),
        'groups': [
            {'key': (g.get('key') or '').rsplit('/', 1)[-1],
             'name': g.get('key_display_name') or '',
             'count': int(g.get('count') or 0)}
            for g in (data.get('group_by') or [])
        ],
    }
    _cache_set(key, out, STATS_TTL)
    return out


def _year_map(grp):
    m = {}
    for it in (grp or {}).get('groups') or []:
        try:
            m[int(it['key'])] = it['count']
        except (TypeError, ValueError):
            continue
    return m


def _yearly(g_years, u_years):
    """Oxirgi YEARS_WINDOW yil bo'yicha global/UZ sonlar (frontend chart)."""
    this_year = date.today().year
    years = list(range(this_year - YEARS_WINDOW + 1, this_year + 1))
    gm, um = _year_map(g_years), _year_map(u_years)
    return {
        'years': years,
        'global': [gm.get(y, 0) for y in years],
        'uz': [um.get(y, 0) for y in years],
    }


def _gap(global_total, uz_total):
    """Research gap bahosi: 0..1 (1 = to'liq bo'shliq) + rangli yorliq."""
    if not global_total:
        return None
    expected = max(1.0, global_total * BASE_UZ_SHARE)
    score = max(0.0, min(1.0, 1.0 - (uz_total / expected)))
    label = ('strong' if score >= GAP_STRONG
             else 'medium' if score >= GAP_MEDIUM
             else 'low')
    return {'global_total': int(global_total), 'uz_total': int(uz_total),
            'score': round(score, 3), 'label': label}


# ── Routes ────────────────────────────────────────────────────────────────────

@maqolalar_bp.route('/maqolalar-tahlili')
def maqolalar_page():
    """Qidiruv sahifasi — mehmonga ham ochiq (qidiruv login talab qiladi)."""
    is_premium, searches_left = False, None
    if getattr(current_user, 'is_authenticated', False):
        try:
            conn = get_connection()
            try:
                cur = conn.cursor()
                _ensure_schema(cur)
                is_premium = user_has_premium(current_user.id, cur)
                if not is_premium:
                    searches_left = max(0, FREE_DAILY_LIMIT -
                                        _searches_today(cur, current_user.id))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            searches_left = FREE_DAILY_LIMIT
    return render_template('maqolalar_tahlili.html',
                           q=(request.args.get('q') or '').strip()[:MAX_QUERY_LEN],
                           is_premium=is_premium,
                           searches_left=searches_left,
                           free_daily_limit=FREE_DAILY_LIMIT,
                           max_query_len=MAX_QUERY_LEN)


@maqolalar_bp.route('/api/maqolalar/search', methods=['POST'])
@csrf.exempt
def api_search():
    # login_required o'rniga qo'lda tekshiruv — fetch uchun toza 401 JSON
    # (Flask-Login redirect'i JSON so'rovni buzadi).
    if not getattr(current_user, 'is_authenticated', False):
        return jsonify({'error': 'login_required',
                        'message': "Qidiruv uchun tizimga kiring."}), 401
    body = request.get_json(silent=True) or {}
    query = ' '.join((body.get('query') or '').split())[:MAX_QUERY_LEN]
    if len(query) < MIN_QUERY_LEN:
        return jsonify({'error': 'invalid',
                        'message': "So'rov juda qisqa — kamida "
                                   f"{MIN_QUERY_LEN} ta belgi kiriting."}), 400
    preview = bool(body.get('preview'))
    try:
        page = max(1, min(MAX_PAGE, int(body.get('page') or 1)))
    except (TypeError, ValueError):
        page = 1
    f = _norm_filters(body.get('filters') or {})
    user_id = current_user.id

    # ── Kunlik limit (DB'da; sessiya tozalansa ham ishlaydi). Sahifalash va
    # preview yangi "qidiruv" emas — limitni faqat 1-sahifa iste'mol qiladi.
    premium, used = False, 0
    conn = get_connection()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        premium = user_has_premium(user_id, cur)
        used = _searches_today(cur, user_id)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()

    counts_toward_limit = (not preview) and page == 1
    if counts_toward_limit:
        if premium:
            if used >= PREMIUM_DAILY_SHIELD:
                return jsonify({'error': 'rate_limited',
                                'message': f"Kunlik {PREMIUM_DAILY_SHIELD} ta "
                                           "qidiruv chegarasiga yetdingiz. "
                                           "Ertaga davom eting."}), 429
        elif used >= FREE_DAILY_LIMIT:
            return jsonify({'error': 'payment_required',
                            'message': "Kunlik limit tugadi. Premium bilan "
                                       "cheksiz tahlil — 29,000 so'm/oy.",
                            'paywall': _PAYWALL_INFO}), 402

    # ── OpenAlex: works sahifasi ─────────────────────────────────────────────
    if preview:
        works = _works_page(query, f, 1, PREVIEW_LIMIT, sort='cited_by_count:desc')
    else:
        works = _works_page(query, f, page, PER_PAGE)
    if works is None:
        return jsonify({'error': 'openalex_unavailable',
                        'message': "Maqolalar bazasi (OpenAlex) hozircha javob "
                                   "bermayapti. Birozdan so'ng urinib ko'ring."}), 503

    # ── Gap statistikasi: global vs UZ (group_by meta.count — 1 soat kesh) ──
    if preview:
        # Preview'da filtr yo'q — works.total = global; faqat UZ soni kerak.
        u_years = _group_by(query, f, 'publication_year', uz=True)
        global_total = works['total']
        uz_total = u_years['total'] if u_years else 0
        gap = _gap(global_total, uz_total)
        return jsonify({
            'preview': True,
            'query': query,
            'gap': gap,
            'works': works['works'],
        })

    g_years = _group_by(query, f, 'publication_year', uz=False)
    u_years = _group_by(query, f, 'publication_year', uz=True)
    global_total = g_years['total'] if g_years else (
        0 if f['uz_only'] else works['total'])
    uz_total = u_years['total'] if u_years else 0
    gap = _gap(global_total, uz_total)
    yearly = _yearly(g_years, u_years) if (g_years or u_years) else None

    # Top jurnallar + bog'liq konseptlar — faqat 1-sahifada (keshdan arzon).
    journals, concepts = [], []
    if page == 1:
        j = _group_by(query, f, 'primary_location.source.id')
        if j:
            journals = [{'name': g['name'], 'count': g['count']}
                        for g in j['groups'] if g['name']][:5]
        c = _group_by(query, f, 'concepts.id')
        if c:
            concepts = [{'id': g['key'], 'name': g['name'], 'count': g['count']}
                        for g in c['groups'] if g['name']][:14]

    # ── Log (limit hisobi + analitika) — faqat 1-sahifa qidiruvi ────────────
    searches_left = None
    if counts_toward_limit:
        conn = get_connection()
        try:
            cur = conn.cursor()
            _ensure_schema(cur)
            cur.execute(
                "INSERT INTO article_search_log "
                "(user_id, query, total_results, uzbekistan_results, gap_score) "
                "VALUES (%s, %s, %s, %s, %s)",
                (user_id, query, works['total'], uz_total,
                 gap['score'] if gap else None))
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            conn.close()
        if not premium:
            searches_left = max(0, FREE_DAILY_LIMIT - used - 1)
    elif not premium:
        searches_left = max(0, FREE_DAILY_LIMIT - used)

    return jsonify({
        'query': query,
        'page': page,
        'per_page': PER_PAGE,
        'total': works['total'],
        'has_next': page < MAX_PAGE and page * PER_PAGE < works['total'],
        'works': works['works'],
        'gap': gap,
        'yearly': yearly,
        'journals': journals,
        'concepts': concepts,
        'is_premium': premium,
        'searches_left': searches_left,
    })


@maqolalar_bp.route('/api/maqolalar/bookmark', methods=['POST'])
@csrf.exempt
@login_required
def api_bookmark_toggle():
    """Saqlash/olib tashlash (toggle) — metadata mijozdan keladi (OpenAlex
    kartasi), ID formati qat'iy tekshiriladi."""
    body = request.get_json(silent=True) or {}
    oid = (body.get('openalex_id') or '').strip()
    if not _OPENALEX_ID_RE.match(oid):
        return jsonify({'success': False, 'error': "Noto'g'ri so'rov"}), 400
    try:
        year = int(body.get('year'))
        if not 1500 <= year <= 2100:
            year = None
    except (TypeError, ValueError):
        year = None
    conn = get_connection()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("DELETE FROM user_article_bookmarks "
                    "WHERE user_id = %s AND openalex_id = %s",
                    (current_user.id, oid))
        if cur.rowcount:
            saved = False
        else:
            cur.execute(
                "INSERT INTO user_article_bookmarks "
                "(user_id, openalex_id, title, authors, journal, year, doi, oa_url) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (current_user.id, oid,
                 (body.get('title') or '')[:600],
                 (body.get('authors') or '')[:2000],
                 (body.get('journal') or '')[:300],
                 year,
                 (body.get('doi') or '')[:200],
                 (body.get('oa_url') or '')[:500]))
            saved = True
        conn.commit()
        return jsonify({'success': True, 'saved': saved})
    finally:
        conn.close()


@maqolalar_bp.route('/api/maqolalar/bookmark/ids')
@login_required
def api_bookmark_ids():
    """Sahifa yuklanishida saqlangan ID'lar (yulduzcha holati uchun)."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("SELECT openalex_id FROM user_article_bookmarks "
                    "WHERE user_id = %s", (current_user.id,))
        ids = [r[0] for r in cur.fetchall()]
        conn.commit()
        return jsonify({'success': True, 'ids': ids, 'count': len(ids)})
    finally:
        conn.close()


@maqolalar_bp.route('/cabinet/maqolalar')
@login_required
def saved_articles_page():
    """Saqlangan maqolalar ro'yxati (20/sahifa, saved_at DESC)."""
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    per_page = 20
    conn = get_connection()
    try:
        cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute("SELECT COUNT(*) FROM user_article_bookmarks "
                    "WHERE user_id = %s", (current_user.id,))
        total = int(cur.fetchone()[0] or 0)
        cur.execute("""
            SELECT openalex_id, title, authors, journal, year, doi, oa_url
            FROM user_article_bookmarks
            WHERE user_id = %s
            ORDER BY saved_at DESC
            LIMIT %s OFFSET %s
        """, (current_user.id, per_page, (page - 1) * per_page))
        cols = ('openalex_id', 'title', 'authors', 'journal', 'year', 'doi', 'oa_url')
        records = [dict(zip(cols, row)) for row in cur.fetchall()]
        conn.commit()
    finally:
        conn.close()
    total_pages = max(1, (total + per_page - 1) // per_page)
    return render_template('maqolalar_saqlangan.html',
                           records=records, page=page, total=total,
                           total_pages=total_pages,
                           has_prev=page > 1, has_next=page < total_pages)
