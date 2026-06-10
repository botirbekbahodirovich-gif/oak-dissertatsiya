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
BASE_URL = "https://oak.uz/page/8"
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
            return json.load(f)
    return {"last_id": 0, "last_run": None}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ----------------- ASOSIY -----------------
def main():
    state = load_state()
    last_id = state.get("last_id", 0)
    print("=" * 50)
    print("OAK.UZ kunlik scraper")
    print("Oxirgi ID: " + str(last_id))
    print("Sana: " + datetime.now().isoformat())
    print("=" * 50)

    new_items = []
    max_id_seen = last_id

    for page_num in range(1, MAX_PAGES + 1):
        url = f"{BASE_URL}?page={page_num}" if page_num > 1 else BASE_URL
        print("Sahifa " + str(page_num) + " tekshirilmoqda...")
        html = fetch(url)
        if html is None:
            continue

        items = parse_list_page(html)
        if not items:
            break

        page_ids = [it["id"] for it in items]
        page_max = max(page_ids)
        page_min = min(page_ids)

        # Eng katta ID ni eslab qolamiz
        if page_max > max_id_seen:
            max_id_seen = page_max

        # Agar sahifadagi barcha ID lar last_id dan kichik — to'xtaymiz
        if page_min <= last_id and last_id > 0:
            # Faqat yangilarini olamiz
            for item in items:
                if item["id"] > last_id:
                    parsed = parse_announcement(item["raw_text"])
                    new_items.append(_build_record(item, parsed))
            print("  Eski e'lonlarga yetildi, to'xtatildi.")
            break

        # Hammasi yangi
        for item in items:
            if item["id"] > last_id:
                parsed = parse_announcement(item["raw_text"])
                new_items.append(_build_record(item, parsed))
                print("  + Yangi: ID=" + str(item["id"]) + " | " + item["date"])

        time.sleep(DELAY)

    print("\nTopildi: " + str(len(new_items)) + " ta yangi e'lon")

    # State yangilash
    if max_id_seen > last_id:
        state["last_id"] = max_id_seen
        state["last_run"] = datetime.now().isoformat()
        save_state(state)
        print("last_id yangilandi: " + str(max_id_seen))

    # Natijani saqlash
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(new_items, f, ensure_ascii=False, indent=2)

    return new_items


def _build_record(item, parsed):
    return {
        "ID": item["id"],
        "Sana": item["date"],
        "Sarlavha": item["title"],
        "Havola": item["link"],
        "Daraja": parsed["daraja"],
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
        "Yonalish": parsed["yo_nalish"],
    }


if __name__ == "__main__":
    items = main()
