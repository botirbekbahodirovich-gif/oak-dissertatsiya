# -*- coding: utf-8 -*-
"""Ommaviy import navbati — haqiqiy Postgres ustidagi integratsiya testlari.

Hardening audit (Phase 2C.2.1), §1 — `WHERE doi = ANY(%s)` FAQAT
PostgreSQL'da haqiqiy execution bilan tekshiriladi (loyihada boshqa DB
backend YO'Q — `data.get_connection()` faqat psycopg2/Postgres, SQLite
ishlatilmaydi; qarang `docs/article-bulk-import.md`). Shunga qaramay, real
so'rov execution'ini `tests/test_articles_dbgated.py` bilan bir xil
konvensiyada qulflaydi.

TEST_DATABASE_URL sozlanmagan bo'lsa O'TKAZIB YUBORILADI — staging yoki
bir martalik baza, hech qachon production emas.

Staging'da ishga tushirish:
    TEST_DATABASE_URL=postgresql://…/staging \
        python3.12 -m unittest tests.test_article_import_dbgated
"""
import os
import unittest

TEST_DB = os.environ.get('TEST_DATABASE_URL')

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTICLES_MIGRATION = os.path.join(_REPO_ROOT, 'migrations', '007_create_article_catalog.sql')
IMPORT_MIGRATION = os.path.join(_REPO_ROOT, 'migrations',
                                '010_create_article_import_queue.sql')

SLUG_PREFIX = 'zz-import-test-'
DOI_PREFIX = '10.9999/zz-import-test-'


@unittest.skipUnless(TEST_DB, 'TEST_DATABASE_URL sozlanmagan (faqat staging)')
class ArticleImportDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2
        cls.psycopg2 = psycopg2
        cls.conn = psycopg2.connect(TEST_DB)
        cls.conn.autocommit = False
        with open(ARTICLES_MIGRATION, encoding='utf-8') as fh:
            articles_sql = fh.read()
        with open(IMPORT_MIGRATION, encoding='utf-8') as fh:
            import_sql = fh.read()
        with cls.conn.cursor() as cur:
            cur.execute(articles_sql)
            cur.execute(import_sql)
            # Idempotentlik: ikkinchi marta ishga tushirish no-op bo'lishi kerak.
            cur.execute(import_sql)
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        with cls.conn.cursor() as cur:
            cur.execute("DELETE FROM article_import_batches WHERE created_by = -999")
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
            ('ZZ Import Test %s' % suffix, SLUG_PREFIX + suffix, doi))
        return self.cur.fetchone()[0]

    def make_batch(self):
        self.cur.execute(
            """INSERT INTO article_import_batches (created_by, source_type)
               VALUES (-999, 'doi_list') RETURNING id""")
        return self.cur.fetchone()[0]

    # ── ANY(%s) real execution (§1) ──────────────────────────────────────

    def test_any_query_with_zero_dois(self):
        from services.article_import.deduplicator import find_existing_db_duplicates
        self.assertEqual(find_existing_db_duplicates(self.cur, []), {})

    def test_any_query_with_one_doi_matches(self):
        from services.article_import.deduplicator import find_existing_db_duplicates
        doi = DOI_PREFIX + 'one'
        self.make_article('one', doi=doi)
        out = find_existing_db_duplicates(self.cur, [doi])
        self.assertIn(doi, out)

    def test_any_query_with_many_dois_and_a_miss(self):
        from services.article_import.deduplicator import find_existing_db_duplicates
        doi_a, doi_b = DOI_PREFIX + 'a', DOI_PREFIX + 'b'
        self.make_article('a', doi=doi_a)
        self.make_article('b', doi=doi_b)
        out = find_existing_db_duplicates(
            self.cur, [doi_a, doi_b, DOI_PREFIX + 'missing'])
        self.assertEqual(set(out.keys()), {doi_a, doi_b})

    def test_any_query_deduplicates_repeated_input_dois(self):
        from services.article_import.deduplicator import find_existing_db_duplicates
        doi = DOI_PREFIX + 'dup'
        self.make_article('dup', doi=doi)
        out = find_existing_db_duplicates(self.cur, [doi, doi, doi])
        self.assertEqual(list(out.keys()), [doi])

    def test_any_query_is_parameterized_not_string_interpolated(self):
        """SQL-injection sog'lig'i: DOI ichida `'; DROP TABLE` bo'lsa ham
        xavfsiz — chunki `= ANY(%s)` parametr, string interpolation emas."""
        from services.article_import.deduplicator import find_existing_db_duplicates
        malicious = "10.1/x'; DROP TABLE scientific_articles; --"
        out = find_existing_db_duplicates(self.cur, [malicious])
        self.assertEqual(out, {})
        self.cur.execute("SELECT 1 FROM scientific_articles LIMIT 1")  # jadval tirik

    # ── Migration 010 xususiyatlari ──────────────────────────────────────

    def test_batch_status_and_source_type_defaults(self):
        batch_id = self.make_batch()
        self.cur.execute("SELECT status, source_type, cancel_requested "
                         "FROM article_import_batches WHERE id = %s", (batch_id,))
        status, source_type, cancel_requested = self.cur.fetchone()
        self.assertEqual(status, 'draft')
        self.assertEqual(source_type, 'doi_list')
        self.assertFalse(cancel_requested)

    def test_item_cascade_deletes_with_batch(self):
        batch_id = self.make_batch()
        self.cur.execute(
            """INSERT INTO article_import_items (batch_id, row_number, raw_input)
               VALUES (%s, 1, '10.1/x') RETURNING id""", (batch_id,))
        item_id = self.cur.fetchone()[0]
        self.cur.execute("DELETE FROM article_import_batches WHERE id = %s", (batch_id,))
        self.cur.execute("SELECT 1 FROM article_import_items WHERE id = %s", (item_id,))
        self.assertIsNone(self.cur.fetchone())

    def test_deleting_referenced_article_sets_existing_article_id_null(self):
        article_id = self.make_article('ref')
        batch_id = self.make_batch()
        self.cur.execute(
            """INSERT INTO article_import_items
                  (batch_id, row_number, raw_input, existing_article_id)
               VALUES (%s, 1, '10.1/x', %s) RETURNING id""", (batch_id, article_id))
        item_id = self.cur.fetchone()[0]
        self.cur.execute("DELETE FROM scientific_articles WHERE id = %s", (article_id,))
        self.cur.execute("SELECT existing_article_id FROM article_import_items "
                         "WHERE id = %s", (item_id,))
        self.assertIsNone(self.cur.fetchone()[0])

    def test_doi_unique_constraint_blocks_concurrent_duplicate_insert(self):
        """§8/§10 approve race — DB darajasidagi YAKUNIY himoya."""
        doi = DOI_PREFIX + 'race'
        self.make_article('race-1', doi=doi)
        with self.assertRaises(self.psycopg2.IntegrityError):
            self.make_article('race-2', doi=doi)


if __name__ == '__main__':
    unittest.main()
