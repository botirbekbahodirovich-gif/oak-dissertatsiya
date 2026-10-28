"""Compare the legacy text chain with canonical IDs. Read-only.

  python3 scripts/compare_institution_modes.py --out /tmp/canonical_compare

Writes <out>_orgs.csv (/reyting/ball organizations: diss / scholars / score /
rank per canonical name in both modes), <out>_scholars.csv (scholars whose
primary organization differs) and <out>_catalog.csv (/universities directory
counts: institution_map text vs institution_id), each with a reason column.
"""
import argparse
import csv
import os
import sys

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()


def _reason(text, ident, multi_names, unlinked_names):
    if text is None:
        return 'faqat_ID: yangi canonical (bola/ko‘p tashkilot qismi yoki mapda yo‘q nom)'
    if ident is None:
        return 'faqat_matn: guruh ID olmagan (ko‘p tashkilotli nom yoki bog‘lanmagan)'
    if text['canonical'] in multi_names:
        return 'hamkor_himoya: ikkinchi tashkilotga ham to‘liq ball'
    if text['diss'] > ident['diss']:
        return f"bog‘lanmagan variantlar: {text['diss'] - ident['diss']} himoya ID’siz"
    if text['diss'] < ident['diss']:
        return 'ID ko‘proq: hamkor himoya yoki mapda yo‘q nom exact bog‘langan'
    if text['category'] != ident['category']:
        return f"kategoriya: {text['category']} → {ident['category']} (ko‘pchilik ovozi)"
    return 'ball farqi: olimning asosiy tashkiloti o‘zgargan'


def main():
    from blueprints.reyting import _aggregate_rating, _fetch_rating_inputs
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='/tmp/canonical_compare')
    args = parser.parse_args()
    conn = psycopg2.connect(os.environ['DATABASE_URL'],
                            options='-c default_transaction_read_only=on -c statement_timeout=120000')
    try:
        with conn.cursor() as cur:
            text = _aggregate_rating(*_fetch_rating_inputs(cur, 'text')[1:])
            ident = _aggregate_rating(*_fetch_rating_inputs(cur, 'identity')[1:])
            cur.execute("""
                SELECT COALESCE(im.canonical_name, im.cyrillic_name), COUNT(d.id)
                FROM institution_map im
                LEFT JOIN dissertations d ON TRIM(d.muassasa) = im.cyrillic_name
                WHERE im.is_active = TRUE GROUP BY 1
            """)
            cat_text = dict(cur.fetchall())
            cur.execute("""
                SELECT i.canonical_name, COUNT(d.id) FROM institutions i
                LEFT JOIN dissertations d ON d.institution_id = i.id
                WHERE i.is_active = TRUE AND i.merged_into_id IS NULL GROUP BY 1
            """)
            cat_id = dict(cur.fetchall())
            cur.execute("""
                SELECT DISTINCT i.canonical_name FROM dissertation_institutions di
                JOIN institutions i ON i.id = di.institution_id
            """)
            multi_names = {r[0] for r in cur.fetchall()}
            cur.execute("""
                SELECT COUNT(*) FILTER (WHERE institution_id IS NULL), COUNT(*)
                FROM dissertations
            """)
            unlinked, total = cur.fetchone()
        conn.rollback()
    finally:
        conn.close()

    t_orgs = {o['canonical']: o for o in text['orgs']}
    i_orgs = {o['canonical']: o for o in ident['orgs']}
    diffs = 0
    with open(args.out + '_orgs.csv', 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['tashkilot', 'diss_matn', 'diss_id', 'olim_matn', 'olim_id',
                    'ball_matn', 'ball_id', 'orin_matn', 'orin_id', 'sabab'])
        for name in sorted(set(t_orgs) | set(i_orgs)):
            a, b = t_orgs.get(name), i_orgs.get(name)
            same = a and b and (a['diss'], a['scholars'], a['score']) == (b['diss'], b['scholars'], b['score'])
            if same:
                continue
            diffs += 1
            g = lambda o, k: o[k] if o else ''
            w.writerow([name, g(a, 'diss'), g(b, 'diss'), g(a, 'scholars'), g(b, 'scholars'),
                        g(a, 'score'), g(b, 'score'), g(a, 'rank'), g(b, 'rank'),
                        _reason(a, b, multi_names, None)])
    t_sch = {s['name']: s for s in text['scholars']}
    moved = 0
    with open(args.out + '_scholars.csv', 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['olim', 'tashkilot_matn', 'tashkilot_id', 'ball_matn', 'ball_id', 'sabab'])
        for s in ident['scholars']:
            old = t_sch.pop(s['name'], None)
            if old and (old['org'], old['score']) == (s['org'], s['score']):
                continue
            moved += 1
            w.writerow([s['name'], old['org'] if old else '', s['org'],
                        old['score'] if old else '', s['score'],
                        'yangi_ID_nomi' if old else 'faqat_ID'])
        for name, old in t_sch.items():
            moved += 1
            w.writerow([name, old['org'], '', old['score'], '',
                        'ID rejimida yo‘q: himoyalari bog‘lanmagan'])
    cat_diffs = 0
    with open(args.out + '_catalog.csv', 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['muassasa', 'diss_matn', 'diss_id', 'sabab'])
        for name in sorted(set(cat_text) | set(cat_id)):
            a, b = cat_text.get(name), cat_id.get(name)
            if a == b:
                continue
            cat_diffs += 1
            reason = ('faqat_ID' if a is None else 'faqat_matn' if b is None
                      else 'bog‘lanmagan variantlar' if a > b else 'ID ko‘proq (exact/mapda yo‘q)')
            w.writerow([name, a if a is not None else '', b if b is not None else '', reason])

    print(f"dissertatsiyalar: {total}, institution_id yo‘q: {unlinked}")
    print(f"reyting totals matn: {text['totals']}")
    print(f"reyting totals ID:   {ident['totals']}")
    print(f"farqli tashkilotlar: {diffs}, farqli olimlar: {moved}, katalog farqlari: {cat_diffs}")
    print(f"CSV: {args.out}_orgs.csv, {args.out}_scholars.csv, {args.out}_catalog.csv")


if __name__ == '__main__':
    main()
