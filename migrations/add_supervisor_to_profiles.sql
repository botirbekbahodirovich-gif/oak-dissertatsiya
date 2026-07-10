-- "Mening dissertatsiya xaritam" migration — EXTENDS the existing
-- `olim_profiles` table (the canonical home of scholar attributes) with a
-- single nullable column that stores the supervisor the researcher wants to
-- explore on their personal dissertation map.
--
-- This is distinct from the existing `advisor_name` column (the scholar's OWN
-- PhD advisor, auto-filled on claim): `supervisor_preference` is a chosen
-- lens — the ilmiy rahbar whose students / directions / free niches the user
-- browses. Reads fall back to `advisor_name` when this is empty, so a scholar
-- who already recorded an advisor sees their map immediately.
--
-- Idempotent: safe to run repeatedly (ADD COLUMN IF NOT EXISTS). The same
-- statement also runs lazily from blueprints/xarita.py (_ensure_schema) on the
-- first request, so the server self-migrates even if this file is never run by
-- hand.
--   Manual run:  psql "$DATABASE_URL" -f migrations/add_supervisor_to_profiles.sql

ALTER TABLE olim_profiles
    ADD COLUMN IF NOT EXISTS supervisor_preference VARCHAR(200);
