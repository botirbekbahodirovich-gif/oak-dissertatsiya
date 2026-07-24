"""Create or reset an administrator account without exposing its password."""
import argparse
import getpass
import hmac
import os
import sys

import bcrypt
import psycopg2
from dotenv import load_dotenv


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="admin")
    parser.add_argument("--email", default="admin@olimlar.uz")
    parser.add_argument("--reset", action="store_true")
    return parser


def resolve_admin_password(environ=None, prompt=None, stdin=None):
    """Return an environment or interactively supplied non-empty password."""
    environ = os.environ if environ is None else environ
    password = environ.get("ADMIN_PASSWORD", "")
    if password:
        return password

    stdin = sys.stdin if stdin is None else stdin
    if prompt is None:
        if not stdin.isatty():
            raise RuntimeError(
                "ADMIN_PASSWORD is required when no interactive terminal is available"
            )
        prompt = getpass.getpass

    password = prompt("Admin password: ")
    if not password:
        raise RuntimeError("Admin password must not be empty")
    confirmation = prompt("Confirm admin password: ")
    if not hmac.compare_digest(password, confirmation):
        raise RuntimeError("Admin password confirmation does not match")
    return password


def main(argv=None, environ=None, prompt=None, stdin=None):
    if environ is None:
        load_dotenv()
        environ = os.environ

    args = build_parser().parse_args(argv)
    database_url = environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL not set in environment or .env")
    password = resolve_admin_password(environ, prompt=prompt, stdin=stdin)
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    conn = psycopg2.connect(database_url)
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM users WHERE username = %s", (args.user,))
        existing = cur.fetchone()
        if existing:
            if args.reset:
                cur.execute(
                    "UPDATE users SET password_hash = %s WHERE username = %s",
                    (password_hash, args.user),
                )
                conn.commit()
                print(f"Password reset for '{args.user}'.")
            else:
                print(
                    f"User '{args.user}' already exists. "
                    "Use --reset to update the password."
                )
        else:
            cur.execute(
                "INSERT INTO users (username, email, password_hash) "
                "VALUES (%s, %s, %s)",
                (args.user, args.email, password_hash),
            )
            conn.commit()
            print(f"Admin user '{args.user}' created.")
    finally:
        cur.close()
        conn.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        raise SystemExit(str(exc))
