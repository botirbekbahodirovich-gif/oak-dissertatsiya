"""Smart Reminders blueprint — deadline-aware academic alerts (Smart Eslatmalar).

Reminds scholars about conferences, grants, journal submission windows and other
deadlines, N days before the date (configurable per reminder), over two channels:
  a) Site — a personal `user_alerts` row (consumed by the existing global
     notification modal in base.html, via blueprints/notifications.py).
  b) Telegram — Bot API sendMessage to cabinet_users.telegram_id.

Audience targeting uses the cabinet identity system (cabinet_users + olim_profiles,
NOT a parallel scheme on `users`): academic_degree / region / ixtisoslik live on
olim_profiles, telegram_id on cabinet_users. Site delivery bridges a cabinet user
to their main-site `users` row by e-mail (same bridge as cabinet.py).

Tables (lazy, idempotent — blueprints/notifications.py pattern):
  smart_reminders        — one row per reminder + array targeting columns.
  reminder_sends         — send log; user_id = cabinet_users.id. `days_before`
                           (-1 for manual "Hozir yuborish") participates in the
                           UNIQUE key so each reminder window fires exactly once
                           per user/channel, while later windows still fire.
  user_notification_prefs— per cabinet user on/off toggles per reminder type
                           and the Telegram channel (default: everything on).

Routes:
  GET/POST /admin/reminders*            — admin CRUD + "Hozir yuborish".
  POST /api/v1/reminders/process        — daily dispatch (admin session OR
                                          X-API-Key == REMINDERS_API_KEY; cron).
  GET  /api/v1/reminders/upcoming       — personalised feed for the widget.
  GET  /reminders                       — full "Yaqinlashayotgan muddatlar" page.
  GET/POST /cabinet/api/notification-prefs — cabinet toggles.
"""
import hmac
import os
from datetime import date

from flask import (Blueprint, jsonify, request, render_template, redirect,
                   flash, session)
from flask_login import login_required, current_user

from app import csrf

reminders_bp = Blueprint('reminders', __name__)

_schema_ready = False

# type key → (Uzbek label, widget icon)
REMINDER_TYPES = {
    'conference': ('Konferensiya', '🎤'),
    'grant':      ('Grant', '🏆'),
    'journal':    ('Jurnal', '📰'),
    'deadline':   ('Deadline', '⏰'),
    'custom':     ('Boshqa', '📅'),
}
DAYS_CHOICES = [30, 14, 7, 3, 1]
# reminder type → user_notification_prefs column; deadline/custom are always on.
_PREF_COL = {'conference': 'conference_reminders', 'grant': 'grant_reminders',
             'journal': 'journal_reminders'}
_MANUAL_SEND = -1  # days_before marker for admin "Hozir yuborish"


def _ensure_schema(cur):
    """Idempotently create the smart-reminder tables + indexes."""
    global _schema_ready
    if _schema_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS smart_reminders (
            id SERIAL PRIMARY KEY,
            title VARCHAR(500) NOT NULL,
            description TEXT,
            reminder_type VARCHAR(50) NOT NULL,
            deadline_date DATE,
            reminder_days_before INTEGER[] DEFAULT '{7, 3, 1}',
            target_degrees VARCHAR[] DEFAULT '{}',
            target_specializations VARCHAR[] DEFAULT '{}',
            target_regions VARCHAR[] DEFAULT '{}',
            url VARCHAR(500),
            is_active BOOLEAN DEFAULT TRUE,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_smart_reminders_deadline "
                "ON smart_reminders(deadline_date) WHERE is_active = TRUE")
    # Send log — user_id is cabinet_users.id (targeting data lives there).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS reminder_sends (
            id SERIAL PRIMARY KEY,
            reminder_id INTEGER NOT NULL REFERENCES smart_reminders(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL,
            days_before INTEGER NOT NULL DEFAULT -1,
            sent_at TIMESTAMP DEFAULT NOW(),
            channel VARCHAR(20) DEFAULT 'site',
            is_read BOOLEAN DEFAULT FALSE,
            UNIQUE (reminder_id, user_id, channel, days_before)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_reminder_sends_reminder "
                "ON reminder_sends(reminder_id)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_notification_prefs (
            user_id INTEGER PRIMARY KEY,
            conference_reminders BOOLEAN DEFAULT TRUE,
            grant_reminders BOOLEAN DEFAULT TRUE,
            journal_reminders BOOLEAN DEFAULT TRUE,
            telegram_enabled BOOLEAN DEFAULT TRUE,
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    _schema_ready = True


# ── matching helpers ─────────────────────────────────────────────────────────

def _norm(s):
    return (s or '').strip().lower()


def _fetch_audience(cur):
    """All cabinet users with their targeting attrs + notification prefs.

    One row per cabinet user (olim_profiles joined on cabinet_user_id, falling
    back to the claimed olim_name — same linkage the cabinet itself uses).
    """
    cur.execute("""
        SELECT DISTINCT ON (cu.id)
               cu.id, cu.email, cu.telegram_id,
               p.academic_degree, p.region, p.ixtisoslik,
               pr.conference_reminders, pr.grant_reminders,
               pr.journal_reminders, pr.telegram_enabled
        FROM cabinet_users cu
        LEFT JOIN olim_profiles p
               ON p.cabinet_user_id = cu.id
              OR (cu.olim_name IS NOT NULL AND TRIM(cu.olim_name) <> ''
                  AND LOWER(TRIM(p.olim_name)) = LOWER(TRIM(cu.olim_name)))
        LEFT JOIN user_notification_prefs pr ON pr.user_id = cu.id
        ORDER BY cu.id, p.id DESC NULLS LAST
    """)
    users = []
    for r in cur.fetchall():
        users.append({
            'id': r[0], 'email': r[1] or '', 'telegram_id': r[2],
            'degree': r[3] or '', 'region': r[4] or '', 'ixtisoslik': r[5] or '',
            'prefs': {
                'conference_reminders': r[6] if r[6] is not None else True,
                'grant_reminders': r[7] if r[7] is not None else True,
                'journal_reminders': r[8] if r[8] is not None else True,
                'telegram_enabled': r[9] if r[9] is not None else True,
            },
        })
    return users


def _matches(reminder, u):
    """Does cabinet user `u` fall inside the reminder's target audience?"""
    degs = reminder.get('target_degrees') or []
    if degs and _norm(u['degree']) not in {_norm(d) for d in degs}:
        return False
    specs = reminder.get('target_specializations') or []
    if specs and _norm(u['ixtisoslik']) not in {_norm(s) for s in specs}:
        return False
    regs = reminder.get('target_regions') or []
    if regs and _norm(u['region']) not in {_norm(r) for r in regs}:
        return False
    pref_col = _PREF_COL.get(reminder.get('reminder_type'))
    if pref_col and not u['prefs'].get(pref_col, True):
        return False
    return True


def _site_message(reminder):
    parts = []
    if reminder.get('deadline_date'):
        parts.append(f"📅 Muddat: {reminder['deadline_date']}")
    if reminder.get('description'):
        parts.append(f"📝 {reminder['description']}")
    if reminder.get('url'):
        parts.append(f"🔗 Batafsil: {reminder['url']}")
    return "\n".join(parts) or reminder.get('title') or ''


def _send_telegram(chat_id, reminder):
    """Telegram Bot API sendMessage; never raises (one bad user must not stop the run)."""
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    if not token or not chat_id:
        return False
    lines = [f"🔔 Eslatma: {reminder.get('title') or ''}"]
    if reminder.get('deadline_date'):
        lines.append(f"📅 Muddat: {reminder['deadline_date']}")
    if reminder.get('description'):
        lines.append(f"📝 {reminder['description']}")
    if reminder.get('url'):
        lines.append(f"🔗 Batafsil: {reminder['url']}")
    lines.append("")
    lines.append("olimlar.uz")
    try:
        import requests
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": "\n".join(lines),
                  "disable_web_page_preview": True},
            timeout=8)
        return bool(r.ok and (r.json() or {}).get('ok'))
    except Exception:
        return False


def _dispatch(cur, reminder, days_before):
    """Send `reminder` to every matching cabinet user who hasn't received this
    window yet. Returns number of channel-sends performed."""
    users = [u for u in _fetch_audience(cur) if _matches(reminder, u)]
    if not users:
        return 0
    # Already-sent (this reminder + window) pairs, per channel.
    cur.execute("SELECT user_id, channel FROM reminder_sends "
                "WHERE reminder_id = %s AND days_before = %s",
                (reminder['id'], days_before))
    already = {(r[0], r[1]) for r in cur.fetchall()}
    # Bridge cabinet e-mails → main-site users.id for site notifications.
    emails = [u['email'].lower() for u in users if u['email']]
    site_uid = {}
    if emails:
        cur.execute("SELECT LOWER(email), id FROM users WHERE LOWER(email) = ANY(%s)",
                    (emails,))
        site_uid = {r[0]: r[1] for r in cur.fetchall()}
    sent = 0
    title = f"🔔 {reminder.get('title') or ''}"
    message = _site_message(reminder)
    for u in users:
        # Site channel — needs a linked main-site account (the modal is
        # Flask-Login gated); goes into user_alerts like any personal alert.
        main_uid = site_uid.get(u['email'].lower()) if u['email'] else None
        if main_uid and (u['id'], 'site') not in already:
            cur.execute("""
                INSERT INTO user_alerts (user_id, title, message, level)
                VALUES (%s, %s, %s, 'info')
            """, (main_uid, title, message))
            cur.execute("""
                INSERT INTO reminder_sends (reminder_id, user_id, days_before, channel)
                VALUES (%s, %s, %s, 'site')
                ON CONFLICT (reminder_id, user_id, channel, days_before) DO NOTHING
            """, (reminder['id'], u['id'], days_before))
            sent += 1
        # Telegram channel — opt-out via prefs, requires a linked telegram_id.
        if (u['telegram_id'] and u['prefs'].get('telegram_enabled', True)
                and (u['id'], 'telegram') not in already):
            if _send_telegram(u['telegram_id'], reminder):
                cur.execute("""
                    INSERT INTO reminder_sends (reminder_id, user_id, days_before, channel)
                    VALUES (%s, %s, %s, 'telegram')
                    ON CONFLICT (reminder_id, user_id, channel, days_before) DO NOTHING
                """, (reminder['id'], u['id'], days_before))
                sent += 1
    return sent


def _reminder_row(cols, row):
    d = dict(zip(cols, row))
    dl = d.get('deadline_date')
    d['deadline_date'] = str(dl) if dl else ''
    for f in ('reminder_days_before', 'target_degrees',
              'target_specializations', 'target_regions'):
        d[f] = list(d.get(f) or [])
    label, icon = REMINDER_TYPES.get(d.get('reminder_type'), ('Boshqa', '📅'))
    d['type_label'], d['icon'] = label, icon
    return d


_REMINDER_COLS = ("id, title, description, reminder_type, deadline_date, "
                  "reminder_days_before, target_degrees, target_specializations, "
                  "target_regions, url, is_active, created_by, created_at")


def _fetch_reminder(cur, id):
    cur.execute(f"SELECT {_REMINDER_COLS} FROM smart_reminders WHERE id = %s", (id,))
    row = cur.fetchone()
    return _reminder_row([c[0] for c in cur.description], row) if row else None


# ── Scheduler entry point (daily cron / GitHub Actions) ─────────────────────

@reminders_bp.route('/api/v1/reminders/process', methods=['POST'])
@csrf.exempt
def process_reminders():
    """Fire every active reminder whose deadline is exactly N days away
    (N ∈ reminder_days_before). Auth: admin session OR X-API-Key header
    matching REMINDERS_API_KEY from .env (for cron)."""
    api_key = os.environ.get('REMINDERS_API_KEY', '')
    provided = request.headers.get('X-API-Key') or request.args.get('key') or ''
    is_admin = (getattr(current_user, 'is_authenticated', False)
                and getattr(current_user, 'is_admin', False))
    if not (is_admin or (api_key and provided and hmac.compare_digest(provided, api_key))):
        return jsonify({"ok": False, "error": "unauthorized"}), 403
    from data import get_connection
    processed = sent = 0
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute(f"""
                    SELECT {_REMINDER_COLS} FROM smart_reminders
                    WHERE is_active = TRUE AND deadline_date IS NOT NULL
                      AND deadline_date >= CURRENT_DATE
                """)
                cols = [c[0] for c in cur.description]
                rows = cur.fetchall()
                today = date.today()
                for row in rows:
                    r = dict(zip(cols, row))
                    days_left = (r['deadline_date'] - today).days
                    processed += 1
                    if days_left in (r.get('reminder_days_before') or []):
                        sent += _dispatch(cur, _reminder_row(cols, row), days_left)
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "processed": processed, "sent": sent})


# ── Personalised feed (widget + /reminders page) ─────────────────────────────

def _current_cabinet_identity(cur):
    """Resolve the visitor to a cabinet user row (or None for guests).

    Order: cabinet session → main-site Flask-Login e-mail bridge."""
    uid = session.get('cabinet_user_id')
    if not uid and getattr(current_user, 'is_authenticated', False):
        email = (getattr(current_user, 'email', '') or '').strip().lower()
        if email:
            cur.execute("SELECT id FROM cabinet_users WHERE LOWER(email) = %s", (email,))
            r = cur.fetchone()
            uid = r[0] if r else None
    if not uid:
        return None
    for u in _fetch_audience(cur):
        if u['id'] == uid:
            return u
    return None


def _relevant_upcoming(cur, limit=None):
    """Active future reminders relevant to the current visitor, soonest first.
    Guests only see untargeted (audience = all) reminders."""
    me = _current_cabinet_identity(cur)
    guest = {'degree': '', 'region': '', 'ixtisoslik': '', 'prefs': {}}
    cur.execute(f"""
        SELECT {_REMINDER_COLS} FROM smart_reminders
        WHERE is_active = TRUE AND deadline_date IS NOT NULL
          AND deadline_date >= CURRENT_DATE
        ORDER BY deadline_date ASC, id DESC
    """)
    cols = [c[0] for c in cur.description]
    today = date.today()
    items = []
    for row in cur.fetchall():
        raw = dict(zip(cols, row))
        r = _reminder_row(cols, row)
        if me is not None:
            if not _matches(r, me):
                continue
        else:
            # Guests: only reminders that target everyone.
            if (r['target_degrees'] or r['target_specializations']
                    or r['target_regions']):
                continue
        days_left = (raw['deadline_date'] - today).days
        items.append({
            "id": r['id'], "title": r['title'], "type": r['reminder_type'],
            "icon": r['icon'], "type_label": r['type_label'],
            "deadline": r['deadline_date'], "days_left": days_left,
            "url": r['url'] or '',
            "days_text": "Bugun oxirgi kun!" if days_left == 0 else f"{days_left} kun qoldi",
        })
        if limit and len(items) >= limit:
            break
    return items


@reminders_bp.route('/api/v1/reminders/upcoming')
def upcoming_reminders():
    from data import get_connection
    items = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                items = _relevant_upcoming(cur, limit=5)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        items = []
    return jsonify({"ok": True, "reminders": items})


@reminders_bp.route('/reminders')
def reminders_page():
    from data import get_connection
    items = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                items = _relevant_upcoming(cur)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        items = []
    return render_template('reminders.html', items=items)


# ── Cabinet: notification preferences ────────────────────────────────────────

_PREF_FIELDS = ('conference_reminders', 'grant_reminders',
                'journal_reminders', 'telegram_enabled')


@reminders_bp.route('/cabinet/api/notification-prefs', methods=['GET', 'POST'])
@csrf.exempt
def notification_prefs():
    from cabinet import current_cabinet_user
    from data import get_connection
    user = current_cabinet_user()
    if not user:
        return jsonify({"ok": False, "error": "auth"}), 401
    uid = user['id']
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                if request.method == 'POST':
                    data = request.get_json(silent=True) or {}
                    vals = {f: bool(data.get(f, True)) for f in _PREF_FIELDS}
                    cur.execute("""
                        INSERT INTO user_notification_prefs
                            (user_id, conference_reminders, grant_reminders,
                             journal_reminders, telegram_enabled, updated_at)
                        VALUES (%s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (user_id) DO UPDATE SET
                            conference_reminders = EXCLUDED.conference_reminders,
                            grant_reminders = EXCLUDED.grant_reminders,
                            journal_reminders = EXCLUDED.journal_reminders,
                            telegram_enabled = EXCLUDED.telegram_enabled,
                            updated_at = NOW()
                    """, (uid, vals['conference_reminders'], vals['grant_reminders'],
                          vals['journal_reminders'], vals['telegram_enabled']))
                cur.execute("SELECT conference_reminders, grant_reminders, "
                            "journal_reminders, telegram_enabled "
                            "FROM user_notification_prefs WHERE user_id = %s", (uid,))
                row = cur.fetchone()
                prefs = (dict(zip(_PREF_FIELDS, row)) if row
                         else {f: True for f in _PREF_FIELDS})
                cur.execute("SELECT telegram_id FROM cabinet_users WHERE id = %s", (uid,))
                tg = cur.fetchone()
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True, "prefs": prefs,
                        "has_telegram": bool(tg and tg[0])})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Admin: Smart Eslatmalar CRUD ─────────────────────────────────────────────

def _reminder_form_values():
    from app import UZ_REGIONS
    from cabinet import _DEGREE_CHOICES
    g = lambda name: (request.form.get(name) or '').strip()
    rtype = g('reminder_type')
    if rtype not in REMINDER_TYPES:
        rtype = 'custom'
    days = sorted({int(d) for d in request.form.getlist('reminder_days_before')
                   if d.isdigit() and int(d) in DAYS_CHOICES}, reverse=True)
    degrees = [d for d in request.form.getlist('target_degrees') if d in _DEGREE_CHOICES]
    regions = [r for r in request.form.getlist('target_regions') if r in UZ_REGIONS]
    specs = [s.strip() for s in g('target_specializations').split(',') if s.strip()]
    return {
        'title': g('title'), 'description': g('description') or None,
        'reminder_type': rtype, 'deadline_date': g('deadline_date') or None,
        'reminder_days_before': days or [7, 3, 1],
        'target_degrees': degrees, 'target_specializations': specs,
        'target_regions': regions, 'url': g('url') or None,
        'is_active': request.form.get('is_active', 'on') == 'on',
    }


def _render_admin(cur, edit_r=None):
    from app import UZ_REGIONS
    from cabinet import _DEGREE_CHOICES
    cur.execute(f"""
        SELECT {_REMINDER_COLS},
               (SELECT COUNT(*) FROM reminder_sends s WHERE s.reminder_id = smart_reminders.id)
        FROM smart_reminders ORDER BY is_active DESC, deadline_date ASC NULLS LAST, id DESC
    """)
    cols = [c[0] for c in cur.description[:-1]] + ['sent_count']
    items = []
    today = date.today()
    for row in cur.fetchall():
        d = _reminder_row(cols, row)
        d['sent_count'] = row[-1] or 0
        try:
            d['days_left'] = (date.fromisoformat(d['deadline_date']) - today).days \
                if d['deadline_date'] else None
        except ValueError:
            d['days_left'] = None
        items.append(d)
    return render_template('admin_reminders.html', items=items, edit_r=edit_r,
                           reminder_types=REMINDER_TYPES, days_choices=DAYS_CHOICES,
                           degree_choices=_DEGREE_CHOICES, regions=UZ_REGIONS)


@reminders_bp.route('/admin/reminders', methods=['GET'])
@login_required
def admin_reminders():
    from app import _require_admin
    _require_admin()
    from data import get_connection
    edit_id = request.args.get('edit', type=int)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            edit_r = _fetch_reminder(cur, edit_id) if edit_id else None
            resp = _render_admin(cur, edit_r)
        conn.commit()
    finally:
        conn.close()
    return resp


@reminders_bp.route('/admin/reminders/add', methods=['POST'])
@login_required
def admin_reminder_add():
    from app import _require_admin
    _require_admin()
    from data import get_connection
    v = _reminder_form_values()
    if not v['title']:
        flash("Sarlavha kiritilishi shart.", "error")
        return redirect('/admin/reminders')
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute("""
                    INSERT INTO smart_reminders
                        (title, description, reminder_type, deadline_date,
                         reminder_days_before, target_degrees, target_specializations,
                         target_regions, url, is_active, created_by)
                    VALUES (%(title)s, %(description)s, %(reminder_type)s,
                            %(deadline_date)s, %(reminder_days_before)s,
                            %(target_degrees)s, %(target_specializations)s,
                            %(target_regions)s, %(url)s, %(is_active)s, %(created_by)s)
                """, dict(v, created_by=current_user.id))
            conn.commit()
        finally:
            conn.close()
        flash("Eslatma qo'shildi!", "success")
    except Exception as e:
        flash("Xatolik: " + str(e), "error")
    return redirect('/admin/reminders')


@reminders_bp.route('/admin/reminders/edit/<int:id>', methods=['POST'])
@login_required
def admin_reminder_edit(id):
    from app import _require_admin
    _require_admin()
    from data import get_connection
    v = _reminder_form_values()
    if not v['title']:
        flash("Sarlavha kiritilishi shart.", "error")
        return redirect(f'/admin/reminders?edit={id}')
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute("""
                    UPDATE smart_reminders SET
                        title=%(title)s, description=%(description)s,
                        reminder_type=%(reminder_type)s, deadline_date=%(deadline_date)s,
                        reminder_days_before=%(reminder_days_before)s,
                        target_degrees=%(target_degrees)s,
                        target_specializations=%(target_specializations)s,
                        target_regions=%(target_regions)s, url=%(url)s,
                        is_active=%(is_active)s
                    WHERE id=%(id)s
                """, dict(v, id=id))
            conn.commit()
        finally:
            conn.close()
        flash("Eslatma yangilandi.", "success")
    except Exception as e:
        flash("Xatolik: " + str(e), "error")
    return redirect('/admin/reminders')


@reminders_bp.route('/admin/reminders/delete/<int:id>', methods=['POST'])
@login_required
def admin_reminder_delete(id):
    from app import _require_admin
    _require_admin()
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute("DELETE FROM smart_reminders WHERE id = %s", (id,))
            conn.commit()
        finally:
            conn.close()
        flash("Eslatma o'chirildi.", "success")
    except Exception as e:
        flash("Xatolik: " + str(e), "error")
    return redirect('/admin/reminders')


@reminders_bp.route('/admin/reminders/send-now/<int:id>', methods=['POST'])
@login_required
def admin_reminder_send_now(id):
    """Immediately dispatch one reminder to every matching user (dedup-guarded:
    a second click won't re-send to people who already got the manual blast)."""
    from app import _require_admin
    _require_admin()
    from data import get_connection
    sent = 0
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                r = _fetch_reminder(cur, id)
                if not r:
                    flash("Eslatma topilmadi.", "error")
                    return redirect('/admin/reminders')
                sent = _dispatch(cur, r, _MANUAL_SEND)
            conn.commit()
        finally:
            conn.close()
        flash(f"Yuborildi: {sent} ta xabar.", "success")
    except Exception as e:
        flash("Xatolik: " + str(e), "error")
    return redirect('/admin/reminders')
