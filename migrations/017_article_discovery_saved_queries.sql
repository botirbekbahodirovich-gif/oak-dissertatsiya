-- ============================================================================
-- Migration 017 — Saved article discovery queries (Phase 2D.2)
-- ============================================================================
-- Admin qidiruv formasini ("kalit so'z + sana oralig'i + provayder(lar) +
-- OAK yo'nalishi + limit") nomlangan holda saqlashi va keyinroq qayta
-- ishlatishi/tahrirlashi mumkin. Bu FAQAT "shu qidiruv konfiguratsiyasini
-- eslab qol" — HECH QANDAY avtomatik/rejalashtirilgan bajarilish yo'q
-- (scheduler/cron — Phase 2D.3, bu migratsiya doirasidan TASHQARIDA).
--
-- Yagona YANGI jadval (Phase 2D.1/2D.1.1'dan farqli — ular faqat mavjud
-- `article_import_batches`/`article_import_items`ni kengaytirgan edi).
-- `article_import_*`ga HECH QANDAY bog'liqlik/FK yo'q — saqlangan so'rov bir
-- marta ham ishga tushirilmagan bo'lishi mumkin.
--
-- USTUNLAR
--   created_by      — `article_import_batches.created_by` bilan BIR XIL
--                      konvensiya (migrations/011): oddiy INTEGER, FK YO'Q.
--                      Bu loyihada `users(id)`ga FK beruvchi jadvallar ham
--                      bor (masalan migrations/004), lekin BEVOSITA shu
--                      admin-panel/import pipeline oilasi (article_import_*,
--                      article_discovery-ga tegishli) izchil ravishda FK
--                      qo'shmaydi — shu YAQIN konvensiya davom ettirilgan
--                      (audit: migrations/011, 015, 016).
--   oak_field_id    — `article_import_batches.source_oak_field_id` bilan
--                      AYNAN bir xil: FK `scientific_fields(id) ON DELETE
--                      SET NULL` — yo'nalish o'chirilsa saqlangan so'rov
--                      o'zi o'chmaydi, faqat NULL bo'ladi.
--   providers       — JSONB massiv, masalan `["openalex","crossref"]`.
--                      Bo'sh/noto'g'ri qiymatlarni servis qatlami
--                      (`services/article_discovery/service.py:
--                      _parse_providers`) rad etadi — bu yerda faqat
--                      saqlash joyi, validatsiya emas.
--   is_active       — soft deactivate (loyiha konvensiyasi — hard DELETE
--                      YO'Q, xuddi `article_import_items`/`scientific_fields`
--                      bilan bir xil "holat ustuni" yondashuvi).
--   last_used_at    — "Run" bosilganda yangilanadi (admin panelda ko'rsatish
--                      uchun, §11).
--
-- XAVFSIZLIK XUSUSIYATLARI (tests/test_article_discovery_saved_queries_migration.py):
--   * Bitta tranzaksiya (BEGIN/COMMIT).
--   * Idempotent — CREATE TABLE/INDEX IF NOT EXISTS.
--   * DROP, TRUNCATE, DELETE, UPDATE yo'q.
--   * `article_import_batches`/`article_import_items`/`scientific_articles`
--     ustunlariga TEGILMAYDI — bu YANGI, mustaqil jadval.
--   * `services/article_discovery/saved_queries.py:ensure_schema` shu bilan
--     AYNAN bir xil DDL'ni lazy qo'llaydi (parity test).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS article_discovery_saved_queries (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(120) NOT NULL,
    created_by      INTEGER      NOT NULL,
    keyword         VARCHAR(300) NOT NULL,
    from_date       DATE         NOT NULL,
    to_date         DATE         NOT NULL,
    oak_field_id    INTEGER      REFERENCES scientific_fields(id) ON DELETE SET NULL,
    providers       JSONB        NOT NULL DEFAULT '["openalex", "crossref"]'::jsonb,
    result_limit    INTEGER      NOT NULL DEFAULT 50,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
    last_used_at    TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_article_discovery_saved_queries_created_by
    ON article_discovery_saved_queries (created_by, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_article_discovery_saved_queries_active
    ON article_discovery_saved_queries (is_active, created_at DESC)
    WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_article_discovery_saved_queries_oak_field
    ON article_discovery_saved_queries (oak_field_id)
    WHERE oak_field_id IS NOT NULL;

COMMIT;

-- ============================================================================
-- ROLLBACK (ATAYIN bajarilmaydigan holda — ma'lumotni yo'q qiladi)
-- ============================================================================
-- DROP TABLE IF EXISTS article_discovery_saved_queries;
