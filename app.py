import os
import io
import sqlite3
import bcrypt
import pandas as pd
from flask import (Flask, render_template, request, jsonify,
                   send_file, redirect, url_for)
from flask_login import (LoginManager, UserMixin, login_user,
                         logout_user, login_required, current_user)

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "oak-dissertatsiya-secret-2024")

BASE_DIR   = os.path.dirname(__file__)
DB_PATH    = os.path.join(BASE_DIR, "users.db")
CSV_PATH   = os.path.join(BASE_DIR, "data", "dissertatsiyalar.csv")

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

# ---------------------------------------------------------------------------
# Flask-Login
# ---------------------------------------------------------------------------

login_manager = LoginManager(app)
login_manager.login_view = "login"
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

# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    error = None
    registered = request.args.get("registered")
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            error = "Foydalanuvchi nomi va parol kiritilishi shart."
        else:
            con = sqlite3.connect(DB_PATH)
            row = con.execute(
                "SELECT id, username, email, password_hash FROM users WHERE username = ?",
                (username,)
            ).fetchone()
            con.close()
            if row and bcrypt.checkpw(password.encode(), row[3].encode()):
                login_user(User(row[0], row[1], row[2]), remember=True)
                return redirect(request.args.get("next") or url_for("index"))
            error = "Foydalanuvchi nomi yoki parol noto'g'ri."
    return render_template("login.html", error=error, registered=registered)


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email    = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm", "")
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
                con = sqlite3.connect(DB_PATH)
                con.execute(
                    "INSERT INTO users (username, email, password_hash) VALUES (?,?,?)",
                    (username, email, pw_hash)
                )
                con.commit()
                con.close()
                return redirect(url_for("login") + "?registered=1")
            except sqlite3.IntegrityError as e:
                error = ("Bu foydalanuvchi nomi band." if "username" in str(e)
                         else "Bu email allaqachon ro'yxatdan o'tgan.")
    return render_template("register.html", error=error)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def load_data():
    try:
        df = pd.read_csv(CSV_PATH, dtype=str).fillna("")
        for col in df.columns:
            df[col] = df[col].astype(str).str.strip()
        return df
    except FileNotFoundError:
        return pd.DataFrame(columns=[
            "Sana", "Daraja", "Olim", "Mavzu",
            "Ixtisoslik", "Muassasa", "Ilmiy_rahbar", "Link"
        ])


def apply_filters(df, search, daraja, muassasa, ixtisoslik):
    if search:
        lo = search.lower()
        df = df[df.apply(lambda r: r.astype(str).str.lower().str.contains(lo).any(), axis=1)]
    if daraja:
        df = df[df["Daraja"].str.upper() == daraja.upper()]
    if muassasa:
        df = df[df["Muassasa"] == muassasa]
    if ixtisoslik:
        df = df[df["Ixtisoslik"] == ixtisoslik]
    return df

# ---------------------------------------------------------------------------
# Protected pages
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/stats")
@login_required
def stats_page():
    return render_template("stats.html")


@app.route("/upload", methods=["GET"])
@login_required
def upload_page():
    return render_template("upload.html")

# ---------------------------------------------------------------------------
# Protected JSON API
# ---------------------------------------------------------------------------

@app.route("/stats-json")
@login_required
def stats_json():
    df = load_data()
    return jsonify({
        "total":      len(df),
        "phd":        len(df[df["Daraja"].str.upper() == "PHD"]),
        "dsc":        len(df[df["Daraja"].str.upper() == "DSC"]),
        "muassasalar": df["Muassasa"].nunique()
    })


@app.route("/analytics-data")
@login_required
def analytics_data():
    df = load_data()

    top_muassasalar = (
        df[df["Muassasa"] != ""].groupby("Muassasa").size()
        .nlargest(20).reset_index(name="count")
        .rename(columns={"Muassasa": "muassasa"}).to_dict(orient="records")
    )

    daraja_counts = (
        df[df["Daraja"] != ""].groupby("Daraja").size()
        .reset_index(name="count")
        .rename(columns={"Daraja": "daraja"}).to_dict(orient="records")
    )

    trend_data = []
    sana_series = pd.to_datetime(df["Sana"], errors="coerce").dropna()
    if len(sana_series):
        tmp = pd.DataFrame({"date": sana_series})
        tmp["period"] = tmp["date"].dt.to_period("M").astype(str)
        trend_data = (tmp.groupby("period").size()
                      .reset_index(name="count")
                      .sort_values("period")
                      .to_dict(orient="records"))

    top_ixtisosliklar = (
        df[df["Ixtisoslik"] != ""].groupby("Ixtisoslik").size()
        .nlargest(15).reset_index(name="count")
        .rename(columns={"Ixtisoslik": "ixtisoslik"}).to_dict(orient="records")
    )

    top15_unis = (
        df[df["Muassasa"] != ""].groupby("Muassasa").size()
        .nlargest(15).index.tolist()
    )
    hm_df = df[df["Muassasa"].isin(top15_unis) & (df["Daraja"] != "")]
    if len(hm_df):
        pivot = pd.crosstab(hm_df["Muassasa"], hm_df["Daraja"])
        pivot = pivot.reindex(top15_unis).fillna(0).astype(int)
        heatmap = {
            "muassasalar": pivot.index.tolist(),
            "darajalar":   pivot.columns.tolist(),
            "data":        pivot.values.tolist()
        }
    else:
        heatmap = {"muassasalar": [], "darajalar": [], "data": []}

    return jsonify({
        "top_muassasalar":  top_muassasalar,
        "daraja_ratio":     daraja_counts,
        "trend":            trend_data,
        "top_ixtisosliklar": top_ixtisosliklar,
        "heatmap":          heatmap
    })


@app.route("/filters")
@login_required
def filters():
    df = load_data()
    return jsonify({
        "darajalar":    [d for d in sorted(df["Daraja"].unique()) if d],
        "muassasalar":  [m for m in sorted(df["Muassasa"].unique()) if m],
        "ixtisosliklar":[i for i in sorted(df["Ixtisoslik"].unique()) if i]
    })


@app.route("/data")
@login_required
def data():
    df       = load_data()
    search   = request.args.get("search", "").strip()
    daraja   = request.args.get("daraja", "").strip()
    muassasa = request.args.get("muassasa", "").strip()
    ixtisoslik = request.args.get("ixtisoslik", "").strip()
    page     = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))

    df = apply_filters(df, search, daraja, muassasa, ixtisoslik)
    total = len(df)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start, end = (page - 1) * per_page, page * per_page

    return jsonify({
        "records":     df.iloc[start:end].to_dict(orient="records"),
        "total":       total,
        "page":        page,
        "per_page":    per_page,
        "total_pages": total_pages
    })


REQUIRED_COLUMNS = {
    "Sana", "Daraja", "Olim", "Mavzu",
    "Ixtisoslik", "Muassasa", "Ilmiy_rahbar", "Link"
}


@app.route("/upload", methods=["POST"])
@login_required
def upload_csv():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "Fayl tanlanmagan."}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"success": False, "error": "Fayl nomi bo'sh."}), 400
    if not file.filename.lower().endswith(".csv"):
        return jsonify({"success": False, "error": "Faqat CSV fayl qabul qilinadi."}), 400
    try:
        df = pd.read_csv(file, dtype=str)
    except Exception as e:
        return jsonify({"success": False, "error": f"CSV o'qishda xatolik: {e}"}), 400
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        return jsonify({
            "success": False,
            "error": f"Ustunlar topilmadi: {', '.join(sorted(missing))}"
        }), 400
    df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
    return jsonify({
        "success": True,
        "rows": len(df),
        "message": f"Muvaffaqiyatli yuklandi! {len(df)} ta yozuv saqlandi."
    })


@app.route("/export")
@login_required
def export():
    df = load_data()
    df = apply_filters(
        df,
        request.args.get("search", "").strip(),
        request.args.get("daraja", "").strip(),
        request.args.get("muassasa", "").strip(),
        request.args.get("ixtisoslik", "").strip()
    )
    buf = io.BytesIO(df.to_csv(index=False).encode("utf-8-sig"))
    buf.seek(0)
    return send_file(buf, mimetype="text/csv", as_attachment=True,
                     download_name="dissertatsiyalar_filtrlangan.csv")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
