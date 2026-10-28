-- Institution hierarchy + multi-institution defences. Additive only.
-- parent_id: "X huzuridagi Y" / "Fanlar akademiyasi ... instituti" -> Y.parent_id = X.
-- dissertation_institutions: co-owners of a defence whose OAK text names two
-- organisations ("A hamda B"); dissertations.institution_id stays the primary.
-- Rollback: migrations/rollback/021_institution_links.down.sql
BEGIN;
ALTER TABLE institutions ADD COLUMN IF NOT EXISTS parent_id BIGINT
 REFERENCES institutions(id) ON DELETE SET NULL;
DO $$ BEGIN
 ALTER TABLE institutions ADD CONSTRAINT institutions_parent_not_self
  CHECK (parent_id IS NULL OR parent_id <> id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
CREATE INDEX IF NOT EXISTS idx_institutions_parent ON institutions(parent_id);
CREATE TABLE IF NOT EXISTS dissertation_institutions (
 dissertation_id INTEGER NOT NULL REFERENCES dissertations(id) ON DELETE CASCADE,
 institution_id BIGINT NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
 role VARCHAR(20) NOT NULL DEFAULT 'co' CHECK (role IN ('co')),
 source VARCHAR(50) NOT NULL DEFAULT 'admin',
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY (dissertation_id, institution_id)
);
CREATE INDEX IF NOT EXISTS idx_dissertation_institutions_institution
 ON dissertation_institutions(institution_id);
COMMIT;
