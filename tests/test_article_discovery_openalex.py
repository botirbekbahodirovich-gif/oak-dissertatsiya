# -*- coding: utf-8 -*-
"""OpenAlex discovery adapter testlari (Phase 2D.1, §39).

`services.article_discovery.openalex.fetch_json`ni monkeypatch qilamiz —
haqiqiy tarmoqqa chiqilmaydi. `services/article_metadata/http.py:HttpOutcome`
qayta ishlatiladi (bir xil status kontrakti).
"""
import unittest
from datetime import date
from unittest import mock

from services.article_discovery.config import ArticleDiscoveryConfig
from services.article_discovery.models import ArticleDiscoveryQuery
from services.article_discovery import openalex
from services.article_metadata.http import HttpOutcome
from services.article_metadata.models import (INVALID_RESPONSE, NOT_CONFIGURED,
                                              NOT_FOUND, OK, RATE_LIMITED,
                                              TIMEOUT, UNAVAILABLE)

CONFIGURED = ArticleDiscoveryConfig(openalex_mailto='test@example.com')
QUERY = ArticleDiscoveryQuery(keyword='oncology', from_date=date(2024, 1, 1),
                              to_date=date(2024, 1, 31), oak_field_id=None, limit=50)


def _work(doi='10.1000/xyz', work_id='https://openalex.org/W1234567', title='Title',
         is_retracted=False):
    return {
        'id': work_id, 'doi': 'https://doi.org/%s' % doi if doi else None,
        'title': title, 'publication_date': '2024-01-15',
        'primary_location': {'source': {'display_name': 'Journal X'}},
        'is_retracted': is_retracted,
        'primary_topic': {'display_name': 'Oncology'},
    }


class NotConfiguredTests(unittest.TestCase):
    def test_no_key_no_mailto_returns_not_configured_without_request(self):
        config = ArticleDiscoveryConfig()
        with mock.patch('services.article_discovery.openalex.fetch_json') as fj:
            result = openalex.search(QUERY, config)
        fj.assert_not_called()
        self.assertEqual(result.status, NOT_CONFIGURED)
        self.assertEqual(result.candidates, [])


class RequestConstructionTests(unittest.TestCase):
    def _call_and_capture(self, config=CONFIGURED):
        with mock.patch('services.article_discovery.openalex.fetch_json') as fj:
            fj.return_value = HttpOutcome(OK, payload={'results': [], 'meta': {'count': 0}})
            openalex.search(QUERY, config)
        return fj.call_args

    def test_uses_fixed_openalex_host(self):
        args, kwargs = self._call_and_capture()
        self.assertEqual(args[0], 'https://api.openalex.org/works')

    def test_keyword_mapped_to_search_param(self):
        _, kwargs = self._call_and_capture()
        self.assertEqual(kwargs['params']['search'], 'oncology')

    def test_filter_includes_has_doi_and_type_article(self):
        _, kwargs = self._call_and_capture()
        filt = kwargs['params']['filter']
        self.assertIn('has_doi:true', filt)
        self.assertIn('type:article', filt)

    def test_filter_includes_date_range(self):
        _, kwargs = self._call_and_capture()
        filt = kwargs['params']['filter']
        self.assertIn('from_publication_date:2024-01-01', filt)
        self.assertIn('to_publication_date:2024-01-31', filt)

    def test_per_page_bounded_by_hard_limit(self):
        big_query = ArticleDiscoveryQuery(keyword='x', from_date=date(2024, 1, 1),
                                          to_date=date(2024, 1, 2), oak_field_id=None,
                                          limit=9999)
        with mock.patch('services.article_discovery.openalex.fetch_json') as fj:
            fj.return_value = HttpOutcome(OK, payload={'results': [], 'meta': {'count': 0}})
            openalex.search(big_query, CONFIGURED)
        self.assertEqual(fj.call_args[1]['params']['per_page'],
                         str(CONFIGURED.hard_result_limit))

    def test_mailto_used_when_no_api_key(self):
        _, kwargs = self._call_and_capture()
        self.assertEqual(kwargs['params']['mailto'], 'test@example.com')
        self.assertNotIn('api_key', kwargs['params'])

    def test_api_key_preferred_over_mailto(self):
        config = ArticleDiscoveryConfig(openalex_api_key='secret-key',
                                        openalex_mailto='test@example.com')
        _, kwargs = self._call_and_capture(config)
        self.assertEqual(kwargs['params']['api_key'], 'secret-key')
        self.assertNotIn('mailto', kwargs['params'])

    def test_api_key_never_appears_in_headers_or_repr(self):
        config = ArticleDiscoveryConfig(openalex_api_key='super-secret')
        _, kwargs = self._call_and_capture(config)
        self.assertNotIn('super-secret', str(kwargs['headers']))


class ExtractionTests(unittest.TestCase):
    def test_minimal_field_extraction(self):
        payload = {'results': [_work()], 'meta': {'count': 1}}
        with mock.patch('services.article_discovery.openalex.fetch_json') as fj:
            fj.return_value = HttpOutcome(OK, payload=payload)
            result = openalex.search(QUERY, CONFIGURED)
        self.assertEqual(result.status, OK)
        self.assertEqual(len(result.candidates), 1)
        c = result.candidates[0]
        self.assertEqual(c.doi, '10.1000/xyz')
        self.assertEqual(c.title, 'Title')
        self.assertEqual(c.source_name, 'Journal X')
        self.assertEqual(c.external_source_id, 'W1234567')
        self.assertFalse(c.is_retracted)

    def test_retracted_flag_extracted(self):
        payload = {'results': [_work(is_retracted=True)], 'meta': {'count': 1}}
        with mock.patch('services.article_discovery.openalex.fetch_json') as fj:
            fj.return_value = HttpOutcome(OK, payload=payload)
            result = openalex.search(QUERY, CONFIGURED)
        self.assertTrue(result.candidates[0].is_retracted)

    def test_no_doi_work_excluded_and_counted(self):
        payload = {'results': [_work(doi=None), _work(doi='10.1000/yes')],
                  'meta': {'count': 2}}
        with mock.patch('services.article_discovery.openalex.fetch_json') as fj:
            fj.return_value = HttpOutcome(OK, payload=payload)
            result = openalex.search(QUERY, CONFIGURED)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.excluded_no_doi_count, 1)

    def test_duplicate_doi_within_response_both_kept_by_adapter(self):
        """Adapter DUPLIKATNI o'zi klassifikatsiya QILMAYDI — bu servis
        qatlami vazifasi (§13). Adapter faqat xom nomzodlarni chiqaradi."""
        payload = {'results': [_work(doi='10.1000/same'), _work(doi='10.1000/same')],
                  'meta': {'count': 2}}
        with mock.patch('services.article_discovery.openalex.fetch_json') as fj:
            fj.return_value = HttpOutcome(OK, payload=payload)
            result = openalex.search(QUERY, CONFIGURED)
        self.assertEqual(len(result.candidates), 2)

    def test_malformed_json_results_not_a_list(self):
        with mock.patch('services.article_discovery.openalex.fetch_json') as fj:
            fj.return_value = HttpOutcome(OK, payload={'results': 'not-a-list'})
            result = openalex.search(QUERY, CONFIGURED)
        self.assertEqual(result.status, INVALID_RESPONSE)

    def test_result_cap_reflected_in_total_vs_inspected(self):
        payload = {'results': [_work(doi='10.1000/%d' % i) for i in range(3)],
                  'meta': {'count': 12000}}
        with mock.patch('services.article_discovery.openalex.fetch_json') as fj:
            fj.return_value = HttpOutcome(OK, payload=payload)
            result = openalex.search(QUERY, CONFIGURED)
        self.assertEqual(result.total_count, 12000)
        self.assertEqual(len(result.candidates), 3)


class ProviderErrorMappingTests(unittest.TestCase):
    def _mapped_status(self, outcome):
        with mock.patch('services.article_discovery.openalex.fetch_json') as fj:
            fj.return_value = outcome
            return openalex.search(QUERY, CONFIGURED).status

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
