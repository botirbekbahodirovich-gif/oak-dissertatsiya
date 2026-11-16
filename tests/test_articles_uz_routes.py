# -*- coding: utf-8 -*-
"""Annotatsiya qoidasi shablonlarda, ommaviy nashr va review himoyasi.

Qalbaki DB (tests/fake_pg.py) FILTRLAMAYDI — shuning uchun semantika
yuborilgan SQL va render qilingan HTML ustida tekshiriladi.
"""
import os
import unittest

os.environ.setdefault('APP_ENV', 'testing')
os.environ.setdefault('SESSION_SECRET', 'articles-uz-routes-test-secret')

from app import app  # noqa: E402
from tests.fake_pg import FakeConnection, article, field, new_store  # noqa: E402
from tests.test_articles_admin import AdminTestCase  # noqa: E402
from tests.test_articles_catalog import fake_db  # noqa: E402

SECRET = 'MAXFIY-ANNOTATSIYA-MATNI'
AUTHOR = {'article_id': 1, 'author_name': 'Karimov Sardor', 'author_order': 1,
          'orcid': None, 'affiliation': None, 'linked_user_id': None,
          'linked_scientist_id': None, 'is_corresponding': False,
          'openalex_author_id': None}
PRIMARY_LINK = {'article_id': 1, 'is_primary': True, 'id': 1,
                'name_uz': 'Pedagogika fanlari', 'slug': 'pedagogika-fanlari-130000',
                'code': '13.00.00', 'level': 1}


def _store(**article_overrides):
    return new_store(articles=[article(abstract=SECRET, **article_overrides)],
                     fields=[field()], authors=[AUTHOR], links=[PRIMARY_LINK])


class AbstractVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def get(self, path, store):
        with fake_db(FakeConnection(store)):
            return self.client.get(path)

    def test_closed_licence_hides_abstract_everywhere_on_detail(self):
        response = self.get('/maqolalar/ochiq-maqola',
                            _store(license_type='unknown', pdf_path=None,
                                   access_type='metadata_only'))
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn(SECRET, html)                 # sahifa, meta, og, JSON-LD
        self.assertNotIn('Annotatsiya</h2>', html)
        # Yopiq maqolada meta description sarlavha · jurnal · yildan tuziladi.
        self.assertIn('content="Ochiq maqola · Ilm Sarchashmalari · 2024"', html)
        self.assertIn('https://doi.org/10.1234/abc.2024', html)

    def test_open_licence_shows_abstract_in_page_meta_and_json_ld(self):
        html = self.get('/maqolalar/ochiq-maqola', _store()).get_data(as_text=True)
        self.assertIn('Annotatsiya</h2>', html)
        self.assertIn('<meta name="description" content="%s' % SECRET, html)
        self.assertIn('<meta property="og:description" content="%s' % SECRET, html)
        self.assertIn('"description": "%s"' % SECRET, html)   # ScholarlyArticle JSON-LD

    def test_catalog_card_respects_the_rule(self):
        closed = self.get('/maqolalar', _store(license_type='author_permission'))
        self.assertNotIn(SECRET, closed.get_data(as_text=True))
        opened = self.get('/maqolalar', _store(license_type='cc_by_nc_nd'))
        self.assertIn(SECRET, opened.get_data(as_text=True))

    def test_attribution_on_detail_and_reader(self):
        html = self.get('/maqolalar/ochiq-maqola', _store()).get_data(as_text=True)
        self.assertIn('Atributsiya', html)
        self.assertIn('Asl nashrdan o&#39;zgarishsiz.', html)       # autoescape
        self.assertIn('rel="license noopener"', html)
        reader = self.get('/maqolalar/ochiq-maqola/oqish', _store()).get_data(as_text=True)
        self.assertIn('data-mqv="attribution"', reader)
        self.assertIn('https://doi.org/10.1234/abc.2024', reader)
        self.assertIn('https://creativecommons.org/licenses/by/4.0/', reader)

    def test_no_attribution_without_open_pdf(self):
        html = self.get('/maqolalar/ochiq-maqola',
                        _store(pdf_path=None)).get_data(as_text=True)
        self.assertNotIn('Atributsiya', html)


def _draft(**overrides):
    values = dict(publication_status='draft', verification_status='pending_review',
                  pdf_path=None, access_type='metadata_only', license_type='unknown')
    values.update(overrides)
    return values


class BulkPublishTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self._orig_csrf = app.config.get('WTF_CSRF_ENABLED', True)
        app.config['WTF_CSRF_ENABLED'] = False

    def tearDown(self):
        app.config['WTF_CSRF_ENABLED'] = self._orig_csrf
        super().tearDown()

    def post(self, data, store):
        connection = FakeConnection(store)
        with fake_db(connection):
            response = self.client.post('/admin/articles/bulk-publish', data=data)
        return response, connection

    def _updates(self, connection):
        return [(sql, p) for sql, p in connection.store['sql']
                if sql.startswith('UPDATE scientific_articles') and 'published' in sql]

    def test_non_admin_is_refused(self):
        self.login(is_admin=False)
        response, connection = self.post({'article_ids': ['1']}, _store(**_draft()))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._updates(connection), [])

    def test_anonymous_is_refused(self):
        response, connection = self.post({'article_ids': ['1']}, _store(**_draft()))
        self.assertIn(response.status_code, (302, 401))
        self.assertEqual(self._updates(connection), [])

    def test_ready_article_is_published_in_one_transaction(self):
        self.login(is_admin=True)
        response, connection = self.post({'article_ids': ['1']}, _store(**_draft()))
        self.assertEqual(response.status_code, 302)
        updates = self._updates(connection)
        self.assertEqual(len(updates), 1)
        sql, params = updates[0]
        self.assertIn("verification_status = 'verified'", sql)
        self.assertIn("publication_status = 'published'", sql)
        self.assertIn('reviewed_by = %s, reviewed_at = NOW()', sql)
        self.assertIn("publication_status = 'draft'", sql)       # faqat qoralamalar
        self.assertEqual(params, (1, [1]))
        self.assertEqual(connection.rollbacks, 0)

    def test_candidates_are_locked_and_rechecked(self):
        self.login(is_admin=True)
        _response, connection = self.post({'article_ids': ['1']}, _store(**_draft()))
        selects = [sql for sql, _ in connection.store['sql']
                   if sql.startswith('SELECT a.id, a.title') and 'FOR UPDATE' in sql]
        self.assertEqual(len(selects), 1)

    def test_blocked_article_is_not_published(self):
        self.login(is_admin=True)
        store = _store(**_draft())
        store['links'] = []                                         # asosiy yo'nalish yo'q
        response, connection = self.post({'article_ids': ['1']}, store)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._updates(connection), [])

    def test_more_than_500_is_refused(self):
        self.login(is_admin=True)
        ids = [str(i) for i in range(1, 502)]
        response, connection = self.post({'article_ids': ids}, _store(**_draft()))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._updates(connection), [])

    def test_nothing_selected(self):
        self.login(is_admin=True)
        _response, connection = self.post({}, _store(**_draft()))
        self.assertEqual(self._updates(connection), [])


class BulkPublishCsrfTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self._orig_csrf = app.config.get('WTF_CSRF_ENABLED', True)
        app.config['WTF_CSRF_ENABLED'] = True

    def tearDown(self):
        app.config['WTF_CSRF_ENABLED'] = self._orig_csrf
        super().tearDown()

    def test_post_without_csrf_token_is_rejected(self):
        self.login(is_admin=True)
        connection = FakeConnection(_store(**_draft()))
        with fake_db(connection):
            response = self.client.post('/admin/articles/bulk-publish',
                                        data={'article_ids': ['1']})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(any(sql.startswith('UPDATE') for sql, _ in connection.store['sql']))


class PublishQueuePageTests(AdminTestCase):
    def test_ready_queue_renders_checkbox_form(self):
        self.login(is_admin=True)
        with fake_db(FakeConnection(_store(**_draft()))):
            response = self.client.get('/admin/articles?queue=ready')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('action="/admin/articles/bulk-publish"', html)
        self.assertIn('name="article_ids" value="1"', html)
        self.assertIn('Tanlanganlarni nashr qilish', html)

    def test_blocked_article_listed_with_reason(self):
        self.login(is_admin=True)
        store = _store(**_draft())
        store['links'] = []
        with fake_db(FakeConnection(store)):
            html = self.client.get('/admin/articles?queue=ready').get_data(as_text=True)
        self.assertIn("Asosiy fan yo‘nalishi tanlanmagan", html)
        self.assertNotIn('name="article_ids" value="1"', html)

    def test_needs_field_queue_filters_in_sql(self):
        self.login(is_admin=True)
        connection = FakeConnection(_store(**_draft()))
        with fake_db(connection):
            response = self.client.get('/admin/articles?queue=needs_field')
        self.assertEqual(response.status_code, 200)
        listing = [sql for sql, _ in connection.store['sql']
                   if sql.startswith('SELECT a.id, a.title')]
        self.assertTrue(listing and 'NOT EXISTS (SELECT 1 FROM article_scientific_fields pf'
                        in listing[0])

    def test_unknown_queue_value_is_ignored(self):
        self.login(is_admin=True)
        with fake_db(FakeConnection(_store())):
            response = self.client.get('/admin/articles?queue=%3Cscript%3E')
        self.assertEqual(response.status_code, 200)


class ReviewGuardTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self._orig_csrf = app.config.get('WTF_CSRF_ENABLED', True)
        app.config['WTF_CSRF_ENABLED'] = False
        self.login(is_admin=True)

    def tearDown(self):
        app.config['WTF_CSRF_ENABLED'] = self._orig_csrf
        super().tearDown()

    def post(self, store, publication='published'):
        connection = FakeConnection(store)
        with fake_db(connection):
            response = self.client.post('/admin/articles/1/review', data={
                'verification_status': 'verified', 'publication_status': publication})
        writes = [sql for sql, _ in connection.store['sql']
                  if sql.startswith('UPDATE scientific_articles')]
        return response, writes

    def test_publishing_without_primary_field_is_refused(self):
        store = _store(**_draft())
        store['links'] = []
        response, writes = self.post(store)
        self.assertEqual(response.status_code, 400)
        self.assertIn('Asosiy fan yo‘nalishi tanlanmagan', response.get_data(as_text=True))
        self.assertEqual(writes, [])

    def test_publishing_without_author_is_refused(self):
        store = _store(**_draft())
        store['authors'] = []
        response, writes = self.post(store)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(writes, [])

    def test_draft_status_change_is_still_allowed_without_field(self):
        store = _store(**_draft())
        store['links'] = []
        response, writes = self.post(store, publication='draft')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(writes), 1)

    def test_complete_article_can_be_published(self):
        response, writes = self.post(_store(**_draft()))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(writes), 1)


class DoiDuplicateTests(BulkPublishTests):
    """DOI noyobligi endi haqiqatan tekshiriladi (ilgari `doi_duplicate=False`)."""

    def test_duplicate_doi_blocks_bulk_publish(self):
        self.login(is_admin=True)
        store = _store(**_draft())
        store['doi_duplicate_ids'] = [1]
        response, connection = self.post({'article_ids': ['1']}, store)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._updates(connection), [])
        check = [sql for sql, _ in connection.store['sql']
                 if sql.startswith('SELECT a.id FROM scientific_articles a WHERE a.id = ANY')]
        self.assertTrue(check)
        self.assertIn('LOWER(BTRIM(b.doi)) = LOWER(BTRIM(a.doi))', check[0])
        self.assertIn('b.id <> a.id', check[0])

    def test_duplicate_doi_is_listed_with_its_reason(self):
        self.login(is_admin=True)
        store = _store(**_draft())
        store['doi_duplicate_ids'] = [1]
        with fake_db(FakeConnection(store)):
            html = self.client.get('/admin/articles?queue=ready').get_data(as_text=True)
        self.assertIn('Bu DOI boshqa maqolada ham bor', html)
        self.assertNotIn('name="article_ids" value="1"', html)


class FormPublishGuardTests(AdminTestCase):
    """`/admin/articles/new` va `/<id>/edit` formasida `published` tanlansa —
    ommaviy nashr bilan bir xil `publish_blockers()`; bloker bo'lsa rollback."""

    def setUp(self):
        super().setUp()
        self._orig_csrf = app.config.get('WTF_CSRF_ENABLED', True)
        app.config['WTF_CSRF_ENABLED'] = False
        self.login(is_admin=True)

    def tearDown(self):
        app.config['WTF_CSRF_ENABLED'] = self._orig_csrf
        super().tearDown()

    def post(self, path, store, **data):
        payload = {
            'title': 'Ochiq maqola', 'language': 'uz', 'access_type': 'metadata_only',
            'license_type': 'unknown', 'version_type': 'version_of_record',
            'source_type': 'open_journal', 'verification_status': 'verified',
            'publication_status': 'published', 'primary_field_id': '1',
            'author_name': 'Karimov Sardor', 'author_orcid': '', 'author_affiliation': '',
            'doi': '10.1234/abc.2024', 'journal_name': 'Ilm Sarchashmalari',
            'publication_year': '2024',
        }
        payload.update(data)
        connection = FakeConnection(store)
        with fake_db(connection):
            response = self.client.post(path, data=payload, content_type='multipart/form-data')
        return response, connection

    def test_edit_publish_without_primary_field_is_rolled_back(self):
        store = _store(**_draft(access_type='metadata_only'))
        store['links'] = []
        response, connection = self.post('/admin/articles/1/edit', store)
        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        self.assertIn('Nashr qilib bo&#39;lmaydi', html)
        self.assertIn('Asosiy fan yo‘nalishi tanlanmagan', html)
        self.assertEqual(connection.rollbacks, 1)

    def test_edit_publish_without_author_is_rolled_back(self):
        store = _store(**_draft())
        store['authors'] = []
        response, connection = self.post('/admin/articles/1/edit', store)
        self.assertEqual(response.status_code, 400)
        self.assertIn('Kamida bitta muallif kerak', response.get_data(as_text=True))
        self.assertEqual(connection.rollbacks, 1)

    def test_edit_publish_with_duplicate_doi_is_rolled_back(self):
        store = _store(**_draft())
        store['doi_duplicate_ids'] = [1]
        response, connection = self.post('/admin/articles/1/edit', store)
        self.assertEqual(response.status_code, 400)
        self.assertIn('Bu DOI boshqa maqolada ham bor', response.get_data(as_text=True))
        self.assertEqual(connection.rollbacks, 1)

    def test_new_article_published_without_doi_is_rolled_back(self):
        store = _store(**_draft(doi=None))
        response, connection = self.post('/admin/articles/new', store, doi='')
        self.assertEqual(response.status_code, 400)
        self.assertIn('DOI yo‘q yoki noto‘g‘ri', response.get_data(as_text=True))
        self.assertEqual(connection.rollbacks, 1)

    def test_complete_article_can_be_published_from_the_edit_form(self):
        response, connection = self.post('/admin/articles/1/edit', _store(**_draft()))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(connection.rollbacks, 0)

    def test_draft_save_without_field_is_not_checked(self):
        store = _store(**_draft())
        store['links'] = []
        response, connection = self.post('/admin/articles/1/edit', store,
                                         publication_status='draft',
                                         verification_status='pending_review')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(connection.rollbacks, 0)


class DiscoveryScopeFormTests(AdminTestCase):
    def test_form_has_scope_checkboxes_and_optional_keyword(self):
        from unittest import mock
        self.login(is_admin=True)
        with mock.patch('blueprints.article_discovery._search_form_context',
                        side_effect=lambda config, **extra: dict(
                            {'config': config, 'field_tree': [], 'saved_queries': [],
                             'providers': ('openalex', 'crossref'),
                             'provider_labels': {'openalex': 'OpenAlex',
                                                 'crossref': 'Crossref'},
                             'frequencies': (), 'frequency_labels': {},
                             'run_status_labels': {}, 'recent_runs': []}, **extra)):
            response = self.client.get('/admin/articles/discovery')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="uz_only"', html)
        self.assertIn('name="core_only"', html)
        keyword_input = html.split('id="ad-keyword"', 1)[1].split('>', 1)[0]
        self.assertNotIn('required', keyword_input)


if __name__ == '__main__':
    unittest.main()
