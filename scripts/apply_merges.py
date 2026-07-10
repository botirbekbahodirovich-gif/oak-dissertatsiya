"""CSV'dagi tasdiqlangan birlashtirishlarni DB'ga qo'llash.

merge_universities.py chiqargan CSV'ni (auto_merge.csv yoki
deterministic_merge.csv) o'qiydi va har bir guruhni ALOHIDA TRANSACTION ichida
qo'llaydi (xato bo'lsa o'sha guruh rollback bo'ladi, qolganlari davom etadi).

Rejimlar (output/merge_meta.json dan, DB'da qayta tekshiriladi):
  - text mode (joriy olimlar.uz schema): dissertations.muassasa matni
    kanonik nomga yangilanadi; institution_map ham moslashtiriladi.
    ID'lar sintetik — moslashtirish NOM bo'yicha.
  - fk mode: dissertatsiyalar (va universities'ga FK bilan bog'liq boshqa
    jadvallar, mas. university_images) canonical_id ga ko'chiriladi, duplikat
    universities qatorlari o'chiriladi, kanonik nom yangilanadi.

Usage (WSL, repo root):
    python3 scripts/apply_merges.py output/auto_merge.csv --dry-run   # sinov
    python3 scripts/apply_merges.py output/auto_merge.csv             # qo'llash

Bajarilgan har bir amal output/applied_log.csv ga yoziladi.
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
import psycopg2

load_dotenv()

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'output')
META_PATH = os.path.join(OUTPUT_DIR, 'merge_meta.json')
LOG_PATH = os.path.join(OUTPUT_DIR, 'applied_log.csv')


def read_groups(csv_path):
    """CSV → [{canonical_id, canonical_name, merge_ids, merge_names}].
    auto_merge.csv va deterministic_merge.csv formatlarini tushunadi."""
    groups = []
    with open(csv_path, newline='', encoding='utf-8-sig') as fh:
        reader = csv.DictReader(fh)
        cols = set(reader.fieldnames or [])
        need = {'canonical_id', 'canonical_name', 'merge_ids'}
        if not need.issubset(cols):
            raise SystemExit(
                f'CSV ustunlari mos emas: {sorted(cols)} — kerak: {sorted(need)}')
        name_col = 'merge_names' if 'merge_names' in cols else 'names'
        for row in reader:
            names = [n.strip() for n in (row.get(name_col) or '').split('|')
                     if n.strip()]
            canonical = (row['canonical_name'] or '').strip()
            groups.append({
                'canonical_id': int(row['canonical_id']),
                'canonical_name': canonical,
                'merge_ids': [int(i) for i in row['merge_ids'].split(';') if i],
                # never rewrite the canonical variant onto itself
                'merge_names': [n for n in names if n != canonical],
            })
    return groups


def detect_mode(cur):
    """Mirror of merge_universities.detect_mode (kept dependency-free)."""
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
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'dissertations'
          AND column_name IN ('university_id', 'universitet_id')
    """)
    row = cur.fetchone()
    if row:
        return 'fk', row[0]
    return 'text', 'muassasa'


def referencing_tables(cur):
    """All (table, column) with a declared FK → universities(id)."""
    cur.execute("""
        SELECT tc.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
             ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage ccu
             ON tc.constraint_name = ccu.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND ccu.table_name = 'universities'
          AND ccu.column_name = 'id'
    """)
    return sorted(set(cur.fetchall()))


def table_exists(cur, name):
    cur.execute("SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name=%s", (name,))
    return cur.fetchone() is not None


def backups_exist(cur):
    cur.execute("SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' "
                "AND table_name LIKE 'dissertations\\_backup\\_%' LIMIT 1")
    return cur.fetchone() is not None


class Logger:
    def __init__(self, path, enabled):
        self.enabled = enabled
        if not enabled:
            return
        new = not os.path.exists(path)
        self.fh = open(path, 'a', newline='', encoding='utf-8-sig')
        self.w = csv.writer(self.fh)
        if new:
            self.w.writerow(['timestamp', 'mode', 'table', 'action',
                             'canonical', 'merged', 'rows_affected'])

    def log(self, mode, table, action, canonical, merged, n):
        if self.enabled:
            self.w.writerow([datetime.now(timezone.utc).isoformat(), mode,
                             table, action, canonical, merged, n])

    def close(self):
        if self.enabled:
            self.fh.close()


def apply_text_group(cur, group, has_map, logger):
    """Rename every merge variant of dissertations.muassasa to the canonical
    name; keep institution_map rows pointing at the new canonical. Returns
    total affected row count."""
    canonical = group['canonical_name']
    total = 0
    for variant in group['merge_names']:
        cur.execute("UPDATE dissertations SET muassasa = %s "
                    "WHERE TRIM(muassasa) = %s", (canonical, variant))
        n = cur.rowcount
        total += n
        logger.log('text', 'dissertations', 'rename_muassasa',
                   canonical, variant, n)
        if has_map:
            cur.execute("UPDATE institution_map SET canonical_name = %s "
                        "WHERE cyrillic_name = %s OR canonical_name = %s",
                        (canonical, variant, variant))
            logger.log('text', 'institution_map', 'repoint_canonical',
                       canonical, variant, cur.rowcount)
    return total


def apply_fk_group(cur, group, fk_col, refs, logger):
    """Repoint every referencing row to canonical_id, drop the duplicate
    universities rows, then update the canonical name."""
    cid = group['canonical_id']
    canonical = group['canonical_name']
    total = 0
    # dissertations FK may be undeclared → make sure it is in the list
    targets = sorted(set(refs) | {('dissertations', fk_col)})
    for mid in group['merge_ids']:
        for table, col in targets:
            cur.execute(f"UPDATE {table} SET {col} = %s WHERE {col} = %s",
                        (cid, mid))
            n = cur.rowcount
            total += n
            logger.log('fk', table, 'repoint_fk', cid, mid, n)
        cur.execute("DELETE FROM universities WHERE id = %s", (mid,))
        logger.log('fk', 'universities', 'delete_duplicate', cid, mid,
                   cur.rowcount)
    cur.execute("UPDATE universities SET name = %s WHERE id = %s",
                (canonical, cid))
    logger.log('fk', 'universities', 'set_canonical_name', canonical, cid,
               cur.rowcount)
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('csv_path', help='output/auto_merge.csv yoki '
                                     'output/deterministic_merge.csv')
    ap.add_argument('--dry-run', action='store_true',
                    help='faqat nima qilinishini chiqaradi, DB ga tegmaydi')
    ap.add_argument('--force', action='store_true',
                    help='backup jadvali topilmasa ham davom etish')
    args = ap.parse_args()

    url = os.environ.get('DATABASE_URL')
    if not url:
        print('DATABASE_URL is not set.'); sys.exit(1)

    groups = read_groups(args.csv_path)
    if not groups:
        print('CSV bo\'sh — qo\'llanadigan guruh yo\'q.'); return
    print(f'{args.csv_path}: {len(groups)} guruh o\'qildi.')

    meta_mode = None
    if os.path.exists(META_PATH):
        with open(META_PATH, encoding='utf-8') as fh:
            meta_mode = json.load(fh).get('mode')

    conn = psycopg2.connect(url)
    conn.autocommit = False
    logger = Logger(LOG_PATH, enabled=not args.dry_run)
    applied = skipped = affected = 0
    try:
        with conn.cursor() as cur:
            mode, fk_col = detect_mode(cur)
            if meta_mode and meta_mode != mode:
                print(f'!!! merge_meta.json rejimi ({meta_mode}) DB rejimiga '
                      f'({mode}) mos emas — CSV eskirgan bo\'lishi mumkin. '
                      'merge_universities.py ni qayta ishga tushiring.')
                sys.exit(1)
            print(f'Rejim: {mode}' + (f' (FK: {fk_col})' if mode == 'fk' else
                                      ' (muassasa nomi bo\'yicha)'))

            if not args.dry_run and not backups_exist(cur):
                if not args.force:
                    print('!!! dissertations_backup_* jadvali topilmadi. '
                          'Avval scripts/backup_tables.py ni ishga tushiring '
                          '(yoki --force bilan davom eting).')
                    sys.exit(1)
                print('Ogohlantirish: backup topilmadi, --force bilan davom.')

            has_map = mode == 'text' and table_exists(cur, 'institution_map')
            refs = referencing_tables(cur) if mode == 'fk' else []
            if refs:
                print('universities\'ga bog\'liq jadvallar: '
                      + ', '.join(f'{t}.{c}' for t, c in refs))
        conn.commit()

        for g in groups:
            label = (g['canonical_name'][:60]
                     + ('…' if len(g['canonical_name']) > 60 else ''))
            try:
                with conn.cursor() as cur:
                    if mode == 'text':
                        if not g['merge_names']:
                            print(f'  ~ "{label}" — variant nomlar yo\'q, '
                                  'o\'tkazildi')
                            skipped += 1
                            conn.rollback()
                            continue
                        n = apply_text_group(cur, g, has_map, logger)
                    else:
                        n = apply_fk_group(cur, g, fk_col, refs, logger)
                if args.dry_run:
                    conn.rollback()
                    print(f'  DRY-RUN "{label}" — {n} qator o\'zgarardi '
                          f'({len(g["merge_names"] or g["merge_ids"])} variant)')
                else:
                    conn.commit()
                    print(f'  ✓ "{label}" — {n} qator yangilandi')
                applied += 1
                affected += n
            except Exception as e:
                conn.rollback()
                skipped += 1
                print(f'  ✗ "{label}" — XATO, rollback: {e}')
    finally:
        logger.close()
        conn.close()

    verb = 'o\'zgarardi (DRY-RUN)' if args.dry_run else 'yangilandi'
    print(f'\nYakun: {applied} guruh qo\'llandi, {skipped} o\'tkazildi, '
          f'{affected} qator {verb}.')
    if not args.dry_run:
        print(f'Log: {LOG_PATH}')
        if mode == 'text':
            print('Eslatma: katalog yangilanishi uchun '
                  '"python3 migrate_institutions.py --apply" ni qayta ishga '
                  'tushiring (institution_map qayta quriladi).')


if __name__ == '__main__':
    main()
