-- Archive scholar photos (old Supabase "avatars" bucket, re-encoded to
-- 256x256 WebP under static/olim_photos/). Additive only.
-- name_norm = utils.name_norm.strict_key(scholar name); file = <sha1[:12]>.webp
-- match_type: how source_file was matched to the name (aniq/normal/fuzzy).
-- Filled by scripts/load_olim_photos.py. Read by utils/olim_photos.py.
-- Rollback: migrations/rollback/022_olim_photos.down.sql
BEGIN;
CREATE TABLE IF NOT EXISTS olim_photos (
 name_norm TEXT PRIMARY KEY,
 file VARCHAR(64) NOT NULL,
 match_type VARCHAR(20) NOT NULL CHECK (match_type IN ('aniq', 'normal', 'fuzzy')),
 source_file TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMIT;
