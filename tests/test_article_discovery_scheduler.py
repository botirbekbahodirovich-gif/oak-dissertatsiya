# -*- coding: utf-8 -*-
"""`services.article_discovery.scheduler.run_due_discovery_queries()`
testlari (Phase 2D.3, §4, §5, §7, §19).
"""
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from services.article_discovery import saved_queries, scheduler
from services.article_discovery.models import (ArticleDiscoveryQuery,
                                                DiscoveryCandidate,
                                                DiscoverySearchResult)
from services.article_discovery.scheduler_config import DiscoverySchedulerConfig
from services.article_discovery.scheduler_state import (FREQUENCY_DAILY, RUN_FAILED,
                                                         RUN_SKIPPED, RUN_SUCCESS)
from services.article_import import persistence as import_persistence
from services.article_metadata.models import (LOOKUP_NOT_FOUND, MetadataLookupResult,
                                              NormalizedArticleMetadata, OK, OPENALEX)
from tests.fake_discovery_scheduler_pg import (DiscoverySchedulerConnection,
                                               DiscoverySchedulerCursor, new_store)

CONFIG = DiscoverySchedulerConfig(enabled=True, interval_minutes=15, max_queries=10)
QUERY = ArticleDiscoveryQuery(keyword='oncology', from_date=date(2024, 1, 1),
                              to_date=date(2024, 1, 31), oak_field_id=None,
                              limit=10, providers=(OPENALEX,))


def _fake_fetch(doi, *, cache_get=None, cache_set=None):
    return MetadataLookupResult(doi=doi, status=LOOKUP_NOT_FOUND,
                                metadata=NormalizedArticleMetadata(doi=doi))


class SchedulerCycleTestCase(unittest.TestCase):
    def setUp(self):
        self.store = new_store()
        patcher = mock.patch.object(import_persistence, '_schema_ready', True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _conn(self):
        return DiscoverySchedulerConnection(self.store)

    def _make_due_query(self, name, *, offset_minutes=-5, providers=(OPENALEX,)):
        conn = self._conn()
        with conn.cursor() as cur:
            query = ArticleDiscoveryQuery(
                keyword='oncology', from_date=date(2024, 1, 1),
                to_date=date(2024, 1, 31), oak_field_id=None, limit=10,
                providers=providers)
            qid = saved_queries.create(cur, 1, name, query)
            from services.article_discovery import scheduler_persistence
            scheduler_persistence.set_schedule(cur, qid, enabled=True,
                                               frequency=FREQUENCY_DAILY)
            row = next(r for r in self.store['saved_queries'] if r['id'] == qid)
            row['next_run_at'] = datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)
        return qid


class DryRunTests(SchedulerCycleTestCase):
    def test_dry_run_lists_due_without_writing(self):
        self._make_due_query('Due one')
        conn = self._conn()
        result = scheduler.run_due_discovery_queries(conn, config=CONFIG, dry_run=True)
        self.assertTrue(result.dry_run)
        self.assertEqual(len(result.due), 1)
        self.assertEqual(self.store['runs'], [])

    def test_dry_run_does_not_touch_next_run_at(self):
        qid = self._make_due_query('Due one')
        conn = self._conn()
        scheduler.run_due_discovery_queries(conn, config=CONFIG, dry_run=True)
        row = next(r for r in self.store['saved_queries'] if r['id'] == qid)
        self.assertLess(row['next_run_at'], datetime.now(timezone.utc))


class ExecutionTests(SchedulerCycleTestCase):
    def _searchers(self, doi='10.1000/a'):
        return {OPENALEX: lambda q, c: DiscoverySearchResult(
            status=OK, candidates=[DiscoveryCandidate(doi=doi, providers=[OPENALEX])],
            total_count=1)}

    def test_due_query_is_executed_and_recorded(self):
        qid = self._make_due_query('Due one')
        conn = self._conn()
        result = scheduler.run_due_discovery_queries(
            conn, config=CONFIG, searchers=self._searchers(), fetch=_fake_fetch)
        self.assertFalse(result.dry_run)
        self.assertEqual(result.claimed, 1)
        self.assertEqual(len(result.results), 1)
        self.assertEqual(result.results[0].saved_query_id, qid)
        self.assertEqual(result.results[0].status, RUN_SUCCESS)
        self.assertEqual(len(self.store['runs']), 1)
        self.assertEqual(self.store['runs'][0]['trigger_type'], 'scheduled')

    def test_next_run_at_moved_into_future_after_execution(self):
        qid = self._make_due_query('Due one')
        conn = self._conn()
        scheduler.run_due_discovery_queries(
            conn, config=CONFIG, searchers=self._searchers(), fetch=_fake_fetch)
        row = next(r for r in self.store['saved_queries'] if r['id'] == qid)
        self.assertGreater(row['next_run_at'], datetime.now(timezone.utc))

    def test_no_due_queries_is_a_noop(self):
        conn = self._conn()
        result = scheduler.run_due_discovery_queries(
            conn, config=CONFIG, searchers=self._searchers(), fetch=_fake_fetch)
        self.assertEqual(result.claimed, 0)
        self.assertEqual(result.results, [])

    def test_max_queries_bounds_a_single_cycle(self):
        for i in range(5):
            self._make_due_query('Due-%d' % i)
        conn = self._conn()
        limited = DiscoverySchedulerConfig(enabled=True, interval_minutes=15,
                                           max_queries=2)
        result = scheduler.run_due_discovery_queries(
            conn, config=limited, searchers=self._searchers(), fetch=_fake_fetch)
        self.assertEqual(result.claimed, 2)


class FailureIsolationTests(SchedulerCycleTestCase):
    def test_one_failing_query_does_not_stop_the_next(self):
        """§7 — bitta due-so'rov ichki xato bersa ham, navbatdagilar
        ishlanadi. `run_multi_provider_search` bitta provayder tanlanganda
        `search(query, config)`ni chaqiradi — biz `query.keyword` orqali
        qaysi saqlangan so'rov ekanini bilolmaymiz, shuning uchun oddiy
        global sanoq bilan "ikkinchi chaqiruv portlaydi" qilib simulyatsiya
        qilamiz (band qilish tartibi — created ketma-ketligi bo'yicha)."""
        self._make_due_query('Good one')
        self._make_due_query('Bad one')
        calls = {'n': 0}

        def flaky_search(q, c):
            calls['n'] += 1
            if calls['n'] == 2:
                raise RuntimeError('kutilmagan')
            return DiscoverySearchResult(
                status=OK, candidates=[DiscoveryCandidate(
                    doi='10.1000/ok-%d' % calls['n'], providers=[OPENALEX])],
                total_count=1)

        conn = self._conn()
        result = scheduler.run_due_discovery_queries(
            conn, config=CONFIG, searchers={OPENALEX: flaky_search}, fetch=_fake_fetch)
        self.assertEqual(result.claimed, 2)
        statuses = {r.saved_query_id: r.status for r in result.results}
        # Bitta muvaffaqiyatli, bitta muvaffaqiyatsiz — lekin IKKALASI HAM
        # ISHLANGAN (ikkinchisi birinchisi portlagani uchun to'xtamagan).
        self.assertEqual(set(statuses.values()), {RUN_SUCCESS, RUN_FAILED})

    def test_claim_failure_does_not_abort_whole_cycle(self):
        """Band qilish bosqichida kutilmagan xato — boshqa due so'rovlar
        baribir ishlanadi (himoya `_run_one_claimed_query` ichida)."""
        qid = self._make_due_query('Due one')
        conn = self._conn()
        with mock.patch.object(saved_queries, 'get', side_effect=[RuntimeError('x')]):
            # birinchi chaqiruv portlaydi -> FAILED status bilan qaytadi,
            # istisno tashqariga chiqmaydi.
            result = scheduler.run_due_discovery_queries(
                conn, config=CONFIG, searchers={}, fetch=_fake_fetch)
        self.assertEqual(len(result.results), 1)
        self.assertEqual(result.results[0].status, RUN_FAILED)


class AdvisoryLockTests(SchedulerCycleTestCase):
    def test_second_concurrent_cycle_raises_already_running(self):
        self._make_due_query('Due one')
        conn = self._conn()
        self.store['lock_held'] = True     # boshqa "worker" allaqachon ushlagan
        with self.assertRaises(scheduler.SchedulerAlreadyRunningError):
            scheduler.run_due_discovery_queries(conn, config=CONFIG, searchers={})

    def test_lock_released_after_cycle_even_on_error(self):
        self._make_due_query('Due one')
        conn = self._conn()

        def boom(q, c):
            raise RuntimeError('x')

        scheduler.run_due_discovery_queries(
            conn, config=CONFIG, searchers={OPENALEX: boom}, fetch=_fake_fetch)
        self.assertFalse(self.store['lock_held'])

    def test_dry_run_never_touches_the_lock(self):
        self._make_due_query('Due one')
        conn = self._conn()
        scheduler.run_due_discovery_queries(conn, config=CONFIG, dry_run=True)
        self.assertFalse(self.store['lock_held'])


class SkippedRaceTests(SchedulerCycleTestCase):
    def test_already_inactive_query_never_claimed(self):
        """`claim_due_queries` o'zi `is_active`ni tekshiradi — allaqachon
        faolsizlantirilgan so'rov claim bosqichida umuman tanlanmaydi."""
        qid = self._make_due_query('Due one')
        conn = self._conn()
        with conn.cursor() as cur:
            saved_queries.set_active(cur, qid, False)
        result = scheduler.run_due_discovery_queries(
            conn, config=CONFIG, searchers={}, fetch=_fake_fetch)
        self.assertEqual(result.claimed, 0)

    def test_deactivated_between_claim_and_fetch_is_skipped(self):
        """Haqiqiy poyga: band qilingandan KEYIN, lekin qatorni qayta
        o'qishdan OLDIN boshqa tranzaksiya so'rovni faolsizlantiradi —
        `_run_one_claimed_query` buni xato EMAS, 'skipped' deb belgilaydi
        (§7)."""
        qid = self._make_due_query('Due one')
        conn = self._conn()
        real_get = saved_queries.get

        def get_then_deactivate(cur, query_id):
            row = real_get(cur, query_id)
            if row is not None:
                row = dict(row)
                row['is_active'] = False
            return row

        with mock.patch.object(saved_queries, 'get', side_effect=get_then_deactivate):
            result = scheduler.run_due_discovery_queries(
                conn, config=CONFIG, searchers={}, fetch=_fake_fetch)
        self.assertEqual(result.claimed, 1)
        self.assertEqual(len(result.results), 1)
        self.assertEqual(result.results[0].status, RUN_SKIPPED)
        self.assertEqual(self.store['runs'], [])  # skipped -> run yozuvi YO'Q


if __name__ == '__main__':
    unittest.main()
