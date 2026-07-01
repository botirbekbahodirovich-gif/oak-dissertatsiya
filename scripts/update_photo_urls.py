"""
update_photo_urls.py — Supabase avatars uchun photo_url backfill skripti.

`dissertations` jadvalidagi har bir olim (olim) va ilmiy rahbar (ilmiy_rahbar)
ismini Supabase `avatars` bucket dagi fayl nomiga aylantirib, mos ravishda
`photo_url` va `ilmiy_rahbar_photo_url` ustunlarini yangilaydi.

Ishlatish:
    DATABASE_URL="postgres://..." python scripts/update_photo_urls.py
    # faqat bo'sh photo_url larni to'ldirish (standart):
    python scripts/update_photo_urls.py
    # barcha satrlarni majburan qayta yozish:
    python scripts/update_photo_urls.py --force
"""
import os
import re
import sys

import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    sys.exit("DATABASE_URL o'rnatilmagan")

AVATAR_BUCKET = ("https://qzbgmfbpryneyacrcdfh.supabase.co/storage/v1/"
                 "object/public/avatars/")
_AVATAR_STRIP = "'\"’‘ʻʼ`´"


def generate_avatar_url(olim_name):
    """Olim ismini tozalab Supabase avatar URL ga aylantiradi.

    - Probellar -> '_'
    - Maxsus belgilar (' " ʻ oʻ va h.k.) olib tashlanadi
    - Ketma-ket '_' bitta '_' ga qisqartiriladi
    Natija: {Familiya}_{Ism}_{Otasining_ismi}.jpg
    """
    s = (olim_name or "").strip()
    if not s:
        return None
    for ch in _AVATAR_STRIP:
        s = s.replace(ch, "")
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        return None
    return AVATAR_BUCKET + s + ".jpg"


def backfill(force=False):
    conn = psycopg2.connect(DATABASE_URL)
    updated_olim = 0
    updated_rahbar = 0

    # ── olim -> photo_url ──
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT olim FROM dissertations "
            "WHERE olim IS NOT NULL AND olim <> ''"
        )
        names = [r[0] for r in cur.fetchall()]

    seen = set()
    with conn.cursor() as cur:
        for name in names:
            key = name.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            url = generate_avatar_url(name)
            if not url:
                continue
            if force:
                cur.execute(
                    "UPDATE dissertations SET photo_url = %s WHERE olim = %s",
                    (url, name),
                )
            else:
                cur.execute(
                    "UPDATE dissertations SET photo_url = %s "
                    "WHERE olim = %s AND (photo_url IS NULL OR photo_url = '')",
                    (url, name),
                )
            updated_olim += cur.rowcount
    conn.commit()

    # ── ilmiy_rahbar -> ilmiy_rahbar_photo_url ──
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ilmiy_rahbar FROM dissertations "
            "WHERE ilmiy_rahbar IS NOT NULL AND ilmiy_rahbar <> ''"
        )
        rahbarlar = [r[0] for r in cur.fetchall()]

    seen = set()
    with conn.cursor() as cur:
        for name in rahbarlar:
            key = name.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            url = generate_avatar_url(name)
            if not url:
                continue
            if force:
                cur.execute(
                    "UPDATE dissertations SET ilmiy_rahbar_photo_url = %s "
                    "WHERE ilmiy_rahbar = %s",
                    (url, name),
                )
            else:
                cur.execute(
                    "UPDATE dissertations SET ilmiy_rahbar_photo_url = %s "
                    "WHERE ilmiy_rahbar = %s AND "
                    "(ilmiy_rahbar_photo_url IS NULL OR ilmiy_rahbar_photo_url = '')",
                    (url, name),
                )
            updated_rahbar += cur.rowcount
    conn.commit()
    conn.close()

    print(f"Olim photo_url yangilandi:          {updated_olim} satr")
    print(f"Ilmiy rahbar photo_url yangilandi:  {updated_rahbar} satr")


if __name__ == "__main__":
    backfill(force="--force" in sys.argv)
