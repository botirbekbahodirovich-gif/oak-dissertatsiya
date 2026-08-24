# -*- coding: utf-8 -*-
"""Maqola o'zbekcha sarlavhasi migratsiyasining statik xavfsizlik testi."""
import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(ROOT, 'migrations', '019_article_uzbek_title.sql')


class ArticleUzbekTitleMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(MIGRATION, encoding='utf-8') as fh:
            cls.sql = fh.read()

    def test_adds_nullable_uzbek_title_idempotently(self):
        self.assertIn('ADD COLUMN IF NOT EXISTS title_uz TEXT', self.sql)
        self.assertNotIn('title_uz TEXT NOT NULL', self.sql)

    def test_is_transactional_and_non_destructive(self):
        self.assertIn('BEGIN;', self.sql)
        self.assertIn('COMMIT;', self.sql)
        self.assertNotIn('DROP ', self.sql.upper())
        self.assertNotIn('DELETE ', self.sql.upper())


if __name__ == '__main__':
    unittest.main()
