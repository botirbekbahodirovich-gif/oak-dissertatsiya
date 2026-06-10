import os
import hmac
import hashlib
import time
from flask import Blueprint, render_template, request, redirect, url_for, jsonify
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
    url = os.environ.get('DATABASE_URL', '')
    if not url or url.startswith('sqlite'):
        url = os.environ.get('POSTGRES_URL', '')
    return url


def get_connection():
    if not psycopg2:
        raise RuntimeError('psycopg2 is required for PostgreSQL support.')
    return psycopg2.connect(get_database_url())


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    error = None
    registered = request.args.get('registered')
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not username or not password:
            error = "Foydalanuvchi nomi va parol kiritilishi shart."
        else:
            from app import User, is_safe_relative_url
            user_row = None
            try:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, username, email, password_hash FROM users WHERE username = %s",
                    (username,)
                )
                user_row = cur.fetchone()
                cur.close()
                conn.close()
            except Exception:
                user_row = None

            if user_row and bcrypt.checkpw(password.encode(), user_row[3].encode()):
                login_user(User(user_row[0], user_row[1], user_row[2]), remember=True)
                next_url = request.args.get('next')
                if next_url and is_safe_relative_url(next_url):
                    return redirect(next_url)
                return redirect(url_for('index'))
            error = "Foydalanuvchi nomi yoki parol noto'g'ri."
    return render_template('login.html', error=error, registered=registered)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        if not username or not email or not password:
            error = "Barcha maydonlarni to'ldiring."
        elif len(username) < 3:
            error = "Foydalanuvchi nomi kamida 3 ta belgi bo'lishi kerak."
        elif len(password) < 6:
            error = "Parol kamida 6 ta belgi bo'lishi kerak."
        elif password != confirm:
            error = "Parollar mos kelmadi."
        else:
            pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            try:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s) RETURNING id",
                    (username, email, pw_hash)
                )
                conn.commit()
                cur.close()
                conn.close()
                return redirect(url_for('auth.login', registered=1))
            except psycopg2.IntegrityError as e:
                message = str(e).lower()
                if 'username' in message:
                    error = "Bu foydalanuvchi nomi band."
                elif 'email' in message:
                    error = "Bu email allaqachon ro'yxatdan o'tgan."
                else:
                    error = "Ro'yxatdan o'tishda xatolik yuz berdi."
            except Exception:
                error = "Ro'yxatdan o'tishda xatolik yuz berdi."
    return render_template('register.html', error=error)


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

    login_user(User(user_id, username, email), remember=True)
    return jsonify({'success': True, 'redirect': '/dashboard'})
