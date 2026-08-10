# -*- coding: utf-8 -*-
"""migrations/017_article_discovery_saved_queries.sql — statik xavfsizlik +
parity testi (Phase 2D.2). `tests/test_article_discovery_migration.py` bilan
bir xil naqsh: bazaga TEGILMAYDI, faqat matnni tahlil qiladi.
"""
import os
import re
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(_REPO_ROOT, 'migrations', '017_article_discovery_saved_queries.sql')
SAVED_QUERIES_MODULE = os.path.join(_REPO_ROOT, 'services', 'article_discovery',
                                    'saved_queries.py')


def _strip_comments(sql):
    out = []
    for line in sql.splitlines():
        idx = line.find('--')
        out.append(line if idx == -1 else line[:idx])
    return '\n'.join(out)


class MigrationNumberingTests(unittest.TestCase):
    def test_017_file_exists(self):
        self.assertTrue(os.path.isfile(MIGRATION))

    def test_017_is_present_and_immediately_follows_016(self):
        """Ticket: 'inspect migrations and use the next actual free number' —
        016 dan keyin bo'shliqsiz keladi. NOTE: bu 017'ning o'zi haqida —
        keyingi raqamlar (018+, masalan Phase 2D.3) bemalol qo'shilishi
        mumkin, shuning uchun "018 yo'q" degan tasdiq ATAYIN olib
        tashlangan (u faqat 2D.2 vaqtida to'g'ri edi)."""
        numbers = [
            int(re.match(r'(\d+)_', name).group(1))
            for name in os.listdir(os.path.join(_REPO_ROOT, 'migrations'))
            if re.match(r'\d+_', name)
        ]
        self.assertIn(16, numbers)
        self.assertIn(17, numbers)

    def test_still_no_gaps_across_all_migrations(self):
        numbers = sorted(set(
            int(re.match(r'(\d+)_', name).group(1))
            for name in os.listdir(os.path.join(_REPO_ROOT, 'migrations'))
            if re.match(r'\d+_', name)))
        self.assertEqual(numbers, list(range(1, max(numbers) + 1)))


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

    def test_creates_the_new_table(self):
        self.assertIn('CREATE TABLE IF NOT EXISTS article_discovery_saved_queries',
                      self.code)

    def test_table_has_expected_columns(self):
        for column in ('id', 'name', 'created_by', 'keyword', 'from_date', 'to_date',
                       'oak_field_id', 'providers', 'result_limit', 'is_active',
                       'created_at', 'updated_at', 'last_used_at'):
            self.assertIn(column, self.code, column)

    def test_every_create_is_idempotent(self):
        for statement in re.findall(
                r'CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)\s+(\S+)', self.upper):
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

    def test_does_not_touch_unrelated_tables(self):
        for forbidden in ('ALTER TABLE scientific_articles', 'ALTER TABLE article_authors',
                          'ALTER TABLE article_import_batches',
                          'ALTER TABLE article_import_items',
                          'ALTER TABLE article_metadata_syncs'):
            self.assertNotIn(forbidden.upper(), self.upper)

    def test_oak_field_fk_is_set_null_on_delete(self):
        self.assertIn('REFERENCES scientific_fields(id) ON DELETE SET NULL', self.code)

    def test_created_by_has_no_users_fk(self):
        """Audit natijasi (§9 ticket): `article_import_batches.created_by`
        bilan bir xil LOKAL konvensiya — FK YO'Q, oddiy INTEGER."""
        match = re.search(r'created_by\s+INTEGER\s+NOT NULL', self.code)
        self.assertIsNotNone(match)
        # Shu qatorda REFERENCES yo'qligini tekshiramiz.
        line = next(l for l in self.code.splitlines() if 'created_by' in l)
        self.assertNotIn('REFERENCES', line.upper())

    def test_providers_column_is_jsonb_with_default(self):
        self.assertIn('providers', self.code)
        self.assertIn('JSONB', self.upper)

    def test_is_active_defaults_true(self):
        self.assertIn('DEFAULT TRUE', self.upper)

    def test_indexes_present_for_admin_listing(self):
        self.assertIn('idx_article_discovery_saved_queries_created_by', self.code)
        self.assertIn('idx_article_discovery_saved_queries_active', self.code)


class PersistenceSchemaMatchesMigrationTests(unittest.TestCase):
    """`services/article_discovery/saved_queries.py:ensure_schema()` — lazy
    DDL migratsiya bilan AYNAN bir xil bo'lishi kerak (ensure_schema
    parity, §10)."""

    @classmethod
    def setUpClass(cls):
        with open(SAVED_QUERIES_MODULE, encoding='utf-8') as fh:
            cls.code = fh.read()

    def test_ensure_schema_creates_same_table(self):
        self.assertIn('CREATE TABLE IF NOT EXISTS article_discovery_saved_queries',
                      self.code)

    def test_ensure_schema_has_same_columns(self):
        for column in ('id', 'name', 'created_by', 'keyword', 'from_date', 'to_date',
                       'oak_field_id', 'providers', 'result_limit', 'is_active',
                       'created_at', 'updated_at', 'last_used_at'):
            self.assertIn(column, self.code, column)

    def test_ensure_schema_creates_same_indexes(self):
        self.assertIn('idx_article_discovery_saved_queries_created_by', self.code)
        self.assertIn('idx_article_discovery_saved_queries_active', self.code)
        self.assertIn('idx_article_discovery_saved_queries_oak_field', self.code)

    def test_ensure_schema_has_no_destructive_statements(self):
        upper = self.code.upper()
        for keyword in ('DROP TABLE', 'DROP COLUMN', 'TRUNCATE'):
            self.assertNotIn(keyword, upper)

    def test_ensure_schema_is_cached_after_first_call(self):
        self.assertIn('_schema_ready', self.code)


if __name__ == '__main__':
    unittest.main()
