# -*- coding: utf-8 -*-
"""migrations/013_article_metadata_sync_scheduler.sql — statik xavfsizlik
testi (Phase 2C.3C). `tests/test_article_sync_migration.py` bilan bir xil
naqsh: bazaga TEGILMAYDI, faqat SQL matnini tahlil qiladi.
"""
import os
import re
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(_REPO_ROOT, 'migrations',
                         '013_article_metadata_sync_scheduler.sql')
PERSISTENCE = os.path.join(_REPO_ROOT, 'services', 'article_sync',
                           'scheduler_persistence.py')


def _strip_comments(sql):
    out = []
    for line in sql.splitlines():
        idx = line.find('--')
        out.append(line if idx == -1 else line[:idx])
    return '\n'.join(out)


class MigrationSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(MIGRATION, encoding='utf-8') as fh:
            cls.raw = fh.read()
        cls.code = _strip_comments(cls.raw)
        cls.upper = cls.code.upper()

    def test_runs_in_a_single_transaction(self):
        self.assertIn('BEGIN;', self.upper)
        self.assertIn('COMMIT;', self.upper)

    def test_creates_expected_table(self):
        self.assertIn('CREATE TABLE IF NOT EXISTS ARTICLE_METADATA_SYNC_RUNS',
                      self.upper)

    def test_every_create_is_idempotent(self):
        for statement in re.findall(r'CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)\s+(\S+)',
                                    self.upper):
            self.assertEqual(statement, 'IF')

    def test_no_destructive_statements_outside_comments(self):
        for keyword in ('DROP ', 'TRUNCATE', 'DELETE FROM', 'UPDATE '):
            self.assertNotIn(keyword, self.upper, keyword)

    def test_rollback_is_documented_but_not_executed(self):
        self.assertIn('ROLLBACK', self.raw.upper())
        after_commit = self.raw.split('COMMIT;', 1)[1]
        for line in after_commit.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            self.assertTrue(stripped.startswith('--'), 'commitdan keyin faol SQL: %r' % line)

    def test_does_not_touch_existing_article_tables(self):
        for forbidden in ('ALTER TABLE scientific_articles', 'ALTER TABLE article_authors',
                          'ALTER TABLE article_metadata_syncs',
                          'ALTER TABLE article_import_batches',
                          'ALTER TABLE article_import_items'):
            self.assertNotIn(forbidden.upper(), self.upper)

    def test_has_indexes_on_expected_query_paths(self):
        for column in ('started_at', 'status'):
            self.assertIn(column, self.code, column)


class PersistenceSchemaMatchesMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(PERSISTENCE, encoding='utf-8') as fh:
            cls.code = fh.read()

    def test_ensure_schema_creates_same_table(self):
        self.assertIn('CREATE TABLE IF NOT EXISTS article_metadata_sync_runs', self.code)

    def test_ensure_schema_has_no_destructive_statements(self):
        upper = self.code.upper()
        for keyword in ('DROP TABLE', 'TRUNCATE'):
            self.assertNotIn(keyword, upper)


if __name__ == '__main__':
    unittest.main()
