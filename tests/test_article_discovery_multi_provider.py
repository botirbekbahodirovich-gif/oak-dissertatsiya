# -*- coding: utf-8 -*-
"""Ko'p-provayderli discovery orkestratsiyasi testlari (Phase 2D.2, §7, §13, §23).

`services.article_discovery.service.run_multi_provider_search` — OpenAlex va
Crossref natijalarini birlashtiradi, DOI bo'yicha deterministik dedup
qiladi, provider provenance saqlaydi va bitta provayder muvaffaqiyatsiz
bo'lsa ikkinchisining natijasini yo'qotmaydi.

Haqiqiy tarmoq YO'Q — `searchers=` orqali sinov stub'lari inject qilinadi
(`services/article_metadata/service.py` testlaridagi `fetchers=` bilan bir
xil naqsh — `DEFAULT_SEARCHERS` funksiya obyektlarini import vaqtida bog'lab
oladi, shuning uchun module-level monkeypatch ishonchsiz).
"""
import unittest
from datetime import date

from services.article_discovery import bridge
from services.article_discovery.config import ArticleDiscoveryConfig
from services.article_discovery.models import (ALREADY_QUEUED, DUPLICATE_IN_RESULT,
                                                EXISTING_ARTICLE, MULTI_PROVIDER, NEW,
                                                ArticleDiscoveryQuery,
                                                DiscoveryCandidate,
                                                DiscoverySearchResult)
from services.article_discovery import service as discovery_service
from services.article_metadata.models import (CROSSREF, NOT_CONFIGURED, OK,
                                              OPENALEX, TIMEOUT, UNAVAILABLE)
from tests.fake_article_discovery_pg import FakeDiscoveryCursor, new_store

CONFIG = ArticleDiscoveryConfig(openalex_mailto='t@example.com',
                                crossref_mailto='t@example.com')
BOTH_QUERY = ArticleDiscoveryQuery(keyword='oncology', from_date=date(2024, 1, 1),
                                   to_date=date(2024, 1, 31), oak_field_id=None,
                                   limit=50, providers=(OPENALEX, CROSSREF))


def _candidate(doi, provider, **overrides):
    fields = dict(doi=doi, title='T-%s' % provider, source_name='J-%s' % provider,
                 providers=[provider])
    fields.update(overrides)
    return DiscoveryCandidate(**fields)


def _result(candidates=None, total=None, status=OK, no_doi=0):
    candidates = candidates or []
    return DiscoverySearchResult(status=status, candidates=candidates,
                                 total_count=total if total is not None else len(candidates),
                                 excluded_no_doi_count=no_doi)


class SingleProviderDelegationTests(unittest.TestCase):
    """Bitta provayder tanlanganda `run_discovery_search()`ga ANIQ
    delegatsiya qiladi — Phase 2D.1 mock-patch testlari (§26 regressiya)
    o'zgarishsiz ishlashi kerak."""

    def setUp(self):
        self.store = new_store()
        self.cur = FakeDiscoveryCursor(self.store)

    def test_single_provider_uses_run_discovery_search_path(self):
        query = ArticleDiscoveryQuery(keyword='oncology', from_date=date(2024, 1, 1),
                                      to_date=date(2024, 1, 31), oak_field_id=None,
                                      limit=50, providers=(OPENALEX,))
        called = {}

        def stub(q, c):
            called['hit'] = True
            return _result([_candidate('10.1000/a', OPENALEX)])

        preview = discovery_service.run_multi_provider_search(
            self.cur, query, config=CONFIG, searchers={OPENALEX: stub, CROSSREF: stub})
        self.assertTrue(called.get('hit'))
        self.assertEqual(preview.status, OK)
        self.assertEqual(preview.candidates[0].classification, NEW)


class DedupAndProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store()
        self.cur = FakeDiscoveryCursor(self.store)

    def _run(self, oa_candidates, cr_candidates, oa_total=None, cr_total=None):
        searchers = {
            OPENALEX: lambda q, c: _result(oa_candidates, total=oa_total),
            CROSSREF: lambda q, c: _result(cr_candidates, total=cr_total),
        }
        return discovery_service.run_multi_provider_search(
            self.cur, BOTH_QUERY, config=CONFIG, searchers=searchers)

    def test_same_doi_both_providers_yields_one_candidate(self):
        preview = self._run([_candidate('10.1000/a', OPENALEX)],
                            [_candidate('10.1000/a', CROSSREF)])
        self.assertEqual(len(preview.candidates), 2)  # both rows kept (Takror ko'rinadi)
        classifications = sorted(c.classification for c in preview.candidates)
        self.assertEqual(classifications, [DUPLICATE_IN_RESULT, NEW])
        self.assertEqual(preview.summary()['new_count'], 1)

    def test_provider_provenance_preserved_on_merged_candidate(self):
        preview = self._run([_candidate('10.1000/a', OPENALEX)],
                            [_candidate('10.1000/a', CROSSREF)])
        primary = next(c for c in preview.candidates if c.classification == NEW)
        self.assertEqual(sorted(primary.providers), [CROSSREF, OPENALEX])

    def test_openalex_display_fields_take_precedence_when_both_present(self):
        oa = _candidate('10.1000/a', OPENALEX, title='OpenAlex Title')
        cr = _candidate('10.1000/a', CROSSREF, title='Crossref Title')
        preview = self._run([oa], [cr])
        primary = next(c for c in preview.candidates if c.classification == NEW)
        self.assertEqual(primary.title, 'OpenAlex Title')

    def test_crossref_fills_gap_when_openalex_field_missing(self):
        oa = _candidate('10.1000/a', OPENALEX, title=None)
        cr = _candidate('10.1000/a', CROSSREF, title='Crossref Title')
        preview = self._run([oa], [cr])
        primary = next(c for c in preview.candidates if c.classification == NEW)
        self.assertEqual(primary.title, 'Crossref Title')

    def test_retraction_flag_is_or_merged(self):
        oa = _candidate('10.1000/a', OPENALEX, is_retracted=False)
        cr = _candidate('10.1000/a', CROSSREF, is_retracted=True)
        preview = self._run([oa], [cr])
        primary = next(c for c in preview.candidates if c.classification == NEW)
        self.assertTrue(primary.is_retracted)

    def test_distinct_dois_both_kept_as_new(self):
        preview = self._run([_candidate('10.1000/a', OPENALEX)],
                            [_candidate('10.1000/b', CROSSREF)])
        self.assertEqual(len(preview.candidates), 2)
        self.assertTrue(all(c.classification == NEW for c in preview.candidates))

    def test_candidate_counts_do_not_double_count_same_doi(self):
        preview = self._run(
            [_candidate('10.1000/a', OPENALEX), _candidate('10.1000/b', OPENALEX)],
            [_candidate('10.1000/a', CROSSREF), _candidate('10.1000/c', CROSSREF)])
        summary = preview.summary()
        # 3 haqiqiy noyob DOI (a, b, c) -> 3 ta yangi, dublikat sifatida
        # faqat ikkinchi 'a' uchrashuvi hisoblanadi.
        self.assertEqual(summary['new_count'], 3)
        self.assertEqual(summary['duplicate_in_result_count'], 1)

    def test_existing_article_duplicate_classified_across_providers(self):
        self.store['articles'].append(
            {'id': 9, 'title': 'X', 'slug': 'x-slug', 'doi': '10.1000/a'})
        preview = self._run([_candidate('10.1000/a', OPENALEX)],
                            [_candidate('10.1000/b', CROSSREF)])
        existing = next(c for c in preview.candidates if c.doi == '10.1000/a')
        self.assertEqual(existing.classification, EXISTING_ARTICLE)
        self.assertEqual(existing.existing_article_id, 9)

    def test_active_queue_duplicate_classified_across_providers(self):
        self.store['items'].append({
            'id': 1, 'batch_id': 1, 'row_number': 1, 'raw_input': '10.1000/a',
            'normalized_doi': '10.1000/a', 'status': 'pending',
            'is_retracted': False, 'attempt_count': 0, 'conflicts': None,
            'warnings': None,
        })
        preview = self._run([_candidate('10.1000/a', OPENALEX)],
                            [_candidate('10.1000/b', CROSSREF)])
        queued = next(c for c in preview.candidates if c.doi == '10.1000/a')
        self.assertEqual(queued.classification, ALREADY_QUEUED)

    def test_deterministic_order_openalex_before_crossref_regardless_of_dict_order(self):
        """§7 — tartib concurrent HTTP tugash tartibidan MUSTAQIL bo'lishi
        kerak; `SUPPORTED_PROVIDERS` tartibi (OpenAlex birinchi) — bu yerda
        Crossref'ni "birinchi" stub sifatida berib ham natija bir xil."""
        searchers = {
            CROSSREF: lambda q, c: _result([_candidate('10.1000/a', CROSSREF, title='CR')]),
            OPENALEX: lambda q, c: _result([_candidate('10.1000/a', OPENALEX, title='OA')]),
        }
        preview = discovery_service.run_multi_provider_search(
            self.cur, BOTH_QUERY, config=CONFIG, searchers=searchers)
        primary = next(c for c in preview.candidates if c.classification == NEW)
        self.assertEqual(primary.title, 'OA')


class PartialFailureTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store()
        self.cur = FakeDiscoveryCursor(self.store)

    def test_one_provider_fails_other_results_preserved(self):
        searchers = {
            OPENALEX: lambda q, c: _result([_candidate('10.1000/a', OPENALEX)]),
            CROSSREF: lambda q, c: _result(status=UNAVAILABLE),
        }
        preview = discovery_service.run_multi_provider_search(
            self.cur, BOTH_QUERY, config=CONFIG, searchers=searchers)
        self.assertEqual(preview.status, OK)
        self.assertEqual(len(preview.candidates), 1)
        self.assertEqual(preview.candidates[0].doi, '10.1000/a')

    def test_provider_status_reflects_failure_and_success(self):
        searchers = {
            OPENALEX: lambda q, c: _result([_candidate('10.1000/a', OPENALEX)]),
            CROSSREF: lambda q, c: _result(status=TIMEOUT),
        }
        preview = discovery_service.run_multi_provider_search(
            self.cur, BOTH_QUERY, config=CONFIG, searchers=searchers)
        self.assertEqual(preview.provider_status[OPENALEX]['status'], OK)
        self.assertEqual(preview.provider_status[OPENALEX]['count'], 1)
        self.assertEqual(preview.provider_status[CROSSREF]['status'], TIMEOUT)

    def test_both_providers_fail_returns_clear_failure(self):
        searchers = {
            OPENALEX: lambda q, c: _result(status=UNAVAILABLE),
            CROSSREF: lambda q, c: _result(status=TIMEOUT),
        }
        preview = discovery_service.run_multi_provider_search(
            self.cur, BOTH_QUERY, config=CONFIG, searchers=searchers)
        self.assertNotEqual(preview.status, OK)
        self.assertEqual(preview.candidates, [])

    def test_both_not_configured_yields_not_configured(self):
        searchers = {
            OPENALEX: lambda q, c: _result(status=NOT_CONFIGURED),
            CROSSREF: lambda q, c: _result(status=NOT_CONFIGURED),
        }
        preview = discovery_service.run_multi_provider_search(
            self.cur, BOTH_QUERY, config=CONFIG, searchers=searchers)
        self.assertEqual(preview.status, NOT_CONFIGURED)

    def test_openalex_not_configured_but_crossref_ok_is_partial_success(self):
        """Crossref hech qachon `not_configured` qaytarmaydi (§4) — shuning
        uchun bu holat "hammasi muvaffaqiyatsiz" EMAS, oddiy qisman
        muvaffaqiyat."""
        searchers = {
            OPENALEX: lambda q, c: _result(status=NOT_CONFIGURED),
            CROSSREF: lambda q, c: _result([_candidate('10.1000/a', CROSSREF)]),
        }
        preview = discovery_service.run_multi_provider_search(
            self.cur, BOTH_QUERY, config=CONFIG, searchers=searchers)
        self.assertEqual(preview.status, OK)
        self.assertEqual(len(preview.candidates), 1)

    def test_unexpected_exception_from_one_provider_does_not_crash_search(self):
        def boom(q, c):
            raise RuntimeError('kutilmagan xato')

        searchers = {
            OPENALEX: lambda q, c: _result([_candidate('10.1000/a', OPENALEX)]),
            CROSSREF: boom,
        }
        preview = discovery_service.run_multi_provider_search(
            self.cur, BOTH_QUERY, config=CONFIG, searchers=searchers)
        self.assertEqual(preview.status, OK)
        self.assertEqual(len(preview.candidates), 1)
        self.assertEqual(preview.provider_status[CROSSREF]['status'], UNAVAILABLE)


class MultiProviderBridgeProvenanceTests(unittest.TestCase):
    """`source_provider` — bitta provayder tanlansa uning nomi, ikkitasi
    tanlansa 'multi' (§16)."""

    def setUp(self):
        self.store = new_store()
        self.cur = FakeDiscoveryCursor(self.store)

    def _candidate(self, doi):
        return DiscoveryCandidate(doi=doi, classification=NEW, providers=[OPENALEX, CROSSREF])

    def test_both_providers_selected_records_multi(self):
        bridge.enqueue_discovery_candidates(
            self.cur, admin_id=1, query=BOTH_QUERY, candidates=[self._candidate('10.1000/a')],
            total_count=1, excluded_no_doi_count=0)
        self.assertEqual(self.store['batches'][0]['source_provider'], MULTI_PROVIDER)

    def test_crossref_only_selected_records_crossref(self):
        query = ArticleDiscoveryQuery(keyword='x', from_date=date(2024, 1, 1),
                                      to_date=date(2024, 1, 2), oak_field_id=None,
                                      limit=10, providers=(CROSSREF,))
        bridge.enqueue_discovery_candidates(
            self.cur, admin_id=1, query=query, candidates=[self._candidate('10.1000/a')],
            total_count=1, excluded_no_doi_count=0)
        self.assertEqual(self.store['batches'][0]['source_provider'], CROSSREF)

    def test_openalex_only_selected_records_openalex_unchanged(self):
        """2D.1 davomiyligi — default `providers=('openalex',)`."""
        query = ArticleDiscoveryQuery(keyword='x', from_date=date(2024, 1, 1),
                                      to_date=date(2024, 1, 2), oak_field_id=None,
                                      limit=10)
        bridge.enqueue_discovery_candidates(
            self.cur, admin_id=1, query=query, candidates=[self._candidate('10.1000/a')],
            total_count=1, excluded_no_doi_count=0)
        self.assertEqual(self.store['batches'][0]['source_provider'], OPENALEX)

    def test_query_canonical_includes_providers_list(self):
        canonical = BOTH_QUERY.canonical()
        self.assertEqual(canonical['providers'], [OPENALEX, CROSSREF])

    def test_candidate_serialization_roundtrip_preserves_providers(self):
        candidate = self._candidate('10.1000/a')
        restored = DiscoveryCandidate.from_dict(candidate.to_dict())
        self.assertEqual(restored.providers, [OPENALEX, CROSSREF])


if __name__ == '__main__':
    unittest.main()
