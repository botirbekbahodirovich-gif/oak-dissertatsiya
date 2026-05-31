import os
import io
from dotenv import load_dotenv
from flask_wtf.csrf import CSRFProtect
import sqlite3
import bcrypt
import pandas as pd
from flask import (Flask, render_template, request, jsonify,
                   send_file, redirect, url_for)
from urllib.parse import urlparse
from flask_login import (LoginManager, UserMixin, login_user,
                         logout_user, login_required, current_user)

app = Flask(__name__)
load_dotenv()
session_secret = os.environ.get("SESSION_SECRET")
if not session_secret:
    raise RuntimeError(
        "SESSION_SECRET is not set. Add it to a .env file or set the environment variable."
    )
app.secret_key = session_secret
# Enable CSRF protection for all POST forms
csrf = CSRFProtect(app)

BASE_DIR   = os.path.dirname(__file__)
DB_PATH    = os.path.join(BASE_DIR, "users.db")
CSV_PATH   = os.path.join(BASE_DIR, "data", "dissertatsiyalar.csv")

# Simple in-memory cache for CSV data
_csv_cache_df = None
_csv_cache_mtime = None

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            email         TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at    TEXT DEFAULT (datetime('now'))
        )
    """)
    con.commit()
    con.close()

init_db()


def is_safe_relative_url(target: str) -> bool:
    """Allow only relative URLs for redirects to prevent open redirect attacks.

    Return True only if the target is a relative path starting with '/'
    and contains no scheme or network location.
    """
    if not target:
        return False
    parsed = urlparse(target)
    return parsed.scheme == "" and parsed.netloc == "" and target.startswith("/")

# ---------------------------------------------------------------------------
# Flask-Login
# ---------------------------------------------------------------------------

login_manager = LoginManager(app)
login_manager.login_view = "auth.login"
login_manager.login_message = "Iltimos, tizimga kiring."


class User(UserMixin):
    def __init__(self, id, username, email):
        self.id       = id
        self.username = username
        self.email    = email


@login_manager.user_loader
def load_user(user_id):
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT id, username, email FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    con.close()
    return User(*row) if row else None

# Register blueprints (auth, data, analytics, upload)
from auth import auth_bp
from data import data_bp
from analytics import analytics_bp
from upload import upload_bp

app.register_blueprint(auth_bp)
app.register_blueprint(data_bp)
app.register_blueprint(analytics_bp)
app.register_blueprint(upload_bp)


@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/stats")
@login_required
def stats_page():
    return render_template("stats.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
