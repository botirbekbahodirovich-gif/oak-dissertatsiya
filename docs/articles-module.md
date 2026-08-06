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

---

## 2. Route'lar

### Public

| Route | Holat |
|---|---|
| `/maqolalar` | katalog: qidiruv, filtr, sort, karta/jadval, sahifalash |
| `/maqolalar/yonalishlar` | ierarxiya, progressive disclosure, qidiruv |
| `/maqolalar/yonalish/<slug>` | yo'nalish + undagi maqolalar |
| `/maqolalar/<slug>` | maqola tafsiloti |
| `/maqolalar/<slug>/oqish` | **Phase 2B.2 viewer uchun zaxiralangan** |
| `/maqolalar/<slug>/fayl` | PDF oqimi, `can_read_pdf` bilan qo'riqlangan |

### Admin

`/admin/articles`, `/admin/articles/new`, `/admin/articles/<id>/edit`,
`/admin/articles/<id>/review`, `/admin/articles/<id>/preview`,
`/admin/articles/<id>/delete` (arxivlash),
`/admin/scientific-fields[/new|/<id>/edit|/<id>/delete]`.

### `/oqish` bo'yicha qaror

Route **hozirdan mavjud va ishlaydi**; 404 bermaydi va tafsilotga ham
qaytarmaydi — u o'zining minimal sahifasini ko'rsatadi.

Sabab: `docs/maqolalar-route-contract.md` uni ichki viewer sifatida e'lon
qilgan. Havolalar hozirdan shu manzilga qo'yiladi va viewer chiqqanda **faqat
shablon almashadi** — URL o'zgarmaydi, SEO buzilmaydi, redirect zanjiri
paydo bo'lmaydi.

Kirish qoidasi hozirdan to'liq kuchda: PDF ochiq bo'lmasa route **404** beradi
(fayl mavjudligi fakti ham oshkor qilinmaydi). Sahifa `noindex` — viewer
tayyor bo'lmaguncha qidiruvdan vaqtinchalik sahifaga tushib qolinmaydi.

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

* fayl brauzerga hech qachon inline `application/pdf` sifatida berilmaydi —
  `/fayl` route `as_attachment=True` bilan ishlaydi;
* Phase 2B.2 viewer faylni **o'zi** render qilishi kerak (pdf.js kabi), uni
  `<iframe>` yoki `<embed>` bilan brauzerga topshirmasligi kerak;
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

### Development sample production'dan ajratilgan

`data/scientific_fields.sample.json` da `"sample": true` bor. Bu faylni seed
qilish uchun `--allow-sample` bayrog'i **ataylab** talab qilinadi; bayroqsiz
skript uni rad etadi. Shu sababli sample hech qachon production bazaga
tasodifan tushmaydi.

---

## 9. Nima yetishmayapti: to'liq OAK dataseti

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
* Admin preview va `/oqish` — `noindex`.

---

## 12. Testlar

| Fayl | Nima tekshiradi | DB |
|---|---|---|
| `tests/test_articles_rules.py` | `can_read_pdf`, litsenziya, DOI, slug, ierarxiya, halqa, PDF yuklash/saqlash | yo'q |
| `tests/test_articles_catalog.py` | route'lar, qidiruv/filtr/sort/sahifalash SQL'i, shablonlar, SEO, accessibility | qalbaki |
| `tests/test_articles_admin.py` | avtorizatsiya, CSRF, forma validatsiyasi, PDF yuklash yo'llari | qalbaki |
| `tests/test_articles_migration.py` | migratsiya xavfsizligi + `_ensure_schema` bilan parity | yo'q |
| `tests/test_articles_seed.py` | seed formati, idempotentlik, dry-run | yo'q |
| `tests/test_articles_dbgated.py` | haqiqiy WHERE semantikasi, DB cheklovlari, CASCADE/RESTRICT | **staging** |

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

## 13. Phase 2B.2 (PDF viewer) uchun kontrakt

Viewer yozilganda **backendga tegish shart emas**. Tayyor kontrakt:

| Element | Qiymat |
|---|---|
| Sahifa route | `GET /maqolalar/<slug>/oqish` (mavjud, ishlaydi) |
| Almashtiriladigan shablon | `templates/maqolalar_oqish.html` |
| Fayl manbai | `GET /maqolalar/<slug>/fayl` → `application/pdf` |
| Kirish qoidasi | `can_read_pdf()` — ikkala route'da allaqachon qo'llangan; qo'shimcha tekshiruv **yozilmasin** |
| Ruxsat yo'q holat | `404` (mavjudlik oshkor qilinmaydi) |
| Shablonga keladigan kontekst | `article` — `_decorate()` dan o'tgan to'liq dict: `title`, `slug`, `pdf_size`, `pdf_original_name`, `license_label`, `source_name`, `authors`, `fields`, `can_read_pdf` |
| Yuklab olish hisoblagichi | `/fayl` `download_count` ni oshiradi |
| Vizual kontrakt | `docs/maqolalar-route-contract.md` — light-only, canvas'ga filtr/invert yo'q, ikonkalar Bootstrap Icons |
| Xavfsizlik | faylni `<iframe>`/`<embed>` bilan brauzerga topshirmang — PDF sanitizatsiya qilinmagan (§5) |

Viewer chiqqanda `maqolalar_oqish.html` dagi `noindex` olib tashlanadi va
`maqolalar_detail.html` dagi "PDF'ni o'qish" tugmasi o'zgarishsiz qoladi —
u allaqachon shu route'ga ishora qiladi.

---

## 14. Migratsiya va rollback

```bash
psql "$DATABASE_URL" -f migrations/007_create_article_catalog.sql
```

Bitta tranzaksiya, to'liq idempotent, mavjud jadvallarga **tegmaydi**, seed
ma'lumoti ichida yo'q. Rollback SQL fayl oxirida izoh sifatida (ataylab
bajarilmaydigan holda) yozilgan — u ma'lumotni yo'q qiladi va faqat butun
Phase 2B.1 ni qaytarishda ishlatiladi. `storage/articles/` dagi PDF fayllar
rollback bilan o'chirilmaydi.

Server `blueprints/articles.py:_ensure_schema` orqali birinchi so'rovda
o'zini-o'zi migratsiya qiladi (grants/conferences konvensiyasi), shuning uchun
migratsiya qo'lda ishlatilmasa ham sxema paydo bo'ladi. Ikkisi ajralib
ketmasligini `tests/test_articles_migration.py:SchemaParityTests` tekshiradi.
