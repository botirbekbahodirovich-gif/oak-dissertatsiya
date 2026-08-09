# -*- coding: utf-8 -*-
"""migrations/016_article_discovery_batch_idempotency.sql — statik
xavfsizlik + parity testi (Phase 2D.1.1, §4, §12).
"""
import os
import re
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(_REPO_ROOT, 'migrations',
                         '016_article_discovery_batch_idempotency.sql')
PERSISTENCE = os.path.join(_REPO_ROOT, 'services', 'article_import', 'persistence.py')

_PREV_MIGRATION = os.path.join(_REPO_ROOT, 'migrations', '015_article_discovery.sql')


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

    def test_015_immediately_follows_014(self):
        self.assertTrue(os.path.exists(_PREV_MIGRATION))
        self.assertTrue(os.path.exists(MIGRATION))

    def test_runs_in_a_single_transaction(self):
        self.assertIn('BEGIN;', self.upper)
        self.assertIn('COMMIT;', self.upper)

    def test_adds_source_request_id_column(self):
        self.assertIn('source_request_id', self.code)

    def test_column_is_nullable(self):
        """NOT NULL bo'lsa mavjud CSV/DOI-list batch'lariga (ular hech qachon
        request_id bermaydi) ALTER TABLE muvaffaqiyatsiz bo'lardi."""
        match = re.search(r'ADD COLUMN IF NOT EXISTS SOURCE_REQUEST_ID[^\n]*', self.upper)
        self.assertIsNotNone(match, 'ADD COLUMN qatori topilmadi')
        self.assertNotIn('NOT NULL', match.group(0))

    def test_every_add_column_is_idempotent(self):
        for statement in re.findall(r'ADD\s+COLUMN\s+(\S+)', self.upper):
            self.assertEqual(statement, 'IF')

    def test_every_create_is_idempotent(self):
        for statement in re.findall(
                r'CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)\s+(\S+)', self.upper):
            self.assertEqual(statement, 'IF')

    def test_creates_no_new_table(self):
        self.assertNotIn('CREATE TABLE', self.upper)

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

    def test_only_touches_article_import_batches(self):
        for forbidden in ('ALTER TABLE scientific_articles', 'ALTER TABLE article_authors',
                          'ALTER TABLE article_import_items',
                          'ALTER TABLE article_metadata_syncs'):
            self.assertNotIn(forbidden.upper(), self.upper)

    def test_index_is_scoped_to_discovery_source_type(self):
        """CSV/DOI-list import (source_request_id doim NULL) bu index'ga
        umuman kirmasligi kerak — faqat 'discovery' + NOT NULL."""
        self.assertIn("source_type = 'discovery'", self.code)
        self.assertIn('source_request_id IS NOT NULL', self.code)

    def test_unique_index_targets_source_request_id(self):
        self.assertIn('uq_article_import_batches_discovery_request', self.code)
        self.assertRegex(
            self.upper,
            r'CREATE UNIQUE INDEX IF NOT EXISTS UQ_ARTICLE_IMPORT_BATCHES_DISCOVERY_REQUEST\s+'
            r'ON ARTICLE_IMPORT_BATCHES \(SOURCE_REQUEST_ID\)')


class PersistenceParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(PERSISTENCE, encoding='utf-8') as fh:
            cls.code = fh.read()
        with open(MIGRATION, encoding='utf-8') as fh:
            cls.migration_code = fh.read()

    def test_ensure_schema_adds_same_column(self):
        self.assertIn('source_request_id', self.code)

    def test_ensure_schema_creates_same_index(self):
        self.assertIn('uq_article_import_batches_discovery_request', self.code)

    def test_ensure_schema_has_no_destructive_statements(self):
        upper = self.code.upper()
        for keyword in ('DROP TABLE', 'DROP COLUMN', 'TRUNCATE'):
            self.assertNotIn(keyword, upper)

    def test_ensure_schema_predicate_matches_migration(self):
        def _predicate(code):
            match = re.search(
                r'discovery_request\s+ON\s+article_import_batches\s+\(source_request_id\)\s+'
                r'WHERE\s+(.*?)\n', code, re.S)
            self.assertIsNotNone(match, 'index topilmadi')
            return re.sub(r'\s+', ' ', match.group(1)).strip().rstrip(';').strip().rstrip('"').strip()

        self.assertEqual(_predicate(self.code), _predicate(self.migration_code))


if __name__ == '__main__':
    unittest.main()
