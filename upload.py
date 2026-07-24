import os
import io
from flask import Blueprint, current_app, render_template, request, jsonify
from flask_login import login_required, current_user
import pandas as pd
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from extensions import cache
from security import admin_required, log_exception
from security_uploads import (
    UploadTooLarge,
    UploadValidationError,
    validate_tabular_upload,
)
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
    from data import get_normalized_db_url
    return get_normalized_db_url()


def get_connection():
    # Use the hardened, pooled connection from data.py (SSL + timeouts).
    from data import get_connection as _get_connection
    return _get_connection()


def _audit_import(file_name, imported_rows, result):
    """Persist a minimal import audit event without storing file contents."""
    safe_name = secure_filename(file_name or '')[:255] or 'unnamed'
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO import_audit_log "
                    "(user_id, file_name, imported_rows, result) "
                    "VALUES (%s, %s, %s, %s)",
                    (int(current_user.get_id()), safe_name,
                     max(0, int(imported_rows or 0)), result))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        current_app.logger.exception("Import audit logini yozib bo'lmadi")


@upload_bp.route('/upload', methods=['GET'])
@login_required
@admin_required
def upload_page():
    return render_template('upload.html')


@upload_bp.route('/upload', methods=['POST'])
@login_required
@admin_required
def upload_csv():
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "Fayl tanlanmagan."}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({"success": False, "error": "Fayl nomi bo'sh."}), 400
    try:
        _, raw = validate_tabular_upload(file, {'csv'})
        df = pd.read_csv(io.BytesIO(raw), dtype=str)
    except UploadTooLarge as exc:
        return jsonify({"success": False, "error": str(exc)}), 413
    except UploadValidationError as exc:
        return jsonify({"success": False, "error": str(exc)}), 422
    except Exception:
        log_exception(current_app.logger, "CSV faylni o'qib bo'lmadi")
        return jsonify({"success": False, "error": "CSV faylni o'qib bo'lmadi."}), 422
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
    except Exception:
        log_exception(current_app.logger, "CSV importida ma'lumotlar bazasi xatosi")
        _audit_import(file.filename, 0, 'failure')
        return jsonify({"success": False, "error": "Importni saqlab bo'lmadi."}), 500

    _audit_import(file.filename, inserted, 'success')
    return jsonify({
        "success": True,
        "rows": inserted,
        "message": f"Muvaffaqiyatli yuklandi! {inserted} ta yozuv saqlandi."
    })


# Column mapping for the bulk clear-and-upload format
_COL_MAP = {
    'ID':                                              'oak_id',
    'Sana':                                            'sana',
    'ism_familya':                                     'olim',
    'Havola':                                          'link',
    'Daraja':                                          'daraja',
    'Mavzular':                                        'mavzu',
    "Ixtisoslik shifri va Ixtisoslik nomi (fan tarmog'i)": 'ixtisoslik_nomi',
    'Ixtisoslik shifrlari':                            'ixtisoslik',
    'Fan tarmogi':                                     'fan_tarmoqi',
    'Royxat raqami':                                   'mavzu_raqami',
    'ilmiy_rahbar':                                    'ilmiy_rahbar',
    'Bajarilgan muassasa':                             'muassasa',
    'IK muassasa':                                     'ilmiy_kengash',
    'IK raqami':                                       'ilmiy_kengash_raqami',
    '1_oponent':                                       'opponent_1',
    '2_oponent':                                       'opponent_2',
    'Yetakchi tashkilot':                              'yetakchi_tashkilot',
}

# DB columns we will insert (must match the mapping values + exist in the table)
_DB_COLS = [
    'oak_id', 'sana', 'olim', 'link', 'daraja', 'mavzu',
    'ixtisoslik_nomi', 'ixtisoslik', 'fan_tarmoqi', 'mavzu_raqami',
    'ilmiy_rahbar', 'muassasa', 'ilmiy_kengash', 'ilmiy_kengash_raqami',
    'opponent_1', 'opponent_2', 'yetakchi_tashkilot',
]


def _ensure_extra_columns(conn):
    """Add any missing columns used by the bulk upload."""
    extra = [
        ('fan_tarmoqi',          'TEXT'),
        ('ixtisoslik_nomi',      'TEXT'),
        ('ilmiy_kengash',        'TEXT'),
        ('oak_id',               'TEXT UNIQUE'),
    ]
    with conn.cursor() as cur:
        for col, col_type in extra:
            cur.execute(
                "ALTER TABLE dissertations ADD COLUMN IF NOT EXISTS %s %s" % (col, col_type)
            )
    conn.commit()


def _read_file(extension, raw) -> pd.DataFrame:
    if extension == 'xlsx':
        return pd.read_excel(io.BytesIO(raw), dtype=str)
    return pd.read_csv(io.BytesIO(raw), dtype=str)


@upload_bp.route('/admin/clear-and-upload', methods=['POST'])
@login_required
@admin_required
def clear_and_upload():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'Fayl tanlanmagan.'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'success': False, 'error': "Fayl nomi bo'sh."}), 400
    try:
        extension, raw = validate_tabular_upload(file, {'csv', 'xlsx'})
        df = _read_file(extension, raw)
    except UploadTooLarge as exc:
        return jsonify({'success': False, 'error': str(exc)}), 413
    except UploadValidationError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 422
    except Exception:
        log_exception(current_app.logger, "Import faylini o'qib bo'lmadi")
        return jsonify({'success': False, 'error': "Faylni o'qib bo'lmadi."}), 422

    # Rename columns to DB names
    df = df.rename(columns=_COL_MAP)
    df = df.fillna('')

    # Keep only columns we know how to insert
    present_cols = [c for c in _DB_COLS if c in df.columns]
    if not present_cols:
        return jsonify({'success': False, 'error': 'Mos ustunlar topilmadi.'}), 400

    try:
        conn = get_connection()
        _ensure_extra_columns(conn)

        with conn.cursor() as cur:
            # Delete everything
            cur.execute('DELETE FROM dissertations')
            deleted = cur.rowcount

            # Build insert rows
            rows = []
            for _, row in df.iterrows():
                mavzu = str(row.get('mavzu', '') or '').strip()
                if not mavzu or len(mavzu) < 5:
                    continue
                rows.append(tuple(str(row.get(c, '') or '')[:500] for c in present_cols))

            if rows:
                placeholders = ','.join(['%s'] * len(present_cols))
                col_list = ','.join(present_cols)
                execute_values(
                    cur,
                    f"INSERT INTO dissertations ({col_list}) VALUES %s ON CONFLICT (oak_id) DO NOTHING",
                    rows
                )

        conn.commit()
        conn.close()
    except Exception:
        log_exception(current_app.logger, "Bulk importida ma'lumotlar bazasi xatosi")
        _audit_import(file.filename, 0, 'failure')
        return jsonify({'success': False, 'error': "Importni saqlab bo'lmadi."}), 500

    _audit_import(file.filename, len(rows), 'success')
    cache.clear()
    return jsonify({
        'success': True,
        'deleted': deleted,
        'added': len(rows),
        'message': f"{deleted} ta o'chirildi, {len(rows)} ta qo'shildi."
    })
