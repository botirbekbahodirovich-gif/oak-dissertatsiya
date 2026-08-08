-- ============================================================================
-- Migration 011 — mavjud maqola metadata sinxronizatsiyasi tarixi (Phase 2C.3A)
-- ============================================================================
-- Admin mavjud (bazadagi) maqolani DOI orqali qayta tekshirganda (Crossref/
-- OpenAlex/Unpaywall), natija shu jadvalga yoziladi. Bu — AUDIT/DIFF yozuvi,
-- article maydonlarini o'zi o'zgartirmaydi: `services/article_sync/service.py`
-- `check_article_metadata()` faqat SHU yerga yozadi, `scientific_articles`ga
-- yozish faqat admin explicit "apply" qilganda (§14).
--
-- Xom API javobi bu yerga SAQLANMAYDI (Phase 2C.1/2C.2 bilan bir xil qoida,
-- §15) — faqat normallashtirilgan diff xulosasi (JSONB).
--
-- `article_updated_at_snapshot` — stale-himoya uchun (§17): apply paytida
-- joriy `scientific_articles.updated_at` shu qiymat bilan solishtiriladi;
-- farq bo'lsa (boshqa admin orada tahrirlagan) apply BLOKLANADI.
--
-- XAVFSIZLIK XUSUSIYATLARI (test_article_sync_migration.py bilan tekshiriladi):
--   * Bitta tranzaksiya (BEGIN/COMMIT).
--   * Idempotent — CREATE TABLE/INDEX IF NOT EXISTS.
--   * DROP, TRUNCATE, DELETE, UPDATE yo'q.
--   * `scientific_articles`/`article_authors` ustunlariga TEGILMAYDI.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS article_metadata_syncs (
    id                          SERIAL PRIMARY KEY,
    article_id                  INTEGER      NOT NULL REFERENCES scientific_articles(id) ON DELETE CASCADE,
    status                      VARCHAR(24)  NOT NULL DEFAULT 'checked',
    checked_at                  TIMESTAMP    NOT NULL DEFAULT NOW(),
    checked_by                  INTEGER      NOT NULL,

    -- {"crossref": "olindi", "openalex": "olindi", "unpaywall": "javob bermadi"}
    provider_summary            JSONB,
    -- [MetadataFieldDiff.to_dict(), ...] — normallashtirilgan, xom JAVOB emas.
    diff_summary                JSONB,
    change_count                INTEGER      NOT NULL DEFAULT 0,
    safe_change_count           INTEGER      NOT NULL DEFAULT 0,
    review_change_count         INTEGER      NOT NULL DEFAULT 0,
    critical_change_count       INTEGER      NOT NULL DEFAULT 0,

    -- Stale-himoya ankeri (§17) — tekshiruv PAYTIDAGI article.updated_at.
    article_updated_at_snapshot TIMESTAMP,

    applied_at                  TIMESTAMP,
    applied_by                  INTEGER,
    -- Qaysi maydonlar aynan qo'llanildi — idempotency/audit uchun (§18).
    applied_fields               JSONB,

    created_at                  TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_article_metadata_syncs_article
    ON article_metadata_syncs (article_id, checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_article_metadata_syncs_status
    ON article_metadata_syncs (status);
CREATE INDEX IF NOT EXISTS idx_article_metadata_syncs_checked_by
    ON article_metadata_syncs (checked_by);

COMMIT;

-- ============================================================================
-- ROLLBACK (ATAYIN bajarilmaydigan holda — ma'lumotni yo'q qiladi)
-- ============================================================================
-- DROP TABLE IF EXISTS article_metadata_syncs;
