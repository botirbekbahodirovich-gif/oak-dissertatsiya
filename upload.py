from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required
import pandas as pd
from data import REQUIRED_COLUMNS, CSV_PATH, load_data

upload_bp = Blueprint('upload', __name__)


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
    # Try writing into PostgreSQL if available, otherwise save CSV file
    from dotenv import load_dotenv
    load_dotenv()
    DATABASE_URL = os.environ.get('DATABASE_URL')
    written = 0
    if DATABASE_URL:
        try:
            import psycopg2
            from psycopg2.extras import execute_values
            conn = psycopg2.connect(DATABASE_URL)
            cur = conn.cursor()
            vals = []
            for _, row in df.iterrows():
                vals.append((row.get('Sana',''), row.get('Daraja',''), row.get('Olim',''), row.get('Mavzu',''), row.get('Ixtisoslik',''), row.get('Muassasa',''), row.get('Ilmiy_rahbar',''), row.get('Link','')))
            if vals:
                execute_values(cur, "INSERT INTO dissertations (sana,daraja,olim,mavzu,ixtisoslik,muassasa,ilmiy_rahbar,link) VALUES %s", vals)
                conn.commit()
                written = len(vals)
            cur.close()
            conn.close()
        except Exception:
            written = 0
    if written == 0:
        # fallback to CSV file
        df.to_csv(CSV_PATH, index=False, encoding='utf-8-sig')
        written = len(df)

    return jsonify({
        "success": True,
        "rows": written,
        "message": f"Muvaffaqiyatli yuklandi! {written} ta yozuv saqlandi."
    })
