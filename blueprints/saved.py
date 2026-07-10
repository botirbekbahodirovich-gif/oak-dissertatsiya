"""Saqlangan dissertatsiyalar — bookmark moduli.

MUHIM: bu YANGI parallel tizim EMAS. Dissertatsiyalarni saqlash allaqachon
data.py da `user_bookmarks` jadvali + /api/bookmarks/toggle orqali mavjud
(dashboard yulduzchasi). Bu blueprint o'sha YAGONA jadval ustiga yetishmagan
qismlarni qo'shadi, shunda dashboard va bu sahifa har doim sinxron bo'ladi:

  - shaxsiy eslatma (notes ustuni)
  - /cabinet/saqlangan — alohida ro'yxat sahifasi (20/sahifa)
  - /api/saved/ids — sahifa yuklanishida barcha saqlangan ID + umumiy son
  - /api/saved/toggle — kartochka tugmalari uchun (dashboard bilan bir jadval)

Sxema lazy va idempotent (data.py:_ensure_dashboard_schema aksi + notes ustuni),
shu sabab migratsiya (migrations/add_bookmark_notes.sql) qo'lda ishlamasa ham
server o'zini-o'zi migratsiya qiladi.
"""
from flask import Blueprint, jsonify, request, render_template, abort
from flask_login import login_required, current_user

from app import csrf
from data import get_connection

saved_bp = Blueprint('saved', __name__)

PER_PAGE = 20
NOTES_MAX = 500

_schema_ready = False


def _ensure_schema(cur):
    """user_bookmarks + notes ustuni (idempotent). data.py ham shu jadvalni
    yaratadi — ikkalasi ham IF NOT EXISTS, ziddiyatsiz."""
    global _schema_ready
    if _schema_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_bookmarks (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            dissertation_id INTEGER NOT NULL REFERENCES dissertations(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, dissertation_id)
        )""")
    cur.execute("ALTER TABLE user_bookmarks ADD COLUMN IF NOT EXISTS notes TEXT")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_bookmarks_user "
                "ON user_bookmarks(user_id, created_at DESC)")
    _schema_ready = True


def _total_saved(cur, uid):
    cur.execute("SELECT COUNT(*) FROM user_bookmarks WHERE user_id = %s", (uid,))
    return cur.fetchone()[0] or 0


# ── API ──────────────────────────────────────────────────────────────────────

@saved_bp.route('/api/saved/toggle', methods=['POST'])
@csrf.exempt
@login_required
def saved_toggle():
    """Saqlangan bo'lsa o'chiradi, bo'lmasa saqlaydi. Dashboard yulduzchasi
    bilan AYNAN bir jadval (user_bookmarks) — holat doim sinxron."""
    data = request.get_json(silent=True) or {}
    try:
        did = int(data.get('dissertation_id'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': "Noto'g'ri so'rov"}), 400
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute("DELETE FROM user_bookmarks "
                        "WHERE user_id = %s AND dissertation_id = %s",
                        (current_user.id, did))
            if cur.rowcount:
                saved = False
            else:
                cur.execute("INSERT INTO user_bookmarks (user_id, dissertation_id) "
                            "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                            (current_user.id, did))
                saved = True
            total = _total_saved(cur, current_user.id)
        conn.commit()
        return jsonify({'success': True, 'saved': saved, 'total_saved': total})
    finally:
        conn.close()


@saved_bp.route('/api/saved/ids')
@login_required
def saved_ids():
    """Sahifa yuklanishida barcha saqlangan ID + umumiy son (tugmalar holati
    va kabinet nav badge'i uchun)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute("SELECT dissertation_id FROM user_bookmarks WHERE user_id = %s",
                        (current_user.id,))
            ids = [r[0] for r in cur.fetchall()]
        conn.commit()
        return jsonify({'success': True, 'ids': ids, 'count': len(ids)})
    finally:
        conn.close()


@saved_bp.route('/api/saved/<int:dissertation_id>/notes', methods=['POST'])
@csrf.exempt
@login_required
def saved_notes(dissertation_id):
    """Saqlangan dissertatsiyaga shaxsiy eslatma (max 500 belgi). Faqat
    allaqachon saqlangan yozuvga yoziladi."""
    data = request.get_json(silent=True) or {}
    notes = (data.get('notes') or '').strip()[:NOTES_MAX]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute("UPDATE user_bookmarks SET notes = %s "
                        "WHERE user_id = %s AND dissertation_id = %s",
                        (notes or None, current_user.id, dissertation_id))
            if not cur.rowcount:
                conn.rollback()
                return jsonify({'success': False,
                                'error': 'Avval dissertatsiyani saqlang'}), 404
        conn.commit()
        return jsonify({'success': True, 'notes': notes})
    finally:
        conn.close()


# ── Sahifa ────────────────────────────────────────────────────────────────────

@saved_bp.route('/cabinet/saqlangan')
@login_required
def saved_page():
    """Saqlangan dissertatsiyalar ro'yxati (20/sahifa, saved_at DESC)."""
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            total = _total_saved(cur, current_user.id)
            cur.execute("""
                SELECT d.id, d.sana, d.daraja, d.olim, d.mavzu, d.ixtisoslik,
                       d.ilmiy_rahbar, d.link, b.notes
                FROM user_bookmarks b
                JOIN dissertations d ON d.id = b.dissertation_id
                WHERE b.user_id = %s
                ORDER BY b.created_at DESC
                LIMIT %s OFFSET %s
            """, (current_user.id, PER_PAGE, (page - 1) * PER_PAGE))
            cols = ('id', 'sana', 'daraja', 'olim', 'mavzu', 'ixtisoslik',
                    'ilmiy_rahbar', 'link', 'notes')
            records = [dict(zip(cols, row)) for row in cur.fetchall()]
        conn.commit()
    finally:
        conn.close()
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    return render_template('saved_dissertations.html',
                           records=records, page=page, total=total,
                           total_pages=total_pages,
                           has_prev=page > 1, has_next=page < total_pages)
