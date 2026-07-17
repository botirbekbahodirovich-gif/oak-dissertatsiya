"""ATMOS to'lov moduli — payments_bp (kartadan to'lov: UzCard/Humo).

Mahsulotlar va narxlar FAQAT shu yerda (server tomonida) — mijoz faqat
product_code yuboradi:
  topic_analysis_1   5 000 so'm — 1 ta AI mavzu tahlili krediti
  csv_export_1      10 000 so'm — 1 ta to'liq CSV eksport (5 000 qatorgacha)
  premium_month     29 000 so'm — Premium 30 kun
  premium_year     249 000 so'm — Premium 365 kun

ATMOS oqimi (docs.atmos.uz, "Merchant API" + "Платежная страница"):
  1) POST {ATMOS_API_BASE}/token — Basic(consumer_key:consumer_secret),
     grant_type=client_credentials → access_token (1 soat, modulda kesh).
  2) POST {ATMOS_API_BASE}/merchant/pay/create
     {amount (tiyin), account (bizning payments.id), store_id, lang}
     → transaction_id (draft).
  3) Foydalanuvchi checkout sahifasiga yo'naltiriladi:
     {CHECKOUT}/invoice/get?storeId=..&transactionId=..&redirectLink=/pay/return
  4) Callback: ATMOS → POST /api/pay/atmos/callback (JSON):
     {store_id, transaction_id, transaction_time, amount, invoice, sign}
     sign = HASH(store_id + transaction_id + invoice + amount + api_key)
     (ajratuvchisiz birlashtirish; docs hash turini aytmaydi — odatda MD5,
      ATMOS_CALLBACK_ALGO bilan almashtiriladi). Tekshiruvlar:
       imzo (hmac.compare_digest) → payments qatori → summa → idempotentlik →
       server-side /merchant/pay/get bilan qayta tasdiqlash → BIR tranzaksiyada
       status='paid' + grant_entitlement(). Javob: {"status":1,"message":...}.

Boshqa modullarga eksport qilinadigan yagona haqiqat manbai:
  user_has_premium(user_id)            — barcha paywall'lar shu orqali
  consume_credit(user_id, entitlement) — atomik kredit yechish (race-safe)

Sxema lazy + idempotent (_ensure_schema) — boshqa blueprint'lar kabi.
Kartaning hech qanday rekviziti bizning serverga kelmaydi (redirect modeli).
"""
import base64
import hashlib
import hmac
import logging
import os
import secrets
import time

import requests
from flask import Blueprint, jsonify, redirect, render_template, request
from flask_login import current_user, login_required

from data import get_connection

logger = logging.getLogger(__name__)

payments_bp = Blueprint('payments', __name__)

# ── Konfiguratsiya (.env) — maxfiy qiymatlar hech qachon loglanmaydi ─────────
ATMOS_CONSUMER_KEY = os.environ.get('ATMOS_CONSUMER_KEY', '')
ATMOS_CONSUMER_SECRET = os.environ.get('ATMOS_CONSUMER_SECRET', '')
ATMOS_STORE_ID = os.environ.get('ATMOS_STORE_ID', '')
ATMOS_API_KEY = os.environ.get('ATMOS_API_KEY', '')          # callback sign kaliti
ATMOS_API_BASE = os.environ.get('ATMOS_API_BASE', 'https://apigw.atmos.uz').rstrip('/')
ATMOS_TEST_MODE = os.environ.get('ATMOS_TEST_MODE', 'false').lower() in ('1', 'true', 'yes')
ATMOS_CALLBACK_ALGO = os.environ.get('ATMOS_CALLBACK_ALGO', 'md5')  # md5|sha1|sha256
ATMOS_CHECKOUT_BASE = os.environ.get(
    'ATMOS_CHECKOUT_BASE',
    'https://test-checkout.pays.uz' if ATMOS_TEST_MODE else 'https://checkout.pays.uz'
).rstrip('/')
ATMOS_TIMEOUT = 15  # soniya — tashqi so'rovlar uchun

# Narxlar TIYINDA (1 so'm = 100 tiyin). MIJOZDAN SUMMA QABUL QILINMAYDI.
PRICES = {
    'topic_analysis_1': 500_000,      # 5 000 so'm
    'csv_export_1': 1_000_000,        # 10 000 so'm
    'premium_month': 2_900_000,       # 29 000 so'm
    'premium_year': 24_900_000,       # 249 000 so'm
}
PRODUCT_LABELS = {
    'topic_analysis_1': 'AI mavzu tahlili (1 marta)',
    'csv_export_1': "To'liq CSV eksport (1 marta)",
    'premium_month': 'Premium obuna — 1 oy',
    'premium_year': 'Premium obuna — 1 yil',
}
# product_code → (entitlement, kredit soni yoki premium kunlari)
PRODUCT_GRANTS = {
    'topic_analysis_1': ('topic_analysis_credit', 1),
    'csv_export_1': ('csv_export_credit', 1),
    'premium_month': ('premium', 30),
    'premium_year': ('premium', 365),
}
STATUS_LABELS = {
    'created': 'Yaratildi', 'pending': 'Kutilmoqda', 'paid': "To'landi",
    'failed': 'Muvaffaqiyatsiz', 'cancelled': 'Bekor qilindi',
    'refunded': 'Qaytarildi',
}

_schema_ready = False
# per-worker holat: ATMOS token keshi va oddiy rate-limit xotiralari
_token_cache = {'token': None, 'exp': 0.0}
_cb_hits = {}            # {ip: [timestamps]} — callback rate-limit
_status_checked = {}     # {payment_id: last_ts} — pay/get polling throttle
CB_RATE_MAX = 30         # bitta IP dan daqiqasiga maksimal callback
STATUS_CHECK_MIN_GAP = 5  # soniya — bitta to'lov uchun pay/get oralig'i


# ── Sxema ────────────────────────────────────────────────────────────────────

def _ensure_schema(cur):
    global _schema_ready
    if _schema_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            product_code VARCHAR(40) NOT NULL,
            amount INTEGER NOT NULL,
            currency VARCHAR(3) DEFAULT 'UZS',
            status VARCHAR(20) NOT NULL DEFAULT 'created'
                CHECK (status IN ('created','pending','paid','failed','cancelled','refunded')),
            provider VARCHAR(20) DEFAULT 'atmos',
            provider_transaction_id VARCHAR(100),
            idempotency_key VARCHAR(64) UNIQUE NOT NULL,
            paid_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            meta JSONB DEFAULT '{}'
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_user "
                "ON payments(user_id, status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_provider_tx "
                "ON payments(provider_transaction_id)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_entitlements (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            entitlement VARCHAR(40) NOT NULL,
            credits_remaining INTEGER,
            valid_until TIMESTAMP,
            source_payment_id INTEGER REFERENCES payments(id),
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_entitlements_user "
                "ON user_entitlements(user_id, entitlement)")
    # Har bir callback xom holda saqlanadi — nizolarni hal qilish uchun.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments_log (
            id SERIAL PRIMARY KEY,
            payment_id INTEGER,
            provider_transaction_id VARCHAR(100),
            event VARCHAR(40),
            raw TEXT,
            ip VARCHAR(64),
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_premium BOOLEAN DEFAULT FALSE")
    _schema_ready = True


def _log_event(cur, event, payment_id=None, tx_id=None, raw=''):
    cur.execute(
        "INSERT INTO payments_log (payment_id, provider_transaction_id, event, raw, ip) "
        "VALUES (%s, %s, %s, %s, %s)",
        (payment_id, str(tx_id) if tx_id else None, event,
         (raw or '')[:8000], (request.remote_addr or '')[:64] if request else None))


# ── Yagona haqiqat manbai: premium va kreditlar (barcha paywall'lar uchun) ───

def _scalar(row):
    """Birinchi ustun qiymati — chaqiruvchi cursor tuple ham, RealDict ham
    bo'lishi mumkin (masalan topic_analysis RealDictCursor uzatadi)."""
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()), None)
    return row[0]


def user_has_premium(user_id, cur=None):
    """users.is_premium YOKI amaldagi premium entitlement (valid_until > NOW())."""
    if not user_id:
        return False
    own = cur is None
    conn = None
    try:
        if own:
            conn = get_connection()
            cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute(
            """
            SELECT COALESCE((SELECT is_premium FROM users WHERE id = %s), FALSE)
                OR EXISTS (SELECT 1 FROM user_entitlements
                           WHERE user_id = %s AND entitlement = 'premium'
                             AND valid_until > NOW())
            """, (user_id, user_id))
        val = _scalar(cur.fetchone())
        if own:
            conn.commit()
        return bool(val)
    except Exception:
        logger.exception('user_has_premium failed')
        if own and conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if own and conn:
            conn.close()


def consume_credit(user_id, entitlement, cur=None):
    """Bitta kreditni atomik yechadi (race-safe): eng eski qatorni FOR UPDATE
    SKIP LOCKED bilan qulflab kamaytiradi. True = yechildi. cur berilsa —
    commit chaqiruvchining zimmasida (o'z tranzaksiyasi ichida yechish uchun)."""
    if not user_id:
        return False
    own = cur is None
    conn = None
    try:
        if own:
            conn = get_connection()
            cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute(
            """
            UPDATE user_entitlements
               SET credits_remaining = credits_remaining - 1
             WHERE id = (SELECT id FROM user_entitlements
                          WHERE user_id = %s AND entitlement = %s
                            AND credits_remaining > 0
                          ORDER BY id
                          FOR UPDATE SKIP LOCKED
                          LIMIT 1)
             RETURNING id
            """, (user_id, entitlement))
        ok = cur.fetchone() is not None
        if own:
            conn.commit()
        return ok
    except Exception:
        logger.exception('consume_credit failed')
        if own and conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if own and conn:
            conn.close()


def credits_remaining(user_id, entitlement, cur=None):
    """Qolgan kreditlar soni (kabinet/paywall ko'rsatkichi uchun)."""
    if not user_id:
        return 0
    own = cur is None
    conn = None
    try:
        if own:
            conn = get_connection()
            cur = conn.cursor()
        _ensure_schema(cur)
        cur.execute(
            "SELECT COALESCE(SUM(credits_remaining), 0) FROM user_entitlements "
            "WHERE user_id = %s AND entitlement = %s", (user_id, entitlement))
        n = int(_scalar(cur.fetchone()) or 0)
        if own:
            conn.commit()
        return n
    except Exception:
        if own and conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return 0
    finally:
        if own and conn:
            conn.close()


def grant_entitlement(cur, payment):
    """FAQAT tasdiqlangan to'lovdan chaqiriladi (callback/pay-get verify).
    Kredit: har bir xarid alohida qator (refund'da aynan shu qator bekor
    qilinadi). Premium: amaldagi muddat USTIGA qo'shiladi (hech qachon
    qisqartirmaydi) va users.is_premium=TRUE qilinadi."""
    entitlement, qty = PRODUCT_GRANTS[payment['product_code']]
    if entitlement == 'premium':
        cur.execute(
            "SELECT MAX(valid_until) FROM user_entitlements "
            "WHERE user_id = %s AND entitlement = 'premium' AND valid_until > NOW()",
            (payment['user_id'],))
        base = _scalar(cur.fetchone())
        if base:
            cur.execute(
                "INSERT INTO user_entitlements "
                "(user_id, entitlement, valid_until, source_payment_id) "
                "VALUES (%s, 'premium', %s + make_interval(days => %s), %s)",
                (payment['user_id'], base, qty, payment['id']))
        else:
            cur.execute(
                "INSERT INTO user_entitlements "
                "(user_id, entitlement, valid_until, source_payment_id) "
                "VALUES (%s, 'premium', NOW() + make_interval(days => %s), %s)",
                (payment['user_id'], qty, payment['id']))
        cur.execute("UPDATE users SET is_premium = TRUE WHERE id = %s",
                    (payment['user_id'],))
    else:
        cur.execute(
            "INSERT INTO user_entitlements "
            "(user_id, entitlement, credits_remaining, source_payment_id) "
            "VALUES (%s, %s, %s, %s)",
            (payment['user_id'], entitlement, qty, payment['id']))


def _revoke_entitlement(cur, payment):
    """Admin refund: shu to'lovdan kelgan, hali ISHLATILMAGAN entitlementni
    bekor qiladi. Premium bo'lsa users.is_premium qolgan aktiv entitlementlar
    bo'yicha qayta hisoblanadi (qo'lda berilgan flag ham shu yerda o'chishi
    mumkin — admin panelda ogohlantirilgan)."""
    cur.execute(
        "SELECT id, entitlement, credits_remaining, valid_until "
        "FROM user_entitlements WHERE source_payment_id = %s", (payment['id'],))
    revoked = False
    for ent_id, ent, credits, _valid in cur.fetchall():
        if ent == 'premium':
            cur.execute("DELETE FROM user_entitlements WHERE id = %s", (ent_id,))
            cur.execute(
                """
                UPDATE users SET is_premium = EXISTS (
                    SELECT 1 FROM user_entitlements
                    WHERE user_id = %s AND entitlement = 'premium'
                      AND valid_until > NOW())
                WHERE id = %s
                """, (payment['user_id'], payment['user_id']))
            revoked = True
        elif credits and credits > 0:
            cur.execute("DELETE FROM user_entitlements WHERE id = %s", (ent_id,))
            revoked = True
    return revoked


# ── ATMOS API mijozi ─────────────────────────────────────────────────────────

def _atmos_token(force=False):
    """OAuth2 client-credentials token (modulda keshlanadi, 401 da yangilanadi)."""
    now = time.time()
    if not force and _token_cache['token'] and now < _token_cache['exp'] - 60:
        return _token_cache['token']
    basic = base64.b64encode(
        f'{ATMOS_CONSUMER_KEY}:{ATMOS_CONSUMER_SECRET}'.encode()).decode()
    resp = requests.post(
        f'{ATMOS_API_BASE}/token',
        data={'grant_type': 'client_credentials'},
        headers={'Authorization': f'Basic {basic}',
                 'Content-Type': 'application/x-www-form-urlencoded'},
        timeout=ATMOS_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    token = data.get('access_token')
    if not token:
        raise RuntimeError('ATMOS token javobida access_token yo\'q')
    _token_cache['token'] = token
    _token_cache['exp'] = now + float(data.get('expires_in') or 3600)
    return token


def _atmos_call(path, payload):
    """Bearer bilan POST; 401 kelsa token yangilanib bir marta qayta uriniladi."""
    token = _atmos_token()
    url = f'{ATMOS_API_BASE}{path}'
    resp = requests.post(url, json=payload,
                         headers={'Authorization': f'Bearer {token}'},
                         timeout=ATMOS_TIMEOUT)
    if resp.status_code == 401:
        token = _atmos_token(force=True)
        resp = requests.post(url, json=payload,
                             headers={'Authorization': f'Bearer {token}'},
                             timeout=ATMOS_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _atmos_configured():
    return bool(ATMOS_CONSUMER_KEY and ATMOS_CONSUMER_SECRET and ATMOS_STORE_ID)


def _checkout_url(transaction_id):
    return (f'{ATMOS_CHECKOUT_BASE}/invoice/get'
            f'?storeId={ATMOS_STORE_ID}&transactionId={transaction_id}'
            f'&redirectLink={request.url_root.rstrip("/")}/pay/return')


def _verify_with_atmos(tx_id):
    """Server-side tasdiqlash: /merchant/pay/get — callback tanasiga yolg'iz
    ishonilmaydi. True faqat confirmed=true bo'lganda. Test rejimida ATMOS
    hisob ma'lumotlari yo'q bo'lsa (lokal simulyatsiya) o'tkazib yuboriladi."""
    if ATMOS_TEST_MODE and not _atmos_configured():
        logger.warning('ATMOS_TEST_MODE: pay/get tekshiruvi o\'tkazib yuborildi '
                       '(hisob ma\'lumotlari sozlanmagan)')
        return True
    try:
        data = _atmos_call('/merchant/pay/get',
                           {'store_id': ATMOS_STORE_ID, 'transaction_id': tx_id})
        st = data.get('store_transaction') or {}
        return bool(st.get('confirmed'))
    except Exception:
        logger.exception('ATMOS pay/get failed (tx=%s)', tx_id)
        return False


def _callback_sign_ok(payload):
    """sign = HASH(store_id + transaction_id + invoice + amount + api_key),
    ajratuvchisiz; qiymatlar payload'dagi ko'rinishida birlashtiriladi."""
    if not ATMOS_API_KEY:
        # Imzo kaliti sozlanmagan: faqat server-side pay/get tasdiqlashiga
        # tayanamiz (u majburiy) — lekin bu holat log qilinadi.
        logger.warning('ATMOS_API_KEY sozlanmagan — callback imzosi tekshirilmadi')
        return True
    algo = {'md5': hashlib.md5, 'sha1': hashlib.sha1,
            'sha256': hashlib.sha256}.get(ATMOS_CALLBACK_ALGO, hashlib.md5)
    raw = (str(payload.get('store_id', '')) + str(payload.get('transaction_id', ''))
           + str(payload.get('invoice', '')) + str(payload.get('amount', ''))
           + ATMOS_API_KEY)
    expected = algo(raw.encode()).hexdigest()
    return hmac.compare_digest(expected, str(payload.get('sign', '')))


def _cb_rate_limited(ip):
    now = time.time()
    hits = [t for t in _cb_hits.get(ip, []) if now - t < 60]
    hits.append(now)
    _cb_hits[ip] = hits
    return len(hits) > CB_RATE_MAX


# ── To'lov yaratish ──────────────────────────────────────────────────────────

@payments_bp.route('/api/pay/create', methods=['POST'])
@login_required
def pay_create():
    data = request.get_json(silent=True) or {}
    product_code = (data.get('product_code') or '').strip()
    if product_code not in PRICES:
        return jsonify({'error': 'invalid_product',
                        'message': "Noma'lum mahsulot"}), 400
    if not _atmos_configured() and not ATMOS_TEST_MODE:
        return jsonify({'error': 'not_configured',
                        'message': "To'lov xizmati hozircha sozlanmagan. "
                                   "Keyinroq urinib ko'ring."}), 503
    amount = PRICES[product_code]  # tiyin — faqat server jadvalidan
    conn = get_connection()
    payment_id = None
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute(
                "INSERT INTO payments (user_id, product_code, amount, idempotency_key) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (current_user.id, product_code, amount, secrets.token_hex(32)))
            payment_id = cur.fetchone()[0]
            _log_event(cur, 'create', payment_id=payment_id,
                       raw=f'product={product_code} amount={amount}')
        conn.commit()

        if ATMOS_TEST_MODE and not _atmos_configured():
            # Lokal simulyatsiya: ATMOS'siz pending holatga o'tkazamiz —
            # callback simulyatori (test) shu tranzaksiyani "to'laydi".
            fake_tx = f'test-{payment_id}'
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE payments SET provider_transaction_id = %s, "
                    "status = 'pending', updated_at = NOW() WHERE id = %s",
                    (fake_tx, payment_id))
            conn.commit()
            return jsonify({'redirect_url': f'/pay/return?payment_id={payment_id}',
                            'payment_id': payment_id, 'test_mode': True})

        created = _atmos_call('/merchant/pay/create', {
            'amount': amount,
            'account': str(payment_id),
            'store_id': ATMOS_STORE_ID,
            'lang': 'uz',
        })
        result_code = ((created.get('result') or {}).get('code') or '').upper()
        tx_id = created.get('transaction_id')
        if result_code != 'OK' or not tx_id:
            raise RuntimeError(f'ATMOS create javobi: {result_code}')
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE payments SET provider_transaction_id = %s, "
                "status = 'pending', updated_at = NOW() WHERE id = %s",
                (str(tx_id), payment_id))
            _log_event(cur, 'atmos_created', payment_id=payment_id, tx_id=tx_id)
        conn.commit()
        return jsonify({'redirect_url': _checkout_url(tx_id),
                        'payment_id': payment_id})
    except Exception:
        logger.exception('pay_create failed (payment_id=%s)', payment_id)
        try:
            conn.rollback()
            if payment_id:
                with conn.cursor() as cur:
                    cur.execute("UPDATE payments SET status = 'failed', "
                                "updated_at = NOW() WHERE id = %s AND status = 'created'",
                                (payment_id,))
                conn.commit()
        except Exception:
            pass
        return jsonify({'error': 'provider_error',
                        'message': "To'lovni boshlashda xatolik yuz berdi. "
                                   "Iltimos, birozdan so'ng qayta urinib ko'ring."}), 502
    finally:
        conn.close()


# ── Tasdiqlash: callback + qaytish sahifasi ──────────────────────────────────

def _fetch_payment(cur, where, params):
    cur.execute(
        "SELECT id, user_id, product_code, amount, status, provider_transaction_id "
        f"FROM payments WHERE {where} LIMIT 1", params)
    row = cur.fetchone()
    if not row:
        return None
    return {'id': row[0], 'user_id': row[1], 'product_code': row[2],
            'amount': row[3], 'status': row[4], 'provider_transaction_id': row[5]}


def _mark_paid_and_grant(cur, payment):
    """BIR tranzaksiya ichida: paid-belgilash (idempotent — WHERE status)
    + entitlement berish. False = allaqachon paid (takroriy callback)."""
    cur.execute(
        "UPDATE payments SET status = 'paid', paid_at = NOW(), updated_at = NOW() "
        "WHERE id = %s AND status IN ('created','pending') RETURNING id",
        (payment['id'],))
    if cur.fetchone() is None:
        return False
    grant_entitlement(cur, payment)
    return True


@payments_bp.route('/api/pay/atmos/callback', methods=['POST'])
def atmos_callback():
    """ATMOS server-to-server tasdiqlash so'rovi. Auth: imzo (api_key) +
    majburiy server-side pay/get qayta tekshiruvi. Hech qachon callback
    tanasiga yolg'iz ishonilmaydi."""
    ip = request.remote_addr or ''
    if _cb_rate_limited(ip):
        return jsonify({'status': 0, 'message': 'Rate limited'}), 429
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    raw = request.get_data(as_text=True)[:8000]

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            _log_event(cur, 'callback', tx_id=payload.get('transaction_id'), raw=raw)
        conn.commit()

        if not _callback_sign_ok(payload):
            with conn.cursor() as cur:
                _log_event(cur, 'callback_bad_sign',
                           tx_id=payload.get('transaction_id'), raw=raw)
            conn.commit()
            return jsonify({'status': 0, 'message': 'Invalid signature'})

        tx_id = str(payload.get('transaction_id') or '')
        invoice = str(payload.get('invoice') or '')
        with conn.cursor() as cur:
            payment = _fetch_payment(
                cur, "provider_transaction_id = %s", (tx_id,)) if tx_id else None
            if payment is None and invoice.isdigit():
                payment = _fetch_payment(cur, "id = %s", (int(invoice),))
        if payment is None:
            with conn.cursor() as cur:
                _log_event(cur, 'callback_no_payment', tx_id=tx_id, raw=raw)
            conn.commit()
            return jsonify({'status': 0,
                            'message': f'Invoice {invoice or tx_id} topilmadi'})

        # Idempotentlik: allaqachon paid → darhol muvaffaqiyat (qayta grant yo'q).
        if payment['status'] == 'paid':
            return jsonify({'status': 1, 'message': 'Muvaffaqiyatli (allaqachon)'})

        # Summa AYNAN mos kelishi shart.
        try:
            cb_amount = int(str(payload.get('amount')))
        except (TypeError, ValueError):
            cb_amount = -1
        if cb_amount != payment['amount']:
            with conn.cursor() as cur:
                _log_event(cur, 'callback_amount_mismatch',
                           payment_id=payment['id'], tx_id=tx_id,
                           raw=f'expected={payment["amount"]} got={cb_amount}')
            conn.commit()
            return jsonify({'status': 0, 'message': 'Summa mos emas'})

        # Server-side qayta tasdiqlash (majburiy himoya qatlami).
        if not _verify_with_atmos(payment['provider_transaction_id'] or tx_id):
            with conn.cursor() as cur:
                _log_event(cur, 'callback_verify_failed',
                           payment_id=payment['id'], tx_id=tx_id, raw=raw)
            conn.commit()
            return jsonify({'status': 0, 'message': 'Tasdiqlanmadi'})

        with conn.cursor() as cur:
            granted = _mark_paid_and_grant(cur, payment)
            _log_event(cur, 'paid' if granted else 'paid_duplicate',
                       payment_id=payment['id'], tx_id=tx_id)
        conn.commit()
        return jsonify({'status': 1, 'message': 'Muvaffaqiyatli'})
    except Exception:
        logger.exception('atmos_callback failed')
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({'status': 0, 'message': 'Server xatosi'}), 500
    finally:
        conn.close()


@payments_bp.route('/pay/return')
@login_required
def pay_return():
    """Foydalanuvchi brauzeri checkout'dan qaytadigan sahifa. To'lov holatini
    ko'rsatadi; pending bo'lsa /api/pay/status ni 3s da poll qiladi."""
    payment_id = request.args.get('payment_id', type=int)
    conn = get_connection()
    payment = None
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            if payment_id:
                payment = _fetch_payment(
                    cur, "id = %s AND user_id = %s", (payment_id, current_user.id))
            if payment is None:
                # redirectLink'da payment_id yo'qolgan bo'lishi mumkin —
                # foydalanuvchining oxirgi to'lovini ko'rsatamiz.
                cur.execute(
                    "SELECT id, user_id, product_code, amount, status, "
                    "provider_transaction_id FROM payments WHERE user_id = %s "
                    "ORDER BY id DESC LIMIT 1", (current_user.id,))
                row = cur.fetchone()
                if row:
                    payment = {'id': row[0], 'user_id': row[1], 'product_code': row[2],
                               'amount': row[3], 'status': row[4],
                               'provider_transaction_id': row[5]}
        conn.commit()
    except Exception:
        logger.exception('pay_return failed')
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()
    feature_urls = {'topic_analysis_1': '/mavzu/tahlil', 'csv_export_1': '/dashboard',
                    'premium_month': '/premium', 'premium_year': '/premium'}
    return render_template(
        'pay_return.html', payment=payment,
        product_label=PRODUCT_LABELS.get(payment['product_code'], '') if payment else '',
        feature_url=feature_urls.get(payment['product_code'], '/') if payment else '/',
        status_labels=STATUS_LABELS)


@payments_bp.route('/api/pay/status/<int:payment_id>')
@login_required
def pay_status(payment_id):
    """Pending sahifasi polling'i. Callback kechiksa — pay/get orqali aktiv
    tekshiradi (throttle bilan) va tasdiqlangan bo'lsa o'zi grant qiladi."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            payment = _fetch_payment(
                cur, "id = %s AND user_id = %s", (payment_id, current_user.id))
        if payment is None:
            return jsonify({'error': 'not_found'}), 404
        if (payment['status'] == 'pending' and payment['provider_transaction_id']
                and not str(payment['provider_transaction_id']).startswith('test-')):
            now = time.time()
            if now - _status_checked.get(payment_id, 0) >= STATUS_CHECK_MIN_GAP:
                _status_checked[payment_id] = now
                if _verify_with_atmos(payment['provider_transaction_id']):
                    with conn.cursor() as cur:
                        granted = _mark_paid_and_grant(cur, payment)
                        _log_event(cur, 'paid_via_poll' if granted else 'paid_duplicate',
                                   payment_id=payment_id,
                                   tx_id=payment['provider_transaction_id'])
                    conn.commit()
                    payment['status'] = 'paid'
        conn.commit()
        return jsonify({'status': payment['status']})
    except Exception:
        logger.exception('pay_status failed')
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({'error': 'server_error'}), 500
    finally:
        conn.close()


# ── /premium narx sahifasi ───────────────────────────────────────────────────

@payments_bp.route('/premium')
def premium_page():
    is_auth = getattr(current_user, 'is_authenticated', False)
    uid = current_user.id if is_auth else None
    premium = user_has_premium(uid) if uid else False
    return render_template(
        'premium.html',
        is_premium=premium,
        prices_som={k: v // 100 for k, v in PRICES.items()},
        test_mode=ATMOS_TEST_MODE,
        is_admin=getattr(current_user, 'is_admin', False))


# ── Kabinet: To'lovlarim ─────────────────────────────────────────────────────

@payments_bp.route('/cabinet/tolovlar')
@login_required
def my_payments():
    uid = current_user.id
    history, premium_until, credits = [], None, {}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute(
                "SELECT id, product_code, amount, status, created_at, paid_at "
                "FROM payments WHERE user_id = %s ORDER BY id DESC LIMIT 100", (uid,))
            history = [{
                'id': r[0], 'product': PRODUCT_LABELS.get(r[1], r[1]),
                'amount_som': (r[2] or 0) // 100,
                'status': r[3], 'status_label': STATUS_LABELS.get(r[3], r[3]),
                'created_at': r[4], 'paid_at': r[5],
            } for r in cur.fetchall()]
            cur.execute(
                "SELECT MAX(valid_until) FROM user_entitlements "
                "WHERE user_id = %s AND entitlement = 'premium' AND valid_until > NOW()",
                (uid,))
            row = cur.fetchone()
            premium_until = row[0] if row else None
            credits = {
                'topic_analysis': credits_remaining(uid, 'topic_analysis_credit', cur),
                'csv_export': credits_remaining(uid, 'csv_export_credit', cur),
            }
        conn.commit()
    except Exception:
        logger.exception('my_payments failed')
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()
    return render_template('payments_history.html', history=history,
                           premium_until=premium_until,
                           is_premium=user_has_premium(uid), credits=credits)


# ── Admin: /admin/payments ───────────────────────────────────────────────────

@payments_bp.route('/admin/payments')
@login_required
def admin_payments():
    if not getattr(current_user, 'is_admin', False):
        return redirect('/')
    status = request.args.get('status', '').strip()
    date_from = request.args.get('from', '').strip()
    date_to = request.args.get('to', '').strip()
    where, params = ["1=1"], []
    if status in STATUS_LABELS:
        where.append("p.status = %s")
        params.append(status)
    if date_from:
        where.append("p.created_at >= %s::date")
        params.append(date_from)
    if date_to:
        where.append("p.created_at < %s::date + 1")
        params.append(date_to)
    rows, totals = [], {'today': 0, 'month': 0, 'count': 0}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute(
                f"""
                SELECT p.id, u.username, u.email, p.product_code, p.amount,
                       p.status, p.provider_transaction_id, p.created_at, p.paid_at
                FROM payments p JOIN users u ON u.id = p.user_id
                WHERE {' AND '.join(where)}
                ORDER BY p.id DESC LIMIT 300
                """, params)
            rows = [{
                'id': r[0], 'username': r[1], 'email': r[2],
                'product': PRODUCT_LABELS.get(r[3], r[3]),
                'amount_som': (r[4] or 0) // 100, 'status': r[5],
                'status_label': STATUS_LABELS.get(r[5], r[5]),
                'provider_tx': r[6], 'created_at': r[7], 'paid_at': r[8],
            } for r in cur.fetchall()]
            cur.execute(
                """
                SELECT COALESCE(SUM(amount) FILTER (WHERE paid_at >= CURRENT_DATE), 0),
                       COALESCE(SUM(amount) FILTER (
                           WHERE paid_at >= date_trunc('month', NOW())), 0),
                       COUNT(*) FILTER (WHERE status = 'paid')
                FROM payments WHERE status IN ('paid', 'refunded')
                """)
            t = cur.fetchone()
            totals = {'today': (t[0] or 0) // 100, 'month': (t[1] or 0) // 100,
                      'count': t[2] or 0}
        conn.commit()
    except Exception:
        logger.exception('admin_payments failed')
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()
    return render_template('admin_payments.html', rows=rows, totals=totals,
                           status_labels=STATUS_LABELS, cur_status=status,
                           date_from=date_from, date_to=date_to)


@payments_bp.route('/admin/payments/<int:payment_id>/refund', methods=['POST'])
@login_required
def admin_refund(payment_id):
    """Support holati uchun: 'Refunded deb belgilash' — statusni o'zgartiradi
    va ISHLATILMAGAN entitlementni bekor qiladi. Pulni qaytarish ATMOS
    kabinetida qo'lda amalga oshiriladi (v1 da API chaqirig'i yo'q)."""
    if not getattr(current_user, 'is_admin', False):
        return redirect('/')
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            payment = _fetch_payment(cur, "id = %s", (payment_id,))
            if payment is None or payment['status'] != 'paid':
                conn.commit()
                return redirect('/admin/payments')
            cur.execute(
                "UPDATE payments SET status = 'refunded', updated_at = NOW() "
                "WHERE id = %s AND status = 'paid'", (payment_id,))
            revoked = _revoke_entitlement(cur, payment)
            _log_event(cur, 'refunded' if revoked else 'refunded_used',
                       payment_id=payment_id,
                       raw=f'by_admin={current_user.id}')
        conn.commit()
    except Exception:
        logger.exception('admin_refund failed')
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()
    return redirect('/admin/payments')
