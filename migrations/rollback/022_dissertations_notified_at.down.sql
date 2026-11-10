-- Reverts migrations/022_dissertations_notified_at.sql. Run by hand after a backup:
--   psql "$DATABASE_URL" -f migrations/rollback/022_dissertations_notified_at.down.sql
-- Deploy the previous code first: services/oak_notify.py needs this column.
-- Also removes the ledger row so scripts/migrate.py sees 022 as pending again.
BEGIN;
ALTER TABLE dissertations DROP COLUMN IF EXISTS notified_at;
DO $$ BEGIN
 IF to_regclass('public.schema_migrations') IS NOT NULL THEN
  DELETE FROM schema_migrations WHERE filename = '022_dissertations_notified_at.sql';
 END IF;
END $$;
COMMIT;
