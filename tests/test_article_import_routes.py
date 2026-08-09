# -*- coding: utf-8 -*-
"""Import admin route'lari — avtorizatsiya, CSRF va asosiy oqim (§71, §75).

`services.article_metadata.fetch_article_metadata` tarmoqqa chiqmasligi
uchun patchlanadi (metadata provider testlari allaqachon buni alohida
qamrab olgan — bu yerda faqat marshrutlash/avtorizatsiya tekshiriladi).
"""
import contextlib
import os
import unittest
from unittest import mock

os.environ.setdefault('APP_ENV', 'testing')
os.environ.setdefault('SESSION_SECRET', 'article-import-test-secret')

from app import app  # noqa: E402
from services.article_import import service as import_service  # noqa: E402
from services.article_import.config import ArticleImportConfig  # noqa: E402
from services.article_metadata.models import (LOOKUP_OK, AuthorCandidate,
                                              MetadataLookupResult,
                                              NormalizedArticleMetadata)  # noqa: E402
from tests.fake_article_import_pg import FakeImportConnection, new_store  # noqa: E402
from tests.test_articles_admin import AdminTestCase  # noqa: E402

TEST_CONFIG = ArticleImportConfig(max_items=500, chunk_size=10, max_attempts=3)

IMPORT_GET_ROUTES = [
    '/admin/articles/import',
    '/admin/articles/imports',
]

IMPORT_POST_ROUTES = [
    '/admin/articles/import',
]


@contextlib.contextmanager
def fake_import_db(connection):
    with mock.patch('blueprints.article_import.get_connection', return_value=connection), \
            mock.patch('data.get_connection', return_value=connection), \
            mock.patch('services.article_import.persistence._schema_ready', False):
        yield


def fake_metadata_result(doi, *, config=None, cache_get=None, cache_set=None):
    metadata = NormalizedArticleMetadata(
        doi=doi, title='Sample Title', journal_name='Test Journal',
        publication_year=2024, authors=[AuthorCandidate(name='Karimov Sardor')])
    return MetadataLookupResult(doi=doi, status=LOOKUP_OK, metadata=metadata,
                                providers={}, conflicts=[], warnings=[])


class AuthorizationTests(AdminTestCase):
    def test_anonymous_is_login_walled(self):
        for path in IMPORT_GET_ROUTES:
            response = self.client.get(path, follow_redirects=False)
            self.assertIn(response.status_code, (302, 401), path)

    def test_non_admin_gets_403(self):
        self.login(is_admin=False)
        for path in IMPORT_GET_ROUTES:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 403, path)

    def test_non_admin_cannot_post(self):
        self.login(is_admin=False)
        for path in IMPORT_POST_ROUTES:
            response = self.client.post(path, data={})
            self.assertIn(response.status_code, (400, 403), path)

    def test_admin_can_view_import_form(self):
        self.login(is_admin=True)
        response = self.client.get('/admin/articles/import')
        self.assertEqual(response.status_code, 200)

    def test_admin_can_view_import_history_empty(self):
        self.login(is_admin=True)
        connection = FakeImportConnection(new_store())
        with fake_import_db(connection):
            response = self.client.get('/admin/articles/imports')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Hali ommaviy import yaratilmagan', response.get_data(as_text=True))


class CsrfTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self._orig_csrf = app.config.get('WTF_CSRF_ENABLED', True)
        app.config['WTF_CSRF_ENABLED'] = True

    def tearDown(self):
        app.config['WTF_CSRF_ENABLED'] = self._orig_csrf
        super().tearDown()

    def test_post_without_csrf_token_is_rejected(self):
        self.login(is_admin=True)
        response = self.client.post('/admin/articles/import',
                                    data={'source_type': 'doi_list', 'doi_list': '10.1000/a'})
        self.assertEqual(response.status_code, 400)


class ImportFlowTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self._orig_csrf = app.config.get('WTF_CSRF_ENABLED', True)
        app.config['WTF_CSRF_ENABLED'] = False
        self.login(is_admin=True)

    def tearDown(self):
        app.config['WTF_CSRF_ENABLED'] = self._orig_csrf
        super().tearDown()

    def test_create_batch_redirects_to_review_queue(self):
        connection = FakeImportConnection(new_store())
        with fake_import_db(connection):
            response = self.client.post(
                '/admin/articles/import',
                data={'source_type': 'doi_list', 'doi_list': '10.1000/a\n10.1000/b'},
                follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/articles/imports/', response.headers['Location'])

    def test_empty_doi_list_shows_error_not_500(self):
        connection = FakeImportConnection(new_store())
        with fake_import_db(connection):
            response = self.client.post(
                '/admin/articles/import',
                data={'source_type': 'doi_list', 'doi_list': ''})
        self.assertEqual(response.status_code, 400)

    def test_full_flow_create_process_approve(self):
        store = new_store()
        connection = FakeImportConnection(store)
        with fake_import_db(connection):
            create_resp = self.client.post(
                '/admin/articles/import',
                data={'source_type': 'doi_list', 'doi_list': '10.1000/a'},
                follow_redirects=False)
            batch_id = int(create_resp.headers['Location'].rstrip('/').rsplit('/', 1)[-1])

            detail_resp = self.client.get('/admin/articles/imports/%d' % batch_id)
            self.assertEqual(detail_resp.status_code, 200)

            # `/process` haqiqiy tarmoqqa chiqadigan `fetch_article_metadata`
            # default parametrini chaqiradi (route uni override qilmaydi —
            # bu qasddan: servis default holatda haqiqiy provayderlarni
            # chaqiradi). Shu sababli HTTP orqali emas, servis funksiyasini
            # to'g'ridan-to'g'ri (tarmoqsiz `fetch`) chaqiramiz — xuddi shu
            # tranzaksiya semantikasi, faqat tarmoqsiz.
            cur = connection.cursor()
            import_service.process_import_batch(cur, batch_id, config=TEST_CONFIG,
                                                 fetch=fake_metadata_result)
            connection.commit()

            item_id = store['items'][0]['id']
            approve_resp = self.client.post(
                '/admin/articles/imports/%d/items/%d/approve' % (batch_id, item_id),
                data={}, follow_redirects=False)
            self.assertEqual(approve_resp.status_code, 302)
            self.assertEqual(len(store['articles']), 1)
            self.assertEqual(store['articles'][0]['doi'], '10.1000/a')

    def test_double_approve_via_http_does_not_duplicate(self):
        store = new_store()
        connection = FakeImportConnection(store)
        with fake_import_db(connection):
            create_resp = self.client.post(
                '/admin/articles/import',
                data={'source_type': 'doi_list', 'doi_list': '10.1000/a'},
                follow_redirects=False)
            batch_id = int(create_resp.headers['Location'].rstrip('/').rsplit('/', 1)[-1])
            cur = connection.cursor()
            import_service.process_import_batch(cur, batch_id, config=TEST_CONFIG,
                                                 fetch=fake_metadata_result)
            connection.commit()
            item_id = store['items'][0]['id']
            self.client.post('/admin/articles/imports/%d/items/%d/approve' % (batch_id, item_id))
            self.client.post('/admin/articles/imports/%d/items/%d/approve' % (batch_id, item_id))
        self.assertEqual(len(store['articles']), 1)

    def test_item_detail_page_renders(self):
        store = new_store()
        connection = FakeImportConnection(store)
        with fake_import_db(connection):
            create_resp = self.client.post(
                '/admin/articles/import',
                data={'source_type': 'doi_list', 'doi_list': '10.1000/a'},
                follow_redirects=False)
            batch_id = int(create_resp.headers['Location'].rstrip('/').rsplit('/', 1)[-1])
            cur = connection.cursor()
            import_service.process_import_batch(cur, batch_id, config=TEST_CONFIG,
                                                 fetch=fake_metadata_result)
            connection.commit()
            item_id = store['items'][0]['id']
            resp = self.client.get(
                '/admin/articles/imports/%d/items/%d' % (batch_id, item_id))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Sample Title', resp.get_data(as_text=True))

    def test_retracted_item_requires_explicit_confirmation(self):
        def retracted_fetch(doi, **kwargs):
            result = fake_metadata_result(doi, **kwargs)
            result.metadata.is_retracted = True
            return result

        store = new_store()
        connection = FakeImportConnection(store)
        with fake_import_db(connection):
            create_resp = self.client.post(
                '/admin/articles/import',
                data={'source_type': 'doi_list', 'doi_list': '10.1000/a'},
                follow_redirects=False)
            batch_id = int(create_resp.headers['Location'].rstrip('/').rsplit('/', 1)[-1])
            cur = connection.cursor()
            import_service.process_import_batch(cur, batch_id, config=TEST_CONFIG,
                                                 fetch=retracted_fetch)
            connection.commit()
            item_id = store['items'][0]['id']

            detail_resp = self.client.get(
                '/admin/articles/imports/%d/items/%d' % (batch_id, item_id))
            self.assertEqual(detail_resp.status_code, 200)
            self.assertIn('retracted', detail_resp.get_data(as_text=True))

            self.client.post(
                '/admin/articles/imports/%d/items/%d/approve' % (batch_id, item_id))
            self.assertEqual(store['articles'], [])

            self.client.post(
                '/admin/articles/imports/%d/items/%d/approve' % (batch_id, item_id),
                data={'confirm_retracted': '1'})
            self.assertEqual(len(store['articles']), 1)


if __name__ == '__main__':
    unittest.main()
