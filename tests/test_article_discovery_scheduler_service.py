# -*- coding: utf-8 -*-
"""`services.article_discovery.service.run_saved_query()` testlari
(Phase 2D.3, §7, §10, §11, §20).

Haqiqiy tarmoq YO'Q — `searchers=` orqali stub'lar inject qilinadi
(`services/article_metadata/service.py` testlaridagi `fetchers=` bilan bir
xil naqsh — `DEFAULT_SEARCHERS` funksiya obyektlarini import vaqtida bog'lab
oladi, shuning uchun module-level monkeypatch ishonchsiz, §Phase 2D.2 audit
natijasi).
"""
import unittest
from datetime import date
from unittest import mock

from services.article_discovery import saved_queries
from services.article_discovery.config import ArticleDiscoveryConfig
from services.article_discovery.models import (DiscoveryCandidate,
                                                DiscoverySearchResult)
from services.article_discovery.service import run_saved_query
from services.article_metadata.models import (CROSSREF, LOOKUP_NOT_FOUND,
                                              MetadataLookupResult, NOT_CONFIGURED, OK,
                                              OPENALEX, NormalizedArticleMetadata,
                                              TIMEOUT, UNAVAILABLE)
from services.article_import import persistence as import_persistence
from tests.fake_discovery_scheduler_pg import DiscoverySchedulerCursor, new_store
from services.article_discovery.models import ArticleDiscoveryQuery


def _fake_fetch(doi, *, cache_get=None, cache_set=None):
    """`process_import_batch()`ning haqiqiy tarmoqqa chiqishini oldini
    oladi — `run_saved_query()` enqueue qilingandan keyin AVTOMATIK shu
    funksiyani (yoki production'da haqiqiy `fetch_article_metadata`ni)
    chaqirib, importni oxirigacha oshiradi (§11 — process_import_batch
    drayveri)."""
    return MetadataLookupResult(doi=doi, status=LOOKUP_NOT_FOUND,
                                metadata=NormalizedArticleMetadata(doi=doi))


CONFIG = ArticleDiscoveryConfig(openalex_mailto='t@example.com',
                                crossref_mailto='t@example.com')
BOTH_QUERY = ArticleDiscoveryQuery(keyword='oncology', from_date=date(2024, 1, 1),
                                   to_date=date(2024, 1, 31), oak_field_id=None,
                                   limit=50, providers=(OPENALEX, CROSSREF))
SINGLE_QUERY = ArticleDiscoveryQuery(keyword='oncology', from_date=date(2024, 1, 1),
                                     to_date=date(2024, 1, 31), oak_field_id=None,
                                     limit=50, providers=(OPENALEX,))


def _candidate(doi, provider):
    return DiscoveryCandidate(doi=doi, providers=[provider])


def _result(candidates=None, status=OK, total=None):
    candidates = candidates or []
    return DiscoverySearchResult(status=status, candidates=candidates,
                                 total_count=total if total is not None else len(candidates))


class RunSavedQueryTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store()
        self.cur = DiscoverySchedulerCursor(self.store)
        self._patch_import_schema_flag()

    def _patch_import_schema_flag(self):
        # `services.article_import.persistence.ensure_schema` haqiqiy DB
        # DDL yubormasin — bridge/process_import_batch chaqiruvlari fake
        # kursur ustida ishlaydi, ensure_schema esa faqat flag tekshiradi.
        patcher = mock.patch.object(import_persistence, '_schema_ready', True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _saved_query_row(self, query=SINGLE_QUERY, created_by=1):
        query_id = saved_queries.create(self.cur, created_by, 'Test Q', query)
        return saved_queries.get(self.cur, query_id)

    def test_success_no_new_candidates(self):
        row = self._saved_query_row()
        outcome = run_saved_query(
            self.cur, row, trigger_type='scheduled', config=CONFIG, fetch=_fake_fetch,
            searchers={OPENALEX: lambda q, c: _result([])})
        self.assertEqual(outcome.status, 'success')
        self.assertEqual(outcome.new_count, 0)
        self.assertEqual(outcome.queued_count, 0)
        self.assertIsNone(outcome.import_batch_id)

    def test_new_candidates_are_queued(self):
        row = self._saved_query_row()
        outcome = run_saved_query(
            self.cur, row, trigger_type='scheduled', config=CONFIG, fetch=_fake_fetch,
            searchers={OPENALEX: lambda q, c: _result(
                [_candidate('10.1000/a', OPENALEX)])})
        self.assertEqual(outcome.status, 'success')
        self.assertEqual(outcome.new_count, 1)
        self.assertEqual(outcome.queued_count, 1)
        self.assertIsNotNone(outcome.import_batch_id)
        self.assertEqual(len(self.store['items']), 1)
        self.assertEqual(self.store['items'][0]['normalized_doi'], '10.1000/a')
        # `run_saved_query()` ITEM'NI 'pending'da QOLDIRMAYDI — u
        # `process_import_batch()`ni oxirigacha ishlatadi (§11), shuning
        # uchun `_fake_fetch` (LOOKUP_NOT_FOUND) bilan yakuniy holat
        # 'metadata_not_found' (mavjud state mashinasi, hech qanday
        # avtomatik approve/publish EMAS — pastdagi
        # test_never_auto_approves_or_publishes shuni tekshiradi).
        self.assertEqual(self.store['items'][0]['status'], 'metadata_not_found')

    def test_existing_article_doi_not_queued(self):
        self.store['articles'].append(
            {'id': 9, 'title': 'X', 'slug': 'x', 'doi': '10.1000/a'})
        row = self._saved_query_row()
        outcome = run_saved_query(
            self.cur, row, trigger_type='scheduled', config=CONFIG, fetch=_fake_fetch,
            searchers={OPENALEX: lambda q, c: _result(
                [_candidate('10.1000/a', OPENALEX)])})
        self.assertEqual(outcome.new_count, 0)
        self.assertEqual(outcome.queued_count, 0)
        self.assertEqual(len(self.store['items']), 0)

    def test_active_import_duplicate_not_queued(self):
        self.store['items'].append({
            'id': 1, 'batch_id': 1, 'row_number': 1, 'raw_input': '10.1000/a',
            'normalized_doi': '10.1000/a', 'status': 'pending',
            'is_retracted': False, 'attempt_count': 0, 'conflicts': None,
            'warnings': None,
        })
        row = self._saved_query_row()
        outcome = run_saved_query(
            self.cur, row, trigger_type='scheduled', config=CONFIG, fetch=_fake_fetch,
            searchers={OPENALEX: lambda q, c: _result(
                [_candidate('10.1000/a', OPENALEX)])})
        self.assertEqual(outcome.new_count, 0)
        self.assertEqual(outcome.queued_count, 0)
        # Faqat asl (oldindan mavjud) element qoladi — yangisi qo'shilmagan.
        self.assertEqual(len(self.store['items']), 1)

    def test_no_doi_candidates_counted_not_queued(self):
        row = self._saved_query_row()
        outcome = run_saved_query(
            self.cur, row, trigger_type='scheduled', config=CONFIG, fetch=_fake_fetch,
            searchers={OPENALEX: lambda q, c: DiscoverySearchResult(
                status=OK, candidates=[], total_count=0, excluded_no_doi_count=4)})
        self.assertEqual(outcome.no_doi_count, 4)
        self.assertEqual(outcome.queued_count, 0)

    def test_both_providers_success(self):
        row = self._saved_query_row(query=BOTH_QUERY)
        outcome = run_saved_query(
            self.cur, row, trigger_type='scheduled', config=CONFIG, fetch=_fake_fetch,
            searchers={OPENALEX: lambda q, c: _result([_candidate('10.1000/a', OPENALEX)]),
                      CROSSREF: lambda q, c: _result([_candidate('10.1000/b', CROSSREF)])})
        self.assertEqual(outcome.status, 'success')
        self.assertEqual(outcome.new_count, 2)
        self.assertEqual(outcome.queued_count, 2)

    def test_one_provider_failure_is_partial(self):
        row = self._saved_query_row(query=BOTH_QUERY)
        outcome = run_saved_query(
            self.cur, row, trigger_type='scheduled', config=CONFIG, fetch=_fake_fetch,
            searchers={OPENALEX: lambda q, c: _result([_candidate('10.1000/a', OPENALEX)]),
                      CROSSREF: lambda q, c: _result(status=UNAVAILABLE)})
        self.assertEqual(outcome.status, 'partial')
        self.assertIn('crossref', outcome.error_summary)
        # OpenAlex natijasi baribir navbatga qo'shilgan.
        self.assertEqual(outcome.new_count, 1)

    def test_all_providers_fail_is_failed(self):
        row = self._saved_query_row(query=BOTH_QUERY)
        outcome = run_saved_query(
            self.cur, row, trigger_type='scheduled', config=CONFIG, fetch=_fake_fetch,
            searchers={OPENALEX: lambda q, c: _result(status=UNAVAILABLE),
                      CROSSREF: lambda q, c: _result(status=TIMEOUT)})
        self.assertEqual(outcome.status, 'failed')
        self.assertEqual(outcome.queued_count, 0)

    def test_not_configured_is_failed_with_message(self):
        row = self._saved_query_row()
        outcome = run_saved_query(
            self.cur, row, trigger_type='scheduled', config=CONFIG, fetch=_fake_fetch,
            searchers={OPENALEX: lambda q, c: _result(status=NOT_CONFIGURED)})
        self.assertEqual(outcome.status, 'failed')
        self.assertIsNotNone(outcome.error_summary)

    def test_provenance_marks_trigger_type_and_saved_query(self):
        row = self._saved_query_row()
        run_saved_query(
            self.cur, row, trigger_type='scheduled', config=CONFIG, fetch=_fake_fetch, run_id=77,
            searchers={OPENALEX: lambda q, c: _result(
                [_candidate('10.1000/a', OPENALEX)])})
        batch = self.store['batches'][0]
        scheduled_meta = batch['source_query']['scheduled']
        self.assertEqual(scheduled_meta['trigger_type'], 'scheduled')
        self.assertEqual(scheduled_meta['saved_query_id'], row['id'])
        self.assertEqual(scheduled_meta['saved_query_name'], row['name'])
        self.assertEqual(scheduled_meta['run_id'], 77)

    def test_no_raw_provider_payload_or_secrets_in_provenance(self):
        row = self._saved_query_row()
        run_saved_query(
            self.cur, row, trigger_type='scheduled', config=CONFIG, fetch=_fake_fetch,
            searchers={OPENALEX: lambda q, c: _result(
                [_candidate('10.1000/a', OPENALEX)])})
        query_json = self.store['batches'][0]['source_query']
        for forbidden in ('api_key', 'mailto', 'url', 'raw'):
            self.assertNotIn(forbidden, query_json)

    def test_never_auto_approves_or_publishes(self):
        """§10 — item statusi ENQUEUE'dan keyin ham `pending`/keyingi
        bosqichdan nariga o'tmaydi; hech qanday `scientific_articles`
        yozuvi/`approved` status yo'q."""
        row = self._saved_query_row()
        run_saved_query(
            self.cur, row, trigger_type='scheduled', config=CONFIG, fetch=_fake_fetch,
            searchers={OPENALEX: lambda q, c: _result(
                [_candidate('10.1000/a', OPENALEX)])})
        for item in self.store['items']:
            self.assertNotIn(item['status'], ('approved', 'published'))
        self.assertEqual(self.store.get('articles', []), [])

    def test_admin_id_is_saved_query_owner(self):
        row = self._saved_query_row(created_by=42)
        run_saved_query(
            self.cur, row, trigger_type='scheduled', config=CONFIG, fetch=_fake_fetch,
            searchers={OPENALEX: lambda q, c: _result(
                [_candidate('10.1000/a', OPENALEX)])})
        self.assertEqual(self.store['batches'][0]['created_by'], 42)


if __name__ == '__main__':
    unittest.main()
