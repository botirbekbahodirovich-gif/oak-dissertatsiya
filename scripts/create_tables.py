"""
Migration helper: create PostgreSQL tables and optionally migrate data from SQLite and CSV.
Run this inside WSL where `.env` contains `DATABASE_URL`.
"""
import os
from dotenv import load_dotenv
load_dotenv()
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise SystemExit('DATABASE_URL not set in environment or .env')

import psycopg2
from psycopg2.extras import execute_values
import sqlite3
import csv

print('Connecting to', DATABASE_URL)
conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

# Create tables
cur.execute('''
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
)
''')

cur.execute('''
CREATE TABLE IF NOT EXISTS dissertations (
    id SERIAL PRIMARY KEY,
    sana TEXT,
    daraja TEXT,
    olim TEXT,
    mavzu TEXT,
    ixtisoslik TEXT,
    muassasa TEXT,
    ilmiy_rahbar TEXT,
    link TEXT,
    created_at TIMESTAMP DEFAULT NOW()
)
''')
conn.commit()
print('Tables created/ensured')

# Migrate users from local SQLite if exists
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
sqlite_path = os.path.join(BASE_DIR, 'users.db')
if os.path.exists(sqlite_path):
    print('Found local SQLite users.db, migrating users...')
    scon = sqlite3.connect(sqlite_path)
    rows = scon.execute('SELECT username, email, password_hash FROM users').fetchall()
    scon.close()
    if rows:
        vals = [(r[0], r[1], r[2]) for r in rows]
        execute_values(cur, "INSERT INTO users (username,email,password_hash) VALUES %s ON CONFLICT (username) DO NOTHING", vals)
        conn.commit()
        print(f'Migrated {len(vals)} users')

# Import CSV dissertations into DB
csv_path = os.path.join(BASE_DIR, 'data', 'dissertatsiyalar.csv')
if os.path.exists(csv_path):
    print('Importing CSV dissertations...')
    with open(csv_path, newline='', encoding='utf-8') as fh:
        reader = csv.DictReader(fh)
        vals = []
        for r in reader:
            vals.append((r.get('Sana',''), r.get('Daraja',''), r.get('Olim',''), r.get('Mavzu',''), r.get('Ixtisoslik',''), r.get('Muassasa',''), r.get('Ilmiy_rahbar',''), r.get('Link','')))
        if vals:
            execute_values(cur, "INSERT INTO dissertations (sana,daraja,olim,mavzu,ixtisoslik,muassasa,ilmiy_rahbar,link) VALUES %s", vals)
            conn.commit()
            print(f'Imported {len(vals)} dissertations')

cur.close()
conn.close()
print('Done')
