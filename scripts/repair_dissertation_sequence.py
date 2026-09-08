"""Realign the dissertations SERIAL sequence before scheduled OAK imports."""

import os

import psycopg2


LOCK_SQL = "LOCK TABLE dissertations IN SHARE ROW EXCLUSIVE MODE"
SETVAL_SQL = """
    SELECT setval(
        pg_get_serial_sequence('dissertations', 'id'),
        COALESCE(MAX(id), 1),
        MAX(id) IS NOT NULL
    )
    FROM dissertations
"""


def repair_sequence(conn):
    """Lock writers briefly, align the sequence, and return its new value."""
    try:
        with conn.cursor() as cur:
            cur.execute(LOCK_SQL)
            cur.execute(SETVAL_SQL)
            row = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return int(row[0]) if row and row[0] is not None else 0


def main():
    database_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL GitHub secreti topilmadi")

    conn = psycopg2.connect(database_url, connect_timeout=15)
    try:
        sequence_value = repair_sequence(conn)
    finally:
        conn.close()

    print("dissertations.id sequence tekislandi: " + str(sequence_value))


if __name__ == "__main__":
    main()
