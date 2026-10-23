"""Bootstrap canonical institution IDs conservatively. Default: dry run."""
import argparse
import os
import sys

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from institution_identity import (ensure_identity_schema,
                                  looks_like_multiple_institutions,
                                  normalize_institution_identity,
                                  queue_resolution)
from institutions import transliterate_display

if not os.environ.get("DATABASE_URL"):
    load_dotenv()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise SystemExit('DATABASE_URL is not set.')
    conn = psycopg2.connect(url)
    stats = {'institutions': 0, 'aliases': 0, 'queued': 0, 'linked': 0}
    try:
        with conn.cursor() as cur:
            ensure_identity_schema(cur)
            cur.execute("""
                SELECT COALESCE(canonical_name,cyrillic_name),
                       MAX(COALESCE(category,'boshqa')), MAX(region)
                FROM institution_map WHERE is_active IS NOT FALSE GROUP BY 1 ORDER BY 1
            """)
            by_name = {}
            for canonical, category, region in cur.fetchall():
                key = normalize_institution_identity(canonical)
                if not key or looks_like_multiple_institutions(canonical):
                    continue
                cur.execute("""
                    INSERT INTO institutions
                     (canonical_name,normalized_name,latin_name,category,region)
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT(normalized_name) DO UPDATE SET
                     category=COALESCE(NULLIF(institutions.category,'boshqa'),EXCLUDED.category),
                     region=COALESCE(institutions.region,EXCLUDED.region)
                    RETURNING id
                """,(canonical,key,transliterate_display(canonical),category,region))
                by_name[canonical]=cur.fetchone()[0]
                stats['institutions']+=1
            cur.execute("""
                SELECT cyrillic_name,COALESCE(canonical_name,cyrillic_name)
                FROM institution_map WHERE is_active IS NOT FALSE ORDER BY cyrillic_name
            """)
            for raw, canonical in cur.fetchall():
                target_id=by_name.get(canonical)
                raw_key=normalize_institution_identity(raw)
                safe=(target_id and raw_key==normalize_institution_identity(canonical)
                      and not looks_like_multiple_institutions(raw))
                if safe:
                    cur.execute("""
                        INSERT INTO institution_aliases
                         (institution_id,raw_name,normalized_name,alias_type,source)
                        VALUES (%s,%s,%s,'exact','legacy_map')
                        ON CONFLICT(normalized_name) DO NOTHING
                    """,(target_id,raw,raw_key))
                    stats['aliases']+=cur.rowcount
                else:
                    reason='multiple' if looks_like_multiple_institutions(raw) else 'legacy_review'
                    queue_resolution(cur,raw,raw_key,reason,'legacy_map',target_id)
                    cur.execute("""
                        UPDATE institution_resolution_queue SET occurrence_count=(
                         SELECT COUNT(*) FROM dissertations WHERE TRIM(muassasa)=%s)
                        WHERE raw_name=%s
                    """,(raw,raw))
                    stats['queued']+=1
            cur.execute("""
                UPDATE dissertations d SET institution_id=a.institution_id
                FROM institution_aliases a
                WHERE d.institution_id IS NULL AND TRIM(d.muassasa)=a.raw_name
                 AND a.review_status='approved'
            """)
            stats['linked']=cur.rowcount
        conn.commit() if args.apply else conn.rollback()
    finally:
        conn.close()
    print('APPLIED' if args.apply else 'DRY RUN — hech narsa yozilmadi')
    for key,value in stats.items():
        print(f'{key}: {value}')


if __name__=='__main__':
    main()
