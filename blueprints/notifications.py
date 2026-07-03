"""Notifications blueprint — dual-channel admin notification engine.

Two dispatch channels:
  a) Broadcast Announcements (global): one row in `admin_notifications`, with per-user
     read state tracked in the `broadcast_reads` junction table (user_id + notification_id).
     This keeps the message text stored once, no matter how many users read it.
  b) Targeted Alerts (personal): rows in `user_alerts`, each bound to a single user_id.

Public API (consumed by the global layout modal):
  GET  /api/v1/notifications/active   → unread global + personal notices for the session user
  POST /api/v1/notifications/dismiss  → acknowledge one notice (insert read / set is_read)

Notification preferences (consumed by the cabinet "Bildirishnomalar" panel and
the smart-reminders dispatcher in blueprints/reminders.py):
  GET  /api/v1/notifications/prefs    → {pref_key: bool} for the current user (default: all ON)
  POST /api/v1/notifications/prefs    → upsert {"prefs": {...}} into notification_prefs

`notification_prefs.user_id` is cabinet_users.id — the cabinet is the identity
home for scholar-facing settings (telegram_id, olim_profiles targeting live
there); main-site Flask-Login visitors are bridged by e-mail, the same linkage
cabinet.py uses.

Admin API (draft + POST notices):
  POST /admin/api/notifications/broadcast  → global announcement
  POST /admin/api/notifications/alert      → targeted personal alert

Schema is created lazily on first request (idempotent CREATE IF NOT EXISTS), so a fresh
database is self-sufficient without touching app.py's init block. Shared helpers are
lazy-imported inside views (auth.py / cabinet.py pattern) to avoid circular imports.
"""
from flask import Blueprint, jsonify, request, session
from flask_login import login_required, current_user

from app import csrf

notifications_bp = Blueprint('notifications', __name__)

_schema_ready = False
_prefs_schema_ready = False

# Canonical preference keys. Content prefs gate WHAT a scholar is notified
# about; channel prefs gate HOW. Everything defaults to ON (opt-out model).
PREF_KEYS = (
    'konferensiya',    # upcoming academic conferences
    'grant',           # grant / funding opportunities
    'himoya_elon',     # OAK defense announcements matching the specialization
    'jurnal',          # new journal issues in the user's field
    'yangilik',        # general olimlar.uz announcements
    'deadline',        # application / submission deadlines
    'telegram_notify', # channel: Telegram bot
    'email_notify',    # channel: e-mail digest (stored now, dispatch needs SMTP)
)


def _ensure_schema(cur):
    """Idempotently create the notification tables + indexes. Cheap after first run."""
    global _schema_ready
    if _schema_ready:
        return
    # Global broadcast announcements — text stored once.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_notifications (
            id SERIAL PRIMARY KEY,
            title TEXT,
            message TEXT NOT NULL,
            level VARCHAR(20) DEFAULT 'info',
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW(),
            expires_at TIMESTAMP
        )
    """)
    # Read-tracking junction — one row per (user, broadcast). Scales without
    # duplicating message text; UNIQUE guards against double-inserts.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS broadcast_reads (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            notification_id INTEGER NOT NULL REFERENCES admin_notifications(id) ON DELETE CASCADE,
            read_at TIMESTAMP DEFAULT NOW(),
            UNIQUE (user_id, notification_id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_reads_user ON broadcast_reads(user_id)")
    # Targeted personal alerts — bound to a single user_id.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_alerts (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            title TEXT,
            message TEXT NOT NULL,
            level VARCHAR(20) DEFAULT 'warning',
            is_read BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_alerts_user ON user_alerts(user_id, is_read)")
    _schema_ready = True


def _ensure_prefs_schema(cur):
    """Key-value notification preferences per cabinet user. Absence of a row
    means the default (enabled) — only explicit choices are stored."""
    global _prefs_schema_ready
    if _prefs_schema_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notification_prefs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            pref_key VARCHAR(100) NOT NULL,
            is_enabled BOOLEAN DEFAULT TRUE,
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, pref_key)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notification_prefs_user "
                "ON notification_prefs(user_id)")
    # One-time migration from the earlier column-per-toggle table
    # (user_notification_prefs) so nobody's saved choices are lost.
    cur.execute("SELECT to_regclass('user_notification_prefs')")
    if cur.fetchone()[0]:
        for old_col, new_key in (('conference_reminders', 'konferensiya'),
                                 ('grant_reminders', 'grant'),
                                 ('journal_reminders', 'jurnal'),
                                 ('telegram_enabled', 'telegram_notify')):
            cur.execute(f"""
                INSERT INTO notification_prefs (user_id, pref_key, is_enabled)
                SELECT user_id, %s, {old_col} FROM user_notification_prefs
                ON CONFLICT (user_id, pref_key) DO NOTHING
            """, (new_key,))
        cur.execute("DROP TABLE user_notification_prefs")
    _prefs_schema_ready = True


def resolve_pref_identity(cur):
    """Resolve the visitor to a cabinet_users row id (or None).

    Order: cabinet session → main-site Flask-Login e-mail bridge (the same
    linkage cabinet.py's _bridge_from_main uses, read-only here)."""
    uid = session.get('cabinet_user_id')
    if uid:
        return uid
    if getattr(current_user, 'is_authenticated', False):
        email = (getattr(current_user, 'email', '') or '').strip().lower()
        if email:
            cur.execute("SELECT id FROM cabinet_users WHERE LOWER(email) = %s", (email,))
            r = cur.fetchone()
            if r:
                return r[0]
    return None


def load_prefs(cur, uid):
    """{pref_key: bool} for a cabinet user — defaults (True) filled in."""
    prefs = {k: True for k in PREF_KEYS}
    cur.execute("SELECT pref_key, is_enabled FROM notification_prefs "
                "WHERE user_id = %s", (uid,))
    for key, enabled in cur.fetchall():
        if key in prefs:
            prefs[key] = bool(enabled)
    return prefs


# ── Public: active notices for the current session ──────────────────────────

@notifications_bp.route('/api/v1/notifications/active')
@login_required
def active_notifications():
    from data import get_connection
    uid = current_user.id
    items = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                # Unread global broadcasts: active, unexpired, not yet in read matrix.
                cur.execute("""
                    SELECT n.id, n.title, n.message, n.level
                    FROM admin_notifications n
                    LEFT JOIN broadcast_reads r
                        ON r.notification_id = n.id AND r.user_id = %s
                    WHERE n.is_active = TRUE
                      AND (n.expires_at IS NULL OR n.expires_at > NOW())
                      AND r.id IS NULL
                    ORDER BY n.created_at DESC
                """, (uid,))
                for x in cur.fetchall():
                    items.append({"id": x[0], "kind": "broadcast", "title": x[1] or "",
                                  "message": x[2] or "", "level": x[3] or "info"})
                # Unread personal alerts.
                cur.execute("""
                    SELECT id, title, message, level FROM user_alerts
                    WHERE user_id = %s AND is_read = FALSE
                    ORDER BY created_at DESC
                """, (uid,))
                for x in cur.fetchall():
                    items.append({"id": x[0], "kind": "personal", "title": x[1] or "",
                                  "message": x[2] or "", "level": x[3] or "warning"})
            conn.commit()
        finally:
            conn.close()
    except Exception:
        items = []
    # Personal alerts first — they are targeted and take priority.
    items.sort(key=lambda i: 0 if i["kind"] == "personal" else 1)
    return jsonify({"ok": True, "notifications": items})


# ── Public: acknowledge / dismiss a notice ──────────────────────────────────

@notifications_bp.route('/api/v1/notifications/dismiss', methods=['POST'])
@csrf.exempt
@login_required
def dismiss_notification():
    from data import get_connection
    data = request.get_json(silent=True) or {}
    kind = data.get('kind')
    nid = data.get('id')
    try:
        nid = int(nid)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid id"}), 400
    if kind not in ('broadcast', 'personal'):
        return jsonify({"ok": False, "error": "invalid kind"}), 400
    uid = current_user.id
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                if kind == 'broadcast':
                    # Insert into the read matrix; ignore if already acknowledged.
                    cur.execute("""
                        INSERT INTO broadcast_reads (user_id, notification_id)
                        VALUES (%s, %s)
                        ON CONFLICT (user_id, notification_id) DO NOTHING
                    """, (uid, nid))
                else:
                    cur.execute(
                        "UPDATE user_alerts SET is_read = TRUE WHERE id = %s AND user_id = %s",
                        (nid, uid))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Admin: draft + POST notices ─────────────────────────────────────────────

_LEVELS = ('info', 'warning', 'success', 'danger')


@notifications_bp.route('/admin/api/notifications/broadcast', methods=['POST'])
@csrf.exempt
@login_required
def admin_post_broadcast():
    from app import _require_admin
    _require_admin()
    from data import get_connection
    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({"ok": False, "error": "message required"}), 400
    title = (data.get('title') or '').strip()
    level = data.get('level') if data.get('level') in _LEVELS else 'info'
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute("""
                    INSERT INTO admin_notifications (title, message, level, is_active)
                    VALUES (%s, %s, %s, TRUE) RETURNING id
                """, (title, message, level))
                new_id = cur.fetchone()[0]
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True, "id": new_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@notifications_bp.route('/admin/api/notifications/alert', methods=['POST'])
@csrf.exempt
@login_required
def admin_post_alert():
    from app import _require_admin
    _require_admin()
    from data import get_connection
    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({"ok": False, "error": "message required"}), 400
    try:
        target_uid = int(data.get('user_id'))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "valid user_id required"}), 400
    title = (data.get('title') or '').strip()
    level = data.get('level') if data.get('level') in _LEVELS else 'warning'
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute("""
                    INSERT INTO user_alerts (user_id, title, message, level)
                    VALUES (%s, %s, %s, %s) RETURNING id
                """, (target_uid, title, message, level))
                new_id = cur.fetchone()[0]
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True, "id": new_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Notification preferences (cabinet toggle panel) ──────────────────────────

@notifications_bp.route('/api/v1/notifications/prefs', methods=['GET', 'POST'])
@csrf.exempt
def notification_prefs():
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_prefs_schema(cur)
                uid = resolve_pref_identity(cur)
                if not uid:
                    return jsonify({"success": False, "error": "auth"}), 401
                if request.method == 'POST':
                    data = request.get_json(silent=True) or {}
                    incoming = data.get('prefs') or {}
                    if not isinstance(incoming, dict):
                        return jsonify({"success": False, "error": "prefs must be an object"}), 400
                    for key, val in incoming.items():
                        if key not in PREF_KEYS:
                            continue
                        cur.execute("""
                            INSERT INTO notification_prefs (user_id, pref_key, is_enabled, updated_at)
                            VALUES (%s, %s, %s, NOW())
                            ON CONFLICT (user_id, pref_key) DO UPDATE
                                SET is_enabled = EXCLUDED.is_enabled, updated_at = NOW()
                        """, (uid, key, bool(val)))
                prefs = load_prefs(cur, uid)
                # Channel availability — the UI hides toggles for unlinked channels.
                cur.execute("SELECT telegram_id, email FROM cabinet_users WHERE id = %s", (uid,))
                row = cur.fetchone() or (None, None)
            conn.commit()
        finally:
            conn.close()
        return jsonify({"success": True, "prefs": prefs,
                        "has_telegram": bool(row[0]),
                        "has_email": bool(row[1] and str(row[1]).strip())})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
