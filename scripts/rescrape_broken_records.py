"""Buzuq yozuvlarni OAK manba sahifasidan qayta o'qish va tiklash.

DEFAULT REJIM — FAQAT HISOBOT (1-faza): skript faqat SELECT qiladi, sahifalarni
yuklab keshlaydi, tahlil qiladi va hisobot chiqaradi. Bazaga hech narsa
yozilmaydi.

--apply --confirm — 2-FAZA (yozish): faqat FILL lar qo'llanadi. Ikkala bayroq
ham majburiy; bittasi yetmaydi. Bu modulning o'zida yozuvchi SQL yo'q (test
bilan tekshiriladi) — har bir UPDATE data_quality/repository.py orqali,
ustunlar ro'yxati (SCOPE_RESCRAPE) bilan cheklangan holda bajariladi va
data_quality_fix_log ga before/after bo'lib yoziladi:

    * FAQAT `FILL`: baza maydoni bo'sh, sahifada qiymat bor. Mavjud qiymat
      hech qachon ustidan yozilmaydi.
    * `CONFLICT` hech qachon avtomatik yozilmaydi — faqat hisobotga chiqadi.
    * Yozishdan oldin maydon HOZIR ham bo'shligi tranzaksiya ichida qayta
      tekshiriladi (hisobot olingandan keyin kimdir to'ldirgan bo'lishi mumkin).
    * Har bir yozuv bitta tranzaksiya: fix_log qatorlari UPDATE lardan OLDIN,
      hammasi birga commit bo'ladi yoki hech biri.
    * Bitta run dagi barcha o'zgarishlar run_type='rescrape' run'ni bo'lishadi;
      oxirida rollback komandasi chop etiladi.

Yozuvlar tanlash: berilgan scan run_id dagi strukturaviy nuqsonlar bo'yicha

    SELECT DISTINCT record_id FROM data_quality_issues
    WHERE run_id = %s AND issue_code IN (...)

Parsing scraper.py dagi mavjud OAK mantiqini qayta ishlatadi
(parse_announcement / extract_field / detect_degree / extract_author_from_title)
— u OAK ning kirill yorliqlariga (Диссертация мавзуси, Илмий раҳбар, ...)
qarab ishlaydi, DOM strukturasiga emas, shuning uchun sahifa dizayni
o'zgarsa ham chidamli.

Har bir maydon uchun uchta holat:
    MATCH            bazadagi qiymat = sahifadagi qiymat (normalizatsiyadan keyin)
    FILL             baza bo'sh, sahifada qiymat bor          ← asosiy maqsad
    CONFLICT         ikkalasida ham qiymat bor, lekin farq qiladi
va qo'shimcha: MISSING_ON_PAGE (sahifada ham yo'q), EMPTY_BOTH.
CONFLICT hech qachon avtomatik hal qilinmaydi — faqat ro'yxatga chiqadi.

Usage (server yoki WSL, repo root):
    export TEST_DATABASE_URL=postgresql://…/olimlar_staging_db
    python3 scripts/rescrape_broken_records.py --run-id 12
    python3 scripts/rescrape_broken_records.py --run-id 12 --limit 20
    python3 scripts/rescrape_broken_records.py --run-id 12 --refresh
    python3 scripts/rescrape_broken_records.py --inspect 44321   # kesh tahlili

    # FILL larni qo'llash (keshdan ishlaydi — avval hisobotni ko'ring):
    python3 scripts/rescrape_broken_records.py --run-id 12 --apply --confirm

Qaytarish (har qanday run uchun bir xil):
    python3 scripts/rollback_data_quality_fixes.py --run-id <rescrape run> --confirm

Resume: yuklangan HTML data/rescrape_cache/<page_id>.html da qoladi va
_fetch_log.json fetch natijalarini yozadi. Qayta ishga tushirilsa keshdagi
sahifalar qayta yuklanmaydi (--refresh majburlaydi), 404/410 doimiy xato deb
belgilanib qayta urinilmaydi, timeout/5xx esa keyingi runda qayta uriniladi.
Ya'ni uzilib qolgan run xuddi to'xtagan joyidan davom etadi.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import re
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

from data_quality import normalizers as N  # noqa: E402
from data_quality import repository as repo  # noqa: E402
from data_quality.cli_common import (  # noqa: E402
    require_manual_transactions, resolve_connection, truncate)
from scraper import (  # noqa: E402  — mavjud OAK parsing mantiqi
    detect_degree, extract_author_from_title, parse_announcement)
from utils.transliterate import kiril_to_lotin  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(REPO_ROOT, "data", "rescrape_cache")
FETCH_LOG = os.path.join(CACHE_DIR, "_fetch_log.json")
DOCS_DIR = os.path.join(REPO_ROOT, "docs")

# Strukturaviy nuqsonlar — sahifadan qayta o'qish mantiqiy bo'lganlari.
DEFAULT_CODES = (
    "MISSING_TITLE", "MISSING_AUTHOR", "MISSING_DATE", "MISSING_SPECIALTY",
    "PLACEHOLDER_SUPERVISOR", "SHORT_TITLE",
)

DELAY_SECONDS = 1.5
TIMEOUT = 25
RETRIES = 3
BACKOFF = 4
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; olimlar.uz-rescrape/1.0; "
                  "+https://olimlar.uz) data-quality repair bot",
}
# oak.uz/pages/<raqam> — boshqa hech qanday hostdan yuklamaymiz.
_PAGE_URL = re.compile(r"^https?://(?:www\.)?oak\.uz/pages/(\d+)", re.IGNORECASE)
_DATE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")
# Sahifadagi himoya sanasi yorlig'i (bo'lsa — undan olamiz, aks holda birinchi sana).
_DATE_LABEL = re.compile(
    r"(?:ҳимоя|химоя|himoya)\s*(?:қилиш|qilish)?\s*(?:санаси|sanasi|куни)?\s*:?\s*"
    r"(\d{2}\.\d{2}\.\d{4})", re.IGNORECASE)

MATCH, FILL, CONFLICT = "MATCH", "FILL", "CONFLICT"
MISSING_ON_PAGE, EMPTY_BOTH = "MISSING_ON_PAGE", "EMPTY_BOTH"

# 2-faza (yozish) doimiylari.
RUN_TYPE = "rescrape"
FIX_TABLE = "dissertations"
SCOPE = repo.SCOPE_RESCRAPE
# CSV/hisobotdagi qo'llash holatlari.
APPLIED = "APPLIED"
SKIPPED_NOT_EMPTY, SKIPPED_GONE, FAILED = "SKIPPED_NOT_EMPTY", "SKIPPED_GONE", "FAILED"
# Manba muammosi topilmagan FILL uchun fix_log dagi kod.
GENERIC_FILL_CODE = "RESCRAPE_FILL"


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ── maydon xaritasi ─────────────────────────────────────────────────────────
def _norm_text(v: str) -> str:
    return N.normalize_whitespace(N.normalize_apostrophes(v or "")).strip().lower()


def _norm_specialties(v: str) -> str:
    """'05.01.02; 05.01.04' → tartiblangan, normalizatsiyalangan to'plam."""
    codes = [N.normalize_specialty_code(t.strip())
             for t in re.split(r"[;,\n]", v or "") if t.strip()]
    return ", ".join(sorted({c for c in codes if c}))


def _norm_date(v: str) -> str:
    return N.normalize_date(v) or _norm_text(v)


def _norm_person(v: str) -> str:
    return _norm_text(N.normalize_person_name(v or ""))


def _norm_org(v: str) -> str:
    return _norm_text(N.normalize_organization_name(v or ""))


def _norm_title(v: str) -> str:
    return _norm_text(N.normalize_title(v or ""))


def _norm_degree(v: str) -> str:
    return (N.normalize_degree(v or "") or "").strip()


# (db ustuni, sahifa kaliti, taqqoslash normalizatori, hisobotdagi nom)
FIELDS: Tuple[Tuple[str, str, callable, str], ...] = (
    ("mavzu", "mavzu", _norm_title, "Mavzu"),
    ("olim", "olim", _norm_person, "Olim"),
    ("sana", "sana", _norm_date, "Himoya sanasi"),
    ("ixtisoslik", "ixtisoslik", _norm_specialties, "Ixtisoslik"),
    ("ilmiy_rahbar", "ilmiy_rahbar", _norm_person, "Ilmiy rahbar"),
    ("muassasa", "muassasa", _norm_org, "Muassasa"),
    ("daraja", "daraja", _norm_degree, "Daraja"),
)

# Ilmiy rahbar maydonida shablon qolgan bo'lsa, u "to'ldirilgan" emas — bo'sh
# deb qaraladi, aks holda PLACEHOLDER_SUPERVISOR yozuvlari CONFLICT bo'lib
# ketardi (validators.py bilan bir xil ro'yxat).
_PLACEHOLDERS = ("ф.и.ш", "f.i.sh", "ф.и.о", "фиш",
                 "ининг ф", "ининг f", "нинг ф.и", "нинг f.i")


def is_effectively_empty(field: str, value) -> bool:
    if value is None or str(value).strip() == "":
        return True
    if field == "ilmiy_rahbar":
        low = str(value).lower()
        return any(p in low for p in _PLACEHOLDERS)
    return False


# ── manba sahifasi ──────────────────────────────────────────────────────────
def page_id_from_link(link: Optional[str]) -> Optional[str]:
    m = _PAGE_URL.match((link or "").strip())
    return m.group(1) if m else None


def cache_path(page_id: str) -> str:
    return os.path.join(CACHE_DIR, f"{page_id}.html")


def load_fetch_log() -> Dict[str, dict]:
    if not os.path.exists(FETCH_LOG):
        return {}
    try:
        with open(FETCH_LOG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        log("  !! _fetch_log.json o'qib bo'lmadi — bo'shdan boshlanadi")
        return {}


def save_fetch_log(entries: Dict[str, dict]) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = FETCH_LOG + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, FETCH_LOG)          # yarim yozilgan fayl qolmasin


def fetch_page(page_id: str, fetch_log: Dict[str, dict],
               refresh: bool = False) -> Tuple[Optional[str], Optional[str]]:
    """(html, xato) qaytaradi. Keshda bo'lsa tarmoqqa chiqmaydi.

    Qaytarilgan ikkinchi qiymat None bo'lsa — muvaffaqiyat.
    """
    path = cache_path(page_id)
    if os.path.exists(path) and not refresh:
        with open(path, encoding="utf-8") as f:
            return f.read(), None

    prev = fetch_log.get(page_id, {})
    if prev.get("permanent") and not refresh:
        # 404/410 — sahifa yo'q. Qayta urinish foydasiz.
        return None, prev.get("error", "permanent error")

    url = f"https://oak.uz/pages/{page_id}"
    delay = BACKOFF
    last_error = "noma'lum xato"
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                os.makedirs(CACHE_DIR, exist_ok=True)
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(r.text)
                fetch_log[page_id] = {"status": "ok", "permanent": False,
                                      "fetched_at": datetime.now().isoformat(timespec="seconds")}
                return r.text, None
            if r.status_code in (404, 410):
                last_error = f"HTTP {r.status_code} — sahifa yo'q"
                fetch_log[page_id] = {"status": r.status_code, "permanent": True,
                                      "error": last_error,
                                      "fetched_at": datetime.now().isoformat(timespec="seconds")}
                return None, last_error
            last_error = f"HTTP {r.status_code}"
        except requests.Timeout:
            last_error = f"timeout ({TIMEOUT}s)"
        except requests.RequestException as e:                 # 5xx, DNS, TLS…
            last_error = f"{e.__class__.__name__}: {str(e)[:120]}"

        if attempt < RETRIES:
            log(f"  .. qayta urinish {attempt}/{RETRIES} ({last_error})")
            time.sleep(delay)
            delay *= 2

    # Vaqtinchalik xato — keyingi runda yana uriniladi (permanent=False).
    fetch_log[page_id] = {"status": "error", "permanent": False,
                          "error": last_error,
                          "fetched_at": datetime.now().isoformat(timespec="seconds")}
    return None, last_error


# ── parsing ─────────────────────────────────────────────────────────────────
def page_text(html: str) -> str:
    """Sahifaning ma'noli matni (nav/skript/futer olib tashlangan)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()
    main = (soup.find("main") or soup.find("article")
            or soup.find(attrs={"class": re.compile(r"content|single|detail", re.I)})
            or soup.body or soup)
    return main.get_text("\n", strip=True)


def page_title(html: str) -> str:
    """E'lon sarlavhasi: h1/h2/h3, keyin og:title, keyin <title>."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in ("h1", "h2", "h3"):
        el = soup.find(tag)
        if el:
            text = el.get_text(" ", strip=True)
            if text:
                return re.sub(r"\s+", " ", text)
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        return re.sub(r"\s+", " ", og["content"].strip())
    if soup.title and soup.title.string:
        return re.sub(r"\s+", " ", soup.title.string.strip())
    return ""


def extract_defense_date(text: str) -> str:
    """Yorliqli sana bo'lsa — o'shani, aks holda matndagi birinchi dd.mm.yyyy."""
    m = _DATE_LABEL.search(text)
    if m:
        return m.group(1)
    m = _DATE_RE.search(text)
    return m.group(1) if m else ""


def parse_page(html: str) -> Dict[str, str]:
    """OAK e'lon sahifasidan maydonlarni ajratadi (scraper.py mantiqi bilan)."""
    text = page_text(html)
    title = page_title(html)
    parsed = parse_announcement(text)

    daraja = parsed.get("daraja") or detect_degree(title)
    return {
        "mavzu": parsed.get("mavzu", ""),
        "olim": extract_author_from_title(title),
        "sana": extract_defense_date(text),
        "ixtisoslik": ", ".join(parsed.get("ixtisoslik_shifrlari", [])),
        "ilmiy_rahbar": parsed.get("ilmiy_rahbar", ""),
        "muassasa": parsed.get("bajarilgan_muassasa", ""),
        "daraja": daraja,
        "_title": title,
    }


def values_agree(normalizer, db_value: str, page_value: str) -> Tuple[bool, str]:
    """Ikki qiymat bir xilmi. (mos_keladi, izoh) qaytaradi.

    Avval maydonning o'z normalizatori bilan (tutuq belgisi, ortiqcha probel,
    sana formati kabi farqlar CONFLICT bo'lib ko'rinmasin), keyin alifboga
    sezgir bo'lmagan holda. Baza kirillcha, OAK sahifasi ham kirillcha — lekin
    ayrim yozuvlar lotinda, va alifbo farqi haqiqiy ziddiyat emas.
    """
    a, b = normalizer(str(db_value)), normalizer(str(page_value))
    if a == b:
        return True, ""
    if kiril_to_lotin(a) == kiril_to_lotin(b):
        return True, "faqat alifbo farqi (kiril/lotin)"
    return False, ""


def compare_record(db_row: Dict, page_values: Dict[str, str]) -> List[Dict]:
    """Har bir maydon uchun holat aniqlaydi. Hech narsani o'zgartirmaydi."""
    out: List[Dict] = []
    for column, key, normalizer, label in FIELDS:
        db_value = db_row.get(column)
        page_value = page_values.get(key, "")
        db_empty = is_effectively_empty(column, db_value)
        page_empty = is_effectively_empty(column, page_value)
        note = ""

        if db_empty and page_empty:
            status = EMPTY_BOTH
        elif db_empty:
            status = FILL
        elif page_empty:
            status = MISSING_ON_PAGE
        else:
            agree, note = values_agree(normalizer, db_value, page_value)
            status = MATCH if agree else CONFLICT

        out.append({
            "record_id": db_row.get("id"), "field": column, "label": label,
            "status": status, "note": note,
            "db_value": "" if db_value is None else str(db_value),
            "page_value": page_value,
        })
    return out


# ── ma'lumotlar bazasi (faqat o'qish) ───────────────────────────────────────
def select_broken_record_ids(conn, run_id: int, codes: Tuple[str, ...]) -> List[int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT record_id FROM data_quality_issues "
            "WHERE run_id = %s AND issue_code = ANY(%s) ORDER BY record_id",
            (run_id, list(codes)))
        return [r[0] for r in cur.fetchall()]


def fetch_rows(conn, record_ids: List[int]) -> Dict[int, Dict]:
    if not record_ids:
        return {}
    cols = ("id", "sana", "daraja", "olim", "mavzu", "ixtisoslik", "muassasa",
            "ilmiy_rahbar", "link")
    with conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(cols)} FROM dissertations "
                    "WHERE id = ANY(%s)", (record_ids,))
        return {row[0]: dict(zip(cols, row)) for row in cur.fetchall()}


def fetch_open_issues(conn, run_id: int,
                      record_ids: List[int]) -> Dict[Tuple[int, str], Dict]:
    """(record_id, maydon) → {'id', 'code'} — FILL qilinayotgan maydonning
    manba muammosi. Shu tufayli qo'llashda issue 'fixed' bo'ladi, rollback esa
    uni qayta 'open' qiladi. Faqat SELECT."""
    if not record_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, record_id, field_name, issue_code "
            "FROM data_quality_issues "
            "WHERE run_id = %s AND record_id = ANY(%s) AND status = 'open' "
            "ORDER BY id",
            (run_id, list(record_ids)))
        out: Dict[Tuple[int, str], Dict] = {}
        for issue_id, rid, field, code in cur.fetchall():
            out.setdefault((rid, field), {"id": issue_id, "code": code})
        return out


# ── qo'llash (2-faza — yagona yozish yo'li) ─────────────────────────────────
def collect_fills(findings: List[Dict]) -> List[Dict]:
    """Faqat FILL. CONFLICT / MATCH / MISSING_ON_PAGE / EMPTY_BOTH bu yerga
    hech qachon tushmaydi — qo'llash mantiqiga o'tadigan yagona darvoza shu."""
    return [f for f in findings
            if f["status"] == FILL and str(f.get("page_value") or "").strip()]


def _group_by_record(fills: List[Dict]) -> List[Tuple[int, List[Dict]]]:
    grouped: Dict[int, List[Dict]] = {}
    for f in fills:
        grouped.setdefault(f["record_id"], []).append(f)
    return sorted(grouped.items())


def apply_fills(conn, fills: List[Dict], *, scan_run_id: int, target: str,
                actor: str, issues: Dict[Tuple[int, str], Dict]) -> Dict:
    """FILL larni qo'llaydi va har birini data_quality_fix_log ga yozadi.

    Bitta YOZUV = bitta tranzaksiya: shu yozuvning barcha fix_log qatorlari
    o'z UPDATE laridan OLDIN yoziladi va hammasi birga commit bo'ladi. Yarim
    to'ldirilgan yozuv qolmaydi.

    Yozishdan oldin maydon hozir ham bo'shligi qayta tekshiriladi: hisobot
    olingandan beri (sahifalarni yuklash bir necha daqiqa davom etadi) kimdir
    to'ldirgan bo'lsa, o'sha maydon o'tkazib yuboriladi — mavjud qiymat hech
    qachon ustidan yozilmaydi.
    """
    by_record = _group_by_record(fills)
    stats = {"records": len(by_record), "applied": 0, "skipped": 0,
             "failed_records": 0, "run_id": None}
    fix_run_id = None
    run_uncommitted = False

    for record_id, items in by_record:
        try:
            if fix_run_id is None:
                # Birinchi yozuvning tranzaksiyasi ichida yaratamiz — qo'llash
                # bo'lmasa bo'sh run qatori qolmasin.
                fix_run_id = repo.create_run(
                    conn, RUN_TYPE, total_records=len(by_record),
                    metadata={"mode": "apply", "source": "oak-rescrape",
                              "scan_run_id": scan_run_id, "target": target,
                              "applied_by": actor})
                run_uncommitted = True

            wrote = 0
            for item in items:
                column, new_value = item["field"], item["page_value"]
                found, current = repo.fetch_current_value(
                    conn, record_id, column, table_name=FIX_TABLE, scope=SCOPE)
                if not found:
                    item["apply_status"] = SKIPPED_GONE
                    log(f"  !! {record_id}: yozuv topilmadi — o'tkazildi")
                    continue
                if not is_effectively_empty(column, current):
                    # Hisobotdan keyin to'ldirilgan — bu FILL emas.
                    item["apply_status"] = SKIPPED_NOT_EMPTY
                    log(f"  !! {record_id}.{column}: endi bo'sh emas "
                        f"({truncate(current, 40)!r}) — o'tkazildi")
                    continue

                issue = issues.get((record_id, column))
                repo.log_fix(conn, run_id=fix_run_id,
                             issue_id=issue["id"] if issue else None,
                             record_id=record_id, table_name=FIX_TABLE,
                             column_name=column, value_before=current,
                             value_after=new_value,
                             issue_code=(issue["code"] if issue else GENERIC_FILL_CODE),
                             applied_by=actor, scope=SCOPE)
                affected = repo.update_record_field(
                    conn, record_id, column, new_value,
                    table_name=FIX_TABLE, scope=SCOPE)
                if affected != 1:
                    raise RuntimeError(f"{affected} qator o'zgardi, 1 kutilgandi")
                if issue:
                    repo.record_issue_values(conn, issue["id"], current, new_value)
                    repo.mark_issue_fixed(conn, issue["id"])
                item["apply_status"] = APPLIED
                wrote += 1

            if wrote:
                conn.commit()
                run_uncommitted = False
                stats["applied"] += wrote
            else:
                # Yozadigan narsa qolmadi — ochilgan tranzaksiyani (va u bilan
                # birga hali commit qilinmagan run qatorini) bekor qilamiz.
                conn.rollback()
                if run_uncommitted:
                    fix_run_id, run_uncommitted = None, False
            stats["skipped"] += sum(
                1 for i in items
                if i.get("apply_status") in (SKIPPED_NOT_EMPTY, SKIPPED_GONE))
        except Exception as exc:                 # noqa: BLE001
            conn.rollback()
            if run_uncommitted:
                fix_run_id, run_uncommitted = None, False
            for item in items:
                if item.get("apply_status") in (None, APPLIED):
                    item["apply_status"] = FAILED
            stats["failed_records"] += 1
            log(f"  !! {record_id}: qo'llanmadi ({exc.__class__.__name__}) — "
                "hech narsa yozilmadi")

    if fix_run_id is not None:
        repo.complete_run(conn, fix_run_id, status="completed",
                          checked_records=stats["records"],
                          issue_count=stats["applied"], duplicate_count=0)
        conn.commit()
    stats["run_id"] = fix_run_id
    return stats


# ── hisobot ─────────────────────────────────────────────────────────────────
def build_report(findings: List[Dict], errors: List[Dict], stats: Dict,
                 run_id: int, target: str, applied: Optional[Dict] = None) -> str:
    by_field: Dict[str, Dict[str, int]] = {}
    for f in findings:
        bucket = by_field.setdefault(f["field"], {})
        bucket[f["status"]] = bucket.get(f["status"], 0) + 1

    conflicts = [f for f in findings if f["status"] == CONFLICT]
    fills = [f for f in findings if f["status"] == FILL]

    banner = ("**1-FAZA: faqat o'qish va hisobot. Bazaga hech narsa yozilmadi.**"
              if applied is None else
              f"**2-FAZA: FILL lar qo'llandi** — {applied['applied']} ta maydon "
              f"yozildi, run_id=`{applied['run_id']}`. CONFLICT lar "
              "tegilmadi.")
    lines = [
        f"# Rescrape hisoboti — {datetime.now():%Y-%m-%d %H:%M}",
        "",
        banner,
        "",
        f"* manba scan: `run_id={run_id}`",
        f"* baza: `{target}` "
        + ("(faqat SELECT)" if applied is None else "(FILL lar yozildi)"),
        f"* issue kodlari: {', '.join(stats['codes'])}",
        "",
        "## Umumiy",
        "",
        "| | |",
        "|---|---:|",
        f"| Tanlangan yozuvlar | {stats['selected']} |",
        f"| Havolasi yaroqli (oak.uz/pages/N) | {stats['with_page']} |",
        f"| Muvaffaqiyatli o'qildi | {stats['ok']} |",
        f"| Keshdan olindi (tarmoqsiz) | {stats['from_cache']} |",
        f"| Yangi yuklandi | {stats['downloaded']} |",
        f"| Xato | {len(errors)} |",
        "",
        "## Maydon bo'yicha natija",
        "",
        "| Maydon | FILL (tiklanadi) | CONFLICT | MATCH | Sahifada ham yo'q | Ikkalasi bo'sh |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for column, _key, _norm, label in FIELDS:
        b = by_field.get(column, {})
        lines.append(
            f"| {label} | **{b.get(FILL, 0)}** | {b.get(CONFLICT, 0)} | "
            f"{b.get(MATCH, 0)} | {b.get(MISSING_ON_PAGE, 0)} | "
            f"{b.get(EMPTY_BOTH, 0)} |")

    script_only = sum(1 for f in findings if f.get("note"))
    found_phrase = ("manbadan tiklanishi mumkin" if applied is None
                    else "topildi (qo'llash natijasi quyida)")
    lines += [
        "",
        f"Jami **{len(fills)}** ta bo'sh maydon {found_phrase}, "
        f"**{len(conflicts)}** ta ziddiyat qo'lda ko'rib chiqilishi kerak.",
        "",
    ]
    if script_only:
        lines += [
            f"> {script_only} ta maydon MATCH deb hisoblandi, chunki farq faqat "
            "alifboda (kiril/lotin) edi — CSV dagi `note` ustunida ko'rinadi.",
            "",
        ]
    lines += [
        "## CONFLICT — qo'lda hal qilinadi",
        "",
    ]
    if conflicts:
        lines += ["Avtomatik hal qilinmaydi. Eski = bazadagi, Yangi = sahifadagi.",
                  "",
                  "| id | Maydon | Bazadagi (eski) | Sahifadagi (yangi) |",
                  "|---|---|---|---|"]
        for c in sorted(conflicts, key=lambda x: (x["record_id"], x["field"])):
            lines.append(
                f"| {c['record_id']} | {c['label']} | "
                f"{_md_cell(c['db_value'])} | {_md_cell(c['page_value'])} |")
    else:
        lines.append("Ziddiyat topilmadi.")

    if applied is not None:
        skipped_items = [f for f in fills
                         if f.get("apply_status") in (SKIPPED_NOT_EMPTY,
                                                      SKIPPED_GONE, FAILED)]
        lines += [
            "",
            "## Qo'llash natijasi",
            "",
            "| | |",
            "|---|---:|",
            f"| Yozuvlar (tranzaksiyalar) | {applied['records']} |",
            f"| Yozilgan maydonlar | **{applied['applied']}** |",
            f"| O'tkazib yuborilgan | {applied['skipped']} |",
            f"| Xato bergan yozuvlar | {applied['failed_records']} |",
            f"| Rescrape run_id | `{applied['run_id']}` |",
            "",
            "Qaytarish:",
            "",
            "```bash",
            f"python3 scripts/rollback_data_quality_fixes.py "
            f"--run-id {applied['run_id']} --confirm",
            "```",
            "",
        ]
        if skipped_items:
            lines += [
                "O'tkazib yuborilganlar (hisobotdan keyin o'zgargan yoki xato):",
                "",
                "| id | Maydon | Sabab |", "|---|---|---|",
            ]
            for s in sorted(skipped_items, key=lambda x: (x["record_id"], x["field"])):
                lines.append(f"| {s['record_id']} | {s['label']} | "
                             f"{s['apply_status']} |")
            lines.append("")

    lines += ["", "## Tiklab bo'lmaganlar", ""]
    if errors:
        lines += ["| id | page_id | Sabab |", "|---|---|---|"]
        for e in sorted(errors, key=lambda x: x["record_id"]):
            lines.append(f"| {e['record_id']} | {e.get('page_id') or '—'} | "
                         f"{_md_cell(e['reason'])} |")
    else:
        lines.append("Hammasi o'qildi.")

    lines += ["", "## Keyingi qadam", ""]
    if applied is None:
        lines += [
            "Bu hisobot hech narsani o'zgartirmaydi. FILL ustunini ko'rib "
            "chiqing, CONFLICT larni qo'lda hal qiling, so'ng qo'llang:",
            "",
            "```bash",
            f"python3 scripts/rescrape_broken_records.py --run-id {run_id} "
            "--apply --confirm",
            "```",
            "",
            "CSV: yonidagi `.csv` fayl (`apply_status` ustuni bilan).",
            "",
        ]
    else:
        lines += [
            "FILL lar qo'llandi. CONFLICT lar hamon qo'lda hal qilinishi kerak "
            "— ular avtomatik yozilmaydi. Natijani tekshirish uchun skanni "
            "qayta ishga tushiring; noto'g'ri bo'lsa yuqoridagi rollback "
            "komandasini ishlating.",
            "",
        ]
    return "\n".join(lines)


def _md_cell(value: str, limit: int = 120) -> str:
    """Markdown jadval katagi uchun xavfsiz matn."""
    s = truncate((value or "").replace("|", "\\|"), limit)
    return s or "—"


def write_csv(path: str, findings: List[Dict], errors: List[Dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["record_id", "page_id", "field", "status",
                    "db_value", "page_value", "note", "apply_status"])
        for x in findings:
            w.writerow([x["record_id"], x.get("page_id", ""), x["field"],
                        x["status"], x["db_value"], x["page_value"],
                        x.get("note", ""), x.get("apply_status", "")])
        for e in errors:
            w.writerow([e["record_id"], e.get("page_id", ""), "", "FETCH_ERROR",
                        "", "", e["reason"], ""])


# ── CLI ─────────────────────────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Buzuq yozuvlarni OAK sahifasidan qayta o'qish "
                    "(default: faqat hisobot; --apply --confirm: FILL larni yozadi)")
    p.add_argument("--run-id", type=int,
                   help="data_quality_issues.run_id — qaysi scan natijasi")
    p.add_argument("--codes", default=",".join(DEFAULT_CODES),
                   help="vergul bilan ajratilgan issue kodlari")
    p.add_argument("--limit", type=int, default=None,
                   help="faqat birinchi N yozuv (sinov uchun)")
    p.add_argument("--record-id", type=int, default=None,
                   help="faqat bitta yozuv")
    p.add_argument("--refresh", action="store_true",
                   help="keshni e'tiborsiz qoldirib qayta yuklash")
    p.add_argument("--inspect", metavar="PAGE_ID",
                   help="keshdagi sahifadan nima ajratilganini ko'rsatish "
                        "(bazaga ulanmaydi)")
    p.add_argument("--out-dir", default=DOCS_DIR, help="hisobot papkasi")
    p.add_argument("--apply", action="store_true",
                   help="FILL larni bazaga yozadi (--confirm bilan birga)")
    p.add_argument("--confirm", action="store_true",
                   help="--apply uchun majburiy tasdiq")
    p.add_argument("--applied-by", default=None,
                   help="fix_log dagi ijrochi yorlig'i (default: cli:<user>)")
    p.add_argument("--database-url", default=None,
                   help="DB URL (aks holda DATA_QUALITY_/TEST_/DATABASE_URL)")
    return p


def _default_actor() -> str:
    try:
        return f"cli:{getpass.getuser()}"
    except Exception:              # cron/konteynerda login nomi bo'lmasligi mumkin
        return "cli"


def inspect_page(page_id: str) -> int:
    """Keshdagi sahifadan nima ajratilganini ko'rsatadi — parserni tekshirish
    uchun eng tez yo'l."""
    path = cache_path(page_id)
    if not os.path.exists(path):
        print(f"Keshda yo'q: {path}")
        return 2
    with open(path, encoding="utf-8") as f:
        html = f.read()
    values = parse_page(html)
    print(f"→ {path}")
    print(f"  sarlavha: {truncate(values.pop('_title'), 100)!r}")
    for column, key, _norm, label in FIELDS:
        v = values.get(key, "")
        mark = "  " if v else "!!"
        print(f"  {mark} {label:<16} {truncate(v, 90)!r}")
    print("\n(bo'sh '!!' maydonlar parser yorlig'i mos kelmaganini bildirishi "
          "mumkin — sahifa matnini tekshiring)")
    return 0


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    if args.inspect:
        return inspect_page(args.inspect)
    if not args.run_id:
        print("--run-id majburiy (yoki --inspect ishlating).")
        return 2
    if args.apply and not args.confirm:
        print("--apply uchun --confirm ham kerak. Avval hisobot rejimida "
              "(bayroqsiz) ishga tushiring va FILL ro'yxatini ko'rib chiqing.")
        return 2
    if args.confirm and not args.apply:
        print("--confirm faqat --apply bilan ma'noga ega.")
        return 2

    apply_mode = bool(args.apply and args.confirm)
    actor = args.applied_by or _default_actor()
    codes = tuple(c.strip() for c in args.codes.split(",") if c.strip())
    conn, target = resolve_connection(args.database_url)
    print(f"→ Baza: {target}   "
          + ("(QO'LLASH — faqat FILL lar yoziladi)" if apply_mode
             else "(FAQAT O'QISH — hech narsa yozilmaydi)"))

    try:
        if args.record_id:
            record_ids = [args.record_id]
        else:
            record_ids = select_broken_record_ids(conn, args.run_id, codes)
        if args.limit:
            record_ids = record_ids[: args.limit]
        rows = fetch_rows(conn, record_ids)
        conn.rollback()          # o'qish tranzaksiyasini yopamiz
    finally:
        try:
            conn.close()
        except Exception:
            pass

    log(f"{len(record_ids)} ta yozuv tanlandi ({', '.join(codes)})")

    fetch_log = load_fetch_log()
    findings: List[Dict] = []
    errors: List[Dict] = []
    interrupted = False
    stats = {"selected": len(record_ids), "with_page": 0, "ok": 0,
             "from_cache": 0, "downloaded": 0, "codes": list(codes)}

    try:
        for i, rid in enumerate(record_ids, 1):
            row = rows.get(rid)
            if row is None:
                errors.append({"record_id": rid, "page_id": None,
                               "reason": "yozuv dissertations da topilmadi"})
                continue

            page_id = page_id_from_link(row.get("link"))
            if not page_id:
                errors.append({"record_id": rid, "page_id": None,
                               "reason": f"havola oak.uz/pages/N ga mos emas: "
                                         f"{truncate(row.get('link'), 80)}"})
                continue
            stats["with_page"] += 1

            cached = os.path.exists(cache_path(page_id)) and not args.refresh
            html, error = fetch_page(page_id, fetch_log, refresh=args.refresh)
            if html is None:
                errors.append({"record_id": rid, "page_id": page_id,
                               "reason": error or "noma'lum xato"})
                save_fetch_log(fetch_log)
                continue

            if cached:
                stats["from_cache"] += 1
            else:
                stats["downloaded"] += 1
                save_fetch_log(fetch_log)

            try:
                values = parse_page(html)
            except Exception as e:               # bitta buzuq sahifa runni to'xtatmaydi
                errors.append({"record_id": rid, "page_id": page_id,
                               "reason": f"parsing xatosi: {e.__class__.__name__}"})
                continue

            stats["ok"] += 1
            for finding in compare_record(row, values):
                finding["page_id"] = page_id
                findings.append(finding)

            if i % 25 == 0 or i == len(record_ids):
                log(f"  {i}/{len(record_ids)} — ok={stats['ok']} "
                    f"xato={len(errors)} (kesh={stats['from_cache']})")

            # Faqat haqiqiy tarmoq so'rovidan keyin kutamiz.
            if not cached and i < len(record_ids):
                time.sleep(DELAY_SECONDS)
    except KeyboardInterrupt:
        interrupted = True
        log("To'xtatildi (Ctrl+C) — shu paytgacha yig'ilgani hisobotga yoziladi. "
            "Qayta ishga tushirsangiz keshdan davom etadi.")
    finally:
        save_fetch_log(fetch_log)

    # ── qo'llash (faqat --apply --confirm bilan) ────────────────────────────
    applied = None
    if apply_mode and interrupted:
        log("Uzilib qolgan run — qo'llash bajarilmadi. Qayta ishga tushiring.")
    elif apply_mode:
        fills_to_apply = collect_fills(findings)
        log(f"Qo'llash: {len(fills_to_apply)} ta FILL "
            f"({sum(1 for f in findings if f['status'] == CONFLICT)} ta CONFLICT "
            "tegilmaydi)")
        conn, target = resolve_connection(args.database_url)
        require_manual_transactions(conn, "apply")
        try:
            issues = fetch_open_issues(
                conn, args.run_id, sorted({f["record_id"] for f in fills_to_apply}))
            conn.rollback()          # o'qish tranzaksiyasini yopamiz
            applied = apply_fills(conn, fills_to_apply, scan_run_id=args.run_id,
                                  target=target, actor=actor, issues=issues)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    os.makedirs(args.out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    prefix = "rescrape_apply" if applied is not None else "rescrape_report"
    md_path = os.path.join(args.out_dir, f"{prefix}_{stamp}.md")
    csv_path = os.path.join(args.out_dir, f"{prefix}_{stamp}.csv")

    with open(md_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(build_report(findings, errors, stats, args.run_id, target, applied))
    write_csv(csv_path, findings, errors)

    fills = sum(1 for f in findings if f["status"] == FILL)
    conflicts = sum(1 for f in findings if f["status"] == CONFLICT)
    log(f"Tayyor: {stats['ok']} ta sahifa o'qildi, {fills} ta FILL, "
        f"{conflicts} ta CONFLICT, {len(errors)} ta xato")
    print(f"\n  {md_path}\n  {csv_path}")
    if applied is None:
        print("\n(bazaga hech narsa yozilmadi)")
    else:
        print(f"\nqo'llandi   : {applied['applied']} ta maydon "
              f"({applied['records']} ta yozuv)")
        print(f"o'tkazildi  : {applied['skipped']}")
        print(f"xato        : {applied['failed_records']} ta yozuv")
        if applied["run_id"] is not None:
            print(f"rescrape run_id : {applied['run_id']}")
            print(f"\nqaytarish:  python3 scripts/rollback_data_quality_fixes.py "
                  f"--run-id {applied['run_id']} --confirm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
