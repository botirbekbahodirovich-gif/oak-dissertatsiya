# Mavjud maqola metadata sinxronizatsiyasi (Phase 2C.3A)

Admin bazadagi (allaqachon yaratilgan) DOI'li maqolani qayta tekshiradi:
Crossref/OpenAlex/Unpaywall'dan yangi metadata olinadi, DB'dagi joriy
qiymatlar bilan solishtiriladi (**diff**), va admin faqat o'zi tanlagan
maydonlarni **explicit** qo'llaydi.

Bog'liq hujjatlar: [`article-metadata-integration.md`](article-metadata-integration.md)
(DOI autofill, provayderlar), [`article-bulk-import.md`](article-bulk-import.md)
(yangi maqola ommaviy importi), [`articles-module.md`](articles-module.md)
(maqola ma'lumotlar modeli).

Ko'p maqolada topilgan o'zgarishlarni bitta markaziy sahifada ko'rish/review
qilish — [`article-metadata-sync-dashboard.md`](article-metadata-sync-dashboard.md)
(Phase 2C.3B). U shu yerdagi `check_article_metadata()`/
`apply_article_metadata_changes()`ni AYNAN o'zi qayta ishlatadi.

**Bir jumlada:** metadata tekshirish — o'qish. Yozish faqat admin
tanlaganini, faqat u "Qo'llash" bosgandan keyin, faqat o'sha element hali
tahrirlanmagan bo'lsa.

---

## 1. Arxitektura

```text
services/article_sync/
├── __init__.py    — ommaviy API
├── models.py       — MetadataFieldDiff, MetadataSyncResult (severity/action)
├── policy.py        — field ro'yxatlari (bitta joyda), matn normalizatsiyasi
├── diff.py            — compare_article_metadata() — sof funksiya
├── persistence.py      — DB CRUD, sxema, apply
└── service.py            — check_article_metadata() / apply_...()

blueprints/articles.py — POST /admin/articles/<id>/metadata/{check,apply}
migrations/011_create_article_metadata_syncs.sql
templates/admin/article_form.html — "Metadata sinxronizatsiyasi" bo'limi
static/{css/maqolalar.css, js/article_metadata_sync.js}
```

`services/article_sync/` Flask'ga BOG'LIQ EMAS — `services/article_import/`
bilan bir xil konvensiya. Metadata FAQAT
`services.article_metadata.fetch_article_metadata()` orqali olinadi;
Crossref/OpenAlex/Unpaywall mantig'i qayta yozilmagan.

Route'lar `blueprints/articles.py`ga qo'shildi (yangi blueprint YARATILMADI)
— chunki ular mavjud `_load_article`/`_admin_form_context` bilan tabiiy
integratsiyalashadi va DOI autofill (`admin_article_metadata_lookup`)
bilan bir xil faylda, bir xil konvensiyada turadi.

---

## 2. Diff modeli va severity

Har maydon — `MetadataFieldDiff`: `field`, `label`, `old_value`, `new_value`,
`source`, `severity`, `recommended_action`, `applicable`, `default_selected`.

```text
unchanged  — hech qachon qaytarilmaydi (diff ro'yxati faqat O'ZGARGANLARNI o'z ichiga oladi)
safe       — bo'sh joyni to'ldirish / iqtibos oshishi — default TANLANGAN
review     — mavjud qiymat bilan farq/to'qnashuv — default TANLANMAGAN
critical   — retraction false→true, DOI nomuvofiqligi — EXPLICIT tasdiq talab qiladi
```

Field policy — **bitta joyda**, `services/article_sync/policy.py`:

| Guruh | Maydonlar | Severity |
| --- | --- | --- |
| Matn/sana | title, abstract, journal_name, publication_year, publication_date, volume, issue, pages, language, external_url | review (o'zgarsa) |
| Tashqi ID | openalex_id, crossref_member_id, primary_issn | bo'sh bo'lsa **safe**, to'qnashsa **review** |
| Iqtibos | citation_count (+source+checked_at) | oshsa **safe**, kamaysa **review** (§7) |
| Mualliflar | authors (to'liq almashtirish) | review — fuzzy merge yo'q, faqat ORCID/normallashtirilgan ism (§11, Phase 2C.1 qoidalari qayta ishlatiladi) |
| Retraction | is_retracted | false→true **critical**; true→false **review** (avtomatik un-retract yo'q, §6) |
| **Faqat ma'lumot** | oa_status, license_candidates, candidate_pdf_url | review, lekin **`applicable=False`** — checkbox YO'Q, hech qachon qo'llanmaydi (§8-10) |
| Bloklovchi | doi (mismatch) | critical, `applicable=False`, apply'ni EXPLICIT tasdiqsiz to'xtatadi |

Matn solishtirish false-positive'ni kamaytiradi (§12): trim, whitespace
collapse, Unicode NFKC, ba'zi maydonlarda case-insensitive. Bo'sh satr va
`NULL` bir xil deb qaraladi. Original qiymat (tozalanmagan) UI'da ko'rsatiladi
— agressiv transliteratsiya yo'q.

`publication_date` — bitta ustun, xuddi Phase 2C.1 create-oqimi bilan bir
xil semantika (Crossref/OpenAlex/Unpaywall'ning online/print/issued farqi
DB modelida ajratilmagan, §13); diff faqat qaysi provayderdan kelganini
(`source`) ko'rsatadi, admin o'zi baholaydi.

---

## 3. Oqim

```text
Admin "Metadata'ni tekshirish" bosadi
  ↓ POST /admin/articles/<id>/metadata/check
  ↓ fetch_article_metadata(article.doi)
  ↓ compare_article_metadata(article, fresh)
  ↓ article_metadata_syncs'ga YOZILADI (status='checked')
  ↓ diff JSON javobda qaytadi — article HALI O'ZGARMAGAN
Admin checkbox bilan maydon tanlaydi (safe — default tanlangan)
  ↓ POST /admin/articles/<id>/metadata/apply {sync_id, fields}
  ↓ stale-check → allowlist → (retraction/DOI confirm) → BITTA tranzaksiya
  ↓ scientific_articles/article_authors yoziladi + sync status='applied'
```

`check` endpoint **hech qachon** article maydonlarini o'zgartirmaydi —
faqat audit yozuvi (`article_metadata_syncs`) yaratadi.

---

## 4. Stale himoya (majburiy, §17)

Har `check` — `scientific_articles.updated_at`ning SHU PAYTDAGI qiymatini
`article_updated_at_snapshot` sifatida saqlaydi. `apply` paytida bu qiymat
joriy `updated_at` bilan solishtiriladi:

```text
mos kelmasa → ArticleSyncError('stale',
  "Metadata tekshirilgandan keyin maqola o'zgartirilgan. Qayta tekshiring.")
```

Bu boshqa admin orada qo'lda tahrirlagan bo'lsa, eski sync natijasi uni
KO'RIB-KO'RMASDAN bosib o'tishining oldini oladi.

---

## 5. Idempotency va allowlist (§16, §18)

- `apply` faqat `sync.status == 'checked'` bo'lganda ishlaydi; muvaffaqiyatli
  qo'llangandan keyin status `'applied'`ga o'tadi va **qayta qo'llanmaydi**
  (`already_applied` xatosi, article ikkinchi marta YOZILMAYDI).
- Client faqat **maydon nomlarini** (`fields: ["citation_count", ...]`) va
  `sync_id`ni yuboradi — **QIYMATNI EMAS**. Server yangi qiymatni
  `article_metadata_syncs.diff_summary`dan (o'zi SAQLAGAN) o'qiydi.
  `APPLICABLE_FIELDS` allowlist'dan tashqari yoki diff'da yo'q nom butunlay
  e'tiborsiz qoldiriladi — client `{"citation_count": 999999}` yuborsa ham
  bu qiymat hech qachon o'qilmaydi.
- Mualliflar tanlansa, `article_authors` **TO'LIQ** almashtiriladi (delete +
  insert, bitta tranzaksiya) — qisman/bulk-avtomatik birlashtirish yo'q.

---

## 6. Retraction siyosati (§6)

```text
false → true   — CRITICAL. Checkbox + admin explicit tasdiqisiz apply BLOKLANADI.
true  → false  — REVIEW. Avtomatik "un-retract" YO'Q — admin qo'lda tanlashi kerak,
                 lekin qo'shimcha tasdiq checkboxi talab qilinmaydi
                 (oddiy review-field kabi, default tanlanmagan).
```

---

## 7. Iqtibos soni siyosati (§7)

```text
eski < yangi  → SAFE, default tanlangan (citation_count + source + checked_at=NOW())
eski > yangi  → REVIEW — "kamaydi" ogohlantirishi bilan, default TANLANMAGAN
                (provayder tuzatishi yoki indekslash o'zgarishi bo'lishi mumkin)
yangi = None  → diff YO'Q (mavjud qiymat saqlanadi)
```

---

## 8. OA / litsenziya / candidate PDF ajratilishi (§8-10)

Uchalasi ham diff ro'yxatida **faqat ma'lumot sifatida** ko'rsatiladi
(`applicable=False`, checkbox yo'q):

- `oa_status` — tashqi OA holati o'zgargani ko'rsatiladi, lekin `access_type`,
  `license_type`, `can_read_pdf()` ga HECH QACHON ta'sir qilmaydi.
- `license_candidates` — nomzod sifatida ko'rsatiladi, yakuniy litsenziya
  avtomatik almashtirilmaydi (Phase 2C.1 qoidasi bilan bir xil).
- `candidate_pdf_url` — faqat havola sifatida ko'rsatiladi. Hech qachon
  yuklab olinmaydi, `pdf_path`ga yozilmaydi, `can_read_pdf()`ga
  ta'sir qilmaydi. `services/article_sync/` paketi HTTP klient yoki
  `security_uploads` import qilmaydi — bu statik test bilan qulflangan.

---

## 9. Transaction va poyga holati

Bitta `apply` — bitta atomic tranzaksiya (route: `conn.commit()`/`rollback()`,
`services/article_import` bilan bir xil naqsh): article maydonlari, authors,
sync record status — hammasi yoki hech biri.

Post-write CAS (`mark_sync_applied`) muvaffaqiyatsiz bo'lsa (amalda deyarli
imkonsiz — bitta tranzaksiya ichida stale-check allaqachon status='checked'
ekanini tasdiqlagan), kod **baland ovozda xato beradi** (jim "success"
qaytarmaydi) — route buni ROLLBACK qiladi, yozilgan article o'zgarishlari
HAM bekor bo'ladi. Orphan/yarim holat qolmaydi.

---

## 10. Provider partial failure (§24)

Bitta provayder (masalan, Unpaywall) timeout bersa, `fetch_article_metadata`
qolgan provayderlar ma'lumotidan diff quradi (Phase 2C.1 qisman-muvaffaqiyat
siyosati); `provider_summary`da holat ko'rsatiladi va admin UI'da
ogohlantirish chiqadi. Butun sync YIQILMAYDI.

---

## 11. Xavfsizlik (§25)

- Admin auth (`_require_admin`) + global CSRF barcha yozuvchi route'da.
- `apply` faqat `sync.article_id == so'ralgan article_id` bo'lsa ishlaydi
  (boshqa maqolaning sync ID'sini almashtirib bo'lmaydi).
- Tashqi metadata Jinja avtoescape orqali chiqadi (`|safe` yo'q); JS
  `textContent` orqali quriladi, `innerHTML` ishlatilmaydi.
- API kalitlar/email hech qachon javobga chiqmaydi (Phase 2C.1 qoidasi
  meros qilib olingan — `describe_configuration()`).
- Xom provayder JSON javobi saqlanmaydi — faqat normallashtirilgan
  `diff_summary`.

---

## 12. Kelajak — scheduler kontrakti (§31, Phase 2C.3B/C)

Hozir scheduler YO'Q. Lekin `check_article_metadata(cur, article_id, actor_id, ...)`
Flask'siz, oddiy `cur` bilan ishlaydi — kelajakdagi background job AYNAN shu
funksiyani chaqiradi, parallel logika yaratilmaydi. `apply_article_metadata_changes`
esa har doim ADMIN TASDIG'INI talab qiladi (`sync_id` + `fields` + confirm
bayroqlari) — avtomatik/agentli apply Phase 2C.3A doirasida MAVJUD EMAS va
qo'shilmagan.
