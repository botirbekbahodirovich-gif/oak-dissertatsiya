"""Mening dissertatsiya xaritam — personal supervisor research map.

The researcher records the ilmiy rahbar (supervisor) they want to explore; the
map then shows every dissertation that supervisor guided, their specialities,
a per-year timeline and a Groq-written analysis of busy vs. free research
niches.

Identity: scholar attributes live on `olim_profiles`, keyed by
`cabinet_user_id` (NOT on the users table). We resolve the visitor through the
cabinet session, bridging a main-site Flask-Login user into a cabinet identity
when needed — the same pattern reminders/grants use. The chosen supervisor is
stored in `olim_profiles.supervisor_preference` (self-migrated below), with a
fallback to the existing `advisor_name` on read.

Routes:
  GET  /xarita/mening          — the map page (login required)
  POST /xarita/supervisor/set  — persist the chosen supervisor name
  GET  /api/xarita/data        — supervisor's students + specialities + timeline
                                 + AI analysis (?supervisor=NAME or from profile)
"""
import re
from collections import Counter

from flask import Blueprint, jsonify, request, render_template, session
from flask_login import login_required

from data import get_connection, cache
from app import csrf

xarita_bp = Blueprint('xarita', __name__)

_MIN_NAME = 3          # avoid '%%' matching the whole table
_MAX_NAME = 100
_PER_PAGE = 10         # client-side; the API returns the full list
_CACHE_TTL = 6 * 3600  # 6 soat
_YEAR_RE = re.compile(r'(19|20)\d{2}')

_schema_ready = False


# ── schema (lazy, idempotent — mirrors migrations/add_supervisor_to_profiles.sql)
def _ensure_schema(cur):
    global _schema_ready
    if _schema_ready:
        return
    cur.execute("ALTER TABLE olim_profiles "
                "ADD COLUMN IF NOT EXISTS supervisor_preference VARCHAR(200)")
    _schema_ready = True


# ── identity ────────────────────────────────────────────────────────────────
def _resolve_uid():
    """cabinet_users.id for the current visitor (bridging a main-site login into
    a cabinet identity when needed), or None. olim_profiles keys on
    cabinet_user_id, so every profile read/write goes through this — never the
    users table."""
    uid = session.get('cabinet_user_id')
    if uid:
        return uid
    try:
        from cabinet import _bridge_from_main
        if _bridge_from_main():
            return session.get('cabinet_user_id')
    except Exception:
        pass
    return None


def _saved_supervisor(uid):
    """The supervisor stored for this user (preference, else recorded advisor)."""
    if not uid:
        return ''
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute(
                    "SELECT COALESCE(NULLIF(TRIM(supervisor_preference), ''), "
                    "                NULLIF(TRIM(advisor_name), '')) "
                    "FROM olim_profiles WHERE cabinet_user_id = %s "
                    "ORDER BY id DESC LIMIT 1", (uid,))
                r = cur.fetchone()
            conn.commit()
        finally:
            conn.close()
    except Exception:
        return ''
    return (r[0] or '').strip() if r and r[0] else ''


# ── data building ───────────────────────────────────────────────────────────
def _year(sana):
    m = _YEAR_RE.search(sana or '')
    return m.group(0) if m else ''


def _ai_tahlil(supervisor, total, ixtisosliklar, shogirdlar):
    """Groq-written niche analysis (Uzbek). Degrades gracefully without a key."""
    from data import GROQ_API_KEY
    if not GROQ_API_KEY:
        return ("AI tahlil hozircha mavjud emas (Groq API kaliti sozlanmagan). "
                "Quyidagi ixtisosliklar va mavzular ro'yxatini ko'rib chiqing.")
    top_specs = ', '.join(f"{i['name']} ({i['count']})"
                          for i in ixtisosliklar[:6]) or "—"
    sample_topics = '\n'.join(f"- {d['mavzu']}"
                              for d in shogirdlar[:12] if d['mavzu'])
    user_prompt = (
        f"Rahbar: {supervisor}\n"
        f"Shogirdlar soni: {total}\n"
        f"Ixtisosliklar: {top_specs}\n"
        f"Mavzular:\n{sample_topics}\n\n"
        "Quyidagilarni yoz:\n"
        "1. Bu rahbarning asosiy ilmiy yo'nalishlari (2-3 ta)\n"
        "2. Qaysi yo'nalishlar ko'p o'rganilgan (band nishalar)\n"
        "3. Yangi tadqiqotchi uchun 2-3 ta bo'sh nisha tavsiyasi\n"
        "4. Qisqa xulosa"
    )
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": (
                    "Sen O'zbekiston ilmiy rahbari tahlilchisisan. "
                    "O'zbek tilida qisqa javob ber (150-200 so'z).")},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=700,
        )
        return (resp.choices[0].message.content or "").strip() or "Tahlil olinmadi."
    except Exception:
        return "AI xizmati hozirda mavjud emas. Iltimos, keyinroq urinib ko'ring."


def _build_xarita(supervisor):
    """Full payload for a supervisor. Cached 6h by (lowercased) name — the AI
    call runs at most once per supervisor per window across all users."""
    key = 'xarita_' + supervisor.lower()
    cached = cache.get(key)
    if cached is not None:
        out = dict(cached)
        out['cached'] = True
        return out

    from data import latin_to_cyrillic
    like = f'%{supervisor}%'
    like_cyr = f'%{latin_to_cyrillic(supervisor)}%'
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, olim, mavzu, sana, daraja, ixtisoslik, muassasa "
                "FROM dissertations "
                "WHERE ilmiy_rahbar ILIKE %s OR ilmiy_rahbar ILIKE %s",
                (like, like_cyr))
            rows = cur.fetchall()
    finally:
        conn.close()

    shogirdlar = []
    for (did, olim, mavzu, sana, daraja, ixt, muassasa) in rows:
        shogirdlar.append({
            'id': did,
            'olim': (olim or '').strip(),
            'mavzu': (mavzu or '').strip(),
            'sana': (sana or '').strip(),
            'yil': _year(sana),
            'daraja': (daraja or '').strip(),
            'ixtisoslik': (ixt or '').strip(),
            'muassasa': (muassasa or '').strip(),
        })
    # newest first (year desc, then id desc) — sana is free-form text
    shogirdlar.sort(key=lambda d: (d['yil'] or '0', d['id'] or 0), reverse=True)

    ixt_counter = Counter(d['ixtisoslik'] for d in shogirdlar if d['ixtisoslik'])
    yil_counter = Counter(d['yil'] for d in shogirdlar if d['yil'])
    ixtisosliklar = [{'name': k, 'count': v} for k, v in ixt_counter.most_common()]
    yillar = [{'year': y, 'count': c} for y, c in sorted(yil_counter.items())]
    years = sorted(yil_counter)
    years_active = f"{years[0]}–{years[-1]}" if years else '—'
    total = len(shogirdlar)

    result = {
        'ok': True,
        'supervisor': {
            'name': supervisor,
            'total_students': total,
            'years_active': years_active,
            'top_ixtisoslik': ixtisosliklar[0]['name'] if ixtisosliklar else '—',
        },
        'shogirdlar': shogirdlar,
        'ixtisosliklar': ixtisosliklar,
        'yillar': yillar,
        'ai_tahlil': _ai_tahlil(supervisor, total, ixtisosliklar, shogirdlar),
        'cached': False,
    }
    if total > 0:
        cache.set(key, result, timeout=_CACHE_TTL)
    return result


# ── routes ──────────────────────────────────────────────────────────────────
@xarita_bp.route('/xarita/mening')
@login_required
def mening_xaritam():
    supervisor = _saved_supervisor(_resolve_uid())
    return render_template('dissertatsiya_xaritasi.html', supervisor=supervisor)


@xarita_bp.route('/xarita/supervisor/set', methods=['POST'])
@csrf.exempt
@login_required
def set_supervisor():
    uid = _resolve_uid()
    if not uid:
        return jsonify({'ok': False, 'error': 'auth'}), 401
    body = request.get_json(silent=True) or {}
    name = (body.get('supervisor_name') or '').strip()[:_MAX_NAME]
    if len(name) < _MIN_NAME:
        return jsonify({'ok': False, 'error': "Ism juda qisqa"}), 400
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute("SELECT id FROM olim_profiles "
                            "WHERE cabinet_user_id = %s LIMIT 1", (uid,))
                row = cur.fetchone()
                if row:
                    cur.execute(
                        "UPDATE olim_profiles SET supervisor_preference = %s, "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                        (name, row[0]))
                else:
                    # No profile row yet — seed one (olim_name is NOT NULL UNIQUE;
                    # the cabinet_{uid} placeholder mirrors app.set_region).
                    cur.execute(
                        "INSERT INTO olim_profiles "
                        "(olim_name, cabinet_user_id, supervisor_preference) "
                        "VALUES (%s, %s, %s)", (f'cabinet_{uid}', uid, name))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
    return jsonify({'ok': True, 'supervisor': name})


@xarita_bp.route('/api/xarita/data')
@login_required
def xarita_data():
    supervisor = (request.args.get('supervisor') or '').strip()[:_MAX_NAME]
    if not supervisor:
        supervisor = _saved_supervisor(_resolve_uid())
    if len(supervisor) < _MIN_NAME:
        return jsonify({'ok': False, 'error': 'no_supervisor'}), 200
    try:
        return jsonify(_build_xarita(supervisor))
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
