# -*- coding: utf-8 -*-
"""scripts/seed_scientific_fields.py — seed formati, idempotentlik, dry-run.

Baza kerak emas: upsert qalbaki kursor ustida bajariladi (tests/fake_pg.py
naqshi, lekin bu yerda seedga xos, sodda variant). Production seed faylining
o'zi ham tekshiriladi — u sun'iy kategoriya bilan to'ldirilib ketmasligi kerak.
"""
import io
import json
import os
import unittest

from scripts.seed_scientific_fields import (SeedError, load_seed, normalize_entries,
                                            resolve_parent, upsert_fields)
from tests.fake_seed_cursor import SeedCursor

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRODUCTION_SEED = os.path.join(_REPO_ROOT, 'data', 'scientific_fields.json')
SAMPLE_SEED = os.path.join(_REPO_ROOT, 'data', 'scientific_fields.sample.json')


def seed(entries, doc=None):
    return normalize_entries(doc or {}, entries)


class SeedFormatTests(unittest.TestCase):
    def test_production_seed_parses(self):
        doc, fields = load_seed(PRODUCTION_SEED)
        self.assertTrue(fields)
        self.assertIn('OAK', doc['source_name'])

    def test_production_seed_only_contains_verified_top_level_branches(self):
        _doc, fields = load_seed(PRODUCTION_SEED)
        self.assertEqual({f.get('level') for f in fields}, {1})
        for entry in fields:
            self.assertRegex(entry['code'], r'^\d{2}\.00\.00$')

    def test_production_seed_matches_the_oak_branch_list_already_in_the_repo(self):
        # app.py:SPECIALTY_NAMES OAK jurnallarini seed qilish uchun ishlatiladi;
        # ikki ro'yxat ajralib ketsa, ulardan biri noto'g'ri bo'ladi.
        from app import SPECIALTY_NAMES
        _doc, fields = load_seed(PRODUCTION_SEED)
        self.assertEqual({f['code']: f['name_uz'] for f in fields},
                         dict(SPECIALTY_NAMES))

    def test_the_sample_file_is_refused_without_an_explicit_flag(self):
        with self.assertRaises(SeedError) as ctx:
            load_seed(SAMPLE_SEED)
        self.assertIn('sample', ctx.exception.args[0].lower())

    def test_the_sample_file_loads_with_the_flag(self):
        _doc, fields = load_seed(SAMPLE_SEED, allow_sample=True)
        self.assertTrue(fields)

    def test_the_production_file_is_not_marked_as_a_sample(self):
        with io.open(PRODUCTION_SEED, encoding='utf-8') as fh:
            self.assertFalse(json.load(fh).get('sample'))

    def test_an_empty_seed_is_rejected(self):
        path = os.path.join(_REPO_ROOT, 'tests', '_tmp_seed.json')
        with io.open(path, 'w', encoding='utf-8') as fh:
            json.dump({'fields': []}, fh)
        try:
            with self.assertRaises(SeedError):
                load_seed(path)
        finally:
            os.unlink(path)


class NormalizationTests(unittest.TestCase):
    def test_parent_code_is_derived_from_the_oak_code(self):
        entries = seed([{'name_uz': 'Ixtisoslik', 'code': '13.00.01'}])
        self.assertEqual(entries[0]['parent_code'], '13.00.00')

    def test_a_duplicate_code_in_the_file_is_a_clear_error(self):
        with self.assertRaises(SeedError) as ctx:
            seed([{'name_uz': 'A', 'code': '13.00.00'},
                  {'name_uz': 'B', 'code': '13.00.00'}])
        self.assertIn('takroriy', ctx.exception.args[0].lower())

    def test_a_missing_name_is_a_clear_error(self):
        with self.assertRaises(SeedError):
            seed([{'name_uz': '', 'code': '13.00.00'}])

    def test_parents_are_ordered_before_their_children(self):
        entries = seed([{'name_uz': 'Ixtisoslik', 'code': '13.00.01'},
                        {'name_uz': 'Tarmoq', 'code': '13.00.00'}])
        self.assertEqual([e['code'] for e in entries], ['13.00.00', '13.00.01'])


class UpsertTests(unittest.TestCase):
    ENTRIES = [
        {'name_uz': 'Pedagogika fanlari', 'code': '13.00.00'},
        {'name_uz': 'Umumiy pedagogika', 'code': '13.00.01'},
    ]

    def test_first_run_creates_everything(self):
        cursor = SeedCursor()
        stats = upsert_fields(cursor, seed(self.ENTRIES), dry_run=False)['stats']
        self.assertEqual((stats['created'], stats['updated'], stats['skipped']),
                         (2, 0, 0))

    def test_second_run_changes_nothing(self):
        cursor = SeedCursor()
        upsert_fields(cursor, seed(self.ENTRIES), dry_run=False)
        writes_after_first = cursor.writes
        stats = upsert_fields(cursor, seed(self.ENTRIES), dry_run=False)['stats']
        self.assertEqual((stats['created'], stats['updated'], stats['skipped']),
                         (0, 0, 2))
        self.assertEqual(cursor.writes, writes_after_first, 'ikkinchi run yozdi')

    def test_a_changed_name_is_a_conflict_by_default(self):
        # Mavjud nom jimlik bilan almashtirilmaydi (§10).
        cursor = SeedCursor()
        upsert_fields(cursor, seed(self.ENTRIES), dry_run=False)
        changed = [dict(self.ENTRIES[0], name_uz='Pedagogika fanlari (yangilangan)'),
                   self.ENTRIES[1]]
        result = upsert_fields(cursor, seed(changed), dry_run=False)
        self.assertEqual(result['stats']['conflict'], 1)
        self.assertEqual(result['stats']['updated'], 0)
        self.assertEqual(cursor.rows[0]['name_uz'], 'Pedagogika fanlari')
        self.assertEqual(result['conflicts'][0]['incoming_name_uz'],
                         'Pedagogika fanlari (yangilangan)')

    def test_a_changed_name_is_applied_with_update_names(self):
        cursor = SeedCursor()
        upsert_fields(cursor, seed(self.ENTRIES), dry_run=False)
        changed = [dict(self.ENTRIES[0], name_uz='Pedagogika fanlari (yangilangan)'),
                   self.ENTRIES[1]]
        result = upsert_fields(cursor, seed(changed), dry_run=False, update_names=True)
        self.assertEqual(result['stats']['updated'], 1)
        self.assertEqual(len(cursor.rows), 2)

    def test_children_are_linked_to_their_parent(self):
        cursor = SeedCursor()
        upsert_fields(cursor, seed(self.ENTRIES), dry_run=False)
        parent = next(r for r in cursor.rows if r['code'] == '13.00.00')
        child = next(r for r in cursor.rows if r['code'] == '13.00.01')
        self.assertEqual(child['parent_id'], parent['id'])
        self.assertEqual(parent['level'], 1)
        self.assertEqual(child['level'], 3)

    def test_an_unreachable_parent_is_reported_not_silently_orphaned(self):
        cursor = SeedCursor()
        result = upsert_fields(cursor, seed([{'name_uz': 'Yetim', 'code': '13.00.01'}]),
                               dry_run=False)
        self.assertEqual(result['stats']['pending'], 1)
        self.assertEqual(result['stats']['created'], 0)
        self.assertEqual(cursor.rows, [], 'soxta ildiz yozuv yaratilmasligi kerak')
        self.assertEqual(result['pending'][0]['expected_parent_code'], '13.00.00')

    def test_dry_run_is_the_default_and_writes_nothing(self):
        cursor = SeedCursor()
        result = upsert_fields(cursor, seed(self.ENTRIES))
        self.assertEqual(result['stats']['created'], 2)
        self.assertEqual(cursor.writes, 0)
        self.assertEqual(cursor.rows, [])

    def test_generated_slugs_are_unique(self):
        cursor = SeedCursor()
        upsert_fields(cursor, seed([
            {'name_uz': 'Umumiy pedagogika', 'code': '13.00.00'},
            {'name_uz': 'Umumiy pedagogika', 'code': '13.00.01'},
        ]), dry_run=False)
        slugs = [r['slug'] for r in cursor.rows]
        self.assertEqual(len(slugs), len(set(slugs)))


if __name__ == '__main__':
    unittest.main()
