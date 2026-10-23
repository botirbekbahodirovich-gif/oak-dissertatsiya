"""SQL migration ledger for migrations/*.sql (schema_migrations).

Until now migrations were applied by hand (psql -f) or lazily by each
blueprint's _ensure_schema(), so nothing recorded what a database has.
This script keeps that record in schema_migrations (one row per file,
keyed by file name, with a sha256 of its contents).

Commands (DATABASE_URL from the environment, else .env):
  status            read-only: applied / pending / checksum-changed per file
  detect            read-only: checks the catalog for the objects each file
                    creates (tables, indexes, columns, views, functions)
  baseline          read-only: prints SQL that records already-present files
                    as method='baseline'; review it and run it by hand
  apply --yes       runs pending files in order and records each one

status/detect/baseline open the connection with
default_transaction_read_only=on and never write. A database without the
schema_migrations table is reported as "not initialised"; `apply` and the
baseline SQL create it.

Duplicate number prefixes (two 006_*) are fine: the file name is the key
and order is plain sorted order - numbered files first, then add_*.sql.
"""
import argparse
import getpass
import hashlib
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(ROOT, 'migrations')

LEDGER_DDL = """CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    checksum    CHAR(64) NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    applied_by  TEXT,
    method      VARCHAR(20) NOT NULL DEFAULT 'applied'
                CHECK (method IN ('applied', 'baseline'))
);"""

READ_ONLY_OPTIONS = '-c default_transaction_read_only=on -c statement_timeout=15000'


# ── files ────────────────────────────────────────────────────────────────

def migration_files(directory=MIGRATIONS_DIR):
    """Sorted *.sql names: numbered (NNN_*) first, then the rest (add_*)."""
    names = [n for n in os.listdir(directory) if n.endswith('.sql')]
    numbered = sorted(n for n in names if re.match(r'^\d{3}_', n))
    other = sorted(n for n in names if n not in numbered)
    return numbered + other


def checksum(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# ── object detection ─────────────────────────────────────────────────────

_IDENT = r'(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_$]*)'
_QUALIFIED = rf'(?:{_IDENT}\.)?({_IDENT})'


def _strip_sql(sql):
    """Drop comments, string literals and dollar-quoted bodies (DO $$ ... $$)."""
    sql = re.sub(r'/\*.*?\*/', ' ', sql, flags=re.S)
    sql = re.sub(r'--[^\n]*', ' ', sql)
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    sql = re.sub(r'(\$[A-Za-z_]*\$).*?\1', ' ', sql, flags=re.S)
    return sql


def _unquote(name):
    return name[1:-1] if name.startswith('"') else name.lower()


def expected_objects(sql):
    """Objects a migration creates, as (kind, name[, column]) tuples.

    kind: table | index | column | view | function. Statements that only
    change data (UPDATE/INSERT) or add constraints yield nothing.
    """
    found = []
    for stmt in _strip_sql(sql).split(';'):
        s = ' '.join(stmt.split())
        m = re.match(rf'(?i)CREATE\s+(?:UNLOGGED\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{_QUALIFIED}', s)
        if m:
            found.append(('table', _unquote(m.group(1))))
            continue
        m = re.match(rf'(?i)CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?{_QUALIFIED}', s)
        if m:
            found.append(('index', _unquote(m.group(1))))
            continue
        m = re.match(rf'(?i)CREATE\s+(?:OR\s+REPLACE\s+)?(?:MATERIALIZED\s+)?VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?{_QUALIFIED}', s)
        if m:
            found.append(('view', _unquote(m.group(1))))
            continue
        m = re.match(rf'(?i)CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+{_QUALIFIED}', s)
        if m:
            found.append(('function', _unquote(m.group(1))))
            continue
        m = re.match(rf'(?i)ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?{_QUALIFIED}\s+(.*)', s)
        if m:
            table = _unquote(m.group(1))
            for col in re.findall(rf'(?i)ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?({_IDENT})', m.group(2)):
                found.append(('column', table, _unquote(col)))
    return found


_PRESENCE_SQL = {
    'table': "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = %s",
    'index': "SELECT 1 FROM pg_indexes WHERE schemaname = current_schema() AND indexname = %s",
    'view': "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = current_schema() AND c.relname = %s AND c.relkind IN ('v', 'm')",
    'function': "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = current_schema() AND p.proname = %s",
    'column': "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
              "AND table_name = %s AND column_name = %s",
}


def detect_file(cur, sql):
    """('present' | 'partial' | 'absent' | 'unknown', [missing objects])."""
    objects = expected_objects(sql)
    if not objects:
        return 'unknown', []
    missing = []
    for obj in objects:
        cur.execute(_PRESENCE_SQL[obj[0]], obj[1:])
        if cur.fetchone() is None:
            missing.append(obj)
    if not missing:
        return 'present', []
    return ('absent' if len(missing) == len(objects) else 'partial'), missing


# ── ledger ───────────────────────────────────────────────────────────────

def ledger_exists(cur):
    cur.execute("SELECT to_regclass('schema_migrations') IS NOT NULL")
    return cur.fetchone()[0]


def applied_rows(cur):
    if not ledger_exists(cur):
        return None
    cur.execute('SELECT filename, checksum, method FROM schema_migrations')
    return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def baseline_sql(entries, applied_by='baseline'):
    """SQL recording [(filename, checksum)] as already applied. Idempotent."""
    lines = ['-- schema_migrations baseline: records files whose objects already exist.',
             '-- Generated by scripts/migrate.py baseline. Review before running.',
             'BEGIN;', LEDGER_DDL]
    for name, digest in entries:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError(f'unexpected migration name/checksum: {name!r}')
        lines.append(
            "INSERT INTO schema_migrations (filename, checksum, applied_by, method) "
            f"VALUES ('{name}', '{digest}', '{applied_by}', 'baseline') "
            'ON CONFLICT (filename) DO NOTHING;')
    lines.append('COMMIT;')
    return '\n'.join(lines) + '\n'


# ── commands ─────────────────────────────────────────────────────────────

def _connect(read_only):
    import psycopg2
    if not os.environ.get('DATABASE_URL'):
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, '.env'))
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise SystemExit('DATABASE_URL is not set.')
    kwargs = {'options': READ_ONLY_OPTIONS} if read_only else {}
    return psycopg2.connect(url, **kwargs)


def _read(name):
    with open(os.path.join(MIGRATIONS_DIR, name), encoding='utf-8') as fh:
        return fh.read()


def cmd_status(cur, files):
    rows = applied_rows(cur)
    if rows is None:
        print('schema_migrations: not initialised (run `detect`, then the baseline SQL)')
        rows = {}
    pending = 0
    for name in files:
        if name not in rows:
            state = 'pending'
            pending += 1
        elif rows[name][0] != checksum(os.path.join(MIGRATIONS_DIR, name)):
            state = f'CHANGED ({rows[name][1]})'
        else:
            state = rows[name][1]
        print(f'{state:<22} {name}')
    for name in sorted(set(rows) - set(files)):
        print(f'{"NO FILE":<22} {name}')
    print(f'{pending} pending')


def cmd_detect(cur, files):
    for name in files:
        verdict, missing = detect_file(cur, _read(name))
        detail = ', '.join('.'.join(m[1:]) for m in missing[:5])
        print(f'{verdict:<8} {name}' + (f'  missing: {detail}' if detail else ''))


def cmd_baseline(cur, files, include):
    entries = []
    for name in files:
        verdict, _ = detect_file(cur, _read(name))
        if verdict == 'present' or name in include:
            entries.append((name, checksum(os.path.join(MIGRATIONS_DIR, name))))
        else:
            print(f'-- skipped ({verdict}): {name}', file=sys.stderr)
    sys.stdout.write(baseline_sql(entries))


def cmd_apply(conn, files):
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(LEDGER_DDL)
    rows = applied_rows(cur)
    who = getpass.getuser()
    for name in files:
        if name in rows:
            continue
        print(f'applying {name} ...', flush=True)
        cur.execute(_read(name))
        cur.execute(
            'INSERT INTO schema_migrations (filename, checksum, applied_by, method) '
            "VALUES (%s, %s, %s, 'applied')",
            (name, checksum(os.path.join(MIGRATIONS_DIR, name)), who))
    print('done')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('command', nargs='?', default='status',
                        choices=['status', 'detect', 'baseline', 'apply'])
    parser.add_argument('--yes', action='store_true', help='required for apply')
    parser.add_argument('--include', action='append', default=[],
                        help='baseline: also record this file (e.g. a data-only migration '
                             'verified by hand); repeatable')
    args = parser.parse_args(argv)
    files = migration_files()

    if args.command == 'apply':
        if not args.yes:
            raise SystemExit('apply writes to the database; pass --yes.')
        conn = _connect(read_only=False)
        try:
            cmd_apply(conn, files)
        finally:
            conn.close()
        return

    conn = _connect(read_only=True)
    try:
        cur = conn.cursor()
        if args.command == 'status':
            cmd_status(cur, files)
        elif args.command == 'detect':
            cmd_detect(cur, files)
        else:
            cmd_baseline(cur, files, set(args.include))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
