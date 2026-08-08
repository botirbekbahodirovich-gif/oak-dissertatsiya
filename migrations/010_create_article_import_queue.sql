-- ============================================================================
-- Migration 010 — DOI/CSV ommaviy import navbati (Phase 2C.2)
-- ============================================================================
-- Admin bir nechta DOI (ro'yxat yoki CSV) yuborganda har bir DOI alohida
-- kuzatiladigan yozuv sifatida saqlanadi. Bu migratsiya faqat SAQLASH
-- JOYINI ochadi — hech qanday maqola avtomatik yaratilmaydi, hech qanday
-- mavjud jadval o'zgartirilmaydi.
--
-- Nega alohida jadval, nima uchun `scientific_articles`ga to'g'ridan-to'g'ri
-- emas: import — MODERATSIYA navbati. Admin ko'rib chiqqunicha, tasdiqlaguncha
-- yozuv `scientific_articles`ga UMUMAN kirmaydi (§13, §30). Batch/element
-- holati serverdan mustaqil saqlanishi kerak — admin sahifani yopsa yoki
-- server qayta ishga tushsa navbat yo'qolmasin (§7, §20).
--
-- XAVFSIZLIK XUSUSIYATLARI (test_article_import_migration.py bilan tekshiriladi):
--   * Bitta tranzaksiya (BEGIN/COMMIT).
--   * Idempotent — CREATE TABLE/INDEX IF NOT EXISTS.
--   * DROP, TRUNCATE, DELETE, UPDATE yo'q.
--   * blueprints/articles.py va scientific_articles ustunlariga TEGILMAYDI.
--
-- JADVALLAR
--   article_import_batches — bitta "yuborish" (DOI ro'yxati yoki CSV fayl).
--     Hisoblagichlar (`pending_count` va h.k.) DERIVED qiymatlar: yagona
--     haqiqat manbai — `article_import_items.status`. Ular har bir holat
--     o'tishidan keyin bitta tranzaksiya ichida QAYTA HISOBLANADI (drift
--     oldini olish uchun +1/-1 increment o'rniga), servis qatlamida
--     (services/article_import/persistence.py:recompute_batch_counts).
--
--   article_import_items — bitta DOI/qator. `metadata_candidate` —
--     NormalizedArticleMetadata.to_dict() natijasi (§8: xom provayder
--     javobi bu yerga SAQLANMAYDI, faqat normallashtirilgan nomzod).
--
-- HOLAT MASHINASI — services/article_import/state.py (ALLOWED_TRANSITIONS)
-- yagona haqiqat manbai; bu yerda faqat matn ustuni sifatida saqlanadi
-- (loyiha konvensiyasi — Python enum o'rniga validated string, bir xil
-- naqsh utils/article_rules.py bilan).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS article_import_batches (
    id                  SERIAL PRIMARY KEY,
    created_by          INTEGER      NOT NULL,
    source_type         VARCHAR(16)  NOT NULL DEFAULT 'doi_list',
    original_filename   TEXT,
    status              VARCHAR(24)  NOT NULL DEFAULT 'draft',

    total_items         INTEGER      NOT NULL DEFAULT 0,
    pending_count       INTEGER      NOT NULL DEFAULT 0,
    processing_count    INTEGER      NOT NULL DEFAULT 0,
    review_count        INTEGER      NOT NULL DEFAULT 0,
    approved_count      INTEGER      NOT NULL DEFAULT 0,
    rejected_count       INTEGER     NOT NULL DEFAULT 0,
    failed_count        INTEGER      NOT NULL DEFAULT 0,
    duplicate_count     INTEGER      NOT NULL DEFAULT 0,
    skipped_count       INTEGER      NOT NULL DEFAULT 0,

    cancel_requested    BOOLEAN      NOT NULL DEFAULT FALSE,

    created_at          TIMESTAMP    NOT NULL DEFAULT NOW(),
    started_at          TIMESTAMP,
    completed_at        TIMESTAMP,
    updated_at          TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_article_import_batches_created_by
    ON article_import_batches (created_by, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_article_import_batches_status
    ON article_import_batches (status);
CREATE INDEX IF NOT EXISTS idx_article_import_batches_created_at
    ON article_import_batches (created_at DESC);

CREATE TABLE IF NOT EXISTS article_import_items (
    id                     SERIAL PRIMARY KEY,
    batch_id               INTEGER      NOT NULL REFERENCES article_import_batches(id) ON DELETE CASCADE,
    row_number              INTEGER      NOT NULL,
    raw_input               TEXT         NOT NULL,
    normalized_doi           VARCHAR(255),

    status                  VARCHAR(24)  NOT NULL DEFAULT 'pending',
    duplicate_of_item_id     INTEGER      REFERENCES article_import_items(id) ON DELETE SET NULL,
    existing_article_id      INTEGER      REFERENCES scientific_articles(id) ON DELETE SET NULL,
    created_article_id       INTEGER      REFERENCES scientific_articles(id) ON DELETE SET NULL,

    -- NormalizedArticleMetadata.to_dict() natijasi — xom API javobi EMAS (§8).
    metadata_candidate       JSONB,
    provider_status_summary  JSONB,
    conflicts                JSONB,
    warnings                 JSONB,

    is_retracted             BOOLEAN      NOT NULL DEFAULT FALSE,
    oa_status                VARCHAR(24),
    candidate_pdf_url        TEXT,

    attempt_count            INTEGER      NOT NULL DEFAULT 0,
    last_error_code          VARCHAR(64),
    last_error_message       TEXT,

    created_at               TIMESTAMP    NOT NULL DEFAULT NOW(),
    started_at               TIMESTAMP,
    processed_at              TIMESTAMP,
    reviewed_at               TIMESTAMP,
    reviewed_by               INTEGER
);

CREATE INDEX IF NOT EXISTS idx_article_import_items_batch
    ON article_import_items (batch_id, row_number);
CREATE INDEX IF NOT EXISTS idx_article_import_items_doi
    ON article_import_items (normalized_doi);
CREATE INDEX IF NOT EXISTS idx_article_import_items_status
    ON article_import_items (status);
CREATE INDEX IF NOT EXISTS idx_article_import_items_created_at
    ON article_import_items (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_article_import_items_existing_article
    ON article_import_items (existing_article_id) WHERE existing_article_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_article_import_items_created_article
    ON article_import_items (created_article_id) WHERE created_article_id IS NOT NULL;
-- Bir chunk ichida "pending yoki retryable failed"ni tez topish uchun
-- (services/article_import/processor.py:_next_chunk).
CREATE INDEX IF NOT EXISTS idx_article_import_items_batch_status
    ON article_import_items (batch_id, status, row_number);

COMMIT;

-- ============================================================================
-- ROLLBACK (ATAYIN bajarilmaydigan holda — ma'lumotni yo'q qiladi)
-- ============================================================================
-- DROP TABLE IF EXISTS article_import_items;
-- DROP TABLE IF EXISTS article_import_batches;
