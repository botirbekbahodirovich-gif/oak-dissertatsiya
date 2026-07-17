"""Tasdiqlangan universitet birlashtirishlarini ADMIN MEXANIZMI orqali qo'llash.

/admin/institutions/rename endpoint'ining aynan o'zi, faqat CSV bo'yicha bulk:
har bir qator uchun institution_map'dagi eski guruh variantlari yangi kanonik
nomga ko'chiriladi VA institution_renames jadvaliga tarix yozuvi qo'shiladi —
shu sabab har bir birlashtirish /admin/institutions sahifasida ko'rinadi va
o'sha yerdan bir bosishda BEKOR QILINADI (undo).

MUHIM: migrate_institutions.py --apply ISHLATILMASIN — u jadvalni DROP qilib
qayta quradi va admin qilgan barcha birlashtirishlarni yo'qotadi.

dissertations jadvaliga TEGILMAYDI (non-destructive) — faqat institution_map
guruhlanishi o'zgaradi. /universities katalogi 30 daqiqalik kesh yangilangach
(yoki servis restart bo'lgach) yangi holatni ko'rsatadi.

Idempotent: qayta ishga tushirilsa, allaqachon birlashgan guruhlar "topilmadi"
deb o'tkazib yuboriladi.

Usage (server, repo root; .env dagi DATABASE_URL bilan):
    python3 scripts/apply_institution_merges.py scripts/institution_merge_plan.csv           # dry-run
    python3 scripts/apply_institution_merges.py scripts/institution_merge_plan.csv --apply   # qo'llash
"""
import argparse
import csv
import os
import sys

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import Json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from institutions import transliterate

load_dotenv()

ADMIN_LABEL = 'fable5-auto'     # tarixda kim qilgani ko'rinadi


def read_plan(path):
    rows = []
    with open(path, newline='', encoding='utf-8-sig') as fh:
        reader = csv.DictReader(fh)
        need = {'old_name', 'new_name'}
        if not need.issubset(set(reader.fieldnames or [])):
            raise SystemExit('CSV ustunlari mos emas — kerak: old_name,new_name')
        for r in reader:
            old = (r['old_name'] or '').strip()
            new = (r['new_name'] or '').strip()
            if old and new and old != new:
                rows.append((old, new))
    return rows


def group_exists(cur, canonical):
    cur.execute("SELECT 1 FROM institution_map "
                "WHERE COALESCE(canonical_name, cyrillic_name) = %s "
                "AND is_active = TRUE LIMIT 1", (canonical,))
    return cur.fetchone() is not None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('csv_path')
    ap.add_argument('--apply', action='store_true',
                    help="haqiqatda yozish (standart: dry-run, DB'ga tegilmaydi)")
    args = ap.parse_args()

    url = os.environ.get('DATABASE_URL')
    if not url:
        print('DATABASE_URL is not set.'); sys.exit(1)

    plan = read_plan(args.csv_path)
    print(f'{args.csv_path}: {len(plan)} birlashtirish o\'qildi.'
          + ('' if args.apply else '  [DRY-RUN]'))

    conn = psycopg2.connect(url)
    conn.autocommit = False
    applied = skipped = variants_moved = 0
    try:
        for old, new in plan:
            try:
                with conn.cursor() as cur:
                    # Eski guruh variantlarini qulflab olamiz (admin endpoint kabi).
                    cur.execute(
                        "SELECT cyrillic_name FROM institution_map "
                        "WHERE COALESCE(canonical_name, cyrillic_name) = %s "
                        "AND is_active = TRUE FOR UPDATE", (old,))
                    moved = [r[0] for r in cur.fetchall()]
                    if not moved:
                        skipped += 1
                        conn.rollback()
                        continue
                    was_merge = group_exists(cur, new)
                    cur.execute(
                        "UPDATE institution_map "
                        "SET canonical_name = %s, latin_name = %s "
                        "WHERE cyrillic_name = ANY(%s)",
                        (new, transliterate(new), moved))
                    cur.execute(
                        "INSERT INTO institution_renames "
                        "(old_name, new_name, was_merge, moved_variants, admin_username) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (old, new, was_merge, Json(moved), ADMIN_LABEL))
                if args.apply:
                    conn.commit()
                    print(f'  ✓ "{old[:58]}" → "{new[:58]}"  ({len(moved)} variant)')
                else:
                    conn.rollback()
                    print(f'  DRY "{old[:58]}" → "{new[:58]}"  ({len(moved)} variant)')
                applied += 1
                variants_moved += len(moved)
            except Exception as e:
                conn.rollback()
                skipped += 1
                print(f'  ✗ "{old[:58]}" — XATO: {e}')
    finally:
        conn.close()

    verb = 'birlashtirildi' if args.apply else "birlashardi (DRY-RUN — hech narsa yozilmadi)"
    print(f'\nYakun: {applied} guruh {verb}, {skipped} o\'tkazildi, '
          f'{variants_moved} variant ko\'chdi.')
    if args.apply:
        print('Tarix: /admin/institutions → "Oxirgi o\'zgarishlar" (undo shu yerda).')
        print('Katalog 30 daqiqalik kesh yangilangach o\'zgaradi '
              '(tezlashtirish: sudo systemctl restart olimlar).')


if __name__ == '__main__':
    main()
