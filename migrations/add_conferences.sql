-- Konferensiyalar katalogi (blueprints/conferences.py _ensure_schema bilan bir xil).
-- Server lazy self-migrate qiladi; bu fayl qo'lda psql uchun hujjat/zaxira.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS conferences (
    id SERIAL PRIMARY KEY,
    title VARCHAR(600) NOT NULL,
    title_slug VARCHAR(250) UNIQUE,
    scope VARCHAR(10) NOT NULL CHECK (scope IN ('local','international')),
    organizer VARCHAR(400),
    field VARCHAR(200),              -- soha (mahalliy) yoki subject area (xalqaro)
    region VARCHAR(100),             -- faqat mahalliy: viloyat
    city VARCHAR(150),
    event_type VARCHAR(50),          -- Anjuman/Forum/... yoki Conference/Workshop
    start_date DATE,
    end_date DATE,
    is_multiday BOOLEAN DEFAULT FALSE,
    format VARCHAR(20),              -- 'onsite','online','hybrid'
    publisher VARCHAR(200),          -- xalqaro: Springer/IEEE/ACM/...
    is_scopus_indexed BOOLEAN DEFAULT FALSE,
    submission_deadline DATE,
    country VARCHAR(100),
    source_url VARCHAR(500),
    source_id VARCHAR(300) UNIQUE,   -- manba dedup kaliti
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conf_scope_date ON conferences(scope, start_date) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_conf_field ON conferences(field);
CREATE INDEX IF NOT EXISTS idx_conf_region ON conferences(region) WHERE scope = 'local';
CREATE INDEX IF NOT EXISTS idx_conf_title_trgm ON conferences USING GIN (title gin_trgm_ops);

CREATE TABLE IF NOT EXISTS user_conference_bookmarks (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conference_id INTEGER NOT NULL REFERENCES conferences(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, conference_id)
);
CREATE INDEX IF NOT EXISTS idx_conf_bm_user
    ON user_conference_bookmarks(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS conference_notifications_log (
    id SERIAL PRIMARY KEY,
    subscription_id INTEGER REFERENCES specialty_subscriptions(id) ON DELETE CASCADE,
    conference_id INTEGER REFERENCES conferences(id) ON DELETE CASCADE,
    sent_via VARCHAR(20) DEFAULT 'site',
    sent_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conf_notif_log
    ON conference_notifications_log(subscription_id, conference_id);

-- Roadmap: katalogdan qo'shilgan yozuv belgisi (qo'ldagilar NULL bo'lib qoladi)
ALTER TABLE IF EXISTS roadmap_conferences
    ADD COLUMN IF NOT EXISTS source_conference_id INTEGER;
