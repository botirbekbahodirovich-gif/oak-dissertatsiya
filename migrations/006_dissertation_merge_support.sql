-- ============================================================================
-- Migration 006 — duplicate-merge support
-- ============================================================================
-- Two additive changes, both required by scripts/merge_duplicates.py:
--
--   1. dissertations.merged_into_id / merged_at — a nullable, self-referencing
--      marker. A merge never deletes the losing record; it sets
--      merged_into_id to point at the surviving record instead, so the row
--      (and anything a foreign key elsewhere still points at) stays resolvable
--      and reversible. NULL means "not merged, this is a live record".
--
--   2. data_quality_fix_log — one row per field value actually rewritten by
--      the merge (before/after), so every write has an audit trail and a
--      rollback source. This is the SAME mechanism `apply_data_quality_fixes.py`
--      is meant to use once migration 005 (`feat/data-quality-fix-log`,
--      not yet on main as of this writing) lands. The schema here is written
--      to match that design so the two migrations reconcile cleanly whichever
--      merges first: CREATE TABLE IF NOT EXISTS makes the second one a no-op.
--      Numbered 006 rather than 005 specifically to avoid a same-numbered,
--      different-content migration file existing on two branches at once —
--      if 005 lands with an identical table, this file becomes redundant and
--      can be deleted in a follow-up; nothing below depends on that happening
--      in any particular order.
--
-- SAFETY PROPERTIES (verified by test_data_quality_merge_migration.py):
--   * Wrapped in a single transaction (BEGIN/COMMIT) — all-or-nothing.
--   * Idempotent — every object uses IF NOT EXISTS, so re-running is a no-op.
--   * The only change to `dissertations` is two ADD COLUMN IF NOT EXISTS
--     statements, both nullable, both defaulting to NULL — no existing row
--     is touched, no NOT NULL is introduced, no data is read or rewritten.
--   * No DROP, no TRUNCATE, no DELETE, no UPDATE anywhere in this file.
--
-- COLUMN NOTES (data_quality_fix_log)
--   run_id      — groups one CLI invocation's writes (data_quality_runs,
--                 run_type='merge' for this script, run_type='fix' for the
--                 field-level auto-fixer). ON DELETE RESTRICT: deleting a run
--                 must not silently erase the audit trail of writes it made.
--   record_id / table_name / column_name — polymorphic, no hard FK (same
--                 reasoning as data_quality_issues.record_id in migration 004).
--   value_before/value_after — TEXT snapshots; NULL means the column really
--                 was NULL, never the string 'None'.
--   applied_by  — free-text actor label ('cli:...', 'admin:<id>'), not a FK,
--                 so it survives the deletion of a user account.
--
-- ROLLBACK (of this migration — safe, additive only):
--     BEGIN;
--       DROP TABLE IF EXISTS data_quality_fix_log;
--       DROP INDEX IF EXISTS idx_dissertations_merged_into;
--       ALTER TABLE dissertations DROP COLUMN IF EXISTS merged_at;
--       ALTER TABLE dissertations DROP COLUMN IF EXISTS merged_into_id;
--     COMMIT;
--   (Run scripts/rollback_data_quality_fixes.py or the equivalent merge
--   rollback FIRST if any recorded write still needs to be reverted — dropping
--   this table only removes the audit trail, it does not undo anything.)
-- ============================================================================

BEGIN;

-- ── dissertations: merge marker ─────────────────────────────────────────────
ALTER TABLE dissertations
    ADD COLUMN IF NOT EXISTS merged_into_id INTEGER
        REFERENCES dissertations (id) ON DELETE SET NULL;
ALTER TABLE dissertations
    ADD COLUMN IF NOT EXISTS merged_at TIMESTAMPTZ;

-- Partial index: only merged-away rows are ever looked up by this column (to
-- exclude them from listings), so a full index would be dead weight.
CREATE INDEX IF NOT EXISTS idx_dissertations_merged_into
    ON dissertations (merged_into_id) WHERE merged_into_id IS NOT NULL;

-- ── data_quality_fix_log ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS data_quality_fix_log (
    id             BIGSERIAL     PRIMARY KEY,
    run_id         BIGINT        NOT NULL
                       REFERENCES data_quality_runs (id) ON DELETE RESTRICT,
    issue_id       BIGINT        REFERENCES data_quality_issues (id) ON DELETE SET NULL,
    record_id      INTEGER       NOT NULL,
    table_name     VARCHAR(64)   NOT NULL DEFAULT 'dissertations',
    column_name    VARCHAR(64)   NOT NULL,
    value_before   TEXT,
    value_after    TEXT,
    issue_code     VARCHAR(48),
    applied_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    applied_by     VARCHAR(128)  NOT NULL DEFAULT 'cli',
    rolled_back_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_dq_fixlog_run
    ON data_quality_fix_log (run_id);
CREATE INDEX IF NOT EXISTS idx_dq_fixlog_record
    ON data_quality_fix_log (record_id);
-- Hot path for a future rollback CLI: "what is still revertible in this run".
CREATE INDEX IF NOT EXISTS idx_dq_fixlog_pending
    ON data_quality_fix_log (run_id)
    WHERE rolled_back_at IS NULL;

COMMIT;
