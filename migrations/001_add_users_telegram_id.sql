BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS telegram_id BIGINT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_users_telegram_id
    ON users (telegram_id)
    WHERE telegram_id IS NOT NULL;

COMMIT;

-- Precondition check (run manually FIRST if the users.telegram_id column may
-- already be populated). The partial UNIQUE index above can only be built when
-- no two rows share a non-NULL telegram_id; otherwise the CREATE fails and its
-- transaction rolls back cleanly (no partial state). On a freshly added column
-- every value is NULL, so this returns nothing:
--     SELECT telegram_id, COUNT(*)
--     FROM users
--     WHERE telegram_id IS NOT NULL
--     GROUP BY telegram_id
--     HAVING COUNT(*) > 1;

-- Rollback (run manually only after confirming no Telegram login depends on it):
-- BEGIN;
-- DROP INDEX IF EXISTS uq_users_telegram_id;
-- ALTER TABLE users DROP COLUMN IF EXISTS telegram_id;
-- COMMIT;
