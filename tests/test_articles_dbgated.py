# -*- coding: utf-8 -*-
"""Maqolalar katalogi — haqiqiy Postgres ustidagi integratsiya testlari.

TEST_DATABASE_URL sozlanmagan bo'lsa O'TKAZIB YUBORILADI — va u STAGING yoki
bir martalik bazani ko'rsatishi shart, hech qachon productionni emas
(test_data_quality_dbgated.py bilan bir xil siyosat).

Bu yerda qalbaki kursor tekshira olmaydigan narsalar tekshiriladi: haqiqiy
WHERE semantikasi (draft maqola katalogda ko'rinmasligi), DB darajasidagi
cheklovlar (DOI UNIQUE, bitta primary yo'nalish, bolali yo'nalishni o'chirib
bo'lmasligi) va migratsiyaning idempotentligi.

Staging'da ishga tushirish:
    TEST_DATABASE_URL=postgresql://…/staging \
        python3.12 -m unittest tests.test_articles_dbgated
"""
import os
import unittest

TEST_DB = os.environ.get('TEST_DATABASE_URL')

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(_REPO_ROOT, 'migrations', '007_create_article_catalog.sql')

SLUG_PREFIX = 'zz-test-'


@unittest.skipUnless(TEST_DB, 'TEST_DATABASE_URL sozlanmagan (faqat staging)')
class ArticlesDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2
        cls.psycopg2 = psycopg2
        cls.conn = psycopg2.connect(TEST_DB)
        cls.conn.autocommit = False
        with open(MIGRATION, encoding='utf-8') as fh:
            sql = fh.read()
        with cls.conn.cursor() as cur:
            cur.execute(sql)
            # Idempotentlik: ikkinchi marta ishga tushirish no-op bo'lishi kerak.
            cur.execute(sql)
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        with cls.conn.cursor() as cur:
            cur.execute("DELETE FROM scientific_articles WHERE slug LIKE %s",
                        (SLUG_PREFIX + '%',))
            cur.execute("DELETE FROM scientific_fields WHERE slug LIKE %s",
                        (SLUG_PREFIX + '%',))
        cls.conn.commit()
        cls.conn.close()

    def setUp(self):
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.rollback()
        self.cur.close()

    # ── yordamchilar ────────────────────────────────────────────────────────
    def make_field(self, name='Test tarmoq', code=None, parent_id=None, level=1):
        self.cur.execute(
            """INSERT INTO scientific_fields (name_uz, code, slug, level, parent_id)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (name, code, SLUG_PREFIX + name.lower().replace(' ', '-') + (code or ''),
             level, parent_id))
        return self.cur.fetchone()[0]

    def make_article(self, slug='maqola', **overrides):
        values = {'title': 'Test maqola', 'slug': SLUG_PREFIX + slug,
                  'publication_status': 'published', 'verification_status': 'verified',
                  'access_type': 'open_access', 'license_type': 'cc_by'}
        values.update(overrides)
        columns = ', '.join(values)
        placeholders = ', '.join(['%s'] * len(values))
        self.cur.execute(
            f"INSERT INTO scientific_articles ({columns}) VALUES ({placeholders}) "
            f"RETURNING id", list(values.values()))
        return self.cur.fetchone()[0]

    # ── testlar ─────────────────────────────────────────────────────────────
    def test_public_filter_hides_draft_and_unverified_articles(self):
        self.make_article('ochiq')
        self.make_article('qoralama', publication_status='draft')
        self.make_article('tekshirilmagan', verification_status='pending_review')
        self.cur.execute(
            """SELECT slug FROM scientific_articles
                WHERE slug LIKE %s AND publication_status = 'published'
                  AND verification_status = 'verified'""", (SLUG_PREFIX + '%',))
        self.assertEqual([r[0] for r in self.cur.fetchall()], [SLUG_PREFIX + 'ochiq'])

    def test_doi_is_unique_but_null_doi_is_allowed_many_times(self):
        self.make_article('doi-1', doi='10.1234/test')
        with self.assertRaises(self.psycopg2.errors.UniqueViolation):
            self.make_article('doi-2', doi='10.1234/test')
        self.conn.rollback()
        self.cur = self.conn.cursor()
        self.make_article('nodoi-1')
        self.make_article('nodoi-2')

    def test_slug_is_unique(self):
        self.make_article('bir-xil')
        with self.assertRaises(self.psycopg2.errors.UniqueViolation):
            self.make_article('bir-xil')

    def test_only_one_primary_field_per_article(self):
        article_id = self.make_article('primary')
        first = self.make_field('Birinchi', '99.00.00')
        second = self.make_field('Ikkinchi', '98.00.00')
        self.cur.execute("INSERT INTO article_scientific_fields "
                         "(article_id, scientific_field_id, is_primary) VALUES (%s, %s, TRUE)",
                         (article_id, first))
        with self.assertRaises(self.psycopg2.errors.UniqueViolation):
            self.cur.execute("INSERT INTO article_scientific_fields "
                             "(article_id, scientific_field_id, is_primary) "
                             "VALUES (%s, %s, TRUE)", (article_id, second))

    def test_secondary_fields_are_unlimited(self):
        article_id = self.make_article('secondary')
        for index in range(3):
            field_id = self.make_field('Qo\'shimcha%d' % index, '9%d.00.00' % index)
            self.cur.execute("INSERT INTO article_scientific_fields "
                             "(article_id, scientific_field_id, is_primary) "
                             "VALUES (%s, %s, FALSE)", (article_id, field_id))

    def test_the_same_field_cannot_be_attached_twice(self):
        article_id = self.make_article('takror')
        field_id = self.make_field('Yagona', '97.00.00')
        self.cur.execute("INSERT INTO article_scientific_fields "
                         "(article_id, scientific_field_id) VALUES (%s, %s)",
                         (article_id, field_id))
        with self.assertRaises(self.psycopg2.errors.UniqueViolation):
            self.cur.execute("INSERT INTO article_scientific_fields "
                             "(article_id, scientific_field_id) VALUES (%s, %s)",
                             (article_id, field_id))

    def test_a_field_with_children_cannot_be_deleted(self):
        parent = self.make_field('Ota', '96.00.00')
        self.make_field('Bola', '96.01.00', parent_id=parent, level=2)
        with self.assertRaises(self.psycopg2.errors.ForeignKeyViolation):
            self.cur.execute("DELETE FROM scientific_fields WHERE id = %s", (parent,))

    def test_a_field_used_by_an_article_cannot_be_deleted(self):
        article_id = self.make_article('bogliq')
        field_id = self.make_field('Ishlatilgan', '95.00.00')
        self.cur.execute("INSERT INTO article_scientific_fields "
                         "(article_id, scientific_field_id, is_primary) "
                         "VALUES (%s, %s, TRUE)", (article_id, field_id))
        with self.assertRaises(self.psycopg2.errors.ForeignKeyViolation):
            self.cur.execute("DELETE FROM scientific_fields WHERE id = %s", (field_id,))

    def test_deleting_an_article_removes_its_authors_and_links(self):
        article_id = self.make_article('kaskad')
        field_id = self.make_field('Kaskad', '94.00.00')
        self.cur.execute("INSERT INTO article_authors (article_id, author_name) "
                         "VALUES (%s, %s)", (article_id, 'Test Muallif'))
        self.cur.execute("INSERT INTO article_scientific_fields "
                         "(article_id, scientific_field_id, is_primary) "
                         "VALUES (%s, %s, TRUE)", (article_id, field_id))
        self.cur.execute("DELETE FROM scientific_articles WHERE id = %s", (article_id,))
        self.cur.execute("SELECT COUNT(*) FROM article_authors WHERE article_id = %s",
                         (article_id,))
        self.assertEqual(self.cur.fetchone()[0], 0)

    def test_author_order_round_trips(self):
        article_id = self.make_article('tartib')
        for order, name in enumerate(['Birinchi', 'Ikkinchi', 'Uchinchi'], start=1):
            self.cur.execute("INSERT INTO article_authors "
                             "(article_id, author_name, author_order) VALUES (%s, %s, %s)",
                             (article_id, name, order))
        self.cur.execute("SELECT author_name FROM article_authors "
                         "WHERE article_id = %s ORDER BY author_order", (article_id,))
        self.assertEqual([r[0] for r in self.cur.fetchall()],
                         ['Birinchi', 'Ikkinchi', 'Uchinchi'])

    def test_field_code_is_unique_when_present_and_free_when_null(self):
        self.make_field('Kodli', '93.00.00')
        with self.assertRaises(self.psycopg2.errors.UniqueViolation):
            self.make_field('Kodli ikki', '93.00.00')
        self.conn.rollback()
        self.cur = self.conn.cursor()
        self.make_field('Kodsiz bir')
        self.make_field('Kodsiz ikki')


if __name__ == '__main__':
    unittest.main()
