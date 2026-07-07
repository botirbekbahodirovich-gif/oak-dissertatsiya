"""Universal messaging — profil chati + dissertatsiya moduli chati.

Real-time o'rniga 5 soniyalik polling ishlatiladi: Gunicorn sync workerlar
bilan infratuzilma o'zgarishisiz ishlaydi (WebSocket v1 da YO'Q — bu ongli
qaror, spec bilan kelishilgan). Poll endpointi bitta indeksli so'rov, bo'sh
javob juda kichik.

Fayllar lokal static/uploads/chat/ ostida (kodbazaning mavjud patterni;
.env da SUPABASE_KEY yo'q). _store_chat_file() ni almashtirish kifoya.
"""
import os
import re
import uuid

from flask import (Blueprint, jsonify, request, render_template, redirect,
                   abort, current_app)
from flask_login import login_required, current_user

from app import csrf

messages_bp = Blueprint('messages', __name__)

_schema_ready = False

MAX_BODY = 10000
FILE_MAX_BYTES = 10 * 1024 * 1024   # 10MB
# ruxsat etilgan biriktirmalar: MIME → (kengaytmalar, tur belgisi)
_ALLOWED_FILES = {
    'image/jpeg': (('.jpg', '.jpeg'), 'image'),
    'image/png': (('.png',), 'image'),
    'image/webp': (('.webp',), 'image'),
    'application/pdf': (('.pdf',), 'pdf'),
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': (('.docx',), 'docx'),
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': (('.xlsx',), 'other'),
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': (('.pptx',), 'other'),
    'application/zip': (('.zip',), 'other'),
    'application/x-zip-compressed': (('.zip',), 'other'),
}


def _ensure_schema(cur):
    global _schema_ready
    if _schema_ready:
        return
    # diss_projects FK uchun dissertation modulining sxemasi ham kerak
    from blueprints.dissertation import _ensure_schema as _diss_schema
    _diss_schema(cur)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id SERIAL PRIMARY KEY,
            conversation_type VARCHAR(20) DEFAULT 'direct'
                CHECK (conversation_type IN ('direct', 'dissertation')),
            dissertation_id INTEGER REFERENCES diss_projects(id) ON DELETE SET NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            last_message_at TIMESTAMP DEFAULT NOW()
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conversation_participants (
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            last_read_at TIMESTAMP DEFAULT NOW(),
            is_muted BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (conversation_id, user_id)
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_conv_participants_user "
                "ON conversation_participants(user_id)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            sender_id INTEGER NOT NULL REFERENCES users(id),
            body TEXT,
            attachment_url VARCHAR(600),
            attachment_name VARCHAR(300),
            attachment_type VARCHAR(50),
            attachment_size INTEGER,
            is_deleted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            CHECK (body IS NOT NULL OR attachment_url IS NOT NULL)
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_conversation "
                "ON messages(conversation_id, created_at DESC)")
    _schema_ready = True


def _conn():
    from data import get_connection
    return get_connection()


def ensure_direct_conversation(cur, user_a, user_b):
    """Ikki foydalanuvchi orasidagi direct suhbatni topadi yoki yaratadi.
    (conversation_id, created) qaytaradi. current_user'ga BOG'LIQ EMAS —
    dissertatsiya moduli qabul qilinganda avtomatik chaqirish uchun."""
    _ensure_schema(cur)
    if user_a == user_b:
        return None, False
    cur.execute("""
        SELECT c.id FROM conversations c
        JOIN conversation_participants p1 ON p1.conversation_id = c.id AND p1.user_id = %s
        JOIN conversation_participants p2 ON p2.conversation_id = c.id AND p2.user_id = %s
        WHERE c.conversation_type = 'direct' LIMIT 1
    """, (user_a, user_b))
    r = cur.fetchone()
    if r:
        return r[0], False
    cur.execute("INSERT INTO conversations (conversation_type) VALUES ('direct') RETURNING id")
    cid = cur.fetchone()[0]
    for uid in (user_a, user_b):
        cur.execute("""INSERT INTO conversation_participants (conversation_id, user_id)
                       VALUES (%s, %s) ON CONFLICT DO NOTHING""", (cid, uid))
    return cid, True


def post_system_message(cur, conversation_id, sender_id, body, notify=True):
    """Xush kelibsiz / tizim xabari. sender_id — haqiqiy foydalanuvchi (odatda
    taklif qilgan tomon). Boshqa ishtirokchilarga new_message bildirishnomasi."""
    import json as _json
    cur.execute("""INSERT INTO messages (conversation_id, sender_id, body)
                   VALUES (%s, %s, %s)""",
                (conversation_id, sender_id, (body or '')[:MAX_BODY]))
    cur.execute("UPDATE conversations SET last_message_at = NOW() WHERE id = %s",
                (conversation_id,))
    if notify:
        cur.execute("""SELECT user_id FROM conversation_participants
                       WHERE conversation_id = %s AND user_id <> %s AND is_muted = FALSE""",
                    (conversation_id, sender_id))
        for (uid,) in cur.fetchall():
            cur.execute("""INSERT INTO diss_notifications (user_id, event_type, actor_id, payload)
                           VALUES (%s, 'new_message', %s, %s)""",
                        (uid, sender_id, _json.dumps(
                            {'conversation_id': conversation_id, 'snippet': (body or '')[:80]},
                            ensure_ascii=False)))


def _require_participant(cur, conversation_id):
    cur.execute("SELECT 1 FROM conversation_participants "
                "WHERE conversation_id = %s AND user_id = %s",
                (conversation_id, current_user.id))
    if not cur.fetchone():
        abort(403)


def _msg_dict(r):
    return {'id': r[0], 'sender_id': r[1], 'body': r[2] or '',
            'attachment_url': r[3] or '', 'attachment_name': r[4] or '',
            'attachment_type': r[5] or '', 'attachment_size': r[6] or 0,
            'created_at': str(r[7])[:16], 'mine': r[1] == current_user.id}


_MSG_COLS = ('id, sender_id, body, attachment_url, attachment_name, '
             'attachment_type, attachment_size, created_at')


# ── Pages ────────────────────────────────────────────────────────────────────

@messages_bp.route('/messages')
@login_required
def inbox():
    conn = _conn()
    convs = []
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            # inbox — 1 so'rov: suhbat + qarshi tomon + oxirgi xabar + unread
            cur.execute("""
                SELECT c.id, c.conversation_type, c.last_message_at,
                       ou.id, ou.username,
                       (SELECT body FROM messages m WHERE m.conversation_id = c.id
                          AND m.is_deleted = FALSE ORDER BY m.created_at DESC LIMIT 1),
                       (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id
                          AND m.created_at > cp.last_read_at
                          AND m.sender_id <> %s AND m.is_deleted = FALSE),
                       dp.title
                FROM conversation_participants cp
                JOIN conversations c ON c.id = cp.conversation_id
                LEFT JOIN conversation_participants op
                       ON op.conversation_id = c.id AND op.user_id <> %s
                LEFT JOIN users ou ON ou.id = op.user_id
                LEFT JOIN diss_projects dp ON dp.id = c.dissertation_id
                WHERE cp.user_id = %s
                ORDER BY c.last_message_at DESC LIMIT 100
            """, (current_user.id, current_user.id, current_user.id))
            for r in cur.fetchall():
                convs.append({'id': r[0], 'type': r[1],
                              'last_at': str(r[2])[:16] if r[2] else '',
                              'other_id': r[3], 'other_name': r[4] or 'Foydalanuvchi',
                              'snippet': (r[5] or '📎 Fayl')[:80],
                              'unread': r[6] or 0,
                              'diss_title': r[7] or ''})
        conn.commit()
    finally:
        conn.close()
    return render_template('messages/inbox.html', conversations=convs)


@messages_bp.route('/messages/<int:conversation_id>')
@login_required
def thread(conversation_id):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            _require_participant(cur, conversation_id)
            cur.execute("""
                SELECT u.id, u.username FROM conversation_participants cp
                JOIN users u ON u.id = cp.user_id
                WHERE cp.conversation_id = %s AND cp.user_id <> %s LIMIT 1
            """, (conversation_id, current_user.id))
            other = cur.fetchone()
            cur.execute(f"""
                SELECT {_MSG_COLS} FROM messages
                WHERE conversation_id = %s AND is_deleted = FALSE
                ORDER BY created_at DESC LIMIT 50
            """, (conversation_id,))
            msgs = [_msg_dict(r) for r in cur.fetchall()][::-1]
            cur.execute("UPDATE conversation_participants SET last_read_at = NOW() "
                        "WHERE conversation_id = %s AND user_id = %s",
                        (conversation_id, current_user.id))
        conn.commit()
    finally:
        conn.close()
    return render_template('messages/thread.html',
                           conversation_id=conversation_id,
                           other={'id': other[0], 'name': other[1]} if other
                                 else {'id': 0, 'name': 'Suhbat'},
                           messages=msgs)


# ── APIs ─────────────────────────────────────────────────────────────────────

@messages_bp.route('/api/messages/start', methods=['POST'])
@csrf.exempt
@login_required
def start_conversation():
    data = request.get_json(silent=True) or {}
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            if data.get('dissertation_id'):
                from blueprints.dissertation import get_dissertation_or_403
                p, role = get_dissertation_or_403(cur, int(data['dissertation_id']))
                if not p['advisor_id']:
                    return jsonify({'success': False,
                                    'error': 'Loyihaga rahbar biriktirilmagan'}), 400
                cur.execute("""
                    SELECT id FROM conversations
                    WHERE conversation_type = 'dissertation' AND dissertation_id = %s
                """, (p['id'],))
                r = cur.fetchone()
                if r:
                    return jsonify({'success': True, 'conversation_id': r[0]})
                cur.execute("""
                    INSERT INTO conversations (conversation_type, dissertation_id)
                    VALUES ('dissertation', %s) RETURNING id
                """, (p['id'],))
                cid = cur.fetchone()[0]
                for uid in (p['owner_id'], p['advisor_id']):
                    cur.execute("""
                        INSERT INTO conversation_participants (conversation_id, user_id)
                        VALUES (%s, %s) ON CONFLICT DO NOTHING
                    """, (cid, uid))
            else:
                try:
                    other_id = int(data.get('user_id'))
                except (TypeError, ValueError):
                    return jsonify({'success': False, 'error': "Noto'g'ri so'rov"}), 400
                if other_id == current_user.id:
                    return jsonify({'success': False,
                                    'error': "O'zingizga xabar yoza olmaysiz"}), 400
                cur.execute("SELECT 1 FROM users WHERE id = %s", (other_id,))
                if not cur.fetchone():
                    return jsonify({'success': False, 'error': 'Foydalanuvchi topilmadi'}), 404
                # mavjud direct suhbatni topish — dublikat YARATILMAYDI
                cur.execute("""
                    SELECT c.id FROM conversations c
                    JOIN conversation_participants p1
                         ON p1.conversation_id = c.id AND p1.user_id = %s
                    JOIN conversation_participants p2
                         ON p2.conversation_id = c.id AND p2.user_id = %s
                    WHERE c.conversation_type = 'direct' LIMIT 1
                """, (current_user.id, other_id))
                r = cur.fetchone()
                if r:
                    return jsonify({'success': True, 'conversation_id': r[0]})
                cur.execute("INSERT INTO conversations (conversation_type) "
                            "VALUES ('direct') RETURNING id")
                cid = cur.fetchone()[0]
                for uid in (current_user.id, other_id):
                    cur.execute("""
                        INSERT INTO conversation_participants (conversation_id, user_id)
                        VALUES (%s, %s)
                    """, (cid, uid))
        conn.commit()
        return jsonify({'success': True, 'conversation_id': cid})
    except Exception as e:
        conn.rollback()
        if getattr(e, 'code', None) in (403, 404):
            raise
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


def _notify_participants(cur, conversation_id, body_snippet):
    """new_message xabari — dedupe: shu suhbat uchun o'qilmagan new_message
    bo'lsa, yangisi yaratilmaydi (spam oldini olish)."""
    cur.execute("""
        SELECT cp.user_id FROM conversation_participants cp
        WHERE cp.conversation_id = %s AND cp.user_id <> %s AND cp.is_muted = FALSE
    """, (conversation_id, current_user.id))
    import json as _json
    for (uid,) in cur.fetchall():
        cur.execute("""
            SELECT 1 FROM diss_notifications
            WHERE user_id = %s AND event_type = 'new_message' AND is_read = FALSE
              AND payload->>'conversation_id' = %s LIMIT 1
        """, (uid, str(conversation_id)))
        if cur.fetchone():
            continue
        cur.execute("""
            INSERT INTO diss_notifications (user_id, event_type, actor_id, payload)
            VALUES (%s, 'new_message', %s, %s)
        """, (uid, current_user.id,
              _json.dumps({'conversation_id': conversation_id,
                           'from': current_user.username,
                           'snippet': (body_snippet or '')[:80]}, ensure_ascii=False)))


@messages_bp.route('/api/messages/<int:conversation_id>/send', methods=['POST'])
@csrf.exempt
@login_required
def send_message(conversation_id):
    body = ((request.get_json(silent=True) or {}).get('body') or '').strip()
    if not body:
        return jsonify({'success': False, 'error': "Bo'sh xabar yuborib bo'lmaydi"}), 400
    if len(body) > MAX_BODY:
        return jsonify({'success': False, 'error': 'Xabar 10 000 belgidan oshmasligi kerak'}), 413
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            _require_participant(cur, conversation_id)
            cur.execute(f"""
                INSERT INTO messages (conversation_id, sender_id, body)
                VALUES (%s, %s, %s) RETURNING {_MSG_COLS}
            """, (conversation_id, current_user.id, body))
            msg = _msg_dict(cur.fetchone())
            cur.execute("UPDATE conversations SET last_message_at = NOW() WHERE id = %s",
                        (conversation_id,))
            cur.execute("UPDATE conversation_participants SET last_read_at = NOW() "
                        "WHERE conversation_id = %s AND user_id = %s",
                        (conversation_id, current_user.id))
            _notify_participants(cur, conversation_id, body)
        conn.commit()
        return jsonify({'success': True, 'message': msg})
    finally:
        conn.close()


@messages_bp.route('/api/messages/<int:conversation_id>/upload', methods=['POST'])
@csrf.exempt
@login_required
def upload_attachment(conversation_id):
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'success': False, 'error': 'Fayl tanlanmagan'}), 400
    from werkzeug.utils import secure_filename
    safe = secure_filename(f.filename)[:100] or 'fayl'
    ext = os.path.splitext(safe)[1].lower()
    mime = (f.mimetype or '').lower()
    allowed = _ALLOWED_FILES.get(mime)
    if not allowed or ext not in allowed[0]:
        return jsonify({'success': False,
                        'error': 'Faqat rasm, PDF, DOCX, XLSX, PPTX, ZIP qabul qilinadi'}), 400
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(0)
    if size > FILE_MAX_BYTES:
        return jsonify({'success': False, 'error': 'Fayl hajmi 10MB dan oshmasligi kerak'}), 413
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            _require_participant(cur, conversation_id)
            try:
                updir = os.path.join(current_app.static_folder, 'uploads', 'chat',
                                     f'conv_{conversation_id}')
                os.makedirs(updir, exist_ok=True)
                stored = f'{uuid.uuid4().hex}_{safe}'
                f.save(os.path.join(updir, stored))
                url = f'/static/uploads/chat/conv_{conversation_id}/{stored}'
            except Exception:
                return jsonify({'success': False,
                                'error': "Fayl yuklashda xatolik. Qayta urinib ko'ring."}), 500
            cur.execute(f"""
                INSERT INTO messages (conversation_id, sender_id, body, attachment_url,
                                      attachment_name, attachment_type, attachment_size)
                VALUES (%s, %s, NULL, %s, %s, %s, %s) RETURNING {_MSG_COLS}
            """, (conversation_id, current_user.id, url, safe, allowed[1], size))
            msg = _msg_dict(cur.fetchone())
            cur.execute("UPDATE conversations SET last_message_at = NOW() WHERE id = %s",
                        (conversation_id,))
            _notify_participants(cur, conversation_id, f'📎 {safe}')
        conn.commit()
        return jsonify({'success': True, 'message': msg})
    finally:
        conn.close()


@messages_bp.route('/api/messages/<int:conversation_id>/poll')
@login_required
def poll_messages(conversation_id):
    """5s polling — WebSocket o'rniga (Gunicorn sync workerlarga mos, v1 qarori).
    Yangi xabar bo'lmasa juda kichik javob qaytadi."""
    after_id = request.args.get('after_id', 0, type=int)
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            _require_participant(cur, conversation_id)
            cur.execute(f"""
                SELECT {_MSG_COLS} FROM messages
                WHERE conversation_id = %s AND id > %s AND is_deleted = FALSE
                ORDER BY id LIMIT 100
            """, (conversation_id, after_id))
            msgs = [_msg_dict(r) for r in cur.fetchall()]
            if msgs:
                cur.execute("UPDATE conversation_participants SET last_read_at = NOW() "
                            "WHERE conversation_id = %s AND user_id = %s",
                            (conversation_id, current_user.id))
        conn.commit()
        return jsonify({'success': True, 'messages': msgs})
    finally:
        conn.close()


@messages_bp.route('/api/messages/older/<int:conversation_id>')
@login_required
def older_messages(conversation_id):
    before_id = request.args.get('before_id', 0, type=int)
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            _require_participant(cur, conversation_id)
            cur.execute(f"""
                SELECT {_MSG_COLS} FROM messages
                WHERE conversation_id = %s AND id < %s AND is_deleted = FALSE
                ORDER BY id DESC LIMIT 50
            """, (conversation_id, before_id))
            msgs = [_msg_dict(r) for r in cur.fetchall()][::-1]
        conn.commit()
        return jsonify({'success': True, 'messages': msgs})
    finally:
        conn.close()


@messages_bp.route('/api/messages/unread-count')
@login_required
def unread_count():
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute("""
                SELECT COUNT(*) FROM messages m
                JOIN conversation_participants cp
                     ON cp.conversation_id = m.conversation_id AND cp.user_id = %s
                WHERE m.created_at > cp.last_read_at
                  AND m.sender_id <> %s AND m.is_deleted = FALSE
            """, (current_user.id, current_user.id))
            n = cur.fetchone()[0] or 0
        conn.commit()
        return jsonify({'success': True, 'unread': n})
    finally:
        conn.close()
