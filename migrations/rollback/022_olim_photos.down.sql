-- Reverts migrations/022_olim_photos.sql. Run by hand after a backup:
--   psql "$DATABASE_URL" -f migrations/rollback/022_olim_photos.down.sql
-- Pages fall back to the placeholder (utils/olim_photos.py treats a missing
-- table as empty). The WebP files in static/olim_photos/ are left in place.
BEGIN;
DROP TABLE IF EXISTS olim_photos;
DO $$ BEGIN
 IF to_regclass('public.schema_migrations') IS NOT NULL THEN
  DELETE FROM schema_migrations WHERE filename = '022_olim_photos.sql';
 END IF;
END $$;
COMMIT;
