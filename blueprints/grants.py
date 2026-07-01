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

from flask import (Blueprint, jsonify, request, render_template,
                   render_template_string, redirect, abort, flash)
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


# ── Admin: grant management (username == 'admin' only) ──────────────────────

_ADMIN_GRANTS_TEMPLATE = """{% extends "base.html" %}
{% block title %}Grantlar (admin){% endblock %}
{% block content %}
<div class="content-card" style="max-width:1100px;margin:0 auto;">
  <h1 style="margin-bottom:4px;">🏆 Grantlarni boshqarish</h1>
  <p style="color:#94a3b8;">Jami: {{ grants|length }} ta grant</p>

  {% with msgs = get_flashed_messages() %}
    {% for m in msgs %}<div class="alert-inline success">{{ m }}</div>{% endfor %}
  {% endwith %}

  <h2 style="font-size:1.05rem;margin-top:18px;">
    {% if edit_g %}✏️ Grantni tahrirlash (#{{ edit_g.id }}){% else %}➕ Yangi grant qo'shish{% endif %}
  </h2>
  <form method="POST"
        action="{% if edit_g %}/admin/grants/edit/{{ edit_g.id }}{% else %}/admin/grants{% endif %}"
        style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:24px;">
    {{ csrf_token()|safe }}
    <label style="grid-column:1/-1;">Sarlavha (title)
      <input name="title" class="form-control" required value="{{ edit_g.title if edit_g else '' }}"></label>
    <label style="grid-column:1/-1;">Tavsif (description)
      <textarea name="description" class="form-control" rows="3">{{ edit_g.description if edit_g else '' }}</textarea></label>
    <label>Davlat (country)
      <input name="country" class="form-control" value="{{ edit_g.country if edit_g else '' }}"></label>
    <label>Provayder (provider)
      <input name="provider" class="form-control" value="{{ edit_g.provider if edit_g else '' }}"></label>
    <label>Moliyalashtirish (funding_type)
      <select name="funding_type" class="form-control">
        <option value="">—</option>
        {% for f in funding_types %}<option value="{{ f }}" {{ 'selected' if edit_g and edit_g.funding_type == f }}>{{ f }}</option>{% endfor %}
      </select></label>
    <label>Daraja (academic_level)
      <select name="academic_level" class="form-control">
        <option value="">—</option>
        {% for l in academic_levels %}<option value="{{ l }}" {{ 'selected' if edit_g and edit_g.academic_level == l }}>{{ l }}</option>{% endfor %}
      </select></label>
    <label>Muddat (application_deadline)
      <input name="application_deadline" type="date" class="form-control" value="{{ edit_g.application_deadline if edit_g else '' }}"></label>
    <label>Mutaxassislik shifrlari (scientific_codes)
      <input name="scientific_codes" class="form-control" placeholder="05.01.01, 05.01.02" value="{{ edit_g.scientific_codes if edit_g else '' }}"></label>
    <label style="grid-column:1/-1;">Manba havolasi (source_url)
      <input name="source_url" class="form-control" value="{{ edit_g.source_url if edit_g else '' }}"></label>
    <label style="grid-column:1/-1;">requirements_json (JSON)
      <textarea name="requirements_json" class="form-control" rows="3" placeholder='{"documents": [...], "strategy": [...]}'>{{ edit_g.requirements_raw if edit_g else '' }}</textarea></label>
    <div style="grid-column:1/-1;">
      <button type="submit" class="btn btn-primary">{% if edit_g %}Saqlash{% else %}Qo'shish{% endif %}</button>
      {% if edit_g %}<a href="/admin/grants" class="btn btn-action">Bekor qilish</a>{% endif %}
    </div>
  </form>

  <table class="table" style="width:100%;">
    <thead><tr>
      <th>ID</th><th>Sarlavha</th><th>Davlat</th><th>Moliya</th><th>Daraja</th><th>Muddat</th><th></th>
    </tr></thead>
    <tbody>
      {% for g in grants %}
      <tr>
        <td>{{ g.id }}</td>
        <td><a href="/grants/{{ g.id }}" target="_blank">{{ g.title }}</a></td>
        <td>{{ g.country or '' }}</td>
        <td>{{ g.funding_type or '' }}</td>
        <td>{{ g.academic_level or '' }}</td>
        <td>{{ g.application_deadline or '' }}</td>
        <td style="white-space:nowrap;">
          <a href="/admin/grants?edit={{ g.id }}" class="btn btn-sm btn-action">Tahrirlash</a>
          <form method="POST" action="/admin/grants/delete/{{ g.id }}" style="display:inline;"
                onsubmit="return confirm('Grantni o‘chirishni tasdiqlaysizmi?');">
            {{ csrf_token()|safe }}
            <button type="submit" class="btn btn-sm" style="color:#f87171;">O'chirish</button>
          </form>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
"""


def _grant_form_values():
    """Reads grant fields from the submitted admin form; returns a values dict."""
    def g(name):
        return (request.form.get(name) or '').strip()
    raw_json = g('requirements_json')
    req = None
    if raw_json:
        try:
            req = json.dumps(json.loads(raw_json))
        except Exception:
            req = None  # invalid JSON — store NULL rather than crash
    return {
        'title': g('title'),
        'description': g('description'),
        'country': g('country') or None,
        'provider': g('provider') or None,
        'funding_type': g('funding_type') or None,
        'academic_level': g('academic_level') or None,
        'application_deadline': g('application_deadline') or None,
        'scientific_codes': g('scientific_codes') or None,
        'source_url': g('source_url') or None,
        'requirements_json': req,
    }


@grants_bp.route('/admin/grants', methods=['GET', 'POST'])
@login_required
def admin_grants():
    from app import _require_admin
    _require_admin()
    from data import get_connection

    if request.method == 'POST':
        v = _grant_form_values()
        if not v['title']:
            flash("Sarlavha kiritilishi shart.")
            return redirect('/admin/grants')
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    _ensure_schema(cur)
                    cur.execute("""
                        INSERT INTO grants (title, description, country, provider,
                            funding_type, academic_level, application_deadline,
                            scientific_codes, source_url, requirements_json)
                        VALUES (%(title)s, %(description)s, %(country)s, %(provider)s,
                            %(funding_type)s, %(academic_level)s, %(application_deadline)s,
                            %(scientific_codes)s, %(source_url)s, %(requirements_json)s)
                    """, v)
                conn.commit()
                flash("Yangi grant qo'shildi.")
            finally:
                conn.close()
        except Exception as e:
            flash("Xatolik: " + str(e))
        return redirect('/admin/grants')

    # GET — list + (optional) edit form
    edit_id = request.args.get('edit', type=int)
    grants, edit_g = [], None
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute("""
                    SELECT id, title, description, scientific_codes, country, funding_type,
                           academic_level, application_deadline, source_url, requirements_json,
                           provider
                    FROM grants ORDER BY id DESC
                """)
                cols = [c[0] for c in cur.description]
                for r in cur.fetchall():
                    d = _grant_row(cols, r)
                    grants.append(d)
                    if edit_id and d['id'] == edit_id:
                        d['requirements_raw'] = (json.dumps(d['requirements_json'],
                                                 ensure_ascii=False, indent=2)
                                                 if d['requirements_json'] else '')
                        edit_g = d
            conn.commit()
        finally:
            conn.close()
    except Exception:
        grants = []
    return render_template_string(_ADMIN_GRANTS_TEMPLATE, grants=grants, edit_g=edit_g,
                                  funding_types=FUNDING_TYPES, academic_levels=ACADEMIC_LEVELS)


@grants_bp.route('/admin/grants/edit/<int:id>', methods=['POST'])
@login_required
def admin_grants_edit(id):
    from app import _require_admin
    _require_admin()
    from data import get_connection
    v = _grant_form_values()
    v['id'] = id
    if not v['title']:
        flash("Sarlavha kiritilishi shart.")
        return redirect('/admin/grants?edit=%d' % id)
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE grants SET title=%(title)s, description=%(description)s,
                        country=%(country)s, provider=%(provider)s,
                        funding_type=%(funding_type)s, academic_level=%(academic_level)s,
                        application_deadline=%(application_deadline)s,
                        scientific_codes=%(scientific_codes)s, source_url=%(source_url)s,
                        requirements_json=%(requirements_json)s
                    WHERE id=%(id)s
                """, v)
            conn.commit()
            flash("Grant yangilandi.")
        finally:
            conn.close()
    except Exception as e:
        flash("Xatolik: " + str(e))
    return redirect('/admin/grants')


@grants_bp.route('/admin/grants/delete/<int:id>', methods=['POST'])
@login_required
def admin_grants_delete(id):
    from app import _require_admin
    _require_admin()
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM grants WHERE id = %s", (id,))
            conn.commit()
            flash("Grant o'chirildi.")
        finally:
            conn.close()
    except Exception as e:
        flash("Xatolik: " + str(e))
    return redirect('/admin/grants')
