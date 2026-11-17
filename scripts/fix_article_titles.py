#!/usr/bin/env python3
"""Bir martalik: scientific_articles.title / title_uz ni clean_title() bilan tozalash.

clean_title() qo'shilishidan oldin import qilingan maqolalarda HTML entity
("&gt;", ikki marta kodlangan "&amp;gt;") yoki inline teg (<i>, <sub>) qolgan
bo'lishi mumkin. Skript har bir sarlavhani yangi ingest qanday saqlagan
bo'lsa, xuddi shunday holatga keltiradi. LaTeX ($...$, \\alpha) o'zgarmaydi.

Standart rejim — DRY RUN: faqat o'qiydi va o'zgarishlarni ko'rsatadi.
    python3 scripts/fix_article_titles.py            # dry-run
    python3 scripts/fix_article_titles.py --apply    # bitta tranzaksiyada yozadi

updated_at ataylab o'zgartirilmaydi: bu mazmun tahriri emas, ingest
normalizatsiyasining kechikkan qismi (metadata sync qatorlari eskirmasin).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from services.article_metadata.normalizer import clean_title  # noqa: E402

COLUMNS = ('title', 'title_uz')


def plan_changes(rows):
    """[(id, column, old, new)] — faqat clean_title natijasi farq qiladiganlar.

    title NOT NULL: tozalangandan keyin bo'sh qolsa, o'zgartirilmaydi.
    """
    changes = []
    for row in rows:
        for column in COLUMNS:
            old = row.get(column)
            if not old:
                continue
            new = clean_title(old)
            if column == 'title' and not new:
                continue
            if (new or '') != old:
                changes.append((row['id'], column, old, new or None))
    return changes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--apply', action='store_true',
                        help="o'zgarishlarni yozish (standart: dry-run)")
    args = parser.parse_args(argv)

    from data import get_connection
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, title, title_uz FROM scientific_articles ORDER BY id")
            rows = [dict(zip(('id', 'title', 'title_uz'), r)) for r in cur.fetchall()]
            changes = plan_changes(rows)
            for article_id, column, old, new in changes:
                print('#%d %s\n  oldin: %s\n  keyin: %s' % (article_id, column, old, new))
            print("Ko'rildi: %d maqola, o'zgaradi: %d maydon (%s)"
                  % (len(rows), len(changes), 'APPLY' if args.apply else 'DRY RUN'))
            if not args.apply:
                conn.rollback()
                print("(DRY RUN — hech narsa yozilmadi. Yozish uchun --apply.)")
                return 0
            for article_id, column, _old, new in changes:
                cur.execute(f"UPDATE scientific_articles SET {column} = %s WHERE id = %s",
                            (new, article_id))
        conn.commit()
        print('Yozildi: %d maydon.' % len(changes))
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
