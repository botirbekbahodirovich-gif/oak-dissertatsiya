"""Konferensiyalar katalogi (conferences_bp) — mahalliy + xalqaro.

Ikkita katalog: mahalliy (vazirlik yillik rejasi, scripts/scrape_conferences_local.py)
va xalqaro (confs.tech ochiq datasetlari, scripts/scrape_conferences_intl.py).
Statik ro'yxatdan farqi — sayt tizimlariga integratsiya:

  * bookmark (user_conference_bookmarks — dissertatsiya user_bookmarks UX'i
    aynan takrorlanadi, lekin FK conferences ga; jadval alohida, chunki
    user_bookmarks.dissertation_id NOT NULL FK va polimorf emas);
  * "Roadmap'ga qo'shish" — mavjud roadmap_conferences jadvaliga bitta bosishda
    yozadi (source_conference_id bilan; qo'lda kiritish o'zgarishsiz ishlaydi);
  * ixtisoslik obunasi — yangi konferensiyalar mos obunachilarga user_alerts +
    Telegram orqali boradi (subscriptions.notify_specialty_subscribers naqshi,
    dedup conference_notifications_log da).

Sxema lazy + idempotent (_ensure_schema) — migrations/add_conferences.sql bilan
bir xil, server birinchi so'rovda self-migrate (grants/reminders konvensiyasi).

Routes:
  GET  /konferensiyalar                — hub: 2 katta karta (jonli sonlar bilan)
  GET  /konferensiyalar/mahalliy       — mahalliy katalog
  GET  /konferensiyalar/xalqaro        — xalqaro katalog
  GET  /konferensiya/<slug>            — detail sahifa
  GET  /api/v1/conferences             — filtrlanadigan JSON (facet countlar bilan)
  POST /api/v1/conferences/bookmark    — ⭐ toggle {conference_id}
  POST /api/v1/conferences/roadmap     — Roadmap'ga qo'shish {conference_id}
  POST /api/v1/conferences/dispatch-alerts — yangi konf. obunachilarga (API key,
                                         GitHub Actions scraperdan keyin chaqiradi)
  /admin/conferences*                  — admin CRUD (admin/grants naqshi)
"""
import os
import re
from datetime import date, datetime

from flask import (Blueprint, jsonify, request, render_template,
                   redirect, abort, flash)
from flask_login import login_required, current_user

from app import csrf
from data import get_connection

try:
    import psycopg2.extras as psycopg2_extras
except Exception:  # pragma: no cover — psycopg2 always present in prod
    psycopg2_extras = None

conferences_bp = Blueprint('conferences', __name__)

_schema_ready = False

PER_PAGE = 12
SIMILAR_LIMIT = 4

SCOPES = ('local', 'international')
FORMATS = {'onsite': 'Anʼanaviy', 'online': 'Onlayn', 'hybrid': 'Gibrid'}
EVENT_TYPES_LOCAL = ('Anjuman', 'Forum', 'Kongress', 'Seminar', 'Simpozium',
                     'Konferensiya')
MONTHS_UZ = ['Yanvar', 'Fevral', 'Mart', 'Aprel', 'May', 'Iyun', 'Iyul',
             'Avgust', 'Sentabr', 'Oktabr', 'Noyabr', 'Dekabr']

# Obuna moslashuvi: ixtisoslik_nomi tokenlari konf. nomi/sohasida qidiriladi.
# Umumiy so'zlar chiqarib tashlanadi — mo'rt moslikni majburlamaymiz (spec).
_MATCH_STOP = {'fanlari', 'fanlar', 'boyicha', "bo'yicha", 'sohasi', 'hamda',
               'умумий', 'фанлари', 'фанлар', 'соҳаси'}
_TOKEN_RE = re.compile(r"[^\W\d_]{6,}", re.UNICODE)


# ── helpers ──────────────────────────────────────────────────────────────────

def _days_until(d):
    """Bugungacha qolgan kunlar (o'tgan bo'lsa manfiy); None — sana yo'q."""
    if not d:
        return None
    if isinstance(d, str):
        try:
            d = date.fromisoformat(d[:10])
        except ValueError:
            return None
    if isinstance(d, datetime):
        d = d.date()
    return (d - date.today()).days


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


def make_slug(title, cur=None):
    """URL slug (kirill→lotin); to'qnashuvda -2, -3… (cur berilsa DB tekshiradi).
    Hech qachon faqat raqam bo'lmaydi."""
    from institutions import transliterate
    s = transliterate((title or '').lower())
    for ch in "'ʻʼ‘’`":
        s = s.replace(ch, '')
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')[:200].strip('-')
    if not s or s.replace('-', '').isdigit():
        s = f'konf-{s}'.strip('-')
    if cur is None:
        return s
    base, n = s, 2
    while True:
        cur.execute("SELECT 1 FROM conferences WHERE title_slug = %s", (s,))
        if not cur.fetchone():
            return s
        s = f'{base}-{n}'[:250]
        n += 1


def _parse_date(v):
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


# ── schema (lazy, idempotent — mirrors migrations/add_conferences.sql) ──────

def _ensure_schema(cur):
    global _schema_ready
    if _schema_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conferences (
            id SERIAL PRIMARY KEY,
            title VARCHAR(600) NOT NULL,
            title_slug VARCHAR(250) UNIQUE,
            scope VARCHAR(10) NOT NULL CHECK (scope IN ('local','international')),
            organizer VARCHAR(400),
            field VARCHAR(200),
            region VARCHAR(100),
            city VARCHAR(150),
            event_type VARCHAR(50),
            start_date DATE,
            end_date DATE,
            is_multiday BOOLEAN DEFAULT FALSE,
            format VARCHAR(20),
            publisher VARCHAR(200),
            is_scopus_indexed BOOLEAN DEFAULT FALSE,
            submission_deadline DATE,
            country VARCHAR(100),
            source_url VARCHAR(500),
            source_id VARCHAR(300) UNIQUE,
            description TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_conf_scope_date "
                "ON conferences(scope, start_date) WHERE is_active = TRUE")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_conf_field ON conferences(field)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_conf_region "
                "ON conferences(region) WHERE scope = 'local'")
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_conf_title_trgm "
                    "ON conferences USING GIN (title gin_trgm_ops)")
    except Exception:
        pass  # pg_trgm yo'q muhitda ILIKE indekssiz ishlayveradi
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_conference_bookmarks (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            conference_id INTEGER NOT NULL REFERENCES conferences(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, conference_id)
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_conf_bm_user "
                "ON user_conference_bookmarks(user_id, created_at DESC)")
    # obuna dedup logi (specialty_notifications_log aksi)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conference_notifications_log (
            id SERIAL PRIMARY KEY,
            subscription_id INTEGER REFERENCES specialty_subscriptions(id) ON DELETE CASCADE,
            conference_id INTEGER REFERENCES conferences(id) ON DELETE CASCADE,
            sent_via VARCHAR(20) DEFAULT 'site',
            sent_at TIMESTAMP DEFAULT NOW()
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_conf_notif_log "
                "ON conference_notifications_log(subscription_id, conference_id)")
    # Roadmap integratsiyasi: katalogdan qo'shilgan yozuvlarni belgilash.
    # Qo'lda kiritilganlar uchun NULL — mavjud xatti-harakat o'zgarmaydi.
    cur.execute("ALTER TABLE IF EXISTS roadmap_conferences "
                "ADD COLUMN IF NOT EXISTS source_conference_id INTEGER")
    _schema_ready = True


_CONF_COLS = ('id', 'title', 'title_slug', 'scope', 'organizer', 'field',
              'region', 'city', 'event_type', 'start_date', 'end_date',
              'is_multiday', 'format', 'publisher', 'is_scopus_indexed',
              'submission_deadline', 'country', 'source_url', 'description',
              'is_active')


def _card(r):
    """DB qatori (RealDict) → JSON/template item."""
    start, end = r.get('start_date'), r.get('end_date')
    sub = r.get('submission_deadline')
    days = _days_until(start)
    item = {
        'id': r['id'],
        'title': r.get('title') or '',
        'slug': r.get('title_slug') or '',
        'scope': r.get('scope'),
        'organizer': r.get('organizer') or '',
        'field': r.get('field') or '',
        'region': r.get('region') or '',
        'city': r.get('city') or '',
        'country': r.get('country') or '',
        'event_type': r.get('event_type') or '',
        'start_date': start.isoformat() if start else None,
        'end_date': end.isoformat() if end else None,
        'start_uz': _uz_date(start),
        'end_uz': _uz_date(end),
        'is_multiday': bool(r.get('is_multiday')),
        'format': r.get('format') or '',
        'format_uz': FORMATS.get(r.get('format') or '', ''),
        'publisher': r.get('publisher') or '',
        'is_scopus_indexed': bool(r.get('is_scopus_indexed')),
        'submission_deadline': sub.isoformat() if sub else None,
        'submission_uz': _uz_date(sub),
        'submission_days': _days_until(sub),
        'source_url': r.get('source_url') or '',
        'description': r.get('description') or '',
        'days_remaining': days,
        'expired': days is not None and days < 0,
    }
    return item


# ── ro'yxat so'rovi (filtrlar + facetlar) ────────────────────────────────────

def _build_where(scope, args, uid):
    """(where_sql, params, saved_join). Facetlar ham shu yordamchidan quriladi
    (exclude parametri bilan) — bitta haqiqat manbai."""
    where = ["is_active = TRUE", "scope = %s"]
    params = [scope]

    time_f = args.get('time') or 'upcoming'
    if time_f == 'upcoming':
        where.append("(COALESCE(end_date, start_date) >= CURRENT_DATE "
                     "OR start_date IS NULL)")
    elif time_f == 'past':
        where.append("COALESCE(end_date, start_date) < CURRENT_DATE")

    q = (args.get('search') or '').strip()
    if q:
        where.append("(title ILIKE %s OR organizer ILIKE %s OR city ILIKE %s)")
        params += [f'%{q}%'] * 3

    def _multi(name, col):
        vals = [v for v in args.getlist(name) if v]
        if vals:
            where.append(f"{col} = ANY(%s)")
            params.append(vals)

    _multi('field', 'field')
    _multi('region', 'region')
    _multi('type', 'event_type')
    _multi('publisher', 'publisher')
    _multi('format', 'format')

    month = args.get('month')
    if month and month.isdigit() and 1 <= int(month) <= 12:
        where.append("EXTRACT(MONTH FROM start_date) = %s")
        params.append(int(month))

    if args.get('scopus') in ('1', 'true'):
        where.append("is_scopus_indexed = TRUE")

    saved_join = ''
    if args.get('saved') in ('1', 'true') and uid:
        saved_join = ("JOIN user_conference_bookmarks bm "
                      "ON bm.conference_id = conferences.id AND bm.user_id = %s")
    return ' AND '.join(where), params, saved_join


_FACET_DIMS = {  # facet nomi → (query param, ustun)
    'field': ('field', 'field'),
    'region': ('region', 'region'),
    'type': ('type', 'event_type'),
    'publisher': ('publisher', 'publisher'),
    'format': ('format', 'format'),
    'month': ('month', 'EXTRACT(MONTH FROM start_date)::int'),
}


class _ArgsView:
    """request.args ko'rinishi — bitta parametr olib tashlangan (facet uchun:
    o'z filtri o'ziga ta'sir qilmasin, boshqalari qo'llansin)."""

    def __init__(self, args, drop):
        self._a, self._drop = args, drop

    def get(self, k, default=None):
        return default if k == self._drop else self._a.get(k, default)

    def getlist(self, k):
        return [] if k == self._drop else self._a.getlist(k)


@conferences_bp.route('/api/v1/conferences')
def api_conferences():
    scope = request.args.get('scope') or 'local'
    if scope not in SCOPES:
        return jsonify({'ok': False, 'error': 'scope local|international'}), 400
    uid = current_user.id if getattr(current_user, 'is_authenticated', False) else None
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1

    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2_extras.RealDictCursor)
        _ensure_schema(cur)

        where, params, saved_join = _build_where(scope, request.args, uid)
        jp = [uid] if saved_join else []
        order = ("start_date ASC NULLS LAST, id DESC"
                 if (request.args.get('time') or 'upcoming') != 'past'
                 else "start_date DESC NULLS LAST, id DESC")

        cur.execute(f"SELECT COUNT(*) AS n FROM conferences {saved_join} "
                    f"WHERE {where}", jp + params)
        total = cur.fetchone()['n']

        cur.execute(
            f"SELECT {', '.join(_CONF_COLS)} FROM conferences {saved_join} "
            f"WHERE {where} ORDER BY {order} LIMIT %s OFFSET %s",
            jp + params + [PER_PAGE, (page - 1) * PER_PAGE])
        items = [_card(r) for r in cur.fetchall()]

        # facet countlar — har o'lchov o'z filtrisiz, qolganlari bilan
        facet_names = (('field', 'region', 'type', 'month') if scope == 'local'
                       else ('field', 'type', 'publisher', 'format', 'month'))
        facets = {}
        for name in facet_names:
            param, col = _FACET_DIMS[name]
            w, p, sj = _build_where(scope, _ArgsView(request.args, param), uid)
            jp2 = [uid] if sj else []
            cur.execute(
                f"SELECT {col} AS v, COUNT(*) AS n FROM conferences {sj} "
                f"WHERE {w} AND {col} IS NOT NULL "
                f"GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 30", jp2 + p)
            facets[name] = [{'value': str(r['v']), 'count': r['n']}
                            for r in cur.fetchall() if r['v'] not in (None, '')]

        saved_ids = []
        if uid:
            cur.execute("SELECT conference_id FROM user_conference_bookmarks "
                        "WHERE user_id = %s", (uid,))
            saved_ids = [r['conference_id'] for r in cur.fetchall()]
        conn.commit()
        pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
        return jsonify({'ok': True, 'items': items, 'count': total,
                        'page': min(page, pages), 'pages': pages,
                        'facets': facets, 'saved_ids': saved_ids})
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({'ok': False, 'error': 'server_error'}), 500
    finally:
        conn.close()


# ── sahifalar ────────────────────────────────────────────────────────────────

def _scope_stats(cur, scope):
    cur.execute("""
        SELECT COUNT(*) AS total,
               COUNT(DISTINCT region) FILTER (WHERE region IS NOT NULL) AS regions,
               COUNT(DISTINCT country) FILTER (WHERE country IS NOT NULL) AS countries,
               COUNT(DISTINCT field) FILTER (WHERE field IS NOT NULL) AS fields,
               COUNT(*) FILTER (WHERE COALESCE(end_date, start_date) >= CURRENT_DATE) AS upcoming
        FROM conferences WHERE is_active = TRUE AND scope = %s
    """, (scope,))
    r = cur.fetchone()
    return {'total': r[0] or 0, 'regions': r[1] or 0, 'countries': r[2] or 0,
            'fields': r[3] or 0, 'upcoming': r[4] or 0}


@conferences_bp.route('/konferensiyalar')
def conferences_hub():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            local = _scope_stats(cur, 'local')
            intl = _scope_stats(cur, 'international')
        conn.commit()
    finally:
        conn.close()
    return render_template('conferences_hub.html', local=local, intl=intl)


def _directory(scope):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            stats = _scope_stats(cur, scope)
        conn.commit()
    finally:
        conn.close()
    return render_template('conferences.html', scope=scope, stats=stats,
                           months=MONTHS_UZ, year=date.today().year,
                           is_authenticated=getattr(current_user,
                                                    'is_authenticated', False))


@conferences_bp.route('/konferensiyalar/mahalliy')
def conferences_local():
    return _directory('local')


@conferences_bp.route('/konferensiyalar/xalqaro')
def conferences_intl():
    return _directory('international')


@conferences_bp.route('/konferensiya/<slug>')
def conference_detail(slug):
    uid = current_user.id if getattr(current_user, 'is_authenticated', False) else None
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2_extras.RealDictCursor)
        _ensure_schema(cur)
        cur.execute(f"SELECT {', '.join(_CONF_COLS)} FROM conferences "
                    "WHERE title_slug = %s AND is_active = TRUE", (slug,))
        row = cur.fetchone()
        if not row:
            conn.commit()
            abort(404)
        conf = _card(row)

        # o'xshashlar: bir xil scope+soha, sana bo'yicha eng yaqinlar
        cur.execute(
            f"""SELECT {', '.join(_CONF_COLS)} FROM conferences
                WHERE is_active = TRUE AND scope = %s AND id <> %s
                  AND (field = %s OR %s = '')
                ORDER BY ABS(COALESCE(start_date, CURRENT_DATE) -
                             COALESCE(%s::date, CURRENT_DATE)) ASC
                LIMIT %s""",
            (conf['scope'], conf['id'], conf['field'], conf['field'],
             conf['start_date'], SIMILAR_LIMIT))
        similar = [_card(r) for r in cur.fetchall()]

        saved = False
        has_plan = False
        in_roadmap = False
        if uid:
            cur.execute("SELECT 1 FROM user_conference_bookmarks "
                        "WHERE user_id = %s AND conference_id = %s", (uid, conf['id']))
            saved = cur.fetchone() is not None
            try:
                cur.execute("SELECT id FROM roadmap_plans "
                            "WHERE user_id = %s AND is_active LIMIT 1", (uid,))
                plan = cur.fetchone()
                has_plan = plan is not None
                if plan:
                    cur.execute("SELECT 1 FROM roadmap_conferences "
                                "WHERE plan_id = %s AND source_conference_id = %s",
                                (plan['id'], conf['id']))
                    in_roadmap = cur.fetchone() is not None
            except Exception:
                conn.rollback()  # roadmap jadvallari hali yo'q muhit
        conn.commit()
    finally:
        conn.close()

    return render_template('conference_detail.html', c=conf, similar=similar,
                           saved=saved, has_plan=has_plan, in_roadmap=in_roadmap,
                           is_authenticated=uid is not None,
                           meta_description=(
                               f"{conf['title']} — {conf['start_uz']}"
                               f"{', ' + (conf['city'] or conf['country']) if (conf['city'] or conf['country']) else ''}."
                               " Muddatlar, manba va Roadmap integratsiyasi — Olimlar.uz"))


# ── bookmark (user_bookmarks UX aksi) ────────────────────────────────────────

@conferences_bp.route('/api/v1/conferences/bookmark', methods=['POST'])
@csrf.exempt
@login_required
def conference_bookmark():
    data = request.get_json(silent=True) or {}
    try:
        cid = int(data.get('conference_id'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': "Noto'g'ri so'rov"}), 400
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute("DELETE FROM user_conference_bookmarks "
                        "WHERE user_id = %s AND conference_id = %s",
                        (current_user.id, cid))
            if cur.rowcount:
                saved = False
            else:
                cur.execute("""
                    INSERT INTO user_conference_bookmarks (user_id, conference_id)
                    VALUES (%s, %s) ON CONFLICT DO NOTHING
                """, (current_user.id, cid))
                saved = True
            cur.execute("SELECT COUNT(*) FROM user_conference_bookmarks "
                        "WHERE user_id = %s", (current_user.id,))
            total = cur.fetchone()[0] or 0
        conn.commit()
        return jsonify({'success': True, 'saved': saved, 'total_saved': total})
    finally:
        conn.close()


# ── Roadmap integratsiyasi ───────────────────────────────────────────────────

@conferences_bp.route('/api/v1/conferences/roadmap', methods=['POST'])
@csrf.exempt
@login_required
def conference_to_roadmap():
    """Katalogdagi konferensiyani foydalanuvchining faol Roadmap rejasiga
    qo'shadi (roadmap_conferences, status='reja'). Faol reja yo'q bo'lsa
    need_plan=True — frontend /reja wizard'ga yo'naltiradi."""
    data = request.get_json(silent=True) or {}
    try:
        cid = int(data.get('conference_id'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': "Noto'g'ri so'rov"}), 400
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute("SELECT id, title, city, country, start_date, "
                        "submission_deadline, title_slug FROM conferences "
                        "WHERE id = %s AND is_active = TRUE", (cid,))
            conf = cur.fetchone()
            if not conf:
                return jsonify({'success': False,
                                'error': 'Konferensiya topilmadi'}), 404
            try:
                cur.execute("SELECT id FROM roadmap_plans "
                            "WHERE user_id = %s AND is_active LIMIT 1",
                            (current_user.id,))
                plan = cur.fetchone()
            except Exception:
                plan = None
            if not plan:
                conn.rollback()
                return jsonify({'success': False, 'need_plan': True,
                                'message': 'Avval Roadmap yarating'})
            # takror qo'shishni yumshoq bloklash
            cur.execute("SELECT 1 FROM roadmap_conferences "
                        "WHERE plan_id = %s AND source_conference_id = %s",
                        (plan[0], cid))
            if cur.fetchone():
                return jsonify({'success': True, 'already': True,
                                'message': "Roadmap'ingizda allaqachon bor"})
            location = ', '.join(x for x in (conf[2], conf[3]) if x) or None
            cur.execute("""
                INSERT INTO roadmap_conferences
                    (plan_id, name, location, event_date, deadline, url,
                     source_conference_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (plan[0], conf[1][:500], location and location[:300],
                  conf[4], conf[5], f"/konferensiya/{conf[6]}", cid))
            new_id = cur.fetchone()[0]
        conn.commit()
        return jsonify({'success': True, 'id': new_id,
                        'message': "Roadmap'ga qo'shildi"})
    finally:
        conn.close()


# ── ixtisoslik obunasi — yangi konf. alertlari ──────────────────────────────

def _match_tokens(label):
    """Obuna nomidan mazmunli tokenlar (≥6 harf, stop-so'zsiz)."""
    return [t for t in (m.group(0).lower()
                        for m in _TOKEN_RE.finditer(label or ''))
            if t not in _MATCH_STOP][:6]


def notify_conference_subscribers(conn, conf_rows):
    """Yangi konferensiyalarni mos ixtisoslik obunachilariga tarqatadi.

    Moslik best-effort: obunaning ixtisoslik_nomi tokenlaridan biri konf.
    title+field matnida uchrasa (spec: mo'rt kod-mapping majburlanmaydi).
    Dedup — conference_notifications_log. Xabar soni qaytadi; ko'tarilmaydi."""
    sent = 0
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            from blueprints.subscriptions import (_ensure_schema as _ensure_subs,
                                                  _telegram_chat_id)
            from blueprints.notifications import _ensure_schema as _ensure_notif
            _ensure_subs(cur)
            _ensure_notif(cur)
            cur.execute("""
                SELECT s.id, s.user_id, s.ixtisoslik, s.ixtisoslik_nomi,
                       s.notify_site, s.notify_telegram, u.telegram_chat_id, u.email
                FROM specialty_subscriptions s
                JOIN users u ON u.id = s.user_id
                WHERE s.notify_site OR s.notify_telegram
            """)
            subs = cur.fetchall()
            if not subs:
                return 0
            from blueprints.reminders import _send_telegram
            for conf in conf_rows:
                hay = f"{conf.get('title', '')} {conf.get('field', '')}".lower()
                when = conf.get('start_uz') or conf.get('start_date') or ''
                for (sub_id, user_id, code, label, n_site, n_tg,
                     chat_id, email) in subs:
                    tokens = _match_tokens(label or code)
                    if not tokens or not any(t in hay for t in tokens):
                        continue
                    cur.execute("""
                        SELECT sent_via FROM conference_notifications_log
                        WHERE subscription_id = %s AND conference_id = %s
                    """, (sub_id, conf['id']))
                    already = {r[0] for r in cur.fetchall()}
                    msg = (f"Siz obuna bo'lgan yo'nalishda yangi konferensiya: "
                           f"{conf['title']}" + (f" — {when}" if when else '') +
                           f"\n🔗 /konferensiya/{conf.get('slug', '')}")
                    if n_site and 'site' not in already:
                        cur.execute("""
                            INSERT INTO user_alerts (user_id, title, message, level)
                            VALUES (%s, %s, %s, 'info')
                        """, (user_id, '🔔 Yangi konferensiya', msg))
                        cur.execute("""
                            INSERT INTO conference_notifications_log
                                (subscription_id, conference_id, sent_via)
                            VALUES (%s, %s, 'site')
                        """, (sub_id, conf['id']))
                        sent += 1
                    tg = _telegram_chat_id(chat_id, email)
                    if n_tg and tg and 'telegram' not in already:
                        if _send_telegram(tg, {
                                'title': 'Yangi konferensiya',
                                'description': f"{conf['title']} — {when}",
                                'url': ('https://olimlar.uz/konferensiya/'
                                        + (conf.get('slug') or ''))}):
                            cur.execute("""
                                INSERT INTO conference_notifications_log
                                    (subscription_id, conference_id, sent_via)
                                VALUES (%s, %s, 'telegram')
                            """, (sub_id, conf['id']))
                            sent += 1
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    return sent


@conferences_bp.route('/api/v1/conferences/dispatch-alerts', methods=['POST'])
@csrf.exempt
def dispatch_alerts():
    """Scraper tugagach GitHub Actions chaqiradi (REMINDERS_API_KEY bilan —
    reminders cron kaliti qayta ishlatiladi). Oxirgi 8 kunda qo'shilgan faol
    konferensiyalarni obunachilarga tarqatadi; dedup log tufayli idempotent."""
    key = request.headers.get('X-Api-Key') or request.args.get('key') or ''
    expected = os.environ.get('REMINDERS_API_KEY', '')
    if not expected or key != expected:
        return jsonify({'ok': False, 'error': 'forbidden'}), 403
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2_extras.RealDictCursor)
        _ensure_schema(cur)
        cur.execute(f"""
            SELECT {', '.join(_CONF_COLS)} FROM conferences
            WHERE is_active = TRUE AND created_at >= NOW() - INTERVAL '8 days'
            ORDER BY id
        """)
        rows = [_card(r) for r in cur.fetchall()]
        conn.commit()
        sent = notify_conference_subscribers(conn, rows)
        return jsonify({'ok': True, 'new_conferences': len(rows), 'sent': sent})
    finally:
        conn.close()


# ── admin CRUD (admin/grants naqshi) ─────────────────────────────────────────

_ADMIN_FIELDS = ('title', 'scope', 'organizer', 'field', 'region', 'city',
                 'event_type', 'start_date', 'end_date', 'format', 'publisher',
                 'submission_deadline', 'country', 'source_url', 'description')


def _admin_form_values():
    f = request.form
    v = {k: (f.get(k) or '').strip() or None for k in _ADMIN_FIELDS}
    v['scope'] = v['scope'] if v['scope'] in SCOPES else 'local'
    for k in ('start_date', 'end_date', 'submission_deadline'):
        v[k] = _parse_date(v[k])
    v['is_scopus_indexed'] = f.get('is_scopus_indexed') == 'on'
    v['is_multiday'] = bool(v['start_date'] and v['end_date']
                            and v['end_date'] != v['start_date'])
    return v


@conferences_bp.route('/admin/conferences')
@login_required
def admin_conferences():
    from app import _require_admin
    _require_admin()
    q = (request.args.get('q') or '').strip()
    scope = request.args.get('scope') or ''
    where, params = ["TRUE"], []
    if q:
        where.append("(title ILIKE %s OR organizer ILIKE %s)")
        params += [f'%{q}%'] * 2
    if scope in SCOPES:
        where.append("scope = %s")
        params.append(scope)
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2_extras.RealDictCursor)
        _ensure_schema(cur)
        cur.execute(
            f"SELECT {', '.join(_CONF_COLS)} FROM conferences "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY is_active DESC, start_date DESC NULLS LAST, id DESC "
            "LIMIT 400", params)
        items = []
        for r in cur.fetchall():
            it = _card(r)
            it['is_active'] = bool(r['is_active'])
            items.append(it)
        conn.commit()
    finally:
        conn.close()
    return render_template('admin/conferences.html', items=items, q=q,
                           scope=scope)


@conferences_bp.route('/admin/conferences/add', methods=['GET', 'POST'])
@login_required
def admin_conferences_add():
    from app import _require_admin
    _require_admin()
    if request.method == 'POST':
        v = _admin_form_values()
        if not v['title']:
            flash('Sarlavha kiritilishi shart.', 'error')
            return render_template('admin/conference_form.html', item=v,
                                   edit_mode=False, formats=FORMATS)
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                slug = make_slug(v['title'], cur)
                cols = list(_ADMIN_FIELDS) + ['is_scopus_indexed', 'is_multiday',
                                              'title_slug']
                ph = ', '.join(['%s'] * len(cols))
                cur.execute(
                    f"INSERT INTO conferences ({', '.join(cols)}) VALUES ({ph})",
                    [v[c] for c in _ADMIN_FIELDS]
                    + [v['is_scopus_indexed'], v['is_multiday'], slug])
            conn.commit()
            flash("Konferensiya qo'shildi!", 'success')
            return redirect('/admin/conferences')
        except Exception as e:
            conn.rollback()
            flash('Xatolik: ' + str(e), 'error')
            return render_template('admin/conference_form.html', item=v,
                                   edit_mode=False, formats=FORMATS)
        finally:
            conn.close()
    return render_template('admin/conference_form.html', item=None,
                           edit_mode=False, formats=FORMATS)


@conferences_bp.route('/admin/conferences/edit/<int:cid>', methods=['GET', 'POST'])
@login_required
def admin_conferences_edit(cid):
    from app import _require_admin
    _require_admin()
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2_extras.RealDictCursor)
        _ensure_schema(cur)
        if request.method == 'POST':
            v = _admin_form_values()
            if not v['title']:
                flash('Sarlavha kiritilishi shart.', 'error')
            else:
                sets = ', '.join(f'{c} = %s' for c in _ADMIN_FIELDS)
                cur.execute(
                    f"UPDATE conferences SET {sets}, is_scopus_indexed = %s, "
                    "is_multiday = %s, updated_at = NOW() WHERE id = %s",
                    [v[c] for c in _ADMIN_FIELDS]
                    + [v['is_scopus_indexed'], v['is_multiday'], cid])
                conn.commit()
                flash('Saqlandi!', 'success')
                return redirect('/admin/conferences')
        cur.execute(f"SELECT {', '.join(_CONF_COLS)} FROM conferences "
                    "WHERE id = %s", (cid,))
        row = cur.fetchone()
        conn.commit()
        if not row:
            abort(404)
    finally:
        conn.close()
    return render_template('admin/conference_form.html', item=dict(row),
                           edit_mode=True, formats=FORMATS)


@conferences_bp.route('/admin/conferences/toggle/<int:cid>', methods=['POST'])
@login_required
def admin_conferences_toggle(cid):
    from app import _require_admin
    _require_admin()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute("UPDATE conferences SET is_active = NOT is_active, "
                        "updated_at = NOW() WHERE id = %s "
                        "RETURNING is_active", (cid,))
            row = cur.fetchone()
        conn.commit()
        flash('Faollik o\'zgartirildi.' if row else 'Topilmadi.',
              'success' if row else 'error')
    finally:
        conn.close()
    return redirect('/admin/conferences')
