"""Mahalliy konferensiyalar — vazirlik yillik rejasidan import.

Manba: Oliy ta'lim, fan va innovatsiyalar vazirligining yillik konferensiyalar
rejasi. Vazirlik rejani odatda PDF jadval ko'rinishida e'lon qiladi va URL har
yili o'zgaradi — shuning uchun bu skript manbani AVTOMATIK TOPMAYDI (mavjud
bo'lmagan URLni qattiq kodlash yolg'on ishonch beradi); admin har yilgi PDF/CSV
manzilini beradi:

    python3 scripts/scrape_conferences_local.py --pdf plan_2026.pdf --year 2026
    python3 scripts/scrape_conferences_local.py --pdf https://.../plan.pdf --year 2026
    python3 scripts/scrape_conferences_local.py --csv plan_2026.csv --year 2026
    ... --dry-run                      # DB'ga yozmaydi, faqat tahlil natijasi

PDF rejimi pdfplumber bilan jadval qatorlarini oladi (ustunlar: nomi,
tashkilotchi, soha, mintaqa/shahar, sana(lar)); ustun tartibi --cols bilan
sozlanadi (default: 1-nomi 2-tashkilotchi 3-soha 4-joy 5-sana, 0-indeksli).
CSV rejimi sarlavhali fayl kutadi: title,organizer,field,region,city,
event_type,start_date,end_date.

Defensiv naqsh: dedup source_id (local:<yil>:<slug>) UNIQUE upsert; qator
darajasida try/except (bitta buzuq qator runni to'xtatmaydi); hech narsa
o'chirilmaydi. Mintaqa nomlari saytning kanonik ro'yxatiga normalizatsiya
qilinadi (migrate_institutions._REGION_RULES bilan bir xil kalitlar).
"""
import argparse
import csv
import io
import os
import re
import sys
from datetime import date, datetime

from dotenv import load_dotenv

load_dotenv()

# Kanonik mintaqalar (migrate_institutions.py bilan sinxron kalit so'zlar).
REGION_RULES = [
    (('buxoro', 'бухоро'), 'Buxoro'),
    (('andijon', 'андижон'), 'Andijon'),
    (('farg', 'qo\'qon', 'quqon', 'фарғ', 'қўқон'), "Farg'ona"),
    (('samarqand', 'самарқанд'), 'Samarqand'),
    (('namangan', 'наманган'), 'Namangan'),
    (('nukus', 'qoraqalpog', 'нукус', 'қорақалпоғ'), "Qoraqalpog'iston"),
    (('termiz', 'surxon', 'термиз', 'сурхон'), 'Surxondaryo'),
    (('qarshi', 'qashqadaryo', 'қарши', 'қашқадарё'), 'Qashqadaryo'),
    (('jizzax', 'жиззах'), 'Jizzax'),
    (('navoiy', 'навоий'), 'Navoiy'),
    (('urganch', 'xorazm', 'урганч', 'хоразм'), 'Xorazm'),
    (('guliston', 'sirdaryo', 'гулистон', 'сирдарё'), 'Sirdaryo'),
    (('chirchiq', 'toshkent', 'чирчиқ', 'тошкент'), 'Toshkent'),
]
EVENT_TYPE_KEYS = [
    ('forum', 'Forum'), ('kongress', 'Kongress'), ('конгресс', 'Kongress'),
    ('seminar', 'Seminar'), ('семинар', 'Seminar'),
    ('simpozium', 'Simpozium'), ('симпозиум', 'Simpozium'),
    ('anjuman', 'Anjuman'), ('анжуман', 'Anjuman'),
]
_slug_re = re.compile(r'[^a-z0-9]+')
# '12.05.2026', '2026-05-12', '12-13 may 2026' kabi variantlar uchun
_DMY = re.compile(r'(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})')
_ISO = re.compile(r'(\d{4})-(\d{2})-(\d{2})')
_MONTHS = {'yanvar': 1, 'fevral': 2, 'mart': 3, 'aprel': 4, 'may': 5,
           'iyun': 6, 'iyul': 7, 'avgust': 8, 'sentabr': 9, 'sentyabr': 9,
           'oktabr': 10, 'oktyabr': 10, 'noyabr': 11, 'dekabr': 12,
           'январ': 1, 'феврал': 2, 'март': 3, 'апрел': 4, 'май': 5,
           'июн': 6, 'июл': 7, 'август': 8, 'сентябр': 9, 'октябр': 10,
           'ноябр': 11, 'декабр': 12}


def log(msg):
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


def slugify_latin(s):
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from institutions import transliterate
        s = transliterate((s or '').lower())
    except Exception:
        s = (s or '').lower()
    for ch in "'ʻʼ‘’`":
        s = s.replace(ch, '')
    return _slug_re.sub('-', s).strip('-')[:200]


def detect_region(text):
    low = (text or '').lower()
    for keys, region in REGION_RULES:
        if any(k in low for k in keys):
            return region
    return None


def detect_event_type(title):
    low = (title or '').lower()
    for key, label in EVENT_TYPE_KEYS:
        if key in low:
            return label
    return 'Konferensiya'


def parse_dates(text, default_year):
    """'12.05.2026', '12-13.05.2026', '2026-05-12', '12-13 may' → (start, end)."""
    t = (text or '').strip().lower()
    if not t:
        return None, None
    iso = _ISO.findall(t)
    if iso:
        ds = [date(int(y), int(m), int(d)) for y, m, d in iso]
        return ds[0], (ds[1] if len(ds) > 1 else None)
    dmy = _DMY.findall(t)
    if dmy:
        ds = []
        for d, m, y in dmy:
            try:
                ds.append(date(int(y), int(m), int(d)))
            except ValueError:
                pass
        if ds:
            # '12-13.05.2026' — birinchi raqam kun bo'lishi mumkin
            m_range = re.match(r'^(\d{1,2})\s*[-–]\s*\d{1,2}[.\-/]', t)
            start = ds[0]
            if m_range:
                try:
                    start = start.replace(day=int(m_range.group(1)))
                except ValueError:
                    pass
            return start, (ds[-1] if ds[-1] != start else None)
    # '12-13 may' / '12 may'
    for name, mnum in _MONTHS.items():
        if name in t:
            days = re.findall(r'\d{1,2}', t.split(name)[0])
            try:
                if len(days) >= 2:
                    return (date(default_year, mnum, int(days[0])),
                            date(default_year, mnum, int(days[1])))
                if days:
                    return date(default_year, mnum, int(days[0])), None
            except ValueError:
                return None, None
    return None, None


def rows_from_pdf(src, cols):
    """PDF jadvallaridan xom qatorlar. cols — ustun indekslari:
    title,organizer,field,place,dates."""
    import pdfplumber
    if src.startswith('http'):
        import requests
        r = requests.get(src, timeout=60)
        r.raise_for_status()
        fh = io.BytesIO(r.content)
    else:
        fh = open(src, 'rb')
    out = []
    with pdfplumber.open(fh) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                for raw in table:
                    if not raw or len(raw) <= max(cols):
                        continue
                    cells = [(c or '').replace('\n', ' ').strip() for c in raw]
                    title = cells[cols[0]]
                    if len(title) < 10 or title.lower().startswith(
                            ('№', 't/r', 'nomi', 'наименование')):
                        continue  # sarlavha/raqam qatorlari
                    out.append({'title': title,
                                'organizer': cells[cols[1]],
                                'field': cells[cols[2]],
                                'place': cells[cols[3]],
                                'dates': cells[cols[4]]})
    fh.close()
    return out


def rows_from_csv(src):
    with open(src, newline='', encoding='utf-8-sig') as fh:
        return list(csv.DictReader(fh))


UPSERT = """
INSERT INTO conferences
    (title, title_slug, scope, organizer, field, region, city, event_type,
     start_date, end_date, is_multiday, format, source_url, source_id)
VALUES (%(title)s, %(title_slug)s, 'local', %(organizer)s, %(field)s,
        %(region)s, %(city)s, %(event_type)s, %(start_date)s, %(end_date)s,
        %(is_multiday)s, 'onsite', %(source_url)s, %(source_id)s)
ON CONFLICT (source_id) DO UPDATE SET
    organizer = EXCLUDED.organizer,
    field = EXCLUDED.field,
    region = EXCLUDED.region,
    city = EXCLUDED.city,
    start_date = EXCLUDED.start_date,
    end_date = EXCLUDED.end_date,
    is_multiday = EXCLUDED.is_multiday,
    updated_at = NOW()
RETURNING (xmax = 0) AS inserted
"""


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument('--pdf', help='vazirlik rejasi PDF (fayl yoki URL)')
    src.add_argument('--csv', help='tayyor CSV (title,organizer,field,region,'
                                   'city,event_type,start_date,end_date)')
    ap.add_argument('--year', type=int, default=date.today().year,
                    help='reja yili (source_id va sanasiz qatorlar uchun)')
    ap.add_argument('--cols', default='1,2,3,4,5',
                    help='PDF ustun indekslari: title,organizer,field,place,dates')
    ap.add_argument('--source-url', default='',
                    help='asl reja havolasi (kartada "manba" sifatida chiqadi)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if args.pdf:
        cols = [int(x) for x in args.cols.split(',')]
        raw = rows_from_pdf(args.pdf, cols)
        rows = []
        for r in raw:
            start, end = parse_dates(r['dates'], args.year)
            rows.append({
                'title': r['title'][:600],
                'organizer': (r['organizer'] or '')[:400] or None,
                'field': (r['field'] or '')[:200] or None,
                'region': detect_region(r['place'] + ' ' + r['organizer']),
                'city': (r['place'] or '')[:150] or None,
                'event_type': detect_event_type(r['title']),
                'start_date': start, 'end_date': end,
            })
    else:
        rows = []
        for r in rows_from_csv(args.csv):
            def g(k):
                return (r.get(k) or '').strip() or None
            start, end = g('start_date'), g('end_date')
            rows.append({
                'title': (g('title') or '')[:600],
                'organizer': g('organizer') and g('organizer')[:400],
                'field': g('field') and g('field')[:200],
                'region': g('region') or detect_region(g('city') or ''),
                'city': g('city') and g('city')[:150],
                'event_type': g('event_type') or detect_event_type(g('title')),
                'start_date': start and date.fromisoformat(start[:10]),
                'end_date': end and date.fromisoformat(end[:10]),
            })
    rows = [r for r in rows if r['title']]
    log(f'{len(rows)} ta qator tayyor (yil: {args.year})')

    if args.dry_run:
        for r in rows[:15]:
            log(f"  {r['start_date']} | {r['region'] or '—':<16} | {r['title'][:70]}")
        log('DRY RUN — DB ga yozilmadi.')
        return

    url = os.environ.get('DATABASE_URL')
    if not url:
        print('DATABASE_URL is not set.'); sys.exit(1)
    import psycopg2
    conn = psycopg2.connect(url)
    inserted = updated = failed = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT title_slug FROM conferences "
                        "WHERE title_slug IS NOT NULL")
            taken = {r[0] for r in cur.fetchall()}
        for r in rows:
            r['is_multiday'] = bool(r['start_date'] and r['end_date']
                                    and r['end_date'] != r['start_date'])
            r['source_url'] = args.source_url[:500] or None
            r['source_id'] = f"local:{args.year}:{slugify_latin(r['title'])}"[:300]
            base = slugify_latin(r['title']) or 'konf'
            s, n = base, 2
            while s in taken:
                s, n = f'{base}-{n}', n + 1
            taken.add(s)
            r['title_slug'] = s[:250]
            try:
                with conn.cursor() as cur:
                    cur.execute(UPSERT, r)
                    if cur.fetchone()[0]:
                        inserted += 1
                    else:
                        updated += 1
                        taken.discard(r['title_slug'])
                conn.commit()
            except Exception as e:
                conn.rollback()
                failed += 1
                log(f"  !! '{r['title'][:60]}': {e}")
    finally:
        conn.close()
    log(f'YAKUN: yangi={inserted} yangilandi={updated} xato={failed}')


if __name__ == '__main__':
    main()
