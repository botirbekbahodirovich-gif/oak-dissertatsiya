-- ============================================================================
-- Migration 005 — Data Quality fix log (audit trail + rollback source)
-- ============================================================================
-- Creates ONE additive table:
--     data_quality_fix_log     -- one row per field value actually rewritten
--                                 by scripts/apply_data_quality_fixes.py
--
-- Every auto-fix writes its before/after pair here IN THE SAME TRANSACTION as
-- the UPDATE it describes, so the log can never disagree with the data:
-- either both the fix and its log row exist, or neither does.
-- scripts/rollback_data_quality_fixes.py reads this table to put the old
-- values back and stamps rolled_back_at on the rows it reverted.
--
-- SAFETY PROPERTIES (verified by test_data_quality_fix_log.py):
--   * Wrapped in a single transaction (BEGIN/COMMIT) — all-or-nothing.
--   * Idempotent — every object uses IF NOT EXISTS, so re-running is a no-op.
--   * NON-destructive — no DROP, no ALTER of existing tables, no UPDATE/DELETE
--     of production data. `dissertations`, `users` and the migration-004 tables
--     are NOT touched.
--
-- COLUMN NOTES
--   run_id      — the *fix* run (data_quality_runs.run_type = 'fix') created by
--                 the fixer CLI, i.e. one batch of applied fixes. It is the unit
--                 of rollback: `rollback_data_quality_fixes.py --run-id N`.
--                 ON DELETE RESTRICT is deliberate: unlike issues (which CASCADE
--                 because they are derived data), these rows describe writes
--                 that really happened to production data. Deleting a run must
--                 not silently erase that audit trail or orphan the rollback key.
--   issue_id    — the data_quality_issues row that motivated the fix, so a
--                 rollback can reopen it. Nullable / ON DELETE SET NULL: the
--                 audit entry outlives the derived issue row.
--   record_id   — polymorphic like data_quality_issues.record_id (no hard FK);
--                 it is qualified by table_name instead.
--   table_name /
--   column_name — the exact target of the UPDATE. Application code validates
--                 both against the fixer allowlist (dissertations; daraja/link)
--                 before writing here and before restoring from here.
--   value_before/
--   value_after — TEXT snapshots. NULL means the column really was NULL (it is
--                 never the string 'None'). Rollback compares the CURRENT value
--                 against value_after and skips the row if they differ, i.e.
--                 if someone changed the field by hand after the fix.
--   applied_by  — free-text actor label ('cli:<user>', 'admin:<id>'), not a FK:
--                 fixes normally run from a CLI with no logged-in user, and the
--                 label must survive the deletion of a user account.
--
-- ROLLBACK (of this migration — safe, the table holds only audit rows; do the
-- DATA rollback with rollback_data_quality_fixes.py FIRST if you still need it):
--     BEGIN;
--       DROP TABLE IF EXISTS data_quality_fix_log;
--     COMMIT;
-- ============================================================================

BEGIN;

-- ── data_quality_fix_log ───────────────────────────────────────────────────
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
-- Hot path for the rollback CLI: "what is still revertible in this run".
CREATE INDEX IF NOT EXISTS idx_dq_fixlog_pending
    ON data_quality_fix_log (run_id)
    WHERE rolled_back_at IS NULL;

COMMIT;
