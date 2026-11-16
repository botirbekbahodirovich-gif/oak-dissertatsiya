# O'zbekiston maqolalari: ommaviy ingestion, ochiq PDF va ommaviy nashr

`/maqolalar` katalogini to'ldirish uchun qabul qilingan qarorlar, ularning
kodda qayerda turgani va ishga tushirish tartibi. Yangi ma'lumot modeli yoki
ikkinchi import yo'li YO'Q — hammasi mavjud discovery → import navbati →
approve → moderatsiya zanjiri ustida.

Bog'liq: [`articles-module.md`](articles-module.md),
[`article-bulk-import.md`](article-bulk-import.md),
[`article-discovery.md`](article-discovery.md).

---

## 1. Qabul qilingan qarorlar

| Mavzu | Qaror |
|---|---|
| Kontent | OpenAlex: DOI'li `type:article`, kamida bitta muallif O'zbekiston muassasasidan (`authorships.institutions.country_code:UZ`), `primary_location.source.is_core:true`, 2024-01-01 dan. 2026-11-16 holatiga **11 519** ta. |
| PDF | Ruxsat etilgan ochiq litsenziyali maqolalarning PDF'i serverga saqlanadi va ichki PDF.js o'quvchisida ochiladi. Qolganlari — metadata + DOI havolasi. |
| Annotatsiya | Faqat ochiq (CC yoki public domain) litsenziyada sahifada ko'rsatiladi. Bazada saqlanadi. |
| Nashr | Avtomatik tekshiruv + admin bitta tugma bilan ommaviy nashr. Shubhalilar qo'lda. Avtomatik nashr YO'Q. |

## 2. Discovery: UZ va core filtri

* `services/article_discovery/openalex.py:build_filter()` — `country_code` va
  `core_only` filtrlari. `ArticleDiscoveryQuery.country_code/core_only`
  (default `None/False` — eski xulq-atvor o'zgarmagan).
* Shu filtrlardan biri berilsa kalit so'z **ixtiyoriy**. Crossref mamlakat/core
  filtrini bilmaydi — bunday so'rovda Crossref tanlansa aniq xato
  (`scope_requires_openalex`).
* Admin UI hamon bitta sahifali (§33). CLI uchun alohida
  `iter_cursor()`: `cursor=*`, `per_page<=200`, umumiy `limit` qat'iy.
* Admin formasida "Faqat O'zbekiston mualliflari" va "Faqat sifatli (core)
  jurnallar" belgilari; saqlangan so'rovlarda `country_code` / `core_only`
  ustunlari (migratsiya 025).

## 3. OAK yo'nalishini avtomatik aniqlash

* Jadval: `data/openalex_oak_mapping.json` — OpenAlex'ning 252 subfield'i
  va 26 field'i uchun qator: `oak_code`, `confidence` (`high`/`low`) yoki
  `null` (aniq mos yo'q — sun'iy moslashtirilmagan). Hozir: 151 high,
  88 low, 13 bo'sh.
* `utils/oak_mapping.py:suggest_oak_field()` — faqat `primary_topic`;
  subfield ustun, subfield bo'lmasa field.
* `approve_import_item()`: admin yo'nalish tanlamagan bo'lsa, `high`
  moslikdagi shifr faol `scientific_fields` qatoriga aylantirilib
  `primary_field_id` qo'yiladi. `low`/bo'sh — maqola yo'nalishsiz qoladi va
  `/admin/articles?queue=needs_field` ("Yo'nalish kerak") ro'yxatiga
  tushadi (taklif ko'rsatiladi).
* `/admin/articles/<id>/review` endi asosiy yo'nalishi yoki muallifi yo'q
  maqolani `published` qilmaydi (`review_blockers`).

Jadvalni o'zgartirish: JSON'ni tahrirlang; `tests/test_articles_uz_ingest.py`
har bir shifr OAK taksonomiyasida borligini tekshiradi.

## 4. Litsenziya

`utils/article_license.py:resolve_license()` — uch manba:

1. Crossref `license[]`, faqat `content-version=vor`; `start` sanasi
   saqlanadi (`services/article_metadata/crossref.py`).
2. OpenAlex `best_oa_location.license` (endi `license_candidates` ga kiradi).
3. Unpaywall `best_oa_location.license`.

| Holat | Natija |
|---|---|
| Tanib olingan litsenziyalar zid (yoki tanib olinmagan matnli litsenziya bor) | `conflict` → `unknown`, `/admin/articles?queue=license_conflict` |
| Crossref VOR, `start <= nashr sanasi` | `crossref_vor` → litsenziya qo'yiladi |
| Crossref VOR (start kech yoki yo'q) + OpenAlex yoki Unpaywall bir xil | `crossref_and_oa` → litsenziya qo'yiladi |
| Faqat OpenAlex + Unpaywall bir xil | `insufficient` → `unknown`: ikkalasi bitta upstream (OurResearch), mustaqil tasdiq emas |
| Qolgan hammasi | `insufficient` → `unknown` |

Approve paytida `resolved` bo'lsa `license_type` + `license_url` yoziladi;
`access_type` hamon `metadata_only` (PDF bo'lmaguncha). Dalil
`scientific_articles.ingest_evidence` (JSONB, migratsiya 025) da.

`cc_by_nd` va `cc_by_nc_nd` lug'atga qo'shildi (`LICENSES`, `LICENSE_URLS`);
normalizer ularni o'z kalitiga xaritalaydi — hech qachon CC BY ga emas.

## 5. Ochiq PDF

`scripts/fetch_open_pdfs.py` (servis: `services/article_pdf_fetch.py`):

* Defaultda dry-run; `--limit` majburiy; `--execute` uchun `--admin-id`
  (`rights_confirmed_by`) va `SCHOLARLY_API_EMAIL` (User-Agent kontakti).
* Litsenziyalar: `ARTICLE_AUTO_PDF_LICENSES` (default `cc_by, cc_by_sa,
  cc_by_nd, cc0, public_domain`). NC variantlari defaultda YO'Q.
* Faqat OpenAlex/Unpaywall `pdf_url`. Joylashuvning o'z litsenziyasi bo'lsa
  maqola litsenziyasiga teng bo'lishi shart; litsenziyasiz joylashuv faqat
  `publishedVersion` bo'lsa.
* Tekshiruvlar: `security_uploads.validate_pdf_upload` (MIME, `%PDF-`,
  `%%EOF`, hajm ≤ `ARTICLE_PDF_MAX_MB`), SHA-256 dublikat, ommaviy IP
  (SSRF), `save_private_pdf` bilan atomik yozish.
* HTTP: timeout, 5xx/tarmoqda 2 qayta urinish, bitta domenga 1 so'rov/s.
  401/403/429 yoki Cloudflare/captcha — o'tkazib yuboriladi va shu domen
  joriy ishga tushirish oxirigacha bloklanadi. Aylanib o'tishga urinilmaydi.
* Diskda 5 GB dan kam joy qolsa to'xtaydi.
* Muvaffaqiyatda: `access_type=open_access`, `version_type`
  (`publishedVersion→version_of_record`, `acceptedVersion→accepted_manuscript`,
  `submittedVersion→preprint`), `source_type=open_journal`, `source_name` =
  jurnal, `license_url`, `rights_confirmed_by/at`, `permission_note` (qaysi
  manba qanday litsenziya bergani, PDF URL, sana, SHA-256).
* Olinmasa — maqola metadata-only qoladi, sabab `ingest_evidence.pdf` va
  JSON hisobotda. Qayta urinish ma'nosiz sabablar (`skipped_final`) keyingi
  ishga tushirishda nomzod bo'lmaydi.

Fayllar servis foydalanuvchisi nomidan yozilishi kerak (gunicorn o'qiy
olsin): `sudo -u botirbekbahodirovich ...`.

## 6. Annotatsiya qoidasi

`utils/article_rules.py:can_show_abstract()` — yagona qoida (CC yoki public
domain). `blueprints/articles.py:_decorate` `can_show_abstract`,
`meta_description` va `attribution` ni qo'yadi; shablonlar faqat shularni
o'qiydi: katalog kartasi, tafsilot, meta description, og, ScholarlyArticle
JSON-LD, o'quvchi. Yopiq maqolada meta = `sarlavha · jurnal · yil`.

PDF ochiq va litsenziya ochiq bo'lsa tafsilot va o'quvchida CC atributsiyasi:
mualliflar, sarlavha, jurnal, DOI havolasi, litsenziya havolasi va
"Asl nashrdan o'zgarishsiz".

## 7. Avto-tekshiruv va ommaviy nashr

* `utils/article_publish_rules.py:publish_blockers()` — DOI to'g'ri va
  noyob, retraction emas, kritik konflikt yo'q, sarlavha, ≥1 muallif,
  jurnal, yil, asosiy yo'nalish, `validate_article_rights` o'tadi.
* `/admin/articles?queue=ready` — "Nashrga tayyor" (checkbox, ≤500) va
  "Blokeri bor" (sababi bilan).
* `POST /admin/articles/bulk-publish` — CSRF, faqat admin, ≤500, bitta
  tranzaksiya, `FOR UPDATE` + qayta `publish_blockers`, `reviewed_by/at`.

## 8. Ommaviy ingestion skripti

```bash
cd /opt/olimlar-uz && set -a && . ./.env && set +a
# dry-run (read-only DB sessiya): son, budjet, yangi/bazadagi/navbatdagi
venv/bin/python scripts/ingest_uz_articles.py --from-date 2024-01-01 \
    --country UZ --core-only --limit 200
# yozish
venv/bin/python scripts/ingest_uz_articles.py --from-date 2024-01-01 \
    --country UZ --core-only --limit 200 --execute --admin-id <ID>
```

* `OPENALEX_API_KEY` yo'q bo'lsa skript to'xtaydi (chiqish kodi 2).
* Budjet (2026): kalit bilan $1/kun; DOI bo'yicha so'rov $0, ro'yxat
  $0.0001. 11 519 ta uchun ~59 ro'yxat so'rovi (~$0.006) — bir kunga
  sig'adi. Dry-run `/rate-limit` javobini ham ko'rsatadi; sig'masa necha
  kunga bo'lishni aytadi.
* Bosqichlar: `discover` (500 talik batchlar, `source_type='discovery'`,
  provenance `source_query.scheduled.trigger_type='cli_ingest_uz'`),
  `process` (har chunk commit), `approve` (`bulk_approve_safe_items`,
  faqat `ready_for_review`; `partial_metadata`/`retracted` qo'lda).
* Idempotent: bazadagi, navbatdagi va admin rad etgan/o'tkazib yuborgan
  DOI'lar qayta qo'shilmaydi; to'xtagan batch'lar davom etadi.
* `bulk_approve_safe_items` endi har element uchun SAVEPOINT ishlatadi —
  DOI to'qnashuvi butun tranzaksiyani buzmaydi.

## 9. Keyingi bosqichga qolganlar

* Maqolani olim profiliga bog'lash (`article_authors.linked_scientist_id`).
* NC litsenziyalar bo'yicha huquqiy qaror (`ARTICLE_AUTO_PDF_LICENSES`).
* Yuklab olish tugmasi (`can_download_pdf`, articles-module.md §13.9).
* Haftalik saqlangan discovery (UZ + core) va cron — to'liq ishga
  tushirishdan keyin, alohida tasdiq bilan.
