-- ============================================================================
-- Migration 004 — Data Quality Phase 1 tables
-- ============================================================================
-- Creates three ADDITIVE tables used by the Data Quality subsystem:
--     data_quality_runs        -- one row per scanner execution
--     data_quality_issues      -- one row per finding (field-level)
--     duplicate_candidates     -- one row per detected candidate pair
--
-- SAFETY PROPERTIES (verified by test_data_quality_migration.py):
--   * Wrapped in a single transaction (BEGIN/COMMIT) — all-or-nothing.
--   * Idempotent — every object uses IF NOT EXISTS, so re-running is a no-op.
--   * NON-destructive — no DROP, no ALTER of existing tables, no UPDATE/DELETE
--     of production data. The `dissertations`, `users` and `import_audit_log`
--     tables are NOT touched.
--   * No Postgres extension is created here (pg_trgm stays opt-in / server-side).
--
-- record_id is POLYMORPHIC (record_type + record_id). For Phase 1 record_type
-- is always 'dissertation', but we deliberately do NOT add a hard FOREIGN KEY
-- to dissertations(id): the column is meant to also address future record
-- types (users, organizations, ...), and a polymorphic FK is not expressible.
-- Integrity of the reference is checked in application code / the scanner.
--
-- ROLLBACK (safe — these tables are additive and hold only derived data):
--     BEGIN;
--       DROP TABLE IF EXISTS duplicate_candidates;
--       DROP TABLE IF EXISTS data_quality_issues;
--       DROP TABLE IF EXISTS data_quality_runs;
--     COMMIT;
-- ============================================================================

BEGIN;

-- ── data_quality_runs ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS data_quality_runs (
    id               BIGSERIAL PRIMARY KEY,
    run_type         VARCHAR(32)  NOT NULL DEFAULT 'scan',
    status           VARCHAR(16)  NOT NULL DEFAULT 'running'
                         CHECK (status IN ('running', 'completed', 'failed')),
    started_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at     TIMESTAMPTZ,
    total_records    INTEGER      NOT NULL DEFAULT 0,
    checked_records  INTEGER      NOT NULL DEFAULT 0,
    issue_count      INTEGER      NOT NULL DEFAULT 0,
    duplicate_count  INTEGER      NOT NULL DEFAULT 0,
    metadata         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    error_message    TEXT
);

CREATE INDEX IF NOT EXISTS idx_dq_runs_started
    ON data_quality_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_dq_runs_status
    ON data_quality_runs (status);

-- ── data_quality_issues ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS data_quality_issues (
    id               BIGSERIAL PRIMARY KEY,
    run_id           BIGINT       REFERENCES data_quality_runs (id) ON DELETE CASCADE,
    record_type      VARCHAR(32)  NOT NULL DEFAULT 'dissertation',
    record_id        INTEGER      NOT NULL,
    field_name       VARCHAR(64)  NOT NULL,
    issue_code       VARCHAR(48)  NOT NULL,
    severity         VARCHAR(16)  NOT NULL DEFAULT 'warning'
                         CHECK (severity IN ('critical', 'warning', 'info')),
    original_value   TEXT,
    normalized_value TEXT,
    description      TEXT,
    status           VARCHAR(16)  NOT NULL DEFAULT 'open'
                         CHECK (status IN ('open', 'resolved', 'ignored', 'fixed')),
    resolved_by      INTEGER      REFERENCES users (id) ON DELETE SET NULL,
    resolved_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dq_issues_run       ON data_quality_issues (run_id);
CREATE INDEX IF NOT EXISTS idx_dq_issues_code      ON data_quality_issues (issue_code);
CREATE INDEX IF NOT EXISTS idx_dq_issues_severity  ON data_quality_issues (severity);
CREATE INDEX IF NOT EXISTS idx_dq_issues_status    ON data_quality_issues (status);
CREATE INDEX IF NOT EXISTS idx_dq_issues_record    ON data_quality_issues (record_type, record_id);

-- ── duplicate_candidates ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS duplicate_candidates (
    id            BIGSERIAL PRIMARY KEY,
    record_type   VARCHAR(32)  NOT NULL DEFAULT 'dissertation',
    record_id_1   INTEGER      NOT NULL,
    record_id_2   INTEGER      NOT NULL,
    score         NUMERIC(5,4) NOT NULL DEFAULT 0,
    match_level   VARCHAR(16)  NOT NULL
                      CHECK (match_level IN ('exact', 'strong', 'possible')),
    reasons       JSONB        NOT NULL DEFAULT '[]'::jsonb,
    status        VARCHAR(16)  NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'confirmed', 'rejected', 'merged')),
    reviewed_by   INTEGER      REFERENCES users (id) ON DELETE SET NULL,
    reviewed_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- Canonical pair ordering (record_id_1 < record_id_2) is enforced by the
    -- application; this constraint guarantees a pair is stored at most once.
    CONSTRAINT uq_duplicate_pair UNIQUE (record_type, record_id_1, record_id_2)
);

CREATE INDEX IF NOT EXISTS idx_dup_status   ON duplicate_candidates (status);
CREATE INDEX IF NOT EXISTS idx_dup_level    ON duplicate_candidates (match_level);
CREATE INDEX IF NOT EXISTS idx_dup_record1  ON duplicate_candidates (record_type, record_id_1);
CREATE INDEX IF NOT EXISTS idx_dup_record2  ON duplicate_candidates (record_type, record_id_2);

COMMIT;
