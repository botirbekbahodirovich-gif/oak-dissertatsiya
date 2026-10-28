-- Reverts migrations/021_institution_links.sql. Run by hand after a backup:
--   psql "$DATABASE_URL" -f migrations/rollback/021_institution_links.down.sql
-- parent_id belongs to 020_institution_identity and is kept; only the values
-- written by the backfill are cleared. Also remove the ledger row so
-- scripts/migrate.py sees 021 as pending again.
BEGIN;
DROP TABLE IF EXISTS dissertation_institutions;
DROP INDEX IF EXISTS idx_institutions_parent;
ALTER TABLE institutions DROP CONSTRAINT IF EXISTS institutions_parent_not_self;
UPDATE institutions SET parent_id = NULL WHERE parent_id IS NOT NULL;
DO $$ BEGIN
 IF to_regclass('public.schema_migrations') IS NOT NULL THEN
  DELETE FROM schema_migrations WHERE filename = '021_institution_links.sql';
 END IF;
END $$;
COMMIT;
