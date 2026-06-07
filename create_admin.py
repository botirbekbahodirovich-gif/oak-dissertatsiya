"""
Run once on a fresh deployment to create (or reset) the admin user.
  python3 create_admin.py
  python3 create_admin.py --reset        # reset existing admin password
  python3 create_admin.py --user myname --password mypass
"""
import os, sys, argparse
from dotenv import load_dotenv
load_dotenv()

import bcrypt
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    sys.exit("DATABASE_URL not set in environment or .env")

parser = argparse.ArgumentParser()
parser.add_argument("--user",     default="admin")
parser.add_argument("--password", default="admin123")
parser.add_argument("--email",    default="admin@olimlar.uz")
parser.add_argument("--reset",    action="store_true")
args = parser.parse_args()

pw_hash = bcrypt.hashpw(args.password.encode(), bcrypt.gensalt()).decode()

conn = psycopg2.connect(DATABASE_URL)
cur  = conn.cursor()

cur.execute("SELECT id FROM users WHERE username = %s", (args.user,))
existing = cur.fetchone()

if existing:
    if args.reset:
        cur.execute("UPDATE users SET password_hash = %s WHERE username = %s",
                    (pw_hash, args.user))
        conn.commit()
        print(f"Password reset for '{args.user}'  ->  {args.password}")
    else:
        print(f"User '{args.user}' already exists. Use --reset to update password.")
else:
    cur.execute(
        "INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s)",
        (args.user, args.email, pw_hash)
    )
    conn.commit()
    print(f"Admin user '{args.user}' created  ->  password: {args.password}")

cur.close()
conn.close()
