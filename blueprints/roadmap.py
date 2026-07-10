"""Akademik Reja (Roadmap) — himoyagacha yo'l xaritasi moduli.

Konstruktor (blueprints/dissertation.py) bilan YAGONA ish maydoni bo'lish uchun
qurilgan: reja Konstruktor loyihasiga bog'lanadi (roadmap_plans.diss_project_id),
bob so'z sanog'i dissertation_blocks'dan JONLI o'qiladi (2-bosqich), wizard
Konstruktor skeletini o'zi yaratadi (3-bosqich).

Jadval nomlari roadmap_* prefiksi bilan — Konstruktor jadvallariga faqat
bog'lovchi ustunlar qo'shiladi, hech narsa o'zgartirilmaydi (guard).

Xavfsizlik: barcha sahifa/API login_required; har so'rovda plan egaligi
tekshiriladi (plan.user_id = current_user.id, aks holda 404/403).
Barcha SQL parametrlangan.
"""
import json
from datetime import date, datetime, timedelta

from flask import (Blueprint, jsonify, request, render_template, redirect,
                   abort)
from flask_login import login_required, current_user

from app import csrf

roadmap_bp = Blueprint('roadmap', __name__)

_schema_ready = False

# ── SOZLANADIGAN KONSTANTALAR ────────────────────────────────────────────────

# Himoya tayyorgarligi formulasi og'irliklari (yig'indisi 1.0).
# Konstruktor ulanmaguncha dissertation komponenti hisobga olinmaydi va
# publications 100% og'irlik oladi (normalizatsiya _readiness() ichida).
READINESS_WEIGHTS = {
    'publications': 0.5,
    'dissertation_words': 0.5,
}

# OAK nashr talablari (daraja → tur → minimal soni). Qiymatlar sozlanadigan —
# rasmiy talab o'zgarsa faqat shu yerni yangilash kifoya.
OAK_REQUIREMENTS = {
    'magistr': {'maqola_milliy': 1, 'konferensiya': 1},
    'phd':     {'maqola_milliy': 3, 'maqola_xalqaro': 1, 'konferensiya': 2},
    'dsc':     {'maqola_milliy': 6, 'maqola_xalqaro': 4, 'konferensiya': 4},
}

PUB_TYPE_LABELS = {
    'maqola_milliy':  "OAK ro'yxatidagi jurnal maqolasi",
    'maqola_xalqaro': 'Xalqaro (Scopus/WoS) maqola',
    'konferensiya':   'Konferensiya tezisi',
}
PUB_STATUS_LABELS = {
    'reja': 'Rejada', 'yuborilgan': 'Yuborilgan',
    'qabul': 'Qabul qilingan', 'chop_etilgan': 'Chop etilgan',
}

# OAK dissertatsiya tuzilmasi + so'z maqsadlari (3-bosqich: wizard shu
# skeletni Konstruktorda yaratadi; 2-bosqich: Dissertatsiya tab shu
# bo'limlarga jonli so'z sanog'ini moslashtiradi).
OAK_STRUCTURE = [
    # (sarlavha, block_type, word_target)
    ('Kirish', 'special', 2000),
    ('I bob', 'chapter', 6000),
    ('II bob', 'chapter', 6000),
    ('III bob', 'chapter', 6000),
    ('Xulosa', 'special', 2000),
    ("Foydalanilgan adabiyotlar ro'yxati", 'special', 500),
    ('Avtoreferat', 'special', 6000),
]

DEGREE_LABELS = {'magistr': 'Magistrlik', 'phd': 'PhD', 'dsc': 'DSc'}

CONF_STATUS_LABELS = {'reja': 'Rejada', 'yuborilgan': 'Tezis yuborilgan',
                      'qabul': 'Qabul qilingan', 'qatnashgan': 'Qatnashilgan'}

# Jadval (reverse-schedule) bosqichlari: (kalit, nom, boshlanish_ulushi,
# tugash_ulushi) — reja start→himoya oralig'ining ulushlari sifatida.
TIMELINE_PHASES = [
    ('mavzu',    'Mavzuni tasdiqlash va reja',          0.00, 0.08),
    ('bob1',     '1-bob yozish',                        0.08, 0.32),
    ('bob2',     '2-bob yozish',                        0.32, 0.56),
    ('bob3',     '3-bob yozish',                        0.56, 0.78),
    ('nashr',    'Nashrlarni yakunlash',                0.20, 0.80),
    ('muhokama', 'Dastlabki muhokama (seminar)',        0.80, 0.90),
    ('topshirish', "Kengashga rasmiy topshirish",       0.90, 0.97),
    ('himoya',   'Himoya',                              0.97, 1.00),
]


# ── sxema (lazy, idempotent — Konstruktor patterni) ─────────────────────────

def _conn():
    from data import get_connection
    return get_connection()


def _ensure_schema(cur):
    global _schema_ready
    if _schema_ready:
        return
    # diss_project_id FK diss_projects'ga tayanadi — toza bazada /reja
    # /workspace'dan oldin ochilsa ham ishlashi uchun Konstruktor sxemasini
    # avval kafolatlaymiz (ikkalasi ham idempotent).
    from blueprints.dissertation import _ensure_schema as _ensure_diss_schema
    _ensure_diss_schema(cur)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS roadmap_plans (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            degree_type VARCHAR(30) NOT NULL DEFAULT 'phd'
                CHECK (degree_type IN ('magistr', 'phd', 'dsc')),
            field_name VARCHAR(300),
            specialty_code VARCHAR(30),
            title VARCHAR(600),
            start_date DATE,
            target_defense_date DATE,
            diss_project_id INTEGER REFERENCES diss_projects(id) ON DELETE SET NULL,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )""")
    # bitta foydalanuvchi → bitta faol reja (guard invarianti)
    cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_roadmap_plans_active
                   ON roadmap_plans(user_id) WHERE is_active""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS roadmap_publications (
            id SERIAL PRIMARY KEY,
            plan_id INTEGER NOT NULL REFERENCES roadmap_plans(id) ON DELETE CASCADE,
            pub_type VARCHAR(30) NOT NULL DEFAULT 'maqola_milliy'
                CHECK (pub_type IN ('maqola_milliy', 'maqola_xalqaro', 'konferensiya')),
            title VARCHAR(600) NOT NULL,
            venue VARCHAR(500),
            status VARCHAR(20) NOT NULL DEFAULT 'reja'
                CHECK (status IN ('reja', 'yuborilgan', 'qabul', 'chop_etilgan')),
            year INTEGER,
            url VARCHAR(600),
            created_at TIMESTAMP DEFAULT NOW()
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_roadmap_pubs_plan "
                "ON roadmap_publications(plan_id)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS roadmap_meetings (
            id SERIAL PRIMARY KEY,
            plan_id INTEGER NOT NULL REFERENCES roadmap_plans(id) ON DELETE CASCADE,
            title VARCHAR(500) NOT NULL,
            meeting_date DATE,
            notes TEXT,
            is_done BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_roadmap_meet_plan "
                "ON roadmap_meetings(plan_id)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS roadmap_conferences (
            id SERIAL PRIMARY KEY,
            plan_id INTEGER NOT NULL REFERENCES roadmap_plans(id) ON DELETE CASCADE,
            name VARCHAR(500) NOT NULL,
            location VARCHAR(300),
            event_date DATE,
            deadline DATE,
            url VARCHAR(600),
            status VARCHAR(20) NOT NULL DEFAULT 'reja'
                CHECK (status IN ('reja', 'yuborilgan', 'qabul', 'qatnashgan')),
            created_at TIMESTAMP DEFAULT NOW()
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_roadmap_conf_plan "
                "ON roadmap_conferences(plan_id)")
    # foydalanuvchi o'zgartirgan bosqich muddatlari (default dinamik hisoblanadi)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS roadmap_milestones (
            id SERIAL PRIMARY KEY,
            plan_id INTEGER NOT NULL REFERENCES roadmap_plans(id) ON DELETE CASCADE,
            phase_key VARCHAR(30) NOT NULL,
            due_date DATE,
            is_done BOOLEAN DEFAULT FALSE,
            UNIQUE(plan_id, phase_key)
        )""")
    _schema_ready = True


# ── yordamchilar ─────────────────────────────────────────────────────────────

_PLAN_COLS = ('id', 'user_id', 'degree_type', 'field_name', 'specialty_code',
              'title', 'start_date', 'target_defense_date', 'diss_project_id',
              'is_active', 'created_at', 'updated_at')


def _fetch_plan(cur, user_id):
    """Foydalanuvchining faol rejasi (dict) yoki None."""
    cur.execute(f"SELECT {', '.join(_PLAN_COLS)} FROM roadmap_plans "
                "WHERE user_id = %s AND is_active LIMIT 1", (user_id,))
    row = cur.fetchone()
    if not row:
        return None
    p = dict(zip(_PLAN_COLS, row))
    p['degree_label'] = DEGREE_LABELS.get(p['degree_type'], p['degree_type'])
    return p


def _plan_or_404(cur):
    p = _fetch_plan(cur, current_user.id)
    if not p:
        abort(404)
    return p


def _days_left(target):
    if not target:
        return None
    return (target - date.today()).days


def _parse_date(s):
    try:
        return datetime.strptime((s or '').strip(), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def _pub_progress(cur, plan):
    """Nashr talablari bo'yicha progress: {tur: {required, done, pct}}, umumiy %."""
    req = OAK_REQUIREMENTS.get(plan['degree_type'], OAK_REQUIREMENTS['phd'])
    cur.execute("""
        SELECT pub_type, COUNT(*) FROM roadmap_publications
        WHERE plan_id = %s AND status = 'chop_etilgan'
        GROUP BY pub_type
    """, (plan['id'],))
    done = dict(cur.fetchall())
    rows, total_req, total_done = [], 0, 0
    for ptype, need in req.items():
        d = min(done.get(ptype, 0), need)
        rows.append({'pub_type': ptype, 'label': PUB_TYPE_LABELS[ptype],
                     'required': need, 'done': done.get(ptype, 0),
                     'pct': round(d * 100 / need) if need else 100})
        total_req += need
        total_done += d
    overall = round(total_done * 100 / total_req) if total_req else 0
    return rows, overall


def _readiness(pub_pct, diss_pct):
    """Himoya tayyorgarligi % — og'irliklar READINESS_WEIGHTS'da.
    diss_pct None bo'lsa (Konstruktor ulanmagan) faqat nashrlar hisoblanadi."""
    if diss_pct is None:
        return pub_pct
    w = READINESS_WEIGHTS
    return round(pub_pct * w['publications'] + diss_pct * w['dissertation_words'])


def _timeline(plan, overrides=None):
    """Reverse-schedule: start→himoya oralig'ini TIMELINE_PHASES ulushlariga
    bo'lib, har bosqichga sana oralig'i beradi. overrides — roadmap_milestones
    dagi foydalanuvchi tahrirlari {phase_key: (due_date, is_done)}."""
    start, end = plan.get('start_date'), plan.get('target_defense_date')
    if not (start and end) or end <= start:
        return []
    overrides = overrides or {}
    span = (end - start).days
    today = date.today()
    out = []
    for key, label, f0, f1 in TIMELINE_PHASES:
        p_start = start + timedelta(days=round(span * f0))
        p_end = start + timedelta(days=round(span * f1))
        ov = overrides.get(key)
        if ov and ov[0]:
            p_end = ov[0]
        state = 'done' if (ov and ov[1]) else (
            'past' if p_end < today else 'active' if p_start <= today else 'future')
        # Gantt segment koordinatalari (%): template hisob-kitob qilmaydi
        left = (p_start - start).days * 100.0 / span
        width = max((p_end - p_start).days * 100.0 / span, 1.0)
        out.append({'key': key, 'label': label, 'start': p_start.isoformat(),
                    'end': p_end.isoformat(), 'state': state,
                    'days_left': (p_end - today).days,
                    'left_pct': round(left, 1), 'width_pct': round(width, 1)})
    return out


def _today_pct(plan):
    """Bugungi kunning start→himoya oralig'idagi o'rni (%) yoki None."""
    start, end = plan.get('start_date'), plan.get('target_defense_date')
    if not (start and end) or end <= start:
        return None
    pct = (date.today() - start).days * 100.0 / (end - start).days
    return round(pct, 1) if 0 <= pct <= 100 else None


def _rows(cur, sql, params, cols):
    cur.execute(sql, params)
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ── Konstruktor bilan jonli bog'lanish (2-bosqich) ──────────────────────────

# Maxsus bo'lim sarlavhasi → OAK_STRUCTURE dagi kanonik nom (so'z maqsadi uchun)
_SPECIAL_MAP = {
    'kirish': 'Kirish',
    'xulosa': 'Xulosa', 'umumiy xulosa': 'Xulosa',
    'xulosa, taklif va tavsiyalar': 'Xulosa',
    'foydalanilgan adabiyotlar': "Foydalanilgan adabiyotlar ro'yxati",
    "foydalanilgan adabiyotlar ro'yxati": "Foydalanilgan adabiyotlar ro'yxati",
    "adabiyotlar ro'yxati": "Foydalanilgan adabiyotlar ro'yxati",
    'avtoreferat': 'Avtoreferat',
}
_STRUCTURE_TARGETS = {name: target for name, _bt, target in OAK_STRUCTURE}
_CHAPTER_NAMES = [name for name, bt, _t in OAK_STRUCTURE if bt == 'chapter']
_DEFAULT_CHAPTER_TARGET = _STRUCTURE_TARGETS.get('I bob', 6000)


def _subtree_words(node):
    return (node.get('word_count') or 0) + sum(_subtree_words(c)
                                               for c in node.get('children', ()))


def _link_diss_project(cur, plan):
    """Reja hali Konstruktorga ulanmagan bo'lsa — foydalanuvchining faol
    loyihasini owner_id bo'yicha topib bog'laydi (dublikat yaratmaydi).
    Ulangan loyiha arxivlangan/o'chirilgan bo'lsa — uzadi. plan dict yangilanadi."""
    pid = plan.get('diss_project_id')
    if pid:
        cur.execute("SELECT 1 FROM diss_projects WHERE id = %s AND status <> 'archived'",
                    (pid,))
        if cur.fetchone():
            return
        pid = None  # arxivlangan — qayta topamiz
    cur.execute("""SELECT id FROM diss_projects
                   WHERE owner_id = %s AND status <> 'archived'
                   ORDER BY updated_at DESC LIMIT 1""", (plan['user_id'],))
    r = cur.fetchone()
    pid = r[0] if r else None
    if pid != plan.get('diss_project_id'):
        cur.execute("UPDATE roadmap_plans SET diss_project_id = %s, updated_at = NOW() "
                    "WHERE id = %s", (pid, plan['id']))
    plan['diss_project_id'] = pid


def _diss_progress(cur, plan):
    """Konstruktor bloklaridan JONLI bo'lim-progress:
    {sections: [...], total_words, total_target, pct, continue_block_id}
    yoki None (loyiha ulanmagan). So'z sanog'i blok + uning bolalari yig'indisi."""
    pid = plan.get('diss_project_id')
    if not pid:
        return None
    from blueprints.dissertation import _fetch_tree, _norm_title
    tree = _fetch_tree(cur, pid)      # bitta so'rov, N+1 yo'q
    if not tree:
        return None
    # blokda word_target bo'lsa (3-bosqich) — shu; aks holda OAK_STRUCTURE dan
    cur.execute("""SELECT id, COALESCE(word_target, 0) FROM dissertation_blocks
                   WHERE dissertation_id = %s""", (pid,))
    targets_db = dict(cur.fetchall())
    sections, chapter_i = [], 0
    for node in tree:                 # faqat ildiz bloklar — bo'limlar
        words = _subtree_words(node)
        if node['is_special']:
            canon = _SPECIAL_MAP.get(_norm_title(node['title']))
            target = _STRUCTURE_TARGETS.get(canon, 1000) if canon else 1000
        else:
            canon = (_CHAPTER_NAMES[chapter_i]
                     if chapter_i < len(_CHAPTER_NAMES) else None)
            target = (_STRUCTURE_TARGETS.get(canon, _DEFAULT_CHAPTER_TARGET)
                      if canon else _DEFAULT_CHAPTER_TARGET)
            chapter_i += 1
        if targets_db.get(node['id']):
            target = targets_db[node['id']]
        sections.append({
            'block_id': node['id'], 'title': node['heading'] or node['title'],
            'words': words, 'target': target,
            'pct': min(round(words * 100 / target), 100) if target else 0,
            'started': words > 0,
        })
    total_words = sum(s['words'] for s in sections)
    total_target = sum(s['target'] for s in sections)
    pct = min(round(total_words * 100 / total_target), 100) if total_target else 0
    # "Yozishni davom ettirish" — birinchi tugallanmagan bo'lim (yoki birinchisi)
    cont = next((s['block_id'] for s in sections if s['pct'] < 100),
                sections[0]['block_id'])
    return {'sections': sections, 'total_words': total_words,
            'total_target': total_target, 'pct': pct,
            'continue_block_id': cont, 'diss_id': pid}


# ── sahifalar ────────────────────────────────────────────────────────────────

@roadmap_bp.route('/reja')
@login_required
def dashboard():
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            plan = _fetch_plan(cur, current_user.id)
            if not plan:
                conn.commit()
                return redirect('/reja/wizard')
            pub_rows, pub_pct = _pub_progress(cur, plan)
            pubs = _rows(cur,
                "SELECT id, pub_type, title, venue, status, year, url "
                "FROM roadmap_publications WHERE plan_id = %s "
                "ORDER BY created_at DESC", (plan['id'],),
                ('id', 'pub_type', 'title', 'venue', 'status', 'year', 'url'))
            meetings = _rows(cur,
                "SELECT id, title, meeting_date, notes, is_done "
                "FROM roadmap_meetings WHERE plan_id = %s "
                "ORDER BY meeting_date DESC NULLS LAST, id DESC", (plan['id'],),
                ('id', 'title', 'meeting_date', 'notes', 'is_done'))
            confs = _rows(cur,
                "SELECT id, name, location, event_date, deadline, url, status "
                "FROM roadmap_conferences WHERE plan_id = %s "
                "ORDER BY event_date ASC NULLS LAST, id DESC", (plan['id'],),
                ('id', 'name', 'location', 'event_date', 'deadline', 'url', 'status'))
            cur.execute("SELECT phase_key, due_date, is_done FROM roadmap_milestones "
                        "WHERE plan_id = %s", (plan['id'],))
            overrides = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
            # Konstruktor bilan jonli bog'lanish: loyihani owner bo'yicha
            # bog'laydi (bo'lsa) va bo'lim so'z sanog'ini o'qiydi
            _link_diss_project(cur, plan)
            diss = _diss_progress(cur, plan)
        conn.commit()
    finally:
        conn.close()
    diss_pct = diss['pct'] if diss else None
    return render_template(
        'roadmap/dashboard.html', plan=plan,
        days_left=_days_left(plan['target_defense_date']),
        readiness=_readiness(pub_pct, diss_pct),
        pub_rows=pub_rows, pub_pct=pub_pct, pubs=pubs,
        meetings=meetings, confs=confs,
        timeline=_timeline(plan, overrides), today_pct=_today_pct(plan),
        oak_structure=OAK_STRUCTURE, diss_progress=diss,
        pub_type_labels=PUB_TYPE_LABELS, pub_status_labels=PUB_STATUS_LABELS,
        conf_status_labels=CONF_STATUS_LABELS, degree_labels=DEGREE_LABELS)


@roadmap_bp.route('/reja/wizard')
@login_required
def wizard():
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            plan = _fetch_plan(cur, current_user.id)
        conn.commit()
    finally:
        conn.close()
    if plan:
        return redirect('/reja')
    return render_template('roadmap/wizard.html', degree_labels=DEGREE_LABELS)


# ── API: reja yaratish / yangilash ───────────────────────────────────────────

def _scaffold_constructor(cur, user_id, title, degree, specialty):
    """Wizard yakunida Konstruktor loyihasini tayyorlaydi (3-bosqich):
      - foydalanuvchining faol loyihasi BOR bo'lsa → o'shanga ulanadi
        (dublikat yaratmaydi); yo'q bo'lsa OAK skeleti bilan yangi yaratadi.
      - Yangi loyiha bloklariga word_target qo'yiladi (OAK_STRUCTURE).
    Qaytaradi: diss_projects.id"""
    cur.execute("""SELECT id FROM diss_projects
                   WHERE owner_id = %s AND status <> 'archived'
                   ORDER BY updated_at DESC LIMIT 1""", (user_id,))
    r = cur.fetchone()
    if r:
        # mavjud loyiha — reja ma'lumotlari bilan to'ldiramiz (bo'sh joylarni)
        cur.execute("""UPDATE diss_projects
                       SET specialty_code = COALESCE(NULLIF(specialty_code, ''), %s),
                           updated_at = NOW()
                       WHERE id = %s""", (specialty or None, r[0]))
        # mavjud bloklarga word_target'ni faqat bo'sh bo'lsa taklif qilamiz
        from blueprints.dissertation import _norm_title
        cur.execute("""SELECT id, title, block_type FROM dissertation_blocks
                       WHERE dissertation_id = %s AND parent_id IS NULL
                       ORDER BY sort_order, id""", (r[0],))
        chapter_i = 0
        for bid, btitle, btype in cur.fetchall():
            if (btype or 'chapter') == 'special':
                canon = _SPECIAL_MAP.get(_norm_title(btitle))
                target = _STRUCTURE_TARGETS.get(canon) if canon else None
            else:
                canon = (_CHAPTER_NAMES[chapter_i]
                         if chapter_i < len(_CHAPTER_NAMES) else None)
                target = _STRUCTURE_TARGETS.get(canon, _DEFAULT_CHAPTER_TARGET)
                chapter_i += 1
            if target:
                cur.execute("""UPDATE dissertation_blocks SET word_target = %s
                               WHERE id = %s AND word_target IS NULL""", (target, bid))
        return r[0]
    # yangi loyiha + to'liq OAK skeleti
    cur.execute("""
        INSERT INTO diss_projects (owner_id, title, degree_type, specialty_code)
        VALUES (%s, %s, %s, %s) RETURNING id
    """, (user_id, title or 'Dissertatsiya', degree, specialty or None))
    pid = cur.fetchone()[0]
    for i, (btitle, btype, target) in enumerate(OAK_STRUCTURE):
        cur.execute("""
            INSERT INTO dissertation_blocks
                (dissertation_id, title, sort_order, depth, block_type, word_target)
            VALUES (%s, %s, %s, 0, %s, %s)
        """, (pid, btitle, i, btype, target))
    from blueprints.dissertation import _recompute_numbering
    _recompute_numbering(cur, pid)
    return pid


@roadmap_bp.route('/api/reja/create', methods=['POST'])
@csrf.exempt
@login_required
def plan_create():
    data = request.get_json(silent=True) or {}
    degree = data.get('degree_type') if data.get('degree_type') in DEGREE_LABELS else 'phd'
    field = (data.get('field_name') or '').strip()[:300]
    specialty = (data.get('specialty_code') or '').strip()[:30]
    title = (data.get('title') or '').strip()[:600]
    start = _parse_date(data.get('start_date')) or date.today()
    defense = _parse_date(data.get('target_defense_date'))
    if not defense:
        return jsonify({'success': False, 'error': 'Himoya sanasini kiriting'}), 400
    if defense <= start:
        return jsonify({'success': False,
                        'error': "Himoya sanasi boshlanishdan keyin bo'lishi kerak"}), 400
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            if _fetch_plan(cur, current_user.id):
                return jsonify({'success': False,
                                'error': 'Sizda allaqachon faol reja bor'}), 400
            # Konstruktor loyihasi: mavjudga ulanish yoki OAK skeleti bilan yangi
            diss_id = _scaffold_constructor(cur, current_user.id, title, degree,
                                            specialty)
            cur.execute("""
                INSERT INTO roadmap_plans (user_id, degree_type, field_name,
                    specialty_code, title, start_date, target_defense_date,
                    diss_project_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (current_user.id, degree, field or None, specialty or None,
                  title or None, start, defense, diss_id))
            plan_id = cur.fetchone()[0]
        conn.commit()
        return jsonify({'success': True, 'id': plan_id, 'diss_id': diss_id,
                        'redirect': '/reja'})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@roadmap_bp.route('/api/reja/update', methods=['POST'])
@csrf.exempt
@login_required
def plan_update():
    data = request.get_json(silent=True) or {}
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            plan = _plan_or_404(cur)
            degree = data.get('degree_type') if data.get('degree_type') in DEGREE_LABELS \
                else plan['degree_type']
            start = _parse_date(data.get('start_date')) or plan['start_date']
            defense = _parse_date(data.get('target_defense_date')) or plan['target_defense_date']
            cur.execute("""
                UPDATE roadmap_plans SET degree_type = %s, field_name = %s,
                    specialty_code = %s, title = %s, start_date = %s,
                    target_defense_date = %s, updated_at = NOW()
                WHERE id = %s
            """, (degree,
                  (data.get('field_name') or plan['field_name'] or '').strip()[:300] or None,
                  (data.get('specialty_code') or plan['specialty_code'] or '').strip()[:30] or None,
                  (data.get('title') or plan['title'] or '').strip()[:600] or None,
                  start, defense, plan['id']))
        conn.commit()
        return jsonify({'success': True})
    finally:
        conn.close()


# ── API: nashrlar CRUD ───────────────────────────────────────────────────────

@roadmap_bp.route('/api/reja/pub/add', methods=['POST'])
@csrf.exempt
@login_required
def pub_add():
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()[:600]
    if not title:
        return jsonify({'success': False, 'error': 'Sarlavha kiritilishi shart'}), 400
    ptype = data.get('pub_type') if data.get('pub_type') in PUB_TYPE_LABELS else 'maqola_milliy'
    status = data.get('status') if data.get('status') in PUB_STATUS_LABELS else 'reja'
    year = None
    try:
        year = int(data.get('year')) if data.get('year') else None
    except (TypeError, ValueError):
        pass
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            plan = _plan_or_404(cur)
            cur.execute("""
                INSERT INTO roadmap_publications
                    (plan_id, pub_type, title, venue, status, year, url)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (plan['id'], ptype, title,
                  (data.get('venue') or '').strip()[:500] or None, status, year,
                  (data.get('url') or '').strip()[:600] or None))
            new_id = cur.fetchone()[0]
        conn.commit()
        return jsonify({'success': True, 'id': new_id})
    finally:
        conn.close()


@roadmap_bp.route('/api/reja/pub/<int:pid>/update', methods=['POST'])
@csrf.exempt
@login_required
def pub_update(pid):
    data = request.get_json(silent=True) or {}
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            plan = _plan_or_404(cur)
            status = data.get('status') if data.get('status') in PUB_STATUS_LABELS else None
            if status:
                cur.execute("UPDATE roadmap_publications SET status = %s "
                            "WHERE id = %s AND plan_id = %s", (status, pid, plan['id']))
        conn.commit()
        return jsonify({'success': True})
    finally:
        conn.close()


@roadmap_bp.route('/api/reja/pub/<int:pid>/delete', methods=['POST'])
@csrf.exempt
@login_required
def pub_delete(pid):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            plan = _plan_or_404(cur)
            cur.execute("DELETE FROM roadmap_publications WHERE id = %s AND plan_id = %s",
                        (pid, plan['id']))
        conn.commit()
        return jsonify({'success': True})
    finally:
        conn.close()


# ── API: uchrashuvlar CRUD ───────────────────────────────────────────────────

@roadmap_bp.route('/api/reja/meeting/add', methods=['POST'])
@csrf.exempt
@login_required
def meeting_add():
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()[:500]
    if not title:
        return jsonify({'success': False, 'error': 'Mavzu kiritilishi shart'}), 400
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            plan = _plan_or_404(cur)
            cur.execute("""
                INSERT INTO roadmap_meetings (plan_id, title, meeting_date, notes)
                VALUES (%s, %s, %s, %s) RETURNING id
            """, (plan['id'], title, _parse_date(data.get('meeting_date')),
                  (data.get('notes') or '').strip()[:2000] or None))
            new_id = cur.fetchone()[0]
        conn.commit()
        return jsonify({'success': True, 'id': new_id})
    finally:
        conn.close()


@roadmap_bp.route('/api/reja/meeting/<int:mid>/toggle', methods=['POST'])
@csrf.exempt
@login_required
def meeting_toggle(mid):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            plan = _plan_or_404(cur)
            cur.execute("UPDATE roadmap_meetings SET is_done = NOT is_done "
                        "WHERE id = %s AND plan_id = %s", (mid, plan['id']))
        conn.commit()
        return jsonify({'success': True})
    finally:
        conn.close()


@roadmap_bp.route('/api/reja/meeting/<int:mid>/delete', methods=['POST'])
@csrf.exempt
@login_required
def meeting_delete(mid):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            plan = _plan_or_404(cur)
            cur.execute("DELETE FROM roadmap_meetings WHERE id = %s AND plan_id = %s",
                        (mid, plan['id']))
        conn.commit()
        return jsonify({'success': True})
    finally:
        conn.close()


# ── API: konferensiyalar CRUD ────────────────────────────────────────────────

@roadmap_bp.route('/api/reja/conf/add', methods=['POST'])
@csrf.exempt
@login_required
def conf_add():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()[:500]
    if not name:
        return jsonify({'success': False, 'error': 'Nomi kiritilishi shart'}), 400
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            plan = _plan_or_404(cur)
            cur.execute("""
                INSERT INTO roadmap_conferences
                    (plan_id, name, location, event_date, deadline, url)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """, (plan['id'], name,
                  (data.get('location') or '').strip()[:300] or None,
                  _parse_date(data.get('event_date')),
                  _parse_date(data.get('deadline')),
                  (data.get('url') or '').strip()[:600] or None))
            new_id = cur.fetchone()[0]
        conn.commit()
        return jsonify({'success': True, 'id': new_id})
    finally:
        conn.close()


@roadmap_bp.route('/api/reja/conf/<int:cid>/status', methods=['POST'])
@csrf.exempt
@login_required
def conf_status(cid):
    data = request.get_json(silent=True) or {}
    status = data.get('status')
    if status not in CONF_STATUS_LABELS:
        return jsonify({'success': False, 'error': "Noto'g'ri holat"}), 400
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            plan = _plan_or_404(cur)
            cur.execute("UPDATE roadmap_conferences SET status = %s "
                        "WHERE id = %s AND plan_id = %s", (status, cid, plan['id']))
        conn.commit()
        return jsonify({'success': True})
    finally:
        conn.close()


@roadmap_bp.route('/api/reja/conf/<int:cid>/delete', methods=['POST'])
@csrf.exempt
@login_required
def conf_delete(cid):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            plan = _plan_or_404(cur)
            cur.execute("DELETE FROM roadmap_conferences WHERE id = %s AND plan_id = %s",
                        (cid, plan['id']))
        conn.commit()
        return jsonify({'success': True})
    finally:
        conn.close()
