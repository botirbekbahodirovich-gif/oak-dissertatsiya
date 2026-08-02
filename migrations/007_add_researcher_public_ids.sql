-- Stable public researcher identifiers for URLs such as /OLIM-A1B2C3D4E5F6.
-- Additive and idempotent: existing canonical rows are preserved.

ALTER TABLE canonical_researchers
    ADD COLUMN IF NOT EXISTS public_id VARCHAR(17);

-- Existing rows get deterministic, non-sequential identifiers.  Twelve hex
-- characters give enough room for the current and planned researcher volume.
UPDATE canonical_researchers
SET public_id = 'OLIM-' || UPPER(SUBSTRING(
    MD5('olimlar-public-id-v1:' || id::TEXT), 1, 12
))
WHERE public_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_canonical_researchers_public_id
    ON canonical_researchers (public_id);

CREATE OR REPLACE FUNCTION generate_olim_public_id()
RETURNS VARCHAR(17)
LANGUAGE plpgsql
AS $$
DECLARE
    candidate VARCHAR(17);
BEGIN
    LOOP
        candidate := 'OLIM-' || UPPER(SUBSTRING(
            MD5(RANDOM()::TEXT || CLOCK_TIMESTAMP()::TEXT || PG_BACKEND_PID()::TEXT),
            1, 12
        ));
        EXIT WHEN NOT EXISTS (
            SELECT 1 FROM canonical_researchers WHERE public_id = candidate
        );
    END LOOP;
    RETURN candidate;
END;
$$;

ALTER TABLE canonical_researchers
    ALTER COLUMN public_id SET DEFAULT generate_olim_public_id();

ALTER TABLE canonical_researchers
    ALTER COLUMN public_id SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_canonical_researcher_public_id'
          AND conrelid = 'canonical_researchers'::regclass
    ) THEN
        ALTER TABLE canonical_researchers
            ADD CONSTRAINT ck_canonical_researcher_public_id
            CHECK (public_id ~ '^OLIM-[A-F0-9]{12}$');
    END IF;
END;
$$;
