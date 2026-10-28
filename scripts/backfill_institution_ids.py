"""Backfill canonical institution IDs. Default: read-only dry run + CSV.

  python3 scripts/backfill_institution_ids.py --csv /tmp/canonical_dryrun.csv
  python3 scripts/backfill_institution_ids.py --apply --expect-db olimlar_staging_2610

The dry run opens a read-only session (default_transaction_read_only) and runs
SELECTs only - no ALTER, no UPDATE, no schema bootstrap. --apply needs
migration 021_institution_links.sql and the exact database name, and writes
everything in one transaction. Rules: institution_backfill.py.
"""
import argparse
import csv
import os
import sys

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from institution_backfill import (CSV_HEADER, apply_plan, build_plan,
                                  plan_csv_rows, read_inputs)

load_dotenv()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expect-db', help='required with --apply: current_database() must match')
    parser.add_argument('--csv', default='/tmp/canonical_dryrun.csv')
    args = parser.parse_args()
    if args.apply and not args.expect_db:
        raise SystemExit('--apply requires --expect-db <database name>.')
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise SystemExit('DATABASE_URL is not set.')

    conn = psycopg2.connect(url, options='' if args.apply else
                            '-c default_transaction_read_only=on -c statement_timeout=60000')
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT current_database()')
            db = cur.fetchone()[0]
            if args.apply and db != args.expect_db:
                raise SystemExit(f'Refusing: connected to {db!r}, expected {args.expect_db!r}.')
            plan = build_plan(*read_inputs(cur))
            with open(args.csv, 'w', newline='', encoding='utf-8') as fh:
                writer = csv.writer(fh)
                writer.writerow(CSV_HEADER)
                writer.writerows(plan_csv_rows(plan))
            written = apply_plan(cur, plan) if args.apply else {}
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
    finally:
        conn.close()

    print(f"{'APPLIED' if args.apply else 'DRY RUN — hech narsa yozilmadi'} ({db})")
    for key, value in sorted(plan.stats.items()):
        print(f'plan.{key}: {value}')
    for key, value in sorted(written.items()):
        print(f'written.{key}: {value}')
    print(f'CSV: {args.csv}')


if __name__ == '__main__':
    main()
