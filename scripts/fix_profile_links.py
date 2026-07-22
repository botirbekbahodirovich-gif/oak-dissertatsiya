"""olim_profiles jadvalidagi mavjud noto'g'ri yozilgan tashqi havolalarni
(Telegram, ORCID, Google Scholar, Scopus/WoS/veb-sayt) kanonik shaklga
keltiradi. Bir xil normalizatsiya mantig'i endi saqlash paytida ham
qo'llanadi (cabinet.py profile_save) — bu skript faqat oldindan noto'g'ri
saqlangan mavjud yozuvlarni tuzatish uchun.

Foydalanish:
    python scripts/fix_profile_links.py            # DRY RUN — hech narsa yozmaydi
    python scripts/fix_profile_links.py --apply     # DB ga real yozadi

Xavfsiz qayta ishga tushiriladi: normalizatsiya idempotent (allaqachon to'g'ri
yozuvlar o'zgarmaydi).
"""
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from data import get_connection
from utils.links import normalize_telegram, normalize_orcid, normalize_scholar, normalize_url

load_dotenv()

_URL_COLUMNS = [
    ('telegram_url', normalize_telegram),
    ('orcid_url', normalize_orcid),
    ('scholar_url', normalize_scholar),
    ('scopus_url', normalize_url),
    ('wos_url', normalize_url),
    ('website_url', normalize_url),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true',
                        help="Real yozish rejimi (bermasa — faqat DRY RUN)")
    parser.add_argument('--examples', type=int, default=10,
                        help="Har ustun uchun ko'rsatiladigan misollar soni")
    args = parser.parse_args()

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            total_changed = 0
            for col, normalize_fn in _URL_COLUMNS:
                cur.execute(f"SELECT id, {col} FROM olim_profiles WHERE {col} IS NOT NULL AND {col} != ''")
                rows = cur.fetchall()
                changes = []
                for row_id, old_val in rows:
                    new_val = normalize_fn(old_val)
                    if new_val and new_val != old_val:
                        changes.append((row_id, old_val, new_val))

                print(f"\n=== {col}: {len(changes)} ta yozuv o'zgaradi (jami {len(rows)} ta) ===")
                for row_id, old_val, new_val in changes[:args.examples]:
                    print(f"  [{row_id}] {old_val!r} -> {new_val!r}")
                if len(changes) > args.examples:
                    print(f"  ... yana {len(changes) - args.examples} ta")

                total_changed += len(changes)

                if args.apply and changes:
                    for row_id, _old_val, new_val in changes:
                        cur.execute(f"UPDATE olim_profiles SET {col} = %s WHERE id = %s",
                                    (new_val, row_id))
                    conn.commit()
                    print(f"  -> yozildi ({len(changes)} ta qator yangilandi).")

            print(f"\nJami: {total_changed} ta havola {'yangilandi' if args.apply else 'yangilanadi (DRY RUN)'}.")
            if not args.apply:
                print("Real yozish uchun: python scripts/fix_profile_links.py --apply")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
