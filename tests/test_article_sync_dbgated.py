# -*- coding: utf-8 -*-
"""Metadata sinxronizatsiyasi — haqiqiy Postgres ustidagi integratsiya
testlari (Phase 2C.3A).

TEST_DATABASE_URL sozlanmagan bo'lsa O'TKAZIB YUBORILADI — staging yoki bir
martalik baza, hech qachon production emas (`tests/test_articles_dbgated.py`
bilan bir xil siyosat).
"""
import os
import unittest

TEST_DB = os.environ.get('TEST_DATABASE_URL')

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTICLES_MIGRATION = os.path.join(_REPO_ROOT, 'migrations', '007_create_article_catalog.sql')
SYNC_MIGRATION = os.path.join(_REPO_ROOT, 'migrations',
                              '011_create_article_metadata_syncs.sql')

SLUG_PREFIX = 'zz-sync-test-'


@unittest.skipUnless(TEST_DB, 'TEST_DATABASE_URL sozlanmagan (faqat staging)')
class ArticleSyncDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2
        cls.psycopg2 = psycopg2
        cls.conn = psycopg2.connect(TEST_DB)
        cls.conn.autocommit = False
        with open(ARTICLES_MIGRATION, encoding='utf-8') as fh:
            articles_sql = fh.read()
        with open(SYNC_MIGRATION, encoding='utf-8') as fh:
            sync_sql = fh.read()
        with cls.conn.cursor() as cur:
            cur.execute(articles_sql)
            cur.execute(sync_sql)
            cur.execute(sync_sql)   # idempotentlik
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        with cls.conn.cursor() as cur:
            cur.execute("DELETE FROM scientific_articles WHERE slug LIKE %s",
                        (SLUG_PREFIX + '%',))
        cls.conn.commit()
        cls.conn.close()

    def setUp(self):
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.rollback()
        self.cur.close()

    def make_article(self, suffix, doi=None):
        self.cur.execute(
            """INSERT INTO scientific_articles (title, slug, doi)
               VALUES (%s, %s, %s) RETURNING id""",
            ('ZZ Sync Test %s' % suffix, SLUG_PREFIX + suffix, doi))
        return self.cur.fetchone()[0]

    def test_sync_row_cascade_deletes_with_article(self):
        article_id = self.make_article('cascade')
        self.cur.execute(
            """INSERT INTO article_metadata_syncs (article_id, checked_by)
               VALUES (%s, -999) RETURNING id""", (article_id,))
        sync_id = self.cur.fetchone()[0]
        self.cur.execute("DELETE FROM scientific_articles WHERE id = %s", (article_id,))
        self.cur.execute("SELECT 1 FROM article_metadata_syncs WHERE id = %s", (sync_id,))
        self.assertIsNone(self.cur.fetchone())

    def test_status_defaults_to_checked(self):
        article_id = self.make_article('status')
        self.cur.execute(
            """INSERT INTO article_metadata_syncs (article_id, checked_by)
               VALUES (%s, -999) RETURNING id""", (article_id,))
        sync_id = self.cur.fetchone()[0]
        self.cur.execute("SELECT status FROM article_metadata_syncs WHERE id = %s",
                         (sync_id,))
        self.assertEqual(self.cur.fetchone()[0], 'checked')

    def test_apply_end_to_end_via_persistence_layer(self):
        """Diff engine + persistence to'liq real Postgres ustida — stale
        check, transaction, idempotency."""
        from services.article_sync import persistence
        from services.article_sync.diff import compare_article_metadata
        from services.article_metadata.models import (AuthorCandidate,
                                                       NormalizedArticleMetadata)

        article_id = self.make_article('e2e', doi='10.9999/' + SLUG_PREFIX + 'e2e')
        article = persistence.get_article_for_sync(self.cur, article_id)
        fresh = NormalizedArticleMetadata(
            doi=article['doi'], citation_count=41, citation_count_source='openalex',
            authors=[AuthorCandidate(name='Real Author')])
        diffs, mismatch = compare_article_metadata(article, fresh)
        self.assertFalse(mismatch)
        sync_id = persistence.create_sync_record(
            self.cur, article_id, -999, {}, diffs, article['updated_at'])

        diffs_by_field = {d.field: d.to_dict() for d in diffs}
        persistence.apply_field_changes(self.cur, article_id, diffs_by_field,
                                        ['citation_count'], -999)
        ok = persistence.mark_sync_applied(self.cur, sync_id, -999, ['citation_count'])
        self.assertTrue(ok)

        # Idempotency: qayta mark qilib bo'lmaydi.
        ok2 = persistence.mark_sync_applied(self.cur, sync_id, -999, ['citation_count'])
        self.assertFalse(ok2)

        self.cur.execute("SELECT citation_count FROM scientific_articles WHERE id = %s",
                         (article_id,))
        self.assertEqual(self.cur.fetchone()[0], 41)


if __name__ == '__main__':
    unittest.main()
