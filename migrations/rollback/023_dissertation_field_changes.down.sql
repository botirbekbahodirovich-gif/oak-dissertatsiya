-- Reverts migrations/023_dissertation_field_changes.sql. Run by hand after a backup:
--   psql "$DATABASE_URL" -f migrations/rollback/023_dissertation_field_changes.down.sql
-- WARNING: drops the change log. Undo any applied batches first
-- (python3 scripts/oak_backfill_from_api.py undo --batch ...), or keep the table.
BEGIN;
DROP TABLE IF EXISTS dissertation_field_changes;
DELETE FROM schema_migrations WHERE filename = '023_dissertation_field_changes.sql';
COMMIT;
