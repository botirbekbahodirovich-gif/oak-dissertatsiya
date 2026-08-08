# Ilmiy maqolalar moduli (Phase 2B.1)

Olimlar.uz'ning **o'z** maqolalar katalogi: admin qo'lda joylashtiradigan,
huquqi tekshirilgan ochiq PDF maqolalar va OAK klassifikatsiyasiga mos fan
yo'nalishlari ierarxiyasi.

> **Adashtirmang.** `/maqolalar-tahlili` — bu boshqa modul
> (`blueprints/maqolalar.py`, OpenAlex nashr tahlili). Katalog —
> `blueprints/articles.py`. Ikkalasi bir-biriga bog'liq emas.

Bu hujjat `docs/maqolalar-route-contract.md` ni **almashtirmaydi**: u route
kontrakti va PDF viewer'ning vizual (light-only) kontraktini saqlaydi; bu yerda
esa ma'lumotlar modeli, huquqiy qoidalar va admin ish oqimi.

Bitta DOI qo'lda kiritish o'rniga o'nlab/yuzlab maqolani DOI ro'yxati yoki
CSV orqali ommaviy import qilish, ko'rib chiqish va tasdiqlash —
[`docs/article-bulk-import.md`](article-bulk-import.md) (Phase 2C.2,
`services/article_import/` + `blueprints/article_import.py`). U shu yerdagi
`scientific_articles`/`article_authors`/`article_scientific_fields`
jadvallariga AYNAN bir xil qoidalar bilan yozadi — parallel ma'lumot modeli
YARATILMAGAN.

Bazada ALLAQACHON mavjud maqolani DOI orqali qayta tekshirish (diff + xavfsiz
apply) — [`docs/article-metadata-sync.md`](article-metadata-sync.md) (Phase
2C.3A, `services/article_sync/`). Admin maqola tahrirlash sahifasidagi
"Metadata sinxronizatsiyasi" bo'limi.

---

## 1. Modellar

ORM yo'q — loyihaning qolgan qismi kabi psycopg2 + xom SQL.

| Jadval | Vazifasi |
|---|---|
| `scientific_fields` | fan tarmog'i → ixtisoslik guruhi → ixtisoslik → kichik yo'nalish (`parent_id` bilan recursive) |
| `scientific_articles` | maqola metadata + PDF saqlash + litsenziya/huquq/holat |
| `article_authors` | mualliflar, `author_order` bilan tartibi saqlanadi |
| `article_scientific_fields` | maqola ↔ yo'nalish (many-to-many, `is_primary`) |

Muhim qarorlar:

* `created_by` / `reviewed_by` / `linked_user_id` da **users ga FK yo'q** —
  foydalanuvchilar legacy SQLite `users.db` da (`blueprints/saved.py` bilan
  bir xil sabab).
* `doi` — **partial UNIQUE** (`WHERE doi IS NOT NULL`): DOI'siz maqolalar
  ruxsat etilgan va bir-biriga xalaqit bermaydi.
* `article_scientific_fields` da bitta asosiy yo'nalish **DB darajasida**
  majburlanadi (`uq_article_primary_field ... WHERE is_primary`) — validatsiya
  faqat Python'da qolmaydi.
* `scientific_fields.parent_id` → `ON DELETE RESTRICT`: bolalari bor
  kategoriya tasodifan yo'qolmaydi.
* `checksum_sha256` UNIQUE **emas**, indekslangan: dublikat fayl admin
  formasida ogohlantirish bilan to'siladi, lekin DB cheklovi migratsiyani
  mo'rt qilardi.

### Fan yo'nalishlari ierarxiyasi

OAK shifri ierarxiyani o'zi kodlaydi, shuning uchun daraja **hisoblab
topiladi**, sun'iy kategoriya o'ylab topilmaydi:

```
01.00.00  →  fan tarmog'i        (level 1)
05.01.00  →  ixtisoslik guruhi   (level 2)
05.01.04  →  ixtisoslik          (level 3)
kodsiz    →  kichik yo'nalish    (otasi + 1)
```

`utils/scientific_fields.py`: `level_from_code`, `parent_code`, `branch_code`,
`resolve_level`, `check_no_cycle` (halqa va `MAX_DEPTH=6` himoyasi),
`build_tree`, `descendant_ids`.

**10 ta sun'iy kategoriya yo'q** — daraxt to'liq recursive va kamida 4 darajani
qo'llaydi.

> **QAT'IY QOIDA.** Rasmiy 2-daraja guruh mavjud bo'lsa, 3-daraja ixtisoslik
> HECH QACHON to'g'ridan-to'g'ri 1-daraja fan tarmog'iga bog'lanmaydi.
> Guruh bazada bo'lmasa yozuv import qilinmaydi va hisobotda ko'rsatiladi
> (`docs/oak-specialties-import.md` §8).

---

## 2. Route'lar

### Public

| Route | Holat |
|---|---|
| `/maqolalar` | katalog: qidiruv, filtr, sort, karta/jadval, sahifalash |
| `/maqolalar/yonalishlar` | ierarxiya, progressive disclosure, qidiruv |
| `/maqolalar/yonalish/<slug>` | yo'nalish + undagi maqolalar |
| `/maqolalar/<slug>` | maqola tafsiloti |
| `/maqolalar/<slug>/oqish` | **ichki PDF.js o'quvchisi** (Phase 2B.2) |
| `/maqolalar/<slug>/pdf` | o'quvchi uchun himoyalangan PDF oqimi (inline, Range) |
| `/maqolalar/<slug>/fayl` | PDF yuklab olish (attachment) — UI'da tugma yo'q |

### Admin

`/admin/articles`, `/admin/articles/new`, `/admin/articles/<id>/edit`,
`POST /admin/articles/metadata/lookup` (DOI autofill — faqat o'qish),
`/admin/articles/<id>/review`, `/admin/articles/<id>/preview`,
`/admin/articles/<id>/delete` (arxivlash),
`/admin/scientific-fields[/new|/<id>/edit|/<id>/delete]`.

### `/oqish` va `/pdf` ajratilishi

O'quvchi sahifasi va PDF baytlari **ataylab ikki route**:

* `/oqish` — HTML karkas (`templates/maqolalar_oqish.html`). U hech qanday
  PDF bayti bermaydi va hech qanday huquq qoidasini o'zi hisoblamaydi.
* `/pdf` — himoyalangan oqim. PDF.js aynan shu manzildan o'qiydi.

Ikkalasi ham `_public_pdf_or_404()` orqali o'tadi, u esa `can_read_pdf()` ni
chaqiradi. Qoida **nusxalanmaydi**: `/oqish` ochiq bo'lsa-yu `/pdf` ochiq
bo'lmasligi (yoki aksincha) mumkin emas. Ruxsat yo'q bo'lsa ikkalasi ham
`404` beradi — fayl mavjudligi fakti ham oshkor qilinmaydi.

Sahifa route'i Phase 2B.1 dan beri **o'zgarmagan**: havolalar allaqachon
`/oqish` ga qo'yilgan edi, Phase 2B.2 da faqat shablon almashdi. URL
o'zgarmadi, redirect zanjiri paydo bo'lmadi.

---

## 3. Statuslar va cheklangan qiymatlar

Python enum emas, **validated string** (loyiha uslubi;
`utils/article_rules.py` da lug'at sifatida yorliqlari bilan).

| Guruh | Qiymatlar |
|---|---|
| `access_type` | `open_access`, `author_permission`, `public_domain`, `repository_copy`, `metadata_only`, `restricted` |
| `version_type` | `version_of_record`, `accepted_manuscript`, `preprint`, `author_copy`, `repository_copy`, `other` |
| `source_type` | `open_journal`, `university_repository`, `subject_repository`, `author_website`, `author_submission`, `public_domain`, `other_verified_source` |
| `verification_status` | `draft`, `pending_review`, `verified`, `rejected`, `copyright_hold` |
| `publication_status` | `draft`, `published`, `archived` |
| `license_type` | `cc_by`, `cc_by_sa`, `cc_by_nc`, `cc_by_nc_sa`, `cc0`, `public_domain`, `author_permission`, `repository_permission`, `other`, `unknown` |

PDF **saqlash** faqat `PDF_ALLOWED_ACCESS_TYPES` uchun:
`open_access`, `author_permission`, `public_domain`, `repository_copy`.
`metadata_only` va `restricted` da fayl serverga umuman yuklanmaydi.

---

## 4. PDF ommaviyligi qoidasi (yagona manba)

```python
from utils.article_rules import can_read_pdf, pdf_block_reason
```

`can_read_pdf()` **faqat** quyidagilar bir vaqtda to'g'ri bo'lsa `True`:

1. PDF fayl mavjud;
2. `publication_status == 'published'`;
3. `verification_status == 'verified'` (va `copyright_hold` emas);
4. `access_type` ruxsat etilgan to'rttadan biri;
5. `license_type` ma'lum (`unknown` emas va ro'yxatda bor);
6. `source_name` bo'sh emas.

Bu qoida **hech qayerda takrorlanmaydi**:

* route (`/oqish`, `/fayl`) uni chaqiradi va bajarilmasa 404 beradi;
* shablon faqat backend qo'ygan `article.can_read_pdf` maydonini o'qiydi;
* admin preview ham aynan shu funksiyadan foydalanadi.

`pdf_block_reason()` sababni qaytaradi — u **faqat admin ekranlarida**
ko'rsatiladi (moderatsiya sahifasi, ro'yxatdagi "Yopiq" badge title'i).
Foydalanuvchi hech qachon sababni ko'rmaydi, u faqat tugmaning yo'qligini
ko'radi.

> **PDF fayl mavjudligi uni ko'rsatishga huquq bermaydi.**

---

## 5. PDF saqlash va xavfsizlik

**Joylashuv:** `storage/articles/YYYY/MM/<random>.pdf` — `static/` dan
**tashqarida**. nginx `/static/` ni to'g'ridan-to'g'ri beradi
(`deploy/olimlar.uz.conf`), shuning uchun u yerda turgan fayl `can_read_pdf`
qoidasini butunlay chetlab o'tar edi. Papka `.gitignore` da.

Sozlash: `ARTICLE_STORAGE_DIR` (ixtiyoriy), `ARTICLE_PDF_MAX_MB` (default 15,
qattiq shift 19 MB — Flask'ning global `MAX_CONTENT_LENGTH=20 MB` idan kichik
bo'lishi shart, aks holda foydalanuvchi tushunarsiz 413 oladi).

**Tekshiruvlar** (`security_uploads.py`):
extension `.pdf` → MIME oq ro'yxati → `%PDF-` magic bytes → oxirida `%%EOF` →
chegaralangan o'qish (hajm) → SHA-256 → dublikat checksum tekshiruvi →
CSRF (global `CSRFProtect`) → admin avtorizatsiyasi.

Fayl nomi **to'liq server tomonidan** generatsiya qilinadi
(`secrets.token_urlsafe(24)`) — foydalanuvchi URL ni taxmin qila olmaydi.
Original nom faqat metadata sifatida saqlanadi va `sanitize_pdf_filename()`
dan o'tadi. Yozish atomik (`tempfile` + `os.replace`).
`resolve_private_pdf()` `realpath` solishtiruvi bilan traversalni to'sadi.

### Ma'lum cheklov: PDF sanitizatsiya qilinmaydi

Hozirgi stack'da (Pillow bor, PDF kutubxonasi yo'q) PDF ichidagi JavaScript,
`/OpenAction` yoki o'rnatilgan fayllarni **tozalash imkoni yo'q**. Shuning
uchun:

* PDF **hech qachon** brauzerning o'z plaginiga topshirilmaydi. `/pdf` route
  `Content-Disposition: inline` beradi, lekin uni faqat PDF.js o'qiydi va
  sahifaga `<canvas>` sifatida chizadi — `<iframe>`, `<embed>` yoki `<object>`
  ishlatilmaydi (bu `tests/test_articles_viewer.py` bilan qulflangan);
* PDF ichidagi JavaScript ijro etilmaydi: `enableScripting: false`, va
  PDF.js ning `quickjs-eval` dvigateli serverga **umuman ko'chirilmagan**
  (`static/vendor/pdfjs/README.md`);
* admin faqat ishonchli manbadan olingan faylni joylashtirishi shart —
  huquqiy tasdiq checkboxi shu javobgarlikni qayd etadi.

### Fayl almashtirish va o'chirish

* Yangi fayl **avval diskka** yoziladi, keyin DB yangilanadi; eski fayl faqat
  tranzaksiya **commit bo'lgandan keyin** o'chiriladi. Xato bo'lsa eski fayl
  joyida qoladi va maqola ishlashda davom etadi.
* Validatsiya yiqilsa yangi saqlangan fayl darhol tozalanadi (yetim qolmaydi).
* Maqola "o'chirilganda" **arxivlanadi** (`publication_status='archived'`),
  PDF diskda qoladi — loyiha siyosati (grants: `is_active=FALSE`,
  dissertations: `merged_into_id`) ma'lumotni yo'q qilmaslik.

---

## 6. Litsenziya va huquqiy tasdiq

`unknown` litsenziyada PDF **hech qachon** ommaga chiqmaydi (default ham shu).
CC litsenziyalari uchun `license_url` bo'sh qolsa kanonik havola avtomatik
qo'yiladi.

Admin PDF yuklashda quyidagini tasdiqlaydi:

> Ushbu faylni Olimlar.uz serverida saqlash va foydalanuvchilarga ko'rsatish
> uchun yetarli huquq yoki ochiq litsenziya mavjudligini tasdiqlayman.

Saqlanadi: `rights_confirmed_by` (admin id), `rights_confirmed_at`
(timestamp), `permission_note` (kim/qachon/qanday ruxsat berdi).

Checkboxning o'zi yetarli huquqiy kafolat **emas** — u audit izi. Backend
baribir litsenziya, manba va tekshiruv holatini talab qiladi.

---

## 7. Admin ish oqimi

1. `/admin/articles/new` — forma 8 ta mantiqiy bo'limga ajratilgan:
   asosiy ma'lumot → mualliflar → nashr → yo'nalishlar → indekslash →
   ochiq kirish va huquq → PDF → holat va moderatsiya.
2. Saqlangach maqola `draft` / `draft` holatida bo'ladi va katalogda
   ko'rinmaydi.
3. `/admin/articles/<id>/preview` — **public shablonning o'zi** (alohida
   ikkinchi dizayn yo'q), draft banneri va `noindex` bilan; faqat admin ochadi.
4. `/admin/articles/<id>/review` — tekshiruv va nashr holatini qo'yish.
   Sahifa PDF nega ochiq emasligini aniq ko'rsatadi.
5. `verification_status='verified'` + `publication_status='published'` →
   maqola katalogga chiqadi; qolgan shartlar bajarilgan bo'lsa "O'qish"
   tugmasi ham paydo bo'ladi.

Xato holatlar foydalanuvchiga **tushunarli xabar** sifatida qaytadi (texnik
exception emas): dublikat DOI, dublikat checksum, noto'g'ri litsenziya/kirish
kombinatsiyasi, asosiy yo'nalish tanlanmagani, PDF xatolari.

---

## 8. Seed formati va CLI

Loyihada Flask CLI komandalari yo'q — barcha seed ishlari `scripts/*.py`
(`seed_grants.py` naqshi).

```bash
# dry-run (hech nima yozilmaydi)
DATABASE_URL=... python scripts/seed_scientific_fields.py \
    --file data/scientific_fields.json --dry-run

# haqiqiy seed
DATABASE_URL=... python scripts/seed_scientific_fields.py \
    --file data/scientific_fields.json
```

Xususiyatlari: idempotent (`code`, kod bo'lmasa `slug` bo'yicha), bitta
tranzaksiya, xatoda to'liq rollback, create/update/skip sonini chiqaradi,
noto'g'ri ota yoki takroriy shifrda **aniq xato**.

Fayl formati:

```json
{
  "source_name": "OAK ilmiy ixtisosliklar klassifikatori — fan tarmoqlari",
  "source_url": "https://oak.uz/",
  "source_updated_at": null,
  "fields": [
    {"code": "13.00.00", "name_uz": "Pedagogika fanlari", "level": 1, "sort_order": 130},
    {"code": "13.00.01", "name_uz": "…", "parent_code": "13.00.00", "sort_order": 1}
  ]
}
```

`parent_code` berilmasa OAK shifridan hisoblanadi.

### OAK ixtisosliklari dataseti

Taksonomiyaning 3-darajasi (ixtisosliklar) alohida quvurdan keladi:
OAK.UZ dan qo'lda yig'ilgan manba → `scripts/parse_oak_specialties.py` →
`data/oak/normalized/oak_specialties_approved.json` → shu seed skripti.

To'liq jarayon, normalizatsiya qoidalari, review workflow, transliteratsiya va
yetishmayotgan guruh nomlari: **`docs/oak-specialties-import.md`**.

### Development sample production'dan ajratilgan

`data/scientific_fields.sample.json` da `"sample": true` bor. Bu faylni seed
qilish uchun `--allow-sample` bayrog'i **ataylab** talab qilinadi; bayroqsiz
skript uni rad etadi. Shu sababli sample hech qachon production bazaga
tasodifan tushmaydi.

---

## 9. Nima yetishmayapti: to'liq OAK dataseti

> **Yangilandi (2-marta).** Taksonomiya endi TO'LIQ: 23 ta fan tarmog'i,
> 19 ta rasmiy ixtisosliklar guruhi va 255 ta ixtisoslik. 2-daraja guruh
> nomlari va 21-fan tarmog'i («Ҳарбий фанлар») rasmiy OAK 2021 ro'yxatidan
> olindi — `docs/oak-specialties-import.md` §9. Quyidagi matn 1-daraja
> repozitoriy seed'i haqida va o'z kuchida qoladi.

`data/scientific_fields.json` da **faqat 1-daraja** — 22 ta fan tarmog'i.
Ular repozitoriyda allaqachon tasdiqlangan holda bor edi
(`app.py:SPECIALTY_NAMES` + `seed_data.JOURNALS_BY_SPECIALTY`, OAK jurnallari
aynan shu 22 shifr bo'yicha guruhlangan), va test ikkalasining bir xilligini
tekshiradi.

**2-daraja (ixtisoslik guruhi) va 3-daraja (ixtisoslik) nomlari repozitoriyda
YO'Q va sun'iy to'ldirilmadi.** Ularni kiritishning ikki qonuniy yo'li bor:

1. **Rasmiy OAK registri** olingach — uni `data/scientific_fields.json` ga
   `parent_code` bilan qo'shish, `source_updated_at` ni to'ldirish va seedni
   qayta ishga tushirish (idempotent, dublikat yaratmaydi).
2. **Production ma'lumotidan qurish** — `dissertations` jadvalidagi haqiqiy
   `ixtisoslik` / `ixtisoslik_nomi` juftliklaridan (OAK importidan kelgan):

   ```bash
   DATABASE_URL=... python scripts/seed_scientific_fields.py \
       --from-dissertations --dry-run
   ```

   Bu ham nom **o'ylab topmaydi**: `ixtisoslik_nomi` bo'sh bo'lgan shifr
   o'tkazib yuboriladi, ota yo'nalish bazada bo'lmasa esa skript qaysi
   shifrlar yetishmayotganini ro'yxatlab beradi va to'xtaydi.

Nima yetishmayotgani aniq: **ixtisoslik guruhi (XX.YY.00) darajasining rasmiy
nomlari.** Ular hech qanday mavjud manbada (repo, CSV, production ustuni)
yo'q — faqat OAK ning o'z registridan olinishi mumkin.

---

## 10. Qidiruv, filtr va performance

Qidiruv `utils/search_helper.build_search_clause` orqali: aniq mos (ILIKE) +
kiril↔lotin variantlari + (agar `pg_trgm` **allaqachon o'rnatilgan** bo'lsa)
trigram. Kengaytma **hech qachon avtomatik yaratilmaydi**
(`data_quality/config.py` bilan bir xil siyosat) — indekssiz `similarity()`
katalogni sekinlashtiradi. Yangi search engine (Elasticsearch) qo'shilmadi.

Qidiruv qamrovi: `title`, `abstract`, `keywords`, `doi`, `journal_name` va
mualliflar nomi (LATERAL `string_agg` orqali, N+1siz).

Filtrlar to'liq query parametrlarda — bookmark qilinadi, sahifalash bilan
saqlanadi, `Tozalash` bilan qaytariladi, noto'g'ri qiymatda **500 bermaydi**
(`_int_arg` / `_choice_arg` jimgina defaultga qaytadi).

Sahifalash server tomonda: default 20, maksimal 60, sahifa raqami 500 bilan
cheklangan (cheksiz OFFSET skani bo'lmasin).

N+1 yo'q: mualliflar va yo'nalishlar sahifadagi barcha maqolalar uchun
**ikkita** so'rovda (`= ANY(%s)`) olinadi.

Indekslar: `slug` (UNIQUE), `doi` (partial UNIQUE), `checksum_sha256`
(partial), `(publication_status, verification_status, publication_year DESC)`
— katalogning issiq yo'li, `publication_year`, `language`, `journal_name`,
`created_at`, `view_count`, va assotsiatsiya FK'lari. Full-text indeks
qo'shilmadi: loyihada `pg_trgm` siyosati allaqachon belgilangan va uni bu
modul o'zgartirmaydi.

---

## 11. SEO

* Katalog va yo'nalishlar: `title`, `meta description`, canonical (base.html),
  Open Graph, `CollectionPage` JSON-LD.
* Maqola tafsiloti: `ScholarlyArticle` JSON-LD — **faqat haqiqatan mavjud
  maydonlar** (author, datePublished, isPartOf, identifier/DOI, license,
  inLanguage, pagination). Taxminiy iqtibos yoki indeks ma'lumoti
  **yozilmaydi** (test buni tekshiradi).
* Admin preview — `noindex, nofollow`.
* `/oqish` — `noindex, nofollow` (meta + `X-Robots-Tag`), canonical maqola
  tafsilotiga ishora qiladi. `base.html` da shu maqsadda `{% block robots %}`
  va `{% block canonical %}` bloklari bor, shunda sahifada ikkita
  qarama-qarshi direktiva qolmaydi.
* `/pdf` va `/fayl` — `X-Robots-Tag: noindex, nofollow, noarchive`: PDF
  matni maqola tafsiloti orqali indekslanadi, faylning o'zi `can_read_pdf()`
  dan tashqarida turgan alohida hujjat bo'lib qolmasligi kerak.

---

## 12. Testlar

| Fayl | Nima tekshiradi | DB |
|---|---|---|
| `tests/test_articles_rules.py` | `can_read_pdf`, litsenziya, DOI, slug, ierarxiya, halqa, PDF yuklash/saqlash | yo'q |
| `tests/test_articles_catalog.py` | route'lar, qidiruv/filtr/sort/sahifalash SQL'i, shablonlar, SEO, accessibility | qalbaki |
| `tests/test_articles_viewer.py` | o'quvchi kirishi, oqim sarlavhalari, Range, traversal, SEO, accessibility, dizayn tizimi, vendoring | qalbaki + haqiqiy fayl |
| `tests/test_article_metadata_providers.py` | Crossref/OpenAlex/Unpaywall klientlari, timeout, 429, sxema | yo'q |
| `tests/test_article_metadata_merge.py` | ustuvorlik, konflikt, muallif moslashtirish, orkestratsiya | yo'q |
| `tests/test_article_metadata_admin.py` | lookup endpointi, DB'ga yozmaslik, javob xavfsizligi, forma UI, sxema 009 | qalbaki |
| `tests/test_articles_admin.py` | avtorizatsiya, CSRF, forma validatsiyasi, PDF yuklash yo'llari | qalbaki |
| `tests/test_articles_migration.py` | migratsiya xavfsizligi + `_ensure_schema` bilan parity | yo'q |
| `tests/test_articles_seed.py` | seed formati, idempotentlik, dry-run | yo'q |
| `tests/test_articles_dbgated.py` | haqiqiy WHERE semantikasi, DB cheklovlari, CASCADE/RESTRICT | **staging** |
| `tests/test_oak_translit.py` | kirill→lotin transliteratsiya | yo'q |
| `tests/test_oak_parser.py` | OAK manbasini parsing va validatsiya | yo'q |
| `tests/test_oak_import.py` | migratsiya 008, ota hal qilish, review gate | qalbaki |
| `tests/test_oak_field_ui.py` | katta taksonomiya UI va admin filtrlari | qalbaki |

Qalbaki DB (`tests/fake_pg.py`) **filtrlamaydi** — u faqat qatorlarni
qaytaradi. Shuning uchun filtr va xavfsizlik semantikasi *yuborilgan SQL
matni* ustida tekshiriladi, haqiqiy semantika esa staging'da:

```bash
TEST_DATABASE_URL=postgresql://…/staging \
    python3.12 -m unittest tests.test_articles_dbgated
```

### Ma'lum cheklov: lokal DB yo'q

Lokal Neon kredensiallari eskirgan (alohida texnik qarz). Shu sababli
`/maqolalar` lokal muhitda 500 qaytaradi — bu **ataylab**: migratsiya
bajarilmagan yoki DB yo'q holat production fallback bilan yashirilmaydi
(§31). Buning bir oqibati bor: `tests/test_light_only_theme.py` va
`tests/test_navigation_shell.py` ilgari shell'ni tekshirish uchun
`/maqolalar` dan foydalanardi va endi ular DB'ga tegmaydigan sahifalarga
(`/about`, `/preparation`) ko'chirildi — aks holda ular sezdirmasdan xato
sahifasini tekshirib yurar edi.

---

## 13. Ichki PDF o'quvchisi (Phase 2B.2)

Maqsad: foydalanuvchi maqolani Olimlar.uz'dan **chiqmasdan** o'qiydi, PDF esa
`can_read_pdf()` qoidasidan tashqariga chiqmaydi.

### 13.1 Arxitektura

```text
GET /maqolalar/<slug>/oqish          -> maqolalar_oqish.html (faqat karkas)
   |  templates/maqolalar_oqish.html
   |    data-lib     -> /static/vendor/pdfjs/pdf.min.js?v=<versiya>
   |    data-worker  -> /static/vendor/pdfjs/pdf.worker.min.js?v=<versiya>
   |    data-pdf-url -> /maqolalar/<slug>/pdf
   v
static/js/maqolalar_viewer.js        -> PDF.js ni yuklaydi, Worker'ni yaratadi
   v
GET /maqolalar/<slug>/pdf            -> send_file(conditional=True), Range 206
```

Server hech qachon fayl tizimidagi yo'lni yoki `pdf_path` storage kalitini
klientga bermaydi — shablonda faqat route manzili bor.

### 13.2 PDF.js

| | |
| --- | --- |
| Versiya | **5.7.284**, `legacy` build (eskiroq brauzerlar uchun transpil qilingan) |
| Joylashuv | `static/vendor/pdfjs/` — vendoring, runtime CDN **yo'q** |
| Versiya manbai | `blueprints/articles.py:PDFJS_VERSION` (shablon `?v=` bilan uzatadi) |
| Provenance | `static/vendor/pdfjs/README.md` — tarball URL, SHA-256, nima olingan/olinmagan |

Upstream `web/pdf_viewer.mjs` viewer komponenti **olinmagan**: u o'zi bilan
`pdf_viewer.css` ni olib keladi, unda esa beshta to'q rejim media so'rovi bor
va `tests/test_light_only_theme.py` ni yiqitadi. O'rniga past darajali API
(`getDocument` + `TextLayer`) ishlatiladi, kerakli `.textLayer` uslublari esa
`static/css/maqolalar_viewer.css` da dizayn tizimi tokenlari bilan qayta
yozilgan.

Worker ochiq (`new Worker(..., {type:'module'})`) yaratiladi va
`PDFWorker({port})` ga uzatiladi. Sabab: PDF.js ning "fake worker" fallback'i
parsingni asosiy oqimga ko'chiradi va interfeys muzlaydi — bunday tanazzul
**jimgina** bo'lmasligi kerak. Worker yuklanmasa foydalanuvchi tushunarli
xato ko'radi, konsolda esa aniq diagnostika qoladi. Kutubxona va worker
versiyalari runtime'da solishtiriladi.

### 13.3 Himoyalangan oqim (`/maqolalar/<slug>/pdf`)

| Sarlavha | Qiymat | Sabab |
| --- | --- | --- |
| `Content-Type` | `application/pdf` | |
| `Content-Disposition` | `inline; filename="<slug>.pdf"` | storage kaliti oshkor qilinmaydi |
| `X-Content-Type-Options` | `nosniff` | |
| `X-Robots-Tag` | `noindex, nofollow, noarchive` | fayl alohida indekslanmaydi |
| `Accept-Ranges` | `bytes` | `send_file(conditional=True)` |
| `Cache-Control` | `private, max-age=3600` | `copyright_hold` ga o'tgan maqola ulashilgan keshdan berilib qolmasin |

**Range:** `send_file(..., conditional=True)` Werkzeug'ning
`make_conditional(accept_ranges=True)` ini chaqiradi, shuning uchun
`Range: bytes=0-1023` so'roviga `206 Partial Content` +
`Content-Range: bytes 0-1023/<hajm>` qaytadi. Bu PDF.js uchun **shart**, chunki
u hujjatni bo'laklab so'raydi. Test:
`tests/test_articles_viewer.py:ViewerResponseHeaderTests.test_range_requests_are_supported`
(haqiqiy vaqtinchalik fayl ustida o'lchanadi, mock emas).

`X-Accel-Redirect` (nginx) **ishlatilmadi**: hozirgi `deploy/olimlar.uz.conf`
da `internal` location yo'q va uni qo'shish yangi infratuzilma bo'lardi.
Fayl WSGI file wrapper orqali oqadi — baytlar Python xotirasiga to'liq
o'qilmaydi. PDF chegarasi 15 MB (`ARTICLE_PDF_MAX_MB`), shuning uchun
gunicorn uchun bu yuk sezilarli emas. Trafik o'sib ketsa `X-Accel-Redirect`
keyingi optimizatsiya sifatida ochiq qoladi.

### 13.4 CSP

**O'zgartirilmadi.** `security.py:_CSP` allaqachon yetarli:

* `default-src 'self'` — worker `worker-src` -> `child-src` -> `default-src`
  zanjiri bo'yicha ruxsat oladi; worker bir xil originda, `blob:` kerak emas;
* `isEvalSupported: false` — PDF.js `new Function` ishlatmaydi, `unsafe-eval`
  **talab qilinmaydi**;
* `connect-src 'self'` — Range so'rovlari, standart shriftlar va WASM
  dekoderlari hammasi bir xil origindan.

`tests/test_articles_viewer.py:ViewerSecurityContractTests` CSP'da
`unsafe-eval` paydo bo'lishini va `worker-src` bo'shatilishini bloklaydi.

### 13.5 Interfeys

Yuqori panel (light, `--color-white`): maqolaga qaytish, sarlavha, panel
tugmasi, oldingi/keyingi sahifa, sahifa raqami maydoni, kichraytirish,
masshtab (50–300% + kenglik bo'yicha), kattalashtirish, kenglikka moslash,
qidiruv; burish va to'liq ekran — "boshqa amallar" menyusida.

ARIA `role="toolbar"` **ataylab ishlatilmagan**: u chap/o'ng strelkalarni
fokus ko'chirishga band qiladi, bizda esa ular sahifa almashtiradi. O'rniga
nomlangan `role="group"` guruhlari (dizayn tizimi §22 buni ochiq ruxsat
etadi).

Chap panel: sahifa eskizlari (lazy) + hujjat tuzilmasi (outline; PDF'da
tuzilma bo'lmasa tab umuman chiqmaydi). ≤767px da panel off-canvas tortmaga
aylanadi (`data-side="open"`), boshlang'ich holat esa CSS media so'rovi bilan
beriladi — JS yuklanmasa ham tartib to'g'ri.

Klaviatura: `←`/`PageUp`, `→`/`PageDown`, `+`/`=`, `-`, `0` (kenglikka
moslash), `Esc` (menyu -> qidiruv -> to'liq ekran -> tortma). Maydonga fokus
bo'lganda shortcut'lar ishlamaydi.

### 13.6 Performance

* Sahifalar oldindan **bo'sh oq varaq** sifatida yaratiladi (nisbat birinchi
  sahifadan) — 300 sahifali hujjatda ham boshida bitta `getPage()` qilinadi,
  lekin scroll balandligi to'g'ri bo'ladi va sahifa sakramaydi.
* `IntersectionObserver` (rootMargin 120%) — faqat ko'rinadigan va yaqin
  sahifalar chiziladi.
* Bir vaqtda eng ko'pi **4 ta** chizilgan sahifa saqlanadi; uzoqdagilarning
  kanvasi `width = height = 0` bilan bo'shatiladi.
* Kanvas maydoni 8 MP bilan chegaralangan — 200% + Retina'da bitta sahifa
  30 MB rastr bo'lib ketmasin.
* Sahifa ko'rinishdan chiqsa `renderTask.cancel()`; masshtab chizish paytida
  o'zgarsa vazifa `dirty` bayrog'i bilan qayta navbatga qo'yiladi.
* Resize 150 ms debounce; qayta chizish faqat "kenglik bo'yicha" rejimda.
* Eskizlar ham lazy, alohida observer bilan.
* PDF baytlari **hech qachon** base64 ichiga solinmaydi — faqat URL beriladi.

### 13.7 Matn qatlami va qidiruv

`TextLayer` chiziladi: matn tanlash, nusxa olish va brauzerning o'z
qidiruvi (ko'rinib turgan sahifalarda) ishlaydi. **OCR yo'q**: skanerlangan
PDF matn qatlamisiz bo'lsa, o'quvchi shunchaki kanvas ko'rsatadi.

"Hujjat ichidan qidirish" MVP darajasida: `getTextContent()` bo'yicha
moslik topilgan **sahifaga o'tiladi**, matn ichida ajratib ko'rsatilmaydi.
Upstream `PDFFindController` qo'shilmadi — u `EventBus` + `PDFViewer` +
bog'langan matn qatlamlarini, ya'ni butun upstream viewer arxitekturasini
talab qiladi (va u bilan birga dark-mode CSS'ni). Ajratib ko'rsatish
keyingi bosqichga qoldirildi.

### 13.8 Havolalar va PDF ichidagi kod

PDF.js `AnnotationLayer` **ishlatilmaydi** (u ham upstream CSS'ni talab
qiladi). O'rniga yupqa o'z qatlamimiz: faqat `Link` tipidagi annotatsiyalar
chiziladi.

* tashqi havola — `new URL()` bilan tekshiriladi, faqat `http:`/`https:`
  o'tadi; `javascript:`, `data:`, `file:` **rad etiladi**;
* ochilishi — `target="_blank" rel="noopener noreferrer nofollow"`;
* hujjat ichidagi havola — `<button>`, `getDestination()`/`getPageIndex()`
  orqali sahifaga o'tadi;
* nomlangan amal yoki JavaScript amali — umuman chizilmaydi.

PDF ichidagi JavaScript ijro etilmaydi: `enableScripting: false`, va PDF.js
ning `quickjs-eval` dvigateli serverga umuman ko'chirilmagan.

### 13.9 Yuklab olish va chop etish

**Yuklab olish tugmasi o'quvchida yo'q.** Sabab: hozirgi huquqiy model faqat
"o'qish" huquqini biladi. `can_read_pdf()` — bu **ko'rsatish** huquqi;
`open_access` + CC BY maqola bilan `author_permission` + "faqat saytda
o'qishga ruxsat" maqolasi ayni shu funksiyada bir xil natija beradi, shuning
uchun uni yuklab olish huquqi deb talqin qilib bo'lmaydi.

`/maqolalar/<slug>/fayl` route'i Phase 2B.1 dan qolgan va ishlaydi, lekin
hech qayerda tugma sifatida ko'rsatilmaydi.

**Keyingi bosqich uchun kontrakt** — `utils/article_rules.py` ga qo'shiladi:

```text
can_download_pdf(article):
    can_read_pdf(article) shart, lekin YETARLI EMAS.
    Qo'shimcha: license_type qayta tarqatishga aniq ruxsat berishi kerak —
    {cc_by, cc_by_sa, cc_by_nc, cc_by_nc_sa, cc0, public_domain}.
    author_permission / repository_permission uchun admin alohida bayroq
    qo'yishi kerak (yangi ustun), chunki ruxsat matni har xil bo'ladi.
```

**Chop etish:** alohida "Chop etish" tugmasi qo'shilmadi — chop etish ham
yuklab olish kabi litsenziyaga bog'liq. Brauzerning o'z `Ctrl+P` imkoniyati
esa **bloklanmaydi**: bu soxta DRM bo'lardi va baribir chetlab o'tiladi.
Chop etilganda faqat ekranda chizilgan sahifalar chiqadi, chunki hujjat
sahifalari lazy chiziladi.

### 13.10 Nusxa ko'chirishdan "himoya" — yo'q

O'ng tugma bloklanmaydi, matn tanlash bloklanmaydi, skrinshot to'silmaydi,
anti-copy JS hiylalari yo'q. Huquq **kirish nazorati va litsenziya** bilan
boshqariladi (`tests/test_articles_viewer.py:test_no_fake_drm_tricks`).

### 13.11 Ma'lum cheklovlar

* **Accessibility manba PDF'ga bog'liq.** Teg qo'yilmagan yoki skanerlangan
  PDF'da matn qatlami bo'lmaydi va skrinrider hujjat mazmunini o'qiy olmaydi.
  O'quvchining o'z interfeysi (panel, tortma, holatlar) to'liq klaviatura va
  ARIA bilan ishlaydi; hujjatning o'zi esa manba sifatidan nariga o'ta olmaydi.
  Bu foydalanuvchiga sahifada ochiq yozilgan.
* **CJK PDF.** `cmaps/` vendoring qilinmagan (1.1 MB, auditoriya o'zbek/rus/
  ingliz). Xitoy/yapon/koreys hujjati paydo bo'lsa shriftlar chiqmaydi.
* **Parol bilan yopilgan PDF.** Aniqlanadi va tushunarli xabar ko'rsatiladi;
  parol yuborish tizimi **qurilmadi** — bunday fayl admin ko'rigidan o'tishi
  kerak.
* **PDF sanitizatsiya qilinmaydi** (§5). O'quvchi buni canvas rendering va
  o'chirilgan skripting bilan qoplaydi, lekin faylning o'zi tozalanmagan.
* **Brauzerda vizual QA qilinmadi** — muhitda brauzer avtomatizatsiyasi va
  ishlaydigan DB yo'q (§12 "Lokal DB yo'q"). PDF.js integratsiyasining
  DOM'siz qismi (parsing, viewport, matn, outline, annotatsiya) haqiqiy
  maqola PDF'lari ustida Node'da tekshirilgan.

### 13.12 Kelajakdagi AI paneli uchun kontrakt

AI paneli **qurilmadi**. Qurilganda o'quvchi quyidagi kontekstni bera oladi:

| Maydon | Manba |
| --- | --- |
| `article_id` | shablon konteksti (`article.id`) |
| `current_page` | `ArticlePdfViewer.currentPage` |
| `selected_text` | `window.getSelection()` — matn qatlami ustida ishlaydi |
| `document_page_count` | `ArticlePdfViewer.pageCount` |

**Majburiy shart:** AI xizmati PDF faylga klient bergan storage yo'li orqali
murojaat qilmaydi. U `article_id` ni oladi va faylni **server tomonida**
`_public_pdf_or_404()` (ya'ni `can_read_pdf()`) orqali ochadi. Aks holda
kirish nazorati klientga ko'chgan bo'lardi.

O'ng panel uchun tartibda joy bor, lekin hozir bo'sh panel ko'rsatilmaydi.

---

## 14. DOI metadata autofill (Phase 2C.1)

Admin DOI kiritganda Crossref / OpenAlex / Unpaywall'dan bibliografik
ma'lumot olinadi va admin formasiga NOMZOD sifatida taklif qilinadi.

To'liq hujjat: **[`docs/article-metadata-integration.md`](article-metadata-integration.md)**
(provayderlar, sozlamalar, manba ustuvorligi, konflikt qoidalari, OA va
huquqiy kirish turi farqi, PDF nomzodi siyosati, kesh, rate limit,
xavfsizlik, bulk import kontrakti).

Bu yerdagi modul uchun eng muhim uchta natija:

* `scientific_articles` ga tashqi identifikatorlar va provenance ustunlari
  qo'shildi (migratsiya 009, §15 dagi rollback naqshi bilan bir xil);
* `access_type` va `license_type` **hech qachon** avtomatik to'ldirilmaydi —
  ular huquqiy qaror va `can_read_pdf()` ga to'g'ridan-to'g'ri ta'sir qiladi;
* `is_retracted` bayrog'i paydo bo'ldi: chaqirib olingan maqola signali
  jimgina yo'qolmaydi.

---

## 15. Migratsiya va rollback

```bash
psql "$DATABASE_URL" -f migrations/007_create_article_catalog.sql
psql "$DATABASE_URL" -f migrations/008_scientific_field_provenance.sql
psql "$DATABASE_URL" -f migrations/009_article_external_metadata.sql
```

Har biri bitta tranzaksiya, to'liq idempotent, seed ma'lumoti ichida yo'q.
007 modul jadvallarini yaratadi va mavjud jadvallarga umuman tegmaydi;
008 va 009 esa faqat SHU modulning jadvallariga NULLable ustun qo'shadi
(`ADD COLUMN IF NOT EXISTS`) — mavjud qatorlar o'qilmaydi va qayta
yozilmaydi. Rollback SQL fayl oxirida izoh sifatida (ataylab bajarilmaydigan
holda) yozilgan — u ma'lumotni yo'q qiladi. `storage/articles/` dagi PDF
fayllar rollback bilan o'chirilmaydi.

Server `blueprints/articles.py:_ensure_schema` orqali birinchi so'rovda
o'zini-o'zi migratsiya qiladi (grants/conferences konvensiyasi), shuning uchun
migratsiya qo'lda ishlatilmasa ham sxema paydo bo'ladi. Ikkisi ajralib
ketmasligini `tests/test_articles_migration.py:SchemaParityTests` tekshiradi.
