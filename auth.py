import os
import hmac
import hashlib
import time
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, session
from flask_login import current_user, login_user, logout_user, login_required
import bcrypt
from dotenv import load_dotenv
load_dotenv()

TELEGRAM_BOT_USERNAME = os.environ.get('TELEGRAM_BOT_USERNAME', 'send_kod_bot')
TELEGRAM_BOT_TOKEN    = os.environ.get('TELEGRAM_BOT_TOKEN', '')
try:
    import psycopg2
    from psycopg2 import errors as psycopg2_errors
except Exception:
    psycopg2 = None
    psycopg2_errors = None

auth_bp = Blueprint('auth', __name__)


def get_database_url():
    from data import get_normalized_db_url
    return get_normalized_db_url()


def get_connection():
    # Delegate to the hardened, pooled connection in data.py (SSL enforced,
    # connect timeout + keepalives) so login works on managed Postgres.
    from data import get_connection as _get_connection
    return _get_connection()


@auth_bp.route('/login')
def login():
    # Parol/username formasi olib tashlandi — faqat Telegram va Google orqali kirish.
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    registered = request.args.get('registered')
    return render_template('login.html', error=None, registered=registered)


@auth_bp.route('/register')
def register():
    # Ro'yxatdan o'tish formasi olib tashlandi — faqat Telegram / Google tugmalari.
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    return render_template('register.html', error=None)


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))


@auth_bp.route('/login/telegram', methods=['POST'])
def telegram_login():
    from app import User
    raw = request.get_json(silent=True) or {}

    if not TELEGRAM_BOT_TOKEN:
        return jsonify({'success': False, 'error': 'TELEGRAM_BOT_TOKEN sozlanmagan'}), 200

    try:
        # Work on a copy so we don't mutate the original dict
        data = dict(raw)
        check_hash = data.pop('hash', '')

        if not check_hash:
            return jsonify({'success': False, 'error': 'Hash mavjud emas'}), 200

        data_check = '\n'.join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hashlib.sha256(TELEGRAM_BOT_TOKEN.encode()).digest()
        computed = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed, check_hash):
            return jsonify({
                'success': False,
                'error': "Bot domain sozlanmagan: @BotFather da /setdomain buyrug'ini bering"
            }), 200

        if time.time() - int(data.get('auth_date', 0)) > 86400:
            return jsonify({'success': False, 'error': "Tasdiqlash muddati o'tgan, qayta urinib ko'ring"}), 200

        tg_id    = str(data.get('id', ''))
        username = (data.get('username') or f"tg_{tg_id}").strip()
        email    = f"{tg_id}@telegram.uz"

        if not tg_id:
            return jsonify({'success': False, 'error': 'Telegram ID topilmadi'}), 200

        conn = get_connection()
        try:
            cur = conn.cursor()
            # Look up by telegram email to handle username changes
            cur.execute("SELECT id, username, email FROM users WHERE email = %s", (email,))
            user_row = cur.fetchone()

            if not user_row:
                # Also check by username in case email lookup misses
                cur.execute("SELECT id, username, email FROM users WHERE username = %s", (username,))
                user_row = cur.fetchone()

            if not user_row:
                cur.execute(
                    "INSERT INTO users (username, email, password_hash, is_admin) "
                    "VALUES (%s, %s, %s, %s) RETURNING id",
                    (username, email, 'telegram_auth', False)
                )
                user_id = cur.fetchone()[0]
                conn.commit()
            else:
                user_id = user_row[0]
                username = user_row[1]
                email    = user_row[2] or email

            cur.close()
        finally:
            conn.close()

    except Exception as e:
        return jsonify({'success': False, 'error': f"Xatolik: {str(e)}"}), 200

    session.permanent = True
    login_user(User(user_id, username, email), remember=True)
    session.modified = True   # sessiya cookie brauzerga aniq yozilishi uchun
    return jsonify({'success': True, 'redirect': '/'})
