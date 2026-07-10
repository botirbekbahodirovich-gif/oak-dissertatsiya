"""Ixtisoslik obunasi (subs_bp) — yangi himoyalardan xabardor bo'lish (bepul).

Foydalanuvchi (asosiy sayt `users`) 5 tagacha ixtisoslikka obuna bo'ladi;
scraper yangi dissertatsiya import qilganda (data.py /api/v1/import-oak)
`notify_specialty_subscribers` mos obunachilarga sayt bildirishnomasi
(user_alerts) va xohlasa Telegram xabari yuboradi.

Konvensiyalar (notifications/reminders blueprint'lari kabi):
  * Sxema lazy + idempotent (_ensure_schema); users.telegram_chat_id ustuni ham
    shu yerda qo'shiladi (Telegram login callback uni to'ldiradi).
  * Dedup — specialty_notifications_log (obuna+dissertatsiya+kanal bo'yicha).
  * Kod (masalan 05.01.01) `ixtisoslik` ustunida saqlanadi; to'liq nom —
    `ixtisoslik_nomi` da. Moslik: kod yangi yozuv ixtisosligida uchrasa.

Routes (JSON, Flask-Login):
  GET  /api/v1/subscriptions          — ro'yxat + meta (max, telegram bor-yo'qligi)
  POST /api/v1/subscriptions          — qo'shish {ixtisoslik, ixtisoslik_nomi?}
  POST /api/v1/subscriptions/remove   — o'chirish {id}
  POST /api/v1/subscriptions/toggle   — kanal {id, channel: site|telegram, enabled}
"""
import re

from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user

from app import csrf
from data import get_connection

subs_bp = Blueprint('subscriptions', __name__)

MAX_SUBSCRIPTIONS = 5
_CODE_RE = re.compile(r'\d{2}\.\d{2}\.\d{2}')

_schema_ready = False


def _ensure_schema(cur):
    """Obuna jadvallarini idempotent yaratadi + users.telegram_chat_id ustuni."""
    global _schema_ready
    if _schema_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS specialty_subscriptions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            ixtisoslik VARCHAR(30) NOT NULL,
            ixtisoslik_nomi VARCHAR(300),
            notify_site BOOLEAN DEFAULT TRUE,
            notify_telegram BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, ixtisoslik)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_spec_subs_user "
                "ON specialty_subscriptions(user_id)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS specialty_notifications_log (
            id SERIAL PRIMARY KEY,
            subscription_id INTEGER REFERENCES specialty_subscriptions(id) ON DELETE CASCADE,
            dissertation_id INTEGER REFERENCES dissertations(id),
            sent_via VARCHAR(20) DEFAULT 'site',
            sent_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_spec_notif_log_sub "
                "ON specialty_notifications_log(subscription_id, dissertation_id)")
    # Telegram bot xabarlari uchun chat id (login callback to'ldiradi; eski
    # telegram-login foydalanuvchilar uchun emaildan tiklab bo'ladi).
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_chat_id BIGINT")
    _schema_ready = True


def _normalize(ixtisoslik, nomi):
    """('05.01.01 – Nazariya…', '') → ('05.01.01', '05.01.01 – Nazariya…').

    Kod topilmasa qisqartirilgan matnning o'zi kod sifatida saqlanadi."""
    raw = ' '.join((ixtisoslik or '').split())
    label = ' '.join((nomi or '').split()) or raw
    m = _CODE_RE.search(raw)
    code = m.group(0) if m else raw[:30]
    return code, label[:300]


def _telegram_chat_id(row_chat_id, email):
    """users.telegram_chat_id yoki '12345@telegram.uz' emaildan chat id."""
    if row_chat_id:
        return row_chat_id
    email = (email or '').strip().lower()
    if email.endswith('@telegram.uz'):
        prefix = email.split('@', 1)[0]
        if prefix.isdigit():
            return int(prefix)
    return None


def _sub_dict(r):
    return {'id': r[0], 'ixtisoslik': r[1], 'ixtisoslik_nomi': r[2] or '',
            'notify_site': bool(r[3]), 'notify_telegram': bool(r[4])}


# ── API ──────────────────────────────────────────────────────────────────────

@subs_bp.route('/api/v1/subscriptions', methods=['GET'])
@login_required
def list_subscriptions():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute("""
                SELECT id, ixtisoslik, ixtisoslik_nomi, notify_site, notify_telegram
                FROM specialty_subscriptions WHERE user_id = %s ORDER BY created_at
            """, (current_user.id,))
            items = [_sub_dict(r) for r in cur.fetchall()]
            cur.execute("SELECT telegram_chat_id, email FROM users WHERE id = %s",
                        (current_user.id,))
            row = cur.fetchone() or (None, None)
        conn.commit()
        return jsonify({'items': items, 'max': MAX_SUBSCRIPTIONS,
                        'telegram_linked': _telegram_chat_id(row[0], row[1]) is not None})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


@subs_bp.route('/api/v1/subscriptions', methods=['POST'])
@csrf.exempt
@login_required
def add_subscription():
    data = request.get_json(silent=True) or {}
    code, label = _normalize(data.get('ixtisoslik'), data.get('ixtisoslik_nomi'))
    if not code:
        return jsonify({'success': False, 'error': 'Ixtisoslik kiritilmadi'}), 400
    notify_telegram = bool(data.get('notify_telegram'))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute("SELECT COUNT(*) FROM specialty_subscriptions WHERE user_id = %s",
                        (current_user.id,))
            if (cur.fetchone()[0] or 0) >= MAX_SUBSCRIPTIONS:
                return jsonify({'success': False, 'error':
                                f"Maksimal {MAX_SUBSCRIPTIONS} ta obuna. Avval bittasini o'chiring."}), 409
            cur.execute("""
                INSERT INTO specialty_subscriptions
                    (user_id, ixtisoslik, ixtisoslik_nomi, notify_telegram)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, ixtisoslik) DO NOTHING
                RETURNING id, ixtisoslik, ixtisoslik_nomi, notify_site, notify_telegram
            """, (current_user.id, code, label, notify_telegram))
            row = cur.fetchone()
        conn.commit()
        if not row:
            return jsonify({'success': True, 'already': True,
                            'message': "Bu ixtisoslikka allaqachon obuna bo'lgansiz"})
        return jsonify({'success': True, 'item': _sub_dict(row)})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@subs_bp.route('/api/v1/subscriptions/remove', methods=['POST'])
@csrf.exempt
@login_required
def remove_subscription():
    data = request.get_json(silent=True) or {}
    try:
        sub_id = int(data.get('id'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': "Noto'g'ri so'rov"}), 400
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute("DELETE FROM specialty_subscriptions WHERE id = %s AND user_id = %s",
                        (sub_id, current_user.id))
            deleted = cur.rowcount
        conn.commit()
        return jsonify({'success': bool(deleted)})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@subs_bp.route('/api/v1/subscriptions/toggle', methods=['POST'])
@csrf.exempt
@login_required
def toggle_subscription():
    data = request.get_json(silent=True) or {}
    channel = data.get('channel')
    if channel not in ('site', 'telegram'):
        return jsonify({'success': False, 'error': "Noto'g'ri kanal"}), 400
    try:
        sub_id = int(data.get('id'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': "Noto'g'ri so'rov"}), 400
    enabled = bool(data.get('enabled'))
    col = 'notify_site' if channel == 'site' else 'notify_telegram'
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute(f"UPDATE specialty_subscriptions SET {col} = %s "
                        "WHERE id = %s AND user_id = %s",
                        (enabled, sub_id, current_user.id))
            updated = cur.rowcount
        conn.commit()
        return jsonify({'success': bool(updated)})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


# ── Dispatch (data.py /api/v1/import-oak dan chaqiriladi) ────────────────────

_SUB_TITLE = "🔔 Obunangiz bo'yicha yangi himoya"


def notify_specialty_subscribers(new_records):
    """Yangi import qilingan himoyalarni obunachilarga tarqatadi.

    `new_records` — [{'id', 'olim', 'mavzu', 'ixtisoslik', 'link'}, …]
    (id bo'lmasa dedup log yozilmaydi, lekin xabar baribir ketadi).
    Import tranzaksiyasidan keyin o'z ulanishida ishlaydi; hech qachon
    exception ko'tarmaydi. Yuborilgan xabarlar sonini qaytaradi."""
    records = [r for r in new_records if (r.get('ixtisoslik') or '').strip()]
    if not records:
        return 0
    sent = 0
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                from blueprints.notifications import _ensure_schema as _ensure_notif
                _ensure_notif(cur)
                cur.execute("""
                    SELECT s.id, s.user_id, s.ixtisoslik, s.notify_site,
                           s.notify_telegram, u.telegram_chat_id, u.email
                    FROM specialty_subscriptions s
                    JOIN users u ON u.id = s.user_id
                    WHERE s.notify_site OR s.notify_telegram
                """)
                subs = cur.fetchall()
                if not subs:
                    return 0
                from blueprints.reminders import _send_telegram
                for (sub_id, user_id, code, notify_site,
                     notify_telegram, chat_id, email) in subs:
                    low = (code or '').strip().lower()
                    if not low:
                        continue
                    mine = [r for r in records
                            if low in (r['ixtisoslik'] or '').lower()]
                    for rec in mine:
                        diss_id = rec.get('id')
                        already = set()
                        if diss_id:
                            cur.execute("""
                                SELECT sent_via FROM specialty_notifications_log
                                WHERE subscription_id = %s AND dissertation_id = %s
                            """, (sub_id, diss_id))
                            already = {r[0] for r in cur.fetchall()}
                        msg = (f"Siz obuna bo'lgan ixtisoslikda ({rec['ixtisoslik']}) "
                               f"yangi himoya: {rec.get('olim') or ''} — "
                               f"{rec.get('mavzu') or ''}")
                        if rec.get('link'):
                            msg += f"\n🔗 {rec['link']}"
                        if notify_site and 'site' not in already:
                            cur.execute("""
                                INSERT INTO user_alerts (user_id, title, message, level)
                                VALUES (%s, %s, %s, 'info')
                            """, (user_id, _SUB_TITLE, msg))
                            if diss_id:
                                cur.execute("""
                                    INSERT INTO specialty_notifications_log
                                        (subscription_id, dissertation_id, sent_via)
                                    VALUES (%s, %s, 'site')
                                """, (sub_id, diss_id))
                            sent += 1
                        tg_chat = _telegram_chat_id(chat_id, email)
                        if notify_telegram and tg_chat and 'telegram' not in already:
                            if _send_telegram(tg_chat, {
                                    'title': f"Yangi himoya ({rec['ixtisoslik']})",
                                    'description': (f"{rec.get('olim') or ''} — "
                                                    f"{rec.get('mavzu') or ''}"),
                                    'url': rec.get('link') or ''}):
                                if diss_id:
                                    cur.execute("""
                                        INSERT INTO specialty_notifications_log
                                            (subscription_id, dissertation_id, sent_via)
                                        VALUES (%s, %s, 'telegram')
                                    """, (sub_id, diss_id))
                                sent += 1
            conn.commit()
        finally:
            conn.close()
    except Exception:
        return sent
    return sent
