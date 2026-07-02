# ============================================================
# Yangi e'lonlarni saytga (Google Cloud VPS) to'g'ridan-to'g'ri yuborish
# ============================================================

import json
import os
import sys
import requests

API_URL = os.environ.get("SITE_API_URL")   # GitHub Secret: https://olimlar.uz/api/oak/import
API_KEY = os.environ.get("SITE_API_KEY")   # GitHub Secret dan (app .env dagi SITE_API_KEY bilan bir xil)


def main():
    # new_items.json ni o'qiymiz
    if not os.path.exists("new_items.json"):
        print("new_items.json topilmadi")
        sys.exit(0)

    try:
        with open("new_items.json", "r", encoding="utf-8") as f:
            items = json.load(f)
    except (OSError, ValueError) as e:
        print("new_items.json o'qishda xatolik: " + str(e))
        sys.exit(1)

    if not items:
        print("Yangi e'lon yo'q, yuborish shart emas.")
        sys.exit(0)

    if not API_URL or not API_KEY:
        print("SITE_API_URL yoki SITE_API_KEY yo'q!")
        sys.exit(1)

    print("Yuborilmoqda: " + str(len(items)) + " ta e'lon...")

    headers = {
        "X-API-KEY": API_KEY,
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            API_URL,
            json={"items": items},
            headers=headers,
            timeout=60,
        )
    except requests.exceptions.RequestException as e:
        # Ulanish/timeout xatoliklari — crash qilmasdan log qilamiz
        print("Ulanishda xatolik: " + str(e))
        sys.exit(1)

    print("Status: " + str(response.status_code))
    print("Javob: " + response.text[:300])

    if response.status_code not in (200, 201):
        sys.exit(1)


if __name__ == "__main__":
    main()
