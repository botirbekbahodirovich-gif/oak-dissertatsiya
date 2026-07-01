"""
OAK.UZ kunlik scraper — ro'yxat sahifasidan ma'lumot oladi.
Faqat yangi e'lonlarni (last oak_id dan kattalarini) oladi.
"""
import os
import sys
import re
import time
import json
import psycopg2
from datetime import datetime

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("pip install requests beautifulsoup4")

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    sys.exit("DATABASE_URL not set")

BASE_URL   = "https://oak.uz/page/8"
MAX_PAGES  = 10
DELAY      = 1.0
TIMEOUT    = 20
HEADERS    = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
}

FAN_TARMOQLARI = [
    "тиббиёт","филология","техника","педагогика","тарих",
    "биология","иқтисодиёт","кимё","қишлоқ хўжалиги",
    "физика","математика","юридик","фалсафа","психология",
    "социология","сиёсат","география","геология","архитектура",
    "санъатшунослик","ветеринария","фармацевтика",
]


# ── DB ──────────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def get_max_oak_id(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(MAX(oak_id::integer), 0) "
            "FROM dissertations WHERE oak_id ~ '^[0-9]+$'"
        )
        return cur.fetchone()[0]


def insert_item(conn, item: dict):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO dissertations (
                oak_id, link, olim, daraja, mavzu,
                ixtisoslik, fan_tarmogi, mavzu_raqami,
                ilmiy_rahbar, muassasa,
                ilmiy_kengash_raqami,
                opponent_1, opponent_2,
                yetakchi_tashkilot, yonalish,
                photo_url, ilmiy_rahbar_photo_url,
                scraped_at
            ) VALUES (
                %(oak_id)s, %(link)s, %(olim)s, %(daraja)s, %(mavzu)s,
                %(ixtisoslik)s, %(fan_tarmogi)s, %(mavzu_raqami)s,
                %(ilmiy_rahbar)s, %(muassasa)s,
                %(ilmiy_kengash_raqami)s,
                %(opponent_1)s, %(opponent_2)s,
                %(yetakchi_tashkilot)s, %(yonalish)s,
                %(photo_url)s, %(ilmiy_rahbar_photo_url)s,
                NOW()
            )
            ON CONFLICT (oak_id) DO NOTHING
        """, item)
    conn.commit()


# ── PARSE ────────────────────────────────────────────────────────────────────

def fetch(url, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.text
        except Exception:
            pass
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


def extract_science_branch(text):
    m = re.search(
        r"\(([а-яёўқғҳА-ЯЁЎҚҒҲ\s\-]+)\s+фанлари\)",
        text, re.IGNORECASE
    )
    if m:
        return m.group(1).strip().lower()
    m = re.search(
        r"([а-яёўқғҳА-ЯЁЎҚҒҲ\s\-]+)\s+фанлари\s+(?:доктори|номзоди)",
        text, re.IGNORECASE
    )
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
    m = re.search(
        r"((?:DSc|PhD)\.\d+/\d+\.\d+\.\d+[\.\w/]*\d+)", text
    )
    return m.group(1) if m else ""


def detect_degree(text):
    if re.search(
        r"\bDSc\b|фан доктори\s*\(DSc\)|doktori\s*\(DSc\)",
        text, re.IGNORECASE
    ):
        return "DSc"
    if re.search(
        r"\bPhD\b|фалсафа доктори|falsafa doktori",
        text, re.IGNORECASE
    ):
        return "PhD"
    return ""


AVATAR_BUCKET = ("https://qzbgmfbpryneyacrcdfh.supabase.co/storage/v1/"
                 "object/public/avatars/")
_AVATAR_STRIP = "'\"’‘ʻʼ`´"


def generate_avatar_url(olim_name: str):
    """Fix: yangi olimlar uchun Supabase avatar URL (ismni tozalab)."""
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


def normalize_patronymic(name: str) -> str:
    """Fix 5.5: standardize Uzbek patronymic suffixes on scholar records.

    Unifies mixed apostrophes/typos of "o'g'li" (oʻgʻli, o’g’li, ogli, ...)
    and "qizi" onto a canonical `o'g'li` / `qizi`.
    """
    if not name:
        return name
    s = re.sub(r"[’‘ʻʼ`´]", "'", name)
    s = re.sub(r"\bo'?\s*g'?\s*li\b", "o'g'li", s, flags=re.IGNORECASE)
    s = re.sub(r"\bqiz[iy]\b", "qizi", s, flags=re.IGNORECASE)
    return s


def extract_olim_name(title: str) -> str:
    """Sarlavhadan olim ismini ajratish."""
    m = re.search(
        r"^([А-ЯЎҚҒҲа-яўқғҳёЁ][а-яўқғҳёЁ]+(?:\s+[А-ЯЎҚҒҲа-яўқғҳёЁ][а-яўқғҳёЁ]+){1,3})",
        title.strip()
    )
    return m.group(1).strip() if m else ""


def split_opponents(opponents_text: str):
    """Opponentlar matnini ikkiga ajratish."""
    if not opponents_text:
        return "", ""
    parts = re.split(r";\s*", opponents_text)
    opp1 = parts[0].strip() if len(parts) > 0 else ""
    opp2 = parts[1].strip() if len(parts) > 1 else ""
    return opp1, opp2


def parse_announcement(text, title=""):
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
        "yo_nalish": [
            r"Диссертация йўналиши",
            r"Диссертасия йўналиши",
        ],
    }

    result = {k: extract_field(text, v) for k, v in fields.items()}

    codes = re.findall(r"\b(\d{2}\.\d{2}\.\d{2})\b", text)
    result["ixtisoslik_shifrlari"] = ", ".join(sorted(set(codes)))
    result["fan_tarmogi"]          = extract_science_branch(text)
    result["ik_raqami"]            = extract_ik_code(text)
    result["daraja"]               = detect_degree(text)
    result["olim"]                 = extract_olim_name(title)

    opp1, opp2 = split_opponents(result["opponentlar"])
    result["opponent_1"] = opp1
    result["opponent_2"] = opp2

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
        link  = h3.find("a")["href"]
        if link.startswith("/"):
            link = "https://oak.uz" + link
        m = re.search(r"/pages/(\d+)", link)
        item_id = int(m.group(1)) if m else 0
        text_block = block.get_text(" ", strip=True)
        date_m = re.search(r"\b(\d{2}\.\d{2}\.\d{4})\b", text_block)
        date   = date_m.group(1) if date_m else ""
        full   = block.get_text("\n", strip=True)
        clean  = full.replace(title, "", 1)
        clean  = re.sub(r"\bBatafsil\b", "", clean)
        clean  = re.sub(r"^\s*" + re.escape(date), "", clean)
        items.append({
            "id": item_id, "title": title, "link": link,
            "date": date,  "raw_text": clean.strip()
        })
    seen, unique = set(), []
    for it in items:
        if it["link"] not in seen:
            seen.add(it["link"])
            unique.append(it)
    return unique


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    conn = get_conn()
    print("DB ga ulandi")

    last_id = get_max_oak_id(conn)
    print("Oxirgi oak_id: " + str(last_id))
    print("Sana: " + datetime.now().isoformat())

    new_count    = 0
    pages_checked = 0
    max_id_seen  = last_id

    for page_num in range(1, MAX_PAGES + 1):
        url  = f"{BASE_URL}?page={page_num}" if page_num > 1 else BASE_URL
        print("Sahifa " + str(page_num) + " tekshirilmoqda...")
        html = fetch(url)
        if html is None:
            pages_checked += 1
            continue

        items = parse_list_page(html)
        pages_checked += 1
        if not items:
            print("  Sahifada e'lon topilmadi, to'xtatildi.")
            break

        page_ids = [it["id"] for it in items]
        page_max = max(page_ids)
        if page_max > max_id_seen:
            max_id_seen = page_max

        # Stop when every link on this page is already in the DB
        if last_id > 0 and all(it["id"] <= last_id for it in items):
            print("  Barcha e'lonlar allaqachon bazada, to'xtatildi.")
            break

        for item in items:
            if item["id"] <= last_id:
                continue
            parsed = parse_announcement(item["raw_text"], item["title"])
            opp1, opp2 = parsed["opponent_1"], parsed["opponent_2"]
            olim_name = normalize_patronymic(parsed["olim"])
            rahbar_name = normalize_patronymic(parsed["ilmiy_rahbar"])
            db_item = {
                "oak_id":               str(item["id"]),
                "link":                 item["link"],
                "olim":                 olim_name,
                "daraja":               parsed["daraja"],
                "mavzu":                parsed["mavzu"],
                "ixtisoslik":           parsed["ixtisoslik_shifrlari"],
                "fan_tarmogi":          parsed["fan_tarmogi"],
                "mavzu_raqami":         parsed["ro_yxat_raqami"],
                "ilmiy_rahbar":         rahbar_name,
                "muassasa":             parsed["bajarilgan_muassasa"],
                "ilmiy_kengash_raqami": parsed["ik_raqami"],
                "opponent_1":           opp1,
                "opponent_2":           opp2,
                "yetakchi_tashkilot":   parsed["yetakchi_tashkilot"],
                "yonalish":             parsed["yo_nalish"],
                "photo_url":            generate_avatar_url(olim_name),
                "ilmiy_rahbar_photo_url": generate_avatar_url(rahbar_name),
            }
            try:
                insert_item(conn, db_item)
                new_count += 1
                print("  + " + str(item["id"]) + " | " + item["date"] + " | " + item["title"][:60])
            except Exception as e:
                print("  ! Xato " + str(item["id"]) + ": " + str(e))

        time.sleep(DELAY)

    if new_count > 0:
        try:
            conn2 = get_conn()
            with conn2.cursor() as cur:
                cur.execute(
                    "INSERT INTO notifications (message, count) VALUES (%s, %s)",
                    (f"{new_count} ta yangi himoya e'loni qo'shildi", new_count)
                )
            conn2.commit()
            conn2.close()
            print("Bildirishnoma saqlandi: " + str(new_count) + " ta yangi e'lon")
        except Exception as e:
            print("Bildirishnoma xatosi: " + str(e))

    conn.close()
    print("=" * 50)
    print("Tekshirilgan sahifalar: " + str(pages_checked))
    print("Yangi qo'shildi: " + str(new_count))
    print("Oxirgi ko'rilgan ID: " + str(max_id_seen))


if __name__ == "__main__":
    main()
