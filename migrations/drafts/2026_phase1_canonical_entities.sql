-- ============================================================================
-- DRAFT — DO NOT APPLY TO PRODUCTION. Not run by any migration runner.
--
-- Phase 1 data-quality design (branch audit/data-quality-phase-1). Reviewed
-- and hand-run only, never wired into an --apply script in this phase. See
-- docs/data-quality/architecture.md §2 for the full rationale per table.
--
-- Design invariants:
--   - Purely additive: no ALTER/DROP on dissertations, olim_profiles,
--     institution_map, institution_renames, or any other existing table.
--   - Tombstone merges: canonical_* rows are never deleted, only pointed at
--     a survivor via merged_into_id.
--   - source_records is insert-only (ON CONFLICT DO NOTHING at the
--     application layer) — never UPDATEd, so it always holds the first
--     value ever captured.
--
-- Purpose: stand up the canonical researcher/institution/specialty entity
-- model + alias/audit tables that Phase 2 normalization would populate. It
-- creates NEW tables only; it does not migrate or rewrite any existing data.
--
-- Pre-check (run manually FIRST, read-only, before ever applying):
--     -- none of these names may already exist with a different shape:
--     SELECT table_name FROM information_schema.tables
--     WHERE table_schema = 'public' AND table_name IN (
--       'canonical_researchers','researcher_aliases','canonical_institutions',
--       'institution_aliases','specialties','specialty_aliases',
--       'normalization_runs','data_quality_issues','source_records',
--       'entity_merge_audit');
--     -- referenced existing tables must exist (FK targets):
--     SELECT to_regclass('public.olim_profiles'), to_regclass('public.institution_map');
--   If any target table above already exists, STOP and diff its columns
--   against this file before applying — CREATE TABLE IF NOT EXISTS will
--   silently keep a pre-existing, differently-shaped table.
--
-- Rollback strategy (manual, reverse creation order so FKs drop cleanly):
--     DROP TABLE IF EXISTS entity_merge_audit, source_records,
--       data_quality_issues, normalization_runs, specialty_aliases,
--       specialties, institution_aliases, canonical_institutions,
--       researcher_aliases, canonical_researchers CASCADE;
--   (CASCADE also removes the fk_canonical_researchers_primary_institution
--   constraint added below.)
--
-- Data-loss risk: LOW on apply — additive tables only, no writes to existing
-- data. The rollback above uses CASCADE and will delete any rows accumulated
-- in these tables, so only roll back before Phase 2 starts populating them.
--
-- Re-run caveat: every CREATE ... uses IF NOT EXISTS and is idempotent EXCEPT
-- the `ALTER TABLE canonical_researchers ADD CONSTRAINT ...` below, which has
-- no IF NOT EXISTS and errors on a second run — guard it or drop the
-- constraint first when re-applying by hand.
--
-- HUMAN REVIEW REQUIRED before this is ever promoted out of drafts/:
--   a DBA must confirm the FK targets, index names, and CHECK constraints
--   against the live production schema, and decide the researcher/institution
--   merge policy (see canonical-entity rules — no automatic destructive
--   merges; ambiguous matches go to review_status = 'needs_review').
--
-- Unknowns vs. current production schema (verify on the server, not locally):
--   - exact column set/types of olim_profiles(id) and institution_map(id)
--     as FK targets;
--   - whether any of the table names above collide with server-only tables
--     not present in this repo;
--   - collation/citext expectations for the *_normalized_key columns.
-- ============================================================================

-- ── 1. canonical_researchers ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS canonical_researchers (
    id                      SERIAL PRIMARY KEY,
    display_name            TEXT NOT NULL,
    normalized_key           TEXT NOT NULL,
    olim_profile_id          INTEGER REFERENCES olim_profiles(id) ON DELETE SET NULL,
    primary_institution_id   INTEGER,  -- FK to canonical_institutions(id), added below (table order)
    review_status            TEXT NOT NULL DEFAULT 'auto'
                              CHECK (review_status IN ('auto', 'confirmed', 'needs_review')),
    merged_into_id           INTEGER REFERENCES canonical_researchers(id) ON DELETE SET NULL,
    created_at               TIMESTAMP DEFAULT NOW(),
    updated_at               TIMESTAMP DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_canonical_researchers_profile
    ON canonical_researchers (olim_profile_id) WHERE olim_profile_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_canonical_researchers_normalized_key
    ON canonical_researchers (normalized_key);
CREATE INDEX IF NOT EXISTS idx_canonical_researchers_merged_into
    ON canonical_researchers (merged_into_id) WHERE merged_into_id IS NOT NULL;

-- ── 2. researcher_aliases ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS researcher_aliases (
    id                       SERIAL PRIMARY KEY,
    canonical_researcher_id   INTEGER NOT NULL REFERENCES canonical_researchers(id) ON DELETE CASCADE,
    raw_name                 TEXT NOT NULL,
    normalized_key            TEXT NOT NULL,
    source                   TEXT NOT NULL,
    confidence                NUMERIC(4,3) NOT NULL DEFAULT 1.0,
    created_at                TIMESTAMP DEFAULT NOW(),
    UNIQUE (raw_name, source)
);

CREATE INDEX IF NOT EXISTS idx_researcher_aliases_normalized_key
    ON researcher_aliases (normalized_key);
CREATE INDEX IF NOT EXISTS idx_researcher_aliases_canonical
    ON researcher_aliases (canonical_researcher_id);

-- ── 3. canonical_institutions ───────────────────────────────────────────────
-- Adapter over the existing, proven institution_map table — never a
-- replacement for it. Populated by a read-only sync job (not part of this
-- phase's shipped code), one row per active institution_map row.
CREATE TABLE IF NOT EXISTS canonical_institutions (
    id                    SERIAL PRIMARY KEY,
    canonical_name         TEXT NOT NULL,
    institution_map_id     INTEGER REFERENCES institution_map(id) ON DELETE SET NULL,
    category               TEXT,
    region                 TEXT,
    merged_into_id         INTEGER REFERENCES canonical_institutions(id) ON DELETE SET NULL,
    created_at             TIMESTAMP DEFAULT NOW(),
    updated_at             TIMESTAMP DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_canonical_institutions_map
    ON canonical_institutions (institution_map_id) WHERE institution_map_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_canonical_institutions_name
    ON canonical_institutions (canonical_name);

ALTER TABLE canonical_researchers
    ADD CONSTRAINT fk_canonical_researchers_primary_institution
    FOREIGN KEY (primary_institution_id) REFERENCES canonical_institutions(id) ON DELETE SET NULL;

-- ── 4. institution_aliases ──────────────────────────────────────────────────
-- Mirrors institution_map.cyrillic_name variants as generic aliases.
CREATE TABLE IF NOT EXISTS institution_aliases (
    id                        SERIAL PRIMARY KEY,
    canonical_institution_id   INTEGER NOT NULL REFERENCES canonical_institutions(id) ON DELETE CASCADE,
    raw_name                  TEXT NOT NULL UNIQUE,
    created_at                 TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_institution_aliases_canonical
    ON institution_aliases (canonical_institution_id);

-- ── 5. specialties ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS specialties (
    id                 SERIAL PRIMARY KEY,
    code               VARCHAR(10) NOT NULL UNIQUE
                        CHECK (code ~ '^\d{2}\.\d{2}\.\d{2}$'),
    name_uz            TEXT,
    name_ru            TEXT,
    branch             TEXT,
    is_active          BOOLEAN NOT NULL DEFAULT TRUE,
    superseded_by_id   INTEGER REFERENCES specialties(id) ON DELETE SET NULL,
    created_at         TIMESTAMP DEFAULT NOW(),
    updated_at         TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_specialties_code ON specialties (code);
CREATE INDEX IF NOT EXISTS idx_specialties_branch ON specialties (branch);

-- ── 6. specialty_aliases ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS specialty_aliases (
    id               SERIAL PRIMARY KEY,
    specialty_id      INTEGER NOT NULL REFERENCES specialties(id) ON DELETE CASCADE,
    raw_text         TEXT NOT NULL UNIQUE,
    extracted_code    VARCHAR(10),
    match_method      TEXT NOT NULL CHECK (match_method IN ('exact_code', 'regex_split', 'manual')),
    confidence        NUMERIC(4,3) NOT NULL DEFAULT 1.0,
    created_at        TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_specialty_aliases_specialty ON specialty_aliases (specialty_id);
CREATE INDEX IF NOT EXISTS idx_specialty_aliases_code ON specialty_aliases (extracted_code);

-- ── 7. normalization_runs ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS normalization_runs (
    id                 SERIAL PRIMARY KEY,
    run_type           TEXT NOT NULL,
    started_at         TIMESTAMP,
    finished_at        TIMESTAMP,
    records_scanned    INTEGER,
    issues_found       INTEGER,
    candidates_found   INTEGER,
    code_version       TEXT,
    notes              TEXT,
    status             TEXT NOT NULL DEFAULT 'running'
                        CHECK (status IN ('running', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_normalization_runs_type_started
    ON normalization_runs (run_type, started_at);

-- ── 8. data_quality_issues ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS data_quality_issues (
    id                      SERIAL PRIMARY KEY,
    entity_type              TEXT NOT NULL
                              CHECK (entity_type IN ('researcher', 'institution', 'specialty', 'dissertation', 'statistic')),
    source_table              TEXT,
    source_id                 BIGINT,
    raw_value                 TEXT,
    issue_type                TEXT NOT NULL,
    severity                  TEXT NOT NULL DEFAULT 'warning'
                              CHECK (severity IN ('info', 'warning', 'error')),
    detail                    JSONB,
    status                    TEXT NOT NULL DEFAULT 'open'
                              CHECK (status IN ('open', 'reviewed', 'resolved', 'ignored')),
    normalization_run_id      INTEGER REFERENCES normalization_runs(id) ON DELETE SET NULL,
    created_at                TIMESTAMP DEFAULT NOW(),
    resolved_at                TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_dqi_entity_type ON data_quality_issues (entity_type);
CREATE INDEX IF NOT EXISTS idx_dqi_status ON data_quality_issues (status);
CREATE INDEX IF NOT EXISTS idx_dqi_issue_type ON data_quality_issues (issue_type);

-- ── 9. source_records ────────────────────────────────────────────────────────
-- Insert-only. Application layer must use ON CONFLICT DO NOTHING so the
-- first captured raw value is permanent.
CREATE TABLE IF NOT EXISTS source_records (
    id                    BIGSERIAL PRIMARY KEY,
    source_table           TEXT NOT NULL,
    source_pk              BIGINT NOT NULL,
    field_name             TEXT NOT NULL,
    raw_value              TEXT,
    captured_at             TIMESTAMP DEFAULT NOW(),
    normalization_run_id    INTEGER REFERENCES normalization_runs(id) ON DELETE SET NULL,
    UNIQUE (source_table, source_pk, field_name)
);

CREATE INDEX IF NOT EXISTS idx_source_records_lookup
    ON source_records (source_table, source_pk);

-- ── 10. entity_merge_audit ───────────────────────────────────────────────────
-- Generalizes the existing institution_renames undo pattern to all entity
-- types. See blueprints/admin.py admin_institutions_undo() for the model
-- this mirrors.
CREATE TABLE IF NOT EXISTS entity_merge_audit (
    id                  SERIAL PRIMARY KEY,
    entity_type          TEXT NOT NULL CHECK (entity_type IN ('researcher', 'institution', 'specialty')),
    action               TEXT NOT NULL CHECK (action IN ('merge', 'split', 'rename', 'confirm', 'unmerge')),
    source_entity_id      INTEGER NOT NULL,
    target_entity_id      INTEGER,
    moved_aliases         JSONB,
    confidence            NUMERIC(4,3),
    performed_by          TEXT NOT NULL DEFAULT 'system',
    reason               TEXT,
    created_at            TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_entity_merge_audit_entity
    ON entity_merge_audit (entity_type, source_entity_id);
