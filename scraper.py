# ============================================================
# OAK.UZ — Kunlik avtomatik scraper
# Server cron'i (06:00) ishga tushiradi: DB ga to'g'ridan-to'g'ri yozadi va
# yangi qo'shilgan qatorlar bo'yicha bildirishnoma yuboradi (services/oak_notify).
#   python3 scraper.py [--dry-run]
#   python3 scraper.py --notify-only --ids 123,124 [--dry-run]
#
# Manba (2026-11-12 dan): oak.uz yangi sayti (SPA) JSON API —
#   GET /api/elonlar?list=8|31&page=N   (har sahifada 10 ta, fields to'liq)
# Eski HTML (/page/8, /pages/N) endi bo'sh qobiq qaytaradi. API har doim asl
# kirill matnni beradi; "fields" [{label, value}] → "label: value" qatorlari →
# o'zgarmagan parse_announcement().
# Bildirishnomalar: OAK_NOTIFY_ENABLED=1 bo'lmasa yuborilmaydi, yangi qatorlar
# esa notified_at=NOW() bilan belgilanadi (keyin yoqilganda eskilarga ketmaydi).
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
# Ikki ro'yxat (oak.uz "list"). Ro'yxat sana, keyin ID bo'yicha kamayadi.
# 2026-11-12 dan yangi e'lonlar 1000000+ raqamlash bilan keladi (eski /pages/N
# raqamlashi 35181 da tugagan) — ikkalasi bir ro'yxatda aralash.
API_URL = "https://oak.uz/api/elonlar"
SITE_URL = "https://oak.uz"
SOURCES = [
    {"list": 8,  "nomi": "Oddiy"},
    {"list": 31, "nomi": "Mustaqil ilmiy kengash"},
]
# "Yangi" = DB ga haqiqatan qo'shilgan (INSERT … ON CONFLICT DO NOTHING
# RETURNING id). Watermark yo'q: ro'yxat boshidan o'qiladi; kamida MIN_PAGES
# sahifa o'qilgach, ketma-ket STOP_EMPTY_PAGES sahifada 0 ta yangi bo'lsa
# to'xtaydi. MAX_PAGES — xavfsizlik chegarasi (uzilishdan keyin --max-pages).
MIN_PAGES = 5
STOP_EMPTY_PAGES = 3
MAX_PAGES = 60
DELAY = 1.0
TIMEOUT = 20
STATE_FILE = "last_state.json"
OUTPUT_FILE = "new_items.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def fetch_json(url, retries=3):
    """JSON javob yoki None. 403/429 da qayta urinmaydi (bloklanmaslik uchun)."""
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                return r.json()
            print(f"  HTTP {r.status_code} ({attempt+1}/{retries}): {url}")
            if r.status_code in (403, 404, 429):
                return None
        except Exception as e:
            print(f"  Xato ({attempt+1}/{retries}): {e}")
        time.sleep(2)
    return None


def fetch_list_page(list_id, page):
    return fetch_json(f"{API_URL}?list={list_id}&page={page}")


_GENERAL_SECTION_RE = re.compile(r"^\s*(?:I\.\s*)?(?:Умумий маълумот|Umumiy ma.lumot)", re.I)


def api_item_text(item):
    """API fields → e'lon matni ("Сарлавҳа: қиймат" qatorlari).

    Label'lar eski sahifadagi sarlavhalar bilan bir xil, shuning uchun
    split_sections()/parse_announcement() o'zgarishsiz ishlaydi.
    - Bo'sh label: qiymat oldingi maydonning davomi.
    - Yangi format (1000000+, ba'zan lotin): maydonlar kam, "Umumiy ma'lumotlar"
      bo'limining paragraflarida "Sarlavha: qiymat" qatorlari keladi —
      ular ham qo'shiladi (fields'dagi bilan takrorlanganlari o'tkaziladi).
    """
    lines = []
    for f in item.get("fields") or []:
        label = " ".join(str(f.get("label") or "").split()).rstrip(":")
        value = str(f.get("value") or "").strip()
        if label:
            lines.append(label + ": " + value)
        elif value and lines:
            lines[-1] += " " + value
    seen = {ln.split(":", 1)[0].strip().lower() for ln in lines}
    for sec in item.get("sections") or []:
        if not _GENERAL_SECTION_RE.match(str(sec.get("title") or "")):
            continue
        for par in sec.get("paragraphs") or []:
            par = " ".join(str(par or "").split())
            head = par.split(":", 1)[0].strip().lower()
            if ":" in par and head not in seen:
                lines.append(par)
                seen.add(head)
    return "\n".join(lines)


def api_list_item(item):
    """API element → parse_list_page() bilan bir xil lug'at (id/title/link/date/raw_text)."""
    item_id = int(item["id"])
    href = str(item.get("href") or "")
    return {
        "id": item_id,
        "title": item.get("title") or "",
        # Yangi saytda e'lon sahifasi /himoya-elonlari/N (API "href"); eski
        # /pages/N ham ishlaydi (301 → /material/N → SPA shu yerga yo'naltiradi).
        "link": SITE_URL + href if href.startswith("/") else f"{SITE_URL}/himoya-elonlari/{item_id}",
        "date": item.get("date") or "",
        "raw_text": api_item_text(item),
        "person": item.get("person") or "",
        "degree": item.get("degree") or "",
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
# Lotin sarlavhalar — yangi saytdagi lotin e'lonlar uchun (1000001 kabi);
# qiymat yozuvi o'zgarmaydi (kirillga o'girilmaydi).
_Q = r"[‘ʻ’'`ʼ]?"
_SECTION_HEADERS = [
    ("ro_yxat_raqami", r"Диссерта(?:ция|сия) мавзуси р[ўо]" + _Q + r"й[хҳ]атга олинган рақам"
                       r"|Dissertatsiya mavzusi ro" + _Q + r"yxatga olingan raqam"),
    ("mavzu", r"Диссерта(?:ция|сия) мавзуси|Dissertatsiya mavzusi"),
    ("ilmiy_rahbar", r"Илмий (?:ра[ҳх]бар|маслаҳатчи)|Ilmiy (?:rahbar|raxbar|maslahatchi)"),
    ("bajarilgan_muassasa", r"(?:Диссерта(?:ция|сия)|Илмий тадқиқот иши) "
                            r"бажари?(?:лган|лаётган|ган) муассаса"
                            r"|(?:Dissertatsiya|Ilmiy tadqiqot ishi) bajarilgan muassasa"),
    ("ik_muassasa", r"(?:ИК|Ик|Илмий [Кк]енгаш) фаолият кўрсатаётган муассаса"
                    r"|(?:IK|Ilmiy [Kk]engash) faoliyat ko" + _Q + r"rsatayotgan muassasa"),
    # Ba'zi e'lonlarda "ИК рақами" alohida maydon (34884) — aks holda IK
    # muassasa qiymatiga qo'shilib ketadi. Kod extract_ik_code() bilan olinadi.
    ("ik_raqami_maydon", r"(?:ИК|Илмий [Кк]енгаш|IK|Ilmiy [Kk]engash) (?:рақами|raqami)"),
    ("opponentlar", r"Расмий оппоне(?:н)?тлар|Rasmiy opponentlar"),   # "оппонетлар" ham uchraydi
    ("yetakchi_tashkilot", r"[ЕЭ]такчи\s+ташкилот|(?:Yetakchi|Etakchi)\s+tashkilot"),
    ("yo_nalish", r"Диссерта(?:ция|сия)(?:нинг)? (?:йўналиши|ё\s*[‘ʻ’']\s*налиши)"
                  r"|Dissertatsiya(?:ning)? yo" + _Q + r"nalishi"),
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
# Lotin (1000000+ e'lonlarning ko'pi, 2026-11): "…vich/…vna/… o'g'li/… qizi".
_CAP = r"[А-ЯЁЎҚҒҲA-Z][\w'ʻ’‘`-]"
_APO = r"[‘’'ʻʼ`]?"
_PATR_TAIL = (r"(?:вич|вна|vich|vna"
              r"|\s+ўғли|\s+қизи|\s+угли|\s+углу|\s+кизи"
              r"|\s+o" + _APO + r"g" + _APO + r"li|\s+ogli|\s+qizi|\s+kizi|\s+uly|\s+kyzy|\s+ugli)")
_FULL_NAME_RE = re.compile(
    r"(?<![\w-])(?:"
    + _CAP + r"+\s+" + _CAP + r"+\s+" + _CAP + r"*" + _PATR_TAIL
    + r"|" + _CAP + r"+\s+" + _CAP + r"*(?:вич|вна|vich|vna)\s+" + _CAP + r"+"
    + r")(?![\w-])")


# Ishonchsiz matn: erkin gap ("… тавсия этилади") yoki faqat bosh harfli ismlar
# ("Ж.Р. Раимбаев (…) ҳамда …", "A.A.Tursunov") — bo'lsak noto'g'ri odamlar chiqadi.
_UNRELIABLE_OPP_RE = re.compile(
    r"(?i:тавсия\s+этил|tavsiya\s+etil)"
    r"|(?<![\w.])[А-ЯЁЎҚҒҲ]\.\s?[А-ЯЁЎҚҒҲ]\.\s?[А-ЯЁЎҚҒҲ][а-яёўқғҳ]+")
# Lotin initsiallar faqat to'liq lotin matnda: kirill e'lon ichidagi "T.N.Qori
# Ниёзий номидаги …" muassasa nomi, "E.A.Asrarxanova" esa avvalgidek bo'linadi.
_UNRELIABLE_LAT_RE = re.compile(r"(?<![\w.])[A-Z]\.\s?[A-Z]\.\s?[A-Z][a-z]+")
_CYR_RE = re.compile(r"[А-Яа-яЁёЎўҚқҒғҲҳ]")


def opponents_unreliable(section):
    s = section or ""
    if _UNRELIABLE_OPP_RE.search(s):
        return True
    return not _CYR_RE.search(s) and bool(_UNRELIABLE_LAT_RE.search(s))


OPPONENT_LIMIT = 3
_UP = "А-ЯЁЎҚҒҲA-Z"
_WORD = r"[" + _UP + r"][\w'ʻ’‘`-]+"
# Daraja iborasi: har opponentda odatda bittasi ("… фанлари доктори", "(DSc)" bilan birga).
_DEGREE_RE = re.compile(
    r"(?i)(?:доктори|номзоди|doktori|nomzodi|ф\.\s?-?\s?м?\.?\s?ф\.\s?[дн]\b|[а-яa-z]\.ф\.[дн]\.)")
# Raqamlangan ro'yxat: "1. A …, 2. B …" / "1) A …".
_NUMBERED_RE = re.compile(r"(?:^|[\s,;.])\d{1,2}[.)]\s+(?=[" + _UP + r"])")
# Ismdan oldingi nuqta: "… профессор. Раҳимов Рустам, …" — oldida kamida 5 harfli
# so'z (yoki ")"), shunda "проф. Абдусалямов" / "доц. Х" bo'linmaydi.
_DOT_BEFORE_NAME_RE = re.compile(
    r"(?:(?<=[^\W\d_]{5})|(?<=\)))\.\s+(?=" + _WORD + r"\s+" + _WORD + r")")
# Otasining ismisiz ism: ", Ўлжаев Эркин, техника фанлари …" / ", Индиаминов Сайит, …"
_SHORT_NAME_CUT_RE = re.compile(
    r"[,;]\s+(?=" + _WORD + r"\s+" + _WORD + r"(?:\s+" + _WORD + r")?\s*[,–—-]\s*"
    r"[^,;]{0,40}?(?:фан|fan|доктор|doktor|номзод|nomzod|профессор|professor|академик|akademik))")
_NAME_TOKEN_RE = re.compile(r"(?<![\w.])" + _WORD)
# Ism bo'lmagan bosh harfli so'zlar (sarlavha/muassasa/qisqartma boshlari).
_NON_NAME_TOKENS = {"PhD", "DSc", "MD", "MPH", "Dr", "Prof", "Professor", "РАМН", "ЎзР", "ФА", "UzR", "FA"}


def _name_tokens(text):
    return {t for t in _NAME_TOKEN_RE.findall(text or "")
            if t not in _NON_NAME_TOKENS and len(t) > 2}


def _invariant_ok(raw, parts):
    """Ajratish xom matndagi hamma ism va daraja bo'laklarini qamraganmi."""
    if not parts or len(parts) > OPPONENT_LIMIT:
        return False
    if any(not _name_tokens(p) for p in parts):           # ismsiz bo'lak (26773)
        return False
    # kimdir tushib qolgan; manbadagi "доктори доктори" takrori bitta sanaladi (33696)
    deduped = re.sub(r"(?i)\b(\w+)(?:\s+\1\b)+", r"\1", raw)
    if len(_DEGREE_RE.findall(deduped)) > len(parts):
        return False
    joined = " ".join(parts)
    return _name_tokens(raw) <= _name_tokens(joined)       # hech bir ism yo'qolmagan


def split_opponents_checked(section, limit=OPPONENT_LIMIT):
    """"Расмий оппонентлар" → (opponentlar, ishonchsiz).

    Jonli scraper ham, backfill ham shu funksiyadan foydalanadi. Ajratib
    bo'lmasa yoki natija invariantdan o'tmasa — to'liq matn opponent_1 ga,
    ishonchsiz=True.
    """
    raw = (section or "").strip()
    if not raw:
        return [], False
    whole = [_clean(raw)]
    if opponents_unreliable(raw):
        return whole, True
    parts = _split_raw(raw)
    if len(parts) > limit or not _invariant_ok(raw, parts):
        return whole, True
    return parts, False


def split_opponents(section, limit=OPPONENT_LIMIT):
    """Moslik uchun: faqat ro'yxat (split_opponents_checked ga qarang)."""
    return split_opponents_checked(section, limit)[0]


def _split_raw(raw):
    parts = [p for p in re.split(r";", raw) if _clean(p)]
    if len(parts) == 1:
        lines = [ln for ln in raw.split("\n") if _clean(ln)]
        if len(lines) > 1:
            parts = lines
    out = []
    for part in parts:
        for sub in _split_numbered(part):
            out.extend(_split_by_names(sub))
    return _merge_nameless([_clean(p) for p in out if _clean(p)])


def _merge_nameless(parts):
    """Ismsiz bo'lak (";" dan keyingi yolg'iz "доцент") qo'shni bo'lakka qo'shiladi:
    oldingisiga, birinchi bo'lsa — keyingisiga."""
    out = []
    pending = ""
    for p in parts:
        if not _name_tokens(p):
            if out:
                out[-1] = out[-1] + ", " + p
            else:
                pending = (pending + ", " + p) if pending else p
            continue
        out.append((pending + ", " + p) if pending else p)
        pending = ""
    if pending:
        out.append(pending)
    return out


_DEGREE_FIRST_RE = re.compile(
    r"(?i)^(?:[\w'ʻ’-]+\s+фан(?:лари|лар)\b|[\w'ʻ’-]+\s+fan(?:lari|lar)\b|[а-яa-z]{1,3}\.\s?-?\s?[а-яa-z]?\.?\s?ф\.)")


def _degree_first(text):
    lead = re.sub(r"^[\W\d_]+", "", text)
    return lead[:1].islower() or bool(_DEGREE_FIRST_RE.match(lead))


def _split_numbered(part):
    text = re.sub(r"\s+", " ", part).strip()
    if not re.match(r"^\d{1,2}[.)]\s", text):
        return [text]
    pieces = [p for p in _NUMBERED_RE.split(" " + text) if p and p.strip(" ,;.")]
    return pieces or [text]


def _split_by_names(part):
    """Bitta bo'lakda bir nechta ism bo'lsa — ism bo'yicha ajratadi.

    1) To'liq ism (otasining ismi bilan) bo'yicha: ism oldin ("A A A, daraja,
       unvon, Б Б Б, …") — yangi opponent ismdan boshlanadi; daraja oldin, ya'ni
       kichik harf bilan ("daraja, unvon A A A, daraja, unvon Б Б Б") — opponent
       ism bilan tugaydi.
    2) Bo'lmasa: ismdan oldingi nuqta ("… профессор. Раҳимов Рустам, …").
    3) Bo'lmasa: otasining ismisiz ism + daraja (", Ўлжаев Эркин, техника …").
    """
    text = re.sub(r"\s+", " ", part).strip()
    names = list(_FULL_NAME_RE.finditer(text))
    if len(names) >= 2:
        if _degree_first(text):
            cuts = [m.end() for m in names[:-1]]
        else:
            cuts = [m.start() for m in names if m.start() > 0]
    else:
        cuts = [m.end() for m in _DOT_BEFORE_NAME_RE.finditer(text)]
        if not cuts and not _degree_first(text):
            cuts = [m.end() for m in _SHORT_NAME_CUT_RE.finditer(text) if m.end() > 0]
        if not cuts:
            return [text]
    bounds = [0] + cuts + [len(text)]
    pieces = [text[a:b] for a, b in zip(bounds, bounds[1:])]
    return [re.sub(r"^[\s,.;]*(?:ва|ҳамда|va|hamda)?\s+", "", piece) for piece in pieces]


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
    opp_raw = sections.get("opponentlar") or result["opponentlar"]
    result["opponentlar_list"], result["opponentlar_ishonchsiz"] = split_opponents_checked(opp_raw)
    result["opponentlar_xom"] = _clean(opp_raw)
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
    """last_state.json — faqat ma'lumot uchun (oxirgi run, har ro'yxat bo'yicha
    sonlar). Yangi e'lonni aniqlashda ISHLATILMAYDI; eski last_id_* kalitlari
    o'zgarishsiz saqlanadi."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            log.warning("%s o'qilmadi — yangisi yoziladi", STATE_FILE)
    return {}


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

    # Har bir ro'yxat boshidan o'qiladi; "yangi" = DB ga haqiqatan qo'shilgan.
    global LAST_RUN_ERRORS, LAST_RUN_STATS
    LAST_RUN_ERRORS = []
    LAST_RUN_STATS = {}
    if conn is None:
        # DB'siz "yangi"ni aniqlab bo'lmaydi (watermark yo'q) — xato sifatida.
        LAST_RUN_ERRORS.append("DB ulanmagan — yangi e'lonlarni aniqlab bo'lmadi")
        log.error(LAST_RUN_ERRORS[-1])
        sources = []
    else:
        sources = SOURCES
    for source in sources:
        list_id = source["list"]
        empty_streak = 0
        pages_read = 0
        stop_reason = f"MAX_PAGES={MAX_PAGES} chegarasi"
        list_new = 0

        print("-" * 50)
        print("Manba: " + source["nomi"] + " (" + API_URL + "?list=" + str(list_id) + ")")
        print("-" * 50)

        for page_num in range(1, MAX_PAGES + 1):
            print("Sahifa " + str(page_num) + " tekshirilmoqda...")
            data = fetch_list_page(list_id, page_num)
            raw_items = (data or {}).get("items") or []
            if not raw_items:
                if data is None or page_num == 1:
                    # Bo'sh/xato javob — cron log'ida ko'rinsin, exit 1.
                    stop_reason = "API bo'sh yoki xato javob (sahifa " + str(page_num) + ")"
                    msg = f"list={list_id}: {stop_reason}"
                    log.error(msg)
                    LAST_RUN_ERRORS.append(msg)
                else:
                    stop_reason = "ro'yxat oxiri"
                break
            pages_read += 1

            items = []
            for raw in raw_items:
                try:
                    items.append(api_list_item(raw))
                except (KeyError, TypeError, ValueError):
                    log.warning("list=%s: tushunilmagan element o'tkazildi: %r",
                                list_id, str(raw)[:80])

            page_new = 0
            for item in items:
                parsed = parse_announcement(item["raw_text"])
                record = _build_record(item, parsed)
                if parsed["opponentlar_ishonchsiz"]:
                    log.warning("ID=%s: opponentlar matni ishonchsiz — bo'linmadi, "
                                "opponent_1 ga xom yozildi", item["id"])

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
                    page_new += 1
                    print("  + Yangi: ID=" + str(item["id"]) + " | " + item["date"])
                    if dry_run:
                        _print_record(record)
                elif status == "error":
                    error_total += 1
                else:
                    skipped_total += 1

            list_new += page_new
            empty_streak = 0 if page_new else empty_streak + 1
            time.sleep(DELAY)
            if pages_read >= MIN_PAGES and empty_streak >= STOP_EMPTY_PAGES:
                stop_reason = (f"ketma-ket {empty_streak} sahifada yangi yo'q "
                               f"({pages_read} sahifa o'qildi)")
                break
            if isinstance(data.get("lastPage"), int) and page_num >= data["lastPage"]:
                stop_reason = "ro'yxat oxiri (lastPage)"
                break
        else:
            # MAX_PAGES tugadi, oxirgi sahifalarda hali yangi bor edi — chuqurroq
            # yangilar qolgan bo'lishi mumkin.
            if empty_streak < STOP_EMPTY_PAGES:
                msg = (f"list={list_id}: {MAX_PAGES} sahifa chegarasi, oxirgi sahifalarda hali "
                       f"yangi bor — --max-pages bilan qayta ishga tushiring")
                log.error(msg)
                LAST_RUN_ERRORS.append(msg)

        print(f"list={list_id}: {list_new} ta yangi; to'xtash: {stop_reason}")
        LAST_RUN_STATS[str(list_id)] = {"yangi": list_new, "sahifa": pages_read,
                                        "toxtash": stop_reason}

    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

    if not dry_run:
        # State fayli — faqat ma'lumot uchun.
        state["last_run"] = datetime.now().isoformat()
        state["last_run_lists"] = LAST_RUN_STATS
        state["last_run_errors"] = LAST_RUN_ERRORS
        save_state(state)
        # Natijani JSON ga saqlash (faqat yangi qo'shilganlar — log uchun)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(new_items, f, ensure_ascii=False, indent=2)

    print("\nAniqlandi: " + str(len(new_items)) + " ta yangi, "
          + str(skipped_total) + " ta mavjud (skip), "
          + str(error_total) + " ta xato."
          + ("" if conn is not None else " (DB ulanmagan — hech narsa yozilmadi)"))

    # Bildirishnomalar — faqat shu ishga tushishda qo'shilgan qatorlar uchun.
    enabled = notify_enabled()
    if dry_run:
        if new_items and enabled:
            run_notifications(records=[_notify_record(r) for r in new_items])
        elif new_items:
            print("Bildirishnoma o'chiq (OAK_NOTIFY_ENABLED=0): "
                  + str(len(new_items)) + " ta yangi qator notified_at bilan belgilanardi.")
    elif new_ids:
        if enabled:
            run_notifications(ids=new_ids)
        else:
            mark_silently(new_ids)

    return new_items


LAST_RUN_ERRORS = []
LAST_RUN_STATS = {}


def notify_enabled():
    """OAK_NOTIFY_ENABLED=1/true/yes/on — aks holda (default) o'chiq."""
    return os.environ.get("OAK_NOTIFY_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def mark_silently(ids):
    """Bildirishnoma o'chiq: qatorlar notified_at bilan belgilanadi, xabar ketmaydi."""
    try:
        from services import oak_notify
        n = oak_notify.mark_without_notify(ids)
        print("Bildirishnoma o'chiq (OAK_NOTIFY_ENABLED=0): " + str(n)
              + " ta qator notified_at bilan belgilandi, xabar yuborilmadi.")
        return n
    except Exception:
        log.exception("notified_at belgilanmadi (scraper davom etadi)")
        return None


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
    # Olim ismini sarlavhadan tozalab ajratish; API'da "person" zaxira manba.
    title = fix_homoglyphs(item.get("title", ""))
    olim = extract_author_from_title(title)
    if not olim and item.get("person"):
        olim = re.sub(r"(нинг|ни)$", "", fix_homoglyphs(item["person"]).strip())

    # Daraja: avval sarlavhadan, keyin API "degree", keyin e'lonning o'z matnidan.
    api_degree = item.get("degree") if item.get("degree") in ("PhD", "DSc") else ""
    daraja = degree_from_title(title) or api_degree or parsed.get("daraja", "")
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
    parser.add_argument("--max-pages", type=int, default=None,
                        help="har ro'yxat uchun sahifa chegarasi (uzoq uzilishdan keyin yetkazib olish)")
    args = parser.parse_args(argv)
    if args.max_pages:
        global MAX_PAGES
        MAX_PAGES = args.max_pages
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
    if LAST_RUN_ERRORS:
        # cron/monitoring ko'rishi uchun: manba javob bermadi.
        raise SystemExit(1)


if __name__ == "__main__":
    cli()
