from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required, current_user
import pandas as pd
from data import REQUIRED_COLUMNS, CSV_PATH, load_data

upload_bp = Blueprint('upload', __name__)


@upload_bp.route('/upload', methods=['GET'])
@login_required
def upload_page():
    if not (current_user and getattr(current_user, 'username', '') == 'admin'):
        abort(403)
    return render_template('upload.html')


@upload_bp.route('/upload', methods=['POST'])
@login_required
def upload_csv():
    if not (current_user and getattr(current_user, 'username', '') == 'admin'):
        abort(403)
    
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
    df.to_csv(CSV_PATH, index=False, encoding='utf-8-sig')
    return jsonify({
        "success": True,
        "rows": len(df),
        "message": f"Muvaffaqiyatli yuklandi! {len(df)} ta yozuv saqlandi."
    })
