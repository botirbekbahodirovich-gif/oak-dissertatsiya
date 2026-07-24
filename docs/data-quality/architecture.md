# Data quality & normalization architecture — Phase 1 audit + design

Origin: Phase 1 data-quality audit + design (branch `audit/data-quality-phase-1`),
now integrated onto the security/onboarding release base.
Scope: read-only audit + additive design. No production writes, no migrations
applied, no auto-merges, nothing in `app.py` touched.

**Status legend** — every design item below is one of:

- **IMPLEMENTED** — code shipped on this branch (see §3, §5 "Ships").
- **PROPOSED** — schema/design agreed in principle, not built.
- **DRAFT — DO NOT APPLY** — SQL exists only under `migrations/drafts/`,
  never wired into any apply path (§2).
- **HUMAN REVIEW REQUIRED** — needs a person to sign off before it runs
  (all merges/candidate matches; §4.2).
- **DEFERRED** — explicitly out of scope for this phase (§5 "Designed,
  not built"). This document never claims more is built than actually is.

## 1. Audit summary

### 1.1 Entity model today

| Entity | Table(s) | Natural key today | Canonical ID? |
|---|---|---|---|
| Dissertation | `dissertations` | `oak_id` (from OAK page URL) | **Yes** — `UNIQUE(oak_id)`, idempotent `ON CONFLICT (oak_id) DO UPDATE` in `data.py:import_oak`. The one entity that already does this right. |
| Scholar (olim) | `olim_profiles` (attributes) + free-text `olim_name` columns on `olim_maqolalar`, `olim_konferensiyalar`, `olim_ish_faoliyati`, `olim_rasmlar`, `olim_shogirdlar`, `scholar_follows`, and `dissertations.olim` / `dissertations.ilmiy_rahbar` | `LOWER(TRIM(olim_name))` — a **string**, enforced only via a functional index, never an FK | **No.** Two different people with the same rendered name collide into one `olim_profiles` row; the same person spelled two ways (Cyrillic/Latin, typo, missing patronymic) becomes two unrelated rows nowhere linked to each other. |
| Institution | `universities` (curated directory) + `institution_map` (raw-variant → canonical bridge) + `institution_renames` (admin undo log) | `institution_map.cyrillic_name` (unique) → `canonical_name` | **Partial.** This is the most mature part of the codebase — see §1.3. But `dissertations.muassasa` itself is never repointed to any ID; the map is a side lookup, not a foreign key. |
| Specialty (ixtisoslik) | *(none)* — bare `TEXT`/`VARCHAR` columns on `dissertations.ixtisoslik`, `olim_profiles.ixtisoslik`, `diss_projects.specialty_code`, plus an unrelated `journal_specialties(journal_id, specialty_code, specialty_name)` scoped only to journals | raw code string, sometimes bare code, sometimes code embedded in a longer topic string | **No.** No master code→name table exists anywhere for the full `NN.NN.NN` code space (only a 13-entry top-level "class" dict, `app.py` `SPECIALTY_NAMES`, keyed by the 2-digit branch, e.g. `05.00.00`). |

Dissertation ↔ scholar and dissertation ↔ institution joins are **string
joins**, computed at query time (`LOWER(TRIM(...))`, `pg_trgm` `similarity()`,
keyword `LIKE`), never persisted as a foreign key. This is the structural root
of most of the reported problems.

### 1.2 Import/parse pipeline (traced end to end)

```
oak.uz announcement page
  → scraper.py  parse_announcement()      regex field extraction
  → scraper.py  _extract_olim-equivalent  name-from-title regex, 3-tier fallback
  → scraper.py  ixtisoslik_shifrlari       re.findall ALL specialty codes (kept)
  → scraper.py db_save_one() / new_items.json → send_to_site.py
  → data.py    import_oak()              X-API-KEY, per-item extract + validate
       data.py _extract_olim()           name regex (duplicated from scraper.py)
       data.py _extract_mavzu()          strips trailing "... NN.NN.NN" from topic
       data.py _extract_ixtisoslik()     keeps only the FIRST code (others dropped!)
       data.py _validate_ingest_record() only rejects the grossest failure ('нинг' in olim)
  → INSERT ... ON CONFLICT (oak_id) DO UPDATE
  → dissertations (Postgres)
```

Two known, currently-live parsing weaknesses that directly cause the reported
"broken names" and "shifr matched to wrong text" symptoms:

- `data.py:_extract_ixtisoslik` collapses a dissertation's specialty codes to
  the *first* match only (`re.search`, not `findall`), even though
  `scraper.py` already extracts the full list — multi-code interdisciplinary
  defenses silently lose all but one code between scrape and import.
- The name-extraction regex chain's last-resort fallback,
  `r'^(.+?)нинг'`, matches the *first* occurrence of "нинг" anywhere in the
  title — a name containing that grammatical sequence, or an oddly-worded
  degree phrase, mis-splits the name. `_validate_ingest_record` only catches
  the case where the cleaner visibly failed (leftover `'нинг'` in the
  extracted name), not partial corruption, bracket/HTML residue, or
  punctuation-only strings.

### 1.3 Existing normalization/dedup logic (institutions — reusable, do not duplicate)

`institutions.py` is a pure, dependency-free module already solving most of
what a "canonical entity" system needs, for institutions specifically:

- `transliterate` / `transliterate_display` — Cyrillic↔Latin, case-preserving variant for display.
- `clean_name()` — strips parenthetical remarks, keeps first part of
  comma/`va`-joined multi-institution strings (only when *both* sides look
  like real institution names).
- `norm_key()` — aggressive grouping key: mixed-script repair (Latin letters
  inside majority-Cyrillic strings), ё→е, й→и, apostrophe/punctuation
  stripping, drops `бажарилган`, strips trailing `нинг$`/`да$` suffixes.
- `_similar_norms()` — **token-wise** `SequenceMatcher` (not whole-string),
  same word count required, every differing word pair ≥0.8 similar — chosen
  specifically so "техника университети" never merges with "транспорт
  университети" while "уневерситети"↔"университети" still does.
- `build_canonical()` — union-find over `norm_key` groups, fuzzy-merged via a
  4-char-prefix-bucketed pairwise pass (perf gate), canonical form chosen
  deterministically (non-suffixed > highest count > shortest > alphabetical).

`migrate_institutions.py` builds `institution_map` from this **non-destructively**
(`dissertations.muassasa` is never rewritten). Two merge paths exist downstream:

- `scripts/apply_institution_merges.py` (+ the live `/admin/institutions/rename`
  endpoint) — **reversible**: only touches `institution_map.canonical_name` and
  writes `institution_renames.moved_variants` (JSONB) as an undo log. This is
  the pattern our new `entity_merge_audit` table generalizes.
- `scripts/apply_merges.py` — **destructive**: direct `UPDATE dissertations SET
  muassasa = ...` (text mode) or `DELETE FROM universities` (fk mode), gated
  behind a required `dissertations_backup_*` table.
- `scripts/merge_universities.py` — offline, AI-assisted (`claude-fable-5`)
  three-tier bucketing (AUTO_MERGE / MANUAL_REVIEW / KEEP_SEPARATE), driven by
  the explicit rule *"a false merge is 10x worse than a false split — any
  doubt goes to MANUAL_REVIEW"*. `manual_review_groups` carry a
  model-supplied `confidence` (0–1) + free-text reason. This is the closest
  existing analogue to the confidence-scored review queue this phase designs
  for researchers and specialties too.

**Consequence for this design:** institutions already have a working,
reversible dedup mechanism. The new schema (§2) does **not** reinvent it — it
adds a thin adapter (`canonical_institutions`/`institution_aliases`, synced
*from* `institution_map`) so institutions participate in the same generic
model as researchers/specialties, without forking the proven admin workflow.

### 1.4 Statistics divergence — root-caused

At least **8 independent computation sites** for "olimlar soni"/"muassasalar
soni", pulling from **3 structurally different sources**:

| Site | What it counts | Method | Source |
|---|---|---|---|
| `app.py get_homepage_stats()` | olim, muassasa | `COUNT(DISTINCT NULLIF(TRIM(col),''))` | `dissertations` raw strings |
| `analytics.py stats_json()` | olim, muassasa | same shape, separate cache | `dissertations` raw strings |
| `blueprints/olimlar_catalog.py` | scholars = **advisors** (`ilmiy_rahbar`, not `olim`!) | `GROUP BY TRIM(ilmiy_rahbar)`, `data_quality='complete'` filter | `dissertations` + `institution_map` join |
| `blueprints/content.py` | olimlar, rahbarlar | `COUNT(DISTINCT col)`, **no TRIM** | `dissertations` raw (untrimmed) |
| `blueprints/univer.py` | active_advisors | `COUNT(DISTINCT TRIM(ilmiy_rahbar))`, **last 4 years only** | `dissertations`, time-windowed |
| `app.py get_university_dissertation_stats()` | olimlar per uni | `COUNT(DISTINCT olim)`, no TRIM, keyword `LIKE` match | `dissertations` + curated `universities` |
| `app.py`/`content.py` `/universities` | muassasalar (`len(items)`) | union of `institution_map` rows + curated `universities`, fuzzy-merged | two tables merged in Python |
| `blueprints/ranking.py` | scholars/institutions | `COUNT(*)` on manually-uploaded snapshot tables | **entirely disconnected** dataset |

Root causes: (a) "olim" sometimes means dissertation author, sometimes
advisor; (b) `TRIM`/`NULLIF` applied inconsistently before `DISTINCT`; (c)
institution counts alternate between raw `muassasa` strings, canonical
`institution_map` names, and the curated `universities` table merged by
imperfect keyword matching; (d) `data_quality`/time-window filters applied
inconsistently; (e) the ranking widget reads a wholly separate snapshot.

**This is exactly what a canonical-ID layer fixes**: once `dissertations` rows
resolve to `canonical_researcher_id`/`canonical_institution_id`, every one of
these sites can run the *same* query (`COUNT(DISTINCT canonical_id)`,
optionally windowed) against the *same* resolved column — no more per-page
reinvention. Wiring that in is out of scope for this phase (it touches
`app.py`, forbidden without explicit sign-off) but is the direct payoff of §2–3.

### 1.5 Ranking, clustering, gender — confirmed/refuted per report

**H-index ranking** (`blueprints/ranking.py` — an admin-uploaded external
snapshot, not a computed h-index; `blueprints/reyting.py` — the legacy
internal scorer):
- Shogird/dissertation mislabeling: **not present in any live route.**
  Found only in `templates/home_legacy.html`, a dead template not
  wired to any route — real evidence the bug pattern existed, but not
  currently user-facing.
- No name-validity filter before ranking: **confirmed**
  (`ranking.py` only checks for emptiness — no bracket/HTML/
  punctuation-only rejection anywhere in the file).
- Duplicate scholars ranked separately: **confirmed** — `scholar_name` is
  `UNIQUE(snapshot_id, scholar_name)` (exact-string dedup only,
  `dedup = name.lower()`); the existing fuzzy `_match_olim()` only attaches a
  profile-link badge, it never merges rows or ranks.

**Topic clustering** (`app.py` `_clustering_build()` — a keyword
co-occurrence heuristic, not TF-IDF/AI; `blueprints/topic_analysis.py` is an
unrelated single-topic similarity tool):
- Generic Russian words polluting clusters: **confirmed** — `app.py`
  `STOP_WORDS` (~35 entries) is Uzbek-only (Latin+Cyrillic), zero Russian
  stems, zero English. `blueprints/topic_analysis.py`'s separate `_STOP_WORDS`
  (~40 entries) is also Uzbek-only. Neither list is combined nor multilingual.
- Only ~20% of records clustered: **confirmed as a structural consequence**
  of the algorithm's own thresholds, not a bug — keywords need ≥15
  supporting documents to count as "significant", only the top-200 keyword
  co-occurrence pairs are examined, each cluster needs ≥8 members, hard cap
  of 30 clusters, and a document assigned to one cluster is excluded from all
  later ones. `unclustered` is already surfaced as a first-class metric.

**Gender statistics** (`app.py detect_gender()`):
- Heuristic: patronymic suffix (`-овна/-евна/.../-қизи` → female,
  `-ович/-евич/.../-ўғли` → male) → surname suffix (`-ова/-ева/... ` →
  female, `-ов/-ев/...` len>3 → male) → hardcoded 26-name Cyrillic-only
  first-name list → `'unknown'`.
- No confidence score anywhere (`grep` for "confidence"/"ishonch": zero hits).
- No per-record "taxminiy" disclaimer: **confirmed** —
  `olim_profile.html`/`cabinet.html` render a bare icon with
  no tooltip/label for the uncertainty.
- "No unknown category": **partially confirmed** — the aggregate `/stats`
  page *does* track and display an "❓ Aniqlanmagan" bucket
  (`app.py compute_gender_stats()`, `stats.html`); it's the
  per-record display and per-record confidence that are missing.

### 1.6 A third, previously-unlisted bug class: region detection (feeds issue #4)

Three **independent** region-detection implementations exist, none shared:

1. `migrate_institutions.py detect_region()` — 13-region keyword table,
   returns `None` on no match (comment: *"so we never mislabel a non-Tashkent
   institution as Tashkent"*).
2. `app.py detect_uni_city_region()` — a near-duplicate keyword table used
   only when seeding `universities.city/region`, but **defaults to
   `return 'Toshkent', 'Toshkent'`** when nothing matches (line 834).
3. `app.py detect_region()` (heatmap) — a third, differently-structured
   keyword dict (`UZ_REGIONS`), **also defaults to `'Toshkent'`** when nothing
   matches.

**None of the three keyword tables contains "denov" or any Denov-area
keyword.** Urganch and Andijon *are* covered by keyword lists in all three, so
those mislabelings likely come from institution names that don't literally
contain the city word (e.g. a branch/filial named after its parent
institution). Denov, however, is unconditionally misrouted to Toshkent by
implementations #2 and #3 every time, via their silent fallback default — this
is the precise, reproducible root cause of complaint #4.

## 2. Proposed schema — canonical entities, aliases, audit trail

**Status: PROPOSED · SQL is DRAFT — DO NOT APPLY**
(`migrations/drafts/2026_phase1_canonical_entities.sql`; never auto-applied).

Design principles (mirroring what already works in `institution_map`/
`institution_renames`):
- **Additive, not replacing.** Existing tables (`dissertations`,
  `olim_profiles`, `institution_map`) are never altered or dropped by this
  phase. New tables sit alongside and are populated by read-only sync jobs.
- **Tombstone merges, never deletes.** A "merged" row gets
  `merged_into_id` pointed at the survivor; it is never removed. Undo = clear
  the pointer + replay `entity_merge_audit` in reverse.
- **Original values are immutable.** `source_records` captures the
  first-ever-seen raw value per (table, row, field) with `ON CONFLICT DO
  NOTHING` — it is never updated, only inserted once.
- **No table auto-decides identity.** Every canonical row starts
  `review_status = 'auto'` (system-proposed) and only becomes authoritative
  once an admin explicitly confirms it, exactly like the existing
  AUTO_MERGE/MANUAL_REVIEW/KEEP_SEPARATE three-tier pattern in
  `merge_universities.py`.

### 2.1 `canonical_researchers`
**Purpose:** one row per real-world scholar; the eventual join target for
`dissertations.olim`/`ilmiy_rahbar`, once a later phase wires it in.

| Column | Type | Notes |
|---|---|---|
| `id` | `SERIAL PRIMARY KEY` | |
| `display_name` | `TEXT NOT NULL` | best/most-common raw form |
| `normalized_key` | `TEXT NOT NULL` | output of `normalize_person_name()`, §3 |
| `olim_profile_id` | `INTEGER REFERENCES olim_profiles(id) ON DELETE SET NULL` | link to existing profile once matched; nullable |
| `primary_institution_id` | `INTEGER REFERENCES canonical_institutions(id) ON DELETE SET NULL` | most-common institution, for disambiguation |
| `review_status` | `TEXT NOT NULL DEFAULT 'auto' CHECK (review_status IN ('auto','confirmed','needs_review'))` | |
| `merged_into_id` | `INTEGER REFERENCES canonical_researchers(id) ON DELETE SET NULL` | tombstone pointer; NULL = live row |
| `created_at`, `updated_at` | `TIMESTAMP DEFAULT NOW()` | |

Constraints/indexes: `UNIQUE(olim_profile_id)` (partial, `WHERE
olim_profile_id IS NOT NULL` — one profile maps to at most one canonical
researcher); `CREATE INDEX idx_canonical_researchers_normalized_key ON
canonical_researchers (normalized_key)`. **No** uniqueness on
`normalized_key` alone — common names legitimately collide, disambiguation
needs institution/specialty/year context (§4).
Audit/rollback: never deleted; `merged_into_id` + `entity_merge_audit` (§2.10).

### 2.2 `researcher_aliases`
**Purpose:** every raw name-string variant seen anywhere, mapped to a
canonical researcher, with provenance.

| Column | Type | Notes |
|---|---|---|
| `id` | `SERIAL PRIMARY KEY` | |
| `canonical_researcher_id` | `INTEGER NOT NULL REFERENCES canonical_researchers(id) ON DELETE CASCADE` | |
| `raw_name` | `TEXT NOT NULL` | |
| `normalized_key` | `TEXT NOT NULL` | |
| `source` | `TEXT NOT NULL` | e.g. `'dissertations.olim'`, `'dissertations.ilmiy_rahbar'`, `'ranking_upload'`, `'olim_profiles'` |
| `confidence` | `NUMERIC(4,3) NOT NULL DEFAULT 1.0` | 1.0 for exact/self-reported, lower for fuzzy-matched aliases |
| `created_at` | `TIMESTAMP DEFAULT NOW()` | |

Constraints/indexes: `UNIQUE(raw_name, source)`; `idx_researcher_aliases_normalized_key
ON researcher_aliases (normalized_key)` (candidate lookup);
`idx_researcher_aliases_canonical ON researcher_aliases (canonical_researcher_id)`.
Rollback unit: re-pointing `canonical_researcher_id` (never delete raw_name/source),
logged in `entity_merge_audit`.

### 2.3 `canonical_institutions`
**Purpose:** thin adapter so institutions share the generic model, **backed
by** (not replacing) `institution_map`.

| Column | Type | Notes |
|---|---|---|
| `id` | `SERIAL PRIMARY KEY` | |
| `canonical_name` | `TEXT NOT NULL` | mirrors `institution_map.canonical_name` |
| `institution_map_id` | `INTEGER REFERENCES institution_map(id) ON DELETE SET NULL` | back-link to the existing, admin-editable table |
| `category` | `TEXT` | |
| `region` | `TEXT` | |
| `merged_into_id` | `INTEGER REFERENCES canonical_institutions(id) ON DELETE SET NULL` | |
| `created_at`, `updated_at` | `TIMESTAMP DEFAULT NOW()` | |

Constraints/indexes: `UNIQUE(institution_map_id)` (partial, `WHERE
institution_map_id IS NOT NULL`); `idx_canonical_institutions_name`.
Sync job (read-only against `dissertations`, write-only to this table):
one `canonical_institutions` row per active `institution_map` row.
Merges/renames continue to happen through the **existing**
`/admin/institutions/rename` + `institution_renames` undo flow — this table
just mirrors the result, it does not gain its own merge UI in this phase.

### 2.4 `institution_aliases`
**Purpose:** mirrors `institution_map.cyrillic_name` variants as generic aliases.

| Column | Type | Notes |
|---|---|---|
| `id` | `SERIAL PRIMARY KEY` | |
| `canonical_institution_id` | `INTEGER NOT NULL REFERENCES canonical_institutions(id) ON DELETE CASCADE` | |
| `raw_name` | `TEXT NOT NULL` | = `institution_map.cyrillic_name` |
| `created_at` | `TIMESTAMP DEFAULT NOW()` | |

Constraints/indexes: `UNIQUE(raw_name)`; `idx_institution_aliases_canonical`.

### 2.5 `specialties`
**Purpose:** the master table ixtisoslik has never had — one row per real
`NN.NN.NN` code with its correct name, plus old→new code succession.

| Column | Type | Notes |
|---|---|---|
| `id` | `SERIAL PRIMARY KEY` | |
| `code` | `VARCHAR(10) NOT NULL` | `CHECK (code ~ '^\d{2}\.\d{2}\.\d{2}$')` |
| `name_uz` | `TEXT` | |
| `name_ru` | `TEXT` | |
| `branch` | `TEXT` | top-level 2-digit class, e.g. `05.00.00` — seedable from existing `app.py` `SPECIALTY_NAMES` |
| `is_active` | `BOOLEAN NOT NULL DEFAULT TRUE` | |
| `superseded_by_id` | `INTEGER REFERENCES specialties(id) ON DELETE SET NULL` | old code → new code, addresses "eski va yangi shifrlar" |
| `created_at`, `updated_at` | `TIMESTAMP DEFAULT NOW()` | |

Constraints/indexes: `UNIQUE(code)`; `idx_specialties_code`.

### 2.6 `specialty_aliases`
**Purpose:** raw ixtisoslik text (which may be a bare code, a code embedded
in a longer topic string, an old-scheme code, or a code/name mismatch) mapped
to a `specialties` row.

| Column | Type | Notes |
|---|---|---|
| `id` | `SERIAL PRIMARY KEY` | |
| `specialty_id` | `INTEGER NOT NULL REFERENCES specialties(id) ON DELETE CASCADE` | |
| `raw_text` | `TEXT NOT NULL` | |
| `extracted_code` | `VARCHAR(10)` | |
| `match_method` | `TEXT NOT NULL` | `'exact_code' \| 'regex_split' \| 'manual'` |
| `confidence` | `NUMERIC(4,3) NOT NULL DEFAULT 1.0` | |
| `created_at` | `TIMESTAMP DEFAULT NOW()` | |

Constraints/indexes: `UNIQUE(raw_text)`; `idx_specialty_aliases_specialty`,
`idx_specialty_aliases_code`.

### 2.7 `data_quality_issues`
**Purpose:** generic, append-only quarantine ledger — one row per detected
problem instance across *any* entity type. This is what the dry-run script
(§5) and, later, a review UI would read from.

| Column | Type | Notes |
|---|---|---|
| `id` | `SERIAL PRIMARY KEY` | |
| `entity_type` | `TEXT NOT NULL CHECK (entity_type IN ('researcher','institution','specialty','dissertation','statistic'))` | |
| `source_table` | `TEXT` | e.g. `'dissertations'` |
| `source_id` | `BIGINT` | e.g. `dissertations.id`; no hard FK (spans tables) |
| `raw_value` | `TEXT` | |
| `issue_type` | `TEXT NOT NULL` | `'broken_name' \| 'html_residue' \| 'duplicate_candidate' \| 'location_mismatch' \| 'code_name_mismatch' \| 'low_confidence_gender' \| ...` |
| `severity` | `TEXT NOT NULL DEFAULT 'warning' CHECK (severity IN ('info','warning','error'))` | |
| `detail` | `JSONB` | structured evidence (candidate pair, scores, matched rule) |
| `status` | `TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','reviewed','resolved','ignored'))` | human review queue state |
| `normalization_run_id` | `INTEGER REFERENCES normalization_runs(id) ON DELETE SET NULL` | |
| `created_at` | `TIMESTAMP DEFAULT NOW()` | |
| `resolved_at` | `TIMESTAMP` | |

Indexes: `idx_dqi_entity_type`, `idx_dqi_status`, `idx_dqi_issue_type`.
Purely additive bookkeeping — never mutates source data.

### 2.8 `normalization_runs`
**Purpose:** provenance for every execution of the dry-run pipeline; lets
every alias/issue/candidate be traced back to *when* and with *what code
version* it was produced, and lets a bad run's output be scoped-deleted.

| Column | Type | Notes |
|---|---|---|
| `id` | `SERIAL PRIMARY KEY` | |
| `run_type` | `TEXT NOT NULL` | `'dry_run_audit' \| 'alias_sync' \| 'duplicate_scan'` |
| `started_at`, `finished_at` | `TIMESTAMP` | |
| `records_scanned`, `issues_found`, `candidates_found` | `INTEGER` | |
| `code_version` | `TEXT` | git short SHA |
| `notes` | `TEXT` | |
| `status` | `TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','completed','failed'))` | |

Index: `idx_normalization_runs_type_started`.

### 2.9 `source_records`
**Purpose:** guarantees "original source qiymati saqlansin" independent of
whatever happens to `dissertations`/`olim_profiles` later — an immutable,
first-seen-wins snapshot of every raw field value.

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL PRIMARY KEY` | |
| `source_table` | `TEXT NOT NULL` | |
| `source_pk` | `BIGINT NOT NULL` | |
| `field_name` | `TEXT NOT NULL` | |
| `raw_value` | `TEXT` | |
| `captured_at` | `TIMESTAMP DEFAULT NOW()` | |
| `normalization_run_id` | `INTEGER REFERENCES normalization_runs(id) ON DELETE SET NULL` | |

Constraints/indexes: `UNIQUE(source_table, source_pk, field_name)` — insert
with `ON CONFLICT DO NOTHING`, **never** `UPDATE`, so the first captured value
is permanent; `idx_source_records_lookup (source_table, source_pk)`.

### 2.10 `entity_merge_audit`
**Purpose:** generalizes the proven `institution_renames` undo pattern to
all three entity types, giving `canonical_researchers`/`specialties` the same
rollback guarantee institutions already have.

| Column | Type | Notes |
|---|---|---|
| `id` | `SERIAL PRIMARY KEY` | |
| `entity_type` | `TEXT NOT NULL CHECK (entity_type IN ('researcher','institution','specialty'))` | |
| `action` | `TEXT NOT NULL CHECK (action IN ('merge','split','rename','confirm','unmerge'))` | |
| `source_entity_id` | `INTEGER NOT NULL` | |
| `target_entity_id` | `INTEGER` | |
| `moved_aliases` | `JSONB` | mirrors `institution_renames.moved_variants` |
| `confidence` | `NUMERIC(4,3)` | |
| `performed_by` | `TEXT NOT NULL DEFAULT 'system'` | |
| `reason` | `TEXT` | |
| `created_at` | `TIMESTAMP DEFAULT NOW()` | |

Index: `idx_entity_merge_audit_entity (entity_type, source_entity_id)`.
**Undo mechanism** (mirrors `admin_institutions_undo()` in
`blueprints/admin.py`): read `moved_aliases` from a `'merge'` row, repoint
those aliases back to `source_entity_id`, clear `merged_into_id` on the
source entity, insert an `'unmerge'` row referencing the original.

## 3. Normalization functions — **Status: IMPLEMENTED** (`utils/data_quality/`)

Pure, dependency-free, unit-tested — no DB, no Flask. Institution
normalization is **imported from the existing `institutions.py`**, not
reimplemented.

- `names.py` — `normalize_person_name()` (order-tolerant token-sorted key +
  patronymic/suffix stripping, mirrors `institutions.norm_key`'s apostrophe/
  ё-й handling), `is_broken_name()` (bracket/HTML-residue/punctuation-only/
  placeholder detection — generalizes the existing
  `_validate_ingest_record`'s single `'нинг' in olim` check).
- `specialties.py` — `extract_specialty_code()`, `split_specialty_text()`
  (topic vs. code, handles both orderings), `all_specialty_codes()` (restores
  the multi-code extraction `data.py` currently drops), `validate_code_format()`.
- `stopwords.py` — combined Uzbek+Russian+English stop-word set (supersets
  both existing incomplete lists; adds Russian morphological stems like
  "ишлаб"/"технологиясини" the current `app.py` `STOP_WORDS` misses).
- `gender.py` — `detect_gender_scored()`: same rule tiers as `app.py`'s
  `detect_gender()` plus a numeric confidence (patronymic match = 0.97,
  surname suffix = 0.75, hardcoded first-name list = 0.6, no match = 0.0/
  `'unknown'`) and an explicit `is_estimated: True` flag for template use.
- `location.py` — `detect_region_scored()`: merges all three existing
  region-keyword tables (§1.6) into one, **never silently defaults to
  Toshkent** (returns `None`/low-confidence instead), and adds the missing
  "denov"→Surxondaryo keyword plus a `flag_location_mismatch()` check for
  branch/filial names whose parent-name keyword and filial-city keyword
  disagree.
- `confidence.py` — `score_candidate_pair()`: weighted rule score for
  duplicate-candidate pairs (see §4).

## 4. Data quality pipeline

```
Raw data (dissertations.olim / .ilmiy_rahbar / .muassasa / .ixtisoslik,
          olim_profiles, ranking uploads)
   │
   ▼
1. VALIDATION            is_broken_name(), validate_code_format() →
                          data_quality_issues (issue_type='broken_name' etc.)
   │
   ▼
2. NORMALIZATION          normalize_person_name() / institutions.norm_key() /
                          extract_specialty_code() → normalized_key
   │
   ▼
3. ALIAS MATCHING         look up normalized_key in researcher_aliases /
                          institution_aliases / specialty_aliases
                          → found: attach to existing canonical_*.id
                          → not found: candidate for step 4
   │
   ▼
4. CANDIDATE DUPLICATE    group unmatched aliases by normalized_key;
   DETECTION              fuzzy-compare via token-wise SequenceMatcher
                          (institutions._similar_norms pattern) across groups
   │
   ▼
5. CONFIDENCE SCORE       score_candidate_pair() — weighted:
                            name similarity      × 0.50
                            shared institution    × 0.20
                            shared specialty      × 0.15
                            year proximity (≤5y)  × 0.15
                          → score in [0, 1]
   │
   ▼
6. HUMAN REVIEW QUEUE     ALL candidates land in data_quality_issues
                          (issue_type='duplicate_candidate', status='open') —
                          score only changes the suggested action shown to
                          the reviewer, it never triggers a write.
   │
   ▼
7. CANONICAL ID ASSIGN.   ⛔ NOT BUILT IN THIS PHASE (explicitly out of scope
                          — "duplicate'larni avtomatik merge qilmang"). When
                          built, it must mirror apply_institution_merges.py:
                          write entity_merge_audit, set merged_into_id,
                          repoint aliases — a human runs it, nothing here does.
   │
   ▼
8. ANALYTICS REFRESH      Future: statistics sites (§1.4) read
                          COUNT(DISTINCT canonical_id) instead of ad-hoc
                          COUNT(DISTINCT TRIM(name)) — the actual fix for the
                          8-way stats divergence. Requires app.py changes,
                          out of scope for this phase.
```

### 4.1 Confidence-score methodology

`score_candidate_pair(a, b)` combines four independent signals into `[0, 1]`:

| Signal | Weight | 1.0 when | 0.0 when |
|---|---|---|---|
| Name similarity | 0.50 | `normalize_person_name(a) == normalize_person_name(b)` | token-wise `SequenceMatcher` ratio < 0.6 on any token pair |
| Shared institution | 0.20 | both resolve to the same `canonical_institution_id` | different institutions, both known |
| Shared specialty | 0.15 | same `specialty_id` (or same `branch`) | different branch |
| Year proximity | 0.15 | defense/advisor years within 5 years, or unknown (neutral) | > 15 years apart |

Bands (informational only — **no band auto-merges**):
- **`≥ 0.90`** — "high": pre-filled as a suggested merge for a human to
  confirm in the review queue.
- **`0.60–0.89`** — "medium": surfaced, no suggestion pre-filled, reviewer
  must actively decide.
- **`< 0.60`** — "low": logged for visibility only (`data_quality_issues`,
  `severity='info'`), never presented as a mergeable candidate.

This directly implements the requirement *"past confidence duplicate
avtomatik birlashtirilmasin"*: the "high" band still requires an explicit,
separate apply step (not built in this phase) run by a human — exactly the
same two-step discipline `merge_universities.py`'s AUTO_MERGE tier already
uses (`apply_merges.py` must be invoked separately, with its own dry-run/
backup gate).

### 4.2 Human review process — **Status: HUMAN REVIEW REQUIRED · UI DEFERRED**

1. Dry-run script (`scripts/data_quality_audit.py`) populates
   `data_quality_issues` with `status='open'`.
2. An admin (future: a `templates/admin_data_quality.html` review screen,
   out of scope here) filters by `issue_type`/`severity`, inspects `detail`
   JSON (raw values + score breakdown), and sets `status` to
   `'reviewed'`/`'resolved'`/`'ignored'`.
3. Confirming a `duplicate_candidate` issue is the only path that would
   eventually call the (not-yet-built) merge-apply script — always a
   deliberate, logged, single-pair action, never a batch auto-merge.
4. Every applied merge writes `entity_merge_audit` before touching
   `merged_into_id`, so any decision can be reversed by an admin later,
   identical in spirit to `admin_institutions_undo()`.

## 5. What ships in this phase vs. what's designed only

**Ships (code, this branch):**
- `utils/data_quality/` — pure normalization/scoring helpers (§3)
- `tests/test_data_quality.py` — unit tests (§6)
- `scripts/data_quality_audit.py` — read-only dry-run report (writes a JSON/
  text quarantine report to disk; does **not** write to any database)
- `migrations/drafts/2026_phase1_canonical_entities.sql` — draft `CREATE
  TABLE` statements for §2, clearly marked **NOT APPLIED**

**Designed, not built (needs explicit follow-up sign-off):**
- Running the migration against any real database
- Alias-sync jobs that populate `canonical_institutions`/`researcher_aliases`
  from live data
- The merge-apply script for researchers/specialties
- Any change to `app.py`/`data.py`/live templates (stop-word list,
  region-detection consolidation, gender-confidence display, statistics
  unification) — all designed above, all require touching the monolith,
  which this phase's constraints explicitly forbid.
