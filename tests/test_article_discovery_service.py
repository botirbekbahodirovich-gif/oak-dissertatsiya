# -*- coding: utf-8 -*-
"""Discovery servis qatlami testlari — validatsiya, klassifikatsiya, preview
token (Phase 2D.1, §40).
"""
import unittest
from datetime import date, timedelta
from unittest import mock

from services.article_discovery.config import ArticleDiscoveryConfig
from services.article_discovery.models import (ALREADY_QUEUED,
                                                DUPLICATE_IN_RESULT,
                                                EXISTING_ARTICLE, NEW,
                                                ArticleDiscoveryQuery,
                                                DiscoveryCandidate,
                                                DiscoveryPreviewResult,
                                                DiscoveryValidationError)
from services.article_discovery import service as svc
from services.article_metadata.models import NOT_CONFIGURED, OK
from tests.fake_article_discovery_pg import FakeDiscoveryCursor, new_store

TEST_CONFIG = ArticleDiscoveryConfig(openalex_mailto='test@example.com')


class ValidateQueryTests(unittest.TestCase):
    def test_keyword_too_short_rejected(self):
        with self.assertRaises(DiscoveryValidationError):
            svc.validate_query({'keyword': 'ab'}, config=TEST_CONFIG)

    def test_keyword_too_long_rejected(self):
        with self.assertRaises(DiscoveryValidationError):
            svc.validate_query({'keyword': 'a' * 301}, config=TEST_CONFIG)

    def test_missing_dates_default_to_window(self):
        query = svc.validate_query({'keyword': 'oncology'}, config=TEST_CONFIG)
        self.assertEqual((query.to_date - query.from_date).days,
                         TEST_CONFIG.default_window_days)

    def test_reversed_date_range_rejected(self):
        with self.assertRaises(DiscoveryValidationError):
            svc.validate_query({'keyword': 'oncology', 'from_date': '2024-02-01',
                               'to_date': '2024-01-01'}, config=TEST_CONFIG)

    def test_window_wider_than_twelve_months_rejected(self):
        with self.assertRaises(DiscoveryValidationError):
            svc.validate_query({'keyword': 'oncology', 'from_date': '2020-01-01',
                               'to_date': '2024-01-01'}, config=TEST_CONFIG)

    def test_one_sided_date_range_rejected(self):
        with self.assertRaises(DiscoveryValidationError):
            svc.validate_query({'keyword': 'oncology', 'from_date': '2024-01-01'},
                              config=TEST_CONFIG)

    def test_limit_defaults_when_missing(self):
        query = svc.validate_query({'keyword': 'oncology'}, config=TEST_CONFIG)
        self.assertEqual(query.limit, TEST_CONFIG.default_result_limit)

    def test_limit_one_accepted(self):
        query = svc.validate_query({'keyword': 'oncology', 'limit': '1'},
                                   config=TEST_CONFIG)
        self.assertEqual(query.limit, 1)

    def test_limit_fifty_accepted(self):
        query = svc.validate_query({'keyword': 'oncology', 'limit': '50'},
                                   config=TEST_CONFIG)
        self.assertEqual(query.limit, 50)

    def test_limit_above_hard_cap_is_clamped_not_rejected(self):
        query = svc.validate_query({'keyword': 'oncology', 'limit': '500'},
                                   config=TEST_CONFIG)
        self.assertEqual(query.limit, TEST_CONFIG.hard_result_limit)

    def test_oak_field_id_optional(self):
        query = svc.validate_query({'keyword': 'oncology'}, config=TEST_CONFIG)
        self.assertIsNone(query.oak_field_id)

    def test_oak_field_id_parsed(self):
        query = svc.validate_query({'keyword': 'oncology', 'oak_field_id': '7'},
                                   config=TEST_CONFIG)
        self.assertEqual(query.oak_field_id, 7)


def _candidate(doi, external='W1', title='T'):
    return DiscoveryCandidate(doi=doi, external_source_id=external, title=title)


class SearchResultStub:
    def __init__(self, status=OK, candidates=None, total_count=0,
                excluded_no_doi_count=0, error_code=None):
        self.status = status
        self.candidates = candidates or []
        self.total_count = total_count
        self.excluded_no_doi_count = excluded_no_doi_count
        self.error_code = error_code


QUERY = ArticleDiscoveryQuery(keyword='oncology', from_date=date(2024, 1, 1),
                              to_date=date(2024, 1, 31), oak_field_id=None, limit=50)


class RunDiscoverySearchTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store()
        self.cur = FakeDiscoveryCursor(self.store)

    def test_not_configured_short_circuits(self):
        result = svc.run_discovery_search(
            self.cur, QUERY, config=TEST_CONFIG,
            search=lambda q, c: SearchResultStub(status=NOT_CONFIGURED))
        self.assertEqual(result.status, NOT_CONFIGURED)
        self.assertEqual(result.candidates, [])

    def test_provider_error_propagates(self):
        result = svc.run_discovery_search(
            self.cur, QUERY, config=TEST_CONFIG,
            search=lambda q, c: SearchResultStub(status='timeout'))
        self.assertEqual(result.status, 'timeout')

    def test_new_candidate_classified_new(self):
        result = svc.run_discovery_search(
            self.cur, QUERY, config=TEST_CONFIG,
            search=lambda q, c: SearchResultStub(
                candidates=[_candidate('10.1000/a')], total_count=1))
        self.assertEqual(result.candidates[0].classification, NEW)

    def test_same_response_duplicate_classified(self):
        result = svc.run_discovery_search(
            self.cur, QUERY, config=TEST_CONFIG,
            search=lambda q, c: SearchResultStub(
                candidates=[_candidate('10.1000/a'), _candidate('10.1000/a')],
                total_count=2))
        statuses = sorted(c.classification for c in result.candidates)
        self.assertEqual(statuses, [DUPLICATE_IN_RESULT, NEW])

    def test_existing_article_classified(self):
        self.store['articles'].append(
            {'id': 5, 'title': 'X', 'slug': 'x-slug', 'doi': '10.1000/a'})
        result = svc.run_discovery_search(
            self.cur, QUERY, config=TEST_CONFIG,
            search=lambda q, c: SearchResultStub(
                candidates=[_candidate('10.1000/a')], total_count=1))
        c = result.candidates[0]
        self.assertEqual(c.classification, EXISTING_ARTICLE)
        self.assertEqual(c.existing_article_id, 5)
        self.assertEqual(c.existing_article_slug, 'x-slug')

    def test_active_import_item_classified_already_queued(self):
        self.store['items'].append({
            'id': 1, 'batch_id': 1, 'row_number': 1, 'raw_input': '10.1000/a',
            'normalized_doi': '10.1000/a', 'status': 'pending',
            'is_retracted': False, 'attempt_count': 0, 'conflicts': None,
            'warnings': None,
        })
        result = svc.run_discovery_search(
            self.cur, QUERY, config=TEST_CONFIG,
            search=lambda q, c: SearchResultStub(
                candidates=[_candidate('10.1000/a')], total_count=1))
        self.assertEqual(result.candidates[0].classification, ALREADY_QUEUED)

    def test_approved_import_item_does_not_block_rediscovery(self):
        """`approved`/`rejected`/`skipped` — TERMINAL, ACTIVE_DOI_ITEM_STATUSES
        ichida yo'q, shuning uchun qayta topilsa `new` (agar article ham
        yaratilmagan bo'lsa — approved holida odatda article bor, lekin bu
        test faqat navbat holatini tekshiradi)."""
        self.store['items'].append({
            'id': 1, 'batch_id': 1, 'row_number': 1, 'raw_input': '10.1000/a',
            'normalized_doi': '10.1000/a', 'status': 'rejected',
            'is_retracted': False, 'attempt_count': 0, 'conflicts': None,
            'warnings': None,
        })
        result = svc.run_discovery_search(
            self.cur, QUERY, config=TEST_CONFIG,
            search=lambda q, c: SearchResultStub(
                candidates=[_candidate('10.1000/a')], total_count=1))
        self.assertEqual(result.candidates[0].classification, NEW)

    def test_provider_total_vs_inspected_preserved(self):
        result = svc.run_discovery_search(
            self.cur, QUERY, config=TEST_CONFIG,
            search=lambda q, c: SearchResultStub(
                candidates=[_candidate('10.1000/a')], total_count=12000))
        self.assertEqual(result.total_count, 12000)
        self.assertEqual(result.inspected_count, 1)

    def test_no_doi_excluded_count_passed_through(self):
        result = svc.run_discovery_search(
            self.cur, QUERY, config=TEST_CONFIG,
            search=lambda q, c: SearchResultStub(excluded_no_doi_count=3))
        self.assertEqual(result.excluded_no_doi_count, 3)


class PreviewTokenTests(unittest.TestCase):
    def setUp(self):
        self.cache = {}

    def _cache_get(self, key):
        return self.cache.get(key)

    def _cache_set(self, key, value, ttl):
        self.cache[key] = value

    def test_store_and_load_roundtrip(self):
        preview = DiscoveryPreviewResult(status=OK, query=QUERY,
                                         candidates=[_candidate('10.1000/a')])
        token = svc.store_preview(self._cache_set, admin_id=7, preview=preview,
                                  config=TEST_CONFIG)
        entry = svc.load_preview(self._cache_get, token, admin_id=7)
        self.assertIsNotNone(entry)
        self.assertEqual(entry['admin_id'], 7)
        self.assertEqual(len(entry['candidates']), 1)

    def test_token_not_actor_bound_returns_none(self):
        preview = DiscoveryPreviewResult(status=OK, query=QUERY, candidates=[])
        token = svc.store_preview(self._cache_set, admin_id=7, preview=preview,
                                  config=TEST_CONFIG)
        entry = svc.load_preview(self._cache_get, token, admin_id=99)
        self.assertIsNone(entry)

    def test_unknown_token_returns_none(self):
        self.assertIsNone(svc.load_preview(self._cache_get, 'nope', admin_id=7))

    def test_empty_token_returns_none(self):
        self.assertIsNone(svc.load_preview(self._cache_get, '', admin_id=7))

    def test_consumed_marker_roundtrip(self):
        svc.mark_preview_consumed(self._cache_set, 'tok123', batch_id=42,
                                  config=TEST_CONFIG)
        consumed = svc.load_consumed(self._cache_get, 'tok123')
        self.assertEqual(consumed['batch_id'], 42)
        self.assertTrue(consumed['consumed'])


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        self.cache = {}
        self.config = ArticleDiscoveryConfig(searches_per_minute=2)

    def _get(self, key):
        return self.cache.get(key)

    def _set(self, key, value, ttl):
        self.cache[key] = value

    def test_allows_up_to_limit(self):
        self.assertTrue(svc.check_rate_limit(self._get, self._set, 1, config=self.config))
        self.assertTrue(svc.check_rate_limit(self._get, self._set, 1, config=self.config))

    def test_blocks_after_limit(self):
        svc.check_rate_limit(self._get, self._set, 1, config=self.config)
        svc.check_rate_limit(self._get, self._set, 1, config=self.config)
        self.assertFalse(svc.check_rate_limit(self._get, self._set, 1, config=self.config))

    def test_limit_is_per_admin(self):
        svc.check_rate_limit(self._get, self._set, 1, config=self.config)
        svc.check_rate_limit(self._get, self._set, 1, config=self.config)
        self.assertTrue(svc.check_rate_limit(self._get, self._set, 2, config=self.config))


if __name__ == '__main__':
    unittest.main()
