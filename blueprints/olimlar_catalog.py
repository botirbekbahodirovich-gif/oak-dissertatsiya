"""Olimlar katalogi (olimlar_catalog_bp).

Ommaviy katalogning yagona reyting metrikasi shogirdlar soni. Profil sahifasi
uchun mavjud obro' maydonlari keshda saqlanadi, lekin katalog API va UI'ga
chiqarilmaydi.

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
from utils.search_helper import matches_query
from utils.olimlar_catalog_quality import (
    canonical_specialty,
    normalize_region,
    normalize_scholar_name,
    normalize_unicode,
    scholar_name_issue,
)

olimlar_catalog_bp = Blueprint('olimlar_catalog', __name__)

_CACHE_TTL = 900          # 15 daqiqa
_scholars_cache = {'data': None, 'facets': None, 'audit': [], 'timestamp': 0}
_schema_ready = False

PER_PAGE_LIST = 24
GROUP_CARD_CHUNK = 12
STUDENT_RANGES = (
    ('51-plus', '51 va undan ko‘p', 51, None),
    ('21-50', '21-50', 21, 50),
    ('11-20', '11-20', 11, 20),
    ('6-10', '6-10', 6, 10),
    ('1-5', '1-5', 1, 5),
    ('zero', 'Shogirdi ko‘rsatilmagan yoki 0', None, 0),
)

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
    audit = []
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
                               FILTER (WHERE im.region IS NOT NULL AND TRIM(im.region) <> '') AS regions
                    FROM dissertations d
                    LEFT JOIN institution_map im ON TRIM(d.muassasa) = im.cyrillic_name
                    WHERE d.ilmiy_rahbar IS NOT NULL AND TRIM(d.ilmiy_rahbar) <> ''
                      AND (d.data_quality = 'complete' OR d.data_quality IS NULL)
                    GROUP BY TRIM(d.ilmiy_rahbar)
                """, (_PHD_PATTERNS, _DSC_PATTERNS))
                for r in base:
                    name = normalize_unicode(r[0])
                    display = normalize_scholar_name(clean_olim_name(name))
                    name_problem = scholar_name_issue(display)
                    if name_problem:
                        audit.append({
                            'entity': 'scholar', 'value': name,
                            'reason': name_problem,
                        })
                        continue
                    scholars[name] = {
                        'name': name,
                        'display': display,
                        'degree': (r[1] or '').strip(),
                        'photo_url': r[2] or '',
                        'total_students': r[3] or 0,
                        'phd_students': r[4] or 0,
                        'dsc_students': r[5] or 0,
                        'first_year': r[6], 'last_year': r[7],
                        'active_years': r[8] or 0,
                        'institutions': sorted({normalize_unicode(v) for v in (r[9] or []) if normalize_unicode(v)}),
                        'regions': sorted({normalize_region(v) for v in (r[10] or []) if normalize_region(v)}),
                        'specialties': [],
                        'specialty_names': [],
                        'specialty_options': [],
                        'yearly': {},
                        'opponent_count': 0,
                        'next_gen_advisors': 0,
                        'publications_count': 0,
                        'is_claimed': False,
                        'has_orcid': False,
                        'has_google_scholar': False,
                        'google_scholar_url': '',
                    }

                # Kod va nom aynan bitta manba qatoridan olinadi.
                try:
                    from app import SPECIALTY_NAMES
                    canonical_names = SPECIALTY_NAMES
                except Exception:
                    canonical_names = {}
                specialty_rows = _fetchall(cur, """
                    SELECT TRIM(d.ilmiy_rahbar), TRIM(d.ixtisoslik),
                           TRIM(d.ixtisoslik_nomi), COUNT(*) AS n
                    FROM dissertations d
                    WHERE d.ilmiy_rahbar IS NOT NULL AND TRIM(d.ilmiy_rahbar) <> ''
                      AND d.ixtisoslik IS NOT NULL AND TRIM(d.ixtisoslik) <> ''
                      AND (d.data_quality = 'complete' OR d.data_quality IS NULL)
                    GROUP BY TRIM(d.ilmiy_rahbar), TRIM(d.ixtisoslik),
                             TRIM(d.ixtisoslik_nomi)
                    ORDER BY n DESC
                """)
                seen_specialties = set()
                for advisor, raw_code, raw_name, _count in specialty_rows:
                    s = scholars.get(normalize_unicode(advisor))
                    code, display_name, reasons = canonical_specialty(
                        raw_code, raw_name, canonical_names
                    )
                    for reason in reasons:
                        audit.append({
                            'entity': 'specialty', 'scholar': advisor,
                            'value': f"{raw_code} | {raw_name or ''}",
                            'reason': reason,
                        })
                    if not s or not code or (s['name'], code) in seen_specialties:
                        continue
                    seen_specialties.add((s['name'], code))
                    s['specialties'].append(code)
                    s['specialty_names'].append(display_name)
                    s['specialty_options'].append({'code': code, 'name': display_name})

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
    _scholars_cache.update(data=data, facets=facets, audit=audit, timestamp=now)
    return _scholars_cache


def _compute_facets(data):
    """Every count is derived from the same validated scholar dataset."""
    spec_counts, spec_names = {}, {}
    region_counts, institution_counts = {}, {}
    phd_n = dsc_n = claimed_n = orcid_n = 0
    for s in data:
        for option in s.get('specialty_options', []):
            code = option['code']
            if code:
                spec_counts[code] = spec_counts.get(code, 0) + 1
                if option.get('name'):
                    spec_names.setdefault(code, option['name'])
        for reg in s['regions']:
            if reg:
                region_counts[reg] = region_counts.get(reg, 0) + 1
        for institution in s['institutions']:
            if institution:
                institution_counts[institution] = institution_counts.get(institution, 0) + 1
        if s['dsc_students']:
            dsc_n += 1
        if s['phd_students']:
            phd_n += 1
        claimed_n += int(bool(s['is_claimed']))
        orcid_n += int(bool(s['has_orcid']))
    specialties = [{'code': c, 'name': spec_names.get(c, ''), 'count': n}
                   for c, n in sorted(spec_counts.items(), key=lambda x: (-x[1], x[0]))]
    regions = [{'name': r, 'count': n}
               for r, n in sorted(region_counts.items(), key=lambda x: (-x[1], x[0]))]
    institutions = [{'name': n, 'count': c}
                    for n, c in sorted(institution_counts.items(), key=lambda x: (-x[1], x[0]))]
    degrees = [{'name': 'DSc', 'count': dsc_n}, {'name': 'PhD', 'count': phd_n}]
    return {
        'specialties': specialties, 'regions': regions,
        'institutions': institutions, 'degrees': degrees,
        'has_orcid': orcid_n > 0, 'claimed_count': claimed_n,
    }


# ── Filtr / saralash / guruhlash (keshlangan ro'yxat ustida) ─────────────────

def _filter_values(args, key):
    return [normalize_unicode(v) for v in (args.get(key) or '').split(',') if normalize_unicode(v)]


def _apply_filters(data, f):
    q = normalize_unicode(f.get('q')).casefold()
    specialties = set(_filter_values(f, 'ixtisoslik'))
    regions = set(_filter_values(f, 'viloyat'))
    institution = normalize_unicode(f.get('muassasa')).casefold()
    degree = normalize_unicode(f.get('daraja')).casefold()
    only_claimed = f.get('claimed') in ('1', 'true', 'on')

    items = data
    if q:
        items = [
            s for s in items
            if matches_query(
                q, s['name'], s['display'], *s['specialties'],
                *s['specialty_names'], *s['institutions']
            )
        ]
    if specialties:
        items = [s for s in items if specialties.intersection(s['specialties'])]
    if regions:
        items = [s for s in items if regions.intersection(s['regions'])]
    if institution:
        items = [
            s for s in items
            if any(institution == normalize_unicode(v).casefold() for v in s['institutions'])
        ]
    if degree == 'dsc':
        items = [s for s in items if s['dsc_students'] > 0]
    elif degree == 'phd':
        items = [s for s in items if s['phd_students'] > 0]
    if only_claimed:
        items = [s for s in items if s['is_claimed']]
    return items


def _sort_items(items, sort):
    if sort == 'name':
        return sorted(
            items,
            key=lambda s: (normalize_unicode(s['display']).casefold(), -s['total_students'])
        )
    return sorted(
        items,
        key=lambda s: (-s['total_students'], normalize_unicode(s['display']).casefold())
    )


def _scholar_public(s, follows=None):
    """Only fields required by the compact catalogue row."""
    return {
        'name': s['name'],
        'display': s['display'],
        'degree': s['degree'],
        'photo_url': s['photo_url'],
        'slug': _slugify(s['name']),
        'total_students': s['total_students'],
        'has_phd': bool(s['phd_students']),
        'has_dsc': bool(s['dsc_students']),
        'institutions': s['institutions'][:1],
        'specialties': s.get('specialty_options', [])[:2],
        'is_claimed': s['is_claimed'],
        'is_following': bool(follows and s['name'].lower() in follows),
    }


def _range_for_count(count):
    for key, label, minimum, maximum in STUDENT_RANGES:
        if minimum is None and count <= maximum:
            return key, label
        if maximum is None and count >= minimum:
            return key, label
        if minimum is not None and maximum is not None and minimum <= count <= maximum:
            return key, label
    return 'zero', STUDENT_RANGES[-1][1]


def _group_members(items, group_key, sort):
    members = [s for s in items if _range_for_count(s['total_students'])[0] == group_key]
    return _sort_items(members, 'name' if sort == 'name' else 'students')


def _group_items(items, sort, follows):
    groups = []
    for key, label, _minimum, _maximum in STUDENT_RANGES:
        members = _group_members(items, key, sort)
        if not members:
            continue
        groups.append({
            'key': key,
            'label': label,
            'scholar_count': len(members),
            'scholars': [_scholar_public(s, follows) for s in members[:GROUP_CARD_CHUNK]],
            'has_more': len(members) > GROUP_CARD_CHUNK,
            'total_in_group': len(members),
        })
    return groups


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
    inst_count = len(cache['facets']['institutions'])
    return render_template('olimlar_catalog.html',
                           total_scholars=total,
                           total_specialties=len(cache['facets']['specialties']),
                           total_institutions=inst_count,
                           facets=cache['facets'])


@olimlar_catalog_bp.route('/api/olimlar')
def api_olimlar():
    cache = _build_cache()
    filters = request.args
    sort = (filters.get('sort') or 'students').strip()
    if sort not in ('students', 'name'):
        sort = 'students'
    page = max(1, request.args.get('page', 1, type=int))
    view = (filters.get('view') or 'list').strip()
    if view == 'cards':
        view = 'list'
    if view not in ('list', 'grouped'):
        view = 'list'

    items = _sort_items(_apply_filters(cache['data'], filters), sort)
    total = len(items)
    follows = _user_follows()
    facets = _compute_facets(items)
    top = [_scholar_public(s, follows) for s in _sort_items(items, 'students')[:5]]

    response = {
        'ok': True,
        'view': view,
        'total': total,
        'sort': sort,
        'facets': facets,
        'top': top,
    }
    if view == 'grouped':
        response.update({
            'groups': _group_items(items, sort, follows),
            'page': 1,
            'pages': 1,
        })
        return jsonify(response)

    pages = max(1, (total + PER_PAGE_LIST - 1) // PER_PAGE_LIST)
    page = min(page, pages)
    start = (page - 1) * PER_PAGE_LIST
    response.update({
        'scholars': [
            _scholar_public(s, follows)
            for s in items[start:start + PER_PAGE_LIST]
        ],
        'page': page,
        'pages': pages,
    })
    return jsonify(response)


@olimlar_catalog_bp.route('/api/olimlar/group')
def api_olimlar_group():
    """Paginate one of the six student-count ranges."""
    cache = _build_cache()
    filters = request.args
    sort = (filters.get('sort') or 'students').strip()
    if sort not in ('students', 'name'):
        sort = 'students'
    group_key = (filters.get('key') or '').strip()
    valid_keys = {row[0] for row in STUDENT_RANGES}
    if group_key not in valid_keys:
        return jsonify({'ok': False, 'error': 'key'}), 400
    group_page = max(1, request.args.get('gpage', 1, type=int))
    items = _apply_filters(cache['data'], filters)
    members = _group_members(items, group_key, sort)
    follows = _user_follows()
    start = (group_page - 1) * GROUP_CARD_CHUNK
    chunk = members[start:start + GROUP_CARD_CHUNK]
    return jsonify({
        'ok': True,
        'scholars': [_scholar_public(s, follows) for s in chunk],
        'has_more': start + GROUP_CARD_CHUNK < len(members),
        'total_in_group': len(members),
    })


@olimlar_catalog_bp.route('/api/olimlar/ratings')
def api_olimlar_ratings():
    """Compatibility endpoint: filtered student ranking, maximum five rows."""
    items = _sort_items(
        _apply_filters(_build_cache()['data'], request.args), 'students'
    )
    return jsonify({
        'ok': True,
        'top_students': [_scholar_public(s) for s in items[:5]],
    })


@olimlar_catalog_bp.route('/api/olimlar/<slug>/follow', methods=['POST'])
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


# ── Follow bildirishnoma hook (Part 5) — import_oak'dan chaqiriladi ─────────

def notify_scholar_follows(new_defenses):
    """Yangi himoyalar ichida kuzatilayotgan ilmiy rahbar bo'lsa, uni kuzatuvchi
    foydalanuvchilarga bell bildirishnomasi. Import'ni hech qachon yiqitmaydi;
    dissertation_id bo'yicha takrorlanmaydi. Qaytaradi: yaratilgan bildirishnomalar
    soni."""
    if not new_defenses:
        return 0
    # rahbar (lower) → shu rahbarli yangi himoyalar
    by_advisor = {}
    for d in new_defenses:
        adv = (d.get('ilmiy_rahbar') or '').strip()
        if adv:
            by_advisor.setdefault(adv.lower(), []).append((adv, d))
    if not by_advisor:
        return 0
    created = 0
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                # faqat kuzatilayotgan rahbarlar
                cur.execute(
                    "SELECT LOWER(scholar_name), user_id FROM scholar_follows "
                    "WHERE LOWER(scholar_name) = ANY(%s)", (list(by_advisor.keys()),))
                follows = cur.fetchall()
                for adv_low, user_id in follows:
                    for adv_name, d in by_advisor.get(adv_low, []):
                        # NB: diss_notifications.dissertation_id → diss_projects FK
                        # (Konstruktor), dissertations EMAS. Shuning uchun himoya
                        # id'sini payload ichida saqlaymiz, ustunni NULL qoldiramiz.
                        diss_id = d.get('id')
                        # takrorlanmaslik: shu user + event + shu himoya (payload'dan)
                        cur.execute(
                            "SELECT 1 FROM diss_notifications WHERE user_id = %s "
                            "AND event_type = 'followed_scholar_new_student' "
                            "AND payload->>'diss_id' = %s LIMIT 1",
                            (user_id, str(diss_id)))
                        if cur.fetchone():
                            continue
                        payload = json.dumps({
                            'scholar_name': adv_name,
                            'student_name': clean_olim_name(d.get('olim') or ''),
                            'mavzu': (d.get('mavzu') or '')[:160],
                            'diss_id': diss_id,
                        }, ensure_ascii=False)
                        cur.execute(
                            "INSERT INTO diss_notifications "
                            "(user_id, event_type, payload) "
                            "VALUES (%s, 'followed_scholar_new_student', %s)",
                            (user_id, payload))
                        created += 1
            conn.commit()
        finally:
            conn.close()
    except Exception:
        return created
    return created


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
