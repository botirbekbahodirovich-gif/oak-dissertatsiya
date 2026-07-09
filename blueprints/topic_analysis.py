"""AI mavzu tahdid tahlili — Topic threat analysis (topic_bp, /mavzu).

Foydalanuvchi rejalashtirayotgan dissertatsiya mavzusini kiritadi → mavjud OAK
korpusi (`dissertations`, 29k+ yozuv) bilan ILIKE bo'yicha solishtiriladi →
o'xshash ishlar Groq AI ga kontekst sifatida beriladi va tahlil qaytariladi:
o'xshashlik darajasi, eng yaqin ishlar, toraytirish/kengaytirish tavsiyasi va
"bu mavzuda ishlash xavfsizmi?" xulosasi.

Konvensiyalar (grants/reminders blueprint'lari kabi):
  * Sxema lazy + idempotent yaratiladi (_ensure_schema) — migrations/
    add_topic_analysis.sql bilan bir xil, server birinchi so'rovda self-migrate.
  * DB — data.get_connection() (PostgreSQL). Korpus ustunlari LOWERCASE:
    id, sana, daraja, olim, mavzu, ixtisoslik, muassasa, ilmiy_rahbar, link.
  * Kunlik limit: 3 tahlil/kun (topic_analysis_log dan hisoblanadi).

Routes:
  GET  /mavzu/tahlil          — sahifa (oxirgi tahlillar bilan).
  POST /mavzu/tahlil/run      — tahlilni bajarish (JSON, @csrf.exempt).
  GET  /mavzu/tahlil/history  — oxirgi 5 tahlil (JSON).
"""
import os
import re

from flask import Blueprint, jsonify, request, render_template
from flask_login import login_required, current_user

from app import csrf
from data import get_connection

try:
    import psycopg2.extras as psycopg2_extras
except Exception:  # pragma: no cover — psycopg2 always present in prod
    psycopg2_extras = None

topic_bp = Blueprint('topic_analysis', __name__, url_prefix='/mavzu')

GROQ_API_KEY = os.environ.get('GROQ_API_KEY')
GROQ_MODEL = os.environ.get('GROQ_TOPIC_MODEL', 'llama-3.3-70b-versatile')
GROQ_TIMEOUT = 15  # soniya — oshsa, faqat o'xshash natijalar qaytadi

DAILY_LIMIT = 3
MAX_TOPIC_LEN = 300
MIN_TOPIC_LEN = 8
SIMILAR_LIMIT = 15
MAX_KEYWORDS = 8

_schema_ready = False

# O'zbek stop-so'zlar — kalit so'z ajratishda tashlab yuboriladi.
_STOP_WORDS = {
    'va', 'bilan', 'uchun', 'da', 'ning', 'ga', 'ni', 'dan', 'ham', 'yoki',
    'hamda', 'bu', 'shu', 'agar', 'lekin', 'ammo', 'biroq', 'hamma', 'har',
    'bir', 'kabi', 'singari', 'orqali', 'asosida', "bo'yicha", 'haqida',
    'doir', 'oid', 'emas', 'edi', 'ekan', 'yana', 'esa', 'faqat', 'ba', 'gina',
    'ushbu', 'mazkur', 'ular', 'uni', 'unga', 'undan', 'uning', 'qanday',
    'qanaqa', 'nima', 'nechta', 'juda', 'eng', 'ko', 'kop', "ko'p", 'oz',
    'tizimi', 'tizim',  # o'zi kam ma'no beruvchi umumiy so'zlar
}

# Harflar (unicode) + ichki apostrof (o', g' variantlari) bo'yicha token.
_WORD_RE = re.compile(r"[^\W\d_]+(?:['ʻʼ‘’`][^\W\d_]+)*", re.UNICODE)
# Korpus (dissertations.mavzu) faqat oddiy ' (U+0027) ishlatadi — kalit so'zdagi
# apostrof variantlarini shunga keltiramiz, aks holda "Sunʼiy" ≠ "Sun'iy".
_APOS_TABLE = {ord(c): "'" for c in "ʻʼ‘’`"}


def _ensure_schema(cur):
    """topic_analysis_log jadvalini idempotent yaratadi (migration bilan bir xil)."""
    global _schema_ready
    if _schema_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS topic_analysis_log (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            topic TEXT NOT NULL,
            result_summary TEXT,
            similar_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_topic_log_user "
                "ON topic_analysis_log(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_topic_log_time "
                "ON topic_analysis_log(created_at)")
    _schema_ready = True


def _uid():
    return current_user.id if getattr(current_user, 'is_authenticated', False) else None


def _check_rate_limit(cur, user_id):
    """Bugun (created_at >= CURRENT_DATE) shu user tahlillari sonini qaytaradi."""
    cur.execute(
        "SELECT COUNT(*) FROM topic_analysis_log "
        "WHERE user_id = %s AND created_at >= CURRENT_DATE",
        (user_id,))
    row = cur.fetchone()
    return int(row[0]) if row else 0


def _extract_keywords(topic):
    """Mavzudan kalit so'zlarni ajratadi → ILIKE '%so'z%' patternlari ro'yxati."""
    seen, keywords = set(), []
    for tok in _WORD_RE.findall(topic or ''):
        low = tok.lower().translate(_APOS_TABLE)
        if len(low) < 4 or low in _STOP_WORDS or low in seen:
            continue
        seen.add(low)
        keywords.append(low)
        if len(keywords) >= MAX_KEYWORDS:
            break
    return ['%' + k + '%' for k in keywords]


def _find_similar(cur, patterns, topic):
    """Korpusdan o'xshash dissertatsiyalarni ILIKE ANY bo'yicha topadi."""
    if not patterns:
        # Kalit so'z chiqmasa — butun mavzu bo'yicha (juda qisqa/no'malum til).
        patterns = ['%' + (topic or '').strip() + '%']
    cur.execute(
        """
        SELECT id, olim, mavzu, sana, ixtisoslik, ilmiy_rahbar, muassasa
        FROM dissertations
        WHERE mavzu ILIKE ANY(%s)
        ORDER BY sana DESC
        LIMIT %s
        """,
        (patterns, SIMILAR_LIMIT))
    rows = cur.fetchall()
    similar = []
    for r in rows:
        similar.append({
            'id': r['id'],
            'olim': (r.get('olim') or '').strip(),
            'mavzu': (r.get('mavzu') or '').strip(),
            'sana': (r.get('sana') or '').strip(),
            'ixtisoslik': (r.get('ixtisoslik') or '').strip(),
            'rahbar': (r.get('ilmiy_rahbar') or '').strip(),
            'muassasa': (r.get('muassasa') or '').strip(),
        })
    return similar


_SYSTEM_PROMPT = (
    "Sen O'zbekiston ilmiy dissertatsiyalari mutaxassisisan. O'zbek tilida "
    "qisqa va aniq javob ber. Foydalanuvchi rejalashtirayotgan mavzu va mavjud "
    "o'xshash dissertatsiyalar beriladi. Tahlil:\n"
    "1. O'xshashlik darajasi: Yuqori/O'rta/Past va sababi\n"
    "2. Eng o'xshash 3 ta (agar bor) va qanday farqlanish mumkin\n"
    "3. Mavzuni qanday toraytirish/kengaytirish tavsiyasi\n"
    "4. Xulosa: bu mavzuda ishlash xavfsizmi?\n"
    "Javob 300-400 so'z, ro'yxat va sarlavhalar bilan."
)


def _build_similar_text(similar):
    if not similar:
        return "Korpusda o'xshash dissertatsiya topilmadi."
    lines = []
    for i, s in enumerate(similar[:SIMILAR_LIMIT], 1):
        lines.append(
            f"{i}. Mavzu: {s['mavzu']} | Olim: {s['olim']} | "
            f"Yil: {s['sana']} | Ixtisoslik: {s['ixtisoslik']} | "
            f"Ilmiy rahbar: {s['rahbar']}")
    return "\n".join(lines)


def _run_groq(topic, similar):
    """Groq AI tahlilini qaytaradi yoki timeout/xatoda None."""
    if not GROQ_API_KEY:
        return None
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY, timeout=GROQ_TIMEOUT)
        similar_text = _build_similar_text(similar)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content":
                    f"Mening mavzum: {topic}\n\nO'xshash dissertatsiyalar:\n{similar_text}"},
            ],
            max_tokens=900,
            temperature=0.4,
        )
        return (resp.choices[0].message.content or '').strip() or None
    except Exception:
        return None


# ── Routes ────────────────────────────────────────────────────────────────────

@topic_bp.route('/tahlil', methods=['GET'])
@login_required
def tahlil_page():
    history = _load_history(_uid(), limit=5)
    return render_template('topic_analysis.html',
                           history=history,
                           daily_limit=DAILY_LIMIT,
                           max_topic_len=MAX_TOPIC_LEN)


@topic_bp.route('/tahlil/run', methods=['POST'])
@csrf.exempt
@login_required
def tahlil_run():
    data = request.get_json(silent=True) or {}
    topic = (data.get('topic') or '').strip()
    if len(topic) < MIN_TOPIC_LEN:
        return jsonify({'error': 'invalid',
                        'message': "Mavzu juda qisqa. Kamida "
                                   f"{MIN_TOPIC_LEN} ta belgi kiriting."}), 400
    topic = topic[:MAX_TOPIC_LEN]
    user_id = _uid()

    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2_extras.RealDictCursor)
        _ensure_schema(cur)

        used = _check_rate_limit(cur, user_id)
        if used >= DAILY_LIMIT:
            return jsonify({
                'error': 'rate_limited',
                'message': f"Bugun {DAILY_LIMIT} ta tahlil limitiga yetdingiz",
                'remaining_today': 0,
            }), 429

        patterns = _extract_keywords(topic)
        similar = _find_similar(cur, patterns, topic)

        analysis = _run_groq(topic, similar)
        if not analysis:
            analysis = "AI tahlili vaqtinchalik mavjud emas"

        cur.execute(
            "INSERT INTO topic_analysis_log (user_id, topic, result_summary, similar_count) "
            "VALUES (%s, %s, %s, %s) RETURNING created_at",
            (user_id, topic, analysis[:2000], len(similar)))
        created_at = cur.fetchone()['created_at']
        conn.commit()

        remaining = max(0, DAILY_LIMIT - (used + 1))
        return jsonify({
            'analysis': analysis,
            'similar': similar,
            'similar_count': len(similar),
            'remaining_today': remaining,
            'checked_at': created_at.isoformat() if created_at else None,
        })
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({'error': 'server_error',
                        'message': "Xatolik yuz berdi. Iltimos, qayta urinib ko'ring."}), 500
    finally:
        conn.close()


@topic_bp.route('/tahlil/history', methods=['GET'])
@login_required
def tahlil_history():
    return jsonify({'history': _load_history(_uid(), limit=5)})


def _load_history(user_id, limit=5):
    """Oxirgi N tahlilni topic_analysis_log dan qaytaradi."""
    if not user_id:
        return []
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2_extras.RealDictCursor)
        _ensure_schema(cur)
        cur.execute(
            "SELECT id, topic, similar_count, created_at "
            "FROM topic_analysis_log WHERE user_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (user_id, limit))
        rows = cur.fetchall()
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    finally:
        conn.close()
    return [{
        'id': r['id'],
        'topic': r['topic'],
        'similar_count': r['similar_count'],
        'created_at': r['created_at'].isoformat() if r['created_at'] else None,
    } for r in rows]
