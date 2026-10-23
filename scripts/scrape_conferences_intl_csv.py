"""Xalqaro konferensiyalar — CSV dan import."""
import argparse, csv, os, re, sys
from datetime import date, datetime
from dotenv import load_dotenv
load_dotenv()

_slug_re = re.compile(r'[^a-z0-9]+')

def log(msg):
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)

def slugify_latin(s):
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from institutions import transliterate
        s = transliterate((s or '').lower())
    except Exception:
        s = (s or '').lower()
    for ch in "'ʻʼ''`":
        s = s.replace(ch, '')
    return _slug_re.sub('-', s).strip('-')[:200]

def rows_from_csv(src):
    with open(src, newline='', encoding='utf-8-sig') as fh:
        return list(csv.DictReader(fh))

UPSERT = """
INSERT INTO conferences
    (title, title_slug, scope, field, city, country, event_type,
     start_date, end_date, is_multiday, format, submission_deadline,
     source_url, source_id,
     acronym, publisher, ccf_rank, core_rank, is_scopus_indexed)
VALUES
    (%(title)s, %(title_slug)s, 'international', %(field)s, %(city)s, %(country)s,
     'Conference', %(start_date)s, %(end_date)s, %(is_multiday)s, 'onsite',
     %(submission_deadline)s, %(source_url)s, %(source_id)s,
     %(acronym)s, %(publisher)s, %(ccf_rank)s, %(core_rank)s, %(is_scopus)s)
ON CONFLICT (source_id) DO UPDATE SET
    title        = EXCLUDED.title,
    field        = EXCLUDED.field,
    city         = EXCLUDED.city,
    country      = EXCLUDED.country,
    start_date   = EXCLUDED.start_date,
    end_date     = EXCLUDED.end_date,
    is_multiday  = EXCLUDED.is_multiday,
    submission_deadline = EXCLUDED.submission_deadline,
    source_url   = EXCLUDED.source_url,
    acronym      = EXCLUDED.acronym,
    publisher    = EXCLUDED.publisher,
    ccf_rank     = EXCLUDED.ccf_rank,
    core_rank    = EXCLUDED.core_rank,
    is_scopus_indexed = EXCLUDED.is_scopus_indexed,
    updated_at   = NOW()
RETURNING (xmax = 0) AS inserted
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv', required=True)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    raw = rows_from_csv(args.csv)
    rows = []
    for r in raw:
        def g(k): return (r.get(k) or '').strip() or None
        title = g('title')
        if not title: continue
        def to_date(s):
            if not s: return None
            try: return date.fromisoformat(s[:10])
            except: return None
        is_scopus = (g('is_scopus') or '').lower() in ('true','ha','yes','1')
        rows.append({
            'title':               title[:600],
            'acronym':             g('abbreviation') and g('abbreviation')[:50],
            'field':               g('field') and g('field')[:200],
            'city':                g('city') and g('city')[:150],
            'country':             g('country') and g('country')[:100],
            'publisher':           g('publisher') and g('publisher')[:100],
            'ccf_rank':            g('ccf_rating') and g('ccf_rating')[:8],
            'core_rank':           g('core_rating') and g('core_rating')[:8],
            'is_scopus':           is_scopus,
            'start_date':          to_date(g('start_date')),
            'end_date':            to_date(g('end_date')),
            'submission_deadline': to_date(g('submission_deadline')),
            'source_url':          g('source_url') and g('source_url')[:500],
            'source_id':           g('source_id') or f"manual:intl:2026:{slugify_latin(title)}"[:300],
        })

    log(f'{len(rows)} ta qator tayyor')

    if args.dry_run:
        for r in rows[:5]:
            log(f"  {r['start_date']} | {r['country']:<20} | {r['title'][:60]}")
        log('DRY RUN — DB ga yozilmadi.')
        return

    url = os.environ.get('DATABASE_URL')
    if not url: print('DATABASE_URL is not set.'); sys.exit(1)
    import psycopg2
    conn = psycopg2.connect(url)
    inserted = updated = failed = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT title_slug FROM conferences WHERE title_slug IS NOT NULL")
            taken = {row[0] for row in cur.fetchall()}
        for r in rows:
            r['is_multiday'] = bool(r['start_date'] and r['end_date'] and r['end_date'] != r['start_date'])
            base = slugify_latin(r['title']) or 'konf'
            s, n = base, 2
            while s in taken: s, n = f'{base}-{n}', n+1
            taken.add(s)
            r['title_slug'] = s[:250]
            try:
                with conn.cursor() as cur:
                    cur.execute(UPSERT, r)
                    if cur.fetchone()[0]: inserted += 1
                    else: updated += 1
                conn.commit()
            except Exception as e:
                conn.rollback(); failed += 1
                log(f"  !! '{r['title'][:60]}': {e}")
    finally:
        conn.close()
    log(f'YAKUN: yangi={inserted} yangilandi={updated} xato={failed}')

if __name__ == '__main__':
    main()
