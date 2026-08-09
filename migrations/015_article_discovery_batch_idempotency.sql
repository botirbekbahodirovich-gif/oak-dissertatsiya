-- ============================================================================
-- Migration 015 — Article discovery enqueue idempotency (Phase 2D.1.1)
-- ============================================================================
-- Phase 2D.1 known limitation: a concurrent/double "navbatga yuborish" click
-- could create TWO `article_import_batches` rows for the same discovery
-- preview — the item-level partial unique index (migrations/014,
-- `uq_article_import_items_active_doi`) already stops any DOI from being
-- queued twice, but nothing stopped the SECOND click from producing its own
-- (usually empty) batch. This migration closes that at the batch level.
--
-- `source_request_id` — a server-computed SHA-256 hex digest of
-- (admin_id + preview token), NEVER the raw token itself (§8, §20 of
-- docs/article-discovery.md: raw tokens are not persisted anywhere). Only
-- discovery batches populate it; CSV/DOI-list batches leave it NULL and are
-- completely unaffected — the partial index only applies to
-- `source_type = 'discovery'`.
--
-- `services/article_discovery/bridge.py` uses
-- `INSERT ... ON CONFLICT (source_request_id) WHERE source_type = 'discovery'
-- AND source_request_id IS NOT NULL DO NOTHING RETURNING id` — the SAME
-- deterministic conflict-handling pattern already used for
-- `uq_article_import_items_active_doi` (migrations/014). A second concurrent
-- insert for the same request simply returns no row; the caller then reads
-- back the FIRST batch instead of creating a new one. No exception, no
-- rollback, no 500.
--
-- XAVFSIZLIK XUSUSIYATLARI (tests/test_article_discovery_migration.py):
--   * Bitta tranzaksiya (BEGIN/COMMIT).
--   * Idempotent — ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
--   * DROP, TRUNCATE, DELETE, UPDATE yo'q.
--   * article_import_items / scientific_articles / article_authors /
--     article_metadata_syncs ustunlariga TEGILMAYDI — bu FAQAT
--     article_import_batches uchun.
--   * `services/article_import/persistence.py:ensure_schema` shu bilan
--     AYNAN bir xil DDL'ni lazy qo'llaydi (parity test).
-- ============================================================================

BEGIN;

ALTER TABLE article_import_batches
    ADD COLUMN IF NOT EXISTS source_request_id VARCHAR(64);

-- Faqat discovery batch'lari orasida — CSV/DOI-list import (source_request_id
-- doim NULL qoladi) bu index'ga UMUMAN kirmaydi, mavjud xulq-atvor buzilmaydi.
CREATE UNIQUE INDEX IF NOT EXISTS uq_article_import_batches_discovery_request
    ON article_import_batches (source_request_id)
    WHERE source_type = 'discovery' AND source_request_id IS NOT NULL;

COMMIT;

-- ============================================================================
-- ROLLBACK (ATAYIN bajarilmaydigan holda — ma'lumotni yo'q qiladi)
-- ============================================================================
-- DROP INDEX IF EXISTS uq_article_import_batches_discovery_request;
-- ALTER TABLE article_import_batches DROP COLUMN IF EXISTS source_request_id;
