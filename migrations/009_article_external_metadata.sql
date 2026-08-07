-- ============================================================================
-- Migration 009 — maqolalar uchun tashqi identifikator va metadata provenance
-- ============================================================================
-- Phase 2C.1: admin DOI kiritganda Crossref / OpenAlex / Unpaywall'dan
-- metadata olinadi. Olingan qiymatlar avtomatik saqlanmaydi — admin formani
-- yuborgandagina yoziladi. Bu migratsiya faqat SAQLASH JOYINI ochadi.
--
-- Faqat NULLable ustunlar qo'shiladi (`is_retracted` dan tashqari, u
-- DEFAULT FALSE bilan keladi). Mavjud qatorlar o'qilmaydi va qayta
-- yozilmaydi; hech qanday ma'lumot o'chirilmaydi.
--
-- XAVFSIZLIK XUSUSIYATLARI (test_articles_migration.py bilan tekshiriladi):
--   * Bitta tranzaksiya (BEGIN/COMMIT).
--   * Idempotent — ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
--   * DROP, TRUNCATE, DELETE, UPDATE yo'q.
--   * blueprints/articles.py:_ensure_schema AYNAN shu ustunlarni qo'shadi.
--
-- USTUNLAR — scientific_articles
--   openalex_id                — OpenAlex work ID ("W2741809807"). To'liq URL
--                                emas: qisqa ID barqaror va indekslash arzon.
--   crossref_member_id         — Crossref a'zo (nashriyot) raqami.
--   primary_issn               — jurnalning asosiy ISSN'i (elektron afzal).
--                                Jurnal jadvali YARATILMAYDI: hozircha
--                                maqolalar jurnal nomi bilan ishlaydi va
--                                ortiqcha normalizatsiya qilinmaydi.
--   is_retracted               — maqola chaqirib olinganmi. Bu signal jimgina
--                                yo'qolmasligi kerak: OpenAlex uni bersa admin
--                                ko'radi va nashr qarorini o'zi qabul qiladi.
--   citation_count             — iqtibos soni. DOIMIY fakt EMAS.
--   citation_count_source      — qaysi manbadan ("openalex").
--   citation_count_checked_at  — QACHON o'lchangani. Sanasiz iqtibos soni
--                                haqiqat sifatida ko'rinib qolardi.
--   metadata_last_synced_at    — oxirgi marta tashqi metadata bilan
--                                solishtirilgan vaqt.
--   metadata_source_summary    — qaysi maydon qaysi provayderdan kelgani:
--                                {"title": "crossref", "oa_status": "unpaywall"}
--                                JSONB — alohida provenance jadvali hozircha
--                                ortiqcha (§19).
--
-- USTUNLAR — article_authors
--   openalex_author_id         — OpenAlex muallif ID'si ("A5023888391").
--                                Institutlar alohida modelga MAJBURAN
--                                normalizatsiya qilinmaydi: `affiliation`
--                                matni hozircha yetarli (§18).
--
-- XOM API JAVOBI BU YERGA SAQLANMAYDI (§20). Kesh — Flask-Caching
-- FileSystemCache (`flask_cache/`), TTL 12 soat.
-- ============================================================================

BEGIN;

ALTER TABLE scientific_articles
    ADD COLUMN IF NOT EXISTS openalex_id               VARCHAR(32),
    ADD COLUMN IF NOT EXISTS crossref_member_id        VARCHAR(32),
    ADD COLUMN IF NOT EXISTS primary_issn              VARCHAR(20),
    ADD COLUMN IF NOT EXISTS is_retracted              BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS citation_count            INTEGER,
    ADD COLUMN IF NOT EXISTS citation_count_source     VARCHAR(24),
    ADD COLUMN IF NOT EXISTS citation_count_checked_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS metadata_last_synced_at   TIMESTAMP,
    ADD COLUMN IF NOT EXISTS metadata_source_summary   JSONB;

-- Bitta OpenAlex yozuvi ikki marta kiritilmasin. Partial unique: ID yo'q
-- maqolalar (ko'pchilik) bir-biriga to'sqinlik qilmaydi.
CREATE UNIQUE INDEX IF NOT EXISTS uq_scientific_articles_openalex
    ON scientific_articles (openalex_id)
    WHERE openalex_id IS NOT NULL AND openalex_id <> '';

CREATE INDEX IF NOT EXISTS idx_articles_primary_issn
    ON scientific_articles (primary_issn)
    WHERE primary_issn IS NOT NULL;

-- Chaqirib olingan maqolalarni admin tez topishi uchun.
CREATE INDEX IF NOT EXISTS idx_articles_retracted
    ON scientific_articles (is_retracted)
    WHERE is_retracted;

-- "Qaysi maqolalar hech qachon sinxronlanmagan" — keyingi bulk import uchun.
CREATE INDEX IF NOT EXISTS idx_articles_metadata_synced
    ON scientific_articles (metadata_last_synced_at NULLS FIRST);

ALTER TABLE article_authors
    ADD COLUMN IF NOT EXISTS openalex_author_id VARCHAR(32);

CREATE INDEX IF NOT EXISTS idx_article_authors_openalex
    ON article_authors (openalex_author_id)
    WHERE openalex_author_id IS NOT NULL;

COMMIT;

-- ============================================================================
-- ROLLBACK (ATAYIN bajarilmaydigan holda — ma'lumotni yo'q qiladi)
-- ============================================================================
-- ALTER TABLE scientific_articles
--     DROP COLUMN IF EXISTS openalex_id,
--     DROP COLUMN IF EXISTS crossref_member_id,
--     DROP COLUMN IF EXISTS primary_issn,
--     DROP COLUMN IF EXISTS is_retracted,
--     DROP COLUMN IF EXISTS citation_count,
--     DROP COLUMN IF EXISTS citation_count_source,
--     DROP COLUMN IF EXISTS citation_count_checked_at,
--     DROP COLUMN IF EXISTS metadata_last_synced_at,
--     DROP COLUMN IF EXISTS metadata_source_summary;
-- ALTER TABLE article_authors DROP COLUMN IF EXISTS openalex_author_id;
