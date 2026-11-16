"""Load the matched archive photos into olim_photos (migration 023).

Input CSV columns: name_norm, file, match_type, source_file — produced by the
local matching step (exports/photo_match/, not in git). Every name_norm must
already be utils.name_norm.strict_key() form and every file must exist in
--photos-dir (static/olim_photos/), so a row never points at a missing image.

  python3 scripts/load_olim_photos.py --csv olim_photos.csv                # dry run
  python3 scripts/load_olim_photos.py --csv olim_photos.csv --apply --expect-db olimlar_staging_2610

Existing rows with the same name_norm are replaced (re-running is safe).
"""
import argparse
import csv
import os
import sys

import psycopg2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from utils.name_norm import strict_key  # noqa: E402

MATCH_TYPES = {'aniq', 'normal', 'fuzzy'}


def read_rows(path, photos_dir):
    rows, problems = [], []
    seen = set()
    with open(path, encoding='utf-8-sig', newline='') as fh:
        for i, r in enumerate(csv.DictReader(fh), start=2):
            key, f, mt = r['name_norm'], r['file'], r['match_type']
            if strict_key(key) != key:
                problems.append(f'{i}: name_norm not normalised: {key!r}')
            elif key in seen:
                problems.append(f'{i}: duplicate name_norm: {key!r}')
            elif mt not in MATCH_TYPES:
                problems.append(f'{i}: bad match_type: {mt!r}')
            elif not os.path.isfile(os.path.join(photos_dir, f)):
                problems.append(f'{i}: file missing: {f}')
            else:
                seen.add(key)
                rows.append((key, f, mt, r['source_file']))
    return rows, problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--csv', required=True)
    parser.add_argument('--photos-dir', default=os.path.join(ROOT, 'static', 'olim_photos'))
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expect-db', help='required with --apply: current_database() must match')
    args = parser.parse_args()
    if args.apply and not args.expect_db:
        raise SystemExit('--apply requires --expect-db <database name>.')
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise SystemExit('DATABASE_URL is not set.')

    rows, problems = read_rows(args.csv, args.photos_dir)
    for p in problems[:20]:
        print('!', p)
    if problems:
        raise SystemExit(f'{len(problems)} bad rows — nothing written.')

    conn = psycopg2.connect(url, options='' if args.apply else
                            '-c default_transaction_read_only=on')
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT current_database()')
            db = cur.fetchone()[0]
            if args.apply and db != args.expect_db:
                raise SystemExit(f'Refusing: connected to {db!r}, expected {args.expect_db!r}.')
            cur.execute("SELECT to_regclass('public.olim_photos')")
            if cur.fetchone()[0] is None:
                raise SystemExit('olim_photos is missing — apply migrations/023_olim_photos.sql first.')
            cur.execute('SELECT COUNT(*) FROM olim_photos')
            before = cur.fetchone()[0]
            if args.apply:
                cur.executemany(
                    "INSERT INTO olim_photos (name_norm, file, match_type, source_file) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (name_norm) DO UPDATE SET file = EXCLUDED.file, "
                    "match_type = EXCLUDED.match_type, source_file = EXCLUDED.source_file, "
                    "created_at = NOW()", rows)
                cur.execute('SELECT COUNT(*) FROM olim_photos')
                after = cur.fetchone()[0]
                conn.commit()
            else:
                after = before
                conn.rollback()
    finally:
        conn.close()
    print(f"{'APPLIED' if args.apply else 'DRY RUN — hech narsa yozilmadi'} ({db}): "
          f"{len(rows)} rows in CSV, olim_photos {before} -> {after}")


if __name__ == '__main__':
    main()
