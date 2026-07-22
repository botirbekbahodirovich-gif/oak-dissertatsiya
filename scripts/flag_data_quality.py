"""dissertations jadvalidagi yozuvlarni ma'lumotlar sifati bo'yicha belgilaydi
(dissertations.data_quality ustuni — app.py migratsiyasida qo'shilgan).

Qoidalar:
  missing_author — olim ustuni bo'sh/NULL (muallif umuman yo'q)
  incomplete     — ilmiy_rahbar bo'sh/NULL yoki to'ldirilmagan shablon matni
                   ("Ф.И.Ш" / "F.I.Sh" / "...нинг Ф.И.Ш" kabi OAK manbasida
                   qolib ketgan placeholder)
  complete       — standart holat (default), ustiga yozilmaydi

Qisqa/shubhali ism (< 5 belgi) hech qanday kategoriyaga AVTOMATIK belgilanmaydi
— faqat ro'yxat sifatida ko'rsatiladi, chunki ba'zi haqiqiy ismlar ham qisqa
bo'lishi mumkin (masalan bitta so'zli taxallus). Qo'lda ko'rib chiqish kerak.

Foydalanish:
    python scripts/flag_data_quality.py            # DRY RUN — hech narsa yozmaydi
    python scripts/flag_data_quality.py --apply     # DB ga real yozadi

Xavfsiz qayta ishga tushiriladi: idempotent (allaqachon to'g'ri belgilangan
yozuvlar qayta o'zgartirilmaydi, chunki har bir UPDATE data_quality='complete'
bo'lgan/mos qatorlarni oldindan filtrlaydi).
"""
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from data import get_connection

load_dotenv()

# OAK manbasida uchraydigan, to'ldirilmagan shablon matnlari (ilmiy_rahbar).
_PLACEHOLDER_PATTERNS = ['%Ф.И.Ш%', '%F.I.Sh%', '%ининг Ф%', '%ининг F%']

_MISSING_AUTHOR_WHERE = "(olim IS NULL OR TRIM(olim) = '')"
_INCOMPLETE_RAHBAR_WHERE = (
    "(ilmiy_rahbar IS NULL OR TRIM(ilmiy_rahbar) = '' OR " +
    " OR ".join(["ilmiy_rahbar LIKE %s"] * len(_PLACEHOLDER_PATTERNS)) + ")"
)


def _print_stats(cur, label):
    cur.execute("SELECT COALESCE(data_quality, 'complete'), COUNT(*) "
                "FROM dissertations GROUP BY COALESCE(data_quality, 'complete') "
                "ORDER BY 2 DESC")
    print(f"\n=== {label} ===")
    for quality, cnt in cur.fetchall():
        print(f"  {quality}: {cnt}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true',
                        help="Real yozish rejimi (bermasa — faqat DRY RUN)")
    parser.add_argument('--examples', type=int, default=10,
                        help="Har qoida uchun ko'rsatiladigan misollar soni")
    args = parser.parse_args()

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _print_stats(cur, "Joriy holat")

            # 1) missing_author — muallif yo'q
            cur.execute(f"SELECT id, sana, mavzu FROM dissertations "
                        f"WHERE {_MISSING_AUTHOR_WHERE} "
                        f"AND COALESCE(data_quality, 'complete') <> 'missing_author'")
            missing = cur.fetchall()
            print(f"\n=== missing_author: {len(missing)} ta yangi yozuv topildi ===")
            for row_id, sana, mavzu in missing[:args.examples]:
                print(f"  [{row_id}] {sana or '—'} — {(mavzu or '')[:60]!r}")
            if len(missing) > args.examples:
                print(f"  ... yana {len(missing) - args.examples} ta")
            if args.apply and missing:
                cur.execute(f"UPDATE dissertations SET data_quality = 'missing_author' "
                            f"WHERE {_MISSING_AUTHOR_WHERE} "
                            f"AND COALESCE(data_quality, 'complete') <> 'missing_author'")
                conn.commit()
                print(f"  -> yozildi ({len(missing)} ta qator yangilandi).")

            # 2) incomplete — rahbar yozuvi bo'sh/shablon (muallif YO'Q bo'lganlar
            #    ustidan yozilmaydi — missing_author ustunlik qiladi)
            cur.execute(f"SELECT id, sana, olim, ilmiy_rahbar FROM dissertations "
                        f"WHERE COALESCE(data_quality, 'complete') = 'complete' "
                        f"AND NOT {_MISSING_AUTHOR_WHERE} "
                        f"AND {_INCOMPLETE_RAHBAR_WHERE}",
                        _PLACEHOLDER_PATTERNS)
            incomplete = cur.fetchall()
            print(f"\n=== incomplete (rahbar yozuvi): {len(incomplete)} ta yangi yozuv topildi ===")
            for row_id, sana, olim, rahbar in incomplete[:args.examples]:
                print(f"  [{row_id}] {sana or '—'} — {olim!r} — rahbar={rahbar!r}")
            if len(incomplete) > args.examples:
                print(f"  ... yana {len(incomplete) - args.examples} ta")
            if args.apply and incomplete:
                cur.execute(f"UPDATE dissertations SET data_quality = 'incomplete' "
                            f"WHERE COALESCE(data_quality, 'complete') = 'complete' "
                            f"AND NOT {_MISSING_AUTHOR_WHERE} "
                            f"AND {_INCOMPLETE_RAHBAR_WHERE}",
                            _PLACEHOLDER_PATTERNS)
                conn.commit()
                print(f"  -> yozildi ({len(incomplete)} ta qator yangilandi).")

            # 3) Qisqa/shubhali ism — FAQAT hisobot, hech qachon avtomatik
            #    belgilanmaydi (ba'zi haqiqiy ismlar qisqa bo'lishi mumkin —
            #    qo'lda ko'rib chiqish talab qilinadi).
            cur.execute(f"SELECT id, olim, sana FROM dissertations "
                        f"WHERE COALESCE(data_quality, 'complete') = 'complete' "
                        f"AND olim IS NOT NULL AND LENGTH(TRIM(olim)) < 5 "
                        f"AND NOT {_MISSING_AUTHOR_WHERE} "
                        f"ORDER BY id")
            short = cur.fetchall()
            print(f"\n=== Qisqa/shubhali ism (< 5 belgi) — FAQAT KO'RIB CHIQISH UCHUN, "
                  f"avtomatik belgilanmaydi: {len(short)} ta ===")
            for row_id, olim, sana in short[:max(args.examples, 20)]:
                print(f"  [{row_id}] {olim!r} — {sana or '—'}")
            if len(short) > max(args.examples, 20):
                print(f"  ... yana {len(short) - max(args.examples, 20)} ta")

            if args.apply:
                _print_stats(cur, "Yangi holat")
            else:
                print("\nReal yozish uchun: python scripts/flag_data_quality.py --apply")
                print("(3-qoida — qisqa ism — --apply bilan ham yozilmaydi, faqat hisobot)")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
