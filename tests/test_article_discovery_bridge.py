# -*- coding: utf-8 -*-
"""Discovery -> import navbati ko'prigi testlari (Phase 2D.1, §41-§42).

`tests/fake_article_discovery_pg.py:FakeDiscoveryCursor` — haqiqatan
filtrlaydigan qalbaki DB (ON CONFLICT DO NOTHING'ni simulyatsiya qiladi).
"""
import unittest
from datetime import date

from services.article_discovery import bridge
from services.article_discovery.models import (ALREADY_QUEUED,
                                                DUPLICATE_IN_RESULT,
                                                EXISTING_ARTICLE, NEW,
                                                ArticleDiscoveryQuery,
                                                DiscoveryCandidate)
from tests.fake_article_discovery_pg import FakeDiscoveryCursor, new_store

QUERY = ArticleDiscoveryQuery(keyword='oncology', from_date=date(2024, 1, 1),
                              to_date=date(2024, 1, 31), oak_field_id=3, limit=50)


def _candidate(doi, classification=NEW, external='W1'):
    return DiscoveryCandidate(doi=doi, external_source_id=external,
                              classification=classification)


class EnqueueTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store()
        self.cur = FakeDiscoveryCursor(self.store)

    def test_new_doi_creates_one_import_item(self):
        result = bridge.enqueue_discovery_candidates(
            self.cur, admin_id=1, query=QUERY,
            candidates=[_candidate('10.1000/a')],
            total_count=1, excluded_no_doi_count=0)
        self.assertEqual(len(result['queued']), 1)
        self.assertEqual(len(self.store['items']), 1)
        item = self.store['items'][0]
        self.assertEqual(item['normalized_doi'], '10.1000/a')
        self.assertEqual(item['status'], 'pending')
        self.assertEqual(item['external_source_id'], 'W1')

    def test_only_new_classification_is_enqueued(self):
        candidates = [
            _candidate('10.1000/a', classification=NEW),
            _candidate('10.1000/b', classification=EXISTING_ARTICLE),
            _candidate('10.1000/c', classification=ALREADY_QUEUED),
            _candidate('10.1000/d', classification=DUPLICATE_IN_RESULT),
        ]
        result = bridge.enqueue_discovery_candidates(
            self.cur, admin_id=1, query=QUERY, candidates=candidates,
            total_count=4, excluded_no_doi_count=0)
        self.assertEqual(len(result['queued']), 1)
        self.assertEqual(len(self.store['items']), 1)
        self.assertEqual(self.store['items'][0]['normalized_doi'], '10.1000/a')

    def test_existing_active_item_in_another_batch_is_not_duplicated(self):
        # Boshqa batch'da AYNAN shu DOI allaqachon `pending` bo'lsin.
        self.store['items'].append({
            'id': 999, 'batch_id': 555, 'row_number': 1, 'raw_input': '10.1000/a',
            'normalized_doi': '10.1000/a', 'status': 'pending',
            'is_retracted': False, 'attempt_count': 0, 'conflicts': None,
            'warnings': None,
        })
        result = bridge.enqueue_discovery_candidates(
            self.cur, admin_id=1, query=QUERY,
            candidates=[_candidate('10.1000/a')],
            total_count=1, excluded_no_doi_count=0)
        self.assertEqual(result['queued'], [])
        self.assertEqual(result['conflicted'], ['10.1000/a'])
        # Faqat AVVALGI (555-batch) item mavjud — yangi qator qo'shilmagan.
        self.assertEqual(len(self.store['items']), 1)

    def test_same_token_twice_no_duplicate_doi(self):
        """§41 — bir xil nomzod ro'yxati IKKI marta enqueue qilinsa (masalan
        double-click simulyatsiyasi — ikkita alohida chaqiruv), faqat BITTASI
        haqiqiy `pending` element yaratadi."""
        candidates = [_candidate('10.1000/a')]
        first = bridge.enqueue_discovery_candidates(
            self.cur, admin_id=1, query=QUERY, candidates=candidates,
            total_count=1, excluded_no_doi_count=0)
        second = bridge.enqueue_discovery_candidates(
            self.cur, admin_id=1, query=QUERY, candidates=candidates,
            total_count=1, excluded_no_doi_count=0)
        self.assertEqual(len(first['queued']), 1)
        self.assertEqual(len(second['queued']), 0)
        self.assertEqual(second['conflicted'], ['10.1000/a'])
        active = [r for r in self.store['items'] if r['normalized_doi'] == '10.1000/a']
        self.assertEqual(len(active), 1)

    def test_provenance_recorded_on_batch(self):
        bridge.enqueue_discovery_candidates(
            self.cur, admin_id=1, query=QUERY, candidates=[_candidate('10.1000/a')],
            total_count=42, excluded_no_doi_count=5)
        batch = self.store['batches'][0]
        self.assertEqual(batch['source_type'], 'discovery')
        self.assertEqual(batch['source_provider'], 'openalex')
        self.assertEqual(batch['source_oak_field_id'], 3)
        self.assertEqual(batch['source_result_count'], 42)
        self.assertEqual(batch['excluded_no_doi_count'], 5)
        self.assertEqual(batch['source_query']['keyword'], 'oncology')

    def test_provenance_query_has_no_secret_fields(self):
        bridge.enqueue_discovery_candidates(
            self.cur, admin_id=1, query=QUERY, candidates=[_candidate('10.1000/a')],
            total_count=1, excluded_no_doi_count=0)
        query_json = self.store['batches'][0]['source_query']
        for forbidden in ('api_key', 'mailto', 'url'):
            self.assertNotIn(forbidden, query_json)

    def test_external_source_id_only_set_by_discovery(self):
        bridge.enqueue_discovery_candidates(
            self.cur, admin_id=1, query=QUERY,
            candidates=[_candidate('10.1000/a', external='W99')],
            total_count=1, excluded_no_doi_count=0)
        self.assertEqual(self.store['items'][0]['external_source_id'], 'W99')

    def test_batch_counts_recomputed_after_enqueue(self):
        bridge.enqueue_discovery_candidates(
            self.cur, admin_id=1, query=QUERY, candidates=[_candidate('10.1000/a')],
            total_count=1, excluded_no_doi_count=0)
        batch = self.store['batches'][0]
        self.assertEqual(batch['total_items'], 1)
        self.assertEqual(batch['pending_count'], 1)

    def test_no_new_candidates_creates_empty_batch_no_items(self):
        result = bridge.enqueue_discovery_candidates(
            self.cur, admin_id=1, query=QUERY,
            candidates=[_candidate('10.1000/a', classification=EXISTING_ARTICLE)],
            total_count=1, excluded_no_doi_count=0)
        self.assertEqual(result['queued'], [])
        self.assertEqual(len(self.store['items']), 0)


if __name__ == '__main__':
    unittest.main()
