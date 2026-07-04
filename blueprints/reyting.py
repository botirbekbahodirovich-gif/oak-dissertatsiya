"""Reyting blueprint — unified organization ↔ scholar rating (uz.h-index model).

One public page (/reyting) where organizations and their scholars form a single
relational chain over the existing linkage:
    dissertations.muassasa → institution_map.cyrillic_name → canonical org

The chain property: an organization's score is the SUM of its scholars' scores,
and every scholar row carries their organization's republic-wide rank — so the
same numbers appear whichever end you enter from. Existing profile pages are
the link targets (/university/<canonical>, /olim/<name>).

Scoring (transparent, from data we actually have — no citation index exists):
    scholar = 50·DSc + 30·PhD + 20·boshqa diss + 8·rahbarlik + 2·opponentlik
    organization = Σ scholar scores of scholars whose primary org it is

Unified filters (region / category / ixtisoslik code / search) apply to BOTH
tabs from the same controls ("bitta tugma bilan").

Heavy aggregation (~27k dissertations) is built once and cached 30 min; the
filter endpoints slice the cached structure in Python.
"""
from flask import Blueprint, jsonify, request, render_template

reyting_bp = Blueprint('reyting', __name__)

_CACHE_KEY = 'reyting_data_v2'
_CACHE_TTL = 1800  # 30 min

# score weights
_W_DSC, _W_PHD, _W_OTHER, _W_RAHBAR, _W_OPP = 50, 30, 20, 8, 2


def _classify(daraja):
    up = (daraja or '').upper()
    low = (daraja or '').lower()
    if 'DSC' in up or 'док' in low:
        return 'dsc'
    if 'PHD' in up or 'фал' in low or 'фан' in low:
        return 'phd'
    return 'other'


def _build_rating():
    """Aggregate scholars + organizations from the DB. Cached 30 minutes."""
    from app import cache
    data = cache.get(_CACHE_KEY)
    if data:
        return data
    from data import get_connection, clean_olim_name
    from institutions import transliterate_display

    imap = {}   # cyrillic variant (trimmed) → org meta
    rows = []   # (olim, muassasa, daraja, ixtisoslik)
    rahbar = {}
    opp = {}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute("""
                    SELECT TRIM(cyrillic_name), TRIM(COALESCE(canonical_name, cyrillic_name)),
                           COALESCE(category, 'boshqa'), COALESCE(region, '')
                    FROM institution_map WHERE is_active IS NOT FALSE
                """)
                for cyr, canon, cat, reg in cur.fetchall():
                    imap[cyr] = {'canonical': canon, 'category': cat, 'region': reg}
            except Exception:
                imap = {}  # map not populated yet — orgs group by raw muassasa
            cur.execute("""
                SELECT TRIM(olim), TRIM(COALESCE(muassasa, '')),
                       COALESCE(daraja, ''), TRIM(COALESCE(ixtisoslik, ''))
                FROM dissertations
                WHERE olim IS NOT NULL AND TRIM(olim) <> ''
            """)
            rows = cur.fetchall()
            cur.execute("""
                SELECT LOWER(TRIM(ilmiy_rahbar)), COUNT(*)
                FROM dissertations
                WHERE ilmiy_rahbar IS NOT NULL AND TRIM(ilmiy_rahbar) <> ''
                GROUP BY 1
            """)
            rahbar = {k: v for k, v in cur.fetchall()}
            for col in ('opponent_1', 'opponent_2', 'opponent_3'):
                try:
                    cur.execute(f"""
                        SELECT LOWER(TRIM({col})), COUNT(*)
                        FROM dissertations
                        WHERE {col} IS NOT NULL AND TRIM({col}) <> ''
                        GROUP BY 1
                    """)
                    for k, v in cur.fetchall():
                        opp[k] = opp.get(k, 0) + v
                except Exception:
                    pass  # column may not exist on a fresh DB
    finally:
        conn.close()

    def org_meta(muassasa):
        m = imap.get(muassasa)
        if m:
            return m
        canon = muassasa or 'Noma’lum muassasa'
        return {'canonical': canon, 'category': 'boshqa', 'region': ''}

    # ── scholars ──
    scholars = {}  # key = lower(cleaned name)
    for olim, muassasa, daraja, code in rows:
        name = clean_olim_name(olim)
        key = name.lower()
        s = scholars.get(key)
        if not s:
            s = scholars[key] = {'name': name, 'dsc': 0, 'phd': 0, 'other': 0,
                                 'codes': set(), 'org_votes': {}}
        s[_classify(daraja)] += 1
        if code:
            s['codes'].add(code)
        if muassasa:
            s['org_votes'][muassasa] = s['org_votes'].get(muassasa, 0) + 1

    for key, s in scholars.items():
        # primary org = the variant this scholar defended at most often
        if s['org_votes']:
            variant = max(s['org_votes'], key=s['org_votes'].get)
            s['org'] = org_meta(variant)['canonical']
        else:
            s['org'] = ''
        s['rahbarlik'] = rahbar.get(key, 0)
        s['opponentlik'] = opp.get(key, 0)
        s['score'] = (s['dsc'] * _W_DSC + s['phd'] * _W_PHD + s['other'] * _W_OTHER
                      + s['rahbarlik'] * _W_RAHBAR + s['opponentlik'] * _W_OPP)
        del s['org_votes']

    # ── organizations (chain: score = Σ member scholar scores) ──
    orgs = {}  # key = canonical name
    for olim, muassasa, daraja, code in rows:
        meta = org_meta(muassasa)
        o = orgs.get(meta['canonical'])
        if not o:
            o = orgs[meta['canonical']] = {
                'canonical': meta['canonical'], 'category': meta['category'],
                'region': meta['region'], 'diss': 0, 'dsc': 0, 'phd': 0,
                'codes': set(), 'scholars': 0, 'score': 0}
        o['diss'] += 1
        kind = _classify(daraja)
        if kind in ('dsc', 'phd'):
            o[kind] += 1
        if code:
            o['codes'].add(code)
    for s in scholars.values():
        o = orgs.get(s['org'])
        if o:
            o['scholars'] += 1
            o['score'] += s['score']

    org_list = sorted(orgs.values(), key=lambda o: (-o['score'], o['canonical']))
    for i, o in enumerate(org_list, 1):
        o['rank'] = i
        o['latin'] = transliterate_display(o['canonical'])
        o['codes'] = sorted(o['codes'])
    org_rank = {o['canonical']: o['rank'] for o in org_list}
    org_latin = {o['canonical']: o['latin'] for o in org_list}

    sch_list = sorted(scholars.values(), key=lambda s: (-s['score'], s['name']))
    for i, s in enumerate(sch_list, 1):
        s['rank'] = i
        s['codes'] = sorted(s['codes'])
        s['org_rank'] = org_rank.get(s['org'])
        s['org_latin'] = org_latin.get(s['org'], s['org'])

    regions = sorted({o['region'] for o in org_list if o['region']})
    data = {'orgs': org_list, 'scholars': sch_list, 'regions': regions,
            'totals': {'orgs': len(org_list), 'scholars': len(sch_list),
                       'dissertations': len(rows)}}
    cache.set(_CACHE_KEY, data, timeout=_CACHE_TTL)
    return data


def _matches_code(codes, wanted):
    """ixtisoslik filter — exact code or prefix ('05' hits '05.01.01')."""
    return any(c == wanted or c.startswith(wanted) for c in codes)


def _apply_filters(orgs, region, category, code, q):
    """Unified filters over the organization list (scholars are filtered in
    api_olimlar through their org — same params, same semantics)."""
    out = orgs
    if region:
        out = [o for o in out if o['region'] == region]
    if category:
        out = [o for o in out if o['category'] == category]
    if code:
        out = [o for o in out if _matches_code(o['codes'], code)]
    if q:
        ql = q.lower()
        out = [o for o in out
               if ql in o['latin'].lower() or ql in o['canonical'].lower()]
    return out


# ── Page ─────────────────────────────────────────────────────────────────────

@reyting_bp.route('/reyting')
def reyting_page():
    from institutions import INSTITUTION_CATEGORIES
    regions, totals = [], {}
    try:
        data = _build_rating()
        regions, totals = data['regions'], data['totals']
    except Exception:
        pass
    categories = dict(INSTITUTION_CATEGORIES)
    categories.setdefault('boshqa', 'Boshqa tashkilotlar')
    return render_template('reyting.html', regions=regions, totals=totals,
                           categories=categories)


# ── Filterable APIs (same filter params drive both tabs) ────────────────────

@reyting_bp.route('/api/v1/reyting/orgs')
def api_orgs():
    region = (request.args.get('region') or '').strip()
    category = (request.args.get('category') or '').strip()
    code = (request.args.get('code') or '').strip()
    q = (request.args.get('q') or '').strip()
    try:
        data = _build_rating()
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
    items = _apply_filters(data['orgs'], region, category, code, q)
    return jsonify({'ok': True, 'total': len(items), 'orgs': [{
        'rank': o['rank'], 'canonical': o['canonical'], 'latin': o['latin'],
        'category': o['category'], 'region': o['region'], 'scholars': o['scholars'],
        'diss': o['diss'], 'dsc': o['dsc'], 'phd': o['phd'], 'score': o['score'],
    } for o in items[:500]]})


@reyting_bp.route('/api/v1/reyting/olimlar')
def api_olimlar():
    region = (request.args.get('region') or '').strip()
    category = (request.args.get('category') or '').strip()
    code = (request.args.get('code') or '').strip()
    q = (request.args.get('q') or '').strip()
    org = (request.args.get('org') or '').strip()
    offset = max(0, request.args.get('offset', 0, type=int))
    limit = min(200, max(1, request.args.get('limit', 100, type=int)))
    try:
        data = _build_rating()
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
    # Scholars inherit region/category from their primary org (the chain).
    org_by_canon = {o['canonical']: o for o in data['orgs']}
    items = data['scholars']
    if region or category:
        def org_ok(s):
            o = org_by_canon.get(s['org'])
            if not o:
                return False
            return ((not region or o['region'] == region)
                    and (not category or o['category'] == category))
        items = [s for s in items if org_ok(s)]
    if code:
        items = [s for s in items if _matches_code(s['codes'], code)]
    if org:
        items = [s for s in items if s['org'] == org]
    if q:
        ql = q.lower()
        items = [s for s in items if ql in s['name'].lower()
                 or ql in (s['org_latin'] or '').lower()]
    total = len(items)
    page = items[offset:offset + limit]
    return jsonify({'ok': True, 'total': total, 'offset': offset, 'olimlar': [{
        'rank': s['rank'], 'name': s['name'], 'dsc': s['dsc'], 'phd': s['phd'],
        'other': s['other'], 'rahbarlik': s['rahbarlik'],
        'opponentlik': s['opponentlik'], 'score': s['score'],
        'org': s['org'], 'org_latin': s['org_latin'], 'org_rank': s['org_rank'],
    } for s in page]})
