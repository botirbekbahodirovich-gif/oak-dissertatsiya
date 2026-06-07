import os
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required
import pandas as pd
from dotenv import load_dotenv
load_dotenv()
try:
    import psycopg2
    from psycopg2.extras import execute_values
except Exception:
    psycopg2 = None
    execute_values = None

REQUIRED_COLUMNS = {
    "Sana", "Daraja", "Olim", "Mavzu",
    "Ixtisoslik", "Muassasa", "Ilmiy_rahbar", "Link"
}

upload_bp = Blueprint('upload', __name__)


def get_database_url():
    url = os.environ.get('DATABASE_URL', '')
    if not url:
        raise RuntimeError('DATABASE_URL is not set. Add it to Railway environment variables.')
    if 'sqlite' in url.lower():
        raise RuntimeError(
            f'DATABASE_URL looks like SQLite ("{url[:40]}...") — set a PostgreSQL URL instead.'
        )
    return url


def get_connection():
    if not psycopg2:
        raise RuntimeError('psycopg2 is required for PostgreSQL support.')
    return psycopg2.connect(get_database_url())


@upload_bp.route('/upload', methods=['GET'])
@login_required
def upload_page():
    return render_template('upload.html')


@upload_bp.route('/upload', methods=['POST'])
@login_required
def upload_csv():
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "Fayl tanlanmagan."}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({"success": False, "error": "Fayl nomi bo'sh."}), 400
    if not file.filename.lower().endswith('.csv'):
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

    try:
        conn = get_connection()
        cur = conn.cursor()
        values = [(
            row.get('Sana', ''), row.get('Daraja', ''), row.get('Olim', ''),
            row.get('Mavzu', ''), row.get('Ixtisoslik', ''), row.get('Muassasa', ''),
            row.get('Ilmiy_rahbar', ''), row.get('Link', '')
        ) for _, row in df.iterrows()]
        if values:
            execute_values(
                cur,
                "INSERT INTO dissertations (sana, daraja, olim, mavzu, ixtisoslik, muassasa, ilmiy_rahbar, link) VALUES %s",
                values
            )
            conn.commit()
        inserted = len(values)
        cur.close()
        conn.close()
    except Exception as e:
        return jsonify({"success": False, "error": f"Ma'lumotlar bazasiga yozishda xatolik: {e}"}), 500

    return jsonify({
        "success": True,
        "rows": inserted,
        "message": f"Muvaffaqiyatli yuklandi! {inserted} ta yozuv saqlandi."
    })
