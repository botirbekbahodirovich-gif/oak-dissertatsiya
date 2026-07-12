# ============================================================
# OAK.UZ — Kunlik avtomatik scraper
# GitHub Actions uchun
# ============================================================

import requests
from bs4 import BeautifulSoup
import json
import re
import time
import os
from datetime import datetime

# ----------------- SOZLAMALAR -----------------
# Bir nechta manba — har biri bir xil strukturada, alohida scraping qilinadi.
SOURCES = [
    {"url": "https://oak.uz/page/8",  "yonalish": "Oddiy"},
    {"url": "https://oak.uz/page/31", "yonalish": "Mustaqil ilmiy kengash"},
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


def parse_announcement(text):
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
    result = {k: extract_field(text, v) for k, v in fields.items()}
    codes = re.findall(r"\b(\d{2}\.\d{2}\.\d{2})\b", text)
    result["ixtisoslik_shifrlari"] = sorted(set(codes))
    result["fan_tarmogi"] = extract_science_branch(text)
    result["ik_raqami"] = extract_ik_code(text)
    result["daraja"] = detect_degree(text)
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
def main():
    state = load_state()
    print("=" * 50)
    print("OAK.UZ kunlik scraper (" + str(len(SOURCES)) + " manba)")
    print("Sana: " + datetime.now().isoformat())
    print("=" * 50)

    new_items = []
    added_total = 0
    skipped_total = 0

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
        yonalish = source["yonalish"]
        key = _state_key(src_url)
        last_id = state.get(key, 0)
        max_id_seen = last_id
        consec_skip = 0
        stop = False

        print("-" * 50)
        print("Manba: " + yonalish + " (" + src_url + ")")
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

                # "Yangi" ni state fayldagi watermark (last_id) bo'yicha aniqlaymiz.
                # Bu bazaga ulanishga bog'liq EMAS — GitHub Actions da psycopg2
                # o'rnatilmagani uchun conn=None bo'lib, avval hamma element "skip"
                # bo'lib qolar edi va yangi e'lonlar hech qachon aniqlanmasdi.
                is_new = item["id"] > last_id

                parsed = parse_announcement(item["raw_text"])
                record = _build_record(item, parsed, yonalish)

                # Bazaga yozish — faqat imkon bo'lsa (mahalliy ishga tushirishda).
                # Saytga yuborish esa new_items.json orqali send_to_site.py bilan
                # amalga oshadi, shuning uchun DB bu yerda majburiy emas.
                if conn is not None:
                    db_save_one(conn, record)

                if is_new:
                    new_items.append(record)
                    added_total += 1
                    consec_skip = 0
                    print("  + Yangi: ID=" + str(item["id"]) + " | " + item["date"])
                else:
                    skipped_total += 1
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

    # State faylini bir marta saqlaymiz
    state["last_run"] = datetime.now().isoformat()
    save_state(state)

    print("\nTopildi: " + str(len(new_items)) + " ta yangi e'lon")

    # Natijani JSON ga saqlash (faqat yangi qo'shilganlar — log uchun)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(new_items, f, ensure_ascii=False, indent=2)

    print("Aniqlandi: " + str(added_total) + " ta yangi, "
          + str(skipped_total) + " ta eski (skip)."
          + ("" if conn is not None else " (DB ulanmagan — faqat saytga yuboriladi)"))

    return new_items


# ── Bazaga yozish yordamchilari ─────────────────────────────────────────────

_INSERT_SQL = """
    INSERT INTO dissertations (
        oak_id, sana, olim, daraja, mavzu, ixtisoslik, fan_tarmoqi,
        mavzu_raqami, ilmiy_rahbar, muassasa, ilmiy_kengash,
        ilmiy_kengash_raqami, opponent_1, yetakchi_tashkilot, link, yonalish
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
    )
    ON CONFLICT (oak_id) DO NOTHING
"""


def _item_values(item):
    """_build_record() lug'atini INSERT ustunlari tartibiga moslaydi."""
    # "Rasmiy opponentlar" — matn; birinchi opponentni ajratamiz.
    opponents = item.get("Rasmiy opponentlar") or ""
    opponent_1 = opponents.split(";")[0].strip() if opponents else ""
    return (
        str(item.get("ID", "")),                # oak_id
        item.get("Sana", ""),                   # sana
        item.get("Olim", ""),                   # olim
        item.get("Daraja", ""),                 # daraja
        item.get("Mavzu va ixtisoslik", ""),    # mavzu
        item.get("Ixtisoslik shifrlari", ""),   # ixtisoslik
        item.get("Fan tarmogi", ""),            # fan_tarmoqi
        item.get("Royxat raqami", ""),          # mavzu_raqami
        item.get("Ilmiy rahbar", ""),           # ilmiy_rahbar
        item.get("Bajarilgan muassasa", ""),    # muassasa
        item.get("IK muassasa", ""),            # ilmiy_kengash
        item.get("IK raqami", ""),              # ilmiy_kengash_raqami
        opponent_1,                             # opponent_1
        item.get("Yetakchi tashkilot", ""),     # yetakchi_tashkilot
        item.get("Havola", ""),                 # link
        item.get("Yonalish", ""),               # yonalish
    )


def db_save_one(conn, item):
    """Bitta elementni bazaga yozadi. 'added' / 'skip' / 'error' qaytaradi."""
    try:
        with conn.cursor() as cur:
            cur.execute(_INSERT_SQL, _item_values(item))
            hit = cur.rowcount
        conn.commit()
        return "added" if hit else "skip"
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
        status = db_save_one(conn, item)
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


def _build_record(item, parsed, yonalish=None):
    import re

    # Olim ismini sarlavhadan tozalab ajratish
    title = item.get("title", "")
    olim = ""
    
    # Kirill: "Фамилия Исм Отасининг фалсафа/фан доктори..."
    m = re.search(
        r'^([А-ЯЎҚҒҲа-яўқғҳёЁ\s\'\-\.]+?)нинг\s+(?:фалсафа|фан)\s+доктори',
        title.strip()
    )
    if m:
        olim = m.group(1).strip()
    else:
        # Lotin: "Familiya Ism Otasinining falsafa/fan doktori..."
        m = re.search(
            r'^([A-Za-z\s\'\-\.]+?)ning\s+(?:falsafa|fan)\s+doktori',
            title.strip(), re.IGNORECASE
        )
        if m:
            olim = m.group(1).strip()
        else:
            # Zaxira: "нинг" gacha
            m = re.search(r'^(.+?)нинг', title)
            if m:
                olim = m.group(1).strip()
    
    # Daraja aniqlashtirish
    daraja = parsed.get("daraja", "")
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
        "Mavzu va ixtisoslik": parsed["mavzu"],
        "Ixtisoslik shifrlari": ", ".join(parsed["ixtisoslik_shifrlari"]),
        "Fan tarmogi": parsed["fan_tarmogi"],
        "Royxat raqami": parsed["ro_yxat_raqami"],
        "Ilmiy rahbar": parsed["ilmiy_rahbar"],
        "Bajarilgan muassasa": parsed["bajarilgan_muassasa"],
        "IK muassasa": parsed["ik_muassasa"],
        "IK raqami": parsed["ik_raqami"],
        "Rasmiy opponentlar": parsed["opponentlar"],
        "Yetakchi tashkilot": parsed["yetakchi_tashkilot"],
        # Manba "yonalish" i berilgan bo'lsa — o'shani yozamiz, aks holda parsed.
        "Yonalish": yonalish if yonalish else parsed["yo_nalish"],
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


if __name__ == "__main__":
    items = main()
    if items:
        ping_google()
