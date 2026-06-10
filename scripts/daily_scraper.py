"""
Daily scraper for oak.uz dissertations.
Fetches new dissertation pages and inserts them into PostgreSQL.
"""
import os
import sys
import time
import re
import psycopg2
from dotenv import load_dotenv

load_dotenv()

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Missing deps: pip install requests beautifulsoup4")

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    sys.exit("DATABASE_URL not set")

LIST_URL = "https://oak.uz/page/8?page=1"
BASE_URL = "https://oak.uz"
DELAY = 1.5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; OAK-scraper/1.0; +https://github.com)"
    )
}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def ensure_columns(conn):
    """Add scraper-specific columns if they don't exist yet."""
    extra_cols = [
        ("oak_id",               "TEXT UNIQUE"),
        ("ixtisoslik_nomi",      "TEXT"),
        ("mavzu_raqami",         "TEXT"),
        ("ilmiy_rahbar_daraja",  "TEXT"),
        ("ilmiy_kengash_raqami", "TEXT"),
        ("opponent_1",           "TEXT"),
        ("opponent_2",           "TEXT"),
        ("yetakchi_tashkilot",   "TEXT"),
    ]
    with conn.cursor() as cur:
        for col, col_type in extra_cols:
            cur.execute(
                """
                ALTER TABLE dissertations
                ADD COLUMN IF NOT EXISTS %s %s
                """ % (col, col_type)  # col names can't be parameterised
            )
    conn.commit()


def existing_oak_ids(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT oak_id FROM dissertations WHERE oak_id IS NOT NULL")
        return {row[0] for row in cur.fetchall()}


def max_oak_id(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(CAST(oak_id AS INTEGER)) FROM dissertations WHERE oak_id ~ '^[0-9]+$'")
        row = cur.fetchone()
        return row[0] if row and row[0] else 0


def is_valid_record(data: dict) -> bool:
    mavzu = (data.get("mavzu") or "").strip()
    olim = (data.get("olim") or "").strip()
    muassasa = (data.get("muassasa") or "").strip()
    ilmiy_rahbar = (data.get("ilmiy_rahbar") or "").strip()
    if not mavzu or len(mavzu) <= 20:
        return False
    if not olim:
        return False
    if "attestatsiya komissiyasi" in mavzu.lower():
        return False
    if "Fanlar akademiyasi" in mavzu:
        return False
    if mavzu == muassasa:
        return False
    if not ilmiy_rahbar:
        return False
    return True


def insert_dissertation(conn, data: dict):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dissertations
                (oak_id, olim, daraja, mavzu, ixtisoslik, ixtisoslik_nomi,
                 mavzu_raqami, ilmiy_rahbar, ilmiy_rahbar_daraja, muassasa,
                 ilmiy_kengash_raqami, opponent_1, opponent_2,
                 yetakchi_tashkilot, link)
            VALUES
                (%(oak_id)s, %(olim)s, %(daraja)s, %(mavzu)s, %(ixtisoslik)s,
                 %(ixtisoslik_nomi)s, %(mavzu_raqami)s, %(ilmiy_rahbar)s,
                 %(ilmiy_rahbar_daraja)s, %(muassasa)s,
                 %(ilmiy_kengash_raqami)s, %(opponent_1)s, %(opponent_2)s,
                 %(yetakchi_tashkilot)s, %(link)s)
            ON CONFLICT (oak_id) DO NOTHING
            """,
            data,
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Scraping helpers
# ---------------------------------------------------------------------------

def fetch(url: str):
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def get_page_links(html: str, min_oak_id: int = 0) -> list[str]:
    """Return /pages/ links with oak_id > min_oak_id, sorted DESC, top 20."""
    soup = BeautifulSoup(html, "html.parser")
    seen = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/pages/" in href:
            if href.startswith("/"):
                href = BASE_URL + href
            oid = oak_id_from_url(href)
            if oid.isdigit() and int(oid) > min_oak_id:
                seen[oid] = href
    sorted_ids = sorted(seen.keys(), key=lambda x: int(x), reverse=True)
    return [seen[k] for k in sorted_ids[:20]]


def oak_id_from_url(url: str) -> str:
    """Extract the numeric id from a /pages/view/NNN URL."""
    m = re.search(r"/pages/(?:view/)?(\d+)", url)
    return m.group(1) if m else url.split("/")[-1]


def _cell_text(tag) -> str:
    return tag.get_text(" ", strip=True) if tag else ""


def parse_dissertation(html: str, url: str) -> dict:
    """
    Parse a single dissertation page.
    Looks for the Umumiy ma'lumotlar / Умумий маълумотлар section
    and extracts key→value pairs from a definition-list or table.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Locate the info block — try <table>, then <dl>
    info: dict[str, str] = {}

    # Strategy 1: find a table whose header row contains Cyrillic "Умумий ма"
    target_table = None
    for table in soup.find_all("table"):
        text = table.get_text()
        if "Умумий ма" in text or "Umumiy ma" in text:
            target_table = table
            break

    if target_table:
        for tr in target_table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) >= 2:
                key = _cell_text(cells[0]).strip(": ")
                val = _cell_text(cells[1]).strip()
                if key:
                    info[key] = val
    else:
        # Strategy 2: look for a <dl> or any key:value pairs near the section
        for dl in soup.find_all("dl"):
            terms = dl.find_all("dt")
            defs  = dl.find_all("dd")
            for dt, dd in zip(terms, defs):
                info[dt.get_text(strip=True)] = dd.get_text(strip=True)

    # Helper: search by substring match in multiple languages
    def get(*keys: str) -> str:
        for key in keys:
            for k, v in info.items():
                if key.lower() in k.lower():
                    return v
        return ""

    # Title / mavzu — often in an <h1> or <h2> if not in table
    mavzu = get("mavzu", "диссертация", "тема", "Mavzu")
    if not mavzu:
        for tag in soup.find_all(["h1", "h2", "h3"]):
            t = tag.get_text(strip=True)
            if len(t) > 20:
                mavzu = t
                break

    # Olim name — extract from h1 using "нинг" possessive suffix pattern
    olim = get("Olim", "Илм", "olim", "Диссертант", "диссертант")
    if not olim:
        title_tag = soup.find("h1")
        if title_tag:
            t = title_tag.get_text(strip=True)
            m = re.search(r'([А-ЯЎҚҒҲа-яўқғҳёЁ\s]+?)нинг', t)
            if m:
                olim = m.group(1).strip().split('\n')[-1].strip()

    return {
        "oak_id":               oak_id_from_url(url),
        "link":                 url,
        "olim":                 olim,
        "daraja":               get("Daraja", "Ilmiy daraja", "daraja", "Учёная степень"),
        "mavzu":                mavzu,
        "ixtisoslik":           get("Ixtisoslik shifri", "Ixtisoslik kodi", "Ихтисослик шифри", "Специальность"),
        "ixtisoslik_nomi":      get("Ixtisoslik nomi", "Ихтисослик номи"),
        "mavzu_raqami":         get("Mavzu raqami", "Мавзу рақами", "Qaror"),
        "ilmiy_rahbar":         get("Ilmiy rahbar", "Илмий раҳбар", "rahbar", "раҳбар"),
        "ilmiy_rahbar_daraja":  get("Ilmiy rahbar daraja", "Илмий раҳбар даража"),
        "muassasa":             get("Muassasa", "Муассаса", "Tashkilot", "Ташкилот"),
        "ilmiy_kengash_raqami": get("Ilmiy kengash", "Илмий кенгаш"),
        "opponent_1":           get("Rasmiy opponent 1", "Расмий оппонент 1", "Opponent 1"),
        "opponent_2":           get("Rasmiy opponent 2", "Расмий оппонент 2", "Opponent 2"),
        "yetakchi_tashkilot":   get("Yetakchi tashkilot", "Етакчи ташкилот"),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    conn = get_conn()
    print("Connected to database")

    ensure_columns(conn)
    print("Schema up-to-date")

    # Fetch listing page
    print(f"Fetching listing: {LIST_URL}")
    try:
        html = fetch(LIST_URL)
    except Exception as e:
        sys.exit(f"Failed to fetch listing page: {e}")

    db_max = max_oak_id(conn)
    print(f"Max oak_id in DB: {db_max}")

    links = get_page_links(html, min_oak_id=db_max)
    print(f"Found {len(links)} new dissertation links (oak_id > {db_max})")

    if not links:
        print("No new links found — DB is up to date")
        conn.close()
        return

    known = existing_oak_ids(conn)
    print(f"Already in DB: {len(known)} records with oak_id")

    new_count = 0
    error_count = 0

    for i, url in enumerate(links, 1):
        oak_id = oak_id_from_url(url)
        if oak_id in known:
            print(f"  [{i}/{len(links)}] SKIP {oak_id} (already exists)")
            continue

        print(f"  [{i}/{len(links)}] Scraping {url} ...", end=" ", flush=True)
        try:
            page_html = fetch(url)
            data = parse_dissertation(page_html, url)
            if not is_valid_record(data):
                print("SKIPPED: bad data")
                continue
            insert_dissertation(conn, data)
            new_count += 1
            print(f"OK  — {data['olim'] or '(no name)'}")
        except Exception as e:
            error_count += 1
            print(f"ERROR: {e}")

        time.sleep(DELAY)

    conn.close()
    print()
    print("=" * 50)
    print(f"Done. New records inserted : {new_count}")
    print(f"      Errors               : {error_count}")
    print(f"      Skipped (existing)   : {len(links) - new_count - error_count}")


if __name__ == "__main__":
    main()
