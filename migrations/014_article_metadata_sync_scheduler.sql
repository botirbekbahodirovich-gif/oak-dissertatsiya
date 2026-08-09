-- ============================================================================
-- Migration 013 — scheduled article metadata sync: run audit table
-- (Phase 2C.3C)
-- ============================================================================
-- YANGI, MUSTAQIL jadval — `article_metadata_syncs` (011/012) ga TEGILMAYDI.
-- Bu jadval faqat scheduler RUN'larining (CLI/cron chaqiruvi) natijasini
-- audit qiladi — har bir maqola sinxronizatsiyasi hali ham
-- `article_metadata_syncs`ga yoziladi (docs/article-metadata-scheduler.md).
--
--   status         — 'running' | 'completed' | 'completed_with_errors' |
--                     'failed'. Global advisory lock (§14) allaqachon
--                     ikkinchi run bir vaqtda ishlashini oldini oladi — bu
--                     ustun faqat KUZATUV, mutual exclusion MANBAI EMAS.
--                     Shu sababli process crash bo'lib, qator abadiy
--                     'running' holida qolib ketishi mumkin — bu alohida,
--                     kam ta'sirli holat (lock process bilan birga o'zi
--                     yechiladi, keyingi run bloklanib qolmaydi).
--   trigger_type   — 'scheduled' (cron) | 'manual_cli' (operator qo'lda).
--   *_count        — item-level failure isolation natijasi (§17, §22):
--                     processed_count = success_count + partial_count +
--                     failed_count; total_selected = processed_count +
--                     skipped_count (invariant, docs'da hujjatlashtirilgan).
--
-- XAVFSIZLIK XUSUSIYATLARI (test_article_metadata_scheduler_migration.py):
--   * Bitta tranzaksiya (BEGIN/COMMIT).
--   * Idempotent — CREATE TABLE/INDEX IF NOT EXISTS.
--   * DROP, TRUNCATE, DELETE, UPDATE yo'q.
--   * scientific_articles/article_authors/article_metadata_syncs ustunlariga
--     TEGILMAYDI.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS article_metadata_sync_runs (
    id                  SERIAL PRIMARY KEY,
    started_at          TIMESTAMP    NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMP,
    status              VARCHAR(24)  NOT NULL DEFAULT 'running',
    trigger_type        VARCHAR(16)  NOT NULL DEFAULT 'manual_cli',
    total_selected      INTEGER      NOT NULL DEFAULT 0,
    processed_count     INTEGER      NOT NULL DEFAULT 0,
    success_count       INTEGER      NOT NULL DEFAULT 0,
    partial_count       INTEGER      NOT NULL DEFAULT 0,
    failed_count        INTEGER      NOT NULL DEFAULT 0,
    skipped_count       INTEGER      NOT NULL DEFAULT 0,
    created_at          TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_article_metadata_sync_runs_started
    ON article_metadata_sync_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_article_metadata_sync_runs_status
    ON article_metadata_sync_runs (status);

COMMIT;

-- ============================================================================
-- ROLLBACK (ATAYIN bajarilmaydigan holda — ma'lumotni yo'q qiladi)
-- ============================================================================
-- DROP TABLE IF EXISTS article_metadata_sync_runs;
