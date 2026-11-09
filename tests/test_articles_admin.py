# -*- coding: utf-8 -*-
"""Maqolalar admin paneli — avtorizatsiya, CSRF va yuklash validatsiyasi.

Avtorizatsiya va CSRF yo'llari DB'ga tegishdan OLDIN hal bo'ladi, shuning uchun
ular haqiqiy bazasiz ishlaydi (test_data_quality_permissions.py naqshi).
Muvaffaqiyatli yozish yo'li qalbaki ulanish bilan tekshiriladi — u SQL ni
qayd qiladi, lekin FILTRLAMAYDI (tests/fake_pg.py izohiga qarang).
"""
import io
import os
import unittest
from unittest import mock

os.environ.setdefault('APP_ENV', 'testing')
os.environ.setdefault('SESSION_SECRET', 'articles-admin-test-secret')

from app import app, login_manager  # noqa: E402
from tests.fake_pg import FakeConnection, article, field, new_store  # noqa: E402
from tests.test_articles_catalog import fake_db  # noqa: E402

ADMIN_GET_ROUTES = [
    '/admin/articles',
    '/admin/articles/new',
    '/admin/articles/1/edit',
    '/admin/articles/1/review',
    '/admin/articles/1/preview',
    '/admin/scientific-fields',
    '/admin/scientific-fields/new',
    '/admin/scientific-fields/1/edit',
]

ADMIN_POST_ROUTES = [
    '/admin/articles/new',
    '/admin/articles/1/edit',
    '/admin/articles/1/review',
    '/admin/articles/1/delete',
    '/admin/scientific-fields/new',
    '/admin/scientific-fields/1/edit',
    '/admin/scientific-fields/1/delete',
]


class _FakeUser:
    is_authenticated = True
    is_active = True
    is_anonymous = False

    def __init__(self, is_admin):
        self.is_admin = is_admin
        self.id = 1
        self.username = 'tester'

    def get_id(self):
        return '1'


class AdminTestCase(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self._orig_loader = login_manager._user_callback
        self._orig_protection = login_manager.session_protection
        login_manager.session_protection = None

    def tearDown(self):
        login_manager._user_callback = self._orig_loader
        login_manager.session_protection = self._orig_protection

    def login(self, is_admin=True):
        user = _FakeUser(is_admin)
        login_manager._user_callback = lambda uid: user
        with self.client.session_transaction() as sess:
            sess['_user_id'] = '1'
            sess['_fresh'] = True

    def store(self, **kwargs):
        kwargs.setdefault('articles', [article()])
        kwargs.setdefault('fields', [field()])
        return new_store(**kwargs)


class AuthorizationTests(AdminTestCase):
    def test_anonymous_is_login_walled_on_every_admin_page(self):
        for path in ADMIN_GET_ROUTES:
            response = self.client.get(path, follow_redirects=False)
            self.assertIn(response.status_code, (302, 401), path)

    def test_non_admin_gets_403_on_every_admin_page(self):
        self.login(is_admin=False)
        for path in ADMIN_GET_ROUTES:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 403, path)

    def test_non_admin_cannot_post_either(self):
        self.login(is_admin=False)
        for path in ADMIN_POST_ROUTES:
            response = self.client.post(path, data={})
            # 403 (rol) yoki 400 (CSRF) — ikkalasi ham rad etish; 200/302 emas.
            self.assertIn(response.status_code, (400, 403), path)

    def test_admin_passes_authorization(self):
        self.login(is_admin=True)
        connection = FakeConnection(self.store())
        with fake_db(connection):
            response = self.client.get('/admin/articles')
        self.assertEqual(response.status_code, 200)

    def test_admin_list_and_form_render(self):
        self.login(is_admin=True)
        for path in ('/admin/articles', '/admin/articles/new',
                     '/admin/articles/1/edit', '/admin/articles/1/review',
                     '/admin/scientific-fields', '/admin/scientific-fields/new',
                     '/admin/scientific-fields/1/edit'):
            connection = FakeConnection(self.store())
            with fake_db(connection):
                response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)


class CsrfTests(AdminTestCase):
    """CSRF global CSRFProtect orqali — bu yerda uni ATAYIN yoqamiz."""

    def setUp(self):
        super().setUp()
        self._orig_csrf = app.config.get('WTF_CSRF_ENABLED', True)
        app.config['WTF_CSRF_ENABLED'] = True

    def tearDown(self):
        app.config['WTF_CSRF_ENABLED'] = self._orig_csrf
        super().tearDown()

    def test_post_without_a_csrf_token_is_rejected(self):
        self.login(is_admin=True)
        for path in ADMIN_POST_ROUTES:
            connection = FakeConnection(self.store())
            with fake_db(connection):
                response = self.client.post(path, data={'title': 'x'})
            self.assertEqual(response.status_code, 400, path)


class ArticleFormTests(AdminTestCase):
    """Forma validatsiyasi — foydalanuvchiga texnik exception ko'rsatilmaydi."""

    def setUp(self):
        super().setUp()
        self._orig_csrf = app.config.get('WTF_CSRF_ENABLED', True)
        app.config['WTF_CSRF_ENABLED'] = False
        self.login(is_admin=True)

    def tearDown(self):
        app.config['WTF_CSRF_ENABLED'] = self._orig_csrf
        super().tearDown()

    def post(self, path='/admin/articles/new', data=None, files=None, store=None):
        payload = {
            'title': 'Yangi maqola',
            'language': 'uz',
            'access_type': 'metadata_only',
            'license_type': 'unknown',
            'version_type': 'preprint',
            'source_type': 'open_journal',
            'verification_status': 'draft',
            'publication_status': 'draft',
            'primary_field_id': '1',
            'author_name': 'Karimov Sardor',
            'author_orcid': '',
            'author_affiliation': '',
        }
        payload.update(data or {})
        payload.update(files or {})
        connection = FakeConnection(store if store is not None else self.store())
        with fake_db(connection):
            response = self.client.post(path, data=payload,
                                        content_type='multipart/form-data')
        self.connection = connection
        return response

    def body(self, response):
        return response.get_data(as_text=True)

    def test_valid_metadata_only_article_is_accepted(self):
        response = self.post()
        self.assertEqual(response.status_code, 302)

    def test_form_has_a_separate_uzbek_title_field(self):
        connection = FakeConnection(self.store())
        with fake_db(connection):
            html = self.client.get('/admin/articles/1/edit').get_data(as_text=True)
        self.assertIn('name="title_uz"', html)
        self.assertIn("Maqolaning o'zbekcha nomi", html)

    def test_uzbek_title_is_written_with_the_article(self):
        response = self.post(data={'title': 'Original title',
                                   'title_uz': "O'zbekcha nom"})
        self.assertEqual(response.status_code, 302)
        insert = next((sql, params) for sql, params in self.connection.store['sql']
                      if sql.startswith('INSERT INTO scientific_articles'))
        self.assertIn('title_uz', insert[0])
        self.assertIn("O'zbekcha nom", insert[1])

    def test_missing_title_is_a_readable_error_not_a_500(self):
        response = self.post(data={'title': ''})
        self.assertEqual(response.status_code, 400)
        self.assertIn('nomi majburiy', self.body(response))

    def test_missing_primary_field_is_rejected(self):
        response = self.post(data={'primary_field_id': ''})
        self.assertEqual(response.status_code, 400)
        self.assertIn('Asosiy fan', self.body(response))

    def test_missing_author_is_rejected(self):
        response = self.post(data={'author_name': ''})
        self.assertEqual(response.status_code, 400)
        self.assertIn('muallif', self.body(response).lower())

    def test_invalid_doi_shows_a_validation_message(self):
        response = self.post(data={'doi': 'nonsense'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('DOI formati', self.body(response))

    def test_duplicate_doi_is_reported_clearly(self):
        response = self.post(data={'doi': '10.1234/abc'},
                             store=self.store(duplicate_doi=True))
        self.assertEqual(response.status_code, 400)
        self.assertIn('DOI bilan maqola allaqachon mavjud', self.body(response))

    def test_a_unique_doi_passes(self):
        self.assertEqual(self.post(data={'doi': '10.1234/abc'}).status_code, 302)

    def test_invalid_year_is_rejected(self):
        response = self.post(data={'publication_year': '3999'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('yili', self.body(response))

    def test_invalid_date_is_rejected(self):
        response = self.post(data={'publication_date': '2024-13-45'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('sanasi', self.body(response))

    def test_bad_status_value_is_rejected(self):
        response = self.post(data={'publication_status': 'hacked'})
        self.assertEqual(response.status_code, 400)

    def test_submitted_values_survive_a_validation_error(self):
        response = self.post(data={'title': '', 'journal_name': 'Ilm Sarchashmalari'})
        self.assertIn('Ilm Sarchashmalari', self.body(response))


class PdfUploadRouteTests(ArticleFormTests):
    """§11 — noto'g'ri, katta va PDF bo'lmagan yuklamalar rad etiladi."""

    def upload(self, data=b'%PDF-1.7\n' + b'x' * 512 + b'\n%%EOF\n',
               filename='maqola.pdf', **overrides):
        payload = {
            'access_type': 'open_access',
            'license_type': 'cc_by',
            'source_name': 'Ilm Sarchashmalari',
            'rights_confirmed': '1',
        }
        payload.update(overrides)
        payload['pdf_file'] = (io.BytesIO(data), filename)
        return self.post(data=payload)

    def test_a_valid_pdf_is_accepted(self):
        with mock.patch('blueprints.articles.save_private_pdf',
                        return_value='2026/08/x.pdf') as saver:
            response = self.upload()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(saver.called)

    def test_non_pdf_upload_is_rejected(self):
        response = self.upload(data=b'GIF89a not a pdf', filename='rasm.gif')
        self.assertEqual(response.status_code, 400)
        self.assertIn('.pdf', self.body(response))

    def test_renamed_non_pdf_is_rejected_by_magic_bytes(self):
        response = self.upload(data=b'MZ\x90 fake', filename='maqola.pdf')
        self.assertEqual(response.status_code, 400)
        self.assertIn('%PDF', self.body(response))

    def test_oversized_upload_is_rejected(self):
        with mock.patch('security_uploads.MAX_PDF_BYTES', 128), \
                mock.patch('blueprints.articles.MAX_PDF_BYTES', 128):
            response = self.upload(data=b'%PDF-1.7\n' + b'x' * 5000 + b'\n%%EOF\n')
        self.assertEqual(response.status_code, 400)
        self.assertIn('hajmdan katta', self.body(response))

    def test_duplicate_checksum_is_reported_and_nothing_is_stored(self):
        payload = {
            'access_type': 'open_access', 'license_type': 'cc_by',
            'source_name': 'Ilm Sarchashmalari', 'rights_confirmed': '1',
            'pdf_file': (io.BytesIO(b'%PDF-1.7\n' + b'x' * 512 + b'\n%%EOF\n'),
                         'maqola.pdf'),
        }
        with mock.patch('blueprints.articles.save_private_pdf') as saver:
            response = self.post(data=payload,
                                 store=self.store(duplicate_checksum=True))
        self.assertEqual(response.status_code, 400)
        self.assertIn('allaqachon mavjud', self.body(response))
        self.assertFalse(saver.called, 'dublikat fayl diskka yozilmasligi kerak')

    def test_metadata_only_access_refuses_the_file_entirely(self):
        response = self.upload(access_type='metadata_only')
        self.assertEqual(response.status_code, 400)
        self.assertIn('PDF', self.body(response))

    def test_upload_without_the_rights_confirmation_is_refused(self):
        response = self.upload(rights_confirmed='')
        self.assertEqual(response.status_code, 400)
        self.assertIn('huquqiy tasdiq', self.body(response).lower())

    def test_a_failed_save_does_not_leave_the_new_file_behind(self):
        # Fayl diskka tushdi, keyin validatsiya yiqildi -> yangi fayl o'chiriladi.
        with mock.patch('blueprints.articles.save_private_pdf',
                        return_value='2026/08/x.pdf'), \
                mock.patch('blueprints.articles._write_associations',
                           side_effect=__import__('utils.article_rules', fromlist=['x'])
                           .ArticleValidationError('sinov xatosi')), \
                mock.patch('blueprints.articles._delete_stored_pdf') as remover:
            response = self.upload()
        self.assertEqual(response.status_code, 400)
        remover.assert_called_once_with('2026/08/x.pdf')


class ScientificFieldFormTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self._orig_csrf = app.config.get('WTF_CSRF_ENABLED', True)
        app.config['WTF_CSRF_ENABLED'] = False
        self.login(is_admin=True)

    def tearDown(self):
        app.config['WTF_CSRF_ENABLED'] = self._orig_csrf
        super().tearDown()

    def post(self, path='/admin/scientific-fields/new', **data):
        payload = {'name_uz': 'Yangi yo\'nalish', 'sort_order': '0', 'is_active': '1'}
        payload.update(data)
        connection = FakeConnection(new_store(fields=[field()]))
        with fake_db(connection):
            return self.client.post(path, data=payload)

    def test_a_valid_field_is_created(self):
        self.assertEqual(self.post(code='14.00.00').status_code, 302)

    def test_missing_name_is_rejected(self):
        response = self.post(name_uz='')
        self.assertEqual(response.status_code, 400)
        self.assertIn('nomi majburiy', response.get_data(as_text=True))

    def test_a_field_cannot_be_made_its_own_parent(self):
        response = self.post(path='/admin/scientific-fields/1/edit', parent_id='1')
        self.assertEqual(response.status_code, 400)
        self.assertIn('ota', response.get_data(as_text=True).lower())

    def test_bad_source_date_is_rejected(self):
        response = self.post(source_updated_at='2024-99-99')
        self.assertEqual(response.status_code, 400)

    def test_delete_keeps_a_field_that_still_has_children(self):
        connection = FakeConnection(new_store(fields=[field()], child_count=2))
        with fake_db(connection):
            response = self.client.post('/admin/scientific-fields/1/delete')
        self.assertEqual(response.status_code, 302)
        sql = ' '.join(s for s, _ in connection.store['sql'])
        self.assertIn('is_active = FALSE', sql)
        self.assertNotIn('DELETE FROM scientific_fields', sql)


class ArchiveTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self._orig_csrf = app.config.get('WTF_CSRF_ENABLED', True)
        app.config['WTF_CSRF_ENABLED'] = False
        self.login(is_admin=True)

    def tearDown(self):
        app.config['WTF_CSRF_ENABLED'] = self._orig_csrf
        super().tearDown()

    def test_deleting_an_article_archives_it_and_keeps_the_file(self):
        connection = FakeConnection(new_store(articles=[article()], fields=[field()]))
        with fake_db(connection):
            response = self.client.post('/admin/articles/1/delete')
        self.assertEqual(response.status_code, 302)
        sql = ' '.join(s for s, _ in connection.store['sql'])
        self.assertIn("publication_status = 'archived'", sql)
        self.assertNotIn('DELETE FROM scientific_articles', sql)


if __name__ == '__main__':
    unittest.main()
