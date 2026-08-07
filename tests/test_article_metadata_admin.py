# -*- coding: utf-8 -*-
"""DOI autofill endpointi, admin formasi va sxema (Phase 2C.1, §37–§38).

Endpoint testlari haqiqiy Flask route'ini chaqiradi, lekin tashqi tarmoqqa
chiqmaydi: `fetch_article_metadata` o'rniga qo'yiladi. Asosiy tekshiruv —
endpoint DB'ga HECH NARSA yozmasligi va javobda xom API ma'lumoti yoki
kalitlar bo'lmasligi.
"""
import io
import json
import os
import re
import unittest
from unittest import mock

os.environ.setdefault('APP_ENV', 'testing')
os.environ.setdefault('SESSION_SECRET', 'article-metadata-test-secret')

from app import app  # noqa: E402
from services.article_metadata.config import MetadataConfig  # noqa: E402
from services.article_metadata.crossref import extract as crossref_extract  # noqa: E402
from services.article_metadata.models import (CROSSREF, NOT_CONFIGURED, OK,  # noqa: E402
                                              OPENALEX, TIMEOUT, UNPAYWALL,
                                              ProviderResult)
from services.article_metadata.openalex import extract as openalex_extract  # noqa: E402
from services.article_metadata.service import (MetadataLookupError,  # noqa: E402
                                               fetch_article_metadata)
from services.article_metadata.unpaywall import extract as unpaywall_extract  # noqa: E402
from tests.fake_pg import FakeConnection, article, field, new_store  # noqa: E402
from tests.fake_scholarly_api import (crossref_work, openalex_work,  # noqa: E402
                                      unpaywall_record)
from tests.test_articles_admin import AdminTestCase  # noqa: E402
from tests.test_articles_catalog import fake_db  # noqa: E402

ROOT = app.root_path
FORM_TEMPLATE = os.path.join(ROOT, 'templates', 'admin', 'article_form.html')
METADATA_JS = os.path.join(ROOT, 'static', 'js', 'maqolalar_metadata.js')
MODULE_CSS = os.path.join(ROOT, 'static', 'css', 'maqolalar.css')
MIGRATION = os.path.join(ROOT, 'migrations', '009_article_external_metadata.sql')
BLUEPRINT = os.path.join(ROOT, 'blueprints', 'articles.py')

ENDPOINT = '/admin/articles/metadata/lookup'
DOI = '10.1234/abcd.2024.01'


def read(path):
    with io.open(path, encoding='utf-8') as fh:
        return fh.read()


def full_result(doi=DOI, **statuses):
    """Uch provayder ham ishlagan haqiqiy natija (tarmoqsiz)."""
    def make(provider, extractor, payload):
        status = statuses.get(provider, OK)
        if status != OK:
            return ProviderResult(provider=provider, status=status)
        data, _warnings = extractor(payload)
        return lambda d, c, **k: ProviderResult(provider=provider, status=OK,
                                                data=data)
    fetchers = {
        CROSSREF: make(CROSSREF, crossref_extract, crossref_work()['message']),
        OPENALEX: make(OPENALEX, openalex_extract, openalex_work()),
        UNPAYWALL: make(UNPAYWALL, unpaywall_extract, unpaywall_record()),
    }
    for provider, value in list(fetchers.items()):
        if not callable(value):
            fetchers[provider] = (lambda captured: (lambda d, c, **k: captured))(value)
    return fetch_article_metadata(doi, config=MetadataConfig(contact_email='t@o.uz'),
                                  fetchers=fetchers)


class LookupEndpointTestCase(AdminTestCase):
    limiter = False        # cheklovchi testdan tashqarida o'chirilgan

    def setUp(self):
        super().setUp()
        self._csrf = app.config.get('WTF_CSRF_ENABLED', True)
        app.config['WTF_CSRF_ENABLED'] = False
        # Hisoblagich Flask-Caching faylida yashaydi va testlar orasida
        # saqlanib qoladi — u faqat o'ziga bag'ishlangan testda yoqiladi.
        if not self.limiter:
            self._limiter_patch = mock.patch(
                'blueprints.articles._metadata_rate_limited', return_value=False)
            self._limiter_patch.start()
            self.addCleanup(self._limiter_patch.stop)

    def tearDown(self):
        app.config['WTF_CSRF_ENABLED'] = self._csrf
        super().tearDown()

    def post(self, payload=None, result=None, error=None, articles=None):
        store = new_store(articles=articles if articles is not None else [],
                          fields=[field()])
        connection = FakeConnection(store)
        target = 'blueprints.articles.fetch_article_metadata'
        if error is not None:
            patch = mock.patch(target, side_effect=error)
        else:
            patch = mock.patch(target, return_value=result or full_result())
        with fake_db(connection), patch as spy:
            response = self.client.post(
                ENDPOINT, data=json.dumps(payload if payload is not None
                                          else {'doi': DOI}),
                content_type='application/json')
        self.store = store
        self.spy = spy
        return response


class AuthorizationTests(LookupEndpointTestCase):
    def test_anonymous_is_rejected(self):
        response = self.client.post(ENDPOINT, json={'doi': DOI})
        self.assertIn(response.status_code, (302, 401))

    def test_non_admin_is_forbidden(self):
        self.login(is_admin=False)
        response = self.client.post(ENDPOINT, json={'doi': DOI})
        self.assertEqual(response.status_code, 403)

    def test_the_endpoint_is_post_only(self):
        self.login(is_admin=True)
        self.assertEqual(self.client.get(ENDPOINT).status_code, 405)


class CsrfTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self._csrf = app.config.get('WTF_CSRF_ENABLED', True)
        app.config['WTF_CSRF_ENABLED'] = True

    def tearDown(self):
        app.config['WTF_CSRF_ENABLED'] = self._csrf
        super().tearDown()

    def test_a_post_without_a_csrf_token_is_rejected(self):
        self.login(is_admin=True)
        response = self.client.post(ENDPOINT, json={'doi': DOI})
        self.assertEqual(response.status_code, 400)


class LookupBehaviourTests(LookupEndpointTestCase):
    def setUp(self):
        super().setUp()
        self.login(is_admin=True)

    def test_a_successful_lookup_returns_normalised_metadata(self):
        body = self.post().get_json()
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['doi'], DOI)
        self.assertEqual(body['metadata']['journal_name'],
                         'Journal of Educational Research')
        self.assertEqual(body['metadata']['openalex_id'], 'W2741809807')
        self.assertGreater(body['field_count'], 5)

    def test_partial_success_is_reported_per_provider(self):
        response = self.post(result=full_result(**{UNPAYWALL: TIMEOUT}))
        body = response.get_json()
        self.assertEqual(body['status'], 'partial_success')
        self.assertEqual(body['providers'][UNPAYWALL]['status'], 'timeout')
        self.assertEqual(body['providers'][CROSSREF]['status'], 'ok')
        # Foydalanuvchi tushunadigan matn — texnik trace emas.
        self.assertTrue(any('javob bermadi' in w['message']
                            for w in body['warnings']))

    def test_a_missing_openalex_key_does_not_break_the_lookup(self):
        body = self.post(result=full_result(**{OPENALEX: NOT_CONFIGURED})).get_json()
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['providers'][OPENALEX]['status'], 'not_configured')
        self.assertEqual(body['providers'][OPENALEX]['status_label'], 'sozlanmagan')

    def test_every_provider_failing_is_reported_without_a_crash(self):
        body = self.post(result=full_result(**{CROSSREF: TIMEOUT, OPENALEX: TIMEOUT,
                                               UNPAYWALL: TIMEOUT})).get_json()
        self.assertEqual(body['status'], 'failed')
        self.assertTrue(any(w['code'] == 'all_providers_failed'
                            for w in body['warnings']))

    def test_an_invalid_doi_is_a_readable_400(self):
        response = self.post(error=MetadataLookupError('DOI formati noto‘g‘ri.',
                                                       code='invalid_doi'))
        self.assertEqual(response.status_code, 400)
        body = response.get_json()
        self.assertEqual(body['error'], 'invalid_doi')
        self.assertIn('DOI', body['message'])

    def test_an_unexpected_error_never_leaks_a_stack_trace(self):
        response = self.post(error=RuntimeError('psycopg2 connection to 10.0.0.1'))
        self.assertEqual(response.status_code, 502)
        body = response.get_json()
        self.assertNotIn('psycopg2', json.dumps(body))
        self.assertNotIn('Traceback', json.dumps(body))

    def test_an_existing_article_with_the_same_doi_is_flagged(self):
        body = self.post(articles=[article(id=7, title='Eski yozuv')]).get_json()
        self.assertEqual(body['existing_article']['id'], 7)
        self.assertEqual(body['existing_article']['edit_url'],
                         '/admin/articles/7/edit')

    def test_no_existing_article_means_no_duplicate_warning(self):
        self.assertIsNone(self.post().get_json()['existing_article'])


class NoMutationTests(LookupEndpointTestCase):
    """§14 — lookup faqat o'qiydi. Bu bosqichning eng muhim invarianti."""

    def setUp(self):
        super().setUp()
        self.login(is_admin=True)

    def test_the_endpoint_never_writes_to_the_database(self):
        # app.py dagi `track_visit` hooki har so'rovda `page_visits` ga yozadi
        # va u bu modulning mas'uliyati emas — faqat MAQOLA jadvallariga
        # tegilmaganini tekshiramiz.
        self.post(articles=[article(id=7)])
        tables = ('scientific_articles', 'article_authors',
                  'article_scientific_fields', 'scientific_fields')
        writes = [sql for sql, _params in self.store['sql']
                  if sql.upper().startswith(('INSERT', 'UPDATE', 'DELETE'))
                  and any(table in sql for table in tables)]
        self.assertEqual(writes, [])

    def test_it_only_reads_the_duplicate_check(self):
        self.post()
        selects = [sql for sql, _params in self.store['sql']
                   if sql.upper().startswith('SELECT')
                   and 'scientific_articles' in sql]
        self.assertTrue(selects)
        for sql in selects:
            self.assertIn('SELECT', sql.upper())

    def test_it_never_downloads_a_pdf(self):
        """Unpaywall PDF havolasi faqat nomzod — fayl olinmaydi (§24)."""
        body = self.post().get_json()
        locations = body['metadata']['external_locations']
        self.assertTrue(any(l['pdf_url'] for l in locations))
        self.assertTrue(any(w['code'] == 'pdf_candidate_only'
                            for w in body['warnings']))
        # Javobda saqlangan fayl yo'li yoki pdf_path yangilanishi yo'q.
        self.assertNotIn('pdf_path', json.dumps(body))

    def test_open_access_status_is_never_mapped_to_the_internal_access_type(self):
        body = self.post().get_json()
        self.assertEqual(body['metadata']['oa_status'], 'gold')
        self.assertNotIn('access_type', json.dumps(body['metadata']))
        self.assertTrue(any(w['code'] == 'oa_status_is_not_access_type'
                            for w in body['warnings']))


class ResponseSafetyTests(LookupEndpointTestCase):
    def setUp(self):
        super().setUp()
        self.login(is_admin=True)

    def test_raw_provider_payloads_are_not_returned(self):
        body = self.post().get_json()
        for provider, info in body['providers'].items():
            self.assertNotIn('data', info, provider)
        # Crossref xom javobidagi kalitlar javobda bo'lmasligi kerak.
        encoded = json.dumps(body)
        for marker in ('message-type', 'date-parts', 'abstract_inverted_index',
                       'authorships', 'oa_locations'):
            self.assertNotIn(marker, encoded, marker)

    def test_no_configuration_secret_reaches_the_browser(self):
        config = MetadataConfig(contact_email='secret@olimlar.uz',
                                openalex_api_key='super-secret-key')
        with mock.patch('services.article_metadata.service.config_from_env',
                        return_value=config):
            body = self.post().get_json()
        encoded = json.dumps(body)
        self.assertNotIn('super-secret-key', encoded)
        self.assertNotIn('secret@olimlar.uz', encoded)

    def test_the_form_page_never_exposes_provider_credentials(self):
        config = MetadataConfig(contact_email='secret@olimlar.uz',
                                openalex_api_key='super-secret-key')
        connection = FakeConnection(new_store(articles=[article()], fields=[field()]))
        with mock.patch('services.article_metadata.service.config_from_env',
                        return_value=config), fake_db(connection):
            html = self.client.get('/admin/articles/new').get_data(as_text=True)
        self.assertNotIn('super-secret-key', html)
        self.assertNotIn('secret@olimlar.uz', html)


class RateLimitTests(LookupEndpointTestCase):
    limiter = True

    def setUp(self):
        super().setUp()
        self.login(is_admin=True)
        from extensions import cache
        cache.delete('article_metadata_lookup:1')

    def test_a_burst_from_one_admin_is_throttled(self):
        from blueprints.articles import METADATA_RATE_LIMIT
        codes = [self.post().status_code
                 for _ in range(METADATA_RATE_LIMIT + 3)]
        self.assertEqual(codes[0], 200)
        self.assertIn(429, codes)
        self.assertEqual(self.post().get_json()['error'], 'rate_limited')


class AdminFormMarkupTests(AdminTestCase):
    """§12, §38 — forma tugmasi, holatlar va dizayn tizimi."""

    def setUp(self):
        super().setUp()
        self.login(is_admin=True)
        connection = FakeConnection(new_store(articles=[article()], fields=[field()]))
        with mock.patch('blueprints.articles._metadata_rate_limited',
                        return_value=False), fake_db(connection):
            self.html = self.client.get('/admin/articles/new').get_data(as_text=True)
        # base.html shelli (header, footer, banner) bu modulning mas'uliyati
        # emas — tekshiruvlar FORMA hududi ustida bajariladi.
        self.form = re.search(r'<form class="mq-form".*?</form>', self.html, re.S).group(0)

    def test_the_doi_field_has_a_lookup_action(self):
        self.assertIn('data-mq-meta-fetch', self.html)
        self.assertIn("Ma'lumotlarni olish", self.html)

    def test_the_panel_declares_the_endpoint_and_starts_hidden(self):
        panel = re.search(r'<section class="mq-meta"[^>]*>', self.html).group(0)
        self.assertIn('data-endpoint="/admin/articles/metadata/lookup"', panel)
        self.assertIn('hidden', panel)

    def test_the_panel_announces_status_and_errors_accessibly(self):
        self.assertIn('role="status"', self.html)
        self.assertIn('aria-labelledby="mq-meta-title"', self.html)
        self.assertIn('aria-expanded="false"', self.html)

    def test_the_page_still_has_exactly_one_h1(self):
        self.assertEqual(len(re.findall(r'<h1[\s>]', self.html)), 1)

    def test_the_hidden_identifier_fields_exist_for_saving(self):
        for name in ('openalex_id', 'crossref_member_id', 'primary_issn',
                     'citation_count', 'citation_count_source',
                     'citation_count_checked_at', 'metadata_source_summary'):
            self.assertIn('name="%s"' % name, self.html, name)
        self.assertIn('name="author_openalex_id"', self.html)

    def test_the_retraction_flag_is_visible_and_explained(self):
        self.assertIn('name="is_retracted"', self.html)
        self.assertIn('Chaqirib olingan', self.html)
        self.assertIn('avtomatik nashr etilmaydi', self.html)

    def test_the_form_says_nothing_is_saved_until_submit(self):
        self.assertIn('"Saqlash" bosilmaguncha bazaga yozilmaydi', self.html)

    def test_only_one_primary_action_in_the_form(self):
        """Yagona asosiy amal — "Saqlash"; lookup tugmasi ikkilamchi."""
        self.assertEqual(self.form.count('ol-btn--primary'), 1)
        self.assertIn('ol-btn--secondary mq-meta__fetch', self.form)

    def test_module_assets_carry_a_cache_buster(self):
        module_assets = [href for href in
                         re.findall(r'(?:href|src)="(/static/[^"]+)"', self.html)
                         if 'maqolalar' in href]
        self.assertGreaterEqual(len(module_assets), 3)
        for href in module_assets:
            self.assertIn('?v=', href, href)

    def test_the_form_carries_no_inline_styles(self):
        self.assertNotIn('style="', self.form)


class MetadataUiSourceTests(unittest.TestCase):
    """Brauzersiz tekshirib bo'ladigan kontrakt: CSS va JS matni."""

    @classmethod
    def setUpClass(cls):
        cls.css = read(MODULE_CSS)
        cls.js = read(METADATA_JS)
        cls.markup = read(FORM_TEMPLATE)

    def test_the_lookup_button_meets_the_44px_target(self):
        block = re.search(r'\.mq-meta__fetch\s*\{[^}]*\}', self.css)
        self.assertIsNotNone(block)
        self.assertIn('44px', block.group(0))
        for selector in (r'\.mq-meta__actions \.ol-btn', r'\.mq-meta__apply'):
            found = re.search(selector + r'\s*\{[^}]*\}', self.css)
            self.assertIsNotNone(found, selector)
            self.assertIn('44px', found.group(0), selector)

    def test_the_module_css_still_uses_tokens_only(self):
        self.assertEqual(re.findall(r'#[0-9a-fA-F]{3,8}\b', self.css), [])
        self.assertNotIn('gradient', self.css)
        self.assertNotIn('backdrop-filter', self.css)
        self.assertNotRegex(self.css, r'color-scheme\s*:\s*[^;}\n]*dark')

    def test_provider_status_is_never_colour_only(self):
        """Chip ichida nom va holat matni bo'ladi (dizayn tizimi §5.2)."""
        self.assertIn("info.label + ' — ' + info.status_label", self.js)

    def test_external_text_is_inserted_as_text_never_as_html(self):
        self.assertNotIn('innerHTML', self.js)
        self.assertNotIn('insertAdjacentHTML', self.js)
        self.assertIn('textContent', self.js)

    def test_the_template_marks_nothing_but_the_csrf_token_as_safe(self):
        """`csrf_token()` serverimiz yasagan <input> — yagona ruxsat etilgan
        `|safe`. Tashqi metadata hech qachon shu yo'ldan o'tmaydi (§31)."""
        self.assertEqual(re.findall(r'\{\{\s*([\w().]+)\|safe\s*\}\}', self.markup),
                         ['csrf_token()'])

    def test_external_links_open_safely(self):
        self.assertIn("link.rel = 'noopener noreferrer nofollow'", self.js)
        self.assertIn("link.target = '_blank'", self.js)

    def test_the_request_sends_the_csrf_header(self):
        self.assertIn("'X-CSRFToken'", self.js)
        self.assertIn("credentials: 'same-origin'", self.js)

    def test_loading_error_and_stale_states_exist(self):
        self.assertIn('Ma‘lumotlar olinmoqda…', self.js)
        self.assertIn('Tarmoq xatosi', self.js)
        self.assertIn('DOI o‘zgardi', self.js)

    def test_a_previous_request_is_aborted_and_double_clicks_blocked(self):
        self.assertIn('AbortController', self.js)
        self.assertIn('state.controller.abort()', self.js)
        self.assertIn('fetchButton.disabled = true', self.js)
        self.assertIn('REQUEST_TIMEOUT_MS', self.js)

    def test_the_bulk_action_only_fills_empty_fields(self):
        self.assertIn("Bo'sh maydonlarni to'ldirish", self.markup)
        block = re.search(r"el\.fill\.addEventListener\('click'.*?\}\);",
                          self.js, re.S).group(0)
        self.assertIn('!isEmpty(entry)', block)

    def test_the_licence_is_excluded_from_the_bulk_action(self):
        """Litsenziya huquqiy qaror — u har doim alohida bosish talab qiladi."""
        for key in ('license_type', 'license_url'):
            entry = re.search(r"\{ key: '%s'.*?\}" % key, self.js).group(0)
            self.assertIn('bulk: false', entry, key)

    def test_the_retraction_flag_is_only_ever_switched_on(self):
        block = re.search(r'function applyField\(.*?\n  \}', self.js, re.S).group(0)
        self.assertIn('if (node.checked) return false;', block)

    def test_unknown_select_values_are_not_guessed(self):
        self.assertIn('function selectHasOption', self.js)
        block = re.search(r"\} else if \(entry\.type === 'select'\) \{[^}]*\}",
                          self.js).group(0)
        self.assertIn('if (!selectHasOption(node, value)) return false;', block)

    def test_topics_are_shown_but_never_mapped_to_an_oak_field(self):
        self.assertIn('OAK fan yo‘nalishi ular asosida avtomatik', self.js)
        self.assertNotIn('primary_field_id', self.js)

    def test_pdf_links_are_labelled_as_information_only(self):
        self.assertIn('Fayl avtomatik yuklanmaydi', self.js)

    def test_no_decorative_emoji_in_the_autofill_ui(self):
        emoji = re.compile('[\U0001F300-\U0001FAFF←-⇿☀-➿]')
        for name, text in (('js', self.js), ('html', self.markup)):
            self.assertEqual(emoji.findall(text), [], name)


class SchemaTests(unittest.TestCase):
    """§18 — migratsiya va `_ensure_schema` ajralib ketmasligi kerak."""

    @classmethod
    def setUpClass(cls):
        cls.raw = read(MIGRATION)
        cls.code = '\n'.join(
            line if line.find('--') == -1 else line[:line.find('--')]
            for line in cls.raw.splitlines())
        cls.blueprint = read(BLUEPRINT)

    NEW_COLUMNS = ('openalex_id', 'crossref_member_id', 'primary_issn',
                   'is_retracted', 'citation_count', 'citation_count_source',
                   'citation_count_checked_at', 'metadata_last_synced_at',
                   'metadata_source_summary')

    def test_it_runs_in_one_transaction(self):
        self.assertIn('BEGIN;', self.code.upper())
        self.assertIn('COMMIT;', self.code.upper())

    def test_every_statement_is_idempotent(self):
        self.assertNotIn('ADD COLUMN ', self.code.replace('ADD COLUMN IF NOT EXISTS', ''))
        for statement in re.findall(r'CREATE\s+(?:UNIQUE\s+)?INDEX\s+(\S+)',
                                    self.code.upper()):
            self.assertEqual(statement, 'IF')

    def test_nothing_destructive_runs(self):
        for keyword in ('DROP ', 'TRUNCATE', 'DELETE FROM', 'UPDATE '):
            self.assertNotIn(keyword, self.code.upper(), keyword)

    def test_the_rollback_is_documented_but_commented_out(self):
        self.assertIn('ROLLBACK', self.raw.upper())
        self.assertNotIn('DROP COLUMN', self.code.upper())

    def test_no_raw_api_response_column_is_created(self):
        """Xom Crossref/OpenAlex/Unpaywall JSON maqola jadvaliga tiqilmaydi (§20)."""
        for forbidden in ('raw_response', 'crossref_raw', 'openalex_raw',
                          'api_response'):
            self.assertNotIn(forbidden, self.code.lower(), forbidden)

    def test_the_blueprint_adds_the_same_columns(self):
        for column in self.NEW_COLUMNS:
            self.assertIn(column, self.code, column)
            self.assertIn("'%s'" % column, self.blueprint, column)
        self.assertIn('openalex_author_id', self.code)
        self.assertIn('openalex_author_id', self.blueprint)

    def test_the_blueprint_creates_the_same_named_indexes(self):
        names = set(re.findall(r'CREATE (?:UNIQUE )?INDEX IF NOT EXISTS (\w+)',
                               self.code))
        self.assertTrue(names)
        for name in names:
            self.assertIn(name, self.blueprint, name)

    def test_the_openalex_id_is_unique_only_for_filled_rows(self):
        self.assertRegex(
            self.code,
            r'CREATE UNIQUE INDEX IF NOT EXISTS uq_scientific_articles_openalex\s+'
            r'ON scientific_articles \(openalex_id\)')

    def test_the_new_columns_are_written_by_the_form_path(self):
        write_block = re.search(r'ARTICLE_WRITE_COLUMNS = \((.*?)\n\)',
                                self.blueprint, re.S).group(1)
        for column in self.NEW_COLUMNS:
            if column == 'metadata_last_synced_at':
                # Uni forma emas, server qo'yadi.
                self.assertNotIn("'%s'" % column, write_block)
                continue
            self.assertIn("'%s'" % column, write_block, column)


class ExternalMetadataValidationTests(unittest.TestCase):
    """Yashirin maydonlar buzilgan bo'lsa maqola saqlanishi to'xtamasin."""

    def normalize(self, **values):
        from blueprints.articles import _normalize_external_metadata

        class Cursor:
            def execute(self, *_a, **_k):
                pass

            def fetchone(self):
                return None

        payload = {'openalex_id': '', 'crossref_member_id': '', 'primary_issn': '',
                   'citation_count': '', 'citation_count_source': '',
                   'citation_count_checked_at': '', 'metadata_source_summary': ''}
        payload.update(values)
        return _normalize_external_metadata(Cursor(), payload)

    def test_valid_identifiers_pass_through(self):
        out = self.normalize(openalex_id='W2741809807', crossref_member_id='286',
                             primary_issn='8765-4321')
        self.assertEqual(out['openalex_id'], 'W2741809807')
        self.assertEqual(out['crossref_member_id'], '286')
        self.assertEqual(out['primary_issn'], '8765-4321')

    def test_garbage_identifiers_are_dropped_not_raised(self):
        out = self.normalize(openalex_id='<script>', crossref_member_id='abc',
                             primary_issn='nope')
        self.assertIsNone(out['openalex_id'])
        self.assertIsNone(out['crossref_member_id'])
        self.assertIsNone(out['primary_issn'])

    def test_a_citation_count_without_a_source_is_discarded(self):
        out = self.normalize(citation_count='11')
        self.assertIsNone(out['citation_count'])
        self.assertIsNone(out['citation_count_checked_at'])

    def test_a_citation_count_always_gets_a_checked_at_timestamp(self):
        out = self.normalize(citation_count='11', citation_count_source='openalex')
        self.assertEqual(out['citation_count'], 11)
        self.assertIsNotNone(out['citation_count_checked_at'])

    def test_an_unknown_citation_source_is_rejected(self):
        out = self.normalize(citation_count='11', citation_count_source='wikipedia')
        self.assertIsNone(out['citation_count_source'])
        self.assertIsNone(out['citation_count'])

    def test_the_source_summary_is_reduced_to_short_strings(self):
        out = self.normalize(metadata_source_summary=json.dumps(
            {'title': 'crossref', 'nested': {'x': 1}, 'oa_status': 'unpaywall'}))
        parsed = json.loads(out['metadata_source_summary'])
        self.assertEqual(parsed, {'title': 'crossref', 'oa_status': 'unpaywall'})

    def test_a_broken_source_summary_becomes_none(self):
        self.assertIsNone(self.normalize(
            metadata_source_summary='{not json')['metadata_source_summary'])

    def test_a_source_summary_can_never_smuggle_a_raw_api_payload(self):
        huge = json.dumps({'k%d' % i: 'v' * 500 for i in range(200)})
        parsed = json.loads(self.normalize(
            metadata_source_summary=huge)['metadata_source_summary'])
        self.assertLessEqual(len(parsed), 40)
        self.assertTrue(all(len(v) <= 64 for v in parsed.values()))


if __name__ == '__main__':
    unittest.main()
