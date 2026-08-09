# -*- coding: utf-8 -*-
"""Hardening audit (Phase 2C.2.1) — statik va servis darajasidagi
xavfsizlik kafolatlari, ilgari testda QULFLANMAGAN edi:

  * hech qanday PDF avtomatik yuklanmaydi (§40, §73);
  * tashqi OA holati ichki `access_type`ga aralashmaydi (§29, §41, §74);
  * `candidate_pdf_url` faqat informational — hech qachon `pdf_path`ga
    yozilmaydi va hech qanday HTTP so'rov manbai emas.

Statik testlar `test_articles_migration.py`dagi bilan bir xil naqsh:
bazaga tegmasdan modul manba kodini tekshiradi.
"""
import inspect
import unittest

from services.article_import import persistence
from tests.fake_article_import_pg import FakeImportCursor, new_store
from tests.test_article_import_service import TEST_CONFIG, make_fetcher
from services.article_import import service as svc

FORBIDDEN_IMPORTS = (
    'import requests', 'from requests', 'urllib.request', 'urlopen',
    'security_uploads',
)


class NoPdfDownloadStaticTests(unittest.TestCase):
    """`services/article_import/` PDF yuklab olish uchun kerak bo'ladigan
    HECH QANDAY vositani import qilmaydi — bu HTTP so'rov qilish
    IMKONSIZLIGINING statik isboti (§40, §73)."""

    def test_no_module_imports_an_http_client_or_pdf_saver(self):
        import services.article_import as pkg
        import os
        pkg_dir = os.path.dirname(pkg.__file__)
        for name in os.listdir(pkg_dir):
            if not name.endswith('.py'):
                continue
            with open(os.path.join(pkg_dir, name), encoding='utf-8') as fh:
                source = fh.read()
            for forbidden in FORBIDDEN_IMPORTS:
                self.assertNotIn(forbidden, source, '%s ichida %r' % (name, forbidden))

    def test_persistence_insert_never_touches_pdf_or_rights_columns(self):
        """`create_article_from_metadata` INSERT ustunlari orasida
        `pdf_path`/`access_type`/`license_type` YO'Q — ular DB DEFAULT
        qiymatida qoladi (`metadata_only`/`unknown`), forma orqali
        tanlanmasa o'zgarmaydi."""
        source = inspect.getsource(persistence.create_article_from_metadata)
        for forbidden_column in ('pdf_path', 'access_type', 'license_type',
                                 'pdf_original_name', 'checksum_sha256'):
            self.assertNotIn("'%s'" % forbidden_column, source, forbidden_column)


class OaAccessSeparationServiceTests(unittest.TestCase):
    """Tashqi OA holati (`item.oa_status`) va ichki `access_type` ORASIDA
    hech qanday avtomatik ko'prik yo'qligini SERVIS darajasida tekshiradi."""

    def setUp(self):
        self.store = new_store()
        self.cur = FakeImportCursor(self.store)
        self.batch_id = svc.create_import_batch(
            self.cur, created_by=1, source_type='doi_list', raw_text='10.1000/a',
            config=TEST_CONFIG)
        svc.process_import_batch(self.cur, self.batch_id, config=TEST_CONFIG,
                                 fetch=make_fetcher())
        self.item_id = svc.list_items(self.cur, self.batch_id)[0][0]['id']

    def test_approved_article_insert_has_no_access_or_license_params(self):
        svc.approve_import_item(self.cur, self.item_id, actor_id=1)
        insert_calls = [(sql, p) for sql, p in self.store['sql']
                        if sql.startswith('INSERT INTO scientific_articles')]
        self.assertEqual(len(insert_calls), 1)
        sql, params = insert_calls[0]
        self.assertNotIn('access_type', sql)
        self.assertNotIn('license_type', sql)
        self.assertNotIn('pdf_path', sql)
        # `doi` dan tashqari hech qanday qiymat oa_status/gold kabi tashqi
        # OA belgisiga ega emas — chunki ustun ro'yxatida access_type yo'q.
        self.assertNotIn('gold', [str(v) for v in params])

    def test_candidate_pdf_url_is_stored_only_on_the_item_never_the_article(self):
        svc.approve_import_item(self.cur, self.item_id, actor_id=1)
        # Item darajasida saqlanishi mumkin (informational) — lekin
        # `scientific_articles` INSERT ustunlari orasida UMUMAN yo'q.
        insert_sql = next(sql for sql, _p in self.store['sql']
                          if sql.startswith('INSERT INTO scientific_articles'))
        self.assertNotIn('candidate_pdf_url', insert_sql)
        self.assertNotIn('pdf_path', insert_sql)


if __name__ == '__main__':
    unittest.main()
