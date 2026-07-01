"""Grants blueprint — Scientific Grants & Fellowships module.

Provides:
  - Lazy schema for `grants` and `user_tracked_grants`.
  - GET /api/v1/grants        — multi-select filterable listing (async grid feed).
  - GET /grants              — listing page (filter sidebar + async grid).
  - GET /grants/<int:id>     — dynamic detail view (checklist + strategy guide).
  - POST /api/v1/grants/track — mark a grant Interested / In Progress.
  - GET  /api/v1/grants/reminders — tracked grants within the 7-day deadline threshold
    (consumed by the global session modal for proactive reminders).

Schema is created lazily on first request (idempotent), matching blueprints/notifications.py.
Shared helpers are lazy-imported inside views to avoid circular imports.
"""
import json

from flask import Blueprint, jsonify, request, render_template, abort
from flask_login import login_required, current_user

from app import csrf

grants_bp = Blueprint('grants', __name__)

_schema_ready = False

FUNDING_TYPES = ('Full', 'Partial')
ACADEMIC_LEVELS = ('Master', 'PhD', 'Postdoc')


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
    cur.execute("CREATE INDEX IF NOT EXISTS idx_grants_deadline ON grants(application_deadline)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_tracked_grants (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            grant_id INTEGER NOT NULL REFERENCES grants(id) ON DELETE CASCADE,
            status VARCHAR(20) DEFAULT 'interested',
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE (user_id, grant_id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tracked_user ON user_tracked_grants(user_id)")
    _schema_ready = True


def _grant_row(cols, row):
    d = dict(zip(cols, row))
    dl = d.get('application_deadline')
    d['application_deadline'] = str(dl) if dl else ''
    req = d.get('requirements_json')
    if isinstance(req, str):
        try:
            req = json.loads(req)
        except Exception:
            req = {}
    d['requirements_json'] = req or {}
    d['codes'] = [c.strip() for c in (d.get('scientific_codes') or '').split(',') if c.strip()]
    return d


# ── Filterable listing API ──────────────────────────────────────────────────

@grants_bp.route('/api/v1/grants')
def api_grants():
    from data import get_connection
    codes = request.args.getlist('scientific_codes')
    countries = request.args.getlist('country')
    funding = [f for f in request.args.getlist('funding_type') if f in FUNDING_TYPES]
    levels = [l for l in request.args.getlist('academic_level') if l in ACADEMIC_LEVELS]

    where, params = ["1=1"], []
    if codes:
        # Match a grant if it lists ANY of the requested specialty codes.
        ors = []
        for c in codes:
            ors.append("scientific_codes ILIKE %s")
            params.append(f"%{c}%")
        where.append("(" + " OR ".join(ors) + ")")
    if countries:
        where.append("country = ANY(%s)")
        params.append(countries)
    if funding:
        where.append("funding_type = ANY(%s)")
        params.append(funding)
    if levels:
        where.append("academic_level = ANY(%s)")
        params.append(levels)

    items = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute(f"""
                    SELECT id, title, description, scientific_codes, country, funding_type,
                           academic_level, application_deadline, source_url, requirements_json,
                           provider
                    FROM grants
                    WHERE {' AND '.join(where)}
                    ORDER BY application_deadline ASC NULLS LAST, id DESC
                """, params)
                cols = [c[0] for c in cur.description]
                items = [_grant_row(cols, r) for r in cur.fetchall()]
            conn.commit()
        finally:
            conn.close()
    except Exception:
        items = []
    return jsonify({"ok": True, "grants": items, "count": len(items)})


# ── Pages ───────────────────────────────────────────────────────────────────

@grants_bp.route('/grants')
def grants_list():
    from data import get_connection
    countries = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute("SELECT DISTINCT country FROM grants WHERE country IS NOT NULL "
                            "AND country <> '' ORDER BY country")
                countries = [r[0] for r in cur.fetchall()]
            conn.commit()
        finally:
            conn.close()
    except Exception:
        countries = []
    return render_template('grants.html', countries=countries,
                           funding_types=FUNDING_TYPES, academic_levels=ACADEMIC_LEVELS)


@grants_bp.route('/grants/<int:id>')
def grant_detail(id):
    from data import get_connection
    grant = None
    tracked_status = None
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute("""
                    SELECT id, title, description, scientific_codes, country, funding_type,
                           academic_level, application_deadline, source_url, requirements_json,
                           provider
                    FROM grants WHERE id = %s
                """, (id,))
                row = cur.fetchone()
                if row:
                    grant = _grant_row([c[0] for c in cur.description], row)
                    if current_user.is_authenticated:
                        cur.execute("SELECT status FROM user_tracked_grants "
                                    "WHERE user_id = %s AND grant_id = %s",
                                    (current_user.id, id))
                        t = cur.fetchone()
                        tracked_status = t[0] if t else None
            conn.commit()
        finally:
            conn.close()
    except Exception:
        grant = None
    if not grant:
        abort(404)
    return render_template('grant_detail.html', g=grant, tracked_status=tracked_status)


# ── Retention tracking ──────────────────────────────────────────────────────

@grants_bp.route('/api/v1/grants/track', methods=['POST'])
@csrf.exempt
@login_required
def track_grant():
    from data import get_connection
    data = request.get_json(silent=True) or {}
    try:
        gid = int(data.get('grant_id'))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid grant_id"}), 400
    status = data.get('status')
    if status not in ('interested', 'in_progress', 'remove'):
        return jsonify({"ok": False, "error": "invalid status"}), 400
    uid = current_user.id
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                if status == 'remove':
                    cur.execute("DELETE FROM user_tracked_grants "
                                "WHERE user_id = %s AND grant_id = %s", (uid, gid))
                else:
                    cur.execute("""
                        INSERT INTO user_tracked_grants (user_id, grant_id, status)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (user_id, grant_id) DO UPDATE SET status = EXCLUDED.status
                    """, (uid, gid, status))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True, "status": status})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Proactive deadline reminders (global session modal feed) ────────────────

@grants_bp.route('/api/v1/grants/reminders')
@login_required
def grant_reminders():
    from data import get_connection
    uid = current_user.id
    items = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                # Tracked grants whose deadline is within the critical 7-day window.
                cur.execute("""
                    SELECT g.id, g.title,
                           (g.application_deadline - CURRENT_DATE) AS days_left
                    FROM user_tracked_grants t
                    JOIN grants g ON g.id = t.grant_id
                    WHERE t.user_id = %s
                      AND g.application_deadline IS NOT NULL
                      AND g.application_deadline >= CURRENT_DATE
                      AND g.application_deadline <= CURRENT_DATE + 7
                    ORDER BY g.application_deadline ASC
                """, (uid,))
                for gid, title, days in cur.fetchall():
                    items.append({
                        "id": gid, "title": title, "days_left": int(days),
                        "message": (f"Application Deadline Approaching: Your tracked grant "
                                    f"closes in {int(days)} days. Review your required "
                                    f"documents now."),
                    })
            conn.commit()
        finally:
            conn.close()
    except Exception:
        items = []
    return jsonify({"ok": True, "reminders": items})
