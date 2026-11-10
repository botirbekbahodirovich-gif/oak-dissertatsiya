# ============================================================
# OAK.UZ — Kunlik avtomatik scraper
# Server cron'i (06:00) ishga tushiradi: DB ga to'g'ridan-to'g'ri yozadi va
# yangi qo'shilgan qatorlar bo'yicha bildirishnoma yuboradi (services/oak_notify).
#   python3 scraper.py [--dry-run]
#   python3 scraper.py --notify-only --ids 123,124 [--dry-run]
# ============================================================

import argparse
import logging
import requests
from bs4 import BeautifulSoup
import json
import re
import time
import os
from datetime import datetime

log = logging.getLogger("scraper")

# ----------------- SOZLAMALAR -----------------
# Bir nechta manba — har biri bir xil strukturada, alohida scraping qilinadi.
SOURCES = [
    {"url": "https://oak.uz/page/8",  "nomi": "Oddiy"},
    {"url": "https://oak.uz/page/31", "nomi": "Mustaqil ilmiy kengash"},
]
MAX_PAGES = 10        # Kuniga max 10 sahifa tekshiriladi (~100 ta e'lon)
DELAY = 1.0
TIMEOUT = 20
STATE_FILE = "last_state.json"
OUTPUT_FILE = "new_items.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
}


# ----------------- YORDAMCHI -----------------
def fetch(url, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.text
        except Exception as e:
            print(f"  Xato ({attempt+1}/3): {e}")
        time.sleep(2)
    return None


def extract_field(text, patterns):
    for pattern in patterns:
        regex = (
            pattern
            + r"\s*:?\s*(.+?)(?=\n[А-ЯЎҒҲҚIVX]|$"
            + r"|\bДиссертация\b|\bИлмий\b|\bИК\b"
            + r"|\bРасмий\b|\bЕтакчи\b|\bТадқиқот\b"
            + r"|\bI\.\b|\bII\.\b|\bIII\.\b|\bIV\.\b)"
        )
        m = re.search(regex, text, re.DOTALL | re.IGNORECASE)
        if m:
            result = re.sub(r"\s+", " ", m.group(1).strip()).rstrip(" .;,")
            if result and len(result) < 2000:
                return result
    return ""


FAN_TARMOQLARI = [
    "тиббиёт", "филология", "техника", "педагогика", "тарих",
    "биология", "иқтисодиёт", "кимё", "қишлоқ хўжалиги",
    "физика", "математика", "юридик", "фалсафа", "психология",
    "социология", "сиёсат", "география", "геология", "архитектура",
    "санъатшунослик", "ветеринария", "фармацевтика",
]


def extract_science_branch(text):
    m = re.search(r"\(([а-яёўқғҳА-ЯЁЎҚҒҲ\s\-]+)\s+фанлари\)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip().lower()
    m = re.search(r"([а-яёўқғҳА-ЯЁЎҚҒҲ\s\-]+)\s+фанлари\s+(?:доктори|номзоди)", text, re.IGNORECASE)
    if m:
        branch = m.group(1).strip().lower()
        for fan in FAN_TARMOQLARI:
            if fan in branch:
                return fan
    text_lower = text.lower()
    for fan in FAN_TARMOQLARI:
        if fan in text_lower:
            return fan
    return ""


def extract_ik_code(text):
    m = re.search(r"((?:DSc|PhD)\.\d+/\d+\.\d+\.\d+[\.\w/]*\d+)", text)
    return m.group(1) if m else ""


def detect_degree(text):
    if re.search(r"\bDSc\b|фан доктори\s*\(DSc\)|doktori\s*\(DSc\)", text, re.IGNORECASE):
        return "DSc"
    if re.search(r"\bPhD\b|фалсафа доктори|falsafa doktori", text, re.IGNORECASE):
        return "PhD"
    return ""


def degree_from_title(title):
    """Sarlavhadagi daraja: "…нинг фалсафа доктори (PhD) диссертацияси".

    Ishonchli manba — e'lon matnida opponent/rahbarlarning "(DSc)" lari va
    "DSc.03/…" kengash kodi ham bor, ular dissertatsiya darajasi emas.
    """
    t = title or ""
    if re.search(r"фалсафа доктори|falsafa doktori|\bPhD\b", t, re.IGNORECASE):
        return "PhD"
    if re.search(r"фан доктори|fan doktori|\bDSc\b", t, re.IGNORECASE):
        return "DSc"
    return ""


# ----------------- BO'LIMLARGA AJRATISH -----------------
# extract_field() qiymatni matn ichidagi "Илмий", "Тадқиқот", "Диссертация"
# so'zlarida kesib qo'yadi ("катта илмий ходим", "илмий-тадқиқот институти").
# Shuning uchun e'lon matni avval qator boshidagi sarlavhalar bo'yicha
# bo'limlarga ajratiladi; qiymat — keyingi sarlavhagacha bo'lgan butun matn.
_SECTION_HEADERS = [
    ("ro_yxat_raqami", r"Диссерта(?:ция|сия) мавзуси рўй[хҳ]атга олинган рақам"),
    ("mavzu", r"Диссерта(?:ция|сия) мавзуси"),
    ("ilmiy_rahbar", r"Илмий (?:раҳбар(?:лар)?|маслаҳатчи)"),
    ("bajarilgan_muassasa", r"Диссерта(?:ция|сия) бажарилган муассаса"),
    ("ik_muassasa", r"(?:ИК|Ик|Илмий [Кк]енгаш) фаолият кўрсатаётган муассаса"),
    ("opponentlar", r"Расмий оппоне(?:н)?тлар"),          # "оппонетлар" ham uchraydi
    ("yetakchi_tashkilot", r"[ЕЭ]такчи\s+ташкилот"),      # "Этакчи" ham uchraydi
    ("yo_nalish", r"Диссерта(?:ция|сия) (?:йўналиши|ё\s*[‘ʻ’']\s*налиши)"),
]
# Bu ikki sarlavha ko'pincha qator o'rtasida keladi: "…, профессор. Етакчи
# ташкилот: …" — ular har qanday bo'shliqdan keyin ham tan olinadi.
_INLINE_HEADERS = ("yetakchi_tashkilot", "yo_nalish")
_SECTION_RE = re.compile(
    r"(?m)^[ \t]*(?:" + "|".join(
        "(?P<%s>%s)" % (k, p) for k, p in _SECTION_HEADERS) + ")"
    + "|(?<=\\s)(?:" + "|".join(
        "(?P<%s__inline>%s)" % (k, p) for k, p in _SECTION_HEADERS
        if k in _INLINE_HEADERS) + ")")

# Ba'zi e'lonlar kirill matn ichida lotin "ç ş ö ğ" bilan yozilgan
# ("Етакçи таşкилот", "Мухаммадовиç") — kirill harfi yonida bo'lsa almashtiriladi.
_HOMOGLYPHS = {"ç": "ч", "Ç": "Ч", "ş": "ш", "Ş": "Ш", "ö": "ў", "Ö": "Ў",
               "ğ": "ғ", "Ğ": "Ғ"}
_CYR = "А-Яа-яЁёЎўҚқҒғҲҳ"
_HOMOGLYPH_RE = re.compile(
    "(?<=[%s])[çÇşŞöÖğĞ]|[çÇşŞöÖğĞ](?=[%s])" % (_CYR, _CYR))


def fix_homoglyphs(text):
    return _HOMOGLYPH_RE.sub(lambda m: _HOMOGLYPHS[m.group(0)], text or "")
# Sarlavha dumi: "… (илмий даража бериладиган фан тармоғи номи):",
# "… номи\n:" yoki "… –". Ikki nuqta bo'lmasa qiymat darhol boshlanadi.
_HEADER_TAIL_RE = re.compile(r"[^:\n]{0,90}:|[^:\n]{0,40}\n\s*:|\s*[–—-]\s")


def split_sections(text):
    """E'lon matnini {kalit: xom qiymat} ga ajratadi (birinchi uchragani)."""
    matches = list(_SECTION_RE.finditer(text or ""))
    out = {}
    for i, m in enumerate(matches):
        key = m.lastgroup.split("__")[0]
        start = m.end()
        tail = _HEADER_TAIL_RE.match(text, start)
        if tail:
            start = tail.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        if key not in out:
            out[key] = text[start:end].strip()
    return out


def _clean(value):
    return re.sub(r"\s+", " ", value or "").strip().lstrip(":–—- ").rstrip(" .;,")


_CODE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{2})\b")


def normalize_codes(text):
    """Ixtisoslik shifrlari: noyob, tartiblangan, ", " bilan (bazadagi mavjud
    ko'p kodli qatorlar formati: "02.00.07, 05.02.01")."""
    return ", ".join(sorted(set(_CODE_RE.findall(text or ""))))


def clean_mavzu(section):
    """"“Mavzu”, 13.00.04 – Ixtisoslik nomi (…)" dan faqat mavzu nomini oladi."""
    text = re.sub(r"\s+", " ", section or "").strip()
    m = re.search(r"[«\"“„]([^»\"”“]{20,})[»\"”]", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"^(.+?)[\s,]+(?:мавзусидаги\s+)?\d{2}\.\d{2}\.\d{2}", text)
    if m:
        return m.group(1).strip(" ,–—-")
    return _clean(text)


# To'liq ism: "Фамилия Исм Отасининг" (…вич/…вна/… ўғли/… қизи) yoki
# teskari tartib "Исм Отасининг Фамилия" ("Жуманиёз Султонович Раматов").
_CAP = r"[А-ЯЁЎҚҒҲA-Z][\w'ʻ’-]"
_FULL_NAME_RE = re.compile(
    r"(?<![\w-])(?:"
    + _CAP + r"+\s+" + _CAP + r"+\s+" + _CAP
    + r"*(?:вич|вна|\s+ўғли|\s+қизи|\s+угли|\s+углу|\s+кизи)"
    + r"|" + _CAP + r"+\s+" + _CAP + r"*(?:вич|вна)\s+" + _CAP + r"+"
    + r")(?![\w-])")


def split_opponents(section, limit=3):
    """"Расмий оппонентлар" bo'limini alohida opponentlarga ajratadi.

    OAK'da uchragan shakllar: "A, …; B, …" (asosiy), har biri yangi qatorda,
    va ";" siz vergul/bo'shliq bilan ("A, …, дотсент Б Б Б, …").
    Har bir element bazadagi formatda: "F.I.Sh., daraja, unvon".
    """
    raw = (section or "").strip()
    if not raw:
        return []
    parts = [p for p in re.split(r";", raw) if _clean(p)]
    if len(parts) == 1:
        lines = [ln for ln in raw.split("\n") if _clean(ln)]
        if len(lines) > 1:
            parts = lines
    out = []
    for part in parts:
        out.extend(_split_by_names(part))
    return [_clean(p) for p in out if _clean(p)][:limit]


def _split_by_names(part):
    """Bitta bo'lakda bir nechta to'liq ism bo'lsa — ism bo'yicha ajratadi.

    Ism oldin ("A A A, daraja, unvon, Б Б Б, …") — yangi opponent ismdan
    boshlanadi; daraja oldin, ya'ni kichik harf bilan ("daraja, unvon A A A,
    daraja, unvon Б Б Б") — opponent ism bilan tugaydi.
    """
    text = re.sub(r"\s+", " ", part).strip()
    names = list(_FULL_NAME_RE.finditer(text))
    if len(names) < 2:
        return [text]
    if text[:1].islower():
        cuts = [m.end() for m in names[:-1]]
    else:
        cuts = [m.start() for m in names if m.start() > 0]
    bounds = [0] + cuts + [len(text)]
    pieces = [text[a:b] for a, b in zip(bounds, bounds[1:])]
    return [re.sub(r"^[\s,.;]*(?:ва|ҳамда)?\s+", "", piece) for piece in pieces]


def parse_announcement(text):
    text = fix_homoglyphs(text)
    sections = split_sections(text)
    fields = {
        "mavzu": [
            r"Диссертация мавзуси[^:]*шифри[^:]*",
            r"Диссертасия мавзуси[^:]*шифри[^:]*",
        ],
        "ro_yxat_raqami": [
            r"Диссертация мавзуси рўйхатга олинган рақам",
            r"мавзуси рўйҳатга олинган рақам",
        ],
        "ilmiy_rahbar": [
            r"Илмий раҳбар(?:лар)?(?:нинг[^:]*)?",
            r"Илмий маслаҳатчи",
        ],
        "bajarilgan_muassasa": [
            r"Диссертация бажарилган муассаса(?:лар)? номи",
            r"Диссертасия бажарилган муассаса(?:лар)? номи",
        ],
        "ik_muassasa": [
            r"ИК фаолият кўрсатаётган муассаса[^:]*номи[^:]*ИК рақами",
            r"ИК фаолият кўрсатаётган муассаса[^:]*",
        ],
        "opponentlar": [r"Расмий оппонентлар"],
        "yetakchi_tashkilot": [r"Етакчи ташкилот"],
        "yo_nalish": [r"Диссертация йўналиши", r"Диссертасия йўналиши"],
    }
    # Bo'lim topilsa — undan (to'liq qiymat), aks holda eski extract_field.
    result = {k: (_clean(sections[k]) if sections.get(k) else extract_field(text, v))
              for k, v in fields.items()}
    if sections.get("yo_nalish"):
        result["yo_nalish"] = _clean(sections["yo_nalish"].split("\n")[0])
    result["mavzu_nomi"] = clean_mavzu(sections.get("mavzu") or result["mavzu"])
    result["opponentlar_list"] = split_opponents(
        sections.get("opponentlar") or result["opponentlar"])
    # Shifrlar mavzu bo'limidan: opponentlar qatoridagi "(06.01.02)" kabi
    # kodlar dissertatsiya ixtisosligi emas. Mavzuda kod bo'lmasa — butun
    # matndan, opponentlar bo'limini chiqarib tashlab.
    codes = _CODE_RE.findall(sections.get("mavzu") or "")
    if not codes:
        codes = _CODE_RE.findall(text.replace(sections.get("opponentlar") or "\0", ""))
    result["ixtisoslik_shifrlari"] = sorted(set(codes))
    result["fan_tarmogi"] = extract_science_branch(text)
    result["ik_raqami"] = extract_ik_code(text)
    # Rahbar, IK va opponentlar bo'limlarida boshqa odamlarning darajasi va
    # "DSc.03/…" kengash kodi bor — ularni chiqarib tashlab aniqlaymiz.
    own_text = text
    for key in ("ilmiy_rahbar", "ik_muassasa", "opponentlar"):
        if sections.get(key):
            own_text = own_text.replace(sections[key], " ")
    result["daraja"] = detect_degree(own_text)
    return result


def parse_list_page(html):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for a in soup.find_all("a", href=True):
        if "/pages/" not in a["href"]:
            continue
        if a.get_text(strip=True).lower() != "batafsil":
            continue
        block = a
        for _ in range(6):
            block = block.parent
            if block is None:
                break
            if block.find("h3"):
                break
        if block is None:
            continue
        h3 = block.find("h3")
        if not h3 or not h3.find("a"):
            continue
        title = h3.get_text(strip=True)
        link = h3.find("a")["href"]
        if link.startswith("/"):
            link = "https://oak.uz" + link
        m = re.search(r"/pages/(\d+)", link)
        item_id = int(m.group(1)) if m else 0
        text_in_block = block.get_text(" ", strip=True)
        date_match = re.search(r"\b(\d{2}\.\d{2}\.\d{4})\b", text_in_block)
        date = date_match.group(1) if date_match else ""
        full_text = block.get_text("\n", strip=True)
        clean_text = full_text.replace(title, "", 1)
        clean_text = re.sub(r"\bBatafsil\b", "", clean_text)
        clean_text = re.sub(r"^\s*" + re.escape(date), "", clean_text)
        items.append({
            "id": item_id, "title": title, "link": link,
            "date": date, "raw_text": clean_text.strip()
        })
    seen, unique = set(), []
    for it in items:
        if it["link"] not in seen:
            seen.add(it["link"])
            unique.append(it)
    return unique


# ----------------- STATE -----------------
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        # Eski bir manbali format ({"last_id": N}) ni page/8 kalitiga ko'chiramiz,
        # aks holda watermark yo'qolib, hamma e'lon "eski" deb hisoblanardi.
        if "last_id" in state and "last_id_page8" not in state:
            state["last_id_page8"] = state.pop("last_id")
        state.setdefault("last_id_page8", 0)
        state.setdefault("last_id_page31", 0)
        return state
    # Har bir manba uchun alohida last_id.
    return {"last_id_page8": 0, "last_id_page31": 0, "last_run": None}


def _state_key(url):
    """Manba URL idan state kalitini hosil qiladi: .../page/8 -> last_id_page8."""
    m = re.search(r"/page/(\d+)", url)
    return "last_id_page" + (m.group(1) if m else "0")


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ----------------- ASOSIY -----------------
def main(dry_run=False):
    """Manbalarni scraping qiladi, yangi e'lonlarni bazaga yozadi va faqat
    HAQIQATAN qo'shilgan qatorlar (INSERT ... RETURNING id) bo'yicha
    bildirishnoma yuboradi.

    dry_run=True: bazaga ham, state/new_items fayllariga ham yozmaydi; qaysi
    e'lon qo'shilishi va kimga qanday xabar ketishini chiqaradi xolos.
    """
    state = load_state()
    print("=" * 50)
    print("OAK.UZ kunlik scraper (" + str(len(SOURCES)) + " manba)"
          + (" [DRY-RUN]" if dry_run else ""))
    print("Sana: " + datetime.now().isoformat())
    print("=" * 50)

    new_items = []
    new_ids = []
    skipped_total = 0
    error_total = 0

    # Bazaga ulanish — barcha manbalar bo'ylab bir marta ochiladi.
    try:
        from data import get_connection
    except ImportError:
        print("data.py topilmadi, bazaga yozilmadi.")
        get_connection = None

    conn = None
    if get_connection is not None:
        try:
            conn = get_connection()
        except Exception as e:
            print("Bazaga ulanib bo'lmadi: " + str(e))
            conn = None

    # Har bir manbani alohida sahifalab, alohida smart-stop bilan scraping qilamiz.
    for source in SOURCES:
        src_url = source["url"]
        key = _state_key(src_url)
        last_id = state.get(key, 0)
        max_id_seen = last_id
        consec_skip = 0
        stop = False

        print("-" * 50)
        print("Manba: " + source["nomi"] + " (" + src_url + ")")
        print("Oxirgi ID (" + key + "): " + str(last_id))
        print("-" * 50)

        for page_num in range(1, MAX_PAGES + 1):
            if stop:
                break
            url = f"{src_url}?page={page_num}" if page_num > 1 else src_url
            print("Sahifa " + str(page_num) + " tekshirilmoqda...")
            html = fetch(url)
            if html is None:
                continue

            items = parse_list_page(html)
            if not items:
                break

            for item in items:
                if item["id"] > max_id_seen:
                    max_id_seen = item["id"]

                # Watermark (last_id) faqat qachon to'xtashni belgilaydi.
                # "Yangi" degani — bazaga haqiqatan qo'shilgan qator.
                is_new = item["id"] > last_id

                parsed = parse_announcement(item["raw_text"])
                record = _build_record(item, parsed)

                status = None
                if conn is not None:
                    if dry_run:
                        status = db_would_insert(conn, record)
                    else:
                        status, new_id = db_save_one(conn, record)
                        if new_id:
                            new_ids.append(new_id)

                if status == "added":
                    new_items.append(record)
                    print("  + Yangi: ID=" + str(item["id"]) + " | " + item["date"])
                    if dry_run:
                        _print_record(record)
                elif status == "error":
                    error_total += 1
                else:
                    skipped_total += 1

                if is_new:
                    consec_skip = 0
                else:
                    consec_skip += 1
                    # 4 ta ketma-ket eski (watermark dan pastroq) element = eski
                    # ma'lumotlarga yetdik, to'xtatamiz.
                    if consec_skip >= 4:
                        print("  4 ta ketma-ket eski element — eski ma'lumotlarga "
                              "yetildi, to'xtatildi.")
                        stop = True
                        break

            time.sleep(DELAY)

        # Ushbu manba uchun alohida last_id yangilash
        if max_id_seen > last_id:
            state[key] = max_id_seen
            print(key + " yangilandi: " + str(max_id_seen))

    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

    if not dry_run:
        # State faylini bir marta saqlaymiz
        state["last_run"] = datetime.now().isoformat()
        save_state(state)
        # Natijani JSON ga saqlash (faqat yangi qo'shilganlar — log uchun)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(new_items, f, ensure_ascii=False, indent=2)

    print("\nAniqlandi: " + str(len(new_items)) + " ta yangi, "
          + str(skipped_total) + " ta mavjud (skip), "
          + str(error_total) + " ta xato."
          + ("" if conn is not None else " (DB ulanmagan — hech narsa yozilmadi)"))

    # Bildirishnomalar — faqat shu ishga tushishda qo'shilgan qatorlar uchun.
    if dry_run:
        if new_items:
            run_notifications(records=[_notify_record(r) for r in new_items])
    elif new_ids:
        run_notifications(ids=new_ids)

    return new_items


def run_notifications(ids=None, records=None, dry_run=False):
    """services/oak_notify ni chaqiradi. Xato scraper'ni to'xtatmaydi — faqat
    log'ga yoziladi. `records` berilsa — bazasiz ko'rib chiqish (dry-run)."""
    try:
        from services import oak_notify
        if records is not None:
            result = oak_notify.preview_records(records)
        else:
            result = oak_notify.notify_new_dissertations(ids, dry_run=dry_run)
        print(oak_notify.format_result(result))
        return result
    except Exception:
        log.exception("Bildirishnomalar yuborilmadi (scraper davom etadi)")
        return None


def _notify_record(record):
    """_build_record() lug'ati → bildirishnoma yozuvi (dry-run, id yo'q)."""
    return {
        "id": None, "olim": record.get("Olim", ""), "mavzu": record.get("Mavzu", ""),
        "ixtisoslik": record.get("Ixtisoslik shifrlari", ""),
        "ilmiy_rahbar": record.get("Ilmiy rahbar", ""), "link": record.get("Havola", ""),
    }


def _print_record(record):
    for k in ("Olim", "Daraja", "Mavzu", "Ixtisoslik shifrlari", "Ilmiy rahbar",
              "Bajarilgan muassasa", "Yetakchi tashkilot", "Yonalish"):
        print("      " + k + ": " + str(record.get(k, ""))[:160])
    for n, opp in enumerate(record.get("Opponentlar") or [], 1):
        print("      Opponent " + str(n) + ": " + opp[:160])


# ── Bazaga yozish yordamchilari ─────────────────────────────────────────────

_INSERT_SQL = """
    INSERT INTO dissertations (
        oak_id, sana, olim, daraja, mavzu, ixtisoslik, fan_tarmoqi,
        mavzu_raqami, ilmiy_rahbar, muassasa, institution_id, ilmiy_kengash,
        ilmiy_kengash_raqami, opponent_1, opponent_2, opponent_3,
        yetakchi_tashkilot, link, yonalish
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s
    )
    ON CONFLICT (oak_id) DO NOTHING
    RETURNING id
"""


def _item_values(item, institution_id=None):
    """_build_record() lug'atini INSERT ustunlari tartibiga moslaydi."""
    opponents = (list(item.get("Opponentlar") or []) + ["", "", ""])[:3]
    return (
        str(item.get("ID", "")),                # oak_id
        item.get("Sana", ""),                   # sana
        item.get("Olim", ""),                   # olim
        item.get("Daraja", ""),                 # daraja
        item.get("Mavzu", ""),                  # mavzu (faqat nomi)
        item.get("Ixtisoslik shifrlari", ""),   # ixtisoslik (barcha kodlar)
        item.get("Fan tarmogi", ""),            # fan_tarmoqi
        item.get("Royxat raqami", ""),          # mavzu_raqami
        item.get("Ilmiy rahbar", ""),           # ilmiy_rahbar
        item.get("Bajarilgan muassasa", ""),    # muassasa
        institution_id,                         # canonical ranking ID
        item.get("IK muassasa", ""),            # ilmiy_kengash
        item.get("IK raqami", ""),              # ilmiy_kengash_raqami
        opponents[0],                           # opponent_1
        opponents[1],                           # opponent_2
        opponents[2],                           # opponent_3
        item.get("Yetakchi tashkilot", ""),     # yetakchi_tashkilot
        item.get("Havola", ""),                 # link
        item.get("Yonalish", ""),               # yonalish
    )


def db_save_one(conn, item):
    """Bitta elementni bazaga yozadi.

    (status, new_id) qaytaradi: status — 'added' / 'skip' / 'error';
    new_id — faqat haqiqatan qo'shilgan qatorning dissertations.id si.
    """
    try:
        from institution_identity import resolve_institution
        with conn.cursor() as cur:
            institution_id = resolve_institution(
                cur, item.get("Bajarilgan muassasa"), source='scraper')
            cur.execute(_INSERT_SQL, _item_values(item, institution_id))
            row = cur.fetchone()
        conn.commit()
        return ("added", row[0]) if row else ("skip", None)
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print("  DB xato (ID=" + str(item.get("ID")) + "): " + str(e))
        return "error", None


def db_would_insert(conn, item):
    """Dry-run: yozmasdan, element qo'shilarmidi — 'added' / 'skip' / 'error'."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM dissertations WHERE oak_id = %s",
                        (str(item.get("ID", "")),))
            exists = cur.fetchone() is not None
        conn.rollback()
        return "skip" if exists else "added"
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print("  DB xato (ID=" + str(item.get("ID")) + "): " + str(e))
        return "error"


def save_to_db(items):
    """Elementlar ro'yxatini bazaga yozadi (har biri alohida ulanish bilan).

    oak_id bo'yicha ON CONFLICT DO NOTHING — takroriy yozuvlar skip qilinadi.
    Bitta yozuvdagi xato boshqalarini to'xtatmaydi.
    """
    if not items:
        print("Bazaga yozish: element yo'q.")
        return

    try:
        from data import get_connection
    except ImportError:
        print("data.py topilmadi, bazaga yozilmadi.")
        return

    added = 0
    skipped = 0
    for item in items:
        try:
            conn = get_connection()
        except Exception as e:
            print("  DB xato (ID=" + str(item.get("ID")) + "): " + str(e))
            continue
        status, _ = db_save_one(conn, item)
        try:
            conn.close()
        except Exception:
            pass
        if status == "added":
            added += 1
        elif status == "skip":
            skipped += 1
    print("Bazaga yozildi: " + str(added) + " ta yangi, "
          + str(skipped) + " ta mavjud (skip).")
    return added, skipped


def extract_author_from_title(title):
    """E'lon sarlavhasidan olim ismini ajratadi.

    Sarlavha odatda "Фамилия Исм Отасининг фалсафа доктори (PhD) ..." shaklida
    bo'ladi. Alohida funksiya — rescrape_broken_records.py ham shu mantiqni
    ishlatadi. "-нинг" siz sarlavhalar ham uchraydi (oak.uz, 2026-11:
    200 tadan 18 tasi).
    """
    title = (title or "").strip()
    # Kirill: "Фамилия Исм Отасининг фалсафа/фан доктори..."
    m = re.search(
        r'^([А-ЯЎҚҒҲа-яўқғҳёЁ\s\'\-\.]+?)нинг\s+(?:фалсафа|фан)\s+доктори',
        title
    )
    if m:
        return m.group(1).strip()
    # Lotin: "Familiya Ism Otasinining falsafa/fan doktori..."
    m = re.search(
        r'^([A-Za-z\s\'\-\.]+?)ning\s+(?:falsafa|fan)\s+doktori',
        title, re.IGNORECASE
    )
    if m:
        return m.group(1).strip()
    # Qo'shimchasiz: "Фамилия Исм Отаси [Педагогика фанлари бўйича] фалсафа
    # доктори (PhD) …" — ism 2–5 so'z (ba'zan lotin harfi aralash: "Қуwанишбай").
    m = re.search(
        r"^((?:[^\s\d()]+\s+){1,4}?[^\s\d()]+)\s+"
        r"(?:[^\s\d()]+\s+фанлари\s+бўйича\s+)?(?:фалсафа|фан)\s+доктори",
        title, re.IGNORECASE
    )
    if m:
        # "…ўғлининг Фан доктори" (katta harf) ham shu yerga tushadi.
        return re.sub(r"нинг$", "", m.group(1).strip())
    # Zaxira: "нинг" gacha
    m = re.search(r'^(.+?)нинг', title)
    if m:
        return m.group(1).strip()
    return ""


def _build_record(item, parsed):
    # Olim ismini sarlavhadan tozalab ajratish
    title = fix_homoglyphs(item.get("title", ""))
    olim = extract_author_from_title(title)

    # Daraja: avval sarlavhadan, keyin e'lonning o'z matnidan.
    daraja = degree_from_title(title) or parsed.get("daraja", "")
    if not daraja:
        text = title + " " + parsed.get("mavzu", "")
        if any(x in text for x in ["фалсафа доктори", "falsafa doktori", "(PhD)", "PhD/"]):
            daraja = "PhD"
        elif any(x in text for x in ["фан доктори", "fan doktori", "(DSc)", "DSc/"]):
            daraja = "DSc"
    
    return {
        "ID": item["id"],
        "Sana": item["date"],
        "Sarlavha": title,           # To'liq sarlavha (log uchun)
        "Olim": olim,                # Tozalangan ism (saytga yuborish uchun)
        "Havola": item["link"],
        "Daraja": daraja,
        "Mavzu va ixtisoslik": parsed["mavzu"],   # xom matn (log uchun)
        "Mavzu": parsed["mavzu_nomi"] or parsed["mavzu"],
        "Ixtisoslik shifrlari": ", ".join(parsed["ixtisoslik_shifrlari"]),
        "Fan tarmogi": parsed["fan_tarmogi"],
        "Royxat raqami": parsed["ro_yxat_raqami"],
        "Ilmiy rahbar": parsed["ilmiy_rahbar"],
        "Bajarilgan muassasa": parsed["bajarilgan_muassasa"],
        "IK muassasa": parsed["ik_muassasa"],
        "IK raqami": parsed["ik_raqami"],
        "Rasmiy opponentlar": parsed["opponentlar"],
        "Opponentlar": parsed["opponentlar_list"],
        "Yetakchi tashkilot": parsed["yetakchi_tashkilot"],
        # "Диссертация йўналиши" (назарий ва амалий аҳамиятга молик …) —
        # bazadagi mavjud qiymatlar shu; manba nomi (Oddiy/Mustaqil) emas.
        "Yonalish": parsed["yo_nalish"],
    }


def ping_google():
    """Sitemap yangilanganini Google'ga bildirish (best-effort, xato yutiladi)."""
    try:
        requests.get(
            "https://www.google.com/ping?sitemap=https://olimlar.uz/sitemap.xml",
            timeout=10,
        )
        print("Google sitemap ping yuborildi.")
    except Exception:
        pass


def cli(argv=None):
    parser = argparse.ArgumentParser(description="OAK.UZ kunlik scraper")
    parser.add_argument("--dry-run", action="store_true",
                        help="bazaga yozmaydi, xabar yubormaydi; nima bo'lishini chiqaradi")
    parser.add_argument("--notify-only", action="store_true",
                        help="scraping qilmaydi; --ids dagi qatorlar uchun bildirishnoma")
    parser.add_argument("--ids", default="",
                        help="--notify-only uchun dissertations.id lar (vergul bilan)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.notify_only:
        ids = [int(x) for x in re.split(r"[,\s]+", args.ids) if x.strip()]
        if not ids:
            parser.error("--notify-only uchun --ids kerak")
        run_notifications(ids=ids, dry_run=args.dry_run)
        return

    items = main(dry_run=args.dry_run)
    if items and not args.dry_run:
        ping_google()


if __name__ == "__main__":
    cli()
