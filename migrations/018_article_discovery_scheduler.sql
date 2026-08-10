-- ============================================================================
-- Migration 018 — Scheduled article discovery (Phase 2D.3)
-- ============================================================================
-- A saved discovery query (migrations/017) may optionally run on a simple
-- cadence (daily/weekly/monthly) instead of only manually. This migration
-- is purely additive:
--   1. Schedule state columns on `article_discovery_saved_queries` itself
--      (smallest clean design — a 1:1 relationship, no separate schedule
--      table: every saved query has AT MOST one schedule configuration,
--      never several, so a join-only table would add complexity without
--      benefit).
--   2. A new, independent `article_discovery_runs` table for lightweight
--      operational history (manual AND scheduled runs alike) — analogous
--      to `article_metadata_sync_runs` (migrations/014) but scoped to a
--      specific saved query.
--
-- SCHEDULE COLUMNS — article_discovery_saved_queries
--   schedule_enabled        — off by default (existing rows: FALSE).
--   schedule_frequency      — 'daily' | 'weekly' | 'monthly'; NULL when
--                              schedule_enabled = FALSE. No arbitrary cron
--                              expressions in this phase (ticket §2).
--   next_run_at             — when this query is next due; NULL when
--                              disabled. Claimed via `FOR UPDATE SKIP
--                              LOCKED` (services/article_discovery/
--                              scheduler_persistence.py), matching the
--                              existing `article_import_items` claiming
--                              convention (migrations/011,
--                              `fetch_pending_chunk`).
--   last_scheduled_run_at   — timestamp of the most recent SCHEDULED
--                              execution (manual "Run" clicks do not touch
--                              this — ticket §9).
--   last_run_status         — 'success' | 'partial' | 'failed' | 'skipped'
--                              from the most recent run (manual or
--                              scheduled) — mirrors article_discovery_runs
--                              .status for quick admin-list display without
--                              a join.
--   last_run_error          — short, sanitized error summary (no raw
--                              exception text/stack traces, no secrets).
--   scheduled_updated_at    — when the SCHEDULE CONFIGURATION itself
--                              (enabled/frequency) last changed — distinct
--                              from the existing `updated_at`, which
--                              already tracks query-parameter edits
--                              (keyword/dates/providers/limit).
--
-- article_discovery_runs
--   saved_query_id     — REFERENCES article_discovery_saved_queries(id)
--                        ON DELETE CASCADE (a run history row is meaningless
--                        without its parent query; saved queries are never
--                        hard-deleted in practice — soft `is_active` only —
--                        so this cascade is a safety net, not an expected
--                        path).
--   trigger_type       — 'manual' | 'scheduled'.
--   status             — 'running' | 'success' | 'partial' | 'failed' |
--                        'skipped'.
--   providers          — JSONB snapshot of which providers were selected
--                        for THIS run (a saved query's provider list can
--                        change between runs via "Tahrirlash").
--   found_count/new_count/queued_count/duplicate_count/no_doi_count —
--                        derived from the existing DiscoveryPreviewResult.
--                        summary()/bridge.enqueue_discovery_candidates()
--                        results — no new classification logic.
--   error_summary      — short, sanitized text. Never a raw provider
--                        payload, stack trace, or secret.
--   import_batch_id    — REFERENCES article_import_batches(id) ON DELETE
--                        SET NULL (same pattern as
--                        article_import_items.existing_article_id) —
--                        nullable because a run with zero NEW candidates
--                        creates no batch at all (existing bridge.py §9
--                        "no orphan batch" behavior, unchanged).
--
-- XAVFSIZLIK XUSUSIYATLARI (tests/test_article_discovery_scheduler_migration.py):
--   * Bitta tranzaksiya (BEGIN/COMMIT).
--   * Idempotent — ADD COLUMN IF NOT EXISTS / CREATE TABLE|INDEX IF NOT EXISTS.
--   * DROP, TRUNCATE, DELETE, UPDATE yo'q.
--   * scientific_articles/article_authors/article_metadata_syncs/
--     article_metadata_sync_runs ustunlariga TEGILMAYDI.
--   * article_import_batches/article_import_items ustunlariga TEGILMAYDI —
--     provenance mavjud `source_query` JSONB ichida kengaytiriladi
--     (bridge.py `extra_provenance`), yangi ustun YO'Q.
--   * `services/article_discovery/saved_queries.py:ensure_schema` va
--     `services/article_discovery/scheduler_persistence.py:ensure_schema`
--     shu bilan AYNAN bir xil DDL'ni lazy qo'llaydi (parity test).
-- ============================================================================

BEGIN;

ALTER TABLE article_discovery_saved_queries
    ADD COLUMN IF NOT EXISTS schedule_enabled        BOOLEAN   NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS schedule_frequency       VARCHAR(16),
    ADD COLUMN IF NOT EXISTS next_run_at              TIMESTAMP,
    ADD COLUMN IF NOT EXISTS last_scheduled_run_at     TIMESTAMP,
    ADD COLUMN IF NOT EXISTS last_run_status           VARCHAR(24),
    ADD COLUMN IF NOT EXISTS last_run_error            TEXT,
    ADD COLUMN IF NOT EXISTS scheduled_updated_at      TIMESTAMP;

-- Due-so'rovlarni tez topish uchun — faqat rejalashtirilgan va faol
-- qatorlar (`WHERE schedule_enabled` — CSV/manual-only qatorlar index'dan
-- tashqarida qoladi, jadval kattalashsa ham due-tekshiruv arzon qoladi).
CREATE INDEX IF NOT EXISTS idx_article_discovery_saved_queries_due
    ON article_discovery_saved_queries (next_run_at)
    WHERE schedule_enabled AND is_active;

CREATE TABLE IF NOT EXISTS article_discovery_runs (
    id                SERIAL PRIMARY KEY,
    saved_query_id    INTEGER      NOT NULL
        REFERENCES article_discovery_saved_queries(id) ON DELETE CASCADE,
    trigger_type      VARCHAR(16)  NOT NULL DEFAULT 'manual',
    status            VARCHAR(24)  NOT NULL DEFAULT 'running',
    started_at        TIMESTAMP    NOT NULL DEFAULT NOW(),
    finished_at       TIMESTAMP,
    providers         JSONB,
    found_count       INTEGER      NOT NULL DEFAULT 0,
    new_count         INTEGER      NOT NULL DEFAULT 0,
    queued_count      INTEGER      NOT NULL DEFAULT 0,
    duplicate_count   INTEGER      NOT NULL DEFAULT 0,
    no_doi_count      INTEGER      NOT NULL DEFAULT 0,
    error_summary     TEXT,
    import_batch_id   INTEGER
        REFERENCES article_import_batches(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_article_discovery_runs_saved_query
    ON article_discovery_runs (saved_query_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_article_discovery_runs_started
    ON article_discovery_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_article_discovery_runs_status
    ON article_discovery_runs (status);

COMMIT;

-- ============================================================================
-- ROLLBACK (ATAYIN bajarilmaydigan holda — ma'lumotni yo'q qiladi)
-- ============================================================================
-- DROP TABLE IF EXISTS article_discovery_runs;
-- DROP INDEX IF EXISTS idx_article_discovery_saved_queries_due;
-- ALTER TABLE article_discovery_saved_queries
--     DROP COLUMN IF EXISTS schedule_enabled,
--     DROP COLUMN IF EXISTS schedule_frequency,
--     DROP COLUMN IF EXISTS next_run_at,
--     DROP COLUMN IF EXISTS last_scheduled_run_at,
--     DROP COLUMN IF EXISTS last_run_status,
--     DROP COLUMN IF EXISTS last_run_error,
--     DROP COLUMN IF EXISTS scheduled_updated_at;
