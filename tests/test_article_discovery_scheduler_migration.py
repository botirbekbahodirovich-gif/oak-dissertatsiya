# -*- coding: utf-8 -*-
"""migrations/018_article_discovery_scheduler.sql — statik xavfsizlik +
parity testi (Phase 2D.3). Bazaga TEGILMAYDI, faqat matnni tahlil qiladi.
"""
import os
import re
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(_REPO_ROOT, 'migrations', '018_article_discovery_scheduler.sql')
SAVED_QUERIES_MODULE = os.path.join(_REPO_ROOT, 'services', 'article_discovery',
                                    'saved_queries.py')
SCHEDULER_PERSISTENCE_MODULE = os.path.join(_REPO_ROOT, 'services', 'article_discovery',
                                            'scheduler_persistence.py')


def _strip_comments(sql):
    out = []
    for line in sql.splitlines():
        idx = line.find('--')
        out.append(line if idx == -1 else line[:idx])
    return '\n'.join(out)


class MigrationNumberingTests(unittest.TestCase):
    def test_018_file_exists(self):
        self.assertTrue(os.path.isfile(MIGRATION))

    def test_migrations_remain_sequential_through_019(self):
        """018 discovery scheduler va 019 maqola tarjimasi ketma-ket,
        navbatdagi 020 raqami esa hali bo'sh."""
        numbers = [
            int(re.match(r'(\d+)_', name).group(1))
            for name in os.listdir(os.path.join(_REPO_ROOT, 'migrations'))
            if re.match(r'\d+_', name)
        ]
        self.assertIn(17, numbers)
        self.assertIn(18, numbers)
        self.assertIn(19, numbers)
        self.assertNotIn(20, numbers)

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

    def test_adds_expected_schedule_columns(self):
        for column in ('schedule_enabled', 'schedule_frequency', 'next_run_at',
                       'last_scheduled_run_at', 'last_run_status', 'last_run_error',
                       'scheduled_updated_at'):
            self.assertIn(column, self.code, column)

    def test_schedule_enabled_defaults_false(self):
        """§13/§25 — production xavfsizligi: sukut bo'yicha FALSE."""
        match = re.search(r'schedule_enabled\s+BOOLEAN\s+NOT NULL DEFAULT (\w+)',
                          self.code)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1).upper(), 'FALSE')

    def test_creates_runs_table(self):
        self.assertIn('CREATE TABLE IF NOT EXISTS article_discovery_runs', self.code)

    def test_runs_table_has_expected_columns(self):
        for column in ('id', 'saved_query_id', 'trigger_type', 'status', 'started_at',
                       'finished_at', 'providers', 'found_count', 'new_count',
                       'queued_count', 'duplicate_count', 'no_doi_count',
                       'error_summary', 'import_batch_id'):
            self.assertIn(column, self.code, column)

    def test_every_add_column_is_idempotent(self):
        for statement in re.findall(r'ADD\s+COLUMN\s+(\S+)', self.upper):
            self.assertEqual(statement, 'IF')

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
                          'ALTER TABLE article_metadata_syncs',
                          'ALTER TABLE article_metadata_sync_runs',
                          'ALTER TABLE article_import_batches',
                          'ALTER TABLE article_import_items'):
            self.assertNotIn(forbidden.upper(), self.upper)

    def test_saved_query_id_fk_cascades(self):
        self.assertIn(
            'REFERENCES article_discovery_saved_queries(id) ON DELETE CASCADE',
            self.code)

    def test_import_batch_id_fk_is_set_null(self):
        self.assertIn(
            'REFERENCES article_import_batches(id) ON DELETE SET NULL', self.code)

    def test_due_index_scoped_to_enabled_active_rows(self):
        match = re.search(
            r'idx_article_discovery_saved_queries_due[\s\S]*?WHERE ([^;]+);', self.code)
        self.assertIsNotNone(match)
        self.assertIn('schedule_enabled', match.group(1))
        self.assertIn('is_active', match.group(1))


class PersistenceSchemaMatchesMigrationTests(unittest.TestCase):
    """`saved_queries.py` (schedule ustunlari) va `scheduler_persistence.py`
    (`article_discovery_runs`) — migratsiya bilan AYNAN bir xil DDL
    (ensure_schema parity, ticket §18)."""

    @classmethod
    def setUpClass(cls):
        with open(SAVED_QUERIES_MODULE, encoding='utf-8') as fh:
            cls.saved_queries_code = fh.read()
        with open(SCHEDULER_PERSISTENCE_MODULE, encoding='utf-8') as fh:
            cls.scheduler_persistence_code = fh.read()

    def test_saved_queries_ensure_schema_adds_same_columns(self):
        for column in ('schedule_enabled', 'schedule_frequency', 'next_run_at',
                       'last_scheduled_run_at', 'last_run_status', 'last_run_error',
                       'scheduled_updated_at'):
            self.assertIn(column, self.saved_queries_code, column)

    def test_scheduler_persistence_creates_same_runs_table(self):
        self.assertIn('CREATE TABLE IF NOT EXISTS article_discovery_runs',
                      self.scheduler_persistence_code)

    def test_scheduler_persistence_calls_saved_queries_ensure_schema_first(self):
        """§18 — jadval sxemasi IKKI joyda YOZILMAYDI: `scheduler_
        persistence.ensure_schema()` bazaviy jadval uchun `saved_queries.
        ensure_schema()`ga TAYANADI, DDL'ni takrorlamaydi."""
        self.assertIn('saved_queries.ensure_schema(cur)', self.scheduler_persistence_code)
        self.assertNotIn('ADD COLUMN IF NOT EXISTS schedule_enabled',
                         self.scheduler_persistence_code)

    def test_no_destructive_statements_in_either_module(self):
        for code in (self.saved_queries_code, self.scheduler_persistence_code):
            upper = code.upper()
            for keyword in ('DROP TABLE', 'DROP COLUMN', 'TRUNCATE'):
                self.assertNotIn(keyword, upper)


if __name__ == '__main__':
    unittest.main()
