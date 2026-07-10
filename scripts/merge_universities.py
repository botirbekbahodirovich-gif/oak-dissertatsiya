"""Universitet nomlaridagi duplikatlarni topish — deterministik + AI (Fable 5).

DB'GA HECH NARSA YOZMAYDI — natijalar faqat output/ dagi CSV fayllarga tushadi;
ularni qo'lda ko'rib chiqib, keyin scripts/apply_merges.py bilan qo'llanadi.

Schema-aware: at startup the script inspects the live schema.
  - FK mode:   dissertations has a foreign-key column referencing
               universities(id) → rows come from the universities table.
  - Text mode: (the current olimlar.uz schema) dissertations.muassasa is a raw
               TEXT value with no FK → the distinct TRIM(muassasa) values ARE
               the university names; "id" is a synthetic per-run number and
               apply_merges.py matches by NAME, not id.

Pipeline:
  1. deterministic normalization (no LLM) → output/deterministic_merge.csv
  2. remaining unique names → claude-fable-5 in batches of 40 →
     output/auto_merge.csv, output/manual_review.csv, output/keep_separate.csv
     (unparseable batches → output/manual_review_failed.csv)
  3. console stats + output/merge_meta.json (mode marker for apply_merges.py)

Usage (WSL, repo root):
    python3 scripts/merge_universities.py [--batch-size 40] [--skip-ai]
"""
import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
import psycopg2

load_dotenv()

MODEL = 'claude-fable-5'
FALLBACK_MODEL = 'claude-opus-4-8'
MAX_TOKENS = 8000
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'output')

SYSTEM_PROMPT = """Sen O'zbekiston oliy ta'lim tizimi bo'yicha ekspertsan. Universitet va
ilmiy muassasalar nomlarini yaxshi bilasan (kirill, lotin, tarixiy nomlar).

VAZIFANG: Berilgan universitet nomlari ro'yxatidan duplikatlarni top va
har bir guruhga uchta statusdan bittasini ber:
1. AUTO_MERGE — 100% ishonch bilan bir xil universitet
2. MANUAL_REVIEW — har qanday shubha bo'lsa
3. KEEP_SEPARATE — alohida qolishi shart

ENG MUHIM QOIDA: Agar 100% ishonching bo'lmasa — HAR DOIM MANUAL_REVIEW.
"Ehtimol", "o'xshaydi" — bularning hammasi MANUAL_REVIEW.
Xato birlashtirish xato ajratishdan 10 marta yomon.

KEEP_SEPARATE (majburiy):
- Nomda "ва", "va", "ҳамда", "hamda" bilan IKKI mustaqil universitet
  bog'langan bo'lsa
- "филиали"/"filiali" bo'lsa va asosiy universitetdan farqli bo'lsa
- Fanlar akademiyasi bo'limlarida yo'nalish farq qilsa
  (гуманитар фанлар ≠ табиий фанлар)

AUTO_MERGE (faqat shu holatlar):
- Faqat tinish belgi farqi: «X» ≡ X ≡ "X"
- Faqat bo'shliq farqi: "АБ" ≡ "А Б"
- Aniq imlo variantlari: Қорақалпоқ≡Қарақалпоқ≡Қорақалпок,
  Бердақ≡Бердах, Ажиниёз≡Ажинияз, Тошкент≡Тошкенд
- "...да бажарилган" qo'shimchasi asosiy nomga birlashadi
- Katta/kichik harf farqi

MANUAL_REVIEW (shart):
- Qisqartma vs to'liq nom (МДУ vs Мирзо Улуғбек номидаги...)
- "номидаги X" qismi bor/yo'q (Бердақ номидаги ҚДУ vs ҚДУ —
  bular bir xil BO'LISHI MUMKIN lekin sen hal qilma)
- Институт vs Университет farqi
- "Миллий" so'zi bor/yo'q
- Tarixiy nom o'zgarishi ehtimoli
- Umuman har qanday shubha

CHIQISH: Faqat toza JSON, boshqa hech narsa yozma, markdown backtick ham yo'q:
{
  "auto_merge_groups": [
    {"canonical_id": 1, "canonical_name": "...", "merge_ids": [5,8], "reason": "..."}
  ],
  "manual_review_groups": [
    {"candidate_ids": [3,47], "reason": "...", "confidence": 0.7}
  ],
  "keep_separate": [
    {"id": 2, "reason": "..."}
  ]
}

canonical_name sifatida eng to'liq va to'g'ri imloli variantni tanla.
canonical_id sifatida eng ko'p dissertatsiyaga ega ID ni tanla
(diss_count berilgan)."""


# ── 1. Schema detection ──────────────────────────────────────────────────────

def detect_mode(cur):
    """Return ('fk', fk_column) if dissertations references universities(id),
    else ('text', 'muassasa')."""
    cur.execute("""
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
             ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage ccu
             ON tc.constraint_name = ccu.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_name = 'dissertations'
          AND ccu.table_name = 'universities'
    """)
    row = cur.fetchone()
    if row:
        return 'fk', row[0]
    # undeclared but conventionally-named FK column
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'dissertations'
          AND column_name IN ('university_id', 'universitet_id')
    """)
    row = cur.fetchone()
    if row:
        return 'fk', row[0]
    return 'text', 'muassasa'


def load_rows(cur, mode, fk_col):
    """[(id, name, diss_count)] — id is synthetic (1..N) in text mode."""
    if mode == 'fk':
        cur.execute(f"""
            SELECT u.id, u.name, COUNT(d.id) AS diss_count
            FROM universities u
            LEFT JOIN dissertations d ON d.{fk_col} = u.id
            GROUP BY u.id, u.name
            ORDER BY u.name
        """)
        return [(r[0], (r[1] or '').strip(), r[2]) for r in cur.fetchall()]
    cur.execute("""
        SELECT TRIM(muassasa) AS name, COUNT(*) AS diss_count
        FROM dissertations
        WHERE muassasa IS NOT NULL AND TRIM(muassasa) <> ''
        GROUP BY TRIM(muassasa)
        ORDER BY 1
    """)
    return [(i + 1, r[0], r[1]) for i, r in enumerate(cur.fetchall())]


# ── 2. Deterministic normalization (LLM'siz) ─────────────────────────────────

_PUNCT = '«»“”„"\'`´ʼ‘’.,;:—–‐­!?()[]{}/\\|№'
_SPELLING_SUBS = [        # Cyrillic imlo variantlari → yagona shakl
    ('қарақалпоқ', 'қорақалпоқ'),
    ('қорақалпок', 'қорақалпоқ'),
    ('қарақалпок', 'қорақалпоқ'),
    ('бердах', 'бердақ'),
    ('ажинияз', 'ажиниёз'),
    ('узбекистон', 'ўзбекистон'),
]
_BAJARILGAN = ('бажарилган', 'bajarilgan')


def normalized_key(name):
    s = (name or '').lower()
    s = s.replace('-', ' ')
    for ch in _PUNCT:
        s = s.replace(ch, ' ')
    s = ' '.join(s.split())
    # "…(университети)да бажарилган" → drop the word + the locative -да suffix
    words = s.split()
    if words and words[-1] in _BAJARILGAN:
        words = words[:-1]
        if words and len(words[-1]) > 4 and words[-1].endswith(('да', 'da')):
            words[-1] = words[-1][:-2]
    s = ' '.join(words)
    for a, b in _SPELLING_SUBS:
        s = s.replace(a, b)
    return ' '.join(s.split())


def deterministic_groups(rows):
    """rows → (groups, survivors). groups: list of dicts for CSV; survivors:
    the deduped [(id, name, diss_count)] list that goes to the AI phase
    (group canonicals + all singletons)."""
    by_key = {}
    for rid, name, cnt in rows:
        by_key.setdefault(normalized_key(name), []).append((rid, name, cnt))

    groups, survivors = [], []
    for key, members in by_key.items():
        if len(members) == 1:
            survivors.append(members[0])
            continue
        # canonical = most dissertations, tie-break: fullest (longest) name
        canonical = max(members, key=lambda m: (m[2], len(m[1])))
        merged = [m for m in members if m[0] != canonical[0]]
        groups.append({
            'normalized_key': key,
            'canonical_id': canonical[0],
            'canonical_name': canonical[1],
            'merge_ids': [m[0] for m in merged],
            'merge_names': [m[1] for m in merged],
            'names': [m[1] for m in members],
        })
        # survivor keeps the whole group's dissertation weight for the AI phase
        survivors.append((canonical[0], canonical[1], sum(m[2] for m in members)))
    survivors.sort(key=lambda m: m[1])
    return groups, survivors


# ── 3. AI phase (claude-fable-5) ─────────────────────────────────────────────

def build_client():
    key = os.environ.get('ANTHROPIC_API_KEY')
    if not key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=key)


def call_fable(client, batch, max_tokens=MAX_TOKENS):
    """One batch → raw response text. Raises on refusal/APIError."""
    payload = [{'id': rid, 'name': name, 'diss_count': cnt}
               for rid, name, cnt in batch]
    user_msg = ('Quyidagi universitet nomlari ro\'yxatini tahlil qil '
                '(id, name, diss_count):\n'
                + json.dumps(payload, ensure_ascii=False, indent=1))
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        betas=['server-side-fallback-2026-06-01'],
        fallbacks=[{'model': FALLBACK_MODEL}],
        system=SYSTEM_PROMPT,
        messages=[{'role': 'user', 'content': user_msg}],
    )
    if resp.stop_reason == 'refusal':
        raise RuntimeError('model refused the request (stop_reason=refusal)')
    if resp.stop_reason == 'max_tokens':
        raise RuntimeError('truncated (stop_reason=max_tokens)')
    return ''.join(b.text for b in resp.content if b.type == 'text')


def parse_response(text):
    """JSON.parse with a ```json fence-stripping second attempt."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    cleaned = re.sub(r'^\s*```(?:json)?\s*|\s*```\s*$', '', text.strip())
    m = re.search(r'\{.*\}', cleaned, re.DOTALL)
    return json.loads(m.group(0) if m else cleaned)


def run_ai_phase(client, survivors, batch_size):
    """→ (auto_merge, manual_review, keep_separate, failed_batches)."""
    by_id = {rid: (name, cnt) for rid, name, cnt in survivors}
    auto_merge, manual_review, keep_separate, failed = [], [], [], []

    batches = [survivors[i:i + batch_size]
               for i in range(0, len(survivors), batch_size)]
    for bi, batch in enumerate(batches, 1):
        print(f'  batch {bi}/{len(batches)} ({len(batch)} nom)...',
              end=' ', flush=True)
        try:
            try:
                text = call_fable(client, batch)
            except RuntimeError as e:
                if 'truncated' not in str(e):
                    raise
                text = call_fable(client, batch, max_tokens=16000)
            data = parse_response(text)
        except Exception as e:
            print(f'XATO: {e}')
            failed.append((bi, batch, str(e)))
            continue

        batch_ids = {rid for rid, _, _ in batch}

        for g in data.get('auto_merge_groups', []) or []:
            ids = [i for i in (g.get('merge_ids') or []) if i in batch_ids]
            cid = g.get('canonical_id')
            if cid not in batch_ids or not ids:
                continue        # hallucinated ids — drop the group
            # enforce the rule: canonical = the member with most dissertations
            members = ids + [cid]
            best = max(members, key=lambda i: by_id[i][1])
            if best != cid:
                ids = [i for i in members if i != best]
                cid = best
            auto_merge.append({
                'canonical_id': cid,
                'canonical_name': g.get('canonical_name') or by_id[cid][0],
                'merge_ids': ids,
                'merge_names': [by_id[i][0] for i in ids],
                'reason': g.get('reason', ''),
            })

        for g in data.get('manual_review_groups', []) or []:
            ids = [i for i in (g.get('candidate_ids') or []) if i in batch_ids]
            if len(ids) < 2:
                continue
            manual_review.append({
                'candidate_ids': ids,
                'candidate_names': [by_id[i][0] for i in ids],
                'reason': g.get('reason', ''),
                'confidence': g.get('confidence', ''),
            })

        for g in data.get('keep_separate', []) or []:
            rid = g.get('id')
            if rid in batch_ids:
                keep_separate.append({
                    'id': rid, 'name': by_id[rid][0],
                    'reason': g.get('reason', ''),
                })
        print('OK')

    return auto_merge, manual_review, keep_separate, failed


# ── 4. CSV output (UTF-8-BOM — Excel'da kirill to'g'ri ochiladi) ─────────────

def _join_ids(ids):
    return ';'.join(str(i) for i in ids)


def _join_names(names):
    return ' | '.join(names)


def write_csv(path, header, rows):
    with open(path, 'w', newline='', encoding='utf-8-sig') as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    print(f'  → {path} ({len(rows)} qator)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--batch-size', type=int, default=40)
    ap.add_argument('--skip-ai', action='store_true',
                    help='faqat deterministik faza (API chaqirmaydi)')
    args = ap.parse_args()

    url = os.environ.get('DATABASE_URL')
    if not url:
        print('DATABASE_URL is not set.'); sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            mode, fk_col = detect_mode(cur)
            print(f"Rejim: {mode}"
                  + (f" (FK ustuni: {fk_col})" if mode == 'fk'
                     else " (dissertations.muassasa — FK yo'q, nom bo'yicha)"))
            rows = load_rows(cur, mode, fk_col)
    finally:
        conn.close()
    print(f'Jami nomlar: {len(rows)}')

    # deterministic phase
    det_groups, survivors = deterministic_groups(rows)
    write_csv(
        os.path.join(OUTPUT_DIR, 'deterministic_merge.csv'),
        ['normalized_key', 'canonical_id', 'canonical_name',
         'merge_ids', 'merge_names', 'names'],
        [[g['normalized_key'], g['canonical_id'], g['canonical_name'],
          _join_ids(g['merge_ids']), _join_names(g['merge_names']),
          _join_names(g['names'])] for g in det_groups])

    meta = {
        'mode': mode,
        'fk_column': fk_col if mode == 'fk' else None,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'total_names': len(rows),
        'model': MODEL,
    }
    with open(os.path.join(OUTPUT_DIR, 'merge_meta.json'), 'w',
              encoding='utf-8') as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)

    # AI phase
    auto_merge = manual_review = keep_separate = failed = None
    if args.skip_ai:
        print('AI fazasi o\'tkazib yuborildi (--skip-ai).')
    else:
        client = build_client()
        if client is None:
            print('\n!!! ANTHROPIC_API_KEY .env da topilmadi — AI fazasi '
                  'ishga tushmadi. Kalitni .env ga qo\'shib qayta ishga '
                  'tushiring. Deterministik natijalar output/ da tayyor.')
            sys.exit(1)
        print(f'AI fazasi: {len(survivors)} nom, batch={args.batch_size}, '
              f'model={MODEL}')
        auto_merge, manual_review, keep_separate, failed = run_ai_phase(
            client, survivors, args.batch_size)

        write_csv(
            os.path.join(OUTPUT_DIR, 'auto_merge.csv'),
            ['canonical_id', 'canonical_name', 'merge_ids', 'merge_names',
             'reason'],
            [[g['canonical_id'], g['canonical_name'], _join_ids(g['merge_ids']),
              _join_names(g['merge_names']), g['reason']] for g in auto_merge])
        write_csv(
            os.path.join(OUTPUT_DIR, 'manual_review.csv'),
            ['candidate_ids', 'candidate_names', 'reason', 'confidence'],
            [[_join_ids(g['candidate_ids']), _join_names(g['candidate_names']),
              g['reason'], g['confidence']] for g in manual_review])
        write_csv(
            os.path.join(OUTPUT_DIR, 'keep_separate.csv'),
            ['id', 'name', 'reason'],
            [[g['id'], g['name'], g['reason']] for g in keep_separate])
        if failed:
            write_csv(
                os.path.join(OUTPUT_DIR, 'manual_review_failed.csv'),
                ['batch_index', 'id', 'name', 'diss_count', 'error'],
                [[bi, rid, name, cnt, err]
                 for bi, batch, err in failed
                 for rid, name, cnt in batch])

    # stats
    print('\n──── STATISTIKA ────')
    print(f'  Jami universitet nomlari        : {len(rows)}')
    print(f'  Deterministik duplikat guruhlar : {len(det_groups)} '
          f'({sum(len(g["merge_ids"]) for g in det_groups)} nom birlashadi)')
    print(f'  AI fazasiga yuborilgan nomlar   : {len(survivors)}')
    if auto_merge is not None:
        print(f'  Fable 5 AUTO_MERGE guruhlar     : {len(auto_merge)}')
        print(f'  MANUAL_REVIEW guruhlar          : {len(manual_review)}')
        print(f'  KEEP_SEPARATE                   : {len(keep_separate)}')
        if failed:
            print(f'  Muvaffaqiyatsiz batchlar        : {len(failed)}')


if __name__ == '__main__':
    main()
