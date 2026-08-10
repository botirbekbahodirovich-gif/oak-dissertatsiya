# -*- coding: utf-8 -*-
"""Scheduled article discovery — DB qatlami testlari (Phase 2D.3, §8, §19).

`tests/fake_discovery_scheduler_pg.py` — Python-side due/claim simulyatsiya
(haqiqiy `FOR UPDATE SKIP LOCKED` POYGA holati EMAS — buning uchun
`tests/test_article_discovery_scheduler_dbgated.py`ga qarang, §21).
"""
import unittest
from datetime import date, datetime, timedelta, timezone

from services.article_discovery import saved_queries, scheduler_persistence
from services.article_discovery.models import ArticleDiscoveryQuery
from services.article_discovery.scheduler_state import (FREQUENCY_DAILY,
                                                         FREQUENCY_MONTHLY,
                                                         FREQUENCY_WEEKLY,
                                                         TRIGGER_MANUAL,
                                                         TRIGGER_SCHEDULED)
from tests.fake_discovery_scheduler_pg import DiscoverySchedulerCursor, new_store

QUERY = ArticleDiscoveryQuery(keyword='oncology', from_date=date(2024, 1, 1),
                              to_date=date(2024, 1, 31), oak_field_id=None,
                              limit=10, providers=('openalex',))


class ComputeNextRunAtTests(unittest.TestCase):
    def test_daily_adds_one_day(self):
        base = datetime(2026, 3, 10, 8, 0, tzinfo=timezone.utc)
        result = scheduler_persistence.compute_next_run_at(FREQUENCY_DAILY, from_time=base)
        self.assertEqual(result, base + timedelta(days=1))

    def test_weekly_adds_seven_days(self):
        base = datetime(2026, 3, 10, 8, 0, tzinfo=timezone.utc)
        result = scheduler_persistence.compute_next_run_at(FREQUENCY_WEEKLY, from_time=base)
        self.assertEqual(result, base + timedelta(weeks=1))

    def test_monthly_advances_calendar_month(self):
        base = datetime(2026, 3, 10, 8, 0, tzinfo=timezone.utc)
        result = scheduler_persistence.compute_next_run_at(FREQUENCY_MONTHLY, from_time=base)
        self.assertEqual(result, datetime(2026, 4, 10, 8, 0, tzinfo=timezone.utc))

    def test_monthly_wraps_year(self):
        base = datetime(2026, 12, 15, 8, 0, tzinfo=timezone.utc)
        result = scheduler_persistence.compute_next_run_at(FREQUENCY_MONTHLY, from_time=base)
        self.assertEqual(result, datetime(2027, 1, 15, 8, 0, tzinfo=timezone.utc))

    def test_monthly_end_of_month_clamps_to_shorter_month(self):
        """31-yanvar -> 28-fevral (2026 kabisa yili emas) — drift oldini
        olish uchun kalendar-to'g'ri arifmetika (§8)."""
        base = datetime(2026, 1, 31, 8, 0, tzinfo=timezone.utc)
        result = scheduler_persistence.compute_next_run_at(FREQUENCY_MONTHLY, from_time=base)
        self.assertEqual(result, datetime(2026, 2, 28, 8, 0, tzinfo=timezone.utc))

    def test_monthly_leap_year_february(self):
        base = datetime(2028, 1, 31, 8, 0, tzinfo=timezone.utc)  # 2028 kabisa
        result = scheduler_persistence.compute_next_run_at(FREQUENCY_MONTHLY, from_time=base)
        self.assertEqual(result, datetime(2028, 2, 29, 8, 0, tzinfo=timezone.utc))

    def test_default_from_time_is_now(self):
        before = datetime.now(timezone.utc)
        result = scheduler_persistence.compute_next_run_at(FREQUENCY_DAILY)
        after = datetime.now(timezone.utc)
        self.assertTrue(before + timedelta(days=1) <= result <= after + timedelta(days=1))

    def test_missed_schedule_computes_from_now_not_old_anchor(self):
        """§8 — 'catch-up storm' oldini olish: agar `from_time` (haqiqiy run
        vaqti) eski `next_run_at`dan ANCHA KEYIN bo'lsa ham, natija FAQAT
        BITTA interval oldinga — o'tkazib yuborilgan voqealar zanjiri
        HISOBLANMAYDI."""
        long_overdue_original = datetime(2026, 1, 1, tzinfo=timezone.utc)
        actual_run_time = datetime(2026, 3, 15, tzinfo=timezone.utc)  # 2+ oy kech
        result = scheduler_persistence.compute_next_run_at(
            FREQUENCY_DAILY, from_time=actual_run_time)
        # Natija `actual_run_time`dan FAQAT 1 kun keyin — `long_overdue_original`
        # asosida hisoblangan o'nlab kunlik zanjir EMAS.
        self.assertEqual(result, actual_run_time + timedelta(days=1))
        self.assertGreater(result, long_overdue_original + timedelta(days=60))

    def test_unknown_frequency_rejected(self):
        with self.assertRaises(ValueError):
            scheduler_persistence.compute_next_run_at('hourly')


class SetScheduleTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store()
        self.cur = DiscoverySchedulerCursor(self.store)
        self.query_id = saved_queries.create(self.cur, 1, 'X', QUERY)

    def test_enable_sets_frequency_and_next_run_at_now(self):
        before = datetime.now(timezone.utc)
        scheduler_persistence.set_schedule(
            self.cur, self.query_id, enabled=True, frequency=FREQUENCY_DAILY)
        row = saved_queries.get(self.cur, self.query_id)
        self.assertTrue(row['schedule_enabled'])
        self.assertEqual(row['schedule_frequency'], FREQUENCY_DAILY)
        self.assertGreaterEqual(row['next_run_at'], before)

    def test_disable_clears_frequency_and_next_run_at(self):
        scheduler_persistence.set_schedule(
            self.cur, self.query_id, enabled=True, frequency=FREQUENCY_WEEKLY)
        scheduler_persistence.set_schedule(
            self.cur, self.query_id, enabled=False, frequency=None)
        row = saved_queries.get(self.cur, self.query_id)
        self.assertFalse(row['schedule_enabled'])
        self.assertIsNone(row['schedule_frequency'])
        self.assertIsNone(row['next_run_at'])

    def test_unknown_id_returns_false(self):
        self.assertFalse(scheduler_persistence.set_schedule(
            self.cur, 999, enabled=True, frequency=FREQUENCY_DAILY))


class RecordRunOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store()
        self.cur = DiscoverySchedulerCursor(self.store)
        self.query_id = saved_queries.create(self.cur, 1, 'X', QUERY)

    def test_scheduled_trigger_updates_next_run_at_and_last_scheduled_run_at(self):
        next_run = datetime.now(timezone.utc) + timedelta(days=1)
        scheduler_persistence.record_run_outcome(
            self.cur, self.query_id, trigger_type=TRIGGER_SCHEDULED, status='success',
            error_summary=None, next_run_at=next_run)
        row = saved_queries.get(self.cur, self.query_id)
        self.assertEqual(row['last_run_status'], 'success')
        self.assertIsNotNone(row['last_scheduled_run_at'])
        self.assertEqual(row['next_run_at'], next_run)

    def test_manual_trigger_does_not_touch_next_run_at(self):
        """§9 — qo'lda ishga tushirish `next_run_at`ni kutilmagan holda
        surmasligi kerak."""
        scheduler_persistence.set_schedule(
            self.cur, self.query_id, enabled=True, frequency=FREQUENCY_DAILY)
        before = saved_queries.get(self.cur, self.query_id)
        scheduler_persistence.record_run_outcome(
            self.cur, self.query_id, trigger_type=TRIGGER_MANUAL, status='success',
            error_summary=None, next_run_at=datetime.now(timezone.utc) + timedelta(days=99))
        after = saved_queries.get(self.cur, self.query_id)
        self.assertEqual(after['next_run_at'], before['next_run_at'])
        self.assertIsNone(after['last_scheduled_run_at'])
        self.assertEqual(after['last_run_status'], 'success')


class ClaimDueQueriesTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store()
        self.cur = DiscoverySchedulerCursor(self.store)

    def _make_due(self, name, *, offset_minutes=-5, enabled=True, active=True,
                 frequency=FREQUENCY_DAILY):
        qid = saved_queries.create(self.cur, 1, name, QUERY)
        if not active:
            saved_queries.set_active(self.cur, qid, False)
        if enabled:
            scheduler_persistence.set_schedule(self.cur, qid, enabled=True,
                                               frequency=frequency)
            # `saved_queries.get()` qaytaradigan dict AJRALGAN nusxa —
            # qalbaki store'dagi HAQIQIY qatorni to'g'ridan-to'g'ri
            # o'zgartiramiz (`_saved_query_by_id` bilan bir xil qidiruv).
            row = next(r for r in self.store['saved_queries'] if r['id'] == qid)
            row['next_run_at'] = (datetime.now(timezone.utc)
                                  + timedelta(minutes=offset_minutes))
        return qid

    def test_due_query_is_claimed(self):
        qid = self._make_due('Due now')
        claimed = scheduler_persistence.claim_due_queries(self.cur, 10)
        self.assertEqual(claimed, [qid])

    def test_future_query_not_claimed(self):
        self._make_due('Future', offset_minutes=+60)
        claimed = scheduler_persistence.claim_due_queries(self.cur, 10)
        self.assertEqual(claimed, [])

    def test_inactive_query_not_claimed(self):
        self._make_due('Inactive', active=False)
        claimed = scheduler_persistence.claim_due_queries(self.cur, 10)
        self.assertEqual(claimed, [])

    def test_manual_only_query_not_claimed(self):
        """`schedule_enabled=False` — hech qachon due bo'lmaydi."""
        self._make_due('Manual only', enabled=False)
        claimed = scheduler_persistence.claim_due_queries(self.cur, 10)
        self.assertEqual(claimed, [])

    def test_claim_respects_max_per_cycle_limit(self):
        for i in range(5):
            self._make_due('Due-%d' % i)
        claimed = scheduler_persistence.claim_due_queries(self.cur, 2)
        self.assertEqual(len(claimed), 2)

    def test_claimed_query_next_run_at_pushed_into_future(self):
        """Band qilingandan keyin qator DARHOL "due emas" bo'lib qoladi
        (§5 — crash-safety hold)."""
        qid = self._make_due('Due now')
        scheduler_persistence.claim_due_queries(self.cur, 10)
        row = saved_queries.get(self.cur, qid)
        self.assertGreater(row['next_run_at'], datetime.now(timezone.utc) + timedelta(minutes=30))

    def test_claimed_query_not_claimed_again_same_cycle(self):
        self._make_due('Due now')
        first = scheduler_persistence.claim_due_queries(self.cur, 10)
        second = scheduler_persistence.claim_due_queries(self.cur, 10)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

    def test_due_ordering_is_oldest_first(self):
        older = self._make_due('Older', offset_minutes=-30)
        newer = self._make_due('Newer', offset_minutes=-5)
        claimed = scheduler_persistence.claim_due_queries(self.cur, 10)
        self.assertEqual(claimed, [older, newer])

    def test_preview_does_not_claim(self):
        qid = self._make_due('Due now')
        preview = scheduler_persistence.list_due_queries_preview(self.cur, 10)
        self.assertEqual([r['id'] for r in preview], [qid])
        # Preview keyin ham HALI due — band qilinmagan.
        claimed = scheduler_persistence.claim_due_queries(self.cur, 10)
        self.assertEqual(claimed, [qid])


class RunAuditTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store()
        self.cur = DiscoverySchedulerCursor(self.store)
        self.query_id = saved_queries.create(self.cur, 1, 'X', QUERY)

    def test_create_and_finalize_run(self):
        run_id = scheduler_persistence.create_run(
            self.cur, self.query_id, TRIGGER_SCHEDULED, ['openalex', 'crossref'])

        class Outcome:
            status = 'success'
            found_count = 10
            new_count = 3
            queued_count = 3
            duplicate_count = 7
            no_doi_count = 0
            error_summary = None
            import_batch_id = 42

        scheduler_persistence.finalize_run(self.cur, run_id, Outcome())
        runs = scheduler_persistence.list_recent_runs(self.cur, saved_query_id=self.query_id)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]['status'], 'success')
        self.assertEqual(runs[0]['new_count'], 3)
        self.assertEqual(runs[0]['import_batch_id'], 42)
        self.assertEqual(runs[0]['saved_query_name'], 'X')

    def test_error_summary_is_bounded(self):
        run_id = scheduler_persistence.create_run(
            self.cur, self.query_id, TRIGGER_SCHEDULED, [])

        class Outcome:
            status = 'failed'
            found_count = 0
            new_count = 0
            queued_count = 0
            duplicate_count = 0
            no_doi_count = 0
            error_summary = 'x' * 1000
            import_batch_id = None

        scheduler_persistence.finalize_run(self.cur, run_id, Outcome())
        runs = scheduler_persistence.list_recent_runs(self.cur, saved_query_id=self.query_id)
        self.assertLessEqual(len(runs[0]['error_summary']), 500)


if __name__ == '__main__':
    unittest.main()
