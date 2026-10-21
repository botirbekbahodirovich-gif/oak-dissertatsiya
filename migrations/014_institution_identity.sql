-- Canonical institution IDs. Additive: dissertations.muassasa is preserved.
BEGIN;
CREATE TABLE IF NOT EXISTS institutions (
 id BIGSERIAL PRIMARY KEY, canonical_name TEXT NOT NULL,
 normalized_name TEXT NOT NULL UNIQUE, latin_name TEXT,
 category VARCHAR(50) NOT NULL DEFAULT 'boshqa', region VARCHAR(100),
 parent_id BIGINT REFERENCES institutions(id) ON DELETE SET NULL,
 merged_into_id BIGINT REFERENCES institutions(id) ON DELETE SET NULL,
 is_active BOOLEAN NOT NULL DEFAULT TRUE,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS institution_aliases (
 id BIGSERIAL PRIMARY KEY,
 institution_id BIGINT NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
 raw_name TEXT NOT NULL, normalized_name TEXT NOT NULL UNIQUE,
 alias_type VARCHAR(30) NOT NULL DEFAULT 'variant',
 source VARCHAR(50) NOT NULL DEFAULT 'admin',
 review_status VARCHAR(20) NOT NULL DEFAULT 'approved'
  CHECK (review_status IN ('approved','rejected')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), UNIQUE(institution_id,raw_name)
);
CREATE TABLE IF NOT EXISTS institution_resolution_queue (
 id BIGSERIAL PRIMARY KEY, raw_name TEXT NOT NULL UNIQUE,
 normalized_name TEXT NOT NULL, reason VARCHAR(30) NOT NULL
  CHECK (reason IN ('unresolved','multiple','legacy_review')),
 source VARCHAR(50) NOT NULL,
 suggested_institution_id BIGINT REFERENCES institutions(id) ON DELETE SET NULL,
 occurrence_count INTEGER NOT NULL DEFAULT 1,
 status VARCHAR(20) NOT NULL DEFAULT 'pending'
  CHECK (status IN ('pending','resolved','ignored')),
 first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), resolved_at TIMESTAMPTZ,
 resolved_by TEXT
);
CREATE TABLE IF NOT EXISTS institution_identity_events (
 id BIGSERIAL PRIMARY KEY, action VARCHAR(30) NOT NULL,
 source_institution_id BIGINT REFERENCES institutions(id) ON DELETE SET NULL,
 target_institution_id BIGINT REFERENCES institutions(id) ON DELETE SET NULL,
 payload JSONB NOT NULL DEFAULT '{}'::jsonb, admin_username TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), undone_at TIMESTAMPTZ,
 undone_by TEXT
);
ALTER TABLE dissertations ADD COLUMN IF NOT EXISTS institution_id BIGINT;
DO $$ BEGIN
 ALTER TABLE dissertations ADD CONSTRAINT dissertations_institution_id_fkey
  FOREIGN KEY(institution_id) REFERENCES institutions(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
CREATE INDEX IF NOT EXISTS idx_dissertations_institution_id ON dissertations(institution_id);
CREATE INDEX IF NOT EXISTS idx_institution_aliases_institution ON institution_aliases(institution_id);
CREATE INDEX IF NOT EXISTS idx_institution_queue_status ON institution_resolution_queue(status,last_seen_at DESC);
COMMIT;
-- Rollback is manual and backup-gated; no destructive rollback executes here.
