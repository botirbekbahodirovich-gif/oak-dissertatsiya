from flask import Blueprint, render_template, request, redirect, url_for
from flask_login import current_user, login_user, logout_user, login_required
import sqlite3
import bcrypt

auth_bp = Blueprint('auth', __name__)


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
            # lazy import to avoid circular import
            from app import DB_PATH, User, is_safe_relative_url
            # prefer PostgreSQL if DATABASE_URL present
            from dotenv import load_dotenv
            load_dotenv()
            DATABASE_URL = os.environ.get('DATABASE_URL')
            user_row = None
            if DATABASE_URL:
                try:
                    import psycopg2
                    conn = psycopg2.connect(DATABASE_URL)
                    cur = conn.cursor()
                    cur.execute("SELECT id, username, email, password_hash FROM users WHERE username = %s", (username,))
                    user_row = cur.fetchone()
                    cur.close()
                    conn.close()
                except Exception:
                    user_row = None
            if not user_row:
                con = sqlite3.connect(DB_PATH)
                user_row = con.execute(
                    "SELECT id, username, email, password_hash FROM users WHERE username = ?",
                    (username,)
                ).fetchone()
                con.close()
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
        email    = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm', '')
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
                from app import DB_PATH
                # prefer PostgreSQL if available
                from dotenv import load_dotenv
                load_dotenv()
                DATABASE_URL = os.environ.get('DATABASE_URL')
                inserted = False
                if DATABASE_URL:
                    try:
                        import psycopg2
                        conn = psycopg2.connect(DATABASE_URL)
                        cur = conn.cursor()
                        cur.execute("INSERT INTO users (username, email, password_hash) VALUES (%s,%s,%s) RETURNING id", (username, email, pw_hash))
                        uid = cur.fetchone()[0]
                        conn.commit()
                        cur.close()
                        conn.close()
                        inserted = True
                    except psycopg2.IntegrityError as e:
                        # translate unique constraint
                        if 'username' in str(e):
                            error = "Bu foydalanuvchi nomi band."
                        else:
                            error = "Bu email allaqachon ro'yxatdan o'tgan."
                        inserted = False
                    except Exception:
                        inserted = False
                if not inserted:
                    con = sqlite3.connect(DB_PATH)
                    try:
                        con.execute(
                            "INSERT INTO users (username, email, password_hash) VALUES (?,?,?)",
                            (username, email, pw_hash)
                        )
                        con.commit()
                        con.close()
                        return redirect(url_for('login') + '?registered=1')
                    except sqlite3.IntegrityError as e:
                        error = ("Bu foydalanuvchi nomi band." if "username" in str(e)
                                 else "Bu email allaqachon ro'yxatdan o'tgan.")
                else:
                    return redirect(url_for('login') + '?registered=1')
            except Exception:
                error = "Ro'yxatdan o'tishda xatolik yuz berdi."
    return render_template('register.html', error=error)


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))
