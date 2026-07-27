"""Bo'sh maydonlarni "qo'shni" (kam ishlatiladigan) ustunlar bilan solishtirish —
FAQAT TAHLIL, hech qanday to'ldirish qoidasi bu yerda tanlanmaydi.

KONTEKST: `dissertations` jadvalida scan tomonidan tekshirilmaydigan qo'shimcha
ustunlar bor — ilmiy_rahbar_daraja, yetakchi_tashkilot, ilmiy_kengash(_raqami),
opponent_1..3, oak_id, mavzu_raqami, ixtisoslik_nomi, fan_tarmoqi, yonalish
(docs/data_quality_inventory.md, "out of Phase-1 scope"). Ba'zi bo'sh
maydonlarning qiymati shu qo'shni ustunlarda allaqachon yotgan bo'lishi
mumkin. Bu skript FAQAT shuni sanaydi va namuna ko'rsatadi — qaysi ustundan
qaysi maydonga qanday qoida bilan ko'chirish kerakligini HAL QILMAYDI (bu
2-BOSQICH ishi, tasdiqlangan qoidalar bilan).

Ikkita tahlil:
  1. Har bir bo'sh maydon (muassasa, ilmiy_rahbar, ixtisoslik, sana) uchun —
     har bir nomzod qo'shni ustunda nechta qatorda qiymat borligini sanaydi.
  2. `ilmiy_rahbar` ustunida ISM emas, DARAJA matni turgan holatlarni
     aniqlaydi (masalan "педагогика фанлари доктори (DSc)") — bu parser
     `ilmiy_rahbar` va `ilmiy_rahbar_daraja` ni chalkashtirgan bo'lishi mumkin
     degan taxminni tekshiradi — va o'sha qatorlarda `ilmiy_rahbar_daraja`
     ustunida nima borligini ko'rsatadi.

Bu modul BAZAGA HECH NARSA YOZMAYDI. Faqat SELECT (test bilan tekshiriladi,
scripts/rescrape_broken_records.py dagi 1-faza uslubida).

Usage (server yoki WSL, repo root):
    export TEST_DATABASE_URL=postgresql://…/olimlar_staging_db
    python3 scripts/analyze_sibling_columns.py
    python3 scripts/analyze_sibling_columns.py --limit 2000     # tez sinov
    python3 scripts/analyze_sibling_columns.py --record-id 123  # bitta yozuv
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from datetime import datetime
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_quality.cli_common import resolve_connection, truncate  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(REPO_ROOT, "docs")
DEFAULT_OUT = os.path.join(DOCS_DIR, "sibling_column_analysis.md")

# Tekshiriladigan bo'sh maydonlar.
TARGET_FIELDS: Tuple[str, ...] = ("muassasa", "ilmiy_rahbar", "ixtisoslik", "sana")

FIELD_LABELS = {
    "muassasa": "Muassasa",
    "ilmiy_rahbar": "Ilmiy rahbar",
    "ixtisoslik": "Ixtisoslik",
    "sana": "Himoya sanasi",
}

# Har bir bo'sh maydon uchun tekshiriladigan nomzod ustunlar — USTUN
# MA'NOSIGA (semantikasiga) qarab tanlangan (docs/data_quality_inventory.md),
# to'ldirish mantig'iga qarab EMAS. Bu modul faqat nima borligini sanaydi.
SIBLING_CANDIDATES: Dict[str, Tuple[str, ...]] = {
    # ilmiy_kengash = "IK fаолият кўрсатаётган муассаса" — ko'pincha
    # dissertatsiya bajarilgan muassasa bilan bir xil yoki unga yaqin.
    "muassasa": ("ilmiy_kengash", "yetakchi_tashkilot"),
    # ilmiy_rahbar_daraja — rahbarning ILMIY DARAJASI, ismi emas; baribir
    # simmetriya uchun tekshiriladi (agar ism bo'sh-u daraja to'ldirilgan
    # bo'lsa, bu alohida qiziq holat).
    "ilmiy_rahbar": ("ilmiy_rahbar_daraja",),
    # ixtisoslik_nomi — ixtisoslik NOMI (kod emas); fan_tarmoqi — kengroq soha.
    "ixtisoslik": ("ixtisoslik_nomi", "fan_tarmoqi"),
    # Sanaga bevosita mos ustun yo'q; ro'yxat raqamlari ko'pincha yil
    # tutadi (masalan "В2026.2.PhD/..."), shuning uchun tekshiriladi.
    "sana": ("mavzu_raqami", "ilmiy_kengash_raqami", "oak_id"),
}

ALL_SIBLING_COLUMNS: Tuple[str, ...] = tuple(sorted(
    {c for cols in SIBLING_CANDIDATES.values() for c in cols}))

SELECT_COLUMNS: Tuple[str, ...] = ("id",) + TARGET_FIELDS + ALL_SIBLING_COLUMNS

# ilmiy_rahbar shablon qoldig'i (validators.py / rescrape skriptidagi ro'yxat
# bilan bir xil) — "to'ldirilgan" emas, bo'sh deb hisoblanadi.
_SUPERVISOR_PLACEHOLDERS = (
    "ф.и.ш", "f.i.sh", "ф.и.о", "фиш",
    "ининг ф", "ининг f", "нинг ф.и", "нинг f.i",
)

# ilmiy_rahbar da ISM o'rniga DARAJA matni borligini bildiruvchi kalit
# so'zlar. Haqiqiy ism bu so'zlarni o'z ichiga olmaydi, shuning uchun
# yolg'on-musbat xavfi past.
_DEGREE_TEXT_HINT = re.compile(
    r"\b(PhD|DSc)\b"
    r"|фан(?:лари)?\s+доктор"
    r"|фалсафа\s+доктор"
    r"|фанлари\s+номзод"
    r"|доктори\b"
    r"|номзоди\b"
    r"|doktori\b"
    r"|nomzodi\b",
    re.IGNORECASE)

_DATE_LIKE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")
# No \b on the left: OAK registry codes glue a Cyrillic letter straight onto
# the year ("В2026.2.PhD/Iqt4897"), and a letter is a \w character too, so a
# word-boundary assertion would never fire between them. Anchor on "not
# preceded/followed by another digit" instead, which still keeps this from
# matching the middle of a longer digit run.
_YEAR_LIKE = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _blank(v) -> bool:
    return v is None or str(v).strip() == ""


def _is_placeholder_supervisor(v) -> bool:
    low = str(v).lower()
    return any(p in low for p in _SUPERVISOR_PLACEHOLDERS)


def _is_effectively_blank(field: str, value) -> bool:
    """Field-aware emptiness — mirrors data_quality/validators.py so 'blank'
    means the same thing here as it does in the scan that produced
    MISSING_ORG / MISSING_SUPERVISOR / MISSING_SPECIALTY / MISSING_DATE."""
    if _blank(value):
        return True
    if field == "ilmiy_rahbar":
        return _is_placeholder_supervisor(value)
    return False


def date_hint(value) -> Optional[str]:
    """Matnda sanaga o'xshash narsa bormi — to'liq dd.mm.yyyy, aks holda
    yolg'iz 4 xonali yil. Faqat ma'lumot uchun, hech narsani hal qilmaydi."""
    s = str(value)
    m = _DATE_LIKE.search(s)
    if m:
        return m.group(0)
    m = _YEAR_LIKE.search(s)
    return m.group(0) if m else None


def looks_like_degree_text(value) -> bool:
    if _blank(value) or _is_placeholder_supervisor(value):
        return False
    return bool(_DEGREE_TEXT_HINT.search(str(value)))


# ── tahlil (DB dan mustaqil — sinov uchun) ──────────────────────────────────
def analyze(rows: Iterable[Dict], sample_cap: int = 15) -> Dict:
    """Bitta o'tishda ikkala tahlilni ham bajaradi. Hech narsa yozmaydi,
    faqat sanaydi va namunalarni yig'adi."""
    total = 0
    blank_count = {f: 0 for f in TARGET_FIELDS}
    sibling_nonblank = {f: {c: 0 for c in SIBLING_CANDIDATES[f]} for f in TARGET_FIELDS}
    sibling_date_like = {c: 0 for c in SIBLING_CANDIDATES["sana"]}
    samples = {f: {c: [] for c in SIBLING_CANDIDATES[f]} for f in TARGET_FIELDS}

    degree_in_name = 0
    degree_in_name_daraja_filled = 0
    degree_in_name_daraja_blank = 0
    degree_samples: List[Dict] = []

    for row in rows:
        total += 1

        for field in TARGET_FIELDS:
            if not _is_effectively_blank(field, row.get(field)):
                continue
            blank_count[field] += 1
            for col in SIBLING_CANDIDATES[field]:
                sib_val = row.get(col)
                if _blank(sib_val):
                    continue
                sibling_nonblank[field][col] += 1
                if field == "sana" and date_hint(sib_val):
                    sibling_date_like[col] += 1
                if len(samples[field][col]) < sample_cap:
                    samples[field][col].append({"id": row.get("id"), "value": sib_val})

        rahbar_val = row.get("ilmiy_rahbar")
        if looks_like_degree_text(rahbar_val):
            degree_in_name += 1
            daraja_val = row.get("ilmiy_rahbar_daraja")
            if _blank(daraja_val):
                degree_in_name_daraja_blank += 1
            else:
                degree_in_name_daraja_filled += 1
            if len(degree_samples) < sample_cap:
                degree_samples.append({"id": row.get("id"), "ilmiy_rahbar": rahbar_val,
                                       "ilmiy_rahbar_daraja": daraja_val})

    return {
        "total": total,
        "blank_count": blank_count,
        "sibling_nonblank": sibling_nonblank,
        "sibling_date_like": sibling_date_like,
        "samples": samples,
        "degree_in_name": degree_in_name,
        "degree_in_name_daraja_filled": degree_in_name_daraja_filled,
        "degree_in_name_daraja_blank": degree_in_name_daraja_blank,
        "degree_samples": degree_samples,
    }


# ── ma'lumotlar bazasi (faqat o'qish) ───────────────────────────────────────
def iter_rows(conn, limit: Optional[int] = None, record_id: Optional[int] = None,
             batch_size: int = 1000) -> Iterator[Dict]:
    """dissertations dan SELECT COLUMNS ni oqim sifatida qaytaradi (server-side
    cursor — 29k+ qatorni xotiraga to'liq yuklamaslik uchun). Faqat SELECT."""
    cols = ", ".join(SELECT_COLUMNS)
    if record_id is not None:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {cols} FROM dissertations WHERE id = %s", (record_id,))
            row = cur.fetchone()
            if row:
                yield dict(zip(SELECT_COLUMNS, row))
        return

    sql = f"SELECT {cols} FROM dissertations ORDER BY id"
    params: Tuple = ()
    if limit is not None:
        sql += " LIMIT %s"
        params = (limit,)

    cur = conn.cursor(name="sibling_scan_cursor")
    cur.itersize = batch_size
    try:
        cur.execute(sql, params)
        for row in cur:
            yield dict(zip(SELECT_COLUMNS, row))
    finally:
        cur.close()


# ── hisobot ─────────────────────────────────────────────────────────────────
def _md_cell(value, limit: int = 100) -> str:
    s = truncate(str(value if value is not None else "").replace("|", "\\|"), limit)
    return s or "—"


def _sibling_table(result: Dict, field: str) -> List[str]:
    lines = ["| Qo'shni ustun | Bo'sh " + FIELD_LABELS[field].lower()
             + " qatorlarida to'ldirilgan | % (bo'shlardan) |",
             "|---|---:|---:|"]
    blank = result["blank_count"][field]
    for col in SIBLING_CANDIDATES[field]:
        n = result["sibling_nonblank"][field][col]
        pct = (100.0 * n / blank) if blank else 0.0
        extra = ""
        if field == "sana":
            dl = result["sibling_date_like"][col]
            extra = f" ({dl} tasi sana/yilga o'xshaydi)"
        lines.append(f"| `{col}` | **{n}**{extra} | {pct:.1f}% |")
    return lines


def _sample_tables(result: Dict, field: str) -> List[str]:
    lines: List[str] = []
    for col in SIBLING_CANDIDATES[field]:
        items = result["samples"][field][col]
        if not items:
            continue
        lines += [
            "",
            f"**Namunalar** — `{field}` bo'sh, `{col}` to'ldirilgan:",
            "",
            f"| id | {col} |", "|---|---|",
        ]
        for it in items:
            lines.append(f"| {it['id']} | {_md_cell(it['value'])} |")
    return lines


def build_report(result: Dict, target: str) -> str:
    lines = [
        f"# Qo'shni ustunlar tahlili — {datetime.now():%Y-%m-%d %H:%M}",
        "",
        "**FAQAT TAHLIL. Bazaga hech narsa yozilmadi. To'ldirish qoidalari bu "
        "yerda TANLANMAGAN** — quyida faqat nima borligi ko'rsatilgan; qaysi "
        "ustundan qaysi maydonga, qanday shartda ko'chirish kerakligi keyingi "
        "bosqichda alohida tasdiqlanadi.",
        "",
        f"* baza: `{target}` (faqat SELECT)",
        f"* skanerlangan yozuvlar: **{result['total']}**",
        "",
        "## 1. Bo'sh maydonlar va qo'shni ustunlar",
        "",
    ]

    for i, field in enumerate(TARGET_FIELDS, 1):
        blank = result["blank_count"][field]
        pct_total = (100.0 * blank / result["total"]) if result["total"] else 0.0
        lines += [
            f"### 1.{i} {FIELD_LABELS[field]} "
            f"(bo'sh: {blank} / {result['total']}, {pct_total:.1f}%)",
            "",
        ]
        lines += _sibling_table(result, field)
        lines += _sample_tables(result, field)
        lines.append("")

    lines += [
        "## 2. `ilmiy_rahbar` ustunida ism o'rniga daraja matni",
        "",
        "Taxmin: parser ba'zan `ilmiy_rahbar` (ism) va `ilmiy_rahbar_daraja` "
        "(daraja) ustunlarini chalkashtirgan — ism o'rniga daraja matni "
        "yozilgan (masalan «педагогика фанлари доктори (DSc)»).",
        "",
        f"* Aniqlangan holatlar: **{result['degree_in_name']}** ta "
        "(`ilmiy_rahbar` bo'sh emas, lekin ism emas — daraja matniga o'xshaydi)",
        f"* Shundan `ilmiy_rahbar_daraja` HAM to'ldirilgan (ehtimol takror): "
        f"**{result['degree_in_name_daraja_filled']}** ta",
        f"* Shundan `ilmiy_rahbar_daraja` BO'SH (ehtimol chalkashgan — daraja "
        f"noto'g'ri ustunda): **{result['degree_in_name_daraja_blank']}** ta",
        "",
    ]
    if result["degree_samples"]:
        lines += [
            "**Namunalar:**",
            "",
            "| id | ilmiy_rahbar (hozirgi) | ilmiy_rahbar_daraja (hozirgi) |",
            "|---|---|---|",
        ]
        for s in result["degree_samples"]:
            lines.append(f"| {s['id']} | {_md_cell(s['ilmiy_rahbar'])} | "
                         f"{_md_cell(s['ilmiy_rahbar_daraja'])} |")
    else:
        lines.append("Bunday holat topilmadi.")

    lines += [
        "",
        "## Keyingi qadam",
        "",
        "Bu hisobot hech narsani o'zgartirmaydi va biror to'ldirish qoidasini "
        "taklif qilmaydi. Yuqoridagi sonlar va namunalarni ko'rib chiqing; "
        "2-BOSQICH (to'ldirish) tasdiqlangan qoidalar asosida, "
        "`data_quality_fix_log` orqali, `--dry-run` default va rollback bilan "
        "amalga oshiriladi.",
        "",
    ]
    return "\n".join(lines)


def write_samples_csv(path: str, result: Dict) -> None:
    """Ixtiyoriy: sinovda (--csv-out) barcha namunalarni bitta CSV ga yozadi —
    hisobotdagi sample-cap chegarasidan tashqarida ham tekshirish uchun."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kind", "field", "sibling_column", "id", "value", "value2"])
        for field in TARGET_FIELDS:
            for col in SIBLING_CANDIDATES[field]:
                for it in result["samples"][field][col]:
                    w.writerow(["sibling_fill", field, col, it["id"], it["value"], ""])
        for s in result["degree_samples"]:
            w.writerow(["degree_in_name", "ilmiy_rahbar", "ilmiy_rahbar_daraja",
                        s["id"], s["ilmiy_rahbar"], s["ilmiy_rahbar_daraja"]])


# ── CLI ─────────────────────────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Bo'sh maydonlarni qo'shni ustunlar bilan solishtirish "
                    "(faqat hisobot — hech narsa yozmaydi)")
    p.add_argument("--limit", type=int, default=None,
                   help="faqat birinchi N yozuv (tez sinov uchun)")
    p.add_argument("--record-id", type=int, default=None, help="faqat bitta yozuv")
    p.add_argument("--sample-cap", type=int, default=15,
                   help="har bir jadvaldagi maksimal namuna soni")
    p.add_argument("--out", default=DEFAULT_OUT, help="hisobot fayli (.md)")
    p.add_argument("--csv-out", default=None,
                   help="ixtiyoriy: namunalarni CSV ga ham yozadi")
    p.add_argument("--database-url", default=None,
                   help="DB URL (aks holda DATA_QUALITY_/TEST_/DATABASE_URL)")
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    conn, target = resolve_connection(args.database_url)
    print(f"→ Baza: {target}   (FAQAT O'QISH — hech narsa yozilmaydi)")

    try:
        rows = list(iter_rows(conn, limit=args.limit, record_id=args.record_id))
        conn.rollback()          # o'qish tranzaksiyasini yopamiz
    finally:
        try:
            conn.close()
        except Exception:
            pass

    log(f"{len(rows)} ta yozuv o'qildi")
    result = analyze(rows, sample_cap=args.sample_cap)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(build_report(result, target))

    if args.csv_out:
        write_samples_csv(args.csv_out, result)

    log(f"Tayyor: {result['total']} yozuv tekshirildi, "
        f"{result['degree_in_name']} ta ilmiy_rahbar'da daraja matni topildi")
    print(f"\n  {args.out}")
    if args.csv_out:
        print(f"  {args.csv_out}")
    print("\n(bazaga hech narsa yozilmadi)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
