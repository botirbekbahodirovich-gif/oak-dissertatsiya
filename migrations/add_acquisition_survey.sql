-- Acquisition-source survey migration — EXTENDS the existing `users` table with
-- four nullable columns used by the one-time post-signup "Bizni qayerdan
-- bildingiz?" modal (marketing attribution). All columns are nullable and
-- backwards compatible, so this runs with zero downtime while old code is live.
--
-- Idempotent: safe to run repeatedly (ADD COLUMN IF NOT EXISTS). The same
-- statements also run lazily from blueprints/acquisition_survey.py
-- (_ensure_schema) on first request, so the server self-migrates even if this
-- file is never executed by hand.
--   Manual run:  psql "$DATABASE_URL" -f migrations/add_acquisition_survey.sql

-- Where the user says they found olimlar.uz. Constrained by app-level validation
-- to the allowed enum (kept as a plain VARCHAR so new channels discovered via the
-- 'other' free-text can be promoted without an ALTER):
--   telegram | youtube | instagram | friend_colleague | advisor
--   | google_search | university | other
ALTER TABLE users ADD COLUMN IF NOT EXISTS acquisition_source VARCHAR(32);

-- Free-text detail, populated only when acquisition_source = 'other'.
ALTER TABLE users ADD COLUMN IF NOT EXISTS acquisition_source_other TEXT;

-- Set when the modal is dismissed without an answer (skip / ×), so it is not
-- shown again. NULL = never shown/skipped.
ALTER TABLE users ADD COLUMN IF NOT EXISTS acquisition_survey_shown_at TIMESTAMP;

-- Set when the user submits an answer. NULL = not answered yet.
ALTER TABLE users ADD COLUMN IF NOT EXISTS acquisition_survey_answered_at TIMESTAMP;

-- Aggregation index for the admin "Foydalanuvchi manbalari" distribution query.
CREATE INDEX IF NOT EXISTS idx_users_acquisition_source ON users(acquisition_source);
