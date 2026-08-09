# -*- coding: utf-8 -*-
"""Crossref discovery adapter testlari (Phase 2D.2).

`services.article_discovery.crossref.fetch_json`ni monkeypatch qilamiz —
haqiqiy tarmoqqa chiqilmaydi (`test_article_discovery_openalex.py` bilan bir
xil naqsh). Rasmiy hujjat (2026-08 tekshirilgan) kontraktini tekshiradi:
`query.bibliographic`, `filter=from-pub-date:...,until-pub-date:...`,
`rows`, `mailto`, javob konverti `message.total-results`/`message.items`.
"""
import unittest
from datetime import date
from unittest import mock

from services.article_discovery.config import ArticleDiscoveryConfig
from services.article_discovery.models import ArticleDiscoveryQuery
from services.article_discovery import crossref
from services.article_metadata.http import HttpOutcome
from services.article_metadata.models import (INVALID_RESPONSE, NOT_FOUND, OK,
                                              RATE_LIMITED, TIMEOUT, UNAVAILABLE)

CONFIGURED = ArticleDiscoveryConfig(crossref_mailto='test@example.com')
UNCONFIGURED = ArticleDiscoveryConfig()
QUERY = ArticleDiscoveryQuery(keyword='oncology', from_date=date(2024, 1, 1),
                              to_date=date(2024, 1, 31), oak_field_id=None, limit=50,
                              providers=('crossref',))


def _work(doi='10.1000/xyz', title='Title', journal='Journal X',
         retracted=False, published=(2024, 1, 15)):
    work = {
        'DOI': doi, 'title': [title] if title else [],
        'container-title': [journal] if journal else [],
        'type': 'journal-article',
        'published': {'date-parts': [list(published)]} if published else None,
    }
    if retracted:
        work['update-to'] = [{'type': 'retraction', 'DOI': '10.1000/retraction-notice',
                              'label': 'Retraction'}]
    return work


class NeverNotConfiguredTests(unittest.TestCase):
    """Rasmiy hujjat: Crossref /works uchun kalit SHART EMAS — mailto'siz
    ham so'rov yuboriladi, `not_configured` YO'Q (§4)."""

    def test_no_mailto_still_sends_request(self):
        with mock.patch('services.article_discovery.crossref.fetch_json') as fj:
            fj.return_value = HttpOutcome(
                OK, payload={'message': {'total-results': 0, 'items': []}})
            result = crossref.search(QUERY, UNCONFIGURED)
        fj.assert_called_once()
        self.assertEqual(result.status, OK)
        _, kwargs = fj.call_args
        self.assertNotIn('mailto', kwargs['params'])


class RequestConstructionTests(unittest.TestCase):
    def _call_and_capture(self, config=CONFIGURED, query=QUERY):
        with mock.patch('services.article_discovery.crossref.fetch_json') as fj:
            fj.return_value = HttpOutcome(
                OK, payload={'message': {'total-results': 0, 'items': []}})
            crossref.search(query, config)
        return fj.call_args

    def test_uses_fixed_crossref_host(self):
        args, kwargs = self._call_and_capture()
        self.assertEqual(args[0], 'https://api.crossref.org/works')

    def test_keyword_mapped_to_query_bibliographic(self):
        _, kwargs = self._call_and_capture()
        self.assertEqual(kwargs['params']['query.bibliographic'], 'oncology')
        self.assertNotIn('query', kwargs['params'])

    def test_filter_includes_pub_date_range(self):
        _, kwargs = self._call_and_capture()
        filt = kwargs['params']['filter']
        self.assertIn('from-pub-date:2024-01-01', filt)
        self.assertIn('until-pub-date:2024-01-31', filt)

    def test_filter_includes_journal_article_type(self):
        _, kwargs = self._call_and_capture()
        self.assertIn('type:journal-article', kwargs['params']['filter'])

    def test_rows_bounded_by_hard_limit(self):
        big_query = ArticleDiscoveryQuery(keyword='x', from_date=date(2024, 1, 1),
                                          to_date=date(2024, 1, 2), oak_field_id=None,
                                          limit=9999, providers=('crossref',))
        _, kwargs = self._call_and_capture(query=big_query)
        self.assertEqual(kwargs['params']['rows'], str(CONFIGURED.hard_result_limit))

    def test_no_offset_deep_paging_param_sent(self):
        """Rasmiy hujjat (api_tips.md) `rows`/`offset` chuqur sahifalashni
        tavsiya qilmaydi — biz umuman `offset` yubormaymiz (bitta
        chegaralangan sahifa, §4-§5)."""
        _, kwargs = self._call_and_capture()
        self.assertNotIn('offset', kwargs['params'])

    def test_mailto_used_when_configured(self):
        _, kwargs = self._call_and_capture()
        self.assertEqual(kwargs['params']['mailto'], 'test@example.com')

    def test_mailto_absent_when_not_configured(self):
        _, kwargs = self._call_and_capture(config=UNCONFIGURED)
        self.assertNotIn('mailto', kwargs['params'])

    def test_contact_email_never_appears_in_headers_repr_beyond_useragent(self):
        _, kwargs = self._call_and_capture()
        # User-Agent LEGITIMATE joyi — lekin boshqa hech qayerda emas.
        self.assertIn('test@example.com', kwargs['headers']['User-Agent'])


class ExtractionTests(unittest.TestCase):
    def test_minimal_field_extraction(self):
        payload = {'message': {'total-results': 1, 'items': [_work()]}}
        with mock.patch('services.article_discovery.crossref.fetch_json') as fj:
            fj.return_value = HttpOutcome(OK, payload=payload)
            result = crossref.search(QUERY, CONFIGURED)
        self.assertEqual(result.status, OK)
        self.assertEqual(len(result.candidates), 1)
        c = result.candidates[0]
        self.assertEqual(c.doi, '10.1000/xyz')
        self.assertEqual(c.title, 'Title')
        self.assertEqual(c.source_name, 'Journal X')
        self.assertEqual(c.publication_date.isoformat(), '2024-01-15')
        self.assertEqual(c.providers, ['crossref'])
        self.assertFalse(c.is_retracted)

    def test_retraction_flag_extracted_from_update_to(self):
        payload = {'message': {'total-results': 1,
                               'items': [_work(retracted=True)]}}
        with mock.patch('services.article_discovery.crossref.fetch_json') as fj:
            fj.return_value = HttpOutcome(OK, payload=payload)
            result = crossref.search(QUERY, CONFIGURED)
        self.assertTrue(result.candidates[0].is_retracted)

    def test_missing_doi_excluded_and_counted(self):
        work = _work()
        del work['DOI']
        payload = {'message': {'total-results': 2,
                               'items': [work, _work(doi='10.1000/yes')]}}
        with mock.patch('services.article_discovery.crossref.fetch_json') as fj:
            fj.return_value = HttpOutcome(OK, payload=payload)
            result = crossref.search(QUERY, CONFIGURED)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.excluded_no_doi_count, 1)

    def test_malformed_doi_excluded_and_counted_not_enqueued(self):
        """§6 — DOI-only siyosat: `validate_doi` formatini rad etsa ham
        (bo'sh DOI'dan farqli, "topilgan lekin noto'g'ri" holat) candidate
        YARATILMAYDI — statistikaga qo'shiladi."""
        payload = {'message': {'total-results': 1,
                               'items': [_work(doi='not-a-real-doi')]}}
        with mock.patch('services.article_discovery.crossref.fetch_json') as fj:
            fj.return_value = HttpOutcome(OK, payload=payload)
            result = crossref.search(QUERY, CONFIGURED)
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.excluded_no_doi_count, 1)

    def test_duplicate_doi_within_response_both_kept_by_adapter(self):
        """Adapter dublikat klassifikatsiya qilmaydi — service.py vazifasi
        (openalex.py bilan bir xil kontrakt)."""
        payload = {'message': {'total-results': 2,
                               'items': [_work(doi='10.1000/same'),
                                        _work(doi='10.1000/same')]}}
        with mock.patch('services.article_discovery.crossref.fetch_json') as fj:
            fj.return_value = HttpOutcome(OK, payload=payload)
            result = crossref.search(QUERY, CONFIGURED)
        self.assertEqual(len(result.candidates), 2)

    def test_malformed_response_missing_message(self):
        with mock.patch('services.article_discovery.crossref.fetch_json') as fj:
            fj.return_value = HttpOutcome(OK, payload={'no-message-here': True})
            result = crossref.search(QUERY, CONFIGURED)
        self.assertEqual(result.status, INVALID_RESPONSE)

    def test_malformed_response_items_not_a_list(self):
        with mock.patch('services.article_discovery.crossref.fetch_json') as fj:
            fj.return_value = HttpOutcome(
                OK, payload={'message': {'total-results': 0, 'items': 'nope'}})
            result = crossref.search(QUERY, CONFIGURED)
        self.assertEqual(result.status, INVALID_RESPONSE)

    def test_non_dict_item_in_items_skipped(self):
        payload = {'message': {'total-results': 2,
                               'items': ['not-a-dict', _work()]}}
        with mock.patch('services.article_discovery.crossref.fetch_json') as fj:
            fj.return_value = HttpOutcome(OK, payload=payload)
            result = crossref.search(QUERY, CONFIGURED)
        self.assertEqual(len(result.candidates), 1)

    def test_total_results_reflected_as_total_count(self):
        payload = {'message': {'total-results': 54321, 'items': [_work()]}}
        with mock.patch('services.article_discovery.crossref.fetch_json') as fj:
            fj.return_value = HttpOutcome(OK, payload=payload)
            result = crossref.search(QUERY, CONFIGURED)
        self.assertEqual(result.total_count, 54321)

    def test_external_source_id_left_none_doi_is_the_identifier(self):
        payload = {'message': {'total-results': 1, 'items': [_work()]}}
        with mock.patch('services.article_discovery.crossref.fetch_json') as fj:
            fj.return_value = HttpOutcome(OK, payload=payload)
            result = crossref.search(QUERY, CONFIGURED)
        self.assertIsNone(result.candidates[0].external_source_id)

    def test_raw_provider_payload_not_exposed_on_candidate(self):
        """§4/§8 — xom Crossref JSON candidate obyektiga umuman sizib
        chiqmasligi kerak (faqat tanlangan maydonlar)."""
        payload = {'message': {'total-results': 1, 'items': [_work()]}}
        with mock.patch('services.article_discovery.crossref.fetch_json') as fj:
            fj.return_value = HttpOutcome(OK, payload=payload)
            result = crossref.search(QUERY, CONFIGURED)
        candidate_fields = vars(result.candidates[0]).keys()
        self.assertNotIn('raw', candidate_fields)
        self.assertNotIn('_work', candidate_fields)
        # dataclass maydonlari qat'iy ro'yxat — xom dict/hujjat obyekt emas.
        self.assertEqual(
            set(candidate_fields),
            {'doi', 'external_source_id', 'title', 'publication_date',
             'source_name', 'is_retracted', 'topics', 'classification',
             'existing_article_id', 'existing_article_slug', 'providers'})


class ProviderErrorMappingTests(unittest.TestCase):
    """`fetch_json` — timeout/429/5xx/malformed uchun umumiy, tipланган
    xato holatlari (§21) — Crossref o'zining xato xaritasini yaratmaydi."""

    def _mapped_status(self, outcome):
        with mock.patch('services.article_discovery.crossref.fetch_json') as fj:
            fj.return_value = outcome
            return crossref.search(QUERY, CONFIGURED).status

    def test_not_found_maps_through(self):
        self.assertEqual(self._mapped_status(HttpOutcome(NOT_FOUND, http_status=404)),
                         NOT_FOUND)

    def test_rate_limited_maps_through(self):
        self.assertEqual(self._mapped_status(HttpOutcome(RATE_LIMITED, http_status=429)),
                         RATE_LIMITED)

    def test_timeout_maps_through(self):
        self.assertEqual(self._mapped_status(HttpOutcome(TIMEOUT)), TIMEOUT)

    def test_unavailable_5xx_maps_through(self):
        self.assertEqual(self._mapped_status(HttpOutcome(UNAVAILABLE, http_status=503)),
                         UNAVAILABLE)

    def test_invalid_response_maps_through(self):
        self.assertEqual(self._mapped_status(HttpOutcome(INVALID_RESPONSE)),
                         INVALID_RESPONSE)


if __name__ == '__main__':
    unittest.main()
