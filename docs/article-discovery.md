# Manual Article Discovery — OpenAlex + Crossref (Phase 2D.1/2D.2)

Admin kalit so'z va sana oralig'i bo'yicha OpenAlex va/yoki Crossref'dan
nomzod maqolalarni qidiradi (Phase 2D.2 — provayder tanlovi va saqlangan
so'rovlar; Phase 2D.1'da faqat OpenAlex bor edi). Tizim hech narsani
avtomatik yaratmaydi — faqat mavjud
[`docs/article-bulk-import.md`](article-bulk-import.md) (Phase 2C.2) navbatiga
"yangi" (bazada yo'q, hech qanday navbatda yo'q) DOI'larni qo'shadi. Bundan
keyingi hamma narsa — metadata olish, ko'rib chiqish, tasdiqlash — ANIQ shu
mavjud pipeline orqali, o'zgarishsiz ishlaydi.

Bu **deterministic scholarly discovery pipeline** — "AI agent" emas: hech
qanday LLM, avtonom tsikl yoki reyting/tanlov mantig'i yo'q. Admin so'rov
beradi, tizim OpenAlex API'ga bitta chegaralangan so'rov yuboradi, natijani
klassifikatsiya qiladi va ko'rsatadi.

---

## 1. Nima uchun avval OpenAlex

OpenAlex — kalitsiz (yoki bepul `mailto` polite pool bilan) ochiq ilmiy
grafik, `/works` endpointi kalit so'z bo'yicha qidiruv, DOI/sana/tur
filtrlarini va bounded pagination'ni bir joyda beradi. Loyihada OpenAlex
allaqachon metadata autofill uchun ishlatiladi (`services/article_metadata/openalex.py`)
— discovery uchun HAM shu provayderdan boshlash yangi tashqi bog'liqlik
qo'shmaydi. Crossref/boshqa provayderlar Phase 2D.2+ uchun qoldirilgan.

## 2. Discovery ≠ metadata enrichment

Ikkita alohida OpenAlex mijoz bor, ATAYIN:

| | `services/article_metadata/openalex.py` | `services/article_discovery/openalex.py` |
| --- | --- | --- |
| Endpoint | `/works/doi:{doi}` — BITTA ish | `/works?search=...&filter=...` — RO'YXAT |
| Vazifa | Authoritative BOYITISH (muallif ORCID, iqtibos, retraction) | Nomzod TOPISH (preview uchun minimal maydon) |
| Natija boradimi DB'ga? | Ha — admin approve qilgach `scientific_articles`ga | YO'Q — hech qachon |
| Kim chaqiradi | `services/article_import/processor.py` (mavjud, o'zgarmagan) | `services/article_discovery/service.py` |

Discovery preview'dagi `title`/`source_name`/`topics` faqat KO'RSATISH uchun.
Enqueue qilingandan keyin, mavjud `process_import_batch()` xuddi CSV/DOI-list
import bilan bir xil yo'l bilan `fetch_article_metadata(doi)`ni chaqiradi —
bu authoritative manba, discovery preview'ni HECH QACHON bypass qilmaydi.

## 3. Oqim

```text
Admin: kalit so'z + sana oralig'i + [OAK yo'nalishi] + limit
  -> POST /admin/articles/discovery/search   (server-side validatsiya)
  -> OpenAlex /works (has_doi=true, type=article, bounded per_page<=100)
  -> DOI-only ekstraksiya (DOI'siz -> statistikada sanaladi, candidate emas)
  -> klassifikatsiya: new | existing_article | already_queued
                       | duplicate_in_result
  -> server-side preview (Flask-Caching FileSystemCache, actor-bound token)
  -> admin: "Yangi maqolalarni ... navbatiga yuborish"
  -> POST /admin/articles/discovery/enqueue  (faqat token, DOI ro'yxati EMAS)
  -> article_import_batches (source_type='discovery') + article_import_items
     (faqat `new` klassifikatsiyali DOI'lar, status='pending')
  -> [MAVJUD, o'zgarmagan] process_import_batch() -> fetch_article_metadata()
  -> [MAVJUD, o'zgarmagan] ko'rib chiqish navbati -> admin explicit approve
  -> ScientificArticle (verification_status='pending_review',
     publication_status='draft')
```

## 4. Fayl xaritasi

| Qism | Joyi |
| --- | --- |
| Query/candidate tiplar, klassifikatsiya konstantalari | `services/article_discovery/models.py` |
| Konfiguratsiya (env, limitlar, TTL) | `services/article_discovery/config.py` |
| OpenAlex ro'yxat/qidiruv adapteri | `services/article_discovery/openalex.py` |
| Validatsiya + klassifikatsiya + preview token | `services/article_discovery/service.py` |
| Import navbatiga ko'prik (enqueue) | `services/article_discovery/bridge.py` |
| Admin route'lar | `blueprints/article_discovery.py` |
| Admin UI | `templates/admin/article_discovery.html`, `article_discovery_preview.html` |
| Sxema (provenance ustunlari) | `migrations/015_article_discovery.sql` |
| Testlar | `tests/test_article_discovery_*.py` |

`services/article_discovery/` Flask'ga BOG'LIQ EMAS — `services/article_import/`
bilan bir xil naqsh: barcha funksiyalar ochiq psycopg2 kursurini oladi,
tranzaksiya chegarasi blueprint'da.

## 5. DOI-only siyosati

OpenAlex ba'zi "article" turidagi ishlar DOI'siz keladi. Discovery ularni:

- statistikaga qo'shadi (`Ko'rib chiqildi` ichida, alohida "DOI yo'q" son
  sifatida ko'rsatiladi);
- candidate'ga AYLANTIRMAYDI;
- import item YARATMAYDI.

Sabab — import navbati butunlay DOI asosida ishlaydi (`normalized_doi`
UNIQUE identifikator); DOI'siz fuzzy identity qo'shish alohida, murakkabroq
muammo va Phase 2D.1 doirasidan tashqarida.

DOI normalizatsiyasi/validatsiyasi — `utils.article_rules.normalize_doi()` /
`validate_doi()` orqali, mavjud bitta manba (import bulk bilan bir xil,
alohida regex YO'Q).

## 6. Bounded qidiruv (§33)

Bitta so'rov — bitta OpenAlex `/works` chaqiruvi, `per_page<=100`, `page=1`.
Cursor bilan minglab natijani avtomatik yig'ish YO'Q. Preview'da:

- **Manbada mos** — OpenAlex `meta.count` (jami mos, provayderda).
- **Ko'rib chiqildi** — bu so'rovda haqiqatan qaytgan/tekshirilgan son
  (`<=limit`, `<=100`).

Ikkalasi teng bo'lmasa, UI buni aniq ko'rsatadi ("manbada bundan ko'proq bor")
— "hammasi yuklandi" deb hech qachon adashtirilmaydi.

## 7. Duplicate klassifikatsiyasi

Metadata fetch qilinishidan OLDIN, ikkita batch SQL (N+1 emas):

1. **Same-response** — bitta OpenAlex javobida ikki marta uchragan DOI ->
   `duplicate_in_result` (birinchisi qoladi, ikkinchisi belgilanadi).
2. **Mavjud maqola** — `scientific_articles.doi = ANY(%s)`
   (`services/article_import/deduplicator.py:find_existing_db_duplicates`,
   MAVJUD funksiya, qayta ishlatilgan).
3. **Faol import navbati** — `article_import_items.normalized_doi = ANY(%s)
   AND status = ANY(<faol statuslar>)`
   (`find_active_import_dois`, YANGI, lekin bir xil modulda, bir xil naqsh).

"Faol" — `services/article_import/state.py:ACTIVE_DOI_ITEM_STATUSES` —
yagona haqiqat manbai (§8-§9 ga qarang).

Faqat `new` klassifikatsiyali nomzodlar enqueue qilinadi.

## 8. Cross-batch idempotency — yagona haqiqat manbai

```python
# services/article_import/state.py
ACTIVE_DOI_ITEM_STATUSES = (IN_FLIGHT_STATUSES | REVIEW_BUCKET_STATUSES
                            | RETRYABLE_ITEM_STATUSES | {METADATA_NOT_FOUND})
# = {pending, processing, ready_for_review, partial_metadata, retracted,
#    provider_failed, failed, metadata_not_found}
```

`duplicate_in_batch` / `already_exists` / `invalid_doi` ATAYIN CHIQARIB
TASHLANGAN: ular hech qachon endi maqola yaratmaydi (yoki `normalized_doi`
umuman yo'q), shuning uchun bir xil DOI bilan boshqa "faol" nomzod bilan
TO'QNASHMASLIGI shart emas. Bu MUHIM: mavjud CSV/DOI-list import'da bitta
partiya ichida original (`pending`) + `duplicate_in_batch` qatorlari BIR XIL
DOI bilan qasddan birga yashaydi — agar ular ham index'ga kirsa, mavjud
import buzilardi.

Bu ro'yxat **ikki joyda, qo'lda, bir xil** ishlatiladi:

1. `migrations/015_article_discovery.sql` — `uq_article_import_items_active_doi`
   partial UNIQUE index predicate'i.
2. `services/article_import/persistence.py:ensure_schema()` — bir xil DDL
   (lazy, idempotent).

`tests/test_article_discovery_migration.py:ActiveDoiStatusParityTests` uchala
manbani (Python konstanta, migration SQL, ensure_schema SQL) taqqoslaydi —
biri o'zgarsa, boshqalari yangilanmaguncha test qiziladi.

## 9. Poyga holati (race) himoyasi

**Enqueue vaqtida (§16):** ikki admin bir vaqtda bir xil DOI'ni navbatga
qo'ysa, Python pre-check YAKUNIY himoya emas. `insert_discovery_item()`
(`services/article_import/persistence.py`) `INSERT ... ON CONFLICT
(normalized_doi) WHERE <faol predicate> DO NOTHING RETURNING id` ishlatadi —
DB darajasida atomik. Ikkinchisi `None` oladi, `bridge.py` buni
"allaqachon navbatda" deb belgilaydi — **500 emas**.

**Preview token double-click (§24):** cache orqali "consumed" belgisi
qo'yiladi (`mark_preview_consumed`) — ikkinchi POST xuddi shu batch'ga
qaytaradi. FileSystemCache tarqatilgan tranzaksiya EMASLIGI sababli bu
best-effort — chinakam concurrency himoyasi baribir yuqoridagi DB UNIQUE
index. Amaliy natija: eng yomon holatda bo'sh ikkinchi batch (0 element)
yaratilishi mumkin, lekin HECH QACHON ikkita faol bir xil DOI elementi.

**Processing vaqtida (§17, generic import race hardening):**
`services/article_import/processor.py:process_item()` metadata providerga
chiqishdan OLDIN `scientific_articles`ni QAYTA tekshiradi (`find_article_by_doi`).
Discovery preview bilan processing orasida boshqa yo'l xuddi shu DOI bilan
maqola yaratgan bo'lsa — item bekorga provayder so'rovisiz to'g'ridan-to'g'ri
`already_exists`ga o'tadi. Bu himoya faqat discovery uchun emas — CSV/DOI-list
import uchun ham amal qiladi (`processor.py` umumiy).

## 10. Provenance (migrations/015)

| Ustun | Jadval | Ma'no |
| --- | --- | --- |
| `source_type='discovery'` | `article_import_batches` (mavjud ustun) | Bulk CSV/DOI-list'dan ajratish |
| `source_provider` | `article_import_batches` | `'openalex'` |
| `source_query` | `article_import_batches` (JSONB) | Validatsiya qilingan CANONICAL so'rov — kalit/mailto/xom URL YO'Q |
| `source_oak_field_id` | `article_import_batches` | Admin tanlagan OAK yo'nalishi — FAQAT niyat, `ON DELETE SET NULL` |
| `source_result_count` | `article_import_batches` | Provayderdagi jami mos (`meta.count`) |
| `excluded_no_doi_count` | `article_import_batches` | DOI'siz bo'lgani uchun tashlab yuborilgan son |
| `external_source_id` | `article_import_items` | OpenAlex work ID (masalan `"W123..."`) |

Hech qachon saqlanmaydi: API kaliti, `mailto`, xom OpenAlex URL/javob.

## 11. Preview token

Server-rendered POST->render UX (JavaScript YO'Q, §36). Qidiruv natijasi
Flask-Caching FileSystemCache'da (gunicorn worker'lari orasida bo'linadi)
saqlanadi:

```python
{admin_id, query (canonical), candidates, total_count, ...,
 created_at, consumed: False, batch_id: None}
```

Token — `secrets.token_urlsafe(32)`, TTL ~15 daqiqa (`config.preview_ttl_seconds`,
env: `ARTICLE_DISCOVERY_PREVIEW_TTL_SECONDS`). Enqueue POST FAQAT tokenni
qabul qiladi — DOI ro'yxati emas (§23). Token:

- current adminga tegishli EMAS bo'lsa (`admin_id` mos kelmasa) — rad etiladi;
- muddati o'tgan/topilmagan bo'lsa — xavfsiz redirect (`/admin/articles/discovery`
  ga, qidiruv sahifasiga), **404/500 emas**;
- tampered/noto'g'ri qiymat bo'lsa — xuddi shunday xavfsiz redirect.

## 12. OAK yo'nalishi — faqat niyat

Admin ixtiyoriy ravishda OAK yo'nalishini tanlashi mumkin — bu FAQAT
`source_oak_field_id` sifatida provenance'da saqlanadi (qidiruv/moderatsiya
konteksti). MVP'da:

- OAK nomidan avtomatik ingliz query generatsiya QILINMAYDI — admin
  keyword'ni o'zi kiritadi;
- approve paytida `article_scientific_fields` AVTOMATIK yaratilmaydi — admin
  mavjud import review ekranida (`primary_field_id`) o'zi tanlaydi, xuddi
  CSV/DOI-list import bilan bir xil.

## 13. Xavfsizlik

- **SSRF** — OpenAlex bazasi kod-belgilangan (`OPENALEX_WORKS_URL`), admin
  host/URL bera olmaydi.
- **XSS** — barcha tashqi matn (`title`, `source_name`, `topics`) Jinja
  autoescape orqali render qilinadi, `|safe` ishlatilmagan.
- **Secrets** — `OPENALEX_API_KEY`/`OPENALEX_MAILTO` DB/HTML/log/exception
  UI'ga hech qachon chiqmaydi (`services/article_discovery/config.py`).
- **SQL** — barcha so'rovlar parametrlangan (`%s`/`ANY(%s)`), string
  interpolation yo'q.
- **Rate limit** — `~10 qidiruv/daqiqa/admin` (env:
  `ARTICLE_DISCOVERY_SEARCHES_PER_MINUTE`), FileSystemCache orqali, alohida
  namespace (`article_discovery:rate:`) — metadata autofill keshi bilan
  aralashmaydi.
- **CSRF/Auth** — mavjud global `CSRFProtect` + `login_required` +
  `_require_admin()`, `blueprints/article_import.py` bilan bir xil naqsh.

## 14. Chegaralar (Phase 2D.1 doirasidan TASHQARIDA — 2D.2'da hal qilindi/qolganlari)

Phase 2D.1'da qat'iyan yaratilmagan bo'lib, 2D.2'da qo'shilganlar: Crossref
discovery provider (§16), saqlangan so'rovlar (§17). Hali ham YO'Q:
scheduler/cron, `ArticleDiscoveryRun` jadvali, avtonom agent, AI
klassifikator, semantik/rank qidiruv, avto-approve, avto-publish,
authoritative OAK biriktirish, PDF yuklab olish, DOI'siz maqola, Scopus/WoS/
Google Scholar/Europe PMC/DataCite scraping, publisher-maxsus discovery,
Celery/Redis, AI so'rov kengaytirish.

`services/article_sync/` va sync scheduler fayllariga tegilmagan.

## 16. Phase 2D.2 — Crossref provider va ko'p-provayderli qidiruv

### 16.1. Nega Crossref qo'shildi

OpenAlex — hosila (derived) grafik, ba'zi yangi/kichik jurnal yozuvlarini
kech ko'rsatishi mumkin. Crossref — DOI registratsiya agentligi, bibliografik
ma'lumotning BIRLAMCHI manbai (`services/article_metadata/crossref.py`
metadata boyitishda buni allaqachon ishlatadi). Discovery uchun ikkalasini
ham so'rash qamrovni kengaytiradi — bitta provayder o'tkazib yuborgan
nomzodni ikkinchisi topishi mumkin.

### 16.2. Rasmiy Crossref REST API kontrakti (2026-08 tekshirilgan)

Eski `github.com/CrossRef/rest-api-doc/rest_api.md` **DEPRECATED** deb
belgilangan — joriy hujjat `api.crossref.org/` (Swagger) va
`crossref.org/documentation/retrieve-metadata/rest-api/{,rest-api-filters}/`
+ `github.com/CrossRef/rest-api-doc/api_tips.md` (hali amaldagi, tavsiya
etilgan).

| Jihat | Rasmiy kontrakt |
| --- | --- |
| Qidiruv maydoni | `query.bibliographic` — rasmiy tavsiya: oddiy `query` butun yozuvni (jumladan bibliografik bo'lmagan maydonlarni) qidiradi, "ko'proq yolg'on-musbat" beradi; `query.bibliographic` aniqroq. |
| Sana filtri | `filter=from-pub-date:YYYY-MM-DD,until-pub-date:YYYY-MM-DD` (chiziqcha bilan — OpenAlex'ning `from_publication_date`dan farqli yozilish). |
| Tur filtri | `filter=type:journal-article` — Crossref taksonomiyasida OpenAlex'ning `type:article`iga 1:1 mos qiymat YO'Q (§16.4 chegara). |
| `rows` | Hujjatda qattiq raqamli cheklov YO'Q; loyiha siyosati 100 dan oshirmaydi (Phase 2D.1 bilan bir xil). |
| Chuqur sahifalash | `api_tips.md`: "Don't use rows/offsets... they are expensive and slow. Use cursors instead." Biz `offset` UMUMAN yubormaymiz — faqat BITTA chegaralangan sahifa (chuqur sahifalash emas). |
| `mailto` | Query parametr — "polite pool" (odobli navbat), ixtiyoriy. |
| API kaliti | SHART EMAS ("No sign-up is required to use the REST API"). |
| Javob konverti | `{"status":"ok","message-type":"work-list","message":{"total-results":N,"items":[...]}}` — har bir item `/works/{doi}` bilan bir xil sxema. |
| Chaqirib olish belgisi | `update-to[].type == "retraction"` (Retraction Watch integratsiyasi). |
| Rate limit | IP bo'yicha; 429/5xx/timeout'da eksponensial backoff tavsiya etiladi — `services/article_metadata/http.py:fetch_json` buni ALLAQACHON qiladi (qayta yozilmagan). |

### 16.3. Fayl xaritasi (yangi)

| Qism | Joyi |
| --- | --- |
| Crossref ro'yxat/qidiruv adapteri | `services/article_discovery/crossref.py` |
| Provider-neutral `DiscoverySearchResult`, `providers` provenance maydoni | `services/article_discovery/models.py` (avval `openalex.py` ichida edi) |
| Ko'p-provayderli orkestratsiya | `services/article_discovery/service.py:run_multi_provider_search` |
| Saqlangan so'rovlar persistence/servis | `services/article_discovery/saved_queries.py` |
| Saqlangan so'rovlar sxemasi | `migrations/017_article_discovery_saved_queries.sql` |

`openalex.py` **o'zgarmagan xulq-atvor** — faqat `DiscoverySearchResult`
importi `models.py`ga ko'chirildi va har bir candidate'ga
`providers=['openalex']` qo'shildi (provenance uchun, mavjud maydonlarga
tegmasdan).

### 16.4. Ma'lum cheklov — Crossref tur filtri

Crossref'da OpenAlex'ning `type:article` bilan aniq 1:1 mos keladigan
qiymat YO'Q (Crossref taksonomiyasi boshqacha: `journal-article`,
`proceedings-article`, `posted-content`, ...). Biz `type:journal-article`ni
tanladik — eng yaqin ekvivalent, lekin bu ATAYIN preprint/proceedings kabi
turlarni chiqarib tashlaydi. Bu chegara soxta moslikni yashirmaydi —
ochiq hujjatlashtirilgan (kelajakda kengaytirish mumkin, hozircha skop
tashqarisida).

### 16.5. Ko'p-provayderli dedup va provenance (§7-§8, §16 ticket)

`run_multi_provider_search()` (bitta provayder tanlansa `run_discovery_search()`ga
ANIQ delegatsiya qiladi — Phase 2D.1 xulq-atvori/testlari o'zgarishsiz):

1. Tanlangan provayderlar **PARALLEL** so'raladi (`ThreadPoolExecutor`,
   `services/article_metadata/service.py` bilan bir xil naqsh), umumiy vaqt
   byudjeti bilan (`config.total_budget`, standart 20s).
2. Natijalar **DETERMINISTIK** tartibda birlashtiriladi — `SUPPORTED_PROVIDERS`
   tartibi (OpenAlex, keyin Crossref), concurrent HTTP tugash tartibidan
   MUSTAQIL.
3. Bir xil DOI ikkala provayderda ham topilsa: FAQAT birinchi uchrashuv
   "canonical" (keyinroq NEW/EXISTING/... klassifikatsiya qilinadi);
   ikkinchisi bitta-provayderli same-response dublikat kabi
   `duplicate_in_result` bo'ladi (final "yangi" hisobda IKKI marta
   SANALMAYDI). Farqi: `providers` ro'yxati (masalan `["openalex",
   "crossref"]`) birinchisiga QO'SHILADI — provenance yo'qolmaydi.
4. Ko'rsatish maydonlari (title/source_name/publication_date/
   external_source_id/topics) — DETERMINISTIK ustunlik: OpenAlex maydoni
   bo'lsa saqlanadi, bo'sh bo'lsa Crossref'dan to'ldiriladi.
   `is_retracted` — OR birlashtiriladi (ikkalasidan biri "true" desa, true).
   Bu **to'liq metadata-merger EMAS** (`services/article_metadata/merger.py`
   bilan chalkashtirilmasin) — faqat preview uchun kerakli oz sonli maydon.
5. Bitta provayder muvaffaqiyatsiz bo'lsa, ikkinchisining natijasi
   YO'QOLMAYDI — `DiscoveryPreviewResult.provider_status` har bir
   provayderning holati/sonini alohida saqlaydi
   (`{'openalex': {'status': 'ok', 'count': 12}, 'crossref': {'status':
   'timeout', 'count': 0}}`). Hammasi muvaffaqiyatsiz bo'lsa —
   `status != 'ok'`, candidate YO'Q, admin uchun aniq xato.
6. `article_import_batches.source_provider` — bitta provayder tanlansa
   uning nomi, ikkitasi tanlansa `'multi'`
   (`ArticleDiscoveryQuery.source_provider_label`).

### 16.6. DOI-only siyosat — Crossref uchun qat'iyroq

Crossref natijalari OpenAlex'dan farqli, kuratsiya darajasi pastroq bo'lishi
mumkin — shuning uchun `crossref.py` oddiy `normalize_doi()` emas, to'liq
`utils.article_rules.validate_doi()` ishlatadi (import bulk'dagi bir xil
qoida — `services/article_import/validator.py`). Bo'sh VA formati
noto'g'ri DOI ikkalasi ham bir xil "DOI yo'q" hisobiga qo'shiladi (§19 UI
spetsifikatsiyasi bitta "DOI yo'q" sonini so'raydi, ikkinchi ajratilgan
bucket qo'shilmadi).

## 17. Phase 2D.2 — Saqlangan qidiruv so'rovlari

### 17.1. Semantika: "eslab qol", "avtomatik ishga tushirmaydi"

`article_discovery_saved_queries` (migrations/017) — admin qidiruv formasini
(kalit so'z, sana oralig'i, provayder(lar), OAK yo'nalishi, limit) nomlab
saqlaydi. Bu FAQAT konfiguratsiyani eslab qolish — hech qanday fon
jarayoni/cron/scheduler YO'Q (Phase 2D.3 uchun ATAYIN qoldirilgan). Ro'yxatni
ko'rish (`GET /admin/articles/discovery`) tashqi API'ga UMUMAN chiqmaydi;
faqat admin aniq "Run" bosganda (`POST .../saved-queries/<id>/run`)
`run_multi_provider_search()` chaqiriladi — xuddi oddiy qidiruvdek.

Saqlangan sana oralig'i **AYNAN saqlangan holicha** qayta ishlatiladi ("last
30 days" kabi nisbiy oyna EMAS) — admin yangi oyna bilan qidirishni
xohlasa, "Tahrirlash" orqali sanalarni yangilaydi.

### 17.2. Sxema va FK konvensiyasi (audit natijasi)

```sql
article_discovery_saved_queries (
  id, name, created_by, keyword, from_date, to_date,
  oak_field_id, providers JSONB, result_limit,
  is_active, created_at, updated_at, last_used_at
)
```

- `created_by` — oddiy `INTEGER`, `users(id)`ga FK **YO'Q**. Audit:
  loyihada ikkala konvensiya ham mavjud (masalan `migrations/004` FK
  qo'yadi), lekin BEVOSITA shu import/discovery pipeline oilasi
  (`article_import_batches.created_by`, `migrations/011`) izchil ravishda
  FK qo'shmaydi — shu YAQIN, lokal konvensiya davom ettirildi.
- `oak_field_id` — `article_import_batches.source_oak_field_id` bilan AYNAN
  bir xil: `REFERENCES scientific_fields(id) ON DELETE SET NULL`.
- `is_active` — soft deactivate, hard `DELETE` YO'Q (loyiha konvensiyasi —
  `scientific_fields.is_active` bilan bir xil "holat ustuni" yondashuvi).
  Deactivate qilingan qator UI ro'yxatidan yashiriladi (audit uchun DB'da
  qoladi), qayta faollashtirish tugmasi UI'da YO'Q.

### 17.3. Egalik siyosati

`created_by` FAQAT audit/provenance — kirish nazorati EMAS. Loyihaning
boshqa admin-panel resurslari (`article_import_batches`, data-quality
navbati) HAM bitta `is_admin` bayrog'i bilan barcha adminlarga ochiq, per-
admin devor yo'q; saqlangan so'rovlar shu konvensiyani davom ettiradi —
istalgan admin istalgan saqlangan so'rovni ko'radi/ishga tushiradi/
tahrirlaydi/faolsizlantiradi.

### 17.4. Validatsiya — ikkinchi validator YO'Q

Saqlangan so'rov yaratish/yangilash `discovery_service.validate_query()`ni
QAYTA ishlatadi (keyword/sana/provider enum/limit/OAK — bir xil qoidalar,
ikki joyda emas). Faqat nom (`saved_queries.validate_name`, 1-120 belgi)
alohida tekshiriladi.

### 17.5. Routes

```
GET  /admin/articles/discovery                          — forma + saqlangan ro'yxat
POST /admin/articles/discovery/saved-queries              — joriy formani saqlash
POST /admin/articles/discovery/saved-queries/<id>/run     — ishga tushirish (tashqi so'rov!)
GET  /admin/articles/discovery/saved-queries/<id>/edit    — tahrirlash formasi
POST /admin/articles/discovery/saved-queries/<id>/update  — yangilash
POST /admin/articles/discovery/saved-queries/<id>/deactivate — soft o'chirish
```

Barchasi admin-only (`_require_admin`), yozuvchi POST'lar CSRF talab qiladi
(global `CSRFProtect`). Faqat `/run` rate-limitga tushadi (haqiqiy tashqi
so'rov) — save/update/deactivate/list oddiy DB operatsiyasi, rate limit
shart emas.

## 18. Kelajak — Phase 2D.3/2D.4 (tayyorgarlik)

- **2D.3** — rejalashtirilgan (scheduled) discovery: saqlangan so'rovlarni
  cron bo'yicha AVTOMATIK ishga tushirish. Bu Phase 2D.2'da ATAYIN
  YO'Q (ehtiyotkorlik bilan qilinishi kerak: "AI agent" chegarasiga
  yaqinlashadi, aniq skop/tezlik-cheklov/monitoring talab qiladi).
- **2D.4** — semantik/rank qidiruv, OAK yo'nalishidan avtomatik query
  generatsiya (hozircha ATAYIN yo'q, §21).

Bog'liq hujjatlar: [`article-bulk-import.md`](article-bulk-import.md) (import
navbati), [`article-metadata-integration.md`](article-metadata-integration.md)
(metadata boyitish), [`articles-module.md`](articles-module.md) (umumiy
maqolalar moduli).
