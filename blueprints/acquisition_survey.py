"""Acquisition-source survey blueprint.

Powers the one-time post-signup modal that asks a new user where they found
olimlar.uz ("Bizni qayerdan bildingiz?") for marketing attribution. The answer
is stored on the main `users` row (users.id == current_user.id) — no third-party
analytics, this is our own data.

Endpoints (all JSON, auth required, CSRF-exempt like the other /api JSON APIs):
  GET  /api/acquisition-survey/should-show → {should_show: bool}
  POST /api/acquisition-survey/submit      → record the chosen source (idempotent)
  POST /api/acquisition-survey/skip        → mark shown so we never ask again

Show logic lives server-side: the modal is offered only while both
acquisition_survey_shown_at and acquisition_survey_answered_at are NULL, and
never to admins (role filter, per spec). The columns are created by
migrations/add_acquisition_survey.sql and also lazily here (_ensure_schema), so a
fresh database is self-sufficient. Shared helpers are lazy-imported inside views
(auth.py / notifications.py pattern) to avoid circular imports.
"""
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user

from app import csrf

acquisition_survey_bp = Blueprint('acquisition_survey', __name__)

# Allowed source values — kept in sync with the modal tiles and the migration
# comment. 'other' unlocks the free-text field (acquisition_source_other).
ALLOWED_SOURCES = (
    'telegram', 'youtube', 'instagram', 'friend_colleague',
    'advisor', 'google_search', 'university', 'other',
)
OTHER_MAX_LEN = 200

_schema_ready = False


def _ensure_schema(cur):
    """Idempotently add the survey columns + index. Cheap after first run."""
    global _schema_ready
    if _schema_ready:
        return
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS acquisition_source VARCHAR(32)")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS acquisition_source_other TEXT")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS acquisition_survey_shown_at TIMESTAMP")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS acquisition_survey_answered_at TIMESTAMP")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_acquisition_source "
                "ON users(acquisition_source)")
    _schema_ready = True


@acquisition_survey_bp.route('/api/acquisition-survey/should-show')
@login_required
def should_show():
    """True only for a non-admin who has neither answered nor skipped the survey."""
    # Admins / test accounts are excluded — attribution is about real signups.
    if getattr(current_user, 'is_admin', False):
        return jsonify({"should_show": False})
    from data import get_connection
    show = False
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute(
                    "SELECT acquisition_survey_shown_at, acquisition_survey_answered_at "
                    "FROM users WHERE id = %s", (int(current_user.id),))
                row = cur.fetchone()
            conn.commit()
        finally:
            conn.close()
        if row is not None:
            show = row[0] is None and row[1] is None
    except Exception:
        show = False
    return jsonify({"should_show": show})


@acquisition_survey_bp.route('/api/acquisition-survey/submit', methods=['POST'])
@csrf.exempt
@login_required
def submit():
    """Record the user's chosen acquisition source. Idempotent: a second submit
    after answering returns already_answered without overwriting the first."""
    data = request.get_json(silent=True) or {}
    source = (data.get('source') or '').strip()
    if source not in ALLOWED_SOURCES:
        return jsonify({"status": "error", "error": "invalid_source"}), 400

    source_other = None
    if source == 'other':
        source_other = (data.get('source_other') or '').strip()
        if not source_other:
            return jsonify({"status": "error", "error": "other_required"}), 400
        source_other = source_other[:OTHER_MAX_LEN]

    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                # Only the first answer wins (idempotent). The UPDATE ... WHERE
                # answered_at IS NULL touches zero rows if already answered.
                cur.execute("""
                    UPDATE users
                       SET acquisition_source = %s,
                           acquisition_source_other = %s,
                           acquisition_survey_answered_at = NOW(),
                           acquisition_survey_shown_at = COALESCE(acquisition_survey_shown_at, NOW())
                     WHERE id = %s AND acquisition_survey_answered_at IS NULL
                """, (source, source_other, int(current_user.id)))
                updated = cur.rowcount
            conn.commit()
        finally:
            conn.close()
        if updated == 0:
            return jsonify({"status": "already_answered"})
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@acquisition_survey_bp.route('/api/acquisition-survey/skip', methods=['POST'])
@csrf.exempt
@login_required
def skip():
    """Mark the survey as shown (once) so it is not offered again after a skip / ×."""
    from data import get_connection
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute(
                    "UPDATE users SET acquisition_survey_shown_at = NOW() "
                    "WHERE id = %s AND acquisition_survey_shown_at IS NULL",
                    (int(current_user.id),))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500
