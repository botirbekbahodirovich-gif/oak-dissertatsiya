"""Kafedra hujjatlari — kafedra_bp (/kafedra).

Kafedra darajasidagi o'quv-uslubiy hujjatlarni (sillabus, ishchi dastur, test
savollari va h.k. — 9 tur, `services/document_prompts.py`dagi DOCUMENT_TYPES)
Groq AI yordamida generatsiya qiladigan bo'lim. Foydalanuvchi formani to'ldiradi →
AI JSON kontent yaratadi → `services/docx_builder.py` uni rasmiy .docx ga aylantiradi.

Konvensiyalar (boshqa blueprint'lar kabi):
  * Sxema lazy + idempotent (_ensure_schema).
  * DB — data.get_connection() (PostgreSQL).
  * Auth — Flask-Login (@login_required), boshqa blueprint'lar bilan bir xil
    (bu yerdagi "kafedra" — foydalanuvchi roli emas, hujjat mavzusi; alohida
    cabinet/olim_profiles bilan bog'liq emas — istalgan login qilgan foydalanuvchi
    hujjat yarata oladi).
  * Generatsiya — AI so'rovi 15-40 soniya olishi mumkin, shuning uchun POST
    /generate darhol `document_id` bilan qaytadi va background thread'da ishlaydi;
    frontend /kafedra/status/<id> ni poll qiladi.

Routes:
  GET  /kafedra/                              — landing (9 card)
  GET  /kafedra/<doc_type>/                   — forma sahifasi
  POST /kafedra/<doc_type>/generate           — generatsiyani boshlaydi (JSON, login)
  GET  /kafedra/status/<doc_id>               — status polling (JSON, egasi)
  GET  /kafedra/generated/<doc_id>/           — natija sahifasi
  GET  /kafedra/generated/<doc_id>/download   — DOCX yuklab olish (egasi)
  GET  /kafedra/tarix/                        — foydalanuvchi tarixi (login)
"""
import logging
import threading

from flask import Blueprint, abort, jsonify, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app import csrf
from data import get_connection
from services.document_prompts import (DOCUMENT_TYPES, EXTRA_FIELDS, FIELD_META, RAHBAR_LABELS,
                                       SAMPLE_TOPICS, SECTIONS, TALIM_BOSQICHLARI,
                                       section_defaults, section_summary)
from services.document_generator import GenerationError, generate_document_content
from services.docx_builder import save_docx

logger = logging.getLogger(__name__)

kafedra_bp = Blueprint('kafedra', __name__, url_prefix='/kafedra')

DAILY_LIMIT = 20  # foydalanuvchi/kun — suiiste'molga qarshi qalqon (hozircha bepul, cheksiz emas)

_schema_ready = False


def _ensure_schema(cur):
    global _schema_ready
    if _schema_ready:
        return
    # UUID Python tomonda (uuid.uuid4()) generatsiya qilinadi — repo konvensiyasi
    # (messages/dissertation kabi). Shu sabab pgcrypto/gen_random_uuid kerak emas;
    # server DB foydalanuvchisida CREATE EXTENSION huquqi bo'lmasligi mumkin.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS generated_documents (
            id UUID PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            document_type VARCHAR(50) NOT NULL,
            fan_nomi VARCHAR(255) NOT NULL,
            input_data JSONB NOT NULL,
            generated_content JSONB,
            docx_path VARCHAR(500),
            status VARCHAR(20) DEFAULT 'pending',
            error_message TEXT,
            is_paid BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            completed_at TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_generated_docs_user "
                "ON generated_documents(user_id, created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_generated_docs_type "
                "ON generated_documents(document_type)")
    _schema_ready = True


def _doc_row_to_dict(row):
    (doc_id, user_id, document_type, fan_nomi, input_data, generated_content,
     docx_path, status, error_message, is_paid, created_at, completed_at) = row
    cfg = DOCUMENT_TYPES.get(document_type, {})
    return {
        'id': str(doc_id), 'user_id': user_id, 'document_type': document_type,
        'document_label': cfg.get('label', document_type), 'document_icon': cfg.get('icon', '📄'),
        'fan_nomi': fan_nomi, 'input_data': input_data, 'generated_content': generated_content,
        'docx_path': docx_path, 'status': status, 'error_message': error_message,
        'is_paid': is_paid, 'created_at': created_at, 'completed_at': completed_at,
    }


def _get_document(doc_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute("""
                SELECT id, user_id, document_type, fan_nomi, input_data, generated_content,
                       docx_path, status, error_message, is_paid, created_at, completed_at
                FROM generated_documents WHERE id = %s
            """, (doc_id,))
            row = cur.fetchone()
        conn.commit()
        return _doc_row_to_dict(row) if row else None
    finally:
        conn.close()


# ── AI + DOCX background ishi ────────────────────────────────────────────────

def _run_generation(app, doc_id, doc_type, form_data):
    with app.app_context():
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute("UPDATE generated_documents SET status = 'generating' WHERE id = %s", (doc_id,))
            conn.commit()
        finally:
            conn.close()

        try:
            content = generate_document_content(doc_type, form_data)
            docx_path = save_docx(doc_type, content, form_data)
        except GenerationError as e:
            logger.warning("kafedra: generatsiya muvaffaqiyatsiz (%s, %s): %s", doc_id, doc_type, e)
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE generated_documents SET status = 'failed', error_message = %s
                        WHERE id = %s
                    """, (str(e)[:500], doc_id))
                conn.commit()
            finally:
                conn.close()
            return
        except Exception as e:
            logger.exception("kafedra: kutilmagan xato (%s, %s)", doc_id, doc_type)
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE generated_documents SET status = 'failed', error_message = %s
                        WHERE id = %s
                    """, (str(e)[:500], doc_id))
                conn.commit()
            finally:
                conn.close()
            return

        import json as _json
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE generated_documents
                    SET status = 'completed', generated_content = %s, docx_path = %s,
                        completed_at = NOW()
                    WHERE id = %s
                """, (_json.dumps(content, ensure_ascii=False), docx_path, doc_id))
            conn.commit()
        finally:
            conn.close()


# ── Sahifalar ─────────────────────────────────────────────────────────────

@kafedra_bp.route('/')
def index():
    return render_template('kafedra/index.html', doc_types=DOCUMENT_TYPES)


def _muassasa_section_key(cfg):
    for key in ('muassasa_shaxslar_full', 'muassasa_shaxslar_qisqa'):
        if key in cfg.get('sections', []):
            return key
    return None


def _degree_to_bosqich(academic_degree):
    d = (academic_degree or '').strip().lower()
    if 'phd' in d or 'dsc' in d:
        return 'Doktorantura (PhD)'
    if 'magistr' in d:
        return 'Magistratura'
    return ''


def _profile_autofill(muassasa_key):
    """Login qilgan foydalanuvchi cabinet orqali olim_profiles'ga bog'langan bo'lsa,
    tuzuvchi F.I.Sh./unvon/lavozim/universitet/email avtomatik to'ldiriladi
    (data.py:_render_olim_profile dagi message_target_id bilan bir xil bridge)."""
    if not muassasa_key or not getattr(current_user, 'is_authenticated', False):
        return {}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.olim_name, p.institution, p.academic_degree, p.academic_rank, p.position
                FROM cabinet_users cu
                JOIN olim_profiles p ON p.cabinet_user_id = cu.id
                WHERE LOWER(cu.email) = LOWER(%s) LIMIT 1
            """, (current_user.email,))
            row = cur.fetchone()
        conn.commit()
    except Exception:
        return {}
    finally:
        conn.close()
    if not row:
        return {}
    olim_name, institution, academic_degree, academic_rank, position = row
    out = {'tuzuvchi_fio': olim_name or '', 'email': current_user.email or ''}
    if academic_degree or academic_rank:
        out['ilmiy_daraja_unvon'] = ', '.join(p for p in (academic_degree, academic_rank) if p)
    if position:
        out['lavozim'] = position
    if institution and muassasa_key == 'muassasa_shaxslar_full':
        out['universitet'] = institution
    elif institution:
        out['universitet'] = institution
    return {k: v for k, v in out.items() if v}


@kafedra_bp.route('/<doc_type>/')
def document_form(doc_type):
    if doc_type not in DOCUMENT_TYPES:
        abort(404)
    cfg = DOCUMENT_TYPES[doc_type]
    muassasa_key = _muassasa_section_key(cfg)

    defaults = {}
    for sect_key in cfg.get('sections', []):
        defaults.update(section_defaults(sect_key))
    for key in cfg['main_fields']:
        meta = FIELD_META.get(key) or EXTRA_FIELDS.get(key) or {}
        if 'default' in meta:
            defaults[key] = meta['default']
    for key in cfg.get('extra_fields', []):
        meta = EXTRA_FIELDS.get(key, {})
        if meta.get('type') == 'group':
            for fk, fmeta in meta['fields'].items():
                if 'default' in fmeta:
                    defaults[fk] = fmeta['default']
        elif 'default' in meta:
            defaults[key] = meta['default']

    profile_autofilled = False
    autofill = _profile_autofill(muassasa_key)
    if autofill:
        defaults.update(autofill)
        profile_autofilled = True

    scholar_id = request.args.get('scholar_id', type=int)
    if scholar_id:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT institution, ixtisoslik, academic_degree, olim_name,
                           academic_rank, position
                    FROM olim_profiles WHERE id = %s
                """, (scholar_id,))
                row = cur.fetchone()
                if row:
                    institution, ixtisoslik, academic_degree, olim_name, academic_rank, position = row
                    defaults['mutaxassislik'] = ixtisoslik or defaults.get('mutaxassislik', '')
                    defaults['talim_bosqichi'] = _degree_to_bosqich(academic_degree) or defaults.get('talim_bosqichi', '')
                    if muassasa_key:
                        if institution:
                            defaults['universitet'] = institution
                        if olim_name:
                            defaults['tuzuvchi_fio'] = olim_name
                        if academic_degree or academic_rank:
                            defaults['ilmiy_daraja_unvon'] = ', '.join(
                                p for p in (academic_degree, academic_rank) if p)
                        if position:
                            defaults['lavozim'] = position
                    profile_autofilled = True
        finally:
            conn.close()

    rahbar_label = RAHBAR_LABELS.get(doc_type, 'Rahbar F.I.Sh.')
    summaries = {sect_key: section_summary(sect_key, defaults) for sect_key in cfg.get('sections', [])}
    sample_topics_key = 'mashgulotlar_rejasi' if 'mashgulotlar_rejasi' in cfg.get('sections', []) else doc_type
    sample_topics = SAMPLE_TOPICS.get(sample_topics_key, [])
    return render_template(f'kafedra/{doc_type.replace("-", "_")}.html',
                           doc_type=doc_type, cfg=cfg, talim_bosqichlari=TALIM_BOSQICHLARI,
                           defaults=defaults, field_meta=FIELD_META, extra_field_meta=EXTRA_FIELDS,
                           sections=SECTIONS, muassasa_key=muassasa_key, rahbar_label=rahbar_label,
                           profile_autofilled=profile_autofilled, summaries=summaries,
                           sample_topics=sample_topics)


@kafedra_bp.route('/<doc_type>/namuna/')
def sample_preview(doc_type):
    if doc_type not in DOCUMENT_TYPES:
        abort(404)
    cfg = DOCUMENT_TYPES[doc_type]
    return jsonify({
        'label': cfg['label'],
        'html': render_template(f'kafedra/samples/{doc_type.replace("-", "_")}.html'),
        'download_url': url_for('kafedra.sample_download', doc_type=doc_type),
    })


@kafedra_bp.route('/<doc_type>/namuna/download')
def sample_download(doc_type):
    if doc_type not in DOCUMENT_TYPES:
        abort(404)
    import os
    path = os.path.join('static', 'samples', f'namuna_{doc_type.replace("-", "_")}.docx')
    if not os.path.exists(path):
        abort(404)
    cfg = DOCUMENT_TYPES[doc_type]
    return send_file(path, as_attachment=True,
                     download_name=f"Namuna_{cfg['label'].replace(' ', '_')}.docx",
                     mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')


@kafedra_bp.route('/<doc_type>/generate', methods=['POST'])
@csrf.exempt
@login_required
def generate(doc_type):
    if doc_type not in DOCUMENT_TYPES:
        return jsonify({'success': False, 'error': "Noma'lum hujjat turi"}), 404

    form_data = request.get_json(silent=True) or request.form.to_dict()
    fan_nomi = (form_data.get('fan_nomi') or '').strip()
    if not fan_nomi:
        return jsonify({'success': False, 'error': "Fan nomi kiritilishi shart"}), 400

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute("""
                SELECT COUNT(*) FROM generated_documents
                WHERE user_id = %s AND created_at >= NOW() - INTERVAL '1 day'
            """, (current_user.id,))
            if (cur.fetchone()[0] or 0) >= DAILY_LIMIT:
                return jsonify({'success': False, 'error':
                                f"Kunlik limit ({DAILY_LIMIT} ta hujjat) tugadi. Ertaga qayta urinib ko'ring."}), 429
            import json as _json
            import uuid as _uuid
            doc_id = str(_uuid.uuid4())
            cur.execute("""
                INSERT INTO generated_documents (id, user_id, document_type, fan_nomi, input_data, status)
                VALUES (%s, %s, %s, %s, %s, 'pending')
            """, (doc_id, current_user.id, doc_type, fan_nomi,
                  _json.dumps(form_data, ensure_ascii=False)))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()

    from flask import current_app
    app = current_app._get_current_object()
    thread = threading.Thread(target=_run_generation, args=(app, str(doc_id), doc_type, form_data), daemon=True)
    thread.start()

    return jsonify({'success': True, 'document_id': str(doc_id), 'status': 'pending'})


@kafedra_bp.route('/status/<doc_id>')
@login_required
def status(doc_id):
    doc = _get_document(doc_id)
    if not doc or doc['user_id'] != current_user.id:
        return jsonify({'error': 'Topilmadi'}), 404
    return jsonify({
        'status': doc['status'],
        'error': doc['error_message'],
        'result_url': url_for('kafedra.result', doc_id=doc['id']) if doc['status'] == 'completed' else None,
    })


@kafedra_bp.route('/generated/<doc_id>/')
@login_required
def result(doc_id):
    doc = _get_document(doc_id)
    if not doc or doc['user_id'] != current_user.id:
        abort(404)
    return render_template('kafedra/result.html', document=doc)


@kafedra_bp.route('/generated/<doc_id>/download')
@login_required
def download(doc_id):
    doc = _get_document(doc_id)
    if not doc or doc['user_id'] != current_user.id:
        abort(403)
    if doc['status'] != 'completed' or not doc['docx_path']:
        abort(404)
    cfg = DOCUMENT_TYPES.get(doc['document_type'], {})
    import re
    safe_name = re.sub(r'[^\w\- ]', '', doc['fan_nomi'] or 'hujjat').strip().replace(' ', '_')[:80]
    download_name = f"{cfg.get('label', doc['document_type']).replace(' ', '_')}_{safe_name}.docx"
    return send_file(doc['docx_path'], as_attachment=True, download_name=download_name,
                     mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')


@kafedra_bp.route('/tarix/')
@login_required
def history():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute("""
                SELECT id, user_id, document_type, fan_nomi, input_data, generated_content,
                       docx_path, status, error_message, is_paid, created_at, completed_at
                FROM generated_documents WHERE user_id = %s ORDER BY created_at DESC LIMIT 100
            """, (current_user.id,))
            documents = [_doc_row_to_dict(r) for r in cur.fetchall()]
        conn.commit()
    finally:
        conn.close()
    return render_template('kafedra/tarix.html', documents=documents)
