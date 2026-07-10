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

# pg_trgm o'xshashlik qatlami (Layer 1) — GIN indeks LOWER(TRIM(mavzu)) ustida.
SIM_THRESHOLD = 0.25          # shu chegaradan pasti "o'xshash" hisoblanmaydi
SIM_LIMIT = 20
SIM_BAND_EXACT = 0.7          # > 0.7  — deyarli aynan
SIM_BAND_STRONG = 0.5         # 0.5–0.7 — kuchli mos kelish
TREND_WINDOW = 5              # trend yo'nalishi oxirgi N yil bo'yicha

# sana TEXT ustunidan 4 xonali yilni ajratish ('2024-05-16' ham, '16.05.2024' ham)
_YEAR_SQL = "substring(sana FROM '((19|20)[0-9]{2})')::int"

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


def _row_to_item(r, sim_score=None):
    band = None
    if sim_score is not None:
        band = ('exact' if sim_score > SIM_BAND_EXACT
                else 'strong' if sim_score >= SIM_BAND_STRONG
                else 'partial')
    return {
        'id': r['id'],
        'olim': (r.get('olim') or '').strip(),
        'mavzu': (r.get('mavzu') or '').strip(),
        'sana': (r.get('sana') or '').strip(),
        'daraja': (r.get('daraja') or '').strip(),
        'ixtisoslik': (r.get('ixtisoslik') or '').strip(),
        'ixtisoslik_nomi': (r.get('ixtisoslik_nomi') or '').strip(),
        'rahbar': (r.get('ilmiy_rahbar') or '').strip(),
        'muassasa': (r.get('muassasa') or '').strip(),
        'sim': round(sim_score * 100) if sim_score is not None else None,
        'band': band,
    }


def _find_similar(cur, patterns, topic):
    """Korpusdan o'xshash dissertatsiyalarni ILIKE ANY bo'yicha topadi
    (trigram hech narsa topmaganida zaxira yo'l)."""
    if not patterns:
        # Kalit so'z chiqmasa — butun mavzu bo'yicha (juda qisqa/no'malum til).
        patterns = ['%' + (topic or '').strip() + '%']
    cur.execute(
        """
        SELECT id, olim, mavzu, sana, daraja, ixtisoslik, ixtisoslik_nomi,
               ilmiy_rahbar, muassasa
        FROM dissertations
        WHERE mavzu ILIKE ANY(%s)
        ORDER BY sana DESC
        LIMIT %s
        """,
        (patterns, SIMILAR_LIMIT))
    return [_row_to_item(r) for r in cur.fetchall()]


def _similarity_search(cur, topic):
    """Layer 1, Step A — pg_trgm similarity bo'yicha eng yaqin mavzular.
    WHERE'dagi %% (similarity operatori) GIN indeksdan foydalanadi; chegara
    set_limit bilan SIM_THRESHOLD ga o'rnatiladi."""
    q = (topic or '').strip()
    cur.execute("SELECT set_limit(%s)", (SIM_THRESHOLD,))
    cur.execute(
        """
        SELECT id, olim, mavzu, sana, daraja, ixtisoslik, ixtisoslik_nomi,
               ilmiy_rahbar, muassasa,
               similarity(LOWER(TRIM(mavzu)), LOWER(TRIM(%s))) AS sim_score
        FROM dissertations
        WHERE LOWER(TRIM(mavzu)) %% LOWER(TRIM(%s))
        ORDER BY sim_score DESC
        LIMIT %s
        """,
        (q, q, SIM_LIMIT))
    return [_row_to_item(r, float(r['sim_score'])) for r in cur.fetchall()]


def _detect_specialty(similar):
    """O'xshash natijalar ichida ustun ixtisoslik kodini aniqlaydi (sim bilan
    vaznlangan). B–D bosqichlar shu kod bo'yicha ishlaydi."""
    weights, names = {}, {}
    for s in similar:
        code = s.get('ixtisoslik')
        if not code:
            continue
        weights[code] = weights.get(code, 0.0) + ((s.get('sim') or 40) / 100.0)
        if s.get('ixtisoslik_nomi') and code not in names:
            names[code] = s['ixtisoslik_nomi']
    if not weights:
        return None, None
    code = max(weights, key=weights.get)
    return code, names.get(code, '')


def _trend_direction(years, counts):
    """Oxirgi TREND_WINDOW yil bo'yicha oddiy chiziqli regressiya qiyaligi →
    'growing' / 'stable' / 'declining'."""
    pts = list(zip(years, counts))[-TREND_WINDOW:]
    if len(pts) < 3:
        return 'stable'
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    n = len(pts)
    mx, my = sum(xs) / n, sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    if not var:
        return 'stable'
    slope = sum((x - mx) * (y - my) for x, y in pts) / var
    thr = max(0.3, 0.08 * my)      # kichik sohalarda shovqinni "trend" demaslik
    if slope >= thr:
        return 'growing'
    if slope <= -thr:
        return 'declining'
    return 'stable'


def _specialty_trend(cur, code):
    """Step B — yillar kesimida himoyalar soni + yo'nalish."""
    cur.execute(
        f"""
        SELECT {_YEAR_SQL} AS year, COUNT(*) AS cnt
        FROM dissertations
        WHERE ixtisoslik = %s AND sana IS NOT NULL
          AND sana ~ '(19|20)[0-9]{{2}}'
        GROUP BY 1 ORDER BY 1
        """,
        (code,))
    rows = [r for r in cur.fetchall() if r['year']]
    if not rows:
        return None
    years = [int(r['year']) for r in rows]
    counts = [int(r['cnt']) for r in rows]
    return {
        'years': years,
        'counts': counts,
        'total': sum(counts),
        'direction': _trend_direction(years, counts),
    }


def _specialty_advisors(cur, code):
    """Step C — shu ixtisoslikdagi faol rahbarlar (Rahbar topish ko'prigi):
    kamida 2 shogird, faollik (oxirgi yil) + tajriba bo'yicha tartiblangan."""
    cur.execute(
        f"""
        SELECT TRIM(ilmiy_rahbar) AS rahbar,
               COUNT(*) AS student_count,
               MAX({_YEAR_SQL}) AS last_active,
               ARRAY_AGG(DISTINCT TRIM(muassasa))
                   FILTER (WHERE muassasa IS NOT NULL AND TRIM(muassasa) <> '')
                   AS institutions
        FROM dissertations
        WHERE ixtisoslik = %s
          AND ilmiy_rahbar IS NOT NULL AND TRIM(ilmiy_rahbar) <> ''
        GROUP BY 1
        HAVING COUNT(*) >= 2
        ORDER BY 3 DESC NULLS LAST, 2 DESC
        LIMIT 15
        """,
        (code,))
    out = []
    for r in cur.fetchall():
        out.append({
            'rahbar': r['rahbar'],
            'student_count': int(r['student_count']),
            'last_active': int(r['last_active']) if r['last_active'] else None,
            'institutions': (r['institutions'] or [])[:3],
        })
    return out


def _specialty_institutions(cur, code):
    """Step D — geografiya: shu ixtisoslik qaysi muassasalarda himoya qilinadi."""
    cur.execute(
        """
        SELECT TRIM(muassasa) AS muassasa, COUNT(*) AS cnt
        FROM dissertations
        WHERE ixtisoslik = %s
          AND muassasa IS NOT NULL AND TRIM(muassasa) <> ''
        GROUP BY 1 ORDER BY 2 DESC LIMIT 10
        """,
        (code,))
    return [{'muassasa': r['muassasa'], 'count': int(r['cnt'])}
            for r in cur.fetchall()]


_SYSTEM_PROMPT = (
    "Sen O'zbekiston ilmiy dissertatsiyalari mutaxassisisan. O'zbek tilida "
    "qisqa va aniq javob ber. Foydalanuvchi rejalashtirayotgan mavzu, mavjud "
    "o'xshash dissertatsiyalar va OAK korpusidan olingan STATISTIK MA'LUMOT "
    "(soha trendi, faol rahbarlar, yetakchi muassasalar) beriladi — xulosalarni "
    "shu real raqamlarga tayangan holda chiqar, taxmin qilma. Tahlil:\n"
    "1. O'xshashlik darajasi: Yuqori/O'rta/Past va sababi (foizlarga tayanib)\n"
    "2. Eng o'xshash 3 ta (agar bor) va qanday farqlanish mumkin\n"
    "3. Mavzuni qanday toraytirish/kengaytirish tavsiyasi\n"
    "4. Soha manzarasi: trend (o'sish/pasayish) nimani anglatadi, qaysi "
    "rahbar(lar) va muassasa(lar)ga murojaat qilish maqsadga muvofiq\n"
    "5. Xulosa: bu mavzuda ishlash xavfsizmi?\n"
    "Javob 300-450 so'z, ro'yxat va sarlavhalar bilan."
)

_TREND_LABELS = {'growing': "o'sib bormoqda", 'stable': 'barqaror',
                 'declining': 'pasaymoqda'}


def _build_stats_text(specialty, trend, advisors, institutions, bands):
    """DB natijalarini (Layer 1) Groq uchun ixcham kontekst matniga aylantiradi."""
    lines = []
    if bands:
        lines.append(
            "O'xshashlik taqsimoti: "
            f"{bands.get('exact', 0)} ta deyarli aynan (>70%), "
            f"{bands.get('strong', 0)} ta kuchli (50-70%), "
            f"{bands.get('partial', 0)} ta qisman (25-50%).")
    if specialty:
        label = specialty['code'] + (
            f" — {specialty['name']}" if specialty.get('name') else '')
        lines.append(f"Aniqlangan ixtisoslik: {label}")
    if trend:
        pairs = ', '.join(f"{y}:{c}" for y, c in
                          list(zip(trend['years'], trend['counts']))[-8:])
        lines.append(
            f"Yillik himoyalar (jami {trend['total']}): {pairs} → trend: "
            + _TREND_LABELS.get(trend['direction'], trend['direction']))
    if advisors:
        tops = '; '.join(
            f"{a['rahbar']} ({a['student_count']} shogird"
            + (f", oxirgi {a['last_active']}" if a['last_active'] else '') + ")"
            for a in advisors[:5])
        lines.append(f"Eng faol ilmiy rahbarlar: {tops}")
    if institutions:
        tops = '; '.join(f"{i['muassasa']} ({i['count']})"
                         for i in institutions[:5])
        lines.append(f"Yetakchi muassasalar: {tops}")
    return "\n".join(lines) if lines else "Statistik ma'lumot topilmadi."


def _build_similar_text(similar):
    if not similar:
        return "Korpusda o'xshash dissertatsiya topilmadi."
    lines = []
    for i, s in enumerate(similar[:SIMILAR_LIMIT], 1):
        sim_part = (f"O'xshashlik: {s['sim']}% | "
                    if s.get('sim') is not None else '')
        lines.append(
            f"{i}. {sim_part}Mavzu: {s['mavzu']} | Olim: {s['olim']} | "
            f"Yil: {s['sana']} | Ixtisoslik: {s['ixtisoslik']} | "
            f"Ilmiy rahbar: {s['rahbar']}")
    return "\n".join(lines)


def _run_groq(topic, similar, stats_text=''):
    """Groq AI tahlilini qaytaradi yoki timeout/xatoda None. stats_text —
    Layer 1 (DB) natijalari, AI xulosani real raqamlarga bog'lash uchun."""
    if not GROQ_API_KEY:
        return None
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY, timeout=GROQ_TIMEOUT)
        similar_text = _build_similar_text(similar)
        user_content = (
            f"Mening mavzum: {topic}\n\n"
            f"STATISTIK MA'LUMOT (OAK korpusi):\n{stats_text}\n\n"
            f"O'xshash dissertatsiyalar:\n{similar_text}")
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            max_tokens=1100,
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

        # Layer 1 (DB): trigram o'xshashlik; topilmasa eski ILIKE zaxira yo'li.
        similar = []
        try:
            cur.execute("SAVEPOINT sim_search")
            similar = _similarity_search(cur, topic)
            cur.execute("RELEASE SAVEPOINT sim_search")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT sim_search")
        if not similar:
            similar = _find_similar(cur, _extract_keywords(topic), topic)

        bands = {'exact': 0, 'strong': 0, 'partial': 0}
        for s in similar:
            if s.get('band') in bands:
                bands[s['band']] += 1

        # Layer 1 davomi: ixtisoslik statistikasi (B–D). Xato tahlilni buzmaydi.
        spec_code, spec_name = _detect_specialty(similar)
        specialty = ({'code': spec_code, 'name': spec_name or ''}
                     if spec_code else None)
        trend, advisors, institutions = None, [], []
        if spec_code:
            try:
                cur.execute("SAVEPOINT data_layer")
                trend = _specialty_trend(cur, spec_code)
                advisors = _specialty_advisors(cur, spec_code)
                institutions = _specialty_institutions(cur, spec_code)
                cur.execute("RELEASE SAVEPOINT data_layer")
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT data_layer")
                trend, advisors, institutions = None, [], []

        # Layer 2 (AI): DB natijalari kontekst sifatida promptga kiradi.
        stats_text = _build_stats_text(specialty, trend, advisors,
                                       institutions, bands)
        analysis = _run_groq(topic, similar, stats_text)
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
            'bands': bands,
            'specialty': specialty,
            'trend': trend,
            'advisors': advisors,
            'institutions': institutions,
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
