-- 003_backfill_users_telegram_id.sql
--
-- Purpose: backfill users.telegram_id from the legacy users.telegram_chat_id
-- column for accounts that predate migration 001.
--
-- Background / root cause:
--   The hardened Telegram login (auth.py) now binds accounts ONLY by the
--   immutable, HMAC-signed users.telegram_id column:
--       SELECT ... FROM users WHERE telegram_id = %s
--   The pre-001 login flow never populated telegram_id; it wrote the same
--   numeric Telegram id into telegram_chat_id instead:
--       UPDATE users SET telegram_chat_id = %s   -- %s = int(tg_id)
--   So legacy Telegram users have telegram_chat_id set but telegram_id NULL,
--   their login lookup misses, and a duplicate ghost account would be created.
--   This migration copies the id into the canonical column so existing
--   Telegram logins resolve to the original account.
--
-- Safety properties:
--   * Only rows with (telegram_id IS NULL AND telegram_chat_id IS NOT NULL)
--     are touched. Rows already carrying a telegram_id are never overwritten.
--   * telegram_id is sourced EXCLUSIVELY from telegram_chat_id. No username /
--     e-mail fallback is used or introduced.
--   * The whole thing runs in a single transaction.
--   * ABORTS (raises, rolls back, no partial state) if the backfill would
--     create a duplicate telegram_id -- either two candidate rows sharing a
--     telegram_chat_id, or a candidate colliding with an already-set
--     telegram_id. In that case a human must reconcile the accounts first.
--   * Idempotent: rerunning is a no-op once telegram_id is populated, because
--     the WHERE clause then matches no rows.
--   * Output shows COUNTS ONLY -- never raw id / username / email values.
--
-- Precondition: migration 001 (users.telegram_id column + uq_users_telegram_id
-- partial unique index) must already be applied.

BEGIN;

-- Guard: refuse to run if the column from 001 is missing (wrong order).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'telegram_id'
    ) THEN
        RAISE EXCEPTION
            'ABORT: users.telegram_id missing -- apply 001_add_users_telegram_id.sql first';
    END IF;
END $$;

-- Guard: abort if the backfill would produce a duplicate telegram_id.
-- Two failure modes are checked:
--   (a) two candidate rows share the same telegram_chat_id;
--   (b) a candidate's telegram_chat_id equals a telegram_id already set on
--       some other row.
DO $$
DECLARE
    dup_within  INTEGER;
    dup_against INTEGER;
BEGIN
    SELECT COUNT(*) INTO dup_within FROM (
        SELECT telegram_chat_id
        FROM users
        WHERE telegram_id IS NULL
          AND telegram_chat_id IS NOT NULL
        GROUP BY telegram_chat_id
        HAVING COUNT(*) > 1
    ) d;

    SELECT COUNT(*) INTO dup_against
    FROM users c
    WHERE c.telegram_id IS NULL
      AND c.telegram_chat_id IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM users e
          WHERE e.telegram_id = c.telegram_chat_id
      );

    IF dup_within > 0 OR dup_against > 0 THEN
        RAISE EXCEPTION
            'ABORT: backfill would create duplicate telegram_id '
            '(colliding chat_id groups=%, collisions with existing telegram_id=%). '
            'Reconcile the affected accounts manually before rerunning.',
            dup_within, dup_against;
    END IF;
END $$;

-- The backfill itself. telegram_id is copied ONLY from telegram_chat_id.
UPDATE users
SET telegram_id = telegram_chat_id
WHERE telegram_id IS NULL
  AND telegram_chat_id IS NOT NULL;

-- Post-update verification (counts only; raises if invariants are violated so
-- the transaction rolls back rather than committing a bad state).
DO $$
DECLARE
    non_null_ids  INTEGER;
    dup_ids       INTEGER;
    has_uq_index  BOOLEAN;
BEGIN
    SELECT COUNT(*) INTO non_null_ids
    FROM users WHERE telegram_id IS NOT NULL;

    SELECT COUNT(*) INTO dup_ids FROM (
        SELECT telegram_id
        FROM users
        WHERE telegram_id IS NOT NULL
        GROUP BY telegram_id
        HAVING COUNT(*) > 1
    ) d;

    SELECT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE indexname = 'uq_users_telegram_id'
    ) INTO has_uq_index;

    RAISE NOTICE 'telegram_id backfill: non-null telegram_id rows = %', non_null_ids;
    RAISE NOTICE 'telegram_id backfill: duplicate telegram_id groups = %', dup_ids;
    RAISE NOTICE 'telegram_id backfill: uq_users_telegram_id present = %', has_uq_index;

    IF dup_ids > 0 THEN
        RAISE EXCEPTION 'ABORT: % duplicate telegram_id group(s) after backfill', dup_ids;
    END IF;
    IF NOT has_uq_index THEN
        RAISE WARNING 'uq_users_telegram_id index absent -- verify 001 was applied';
    END IF;
END $$;

COMMIT;

-- ---------------------------------------------------------------------------
-- READ-ONLY pre-flight (run these BEFORE the migration; they change nothing):
--
--   -- how many rows still need backfilling:
--   SELECT COUNT(*) AS candidates
--   FROM users
--   WHERE telegram_id IS NULL AND telegram_chat_id IS NOT NULL;
--
--   -- would any candidate collide (must return zero rows to be safe):
--   SELECT telegram_chat_id, COUNT(*)
--   FROM users
--   WHERE telegram_id IS NULL AND telegram_chat_id IS NOT NULL
--   GROUP BY telegram_chat_id
--   HAVING COUNT(*) > 1;
--
-- ---------------------------------------------------------------------------
-- Rollback:
--   This migration only fills a previously-NULL column from data already in
--   the same row, so it is data-preserving and normally does NOT need
--   reverting. Prefer NOT to roll back at all.
--
--   Do NOT use a blanket
--       UPDATE users SET telegram_id = NULL WHERE telegram_id = telegram_chat_id;
--   as a rollback. It is unsafe: (a) it also nulls rows where a genuine, later
--   Telegram login set telegram_id to the same value that was already in
--   telegram_chat_id, re-breaking working logins; and (b) it silently nulls any
--   row where the two columns happen to coincide for unrelated reasons. A
--   rollback must revert ONLY the exact rows this run changed -- nothing else.
--
--   Two safe options, in order of preference:
--
--   1) RESTORE FROM BACKUP (preferred, authoritative):
--      Restore the `users` table (or just its telegram_id column) from the
--      pre-migration dump taken in the APPLY runbook, e.g.
--          pg_restore --data-only --table=users -d "$DATABASE_URL" \
--              users_before_003_<date>.dump
--      (into a staging table, then copy back telegram_id if a full-table
--      restore is not acceptable). This is the only rollback that cannot
--      over-revert.
--
--   2) TARGETED REVERT BY PRE-RECORDED IDs:
--      Before applying, the APPLY runbook records the exact ids that will be
--      changed:
--          SELECT id FROM users
--          WHERE telegram_id IS NULL AND telegram_chat_id IS NOT NULL;
--      Keep that id list (out of band). To roll back, null telegram_id for
--      ONLY those specific ids:
--          BEGIN;
--          UPDATE users SET telegram_id = NULL
--          WHERE id IN (<exact ids captured before APPLY>);
--          COMMIT;
--      Never substitute a column-comparison predicate for this explicit id set.
--
--   WARNING: any rollback re-breaks legacy Telegram login for those accounts;
--   only roll back if the migration itself was wrong, not merely to undo it.
-- ---------------------------------------------------------------------------
