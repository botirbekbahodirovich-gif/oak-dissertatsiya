-- ============================================================================
-- Migration 012 — metadata sync dashboard: actor semantics + dismiss + filter
-- flags (Phase 2C.3B)
-- ============================================================================
-- Migration 011 (`article_metadata_syncs`) ni KENGAYTIRADI — parallel jadval
-- YARATILMAYDI. Faqat markaziy review dashboard uchun real yetishmayotgan
-- ustunlar qo'shiladi:
--
--   actor_type            — kim tekshirdi: 'admin' | 'system' | 'agent'.
--                            Hozircha faqat 'admin' yoziladi (Phase 2C.3B da
--                            scheduler YO'Q), lekin ustun BOR bo'lishi kerak —
--                            kelajakdagi background job (Phase 2C.3C)
--                            `actor_type='system'` bilan `checked_by=NULL`
--                            yoza olishi uchun. Fake "system" user
--                            YARATILMAYDI (§3).
--   dismissed_*            — admin diff'ni "ko'rib chiqildi, kerak emas" deb
--                            yopishi mumkin (§12). Bu maqolani RAD ETISH
--                            emas — faqat shu sync candidate yopiladi,
--                            tarix o'chirilmaydi.
--   has_retraction_change,
--   has_citation_change,
--   has_author_change,
--   has_provider_issue     — `diff_summary`/`provider_summary` JSONB'ni har
--                            dashboard so'rovida qayta tahlil qilmaslik uchun
--                            INSERT vaqtida hisoblab qo'yiladigan flag'lar
--                            (§8 filtrlar, §23 — premature index emas, real
--                            query ehtiyoji).
--
-- `checked_by` NOT NULL dan BO'SHATILADI — 'system' actor uchun haqiqiy
-- foydalanuvchi yo'q, soxta ID o'ylab topilmaydi (§3).
--
-- XAVFSIZLIK XUSUSIYATLARI (test_article_sync_migration.py bilan tekshiriladi):
--   * Bitta tranzaksiya (BEGIN/COMMIT).
--   * Idempotent — ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
--   * DROP, TRUNCATE, DELETE yo'q. Mavjud qatorlar o'qilmaydi/o'chirilmaydi.
--   * `scientific_articles`/`article_authors`/`article_import_*` ustunlariga
--     TEGILMAYDI.
-- ============================================================================

BEGIN;

ALTER TABLE article_metadata_syncs
    ADD COLUMN IF NOT EXISTS actor_type             VARCHAR(16) NOT NULL DEFAULT 'admin',
    ADD COLUMN IF NOT EXISTS dismissed_at            TIMESTAMP,
    ADD COLUMN IF NOT EXISTS dismissed_by            INTEGER,
    ADD COLUMN IF NOT EXISTS dismissed_reason        TEXT,
    ADD COLUMN IF NOT EXISTS has_retraction_change   BOOLEAN     NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS has_citation_change     BOOLEAN     NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS has_author_change       BOOLEAN     NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS has_provider_issue      BOOLEAN     NOT NULL DEFAULT FALSE;

-- `checked_by` — allaqachon NOT NULL bo'lsa bo'shatiladi; allaqachon
-- bo'shatilgan bo'lsa (ikkinchi marta ishga tushirilsa) no-op.
ALTER TABLE article_metadata_syncs ALTER COLUMN checked_by DROP NOT NULL;

CREATE INDEX IF NOT EXISTS idx_article_metadata_syncs_actor_type
    ON article_metadata_syncs (actor_type);
-- Dashboard "Muhim" filtri/saralashi uchun — faqat critical>0 qatorlar kam,
-- partial index arzon va tez-tez ishlatiladigan yo'l.
CREATE INDEX IF NOT EXISTS idx_article_metadata_syncs_critical
    ON article_metadata_syncs (checked_at DESC) WHERE critical_change_count > 0;

COMMIT;

-- ============================================================================
-- ROLLBACK (ATAYIN bajarilmaydigan holda — ma'lumotni yo'q qiladi)
-- ============================================================================
-- ALTER TABLE article_metadata_syncs
--     DROP COLUMN IF EXISTS actor_type,
--     DROP COLUMN IF EXISTS dismissed_at,
--     DROP COLUMN IF EXISTS dismissed_by,
--     DROP COLUMN IF EXISTS dismissed_reason,
--     DROP COLUMN IF EXISTS has_retraction_change,
--     DROP COLUMN IF EXISTS has_citation_change,
--     DROP COLUMN IF EXISTS has_author_change,
--     DROP COLUMN IF EXISTS has_provider_issue;
-- ALTER TABLE article_metadata_syncs ALTER COLUMN checked_by SET NOT NULL;
