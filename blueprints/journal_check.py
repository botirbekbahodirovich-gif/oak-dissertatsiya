"""Jurnal tekshiruvi — journal verification against OAK / Scopus / suspect lists.

Extends the existing `journals` table (never a parallel registry):
  - name_normalized  — trigram-searchable canonical title
  - quartile / sjr / h_index — Scopus metrics
  - suspect_reason   — why is_predatory was set (advisory text)
  - source           — where the row came from ('oak' | 'scopus' | 'wos' | 'manual')

New tables:
  - suspect_publishers  — publisher-level advisory list, seeded from
    static/data/suspect_publishers.json (admin can re-upload to replace)
  - journal_search_log  — lightweight analytics, capped at ~10K rows

Public API (advisory only — never blocks anything):
  GET /api/journals/verify?q=<name-or-ISSN>   (rate-limited 10/min per IP)
  GET /jurnal-tekshirish                      (standalone checker page)

UI wording rule: never "yirtqich"/"predatory" — always "shubhali" / "ehtiyot bo'ling".
"""
import json
import os
import random
import re

from flask import Blueprint, jsonify, render_template, request

journal_check_bp = Blueprint('journal_check', __name__)

SUSPECT_JSON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'static', 'data', 'suspect_publishers.json')

VERIFY_LIMIT_PER_MIN = 10
SEARCH_LOG_CAP = 10000

# ── normalization / ISSN helpers ────────────────────────────────────────────
_APOSTROPHES = "‘’ʻʼ`´'"
_NON_WORD = re.compile(r"[^0-9a-zЀ-ӿ]+")

def normalize_title(s):
    """Canonical form used for trigram matching: lowercase, apostrophe
    variants unified then dropped with all other punctuation, spaces collapsed."""
    s = (s or '').strip().lower()
    for ch in _APOSTROPHES:
        s = s.replace(ch, '')
    s = _NON_WORD.sub(' ', s)
    return ' '.join(s.split())[:600]


_ISSN_RE = re.compile(r'^\d{4}-?\d{3}[\dXx]$')

def looks_like_issn(q):
    return bool(_ISSN_RE.match((q or '').strip()))


def issn_normalize(q):
    digits = (q or '').strip().replace('-', '').upper()
    return digits[:4] + '-' + digits[4:] if len(digits) == 8 else None


def issn_valid(q):
    """ISSN check digit: weighted mod-11 (weights 8..2, X = 10)."""
    s = (q or '').strip().replace('-', '').upper()
    if not re.match(r'^\d{7}[\dX]$', s):
        return False
    total = sum(int(c) * w for c, w in zip(s[:7], range(8, 1, -1)))
    check = (11 - total % 11) % 11
    return s[7] == ('X' if check == 10 else str(check))


# ── schema (called from app.py init_database after core tables exist) ──────
def ensure_schema(cur):
    for col, typ in (
        ('name_normalized', 'VARCHAR(600)'),
        ('quartile', 'VARCHAR(5)'),
        ('sjr', 'DECIMAL(6,3)'),
        ('h_index', 'INTEGER'),
        ('suspect_reason', 'VARCHAR(200)'),
        ("source", "VARCHAR(30) DEFAULT 'manual'"),
    ):
        cur.execute(f"ALTER TABLE journals ADD COLUMN IF NOT EXISTS {col} {typ}")
    cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_journals_name_trgm "
                "ON journals USING gin (name_normalized gin_trgm_ops)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_journals_issn "
                "ON journals (issn) WHERE issn IS NOT NULL")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_journals_eissn "
                "ON journals (eissn) WHERE eissn IS NOT NULL")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS suspect_publishers (
            id SERIAL PRIMARY KEY,
            publisher VARCHAR(400) NOT NULL UNIQUE,
            publisher_normalized VARCHAR(400),
            reason VARCHAR(200),
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS journal_search_log (
            id SERIAL PRIMARY KEY,
            query VARCHAR(300),
            result_count INTEGER DEFAULT 0,
            had_warning BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    # advisory flag saved with a publication when its journal was flagged
    cur.execute("ALTER TABLE olim_maqolalar "
                "ADD COLUMN IF NOT EXISTS journal_flag VARCHAR(20)")
    # backfill normalized titles for rows seeded/added before this column
    cur.execute("SELECT id, name FROM journals "
                "WHERE name_normalized IS NULL OR name_normalized = ''")
    for jid, name in cur.fetchall():
        cur.execute("UPDATE journals SET name_normalized = %s WHERE id = %s",
                    (normalize_title(name), jid))
    cur.execute("SELECT COUNT(*) FROM suspect_publishers")
    if (cur.fetchone()[0] or 0) == 0:
        seed_suspects(cur)


def seed_suspects(cur, replace=False):
    """Load static/data/suspect_publishers.json into suspect_publishers.
    replace=True wipes the table first (admin re-upload)."""
    try:
        with open(SUSPECT_JSON_PATH, encoding='utf-8') as f:
            entries = json.load(f)
    except Exception:
        return 0
    if replace:
        cur.execute("DELETE FROM suspect_publishers")
    n = 0
    for e in entries:
        pub = (e.get('publisher') or '').strip()
        if not pub:
            continue
        cur.execute("""
            INSERT INTO suspect_publishers (publisher, publisher_normalized, reason)
            VALUES (%s, %s, %s)
            ON CONFLICT (publisher) DO UPDATE
                SET publisher_normalized = EXCLUDED.publisher_normalized,
                    reason = EXCLUDED.reason
        """, (pub[:400], normalize_title(pub)[:400],
              (e.get('reason') or 'shubhali deb belgilangan')[:200]))
        n += 1
    return n


# ── verification core (shared by the API and any server-side caller) ───────
def _journal_result(row):
    (jid, name, issn, eissn, publisher, country, quartile, h_index,
     impact_factor, oak, scopus, wos, scholar, suspect, reason) = row
    badges, parts = [], []
    if oak:
        badges.append('oak')
        parts.append('OAK tomonidan tan olingan')
    if scopus:
        badges.append('scopus_' + quartile.lower() if quartile else 'scopus')
        parts.append('Scopus indekslangan' + (f' ({quartile})' if quartile else ''))
    if wos:
        badges.append('wos')
        parts.append('Web of Science indekslangan')
    if scholar:
        badges.append('scholar')
    if suspect:
        status = 'suspect'
        message = "Ehtiyot bo'ling — bu nashriyot shubhali deb belgilangan"
        if reason:
            message += f' ({reason})'
    elif parts:
        status = 'trusted'
        message = ', '.join(parts)
    else:
        status = 'unknown'
        message = "Ro'yxatlarimizda bor, lekin OAK/Scopus tasdig'i yo'q"
    return {
        'id': jid, 'title': name, 'issn': issn or '', 'eissn': eissn or '',
        'publisher': publisher or '', 'country': country or '',
        'quartile': quartile or '', 'h_index': h_index,
        'impact_factor': float(impact_factor) if impact_factor is not None else None,
        'status': status, 'badges': badges, 'message': message,
        'url': f'/journals/{jid}',
    }


_JR_SELECT = """
    SELECT id, name, issn, eissn, publisher, country, quartile, h_index,
           impact_factor, oak_approved, scopus_indexed, wos_indexed,
           scholar_indexed, is_predatory, suspect_reason
    FROM journals
"""


def verify_query(cur, q):
    """Return (results, warnings) for a journal name or ISSN query."""
    q = (q or '').strip()[:300]
    results, warnings = [], []
    if looks_like_issn(q):
        if not issn_valid(q):
            warnings.append({'type': 'invalid_issn',
                             'message': "ISSN formati noto'g'ri (masalan: 1234-5678)"})
            return results, warnings
        norm = issn_normalize(q)
        cur.execute(_JR_SELECT + """
            WHERE is_active = TRUE AND (
                REPLACE(UPPER(COALESCE(issn, '')), '-', '') = REPLACE(%s, '-', '')
                OR REPLACE(UPPER(COALESCE(eissn, '')), '-', '') = REPLACE(%s, '-', ''))
            LIMIT 5
        """, (norm, norm))
        results = [_journal_result(r) for r in cur.fetchall()]
    else:
        nq = normalize_title(q)
        if len(nq) >= 3:
            cur.execute(_JR_SELECT + """
                WHERE is_active = TRUE AND COALESCE(name_normalized, '') <> ''
                  AND (similarity(name_normalized, %s) >= 0.25
                       OR name_normalized LIKE %s)
                ORDER BY GREATEST(
                    similarity(name_normalized, %s),
                    CASE WHEN name_normalized LIKE %s THEN 0.9 ELSE 0 END) DESC,
                    LOWER(name)
                LIMIT 6
            """, (nq, f'%{nq}%', nq, f'%{nq}%'))
            results = [_journal_result(r) for r in cur.fetchall()]
    if not results and not warnings:
        suspect = _suspect_publisher_match(cur, q)
        if suspect:
            warnings.append({
                'type': 'not_found',
                'message': "Ushbu jurnal OAK va Scopus ro'yxatlarida topilmadi. "
                           "Ehtiyot bo'ling.",
                'suspect_match': f"{suspect[0]} — {suspect[1]}",
            })
        else:
            warnings.append({
                'type': 'not_found',
                'message': "Ro'yxatlarimizda topilmadi — OAK rasmiy ro'yxatidan "
                           "tekshirishni tavsiya qilamiz.",
            })
    return results, warnings


def _suspect_publisher_match(cur, q):
    """Substring match (either direction) between the query and the suspect
    publisher list. Returns (publisher, reason) or None."""
    nq = normalize_title(q)
    if len(nq) < 4:
        return None
    cur.execute("""
        SELECT publisher, COALESCE(reason, 'shubhali deb belgilangan')
        FROM suspect_publishers
        WHERE publisher_normalized <> '' AND (
              %s LIKE '%%' || publisher_normalized || '%%'
              OR publisher_normalized LIKE %s)
        ORDER BY LENGTH(publisher_normalized) DESC LIMIT 1
    """, (nq, f'%{nq}%'))
    return cur.fetchone()


def _log_search(cur, q, result_count, had_warning):
    cur.execute("INSERT INTO journal_search_log (query, result_count, had_warning) "
                "VALUES (%s, %s, %s)", (q[:300], result_count, had_warning))
    if random.random() < 0.02:   # occasional cap enforcement, keeps table ~10K
        cur.execute("""
            DELETE FROM journal_search_log WHERE id < (
                SELECT COALESCE(MIN(id), 0) FROM (
                    SELECT id FROM journal_search_log
                    ORDER BY id DESC LIMIT %s) recent)
        """, (SEARCH_LOG_CAP,))


# ── public API ──────────────────────────────────────────────────────────────
@journal_check_bp.route('/api/journals/verify')
def api_verify():
    from app import get_real_ip
    from extensions import cache
    ip = get_real_ip()
    key = f'jv_rl:{ip}'
    n = cache.get(key) or 0
    if n >= VERIFY_LIMIT_PER_MIN:
        return jsonify({'error': "So'rovlar ko'payib ketdi. Iltimos, biroz kuting."}), 429
    cache.set(key, n + 1, timeout=60)

    q = (request.args.get('q') or '').strip()[:300]
    if len(q) < 2:
        return jsonify({'results': [], 'warnings': [], 'query': q})
    from data import get_connection
    results, warnings = [], []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                results, warnings = verify_query(cur, q)
                had_warning = bool(warnings) or any(
                    r['status'] == 'suspect' for r in results)
                _log_search(cur, q, len(results), had_warning)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        return jsonify({'results': [], 'warnings': [], 'query': q})
    return jsonify({'results': results, 'warnings': warnings, 'query': q})


# ── standalone checker page ─────────────────────────────────────────────────
@journal_check_bp.route('/jurnal-tekshirish')
def checker_page():
    return render_template('jurnal_tekshirish.html')
