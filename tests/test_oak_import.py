# -*- coding: utf-8 -*-
"""OAK datasetini `scientific_fields` ga import qilish.

Ikki qatlam:
  * migratsiya 008 ning statik xavfsizligi (SQL matni ustida);
  * import mantiqi — 22 tarmoq + 255 ixtisoslik xotiradagi jadval ustida
    (tests/fake_seed_cursor.py). Haqiqiy SQL semantikasi staging'da
    (tests/test_articles_dbgated.py).
"""
import io
import json
import os
import re
import unittest

from scripts.seed_scientific_fields import (PARENT_EXISTS, PARENT_MISSING_KNOWN_CODE,
                                            PARENT_UNRESOLVABLE, SeedError,
                                            load_seed, normalize_entries,
                                            resolve_parent, upsert_fields)
from tests.fake_seed_cursor import SeedCursor

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRANCH_SEED = os.path.join(_REPO_ROOT, 'data', 'scientific_fields.json')
OAK_APPROVED = os.path.join(_REPO_ROOT, 'data', 'oak', 'normalized',
                            'oak_specialties_approved.json')
OAK_REVIEW = os.path.join(_REPO_ROOT, 'data', 'oak', 'review',
                          'oak_specialties_review_required.json')
MIGRATION = os.path.join(_REPO_ROOT, 'migrations',
                         '008_scientific_field_provenance.sql')
BLUEPRINT = os.path.join(_REPO_ROOT, 'blueprints', 'articles.py')

PROVENANCE_COLUMNS = ('raw_name', 'source_line', 'source_sha256', 'review_status',
                      'reviewed_by', 'reviewed_at', 'missing_parent_code')


def seeded_cursor():
    """22 ta fan tarmog'i allaqachon kiritilgan jadval."""
    cursor = SeedCursor()
    doc, fields = load_seed(BRANCH_SEED)
    upsert_fields(cursor, normalize_entries(doc, fields), dry_run=False)
    return cursor


def oak_entries():
    doc, records = load_seed(OAK_APPROVED)
    return normalize_entries(doc, records)


class MigrationSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with io.open(MIGRATION, encoding='utf-8') as fh:
            cls.raw = fh.read()
        cls.code = '\n'.join(
            line if line.find('--') == -1 else line[:line.find('--')]
            for line in cls.raw.splitlines())

    def test_single_transaction(self):
        self.assertIn('BEGIN;', self.code)
        self.assertIn('COMMIT;', self.code)

    def test_every_column_add_is_idempotent(self):
        adds = re.findall(r'ADD COLUMN (IF NOT EXISTS)?', self.code)
        self.assertTrue(adds)
        self.assertTrue(all(adds))

    def test_all_provenance_columns_are_added(self):
        for column in PROVENANCE_COLUMNS:
            self.assertIn('ADD COLUMN IF NOT EXISTS %s' % column, self.code, column)

    def test_no_destructive_statements(self):
        for keyword in ('DROP ', 'TRUNCATE', 'DELETE FROM', 'UPDATE '):
            self.assertNotIn(keyword, self.code.upper(), keyword)

    def test_only_scientific_fields_is_altered(self):
        tables = set(re.findall(r'ALTER TABLE (\w+)', self.code))
        self.assertEqual(tables, {'scientific_fields'})

    def test_no_not_null_is_introduced(self):
        # `IF NOT EXISTS` va `IS NOT NULL` (partial indeks) qonuniy — faqat
        # ustun ta'rifiga NOT NULL qo'yilmasligi tekshiriladi.
        for statement in re.findall(r'ADD COLUMN[^;]*', self.code, re.I):
            definition = re.sub(r'IF NOT EXISTS', '', statement, flags=re.I)
            self.assertNotIn('NOT NULL', definition.upper(), statement)
        self.assertNotIn('SET NOT NULL', self.code.upper())

    def test_rollback_is_documented_but_commented_out(self):
        self.assertIn('ROLLBACK', self.raw.upper())
        self.assertNotIn('DROP COLUMN', self.code.upper())

    def test_the_blueprint_adds_the_same_columns(self):
        with io.open(BLUEPRINT, encoding='utf-8') as fh:
            blueprint = fh.read()
        for column in PROVENANCE_COLUMNS:
            self.assertIn("'%s'" % column, blueprint, column)


class ParentResolutionTests(unittest.TestCase):
    """§16 — ota topilmasa sun'iy nom YARATILMAYDI."""

    def index(self, *codes):
        return {code: {'id': i, 'code': code, 'level': 1, 'name_uz': code}
                for i, code in enumerate(codes, start=1)}

    def test_an_existing_parent_is_used(self):
        entry = {'code': '13.00.01', 'parent_code': '13.00.00'}
        status, parent, missing = resolve_parent(entry, self.index('13.00.00'))
        self.assertEqual(status, PARENT_EXISTS)
        self.assertEqual(parent['code'], '13.00.00')
        self.assertIsNone(missing)

    def test_a_root_record_has_no_parent(self):
        status, parent, missing = resolve_parent(
            {'code': '13.00.00', 'parent_code': None}, {})
        self.assertEqual(status, PARENT_EXISTS)
        self.assertIsNone(parent)

    def test_a_missing_group_falls_back_to_the_branch_and_keeps_the_real_code(self):
        entry = {'code': '05.01.05', 'parent_code': '05.01.00'}
        status, parent, missing = resolve_parent(entry, self.index('05.00.00'))
        self.assertEqual(status, PARENT_MISSING_KNOWN_CODE)
        self.assertEqual(parent['code'], '05.00.00')
        self.assertEqual(missing, '05.01.00',
                         'haqiqiy guruh shifri saqlanishi kerak')

    def test_a_missing_branch_is_unresolvable(self):
        entry = {'code': '21.01.06', 'parent_code': '21.01.00'}
        status, parent, missing = resolve_parent(entry, self.index('05.00.00'))
        self.assertEqual(status, PARENT_UNRESOLVABLE)
        self.assertIsNone(parent)
        self.assertEqual(missing, '21.01.00')


class OakDatasetImportTests(unittest.TestCase):
    """Haqiqiy dataset ustida to'liq import oqimi."""

    @classmethod
    def setUpClass(cls):
        cls.entries = oak_entries()

    def test_the_approved_dataset_loads(self):
        self.assertEqual(len(self.entries), 255)

    def test_every_entry_carries_provenance(self):
        for entry in self.entries:
            self.assertTrue(entry['source_line'], entry['code'])
            self.assertTrue(entry['source_sha256'], entry['code'])
            self.assertTrue(entry['name_uz_cyrillic'], entry['code'])

    def test_import_creates_the_specialties_under_the_existing_branches(self):
        cursor = seeded_cursor()
        before = len(cursor.rows)
        result = upsert_fields(cursor, self.entries, dry_run=False)
        self.assertEqual(result['stats']['created'], 253)
        self.assertEqual(result['stats']['conflict'], 0)
        self.assertEqual(len(cursor.rows), before + 253)

    def test_existing_branch_ids_are_preserved(self):
        cursor = seeded_cursor()
        branch_ids = {r['code']: r['id'] for r in cursor.rows}
        upsert_fields(cursor, self.entries, dry_run=False)
        for code, original_id in branch_ids.items():
            row = next(r for r in cursor.rows if r['code'] == code)
            self.assertEqual(row['id'], original_id, code)

    def test_the_import_is_idempotent(self):
        cursor = seeded_cursor()
        upsert_fields(cursor, self.entries, dry_run=False)
        rows_after_first = len(cursor.rows)
        writes_after_first = cursor.writes
        result = upsert_fields(cursor, self.entries, dry_run=False)
        self.assertEqual(result['stats']['created'], 0)
        self.assertEqual(result['stats']['updated'], 0)
        self.assertEqual(len(cursor.rows), rows_after_first)
        self.assertEqual(cursor.writes, writes_after_first)

    def test_dry_run_writes_nothing(self):
        cursor = seeded_cursor()
        rows_before = len(cursor.rows)
        writes_before = cursor.writes
        result = upsert_fields(cursor, self.entries)
        self.assertEqual(result['stats']['created'], 253)
        self.assertEqual(len(cursor.rows), rows_before)
        self.assertEqual(cursor.writes, writes_before)

    def test_records_without_a_reachable_branch_are_skipped_not_orphaned(self):
        cursor = seeded_cursor()
        result = upsert_fields(cursor, self.entries, dry_run=False)
        self.assertEqual(result['stats']['unresolvable'], 2)
        skipped = {item['code'] for item in result['unresolvable']}
        self.assertEqual(skipped, {'21.01.08', '21.02.23'})
        for code in skipped:
            self.assertFalse(any(r['code'] == code for r in cursor.rows), code)

    def test_specialties_with_an_unknown_group_keep_the_real_group_code(self):
        cursor = seeded_cursor()
        result = upsert_fields(cursor, self.entries, dry_run=False)
        self.assertEqual(len(result['missing_parent']), 70)
        row = next(r for r in cursor.rows if r['code'] == '05.01.05')
        self.assertEqual(row['missing_parent_code'], '05.01.00')
        branch = next(r for r in cursor.rows if r['code'] == '05.00.00')
        self.assertEqual(row['parent_id'], branch['id'])

    def test_no_artificial_group_record_is_created(self):
        cursor = seeded_cursor()
        upsert_fields(cursor, self.entries, dry_run=False)
        group_codes = [r['code'] for r in cursor.rows
                       if r['code'] and r['code'].endswith('.00')
                       and not r['code'].endswith('.00.00')]
        self.assertEqual(group_codes, [],
                         "rasmiy nomi yo'q guruh yozuvi yaratilmasligi kerak")

    def test_no_record_is_deleted_when_it_disappears_from_the_dataset(self):
        cursor = seeded_cursor()
        upsert_fields(cursor, self.entries, dry_run=False)
        count_before = len(cursor.rows)
        upsert_fields(cursor, self.entries[:10], dry_run=False)
        self.assertEqual(len(cursor.rows), count_before)

    def test_every_imported_row_records_where_it_came_from(self):
        cursor = seeded_cursor()
        upsert_fields(cursor, self.entries, dry_run=False)
        row = next(r for r in cursor.rows if r['code'] == '05.01.05')
        self.assertEqual(row['review_status'], 'auto_normalized')
        self.assertTrue(row['source_line'])
        self.assertTrue(row['source_sha256'])
        self.assertTrue(row['raw_name'])

    def test_latin_is_the_stored_name_and_cyrillic_is_kept_alongside(self):
        cursor = seeded_cursor()
        upsert_fields(cursor, self.entries, dry_run=False)
        row = next(r for r in cursor.rows if r['code'] == '01.01.01')
        self.assertEqual(row['name_uz'], 'Matematik analiz')
        self.assertEqual(row['name_uz_cyrillic'], 'Математик анализ')


class ReviewGateTests(unittest.TestCase):
    """§15 — review qilinmagan yozuv HECH QACHON import qilinmaydi."""

    def test_a_review_required_record_is_refused(self):
        with self.assertRaises(SeedError) as ctx:
            normalize_entries({}, [{'name_uz': 'Tekshirilmagan', 'code': '99.00.00',
                                    'review_status': 'review_required'}])
        self.assertIn('tasdiqlanmagan', ctx.exception.args[0])

    def test_an_unknown_review_status_is_refused(self):
        with self.assertRaises(SeedError):
            normalize_entries({}, [{'name_uz': 'X', 'code': '99.00.00',
                                    'review_status': 'nimadir'}])

    def test_the_review_queue_is_never_importable_as_is(self):
        with io.open(OAK_REVIEW, encoding='utf-8') as fh:
            review = json.load(fh)
        for record in review['records']:
            self.assertEqual(record['status'], 'review_required', record['code'])

    def test_review_codes_are_never_created_by_the_import(self):
        with io.open(OAK_REVIEW, encoding='utf-8') as fh:
            review_codes = {r['code'] for r in json.load(fh)['records']}
        cursor = seeded_cursor()
        seeded_codes = {r['code'] for r in cursor.rows}
        upsert_fields(cursor, oak_entries(), dry_run=False)
        created = {r['code'] for r in cursor.rows} - seeded_codes
        self.assertEqual(created & review_codes, set())
        # 10.00.00 seed'dan kelgan fan tarmog'i — u review'da ham bor, lekin
        # import uni QAYTA YARATMAGAN va nomiga tegmagan (quyidagi testga q.).
        self.assertIn('10.00.00', seeded_codes & review_codes)

    def test_the_corrupted_branch_record_never_overwrites_the_seeded_branch(self):
        # 10.00.00 manbada "Матншунослик..." deb kelgan; u review'da qolgani
        # uchun bazadagi "Filologiya fanlari" o'zgarishsiz qolishi kerak.
        cursor = seeded_cursor()
        upsert_fields(cursor, oak_entries(), dry_run=False)
        row = next(r for r in cursor.rows if r['code'] == '10.00.00')
        self.assertEqual(row['name_uz'], 'Filologiya fanlari')


if __name__ == '__main__':
    unittest.main()
