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
            con = sqlite3.connect(DB_PATH)
            row = con.execute(
                "SELECT id, username, email, password_hash FROM users WHERE username = ?",
                (username,)
            ).fetchone()
            con.close()
            if row and bcrypt.checkpw(password.encode(), row[3].encode()):
                login_user(User(row[0], row[1], row[2]), remember=True)
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
                con = sqlite3.connect(DB_PATH)
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
    return render_template('register.html', error=error)


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))
