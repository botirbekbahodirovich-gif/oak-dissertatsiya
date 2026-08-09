-- ============================================================================
-- Migration 014 — Manual OpenAlex Article Discovery provenance (Phase 2D.1)
-- ============================================================================
-- Discovery HECH QANDAY YANGI jadval yaratmaydi (ArticleDiscoveryRun YO'Q,
-- §18) — u faqat mavjud `article_import_batches` / `article_import_items`ga
-- minimal provenance ustunlari qo'shadi. Enqueue qilingan nomzod bundan keyin
-- 100% mavjud `process_import_batch()` / `fetch_article_metadata()` orqali
-- ishlanadi (docs/article-discovery.md).
--
-- USTUNLAR — article_import_batches
--   source_provider       — masalan 'openalex'. `source_type='discovery'`
--                            (allaqachon mavjud ustun) bilan birga: "qayerdan
--                            va qaysi provayderdan" ikkalasi ham saqlanadi.
--   source_query           — validatsiya qilingan CANONICAL so'rov JSON'i
--                            (keyword/from_date/to_date/oak_field_id/limit).
--                            API kaliti/mailto/xom URL HECH QACHON bu yerga
--                            YOZILMAYDI (§20).
--   source_oak_field_id    — admin tanlagan OAK yo'nalishi — FAQAT qidiruv
--                            niyati/provenance, authoritative EMAS (§21).
--   source_result_count    — provayder javobidagi jami mos son (bounded
--                            preview'dan OLDIN, §33 — "hammasi yuklandi" deb
--                            adashtirmaslik uchun).
--   excluded_no_doi_count  — DOI'siz bo'lgani uchun candidate'ga
--                            AYLANTIRILMAGAN natijalar soni (§11).
--
-- USTUN — article_import_items
--   external_source_id     — provayder work ID'si (masalan OpenAlex "W123...").
--                            Faqat discovery orqali kelgan elementlarda
--                            to'ldiriladi; CSV/DOI-list importda NULL qoladi.
--
-- CROSS-BATCH IDEMPOTENCY (§14-§16): bitta DOI ikki marta "faol" import
-- elementi sifatida yashamasligi kerak — Python pre-check YAKUNIY himoya
-- EMAS (race, §16), shuning uchun partial UNIQUE index qo'shiladi. "Faol"
-- statuslar ro'yxati `services/article_import/state.py:ACTIVE_DOI_ITEM_STATUSES`
-- bilan AYNAN bir xil bo'lishi SHART (parity test —
-- tests/test_article_discovery_migration.py). `duplicate_in_batch` /
-- `already_exists` / `invalid_doi` ATAYIN chiqarib tashlangan: ular hech
-- qachon endi article yaratmaydi (yoki normalized_doi umuman yo'q, §11),
-- shuning uchun bir xil DOI bilan boshqa "faol" nomzod bilan
-- TO'QNASHMASLIGI SHART EMAS (masalan CSV import'dagi mavjud
-- duplicate_in_batch qatori shu index'ga kirmaydi — mavjud xulq-atvor
-- buzilmaydi).
--
-- XAVFSIZLIK XUSUSIYATLARI (test_article_discovery_migration.py):
--   * Bitta tranzaksiya (BEGIN/COMMIT).
--   * Idempotent — ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
--   * DROP, TRUNCATE, DELETE, UPDATE yo'q.
--   * scientific_articles/article_authors/article_metadata_syncs/
--     article_metadata_sync_runs ustunlariga TEGILMAYDI.
--   * `services/article_import/persistence.py:ensure_schema` shu bilan
--     AYNAN bir xil DDL'ni lazy qo'llaydi (parity test).
-- ============================================================================

BEGIN;

ALTER TABLE article_import_batches
    ADD COLUMN IF NOT EXISTS source_provider      VARCHAR(24),
    ADD COLUMN IF NOT EXISTS source_query         JSONB,
    ADD COLUMN IF NOT EXISTS source_oak_field_id  INTEGER
        REFERENCES scientific_fields(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS source_result_count  INTEGER,
    ADD COLUMN IF NOT EXISTS excluded_no_doi_count INTEGER;

CREATE INDEX IF NOT EXISTS idx_article_import_batches_source_oak_field
    ON article_import_batches (source_oak_field_id)
    WHERE source_oak_field_id IS NOT NULL;

ALTER TABLE article_import_items
    ADD COLUMN IF NOT EXISTS external_source_id VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_article_import_items_external_source
    ON article_import_items (external_source_id)
    WHERE external_source_id IS NOT NULL;

-- Yagona haqiqat manbai: services/article_import/state.py:ACTIVE_DOI_ITEM_STATUSES
-- ('pending', 'processing', 'ready_for_review', 'partial_metadata',
--  'retracted', 'provider_failed', 'failed', 'metadata_not_found').
CREATE UNIQUE INDEX IF NOT EXISTS uq_article_import_items_active_doi
    ON article_import_items (normalized_doi)
    WHERE normalized_doi IS NOT NULL AND status IN (
        'pending', 'processing', 'ready_for_review', 'partial_metadata',
        'retracted', 'provider_failed', 'failed', 'metadata_not_found'
    );

COMMIT;

-- ============================================================================
-- ROLLBACK (ATAYIN bajarilmaydigan holda — ma'lumotni yo'q qiladi)
-- ============================================================================
-- DROP INDEX IF EXISTS uq_article_import_items_active_doi;
-- DROP INDEX IF EXISTS idx_article_import_items_external_source;
-- DROP INDEX IF EXISTS idx_article_import_batches_source_oak_field;
-- ALTER TABLE article_import_items DROP COLUMN IF EXISTS external_source_id;
-- ALTER TABLE article_import_batches
--     DROP COLUMN IF EXISTS source_provider,
--     DROP COLUMN IF EXISTS source_query,
--     DROP COLUMN IF EXISTS source_oak_field_id,
--     DROP COLUMN IF EXISTS source_result_count,
--     DROP COLUMN IF EXISTS excluded_no_doi_count;
