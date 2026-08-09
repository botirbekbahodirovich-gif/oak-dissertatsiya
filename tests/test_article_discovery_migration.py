# -*- coding: utf-8 -*-
"""migrations/014_article_discovery.sql — statik xavfsizlik + parity testi
(Phase 2D.1, §45). `tests/test_article_metadata_scheduler_migration.py` bilan
bir xil naqsh: bazaga TEGILMAYDI, faqat matnni tahlil qiladi.
"""
import os
import re
import unittest

from services.article_import.state import ACTIVE_DOI_ITEM_STATUSES

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(_REPO_ROOT, 'migrations', '014_article_discovery.sql')
PERSISTENCE = os.path.join(_REPO_ROOT, 'services', 'article_import', 'persistence.py')

EXISTING_MIGRATION_NUMBERS = [
    int(re.match(r'(\d+)_', name).group(1))
    for name in os.listdir(os.path.join(_REPO_ROOT, 'migrations'))
    if re.match(r'\d+_', name)
]


def _strip_comments(sql):
    out = []
    for line in sql.splitlines():
        idx = line.find('--')
        out.append(line if idx == -1 else line[:idx])
    return '\n'.join(out)


class MigrationNumberingTests(unittest.TestCase):
    """Raqamlash — 014 yozilgan paytda "keyingi bo'sh raqam" edi (013'dan
    keyin). Keyinroq 015+ qo'shilishi kutilgan (Phase 2D.1.1), shuning uchun
    bu yerda "014 = eng kattasi" deb QOTIRILMAYDI — buning o'rniga umumiy,
    vaqt o'tishi bilan buzilmaydigan invariant tekshiriladi."""

    def test_014_file_exists_and_immediately_follows_013(self):
        self.assertIn(13, EXISTING_MIGRATION_NUMBERS)
        self.assertIn(14, EXISTING_MIGRATION_NUMBERS)

    def test_migration_numbers_have_no_gaps(self):
        numbers = sorted(EXISTING_MIGRATION_NUMBERS)
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))

    def test_no_duplicate_migration_numbers(self):
        self.assertEqual(len(EXISTING_MIGRATION_NUMBERS),
                         len(set(EXISTING_MIGRATION_NUMBERS)))


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

    def test_adds_expected_batch_columns(self):
        for column in ('source_provider', 'source_query', 'source_oak_field_id',
                       'source_result_count', 'excluded_no_doi_count'):
            self.assertIn(column, self.code, column)

    def test_adds_expected_item_column(self):
        self.assertIn('external_source_id', self.code)

    def test_every_add_column_is_idempotent(self):
        for statement in re.findall(r'ADD\s+COLUMN\s+(\S+)', self.upper):
            self.assertEqual(statement, 'IF')

    def test_every_create_is_idempotent(self):
        for statement in re.findall(
                r'CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)\s+(\S+)', self.upper):
            self.assertEqual(statement, 'IF')

    def test_creates_no_new_table(self):
        """§18 — ArticleDiscoveryRun YO'Q, faqat mavjud jadvallar kengaytiriladi."""
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

    def test_does_not_touch_unrelated_article_tables(self):
        for forbidden in ('ALTER TABLE scientific_articles', 'ALTER TABLE article_authors',
                          'ALTER TABLE article_metadata_syncs',
                          'ALTER TABLE article_metadata_sync_runs'):
            self.assertNotIn(forbidden.upper(), self.upper)

    def test_source_oak_field_fk_is_set_null_on_delete(self):
        self.assertIn('REFERENCES scientific_fields(id) ON DELETE SET NULL', self.code)


class ActiveDoiStatusParityTests(unittest.TestCase):
    """`state.py:ACTIVE_DOI_ITEM_STATUSES` — yagona haqiqat manbai. Migration
    va `ensure_schema()`dagi partial unique index predicate shu ro'yxat bilan
    AYNAN mos bo'lishi SHART (§15, §45)."""

    def setUp(self):
        with open(MIGRATION, encoding='utf-8') as fh:
            self.migration_code = fh.read()
        with open(PERSISTENCE, encoding='utf-8') as fh:
            self.persistence_code = fh.read()

    def _extract_status_list(self, code):
        match = re.search(
            r'uq_article_import_items_active_doi.*?status IN \(([^)]*)\)',
            code, re.S)
        self.assertIsNotNone(match, 'partial unique index topilmadi')
        return {s.strip().strip("'") for s in match.group(1).split(',')}

    def test_migration_predicate_matches_state_py(self):
        self.assertEqual(self._extract_status_list(self.migration_code),
                         set(ACTIVE_DOI_ITEM_STATUSES))

    def test_ensure_schema_predicate_matches_state_py(self):
        self.assertEqual(self._extract_status_list(self.persistence_code),
                         set(ACTIVE_DOI_ITEM_STATUSES))

    def test_migration_and_ensure_schema_predicate_identical(self):
        self.assertEqual(self._extract_status_list(self.migration_code),
                         self._extract_status_list(self.persistence_code))

    def test_duplicate_in_batch_excluded(self):
        """Bu status hali ham CSV/DOI-list import'da BIR XIL DOI bilan
        (original + duplicate_in_batch) BIRGA yashaydi — index'ga kirsa
        mavjud xulq-atvor buziladi."""
        self.assertNotIn('duplicate_in_batch', self._extract_status_list(self.migration_code))

    def test_already_exists_excluded(self):
        self.assertNotIn('already_exists', self._extract_status_list(self.migration_code))


class PersistenceSchemaMatchesMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(PERSISTENCE, encoding='utf-8') as fh:
            cls.code = fh.read()

    def test_ensure_schema_adds_same_batch_columns(self):
        for column in ('source_provider', 'source_query', 'source_oak_field_id',
                       'source_result_count', 'excluded_no_doi_count'):
            self.assertIn(column, self.code, column)

    def test_ensure_schema_adds_same_item_column(self):
        self.assertIn('external_source_id', self.code)

    def test_ensure_schema_has_no_destructive_statements(self):
        upper = self.code.upper()
        for keyword in ('DROP TABLE', 'DROP COLUMN', 'TRUNCATE'):
            self.assertNotIn(keyword, upper)


if __name__ == '__main__':
    unittest.main()
