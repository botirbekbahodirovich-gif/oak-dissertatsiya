# Ommaviy DOI/CSV import va ko'rib chiqish navbati (Phase 2C.2)

Admin bir nechta DOI (ro'yxat yoki CSV) yuboradi. Tizim har biri uchun
tashqi metadata topadi (Crossref/OpenAlex/Unpaywall — Phase 2C.1 orqali),
lekin **hech qanday maqolani avtomatik nashr qilmaydi**. Import — moderatsiya
navbati: admin har bir nomzodni ko'rib chiqadi va explicit tasdiqlaydi.

Bog'liq hujjat: [`article-metadata-integration.md`](article-metadata-integration.md)
(DOI autofill, provayderlar, kesh). Bu ikki modul bir xil
`fetch_article_metadata()` funksiyasidan foydalanadi — bulk importer
Crossref/OpenAlex/Unpaywall mantig'ini qayta yozmaydi.

Admin qo'lda DOI kiritish o'rniga OpenAlex'dan kalit so'z bo'yicha nomzod
qidirishi mumkin — [`article-discovery.md`](article-discovery.md) (Phase 2D.1).
Discovery faqat shu yerdagi navbatga "yangi" DOI qo'shadi; qolgan hammasi —
metadata olish, ko'rib chiqish, tasdiqlash — o'zgarishsiz shu sahifadagi
pipeline orqali ishlaydi.

**Bir jumlada: import ≠ publish.** Tasdiqlangan (`approve`) yozuv har doim
`verification_status='pending_review'`, `publication_status='draft'` bilan
yaratiladi. Nashr qilish — mavjud, alohida admin moderatsiya oqimi
(`/admin/articles/<id>/review`).

---

## 1. Arxitektura

```text
services/article_import/
├── __init__.py       — ommaviy API
├── config.py          — ARTICLE_IMPORT_* env sozlamalari
├── parser.py           — DOI ro'yxati / CSV -> xom qatorlar
├── validator.py         — normalize_doi/validate_doi qo'llash + limit
├── deduplicator.py       — batch-ichi va DB duplicate aniqlash
├── state.py              — holat mashinasi (yagona haqiqat manbai)
├── models.py              — DB'dan mustaqil dataclass'lar
├── persistence.py          — DB CRUD, sxema, article yozish
├── processor.py             — bitta element/chunk ishlash
└── service.py                — ommaviy orkestratsiya API

blueprints/article_import.py — HTTP marshrutlash (biznes logikasiz)
migrations/010_create_article_import_queue.sql
templates/admin/article_import*.html
static/{css,js}/article_import.*
```

`services/article_import/` Flask'ga BOG'LIQ EMAS — barcha funksiyalar
ochiq psycopg2 kursurini oladi (`services/article_metadata` bilan bir xil
konvensiya). Bu Phase 2D agentiga ham, keyingi CSV importerga ham xizmat
qiladi (§86/§16 pastda).

**Persistence reuse qarori:** `create_article_from_metadata()`
`blueprints/articles.py`dagi mavjud yozish mantig'ini (ustun ro'yxati,
default qiymatlar) TAKRORLAYDI, lekin uni chaqirmaydi — chunki
`blueprints/articles.py` Flask blueprint (route ichida `request`/PDF-forma
maydonlariga bog'liq), servis qatlami esa undan mustaqil qolishi kerak edi
(§77). Bu ataylab qilingan reuse-vs-duplicate qaror: DOI/provider mantig'i
hech qayerda takrorlanmaydi (§6), faqat "qanday INSERT qilish" qatlami —
ikkisi bir xil `scientific_articles` ustun to'plamidan foydalanadi.

---

## 2. Batch modeli

`article_import_batches` — bitta "yuborish" (DOI ro'yxati yoki CSV fayl).

| Ustun | Ma'no |
| --- | --- |
| `status` | `draft, queued, processing, needs_review, completed, completed_with_errors, cancelled, failed` |
| `total_items`, `pending_count`, … | **DERIVED** — item statuslaridan qayta hisoblanadi |
| `cancel_requested` | yumshoq bekor qilish bayrog'i |
| `started_at`, `completed_at` | birinchi chunk / yakuniy holat vaqti |

**Hisoblagichlar drift qilmasligi uchun** (§7): ular `+1`/`-1` increment
qilinmaydi. Har bir item status o'tishidan keyin
`persistence.recompute_batch_counts()` BUTUN batch bo'yicha
`GROUP BY status` qilib qayta hisoblaydi va shu asosida batch statusini ham
qayta chiqaradi (`state.derive_batch_status`). Bu bitta qo'shimcha SQL
so'rov evaziga to'liq deterministiklik beradi.

Batch status qanday hisoblanadi (`state.derive_batch_status`):

```text
elementlar yo'q                          -> draft
cancel_requested va hech narsa PROCESSING -> cancelled  (darhol, pending kutmaydi)
hali started_at yo'q                      -> queued
pending/processing element bor            -> processing
"ko'rib chiqish" holatidagi element bor    -> needs_review
faqat failed/provider_failed qoldi         -> completed_with_errors
qolgani terminal (approved/rejected/…)     -> completed
```

## 3. Element modeli va holat mashinasi

`article_import_items` — bitta DOI/qator. Yagona haqiqat manbai —
[`services/article_import/state.py`](../services/article_import/state.py):
`ALLOWED_TRANSITIONS`.

```text
pending → processing → ready_for_review → approved
                     → ready_for_review → rejected / skipped
                     → partial_metadata → (xuddi shu uch yo'l)
                     → retracted        → (xuddi shu uch yo'l, + confirm_retracted talab qilinadi)
                     → duplicate_in_batch → skipped
                     → already_exists     → skipped
                     → invalid_doi        → skipped
                     → metadata_not_found → rejected / skipped
                     → provider_failed    → pending (retry) / skipped
                     → failed             → pending (retry) / skipped
approved / rejected / skipped — TERMINAL, hech qayerga o'tmaydi
```

`metadata_candidate`, `provider_status_summary`, `conflicts`, `warnings` —
`NormalizedArticleMetadata`/`MetadataLookupResult`ning JSON snapshot'i
(`services.article_metadata.models`). **Xom provayder javobi bu yerga
SAQLANMAYDI** — Phase 2C.1 bilan bir xil qoida.

---

## 4. Kirish formatlari va limitlar

- **DOI ro'yxati** — textarea, har qatorda bitta DOI. `10.xxxx/…`,
  `https://doi.org/…`, `doi:…` — barchasi qabul qilinadi (normalizatsiya
  `utils.article_rules.normalize_doi` orqali, §15 — bulk importer alohida
  regex yaratmaydi).
- **CSV** — UTF-8 (BOM ixtiyoriy), `,`/`;` avtomatik aniqlanadi, `doi` ustuni
  case-insensitive majburiy. Validatsiya:
  `security_uploads.validate_tabular_upload` (kengaytma/MIME/magic bytes,
  15MB chegara) + `services/article_import/parser.py` (bo'sh fayl, ustun
  yo'q, takrorlangan sarlavha, qator soni).
- **Limit** — `ARTICLE_IMPORT_MAX_ITEMS` (default 500). Oshsa aniq xato,
  hech narsa yozilmaydi.

CSV preview (spetsifikatsiya §51, ixtiyoriy deb belgilangan) — Phase
2C.2'da QO'SHILMAGAN: batch yaratish bir bosqichda parsing+dedup+persist
qiladi, natija darhol review-navbat sahifasida (jami/tayyor/mavjud/xato
xulosasi bilan) ko'rinadi. Bu amaliy ekvivalent — admin yuborishdan oldin
alohida "preview" ekranini ko'rmaydi, lekin yuborilgach natijani darhol
ko'radi va hech narsa noto'g'ri import qilinmaydi (approve alohida qadam).
Servis darajasida `preview_import_input()` funksiyasi baribir mavjud —
kelajakda UI qo'shish uchun tayyor.

---

## 5. Duplicate aniqlash (metadata so'rovidan OLDIN)

1. **Batch ichida** — normalized DOI takrorlansa, ikkinchi va keyingi
   yozuvlar `duplicate_in_batch` (birinchisiga `duplicate_of_item_id` bilan
   bog'langan). API'ga so'rov YUBORILMAYDI.
2. **Bazada** — `scientific_articles.doi = ANY(%s)` bitta so'rovda (N+1
   emas). Topilsa `already_exists` + `existing_article_id`.

---

## 6. Ishlash arxitekturasi — Variant A + B gibrid

Loyihada durable job queue (Celery/Redis) yo'q va bu bosqichda ham
qo'shilmaydi. Tanlangan yechim:

- Admin batch yaratadi (`create_import_batch`) — hech qanday tashqi so'rov
  YUBORILMAYDI, faqat parsing/dedup/persist.
- `POST /admin/articles/imports/<id>/process` — **bitta chunk**
  (`ARTICLE_IMPORT_CHUNK_SIZE`, default 10) ishlaydi va qaytadi. Har element
  o'z natijasini alohida yozadi va COMMIT bo'ladi — shuning uchun HTTP
  so'rov 500 DOI tugaguncha osilib qolmaydi (§20).
- Review-navbat sahifasidagi JS (`static/js/article_import.js`) shu
  endpointni **qayta-qayta chaqiradi** (~2.5s oralig'ida) — bir vaqtning
  o'zida ham progress ko'rsatkichi, ham processing drayveri (§44).
- **Resume** — har chunk commit bo'lgani uchun server/browser
  qayta ishga tushsa (yoki sahifa yopilib qayta ochilsa) keyingi chaqiruv
  faqat qolgan `pending` elementlarni oladi. Yo'qotilgan ish yo'q.
- **Cancel** — `cancel_requested=TRUE`; joriy ishlanayotgan element
  tugaydi, keyingi chunk boshlanmaydi (§24 — yumshoq, majburiy kill yo'q).

Ushbu yondashuvning bilinган cheklovi: agar admin sahifani yopib qo'ysa va
qayta ochmasa, processing avtomatik davom ETMAYDI (chinakam background
worker yo'q). Bu ataylab: yangi katta infratuzilma (Celery/Redis) faqat shu
feature uchun MAJBURAN qo'shilmaydi (spetsifikatsiya §19-20). Admin "Davom
ettirish" tugmasini bosib istalgan vaqt davom ettira oladi — hech narsa
yo'qolmaydi, faqat pauza bo'ladi.

**Parallellik** — provayderlar (Crossref/OpenAlex/Unpaywall) Phase 2C.1
ichida ALLAQACHON parallel (`ThreadPoolExecutor`, `services/article_metadata`).
Bulk processor buning ustiga qo'shimcha parallellik QO'SHMAYDI — elementlar
chunk ichida KETMA-KET ishlanadi (correctness > performance, §19). Chunk
o'lchami kichik tutilgani (10) tashqi API'larni haddan tashqari bosmaslik
uchun yetarli.

---

## 7. Retry va urinishlar

- Faqat `provider_failed`/`failed` -> `pending` retry qila oladi
  (`ARTICLE_IMPORT_MAX_ATTEMPTS`, default 3). `invalid_doi`,
  `already_exists`, `duplicate_in_batch` — retry qilinmaydi (natija
  o'zgarmaydi).
- Provider-level retry (timeout/429/5xx) `services/article_metadata`
  ichida ALLAQACHON bor (2 urinish, backoff). Bulk-level retry — ADMIN
  EXPLICIT harakati, provider retry tugagandan keyingi qo'shimcha qatlam.

---

## 8. Ko'rib chiqish va tasdiqlash

`/admin/articles/imports/<batch_id>` — summary + filtrlar + jadval.
`/admin/articles/imports/<batch_id>/items/<item_id>` — to'liq tafsilot:
sarlavha, mualliflar, jurnal, OA holati (tashqi, ichki `access_type` bilan
ARALASHTIRILMAYDI), litsenziya nomzodlari (informational), candidate PDF
URL (informational, HECH QACHON yuklanmaydi), konfliktlar, ogohlantirishlar,
provayder holati (matn bilan, rang bilan emas).

**Approve semantikasi** (`persistence.create_article_from_metadata`):

```text
scientific_articles.verification_status = 'pending_review'
scientific_articles.publication_status  = 'draft'
scientific_articles.access_type          = DEFAULT 'metadata_only'  (o'zgarmaydi)
scientific_articles.license_type         = DEFAULT 'unknown'         (o'zgarmaydi)
```

Asosiy fan yo'nalishi **ixtiyoriy** admin approve paytida: agar
tanlanmasa, maqola primary field'siz draft sifatida yaratiladi (§35 —
"sun'iy field tanlama"). Nashr qilish alohida moderatsiya oqimida bo'ladi,
u yerda field talab qilinishi mumkin — bu bulk importer javobgarligi emas.

**Retraction** — `is_retracted=TRUE` element approve qilinishi uchun
`confirm_retracted=1` MAJBURIY (forma checkbox `required`). Admin
tasdiqlasa ham, maqola oddiy draft bo'lib qoladi — avtomatik hech narsa
yuqori holatga o'tmaydi.

**Kritik konflikt** (masalan, DOI mismatch — provayder qaytargan DOI
kiritilgandan farq qilsa) — `confirm_critical=1` xuddi shunday talab
qilinadi.

**Bulk approve** (`bulk_approve_safe_items`) — faqat `ready_for_review` VA
`is_retracted=FALSE` elementlar. Retracted, kritik konflikt, invalid,
already_exists, provider_failed — HECH QACHON bulk ro'yxatga kirmaydi
(shablon ham, servis ham ikki qatlamli tekshiradi).

---

## 9. Transaction va poyga holati xavfsizligi

- Har bir `approve`/`reject`/`skip`/`retry` — BITTA atomic transaction
  (route: `conn.commit()`/`conn.rollback()`).
- Article + authors + field bog'lanishi bitta transaction ichida yoziladi;
  xato bo'lsa HAMMASI rollback (yarim yaratilgan maqola qolmaydi).
- **Poyga holati** (ikki admin bir xil DOI'ni bir vaqtda approve qilsa):
  Python pre-check YETARLI EMAS — `scientific_articles.doi` UNIQUE indeksi
  YAKUNIY himoya. Ikkinchi INSERT `psycopg2.IntegrityError` beradi;
  `DuplicateArticleError` sifatida qayta ko'tariladi; route
  `conn.rollback()` qiladi va YANGI tranzaksiyada itemni `already_exists`ga
  o'tkazadi. Hech qachon 500 qaytmaydi.
- **Idempotency** — `approve` ikki marta bosilsa (double-click yoki qayta
  urinish), ikkinchi chaqiruv item allaqachon `approved` ekanini ko'rib
  xato bermay mavjud `article_id`ni qaytaradi. Ikkinchi maqola YARATILMAYDI.

---

## 10. Xavfsizlik

- **SSRF** — faqat normalized DOI `fetch_article_metadata()`ga boradi;
  provider base URL'lari FIXED (`services/article_metadata/config.py`).
  CSV'dagi boshqa ustunlar (agar bo'lsa) hech qachon URL sifatida
  so'ralmaydi.
- **XSS** — tashqi metadata (sarlavha, jurnal, mualliflar, annotatsiya)
  Jinja avtoescape orqali chiqadi; `|safe` ISHLATILMAYDI.
- **CSV** — hajm/qator/encoding chegaralari (`security_uploads.py` +
  `parser.py`); fayl diskka SAQLANMAYDI (xotirada parse qilinadi).
- **Auth/CSRF** — barcha yozuvchi route `login_required` + `_require_admin()`
  + global `CSRFProtect`.

---

## 11. Konfiguratsiya

```bash
ARTICLE_IMPORT_MAX_ITEMS=500      # bitta partiyadagi maksimal yozuv
ARTICLE_IMPORT_CHUNK_SIZE=10      # bitta HTTP so'rovda ishlanadigan son
ARTICLE_IMPORT_MAX_ATTEMPTS=3     # elementning maksimal qayta urinishi
```

`.env.example` da hujjatlashtirilgan. `services/article_metadata`ning
`SCHOLARLY_API_EMAIL`/`METADATA_*` sozlamalari o'zgarishsiz qayta
ishlatiladi (§50 — kesh ham shu FileSystemCache).

---

## 12. Kelajak — Phase 2C.3 (sinxronizatsiya) kontrakti

Bu bosqichda YARATILMAGAN, lekin kontrakt shu:

```text
mavjud ScientificArticle
  ↓ DOI
  ↓ fetch_article_metadata()   (aynan shu funksiya)
  ↓ joriy vs tashqi solishtirish
  ↓ yangilanish nomzodlari
  ↓ rejalashtirilgan/qo'lda sinxronizatsiya
```

Bulk importer `already_exists` elementlarni HECH QACHON avtomatik
yangilamaydi — bu Phase 2C.3'ning ishi.

## 13. Kelajak — Phase 2D agent kontrakti

Kelajakdagi agent (DOI topuvchi) shu pipeline'ni AYNAN qayta ishlatishi
kerak:

```text
agent DOI topadi
  ↓ create_import_batch(...)   (yoki bevosita insert_items — kichik API)
  ↓ process_import_batch(...)  (bir xil processor)
  ↓ bir xil review-navbat, bir xil approve
```

Shu sababli `services/article_import/` HTTP route'ga qattiq bog'lanmagan —
har bir funksiya oddiy `cur` va Python qiymatlarini oladi.
