# -*- coding: utf-8 -*-
"""Due-article tanlash — haqiqiy Postgres ustidagi integratsiya testlari
(Phase 2C.3C). `LEFT JOIN LATERAL` + `NULLS FIRST` semantikasi Postgres-ga
xos — `tests/fake_article_sync_pg.py` fake kursurida simulyatsiya
QILINMAYDI (`tests/test_article_sync_dbgated.py`dagi bilan bir xil siyosat).

TEST_DATABASE_URL sozlanmagan bo'lsa O'TKAZIB YUBORILADI — staging yoki bir
martalik baza, hech qachon production emas.
"""
import os
import unittest

TEST_DB = os.environ.get('TEST_DATABASE_URL')

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTICLES_MIGRATION = os.path.join(_REPO_ROOT, 'migrations', '007_create_article_catalog.sql')
EXTERNAL_META_MIGRATION = os.path.join(_REPO_ROOT, 'migrations',
                                       '009_article_external_metadata.sql')
SYNC_MIGRATION = os.path.join(_REPO_ROOT, 'migrations',
                              '011_create_article_metadata_syncs.sql')
DASHBOARD_MIGRATION = os.path.join(_REPO_ROOT, 'migrations',
                                   '012_article_metadata_sync_dashboard.sql')
SCHEDULER_MIGRATION = os.path.join(_REPO_ROOT, 'migrations',
                                   '013_article_metadata_sync_scheduler.sql')

SLUG_PREFIX = 'zz-scheduler-test-'


@unittest.skipUnless(TEST_DB, 'TEST_DATABASE_URL sozlanmagan (faqat staging)')
class DueSelectionDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2
        cls.psycopg2 = psycopg2
        cls.conn = psycopg2.connect(TEST_DB)
        cls.conn.autocommit = False
        for path in (ARTICLES_MIGRATION, EXTERNAL_META_MIGRATION, SYNC_MIGRATION,
                    DASHBOARD_MIGRATION, SCHEDULER_MIGRATION):
            with open(path, encoding='utf-8') as fh:
                sql = fh.read()
            with cls.conn.cursor() as cur:
                cur.execute(sql)
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

    def make_article(self, suffix, *, doi=None, publication_status='draft'):
        self.cur.execute(
            """INSERT INTO scientific_articles (title, slug, doi, publication_status)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            ('ZZ Scheduler Test %s' % suffix, SLUG_PREFIX + suffix, doi,
             publication_status))
        return self.cur.fetchone()[0]

    def make_sync(self, article_id, *, checked_at_offset=0):
        self.cur.execute(
            """INSERT INTO article_metadata_syncs
                  (article_id, checked_by, actor_type, checked_at)
               VALUES (%s, NULL, 'system',
                       NOW() + (%s || ' days')::interval)""",
            (article_id, checked_at_offset))

    def test_never_checked_article_is_due(self):
        from services.article_sync import scheduler_persistence
        article_id = self.make_article('never-checked', doi='10.9/nc')
        due = scheduler_persistence.select_due_article_ids(self.cur, 30, 100)
        self.assertIn(article_id, due)

    def test_recently_checked_article_is_not_due(self):
        from services.article_sync import scheduler_persistence
        article_id = self.make_article('recent', doi='10.9/recent')
        self.make_sync(article_id, checked_at_offset=0)
        due = scheduler_persistence.select_due_article_ids(self.cur, 30, 100)
        self.assertNotIn(article_id, due)

    def test_old_check_is_due_again(self):
        from services.article_sync import scheduler_persistence
        article_id = self.make_article('old', doi='10.9/old')
        self.make_sync(article_id, checked_at_offset=-40)
        due = scheduler_persistence.select_due_article_ids(self.cur, 30, 100)
        self.assertIn(article_id, due)

    def test_missing_doi_excluded(self):
        from services.article_sync import scheduler_persistence
        article_id = self.make_article('no-doi', doi=None)
        due = scheduler_persistence.select_due_article_ids(self.cur, 30, 100)
        self.assertNotIn(article_id, due)

    def test_archived_excluded(self):
        from services.article_sync import scheduler_persistence
        article_id = self.make_article('archived', doi='10.9/archived',
                                       publication_status='archived')
        due = scheduler_persistence.select_due_article_ids(self.cur, 30, 100)
        self.assertNotIn(article_id, due)

    def test_never_checked_ordered_before_old_checked(self):
        from services.article_sync import scheduler_persistence
        old_checked = self.make_article('order-old', doi='10.9/order-old')
        self.make_sync(old_checked, checked_at_offset=-40)
        never_checked = self.make_article('order-never', doi='10.9/order-never')
        due = scheduler_persistence.select_due_article_ids(self.cur, 30, 100)
        self.assertLess(due.index(never_checked), due.index(old_checked))

    def test_batch_limit_is_respected(self):
        from services.article_sync import scheduler_persistence
        for i in range(5):
            self.make_article('batch-%d' % i, doi='10.9/batch-%d' % i)
        due = scheduler_persistence.select_due_article_ids(self.cur, 30, 2)
        self.assertEqual(len(due), 2)

    def test_article_id_debug_mode_ignores_due_window(self):
        from services.article_sync import scheduler_persistence
        article_id = self.make_article('debug', doi='10.9/debug')
        self.make_sync(article_id, checked_at_offset=0)  # recently checked
        due = scheduler_persistence.select_due_article_ids(
            self.cur, 30, 100, article_id=article_id)
        self.assertEqual(due, [article_id])


if __name__ == '__main__':
    unittest.main()
