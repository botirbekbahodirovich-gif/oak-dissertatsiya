"""Daily scientific-grant scraper — modular multi-portal worker.

Targets: Erasmus+, Fulbright, DAAD, El-yurt umidi, and the Ministry of
Innovative Development of Uzbekistan.

Design notes:
  - Each portal has its own parser fn returning a list of normalized dicts.
  - Anti-duplicate: `source_url` is UNIQUE and inserts use ON CONFLICT DO NOTHING,
    so re-running never injects duplicates.
  - Rate-limit friendly: a shared throttled `fetch()` sleeps between requests and
    backs off on failure; parsers are iterative (no unbounded recursion).
  - Cron: run daily via `scripts/grant_scraper.py` (see deploy/grant-scraper.cron).

Usage:  DATABASE_URL=... python scripts/grant_scraper.py
"""
import os
import sys
import time
import json
from datetime import datetime

try:
    import requests
    from bs4 import BeautifulSoup  # noqa: F401  (used by real parsers)
except ImportError:
    sys.exit("pip install requests beautifulsoup4")

try:
    import psycopg2
except ImportError:
    sys.exit("pip install psycopg2-binary")

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    sys.exit("DATABASE_URL not set")

DELAY = 2.0       # seconds between requests — polite, avoids rate limits.
TIMEOUT = 20
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; OlimlarGrantBot/1.0)"}

_last_fetch = 0.0


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS grants (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                scientific_codes TEXT,
                country TEXT,
                funding_type VARCHAR(20),
                academic_level VARCHAR(20),
                application_deadline DATE,
                source_url TEXT UNIQUE,
                requirements_json JSONB,
                provider TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
    conn.commit()


def fetch(url, retries=3):
    """Throttled GET with linear backoff. Returns HTML text or None."""
    global _last_fetch
    for attempt in range(retries):
        wait = DELAY - (time.time() - _last_fetch)
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            _last_fetch = time.time()
            if r.status_code == 200:
                return r.text
            if r.status_code == 429:      # explicit rate limit — back off harder.
                time.sleep(DELAY * (attempt + 2))
        except Exception:
            time.sleep(DELAY * (attempt + 1))
    return None


def _norm(title, provider, country, url, **extra):
    """Normalize a raw record into the DB shape."""
    return {
        "title": (title or "").strip(),
        "description": extra.get("description", ""),
        "scientific_codes": extra.get("scientific_codes", ""),
        "country": country,
        "funding_type": extra.get("funding_type", "Full"),
        "academic_level": extra.get("academic_level", "PhD"),
        "application_deadline": extra.get("application_deadline"),  # 'YYYY-MM-DD' or None
        "source_url": url,
        "requirements_json": json.dumps(extra.get("requirements", {})),
        "provider": provider,
    }


# ── Portal parsers ───────────────────────────────────────────────────────────
# Each returns a list of normalized dicts. Selectors are portal-specific; kept
# defensive so a markup change degrades to "no new items" instead of crashing.

def parse_erasmus():
    items = []
    html = fetch("https://erasmus-plus.ec.europa.eu/opportunities")
    if not html:
        return items
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if "/opportunities/" in href and text:
            url = href if href.startswith("http") else "https://erasmus-plus.ec.europa.eu" + href
            items.append(_norm(text, "Erasmus+", "EU", url,
                               academic_level="Master", funding_type="Full"))
    return items


def parse_fulbright():
    items = []
    html = fetch("https://foreign.fulbrightonline.org/about/foreign-fulbright")
    if not html:
        return items
    soup = BeautifulSoup(html, "html.parser")
    for h in soup.select("h2, h3"):
        t = h.get_text(strip=True)
        if t and "fulbright" in t.lower():
            items.append(_norm(t, "Fulbright", "USA",
                               "https://foreign.fulbrightonline.org/#" + t[:40],
                               academic_level="PhD", funding_type="Full"))
    return items


def parse_daad():
    items = []
    html = fetch("https://www2.daad.de/deutschland/stipendium/datenbank/en/")
    if not html:
        return items
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select("a[href]"):
        if "/stipendium/" in a.get("href", "") and a.get_text(strip=True):
            href = a["href"]
            url = href if href.startswith("http") else "https://www2.daad.de" + href
            items.append(_norm(a.get_text(strip=True), "DAAD", "Germany", url,
                               academic_level="PhD", funding_type="Partial"))
    return items


def parse_elyurt():
    items = []
    html = fetch("https://elyurtumidi.uz/uz/programs")
    if not html:
        return items
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select("a[href]"):
        if "/programs/" in a.get("href", "") and a.get_text(strip=True):
            href = a["href"]
            url = href if href.startswith("http") else "https://elyurtumidi.uz" + href
            items.append(_norm(a.get_text(strip=True), "El-yurt umidi", "Uzbekistan", url,
                               academic_level="Master", funding_type="Full"))
    return items


def parse_mininnovation():
    items = []
    html = fetch("https://mininnovation.uz/uz/grantlar")
    if not html:
        return items
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select("a[href]"):
        t = a.get_text(strip=True)
        if t and ("grant" in a.get("href", "").lower() or "grant" in t.lower()):
            href = a["href"]
            url = href if href.startswith("http") else "https://mininnovation.uz" + href
            items.append(_norm(t, "Innovatsion rivojlanish vazirligi", "Uzbekistan", url,
                               academic_level="PhD", funding_type="Partial"))
    return items


PARSERS = [parse_erasmus, parse_fulbright, parse_daad, parse_elyurt, parse_mininnovation]


# ── Insert (anti-duplicate) ──────────────────────────────────────────────────

def insert_grant(conn, item):
    """Insert a grant; returns True if a NEW row was created."""
    if not item.get("title") or not item.get("source_url"):
        return False
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO grants (title, description, scientific_codes, country,
                funding_type, academic_level, application_deadline, source_url,
                requirements_json, provider)
            VALUES (%(title)s, %(description)s, %(scientific_codes)s, %(country)s,
                %(funding_type)s, %(academic_level)s, %(application_deadline)s,
                %(source_url)s, %(requirements_json)s, %(provider)s)
            ON CONFLICT (source_url) DO NOTHING
            RETURNING id
        """, item)
        created = cur.fetchone() is not None
    conn.commit()
    return created


def main():
    conn = get_conn()
    ensure_schema(conn)
    print("Grant scraper started:", datetime.now().isoformat())
    new_count = 0
    for parser in PARSERS:
        name = parser.__name__
        try:
            records = parser()
            print(f"  {name}: {len(records)} candidate(s)")
            for item in records:
                try:
                    if insert_grant(conn, item):
                        new_count += 1
                        print("   + " + item["title"][:70])
                except Exception as e:
                    conn.rollback()
                    print(f"   ! insert error: {e}")
        except Exception as e:
            print(f"  ! {name} failed: {e}")
    conn.close()
    print("=" * 50)
    print("New grants added:", new_count)


if __name__ == "__main__":
    main()
