import io
import os
import sys
import unittest

ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from institution_identity import (looks_like_multiple_institutions,
                                  normalize_institution_identity)

MIGRATION=os.path.join(ROOT,'migrations','020_institution_identity.sql')


class InstitutionIdentityNormalizationTests(unittest.TestCase):
    def test_cyrillic_latin_and_apostrophes_share_an_exact_key(self):
        self.assertEqual(
            normalize_institution_identity('Ўзбекистон Миллий университети'),
            normalize_institution_identity("O‘zbekiston Milliy universiteti"))

    def test_official_name_with_va_is_not_split(self):
        self.assertFalse(looks_like_multiple_institutions(
            'Iqtisodiyot va pedagogika universiteti'))

    def test_two_independent_institutions_are_queued(self):
        self.assertTrue(looks_like_multiple_institutions(
            'O‘zbekiston Milliy universiteti va Astronomiya instituti'))

    def test_parent_child_phrase_is_not_collaboration(self):
        self.assertFalse(looks_like_multiple_institutions(
            'O‘zbekiston Fanlar akademiyasi Yadro fizikasi instituti'))

    def test_words_are_never_dropped(self):
        key=normalize_institution_identity(
            'Samarqand veterinariya meditsinasi, chorvachilik va biotexnologiyalar universiteti')
        self.assertIn('chorvachilik',key)
        self.assertIn('biotexnologiyalar',key)


class _ResolverCursor:
    def __init__(self, approved_id=None):
        self.approved_id = approved_id
        self.row = None
        self.queries = []

    def execute(self, query, params=None):
        normalized = " ".join(query.split())
        self.queries.append((normalized, params))
        if 'FROM institution_aliases a' in normalized:
            self.row = ((self.approved_id,) if self.approved_id is not None else None)
        elif 'SELECT id FROM institutions' in normalized:
            self.row = None
        else:
            self.row = None

    def fetchone(self):
        return self.row


class InstitutionResolverPolicyTests(unittest.TestCase):
    def test_admin_approved_composite_alias_wins_on_future_scrapes(self):
        from institution_identity import resolve_institution
        cur = _ResolverCursor(approved_id=77)
        result = resolve_institution(
            cur, 'O‘zbekiston Milliy universiteti va Astronomiya instituti',
            source='test')
        self.assertEqual(result, 77)
        self.assertFalse(any('institution_resolution_queue' in q for q, _ in cur.queries))

    def test_unknown_composite_is_queued_without_ranking_id(self):
        from institution_identity import resolve_institution
        cur = _ResolverCursor()
        result = resolve_institution(
            cur, 'O‘zbekiston Milliy universiteti va Astronomiya instituti',
            source='test')
        self.assertIsNone(result)
        self.assertTrue(any('INSERT INTO institution_resolution_queue' in q
                            for q, _ in cur.queries))


class InstitutionIdentityMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with io.open(MIGRATION,encoding='utf-8') as fh:
            cls.raw=fh.read()
        cls.code='\n'.join(line.split('--',1)[0] for line in cls.raw.splitlines())

    def test_migration_is_additive(self):
        upper=self.code.upper()
        self.assertNotIn('DROP ',upper)
        self.assertNotIn('TRUNCATE',upper)
        self.assertNotIn('DELETE FROM',upper)

    def test_raw_muassasa_is_preserved(self):
        self.assertIn('ADD COLUMN IF NOT EXISTS institution_id BIGINT',self.code)
        self.assertNotIn('UPDATE dissertations SET muassasa',self.code)

    def test_review_queue_and_audit_log_exist(self):
        self.assertIn('institution_resolution_queue',self.code)
        self.assertIn('institution_identity_events',self.code)


if __name__=='__main__':
    unittest.main()
