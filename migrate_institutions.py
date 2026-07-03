"""Populate institution_map — the Cyrillic↔Latin bridge for dissertation
institutions. NON-DESTRUCTIVE: dissertations.muassasa is never modified; we only
record, per raw variant, its canonical (most-common) variant + Latin name +
category + region, so the directory can group/label/count without touching data.

Usage:
    python migrate_institutions.py          # DRY RUN — prints summary, writes nothing
    python migrate_institutions.py --apply   # actually create + populate the table

Safe to re-run: the table is created IF NOT EXISTS and rows are upserted by
cyrillic_name, so applying repeatedly converges (idempotent).
"""
import os
import sys
import argparse
from collections import Counter

from dotenv import load_dotenv
import psycopg2

from institutions import (transliterate, detect_category, build_canonical,
                          INSTITUTION_CATEGORIES)

load_dotenv()

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS institution_map (
    id SERIAL PRIMARY KEY,
    cyrillic_name TEXT NOT NULL UNIQUE,
    canonical_name TEXT,
    latin_name TEXT,
    category VARCHAR(50) DEFAULT 'universitet',
    region VARCHAR(100),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);
"""

INDEX_SQL = ("CREATE INDEX IF NOT EXISTS idx_institution_map_canonical "
             "ON institution_map (canonical_name)")

UPSERT_SQL = """
INSERT INTO institution_map (cyrillic_name, canonical_name, latin_name, category, region)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (cyrillic_name) DO UPDATE SET
    canonical_name = EXCLUDED.canonical_name,
    latin_name     = EXCLUDED.latin_name,
    category       = EXCLUDED.category,
    region         = EXCLUDED.region
"""

# Region from Latin keywords (mirrors app.detect_uni_city_region; None if unknown
# so we never mislabel a non-Tashkent institution as Tashkent).
_REGION_RULES = [
    (('buxoro', 'bukhara'), 'Buxoro'),
    (('andijon', 'andijan'), 'Andijon'),
    (('farg', 'fargona', 'qoqon', 'kokand'), 'Fargona'),
    (('samarqand', 'samarkand'), 'Samarqand'),
    (('namangan',), 'Namangan'),
    (('nukus', 'qoraqalpoq', 'ajiniyoz', 'berdaq'), 'Qoraqalpogiston'),
    (('termiz', 'surxon'), 'Surxondaryo'),
    (('qarshi', 'shahrisabz'), 'Qashqadaryo'),
    (('jizzax',), 'Jizzax'),
    (('navoiy',), 'Navoiy'),
    (('urganch', 'xorazm'), 'Xorazm'),
    (('guliston', 'sirdaryo'), 'Sirdaryo'),
    (('chirchiq', 'toshkent', 'tashkent'), 'Toshkent'),
]


def detect_region(latin_name):
    n = (latin_name or '').lower()
    for keys, region in _REGION_RULES:
        if any(k in n for k in keys):
            return region
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true',
                    help='commit changes (default is a dry run that writes nothing)')
    args = ap.parse_args()

    url = os.environ.get('DATABASE_URL')
    if not url:
        print('DATABASE_URL is not set.'); sys.exit(1)

    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            # Fresh rebuild: dedup logic defines the whole table content, so
            # recreate it from scratch (stale rows from earlier logic would
            # otherwise linger). Safe in dry-run — the transaction rolls back.
            cur.execute("DROP TABLE IF EXISTS institution_map")
            cur.execute(CREATE_SQL)
            cur.execute(INDEX_SQL)

            cur.execute(
                "SELECT TRIM(muassasa), COUNT(*) FROM dissertations "
                "WHERE muassasa IS NOT NULL AND TRIM(muassasa) <> '' "
                "GROUP BY TRIM(muassasa)")
            variant_counts = {name: cnt for name, cnt in cur.fetchall()}

            mapping = build_canonical(variant_counts)          # raw -> canonical
            canon_set = set(mapping.values())
            cat_counter = Counter()
            rows = []
            for raw, canonical in mapping.items():
                latin = transliterate(canonical)
                category = detect_category(canonical)
                region = detect_region(latin)
                rows.append((raw, canonical, latin, category, region))
            for canonical in canon_set:                        # category per group
                cat_counter[detect_category(canonical)] += 1

            for r in rows:
                cur.execute(UPSERT_SQL, r)

            print('── institution_map population ──')
            print(f'  raw variants (distinct muassasa) : {len(variant_counts)}')
            print(f'  canonical groups after dedup      : {len(canon_set)}')
            if variant_counts:
                print(f'  duplicates collapsed              : {len(variant_counts) - len(canon_set)}')
            print('  by category:')
            for cat, label in INSTITUTION_CATEGORIES.items():
                print(f'    {label:<14} {cat_counter.get(cat, 0)}')

            # largest merge groups — eyeball check that dedup worked
            by_canon = {}
            for raw, canonical in mapping.items():
                by_canon.setdefault(canonical, []).append(raw)
            biggest = sorted(((c, v) for c, v in by_canon.items() if len(v) > 1),
                             key=lambda cv: -len(cv[1]))[:8]
            print('  largest merges (canonical ← N variants):')
            for canonical, variants in biggest:
                total = sum(variant_counts.get(v, 0) for v in variants)
                print(f'    "{canonical}"  ←  {len(variants)} variants, {total} diss.')
                for v in variants[:4]:
                    if v != canonical:
                        print(f'        · {v}')

        if args.apply:
            conn.commit()
            print('APPLIED ✓  institution_map committed.')
        else:
            conn.rollback()
            print('DRY RUN — nothing written. Re-run with --apply to commit.')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
