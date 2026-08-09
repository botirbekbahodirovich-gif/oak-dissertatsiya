# -*- coding: utf-8 -*-
"""Admin sinxronizatsiya endpointlari — avtorizatsiya, CSRF, asosiy oqim
(Phase 2C.3A, §28).
"""
import contextlib
import os
import unittest
from unittest import mock

os.environ.setdefault('APP_ENV', 'testing')
os.environ.setdefault('SESSION_SECRET', 'article-sync-test-secret')

from app import app  # noqa: E402
from services.article_sync import service as sync_service  # noqa: E402
from services.article_metadata.models import (LOOKUP_OK, AuthorCandidate,
                                              MetadataLookupResult,
                                              NormalizedArticleMetadata)  # noqa: E402
from tests.fake_article_sync_pg import FakeSyncConnection, new_store  # noqa: E402
from tests.test_article_sync_service import base_article_row  # noqa: E402
from tests.test_articles_admin import AdminTestCase  # noqa: E402

CHECK_URL = '/admin/articles/1/metadata/check'
APPLY_URL = '/admin/articles/1/metadata/apply'


@contextlib.contextmanager
def fake_sync_db(connection):
    # Rate limiter Flask-Caching FileSystemCache'da yashaydi va testlar
    # orasida SAQLANIB QOLADI (xuddi test_article_metadata_admin.py'dagi
    # izoh) — DOI autofill lookup bilan BIR XIL hisoblagichni ishlatadi
    # (`_metadata_rate_limited`), shuning uchun bu yerda ham o'chiriladi.
    with mock.patch('blueprints.articles.get_connection', return_value=connection), \
            mock.patch('data.get_connection', return_value=connection), \
            mock.patch('services.article_sync.persistence._schema_ready', False), \
            mock.patch('blueprints.articles._metadata_rate_limited', return_value=False):
        yield


def fake_fetch(doi, *, config=None, cache_get=None, cache_set=None):
    metadata = NormalizedArticleMetadata(
        doi=doi, title='New Title', citation_count=41,
        citation_count_source='openalex',
        authors=[AuthorCandidate(name='John Smith')])
    return MetadataLookupResult(doi=doi, status=LOOKUP_OK, metadata=metadata,
                                providers={}, conflicts=[], warnings=[])


class AuthorizationTests(AdminTestCase):
    """CSRF global `before_request`da ishlaydi — u view function/decorator
    ishga tushishidan OLDIN. Shuning uchun 400 (CSRF) ham 302/401/403 bilan
    bir xil "rad etildi" natijasi (xuddi `test_articles_admin.py`dagi
    `test_non_admin_cannot_post_either` konvensiyasi — 200/302 EMASligi
    muhim, aniq kod emas)."""

    def test_anonymous_denied_on_check(self):
        response = self.client.post(CHECK_URL, json={})
        self.assertIn(response.status_code, (302, 400, 401))

    def test_anonymous_denied_on_apply(self):
        response = self.client.post(APPLY_URL, json={})
        self.assertIn(response.status_code, (302, 400, 401))

    def test_non_admin_denied_on_check(self):
        self.login(is_admin=False)
        response = self.client.post(CHECK_URL, json={})
        self.assertIn(response.status_code, (400, 403))

    def test_non_admin_denied_on_apply(self):
        self.login(is_admin=False)
        response = self.client.post(APPLY_URL, json={})
        self.assertIn(response.status_code, (400, 403))


class CsrfTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self._orig_csrf = app.config.get('WTF_CSRF_ENABLED', True)
        app.config['WTF_CSRF_ENABLED'] = True

    def tearDown(self):
        app.config['WTF_CSRF_ENABLED'] = self._orig_csrf
        super().tearDown()

    def test_check_requires_csrf_token(self):
        self.login(is_admin=True)
        response = self.client.post(CHECK_URL, data='{}',
                                    content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_apply_requires_csrf_token(self):
        self.login(is_admin=True)
        response = self.client.post(APPLY_URL, data='{}',
                                    content_type='application/json')
        self.assertEqual(response.status_code, 400)


class SyncFlowTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self._orig_csrf = app.config.get('WTF_CSRF_ENABLED', True)
        app.config['WTF_CSRF_ENABLED'] = False
        self.login(is_admin=True)

    def tearDown(self):
        app.config['WTF_CSRF_ENABLED'] = self._orig_csrf
        super().tearDown()

    def _checked_sync_id(self, cur):
        """`fetch_article_metadata`ning `services.article_sync.service`
        ichidagi default parametri IMPORT vaqtida ANIQ obyektga bog'lanadi —
        `mock.patch('services.article_metadata.fetch_article_metadata', ...)`
        keyinchalik chaqirilsa ham bu already-bound default'ni O'ZGARTIRMAYDI
        (Python semantikasi). Shuning uchun tarmoqsiz `fetch` sync yozuvini
        to'g'ridan-to'g'ri servis orqali (HTTP'siz) yaratamiz — xuddi
        `test_article_import_routes.py`dagi bir xil, allaqachon tasdiqlangan
        yechim bilan. `/check` route'ining o'zi (JSON shakli, avtorizatsiya)
        `test_article_sync_service.py`da servis darajasida qamrab olingan;
        bu yerda `/apply` HTTP orqali — u tarmoqqa muhtoj EMAS."""
        result = sync_service.check_article_metadata(cur, 1, actor_id=1,
                                                      fetch=fake_fetch)
        return result.sync_id

    def test_missing_doi_returns_400_not_500(self):
        store = new_store(base_article_row(doi=None))
        connection = FakeSyncConnection(store)
        with fake_sync_db(connection):
            response = self.client.post(CHECK_URL, json={})
        self.assertEqual(response.status_code, 400)

    def test_check_then_apply_updates_article(self):
        store = new_store(base_article_row())
        connection = FakeSyncConnection(store)
        with fake_sync_db(connection):
            sync_id = self._checked_sync_id(connection.cursor())
            apply_resp = self.client.post(
                APPLY_URL, json={'sync_id': sync_id, 'fields': ['citation_count']})
        self.assertEqual(apply_resp.status_code, 200)
        self.assertEqual(store['articles'][1]['citation_count'], 41)
        self.assertEqual(store['articles'][1]['title'], 'Old Title')

    def test_apply_without_sync_id_is_400(self):
        store = new_store(base_article_row())
        connection = FakeSyncConnection(store)
        with fake_sync_db(connection):
            response = self.client.post(APPLY_URL, json={'fields': ['title']})
        self.assertEqual(response.status_code, 400)

    def test_apply_with_arbitrary_client_value_is_ignored(self):
        """§16 — client hech qanday QIYMAT yubora olmaydi, faqat maydon
        nomlari. `new_value`/qo'shimcha kalitlar e'tiborsiz qoldiriladi."""
        store = new_store(base_article_row())
        connection = FakeSyncConnection(store)
        with fake_sync_db(connection):
            sync_id = self._checked_sync_id(connection.cursor())
            self.client.post(APPLY_URL, json={
                'sync_id': sync_id, 'fields': ['citation_count'],
                'citation_count': 999999, 'new_value': 'hacked'})
        # Faqat SERVERDA saqlangan diff qiymati (41) yozildi, client'niki emas.
        self.assertEqual(store['articles'][1]['citation_count'], 41)

    def test_apply_twice_via_http_is_idempotent(self):
        store = new_store(base_article_row())
        connection = FakeSyncConnection(store)
        with fake_sync_db(connection):
            sync_id = self._checked_sync_id(connection.cursor())
            self.client.post(APPLY_URL, json={'sync_id': sync_id,
                                              'fields': ['citation_count']})
            second = self.client.post(APPLY_URL, json={'sync_id': sync_id,
                                                        'fields': ['title']})
        self.assertEqual(second.status_code, 400)
        self.assertEqual(store['articles'][1]['title'], 'Old Title')


if __name__ == '__main__':
    unittest.main()
