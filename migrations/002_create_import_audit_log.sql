BEGIN;

CREATE TABLE IF NOT EXISTS import_audit_log (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    file_name VARCHAR(255) NOT NULL,
    imported_rows INTEGER NOT NULL DEFAULT 0,
    result VARCHAR(20) NOT NULL CHECK (result IN ('success', 'failure')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_import_audit_created
    ON import_audit_log (created_at DESC);

COMMIT;

-- Rollback (manual):
-- BEGIN;
-- DROP TABLE IF EXISTS import_audit_log;
-- COMMIT;
