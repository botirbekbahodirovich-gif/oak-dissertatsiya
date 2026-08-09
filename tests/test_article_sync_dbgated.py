# -*- coding: utf-8 -*-
"""Metadata sinxronizatsiyasi — haqiqiy Postgres ustidagi integratsiya
testlari (Phase 2C.3A + 2C.3B dashboard).

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
DASHBOARD_MIGRATION = os.path.join(_REPO_ROOT, 'migrations',
                                   '012_article_metadata_sync_dashboard.sql')

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
        with open(DASHBOARD_MIGRATION, encoding='utf-8') as fh:
            dashboard_sql = fh.read()
        with cls.conn.cursor() as cur:
            cur.execute(articles_sql)
            cur.execute(sync_sql)
            cur.execute(sync_sql)         # idempotentlik
            cur.execute(dashboard_sql)
            cur.execute(dashboard_sql)    # idempotentlik
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


@unittest.skipUnless(TEST_DB, 'TEST_DATABASE_URL sozlanmagan (faqat staging)')
class DashboardQueryDbTests(unittest.TestCase):
    """Haqiqiy `DISTINCT ON` + `FILTER (WHERE …)` semantikasi (Phase 2C.3B,
    §28) — qalbaki kursorda simulyatsiya QILINMAYDI (tests/fake_article_sync_pg.py
    docstringiga qarang), faqat shu yerda, real Postgres ustida."""

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
        with open(DASHBOARD_MIGRATION, encoding='utf-8') as fh:
            dashboard_sql = fh.read()
        with cls.conn.cursor() as cur:
            cur.execute(articles_sql)
            cur.execute(sync_sql)
            cur.execute(dashboard_sql)
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
            ('ZZ Dash Test %s' % suffix, SLUG_PREFIX + 'dash-' + suffix, doi))
        return self.cur.fetchone()[0]

    def make_sync(self, article_id, *, status='checked', change_count=1,
                 critical_change_count=0, checked_at_offset=0):
        from services.article_sync import persistence
        snapshot = persistence.get_article_updated_at(self.cur, article_id)
        self.cur.execute(
            """INSERT INTO article_metadata_syncs
                  (article_id, checked_by, status, change_count,
                   critical_change_count, article_updated_at_snapshot,
                   checked_at)
               VALUES (%s, -999, %s, %s, %s, %s, NOW() + (%s || ' seconds')::interval)
               RETURNING id""",
            (article_id, status, change_count, critical_change_count, snapshot,
             checked_at_offset))
        return self.cur.fetchone()[0]

    def test_latest_sync_per_article_is_the_one_returned(self):
        from services.article_sync import persistence
        article_id = self.make_article('latest')
        self.make_sync(article_id, checked_at_offset=0)
        newest_id = self.make_sync(article_id, checked_at_offset=100)
        rows, total = persistence.list_dashboard(self.cur, queue_status='all',
                                                  limit=50, offset=0)
        matching = [r for r in rows if r['article_id'] == article_id]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['id'], newest_id)

    def test_older_unapplied_sync_excluded_from_default_queue(self):
        from services.article_sync import persistence
        article_id = self.make_article('older')
        self.make_sync(article_id, checked_at_offset=0, change_count=3)
        # Yangiroq tekshiruv o'zgarish topmadi — eski "actionable" ko'rinmasin.
        self.make_sync(article_id, checked_at_offset=100, change_count=0)
        rows, _total = persistence.list_dashboard(self.cur, limit=50, offset=0)
        self.assertFalse(any(r['article_id'] == article_id for r in rows))

    def test_no_change_sync_excluded_from_default_queue(self):
        from services.article_sync import persistence
        article_id = self.make_article('nochange')
        self.make_sync(article_id, change_count=0)
        rows, _total = persistence.list_dashboard(self.cur, limit=50, offset=0)
        self.assertFalse(any(r['article_id'] == article_id for r in rows))

    def test_applied_sync_excluded_from_default_queue(self):
        from services.article_sync import persistence
        article_id = self.make_article('applied')
        sync_id = self.make_sync(article_id, change_count=1)
        persistence.mark_sync_applied(self.cur, sync_id, -999, ['citation_count'])
        rows, _total = persistence.list_dashboard(self.cur, limit=50, offset=0)
        self.assertFalse(any(r['article_id'] == article_id for r in rows))

    def test_dismissed_sync_excluded_from_default_queue(self):
        from services.article_sync import persistence
        article_id = self.make_article('dismissed')
        sync_id = self.make_sync(article_id, change_count=1)
        persistence.dismiss_sync(self.cur, sync_id, -999)
        rows, _total = persistence.list_dashboard(self.cur, limit=50, offset=0)
        self.assertFalse(any(r['article_id'] == article_id for r in rows))

    def test_critical_first_ordering(self):
        from services.article_sync import persistence
        a1 = self.make_article('crit-low')
        a2 = self.make_article('crit-high')
        self.make_sync(a1, change_count=1, critical_change_count=0)
        self.make_sync(a2, change_count=1, critical_change_count=1)
        rows, _total = persistence.list_dashboard(
            self.cur, queue_status='all', sort='critical_first', limit=50, offset=0)
        ids_in_order = [r['article_id'] for r in rows if r['article_id'] in (a1, a2)]
        self.assertEqual(ids_in_order, [a2, a1])

    def test_pagination_limits_page_size(self):
        from services.article_sync import persistence
        for i in range(5):
            article_id = self.make_article('page-%d' % i)
            self.make_sync(article_id, change_count=1)
        rows, total = persistence.list_dashboard(
            self.cur, queue_status='needs_review', limit=2, offset=0)
        self.assertLessEqual(len(rows), 2)
        self.assertGreaterEqual(total, 5)

    def test_retraction_filter(self):
        from services.article_sync import persistence
        article_id = self.make_article('retraction-filter')
        self.cur.execute(
            """INSERT INTO article_metadata_syncs
                  (article_id, checked_by, change_count, has_retraction_change)
               VALUES (%s, -999, 1, TRUE) RETURNING id""", (article_id,))
        rows, _total = persistence.list_dashboard(self.cur, has_retraction=True,
                                                   limit=50, offset=0)
        self.assertTrue(any(r['article_id'] == article_id for r in rows))


if __name__ == '__main__':
    unittest.main()
