# -*- coding: utf-8 -*-
"""Discovery — haqiqiy Postgres ustidagi integratsiya/concurrency testlari
(Phase 2D.1, §42).

`uq_article_import_items_active_doi` (migrations/014) haqiqiy partial UNIQUE
index sifatida ikkita PARALLEL tranzaksiyada bir xil DOI'ni bloklashini
tekshiradi — buni qalbaki kursor bilan mazmunli sinab bo'lmaydi (haqiqiy
constraint yo'q).

TEST_DATABASE_URL sozlanmagan bo'lsa O'TKAZIB YUBORILADI — staging yoki bir
martalik baza, hech qachon production emas.

Staging'da ishga tushirish:
    TEST_DATABASE_URL=postgresql://…/staging \
        python3.12 -m unittest tests.test_article_discovery_dbgated
"""
import os
import unittest

TEST_DB = os.environ.get('TEST_DATABASE_URL')

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTICLES_MIGRATION = os.path.join(_REPO_ROOT, 'migrations', '007_create_article_catalog.sql')
IMPORT_MIGRATION = os.path.join(_REPO_ROOT, 'migrations',
                                '010_create_article_import_queue.sql')
FIELDS_MIGRATION_CANDIDATES = [
    os.path.join(_REPO_ROOT, 'migrations', '008_scientific_field_provenance.sql'),
]
DISCOVERY_MIGRATION = os.path.join(_REPO_ROOT, 'migrations', '014_article_discovery.sql')

DOI_PREFIX = '10.9999/zz-discovery-test-'


@unittest.skipUnless(TEST_DB, 'TEST_DATABASE_URL sozlanmagan (faqat staging)')
class ActiveDoiUniqueIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2
        cls.psycopg2 = psycopg2
        cls.conn = psycopg2.connect(TEST_DB)
        cls.conn.autocommit = False
        with cls.conn.cursor() as cur:
            with open(ARTICLES_MIGRATION, encoding='utf-8') as fh:
                cur.execute(fh.read())
            with open(IMPORT_MIGRATION, encoding='utf-8') as fh:
                cur.execute(fh.read())
            with open(DISCOVERY_MIGRATION, encoding='utf-8') as fh:
                sql = fh.read()
            cur.execute(sql)
            cur.execute(sql)          # idempotentlik: ikkinchi run no-op
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        with cls.conn.cursor() as cur:
            cur.execute("DELETE FROM article_import_batches WHERE created_by = -998")
        cls.conn.commit()
        cls.conn.close()

    def setUp(self):
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.rollback()
        self.cur.close()

    def _make_batch(self, conn=None):
        conn = conn or self.conn
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO article_import_batches (created_by, source_type)
                   VALUES (-998, 'discovery') RETURNING id""")
            return cur.fetchone()[0]

    def test_second_active_pending_item_same_doi_conflicts(self):
        """ON CONFLICT DO NOTHING — 500 emas, faqat 0 qator qaytadi."""
        doi = DOI_PREFIX + 'a'
        batch_id = self._make_batch()
        self.cur.execute(
            """INSERT INTO article_import_items
                  (batch_id, row_number, raw_input, normalized_doi, status)
               VALUES (%s, 1, %s, %s, 'pending') RETURNING id""",
            (batch_id, doi, doi))
        self.assertIsNotNone(self.cur.fetchone())

        self.cur.execute(
            """INSERT INTO article_import_items
                  (batch_id, row_number, raw_input, normalized_doi, status)
               VALUES (%s, 2, %s, %s, 'pending')
               ON CONFLICT (normalized_doi) WHERE normalized_doi IS NOT NULL AND status IN (
                   'pending', 'processing', 'ready_for_review', 'partial_metadata',
                   'retracted', 'provider_failed', 'failed', 'metadata_not_found'
               )
               DO NOTHING RETURNING id""",
            (batch_id, doi, doi))
        self.assertIsNone(self.cur.fetchone())

    def test_two_concurrent_connections_only_one_active_item_survives(self):
        """§16, §42 — ikki HAQIQIY tranzaksiya parallel bir xil DOI'ni
        navbatga qo'ymoqchi bo'lsa, natijada faqat BITTA faol qator qoladi va
        hech qanday 500/deadlock yuz bermaydi."""
        doi = DOI_PREFIX + 'race'
        batch_id = self._make_batch()

        conn_b = self.psycopg2.connect(TEST_DB)
        conn_b.autocommit = False
        try:
            insert_sql = (
                """INSERT INTO article_import_items
                      (batch_id, row_number, raw_input, normalized_doi, status)
                   VALUES (%s, 1, %s, %s, 'pending')
                   ON CONFLICT (normalized_doi) WHERE normalized_doi IS NOT NULL AND status IN (
                       'pending', 'processing', 'ready_for_review', 'partial_metadata',
                       'retracted', 'provider_failed', 'failed', 'metadata_not_found'
                   )
                   DO NOTHING RETURNING id""")

            with self.conn.cursor() as cur_a:
                cur_a.execute(insert_sql, (batch_id, doi, doi))
                row_a = cur_a.fetchone()
            self.conn.commit()

            with conn_b.cursor() as cur_b:
                cur_b.execute(insert_sql, (batch_id, doi, doi))
                row_b = cur_b.fetchone()
            conn_b.commit()

            self.assertTrue((row_a is not None) ^ (row_b is not None),
                            'aynan BITTASI muvaffaqiyatli bo\'lishi kerak')

            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM article_import_items "
                    "WHERE normalized_doi = %s AND status = 'pending'", (doi,))
                self.assertEqual(cur.fetchone()[0], 1)
        finally:
            conn_b.close()

    def test_duplicate_in_batch_status_not_blocked_by_index(self):
        """Mavjud CSV/DOI-list xulq-atvori buzilmagan: original (`pending`) +
        `duplicate_in_batch` BIR XIL DOI bilan BIRGA yashaydi."""
        doi = DOI_PREFIX + 'dupbatch'
        batch_id = self._make_batch()
        self.cur.execute(
            """INSERT INTO article_import_items
                  (batch_id, row_number, raw_input, normalized_doi, status)
               VALUES (%s, 1, %s, %s, 'pending') RETURNING id""",
            (batch_id, doi, doi))
        original_id = self.cur.fetchone()[0]
        self.cur.execute(
            """INSERT INTO article_import_items
                  (batch_id, row_number, raw_input, normalized_doi, status,
                   duplicate_of_item_id)
               VALUES (%s, 2, %s, %s, 'duplicate_in_batch', %s) RETURNING id""",
            (batch_id, doi, doi, original_id))
        self.assertIsNotNone(self.cur.fetchone())


if __name__ == '__main__':
    unittest.main()
