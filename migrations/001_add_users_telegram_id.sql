BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS telegram_id BIGINT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_users_telegram_id
    ON users (telegram_id)
    WHERE telegram_id IS NOT NULL;

COMMIT;

-- Rollback (run manually only after confirming no Telegram login depends on it):
-- BEGIN;
-- DROP INDEX IF EXISTS uq_users_telegram_id;
-- ALTER TABLE users DROP COLUMN IF EXISTS telegram_id;
-- COMMIT;
