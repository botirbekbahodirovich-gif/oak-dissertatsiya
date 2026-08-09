# -*- coding: utf-8 -*-
"""Saqlangan qidiruv so'rovlari — admin route testlari (Phase 2D.2, §20, §24).

`tests/test_article_discovery_routes.py` bilan bir xil naqsh
(`AdminTestCase`, `fake_discovery_env`) — `FakeDiscoveryCursor`
(`tests/fake_article_discovery_pg.py`) endi `article_discovery_saved_queries`
SQL'ini ham tushunadi, shuning uchun bitta umumiy fixture yetarli.
"""
import contextlib
import os
import unittest
from datetime import date
from unittest import mock

os.environ.setdefault('APP_ENV', 'testing')
os.environ.setdefault('SESSION_SECRET', 'article-discovery-saved-queries-test-secret')

from app import app  # noqa: E402
from services.article_discovery.config import ArticleDiscoveryConfig  # noqa: E402
from services.article_discovery import saved_queries  # noqa: E402
from tests.fake_article_discovery_pg import (FakeDiscoveryConnection,
                                             new_store)  # noqa: E402
from tests.test_articles_admin import AdminTestCase  # noqa: E402

CONFIGURED = ArticleDiscoveryConfig(openalex_mailto='test@example.com',
                                    crossref_mailto='test@example.com')


class FakeCache:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, timeout=None):
        self.store[key] = value
        return True


@contextlib.contextmanager
def fake_env(connection, cache=None):
    cache = cache if cache is not None else FakeCache()
    with mock.patch('blueprints.article_discovery.get_connection',
                    return_value=connection), \
            mock.patch('data.get_connection', return_value=connection), \
            mock.patch('blueprints.article_discovery._cache', return_value=cache), \
            mock.patch('services.article_import.persistence._schema_ready', False), \
            mock.patch.object(saved_queries, '_schema_ready', False):
        yield cache


BASE_FORM = {'keyword': 'oncology', 'from_date': '2024-01-01', 'to_date': '2024-01-31',
            'limit': '50', 'providers': ['openalex', 'crossref']}


class AuthorizationTests(AdminTestCase):
    ROUTES = [
        ('/admin/articles/discovery/saved-queries', 'POST'),
        ('/admin/articles/discovery/saved-queries/1/run', 'POST'),
        ('/admin/articles/discovery/saved-queries/1/edit', 'GET'),
        ('/admin/articles/discovery/saved-queries/1/update', 'POST'),
        ('/admin/articles/discovery/saved-queries/1/deactivate', 'POST'),
    ]

    def test_anonymous_is_login_walled(self):
        for path, method in self.ROUTES:
            fn = self.client.get if method == 'GET' else self.client.post
            response = fn(path, follow_redirects=False)
            self.assertIn(response.status_code, (302, 400, 401), path)

    def test_non_admin_gets_403(self):
        self.login(is_admin=False)
        for path, method in self.ROUTES:
            fn = self.client.get if method == 'GET' else self.client.post
            self.assertIn(fn(path, data={} if method == 'POST' else None).status_code,
                          (400, 403), path)


class SavedQueryCrudRouteTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self._orig_csrf = app.config.get('WTF_CSRF_ENABLED', True)
        app.config['WTF_CSRF_ENABLED'] = False
        self.login(is_admin=True)

    def tearDown(self):
        app.config['WTF_CSRF_ENABLED'] = self._orig_csrf
        super().tearDown()

    def _connection(self):
        store = new_store()
        return FakeDiscoveryConnection(store), store

    def test_create_saved_query(self):
        connection, store = self._connection()
        with fake_env(connection), \
                mock.patch('blueprints.article_discovery.config_from_env',
                          return_value=CONFIGURED):
            resp = self.client.post(
                '/admin/articles/discovery/saved-queries',
                data=dict(BASE_FORM, name='Oncology weekly'), follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(store['saved_queries']), 1)
        self.assertEqual(store['saved_queries'][0]['name'], 'Oncology weekly')

    def test_create_without_name_rejected(self):
        connection, store = self._connection()
        with fake_env(connection), \
                mock.patch('blueprints.article_discovery.config_from_env',
                          return_value=CONFIGURED):
            resp = self.client.post(
                '/admin/articles/discovery/saved-queries',
                data=dict(BASE_FORM, name=''))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(store['saved_queries'], [])

    def test_create_with_invalid_provider_rejected(self):
        connection, store = self._connection()
        form = dict(BASE_FORM, name='X')
        form['providers'] = ['not-a-real-provider']
        with fake_env(connection), \
                mock.patch('blueprints.article_discovery.config_from_env',
                          return_value=CONFIGURED):
            resp = self.client.post('/admin/articles/discovery/saved-queries', data=form)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(store['saved_queries'], [])

    def test_list_shown_on_search_form(self):
        connection, store = self._connection()
        from services.article_discovery.models import ArticleDiscoveryQuery
        saved_queries.create(
            connection.cursor(),
            created_by=1, name='Existing query',
            query=ArticleDiscoveryQuery(
                keyword='oncology', from_date=date(2024, 1, 1), to_date=date(2024, 1, 31),
                oak_field_id=None, limit=50, providers=('openalex',)))
        with fake_env(connection), \
                mock.patch('blueprints.article_discovery.config_from_env',
                          return_value=CONFIGURED):
            resp = self.client.get('/admin/articles/discovery')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Existing query', resp.get_data(as_text=True))

    def test_run_executes_search_and_does_not_error(self):
        connection, store = self._connection()
        from services.article_discovery.models import (ArticleDiscoveryQuery,
                                                        DiscoveryPreviewResult)
        from services.article_metadata.models import OK
        query_id = saved_queries.create(
            connection.cursor(), created_by=1, name='X',
            query=ArticleDiscoveryQuery(
                keyword='oncology', from_date=date(2024, 1, 1), to_date=date(2024, 1, 31),
                oak_field_id=None, limit=5, providers=('openalex',)))

        preview = DiscoveryPreviewResult(
            status=OK,
            query=ArticleDiscoveryQuery(keyword='oncology', from_date=date(2024, 1, 1),
                                        to_date=date(2024, 1, 31), oak_field_id=None,
                                        limit=5, providers=('openalex',)),
            candidates=[])
        with fake_env(connection), \
                mock.patch('blueprints.article_discovery.config_from_env',
                          return_value=CONFIGURED), \
                mock.patch('services.article_discovery.service.run_discovery_search',
                          return_value=preview):
            resp = self.client.post(
                '/admin/articles/discovery/saved-queries/%d/run' % query_id,
                follow_redirects=False)
        self.assertEqual(resp.status_code, 200)

    def test_run_touches_last_used_on_success(self):
        connection, store = self._connection()
        from services.article_discovery.models import (ArticleDiscoveryQuery,
                                                        DiscoveryPreviewResult)
        from services.article_metadata.models import OK
        query_id = saved_queries.create(
            connection.cursor(), created_by=1, name='X',
            query=ArticleDiscoveryQuery(
                keyword='oncology', from_date=date(2024, 1, 1), to_date=date(2024, 1, 31),
                oak_field_id=None, limit=5, providers=('openalex',)))

        preview = DiscoveryPreviewResult(
            status=OK,
            query=ArticleDiscoveryQuery(keyword='oncology', from_date=date(2024, 1, 1),
                                        to_date=date(2024, 1, 31), oak_field_id=None,
                                        limit=5, providers=('openalex',)),
            candidates=[])
        with fake_env(connection), \
                mock.patch('blueprints.article_discovery.config_from_env',
                          return_value=CONFIGURED), \
                mock.patch('services.article_discovery.service.run_discovery_search',
                          return_value=preview):
            self.client.post('/admin/articles/discovery/saved-queries/%d/run' % query_id)
        self.assertIsNotNone(store['saved_queries'][0]['last_used_at'])

    def test_run_missing_query_redirects_safely(self):
        connection, store = self._connection()
        with fake_env(connection), \
                mock.patch('blueprints.article_discovery.config_from_env',
                          return_value=CONFIGURED):
            resp = self.client.post(
                '/admin/articles/discovery/saved-queries/999/run', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

    def test_edit_form_renders_existing_values(self):
        connection, store = self._connection()
        from services.article_discovery.models import ArticleDiscoveryQuery
        query_id = saved_queries.create(
            connection.cursor(), created_by=1, name='Edit me',
            query=ArticleDiscoveryQuery(
                keyword='oncology', from_date=date(2024, 1, 1), to_date=date(2024, 1, 31),
                oak_field_id=None, limit=5, providers=('crossref',)))
        with fake_env(connection), \
                mock.patch('blueprints.article_discovery.config_from_env',
                          return_value=CONFIGURED):
            resp = self.client.get(
                '/admin/articles/discovery/saved-queries/%d/edit' % query_id)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Edit me', resp.get_data(as_text=True))

    def test_edit_form_unknown_id_404s(self):
        connection, store = self._connection()
        with fake_env(connection), \
                mock.patch('blueprints.article_discovery.config_from_env',
                          return_value=CONFIGURED):
            resp = self.client.get('/admin/articles/discovery/saved-queries/999/edit')
        self.assertEqual(resp.status_code, 404)

    def test_update_renames_query(self):
        connection, store = self._connection()
        from services.article_discovery.models import ArticleDiscoveryQuery
        query_id = saved_queries.create(
            connection.cursor(), created_by=1, name='Old',
            query=ArticleDiscoveryQuery(
                keyword='oncology', from_date=date(2024, 1, 1), to_date=date(2024, 1, 31),
                oak_field_id=None, limit=5, providers=('openalex',)))
        with fake_env(connection), \
                mock.patch('blueprints.article_discovery.config_from_env',
                          return_value=CONFIGURED):
            resp = self.client.post(
                '/admin/articles/discovery/saved-queries/%d/update' % query_id,
                data=dict(BASE_FORM, name='New name'), follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(store['saved_queries'][0]['name'], 'New name')

    def test_deactivate_hides_from_default_listing(self):
        connection, store = self._connection()
        from services.article_discovery.models import ArticleDiscoveryQuery
        query_id = saved_queries.create(
            connection.cursor(), created_by=1, name='Bye',
            query=ArticleDiscoveryQuery(
                keyword='oncology', from_date=date(2024, 1, 1), to_date=date(2024, 1, 31),
                oak_field_id=None, limit=5, providers=('openalex',)))
        with fake_env(connection), \
                mock.patch('blueprints.article_discovery.config_from_env',
                          return_value=CONFIGURED):
            resp = self.client.post(
                '/admin/articles/discovery/saved-queries/%d/deactivate' % query_id,
                follow_redirects=False)
            self.assertEqual(resp.status_code, 302)
            listing = self.client.get('/admin/articles/discovery')
        self.assertNotIn('Bye', listing.get_data(as_text=True))
        self.assertFalse(store['saved_queries'][0]['is_active'])


if __name__ == '__main__':
    unittest.main()
