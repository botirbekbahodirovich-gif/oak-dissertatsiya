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
        return None
    try:
        import psycopg2
        conn = psycopg2.connect(database_url)
        cur = conn.cursor()
        cur.execute("SELECT id, username, email FROM users WHERE id = %s", (int(user_id),))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return User(*row) if row else None
    except Exception:
        return None

from auth import auth_bp
from data import data_bp, query_dissertations
csrf.exempt(data_bp)
from analytics import analytics_bp
from upload import upload_bp

app.register_blueprint(auth_bp)
app.register_blueprint(data_bp)
app.register_blueprint(analytics_bp)
app.register_blueprint(upload_bp)


@app.route("/")
def home():
    try:
        rows = query_dissertations("", "", "", "", "id", "desc", page=1, per_page=6)
    except Exception:
        rows = []
    recent = []
    if rows:
        recent = [{
            "id": row.get("id"),
            "Olim": row.get("Olim", ""),
            "Mavzu": row.get("Mavzu", ""),
            "Daraja": row.get("Daraja", ""),
            "Sana": row.get("Sana", ""),
            "Muassasa": row.get("Muassasa", ""),
        } for row in rows]
    return render_template("home.html", recent=recent)


@app.route("/dashboard")
@login_required
def index():
    return render_template("dashboard.html")


@app.route("/stats")
@login_required
def stats_page():
    return render_template("stats.html")


@app.route("/profile")
@login_required
def profile():
    from data import get_connection
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute('''
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE UPPER(TRIM(daraja)) = 'PHD') AS phd,
                    COUNT(*) FILTER (WHERE UPPER(TRIM(daraja)) = 'DSC') AS dsc,
                    COUNT(DISTINCT NULLIF(TRIM(muassasa), '')) AS muassasalar
                FROM dissertations
            ''')
            row = cur.fetchone()
            stats = {"total": row[0] or 0, "phd": row[1] or 0, "dsc": row[2] or 0, "muassasalar": row[3] or 0}
        conn.close()
    except Exception:
        stats = {"total": 0, "phd": 0, "dsc": 0, "muassasalar": 0}
    return render_template("profile.html", user=current_user, stats=stats)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
