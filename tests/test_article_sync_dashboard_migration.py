# -*- coding: utf-8 -*-
"""migrations/013_article_metadata_sync_dashboard.sql — statik xavfsizlik
testi (Phase 2C.3B).

`tests/test_article_sync_migration.py` bilan bir xil naqsh, FAQAT bitta farq
bilan: bu migratsiya `ALTER COLUMN checked_by DROP NOT NULL` ishlatadi
(§3 — 'system' actor uchun). Bu DESTRUKTIV EMAS (ma'lumot o'chmaydi, faqat
cheklov bo'shatiladi), shuning uchun umumiy "DROP " qidiruvi bu yerda
ANIQ ISTISNO bilan qo'llanadi.
"""
import os
import re
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(_REPO_ROOT, 'migrations',
                         '013_article_metadata_sync_dashboard.sql')
PERSISTENCE = os.path.join(_REPO_ROOT, 'services', 'article_sync', 'persistence.py')

_ALLOWED_DROP = 'DROP NOT NULL'


def _strip_comments(sql):
    out = []
    for line in sql.splitlines():
        idx = line.find('--')
        out.append(line if idx == -1 else line[:idx])
    return '\n'.join(out)


def _strip_allowed_drop(upper_sql):
    return upper_sql.replace(_ALLOWED_DROP, '')


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

    def test_only_alters_the_sync_table(self):
        self.assertIn('ALTER TABLE ARTICLE_METADATA_SYNCS', self.upper)
        for forbidden in ('ALTER TABLE SCIENTIFIC_ARTICLES', 'ALTER TABLE ARTICLE_AUTHORS',
                          'ALTER TABLE ARTICLE_IMPORT_BATCHES',
                          'ALTER TABLE ARTICLE_IMPORT_ITEMS'):
            self.assertNotIn(forbidden, self.upper)

    def test_every_create_is_idempotent(self):
        for statement in re.findall(r'CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)\s+(\S+)',
                                    self.upper):
            self.assertEqual(statement, 'IF')

    def test_every_column_add_is_idempotent(self):
        # `ADD COLUMN IF NOT EXISTS` — har bir ADD COLUMN shu shaklda bo'lishi kerak.
        for match in re.finditer(r'ADD COLUMN\s+(\S+)', self.upper):
            self.assertEqual(match.group(1), 'IF')

    def test_no_destructive_statements_outside_the_documented_drop_not_null(self):
        """`DROP NOT NULL` — YAGONA ruxsat etilgan "DROP". Boshqa hech qanday
        DROP/TRUNCATE/DELETE/UPDATE bo'lmasligi kerak."""
        sanitized = _strip_allowed_drop(self.upper)
        for keyword in ('DROP ', 'TRUNCATE', 'DELETE FROM', 'UPDATE '):
            self.assertNotIn(keyword, sanitized, keyword)

    def test_drop_not_null_is_the_only_drop_and_applies_to_checked_by(self):
        self.assertIn('ALTER COLUMN CHECKED_BY DROP NOT NULL', self.upper)
        self.assertEqual(self.upper.count('DROP '), 1)

    def test_rollback_is_documented_but_not_executed(self):
        self.assertIn('ROLLBACK', self.raw.upper())
        after_commit = self.raw.split('COMMIT;', 1)[1]
        for line in after_commit.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            self.assertTrue(stripped.startswith('--'), 'commitdan keyin faol SQL: %r' % line)

    def test_actor_type_column_added(self):
        self.assertIn('ACTOR_TYPE', self.upper)
        self.assertIn("DEFAULT 'ADMIN'", self.upper)

    def test_dismiss_columns_added(self):
        for column in ('DISMISSED_AT', 'DISMISSED_BY', 'DISMISSED_REASON'):
            self.assertIn(column, self.upper)

    def test_filter_flag_columns_added(self):
        for column in ('HAS_RETRACTION_CHANGE', 'HAS_CITATION_CHANGE',
                       'HAS_AUTHOR_CHANGE', 'HAS_PROVIDER_ISSUE'):
            self.assertIn(column, self.upper)


class PersistenceSchemaMatchesMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(PERSISTENCE, encoding='utf-8') as fh:
            cls.code = fh.read()

    def test_ensure_schema_creates_all_new_columns(self):
        for column in ('actor_type', 'dismissed_at', 'dismissed_by',
                       'dismissed_reason', 'has_retraction_change',
                       'has_citation_change', 'has_author_change',
                       'has_provider_issue'):
            self.assertIn(column, self.code, column)

    def test_ensure_schema_checked_by_is_nullable(self):
        self.assertIn('checked_by                  INTEGER,', self.code)
        self.assertNotIn('checked_by                  INTEGER      NOT NULL', self.code)


if __name__ == '__main__':
    unittest.main()
