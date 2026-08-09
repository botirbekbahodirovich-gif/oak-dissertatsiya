# -*- coding: utf-8 -*-
"""migrations/008_create_article_catalog.sql uchun statik xavfsizlik tekshiruvi.

Bazaga TEGILMAYDI — SQL matni tahlil qilinadi va spetsifikatsiya talab qilgan
invariantlar tasdiqlanadi (tranzaksiya, idempotentlik, buzuvchi buyruqlar yo'q,
kutilgan jadvallar va indekslar). DB darajasidagi qo'llash staging'da
tekshiriladi (tests/test_articles_dbgated.py, TEST_DATABASE_URL bilan).

Alohida tekshiruv: `blueprints/articles.py:_ensure_schema` migratsiya bilan bir
xil jadvallarni yaratadi — ikkisi ajralib ketmasligi kerak, chunki server
birinchi so'rovda o'zini-o'zi migratsiya qiladi.
"""
import os
import re
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(_REPO_ROOT, 'migrations', '008_create_article_catalog.sql')
BLUEPRINT = os.path.join(_REPO_ROOT, 'blueprints', 'articles.py')

TABLES = ('scientific_fields', 'scientific_articles', 'article_authors',
          'article_scientific_fields')


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
        # Rollback bloki faqat izohda bo'lishi kerak.
        self.assertNotIn('DROP TABLE', self.code.upper())

    def test_no_existing_table_is_altered(self):
        self.assertNotIn('ALTER TABLE', self.upper)

    def test_no_seed_data_in_the_migration(self):
        self.assertNotIn('INSERT INTO', self.upper)

    def test_doi_is_unique_only_for_non_null_rows(self):
        self.assertRegex(
            self.code,
            r'CREATE UNIQUE INDEX IF NOT EXISTS uq_scientific_articles_doi\s+'
            r'ON scientific_articles \(doi\) WHERE doi IS NOT NULL')

    def test_only_one_primary_field_per_article_is_enforced_in_the_database(self):
        self.assertRegex(
            self.code,
            r'CREATE UNIQUE INDEX IF NOT EXISTS uq_article_primary_field\s+'
            r'ON article_scientific_fields \(article_id\) WHERE is_primary')

    def test_field_deletion_cannot_orphan_children_or_articles(self):
        self.assertIn('REFERENCES scientific_fields(id) ON DELETE RESTRICT', self.code)

    def test_article_children_are_cascade_deleted(self):
        self.assertEqual(
            self.code.count('REFERENCES scientific_articles(id) ON DELETE CASCADE'), 2)

    def test_indexed_columns_the_spec_requires(self):
        for column in ('slug', 'doi', 'publication_year', 'publication_status',
                       'verification_status', 'language', 'journal_name', 'created_at'):
            self.assertIn(column, self.code, column)

    def test_users_are_not_foreign_keyed(self):
        # users legacy SQLite'da — FK qo'yilsa migratsiya serverda yiqiladi.
        self.assertNotIn('REFERENCES users', self.code)


class SchemaParityTests(unittest.TestCase):
    """_ensure_schema migratsiyadan ajralib ketmasligi kerak."""

    @classmethod
    def setUpClass(cls):
        with open(MIGRATION, encoding='utf-8') as fh:
            cls.migration = _strip_comments(fh.read())
        with open(BLUEPRINT, encoding='utf-8') as fh:
            cls.blueprint = fh.read()

    def test_blueprint_creates_the_same_tables(self):
        for table in TABLES:
            self.assertIn('CREATE TABLE IF NOT EXISTS %s' % table, self.blueprint, table)

    def test_blueprint_creates_the_same_named_indexes(self):
        names = set(re.findall(r'CREATE (?:UNIQUE )?INDEX IF NOT EXISTS (\w+)',
                               self.migration))
        self.assertTrue(names)
        for name in names:
            self.assertIn(name, self.blueprint, name)

    def test_both_declare_the_same_columns_for_the_article_table(self):
        def columns(text, table):
            block = re.search(r'CREATE TABLE IF NOT EXISTS %s \((.*?)\n\s*\)' % table,
                              text, re.S)
            self.assertIsNotNone(block, table)
            found = []
            for line in block.group(1).splitlines():
                match = re.match(r'\s*(\w+)\s+[A-Z]', line)
                if match and match.group(1).upper() not in ('UNIQUE', 'PRIMARY',
                                                            'FOREIGN', 'CONSTRAINT'):
                    found.append(match.group(1))
            return set(found)

        for table in TABLES:
            self.assertEqual(columns(self.migration, table),
                             columns(self.blueprint, table), table)


if __name__ == '__main__':
    unittest.main()
