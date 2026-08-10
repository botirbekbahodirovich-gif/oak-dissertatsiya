# -*- coding: utf-8 -*-
"""Scheduled article discovery — haqiqiy Postgres ustidagi
concurrency/integratsiya testlari (Phase 2D.3, §5, §21).

`FOR UPDATE SKIP LOCKED` va `pg_try_advisory_lock` poyga xatti-harakati —
Postgres-ga XOS semantika, qalbaki kursur bilan MAZMUNLI sinab bo'lmaydi
(`tests/test_article_discovery_dbgated.py`, Phase 2D.1 bilan bir xil
siyosat). Shu fayl ISBOTLAYDI: ikkita HAQIQIY parallel ulanish bir xil due
saqlangan so'rovni bir vaqtda muvaffaqiyatli band qila OLMAYDI.

TEST_DATABASE_URL sozlanmagan bo'lsa O'TKAZIB YUBORILADI — staging yoki bir
martalik baza, hech qachon production emas.
"""
import os
import unittest
from datetime import datetime, timedelta, timezone

TEST_DB = os.environ.get('TEST_DATABASE_URL')

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTICLES_MIGRATION = os.path.join(_REPO_ROOT, 'migrations', '008_create_article_catalog.sql')
FIELDS_MIGRATION = os.path.join(_REPO_ROOT, 'migrations', '009_scientific_field_provenance.sql')
IMPORT_MIGRATION = os.path.join(_REPO_ROOT, 'migrations', '011_create_article_import_queue.sql')
DISCOVERY_MIGRATION = os.path.join(_REPO_ROOT, 'migrations', '015_article_discovery.sql')
BATCH_IDEMPOTENCY_MIGRATION = os.path.join(
    _REPO_ROOT, 'migrations', '016_article_discovery_batch_idempotency.sql')
SAVED_QUERIES_MIGRATION = os.path.join(
    _REPO_ROOT, 'migrations', '017_article_discovery_saved_queries.sql')
SCHEDULER_MIGRATION = os.path.join(
    _REPO_ROOT, 'migrations', '018_article_discovery_scheduler.sql')

ALL_MIGRATIONS = (ARTICLES_MIGRATION, FIELDS_MIGRATION, IMPORT_MIGRATION,
                  DISCOVERY_MIGRATION, BATCH_IDEMPOTENCY_MIGRATION,
                  SAVED_QUERIES_MIGRATION, SCHEDULER_MIGRATION)

NAME_PREFIX = 'zz-discovery-scheduler-test-'


@unittest.skipUnless(TEST_DB, 'TEST_DATABASE_URL sozlanmagan (faqat staging)')
class ClaimConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2
        cls.psycopg2 = psycopg2
        cls.conn = psycopg2.connect(TEST_DB)
        cls.conn.autocommit = False
        with cls.conn.cursor() as cur:
            for path in ALL_MIGRATIONS:
                with open(path, encoding='utf-8') as fh:
                    cur.execute(fh.read())
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        with cls.conn.cursor() as cur:
            cur.execute(
                "DELETE FROM article_discovery_saved_queries WHERE name LIKE %s",
                (NAME_PREFIX + '%',))
        cls.conn.commit()
        cls.conn.close()

    def setUp(self):
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.rollback()
        self.cur.close()

    def _make_due_saved_query(self, suffix, *, offset_minutes=-5):
        self.cur.execute(
            """INSERT INTO article_discovery_saved_queries
                  (name, created_by, keyword, from_date, to_date, providers,
                   result_limit, schedule_enabled, schedule_frequency, next_run_at)
               VALUES (%s, -999, 'oncology', '2024-01-01', '2024-01-31',
                       '["openalex"]'::jsonb, 10, TRUE, 'daily',
                       NOW() + (%s || ' minutes')::interval)
               RETURNING id""",
            (NAME_PREFIX + suffix, offset_minutes))
        self.conn.commit()
        return self.cur.fetchone()[0]

    CLAIM_SQL = (
        """SELECT id FROM article_discovery_saved_queries
           WHERE schedule_enabled AND is_active AND next_run_at <= NOW()
             AND id = %s
           ORDER BY next_run_at ASC
           LIMIT 1 FOR UPDATE SKIP LOCKED""")

    def test_for_update_skip_locked_prevents_double_claim(self):
        """§5, §21 — ikkita HAQIQIY parallel tranzaksiya bir xil due
        saqlangan so'rovni band qilmoqchi bo'lsa, faqat BITTASI muvaffaqiyat
        qozonadi (SKIP LOCKED — bloklanib kutish EMAS, boshqasi bo'sh
        ro'yxat oladi)."""
        from services.article_discovery import scheduler_persistence
        query_id = self._make_due_saved_query('race')

        conn_b = self.psycopg2.connect(TEST_DB)
        conn_b.autocommit = False
        try:
            with self.conn.cursor() as cur_a:
                claimed_a = scheduler_persistence.claim_due_queries(cur_a, 10)
            # A hali COMMIT qilmagan — B ni ushbu qatorni ko'rishdan SKIP
            # LOCKED orqali bloklaydi (kutmasdan, bo'sh natija bilan).
            with conn_b.cursor() as cur_b:
                claimed_b = scheduler_persistence.claim_due_queries(cur_b, 10)
            self.conn.commit()
            conn_b.commit()

            self.assertIn(query_id, claimed_a)
            self.assertNotIn(query_id, claimed_b)
        finally:
            conn_b.close()

    def test_claimed_row_pushed_out_of_due_window_after_commit(self):
        from services.article_discovery import scheduler_persistence
        query_id = self._make_due_saved_query('claimed-then-due-check')

        with self.conn.cursor() as cur:
            claimed = scheduler_persistence.claim_due_queries(cur, 10)
        self.conn.commit()
        self.assertIn(query_id, claimed)

        with self.conn.cursor() as cur:
            still_due = scheduler_persistence.claim_due_queries(cur, 10)
        self.conn.commit()
        self.assertNotIn(query_id, still_due)

    def test_future_query_never_claimed_by_either_connection(self):
        from services.article_discovery import scheduler_persistence
        query_id = self._make_due_saved_query('future', offset_minutes=+120)

        conn_b = self.psycopg2.connect(TEST_DB)
        conn_b.autocommit = False
        try:
            with self.conn.cursor() as cur_a:
                claimed_a = scheduler_persistence.claim_due_queries(cur_a, 10)
            with conn_b.cursor() as cur_b:
                claimed_b = scheduler_persistence.claim_due_queries(cur_b, 10)
            self.conn.commit()
            conn_b.commit()
            self.assertNotIn(query_id, claimed_a)
            self.assertNotIn(query_id, claimed_b)
        finally:
            conn_b.close()


@unittest.skipUnless(TEST_DB, 'TEST_DATABASE_URL sozlanmagan (faqat staging)')
class AdvisoryLockConcurrencyTests(unittest.TestCase):
    """`services.article_discovery.scheduler`ning global
    `pg_try_advisory_lock` — ikkita HAQIQIY ulanish bir vaqtda tsikl
    ishlata OLMAYDI (§5)."""

    @classmethod
    def setUpClass(cls):
        import psycopg2
        cls.psycopg2 = psycopg2
        cls.conn = psycopg2.connect(TEST_DB)
        cls.conn.autocommit = False

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_second_connection_cannot_acquire_held_lock(self):
        from services.article_discovery.scheduler import _try_acquire_lock, _release_lock

        conn_b = self.psycopg2.connect(TEST_DB)
        conn_b.autocommit = False
        try:
            with self.conn.cursor() as cur_a:
                acquired_a = _try_acquire_lock(cur_a)
            self.conn.commit()
            self.assertTrue(acquired_a)

            with conn_b.cursor() as cur_b:
                acquired_b = _try_acquire_lock(cur_b)
            conn_b.commit()
            self.assertFalse(acquired_b)
        finally:
            with self.conn.cursor() as cur_a:
                _release_lock(cur_a)
            self.conn.commit()
            conn_b.close()

    def test_lock_available_again_after_release(self):
        from services.article_discovery.scheduler import _try_acquire_lock, _release_lock

        with self.conn.cursor() as cur:
            acquired = _try_acquire_lock(cur)
            self.conn.commit()
            self.assertTrue(acquired)
            _release_lock(cur)
            self.conn.commit()

        conn_b = self.psycopg2.connect(TEST_DB)
        conn_b.autocommit = False
        try:
            with conn_b.cursor() as cur_b:
                acquired_again = _try_acquire_lock(cur_b)
            conn_b.commit()
            self.assertTrue(acquired_again)
            with conn_b.cursor() as cur_b:
                _release_lock(cur_b)
            conn_b.commit()
        finally:
            conn_b.close()


if __name__ == '__main__':
    unittest.main()
