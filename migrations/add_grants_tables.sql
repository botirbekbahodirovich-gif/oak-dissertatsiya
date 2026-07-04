-- Grants module v2 migration — EXTENDS the existing `grants` table (created by
-- blueprints/grants.py + scripts/grant_scraper.py) instead of replacing it.
-- The scraper keeps writing the legacy columns (title, description,
-- scientific_codes, country, funding_type, academic_level,
-- application_deadline, source_url, requirements_json, provider); new code
-- reads new columns with legacy fallbacks, so both coexist.
--
-- Idempotent: safe to run repeatedly. The same statements also run lazily from
-- blueprints/grants.py _ensure_schema on first request, so the server
-- self-migrates even if this file is never executed by hand.
--   Manual run:  psql "$DATABASE_URL" -f migrations/add_grants_tables.sql

-- ── grants: new columns ─────────────────────────────────────────────────────
ALTER TABLE grants ADD COLUMN IF NOT EXISTS title_uz VARCHAR(500);
ALTER TABLE grants ADD COLUMN IF NOT EXISTS slug VARCHAR(200);
ALTER TABLE grants ADD COLUMN IF NOT EXISTS organization VARCHAR(300);
ALTER TABLE grants ADD COLUMN IF NOT EXISTS country_flag VARCHAR(10);
ALTER TABLE grants ADD COLUMN IF NOT EXISTS academic_levels TEXT[] DEFAULT '{}';
ALTER TABLE grants ADD COLUMN IF NOT EXISTS scientific_fields TEXT[] DEFAULT '{}';
ALTER TABLE grants ADD COLUMN IF NOT EXISTS requirements TEXT;
ALTER TABLE grants ADD COLUMN IF NOT EXISTS benefits TEXT;
ALTER TABLE grants ADD COLUMN IF NOT EXISTS documents_checklist JSONB DEFAULT '[]';
ALTER TABLE grants ADD COLUMN IF NOT EXISTS application_tips TEXT;
ALTER TABLE grants ADD COLUMN IF NOT EXISTS start_date DATE;
ALTER TABLE grants ADD COLUMN IF NOT EXISTS stipend_amount VARCHAR(200);
ALTER TABLE grants ADD COLUMN IF NOT EXISTS duration VARCHAR(200);
ALTER TABLE grants ADD COLUMN IF NOT EXISTS language_requirements VARCHAR(300);
ALTER TABLE grants ADD COLUMN IF NOT EXISTS source_id VARCHAR(200);
ALTER TABLE grants ADD COLUMN IF NOT EXISTS cover_image_url VARCHAR(500);
ALTER TABLE grants ADD COLUMN IF NOT EXISTS tags TEXT[] DEFAULT '{}';
ALTER TABLE grants ADD COLUMN IF NOT EXISTS view_count INTEGER DEFAULT 0;
ALTER TABLE grants ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;
ALTER TABLE grants ADD COLUMN IF NOT EXISTS is_featured BOOLEAN DEFAULT FALSE;
ALTER TABLE grants ADD COLUMN IF NOT EXISTS created_by INTEGER;
ALTER TABLE grants ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();

-- Unique keys (partial: legacy rows may have NULLs)
CREATE UNIQUE INDEX IF NOT EXISTS uq_grants_slug ON grants(slug) WHERE slug IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_grants_source_id ON grants(source_id) WHERE source_id IS NOT NULL;

-- ── Backfill legacy → new (only rows not yet migrated) ──────────────────────
UPDATE grants SET organization = provider
 WHERE organization IS NULL AND provider IS NOT NULL;
UPDATE grants SET academic_levels = ARRAY[academic_level]
 WHERE (academic_levels IS NULL OR academic_levels = '{}')
   AND academic_level IS NOT NULL AND academic_level <> '';
UPDATE grants SET scientific_fields = string_to_array(replace(scientific_codes, ' ', ''), ',')
 WHERE (scientific_fields IS NULL OR scientific_fields = '{}')
   AND scientific_codes IS NOT NULL AND scientific_codes <> '';
UPDATE grants SET is_active = TRUE WHERE is_active IS NULL;
UPDATE grants SET view_count = 0 WHERE view_count IS NULL;
-- slug backfill is done in Python (needs Cyrillic→Latin transliteration);
-- see blueprints/grants.py _ensure_schema.

-- ── user_grant_tracking (successor of user_tracked_grants) ──────────────────
CREATE TABLE IF NOT EXISTS user_grant_tracking (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    grant_id INTEGER NOT NULL REFERENCES grants(id) ON DELETE CASCADE,
    status VARCHAR(30) DEFAULT 'interested'
        CHECK (status IN ('interested', 'preparing', 'documents_ready',
                          'applied', 'accepted', 'rejected')),
    notes TEXT,
    tracked_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, grant_id)
);
CREATE INDEX IF NOT EXISTS idx_user_grant_tracking_user ON user_grant_tracking(user_id);

-- migrate rows from the old table once, then keep the old table (read-only
-- history; the app no longer writes it)
DO $$
BEGIN
  IF to_regclass('user_tracked_grants') IS NOT NULL THEN
    INSERT INTO user_grant_tracking (user_id, grant_id, status, tracked_at)
    SELECT t.user_id, t.grant_id,
           CASE t.status WHEN 'in_progress' THEN 'preparing' ELSE 'interested' END,
           t.created_at
    FROM user_tracked_grants t
    ON CONFLICT (user_id, grant_id) DO NOTHING;
  END IF;
END $$;

-- ── grant_success_stories ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS grant_success_stories (
    id SERIAL PRIMARY KEY,
    grant_id INTEGER NOT NULL REFERENCES grants(id) ON DELETE CASCADE,
    user_id INTEGER,
    year INTEGER,
    university_name VARCHAR(300),
    country VARCHAR(100),
    testimonial TEXT,
    is_anonymous BOOLEAN DEFAULT FALSE,
    is_approved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_success_stories_grant ON grant_success_stories(grant_id);

-- ── query indexes ────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_grants_deadline_active
    ON grants(application_deadline) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_grants_country ON grants(country);
CREATE INDEX IF NOT EXISTS idx_grants_levels ON grants USING GIN(academic_levels);
CREATE INDEX IF NOT EXISTS idx_grants_fields ON grants USING GIN(scientific_fields);
CREATE INDEX IF NOT EXISTS idx_grants_tags ON grants USING GIN(tags);
