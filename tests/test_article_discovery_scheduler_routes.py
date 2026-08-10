# -*- coding: utf-8 -*-
"""Rejalashtirish admin route testlari (Phase 2D.3, §17, §22).

`tests/test_article_discovery_saved_queries_routes.py` bilan bir xil naqsh.
"""
import contextlib
import os
import unittest
from datetime import date
from unittest import mock

os.environ.setdefault('APP_ENV', 'testing')
os.environ.setdefault('SESSION_SECRET', 'article-discovery-scheduler-test-secret')

from app import app  # noqa: E402
from services.article_discovery import saved_queries  # noqa: E402
from services.article_discovery.config import ArticleDiscoveryConfig  # noqa: E402
from services.article_discovery.models import ArticleDiscoveryQuery  # noqa: E402
from tests.fake_article_discovery_pg import (FakeDiscoveryConnection,
                                             new_store)  # noqa: E402
from tests.test_articles_admin import AdminTestCase  # noqa: E402

CONFIGURED = ArticleDiscoveryConfig(openalex_mailto='test@example.com')
QUERY = ArticleDiscoveryQuery(keyword='oncology', from_date=date(2024, 1, 1),
                              to_date=date(2024, 1, 31), oak_field_id=None,
                              limit=50, providers=('openalex',))


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


class AuthorizationTests(AdminTestCase):
    ROUTE = '/admin/articles/discovery/saved-queries/1/schedule'

    def test_anonymous_is_login_walled(self):
        response = self.client.post(self.ROUTE, data={}, follow_redirects=False)
        self.assertIn(response.status_code, (302, 400, 401))

    def test_non_admin_gets_403(self):
        self.login(is_admin=False)
        self.assertIn(self.client.post(self.ROUTE, data={}).status_code, (400, 403))


class ScheduleRouteTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self._orig_csrf = app.config.get('WTF_CSRF_ENABLED', True)
        app.config['WTF_CSRF_ENABLED'] = False
        self.login(is_admin=True)

    def tearDown(self):
        app.config['WTF_CSRF_ENABLED'] = self._orig_csrf
        super().tearDown()

    def _connection_with_query(self, *, active=True):
        store = new_store()
        connection = FakeDiscoveryConnection(store)
        query_id = saved_queries.create(connection.cursor(), 1, 'X', QUERY)
        if not active:
            saved_queries.set_active(connection.cursor(), query_id, False)
        return connection, store, query_id

    def test_enable_daily_schedule(self):
        connection, store, query_id = self._connection_with_query()
        with fake_env(connection):
            resp = self.client.post(
                '/admin/articles/discovery/saved-queries/%d/schedule' % query_id,
                data={'schedule_enabled': 'on', 'schedule_frequency': 'daily'},
                follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        row = store['saved_queries'][0]
        self.assertTrue(row['schedule_enabled'])
        self.assertEqual(row['schedule_frequency'], 'daily')
        self.assertIsNotNone(row['next_run_at'])

    def test_enable_weekly_schedule(self):
        connection, store, query_id = self._connection_with_query()
        with fake_env(connection):
            self.client.post(
                '/admin/articles/discovery/saved-queries/%d/schedule' % query_id,
                data={'schedule_enabled': 'on', 'schedule_frequency': 'weekly'})
        self.assertEqual(store['saved_queries'][0]['schedule_frequency'], 'weekly')

    def test_enable_monthly_schedule(self):
        connection, store, query_id = self._connection_with_query()
        with fake_env(connection):
            self.client.post(
                '/admin/articles/discovery/saved-queries/%d/schedule' % query_id,
                data={'schedule_enabled': 'on', 'schedule_frequency': 'monthly'})
        self.assertEqual(store['saved_queries'][0]['schedule_frequency'], 'monthly')

    def test_disable_schedule(self):
        connection, store, query_id = self._connection_with_query()
        with fake_env(connection):
            self.client.post(
                '/admin/articles/discovery/saved-queries/%d/schedule' % query_id,
                data={'schedule_enabled': 'on', 'schedule_frequency': 'daily'})
            self.client.post(
                '/admin/articles/discovery/saved-queries/%d/schedule' % query_id,
                data={})  # checkbox yo'q -> unchecked
        row = store['saved_queries'][0]
        self.assertFalse(row['schedule_enabled'])
        self.assertIsNone(row['schedule_frequency'])
        self.assertIsNone(row['next_run_at'])

    def test_invalid_frequency_rejected(self):
        connection, store, query_id = self._connection_with_query()
        with fake_env(connection):
            resp = self.client.post(
                '/admin/articles/discovery/saved-queries/%d/schedule' % query_id,
                data={'schedule_enabled': 'on', 'schedule_frequency': 'hourly'},
                follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(store['saved_queries'][0]['schedule_enabled'])

    def test_missing_frequency_rejected_when_enabling(self):
        connection, store, query_id = self._connection_with_query()
        with fake_env(connection):
            self.client.post(
                '/admin/articles/discovery/saved-queries/%d/schedule' % query_id,
                data={'schedule_enabled': 'on'})
        self.assertFalse(store['saved_queries'][0]['schedule_enabled'])

    def test_unknown_saved_query_id_handled_gracefully(self):
        connection, store, _ = self._connection_with_query()
        with fake_env(connection):
            resp = self.client.post(
                '/admin/articles/discovery/saved-queries/999/schedule',
                data={'schedule_enabled': 'on', 'schedule_frequency': 'daily'},
                follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

    def test_inactive_saved_query_cannot_be_scheduled(self):
        """§17 — validatsiya: so'rov FAOL bo'lishi kerak."""
        connection, store, query_id = self._connection_with_query(active=False)
        with fake_env(connection):
            self.client.post(
                '/admin/articles/discovery/saved-queries/%d/schedule' % query_id,
                data={'schedule_enabled': 'on', 'schedule_frequency': 'daily'})
        self.assertFalse(store['saved_queries'][0]['schedule_enabled'])

    def test_schedule_change_does_not_call_any_provider(self):
        """§14 — DB yozuvi, tashqi API'ga chiqmaydi."""
        from services.article_discovery import openalex, crossref
        connection, store, query_id = self._connection_with_query()
        with fake_env(connection), \
                mock.patch.object(openalex, 'search') as oa, \
                mock.patch.object(crossref, 'search') as cr:
            self.client.post(
                '/admin/articles/discovery/saved-queries/%d/schedule' % query_id,
                data={'schedule_enabled': 'on', 'schedule_frequency': 'daily'})
        oa.assert_not_called()
        cr.assert_not_called()

    def test_search_form_shows_schedule_status(self):
        connection, store, query_id = self._connection_with_query()
        with fake_env(connection):
            self.client.post(
                '/admin/articles/discovery/saved-queries/%d/schedule' % query_id,
                data={'schedule_enabled': 'on', 'schedule_frequency': 'daily'})
            resp = self.client.get('/admin/articles/discovery')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('Avtomatik qidiruv', body)


if __name__ == '__main__':
    unittest.main()
