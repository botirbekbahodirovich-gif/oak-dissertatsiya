"""Olimlar katalogi 2.0 (olimlar_catalog_bp).

dissertations jadvalidan ilmiy rahbarlar (olimlar) bo'yicha obro' metrikalarini
hisoblaydigan katalog: shogirdlar (PhD/DSc), opponentlik, ilmiy avlodlar, nashrlar
(Roadmap Nashrlar'dan), yillik faollik sparkline'i. Guruhlangan (shogird soni
bo'yicha akkordeon) va kartochka ko'rinishlari.

Manba jadvallar (hech biri yangi emas, hammasi mavjud):
  dissertations        — olim, ilmiy_rahbar(+daraja,+photo), daraja, ixtisoslik,
                         muassasa, sana, opponent_1..3
  institution_map      — TRIM(muassasa)=cyrillic_name → canonical_name, region
  roadmap_publications — nashrlar (plan_id→roadmap_plans.user_id→users.id).
                         olim_name→cabinet_users→users zanjiri orqali bog'lanadi
  olim_profiles        — tasdiqlangan (claimed) profil + scholar_url
  cabinet_users        — olim_name, email, orcid_url

MUHIM (spec vs real sxema tuzatishlari):
  - institution_map ustuni cyrillic_name (raw_name emas)
  - sana erkin matn (DD.MM.YYYY / YYYY-MM-DD) — ::date ishlamaydi, yil regexp bilan
  - yillik faollik alohida GROUP BY so'rovi bilan (agregat ichida agregat yaroqsiz)

Kesh: modul-daraja dict + 15 daqiqa TTL (get_homepage_stats naqshi). Filtr,
saralash, guruhlash, paginatsiya keshlangan ro'yxat ustida — DB'ga urilmaydi.

Routes:
  GET  /olimlar                     — sahifa (server-render shell + boshlang'ich)
  GET  /api/olimlar                 — filtr/saralash/guruh JSON
  POST /api/olimlar/<slug>/follow   — kuzatish toggle (@login_required)
"""
import re
import time
import json

from flask import (Blueprint, jsonify, request, render_template, abort)
from flask_login import login_required, current_user

from data import get_connection, clean_olim_name
from app import csrf

olimlar_catalog_bp = Blueprint('olimlar_catalog', __name__)

_CACHE_TTL = 900          # 15 daqiqa
_scholars_cache = {'data': None, 'facets': None, 'timestamp': 0}
_schema_ready = False

PER_PAGE_CARDS = 24
PER_PAGE_GROUPS = 20
GROUP_CARD_CHUNK = 12     # akkordeon ichida bir marta ko'rsatiladigan kartalar

# daraja naqshlari (dissertations.daraja — o'zbek/lotin/kirill variantlari)
_PHD_PATTERNS = ['%PhD%', '%falsafa%', '%фалсафа%']
_DSC_PATTERNS = ['%DSc%', '%fan doktori%', '%фан доктори%']

# sana (erkin matn) → 4 xonali yil matni yoki NULL
_YEAR_SQL = (
    "CASE "
    "WHEN TRIM(sana) ~ '^[0-9]{2}[.][0-9]{2}[.][0-9]{4}$' "
    "THEN substring(TRIM(sana) from 7 for 4) "
    "WHEN TRIM(sana) ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' "
    "THEN substring(TRIM(sana) from 1 for 4) "
    "ELSE NULL END"
)


# ── Schema (lazy) ────────────────────────────────────────────────────────────

def _ensure_schema(cur):
    global _schema_ready
    if _schema_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scholar_follows (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            scholar_name VARCHAR(300) NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, scholar_name)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scholar_follows_name "
                "ON scholar_follows(LOWER(scholar_name))")
    # Google Scholar profil havolasi — foydalanuvchi kabinetda kiritadi (Part 6)
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "google_scholar_url VARCHAR(500)")
    _schema_ready = True


# ── Sparkline (server-render SVG) ────────────────────────────────────────────

def _sparkline_svg(yearly, width=120, height=24):
    """{year:int -> count} → inline SVG bar sparkline (accent ko'k). Bo'sh → ''."""
    if not yearly:
        return ''
    years = sorted(yearly)
    y0, y1 = years[0], years[-1]
    span = list(range(y0, y1 + 1))          # yo'q yillar 0 bo'lib ko'rinadi
    vals = [yearly.get(y, 0) for y in span]
    mx = max(vals) or 1
    n = len(span)
    bw = width / n
    gap = min(1.5, bw * 0.2)
    bars = []
    for i, v in enumerate(vals):
        bh = (v / mx) * (height - 2)
        x = i * bw
        y = height - bh
        bars.append(
            f'<rect x="{x + gap/2:.1f}" y="{y:.1f}" width="{max(0.5, bw-gap):.1f}" '
            f'height="{max(0.5, bh):.1f}" rx="0.5" fill="#3b82f6" opacity="0.85"/>')
    return (f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
            f'preserveAspectRatio="none" role="img" aria-label="Yillik faollik '
            f'{y0}-{y1}">{"".join(bars)}</svg>')


# ── Aggregation cache builder ────────────────────────────────────────────────

def _fetchall(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchall()


def _build_cache():
    """Barcha agregatsiya so'rovlarini bajarib, olimlar ro'yxatini quradi.
    Kutilgan vaqt: 2-5 s. Xatoda bo'sh ro'yxat (sahifa 500 bermaydi)."""
    now = time.time()
    if _scholars_cache['data'] is not None and (now - _scholars_cache['timestamp']) < _CACHE_TTL:
        return _scholars_cache

    scholars = {}
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                conn.commit()

                # 1) Baza agregatsiya (rahbar bo'yicha)
                base = _fetchall(cur, f"""
                    SELECT TRIM(d.ilmiy_rahbar) AS name,
                           MAX(d.ilmiy_rahbar_daraja) AS degree,
                           MAX(d.ilmiy_rahbar_photo_url) AS photo_url,
                           COUNT(*) AS total_students,
                           COUNT(*) FILTER (WHERE d.daraja ILIKE ANY(%s)) AS phd_students,
                           COUNT(*) FILTER (WHERE d.daraja ILIKE ANY(%s)) AS dsc_students,
                           MIN({_YEAR_SQL}) AS first_year,
                           MAX({_YEAR_SQL}) AS last_year,
                           COUNT(DISTINCT {_YEAR_SQL}) AS active_years,
                           array_agg(DISTINCT COALESCE(im.canonical_name, im.cyrillic_name))
                               FILTER (WHERE im.cyrillic_name IS NOT NULL) AS institutions,
                           array_agg(DISTINCT im.region)
                               FILTER (WHERE im.region IS NOT NULL AND TRIM(im.region) <> '') AS regions,
                           array_agg(DISTINCT TRIM(d.ixtisoslik))
                               FILTER (WHERE d.ixtisoslik IS NOT NULL AND TRIM(d.ixtisoslik) <> '') AS specialties,
                           array_agg(DISTINCT TRIM(d.ixtisoslik_nomi))
                               FILTER (WHERE d.ixtisoslik_nomi IS NOT NULL AND TRIM(d.ixtisoslik_nomi) <> '') AS specialty_names
                    FROM dissertations d
                    LEFT JOIN institution_map im ON TRIM(d.muassasa) = im.cyrillic_name
                    WHERE d.ilmiy_rahbar IS NOT NULL AND TRIM(d.ilmiy_rahbar) <> ''
                    GROUP BY TRIM(d.ilmiy_rahbar)
                """, (_PHD_PATTERNS, _DSC_PATTERNS))
                for r in base:
                    name = r[0]
                    scholars[name] = {
                        'name': name,
                        'display': clean_olim_name(name),
                        'degree': (r[1] or '').strip(),
                        'photo_url': r[2] or '',
                        'total_students': r[3] or 0,
                        'phd_students': r[4] or 0,
                        'dsc_students': r[5] or 0,
                        'first_year': r[6], 'last_year': r[7],
                        'active_years': r[8] or 0,
                        'institutions': list(r[9] or []),
                        'regions': list(r[10] or []),
                        'specialties': list(r[11] or []),
                        'specialty_names': list(r[12] or []),
                        'yearly': {},
                        'opponent_count': 0,
                        'next_gen_advisors': 0,
                        'publications_count': 0,
                        'is_claimed': False,
                        'has_orcid': False,
                        'has_google_scholar': False,
                        'google_scholar_url': '',
                    }

                # 2) Yillik faollik (alohida — sparkline uchun)
                for name, yr, cnt in _fetchall(cur, f"""
                    SELECT TRIM(ilmiy_rahbar) AS name, {_YEAR_SQL} AS yr, COUNT(*)
                    FROM dissertations
                    WHERE ilmiy_rahbar IS NOT NULL AND TRIM(ilmiy_rahbar) <> ''
                      AND {_YEAR_SQL} IS NOT NULL
                    GROUP BY TRIM(ilmiy_rahbar), {_YEAR_SQL}
                """):
                    s = scholars.get(name)
                    if s and yr:
                        try:
                            s['yearly'][int(yr)] = cnt
                        except (ValueError, TypeError):
                            pass

                # 3) Opponentlik soni (opponent_1..3 UNION)
                for name, cnt in _fetchall(cur, """
                    SELECT TRIM(name), COUNT(*) FROM (
                        SELECT opponent_1 AS name FROM dissertations WHERE opponent_1 IS NOT NULL AND TRIM(opponent_1) <> ''
                        UNION ALL
                        SELECT opponent_2 FROM dissertations WHERE opponent_2 IS NOT NULL AND TRIM(opponent_2) <> ''
                        UNION ALL
                        SELECT opponent_3 FROM dissertations WHERE opponent_3 IS NOT NULL AND TRIM(opponent_3) <> ''
                    ) o GROUP BY TRIM(name)
                """):
                    s = scholars.get(name)
                    if s:
                        s['opponent_count'] = cnt

                # 4) Ilmiy avlodlar — shogirdlaridan qanchasi o'zi rahbar bo'lgan
                for advisor, ng in _fetchall(cur, """
                    SELECT TRIM(d1.ilmiy_rahbar), COUNT(DISTINCT TRIM(d2.ilmiy_rahbar))
                    FROM dissertations d1
                    JOIN dissertations d2 ON TRIM(d1.olim) = TRIM(d2.ilmiy_rahbar)
                    WHERE d1.ilmiy_rahbar IS NOT NULL AND TRIM(d1.ilmiy_rahbar) <> ''
                    GROUP BY TRIM(d1.ilmiy_rahbar)
                """):
                    s = scholars.get(advisor)
                    if s:
                        s['next_gen_advisors'] = ng

                # 5) Nashrlar (Roadmap) — olim_name→cabinet_users→users→roadmap zanjiri.
                #    olim_name lower kalit orqali bog'laymiz (dissertations.olim ≈ cabinet_users.olim_name).
                try:
                    pubs = _fetchall(cur, """
                        SELECT LOWER(TRIM(cu.olim_name)) AS oname, COUNT(pub.id)
                        FROM cabinet_users cu
                        JOIN users u ON LOWER(u.email) = LOWER(cu.email)
                        JOIN roadmap_plans rp ON rp.user_id = u.id
                        JOIN roadmap_publications pub ON pub.plan_id = rp.id
                        WHERE cu.olim_name IS NOT NULL AND TRIM(cu.olim_name) <> ''
                        GROUP BY LOWER(TRIM(cu.olim_name))
                    """)
                    pub_map = {o: n for o, n in pubs}
                    gs_map = {}   # olim_name lower → google_scholar_url (users)
                    try:
                        for oname, gsu in _fetchall(cur, """
                            SELECT LOWER(TRIM(cu.olim_name)), MAX(u.google_scholar_url)
                            FROM cabinet_users cu
                            JOIN users u ON LOWER(u.email) = LOWER(cu.email)
                            WHERE cu.olim_name IS NOT NULL AND TRIM(cu.olim_name) <> ''
                              AND u.google_scholar_url IS NOT NULL AND TRIM(u.google_scholar_url) <> ''
                            GROUP BY LOWER(TRIM(cu.olim_name))
                        """):
                            if gsu:
                                gs_map[oname] = gsu
                    except Exception:
                        gs_map = {}
                except Exception:
                    pub_map, gs_map = {}, {}

                # 6) Claimed profil + ORCID + Scholar (olim_profiles + cabinet_users)
                claimed, orcid_set, scholar_url_map = set(), set(), {}
                try:
                    for (oname,) in _fetchall(cur,
                            "SELECT LOWER(TRIM(olim_name)) FROM olim_profiles "
                            "WHERE olim_name IS NOT NULL AND TRIM(olim_name) <> ''"):
                        claimed.add(oname)
                    for oname, surl in _fetchall(cur,
                            "SELECT LOWER(TRIM(olim_name)), MAX(scholar_url) FROM olim_profiles "
                            "WHERE scholar_url IS NOT NULL AND TRIM(scholar_url) <> '' "
                            "GROUP BY LOWER(TRIM(olim_name))"):
                        if surl:
                            scholar_url_map[oname] = surl
                except Exception:
                    pass
                try:
                    for oname, orcid in _fetchall(cur,
                            "SELECT LOWER(TRIM(olim_name)), MAX(orcid_url) FROM cabinet_users "
                            "WHERE olim_name IS NOT NULL AND TRIM(olim_name) <> '' "
                            "GROUP BY LOWER(TRIM(olim_name))"):
                        if oname:
                            claimed.add(oname)
                            if orcid and str(orcid).strip():
                                orcid_set.add(oname)
                except Exception:
                    pass

                # enrichment'ni ro'yxatga qo'llash
                for s in scholars.values():
                    key = s['name'].lower()
                    s['publications_count'] = pub_map.get(key, 0)
                    s['is_claimed'] = key in claimed
                    s['has_orcid'] = key in orcid_set
                    gsu = gs_map.get(key) or scholar_url_map.get(key) or ''
                    s['google_scholar_url'] = gsu
                    s['has_google_scholar'] = bool(gsu)
                    s['sparkline'] = _sparkline_svg(s['yearly'])
        finally:
            conn.close()
    except Exception:
        # DB muammosi — bor keshni saqlab qolamiz (bo'sh bo'lsa bo'sh ro'yxat)
        if _scholars_cache['data'] is not None:
            return _scholars_cache
        scholars = {}

    data = list(scholars.values())
    facets = _compute_facets(data)
    _scholars_cache.update(data=data, facets=facets, timestamp=now)
    return _scholars_cache


def _compute_facets(data):
    """Keshlangan ro'yxatdan facet sanoqlari (specialty / region / degree)."""
    spec_counts, spec_names, region_counts = {}, {}, {}
    phd_n = dsc_n = 0
    for s in data:
        for code, nm in zip(s['specialties'], s['specialty_names'] + [''] * len(s['specialties'])):
            if code:
                spec_counts[code] = spec_counts.get(code, 0) + 1
                if nm and code not in spec_names:
                    spec_names[code] = nm
        for reg in s['regions']:
            if reg:
                region_counts[reg] = region_counts.get(reg, 0) + 1
        if s['dsc_students']:
            dsc_n += 1
        if s['phd_students']:
            phd_n += 1
    specialties = [{'code': c, 'name': spec_names.get(c, ''), 'count': n}
                   for c, n in sorted(spec_counts.items(), key=lambda x: -x[1])]
    regions = [{'name': r, 'count': n}
               for r, n in sorted(region_counts.items(), key=lambda x: -x[1])]
    degrees = [{'name': 'DSc', 'count': dsc_n}, {'name': 'PhD', 'count': phd_n}]
    return {'specialties': specialties, 'regions': regions, 'degrees': degrees}


# ── Filtr / saralash / guruhlash (keshlangan ro'yxat ustida) ─────────────────

_SORT_KEYS = {
    'students': 'total_students', 'opponents': 'opponent_count',
    'generations': 'next_gen_advisors', 'publications': 'publications_count',
}


def _translit_variants(q):
    """Qidiruv uchun lotin↔kirill variantlari (ikki yo'nalishda)."""
    from institutions import transliterate
    out = {q.lower()}
    try:
        out.add(transliterate(q).lower())
    except Exception:
        pass
    return {v for v in out if v}


def _apply_filters(data, f):
    q = (f.get('q') or '').strip().lower()
    ixt = (f.get('ixtisoslik') or '').strip()
    viloyat = (f.get('viloyat') or '').strip()
    muassasa = (f.get('muassasa') or '').strip().lower()
    daraja = (f.get('daraja') or '').strip().lower()
    faollik = f.get('faollik')
    only_orcid = f.get('orcid') in ('1', 'true', 'on')
    only_claimed = f.get('claimed') in ('1', 'true', 'on')

    items = data
    if q:
        variants = _translit_variants(q)
        items = [s for s in items
                 if any(v in s['name'].lower() or v in s['display'].lower()
                        or any(v in (sp or '').lower() for sp in s['specialty_names'])
                        or any(v in (i or '').lower() for i in s['institutions'])
                        for v in variants)]
    if ixt:
        items = [s for s in items if any(ixt in (c or '') for c in s['specialties'])]
    if viloyat:
        items = [s for s in items if viloyat in s['regions']]
    if muassasa:
        items = [s for s in items if any(muassasa in (i or '').lower() for i in s['institutions'])]
    if daraja == 'dsc':
        items = [s for s in items if s['dsc_students'] > 0]
    elif daraja == 'phd':
        items = [s for s in items if s['phd_students'] > 0]
    if faollik in ('5', '10'):
        import datetime
        cutoff = datetime.date.today().year - int(faollik)
        items = [s for s in items if s['last_year'] and int(s['last_year']) >= cutoff]
    if only_orcid:
        items = [s for s in items if s['has_orcid']]
    if only_claimed:
        items = [s for s in items if s['is_claimed']]
    return items


def _sort_items(items, sort):
    if sort == 'name':
        return sorted(items, key=lambda s: s['display'].lower())
    if sort == 'activity':
        return sorted(items, key=lambda s: (s['last_year'] or '0000', s['total_students']), reverse=True)
    key = _SORT_KEYS.get(sort, 'total_students')
    return sorted(items, key=lambda s: (s[key], s['total_students']), reverse=True)


def _scholar_public(s, follows=None):
    """Kartochka uchun commonlashtirilgan (og'ir maydonlarsiz) dict."""
    return {
        'name': s['name'], 'display': s['display'], 'degree': s['degree'],
        'photo_url': s['photo_url'], 'slug': _slugify(s['name']),
        'total_students': s['total_students'], 'phd_students': s['phd_students'],
        'dsc_students': s['dsc_students'], 'opponent_count': s['opponent_count'],
        'next_gen_advisors': s['next_gen_advisors'],
        'publications_count': s['publications_count'],
        'institutions': s['institutions'][:2], 'regions': s['regions'][:1],
        'specialties': s['specialties'][:3],
        'first_year': s['first_year'], 'last_year': s['last_year'],
        'is_claimed': s['is_claimed'], 'has_orcid': s['has_orcid'],
        'has_google_scholar': s['has_google_scholar'],
        'google_scholar_url': s['google_scholar_url'],
        'sparkline': s.get('sparkline', ''),
        'is_following': bool(follows and s['name'].lower() in follows),
    }


def _group_items(items, sort, follows):
    """Saralash kalitiga ko'ra guruhlash (spec 3.2/1.1): guruh kaliti = sort metrikasi."""
    key = _SORT_KEYS.get(sort, 'total_students')
    if sort in ('name', 'activity'):
        key = 'total_students'      # bu saralashlar guruhlashga mos emas — shogird bo'yicha
    from collections import defaultdict
    buckets = defaultdict(list)
    for s in items:
        buckets[s[key]].append(s)
    groups = []
    for val in sorted(buckets, reverse=True):
        members = buckets[val]
        groups.append({
            'key': val,
            'scholar_count': len(members),
            'scholars': [_scholar_public(s, follows) for s in members[:GROUP_CARD_CHUNK]],
            'has_more': len(members) > GROUP_CARD_CHUNK,
            'total_in_group': len(members),
        })
    return groups, key


def _slugify(name):
    from institutions import transliterate
    s = transliterate((name or '').lower())
    s = re.sub(r"[^a-z0-9]+", '-', s.replace("'", '')).strip('-')
    return s or 'olim'


def _user_follows():
    """Joriy foydalanuvchi kuzatayotgan olim nomlari (lower) to'plami."""
    if not getattr(current_user, 'is_authenticated', False):
        return set()
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute("SELECT LOWER(scholar_name) FROM scholar_follows WHERE user_id = %s",
                            (current_user.id,))
                return {r[0] for r in cur.fetchall()}
        finally:
            conn.close()
    except Exception:
        return set()


# ── Routes ───────────────────────────────────────────────────────────────────

@olimlar_catalog_bp.route('/olimlar')
def olimlar_page():
    cache = _build_cache()
    total = len(cache['data'])
    inst_count = len({i for s in cache['data'] for i in s['institutions']})
    return render_template('olimlar_catalog.html',
                           total_scholars=total,
                           total_specialties=len(cache['facets']['specialties']),
                           total_institutions=inst_count,
                           facets=cache['facets'])


@olimlar_catalog_bp.route('/api/olimlar')
def api_olimlar():
    cache = _build_cache()
    f = request.args
    sort = (f.get('sort') or 'students').strip()
    page = max(1, request.args.get('page', 1, type=int))
    view = (f.get('view') or '').strip()

    items = _apply_filters(cache['data'], f)
    items = _sort_items(items, sort)
    total = len(items)
    follows = _user_follows()

    # avtomatik ko'rinish: natija ≤ 50 → karta, aks holda guruh (foydalanuvchi bekor qilishi mumkin)
    if view not in ('grouped', 'cards'):
        view = 'cards' if total <= 50 else 'grouped'

    facets = _compute_facets(items)      # filtrlangan to'plamdan jonli sanoqlar

    if view == 'grouped':
        groups, gkey = _group_items(items, sort, follows)
        pages = max(1, (len(groups) + PER_PAGE_GROUPS - 1) // PER_PAGE_GROUPS)
        page = min(page, pages)
        start = (page - 1) * PER_PAGE_GROUPS
        return jsonify({
            'ok': True, 'view': 'grouped', 'total': total,
            'group_key': gkey, 'sort': sort,
            'groups': groups[start:start + PER_PAGE_GROUPS],
            'page': page, 'pages': pages, 'facets': facets,
        })
    else:
        pages = max(1, (total + PER_PAGE_CARDS - 1) // PER_PAGE_CARDS)
        page = min(page, pages)
        start = (page - 1) * PER_PAGE_CARDS
        scholars = [_scholar_public(s, follows) for s in items[start:start + PER_PAGE_CARDS]]
        return jsonify({
            'ok': True, 'view': 'cards', 'total': total, 'sort': sort,
            'scholars': scholars, 'page': page, 'pages': pages, 'facets': facets,
        })


@olimlar_catalog_bp.route('/api/olimlar/group')
def api_olimlar_group():
    """Guruh ichida 'Yana N ta' — bitta guruhning kartalarini paginatsiyalaydi."""
    cache = _build_cache()
    f = request.args
    sort = (f.get('sort') or 'students').strip()
    try:
        gkey = int(f.get('key'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'key'}), 400
    gpage = max(1, request.args.get('gpage', 1, type=int))
    items = _sort_items(_apply_filters(cache['data'], f), sort)
    metric = _SORT_KEYS.get(sort, 'total_students')
    if sort in ('name', 'activity'):
        metric = 'total_students'
    members = [s for s in items if s[metric] == gkey]
    follows = _user_follows()
    start = (gpage - 1) * GROUP_CARD_CHUNK
    chunk = members[start:start + GROUP_CARD_CHUNK]
    return jsonify({
        'ok': True, 'scholars': [_scholar_public(s, follows) for s in chunk],
        'has_more': start + GROUP_CARD_CHUNK < len(members),
        'total_in_group': len(members),
    })


@olimlar_catalog_bp.route('/api/olimlar/ratings')
def api_olimlar_ratings():
    """TOP reyting bo'limi (3 tab): eng ko'p shogird / eng faol yosh / viloyat yetakchilari."""
    cache = _build_cache()
    data = cache['data']
    import datetime
    cutoff = datetime.date.today().year - 5
    top_students = sorted(data, key=lambda s: s['total_students'], reverse=True)[:10]
    young = sorted([s for s in data if s['first_year'] and int(s['first_year']) >= cutoff],
                   key=lambda s: s['total_students'], reverse=True)[:10]
    by_region = {}
    for s in sorted(data, key=lambda s: s['total_students'], reverse=True):
        for reg in s['regions'][:1]:
            by_region.setdefault(reg, [])
            if len(by_region[reg]) < 3:
                by_region[reg].append({'display': s['display'], 'name': s['name'],
                                       'total_students': s['total_students']})
    lite = lambda s: {'display': s['display'], 'name': s['name'],
                      'total_students': s['total_students'], 'degree': s['degree']}
    return jsonify({
        'ok': True,
        'top_students': [lite(s) for s in top_students],
        'young': [lite(s) for s in young],
        'regions': [{'region': r, 'scholars': v}
                    for r, v in sorted(by_region.items(), key=lambda x: -len(x[1]))[:8]],
    })


@olimlar_catalog_bp.route('/api/olimlar/<slug>/follow', methods=['POST'])
@csrf.exempt
@login_required
def api_follow(slug):
    """Kuzatish toggle. Slug → asl olim nomi keshdan topiladi."""
    cache = _build_cache()
    target = None
    for s in cache['data']:
        if _slugify(s['name']) == slug:
            target = s['name']
            break
    # zaxira: body'da to'liq nom kelsa
    if not target:
        target = (request.form.get('name') or (request.json or {}).get('name')
                  if request.is_json else request.form.get('name'))
    if not target:
        abort(404)
    following = False
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute("DELETE FROM scholar_follows WHERE user_id = %s "
                            "AND LOWER(scholar_name) = LOWER(%s)",
                            (current_user.id, target))
                if cur.rowcount == 0:
                    cur.execute("INSERT INTO scholar_follows (user_id, scholar_name) "
                                "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                                (current_user.id, target))
                    following = True
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
    return jsonify({'ok': True, 'following': following, 'scholar': target})


# ── Profil boyitish helper'i (Part 4 backend) ───────────────────────────────

def get_scholar_reputation(name):
    """olim_profile sahifasi uchun: bitta olimning obro' metrikalari yoki None."""
    if not name:
        return None
    cache = _build_cache()
    key = name.strip().lower()
    for s in cache['data']:
        if s['name'].lower() == key:
            similar = [_scholar_public(x) for x in sorted(
                (o for o in cache['data']
                 if o['name'] != s['name']
                 and set(o['specialties']) & set(s['specialties'])),
                key=lambda o: o['total_students'], reverse=True)[:6]]
            return {
                'total_students': s['total_students'], 'phd_students': s['phd_students'],
                'dsc_students': s['dsc_students'], 'opponent_count': s['opponent_count'],
                'next_gen_advisors': s['next_gen_advisors'],
                'publications_count': s['publications_count'],
                'sparkline': _sparkline_svg(s['yearly'], width=200, height=48),
                'has_google_scholar': s['has_google_scholar'],
                'google_scholar_url': s['google_scholar_url'],
                'similar': similar,
            }
    return None
