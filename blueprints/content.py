"""Content blueprint — public-facing informational routes extracted from app.py.

Covers the read-only content pages: news (yangiliklar), vacancies, blog,
universities, and journals. URL paths are unchanged, so existing links keep
working; endpoints are namespaced under the 'content' blueprint.

Shared helpers and module constants stay in app.py and are lazy-imported inside
each view (auth.py / cabinet.py pattern) to avoid circular imports.
"""
from flask import Blueprint, render_template, request, abort
from flask_login import current_user

content_bp = Blueprint('content', __name__)


@content_bp.route("/yangiliklar")
def yangiliklar():
    from data import get_connection
    from app import _placeholder_news
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1
    per_page = 20
    offset = (page - 1) * per_page
    items = []
    total = 0
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM yangiliklar WHERE is_published = TRUE")
                total = cur.fetchone()[0] or 0
                cur.execute(
                    "SELECT id, title, summary, created_at, image_url, image_data FROM yangiliklar "
                    "WHERE is_published = TRUE ORDER BY created_at DESC "
                    "LIMIT %s OFFSET %s",
                    (per_page, offset)
                )
                items = [{
                    "id": r[0], "title": r[1] or "", "summary": r[2] or "",
                    "created_at": str(r[3])[:10] if r[3] else "",
                    "image": r[4] or r[5] or "", "is_placeholder": False,
                } for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        items, total = [], 0

    if not items and page == 1:
        items = _placeholder_news()
        total = len(items)

    total_pages = max(1, (total + per_page - 1) // per_page)
    return render_template("yangiliklar.html", items=items, page=page,
                           total_pages=total_pages, total=total)


@content_bp.route("/yangiliklar/<int:id>")
def yangilik_detail(id):
    from data import get_connection
    item = None
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, title, content, summary, source_url, created_at, image_url, image_data "
                    "FROM yangiliklar WHERE id = %s AND is_published = TRUE",
                    (id,)
                )
                r = cur.fetchone()
                if r:
                    item = {
                        "id": r[0], "title": r[1] or "", "content": r[2] or "",
                        "summary": r[3] or "", "source_url": r[4] or "",
                        "created_at": str(r[5])[:16] if r[5] else "",
                        "image": r[6] or r[7] or "",
                    }
        finally:
            conn.close()
    except Exception:
        item = None
    if not item:
        abort(404)
    return render_template("yangilik_detail.html", item=item)


@content_bp.route("/vacancies")
def vacancies():
    from data import get_connection
    from app import _vacancy_from_row, VACANCY_TYPES
    items = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM vacancies WHERE is_published = TRUE "
                    "ORDER BY created_at DESC"
                )
                cols = [d[0] for d in cur.description]
                items = [_vacancy_from_row(cols, r) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        items = []
    return render_template("vacancies.html", items=items, vacancy_types=VACANCY_TYPES)


@content_bp.route("/vacancies/<int:id>")
def vacancy_detail(id):
    from data import get_connection
    from app import _vacancy_from_row
    item = None
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM vacancies WHERE id = %s AND is_published = TRUE", (id,))
                row = cur.fetchone()
                if row:
                    cols = [d[0] for d in cur.description]
                    item = _vacancy_from_row(cols, row)
        finally:
            conn.close()
    except Exception:
        item = None
    if not item:
        abort(404)
    return render_template("vacancy_detail.html", item=item)


@content_bp.route("/blog")
def blog():
    from data import get_connection
    from app import BLOG_CATEGORIES
    category = (request.args.get("category") or "").strip()
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1
    per_page = 12
    offset = (page - 1) * per_page
    posts, total = [], 0
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                where = "WHERE is_published = TRUE"
                params = []
                if category in BLOG_CATEGORIES:
                    where += " AND category = %s"
                    params.append(category)
                cur.execute(f"SELECT COUNT(*) FROM blog_posts {where}", params)
                total = cur.fetchone()[0] or 0
                cur.execute(
                    f"SELECT id, title, slug, summary, category, image_url, author, views, created_at "
                    f"FROM blog_posts {where} ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
                    params + [per_page, offset])
                posts = [{
                    "id": r[0], "title": r[1] or "", "slug": r[2] or "",
                    "summary": r[3] or "", "category": r[4] or "",
                    "category_label": BLOG_CATEGORIES.get(r[4] or "", r[4] or ""),
                    "image_url": r[5] or "", "author": r[6] or "", "views": r[7] or 0,
                    "created_at": str(r[8])[:10] if r[8] else "",
                } for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        posts, total = [], 0
    total_pages = max(1, (total + per_page - 1) // per_page)
    return render_template("blog.html", posts=posts, page=page, total_pages=total_pages,
                           total=total, category=category, categories=BLOG_CATEGORIES)


@content_bp.route("/blog/<slug>")
def blog_post(slug):
    from data import get_connection
    from app import BLOG_CATEGORIES
    post = None
    related = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, title, slug, summary, content, category, image_url, author, "
                    "views, created_at FROM blog_posts WHERE slug = %s AND is_published = TRUE", (slug,))
                r = cur.fetchone()
                if r:
                    post = {
                        "id": r[0], "title": r[1] or "", "slug": r[2] or "",
                        "summary": r[3] or "", "content": r[4] or "", "category": r[5] or "",
                        "category_label": BLOG_CATEGORIES.get(r[5] or "", r[5] or ""),
                        "image_url": r[6] or "", "author": r[7] or "", "views": (r[8] or 0) + 1,
                        "created_at": str(r[9])[:10] if r[9] else "",
                    }
                    cur.execute("UPDATE blog_posts SET views = views + 1 WHERE id = %s", (r[0],))
                    conn.commit()
                    cur.execute(
                        "SELECT title, slug, summary, category FROM blog_posts "
                        "WHERE is_published = TRUE AND category = %s AND id <> %s "
                        "ORDER BY created_at DESC LIMIT 3", (post["category"], post["id"]))
                    related = [{
                        "title": rr[0], "slug": rr[1], "summary": rr[2] or "",
                        "category_label": BLOG_CATEGORIES.get(rr[3] or "", rr[3] or ""),
                    } for rr in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        post = None
    if not post:
        abort(404)
    return render_template("blog_detail.html", post=post, related=related)


@content_bp.route('/universities')
def universities():
    from data import get_connection
    from app import (get_institution_directory, get_university_dissertation_stats,
                     _uni_keywords, _UNI_TYPE_LABELS, INSTITUTION_CATEGORIES)
    from institutions import detect_category

    # Primary source: institution_map (every real defence institution, deduped,
    # categorized, with real dissertation counts). The curated `universities`
    # table (manually-added OTMs incl. xorijiy — logos, types) is merged in:
    # matching map entries get enriched, unmatched curated rows get appended.
    try:
        directory = get_institution_directory()
    except Exception:
        directory = []

    curated = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, logo_url, city, region, university_type "
                            "FROM universities WHERE is_active = TRUE")
                curated = [{'id': r[0], 'name': r[1], 'logo_url': r[2] or '',
                            'city': r[3] or '', 'region': r[4] or '',
                            'university_type': r[5] or ''} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        curated = []

    items = []
    if directory:
        for d in directory:
            items.append({
                'name': d['name'],                                # canonical Cyrillic — profile URL
                'display': d.get('latin_name') or d['name'],      # Latin, primary label
                'sub': d['name'],                                 # Cyrillic, secondary label
                'category': d.get('category') or 'universitet',
                'region': d.get('region') or '',
                'logo_url': '', 'type_label': '',
                'diss_count': d.get('diss_count', 0),
                'olim_count': d.get('olim_count', 0),
                'ixt_count': d.get('ixt_count', 0),
            })
        for cu in curated:
            kws = _uni_keywords(cu['name'])
            match = None
            if kws:
                for it in items:
                    hay = (it['display'] or '').lower()
                    if all(k in hay for k in kws):
                        match = it
                        break
            if match:
                # enrich the map entry with curated metadata (logo, region, type)
                match['logo_url'] = match['logo_url'] or cu['logo_url']
                match['region'] = match['region'] or cu['region'] or cu['city']
                if cu['university_type'] and not match['type_label']:
                    match['type_label'] = _UNI_TYPE_LABELS.get(cu['university_type'], '')
            else:
                # manually-added university with no defence data (e.g. xorijiy)
                items.append({
                    'name': cu['name'], 'display': cu['name'], 'sub': '',
                    'category': detect_category(cu['name']),
                    'region': cu['region'] or cu['city'],
                    'logo_url': cu['logo_url'],
                    'type_label': _UNI_TYPE_LABELS.get(cu['university_type'], ''),
                    'diss_count': 0, 'olim_count': 0, 'ixt_count': 0,
                })
    else:
        # Map not populated yet — curated list with the legacy keyword stats.
        stats = {}
        try:
            stats = get_university_dissertation_stats()
        except Exception:
            pass
        for cu in curated:
            s = stats.get(cu['id'], {})
            items.append({
                'name': cu['name'], 'display': cu['name'], 'sub': '',
                'category': detect_category(cu['name']),
                'region': cu['region'] or cu['city'],
                'logo_url': cu['logo_url'],
                'type_label': _UNI_TYPE_LABELS.get(cu['university_type'], ''),
                'diss_count': s.get('total', 0), 'olim_count': s.get('olimlar', 0),
                'ixt_count': 0,
            })

    items.sort(key=lambda x: (-x['diss_count'], (x['display'] or '').lower()))
    regions = sorted({i['region'] for i in items if i['region']})
    return render_template('universities.html', items=items, regions=regions,
                           categories=INSTITUTION_CATEGORIES)


@content_bp.route('/university/<path:name>')
def university_profile(name):
    from data import get_connection, clean_olim_name
    from app import _uni_where, _find_university, detect_uni_city_region
    term = (name or '').strip()
    where, params = _uni_where(term)
    uni = None
    stats = {'total': 0, 'phd': 0, 'dsc': 0, 'olimlar': 0, 'ixtisosliklar': 0, 'rahbarlar': 0}
    top_olimlar, top_rahbarlar, recent, by_year, top_ixtisos = [], [], [], [], []
    gallery, rector_olim, im_info = [], None, None
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                uni = _find_university(cur, term)
                # Prefer exact institution_map variant matching so the profile's
                # counts equal the /universities directory's grouped counts.
                # Falls back to the fuzzy _uni_where computed above.
                try:
                    cur.execute(
                        "SELECT cyrillic_name FROM institution_map "
                        "WHERE canonical_name = %s OR cyrillic_name = %s", (term, term))
                    variants = [r[0] for r in cur.fetchall()]
                    if variants:
                        where = "TRIM(muassasa) IN (" + ",".join(["%s"] * len(variants)) + ")"
                        params = list(variants)
                    cur.execute(
                        "SELECT MAX(canonical_name), MAX(category), MAX(region) "
                        "FROM institution_map "
                        "WHERE canonical_name = %s OR cyrillic_name = %s", (term, term))
                    imr = cur.fetchone()
                    if imr and imr[0]:
                        from institutions import transliterate_display
                        im_info = {'latin_name': transliterate_display(imr[0]),
                                   'category': imr[1] or '', 'region': imr[2] or ''}
                except Exception:
                    pass
                cur.execute(f"""
                    SELECT COUNT(*),
                           SUM(CASE WHEN daraja ILIKE '%%PhD%%' OR daraja ILIKE '%%фан%%' THEN 1 ELSE 0 END),
                           SUM(CASE WHEN daraja ILIKE '%%DSc%%' OR daraja ILIKE '%%док%%' THEN 1 ELSE 0 END),
                           COUNT(DISTINCT olim), COUNT(DISTINCT ixtisoslik), COUNT(DISTINCT ilmiy_rahbar)
                    FROM dissertations WHERE {where}
                """, params)
                r = cur.fetchone()
                if r:
                    stats = {'total': r[0] or 0, 'phd': r[1] or 0, 'dsc': r[2] or 0,
                             'olimlar': r[3] or 0, 'ixtisosliklar': r[4] or 0, 'rahbarlar': r[5] or 0}
                cur.execute(f"""
                    SELECT TRIM(olim), COUNT(*) cnt, MAX(daraja), MAX(photo_url)
                    FROM dissertations WHERE {where} AND olim IS NOT NULL AND TRIM(olim) <> ''
                    GROUP BY TRIM(olim) ORDER BY cnt DESC LIMIT 24
                """, params)
                top_olimlar = [{'name': x[0], 'display': clean_olim_name(x[0]), 'count': x[1],
                                'daraja': x[2] or '', 'photo_url': x[3] or ''} for x in cur.fetchall()]
                cur.execute(f"""
                    SELECT TRIM(ilmiy_rahbar), COUNT(*) cnt, MAX(ilmiy_rahbar_photo_url)
                    FROM dissertations WHERE {where} AND ilmiy_rahbar IS NOT NULL AND TRIM(ilmiy_rahbar) <> ''
                    GROUP BY TRIM(ilmiy_rahbar) ORDER BY cnt DESC LIMIT 10
                """, params)
                top_rahbarlar = [{'name': x[0], 'display': clean_olim_name(x[0]), 'count': x[1],
                                  'photo_url': x[2] or ''} for x in cur.fetchall()]
                cur.execute(f"""
                    SELECT id, olim, mavzu, daraja, sana FROM dissertations WHERE {where}
                    ORDER BY id DESC LIMIT 30
                """, params)
                recent = [{'id': x[0], 'olim': x[1] or '', 'display': clean_olim_name(x[1] or ''),
                           'mavzu': x[2] or '', 'daraja': x[3] or '', 'sana': x[4] or ''}
                          for x in cur.fetchall()]
                cur.execute(f"""
                    SELECT substring(sana from '(19|20)[0-9][0-9]') AS yr, COUNT(*)
                    FROM dissertations WHERE {where} AND sana ~ '(19|20)[0-9][0-9]'
                    GROUP BY yr ORDER BY yr
                """, params)
                by_year = [{'year': x[0], 'count': x[1]} for x in cur.fetchall() if x[0]]
                cur.execute(f"""
                    SELECT TRIM(ixtisoslik), COUNT(*) cnt FROM dissertations
                    WHERE {where} AND ixtisoslik IS NOT NULL AND TRIM(ixtisoslik) <> ''
                    GROUP BY TRIM(ixtisoslik) ORDER BY cnt DESC LIMIT 30
                """, params)
                top_ixtisos = [{'name': x[0], 'count': x[1]} for x in cur.fetchall()]
                # Gallery images for this university.
                if uni and uni.get('id'):
                    cur.execute("SELECT id, image_url, caption FROM university_images "
                                "WHERE university_id = %s ORDER BY id", (uni['id'],))
                    gallery = [{'id': g[0], 'image_url': g[1], 'caption': g[2] or ''}
                               for g in cur.fetchall()]
                # If the rector's name exactly matches a known olim profile, link it.
                if uni and uni.get('rector'):
                    cur.execute("SELECT olim_name FROM olim_profiles "
                                "WHERE LOWER(TRIM(olim_name)) = LOWER(TRIM(%s)) LIMIT 1",
                                (uni['rector'],))
                    rr = cur.fetchone()
                    if rr:
                        rector_olim = rr[0]
        finally:
            conn.close()
    except Exception:
        pass

    if not uni and stats['total'] == 0:
        abort(404)
    if not uni:
        city, region = detect_uni_city_region(term)
        uni = {'id': None, 'name': term, 'short_name': '', 'logo_url': '', 'website': '',
               'city': city, 'region': region, 'university_type': '', 'type_label': '',
               'description': '', 'founded_year': None, 'rector': '', 'address': '',
               'phone': '', 'email': '', 'telegram': ''}

    # Institution-map metadata (Latin name, category, region) for the header.
    if im_info:
        # Curated rows already carry a proper Latin name; only map-only
        # (Cyrillic) profiles get the transliterated display name.
        if not uni.get('id'):
            uni['latin_name'] = im_info['latin_name']
        uni['im_category'] = im_info['category']
        if not uni.get('region'):
            uni['region'] = im_info['region']
    stats['years'] = (by_year[0]['year'] + ' – ' + by_year[-1]['year']) if len(by_year) > 1 \
        else (by_year[0]['year'] if by_year else '')

    is_admin = (current_user.is_authenticated and getattr(current_user, 'is_admin', False))
    return render_template('university_profile.html', uni=uni, stats=stats,
                           top_olimlar=top_olimlar, top_rahbarlar=top_rahbarlar,
                           recent=recent, by_year=by_year, top_ixtisos=top_ixtisos,
                           gallery=gallery, rector_olim=rector_olim,
                           is_admin=is_admin)


@content_bp.route('/journals')
def journals():
    from data import get_connection
    from app import SPECIALTY_NAMES
    items, spec_counts = [], {}
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT j.id, j.name, j.name_en, j.country, j.languages, j.indexing,
                           j.impact_factor, j.publish_fee, j.review_period, j.frequency,
                           j.logo_url, j.oak_approved, j.scopus_indexed, j.wos_indexed,
                           j.is_predatory, j.scholar_indexed,
                           COALESCE(string_agg(DISTINCT js.specialty_code, ',' ORDER BY js.specialty_code), '') AS codes
                    FROM journals j
                    LEFT JOIN journal_specialties js ON js.journal_id = j.id
                    WHERE j.is_active = TRUE
                    GROUP BY j.id
                    ORDER BY LOWER(j.name)
                """)
                for r in cur.fetchall():
                    items.append({
                        "id": r[0], "name": r[1] or "", "name_en": r[2] or "",
                        "country": r[3] or "", "languages": r[4] or "", "indexing": r[5] or "",
                        "impact_factor": r[6], "publish_fee": r[7] or "", "review_period": r[8] or "",
                        "frequency": r[9] or "", "logo_url": r[10] or "", "oak_approved": r[11],
                        "scopus_indexed": r[12], "wos_indexed": r[13], "is_predatory": r[14],
                        "scholar_indexed": r[15],
                        "codes": [c for c in (r[16] or '').split(',') if c],
                    })
                cur.execute("SELECT specialty_code, COUNT(DISTINCT journal_id) "
                            "FROM journal_specialties GROUP BY specialty_code")
                spec_counts = {r[0]: r[1] for r in cur.fetchall()}
        finally:
            conn.close()
    except Exception:
        items = []
    oak_count = sum(1 for j in items if j['oak_approved'])
    scopus_count = sum(1 for j in items if j['scopus_indexed'])
    return render_template('journals.html', items=items,
                           specialty_names=SPECIALTY_NAMES, spec_counts=spec_counts,
                           total_journals=len(items), oak_count=oak_count,
                           scopus_count=scopus_count)


@content_bp.route('/journals/<int:id>')
def journal_detail(id):
    from data import get_connection
    from app import _journal_row, SPECIALTY_NAMES
    journal, codes, similar = None, [], []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM journals WHERE id = %s", (id,))
                row = cur.fetchone()
                if row:
                    journal = _journal_row([c[0] for c in cur.description], row)
                    # Specialty codes for this journal + dissertation counts per field.
                    cur.execute("SELECT specialty_code, specialty_name FROM journal_specialties "
                                "WHERE journal_id = %s ORDER BY specialty_code", (id,))
                    for code, sname in cur.fetchall():
                        prefix = (code.split('.')[0] + '.') if '.' in code else code
                        try:
                            cur.execute("SELECT COUNT(*) FROM dissertations WHERE ixtisoslik LIKE %s",
                                        (prefix + '%',))
                            cnt = cur.fetchone()[0] or 0
                        except Exception:
                            cnt = 0
                        codes.append({"code": code, "name": sname or SPECIALTY_NAMES.get(code, ''),
                                      "count": cnt})
                    if codes:
                        code_list = [c["code"] for c in codes]
                        cur.execute("""
                            SELECT j.id, j.name, j.logo_url,
                                   COALESCE(string_agg(DISTINCT js2.specialty_code, ',' ORDER BY js2.specialty_code), '')
                            FROM journals j
                            JOIN journal_specialties js ON js.journal_id = j.id
                            LEFT JOIN journal_specialties js2 ON js2.journal_id = j.id
                            WHERE js.specialty_code = ANY(%s) AND j.id <> %s AND j.is_active = TRUE
                            GROUP BY j.id ORDER BY LOWER(j.name) LIMIT 4
                        """, (code_list, id))
                        similar = [{"id": x[0], "name": x[1], "logo_url": x[2] or "",
                                    "codes": [c for c in (x[3] or '').split(',') if c]}
                                   for x in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        journal = None
    if not journal:
        abort(404)
    is_admin = (current_user.is_authenticated and getattr(current_user, 'is_admin', False))
    return render_template('journal_detail.html', j=journal, codes=codes,
                           similar=similar, is_admin=is_admin)
