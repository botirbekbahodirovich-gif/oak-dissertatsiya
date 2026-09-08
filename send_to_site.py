# ============================================================
# Yangi e'lonlarni saytga (Google Cloud VPS) to'g'ridan-to'g'ri yuborish
# ============================================================

import json
import os
import sys
import requests

API_URL = os.environ.get("SITE_API_URL")   # GitHub Secret: https://olimlar.uz/api/oak/import
API_KEY = os.environ.get("SITE_API_KEY")   # GitHub Secret dan (app .env dagi SITE_API_KEY bilan bir xil)
INPUT_FILE = os.environ.get("SCRAPER_OUTPUT_FILE", "new_items.json")
RETRY_FILE = os.environ.get("SCRAPER_RETRY_FILE", "retry_items.json")
REQUEST_TIMEOUT = 60
try:
    BATCH_SIZE = max(1, int(os.environ.get("SITE_BATCH_SIZE", "25")))
except ValueError:
    BATCH_SIZE = 25


class FatalDeliveryError(RuntimeError):
    """Authentication, networking, or service-wide failures that need a retry."""


def _load_items(path, *, missing_ok=False):
    if not os.path.exists(path):
        if missing_ok:
            return []
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        items = json.load(f)
    if not isinstance(items, list):
        raise ValueError(path + " JSON ro'yxat bo'lishi kerak")
    return [item for item in items if isinstance(item, dict)]


def _item_key(item):
    oak_id = str(item.get("ID", "") or "").strip()
    if oak_id:
        return "id:" + oak_id
    return "json:" + json.dumps(item, ensure_ascii=False, sort_keys=True)


def _deduplicate(items):
    result = []
    seen = set()
    for item in items:
        key = _item_key(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _save_retry_items(items):
    with open(RETRY_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def _post(items, headers):
    try:
        response = requests.post(
            API_URL,
            json={"items": items},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.RequestException as exc:
        raise FatalDeliveryError("Ulanishda xatolik: " + str(exc)) from exc

    request_id = response.headers.get("X-Request-ID", "")
    suffix = (" | request_id=" + request_id) if request_id else ""
    print("Status: " + str(response.status_code) + suffix)
    print("Javob: " + response.text[:300])
    return response


def _deliver_batch(items, headers):
    """Return (delivered, retry) and isolate a row that makes a bulk import 500."""
    response = _post(items, headers)
    if response.status_code in (200, 201):
        return items, []

    # Bad secret/configuration and transient gateway failures should fail the run.
    if response.status_code in (400, 401, 403, 408, 429, 502, 503, 504):
        raise FatalDeliveryError(
            "Sayt importni qabul qilmadi (status " + str(response.status_code) + ")"
        )

    # A single malformed/legacy record must not block every newer announcement.
    # Divide-and-conquer finds only the offending record(s), which are persisted
    # in retry_items.json and tried again by the next scheduled run.
    if len(items) == 1:
        print("Keyingi run uchun qoldirildi: " + _item_key(items[0]))
        return [], items

    middle = len(items) // 2
    delivered_left, retry_left = _deliver_batch(items[:middle], headers)
    delivered_right, retry_right = _deliver_batch(items[middle:], headers)
    return delivered_left + delivered_right, retry_left + retry_right


def main():
    try:
        new_items = _load_items(INPUT_FILE)
        retry_items = _load_items(RETRY_FILE, missing_ok=True)
    except FileNotFoundError:
        print(INPUT_FILE + " topilmadi")
        return 0
    except (OSError, ValueError) as e:
        print("Scraper JSON faylini o'qishda xatolik: " + str(e))
        return 1

    # Avvalgi run'da bitta nosoz yozuv qolgan bo'lsa, uni yangi e'lonlar bilan
    # birga qayta jo'natamiz. ID bo'yicha dublikatlar yuborilmaydi.
    items = _deduplicate(retry_items + new_items)

    if not items:
        print("Yangi e'lon yo'q, yuborish shart emas.")
        _save_retry_items([])
        return 0

    if not API_URL or not API_KEY:
        print("SITE_API_URL yoki SITE_API_KEY yo'q!")
        return 1

    print(
        "Yuborilmoqda: " + str(len(items)) + " ta e'lon"
        + (" (" + str(len(retry_items)) + " ta retry)" if retry_items else "")
        + "..."
    )

    headers = {
        "X-API-KEY": API_KEY,
        "Content-Type": "application/json",
    }

    delivered = []
    retry = []
    try:
        for start in range(0, len(items), BATCH_SIZE):
            batch = items[start:start + BATCH_SIZE]
            batch_delivered, batch_retry = _deliver_batch(batch, headers)
            delivered.extend(batch_delivered)
            retry.extend(batch_retry)
    except FatalDeliveryError as exc:
        print(str(exc))
        # Old retry queue stays intact; failed run also prevents state cache save.
        return 1

    _save_retry_items(_deduplicate(retry))
    print(
        "Yakun: " + str(len(delivered)) + " ta yuborildi, "
        + str(len(retry)) + " ta keyingi run uchun saqlandi."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
