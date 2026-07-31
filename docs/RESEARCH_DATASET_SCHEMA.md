# Research Dataset Schema

Ushbu hujjat H-index Researcher Profile va University Analytics ma'lumotlari
uchun `006_create_research_dataset_schema.sql` migrationining kontraktini
belgilaydi. Migration faqat schema yaratadi: CSV/XLSX qatorlari yuklanmaydi,
canonical merge bajarilmaydi va mavjud `/reyting` ma'lumotlari o'zgarmaydi.

## Mavjud schema bilan moslik

Auditda quyidagi mavjud primary key turlari aniqlandi:

| Jadval | Primary key | Muhim mavjud constraint/index |
|---|---|---|
| `universities` | `id INTEGER` (`SERIAL`) | `name UNIQUE`, `LOWER(name)` va trigram index |
| `olim_profiles` | `id INTEGER` (`SERIAL`) | `olim_name UNIQUE`, nullable `slug` partial-unique |
| `h_index_snapshots` | `id INTEGER` (`SERIAL`) | `snapshot_date NOT NULL` |
| `h_index_institutions` | `id INTEGER` (`SERIAL`) | snapshot FK `ON DELETE CASCADE`; `(snapshot_id, institution_name) UNIQUE` |
| `h_index_scholars` | `id INTEGER` (`SERIAL`) | snapshot FK `ON DELETE CASCADE`; `(snapshot_id, scholar_name) UNIQUE` |

Shu sabab `university_id`, `olim_profile_id`, `created_by` va `reviewed_by`
`INTEGER`; yangi `research_*` obyektlari `BIGSERIAL`, ular orasidagi FKlar
`BIGINT`. Mavjud ustunlar rename yoki delete qilinmaydi.

Migration `h_index_institutions.research_organization_id` va
`h_index_scholars.canonical_researcher_id` ustunlarini nullable FK sifatida
qo'shadi. Ranking jadvallari lazy yaratilgani uchun migration paytida jadval
bo'lmasa link skip qilinadi; migration ranking jadvallari yaratilgach qayta
qo'llansa link xavfsiz qo'shiladi.

## Jadvallar

### `research_import_batches`

Har bir validatsiya/import urinishining audit va idempotency birligi.

| Maydon | Talab |
|---|---|
| `id` | PK, avtomatik |
| `dataset_type`, `source`, `file_name`, `file_sha256` | majburiy |
| `reporting_period`, `snapshot_date`, `notes`, `created_by`, `completed_at` | nullable |
| `status` | majburiy: `pending`, `validated`, `importing`, `completed`, `failed`, `rolled_back` |
| `total_rows`, `inserted_rows`, `skipped_rows`, `error_rows` | majburiy, default `0`, manfiy emas |
| `created_at` | majburiy, avtomatik |

`file_sha256` 64 belgili SHA-256 hex bo'lishi shart. Dataset turi, manba,
davr, snapshot sanasi va case-insensitive hash kombinatsiyasi unique; shu fayl
takror yuklansa ikkinchi batch yaratilmaydi.

### `research_organizations`

Universitet, institut, akademiya va markaz uchun bitta canonical qator.

| Maydon | Talab |
|---|---|
| `id` | PK, avtomatik |
| `slug`, `name`, `normalized_name`, `source`, `is_active` | majburiy |
| `category`, `university_id`, `source_url` | nullable |
| `created_at`, `updated_at` | majburiy, avtomatik boshlang'ich qiymat |

`(source, slug)` unique. `university_id` faqat mos mavjud universitet topilsa
`universities.id`ga ulanadi. Universitet bo'lmagan tashkilotlarda u `NULL`.

### `canonical_researchers`

Bir haqiqiy odam uchun bitta canonical identity. Ilmiy profil atributlari
`users`ga ko'chirilmaydi; mavjud manba `olim_profiles` bo'lib qoladi.

| Maydon | Talab |
|---|---|
| `id` | PK, avtomatik |
| `display_name`, `normalized_name`, `canonical_status` | majburiy |
| `orcid`, `olim_profile_id` | nullable |
| `created_at`, `updated_at` | majburiy |

`canonical_status`: `unreviewed`, `auto_matched`, `reviewed`, `rejected`.
ORCID bo'sh bo'lmasa `0000-0000-0000-000X` formatida va unique bo'lishi
shart. Bitta `olim_profiles` qatori ko'pi bilan bitta canonical researcherga
bog'lanadi.

### `researcher_aliases`

Lotin, kirill va boshqa ism yozilishlari.

| Maydon | Talab |
|---|---|
| `researcher_id`, `alias`, `normalized_alias` | majburiy |
| `language`, `source` | nullable |
| `created_at` | majburiy |

`(researcher_id, normalized_alias)` unique. Alias yoki source fayldagi
`raw_name` public `display_name` sifatida avtomatik ishlatilmaydi.

### `researcher_external_profiles`

Canonical odamning Scopus, Google Scholar, WoS yoki ORCID profili.

| Maydon | Talab |
|---|---|
| `researcher_id`, `source` | majburiy |
| `source_author_id`, `internal_profile_url`, `external_profile_url`, `orcid` | nullable, lekin kamida bitta locator mavjud bo'lishi shart |
| `created_at`, `updated_at` | majburiy |

`source`: `scopus`, `scholar`, `wos`, `orcid`. Bo'sh bo'lmagan
`(source, source_author_id)` unique. ID bo'lmasa URL orqali profil saqlanadi.
Scopus va Scholar qatorlari alohida canonical odam yaratmaydi.

### `researcher_affiliations`

Canonical researcher va canonical organizationning davriy bog'lanishi.

| Maydon | Talab |
|---|---|
| `researcher_id`, `organization_id`, `source`, `reporting_period`, `is_primary` | majburiy |
| `organization_rank` | nullable; bo'lsa musbat integer |
| `created_at` | majburiy |

`(researcher_id, organization_id, source, reporting_period)` unique. Tashkilot
nomi bu jadvalda matn sifatida takrorlanmaydi.

### `researcher_metric_snapshots`

Bir external profilning bitta davr/sanadagi manba H-index holati.

| Maydon | Talab |
|---|---|
| `researcher_id`, `external_profile_id`, `source`, `hindex_status`, `reporting_period`, `snapshot_date` | majburiy |
| `import_batch_id`, `h_index`, `rank`, `source_url`, `scraped_at` | nullable |
| `created_at` | majburiy |

`source`: `scopus`, `scholar`, `wos`. `h_index` bo'lsa `>= 0`; `rank` bo'lsa
`> 0`. `hindex_status`: `reported`, `source_na`, `missing`, `invalid`.
`(external_profile_id, reporting_period, snapshot_date)` unique.

930 ta manba N/A qatori tashlab yuborilmaydi: har biri uchun
`h_index = NULL` va `hindex_status = 'source_na'` yoziladi. `missing` manbada
qiymat topilmagani, `invalid` esa kelgan qiymat parse/validatsiyadan o'tmagani
uchun ishlatiladi. Scopus va Scholar H-indexlari alohida metric qatorlari.

### `researcher_match_candidates`

Scopus va Scholar profillari bir odam bo'lishi mumkinligini review navbatida
saqlaydi.

| Maydon | Talab |
|---|---|
| `organization_id`, `scopus_profile_id`, `scholar_profile_id`, `name_similarity`, `signals`, `decision`, `review_status` | majburiy |
| `reviewed_by`, `reviewed_at` | nullable |
| `created_at` | majburiy |

`signals` JSONB; `name_similarity` 0–1. `decision` qiymatlari:
`high_confidence_candidate`, `manual_review`, `rejected`. `review_status`
default `pending`, keyin faqat inson tasdig'i bilan `approved` yoki `rejected`.
Scopus/Scholar juftligi unique. 349 ta high-confidence juftlik import vaqtida
ham avtomatik merge qilinmaydi.

## CSV → database mapping

### `research_organizations_template.csv`

| CSV | Database/transform |
|---|---|
| `slug` | `research_organizations.slug` |
| `name` | `research_organizations.name` |
| `normalized_name` | `research_organizations.normalized_name` |
| `rank` | `h_index_institutions.rank` |
| `national_value` | `h_index_institutions.national_h_index` |
| `scopus_value` | `h_index_institutions.h_scopus` |
| `wos_value` | `h_index_institutions.h_wos` |
| `scholar_value` | `h_index_institutions.h_scholar` |
| `metric_status` | import validatsiyasi; N/A bo'lsa tegishli metric ustunlari `NULL` |
| `reporting_period` | `research_import_batches.reporting_period`; snapshot sanasiga resolve qilinadi |
| `scraped_at` | provenance validatsiyasi; hozirgi legacy organization snapshot jadvalida alohida ustun yo'q |
| `source_url` | `research_organizations.source_url` |

Canonical tashkilot va uning davriy ranking qiymatlari ataylab ajratiladi.
Legacy `h_index_institutions` ustunlari rename qilinmaydi. `metric_status` va
`scraped_at` kelajak organization-metric modeli yaratilmaguncha importer
provenance/validatsiya maydoni bo'lib qoladi; bu migration ma'lumot import
qilmaydi.

### `canonical_researchers_template.csv`

| CSV | Database/transform |
|---|---|
| `canonical_researcher_id` | import correlation key → `canonical_researchers.id` lookup |
| `display_name` | `canonical_researchers.display_name` |
| `normalized_name` | `canonical_researchers.normalized_name` |
| `orcid` | `canonical_researchers.orcid` |
| `primary_org_slug` | organization lookup → `researcher_affiliations.organization_id`, `is_primary=TRUE` |
| `canonical_status` | `canonical_researchers.canonical_status` |

### `researcher_aliases_template.csv`

| CSV | Database |
|---|---|
| `canonical_researcher_id` | lookup → `researcher_aliases.researcher_id` |
| `alias` | `researcher_aliases.alias` |
| `normalized_alias` | `researcher_aliases.normalized_alias` |
| `language` | `researcher_aliases.language` |
| `source` | `researcher_aliases.source` |

### `researcher_external_profiles_template.csv`

| CSV | Database |
|---|---|
| `canonical_researcher_id` | lookup → `researcher_external_profiles.researcher_id` |
| `source` | `researcher_external_profiles.source` |
| `source_author_id` | `researcher_external_profiles.source_author_id` |
| `internal_profile_url` | `researcher_external_profiles.internal_profile_url` |
| `external_profile_url` | `researcher_external_profiles.external_profile_url` |
| `orcid` | `researcher_external_profiles.orcid` |

### `researcher_affiliations_template.csv`

| CSV | Database |
|---|---|
| `canonical_researcher_id` | lookup → `researcher_affiliations.researcher_id` |
| `org_slug` | `(source, slug)` lookup → `organization_id` |
| `source` | `researcher_affiliations.source` |
| `organization_rank` | `researcher_affiliations.organization_rank` |
| `reporting_period` | `researcher_affiliations.reporting_period` |
| `is_primary` | `researcher_affiliations.is_primary` |

### `researcher_metric_snapshots_template.csv`

| CSV | Database |
|---|---|
| `canonical_researcher_id` | lookup → `researcher_metric_snapshots.researcher_id` |
| `source`, `source_author_id` | external profile lookup → `external_profile_id`; `source` metricga ham yoziladi |
| `h_index` | `researcher_metric_snapshots.h_index` |
| `hindex_status` | `researcher_metric_snapshots.hindex_status` |
| `rank` | `researcher_metric_snapshots.rank` |
| `reporting_period` | `researcher_metric_snapshots.reporting_period` |
| `snapshot_date` | `researcher_metric_snapshots.snapshot_date` |
| `source_url` | `researcher_metric_snapshots.source_url` |
| `scraped_at` | `researcher_metric_snapshots.scraped_at` |

### `researcher_match_candidates_template.csv`

| CSV | Database/lookup |
|---|---|
| `organization_slug` | lookup → `researcher_match_candidates.organization_id` |
| `scopus_source_author_id` | `(scopus, id)` lookup → `scopus_profile_id` |
| `scholar_source_author_id` | `(scholar, id)` lookup → `scholar_profile_id` |
| `name_similarity` | `researcher_match_candidates.name_similarity` |
| `signals` | JSON parse → `researcher_match_candidates.signals` |
| `decision` | `researcher_match_candidates.decision` |
| `review_status` | `researcher_match_candidates.review_status`; import defaulti `pending` |

## ORCIDni ism maydonidan ajratish

Importer avval ORCID URL/prefiksini olib tashlab, faqat standart
`0000-0000-0000-000X` qismini ajratadi. Ajratilgan qiymat format va unique
constraintdan o'tadi; ismning qolgan qismi `display_name`, normalizatsiyalangan
ko'rinishi `normalized_name` bo'ladi. ORCID ism/alias ichida saqlanmaydi. Format
to'g'ri bo'lmasa canonical qator avtomatik bog'lanmaydi va import xatosi sifatida
qayd etiladi.

## Scopus va Scholar canonical merge

1. Har ikkala manba avval `researcher_external_profiles`ga alohida yoziladi.
2. Ism, tashkilot, ORCID va boshqa signallar candidate generatorga beriladi.
3. Natija `researcher_match_candidates`ga `review_status='pending'` bilan tushadi.
4. `high_confidence_candidate` ham canonical researcherga avtomatik merge emas.
5. Faqat tasdiqlangan reviewdan keyin ikkala external profil bitta
   `canonical_researchers.id`ga bog'lanadi; audit/import batch saqlanadi.

Individual WoS ma'lumoti hozir mavjud emas. WoS uchun bo'sh yoki sun'iy
external profile/metric qatori yaratilmaydi. Haqiqiy manba kelgandagina alohida
`source='wos'` qatori import qilinadi.

## Import tartibi

1. `research_import_batches`
2. `research_organizations`
3. `canonical_researchers`
4. `researcher_aliases`
5. `researcher_external_profiles`
6. `researcher_affiliations`
7. `researcher_metric_snapshots`
8. `researcher_match_candidates`

Har bosqich batch status/counterlarini yangilaydi. Keyingi bosqich faqat oldingi
bosqich validatsiyadan o'tganda boshlanadi. Ushbu repository bosqichida importer
yo'q va shablonlarda faqat header qatori bor.

## Migration testi va rollback

Statik test migration SQL, constraintlar va template headerlarini DBsiz
tekshiradi. DB-level test faqat `TEST_DATABASE_URL` mavjud va database nomida
`test`, `staging`, `stage` yoki `stg` bo'lsa alohida vaqtinchalik schema ichida
ishlaydi; aks holda skip. Production `DATABASE_URL` testlarda ishlatilmaydi.

Migration rollback SQL fayl boshidagi kommentda berilgan. Rollback avval
nullable ranking linklarini, keyin yangi jadvallarni dependencyga teskari
tartibda olib tashlaydi. Productionga qo'llash va rollback bu bosqich doirasiga
kirmaydi.
