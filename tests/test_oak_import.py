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

from scripts.seed_scientific_fields import (PARENT_EXISTS, PARENT_PENDING,
                                            SeedError, load_seed,
                                            normalize_entries, repair_hierarchy,
                                            resolve_parent, upsert_fields)
from tests.fake_seed_cursor import SeedCursor

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRANCH_SEED = os.path.join(_REPO_ROOT, 'data', 'scientific_fields.json')
OAK_APPROVED = os.path.join(_REPO_ROOT, 'data', 'oak', 'normalized',
                            'oak_specialties_approved.json')
OAK_GROUPS = os.path.join(_REPO_ROOT, 'data', 'oak', 'normalized',
                          'oak_specialty_groups.json')
OAK_REVIEW = os.path.join(_REPO_ROOT, 'data', 'oak', 'review',
                          'oak_specialties_review_required.json')
MIGRATION = os.path.join(_REPO_ROOT, 'migrations',
                         '008_scientific_field_provenance.sql')
BLUEPRINT = os.path.join(_REPO_ROOT, 'blueprints', 'articles.py')

PROVENANCE_COLUMNS = ('raw_name', 'source_line', 'source_sha256', 'review_status',
                      'reviewed_by', 'reviewed_at', 'missing_parent_code')


def seeded_cursor(with_groups=True, article_links=0):
    """Repozitoriy tarmoqlari (+ ixtiyoriy rasmiy guruhlar) kiritilgan jadval."""
    cursor = SeedCursor(article_links=article_links)
    doc, fields = load_seed(BRANCH_SEED)
    upsert_fields(cursor, normalize_entries(doc, fields), dry_run=False)
    if with_groups:
        doc, records = load_seed(OAK_GROUPS)
        upsert_fields(cursor, normalize_entries(doc, records), dry_run=False)
    return cursor


def group_entries():
    doc, records = load_seed(OAK_GROUPS)
    return normalize_entries(doc, records)


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
    """Rasmiy ota bo'lmasa yozuv KUTIB TURADI — tarmoqqa bog'lanmaydi."""

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

    def test_a_missing_group_never_falls_back_to_the_branch(self):
        # ENG MUHIM QOIDA: rasmiy 2-daraja guruh mavjud bo'lsa, 3-daraja
        # ixtisoslik 1-daraja tarmoqqa BOG'LANMAYDI. Guruh bazada bo'lmasa
        # yozuv kutib turadi.
        entry = {'code': '05.01.05', 'parent_code': '05.01.00'}
        status, parent, missing = resolve_parent(entry, self.index('05.00.00'))
        self.assertEqual(status, PARENT_PENDING)
        self.assertIsNone(parent, 'tarmoqqa fallback qilinmasligi kerak')
        self.assertEqual(missing, '05.01.00')

    def test_an_existing_group_is_used_directly(self):
        entry = {'code': '05.01.05', 'parent_code': '05.01.00'}
        status, parent, missing = resolve_parent(
            entry, self.index('05.00.00', '05.01.00'))
        self.assertEqual(status, PARENT_EXISTS)
        self.assertEqual(parent['code'], '05.01.00')
        self.assertIsNone(missing)

    def test_a_missing_branch_is_pending_too(self):
        entry = {'code': '21.01.06', 'parent_code': '21.01.00'}
        status, parent, missing = resolve_parent(entry, self.index('05.00.00'))
        self.assertEqual(status, PARENT_PENDING)
        self.assertIsNone(parent)
        self.assertEqual(missing, '21.01.00')


class OfficialGroupDatasetTests(unittest.TestCase):
    """Rasmiy OAK hujjatidan olingan 1- va 2-daraja yozuvlar."""

    @classmethod
    def setUpClass(cls):
        with io.open(OAK_GROUPS, encoding='utf-8') as fh:
            cls.doc = json.load(fh)
        cls.records = cls.doc['records']
        cls.by_code = {r['code']: r for r in cls.records}

    def test_every_previously_missing_group_is_now_present(self):
        for code in ('01.01.00', '01.02.00', '01.03.00', '01.04.00',
                     '05.01.00', '05.02.00', '05.03.00', '05.05.00', '05.06.00',
                     '05.07.00', '05.08.00', '05.09.00', '05.10.00',
                     '06.01.00', '06.02.00', '06.03.00'):
            self.assertIn(code, self.by_code, code)

    def test_the_military_branch_is_present_with_its_groups(self):
        self.assertIn('21.00.00', self.by_code)
        self.assertEqual(self.by_code['21.00.00']['level'], 1)
        self.assertIsNone(self.by_code['21.00.00']['parent_code'])
        for code in ('21.01.00', '21.02.00'):
            self.assertIn(code, self.by_code, code)
            self.assertEqual(self.by_code[code]['parent_code'], '21.00.00')

    def test_names_come_from_the_official_document_verbatim(self):
        # Rasmiy PDF ning o'zbek kirill bo'limidan AYNAN ko'chirilgan.
        self.assertEqual(self.by_code['21.00.00']['name_uz_cyrillic'],
                         'Ҳарбий фанлар')
        self.assertEqual(self.by_code['01.01.00']['name_uz_cyrillic'], 'Математика')
        self.assertEqual(self.by_code['05.01.00']['name_uz_cyrillic'],
                         'Ахборот технологиялари, бошқарув ва компьютер графикаси')
        self.assertEqual(self.by_code['06.03.00']['name_uz_cyrillic'],
                         'Ўрмон хўжалиги')

    def test_every_record_carries_authoritative_provenance(self):
        for record in self.records:
            self.assertEqual(record['review_status'], 'official_source', record['code'])
            self.assertIn('oak.uz', record['source_url'], record['code'])
            self.assertEqual(record['source_year'], 2021, record['code'])
            self.assertTrue(record['source_page'], record['code'])
            self.assertIn('%d-bet' % record['source_page'], record['source_name'])
            self.assertTrue(record['source_sha256'], record['code'])
            self.assertTrue(record['source_updated_at'], record['code'])

    def test_the_whole_dataset_shares_one_verified_source(self):
        meta = self.doc['metadata']
        self.assertEqual(meta['source_year'], 2021)
        self.assertEqual(meta['record_count'], len(self.records))
        self.assertEqual({r['source_sha256'] for r in self.records},
                         {meta['source_sha256']})

    def test_levels_follow_the_code_shape(self):
        for record in self.records:
            branch, group, specialty = record['code'].split('.')
            self.assertEqual(specialty, '00', record['code'])
            expected = 1 if group == '00' else 2
            self.assertEqual(record['level'], expected, record['code'])

    def test_latin_names_are_generated_and_never_empty(self):
        for record in self.records:
            self.assertTrue(record['name_uz'].strip(), record['code'])
            self.assertNotRegex(record['name_uz'], r'[\u0400-\u04ff]', record['code'])

    def test_slugs_are_unique(self):
        slugs = [r['slug'] for r in self.records]
        self.assertEqual(len(slugs), len(set(slugs)))


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

    def test_all_255_specialties_import_once_the_groups_exist(self):
        cursor = seeded_cursor()
        result = upsert_fields(cursor, self.entries, dry_run=False)
        self.assertEqual(result['stats']['created'], 255)
        self.assertEqual(result['stats']['conflict'], 0)
        self.assertEqual(result['stats']['pending'], 0)

    def test_the_two_military_specialties_are_no_longer_blocked(self):
        cursor = seeded_cursor()
        upsert_fields(cursor, self.entries, dry_run=False)
        codes = {r['code'] for r in cursor.rows}
        self.assertIn('21.01.08', codes)
        self.assertIn('21.02.23', codes)

    def test_specialties_hang_off_their_official_group_not_the_branch(self):
        cursor = seeded_cursor()
        upsert_fields(cursor, self.entries, dry_run=False)
        by_id = {r['id']: r for r in cursor.rows}
        by_code = {r['code']: r for r in cursor.rows}
        for code, group in (('05.01.05', '05.01.00'), ('01.01.01', '01.01.00'),
                            ('06.01.01', '06.01.00'), ('21.02.23', '21.02.00')):
            parent = by_id[by_code[code]['parent_id']]
            self.assertEqual(parent['code'], group, code)
            self.assertEqual(parent['level'], 2, code)

    def test_no_specialty_is_attached_to_a_branch_that_has_an_official_group(self):
        cursor = seeded_cursor()
        upsert_fields(cursor, self.entries, dry_run=False)
        by_id = {r['id']: r for r in cursor.rows}
        by_code = {r['code']: r for r in cursor.rows}
        offenders = []
        for row in cursor.rows:
            if not row.get('code') or row.get('level') != 3:
                continue
            expected_group = row['code'][:5] + '.00'
            if expected_group.endswith('.00.00') or expected_group not in by_code:
                continue
            parent = by_id.get(row['parent_id'])
            if not parent or parent['code'] != expected_group:
                offenders.append(row['code'])
        self.assertEqual(offenders, [])

    def test_no_record_keeps_a_temporary_fallback_marker(self):
        cursor = seeded_cursor()
        upsert_fields(cursor, self.entries, dry_run=False)
        flagged = [r['code'] for r in cursor.rows if r.get('missing_parent_code')]
        self.assertEqual(flagged, [])

    def test_a_three_level_chain_exists(self):
        cursor = seeded_cursor()
        upsert_fields(cursor, self.entries, dry_run=False)
        by_id = {r['id']: r for r in cursor.rows}
        by_code = {r['code']: r for r in cursor.rows}
        node, chain = by_code['05.01.05'], []
        while node:
            chain.append(node['code'])
            node = by_id.get(node.get('parent_id'))
        self.assertEqual(chain, ['05.01.05', '05.01.00', '05.00.00'])

    def test_specialties_are_pending_when_the_groups_are_absent(self):
        cursor = seeded_cursor(with_groups=False)
        result = upsert_fields(cursor, self.entries, dry_run=False)
        self.assertGreater(result['stats']['pending'], 0)
        pending = {p['code'] for p in result['pending']}
        self.assertIn('05.01.05', pending)
        self.assertNotIn('05.01.05', {r['code'] for r in cursor.rows})

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
        self.assertEqual(result['stats']['created'], 255)
        self.assertEqual(len(cursor.rows), rows_before)
        self.assertEqual(cursor.writes, writes_before)

    def test_no_artificial_group_record_is_created(self):
        cursor = seeded_cursor()
        upsert_fields(cursor, self.entries, dry_run=False)
        groups = {r['code'] for r in cursor.rows
                  if r.get('code') and r['code'].endswith('.00')
                  and not r['code'].endswith('.00.00')}
        official = {r['code'] for r in group_entries() if r['level'] == 2}
        self.assertEqual(groups - official, set())

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
        self.assertEqual(row['name_uz_cyrillic'],
                         'Математик анализ')

    def test_duplicate_names_under_different_codes_stay_separate(self):
        """Identity — SHIFR, nom emas."""
        cursor = seeded_cursor()
        upsert_fields(cursor, self.entries, dry_run=False)
        codes = [r['code'] for r in cursor.rows if r.get('code')]
        self.assertEqual(len(codes), len(set(codes)))
        zoology = [r['code'] for r in cursor.rows
                   if (r.get('name_uz') or '').lower().startswith('zoologiya')]
        self.assertEqual(len(zoology), len(set(zoology)))


class HierarchyRepairTests(unittest.TestCase):
    """--repair-hierarchy: vaqtinchalik fallback'ni rasmiy otaga ko'chiradi."""

    def legacy_cursor(self, article_links=7):
        """Eski holat: guruh yo'q, ixtisoslik to'g'ridan-to'g'ri tarmoqqa ulangan."""
        cursor = seeded_cursor(with_groups=False, article_links=article_links)
        branch = next(r for r in cursor.rows if r['code'] == '05.00.00')
        cursor.rows.append({
            'id': 9001, 'code': '05.01.05', 'slug': 'kiberxavfsizlik-050105',
            'name_uz': 'Axborotlarni himoyalash', 'parent_id': branch['id'],
            'level': 3, 'missing_parent_code': '05.01.00', 'description': None,
            'review_status': 'auto_normalized', 'source_sha256': 'x',
        })
        return cursor

    def add_groups(self, cursor):
        doc, records = load_seed(OAK_GROUPS)
        upsert_fields(cursor, normalize_entries(doc, records), dry_run=False)

    def test_repair_moves_the_record_under_its_official_group(self):
        cursor = self.legacy_cursor()
        self.add_groups(cursor)
        report = repair_hierarchy(cursor, dry_run=False)
        row = next(r for r in cursor.rows if r['id'] == 9001)
        group = next(r for r in cursor.rows if r['code'] == '05.01.00')
        self.assertEqual(row['parent_id'], group['id'])
        self.assertIsNone(row['missing_parent_code'])
        self.assertGreaterEqual(report['specialties_reparented'], 1)
        self.assertGreaterEqual(report['records_unblocked'], 1)

    def test_repair_preserves_the_slug_so_urls_keep_working(self):
        cursor = self.legacy_cursor()
        self.add_groups(cursor)
        repair_hierarchy(cursor, dry_run=False)
        row = next(r for r in cursor.rows if r['id'] == 9001)
        self.assertEqual(row['slug'], 'kiberxavfsizlik-050105')

    def test_repair_never_touches_article_associations(self):
        cursor = self.legacy_cursor(article_links=7)
        self.add_groups(cursor)
        report = repair_hierarchy(cursor, dry_run=False)
        self.assertEqual(report['article_links_before'], 7)
        self.assertEqual(report['article_links_after'], 7)
        self.assertTrue(report['article_links_preserved'])

    def test_repair_keeps_the_record_id_so_links_resolve(self):
        cursor = self.legacy_cursor()
        self.add_groups(cursor)
        repair_hierarchy(cursor, dry_run=False)
        self.assertEqual(len([r for r in cursor.rows if r['id'] == 9001]), 1)

    def test_repair_dry_run_changes_nothing(self):
        cursor = self.legacy_cursor()
        self.add_groups(cursor)
        writes = cursor.writes
        report = repair_hierarchy(cursor, dry_run=True)
        self.assertGreaterEqual(report['specialties_reparented'], 1)
        self.assertEqual(cursor.writes, writes)
        row = next(r for r in cursor.rows if r['id'] == 9001)
        self.assertEqual(row['missing_parent_code'], '05.01.00')

    def test_a_second_repair_run_changes_nothing(self):
        cursor = seeded_cursor()
        upsert_fields(cursor, oak_entries(), dry_run=False)
        repair_hierarchy(cursor, dry_run=False)
        writes = cursor.writes
        report = repair_hierarchy(cursor, dry_run=False)
        self.assertEqual(report['specialties_reparented'], 0)
        self.assertEqual(report['records_unblocked'], 0)
        self.assertEqual(cursor.writes, writes)

    def test_repair_reports_a_group_that_is_still_missing(self):
        cursor = self.legacy_cursor()
        report = repair_hierarchy(cursor, dry_run=False)
        self.assertIn('05.01.00', report['still_missing_parents'])
        self.assertEqual(report['specialties_reparented'], 0)

    def test_the_full_chain_leaves_no_missing_parent(self):
        cursor = seeded_cursor()
        upsert_fields(cursor, oak_entries(), dry_run=False)
        report = repair_hierarchy(cursor, dry_run=False)
        self.assertEqual(report['still_missing_parents'], [])


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
