# -*- coding: utf-8 -*-
"""migrations/011_create_article_import_queue.sql — statik xavfsizlik testi.

`tests/test_articles_migration.py` bilan bir xil naqsh: bazaga TEGILMAYDI,
faqat SQL matnini tahlil qiladi.
"""
import os
import re
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(_REPO_ROOT, 'migrations', '011_create_article_import_queue.sql')
PERSISTENCE = os.path.join(_REPO_ROOT, 'services', 'article_import', 'persistence.py')

TABLES = ('article_import_batches', 'article_import_items')


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

    def test_creates_every_expected_table(self):
        for table in TABLES:
            self.assertIn('CREATE TABLE IF NOT EXISTS %s' % table.upper(), self.upper, table)

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
                          'ALTER TABLE scientific_fields',
                          'ALTER TABLE article_scientific_fields'):
            self.assertNotIn(forbidden.upper(), self.upper)

    def test_references_scientific_articles_via_fk_only(self):
        self.assertIn('REFERENCES SCIENTIFIC_ARTICLES(ID)', self.upper)

    def test_has_indexes_on_expected_query_paths(self):
        for column in ('batch_id', 'normalized_doi', 'status', 'created_by',
                       'created_at', 'existing_article_id', 'created_article_id'):
            self.assertIn(column, self.code, column)


class PersistenceSchemaMatchesMigrationTests(unittest.TestCase):
    """`services/article_import/persistence.py:ensure_schema` migratsiya bilan
    bir xil jadval/ustun nomlarini yaratishi kerak (server o'zini-o'zi
    migratsiya qiladi — ikkisi ajralib ketmasligi kerak)."""

    @classmethod
    def setUpClass(cls):
        with open(PERSISTENCE, encoding='utf-8') as fh:
            cls.code = fh.read()

    def test_ensure_schema_creates_same_tables(self):
        for table in TABLES:
            self.assertIn('CREATE TABLE IF NOT EXISTS %s' % table, self.code)

    def test_ensure_schema_has_no_destructive_statements(self):
        upper = self.code.upper()
        for keyword in ('DROP TABLE', 'TRUNCATE', "'DELETE FROM ARTICLE_IMPORT"):
            self.assertNotIn(keyword, upper)


if __name__ == '__main__':
    unittest.main()
