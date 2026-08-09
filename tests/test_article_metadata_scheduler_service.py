# -*- coding: utf-8 -*-
"""`services/article_sync/scheduler.py` — orkestratsiya testlari
(Phase 2C.3C): global lock, item-level isolation, run audit, dry-run,
actor semantikasi, avtomatik mutatsiya YO'QLIGI.
"""
import unittest

from services.article_sync.models import MetadataSyncResult
from services.article_sync.scheduler import (SchedulerAlreadyRunningError,
                                              run_scheduled_metadata_sync)
from services.article_sync.scheduler_config import SchedulerConfig
from services.article_sync.scheduler_state import (RUN_COMPLETED,
                                                    RUN_COMPLETED_WITH_ERRORS,
                                                    TRIGGER_MANUAL_CLI)
from services.article_sync.service import ArticleSyncError
from tests.fake_scheduler_pg import SchedulerFakeConnection
from tests.test_article_sync_service import base_article_row, make_fetcher

CONFIG = SchedulerConfig(enabled=True, interval_days=30, batch_size=50)


def _new_store_with_articles(article_ids):
    store = {'articles': {}, 'authors': {}, 'syncs': {}, 'next_sync_id': 1,
            'clock': 1, 'sql': []}
    for aid in article_ids:
        row = base_article_row(id=aid)
        store['articles'][aid] = row
    return store


def _select_due_stub(article_ids):
    def select_due(conn, config, *, article_id=None):
        return list(article_ids)
    return select_due


def _sync_result(article_id, provider_statuses):
    """`check_article_metadata()` MUVAFFAQIYATLI yakunlangan holatini
    simulyatsiya qiladi — `provider_summary` HAQIQIY shaklda
    (`ProviderResult.public_dict()`dan chiqqan oddiy dict, xom obyekt EMAS),
    xuddi real servis qaytargani kabi (`_classify()` shu shaklni kutadi)."""
    provider_summary = {name: {'status': status}
                        for name, status in provider_statuses.items()}
    return MetadataSyncResult(article_id=article_id, sync_id=article_id,
                              provider_summary=provider_summary)


class LockTests(unittest.TestCase):
    def test_second_concurrent_run_gets_already_running(self):
        store = _new_store_with_articles([1])
        conn = SchedulerFakeConnection(store)
        # Simulate a lock already held by another session/process.
        store['lock_held'] = True
        with self.assertRaises(SchedulerAlreadyRunningError):
            run_scheduled_metadata_sync(
                conn, config=CONFIG, select_due=_select_due_stub([1]),
                check=lambda *a, **k: self.fail('check() chaqirilmasligi kerak'))

    def test_lock_released_after_run_so_next_run_can_acquire(self):
        store = _new_store_with_articles([1])
        conn = SchedulerFakeConnection(store)

        def ok_check(cur, article_id, actor_id, *, actor_type, fetch=None):
            return _sync_result(article_id, {'crossref': 'ok'})

        result1 = run_scheduled_metadata_sync(
            conn, config=CONFIG, select_due=_select_due_stub([1]), check=ok_check)
        self.assertEqual(result1.status, RUN_COMPLETED)
        self.assertFalse(store['lock_held'])

        # Second run — lock must be free again.
        result2 = run_scheduled_metadata_sync(
            conn, config=CONFIG, select_due=_select_due_stub([1]), check=ok_check)
        self.assertEqual(result2.status, RUN_COMPLETED)


class ItemLevelIsolationTests(unittest.TestCase):
    def test_one_failure_does_not_stop_the_rest(self):
        article_ids = list(range(1, 11))
        store = _new_store_with_articles(article_ids)
        conn = SchedulerFakeConnection(store)

        def flaky_check(cur, article_id, actor_id, *, actor_type, fetch=None):
            if article_id == 5:
                raise RuntimeError('provider timeout')
            return _sync_result(article_id, {'crossref': 'ok'})

        result = run_scheduled_metadata_sync(
            conn, config=CONFIG, select_due=_select_due_stub(article_ids),
            check=flaky_check)

        self.assertEqual(result.total_selected, 10)
        self.assertEqual(result.processed, 10)
        self.assertEqual(result.success, 9)
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.skipped, 0)
        self.assertEqual(result.status, RUN_COMPLETED_WITH_ERRORS)

    def test_article_sync_error_counts_as_skipped_not_failed(self):
        store = _new_store_with_articles([1, 2])
        conn = SchedulerFakeConnection(store)

        def check_missing_doi(cur, article_id, actor_id, *, actor_type, fetch=None):
            if article_id == 1:
                raise ArticleSyncError('missing_doi', 'DOI yo\'q')
            return _sync_result(article_id, {'crossref': 'ok'})

        result = run_scheduled_metadata_sync(
            conn, config=CONFIG, select_due=_select_due_stub([1, 2]),
            check=check_missing_doi)

        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.processed, 1)
        self.assertEqual(result.success, 1)
        self.assertEqual(result.total_selected, result.processed + result.skipped)

    def test_partial_and_failed_provider_classification(self):
        store = _new_store_with_articles([1, 2])
        conn = SchedulerFakeConnection(store)

        def classify_check(cur, article_id, actor_id, *, actor_type, fetch=None):
            if article_id == 1:
                return _sync_result(article_id, {'crossref': 'ok',
                                                 'unpaywall': 'unavailable'})
            return _sync_result(article_id, {'crossref': 'unavailable',
                                             'openalex': 'unavailable'})

        result = run_scheduled_metadata_sync(
            conn, config=CONFIG, select_due=_select_due_stub([1, 2]),
            check=classify_check)

        self.assertEqual(result.partial, 1)
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.success, 0)
        # All-providers-failed is still `processed` — check_article_metadata()
        # completed and created a sync record, no new error model (§18).
        self.assertEqual(result.processed, 2)
        self.assertEqual(result.status, RUN_COMPLETED_WITH_ERRORS)


class DryRunTests(unittest.TestCase):
    def test_dry_run_makes_no_provider_calls_or_writes(self):
        store = _new_store_with_articles([1, 2, 3])
        conn = SchedulerFakeConnection(store)
        calls = []

        def spy_check(*a, **k):
            calls.append((a, k))
            raise AssertionError('dry-run check() chaqirmasligi kerak')

        result = run_scheduled_metadata_sync(
            conn, config=CONFIG, dry_run=True,
            select_due=_select_due_stub([1, 2, 3]), check=spy_check)

        self.assertTrue(result.dry_run)
        self.assertEqual(result.total_selected, 3)
        self.assertEqual(result.article_ids, [1, 2, 3])
        self.assertEqual(calls, [])
        self.assertEqual(store['syncs'], {})
        self.assertNotIn('runs', store)
        self.assertFalse(store.get('lock_held', False))


class ActorSemanticsTests(unittest.TestCase):
    def test_check_called_with_system_actor_and_null_actor_id(self):
        store = _new_store_with_articles([1])
        conn = SchedulerFakeConnection(store)
        seen = {}

        def capture_check(cur, article_id, actor_id, *, actor_type, fetch=None):
            seen['article_id'] = article_id
            seen['actor_id'] = actor_id
            seen['actor_type'] = actor_type
            return _sync_result(article_id, {'crossref': 'ok'})

        run_scheduled_metadata_sync(
            conn, config=CONFIG, select_due=_select_due_stub([1]),
            check=capture_check, trigger_type=TRIGGER_MANUAL_CLI)

        self.assertEqual(seen['actor_id'], None)
        self.assertEqual(seen['actor_type'], 'system')


class RunAuditTests(unittest.TestCase):
    def test_run_row_transitions_running_to_completed(self):
        store = _new_store_with_articles([1])
        conn = SchedulerFakeConnection(store)

        def ok_check(cur, article_id, actor_id, *, actor_type, fetch=None):
            return _sync_result(article_id, {'crossref': 'ok'})

        result = run_scheduled_metadata_sync(
            conn, config=CONFIG, select_due=_select_due_stub([1]), check=ok_check)

        run_row = store['runs'][result.run_id]
        self.assertEqual(run_row['status'], RUN_COMPLETED)
        self.assertEqual(run_row['total_selected'], 1)
        self.assertEqual(run_row['success_count'], 1)
        self.assertIsNotNone(result.run_id)


class NoAutomaticMutationTests(unittest.TestCase):
    """Haqiqiy `check_article_metadata()` orqali — article maydonlari
    o'zgarmaydi, faqat sync yozuvi yaratiladi (§36, acceptance criteria).
    """

    def test_real_check_creates_sync_record_without_mutating_article(self):
        store = _new_store_with_articles([1])
        store['articles'][1].update({
            'doi': '10.1000/abc', 'title': 'Old Title', 'citation_count': 24,
            'is_retracted': False,
        })
        conn = SchedulerFakeConnection(store)

        fetch = make_fetcher(title='New Title', citation_count=99,
                             is_retracted=True)

        result = run_scheduled_metadata_sync(
            conn, config=CONFIG, select_due=_select_due_stub([1]), fetch=fetch)

        self.assertEqual(store['articles'][1]['title'], 'Old Title')
        self.assertEqual(store['articles'][1]['citation_count'], 24)
        self.assertEqual(store['articles'][1]['is_retracted'], False)
        self.assertEqual(len(store['syncs']), 1)
        sync_row = next(iter(store['syncs'].values()))
        self.assertEqual(sync_row['actor_type'], 'system')
        self.assertIsNone(sync_row['checked_by'])
        self.assertEqual(sync_row['status'], 'checked')
        self.assertEqual(result.success, 1)


if __name__ == '__main__':
    unittest.main()
