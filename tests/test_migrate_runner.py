import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scripts import migrate  # noqa: E402


class _FakeCursor:
    """Answers presence queries from a set of (kind, name[, column]) tuples."""

    def __init__(self, present):
        self.present = set(present)
        self._row = None

    def execute(self, sql, params=()):
        kind = next(k for k, q in migrate._PRESENCE_SQL.items() if q == sql)
        self._row = (1,) if (kind, *params) in self.present else None

    def fetchone(self):
        return self._row


class MigrationFilesTests(unittest.TestCase):
    def test_numbered_files_sort_before_add_files(self):
        files = migrate.migration_files()
        numbered = [f for f in files if f[:3].isdigit()]
        self.assertEqual(files[:len(numbered)], numbered)
        self.assertTrue(all(f.startswith('add_') for f in files[len(numbered):]))

    def test_no_reused_name_after_renumbering(self):
        files = migrate.migration_files()
        self.assertIn('020_institution_identity.sql', files)
        self.assertNotIn('014_institution_identity.sql', files)
        self.assertEqual(len(files), len(set(files)))

    def test_every_schema_migration_has_detectable_objects(self):
        # 003 is a data backfill (UPDATE only) and is verified by hand.
        for name in migrate.migration_files():
            if name == '003_backfill_users_telegram_id.sql':
                continue
            with open(os.path.join(migrate.MIGRATIONS_DIR, name), encoding='utf-8') as fh:
                self.assertTrue(migrate.expected_objects(fh.read()), name)


class ExpectedObjectsTests(unittest.TestCase):
    def test_tables_indexes_and_multi_column_alter(self):
        sql = """
        -- CREATE TABLE commented_out (id INT);
        CREATE TABLE IF NOT EXISTS public.Foo (id INT, note TEXT DEFAULT 'a;b');
        CREATE UNIQUE INDEX IF NOT EXISTS uq_foo ON foo(id);
        ALTER TABLE foo
            ADD COLUMN IF NOT EXISTS a INT,
            ADD COLUMN b TEXT;
        """
        self.assertEqual(migrate.expected_objects(sql), [
            ('table', 'foo'), ('index', 'uq_foo'),
            ('column', 'foo', 'a'), ('column', 'foo', 'b')])

    def test_do_blocks_and_data_statements_are_ignored(self):
        sql = """
        DO $$ BEGIN
          CREATE TABLE inside_do (id INT);
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        UPDATE users SET x = 1;
        """
        self.assertEqual(migrate.expected_objects(sql), [])

    def test_institution_identity_objects(self):
        with open(os.path.join(migrate.MIGRATIONS_DIR, '020_institution_identity.sql'),
                  encoding='utf-8') as fh:
            objects = migrate.expected_objects(fh.read())
        self.assertIn(('table', 'institutions'), objects)
        self.assertIn(('column', 'dissertations', 'institution_id'), objects)
        self.assertIn(('index', 'idx_institution_queue_status'), objects)


class DetectAndBaselineTests(unittest.TestCase):
    SQL = 'CREATE TABLE t1 (id INT); CREATE INDEX i1 ON t1(id);'

    def test_detect_verdicts(self):
        self.assertEqual(migrate.detect_file(_FakeCursor({('table', 't1'), ('index', 'i1')}), self.SQL),
                         ('present', []))
        self.assertEqual(migrate.detect_file(_FakeCursor({('table', 't1')}), self.SQL),
                         ('partial', [('index', 'i1')]))
        self.assertEqual(migrate.detect_file(_FakeCursor(set()), self.SQL)[0], 'absent')
        self.assertEqual(migrate.detect_file(_FakeCursor(set()), 'UPDATE x SET y = 1;'),
                         ('unknown', []))

    def test_baseline_sql_is_idempotent_and_creates_ledger(self):
        sql = migrate.baseline_sql([('001_a.sql', 'a' * 64)])
        self.assertIn('CREATE TABLE IF NOT EXISTS schema_migrations', sql)
        self.assertIn("VALUES ('001_a.sql', '" + 'a' * 64 + "', 'baseline', 'baseline')", sql)
        self.assertIn('ON CONFLICT (filename) DO NOTHING', sql)
        self.assertTrue(sql.startswith('--') and 'BEGIN;' in sql and sql.rstrip().endswith('COMMIT;'))

    def test_baseline_rejects_unexpected_names(self):
        with self.assertRaises(ValueError):
            migrate.baseline_sql([("x'; DROP TABLE users; --.sql", 'a' * 64)])

    def test_read_only_options_and_apply_guard(self):
        self.assertIn('default_transaction_read_only=on', migrate.READ_ONLY_OPTIONS)
        with self.assertRaises(SystemExit):
            migrate.main(['apply'])


if __name__ == '__main__':
    unittest.main()
