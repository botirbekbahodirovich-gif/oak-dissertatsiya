# -*- coding: utf-8 -*-
"""Saqlangan qidiruv so'rovlari — servis/persistence qatlami testlari
(Phase 2D.2, §9-§11, §24).
"""
import unittest
from datetime import date
from unittest import mock

from services.article_discovery import crossref, openalex, saved_queries
from services.article_discovery.config import ArticleDiscoveryConfig
from services.article_discovery.models import (ArticleDiscoveryQuery,
                                                DiscoveryValidationError)
from services.article_discovery import service as discovery_service
from tests.fake_saved_queries_pg import FakeSavedQueriesCursor, new_store

CONFIG = ArticleDiscoveryConfig(openalex_mailto='t@example.com')
QUERY = ArticleDiscoveryQuery(keyword='oncology', from_date=date(2024, 1, 1),
                              to_date=date(2024, 1, 31), oak_field_id=7, limit=25,
                              providers=('openalex', 'crossref'))


class NameValidationTests(unittest.TestCase):
    def test_empty_name_rejected(self):
        with self.assertRaises(DiscoveryValidationError):
            saved_queries.validate_name('')

    def test_whitespace_only_name_rejected(self):
        with self.assertRaises(DiscoveryValidationError):
            saved_queries.validate_name('   ')

    def test_too_long_name_rejected(self):
        with self.assertRaises(DiscoveryValidationError):
            saved_queries.validate_name('x' * 121)

    def test_valid_name_stripped(self):
        self.assertEqual(saved_queries.validate_name('  Oncology weekly  '),
                         'Oncology weekly')

    def test_max_length_name_accepted(self):
        name = 'x' * 120
        self.assertEqual(saved_queries.validate_name(name), name)


class ProviderEnumValidationTests(unittest.TestCase):
    """Saqlangan so'rov `discovery_service.validate_query()` orqali
    tekshiriladi — ikkinchi validator YO'Q (§20)."""

    def test_unknown_provider_rejected(self):
        with self.assertRaises(DiscoveryValidationError):
            discovery_service.validate_query(
                {'keyword': 'oncology', 'providers': ['scopus']}, config=CONFIG)

    def test_empty_provider_list_rejected(self):
        with self.assertRaises(DiscoveryValidationError):
            discovery_service.validate_query(
                {'keyword': 'oncology', 'providers': []}, config=CONFIG)

    def test_both_providers_accepted(self):
        query = discovery_service.validate_query(
            {'keyword': 'oncology', 'providers': ['openalex', 'crossref']}, config=CONFIG)
        self.assertEqual(query.providers, ('openalex', 'crossref'))

    def test_crossref_only_accepted(self):
        query = discovery_service.validate_query(
            {'keyword': 'oncology', 'providers': ['crossref']}, config=CONFIG)
        self.assertEqual(query.providers, ('crossref',))

    def test_missing_providers_field_defaults_to_openalex_only(self):
        """Backward compat (2D.1) — maydon UMUMAN berilmasa."""
        query = discovery_service.validate_query({'keyword': 'oncology'}, config=CONFIG)
        self.assertEqual(query.providers, ('openalex',))

    def test_duplicate_provider_values_deduplicated(self):
        query = discovery_service.validate_query(
            {'keyword': 'oncology', 'providers': ['openalex', 'openalex']},
            config=CONFIG)
        self.assertEqual(query.providers, ('openalex',))

    def test_invalid_limit_clamped_not_rejected(self):
        query = discovery_service.validate_query(
            {'keyword': 'oncology', 'limit': '99999'}, config=CONFIG)
        self.assertEqual(query.limit, CONFIG.hard_result_limit)

    def test_invalid_date_rejected(self):
        with self.assertRaises(DiscoveryValidationError):
            discovery_service.validate_query(
                {'keyword': 'oncology', 'from_date': 'not-a-date',
                 'to_date': '2024-01-01'}, config=CONFIG)

    def test_oak_field_optional(self):
        query = discovery_service.validate_query({'keyword': 'oncology'}, config=CONFIG)
        self.assertIsNone(query.oak_field_id)


class CrudTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store()
        self.cur = FakeSavedQueriesCursor(self.store)

    def test_create_saved_query(self):
        query_id = saved_queries.create(self.cur, created_by=1, name='Oncology weekly',
                                        query=QUERY)
        self.assertEqual(len(self.store['rows']), 1)
        row = self.store['rows'][0]
        self.assertEqual(row['id'], query_id)
        self.assertEqual(row['name'], 'Oncology weekly')
        self.assertEqual(row['keyword'], 'oncology')
        self.assertEqual(row['providers'], ['openalex', 'crossref'])
        self.assertTrue(row['is_active'])

    def test_get_returns_created_row(self):
        query_id = saved_queries.create(self.cur, 1, 'X', QUERY)
        row = saved_queries.get(self.cur, query_id)
        self.assertEqual(row['name'], 'X')
        self.assertEqual(row['oak_field_id'], 7)

    def test_get_unknown_id_returns_none(self):
        self.assertIsNone(saved_queries.get(self.cur, 999))

    def test_list_all_orders_active_first_then_newest(self):
        id1 = saved_queries.create(self.cur, 1, 'First', QUERY)
        id2 = saved_queries.create(self.cur, 1, 'Second', QUERY)
        saved_queries.set_active(self.cur, id1, False)
        rows = saved_queries.list_all(self.cur, include_inactive=True)
        self.assertEqual([r['id'] for r in rows], [id2, id1])

    def test_list_all_excludes_inactive_when_requested(self):
        id1 = saved_queries.create(self.cur, 1, 'First', QUERY)
        id2 = saved_queries.create(self.cur, 1, 'Second', QUERY)
        saved_queries.set_active(self.cur, id1, False)
        rows = saved_queries.list_all(self.cur, include_inactive=False)
        self.assertEqual([r['id'] for r in rows], [id2])

    def test_update_renames_and_changes_params(self):
        query_id = saved_queries.create(self.cur, 1, 'Old name', QUERY)
        new_query = ArticleDiscoveryQuery(
            keyword='genomics', from_date=date(2024, 2, 1), to_date=date(2024, 2, 28),
            oak_field_id=None, limit=10, providers=('crossref',))
        ok = saved_queries.update(self.cur, query_id, 'New name', new_query)
        self.assertTrue(ok)
        row = saved_queries.get(self.cur, query_id)
        self.assertEqual(row['name'], 'New name')
        self.assertEqual(row['keyword'], 'genomics')
        self.assertEqual(row['providers'], ['crossref'])

    def test_update_unknown_id_returns_false(self):
        self.assertFalse(saved_queries.update(self.cur, 999, 'X', QUERY))

    def test_deactivate_sets_is_active_false(self):
        query_id = saved_queries.create(self.cur, 1, 'X', QUERY)
        ok = saved_queries.set_active(self.cur, query_id, False)
        self.assertTrue(ok)
        self.assertFalse(saved_queries.get(self.cur, query_id)['is_active'])

    def test_deactivate_unknown_id_returns_false(self):
        self.assertFalse(saved_queries.set_active(self.cur, 999, False))

    def test_touch_last_used_sets_timestamp(self):
        query_id = saved_queries.create(self.cur, 1, 'X', QUERY)
        self.assertIsNone(saved_queries.get(self.cur, query_id)['last_used_at'])
        saved_queries.touch_last_used(self.cur, query_id)
        self.assertIsNotNone(saved_queries.get(self.cur, query_id)['last_used_at'])

    def test_to_query_reconstructs_article_discovery_query(self):
        query_id = saved_queries.create(self.cur, 1, 'X', QUERY)
        row = saved_queries.get(self.cur, query_id)
        rebuilt = saved_queries.to_query(row)
        self.assertEqual(rebuilt.keyword, QUERY.keyword)
        self.assertEqual(rebuilt.from_date, QUERY.from_date)
        self.assertEqual(rebuilt.to_date, QUERY.to_date)
        self.assertEqual(rebuilt.oak_field_id, QUERY.oak_field_id)
        self.assertEqual(rebuilt.limit, QUERY.limit)
        self.assertEqual(rebuilt.providers, QUERY.providers)

    def test_no_ownership_wall_any_admin_sees_all_rows(self):
        """§20 audit natijasi (saved_queries.py docstring'iga qarang):
        `article_import_batches` bilan bir xil siyosat — `created_by` FAQAT
        audit, kirish nazorati EMAS. `list_all`/`get` `created_by`ni
        filtrlamaydi."""
        saved_queries.create(self.cur, created_by=1, name='Admin1 query', query=QUERY)
        saved_queries.create(self.cur, created_by=2, name='Admin2 query', query=QUERY)
        rows = saved_queries.list_all(self.cur, include_inactive=True)
        self.assertEqual({r['created_by'] for r in rows}, {1, 2})


class NoExternalCallOnLoadTests(unittest.TestCase):
    """§14 — saqlangan so'rovlarni RO'YXAT/GET qilish tashqi API'ga
    UMUMAN chiqmaydi; faqat aniq "Run" chaqirilganda provider so'raladi."""

    def setUp(self):
        self.store = new_store()
        self.cur = FakeSavedQueriesCursor(self.store)
        self.query_id = saved_queries.create(self.cur, 1, 'X', QUERY)

    def test_list_all_does_not_call_any_provider(self):
        with mock.patch.object(openalex, 'search') as oa, \
             mock.patch.object(crossref, 'search') as cr:
            saved_queries.list_all(self.cur)
        oa.assert_not_called()
        cr.assert_not_called()

    def test_get_does_not_call_any_provider(self):
        with mock.patch.object(openalex, 'search') as oa, \
             mock.patch.object(crossref, 'search') as cr:
            saved_queries.get(self.cur, self.query_id)
        oa.assert_not_called()
        cr.assert_not_called()

    def test_running_saved_query_does_call_selected_providers(self):
        """Kontrast: `to_query()` + `run_multi_provider_search()` orqali
        AYNAN ishga tushirishning o'zi provayder(lar)ni chaqiradi.

        `searchers=` orqali INJECT qilinadi (`services/article_metadata/
        service.py` testlaridagi `fetchers=` bilan bir xil naqsh) —
        `DEFAULT_SEARCHERS` dict funksiya obyektlarini import vaqtida
        BOG'LAB oladi, shuning uchun `mock.patch.object(openalex, 'search')`
        keyingi chaqiruvda ko'rinmaydi; eksplitsit inъektsiya haqiqiy va
        ishonchli usul."""
        from services.article_metadata.models import CROSSREF, OPENALEX
        row = saved_queries.get(self.cur, self.query_id)
        query = saved_queries.to_query(row)
        oa = mock.Mock(return_value=_empty_result())
        cr = mock.Mock(return_value=_empty_result())
        discovery_service.run_multi_provider_search(
            self.cur, query, config=CONFIG, searchers={OPENALEX: oa, CROSSREF: cr})
        oa.assert_called_once()
        cr.assert_called_once()


def _empty_result():
    from services.article_discovery.models import DiscoverySearchResult
    from services.article_metadata.models import OK
    return DiscoverySearchResult(status=OK, candidates=[], total_count=0)


if __name__ == '__main__':
    unittest.main()
