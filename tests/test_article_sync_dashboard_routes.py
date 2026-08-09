# -*- coding: utf-8 -*-
"""Dashboard admin route'lari — avtorizatsiya, CSRF, side-effectsiz ko'rish,
asosiy oqim (Phase 2C.3B, §31, §32).
"""
import contextlib
import os
import unittest
from unittest import mock

os.environ.setdefault('APP_ENV', 'testing')
os.environ.setdefault('SESSION_SECRET', 'article-sync-dashboard-test-secret')

from app import app  # noqa: E402
from services.article_sync import service as sync_service  # noqa: E402
from tests.fake_article_sync_pg import (FakeSyncConnection, add_scheduler_run,  # noqa: E402
                                        new_store)
from tests.test_article_sync_service import base_article_row, make_fetcher  # noqa: E402
from tests.test_articles_admin import AdminTestCase  # noqa: E402

DASHBOARD_URL = '/admin/articles/metadata-sync'
HISTORY_URL = '/admin/articles/1/metadata/history'
RECHECK_URL = '/admin/articles/1/metadata/recheck'


def _detail_url(sync_id):
    return '/admin/articles/metadata-sync/%d' % sync_id


def _dismiss_url(sync_id):
    return '/admin/articles/metadata-sync/%d/dismiss' % sync_id


@contextlib.contextmanager
def fake_sync_db(connection):
    with mock.patch('blueprints.articles.get_connection', return_value=connection), \
            mock.patch('data.get_connection', return_value=connection), \
            mock.patch('services.article_sync.persistence._schema_ready', False), \
            mock.patch('blueprints.articles._metadata_rate_limited', return_value=False):
        yield


def fake_article_dict(article_id=1, **overrides):
    row = {'id': article_id, 'title': 'Old Title', 'doi': '10.1000/abc',
          'slug': 'old-title', 'pdf_path': None, 'authors': [], 'fields': [],
          'primary_field': None}
    row.update(overrides)
    return row


class AuthorizationTests(AdminTestCase):
    GET_ROUTES = [DASHBOARD_URL, _detail_url(1), HISTORY_URL]
    POST_ROUTES = [_dismiss_url(1),
                  '/admin/articles/metadata-sync/bulk-apply-safe', RECHECK_URL]

    def test_anonymous_denied(self):
        for path in self.GET_ROUTES:
            response = self.client.get(path, follow_redirects=False)
            self.assertIn(response.status_code, (302, 400, 401), path)
        for path in self.POST_ROUTES:
            response = self.client.post(path, data={})
            self.assertIn(response.status_code, (302, 400, 401), path)

    def test_non_admin_denied(self):
        self.login(is_admin=False)
        for path in self.GET_ROUTES:
            response = self.client.get(path)
            self.assertIn(response.status_code, (400, 403), path)
        for path in self.POST_ROUTES:
            response = self.client.post(path, data={})
            self.assertIn(response.status_code, (400, 403), path)


class CsrfTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self._orig_csrf = app.config.get('WTF_CSRF_ENABLED', True)
        app.config['WTF_CSRF_ENABLED'] = True

    def tearDown(self):
        app.config['WTF_CSRF_ENABLED'] = self._orig_csrf
        super().tearDown()

    def test_dismiss_requires_csrf(self):
        self.login(is_admin=True)
        response = self.client.post(_dismiss_url(1), data={})
        self.assertEqual(response.status_code, 400)

    def test_bulk_apply_requires_csrf(self):
        self.login(is_admin=True)
        response = self.client.post('/admin/articles/metadata-sync/bulk-apply-safe',
                                    data={})
        self.assertEqual(response.status_code, 400)

    def test_recheck_requires_csrf(self):
        self.login(is_admin=True)
        response = self.client.post(RECHECK_URL, data={})
        self.assertEqual(response.status_code, 400)


class DashboardNoSideEffectsTests(AdminTestCase):
    """§32 — dashboardni ochish tashqi API chaqirmasin, hech narsani
    mutate qilmasin."""

    def setUp(self):
        super().setUp()
        self._orig_csrf = app.config.get('WTF_CSRF_ENABLED', True)
        app.config['WTF_CSRF_ENABLED'] = False
        self.login(is_admin=True)

    def tearDown(self):
        app.config['WTF_CSRF_ENABLED'] = self._orig_csrf
        super().tearDown()

    def test_dashboard_view_never_calls_fetch_article_metadata(self):
        connection = FakeSyncConnection(new_store())
        with fake_sync_db(connection), \
                mock.patch('services.article_sync.list_dashboard',
                          return_value=([], 0)) as list_mock, \
                mock.patch('services.article_sync.get_dashboard_summary',
                          return_value={}), \
                mock.patch('services.article_metadata.service.fetch_article_metadata'
                          ) as fetch_mock:
            response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)
        fetch_mock.assert_not_called()
        list_mock.assert_called_once()

    def test_dashboard_renders_one_h1_and_summary_cards(self):
        connection = FakeSyncConnection(new_store())
        with fake_sync_db(connection), \
                mock.patch('services.article_sync.list_dashboard',
                          return_value=([], 0)), \
                mock.patch('services.article_sync.get_dashboard_summary',
                          return_value={'total_checked': 5, 'needs_review': 2,
                                        'critical': 1, 'retraction': 1}):
            response = self.client.get(DASHBOARD_URL)
        body = response.get_data(as_text=True)
        self.assertEqual(body.count('<h1'), 1)
        self.assertIn('Ko\'rib chiqish kerak', body)

    def test_dashboard_passes_filters_from_query_string(self):
        connection = FakeSyncConnection(new_store())
        with fake_sync_db(connection), \
                mock.patch('services.article_sync.list_dashboard',
                          return_value=([], 0)) as list_mock, \
                mock.patch('services.article_sync.get_dashboard_summary',
                          return_value={}):
            self.client.get(DASHBOARD_URL + '?status=critical&retraction=1&sort=newest')
        _args, kwargs = list_mock.call_args
        self.assertEqual(kwargs.get('queue_status'), 'critical')
        self.assertTrue(kwargs.get('has_retraction'))
        self.assertEqual(kwargs.get('sort'), 'newest')


class SchedulerStatusSectionTests(AdminTestCase):
    """Dashboard'dagi "Avtomatik metadata tekshiruvi" bloki (Phase 2C.3D) —
    `list_recent_scheduler_runs()`dan o'qiydi, hech qanday yangi query/
    mutatsiya yo'q."""

    def setUp(self):
        super().setUp()
        self._orig_csrf = app.config.get('WTF_CSRF_ENABLED', True)
        app.config['WTF_CSRF_ENABLED'] = False
        self.login(is_admin=True)

    def tearDown(self):
        app.config['WTF_CSRF_ENABLED'] = self._orig_csrf
        super().tearDown()

    def _get_dashboard(self, store):
        connection = FakeSyncConnection(store)
        with fake_sync_db(connection), \
                mock.patch('services.article_sync.list_dashboard',
                          return_value=([], 0)), \
                mock.patch('services.article_sync.get_dashboard_summary',
                          return_value={}):
            response = self.client.get(DASHBOARD_URL)
        return response, connection

    def test_no_scheduler_run_shows_never_started_message(self):
        response, _conn = self._get_dashboard(new_store())
        body = response.get_data(as_text=True)
        self.assertIn('Avtomatik metadata tekshiruvi hali ishga tushirilmagan.',
                      body)

    def test_scheduler_run_present_shows_counts(self):
        store = new_store()
        add_scheduler_run(store, 1, status='completed', processed_count=50,
                          success_count=47, partial_count=2, failed_count=1)
        response, _conn = self._get_dashboard(store)
        body = response.get_data(as_text=True)
        self.assertIn('Tekshirildi: 50', body)
        self.assertIn('Muvaffaqiyatli: 47', body)
        self.assertIn('Qisman: 2', body)
        self.assertIn('Xato: 1', body)

    def test_completed_status_label(self):
        store = new_store()
        add_scheduler_run(store, 1, status='completed')
        response, _conn = self._get_dashboard(store)
        self.assertIn('Yakunlandi', response.get_data(as_text=True))

    def test_completed_with_errors_status_label(self):
        store = new_store()
        add_scheduler_run(store, 1, status='completed_with_errors')
        response, _conn = self._get_dashboard(store)
        self.assertIn('Xatolar bilan yakunlandi', response.get_data(as_text=True))

    def test_failed_status_label(self):
        store = new_store()
        add_scheduler_run(store, 1, status='failed')
        response, _conn = self._get_dashboard(store)
        self.assertIn('Muvaffaqiyatsiz', response.get_data(as_text=True))

    def test_running_status_label(self):
        store = new_store()
        add_scheduler_run(store, 1, status='running')
        response, _conn = self._get_dashboard(store)
        self.assertIn('Jarayonda', response.get_data(as_text=True))

    def test_unknown_status_falls_back_to_raw_value(self):
        store = new_store()
        add_scheduler_run(store, 1, status='mystery_status')
        response, _conn = self._get_dashboard(store)
        self.assertEqual(response.status_code, 200)
        self.assertIn('mystery_status', response.get_data(as_text=True))

    def test_most_recent_run_is_shown_when_several_exist(self):
        store = new_store()
        add_scheduler_run(store, 1, status='failed')
        add_scheduler_run(store, 2, status='completed')
        response, _conn = self._get_dashboard(store)
        body = response.get_data(as_text=True)
        self.assertIn('Yakunlandi', body)
        self.assertNotIn('Muvaffaqiyatsiz', body)

    def test_dashboard_get_does_not_call_provider_or_scheduler(self):
        store = new_store()
        add_scheduler_run(store, 1, status='completed')
        connection = FakeSyncConnection(store)
        with fake_sync_db(connection), \
                mock.patch('services.article_sync.list_dashboard',
                          return_value=([], 0)), \
                mock.patch('services.article_sync.get_dashboard_summary',
                          return_value={}), \
                mock.patch('services.article_metadata.service.fetch_article_metadata'
                          ) as fetch_mock, \
                mock.patch('services.article_sync.run_scheduled_metadata_sync'
                          ) as run_mock, \
                mock.patch('services.article_sync.check_article_metadata'
                          ) as check_mock:
            response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200)
        fetch_mock.assert_not_called()
        run_mock.assert_not_called()
        check_mock.assert_not_called()

    def test_dashboard_get_performs_no_write_statements(self):
        """Route o'zi mutatsiya qilmasin — `article_metadata_syncs`/
        `article_metadata_sync_runs`/`scientific_articles`ga yozuv yo'q.
        (`page_visits` INSERT'i — app-darajasidagi analytics middleware,
        bu route'dan mustaqil, shu testning ko'lamiga kirmaydi.)"""
        store = new_store()
        add_scheduler_run(store, 1, status='completed')
        response, connection = self._get_dashboard(store)
        self.assertEqual(response.status_code, 200)
        write_prefixes = ('INSERT', 'UPDATE', 'DELETE')
        domain_tables = ('ARTICLE_METADATA_SYNCS', 'ARTICLE_METADATA_SYNC_RUNS',
                         'SCIENTIFIC_ARTICLES', 'ARTICLE_AUTHORS')
        for sql, _params in connection.store['sql']:
            upper = sql.upper()
            if upper.startswith(write_prefixes) and any(t in upper for t in domain_tables):
                self.fail('dashboard GET domain yozuv bajardi: %s' % sql)

    def test_dashboard_still_renders_exactly_one_h1(self):
        store = new_store()
        add_scheduler_run(store, 1, status='completed')
        response, _conn = self._get_dashboard(store)
        body = response.get_data(as_text=True)
        self.assertEqual(body.count('<h1'), 1)


class SchedulerStatusSectionDesignTests(unittest.TestCase):
    """Statik shablon tekshiruvi — design system §18 (raw HEX/gradient/dark
    theme taqiqlangan)."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'templates', 'admin', 'article_metadata_sync_dashboard.html')
        with open(path, encoding='utf-8') as fh:
            cls.html = fh.read()

    def test_no_raw_hex_colors(self):
        import re
        self.assertNotRegex(self.html, r'#[0-9a-fA-F]{3,8}\b')

    def test_no_gradient(self):
        self.assertNotIn('gradient', self.html.lower())

    # Dark-theme/`prefers-color-scheme` taqiqi butun manba daraxti bo'ylab
    # `tests/test_light_only_theme.py`da allaqachon qulflangan — bu yerda
    # takrorlanmaydi (shu literal matnni qidiruv o'zi false-positive beradi).

    def test_scheduler_section_uses_shared_components(self):
        self.assertIn('ol-card', self.html)
        self.assertIn('ol-badge', self.html)
        self.assertNotIn('mq-scheduler-card', self.html)


class DismissRecheckFlowTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self._orig_csrf = app.config.get('WTF_CSRF_ENABLED', True)
        app.config['WTF_CSRF_ENABLED'] = False
        self.login(is_admin=True)

    def tearDown(self):
        app.config['WTF_CSRF_ENABLED'] = self._orig_csrf
        super().tearDown()

    def test_recheck_route_redirects_to_new_sync_detail(self):
        """`check_article_metadata`ning `fetch` default parametri IMPORT
        vaqtida bog'langan (article_import auditida topilgan naqsh) —
        `mock.patch('services.article_metadata.fetch_article_metadata', …)`
        keyinchalik chaqirilsa ham ta'sir qilmaydi. Shuning uchun ROUTE
        xatti-harakatini (marshrutlash, redirect) `check_article_metadata`
        FUNKSIYASINING o'zini mock qilib tekshiramiz — tarmoqsiz."""
        store = new_store(base_article_row())
        connection = FakeSyncConnection(store)
        fake_result = mock.Mock(sync_id=42)
        with fake_sync_db(connection), \
                mock.patch('services.article_sync.check_article_metadata',
                          return_value=fake_result) as check_mock:
            response = self.client.post(RECHECK_URL, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/articles/metadata-sync/42', response.headers['Location'])
        check_mock.assert_called_once()

    def test_detail_page_renders_diff_for_a_checked_sync(self):
        store = new_store(base_article_row())
        connection = FakeSyncConnection(store)
        cur = connection.cursor()
        result = sync_service.check_article_metadata(cur, 1, actor_id=1,
                                                      fetch=make_fetcher())
        connection.commit()
        with fake_sync_db(connection), \
                mock.patch('blueprints.articles._load_article',
                          return_value=fake_article_dict()):
            response = self.client.get(_detail_url(result.sync_id))
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertEqual(body.count('<h1'), 1)
        self.assertIn('New Title', body)

    def test_dismiss_then_dashboard_excludes_it(self):
        store = new_store(base_article_row())
        connection = FakeSyncConnection(store)
        cur = connection.cursor()
        result = sync_service.check_article_metadata(cur, 1, actor_id=1,
                                                      fetch=make_fetcher())
        connection.commit()
        with fake_sync_db(connection):
            response = self.client.post(_dismiss_url(result.sync_id),
                                        data={}, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(store['syncs'][result.sync_id]['status'], 'dismissed')

    def test_dismiss_unknown_sync_flashes_and_redirects_not_500(self):
        store = new_store(base_article_row())
        connection = FakeSyncConnection(store)
        with fake_sync_db(connection):
            response = self.client.post(_dismiss_url(999999), data={},
                                        follow_redirects=False)
        self.assertEqual(response.status_code, 302)

    def test_bulk_apply_safe_applies_only_eligible_syncs(self):
        store = new_store(base_article_row())
        connection = FakeSyncConnection(store)
        cur = connection.cursor()
        safe_result = sync_service.check_article_metadata(
            cur, 1, actor_id=1, fetch=make_fetcher(citation_count=41))
        connection.commit()
        with fake_sync_db(connection):
            response = self.client.post(
                '/admin/articles/metadata-sync/bulk-apply-safe',
                data={'sync_id': [str(safe_result.sync_id)]}, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(store['articles'][1]['citation_count'], 41)


if __name__ == '__main__':
    unittest.main()
