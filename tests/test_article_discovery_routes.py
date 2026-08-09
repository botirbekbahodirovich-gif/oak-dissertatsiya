# -*- coding: utf-8 -*-
"""Discovery admin route'lari — avtorizatsiya, CSRF, preview token xavfsizligi
(Phase 2D.1, §44).
"""
import contextlib
import os
import unittest
from datetime import date
from unittest import mock

os.environ.setdefault('APP_ENV', 'testing')
os.environ.setdefault('SESSION_SECRET', 'article-discovery-test-secret')

from app import app  # noqa: E402
from services.article_discovery.config import ArticleDiscoveryConfig  # noqa: E402
from services.article_discovery.models import (ArticleDiscoveryQuery,
                                                DiscoveryCandidate,
                                                DiscoveryPreviewResult)  # noqa: E402
from services.article_metadata.models import OK  # noqa: E402
from tests.fake_article_discovery_pg import (FakeDiscoveryConnection,
                                             new_store)  # noqa: E402
from tests.test_articles_admin import AdminTestCase  # noqa: E402

DISCOVERY_GET_ROUTES = ['/admin/articles/discovery']
DISCOVERY_POST_ROUTES = ['/admin/articles/discovery/search',
                         '/admin/articles/discovery/enqueue']

CONFIGURED = ArticleDiscoveryConfig(openalex_mailto='test@example.com')


class FakeCache:
    """Flask-Caching `Cache` interfeysining minimal qalbaki nusxasi —
    `cache.get(key)` / `cache.set(key, value, timeout=None)`."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, timeout=None):
        self.store[key] = value
        return True


@contextlib.contextmanager
def fake_discovery_env(connection, cache=None):
    cache = cache if cache is not None else FakeCache()
    with mock.patch('blueprints.article_discovery.get_connection',
                    return_value=connection), \
            mock.patch('data.get_connection', return_value=connection), \
            mock.patch('blueprints.article_discovery._cache', return_value=cache), \
            mock.patch('services.article_import.persistence._schema_ready', False):
        yield cache


class AuthorizationTests(AdminTestCase):
    def test_anonymous_is_login_walled(self):
        for path in DISCOVERY_GET_ROUTES:
            response = self.client.get(path, follow_redirects=False)
            self.assertIn(response.status_code, (302, 401), path)
        for path in DISCOVERY_POST_ROUTES:
            response = self.client.post(path, data={}, follow_redirects=False)
            # CSRF (global before_request) yoki login_required — qaysi
            # birinchi ishlashiga qarab 400/302/401 (article_import bilan
            # bir xil qabul qilingan qiymatlar to'plami).
            self.assertIn(response.status_code, (302, 400, 401), path)

    def test_non_admin_gets_403(self):
        self.login(is_admin=False)
        for path in DISCOVERY_GET_ROUTES:
            self.assertEqual(self.client.get(path).status_code, 403, path)
        for path in DISCOVERY_POST_ROUTES:
            self.assertIn(self.client.post(path, data={}).status_code, (400, 403), path)


class SearchFormTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self._orig_csrf = app.config.get('WTF_CSRF_ENABLED', True)
        app.config['WTF_CSRF_ENABLED'] = False
        self.login(is_admin=True)

    def tearDown(self):
        app.config['WTF_CSRF_ENABLED'] = self._orig_csrf
        super().tearDown()

    def test_admin_can_view_search_form(self):
        connection = FakeDiscoveryConnection(new_store())
        with fake_discovery_env(connection):
            response = self.client.get('/admin/articles/discovery')
        self.assertEqual(response.status_code, 200)

    def test_not_configured_shows_clear_state_not_500(self):
        connection = FakeDiscoveryConnection(new_store())
        with fake_discovery_env(connection), \
                mock.patch('blueprints.article_discovery.config_from_env',
                          return_value=ArticleDiscoveryConfig()):
            response = self.client.post('/admin/articles/discovery/search',
                                        data={'keyword': 'oncology'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('sozlanmagan', response.get_data(as_text=True))

    def test_invalid_keyword_returns_400_not_500(self):
        connection = FakeDiscoveryConnection(new_store())
        with fake_discovery_env(connection), \
                mock.patch('blueprints.article_discovery.config_from_env',
                          return_value=CONFIGURED):
            response = self.client.post('/admin/articles/discovery/search',
                                        data={'keyword': 'ab'})
        self.assertEqual(response.status_code, 400)

    def test_successful_search_renders_preview_with_token(self):
        connection = FakeDiscoveryConnection(new_store())
        preview = DiscoveryPreviewResult(
            status=OK,
            query=ArticleDiscoveryQuery(keyword='oncology', from_date=date(2024, 1, 1),
                                        to_date=date(2024, 1, 31), oak_field_id=None,
                                        limit=50),
            candidates=[DiscoveryCandidate(doi='10.1000/a', title='<script>bad</script>Title')])
        with fake_discovery_env(connection), \
                mock.patch('blueprints.article_discovery.config_from_env',
                          return_value=CONFIGURED), \
                mock.patch('services.article_discovery.service.run_discovery_search',
                          return_value=preview):
            response = self.client.post(
                '/admin/articles/discovery/search',
                data={'keyword': 'oncology', 'from_date': '2024-01-01',
                     'to_date': '2024-01-31'})
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('preview_token', body)
        # XSS — Jinja autoescape, |safe yo'q (§35).
        self.assertNotIn('<script>bad</script>', body)
        self.assertIn('&lt;script&gt;', body)

    def test_rate_limit_returns_429(self):
        connection = FakeDiscoveryConnection(new_store())
        limited_config = ArticleDiscoveryConfig(openalex_mailto='t@example.com',
                                                 searches_per_minute=0)
        with fake_discovery_env(connection), \
                mock.patch('blueprints.article_discovery.config_from_env',
                          return_value=limited_config):
            response = self.client.post('/admin/articles/discovery/search',
                                        data={'keyword': 'oncology'})
        self.assertEqual(response.status_code, 429)

    def test_client_cannot_smuggle_candidate_list_into_enqueue(self):
        """§23 — enqueue faqat token qabul qiladi, DOI ro'yxatini EMAS."""
        connection = FakeDiscoveryConnection(new_store())
        with fake_discovery_env(connection):
            response = self.client.post(
                '/admin/articles/discovery/enqueue',
                data={'preview_token': 'does-not-exist',
                     'doi_list': '10.1000/a\n10.1000/b'},
                follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/articles/discovery', response.headers['Location'])
        self.assertEqual(connection.store['batches'], [])

    def test_tampered_token_is_safe_redirect_not_500(self):
        connection = FakeDiscoveryConnection(new_store())
        with fake_discovery_env(connection):
            response = self.client.post(
                '/admin/articles/discovery/enqueue',
                data={'preview_token': 'totally-invalid-token'},
                follow_redirects=False)
        self.assertEqual(response.status_code, 302)

    def test_token_bound_to_other_admin_is_rejected(self):
        connection = FakeDiscoveryConnection(new_store())
        cache = FakeCache()
        preview = DiscoveryPreviewResult(
            status=OK,
            query=ArticleDiscoveryQuery(keyword='oncology', from_date=date(2024, 1, 1),
                                        to_date=date(2024, 1, 31), oak_field_id=None,
                                        limit=50),
            candidates=[DiscoveryCandidate(doi='10.1000/a', classification='new')])
        from services.article_discovery import service as discovery_service
        token = discovery_service.store_preview(cache.set, admin_id=999,
                                                preview=preview, config=CONFIGURED)
        with fake_discovery_env(connection, cache=cache):
            response = self.client.post(
                '/admin/articles/discovery/enqueue',
                data={'preview_token': token}, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(connection.store['batches'], [])

    def test_double_submit_same_token_creates_one_batch(self):
        connection = FakeDiscoveryConnection(new_store())
        cache = FakeCache()
        self.login(is_admin=True)
        preview = DiscoveryPreviewResult(
            status=OK,
            query=ArticleDiscoveryQuery(keyword='oncology', from_date=date(2024, 1, 1),
                                        to_date=date(2024, 1, 31), oak_field_id=None,
                                        limit=50),
            candidates=[DiscoveryCandidate(doi='10.1000/a', classification='new')])
        from services.article_discovery import service as discovery_service
        token = discovery_service.store_preview(cache.set, admin_id=1,
                                                preview=preview, config=CONFIGURED)
        with fake_discovery_env(connection, cache=cache):
            first = self.client.post('/admin/articles/discovery/enqueue',
                                     data={'preview_token': token}, follow_redirects=False)
            second = self.client.post('/admin/articles/discovery/enqueue',
                                      data={'preview_token': token}, follow_redirects=False)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(first.headers['Location'], second.headers['Location'])
        self.assertEqual(len(connection.store['batches']), 1)

    def test_double_submit_survives_consumed_marker_eviction(self):
        """Phase 2D.1.1, §6 — cache 'consumed' belgisi (best-effort) yo'qolib
        qolsa ham, DB'dagi `source_request_id` yakuniy himoya bo'lib qoladi:
        ikkinchi POST hali ham bir xil batch'ga qaytadi, YANGI/bo'sh batch
        yaratmaydi."""
        connection = FakeDiscoveryConnection(new_store())
        cache = FakeCache()
        preview = DiscoveryPreviewResult(
            status=OK,
            query=ArticleDiscoveryQuery(keyword='oncology', from_date=date(2024, 1, 1),
                                        to_date=date(2024, 1, 31), oak_field_id=None,
                                        limit=50),
            candidates=[DiscoveryCandidate(doi='10.1000/a', classification='new')])
        from services.article_discovery import service as discovery_service
        token = discovery_service.store_preview(cache.set, admin_id=1,
                                                preview=preview, config=CONFIGURED)
        with fake_discovery_env(connection, cache=cache):
            first = self.client.post('/admin/articles/discovery/enqueue',
                                     data={'preview_token': token}, follow_redirects=False)
            # Cache eviction simulyatsiyasi — "consumed" belgisi yo'qoladi,
            # lekin preview entry hali ham bor (TTL bir xil emas amalda ham).
            cache.store.pop('article_discovery:preview:%s:consumed' % token, None)
            second = self.client.post('/admin/articles/discovery/enqueue',
                                      data={'preview_token': token}, follow_redirects=False)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(first.headers['Location'], second.headers['Location'])
        self.assertEqual(len(connection.store['batches']), 1)
        self.assertEqual(len(connection.store['items']), 1)

    def test_raw_token_never_persisted_in_batch_row(self):
        connection = FakeDiscoveryConnection(new_store())
        cache = FakeCache()
        preview = DiscoveryPreviewResult(
            status=OK,
            query=ArticleDiscoveryQuery(keyword='oncology', from_date=date(2024, 1, 1),
                                        to_date=date(2024, 1, 31), oak_field_id=None,
                                        limit=50),
            candidates=[DiscoveryCandidate(doi='10.1000/a', classification='new')])
        from services.article_discovery import service as discovery_service
        token = discovery_service.store_preview(cache.set, admin_id=1,
                                                preview=preview, config=CONFIGURED)
        with fake_discovery_env(connection, cache=cache):
            self.client.post('/admin/articles/discovery/enqueue',
                             data={'preview_token': token}, follow_redirects=False)
        batch = connection.store['batches'][0]
        self.assertNotEqual(batch['source_request_id'], token)
        for value in batch.values():
            self.assertNotEqual(value, token)
        # Hash — deterministik, lekin xom token QIYMATINING o'zi emas.
        self.assertIsNotNone(batch['source_request_id'])
        self.assertEqual(len(batch['source_request_id']), 64)  # sha256 hex

    def test_zero_eligible_candidates_creates_no_batch_and_redirects_to_search(self):
        connection = FakeDiscoveryConnection(new_store())
        cache = FakeCache()
        preview = DiscoveryPreviewResult(
            status=OK,
            query=ArticleDiscoveryQuery(keyword='oncology', from_date=date(2024, 1, 1),
                                        to_date=date(2024, 1, 31), oak_field_id=None,
                                        limit=50),
            candidates=[DiscoveryCandidate(doi='10.1000/a', classification='existing_article')])
        from services.article_discovery import service as discovery_service
        token = discovery_service.store_preview(cache.set, admin_id=1,
                                                preview=preview, config=CONFIGURED)
        with fake_discovery_env(connection, cache=cache):
            response = self.client.post('/admin/articles/discovery/enqueue',
                                        data={'preview_token': token}, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/articles/discovery', response.headers['Location'])
        self.assertNotIn('/admin/articles/imports/', response.headers['Location'])
        self.assertEqual(connection.store['batches'], [])


if __name__ == '__main__':
    unittest.main()
