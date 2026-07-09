"""Admin blueprint — administrative routes extracted from the app.py monolith.

All routes are prefixed by their original URL paths (e.g. /admin/...), so existing
hardcoded links and form actions keep working. Endpoint names are now namespaced
under the 'admin' blueprint (e.g. url_for('admin.admin_yangiliklar')).

Shared helpers and module constants still live in app.py and are imported lazily
inside each view (matching the auth.py / cabinet.py pattern) to avoid circular
imports. `csrf` is imported at module load — it is defined in app.py before the
blueprints are registered, so this is safe.
"""
import os
from datetime import datetime, timezone

from flask import (
    Blueprint, render_template, request, redirect, url_for, jsonify, abort, flash
)
from flask_login import login_required, current_user

from app import csrf

admin_bp = Blueprint('admin', __name__)


# Duration string → Postgres interval literal (expiry computed server-side via NOW()).
_DURATION_INTERVALS = {
    "10m": "10 minutes", "30m": "30 minutes",
    "1h": "1 hour", "24h": "24 hours", "7d": "7 days", "1d": "1 day",
}


@admin_bp.route('/admin/analytics')
@login_required
def admin_analytics():
    if not getattr(current_user, 'is_admin', False):
        abort(403)
    from data import get_connection
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Today's visits
            cur.execute("SELECT COUNT(*) FROM page_visits WHERE visited_at::date = CURRENT_DATE")
            today_visits = cur.fetchone()[0]

            # Today's unique visitors
            cur.execute("""SELECT COUNT(DISTINCT COALESCE(user_id::text, ip_address))
                FROM page_visits WHERE visited_at::date = CURRENT_DATE""")
            today_unique = cur.fetchone()[0]

            # Online now (last 5 min)
            cur.execute("""SELECT COUNT(DISTINCT COALESCE(user_id::text, ip_address))
                FROM page_visits WHERE visited_at > NOW() - INTERVAL '5 minutes'""")
            online_now = cur.fetchone()[0]

            # Identity = registered username (case-folded) for real accounts,
            # else the IP. Guests are logged as 'Mehmon'/'Anonim' so they group by IP.
            _ID = ("CASE WHEN username IS NOT NULL AND TRIM(username) <> '' "
                   "AND username NOT IN ('Mehmon', 'Anonim') "
                   "THEN LOWER(TRIM(username)) ELSE ip_address END")

            # Recent visitors — one row per identity, latest activity first (last 24h)
            cur.execute(f"""
                SELECT {_ID} AS identity,
                       MAX(username) AS username,
                       MAX(user_id) AS user_id,
                       (array_agg(page ORDER BY visited_at DESC))[1] AS last_page,
                       MAX(visited_at) AS last_visit,
                       COUNT(*) AS total_visits,
                       COUNT(DISTINCT page) AS unique_pages,
                       MAX(ip_address) AS ip,
                       MAX(country) AS country
                FROM page_visits
                WHERE visited_at >= NOW() - INTERVAL '24 hours'
                GROUP BY {_ID}
                ORDER BY last_visit DESC
                LIMIT 50
            """)
            recent = [{
                "username": r[1] or "", "user_id": r[2],
                "page": r[3] or "/", "visited_at": r[4],
                "total_visits": r[5] or 0, "unique_pages": r[6] or 0,
                "ip": r[7] or "—", "country": r[8] or "",
            } for r in cur.fetchall()]

            # Daily visits last 7 days
            cur.execute("""SELECT visited_at::date AS d, COUNT(*) AS cnt FROM page_visits
                WHERE visited_at > NOW() - INTERVAL '7 days'
                GROUP BY d ORDER BY d""")
            weekly = cur.fetchall()

            # Top registered visitors — grouped by username (one row per account)
            registered_visitors = []
            try:
                cur.execute("""
                    SELECT LOWER(TRIM(username)) AS identity,
                           MAX(username) AS username,
                           MAX(user_id) AS user_id,
                           COUNT(*) AS visit_count,
                           COUNT(DISTINCT ip_address) AS ip_count,
                           COUNT(DISTINCT page) AS unique_pages,
                           MIN(visited_at) AS first_visit,
                           MAX(visited_at) AS last_visit,
                           MAX(country) AS country,
                           MAX(ip_address) AS ip
                    FROM page_visits
                    WHERE username IS NOT NULL AND TRIM(username) <> ''
                          AND username NOT IN ('Mehmon', 'Anonim')
                    GROUP BY LOWER(TRIM(username))
                    ORDER BY visit_count DESC
                    LIMIT 20
                """)
                registered_visitors = [{
                    "username": r[1] or "", "user_id": r[2], "visit_count": r[3] or 0,
                    "ip_count": r[4] or 0, "unique_pages": r[5] or 0,
                    "first_visit": str(r[6])[:16] if r[6] else "",
                    "last_visit": str(r[7])[:16] if r[7] else "",
                    "country": r[8] or "", "ip": r[9] or "—",
                } for r in cur.fetchall()]
            except Exception:
                registered_visitors = []

            # Top guest visitors — grouped by IP (no real account)
            guest_visitors = []
            try:
                cur.execute("""
                    SELECT ip_address,
                           COUNT(*) AS visit_count,
                           COUNT(DISTINCT page) AS unique_pages,
                           MIN(visited_at) AS first_visit,
                           MAX(visited_at) AS last_visit,
                           MAX(country) AS country
                    FROM page_visits
                    WHERE username IS NULL OR TRIM(username) = ''
                          OR username IN ('Mehmon', 'Anonim')
                    GROUP BY ip_address
                    ORDER BY visit_count DESC
                    LIMIT 20
                """)
                guest_visitors = [{
                    "ip": r[0] or "—", "visit_count": r[1] or 0, "unique_pages": r[2] or 0,
                    "first_visit": str(r[3])[:16] if r[3] else "",
                    "last_visit": str(r[4])[:16] if r[4] else "",
                    "country": r[5] or "",
                } for r in cur.fetchall()]
            except Exception:
                guest_visitors = []

            # Registered cabinet users (if any)
            registered_users = []
            try:
                cur.execute("""
                    SELECT id, email, telegram_username, telegram_first_name,
                           olim_name, created_at, last_login
                    FROM cabinet_users ORDER BY created_at DESC LIMIT 50
                """)
                registered_users = [{
                    "id": r[0], "email": r[1] or "", "telegram_username": r[2] or "",
                    "telegram_first_name": r[3] or "", "olim_name": r[4] or "",
                    "created_at": r[5], "last_login": r[6],
                } for r in cur.fetchall()]
            except Exception:
                registered_users = []

            # ── Summary: registered users / today's new sign-ups / guests ──
            registered_count = 0
            new_today_count = 0
            guest_count = 0
            try:
                cur.execute("SELECT COUNT(*) FROM cabinet_users")
                registered_count = cur.fetchone()[0] or 0
            except Exception:
                registered_count = 0
            try:
                cur.execute("SELECT COUNT(*) FROM cabinet_users WHERE created_at >= CURRENT_DATE")
                new_today_count = cur.fetchone()[0] or 0
            except Exception:
                new_today_count = 0
            try:
                # Distinct guest visitors (no account → logged as "Mehmon"), by IP
                cur.execute("""SELECT COUNT(DISTINCT ip_address) FROM page_visits
                    WHERE user_id IS NULL AND (username = 'Mehmon' OR username IS NULL)""")
                guest_count = cur.fetchone()[0] or 0
            except Exception:
                guest_count = 0

            # Totals for the summary row
            total_dissertations = 0
            total_news = 0
            try:
                cur.execute("SELECT COUNT(*) FROM dissertations")
                total_dissertations = cur.fetchone()[0] or 0
            except Exception:
                total_dissertations = 0
            try:
                cur.execute("SELECT COUNT(*) FROM yangiliklar WHERE is_published = TRUE")
                total_news = cur.fetchone()[0] or 0
            except Exception:
                total_news = 0

            # Live online users (last 5 min) — one row per distinct visitor,
            # showing the page they are currently on.
            online_users = []
            try:
                cur.execute(f"""
                    SELECT {_ID} AS identity,
                           MAX(username) AS username,
                           MAX(user_id) AS user_id,
                           (array_agg(page ORDER BY visited_at DESC))[1] AS current_page,
                           MAX(visited_at) AS last_activity,
                           COUNT(DISTINCT ip_address) AS ip_count,
                           MAX(ip_address) AS ip,
                           MAX(country) AS country
                    FROM page_visits
                    WHERE visited_at > NOW() - INTERVAL '5 minutes'
                    GROUP BY {_ID}
                    ORDER BY last_activity DESC
                """)
                online_users = [{
                    "ip": r[6] or "—", "username": r[1] or "", "user_id": r[2],
                    "page": r[3] or "/", "visited_at": r[4], "country": r[7] or "",
                } for r in cur.fetchall()]
            except Exception:
                online_users = []

            # Currently active blocks
            active_blocks = []
            try:
                cur.execute("""
                    SELECT id, ip_address, reason, blocked_by, blocked_until,
                           is_permanent, created_at, duration_text
                    FROM blocked_users
                    WHERE is_active = TRUE AND (is_permanent = TRUE OR blocked_until > NOW())
                    ORDER BY created_at DESC
                """)
                active_blocks = [{
                    "id": r[0], "ip_address": r[1] or "—", "reason": r[2] or "",
                    "blocked_by": r[3] or "admin", "blocked_until": r[4],
                    "is_permanent": r[5], "created_at": r[6], "duration_text": r[7] or "",
                } for r in cur.fetchall()]
            except Exception:
                active_blocks = []

            # Block history (inactive / expired / unblocked)
            block_history = []
            try:
                cur.execute("""
                    SELECT id, ip_address, reason, blocked_by, blocked_until,
                           is_permanent, created_at, duration_text, unblocked_at, unblocked_by
                    FROM blocked_users
                    WHERE is_active = FALSE
                    ORDER BY created_at DESC LIMIT 50
                """)
                block_history = [{
                    "id": r[0], "ip_address": r[1] or "—", "reason": r[2] or "",
                    "blocked_by": r[3] or "admin", "blocked_until": r[4],
                    "is_permanent": r[5], "created_at": r[6], "duration_text": r[7] or "",
                    "unblocked_at": r[8], "unblocked_by": r[9] or "",
                } for r in cur.fetchall()]
            except Exception:
                block_history = []

            # Active broadcasts list (for admin management)
            broadcasts = []
            try:
                cur.execute("""
                    SELECT id, message, message_type, is_active, show_to,
                           created_at, expires_at
                    FROM admin_broadcasts
                    WHERE is_active = TRUE
                    ORDER BY created_at DESC
                """)
                broadcasts = [{
                    "id": r[0], "message": r[1] or "", "message_type": r[2] or "info",
                    "is_active": r[3], "show_to": r[4] or "all",
                    "created_at": r[5], "expires_at": r[6],
                } for r in cur.fetchall()]
            except Exception:
                broadcasts = []
    finally:
        conn.close()

    return render_template('admin_analytics.html',
        today_visits=today_visits, today_unique=today_unique, online_now=online_now,
        recent=recent, weekly=weekly,
        registered_visitors=registered_visitors, guest_visitors=guest_visitors,
        registered_users=registered_users,
        registered_count=registered_count, new_today_count=new_today_count,
        guest_count=guest_count, total_dissertations=total_dissertations,
        total_news=total_news, online_users=online_users,
        active_blocks=active_blocks, block_history=block_history,
        now=datetime.utcnow(), broadcasts=broadcasts)


# ── Acquisition-source survey analytics ("Foydalanuvchi manbalari") ──────────
# Reference SQL (reused by the queries below):
#
#   Overall distribution:
#     SELECT acquisition_source, COUNT(*)
#     FROM users
#     WHERE acquisition_survey_answered_at IS NOT NULL
#     GROUP BY acquisition_source
#     ORDER BY 2 DESC;
#
#   Weekly cohort:
#     SELECT DATE_TRUNC('week', created_at) AS signup_week,
#            acquisition_source,
#            COUNT(*)
#     FROM users
#     WHERE acquisition_survey_answered_at IS NOT NULL
#     GROUP BY 1, 2
#     ORDER BY 1 DESC, 3 DESC;
#
#   Response rate:
#     SELECT
#       COUNT(*) FILTER (WHERE acquisition_survey_answered_at IS NOT NULL) AS answered,
#       COUNT(*) FILTER (WHERE acquisition_survey_shown_at IS NOT NULL
#                        AND acquisition_survey_answered_at IS NULL) AS skipped,
#       COUNT(*) FILTER (WHERE acquisition_survey_shown_at IS NULL) AS pending
#     FROM users;

# Uzbek labels for the eight allowed sources (matches the modal tiles).
_ACQ_SOURCE_LABELS = {
    'telegram': 'Telegram',
    'youtube': 'YouTube',
    'instagram': 'Instagram',
    'friend_colleague': "Do'st / hamkasb",
    'advisor': 'Ilmiy rahbar',
    'google_search': 'Google qidiruv',
    'university': "Universitet e'loni",
    'other': 'Boshqa',
}
# ?range= → number of days back for the distribution + trend (top-line counts
# are lifetime). 'all' means no lower bound.
_ACQ_RANGES = {'7': 7, '30': 30, '90': 90, 'all': None}


@admin_bp.route('/admin/acquisition')
@login_required
def admin_acquisition():
    if not getattr(current_user, 'is_admin', False):
        abort(403)
    from data import get_connection

    rng = request.args.get('range', '30')
    if rng not in _ACQ_RANGES:
        rng = '30'
    days = _ACQ_RANGES[rng]
    # Safe: `days` is an int drawn from the fixed whitelist above.
    since_clause = "" if days is None else \
        " AND acquisition_survey_answered_at >= NOW() - INTERVAL '%d days'" % days

    answered = skipped = pending = 0
    distribution = []
    weekly = []
    others = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                # Response rate — lifetime snapshot across all users.
                cur.execute("""
                    SELECT
                      COUNT(*) FILTER (WHERE acquisition_survey_answered_at IS NOT NULL),
                      COUNT(*) FILTER (WHERE acquisition_survey_shown_at IS NOT NULL
                                       AND acquisition_survey_answered_at IS NULL),
                      COUNT(*) FILTER (WHERE acquisition_survey_shown_at IS NULL)
                    FROM users
                """)
                r = cur.fetchone() or (0, 0, 0)
                answered, skipped, pending = r[0] or 0, r[1] or 0, r[2] or 0

                # Distribution per source (respects the date filter).
                cur.execute("""
                    SELECT acquisition_source, COUNT(*)
                    FROM users
                    WHERE acquisition_survey_answered_at IS NOT NULL""" + since_clause + """
                    GROUP BY acquisition_source
                    ORDER BY 2 DESC
                """)
                rows = cur.fetchall()
                total_in_range = sum((c or 0) for _, c in rows) or 0
                for src, cnt in rows:
                    cnt = cnt or 0
                    distribution.append({
                        "source": src or "—",
                        "label": _ACQ_SOURCE_LABELS.get(src, src or "—"),
                        "count": cnt,
                        "pct": round(cnt * 100.0 / total_in_range, 1) if total_in_range else 0.0,
                    })

                # Weekly trend of answers (respects the date filter).
                cur.execute("""
                    SELECT DATE_TRUNC('week', acquisition_survey_answered_at) AS wk, COUNT(*)
                    FROM users
                    WHERE acquisition_survey_answered_at IS NOT NULL""" + since_clause + """
                    GROUP BY wk ORDER BY wk
                """)
                weekly = [{"week": str(w)[:10] if w else "", "count": c or 0}
                          for w, c in cur.fetchall()]

                # Latest 'other' free-text responses — where new channels surface.
                cur.execute("""
                    SELECT COALESCE(username, ''), acquisition_source_other,
                           acquisition_survey_answered_at
                    FROM users
                    WHERE acquisition_source = 'other'
                      AND acquisition_source_other IS NOT NULL
                      AND TRIM(acquisition_source_other) <> ''
                    ORDER BY acquisition_survey_answered_at DESC
                    LIMIT 50
                """)
                others = [{"username": o[0] or "—", "text": o[1] or "",
                           "answered_at": str(o[2])[:16] if o[2] else ""}
                          for o in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        # Fresh DB before migration → show an empty (but functional) dashboard.
        pass

    total_users = answered + skipped + pending
    return render_template('admin_acquisition.html',
        rng=rng, answered=answered, skipped=skipped, pending=pending,
        total_users=total_users, distribution=distribution,
        weekly=weekly, others=others)


@admin_bp.route('/admin/api/block-user', methods=['POST'])
@csrf.exempt
@login_required
def admin_block_user():
    from app import _require_admin
    _require_admin()
    from data import get_connection
    data = request.get_json(silent=True) or {}
    ip = (data.get('ip_address') or '').strip()
    if not ip:
        return jsonify({"success": False, "error": "ip_address required"}), 400
    reason = (data.get('reason') or '').strip()[:500] or None
    duration = data.get('duration') or '30m'
    if duration != "permanent" and duration not in _DURATION_INTERVALS:
        duration = '30m'
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                # Keep history — supersede any existing active block for this IP
                # rather than deleting it.
                cur.execute(
                    "UPDATE blocked_users SET is_active = FALSE, unblocked_at = NOW(), "
                    "unblocked_by = 'admin (qayta bloklash)' "
                    "WHERE ip_address = %s AND is_active = TRUE", (ip,))
                if duration == "permanent":
                    cur.execute("""
                        INSERT INTO blocked_users
                            (ip_address, reason, blocked_by, blocked_until,
                             is_permanent, is_active, duration_text)
                        VALUES (%s, %s, 'admin', NULL, TRUE, TRUE, 'permanent')
                    """, (ip, reason))
                else:
                    interval = _DURATION_INTERVALS.get(duration, "30 minutes")
                    cur.execute("""
                        INSERT INTO blocked_users
                            (ip_address, reason, blocked_by, blocked_until,
                             is_permanent, is_active, duration_text)
                        VALUES (%s, %s, 'admin', NOW() + INTERVAL %s, FALSE, TRUE, %s)
                    """, (ip, reason, interval, duration))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route('/admin/api/unblock-user', methods=['POST'])
@csrf.exempt
@login_required
def admin_unblock_user():
    from app import _require_admin
    _require_admin()
    from data import get_connection
    data = request.get_json(silent=True) or {}
    ip = (data.get('ip_address') or '').strip()
    if not ip:
        return jsonify({"success": False, "error": "ip_address required"}), 400
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE blocked_users SET is_active = FALSE, unblocked_at = NOW(), "
                    "unblocked_by = 'admin' WHERE ip_address = %s AND is_active = TRUE", (ip,))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route('/admin/api/broadcast', methods=['POST'])
@csrf.exempt
@login_required
def admin_broadcast():
    from app import _require_admin
    _require_admin()
    from data import get_connection
    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({"success": False, "error": "message required"}), 400
    mtype = data.get('type') or 'info'
    if mtype not in ('info', 'warning', 'success'):
        mtype = 'info'
    show_to = data.get('show_to') or 'all'
    if show_to not in ('all', 'guests', 'registered'):
        show_to = 'all'
    duration = data.get('duration') or '24h'
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                if duration == "permanent":
                    cur.execute("""
                        INSERT INTO admin_broadcasts
                            (message, message_type, show_to, expires_at, is_active)
                        VALUES (%s, %s, %s, NULL, TRUE)
                    """, (message, mtype, show_to))
                else:
                    interval = _DURATION_INTERVALS.get(duration, "24 hours")
                    cur.execute("""
                        INSERT INTO admin_broadcasts
                            (message, message_type, show_to, expires_at, is_active)
                        VALUES (%s, %s, %s, NOW() + INTERVAL %s, TRUE)
                    """, (message, mtype, show_to, interval))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route('/admin/api/broadcast/delete/<int:id>', methods=['POST'])
@csrf.exempt
@login_required
def admin_broadcast_delete(id):
    from app import _require_admin
    _require_admin()
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE admin_broadcasts SET is_active = FALSE WHERE id = %s", (id,))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route('/admin/user-activity/<identifier>')
@login_required
def admin_user_activity(identifier):
    if not getattr(current_user, 'is_admin', False):
        abort(403)
    from collections import OrderedDict, Counter
    from data import get_connection
    from app import UZT, parse_device, parse_referrer

    def _to_uzt(dt):
        if not isinstance(dt, datetime):
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(UZT)

    is_ip = identifier.replace('.', '').isdigit() and '.' in identifier
    user_info = {'type': 'ip' if is_ip else 'user', 'identifier': identifier,
                 'username': '', 'email': '', 'telegram_username': '',
                 'olim_name': '', 'registered': False, 'user_id': None}
    rows = []
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if is_ip:
                cur.execute("""
                    SELECT page, visited_at, user_agent, referrer, username, user_id
                    FROM page_visits WHERE ip_address = %s
                    ORDER BY visited_at DESC LIMIT 200
                """, (identifier,))
                rows = cur.fetchall()
                for r in rows:
                    if r[4]:
                        user_info['username'] = r[4]
                        user_info['user_id'] = r[5]
                        break
            else:
                uid = None
                try:
                    uid = int(identifier)
                except Exception:
                    uid = None
                cur.execute("""
                    SELECT page, visited_at, user_agent, referrer, username, user_id
                    FROM page_visits WHERE user_id = %s OR username = %s
                    ORDER BY visited_at DESC LIMIT 200
                """, (uid, identifier))
                rows = cur.fetchall()
                user_info['username'] = (rows[0][4] if rows else '') or identifier
                user_info['user_id'] = uid if uid is not None else (rows[0][5] if rows else None)
                # Enrich from cabinet_users if registered
                try:
                    if uid is not None:
                        cur.execute("""SELECT id, email, telegram_username, olim_name
                                       FROM cabinet_users WHERE id = %s""", (uid,))
                    else:
                        cur.execute("""SELECT id, email, telegram_username, olim_name
                                       FROM cabinet_users
                                       WHERE email = %s OR telegram_username = %s OR olim_name = %s""",
                                    (identifier, identifier, identifier))
                    cu = cur.fetchone()
                    if cu:
                        user_info['registered'] = True
                        user_info['user_id'] = cu[0]
                        user_info['email'] = cu[1] or ''
                        user_info['telegram_username'] = cu[2] or ''
                        user_info['olim_name'] = cu[3] or ''
                except Exception:
                    pass
    finally:
        conn.close()

    # Build per-visit dicts (converted to Tashkent time)
    activities = []
    for page, visited_at, ua, ref, _uname, _uid in rows:
        uz = _to_uzt(visited_at)
        activities.append({
            'page': page or '/', 'visited_at': visited_at, 'uz': uz,
            'user_agent': ua or '', 'referrer': ref or '',
        })

    # Stats
    pages = [a['page'] for a in activities]
    uz_dates = [a['uz'] for a in activities if a['uz']]
    page_counter = Counter(pages)
    most_visited_page = page_counter.most_common(1)[0][0] if page_counter else '—'
    distinct_days = len({d.date() for d in uz_dates}) or 1
    devices = len({a['user_agent'] for a in activities if a['user_agent']})
    last_dt = max(uz_dates) if uz_dates else None
    first_dt = min(uz_dates) if uz_dates else None

    stats = {
        'total_visits': len(activities),
        'unique_pages': len(page_counter),
        'first_visit': first_dt.strftime('%d.%m.%Y %H:%M') if first_dt else '—',
        'last_visit': last_dt.strftime('%d.%m.%Y %H:%M') if last_dt else '—',
        'most_visited_page': most_visited_page,
        'avg_visits_per_day': round(len(activities) / distinct_days, 1),
        'devices': devices,
    }

    # Most common device / referrer
    dev_counter = Counter(parse_device(a['user_agent']) for a in activities if a['user_agent'])
    ref_counter = Counter(parse_referrer(a['referrer']) for a in activities)
    stats['device_info'] = dev_counter.most_common(1)[0][0] if dev_counter else '—'
    stats['referrer_info'] = ref_counter.most_common(1)[0][0] if ref_counter else '—'

    # Timeline grouped by date (already DESC by visited_at)
    visits_by_date = OrderedDict()
    for a in activities:
        if not a['uz']:
            continue
        key = a['uz'].strftime('%d.%m.%Y')
        visits_by_date.setdefault(key, []).append({
            'time': a['uz'].strftime('%H:%M'), 'page': a['page'],
        })

    # Page frequency (top 10) for CSS bar chart
    top_pages_freq = page_counter.most_common(10)
    max_page_count = top_pages_freq[0][1] if top_pages_freq else 1

    # Hour-of-day heatmap (Tashkent hours)
    hour_counts = [0] * 24
    for d in uz_dates:
        hour_counts[d.hour] += 1
    max_hour_count = max(hour_counts) if any(hour_counts) else 1

    # Block status + history for this IP (block actions are IP-based)
    block_ip = identifier if is_ip else None
    current_block = None
    user_block_history = []
    if block_ip:
        try:
            bconn = get_connection()
            try:
                with bconn.cursor() as bcur:
                    bcur.execute("""
                        SELECT reason, blocked_until, is_permanent, duration_text
                        FROM blocked_users
                        WHERE ip_address = %s AND is_active = TRUE
                        AND (is_permanent = TRUE OR blocked_until > NOW())
                        ORDER BY created_at DESC LIMIT 1
                    """, (block_ip,))
                    br = bcur.fetchone()
                    if br:
                        current_block = {
                            "reason": br[0] or "", "blocked_until": br[1],
                            "is_permanent": br[2], "duration_text": br[3] or "",
                        }
                    bcur.execute("""
                        SELECT reason, blocked_until, is_permanent, created_at,
                               duration_text, is_active, unblocked_at, unblocked_by
                        FROM blocked_users WHERE ip_address = %s
                        ORDER BY created_at DESC
                    """, (block_ip,))
                    user_block_history = [{
                        "reason": r[0] or "", "blocked_until": r[1], "is_permanent": r[2],
                        "created_at": r[3], "duration_text": r[4] or "",
                        "is_active": r[5], "unblocked_at": r[6], "unblocked_by": r[7] or "",
                    } for r in bcur.fetchall()]
            finally:
                bconn.close()
        except Exception:
            current_block = None
            user_block_history = []

    return render_template('admin_user_activity.html',
        user_info=user_info, activities=activities, stats=stats,
        visits_by_date=visits_by_date, top_pages_freq=top_pages_freq,
        max_page_count=max_page_count, hour_counts=hour_counts,
        max_hour_count=max_hour_count,
        block_ip=block_ip, current_block=current_block,
        user_block_history=user_block_history)


@admin_bp.route("/admin/yangiliklar")
@login_required
def admin_yangiliklar():
    from app import _require_admin
    _require_admin()
    from data import get_connection
    items = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, title, summary, created_at, is_published, image_url "
                    "FROM yangiliklar ORDER BY created_at DESC, id DESC"
                )
                items = [{
                    "id": r[0], "title": r[1] or "", "summary": r[2] or "",
                    "created_at": str(r[3])[:16] if r[3] else "", "is_published": r[4],
                    "image_url": r[5] or "",
                } for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        items = []
    return render_template("admin_yangiliklar.html", items=items)


@admin_bp.route("/admin/yangiliklar/add", methods=["GET", "POST"])
@login_required
def admin_yangilik_add():
    from app import _require_admin, _yangilik_form_values
    _require_admin()
    from data import get_connection
    if request.method == "POST":
        v = _yangilik_form_values()
        if not v["title"] or not v["summary"]:
            flash("Sarlavha va qisqa matn majburiy.", "error")
            return render_template("admin_yangilik_form.html", item=v, edit_mode=False)
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO yangiliklar (title, summary, content, image_url, source_url, is_published) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (v["title"], v["summary"], v["content"], v["image_url"], v["source_url"], v["is_published"])
                    )
                conn.commit()
            finally:
                conn.close()
            flash("Yangilik muvaffaqiyatli qo'shildi!", "success")
        except Exception:
            flash("Yangilik qo'shishda xatolik yuz berdi.", "error")
        return redirect(url_for("admin.admin_yangiliklar"))
    return render_template("admin_yangilik_form.html", item=None, edit_mode=False)


@admin_bp.route("/admin/yangiliklar/edit/<int:id>", methods=["GET", "POST"])
@login_required
def admin_yangilik_edit(id):
    from app import _require_admin, _yangilik_form_values, _delete_local_news_image
    _require_admin()
    from data import get_connection

    def _load():
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, title, summary, content, image_url, source_url, is_published "
                        "FROM yangiliklar WHERE id = %s", (id,))
                    r = cur.fetchone()
                    if r:
                        return {
                            "id": r[0], "title": r[1] or "", "summary": r[2] or "",
                            "content": r[3] or "", "image_url": r[4] or "",
                            "source_url": r[5] or "", "is_published": r[6],
                        }
            finally:
                conn.close()
        except Exception:
            return None
        return None

    current = _load()
    if not current:
        abort(404)

    if request.method == "POST":
        v = _yangilik_form_values(existing_image=current.get("image_url") or None)
        if not v["title"] or not v["summary"]:
            flash("Sarlavha va qisqa matn majburiy.", "error")
            v["id"] = id
            return render_template("admin_yangilik_form.html", item=v, edit_mode=True)
        # if the stored image changed/removed and it was a local file, drop it from disk
        old_img = current.get("image_url") or ""
        if old_img and old_img != (v["image_url"] or ""):
            _delete_local_news_image(old_img)
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE yangiliklar SET title=%s, summary=%s, content=%s, "
                        "image_url=%s, source_url=%s, is_published=%s, updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=%s",
                        (v["title"], v["summary"], v["content"], v["image_url"],
                         v["source_url"], v["is_published"], id))
                conn.commit()
            finally:
                conn.close()
            flash("Yangilik yangilandi!", "success")
        except Exception:
            flash("Yangilikni yangilashda xatolik yuz berdi.", "error")
        return redirect(url_for("admin.admin_yangiliklar"))

    return render_template("admin_yangilik_form.html", item=current, edit_mode=True)


@admin_bp.route("/admin/yangiliklar/delete/<int:id>", methods=["POST"])
@login_required
def admin_yangilik_delete(id):
    from app import _require_admin, _delete_local_news_image
    _require_admin()
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT image_url FROM yangiliklar WHERE id = %s", (id,))
                row = cur.fetchone()
                cur.execute("DELETE FROM yangiliklar WHERE id = %s", (id,))
            conn.commit()
            if row and row[0]:
                _delete_local_news_image(row[0])
        finally:
            conn.close()
        flash("Yangilik o'chirildi.", "success")
    except Exception:
        flash("O'chirishda xatolik yuz berdi.", "error")
    return redirect(url_for("admin.admin_yangiliklar"))


@admin_bp.route("/admin/vacancies")
@login_required
def admin_vacancies():
    from app import _require_admin, VACANCY_TYPE_LABELS
    _require_admin()
    from data import get_connection
    items = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, title, organization, vacancy_type, deadline, is_published "
                    "FROM vacancies ORDER BY created_at DESC, id DESC"
                )
                items = [{
                    "id": r[0], "title": r[1] or "", "organization": r[2] or "",
                    "vacancy_type": r[3] or "", "type_label": VACANCY_TYPE_LABELS.get(r[3], ""),
                    "deadline": str(r[4])[:10] if r[4] else "", "is_published": r[5],
                } for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        items = []
    return render_template("admin_vacancies.html", items=items)


@admin_bp.route("/admin/vacancies/add", methods=["GET", "POST"])
@login_required
def admin_vacancy_add():
    from app import _require_admin, _vacancy_form_values, VACANCY_TYPES
    _require_admin()
    from data import get_connection
    if request.method == "POST":
        v = _vacancy_form_values()
        if not v["title"] or not v["organization"]:
            flash("Sarlavha va tashkilot majburiy.", "error")
            return render_template("admin_vacancy_form.html", item=v, edit_mode=False,
                                   vacancy_types=VACANCY_TYPES)
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO vacancies (title, organization, location, specialty, "
                        "requirements, description, salary, contact_info, contact_url, "
                        "vacancy_type, deadline, is_published) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (v["title"], v["organization"], v["location"], v["specialty"],
                         v["requirements"], v["description"], v["salary"], v["contact_info"],
                         v["contact_url"], v["vacancy_type"], v["deadline"], v["is_published"])
                    )
                conn.commit()
            finally:
                conn.close()
            flash("Vakansiya qo'shildi!", "success")
        except Exception:
            flash("Vakansiya qo'shishda xatolik yuz berdi.", "error")
        return redirect(url_for("admin.admin_vacancies"))
    return render_template("admin_vacancy_form.html", item=None, edit_mode=False,
                           vacancy_types=VACANCY_TYPES)


@admin_bp.route("/admin/vacancies/edit/<int:id>", methods=["GET", "POST"])
@login_required
def admin_vacancy_edit(id):
    from app import _require_admin, _vacancy_form_values, _vacancy_from_row, VACANCY_TYPES
    _require_admin()
    from data import get_connection

    def _load():
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM vacancies WHERE id = %s", (id,))
                    row = cur.fetchone()
                    if row:
                        return _vacancy_from_row([d[0] for d in cur.description], row)
            finally:
                conn.close()
        except Exception:
            return None
        return None

    current = _load()
    if not current:
        abort(404)

    if request.method == "POST":
        v = _vacancy_form_values()
        if not v["title"] or not v["organization"]:
            flash("Sarlavha va tashkilot majburiy.", "error")
            v["id"] = id
            return render_template("admin_vacancy_form.html", item=v, edit_mode=True,
                                   vacancy_types=VACANCY_TYPES)
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE vacancies SET title=%s, organization=%s, location=%s, "
                        "specialty=%s, requirements=%s, description=%s, salary=%s, "
                        "contact_info=%s, contact_url=%s, vacancy_type=%s, deadline=%s, "
                        "is_published=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                        (v["title"], v["organization"], v["location"], v["specialty"],
                         v["requirements"], v["description"], v["salary"], v["contact_info"],
                         v["contact_url"], v["vacancy_type"], v["deadline"], v["is_published"], id)
                    )
                conn.commit()
            finally:
                conn.close()
            flash("Vakansiya yangilandi!", "success")
        except Exception:
            flash("Vakansiyani yangilashda xatolik yuz berdi.", "error")
        return redirect(url_for("admin.admin_vacancies"))

    return render_template("admin_vacancy_form.html", item=current, edit_mode=True,
                           vacancy_types=VACANCY_TYPES)


@admin_bp.route("/admin/vacancies/delete/<int:id>", methods=["POST"])
@login_required
def admin_vacancy_delete(id):
    from app import _require_admin
    _require_admin()
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM vacancies WHERE id = %s", (id,))
            conn.commit()
        finally:
            conn.close()
        flash("Vakansiya o'chirildi", "success")
    except Exception:
        flash("O'chirishda xatolik yuz berdi.", "error")
    return redirect(url_for("admin.admin_vacancies"))


@admin_bp.route("/admin/blog")
@login_required
def admin_blog():
    from app import _require_admin, BLOG_CATEGORIES
    _require_admin()
    from data import get_connection
    items = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, title, slug, category, views, is_published, created_at "
                    "FROM blog_posts ORDER BY created_at DESC, id DESC")
                items = [{
                    "id": r[0], "title": r[1] or "", "slug": r[2] or "", "category": r[3] or "",
                    "category_label": BLOG_CATEGORIES.get(r[3] or "", r[3] or ""),
                    "views": r[4] or 0, "is_published": r[5],
                    "created_at": str(r[6])[:16] if r[6] else "",
                } for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        items = []
    return render_template("admin_blog.html", items=items)


@admin_bp.route("/admin/blog/add", methods=["GET", "POST"])
@login_required
def admin_blog_add():
    from app import _require_admin, _blog_form_values, BLOG_CATEGORIES
    _require_admin()
    from data import get_connection
    if request.method == "POST":
        v = _blog_form_values()
        if not v["title"] or not v["content"]:
            flash("Sarlavha va to'liq matn majburiy.", "error")
            return render_template("admin_blog_form.html", item=v, edit_mode=False, categories=BLOG_CATEGORIES)
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO blog_posts (title, slug, summary, content, category, image_url, is_published) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (v["title"], v["slug"], v["summary"], v["content"], v["category"],
                         v["image_url"], v["is_published"]))
                conn.commit()
                flash("Maqola qo'shildi!", "success")
            finally:
                conn.close()
        except Exception:
            flash("Saqlashda xatolik (slug takrorlangan bo'lishi mumkin).", "error")
            return render_template("admin_blog_form.html", item=v, edit_mode=False, categories=BLOG_CATEGORIES)
        return redirect(url_for("admin.admin_blog"))
    return render_template("admin_blog_form.html", item=None, edit_mode=False, categories=BLOG_CATEGORIES)


@admin_bp.route("/admin/blog/edit/<int:id>", methods=["GET", "POST"])
@login_required
def admin_blog_edit(id):
    from app import _require_admin, _blog_form_values, BLOG_CATEGORIES
    _require_admin()
    from data import get_connection

    def _load():
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, title, slug, summary, content, category, image_url, is_published "
                        "FROM blog_posts WHERE id = %s", (id,))
                    r = cur.fetchone()
                    if r:
                        return {"id": r[0], "title": r[1] or "", "slug": r[2] or "",
                                "summary": r[3] or "", "content": r[4] or "", "category": r[5] or "",
                                "image_url": r[6] or "", "is_published": r[7]}
            finally:
                conn.close()
        except Exception:
            return None
        return None

    current = _load()
    if not current:
        abort(404)
    if request.method == "POST":
        v = _blog_form_values()
        if not v["title"] or not v["content"]:
            flash("Sarlavha va to'liq matn majburiy.", "error")
            v["id"] = id
            return render_template("admin_blog_form.html", item=v, edit_mode=True, categories=BLOG_CATEGORIES)
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE blog_posts SET title=%s, slug=%s, summary=%s, content=%s, category=%s, "
                        "image_url=%s, is_published=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                        (v["title"], v["slug"], v["summary"], v["content"], v["category"],
                         v["image_url"], v["is_published"], id))
                conn.commit()
                flash("Maqola yangilandi!", "success")
            finally:
                conn.close()
        except Exception:
            flash("Yangilashda xatolik (slug takrorlangan bo'lishi mumkin).", "error")
            v["id"] = id
            return render_template("admin_blog_form.html", item=v, edit_mode=True, categories=BLOG_CATEGORIES)
        return redirect(url_for("admin.admin_blog"))
    return render_template("admin_blog_form.html", item=current, edit_mode=True, categories=BLOG_CATEGORIES)


@admin_bp.route("/admin/blog/delete/<int:id>", methods=["POST"])
@login_required
def admin_blog_delete(id):
    from app import _require_admin
    _require_admin()
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM blog_posts WHERE id = %s", (id,))
            conn.commit()
            flash("Maqola o'chirildi.", "success")
        finally:
            conn.close()
    except Exception:
        flash("O'chirishda xatolik.", "error")
    return redirect(url_for("admin.admin_blog"))


@admin_bp.route('/admin/survey')
@login_required
def admin_survey():
    from app import _require_admin
    _require_admin()
    from data import get_connection
    total_responses = 0
    total_participants = 0
    yes_pct = 0
    questions = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM survey_responses")
                total_responses = cur.fetchone()[0] or 0
                cur.execute("SELECT COUNT(DISTINCT ip_address) FROM survey_responses")
                total_participants = cur.fetchone()[0] or 0
                cur.execute("SELECT COUNT(*) FROM survey_responses WHERE answer = 'ha'")
                yes_total = cur.fetchone()[0] or 0
                yes_pct = round(yes_total / total_responses * 100, 1) if total_responses else 0

                cur.execute("""
                    SELECT q.id, q.question_text, q.question_group, q.question_order,
                           COUNT(r.id) AS total_responses,
                           SUM(CASE WHEN r.answer = 'ha' THEN 1 ELSE 0 END) AS yes_count,
                           SUM(CASE WHEN r.answer = 'yoq' THEN 1 ELSE 0 END) AS no_count,
                           SUM(CASE WHEN r.answer = 'custom' THEN 1 ELSE 0 END) AS custom_count
                    FROM survey_questions q
                    LEFT JOIN survey_responses r ON q.id = r.question_id
                    GROUP BY q.id, q.question_text, q.question_group, q.question_order
                    ORDER BY q.question_group, q.question_order
                """)
                rows = cur.fetchall()
                for r in rows:
                    qid, qtext, qgroup = r[0], r[1], r[2]
                    total = r[4] or 0
                    yc, nc, cc = r[5] or 0, r[6] or 0, r[7] or 0
                    custom_answers = []
                    if cc:
                        cur.execute("""
                            SELECT custom_text, username, ip_address, created_at
                            FROM survey_responses
                            WHERE question_id = %s AND answer = 'custom'
                            AND custom_text IS NOT NULL AND custom_text <> ''
                            ORDER BY created_at DESC
                        """, (qid,))
                        custom_answers = [{
                            "custom_text": cr[0], "username": cr[1] or "",
                            "ip_address": cr[2] or "", "created_at": cr[3],
                        } for cr in cur.fetchall()]
                    questions.append({
                        "id": qid, "question_text": qtext, "question_group": qgroup,
                        "total_responses": total,
                        "yes_count": yc, "no_count": nc, "custom_count": cc,
                        "yes_pct": round(yc / total * 100, 1) if total else 0,
                        "no_pct": round(nc / total * 100, 1) if total else 0,
                        "custom_pct": round(cc / total * 100, 1) if total else 0,
                        "custom_answers": custom_answers,
                    })
        finally:
            conn.close()
    except Exception:
        questions = []
    # Group questions for the template
    grouped = {}
    for q in questions:
        grouped.setdefault(q["question_group"], []).append(q)
    grouped = dict(sorted(grouped.items()))
    return render_template('admin_survey.html',
        total_responses=total_responses, total_participants=total_participants,
        yes_pct=yes_pct, grouped_questions=grouped)


@admin_bp.route('/admin/universities')
@login_required
def admin_universities():
    from app import (_require_admin, get_university_dissertation_stats,
                     _UNI_TYPE_LABELS)
    _require_admin()
    from data import get_connection
    items = []
    try:
        stats = get_university_dissertation_stats()
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, university_type, city, region, logo_url, is_active "
                            "FROM universities ORDER BY name")
                for r in cur.fetchall():
                    items.append({"id": r[0], "name": r[1] or "", "university_type": r[2] or "",
                                  "type_label": _UNI_TYPE_LABELS.get(r[2], r[2] or ""),
                                  "city": r[3] or "", "region": r[4] or "", "logo_url": r[5] or "",
                                  "is_active": r[6], "diss_count": stats.get(r[0], {}).get('total', 0)})
        finally:
            conn.close()
    except Exception:
        items = []
    return render_template('admin_universities.html', items=items)


@admin_bp.route('/admin/university/add', methods=['GET', 'POST'])
@login_required
def admin_university_add():
    from app import (_require_admin, _uni_form_values, _save_university_logo,
                     _UNI_EDIT_FIELDS, cache)
    _require_admin()
    from data import get_connection
    if request.method == 'POST':
        vals = _uni_form_values()
        if not vals.get('name'):
            flash("Nomi majburiy.", "error")
            return render_template('admin_university_form.html', item=vals, edit_mode=False)
        logo = _save_university_logo()
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cols = _UNI_EDIT_FIELDS + (['logo_url'] if logo else [])
                    placeholders = ", ".join(["%s"] * len(cols))
                    args = [vals[f] for f in _UNI_EDIT_FIELDS] + ([logo] if logo else [])
                    cur.execute(
                        f"INSERT INTO universities ({', '.join(cols)}) VALUES ({placeholders}) "
                        f"ON CONFLICT (name) DO NOTHING", args)
                conn.commit()
            finally:
                conn.close()
            cache.delete('university_stats')
            flash("Universitet qo'shildi!", "success")
        except Exception:
            flash("Qo'shishda xatolik yuz berdi.", "error")
        return redirect(url_for('admin.admin_universities'))
    return render_template('admin_university_form.html', item=None, edit_mode=False)


@admin_bp.route('/admin/university/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def admin_university_edit(id):
    from app import (_require_admin, _uni_form_values, _save_university_logo,
                     _UNI_EDIT_FIELDS, _uni_row_to_dict, cache)
    _require_admin()
    from data import get_connection

    def _load():
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM universities WHERE id = %s", (id,))
                    row = cur.fetchone()
                    if row:
                        return _uni_row_to_dict([c[0] for c in cur.description], row)
            finally:
                conn.close()
        except Exception:
            return None
        return None

    current = _load()
    if not current:
        abort(404)
    if request.method == 'POST':
        vals = _uni_form_values()
        if not vals.get('name'):
            flash("Nomi majburiy.", "error")
            vals['id'] = id
            return render_template('admin_university_form.html', item=vals, edit_mode=True)
        logo = _save_university_logo()
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cols = list(_UNI_EDIT_FIELDS)
                    args = [vals[f] for f in _UNI_EDIT_FIELDS]
                    if logo:
                        cols.append('logo_url')
                        args.append(logo)
                    set_clause = ", ".join(f"{c} = %s" for c in cols) + ", updated_at = NOW()"
                    cur.execute(f"UPDATE universities SET {set_clause} WHERE id = %s", args + [id])
                conn.commit()
            finally:
                conn.close()
            cache.delete('university_stats')
            flash("Universitet yangilandi!", "success")
        except Exception:
            flash("Yangilashda xatolik yuz berdi.", "error")
        return redirect(url_for('admin.admin_universities'))
    return render_template('admin_university_form.html', item=current, edit_mode=True)


@admin_bp.route('/admin/university/logo/<int:id>', methods=['POST'])
@login_required
def admin_university_logo(id):
    from app import _require_admin, _save_university_logo
    _require_admin()
    from data import get_connection
    logo = _save_university_logo()
    if not logo:
        flash("Rasm yuklanmadi (JPG/PNG/WEBP/SVG).", "error")
        return redirect(request.referrer or url_for('admin.admin_universities'))
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE universities SET logo_url = %s, updated_at = NOW() WHERE id = %s",
                            (logo, id))
            conn.commit()
        finally:
            conn.close()
        flash("Logo yuklandi!", "success")
    except Exception:
        flash("Logo saqlashda xatolik.", "error")
    return redirect(request.referrer or url_for('admin.admin_universities'))


@admin_bp.route('/admin/university/gallery/<int:id>', methods=['POST'])
@login_required
def admin_university_gallery_add(id):
    """Upload one or more gallery images for a university."""
    from app import _require_admin
    _require_admin()
    from data import get_connection
    from werkzeug.utils import secure_filename
    import time as _time
    from flask import current_app
    files = request.files.getlist('images')
    caption = (request.form.get('caption') or '').strip() or None
    upload_dir = os.path.join(current_app.static_folder, "uploads", "university_gallery")
    os.makedirs(upload_dir, exist_ok=True)
    saved_urls = []
    for f in files:
        if not f or not f.filename:
            continue
        fname = secure_filename(f.filename)
        ext = os.path.splitext(fname)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            continue
        saved = f"{id}_{int(_time.time()*1000)}_{fname}"
        try:
            f.save(os.path.join(upload_dir, saved))
            saved_urls.append(f"/static/uploads/university_gallery/{saved}")
        except Exception:
            continue
    if not saved_urls:
        flash("Rasm yuklanmadi (JPG/PNG/WEBP).", "error")
        return redirect(request.referrer or url_for('admin.admin_universities'))
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                for u in saved_urls:
                    cur.execute("INSERT INTO university_images (university_id, image_url, caption) "
                                "VALUES (%s, %s, %s)", (id, u, caption))
            conn.commit()
        finally:
            conn.close()
        flash(f"{len(saved_urls)} ta rasm yuklandi!", "success")
    except Exception:
        flash("Galereya saqlashda xatolik.", "error")
    return redirect(request.referrer or url_for('admin.admin_universities'))


@admin_bp.route('/admin/university/gallery/delete/<int:id>', methods=['POST'])
@login_required
def admin_university_gallery_delete(id):
    """Delete a single gallery image (and its file)."""
    from app import _require_admin
    _require_admin()
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT image_url FROM university_images WHERE id = %s", (id,))
                row = cur.fetchone()
                if row and row[0] and row[0].startswith('/static/uploads/university_gallery/'):
                    try:
                        os.remove(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                '..', row[0].lstrip('/')))
                    except Exception:
                        pass
                cur.execute("DELETE FROM university_images WHERE id = %s", (id,))
            conn.commit()
        finally:
            conn.close()
        flash("Rasm o'chirildi.", "success")
    except Exception:
        flash("O'chirishda xatolik.", "error")
    return redirect(request.referrer or url_for('admin.admin_universities'))


@admin_bp.route('/admin/university/delete/<int:id>', methods=['POST'])
@login_required
def admin_university_delete(id):
    from app import _require_admin, cache
    _require_admin()
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM universities WHERE id = %s", (id,))
            conn.commit()
        finally:
            conn.close()
        cache.delete('university_stats')
        flash("Universitet o'chirildi.", "success")
    except Exception:
        flash("O'chirishda xatolik.", "error")
    return redirect(url_for('admin.admin_universities'))


# ── Institution rename + merge-on-collision ─────────────────────────────────
# Admins rename any canonical institution in institution_map. Renaming to a name
# that already exists MERGES the two groups: every raw variant of the old group
# repoints to the one canonical string. Each change is logged in
# institution_renames (with the moved variant list) and is fully reversible.

# Canonical groups with their variant + dissertation counts. Grouping key is the
# same COALESCE the /universities directory uses, so counts stay in lock-step.
_INST_GROUPS_SQL = """
    SELECT COALESCE(im.canonical_name, im.cyrillic_name) AS canon,
           COUNT(DISTINCT im.cyrillic_name)              AS variant_count,
           COUNT(d.id)                                   AS diss_count
    FROM institution_map im
    LEFT JOIN dissertations d ON TRIM(d.muassasa) = im.cyrillic_name
    WHERE im.is_active = TRUE
    GROUP BY canon
"""


def _inst_group_exists(cur, canonical):
    cur.execute("SELECT 1 FROM institution_map "
                "WHERE COALESCE(canonical_name, cyrillic_name) = %s AND is_active = TRUE "
                "LIMIT 1", (canonical,))
    return cur.fetchone() is not None


def _inst_group_diss_count(cur, canonical):
    cur.execute("SELECT COUNT(d.id) FROM institution_map im "
                "LEFT JOIN dissertations d ON TRIM(d.muassasa) = im.cyrillic_name "
                "WHERE im.is_active = TRUE "
                "AND COALESCE(im.canonical_name, im.cyrillic_name) = %s", (canonical,))
    r = cur.fetchone()
    return (r[0] or 0) if r else 0


@admin_bp.route('/admin/institutions')
@login_required
def admin_institutions():
    from app import _require_admin, transliterate
    _require_admin()
    from data import get_connection
    q = (request.args.get('q') or '').strip()
    page = request.args.get('page', 1, type=int)
    if page < 1:
        page = 1
    per_page = 50

    groups, recent = [], []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(_INST_GROUPS_SQL)
                groups = [{'name': r[0], 'variant_count': r[1] or 0,
                           'diss_count': r[2] or 0} for r in cur.fetchall()]
                cur.execute(
                    "SELECT id, old_name, new_name, was_merge, moved_variants, "
                    "admin_username, created_at FROM institution_renames "
                    "ORDER BY id DESC LIMIT 20")
                for r in cur.fetchall():
                    mv = r[4] if isinstance(r[4], list) else []
                    recent.append({
                        'id': r[0], 'old_name': r[1], 'new_name': r[2],
                        'was_merge': r[3], 'variant_count': len(mv),
                        'admin_username': r[5] or '',
                        'created_at': str(r[6])[:16] if r[6] else ''})
        finally:
            conn.close()
    except Exception:
        groups, recent = [], []

    # Search matches in both scripts via the site's Cyrillic→Latin helper: fold
    # the query and each name to Latin so Latin input finds Cyrillic names.
    if q:
        ql = transliterate(q).lower()
        groups = [g for g in groups if ql in transliterate(g['name']).lower()]
    groups.sort(key=lambda g: (-g['diss_count'], (g['name'] or '').lower()))

    total = len(groups)
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
    start = (page - 1) * per_page
    items = groups[start:start + per_page]

    return render_template('admin_institutions.html', items=items, q=q,
                           page=page, total_pages=total_pages, total=total,
                           recent=recent)


@admin_bp.route('/admin/institutions/rename', methods=['POST'])
@login_required
def admin_institutions_rename():
    from urllib.parse import quote
    from app import _require_admin, cache
    from institutions import transliterate as _translit
    _require_admin()
    from data import get_connection
    from psycopg2.extras import Json

    data = request.get_json(silent=True) or {}
    old_name = (data.get('old_name') or '').strip()
    new_name = (data.get('new_name') or '').strip()
    confirm = bool(data.get('confirm'))

    if not old_name or not new_name:
        return jsonify(ok=False, error="Nom bo'sh bo'lishi mumkin emas."), 400
    if new_name == old_name:
        return jsonify(ok=False, status='noop', message="Nom o'zgarmadi."), 200

    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                if not _inst_group_exists(cur, old_name):
                    return jsonify(ok=False, error="Institut topilmadi."), 404
                is_merge = _inst_group_exists(cur, new_name)

                # Merge needs an explicit confirm from the UI with the sums shown.
                if is_merge and not confirm:
                    x = _inst_group_diss_count(cur, old_name)
                    y = _inst_group_diss_count(cur, new_name)
                    msg = ("«%s» «%s» bilan birlashtiriladi: %d + %d = %d ta "
                           "dissertatsiya. Davom etasizmi?"
                           % (old_name, new_name, x, y, x + y))
                    return jsonify(ok=True, status='confirm', merge=True,
                                   message=msg), 200

                # Lock the source group's rows, then repoint them to new_name.
                cur.execute(
                    "SELECT cyrillic_name FROM institution_map "
                    "WHERE COALESCE(canonical_name, cyrillic_name) = %s AND is_active = TRUE "
                    "FOR UPDATE", (old_name,))
                moved = [r[0] for r in cur.fetchall()]
                if not moved:
                    return jsonify(ok=False, error="Institut topilmadi."), 404
                cur.execute(
                    "UPDATE institution_map SET canonical_name = %s, latin_name = %s "
                    "WHERE cyrillic_name = ANY(%s)",
                    (new_name, _translit(new_name), moved))
                cur.execute(
                    "INSERT INTO institution_renames "
                    "(old_name, new_name, was_merge, moved_variants, admin_username) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (old_name, new_name, is_merge, Json(moved),
                     getattr(current_user, 'username', '') or ''))
            conn.commit()
        finally:
            conn.close()
        cache.delete('institution_directory')
        cache.delete('university_stats')
        return jsonify(ok=True, status=('merged' if is_merge else 'renamed'),
                       new_name=new_name,
                       redirect='/university/' + quote(new_name, safe='')), 200
    except Exception:
        return jsonify(ok=False, error="Saqlashda xatolik yuz berdi."), 500


@admin_bp.route('/admin/institutions/undo/<int:rename_id>', methods=['POST'])
@login_required
def admin_institutions_undo(rename_id):
    from app import _require_admin, cache
    from institutions import transliterate as _translit
    _require_admin()
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT old_name, moved_variants FROM institution_renames "
                            "WHERE id = %s FOR UPDATE", (rename_id,))
                row = cur.fetchone()
                if not row:
                    flash("O'zgarish topilmadi.", "error")
                    return redirect(url_for('admin.admin_institutions'))
                old_name = row[0]
                moved = row[1] if isinstance(row[1], list) else []
                if moved:
                    # Restore the previous mapping; un-merges a merge.
                    cur.execute("SELECT 1 FROM institution_map "
                                "WHERE cyrillic_name = ANY(%s) FOR UPDATE", (moved,))
                    cur.execute(
                        "UPDATE institution_map SET canonical_name = %s, latin_name = %s "
                        "WHERE cyrillic_name = ANY(%s)",
                        (old_name, _translit(old_name), moved))
                cur.execute("DELETE FROM institution_renames WHERE id = %s", (rename_id,))
            conn.commit()
        finally:
            conn.close()
        cache.delete('institution_directory')
        cache.delete('university_stats')
        flash("«%s» qayta tiklandi." % old_name, "success")
    except Exception:
        flash("Bekor qilishda xatolik.", "error")
    return redirect(url_for('admin.admin_institutions'))


@admin_bp.route('/admin/journals')
@login_required
def admin_journals():
    from app import _require_admin
    _require_admin()
    from data import get_connection
    items = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT j.id, j.name, j.indexing, j.country, j.is_active, j.is_predatory,
                           j.logo_url, j.oak_approved, j.scopus_indexed, j.wos_indexed,
                           COALESCE(string_agg(DISTINCT js.specialty_code, ', ' ORDER BY js.specialty_code), '')
                    FROM journals j
                    LEFT JOIN journal_specialties js ON js.journal_id = j.id
                    GROUP BY j.id ORDER BY LOWER(j.name)
                """)
                items = [{"id": r[0], "name": r[1] or "", "indexing": r[2] or "",
                          "country": r[3] or "", "is_active": r[4], "is_predatory": r[5],
                          "logo_url": r[6] or "", "oak_approved": r[7], "scopus_indexed": r[8],
                          "wos_indexed": r[9], "codes": r[10] or ""} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        items = []
    return render_template('admin_journals.html', items=items)


@admin_bp.route('/admin/journals/add', methods=['GET', 'POST'])
@login_required
def admin_journal_add():
    from app import (_require_admin, _journal_form_values, _save_journal_logo,
                     _save_journal_specialties, _JOURNAL_COLS, SPECIALTY_NAMES)
    _require_admin()
    from data import get_connection
    if request.method == 'POST':
        vals = _journal_form_values()
        if not vals.get('name'):
            flash("Nomi majburiy.", "error")
            return render_template('admin_journal_form.html', item=vals, edit_mode=False,
                                   specialty_names=SPECIALTY_NAMES,
                                   selected_codes=request.form.getlist('specialty_codes'))
        logo = _save_journal_logo()
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cols = list(_JOURNAL_COLS) + (['logo_url'] if logo else [])
                    placeholders = ", ".join(["%s"] * len(cols))
                    args = [vals[f] for f in _JOURNAL_COLS] + ([logo] if logo else [])
                    cur.execute(
                        f"INSERT INTO journals ({', '.join(cols)}) VALUES ({placeholders}) "
                        f"ON CONFLICT (name) DO NOTHING RETURNING id", args)
                    row = cur.fetchone()
                    if row:
                        jid = row[0]
                    else:
                        cur.execute("SELECT id FROM journals WHERE name = %s", (vals['name'],))
                        jid = (cur.fetchone() or [None])[0]
                    if jid:
                        _save_journal_specialties(cur, jid)
                conn.commit()
            finally:
                conn.close()
            flash("Jurnal qo'shildi!", "success")
        except Exception:
            flash("Qo'shishda xatolik yuz berdi.", "error")
        return redirect(url_for('admin.admin_journals'))
    return render_template('admin_journal_form.html', item=None, edit_mode=False,
                           specialty_names=SPECIALTY_NAMES, selected_codes=[])


@admin_bp.route('/admin/journals/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def admin_journal_edit(id):
    from app import (_require_admin, _journal_form_values, _journal_row,
                     _save_journal_logo, _save_journal_specialties,
                     _JOURNAL_COLS, SPECIALTY_NAMES)
    _require_admin()
    from data import get_connection

    def _load():
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM journals WHERE id = %s", (id,))
                    row = cur.fetchone()
                    if row:
                        return _journal_row([c[0] for c in cur.description], row)
            finally:
                conn.close()
        except Exception:
            return None
        return None

    current = _load()
    if not current:
        abort(404)

    def _codes_for(jid):
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT specialty_code FROM journal_specialties WHERE journal_id = %s",
                                (jid,))
                    return [r[0] for r in cur.fetchall()]
            finally:
                conn.close()
        except Exception:
            return []

    if request.method == 'POST':
        vals = _journal_form_values()
        if not vals.get('name'):
            flash("Nomi majburiy.", "error")
            vals['id'] = id
            return render_template('admin_journal_form.html', item=vals, edit_mode=True,
                                   specialty_names=SPECIALTY_NAMES,
                                   selected_codes=request.form.getlist('specialty_codes'))
        logo = _save_journal_logo()
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cols = list(_JOURNAL_COLS)
                    args = [vals[f] for f in _JOURNAL_COLS]
                    if logo:
                        cols.append('logo_url')
                        args.append(logo)
                    set_clause = ", ".join(f"{c} = %s" for c in cols) + ", updated_at = NOW()"
                    cur.execute(f"UPDATE journals SET {set_clause} WHERE id = %s", args + [id])
                    _save_journal_specialties(cur, id)
                conn.commit()
            finally:
                conn.close()
            flash("Jurnal yangilandi!", "success")
        except Exception:
            flash("Yangilashda xatolik yuz berdi.", "error")
        return redirect(url_for('admin.admin_journals'))
    return render_template('admin_journal_form.html', item=current, edit_mode=True,
                           specialty_names=SPECIALTY_NAMES, selected_codes=_codes_for(id))


@admin_bp.route('/admin/journals/logo/<int:id>', methods=['POST'])
@login_required
def admin_journal_logo(id):
    from app import _require_admin, _save_journal_logo
    _require_admin()
    from data import get_connection
    logo = _save_journal_logo()
    if not logo:
        flash("Rasm yuklanmadi.", "error")
        return redirect(request.referrer or url_for('admin.admin_journals'))
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE journals SET logo_url = %s, updated_at = NOW() WHERE id = %s",
                            (logo, id))
            conn.commit()
        finally:
            conn.close()
        flash("Logo yuklandi!", "success")
    except Exception:
        flash("Logo saqlashda xatolik.", "error")
    return redirect(request.referrer or url_for('admin.admin_journals'))


@admin_bp.route('/admin/journals/delete/<int:id>', methods=['POST'])
@login_required
def admin_journal_delete(id):
    from app import _require_admin
    _require_admin()
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM journals WHERE id = %s", (id,))
            conn.commit()
        finally:
            conn.close()
        flash("Jurnal o'chirildi.", "success")
    except Exception:
        flash("O'chirishda xatolik.", "error")
    return redirect(url_for('admin.admin_journals'))
