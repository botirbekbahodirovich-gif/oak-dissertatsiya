import os
from dotenv import load_dotenv
from flask_wtf.csrf import CSRFProtect
import bcrypt
from flask import Flask, render_template, redirect, url_for
from urllib.parse import urlparse
from flask_login import (LoginManager, UserMixin, logout_user,
                         login_required, current_user)

app = Flask(__name__)
load_dotenv()
session_secret = os.environ.get("SESSION_SECRET")
if not session_secret:
    raise RuntimeError(
        "SESSION_SECRET is not set. Add it to a .env file or set the environment variable."
    )
app.secret_key = session_secret
csrf = CSRFProtect(app)

from flask_wtf.csrf import generate_csrf

@app.context_processor
def _inject_csrf_token():
    return dict(csrf_token=lambda: '<input type="hidden" name="csrf_token" value="%s">' % generate_csrf())



def is_safe_relative_url(target: str) -> bool:
    if not target:
        return False
    parsed = urlparse(target)
    return parsed.scheme == "" and parsed.netloc == "" and target.startswith("/")

login_manager = LoginManager(app)
login_manager.login_view = "auth.login"
login_manager.login_message = "Iltimos, tizimga kiring."


class User(UserMixin):
    def __init__(self, id, username, email):
        self.id = id
        self.username = username
        self.email = email


@login_manager.user_loader
def load_user(user_id):
    from dotenv import load_dotenv
    load_dotenv()
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        if str(user_id) == '1':
            return User(1, 'admin', 'admin@example.com')
        return None
    try:
        import psycopg2
        conn = psycopg2.connect(database_url)
        cur = conn.cursor()
        cur.execute("SELECT id, username, email FROM users WHERE id = %s", (int(user_id),))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return User(*row)
        if str(user_id) == '1':
            return User(1, 'admin', 'admin@example.com')
        return None
    except Exception:
        if str(user_id) == '1':
            return User(1, 'admin', 'admin@example.com')
        return None

from auth import auth_bp
from data import data_bp, load_data
from analytics import analytics_bp
from upload import upload_bp

app.register_blueprint(auth_bp)
app.register_blueprint(data_bp)
app.register_blueprint(analytics_bp)
app.register_blueprint(upload_bp)


@app.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    rows = load_data()
    recent = []
    if rows:
        recent = sorted(
            rows,
            key=lambda row: row.get("Sana", ""),
            reverse=True
        )[:5]
        recent = [{
            "Olim": row.get("Olim", ""),
            "Mavzu": row.get("Mavzu", ""),
            "Daraja": row.get("Daraja", ""),
            "Sana": row.get("Sana", "")
        } for row in recent]
    return render_template("home.html", recent=recent)


@app.route("/dashboard")
@login_required
def index():
    return render_template("dashboard.html")


@app.route("/stats")
@login_required
def stats_page():
    return render_template("stats.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
