"""Merge oldidan xavfsizlik backup'lari.

Creates snapshot copies of the tables the merge pipeline touches:
    dissertations_backup_YYYYMMDD
    universities_backup_YYYYMMDD      (if the table exists)
    institution_map_backup_YYYYMMDD   (if the table exists)

Usage (WSL, repo root):
    python3 scripts/backup_tables.py
"""
import os
import sys
from datetime import date

from dotenv import load_dotenv
import psycopg2

load_dotenv()


def main():
    url = os.environ.get('DATABASE_URL')
    if not url:
        print('DATABASE_URL is not set.'); sys.exit(1)

    suffix = date.today().strftime('%Y%m%d')
    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public'")
            tables = {r[0] for r in cur.fetchall()}

            for table in ('dissertations', 'universities', 'institution_map'):
                if table not in tables:
                    print(f'  ~ {table}: jadval topilmadi, backup o\'tkazib yuborildi')
                    continue
                backup = f'{table}_backup_{suffix}'
                cur.execute(
                    f'CREATE TABLE IF NOT EXISTS {backup} AS SELECT * FROM {table}')
                cur.execute(f'SELECT COUNT(*) FROM {backup}')
                print(f'  ✓ {backup}: {cur.fetchone()[0]} qator')
        conn.commit()
        print('Backup tayyor.')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
