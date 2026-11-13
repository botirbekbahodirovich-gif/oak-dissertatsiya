-- dissertation_field_changes: every value scripts/oak_backfill_from_api.py
-- (or a later bulk fix) writes into dissertations is logged here first, in the
-- same transaction, with the old value. `undo --batch` restores old values
-- only where the row still holds the new value, then sets undone_at.
--   action: replace (opponent set from the announcement itself replaces a
--           neighbour's), fill (empty column filled), clear (neighbour's
--           opponents removed; the announcement itself has none)
-- Rollback: migrations/rollback/023_dissertation_field_changes.down.sql
BEGIN;
CREATE TABLE IF NOT EXISTS dissertation_field_changes (
    id               BIGSERIAL PRIMARY KEY,
    batch_id         TEXT NOT NULL,
    dissertation_id  INTEGER NOT NULL REFERENCES dissertations(id) ON DELETE CASCADE,
    oak_id           TEXT,
    column_name      TEXT NOT NULL,
    old_value        TEXT,
    new_value        TEXT,
    action           VARCHAR(20) NOT NULL CHECK (action IN ('replace', 'fill', 'clear')),
    reason           TEXT,
    source           TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    undone_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_dfc_batch ON dissertation_field_changes (batch_id);
CREATE INDEX IF NOT EXISTS idx_dfc_dissertation ON dissertation_field_changes (dissertation_id);
COMMIT;
