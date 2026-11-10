-- dissertations.notified_at: when the new-defense notifications for this row
-- were claimed by services/oak_notify.py (scraper.py → notify_new_dissertations).
-- NULL = not yet notified. Every existing row is marked now, so the first run
-- after deploy never notifies about old records. New rows are inserted with
-- NULL (no column default) and claimed exactly once:
--   UPDATE dissertations SET notified_at = NOW()
--   WHERE id = ANY(...) AND notified_at IS NULL RETURNING ...
-- Rollback: migrations/rollback/022_dissertations_notified_at.down.sql
BEGIN;
ALTER TABLE dissertations ADD COLUMN IF NOT EXISTS notified_at TIMESTAMPTZ;
UPDATE dissertations SET notified_at = NOW() WHERE notified_at IS NULL;
COMMIT;
