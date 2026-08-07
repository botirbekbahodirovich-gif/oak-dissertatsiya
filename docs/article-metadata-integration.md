# Ilmiy metadata integratsiyasi — DOI autofill (Phase 2C.1)

Admin maqola formasiga DOI kiritib, Crossref / OpenAlex / Unpaywall'dan
bibliografik ma'lumot oladi. Modulning qolgan qismi:
[`docs/articles-module.md`](articles-module.md).

**Bir jumlada:** tashqi metadata hech qachon avtomatik haqiqat emas — u
NOMZOD. Uni saqlash qarorini faqat odam qabul qiladi.

---

## 1. Nima qurilgan

| Qism | Joyi |
| --- | --- |
| Provayder klientlari | `services/article_metadata/{crossref,openalex,unpaywall}.py` |
| HTTP siyosati (timeout, retry, 429) | `services/article_metadata/http.py` |
| Tozalash va o'girish | `services/article_metadata/normalizer.py` |
| Ustuvorlik, konflikt, muallif moslashtirish | `services/article_metadata/merger.py` |
| Orkestratsiya, kesh, holat | `services/article_metadata/service.py` |
| Tiplar | `services/article_metadata/models.py` |
| Konfiguratsiya | `services/article_metadata/config.py` |
| Admin endpointi | `blueprints/articles.py:admin_article_metadata_lookup` |
| Admin UI | `templates/admin/article_form.html` + `static/js/maqolalar_metadata.js` |
| Sxema | `migrations/009_article_external_metadata.sql` |
| Developer smoke | `scripts/smoke_article_metadata.py` |

Ommaviy API — bitta funksiya:

```python
from services.article_metadata import fetch_article_metadata
result = fetch_article_metadata('10.1234/abcd')
```

`services/article_metadata/` paketi Flask'ni ham, DB'ni ham import qilmaydi
(test bilan qulflangan). Shu sababli keyingi CSV/DOI bulk importeri va agent
uni o'zgartirmasdan qayta ishlata oladi (§9).

---

## 2. Provayderlar

| Provayder | Nima uchun | E-mail/kalit |
| --- | --- | --- |
| **Crossref** | Bibliografiyaning BIRLAMCHI manbai: sarlavha, jurnal, sana, jild/son/betlar, mualliflar tartibi, annotatsiya, litsenziya havolalari | `mailto` ixtiyoriy ("polite pool" — tezroq navbat) |
| **OpenAlex** | Identifikator va kontekst: OpenAlex ID'lari, muallif ORCID/ID, institutlar (ROR), mavzular, iqtibos soni, chaqirib olish bayrog'i | kalit ixtiyoriy; kalitsiz `mailto` bilan ishlaydi |
| **Unpaywall** | Ochiq kirish holatining BIRLAMCHI manbai: `is_oa`, `oa_status`, eng yaxshi ochiq nusxa, litsenziya nomzodi | e-mail **majburiy** (e-mailsiz API 422 beradi) |

`GET https://api.crossref.org/works/{doi}` ·
`GET https://api.openalex.org/works/doi:{doi}` ·
`GET https://api.unpaywall.org/v2/{doi}?email=…`

User-Agent: `Olimlar.uz/2C.1 (+https://olimlar.uz; mailto:<e-mail>)`.

### OpenAlex kaliti haqida qaror

Vazifa spetsifikatsiyasi OpenAlex uchun API kalitini talab qilgan edi, lekin
loyihada OpenAlex allaqachon **kalitsiz** ishlaydi (`blueprints/maqolalar.py`
va `.env.example`: "API kalit KERAK EMAS"). Kalitni majburiy qilish ishlab
turgan funksiyani buzardi, shuning uchun:

1. `OPENALEX_API_KEY` bo'lsa — ishlatiladi (yuqori limit);
2. bo'lmasa, kontakt e-mail bilan polite pool;
3. ikkalasi ham bo'lmasa — `not_configured`, Crossref + Unpaywall davom etadi.

---

## 3. Konfiguratsiya

```text
SCHOLARLY_API_EMAIL=info@olimlar.uz    # Crossref + Unpaywall uchun umumiy
CROSSREF_MAILTO=                        # ixtiyoriy, ustidan yozadi
UNPAYWALL_EMAIL=                        # ixtiyoriy, ustidan yozadi
OPENALEX_MAILTO=info@olimlar.uz         # allaqachon mavjud edi
OPENALEX_API_KEY=                       # ixtiyoriy

METADATA_CONNECT_TIMEOUT=4              # soniya
METADATA_READ_TIMEOUT=12
METADATA_MAX_ATTEMPTS=2
METADATA_TOTAL_BUDGET=20
METADATA_CACHE_TTL=43200                # 12 soat
```

Nomlash: `SCHOLARLY_API_EMAIL` — umumiy kontakt, chunki Crossref va Unpaywall
bir xil narsani (murojaat qiluvchining e-maili) so'raydi va ularni ikki
joyda takrorlash xatoga olib keladi. Provayderga xos qiymat kerak bo'lsa
alohida o'zgaruvchi ustidan yozadi.

Kalitlar va e-mail **hech qachon** brauzerga, JSON javobga yoki log'ga
chiqmaydi. Admin UI faqat "sozlangan / sozlanmagan" holatini ko'radi.

---

## 4. HTTP siyosati

| | |
| --- | --- |
| Ulanish timeouti | 4 s (uzoq kutish — server yotgan degani) |
| O'qish timeouti | 12 s |
| Urinishlar | 2 (jami), eksponensial backoff + jitter |
| Qayta uriniladi | 429, 5xx, ulanish/o'qish timeouti |
| Qayta urinilmaydi | 404 (yakuniy javob), 400/401/403 (so'rov yoki kalit xato) |
| `Retry-After` | 5 soniyagacha hurmat qilinadi; undan uzun bo'lsa kutilmaydi, `rate_limited` qaytadi |
| Umumiy byudjet | 20 s (uch provayder PARALLEL, `ThreadPoolExecutor`) |

Parallel chaqiruv sabab: ketma-ket bo'lsa admin uch timeoutni QO'SHIB kutardi.

Provayder holatlari: `ok`, `not_found`, `timeout`, `rate_limited`,
`unavailable`, `invalid_response`, `not_configured`.

Umumiy holat: `ok` (barcha sozlangan provayder ishladi) · `partial_success`
(kamida bittasi) · `not_found` (hech biri bilmaydi) · `failed`.

**Provayderlar mustaqil.** Bittasi yiqilsa qolganlari ishlashda davom etadi va
natija `partial_success` bo'ladi.

---

## 5. Manba ustuvorligi

`services/article_metadata/merger.py:PRECEDENCE` — yagona manba. Jadvalda
bo'lmagan maydon umuman merge qilinmaydi.

| Maydon | Tartib |
| --- | --- |
| `title`, `journal_name`, `publisher` | Crossref → OpenAlex → Unpaywall |
| `abstract` | **faqat Crossref** |
| `publication_year`, `publication_date` | Crossref → OpenAlex → Unpaywall |
| `volume`, `issue`, `pages`, `language`, `work_type`, `primary_issn` | Crossref → OpenAlex |
| `crossref_member_id` | Crossref |
| `openalex_id`, `is_retracted` | OpenAlex |
| `citation_count` | OpenAlex (+ manba va sana) |
| `is_oa`, `oa_status` | Unpaywall → OpenAlex |
| `journal_is_oa`, `journal_is_in_doaj` | Unpaywall |
| `license_candidates` | Crossref **va** Unpaywall (ikkalasi ham nomzod) |
| `topics`, `keywords` | OpenAlex |
| `external_locations` | Unpaywall + OpenAlex |

**Annotatsiya faqat Crossref'dan.** OpenAlex uni faqat
`abstract_inverted_index` ko'rinishida beradi; uni qayta tiklash nashriyot
matnining hosila nusxasini yaratish bo'lardi va bunga huquqimiz borligi
tekshirilmagan. Shuning uchun u ATAYIN olinmaydi (ogohlantirish beriladi).

`metadata.source_summary` — qaysi maydon qaysi provayderdan kelgani:
`{"title": "crossref", "oa_status": "unpaywall", "citation_count": "openalex"}`.
Bu `scientific_articles.metadata_source_summary` (JSONB) ga saqlanadi.

---

## 6. Konfliktlar

Ikki manba bir maydonga har xil qiymat bersa: yuqoriroq manba **tanlanadi**,
farq esa **ko'rsatiladi** (`FieldConflict`).

```json
{"field": "publication_year", "label": "Nashr yili",
 "preferred": 2024, "preferred_source": "crossref",
 "alternatives": [{"source": "openalex", "value": 2023}]}
```

UI'da bu **qizil xato emas** — "tekshirish tavsiya etiladi" ogohlantirishi.

Solishtirish matnli maydonlarda registr va bo'shliqqa sezgir emas:
"Journal Of A" va "Journal of A" farq hisoblanmaydi.

Konflikt kuzatiladigan maydonlar: `title`, `journal_name`,
`publication_year`, `publication_date`, `volume`, `issue`, `pages`,
`oa_status`, `license_type`.

`publisher` ATAYIN ro'yxatda yo'q: u bizning jadvalda saqlanmaydi va
manbalar uni deyarli har doim boshqacha yozadi ("Public Library of Science"
va "Public Library of Science (PLoS)") — bunday shovqin haqiqiy farqlarni
ko'mib yuborardi.

---

## 7. Mualliflar

Moslashtirish FAQAT ikki asosda:

1. **ORCID aynan teng** — eng ishonchli;
2. **normallashtirilgan to'liq ism aynan teng** (aksent, tinish belgisi va
   registr e'tiborga olinmaydi).

Fuzzy o'xshashlik ishlatilmaydi: "J. Smith" va "John Smith"
**birlashtirilmaydi**. Bir xil ism ikki marta uchrasa yoki ORCID'lar
qarama-qarshi bo'lsa ham moslashtirilmaydi.

**Moslanmagan OpenAlex mualliflari ro'yxatga QO'SHILMAYDI.** Haqiqiy
maqolalarda tekshirilgan sabab: OpenAlex o'rta ismni tashlab ketadi
("Heather A. Piwowar" → "Heather Piwowar"), natijada 8 muallifli maqola 15
qatorga aylanib qolardi. Ular `metadata.unmatched_authors` da alohida
qaytadi va panelda ko'rsatiladi — ma'lumot yo'qolmaydi, lekin bibliografik
ro'yxat Crossref'niki bo'lib qoladi.

Crossref umuman muallif bermasa, OpenAlex ro'yxati asosiy bo'ladi.

Boyitish hech qachon Crossref qiymatini **qayta yozmaydi** — faqat bo'sh
maydonni to'ldiradi (ORCID, OpenAlex ID, institut, ROR).

---

## 8. Ochiq kirish ≠ kirish turi

Unpaywall `oa_status` (`gold`, `green`, `hybrid`, `bronze`, `closed`) —
**tashqi signal**. Saytdagi `access_type` (`open_access`,
`author_permission`, `repository_copy`, …) — **huquqiy** tushuncha:
u "biz bu faylni serverda saqlab, ko'rsata olamizmi" degan savolga javob
beradi.

Ular 1:1 map QILINMAYDI. Masalan `green` — repozitoriydagi qabul qilingan
qo'lyozma bo'lishi mumkin, va uni bizning serverda saqlash huquqi
avtomatik kelib chiqmaydi. Har bir lookupda bu ogohlantirish beriladi va
`access_type` maydoni autofill bilan **hech qachon** to'ldirilmaydi.

### Litsenziya

Crossref va Unpaywall ikkalasi ham **nomzod** beradi. Ular zid bo'lsa
(`cc_by` va `cc_by_nc`) — `license_conflict` ogohlantirishi, avtomatik
tanlov YO'Q.

Tanib bo'lmagan litsenziya ruxsat beruvchi kalitga aylantirilmaydi. CC BY-ND
va CC BY-NC-ND `utils.article_rules.LICENSES` da yo'q — ular `None` bo'lib
qoladi va admin qo'lda hal qiladi. Aks holda qayta tarqatishga ruxsat
bermaydigan maqola "CC BY" bo'lib qolardi.

Litsenziya **guruh tugmasidan chiqarilgan**: "Bo'sh maydonlarni to'ldirish"
unga tegmaydi, u har doim alohida "Qo'llash" talab qiladi.

---

## 9. PDF nomzodi

Unpaywall `best_oa_location.pdf_url` berishi mumkin. U:

* `metadata.external_locations` ichida `pdf_url` sifatida qaytadi;
* admin panelida havola sifatida ko'rsatiladi (`noopener noreferrer nofollow`);
* har lookupda `pdf_candidate_only` ogohlantirishi beriladi.

**Nima QILINMAYDI:** fayl yuklab olinmaydi, `pdf_path` ga yozilmaydi,
`can_read_pdf()` ga ta'sir qilmaydi, maqola avtomatik `verified` bo'lmaydi.

Bu keyingi "Legal OA import" bosqichi uchun data kontrakti.

---

## 10. Chaqirib olish va iqtibos soni

**Retraction.** OpenAlex `is_retracted=true` bersa: ogohlantirish ko'rsatiladi,
`is_retracted` bayrog'i (yangi ustun) formada ko'rinadigan checkbox sifatida
turadi va autofill uni faqat **yoqadi**, hech qachon o'chirmaydi. Maqola
avtomatik nashr etilmaydi — nashr holatini admin belgilaydi. Crossref
`update-to` da "retraction" bo'lsa ham alohida ogohlantirish beriladi.

**Iqtibos soni** doimiy bibliografik fakt EMAS. Shuning uchun u har doim
uch maydon bilan birga yuradi va manbasiz/sanasiz saqlanmaydi:

```text
citation_count            11
citation_count_source     openalex
citation_count_checked_at 2026-08-07 21:14
```

Backend buni majburlaydi: manba yoki sana bo'lmasa uchalasi ham `NULL`
bo'ladi.

---

## 11. OAK fan yo'nalishi

OpenAlex mavzulari va kalit so'zlari olinadi va panelda **ko'rsatiladi**,
lekin OAK yo'nalishiga avtomatik aylantirilmaydi. Formadagi yo'nalish
selektori o'z holicha qoladi; JS uning `primary_field_id` maydoniga umuman
tegmaydi (test bilan qulflangan). Klassifikator — Phase 2D.

---

## 12. Admin oqimi

```text
Admin DOI kiritadi
      ↓  [Ma'lumotlarni olish]
POST /admin/articles/metadata/lookup      (admin auth + CSRF + rate limit)
      ↓
uch provayder parallel  →  normalizatsiya  →  merge  →  konfliktlar
      ↓
JSON  →  panel: provayder holati, "N ta maydon topildi", jadval
      ↓
admin tekshiradi va qo'llaydi (bo'sh maydonlar / alohida almashtirish)
      ↓
oddiy forma submit  →  DB
```

**Lookup endpointi DB'ga hech nima yozmaydi.** U faqat bitta SELECT qiladi —
shu DOI/OpenAlex ID bilan maqola allaqachon bor-yo'qligini tekshirish uchun.
Tashqi metadata admin "Saqlash" bosmaguncha faqat formada turadi.

### Mavjud ma'lumot ustuvorligi

* **bo'sh** maydon — "Bo'sh maydonlarni to'ldirish" bilan to'ldiriladi;
* **to'lgan** maydon — jadvalda yonma-yon ko'rsatiladi va faqat alohida
  "Almashtirish" bosilganda o'zgaradi;
* mualliflar ro'yxati bo'sh bo'lmasa — tasdiqlash so'raladi;
* `is_retracted` faqat yoqiladi;
* `<select>` qiymati bizning variantlarimizda bo'lmasa — taxmin qilinmaydi
  (noma'lum til kodi "other" ga aylantirilmaydi), qiymat ko'rsatiladi va
  admin o'zi tanlaydi.

### Rate limit

Bitta admin uchun daqiqasiga 20 lookup (`blueprints/articles.py:
METADATA_RATE_LIMIT`), mavjud Flask-Caching hisoblagichi ustida —
`app.py:api_protection` bilan bir xil naqsh. Yangi infra qo'shilmagan.

---

## 13. Kesh

Provayder natijalari `flask_cache/` (Flask-Caching **FileSystemCache**,
`app.py`) ga yoziladi, kalit `article_metadata:v1:<provider>:<doi-hash>`,
TTL 12 soat. Faqat MUVAFFAQIYATLI natija keshlanadi.

FileSystemCache tanlangani muhim: u gunicorn worker'lari orasida fayl tizimi
orqali bo'linadi, ya'ni jarayon ichidagi lug'atdan farqli o'laroq multi-worker
muhitda ishonchli. Redis kabi yangi infra qo'shilmagan.

Keshga faqat **kesilgan** provayder ma'lumoti tushadi (klientlar kerakli
maydonlarni ajratib oladi), butun xom javob emas.

**Xom API javobi `scientific_articles` ga hech qachon yozilmaydi.**

---

## 14. Sxema

`migrations/009_article_external_metadata.sql` (va `_ensure_schema` da
aynan shu ustunlar):

```text
scientific_articles:
  openalex_id, crossref_member_id, primary_issn,
  is_retracted, citation_count, citation_count_source,
  citation_count_checked_at, metadata_last_synced_at,
  metadata_source_summary (JSONB)

article_authors:
  openalex_author_id
```

Institutlar alohida modelga majburan normalizatsiya qilinmadi —
`affiliation` matni va OpenAlex institut ID'lari hozircha yetarli.
PMID/PMCID/arXiv uchun ham yangi ustun qo'shilmadi: hozir ular ishlatilmaydi.

`metadata_last_synced_at` ni forma emas, server qo'yadi (manba xaritasi
mavjud bo'lganda) — `rights_confirmed_at` bilan bir xil naqsh.

---

## 15. Xavfsizlik

* Endpoint: admin auth + CSRF (global `CSRFProtect`, JS `X-CSRFToken`
  yuboradi) + rate limit.
* Javobda **xom provayder ma'lumoti yo'q** — faqat normallashtirilgan
  ko'rinish va provayder holati.
* Kalit/e-mail javobga ham, log'ga ham, brauzerga ham chiqmaydi. Log'da URL
  query'siz yoziladi.
* Kutilmagan xato adminga stacktrace sifatida ko'rsatilmaydi (502 + qisqa
  matn); tafsilot server log'ida `request_id` bilan.
* Tashqi matn ishonchsiz deb qaraladi: HTML/JATS tozalanadi (`bleach`,
  `<script>`/`<style>` mazmuni bilan birga olib tashlanadi), uzunlik
  cheklanadi. Panel DOM'ni qo'lda quradi va matnni faqat `textContent`
  orqali qo'yadi — HTML satrini elementga tiqadigan API ishlatilmaydi.
  Shablonda `|safe` faqat `csrf_token()` uchun.
* Yashirin identifikator maydonlari server tomonda qayta tekshiriladi
  (`_normalize_external_metadata`): buzilgan qiymat saqlanmaydi, lekin
  maqolani saqlashni ham to'xtatmaydi. Istisno — OpenAlex ID to'qnashuvi:
  u dublikat degani va admin buni bilishi shart.

---

## 16. Testlar

| Fayl | Nima |
| --- | --- |
| `tests/test_article_metadata_providers.py` | uch klient + HTTP siyosati (soxta sessiya) |
| `tests/test_article_metadata_merge.py` | normalizator, ustuvorlik, konflikt, muallif, orkestratsiya |
| `tests/test_article_metadata_admin.py` | endpoint, DB'ga yozmaslik, javob xavfsizligi, forma UI, sxema |
| `tests/fake_scholarly_api.py` | fixture'lar + soxta HTTP sessiyasi |

Unit testlar **tashqi tarmoqqa chiqmaydi**. Haqiqiy API bilan tekshiruv —
alohida developer skripti:

```bash
SCHOLARLY_API_EMAIL=siz@example.uz python3.12 scripts/smoke_article_metadata.py
```

U hech qanday javobni repozitoriyga saqlamaydi va kalit/e-mail chop etmaydi.

---

## 17. Keyingi bosqichlar

### Phase 2C.2 — bulk import

`fetch_article_metadata(doi)` allaqachon UI'dan mustaqil. CSV/DOI importer
qilishi kerak bo'lgan narsa:

```python
for doi in doi_list:
    result = fetch_article_metadata(doi, config=config,
                                    cache_get=..., cache_set=...)
    # result.status, result.metadata, result.conflicts -> ko'rib chiqish navbati
```

Import **avtomatik nashr qilmasligi** kerak: `publication_status='draft'`,
`verification_status='pending_review'`, va konflikt/retraction bo'lgan
yozuvlar admin ko'rigiga tushishi shart.

### Legal OA import

`external_locations[].pdf_url` + `license_candidates` + `oa_status` allaqachon
saqlanadi. Yuklab olishdan oldin kerak bo'ladigan narsa — `can_download_pdf()`
kontrakti (`docs/articles-module.md` §13.9) va host bo'yicha oq ro'yxat.

### Agent

Agent shu service'ni chaqiradi va DB'ga o'zi yozmaydi — u ham admin
ko'rigiga navbat qo'yadi. Kirish nazorati klientga ko'chmasligi kerak.
