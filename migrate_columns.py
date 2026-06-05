import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()

columns = [
    ('ixtisoslik_nomi', 'TEXT'),
    ('mavzu_raqami', 'TEXT'),
    ('ilmiy_rahbar_daraja', 'TEXT'),
    ('ilmiy_kengash', 'TEXT'),
    ('ilmiy_kengash_raqami', 'TEXT'),
    ('opponent_1', 'TEXT'),
    ('opponent_2', 'TEXT'),
    ('yetakchi_tashkilot', 'TEXT'),
    ('oak_id', 'INTEGER'),
]

for col, coltype in columns:
    cur.execute(f"ALTER TABLE dissertations ADD COLUMN IF NOT EXISTS {col} {coltype}")
    print(f"OK: {col} {coltype}")

cur.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'dissertations_oak_id_key'
        ) THEN
            ALTER TABLE dissertations ADD CONSTRAINT dissertations_oak_id_key UNIQUE (oak_id);
        END IF;
    END$$;
""")
print("OK: UNIQUE constraint on oak_id")

conn.commit()
cur.close()
conn.close()
print("Migration complete.")
