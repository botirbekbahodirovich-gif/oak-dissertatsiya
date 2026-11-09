-- ============================================================================
-- Migration 007 — ilmiy maqolalar katalogi va fan yo'nalishlari ierarxiyasi
-- ============================================================================
-- Phase 2B.1. To'rtta yangi jadval qo'shiladi; MAVJUD birorta jadval
-- o'zgartirilmaydi, o'qilmaydi va qayta yozilmaydi.
--
--   scientific_fields         — fan tarmog'i -> guruh -> ixtisoslik -> kichik
--                               yo'nalish (parent_id bilan recursive daraxt)
--   scientific_articles       — maqola metadata + PDF saqlash ma'lumoti +
--                               litsenziya/huquq/tekshiruv holati
--   article_authors           — mualliflar, tartibi saqlanadi
--   article_scientific_fields — maqola <-> yo'nalish (many-to-many, is_primary)
--
-- XAVFSIZLIK XUSUSIYATLARI (test_articles_migration.py bilan tekshiriladi):
--   * Bitta tranzaksiya (BEGIN/COMMIT) — hammasi yoki hech nima.
--   * Idempotent — har bir obyekt IF NOT EXISTS bilan; qayta ishga tushirish
--     no-op. blueprints/articles.py:_ensure_schema() AYNAN shu DDL ni
--     takrorlaydi (grants/conferences konvensiyasi: server birinchi so'rovda
--     self-migrate qiladi), shuning uchun ikkalasi ziddiyatsiz.
--   * Faylda DROP, TRUNCATE, DELETE va UPDATE yo'q.
--   * Seed ma'lumoti bu yerda YO'Q (§31): yo'nalishlar
--     scripts/seed_scientific_fields.py orqali alohida kiritiladi.
--
-- ROLLBACK: fayl oxiridagi izohli blokka qarang.
--
-- MUHIM QARORLAR
--   * created_by / reviewed_by da users ga FK YO'Q — foydalanuvchilar legacy
--     SQLite users.db da (saved.py / maqolalar.py bilan bir xil sabab).
--   * doi UNIQUE, lekin faqat NOT NULL qatorlar uchun (partial unique index):
--     DOI'siz maqola ruxsat etilgan va ular bir-biriga xalaqit bermaydi.
--   * checksum_sha256 UNIQUE emas, indekslangan: bir xil faylni ikkinchi
--     marta yuklashni admin formasi ogohlantirish bilan bloklaydi, lekin
--     ma'lumotlar bazasi darajasidagi qattiq cheklov migratsiyani mo'rt
--     qiladi (bir maqolaning ikki versiyasi bir xil faylga ega bo'lishi
--     nazariy jihatdan mumkin).
--   * article_scientific_fields da bitta asosiy yo'nalish partial UNIQUE
--     indeks bilan majburlanadi — validatsiya faqat Python'da qolmaydi.
--   * scientific_fields.parent_id ON DELETE RESTRICT: bolalari bor
--     kategoriya tasodifan yo'qolmaydi (§4).
-- ============================================================================

BEGIN;

-- ── Fan yo'nalishlari ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scientific_fields (
    id                SERIAL PRIMARY KEY,
    parent_id         INTEGER REFERENCES scientific_fields(id) ON DELETE RESTRICT,
    name_uz           TEXT         NOT NULL,
    name_uz_cyrillic  TEXT,
    name_ru           TEXT,
    code              VARCHAR(32),
    slug              VARCHAR(200) NOT NULL UNIQUE,
    level             SMALLINT     NOT NULL DEFAULT 1,
    description       TEXT,
    sort_order        INTEGER      NOT NULL DEFAULT 0,
    is_active         BOOLEAN      NOT NULL DEFAULT TRUE,
    source_name       TEXT,
    source_url        TEXT,
    source_updated_at DATE,
    created_at        TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- Kod ixtiyoriy, lekin berilgan bo'lsa yagona (seed idempotentligi shunga tayanadi).
CREATE UNIQUE INDEX IF NOT EXISTS uq_scientific_fields_code
    ON scientific_fields (code) WHERE code IS NOT NULL AND code <> '';
CREATE INDEX IF NOT EXISTS idx_scientific_fields_parent
    ON scientific_fields (parent_id);
CREATE INDEX IF NOT EXISTS idx_scientific_fields_level
    ON scientific_fields (level, sort_order);
CREATE INDEX IF NOT EXISTS idx_scientific_fields_active
    ON scientific_fields (is_active) WHERE is_active;

-- ── Maqolalar ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scientific_articles (
    id                  SERIAL PRIMARY KEY,
    title               TEXT         NOT NULL,
    title_uz            TEXT,
    slug                VARCHAR(220) NOT NULL UNIQUE,
    abstract            TEXT,
    keywords            TEXT,
    language            VARCHAR(8)   NOT NULL DEFAULT 'uz',
    publication_year    INTEGER,
    publication_date    DATE,
    journal_name        TEXT,
    volume              VARCHAR(50),
    issue               VARCHAR(50),
    pages               VARCHAR(50),
    doi                 VARCHAR(255),
    external_url        TEXT,

    -- PDF saqlash. pdf_path — static/ TASHQARISIDAGI storage/articles/ ichidagi
    -- nisbiy yo'l; nginx uni to'g'ridan-to'g'ri bermaydi (docs/articles-module.md).
    pdf_path            TEXT,
    pdf_original_name   TEXT,
    pdf_size            BIGINT,
    pdf_mime_type       VARCHAR(100),
    checksum_sha256     CHAR(64),

    license_type        VARCHAR(40)  NOT NULL DEFAULT 'unknown',
    license_url         TEXT,
    access_type         VARCHAR(40)  NOT NULL DEFAULT 'metadata_only',
    version_type        VARCHAR(40)  NOT NULL DEFAULT 'other',
    source_type         VARCHAR(40)  NOT NULL DEFAULT 'other_verified_source',
    source_name         TEXT,
    source_url          TEXT,
    copyright_note      TEXT,
    permission_note     TEXT,

    -- Huquqiy tasdiq (§14) — checkbox o'zi kafolat emas, audit izi sifatida saqlanadi.
    rights_confirmed_by INTEGER,
    rights_confirmed_at TIMESTAMP,

    index_scopus        BOOLEAN      NOT NULL DEFAULT FALSE,
    index_wos           BOOLEAN      NOT NULL DEFAULT FALSE,
    index_doaj          BOOLEAN      NOT NULL DEFAULT FALSE,
    index_other         TEXT,

    verification_status VARCHAR(24)  NOT NULL DEFAULT 'draft',
    publication_status  VARCHAR(24)  NOT NULL DEFAULT 'draft',
    is_featured         BOOLEAN      NOT NULL DEFAULT FALSE,
    view_count          INTEGER      NOT NULL DEFAULT 0,
    download_count      INTEGER      NOT NULL DEFAULT 0,

    created_by          INTEGER,
    reviewed_by         INTEGER,
    reviewed_at         TIMESTAMP,
    created_at          TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_scientific_articles_doi
    ON scientific_articles (doi) WHERE doi IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_articles_checksum
    ON scientific_articles (checksum_sha256) WHERE checksum_sha256 IS NOT NULL;
-- Katalogning issiq yo'li: faqat nashr etilgan + tekshirilgan qatorlar.
CREATE INDEX IF NOT EXISTS idx_articles_public
    ON scientific_articles (publication_status, verification_status, publication_year DESC);
CREATE INDEX IF NOT EXISTS idx_articles_year
    ON scientific_articles (publication_year DESC);
CREATE INDEX IF NOT EXISTS idx_articles_language
    ON scientific_articles (language);
CREATE INDEX IF NOT EXISTS idx_articles_journal
    ON scientific_articles (journal_name);
CREATE INDEX IF NOT EXISTS idx_articles_created
    ON scientific_articles (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_views
    ON scientific_articles (view_count DESC);

-- ── Mualliflar ──────────────────────────────────────────────────────────────
-- Tashqi mualliflar oddiy matn sifatida qoladi; sayt olimiga ishonchli moslik
-- bo'lsagina linked_* to'ldiriladi (§7) — majburiy bog'lash yo'q, FK ham yo'q.
CREATE TABLE IF NOT EXISTS article_authors (
    id                 SERIAL PRIMARY KEY,
    article_id         INTEGER NOT NULL REFERENCES scientific_articles(id) ON DELETE CASCADE,
    author_name        TEXT    NOT NULL,
    author_order       INTEGER NOT NULL DEFAULT 1,
    orcid              VARCHAR(64),
    affiliation        TEXT,
    linked_user_id     INTEGER,
    linked_scientist_id INTEGER,
    is_corresponding   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at         TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_article_authors_article
    ON article_authors (article_id, author_order);
CREATE INDEX IF NOT EXISTS idx_article_authors_name
    ON article_authors (author_name);

-- ── Maqola <-> yo'nalish ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS article_scientific_fields (
    id                  SERIAL PRIMARY KEY,
    article_id          INTEGER NOT NULL REFERENCES scientific_articles(id) ON DELETE CASCADE,
    scientific_field_id INTEGER NOT NULL REFERENCES scientific_fields(id) ON DELETE RESTRICT,
    is_primary          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (article_id, scientific_field_id)
);

-- Bitta maqolada faqat bitta asosiy yo'nalish (§8) — DB darajasida majburlanadi.
CREATE UNIQUE INDEX IF NOT EXISTS uq_article_primary_field
    ON article_scientific_fields (article_id) WHERE is_primary;
CREATE INDEX IF NOT EXISTS idx_article_fields_field
    ON article_scientific_fields (scientific_field_id);

COMMIT;

-- ============================================================================
-- ROLLBACK (qo'lda ishga tushiriladi — bu blok ataylab izohda qoldirilgan,
-- chunki u ma'lumotni yo'q qiladi; faqat Phase 2B.1 ni butunlay qaytarishda):
--
--   BEGIN;
--   DROP TABLE IF EXISTS article_scientific_fields;
--   DROP TABLE IF EXISTS article_authors;
--   DROP TABLE IF EXISTS scientific_articles;
--   DROP TABLE IF EXISTS scientific_fields;
--   COMMIT;
--
-- Jadvallar shu tartibda tushiriladi (bola -> ota), aks holda FK to'sadi.
-- storage/articles/ ichidagi PDF fayllar rollback bilan O'CHIRILMAYDI —
-- ular qo'lda ko'rib chiqilishi kerak.
-- ============================================================================
