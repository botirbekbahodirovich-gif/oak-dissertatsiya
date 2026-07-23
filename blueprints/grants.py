"""Grants blueprint v2 — Ilmiy grantlar va stipendiyalar moduli.

EVOLVES the original module in place (no parallel system): the legacy `grants`
columns that scripts/grant_scraper.py writes (title, description,
scientific_codes, country, funding_type, academic_level, application_deadline,
source_url, requirements_json, provider) stay valid; v2 adds rich columns
(slug, title_uz, organization, academic_levels[], documents_checklist JSONB,
stipend_amount, view_count, is_featured, …) with legacy fallbacks at read time.

Schema is created/migrated lazily on first request (idempotent — mirrors
migrations/add_grants_tables.sql), so the server self-migrates without a
manual psql run. Tracking moved to `user_grant_tracking` (richer statuses);
rows from the old `user_tracked_grants` are migrated once and the old table is
left untouched as history.

Preserved public contracts (consumed elsewhere):
  GET  /grants                    — directory page (now v2 UI)
  GET  /grants/<int:id>           — detail by id (base.html deadline modal links here)
  GET  /api/v1/grants             — legacy JSON listing shape
  POST /api/v1/grants/track       — legacy tracking body (interested/in_progress/remove)
  GET  /api/v1/grants/reminders   — 7-day deadline feed for the global session modal
  /admin/grants*                  — admin CRUD (new UI, same paths)

New in v2:
  GET  /grants/<slug>             — canonical detail URL
  GET  /api/grants                — filterable JSON (country/funding/level/field/search/sort/page)
  POST /api/grants/<id>/track     — {"status": ...} | {"action": "untrack"}
  POST /api/grants/<id>/update-status — {"status": ..., "notes": ...}
  GET  /api/grants/my-tracked     — kanban feed for /grants?my=1
  GET  /api/grants/deadline-alerts — ≤7-day alerts for tracked grants
  POST /admin/grants/toggle-featured/<id>, /admin/grants/seed
"""
import json
import re
from datetime import date, datetime

from flask import (Blueprint, jsonify, request, render_template,
                   redirect, abort, flash, session)
from flask_login import login_required, current_user

from app import csrf
from utils.search_helper import build_search_clause

grants_bp = Blueprint('grants', __name__)

_schema_ready = False

PER_PAGE = 12

# canonical stored keys → Uzbek labels
FUNDING_TYPES = {'full': "To'liq grant", 'partial': 'Qisman', 'research': 'Tadqiqot granti'}
LEVELS = {'Master': 'Magistr', 'PhD': 'PhD', 'Postdoc': 'Postdoc', 'Research': 'Tadqiqot'}
TRACK_STATUSES = ('interested', 'preparing', 'documents_ready',
                  'applied', 'accepted', 'rejected')
STATUS_LABELS = {
    'interested': 'Qiziqarli', 'preparing': 'Tayyorlanmoqda',
    'documents_ready': 'Hujjatlar tayyor', 'applied': 'Ariza yuborildi',
    'accepted': 'Qabul qilindi', 'rejected': 'Rad etildi',
}
SORTS = ('deadline', 'new', 'popular', 'stipend')

COUNTRY_FLAGS = {
    'germaniya': '🇩🇪', 'germany': '🇩🇪', 'aqsh': '🇺🇸', 'usa': '🇺🇸',
    'buyuk britaniya': '🇬🇧', 'uk': '🇬🇧', 'angliya': '🇬🇧',
    'yaponiya': '🇯🇵', 'japan': '🇯🇵', 'turkiya': '🇹🇷', 'turkey': '🇹🇷',
    'janubiy koreya': '🇰🇷', 'koreya': '🇰🇷', 'south korea': '🇰🇷',
    'xitoy': '🇨🇳', 'china': '🇨🇳', 'vengriya': '🇭🇺', 'hungary': '🇭🇺',
    'shveysariya': '🇨🇭', 'switzerland': '🇨🇭', 'avstraliya': '🇦🇺',
    'australia': '🇦🇺', 'kanada': '🇨🇦', 'canada': '🇨🇦',
    'fransiya': '🇫🇷', 'france': '🇫🇷', 'italiya': '🇮🇹', 'italy': '🇮🇹',
    'niderlandiya': '🇳🇱', 'gollandiya': '🇳🇱', 'netherlands': '🇳🇱',
    'yevropa ittifoqi': '🇪🇺', 'eu': '🇪🇺', 'yevropa': '🇪🇺',
    "o'zbekiston": '🇺🇿', 'ozbekiston': '🇺🇿', 'uzbekistan': '🇺🇿',
    'rossiya': '🇷🇺', 'russia': '🇷🇺', 'hindiston': '🇮🇳', 'india': '🇮🇳',
    'ispaniya': '🇪🇸', 'spain': '🇪🇸', 'shvetsiya': '🇸🇪', 'sweden': '🇸🇪',
    'norvegiya': '🇳🇴', 'norway': '🇳🇴', 'finlyandiya': '🇫🇮', 'finland': '🇫🇮',
}


# ── helpers ──────────────────────────────────────────────────────────────────

def get_country_flag(country):
    key = (country or '').strip().lower().replace('ʻ', "'").replace('`', "'")
    return COUNTRY_FLAGS.get(key, '🌍')


def calculate_days_remaining(deadline):
    """Days until deadline (negative if passed); None if no deadline."""
    if not deadline:
        return None
    if isinstance(deadline, str):
        try:
            deadline = date.fromisoformat(deadline[:10])
        except ValueError:
            return None
    if isinstance(deadline, datetime):
        deadline = deadline.date()
    return (deadline - date.today()).days


def generate_slug(title, fallback_id=None):
    """URL-friendly slug: Cyrillic→Latin, lowercase, dashes. Never digits-only
    (would collide with the /grants/<int:id> route)."""
    from institutions import transliterate
    s = transliterate((title or '').lower())
    s = s.replace("'", '').replace('ʻ', '').replace('`', '')
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')[:80].strip('-')
    if not s or s.replace('-', '').isdigit():
        s = f'grant-{fallback_id or ""}'.strip('-')
    return s


def _uz_date(d):
    months = ['yanvar', 'fevral', 'mart', 'aprel', 'may', 'iyun', 'iyul',
              'avgust', 'sentabr', 'oktabr', 'noyabr', 'dekabr']
    if isinstance(d, str):
        try:
            d = date.fromisoformat(d[:10])
        except ValueError:
            return d
    if isinstance(d, datetime):
        d = d.date()
    return f'{d.day} {months[d.month - 1]} {d.year}' if d else ''


# ── schema (lazy, idempotent — mirrors migrations/add_grants_tables.sql) ────

def _ensure_schema(cur):
    global _schema_ready
    if _schema_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS grants (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            scientific_codes TEXT,
            country TEXT,
            funding_type VARCHAR(20),
            academic_level VARCHAR(20),
            application_deadline DATE,
            source_url TEXT UNIQUE,
            requirements_json JSONB,
            provider TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    for col, typ in (
        ('title_uz', 'VARCHAR(500)'), ('slug', 'VARCHAR(200)'),
        ('organization', 'VARCHAR(300)'), ('country_flag', 'VARCHAR(10)'),
        ('academic_levels', "TEXT[] DEFAULT '{}'"),
        ('scientific_fields', "TEXT[] DEFAULT '{}'"),
        ('requirements', 'TEXT'), ('benefits', 'TEXT'),
        ('documents_checklist', "JSONB DEFAULT '[]'"),
        ('application_tips', 'TEXT'), ('start_date', 'DATE'),
        ('stipend_amount', 'VARCHAR(200)'), ('duration', 'VARCHAR(200)'),
        ('language_requirements', 'VARCHAR(300)'), ('source_id', 'VARCHAR(200)'),
        ('cover_image_url', 'VARCHAR(500)'), ('tags', "TEXT[] DEFAULT '{}'"),
        ('view_count', 'INTEGER DEFAULT 0'), ('is_active', 'BOOLEAN DEFAULT TRUE'),
        ('is_featured', 'BOOLEAN DEFAULT FALSE'), ('created_by', 'INTEGER'),
        ('updated_at', 'TIMESTAMP DEFAULT NOW()'),
    ):
        cur.execute(f'ALTER TABLE grants ADD COLUMN IF NOT EXISTS {col} {typ}')
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_grants_slug "
                "ON grants(slug) WHERE slug IS NOT NULL")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_grants_source_id "
                "ON grants(source_id) WHERE source_id IS NOT NULL")
    # legacy → v2 backfill (idempotent: only touches unmigrated rows)
    cur.execute("UPDATE grants SET organization = provider "
                "WHERE organization IS NULL AND provider IS NOT NULL")
    cur.execute("UPDATE grants SET academic_levels = ARRAY[academic_level] "
                "WHERE (academic_levels IS NULL OR academic_levels = '{}') "
                "AND academic_level IS NOT NULL AND academic_level <> ''")
    cur.execute("UPDATE grants SET scientific_fields = "
                "string_to_array(replace(scientific_codes, ' ', ''), ',') "
                "WHERE (scientific_fields IS NULL OR scientific_fields = '{}') "
                "AND scientific_codes IS NOT NULL AND scientific_codes <> ''")
    cur.execute("UPDATE grants SET is_active = TRUE WHERE is_active IS NULL")
    cur.execute("UPDATE grants SET view_count = 0 WHERE view_count IS NULL")
    # slug backfill needs transliteration → Python side
    cur.execute("SELECT id, title FROM grants WHERE slug IS NULL")
    for gid, title in cur.fetchall():
        base_slug = generate_slug(title, gid)
        cur.execute("SELECT 1 FROM grants WHERE slug = %s AND id <> %s",
                    (base_slug, gid))
        slug = f'{base_slug}-{gid}' if cur.fetchone() else base_slug
        cur.execute("UPDATE grants SET slug = %s WHERE id = %s", (slug, gid))
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_grant_tracking (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            grant_id INTEGER NOT NULL REFERENCES grants(id) ON DELETE CASCADE,
            status VARCHAR(30) DEFAULT 'interested'
                CHECK (status IN ('interested', 'preparing', 'documents_ready',
                                  'applied', 'accepted', 'rejected')),
            notes TEXT,
            tracked_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, grant_id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_grant_tracking_user "
                "ON user_grant_tracking(user_id)")
    cur.execute("SELECT to_regclass('user_tracked_grants')")
    if cur.fetchone()[0]:
        cur.execute("""
            INSERT INTO user_grant_tracking (user_id, grant_id, status, tracked_at)
            SELECT user_id, grant_id,
                   CASE status WHEN 'in_progress' THEN 'preparing'
                               ELSE 'interested' END,
                   created_at
            FROM user_tracked_grants
            ON CONFLICT (user_id, grant_id) DO NOTHING
        """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS grant_success_stories (
            id SERIAL PRIMARY KEY,
            grant_id INTEGER NOT NULL REFERENCES grants(id) ON DELETE CASCADE,
            user_id INTEGER,
            year INTEGER,
            university_name VARCHAR(300),
            country VARCHAR(100),
            testimonial TEXT,
            is_anonymous BOOLEAN DEFAULT FALSE,
            is_approved BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_success_stories_grant "
                "ON grant_success_stories(grant_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_grants_deadline_active "
                "ON grants(application_deadline) WHERE is_active = TRUE")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_grants_country ON grants(country)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_grants_levels "
                "ON grants USING GIN(academic_levels)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_grants_fields "
                "ON grants USING GIN(scientific_fields)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_grants_tags ON grants USING GIN(tags)")
    # Fuzzy/kiril-lotin qidiruv uchun (utils.search_helper.build_search_clause)
    cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_trgm_grants_title "
                "ON grants USING gin (lower(title) gin_trgm_ops)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_trgm_grants_org "
                "ON grants USING gin (lower(COALESCE(organization, provider, '')) gin_trgm_ops)")
    _schema_ready = True


# ── row normalization ────────────────────────────────────────────────────────

_GRANT_COLS = ("id, title, title_uz, slug, COALESCE(organization, provider), "
               "country, country_flag, LOWER(COALESCE(funding_type, '')), "
               "academic_levels, academic_level, scientific_fields, "
               "application_deadline, start_date, stipend_amount, duration, "
               "language_requirements, application_url_x, cover_image_url, tags, "
               "view_count, is_active, is_featured, created_at")
# application_url: v2 spec column; legacy rows carry source_url. Read both.
_GRANT_COLS = _GRANT_COLS.replace('application_url_x', 'source_url')


def _card(row):
    """Row (in _GRANT_COLS order) → normalized card dict with legacy fallbacks."""
    (gid, title, title_uz, slug, organization, country, flag, funding,
     levels, level_legacy, fields, deadline, start, stipend, duration,
     lang, url, cover, tags, views, active, featured, created) = row
    levels = list(levels or []) or ([level_legacy] if level_legacy else [])
    days = calculate_days_remaining(deadline)
    return {
        'id': gid, 'title': title or '', 'title_uz': title_uz or '',
        'display_title': title_uz or title or '',
        'slug': slug or str(gid), 'organization': organization or '',
        'country': country or '', 'country_flag': flag or get_country_flag(country),
        'funding_type': funding if funding in FUNDING_TYPES else '',
        'funding_label': FUNDING_TYPES.get(funding, ''),
        'academic_levels': levels,
        'levels_uz': [LEVELS.get(l, l) for l in levels],
        'scientific_fields': list(fields or []),
        'deadline': str(deadline) if deadline else '',
        'deadline_uz': _uz_date(deadline) if deadline else '',
        'start_date': str(start) if start else '',
        'stipend_amount': stipend or '', 'duration': duration or '',
        'language_requirements': lang or '', 'application_url': url or '',
        'cover_image_url': cover or '', 'tags': list(tags or []),
        'view_count': views or 0, 'is_active': bool(active),
        'is_featured': bool(featured),
        'days_remaining': days,
        'expired': days is not None and days < 0,
        'created_at': str(created)[:10] if created else '',
    }


def _query_grants(cur, args, user_id=None):
    """Shared filter/sort/paginate logic for /grants and /api/grants."""
    where, params = ['is_active IS NOT FALSE'], []
    joins = ''
    if args.get('my') and user_id:
        joins = 'JOIN user_grant_tracking ugt ON ugt.grant_id = grants.id'
        where.append('ugt.user_id = %s')
        params.append(user_id)
    if not args.get('show_expired') and not args.get('my'):
        where.append('(application_deadline IS NULL OR application_deadline >= CURRENT_DATE)')
    countries = [c for c in (args.getlist('country') if hasattr(args, 'getlist')
                             else args.get('country', [])) if c]
    if countries:
        where.append('country = ANY(%s)')
        params.append(countries)
    funding = (args.get('funding') or '').lower()
    if funding in FUNDING_TYPES:
        where.append('LOWER(COALESCE(funding_type, %s)) = %s')
        params.extend(['', funding])
    level = args.get('level') or ''
    if level in LEVELS:
        where.append('(academic_levels && %s OR academic_level = %s)')
        params.extend([[level], level])
    field = (args.get('field') or '').strip()
    if field:
        where.append("(EXISTS (SELECT 1 FROM unnest(scientific_fields) f "
                     "WHERE f = %s OR f LIKE %s) OR scientific_codes ILIKE %s)")
        params.extend([field, field + '%', f'%{field}%'])
    q = (args.get('search') or args.get('q') or '').strip()
    if q:
        # Kiril<->lotin + pg_trgm fuzzy (idx_trgm_grants_title/_org — grants.py _ensure_schema)
        search_where, search_params, _order, _order_params = build_search_clause(
            q, ['title', 'title_uz', 'COALESCE(organization, provider)', 'country'])
        where.append(search_where)
        params.extend(search_params)
    if args.get('featured'):
        where.append('is_featured = TRUE')

    sort = args.get('sort') or 'deadline'
    order = {
        'deadline': 'application_deadline ASC NULLS LAST, id DESC',
        'new': 'created_at DESC, id DESC',
        'popular': 'view_count DESC, id DESC',
        'stipend': 'stipend_amount DESC NULLS LAST, id DESC',
    }.get(sort if sort in SORTS else 'deadline')

    w = ' AND '.join(where)
    cur.execute(f'SELECT COUNT(*) FROM grants {joins} WHERE {w}', params)
    total = cur.fetchone()[0] or 0
    page = max(1, args.get('page', 1, type=int) if hasattr(args, 'get') else 1)
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = min(page, pages)
    cur.execute(f"""
        SELECT {_GRANT_COLS} FROM grants {joins}
        WHERE {w} ORDER BY is_featured DESC, {order}
        LIMIT %s OFFSET %s
    """, params + [PER_PAGE, (page - 1) * PER_PAGE])
    cards = [_card(r) for r in cur.fetchall()]
    return cards, total, page, pages


def _tracking_map(cur, user_id, grant_ids):
    if not user_id or not grant_ids:
        return {}
    cur.execute("SELECT grant_id, status FROM user_grant_tracking "
                "WHERE user_id = %s AND grant_id = ANY(%s)", (user_id, grant_ids))
    return {r[0]: r[1] for r in cur.fetchall()}


def _user_targets(cur):
    """Academic levels + specialization for personalized recommendations.

    Profile attributes live on cabinet_users/olim_profiles (NOT on users):
    resolve the visitor through the cabinet e-mail bridge like reminders do."""
    uid = session.get('cabinet_user_id')
    email = ''
    if not uid and getattr(current_user, 'is_authenticated', False):
        email = (getattr(current_user, 'email', '') or '').strip().lower()
    if not uid and not email:
        return None
    try:
        if uid:
            cur.execute("""
                SELECT p.academic_degree, p.ixtisoslik FROM olim_profiles p
                WHERE p.cabinet_user_id = %s ORDER BY p.id DESC LIMIT 1
            """, (uid,))
        else:
            cur.execute("""
                SELECT p.academic_degree, p.ixtisoslik
                FROM cabinet_users cu
                JOIN olim_profiles p ON p.cabinet_user_id = cu.id
                WHERE LOWER(cu.email) = %s ORDER BY p.id DESC LIMIT 1
            """, (email,))
        r = cur.fetchone()
    except Exception:
        return None
    if not r or not (r[0] or r[1]):
        return None
    degree = (r[0] or '').lower()
    levels = []
    if 'magistrant' in degree or degree == 'magistr':
        levels = ['Master']
    elif 'phd' in degree:
        levels = ['PhD', 'Master']
    elif 'dsc' in degree:
        levels = ['Postdoc', 'Research', 'PhD']
    return {'levels': levels, 'field': (r[1] or '').strip()}


def _recommended(cur, targets, limit=6):
    where = ["is_active IS NOT FALSE",
             "(application_deadline IS NULL OR application_deadline >= CURRENT_DATE)"]
    params = []
    ors = []
    if targets.get('levels'):
        ors.append('academic_levels && %s')
        params.append(targets['levels'])
    if targets.get('field'):
        ors.append("EXISTS (SELECT 1 FROM unnest(scientific_fields) f WHERE f LIKE %s)")
        params.append(targets['field'].split('.')[0] + '%')
    if not ors:
        return []
    where.append('(' + ' OR '.join(ors) + ')')
    cur.execute(f"""
        SELECT {_GRANT_COLS} FROM grants WHERE {' AND '.join(where)}
        ORDER BY is_featured DESC, application_deadline ASC NULLS LAST
        LIMIT %s
    """, params + [limit])
    return [_card(r) for r in cur.fetchall()]


# ── Pages ────────────────────────────────────────────────────────────────────

@grants_bp.route('/grants')
def grants_list():
    from data import get_connection
    my = request.args.get('my') == '1'
    if my and not getattr(current_user, 'is_authenticated', False):
        return redirect('/login')
    ctx = {'grants': [], 'total': 0, 'page': 1, 'pages': 1, 'countries': [],
           'featured': [], 'recommended': None, 'has_profile': True,
           'stats': {'active': 0, 'countries': 0, 'tracked': 0}, 'my': my,
           'tracking': {}, 'my_grants': []}
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                uid = current_user.id if getattr(current_user, 'is_authenticated', False) else None
                cards, total, page, pages = _query_grants(cur, request.args, uid)
                ctx.update(grants=cards, total=total, page=page, pages=pages)
                cur.execute("SELECT DISTINCT country FROM grants WHERE is_active IS NOT FALSE "
                            "AND country IS NOT NULL AND country <> '' ORDER BY country")
                ctx['countries'] = [{'name': r[0], 'flag': get_country_flag(r[0])}
                                    for r in cur.fetchall()]
                cur.execute("SELECT COUNT(*), COUNT(DISTINCT country) FROM grants "
                            "WHERE is_active IS NOT FALSE AND "
                            "(application_deadline IS NULL OR application_deadline >= CURRENT_DATE)")
                r = cur.fetchone()
                ctx['stats'] = {'active': r[0] or 0, 'countries': r[1] or 0, 'tracked': 0}
                cur.execute("SELECT COUNT(*) FROM user_grant_tracking")
                ctx['stats']['tracked'] = cur.fetchone()[0] or 0
                cur.execute(f"""
                    SELECT {_GRANT_COLS} FROM grants
                    WHERE is_featured = TRUE AND is_active IS NOT FALSE
                      AND (application_deadline IS NULL OR application_deadline >= CURRENT_DATE)
                    ORDER BY application_deadline ASC NULLS LAST LIMIT 8
                """)
                ctx['featured'] = [_card(r) for r in cur.fetchall()]
                if uid:
                    ctx['tracking'] = _tracking_map(
                        cur, uid, [g['id'] for g in ctx['grants'] + ctx['featured']])
                    targets = _user_targets(cur)
                    if targets:
                        ctx['recommended'] = _recommended(cur, targets)
                    else:
                        ctx['has_profile'] = False
                if my and uid:
                    cur.execute(f"""
                        SELECT {_GRANT_COLS}, ugt.status, ugt.notes
                        FROM grants JOIN user_grant_tracking ugt ON ugt.grant_id = grants.id
                        WHERE ugt.user_id = %s
                        ORDER BY application_deadline ASC NULLS LAST
                    """, (uid,))
                    for row in cur.fetchall():
                        c = _card(row[:-2])
                        c['track_status'] = row[-2]
                        c['notes'] = row[-1] or ''
                        ctx['my_grants'].append(c)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass
    return render_template('grants.html',
                           funding_types=FUNDING_TYPES, levels=LEVELS,
                           status_labels=STATUS_LABELS, sorts=SORTS, **ctx)


def _render_detail(cur, grant_row):
    g = _card(grant_row[:23])
    (description, requirements, benefits, docs, tips, req_json) = grant_row[23:]
    if isinstance(docs, str):
        try:
            docs = json.loads(docs)
        except Exception:
            docs = []
    docs = docs or []
    # legacy requirements_json {"documents": [...], "strategy": [...]} fallback
    legacy = req_json if isinstance(req_json, dict) else {}
    if not docs and legacy.get('documents'):
        docs = [{'name': d, 'required': True, 'tip': '', 'template_url': ''}
                if isinstance(d, str) else d for d in legacy['documents']]
    if not tips and legacy.get('strategy'):
        strat = legacy['strategy']
        tips = '\n'.join(strat) if isinstance(strat, list) else str(strat)
    g.update(description=description or '', requirements=requirements or '',
             benefits=benefits or '', documents_checklist=docs,
             application_tips=tips or '')
    return g


_DETAIL_EXTRA = ("description, requirements, benefits, documents_checklist, "
                 "application_tips, requirements_json")


def _detail_response(cur, g):
    uid = current_user.id if getattr(current_user, 'is_authenticated', False) else None
    tracking = None
    if uid:
        cur.execute("SELECT status, notes FROM user_grant_tracking "
                    "WHERE user_id = %s AND grant_id = %s", (uid, g['id']))
        r = cur.fetchone()
        if r:
            tracking = {'status': r[0], 'notes': r[1] or ''}
    cur.execute("""
        SELECT year, university_name, country, testimonial, is_anonymous
        FROM grant_success_stories
        WHERE grant_id = %s AND is_approved = TRUE
        ORDER BY year DESC NULLS LAST, id DESC LIMIT 20
    """, (g['id'],))
    stories = [{'year': r[0], 'university': r[1] or '', 'country': r[2] or '',
                'testimonial': r[3] or '', 'anonymous': r[4]} for r in cur.fetchall()]
    cur.execute(f"""
        SELECT {_GRANT_COLS} FROM grants
        WHERE id <> %s AND is_active IS NOT FALSE
          AND (application_deadline IS NULL OR application_deadline >= CURRENT_DATE)
          AND (country = %s OR academic_levels && %s)
        ORDER BY application_deadline ASC NULLS LAST LIMIT 3
    """, (g['id'], g['country'], g['academic_levels'] or ['—']))
    related = [_card(r) for r in cur.fetchall()]
    return render_template('grant_detail.html', g=g, tracking=tracking,
                           stories=stories, related=related,
                           status_labels=STATUS_LABELS, levels=LEVELS)


@grants_bp.route('/grants/<int:id>')
def grant_detail(id):
    """Legacy id URL (base.html modal links here) → canonical slug URL."""
    from data import get_connection
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute("SELECT slug FROM grants WHERE id = %s", (id,))
            r = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    if not r:
        abort(404)
    return redirect(f'/grants/{r[0]}' if r[0] else '/grants', code=302)


@grants_bp.route('/grants/<slug>')
def grant_detail_slug(slug):
    from data import get_connection
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute("UPDATE grants SET view_count = COALESCE(view_count, 0) + 1 "
                        "WHERE slug = %s", (slug,))
            cur.execute(f"SELECT {_GRANT_COLS}, {_DETAIL_EXTRA} FROM grants "
                        f"WHERE slug = %s", (slug,))
            row = cur.fetchone()
            if not row:
                conn.commit()
                abort(404)
            g = _render_detail(cur, row)
            resp = _detail_response(cur, g)
        conn.commit()
    finally:
        conn.close()
    return resp


# ── JSON APIs ────────────────────────────────────────────────────────────────

@grants_bp.route('/api/grants')
def api_grants_v2():
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                uid = current_user.id if getattr(current_user, 'is_authenticated', False) else None
                cards, total, page, pages = _query_grants(cur, request.args, uid)
                tracking = _tracking_map(cur, uid, [c['id'] for c in cards])
                for c in cards:
                    c['track_status'] = tracking.get(c['id'])
            conn.commit()
        finally:
            conn.close()
        return jsonify({'grants': cards, 'total': total, 'page': page,
                        'pages': pages, 'has_next': page < pages})
    except Exception as e:
        return jsonify({'grants': [], 'total': 0, 'error': str(e)}), 500


@grants_bp.route('/api/grants/<int:grant_id>/track', methods=['POST'])
@csrf.exempt
@login_required
def api_track(grant_id):
    from data import get_connection
    data = request.get_json(silent=True) or {}
    untrack = data.get('action') == 'untrack'
    status = data.get('status') or 'interested'
    if not untrack and status not in TRACK_STATUSES:
        return jsonify({'success': False, 'error': 'invalid status'}), 400
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                if untrack:
                    cur.execute("DELETE FROM user_grant_tracking "
                                "WHERE user_id = %s AND grant_id = %s",
                                (current_user.id, grant_id))
                else:
                    cur.execute("""
                        INSERT INTO user_grant_tracking (user_id, grant_id, status)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (user_id, grant_id)
                        DO UPDATE SET status = EXCLUDED.status, updated_at = NOW()
                    """, (current_user.id, grant_id, status))
            conn.commit()
        finally:
            conn.close()
        return jsonify({'success': True,
                        'status': None if untrack else status,
                        'tracked': not untrack})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@grants_bp.route('/api/grants/<int:grant_id>/update-status', methods=['POST'])
@csrf.exempt
@login_required
def api_update_status(grant_id):
    from data import get_connection
    data = request.get_json(silent=True) or {}
    status = data.get('status')
    notes = data.get('notes')
    if status is not None and status not in TRACK_STATUSES:
        return jsonify({'success': False, 'error': 'invalid status'}), 400
    if status is None and notes is None:
        return jsonify({'success': False, 'error': 'nothing to update'}), 400
    sets, params = [], []
    if status is not None:
        sets.append('status = %s')
        params.append(status)
    if notes is not None:
        sets.append('notes = %s')
        params.append(str(notes)[:2000])
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute(f"UPDATE user_grant_tracking SET {', '.join(sets)}, "
                            f"updated_at = NOW() WHERE user_id = %s AND grant_id = %s",
                            params + [current_user.id, grant_id])
                updated = cur.rowcount
            conn.commit()
        finally:
            conn.close()
        if not updated:
            return jsonify({'success': False, 'error': 'not tracked'}), 404
        return jsonify({'success': True, 'status': status})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@grants_bp.route('/api/grants/my-tracked')
@login_required
def api_my_tracked():
    from data import get_connection
    items = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute(f"""
                    SELECT {_GRANT_COLS}, ugt.status, ugt.notes
                    FROM grants JOIN user_grant_tracking ugt ON ugt.grant_id = grants.id
                    WHERE ugt.user_id = %s
                    ORDER BY application_deadline ASC NULLS LAST
                """, (current_user.id,))
                for row in cur.fetchall():
                    c = _card(row[:-2])
                    c['track_status'] = row[-2]
                    c['notes'] = row[-1] or ''
                    items.append(c)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        items = []
    return jsonify({'success': True, 'grants': items})


@grants_bp.route('/api/grants/deadline-alerts')
@login_required
def api_deadline_alerts():
    from data import get_connection
    items = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute("""
                    SELECT g.id, g.slug, COALESCE(g.title_uz, g.title),
                           (g.application_deadline - CURRENT_DATE), ugt.status
                    FROM user_grant_tracking ugt
                    JOIN grants g ON g.id = ugt.grant_id
                    WHERE ugt.user_id = %s
                      AND g.is_active IS NOT FALSE
                      AND ugt.status NOT IN ('applied', 'accepted', 'rejected')
                      AND g.application_deadline IS NOT NULL
                      AND g.application_deadline >= CURRENT_DATE
                      AND g.application_deadline <= CURRENT_DATE + 7
                    ORDER BY g.application_deadline ASC
                """, (current_user.id,))
                items = [{'id': r[0], 'slug': r[1] or str(r[0]), 'title': r[2],
                          'days_remaining': int(r[3]),
                          'status': r[4]} for r in cur.fetchall()]
            conn.commit()
        finally:
            conn.close()
    except Exception:
        items = []
    return jsonify({'success': True, 'alerts': items})


# ── Legacy API compatibility (old consumers keep working) ────────────────────

@grants_bp.route('/api/v1/grants')
def api_grants_legacy():
    from data import get_connection
    items = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cards, total, _, _ = _query_grants(cur, request.args)
                items = cards
            conn.commit()
        finally:
            conn.close()
    except Exception:
        items = []
    # old shape keys kept alongside the new ones
    for c in items:
        c['application_deadline'] = c['deadline']
        c['provider'] = c['organization']
        c['academic_level'] = (c['academic_levels'] or [''])[0]
        c['codes'] = c['scientific_fields']
    return jsonify({'ok': True, 'grants': items, 'count': len(items)})


@grants_bp.route('/api/v1/grants/track', methods=['POST'])
@csrf.exempt
@login_required
def track_grant_legacy():
    data = request.get_json(silent=True) or {}
    try:
        gid = int(data.get('grant_id'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'invalid grant_id'}), 400
    status = data.get('status')
    mapped = {'interested': 'interested', 'in_progress': 'preparing'}.get(status)
    if status == 'remove':
        request_json = {'action': 'untrack'}
    elif mapped:
        request_json = {'status': mapped}
    else:
        return jsonify({'ok': False, 'error': 'invalid status'}), 400
    # delegate to the v2 handler logic
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                if request_json.get('action') == 'untrack':
                    cur.execute("DELETE FROM user_grant_tracking "
                                "WHERE user_id = %s AND grant_id = %s",
                                (current_user.id, gid))
                else:
                    cur.execute("""
                        INSERT INTO user_grant_tracking (user_id, grant_id, status)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (user_id, grant_id)
                        DO UPDATE SET status = EXCLUDED.status, updated_at = NOW()
                    """, (current_user.id, gid, request_json['status']))
            conn.commit()
        finally:
            conn.close()
        return jsonify({'ok': True, 'status': status})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@grants_bp.route('/api/v1/grants/reminders')
@login_required
def grant_reminders():
    """7-day deadline feed for the global session modal in base.html."""
    from data import get_connection
    items = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute("""
                    SELECT g.id, COALESCE(g.title_uz, g.title),
                           (g.application_deadline - CURRENT_DATE)
                    FROM user_grant_tracking t
                    JOIN grants g ON g.id = t.grant_id
                    WHERE t.user_id = %s
                      AND g.is_active IS NOT FALSE
                      AND t.status NOT IN ('applied', 'accepted', 'rejected')
                      AND g.application_deadline IS NOT NULL
                      AND g.application_deadline >= CURRENT_DATE
                      AND g.application_deadline <= CURRENT_DATE + 7
                    ORDER BY g.application_deadline ASC
                """, (current_user.id,))
                for gid, title, days in cur.fetchall():
                    d = int(days)
                    items.append({
                        'id': gid, 'title': title, 'days_left': d,
                        'message': (f'“{title}” granti arizasiga '
                                    f'{d} kun qoldi. Hujjatlaringizni tekshiring.'),
                    })
            conn.commit()
        finally:
            conn.close()
    except Exception:
        items = []
    return jsonify({'ok': True, 'reminders': items})


# ── Admin ────────────────────────────────────────────────────────────────────

def _form_render_view(v):
    """Form values dict → the template's expected keys (used on error re-render)."""
    out = dict(v)
    out['deadline'] = v.get('application_deadline') or ''
    out['application_url'] = v.get('source_url') or ''
    try:
        out['documents_checklist'] = json.loads(v.get('documents_checklist') or '[]')
    except Exception:
        out['documents_checklist'] = []
    return out


def _admin_form_values():
    g = lambda k: (request.form.get(k) or '').strip()
    levels = [l for l in request.form.getlist('academic_levels') if l in LEVELS]
    funding = g('funding_type').lower()
    docs = []
    names = request.form.getlist('doc_name')
    reqs = request.form.getlist('doc_required')
    tips = request.form.getlist('doc_tip')
    tmpls = request.form.getlist('doc_template')
    for i, name in enumerate(names):
        name = (name or '').strip()
        if not name:
            continue
        docs.append({
            'name': name,
            'required': (reqs[i] if i < len(reqs) else '1') == '1',
            'tip': (tips[i] if i < len(tips) else '').strip(),
            'template_url': (tmpls[i] if i < len(tmpls) else '').strip(),
        })
    split = lambda s: [x.strip() for x in s.split(',') if x.strip()]
    country = g('country')
    return {
        'title': g('title'), 'title_uz': g('title_uz') or None,
        'slug': g('slug') or None,
        'organization': g('organization') or None,
        'country': country or None,
        'country_flag': g('country_flag') or get_country_flag(country),
        'funding_type': funding if funding in FUNDING_TYPES else None,
        'academic_levels': levels,
        'scientific_fields': split(g('scientific_fields')),
        'description': g('description') or None,
        'requirements': g('requirements') or None,
        'benefits': g('benefits') or None,
        'documents_checklist': json.dumps(docs, ensure_ascii=False),
        'application_tips': g('application_tips') or None,
        'application_deadline': g('deadline') or None,
        'start_date': g('start_date') or None,
        'stipend_amount': g('stipend_amount') or None,
        'duration': g('duration') or None,
        'language_requirements': g('language_requirements') or None,
        'source_url': g('application_url') or None,
        'cover_image_url': g('cover_image_url') or None,
        'tags': split(g('tags')),
        'is_featured': request.form.get('is_featured') == 'on',
        'is_active': request.form.get('is_active', 'on') == 'on',
        # legacy mirrors so the scraper/old consumers stay coherent
        'academic_level': levels[0] if levels else None,
        'scientific_codes': ','.join(split(g('scientific_fields'))) or None,
        'provider': g('organization') or None,
    }


_ADMIN_COLS = ('title', 'title_uz', 'slug', 'organization', 'country',
               'country_flag', 'funding_type', 'academic_levels',
               'scientific_fields', 'description', 'requirements', 'benefits',
               'documents_checklist', 'application_tips', 'application_deadline',
               'start_date', 'stipend_amount', 'duration',
               'language_requirements', 'source_url', 'cover_image_url', 'tags',
               'is_featured', 'is_active', 'academic_level', 'scientific_codes',
               'provider')


def _load_admin_grant(cur, id):
    cur.execute(f"SELECT {_GRANT_COLS}, {_DETAIL_EXTRA} FROM grants WHERE id = %s", (id,))
    row = cur.fetchone()
    return _render_detail(cur, row) if row else None


@grants_bp.route('/admin/grants')
@login_required
def admin_grants():
    from app import _require_admin
    _require_admin()
    from data import get_connection
    flt = request.args.get('f') or 'all'
    items, stats, top = [], {}, []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                where = 'TRUE'
                if flt == 'active':
                    where = ("is_active IS NOT FALSE AND (application_deadline IS NULL "
                             "OR application_deadline >= CURRENT_DATE)")
                elif flt == 'expired':
                    where = "application_deadline < CURRENT_DATE"
                elif flt == 'inactive':
                    where = "is_active = FALSE"
                cur.execute(f"""
                    SELECT {_GRANT_COLS} FROM grants WHERE {where}
                    ORDER BY is_active DESC, application_deadline ASC NULLS LAST, id DESC
                """)
                items = [_card(r) for r in cur.fetchall()]
                cur.execute("SELECT COUNT(*) FROM grants WHERE is_active IS NOT FALSE")
                stats['active'] = cur.fetchone()[0] or 0
                cur.execute("SELECT COUNT(*) FROM user_grant_tracking")
                stats['tracked'] = cur.fetchone()[0] or 0
                cur.execute("SELECT status, COUNT(*) FROM user_grant_tracking GROUP BY status")
                stats['by_status'] = {r[0]: r[1] for r in cur.fetchall()}
                cur.execute("""
                    SELECT COALESCE(title_uz, title), view_count FROM grants
                    ORDER BY view_count DESC NULLS LAST LIMIT 5
                """)
                top = [{'title': r[0], 'views': r[1] or 0} for r in cur.fetchall()]
            conn.commit()
        finally:
            conn.close()
    except Exception:
        items = []
    return render_template('admin/grants.html', items=items, stats=stats,
                           top=top, flt=flt, status_labels=STATUS_LABELS)


@grants_bp.route('/admin/grants/add', methods=['GET', 'POST'])
@login_required
def admin_grants_add():
    from app import _require_admin
    _require_admin()
    from data import get_connection
    if request.method == 'POST':
        v = _admin_form_values()
        if not v['title']:
            flash('Sarlavha kiritilishi shart.', 'error')
            return render_template('admin/grant_form.html', item=_form_render_view(v),
                                   edit_mode=False,
                                   funding_types=FUNDING_TYPES, levels=LEVELS)
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    _ensure_schema(cur)
                    if not v['slug']:
                        v['slug'] = generate_slug(v['title'])
                    cur.execute("SELECT 1 FROM grants WHERE slug = %s", (v['slug'],))
                    if cur.fetchone():
                        v['slug'] = f"{v['slug']}-{int(datetime.now().timestamp()) % 10000}"
                    cols = list(_ADMIN_COLS) + ['created_by']
                    ph = ', '.join(['%s'] * len(cols))
                    cur.execute(
                        f"INSERT INTO grants ({', '.join(cols)}) VALUES ({ph})",
                        [v[c] for c in _ADMIN_COLS] + [current_user.id])
                conn.commit()
            finally:
                conn.close()
            flash("Grant qo'shildi!", 'success')
            return redirect('/admin/grants')
        except Exception as e:
            flash('Xatolik: ' + str(e), 'error')
            return render_template('admin/grant_form.html', item=_form_render_view(v),
                                   edit_mode=False,
                                   funding_types=FUNDING_TYPES, levels=LEVELS)
    return render_template('admin/grant_form.html', item=None, edit_mode=False,
                           funding_types=FUNDING_TYPES, levels=LEVELS)


@grants_bp.route('/admin/grants/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def admin_grants_edit(id):
    from app import _require_admin
    _require_admin()
    from data import get_connection
    if request.method == 'POST':
        v = _admin_form_values()
        if not v['title']:
            flash('Sarlavha kiritilishi shart.', 'error')
            return redirect(f'/admin/grants/edit/{id}')
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    _ensure_schema(cur)
                    if not v['slug']:
                        v['slug'] = generate_slug(v['title'], id)
                    cur.execute("SELECT 1 FROM grants WHERE slug = %s AND id <> %s",
                                (v['slug'], id))
                    if cur.fetchone():
                        v['slug'] = f"{v['slug']}-{id}"
                    sets = ', '.join(f'{c} = %s' for c in _ADMIN_COLS)
                    cur.execute(f"UPDATE grants SET {sets}, updated_at = NOW() "
                                f"WHERE id = %s", [v[c] for c in _ADMIN_COLS] + [id])
                conn.commit()
            finally:
                conn.close()
            flash('Grant yangilandi.', 'success')
            return redirect('/admin/grants')
        except Exception as e:
            flash('Xatolik: ' + str(e), 'error')
            return redirect(f'/admin/grants/edit/{id}')
    item = None
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                item = _load_admin_grant(cur, id)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        item = None
    if not item:
        abort(404)
    return render_template('admin/grant_form.html', item=item, edit_mode=True,
                           funding_types=FUNDING_TYPES, levels=LEVELS)


@grants_bp.route('/admin/grants/delete/<int:id>', methods=['POST'])
@login_required
def admin_grants_delete(id):
    """Soft delete — sets is_active = FALSE (tracking history preserved)."""
    from app import _require_admin
    _require_admin()
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute("UPDATE grants SET is_active = FALSE, updated_at = NOW() "
                            "WHERE id = %s", (id,))
            conn.commit()
        finally:
            conn.close()
        flash("Grant o'chirildi (nofaol qilindi).", 'success')
    except Exception as e:
        flash('Xatolik: ' + str(e), 'error')
    return redirect('/admin/grants')


@grants_bp.route('/admin/grants/toggle-featured/<int:id>', methods=['POST'])
@login_required
def admin_grants_toggle_featured(id):
    from app import _require_admin
    _require_admin()
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute("UPDATE grants SET is_featured = NOT COALESCE(is_featured, FALSE), "
                            "updated_at = NOW() WHERE id = %s RETURNING is_featured", (id,))
                r = cur.fetchone()
            conn.commit()
        finally:
            conn.close()
        flash('Featured: ' + ('yoqildi ⭐' if r and r[0] else "o'chirildi"), 'success')
    except Exception as e:
        flash('Xatolik: ' + str(e), 'error')
    return redirect('/admin/grants')


@grants_bp.route('/admin/grants/seed', methods=['POST'])
@login_required
def admin_grants_seed():
    """One-click seed: upserts the curated starter grants (scripts/seed_grants.py).
    ON CONFLICT (source_id) DO NOTHING — safe to press repeatedly."""
    from app import _require_admin
    _require_admin()
    from data import get_connection
    from scripts.seed_grants import SEED_GRANTS, upsert_seed
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                added = upsert_seed(cur, SEED_GRANTS)
            conn.commit()
        finally:
            conn.close()
        flash(f'Seed: {added} ta yangi grant qo\'shildi '
              f'({len(SEED_GRANTS)} tadan).', 'success')
    except Exception as e:
        flash('Xatolik: ' + str(e), 'error')
    return redirect('/admin/grants')
