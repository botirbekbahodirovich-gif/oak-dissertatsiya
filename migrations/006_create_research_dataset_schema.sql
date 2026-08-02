-- ============================================================================
-- Migration 006 — normalized research dataset foundation
-- ============================================================================
-- Additive schema for canonical organizations/researchers, source profiles,
-- affiliations, metric history and human-reviewed Scopus/Scholar matching.
-- No source data is imported by this migration.
--
-- Safety:
--   * one PostgreSQL transaction;
--   * CREATE TABLE/INDEX and ranking-link columns are idempotent;
--   * no existing column is renamed or removed;
--   * optional ranking links are added only when the lazy ranking tables exist.
--
-- Existing-key compatibility established before authoring:
--   universities.id   INTEGER (SERIAL)
--   olim_profiles.id  INTEGER (SERIAL)
--   users.id          INTEGER (SERIAL)
--   h_index_*.id      INTEGER (SERIAL)
-- New canonical entities use BIGSERIAL; references to existing entities remain
-- INTEGER, while references between the new entities use BIGINT.
--
-- Manual rollback (only after confirming no importer/profile depends on it):
--   BEGIN;
--   ALTER TABLE IF EXISTS h_index_scholars
--       DROP COLUMN IF EXISTS canonical_researcher_id;
--   ALTER TABLE IF EXISTS h_index_institutions
--       DROP COLUMN IF EXISTS research_organization_id;
--   DROP TABLE IF EXISTS researcher_match_candidates;
--   DROP TABLE IF EXISTS researcher_metric_snapshots;
--   DROP TABLE IF EXISTS researcher_affiliations;
--   DROP TABLE IF EXISTS researcher_external_profiles;
--   DROP TABLE IF EXISTS researcher_aliases;
--   DROP TABLE IF EXISTS canonical_researchers;
--   DROP TABLE IF EXISTS research_organizations;
--   DROP TABLE IF EXISTS research_import_batches;
--   COMMIT;
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS research_import_batches (
    id               BIGSERIAL PRIMARY KEY,
    dataset_type     VARCHAR(64)  NOT NULL,
    source           VARCHAR(64)  NOT NULL,
    reporting_period VARCHAR(64),
    snapshot_date    DATE,
    file_name        VARCHAR(512) NOT NULL,
    file_sha256      CHAR(64)     NOT NULL,
    status           VARCHAR(16)  NOT NULL DEFAULT 'pending'
                         CHECK (status IN (
                             'pending', 'validated', 'importing', 'completed',
                             'failed', 'rolled_back'
                         )),
    total_rows       INTEGER      NOT NULL DEFAULT 0 CHECK (total_rows >= 0),
    inserted_rows    INTEGER      NOT NULL DEFAULT 0 CHECK (inserted_rows >= 0),
    skipped_rows     INTEGER      NOT NULL DEFAULT 0 CHECK (skipped_rows >= 0),
    error_rows       INTEGER      NOT NULL DEFAULT 0 CHECK (error_rows >= 0),
    notes            TEXT,
    created_by       INTEGER      REFERENCES users (id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at     TIMESTAMPTZ,
    CONSTRAINT ck_research_batch_sha256
        CHECK (file_sha256 ~ '^[0-9A-Fa-f]{64}$')
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_research_batch_file_dataset
    ON research_import_batches (
        dataset_type,
        source,
        COALESCE(reporting_period, ''),
        COALESCE(snapshot_date, DATE '0001-01-01'),
        LOWER(file_sha256)
    );
CREATE INDEX IF NOT EXISTS idx_research_batches_status
    ON research_import_batches (status, created_at DESC);

CREATE TABLE IF NOT EXISTS research_organizations (
    id              BIGSERIAL    PRIMARY KEY,
    slug            VARCHAR(255) NOT NULL,
    name            TEXT         NOT NULL,
    normalized_name TEXT         NOT NULL,
    category        VARCHAR(128),
    university_id   INTEGER      REFERENCES universities (id) ON DELETE SET NULL,
    source          VARCHAR(64)  NOT NULL,
    source_url      TEXT,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_research_organizations_source_slug UNIQUE (source, slug)
);

CREATE INDEX IF NOT EXISTS idx_research_org_normalized_name
    ON research_organizations (normalized_name);
CREATE INDEX IF NOT EXISTS idx_research_org_university
    ON research_organizations (university_id)
    WHERE university_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS canonical_researchers (
    id                BIGSERIAL    PRIMARY KEY,
    display_name      TEXT         NOT NULL,
    normalized_name   TEXT         NOT NULL,
    orcid             VARCHAR(19),
    olim_profile_id   INTEGER      REFERENCES olim_profiles (id) ON DELETE SET NULL,
    canonical_status  VARCHAR(16)  NOT NULL DEFAULT 'unreviewed'
                           CHECK (canonical_status IN (
                               'unreviewed', 'auto_matched', 'reviewed', 'rejected'
                           )),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_canonical_researcher_orcid
        CHECK (orcid IS NULL OR orcid ~ '^[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]$')
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_canonical_researchers_orcid
    ON canonical_researchers (orcid)
    WHERE orcid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_canonical_researchers_olim_profile
    ON canonical_researchers (olim_profile_id)
    WHERE olim_profile_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_canonical_researchers_normalized_name
    ON canonical_researchers (normalized_name);

CREATE TABLE IF NOT EXISTS researcher_aliases (
    id               BIGSERIAL   PRIMARY KEY,
    researcher_id    BIGINT      NOT NULL
                                  REFERENCES canonical_researchers (id) ON DELETE CASCADE,
    alias            TEXT        NOT NULL,
    normalized_alias TEXT        NOT NULL,
    language         VARCHAR(16),
    source           VARCHAR(64),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_researcher_alias UNIQUE (researcher_id, normalized_alias)
);

CREATE INDEX IF NOT EXISTS idx_researcher_alias_normalized
    ON researcher_aliases (normalized_alias);

CREATE TABLE IF NOT EXISTS researcher_external_profiles (
    id                   BIGSERIAL   PRIMARY KEY,
    researcher_id        BIGINT      NOT NULL
                                      REFERENCES canonical_researchers (id) ON DELETE CASCADE,
    source               VARCHAR(16) NOT NULL
                                      CHECK (source IN ('scopus', 'scholar', 'wos', 'orcid')),
    source_author_id     TEXT,
    internal_profile_url TEXT,
    external_profile_url TEXT,
    orcid                VARCHAR(19),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_external_profile_orcid
        CHECK (orcid IS NULL OR orcid ~ '^[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]$'),
    CONSTRAINT ck_external_profile_locator
        CHECK (
            source_author_id IS NOT NULL OR internal_profile_url IS NOT NULL
            OR external_profile_url IS NOT NULL OR orcid IS NOT NULL
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_external_profile_source_author
    ON researcher_external_profiles (source, source_author_id)
    WHERE source_author_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_external_profiles_researcher
    ON researcher_external_profiles (researcher_id);

CREATE TABLE IF NOT EXISTS researcher_affiliations (
    id                BIGSERIAL   PRIMARY KEY,
    researcher_id     BIGINT      NOT NULL
                                   REFERENCES canonical_researchers (id) ON DELETE CASCADE,
    organization_id   BIGINT      NOT NULL
                                   REFERENCES research_organizations (id) ON DELETE CASCADE,
    source            VARCHAR(64) NOT NULL,
    organization_rank INTEGER     CHECK (organization_rank IS NULL OR organization_rank > 0),
    reporting_period  VARCHAR(64) NOT NULL,
    is_primary        BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_researcher_affiliation
        UNIQUE (researcher_id, organization_id, source, reporting_period)
);

CREATE INDEX IF NOT EXISTS idx_researcher_affiliations_org
    ON researcher_affiliations (organization_id, reporting_period);

CREATE TABLE IF NOT EXISTS researcher_metric_snapshots (
    id                  BIGSERIAL   PRIMARY KEY,
    researcher_id       BIGINT      NOT NULL
                                     REFERENCES canonical_researchers (id) ON DELETE CASCADE,
    external_profile_id BIGINT      NOT NULL
                                     REFERENCES researcher_external_profiles (id) ON DELETE CASCADE,
    import_batch_id     BIGINT      REFERENCES research_import_batches (id) ON DELETE SET NULL,
    source              VARCHAR(16) NOT NULL
                                     CHECK (source IN ('scopus', 'scholar', 'wos')),
    h_index             INTEGER     CHECK (h_index IS NULL OR h_index >= 0),
    hindex_status       VARCHAR(16) NOT NULL
                                     CHECK (hindex_status IN (
                                         'reported', 'source_na', 'missing', 'invalid'
                                     )),
    rank                INTEGER     CHECK (rank IS NULL OR rank > 0),
    reporting_period    VARCHAR(64) NOT NULL,
    snapshot_date       DATE        NOT NULL,
    source_url          TEXT,
    scraped_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_researcher_metric_snapshot
        UNIQUE (external_profile_id, reporting_period, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_researcher_metrics_researcher
    ON researcher_metric_snapshots (researcher_id, source, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_researcher_metrics_batch
    ON researcher_metric_snapshots (import_batch_id)
    WHERE import_batch_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS researcher_match_candidates (
    id                 BIGSERIAL   PRIMARY KEY,
    organization_id    BIGINT      NOT NULL
                                    REFERENCES research_organizations (id) ON DELETE CASCADE,
    scopus_profile_id  BIGINT      NOT NULL
                                    REFERENCES researcher_external_profiles (id) ON DELETE CASCADE,
    scholar_profile_id BIGINT      NOT NULL
                                    REFERENCES researcher_external_profiles (id) ON DELETE CASCADE,
    name_similarity    NUMERIC(5,4) NOT NULL
                                    CHECK (name_similarity >= 0 AND name_similarity <= 1),
    signals            JSONB       NOT NULL DEFAULT '{}'::jsonb,
    decision           VARCHAR(32) NOT NULL
                                    CHECK (decision IN (
                                        'high_confidence_candidate',
                                        'manual_review',
                                        'rejected'
                                    )),
    review_status      VARCHAR(16) NOT NULL DEFAULT 'pending'
                                    CHECK (review_status IN ('pending', 'approved', 'rejected')),
    reviewed_by        INTEGER     REFERENCES users (id) ON DELETE SET NULL,
    reviewed_at        TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_researcher_match_pair UNIQUE (scopus_profile_id, scholar_profile_id)
);

CREATE INDEX IF NOT EXISTS idx_researcher_matches_review
    ON researcher_match_candidates (review_status, decision, created_at);
CREATE INDEX IF NOT EXISTS idx_researcher_matches_org
    ON researcher_match_candidates (organization_id);

-- Ranking tables are lazily created by blueprints/ranking.py. Link them only
-- when present, so a fresh database can apply this migration before /reyting
-- is first opened. Re-running after the ranking tables exist adds the links.
DO $migration$
BEGIN
    IF to_regclass('h_index_institutions') IS NOT NULL THEN
        ALTER TABLE h_index_institutions
            ADD COLUMN IF NOT EXISTS research_organization_id BIGINT;
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'fk_hidx_institution_research_org'
              AND conrelid = 'h_index_institutions'::regclass
        ) THEN
            ALTER TABLE h_index_institutions
                ADD CONSTRAINT fk_hidx_institution_research_org
                FOREIGN KEY (research_organization_id)
                REFERENCES research_organizations (id) ON DELETE SET NULL;
        END IF;
        CREATE INDEX IF NOT EXISTS idx_hidx_inst_research_org
            ON h_index_institutions (research_organization_id)
            WHERE research_organization_id IS NOT NULL;
    END IF;

    IF to_regclass('h_index_scholars') IS NOT NULL THEN
        ALTER TABLE h_index_scholars
            ADD COLUMN IF NOT EXISTS canonical_researcher_id BIGINT;
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'fk_hidx_scholar_canonical_researcher'
              AND conrelid = 'h_index_scholars'::regclass
        ) THEN
            ALTER TABLE h_index_scholars
                ADD CONSTRAINT fk_hidx_scholar_canonical_researcher
                FOREIGN KEY (canonical_researcher_id)
                REFERENCES canonical_researchers (id) ON DELETE SET NULL;
        END IF;
        CREATE INDEX IF NOT EXISTS idx_hidx_sch_canonical_researcher
            ON h_index_scholars (canonical_researcher_id)
            WHERE canonical_researcher_id IS NOT NULL;
    END IF;
END
$migration$;

COMMIT;
