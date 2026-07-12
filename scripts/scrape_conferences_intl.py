"""Xalqaro konferensiyalar — confs.tech ochiq datasetidan import.

Manba: github.com/tech-conferences/conference-data (MIT, jamiyat yuritadi) —
conferences/<year>/<topic>.json fayllari. O'zbekiston tadqiqot yo'nalishlariga
mos TOPICS ro'yxati import qilinadi (gaming/design kabi nomuvofiqlar emas).

Defensiv naqsh (migrate_institutions/daily_scraper kabi):
  * dedup — source_id (confstech:<topic>:<year>:<slug>) UNIQUE upsert;
  * state fayl (.conf_intl_state.json) — oxirgi muvaffaqiyatli yil/topic,
    qayta ishga tushirishda davom etadi;
  * retry + backoff har bir HTTP so'rovda; bitta topic xatosi runni to'xtatmaydi;
  * hech qachon o'chirmaydi — faqat INSERT/UPDATE (is_active admin nazoratida).

Scopus/publisher: confs.tech da BU MA'LUMOT YO'Q — is_scopus_indexed FALSE
qoladi, publisher NULL (spec: fake qilinmaydi; admin qo'lda belgilaydi).

Usage (WSL/server, repo root, .env da DATABASE_URL):
    python3 scripts/scrape_conferences_intl.py            # joriy + keyingi yil
    python3 scripts/scrape_conferences_intl.py --years 2026 2027
    python3 scripts/scrape_conferences_intl.py --dry-run  # DB'ga yozmaydi
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime

import requests
from dotenv import load_dotenv

load_dotenv()

BASE = ('https://raw.githubusercontent.com/tech-conferences/'
        'conference-data/main/conferences')
# O'zbekiston ixtisosliklariga mos keluvchi topiclar (CS/texnika/data).
TOPICS = ['general', 'data', 'python', 'java', 'javascript', 'security',
          'networking', 'devops', 'dotnet', 'golang', 'rust', 'iot',
          'php', 'ruby', 'cpp', 'mobile', 'cloud', 'ml']
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          '.conf_intl_state.json')
RETRIES = 3
BACKOFF = 4  # soniya, har urinishda x2

_slug_re = re.compile(r'[^a-z0-9]+')


def log(msg):
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


def slugify(s):
    return _slug_re.sub('-', (s or '').lower()).strip('-')[:120]


def fetch_json(url):
    """GET + retry/backoff. 404 → None (yil/topic hali yo'q — xato emas)."""
    delay = BACKOFF
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, timeout=20)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == RETRIES:
                log(f'  !! {url}: {e} — tashlab ketildi')
                return None
            log(f'  .. retry {attempt} ({e})')
            time.sleep(delay)
            delay *= 2
    return None


def parse_date(v):
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def normalize(item, topic, year):
    """confs.tech yozuvi → conferences qatori (dict)."""
    title = (item.get('name') or '').strip()
    if not title:
        return None
    start = parse_date(item.get('startDate'))
    end = parse_date(item.get('endDate'))
    online = bool(item.get('online'))
    city = (item.get('city') or '').strip() or None
    country = (item.get('country') or '').strip() or None
    fmt = 'online' if (online and not city) else ('hybrid' if online else 'onsite')
    sid = f'confstech:{topic}:{year}:{slugify(title)}'
    return {
        'title': title[:600],
        'scope': 'international',
        'field': topic,
        'city': city and city[:150],
        'country': country and country[:100],
        'event_type': 'Conference',
        'start_date': start,
        'end_date': end,
        'is_multiday': bool(start and end and end != start),
        'format': fmt,
        'submission_deadline': parse_date(item.get('cfpEndDate')),
        'source_url': (item.get('url') or '')[:500] or None,
        'source_id': sid[:300],
        'description': None,
    }


def make_slug(title, taken):
    base = slugify(title) or 'konf'
    if base[0].isdigit():
        base = 'konf-' + base
    s, n = base, 2
    while s in taken:
        s = f'{base}-{n}'
        n += 1
    taken.add(s)
    return s[:250]


UPSERT = """
INSERT INTO conferences
    (title, title_slug, scope, field, city, country, event_type,
     start_date, end_date, is_multiday, format, submission_deadline,
     source_url, source_id)
VALUES (%(title)s, %(title_slug)s, %(scope)s, %(field)s, %(city)s, %(country)s,
        %(event_type)s, %(start_date)s, %(end_date)s, %(is_multiday)s,
        %(format)s, %(submission_deadline)s, %(source_url)s, %(source_id)s)
ON CONFLICT (source_id) DO UPDATE SET
    title = EXCLUDED.title,
    start_date = EXCLUDED.start_date,
    end_date = EXCLUDED.end_date,
    is_multiday = EXCLUDED.is_multiday,
    format = EXCLUDED.format,
    city = EXCLUDED.city,
    country = EXCLUDED.country,
    submission_deadline = EXCLUDED.submission_deadline,
    source_url = EXCLUDED.source_url,
    updated_at = NOW()
RETURNING (xmax = 0) AS inserted
"""


def load_state():
    try:
        with open(STATE_FILE, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return {'done': []}


def save_state(state):
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as fh:
            json.dump(state, fh)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    this_year = date.today().year
    ap.add_argument('--years', nargs='*', type=int,
                    default=[this_year, this_year + 1])
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--fresh', action='store_true',
                    help='state faylni e’tiborsiz qoldirib hammasini qayta yurish')
    args = ap.parse_args()

    url = os.environ.get('DATABASE_URL')
    if not args.dry_run and not url:
        print('DATABASE_URL is not set.'); sys.exit(1)

    conn = None
    taken = set()
    if not args.dry_run:
        import psycopg2
        conn = psycopg2.connect(url)
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM information_schema.tables "
                        "WHERE table_name = 'conferences'")
            if not cur.fetchone():
                print('conferences jadvali topilmadi — avval saytni bir marta '
                      'oching (lazy migratsiya) yoki migrations/add_conferences.sql '
                      'ni bajaring.')
                sys.exit(1)
            cur.execute("SELECT title_slug FROM conferences "
                        "WHERE title_slug IS NOT NULL")
            taken = {r[0] for r in cur.fetchall()}

    state = {'done': []} if args.fresh else load_state()
    inserted = updated = skipped = 0
    try:
        for year in args.years:
            for topic in TOPICS:
                key = f'{year}/{topic}'
                if key in state['done']:
                    continue
                data = fetch_json(f'{BASE}/{year}/{topic}.json')
                if data is None:
                    state['done'].append(key)  # 404 — bu yil/topic yo'q
                    save_state(state)
                    continue
                batch = [normalize(i, topic, year) for i in data]
                batch = [b for b in batch if b]
                log(f'{key}: {len(batch)} ta yozuv')
                if args.dry_run:
                    skipped += len(batch)
                else:
                    with conn.cursor() as cur:
                        for row in batch:
                            row['title_slug'] = make_slug(row['title'], taken)
                            try:
                                cur.execute(UPSERT, row)
                                if cur.fetchone()[0]:
                                    inserted += 1
                                else:
                                    updated += 1
                                    taken.discard(row['title_slug'])
                            except Exception as e:
                                conn.rollback()
                                log(f"  !! '{row['title'][:60]}': {e}")
                            else:
                                conn.commit()
                state['done'].append(key)
                save_state(state)
                time.sleep(0.5)  # GitHub raw'ga muloyim
    finally:
        if conn:
            conn.close()

    # Run tugadi — keyingi run yana hammasini yangilashi uchun state tozalanadi
    # (dedup baribir source_id ustida; state faqat uzilgan runni davom ettiradi).
    save_state({'done': []})
    log(f'YAKUN: yangi={inserted} yangilandi={updated}'
        + (f' (dry-run: {skipped} ta yozuv tekshirildi)' if args.dry_run else ''))
    log("Eslatma: obunachilarga alert /api/v1/conferences/dispatch-alerts "
        "endpointi orqali yuboriladi (workflow keyingi bosqichda chaqiradi).")


if __name__ == '__main__':
    main()
