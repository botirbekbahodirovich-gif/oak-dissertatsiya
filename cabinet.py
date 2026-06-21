"""Researcher cabinet / portfolio system.

Self-contained auth via Flask `session` (separate from the main-site Flask-Login
session), so it never interferes with the existing Telegram login on the main site.
"""
import os
import hmac
import hashlib
import time
from functools import wraps

from flask import (Blueprint, render_template, request, redirect, url_for,
                   jsonify, session)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

from data import get_connection

load_dotenv()
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_BOT_USERNAME = os.environ.get('TELEGRAM_BOT_USERNAME', 'send_kod_bot')

cabinet_bp = Blueprint('cabinet', __name__)


# ── helpers ────────────────────────────────────────────────────────────────
def current_cabinet_user():
    uid = session.get('cabinet_user_id')
    if not uid:
        return None
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, email, telegram_username, telegram_first_name, olim_name "
                    "FROM cabinet_users WHERE id = %s", (uid,))
                r = cur.fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    if not r:
        return None
    return {"id": r[0], "email": r[1], "telegram_username": r[2],
            "telegram_first_name": r[3], "olim_name": r[4]}


def cabinet_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('cabinet_user_id'):
            if request.path.startswith('/cabinet/api/'):
                return jsonify({"ok": False, "error": "auth"}), 401
            return redirect(url_for('cabinet.login', next=request.full_path
                                    if request.query_string else request.path))
        return view(*args, **kwargs)
    return wrapped


def _set_session(user_id, olim_name=None):
    session['cabinet_user_id'] = user_id
    session['cabinet_olim_name'] = olim_name or ''


def _touch_login(cur, user_id):
    cur.execute("UPDATE cabinet_users SET last_login = CURRENT_TIMESTAMP WHERE id = %s", (user_id,))


def _olim_name():
    """Primary claimed name for the current cabinet user (or '')."""
    u = current_cabinet_user()
    return (u or {}).get('olim_name') or ''


# ── pages ──────────────────────────────────────────────────────────────────
@cabinet_bp.route('/cabinet')
@cabinet_login_required
def cabinet():
    user = current_cabinet_user()
    olim_name = (user or {}).get('olim_name') or ''
    profile = None
    maqolalar = konferensiyalar = ish_faoliyati = rasmlar = []
    claimed = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM olim_profiles WHERE cabinet_user_id = %s LIMIT 1", (user['id'],))
                cols = [c[0] for c in cur.description]
                row = cur.fetchone()
                if row:
                    profile = dict(zip(cols, row))
                # all names claimed by this user
                cur.execute("SELECT olim_name FROM olim_profiles WHERE cabinet_user_id = %s", (user['id'],))
                claimed = [r[0] for r in cur.fetchall() if r[0]]
                if olim_name:
                    def _f(sql, order):
                        cur.execute(sql + " ORDER BY " + order, (olim_name,))
                        cn = [c[0] for c in cur.description]
                        return [dict(zip(cn, rr)) for rr in cur.fetchall()]
                    maqolalar = _f("SELECT * FROM olim_maqolalar WHERE LOWER(TRIM(olim_name))=LOWER(TRIM(%s))",
                                   "year DESC NULLS LAST, id DESC")
                    konferensiyalar = _f("SELECT * FROM olim_konferensiyalar WHERE LOWER(TRIM(olim_name))=LOWER(TRIM(%s))",
                                         "date DESC NULLS LAST, id DESC")
                    ish_faoliyati = _f("SELECT * FROM olim_ish_faoliyati WHERE LOWER(TRIM(olim_name))=LOWER(TRIM(%s))",
                                       "start_date DESC NULLS LAST, id DESC")
                    rasmlar = _f("SELECT * FROM olim_rasmlar WHERE LOWER(TRIM(olim_name))=LOWER(TRIM(%s))",
                                 "created_at DESC, id DESC")
        finally:
            conn.close()
    except Exception:
        pass
    return render_template('cabinet.html', user=user, profile=profile or {},
                           olim_name=olim_name, claimed=claimed,
                           maqolalar=maqolalar, konferensiyalar=konferensiyalar,
                           ish_faoliyati=ish_faoliyati, rasmlar=rasmlar,
                           telegram_bot_username=TELEGRAM_BOT_USERNAME)


def _safe_next(target):
    """Return a safe local redirect path, or None."""
    if not target:
        return None
    if target.startswith('/') and not target.startswith('//') and '\\' not in target:
        return target
    return None


@cabinet_bp.route('/cabinet/login', methods=['GET', 'POST'])
def login():
    nxt = _safe_next(request.values.get('next'))
    if session.get('cabinet_user_id'):
        return redirect(nxt or url_for('cabinet.cabinet'))
    error = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        if not email or not password:
            error = "Email va parolni kiriting."
        else:
            try:
                conn = get_connection()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id, password_hash, olim_name FROM cabinet_users WHERE email = %s", (email,))
                        r = cur.fetchone()
                        if r and r[1] and check_password_hash(r[1], password):
                            _touch_login(cur, r[0])
                            conn.commit()
                            _set_session(r[0], r[2])
                            return redirect(nxt or url_for('cabinet.cabinet'))
                        error = "Email yoki parol noto'g'ri."
                finally:
                    conn.close()
            except Exception:
                error = "Kirishda xatolik yuz berdi."
    return render_template('cabinet_login.html', error=error, next=nxt or '',
                           telegram_bot_username=TELEGRAM_BOT_USERNAME)


@cabinet_bp.route('/cabinet/register', methods=['GET', 'POST'])
def register():
    if session.get('cabinet_user_id'):
        return redirect(url_for('cabinet.cabinet'))
    error = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        if not email or not password:
            error = "Barcha maydonlarni to'ldiring."
        elif len(password) < 6:
            error = "Parol kamida 6 ta belgi bo'lishi kerak."
        elif password != confirm:
            error = "Parollar mos kelmadi."
        else:
            pw_hash = generate_password_hash(password)
            try:
                conn = get_connection()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id FROM cabinet_users WHERE email = %s", (email,))
                        if cur.fetchone():
                            error = "Bu email allaqachon ro'yxatdan o'tgan."
                        else:
                            cur.execute(
                                "INSERT INTO cabinet_users (email, password_hash) VALUES (%s, %s) RETURNING id",
                                (email, pw_hash))
                            new_id = cur.fetchone()[0]
                            conn.commit()
                            _set_session(new_id, None)
                            return redirect(url_for('cabinet.cabinet'))
                finally:
                    conn.close()
            except Exception:
                error = "Ro'yxatdan o'tishda xatolik yuz berdi."
    return render_template('cabinet_register.html', error=error)


@cabinet_bp.route('/cabinet/telegram', methods=['POST'])
def telegram():
    """Telegram Login Widget callback for the cabinet (separate from main-site login)."""
    raw = request.get_json(silent=True) or {}
    if not TELEGRAM_BOT_TOKEN:
        return jsonify({"success": False, "error": "TELEGRAM_BOT_TOKEN sozlanmagan"}), 200
    try:
        data = dict(raw)
        check_hash = data.pop('hash', '')
        if not check_hash:
            return jsonify({"success": False, "error": "Hash mavjud emas"}), 200
        data_check = '\n'.join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hashlib.sha256(TELEGRAM_BOT_TOKEN.encode()).digest()
        computed = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed, check_hash):
            return jsonify({"success": False, "error": "Tasdiqlash amalga oshmadi"}), 200
        if time.time() - int(data.get('auth_date', 0)) > 86400:
            return jsonify({"success": False, "error": "Muddati o'tgan"}), 200
        tg_id = int(data.get('id', 0))
        if not tg_id:
            return jsonify({"success": False, "error": "Telegram ID topilmadi"}), 200
        username = (data.get('username') or '').strip()
        first_name = (data.get('first_name') or '').strip()
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, olim_name FROM cabinet_users WHERE telegram_id = %s", (tg_id,))
                r = cur.fetchone()
                if r:
                    _touch_login(cur, r[0])
                    conn.commit()
                    _set_session(r[0], r[1])
                else:
                    cur.execute(
                        "INSERT INTO cabinet_users (telegram_id, telegram_username, telegram_first_name) "
                        "VALUES (%s, %s, %s) RETURNING id", (tg_id, username, first_name))
                    new_id = cur.fetchone()[0]
                    conn.commit()
                    _set_session(new_id, None)
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"success": False, "error": f"Xatolik: {e}"}), 200
    return jsonify({"success": True, "redirect": "/cabinet"})


@cabinet_bp.route('/cabinet/api/logout', methods=['POST', 'GET'])
def logout():
    session.pop('cabinet_user_id', None)
    session.pop('cabinet_olim_name', None)
    return redirect(url_for('home'))


# ── profile + claim ────────────────────────────────────────────────────────
_PROFILE_FIELDS = [
    'first_name', 'last_name', 'patronymic', 'title', 'position', 'institution',
    'bio', 'birth_year', 'photo_url',
    'scopus_url', 'wos_url', 'scholar_url', 'orcid_url', 'website_url',
    'youtube_url', 'facebook_url', 'twitter_url', 'instagram_url',
    'telegram_url', 'pinterest_url',
]


@cabinet_bp.route('/cabinet/api/profile/save', methods=['POST'])
@cabinet_login_required
def profile_save():
    user = current_cabinet_user()
    data = request.get_json(silent=True) or request.form
    vals = {}
    for f in _PROFILE_FIELDS:
        v = (data.get(f) or '').strip() if isinstance(data.get(f), str) else data.get(f)
        vals[f] = v if v not in ('', None) else None
    if vals.get('birth_year'):
        try:
            vals['birth_year'] = int(vals['birth_year'])
        except (TypeError, ValueError):
            vals['birth_year'] = None
    olim_name = user.get('olim_name') or (vals.get('last_name') or vals.get('first_name') or '').strip()
    if not olim_name:
        olim_name = f"cabinet_{user['id']}"
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM olim_profiles WHERE cabinet_user_id = %s LIMIT 1", (user['id'],))
                existing = cur.fetchone()
                cols = list(vals.keys())
                if existing:
                    set_clause = ", ".join(f"{c} = %s" for c in cols) + ", updated_at = CURRENT_TIMESTAMP"
                    cur.execute(f"UPDATE olim_profiles SET {set_clause} WHERE id = %s",
                                [vals[c] for c in cols] + [existing[0]])
                else:
                    all_cols = ['olim_name', 'cabinet_user_id'] + cols
                    placeholders = ", ".join(["%s"] * len(all_cols))
                    cur.execute(
                        f"INSERT INTO olim_profiles ({', '.join(all_cols)}) VALUES ({placeholders})",
                        [olim_name, user['id']] + [vals[c] for c in cols])
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200
    return jsonify({"ok": True})


@cabinet_bp.route('/cabinet/api/search-olim')
@cabinet_login_required
def search_olim():
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify({"results": []})
    like = f"%{q.lower()}%"
    results = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT TRIM(olim), COUNT(*) AS cnt, MIN(mavzu) AS sample_mavzu, "
                    "MIN(daraja) AS sample_daraja FROM dissertations "
                    "WHERE olim IS NOT NULL AND TRIM(olim) <> '' AND LOWER(TRIM(olim)) LIKE %s "
                    "GROUP BY TRIM(olim) ORDER BY cnt DESC LIMIT 25", (like,))
                results = [{"name": r[0], "count": r[1],
                            "mavzu": r[2] or "", "daraja": (r[3] or "").upper()}
                           for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        results = []
    return jsonify({"results": results})


@cabinet_bp.route('/cabinet/api/claim', methods=['POST'])
@cabinet_login_required
def claim():
    user = current_cabinet_user()
    data = request.get_json(silent=True) or {}
    name = (data.get('olim_name') or '').strip()
    if not name:
        return jsonify({"ok": False, "error": "Ism kiritilmagan"}), 200
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                # upsert an olim_profiles row for this name linked to the user
                cur.execute("SELECT id, cabinet_user_id FROM olim_profiles WHERE LOWER(TRIM(olim_name))=LOWER(TRIM(%s))", (name,))
                row = cur.fetchone()
                if row and row[1] and row[1] != user['id']:
                    return jsonify({"ok": False, "error": "Bu profil boshqa foydalanuvchi tomonidan band qilingan."}), 200
                if row:
                    cur.execute("UPDATE olim_profiles SET cabinet_user_id = %s WHERE id = %s", (user['id'], row[0]))
                else:
                    cur.execute("INSERT INTO olim_profiles (olim_name, cabinet_user_id) VALUES (%s, %s)", (name, user['id']))
                cur.execute("UPDATE cabinet_users SET olim_name = %s WHERE id = %s", (name, user['id']))
            conn.commit()
            session['cabinet_olim_name'] = name
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200
    return jsonify({"ok": True, "olim_name": name})


@cabinet_bp.route('/cabinet/api/unclaim', methods=['POST'])
@cabinet_login_required
def unclaim():
    user = current_cabinet_user()
    data = request.get_json(silent=True) or {}
    name = (data.get('olim_name') or '').strip()
    if not name:
        return jsonify({"success": False, "error": "Ism kiritilmagan"}), 400
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                # the claimed profile row for this name must belong to this user
                cur.execute(
                    "SELECT id FROM olim_profiles "
                    "WHERE cabinet_user_id = %s AND LOWER(TRIM(olim_name)) = LOWER(TRIM(%s)) LIMIT 1",
                    (user['id'], name))
                row = cur.fetchone()
                if not row:
                    return jsonify({"success": False, "error": "Bu dissertatsiya sizga biriktirilmagan"}), 400
                # release the claim on this profile
                cur.execute("UPDATE olim_profiles SET cabinet_user_id = NULL WHERE id = %s", (row[0],))
                # if it was the user's primary name, repoint to another remaining claim (or clear)
                new_primary = None
                if (user.get('olim_name') or '').strip().lower() == name.lower():
                    cur.execute(
                        "SELECT olim_name FROM olim_profiles "
                        "WHERE cabinet_user_id = %s AND olim_name IS NOT NULL ORDER BY id LIMIT 1",
                        (user['id'],))
                    rem = cur.fetchone()
                    new_primary = rem[0] if rem else None
                    cur.execute("UPDATE cabinet_users SET olim_name = %s WHERE id = %s",
                                (new_primary, user['id']))
            conn.commit()
            if (user.get('olim_name') or '').strip().lower() == name.lower():
                session['cabinet_olim_name'] = new_primary or ''
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 200
    return jsonify({"success": True, "message": "Dissertatsiya ajratildi"})


# ── generic portfolio item CRUD ────────────────────────────────────────────
def _insert_item(table, fields, form):
    name = _olim_name()
    if not name:
        return jsonify({"ok": False, "error": "Avval dissertatsiyangizni biriktiring."}), 200
    vals = [name] + [(_clean(form.get(f))) for f in fields]
    cols = ['olim_name'] + fields
    placeholders = ", ".join(["%s"] * len(cols))
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) RETURNING id", vals)
                new_id = cur.fetchone()[0]
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200
    return jsonify({"ok": True, "id": new_id})


def _edit_item(table, fields, item_id, form):
    name = _olim_name()
    set_clause = ", ".join(f"{f} = %s" for f in fields)
    vals = [_clean(form.get(f)) for f in fields] + [item_id, name]
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {table} SET {set_clause} WHERE id = %s AND LOWER(TRIM(olim_name))=LOWER(TRIM(%s))", vals)
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200
    return jsonify({"ok": True})


def _delete_item(table, item_id):
    name = _olim_name()
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {table} WHERE id = %s AND LOWER(TRIM(olim_name))=LOWER(TRIM(%s))", (item_id, name))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200
    return jsonify({"ok": True})


def _clean(v):
    if v is None:
        return None
    v = str(v).strip()
    return v if v else None


def _form():
    return request.get_json(silent=True) or request.form


# maqolalar
_MAQOLA = ['title', 'authors', 'journal', 'year', 'citations', 'url']


@cabinet_bp.route('/cabinet/api/maqola/add', methods=['POST'])
@cabinet_login_required
def maqola_add():
    return _insert_item('olim_maqolalar', _MAQOLA, _form())


@cabinet_bp.route('/cabinet/api/maqola/edit/<int:id>', methods=['POST'])
@cabinet_login_required
def maqola_edit(id):
    return _edit_item('olim_maqolalar', _MAQOLA, id, _form())


@cabinet_bp.route('/cabinet/api/maqola/delete/<int:id>', methods=['POST'])
@cabinet_login_required
def maqola_delete(id):
    return _delete_item('olim_maqolalar', id)


# konferensiyalar
_KONF = ['title', 'conference_name', 'location', 'date', 'url']


@cabinet_bp.route('/cabinet/api/konferensiya/add', methods=['POST'])
@cabinet_login_required
def konf_add():
    return _insert_item('olim_konferensiyalar', _KONF, _form())


@cabinet_bp.route('/cabinet/api/konferensiya/edit/<int:id>', methods=['POST'])
@cabinet_login_required
def konf_edit(id):
    return _edit_item('olim_konferensiyalar', _KONF, id, _form())


@cabinet_bp.route('/cabinet/api/konferensiya/delete/<int:id>', methods=['POST'])
@cabinet_login_required
def konf_delete(id):
    return _delete_item('olim_konferensiyalar', id)


# ish faoliyati
_ISH = ['position', 'organization', 'start_date', 'end_date', 'is_current']


@cabinet_bp.route('/cabinet/api/ish/add', methods=['POST'])
@cabinet_login_required
def ish_add():
    f = _form()
    name = _olim_name()
    if not name:
        return jsonify({"ok": False, "error": "Avval dissertatsiyangizni biriktiring."}), 200
    is_current = bool(f.get('is_current'))
    end_date = None if is_current else _clean(f.get('end_date'))
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO olim_ish_faoliyati (olim_name, position, organization, start_date, end_date, is_current) "
                    "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (name, _clean(f.get('position')), _clean(f.get('organization')),
                     _clean(f.get('start_date')), end_date, is_current))
                new_id = cur.fetchone()[0]
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200
    return jsonify({"ok": True, "id": new_id})


@cabinet_bp.route('/cabinet/api/ish/edit/<int:id>', methods=['POST'])
@cabinet_login_required
def ish_edit(id):
    f = _form()
    name = _olim_name()
    is_current = bool(f.get('is_current'))
    end_date = None if is_current else _clean(f.get('end_date'))
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE olim_ish_faoliyati SET position=%s, organization=%s, start_date=%s, "
                    "end_date=%s, is_current=%s WHERE id=%s AND LOWER(TRIM(olim_name))=LOWER(TRIM(%s))",
                    (_clean(f.get('position')), _clean(f.get('organization')),
                     _clean(f.get('start_date')), end_date, is_current, id, name))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200
    return jsonify({"ok": True})


@cabinet_bp.route('/cabinet/api/ish/delete/<int:id>', methods=['POST'])
@cabinet_login_required
def ish_delete(id):
    return _delete_item('olim_ish_faoliyati', id)


# rasmlar
_RASM = ['image_url', 'caption']


@cabinet_bp.route('/cabinet/api/rasm/add', methods=['POST'])
@cabinet_login_required
def rasm_add():
    return _insert_item('olim_rasmlar', _RASM, _form())


@cabinet_bp.route('/cabinet/api/rasm/delete/<int:id>', methods=['POST'])
@cabinet_login_required
def rasm_delete(id):
    return _delete_item('olim_rasmlar', id)
