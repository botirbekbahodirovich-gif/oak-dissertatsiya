# -*- coding: utf-8 -*-
"""Dashboard servis testlari — dismiss, bulk-safe-apply, actor semantikasi
(Phase 2C.3B, §29, §30).
"""
import unittest

from services.article_sync import service as svc
from services.article_sync.state import ACTOR_ADMIN, ACTOR_SYSTEM
from tests.fake_article_sync_pg import FakeSyncCursor, new_store
from tests.test_article_sync_service import base_article_row, make_fetcher


class ActorSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store(base_article_row())
        self.cur = FakeSyncCursor(self.store)

    def test_admin_actor_check_records_actor_type(self):
        result = svc.check_article_metadata(self.cur, 1, actor_id=7,
                                            actor_type=ACTOR_ADMIN,
                                            fetch=make_fetcher())
        sync = self.store['syncs'][result.sync_id]
        self.assertEqual(sync['actor_type'], 'admin')
        self.assertEqual(sync['checked_by'], 7)

    def test_system_actor_allowed_with_null_actor_id(self):
        result = svc.check_article_metadata(self.cur, 1, actor_id=None,
                                            actor_type=ACTOR_SYSTEM,
                                            fetch=make_fetcher())
        sync = self.store['syncs'][result.sync_id]
        self.assertEqual(sync['actor_type'], 'system')
        self.assertIsNone(sync['checked_by'])

    def test_admin_actor_without_actor_id_rejected(self):
        with self.assertRaises(svc.ArticleSyncError) as ctx:
            svc.check_article_metadata(self.cur, 1, actor_id=None,
                                       actor_type=ACTOR_ADMIN, fetch=make_fetcher())
        self.assertEqual(ctx.exception.code, 'invalid_actor')
        self.assertEqual(self.store['syncs'], {})   # hech narsa yozilmadi

    def test_invalid_actor_type_rejected(self):
        with self.assertRaises(svc.ArticleSyncError) as ctx:
            svc.check_article_metadata(self.cur, 1, actor_id=1,
                                       actor_type='hacker', fetch=make_fetcher())
        self.assertEqual(ctx.exception.code, 'invalid_actor')

    def test_default_actor_type_is_admin_backward_compatible(self):
        """Eski chaqiruvchilar (2C.3A) `actor_type` bermaydi — default
        'admin' bo'lishi kerak, mavjud xatti-harakat buzilmasin."""
        result = svc.check_article_metadata(self.cur, 1, actor_id=1,
                                            fetch=make_fetcher())
        sync = self.store['syncs'][result.sync_id]
        self.assertEqual(sync['actor_type'], 'admin')


class DismissTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store(base_article_row())
        self.cur = FakeSyncCursor(self.store)
        self.result = svc.check_article_metadata(self.cur, 1, actor_id=1,
                                                  fetch=make_fetcher())

    def test_dismiss_closes_sync_without_touching_article(self):
        svc.dismiss_sync_result(self.cur, self.result.sync_id, actor_id=2,
                                reason='External variant kerak emas')
        sync = self.store['syncs'][self.result.sync_id]
        self.assertEqual(sync['status'], 'dismissed')
        self.assertEqual(sync['dismissed_by'], 2)
        self.assertEqual(sync['dismissed_reason'], 'External variant kerak emas')
        self.assertEqual(self.store['articles'][1]['title'], 'Old Title')

    def test_dismiss_does_not_delete_history(self):
        svc.dismiss_sync_result(self.cur, self.result.sync_id, actor_id=2)
        self.assertIn(self.result.sync_id, self.store['syncs'])

    def test_dismiss_twice_is_rejected(self):
        svc.dismiss_sync_result(self.cur, self.result.sync_id, actor_id=2)
        with self.assertRaises(svc.ArticleSyncError) as ctx:
            svc.dismiss_sync_result(self.cur, self.result.sync_id, actor_id=2)
        self.assertEqual(ctx.exception.code, 'cannot_dismiss')

    def test_dismiss_after_apply_is_rejected(self):
        svc.apply_article_metadata_changes(self.cur, 1, self.result.sync_id,
                                           actor_id=2, selected_fields=['citation_count'])
        with self.assertRaises(svc.ArticleSyncError) as ctx:
            svc.dismiss_sync_result(self.cur, self.result.sync_id, actor_id=2)
        self.assertEqual(ctx.exception.code, 'cannot_dismiss')

    def test_dismiss_unknown_sync_raises_not_found(self):
        with self.assertRaises(svc.ArticleSyncError) as ctx:
            svc.dismiss_sync_result(self.cur, 999999, actor_id=2)
        self.assertEqual(ctx.exception.code, 'not_found')

    def test_dismiss_is_not_reject_article(self):
        """Dismiss ≠ reject article — maqola holati umuman o'zgarmaydi."""
        before = dict(self.store['articles'][1])
        svc.dismiss_sync_result(self.cur, self.result.sync_id, actor_id=2)
        self.assertEqual(self.store['articles'][1], before)


class RecheckTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store(base_article_row())
        self.cur = FakeSyncCursor(self.store)

    def test_recheck_creates_new_sync_and_preserves_old(self):
        first = svc.check_article_metadata(self.cur, 1, actor_id=1,
                                           fetch=make_fetcher(citation_count=30))
        second = svc.check_article_metadata(self.cur, 1, actor_id=1,
                                            fetch=make_fetcher(citation_count=41))
        self.assertNotEqual(first.sync_id, second.sync_id)
        self.assertIn(first.sync_id, self.store['syncs'])
        self.assertIn(second.sync_id, self.store['syncs'])
        # Ikkalasi ham TARIXDA qoladi — eski overwrite qilinmagan.
        self.assertEqual(len(self.store['syncs']), 2)

    def test_recheck_does_not_overwrite_applied_history(self):
        first = svc.check_article_metadata(self.cur, 1, actor_id=1,
                                           fetch=make_fetcher(citation_count=30))
        svc.apply_article_metadata_changes(self.cur, 1, first.sync_id, actor_id=1,
                                           selected_fields=['citation_count'])
        svc.check_article_metadata(self.cur, 1, actor_id=1,
                                   fetch=make_fetcher(citation_count=41))
        applied_sync = self.store['syncs'][first.sync_id]
        self.assertEqual(applied_sync['status'], 'applied')


class BulkApplySafeTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store(base_article_row())
        self.cur = FakeSyncCursor(self.store)

    def test_safe_only_sync_is_bulk_applied(self):
        result = svc.check_article_metadata(self.cur, 1, actor_id=1,
                                            fetch=make_fetcher(title='Old Title',
                                                               citation_count=41))
        summary = svc.bulk_apply_safe_changes(self.cur, [result.sync_id], actor_id=2)
        self.assertEqual(len(summary['applied']), 1)
        self.assertEqual(self.store['articles'][1]['citation_count'], 41)

    def test_retraction_never_bulk_applied(self):
        result = svc.check_article_metadata(self.cur, 1, actor_id=1,
                                            fetch=make_fetcher(is_retracted=True))
        summary = svc.bulk_apply_safe_changes(self.cur, [result.sync_id], actor_id=2)
        self.assertEqual(summary['applied'], [])
        self.assertEqual(len(summary['skipped']), 1)
        self.assertFalse(self.store['articles'][1]['is_retracted'])

    def test_review_only_change_never_bulk_applied(self):
        result = svc.check_article_metadata(
            self.cur, 1, actor_id=1,
            fetch=make_fetcher(title='Completely Different Title', citation_count=None))
        summary = svc.bulk_apply_safe_changes(self.cur, [result.sync_id], actor_id=2)
        self.assertEqual(summary['applied'], [])
        self.assertEqual(self.store['articles'][1]['title'], 'Old Title')

    def test_stale_sync_skipped_not_applied(self):
        result = svc.check_article_metadata(self.cur, 1, actor_id=1,
                                            fetch=make_fetcher(citation_count=41))
        # Boshqa admin orada maqolani tahrirlaydi.
        self.cur.execute("UPDATE scientific_articles SET title = %s WHERE id = %s",
                         ('Edited elsewhere', 1))
        summary = svc.bulk_apply_safe_changes(self.cur, [result.sync_id], actor_id=2)
        self.assertEqual(summary['applied'], [])
        self.assertEqual(self.store['articles'][1]['citation_count'], 24)

    def test_already_applied_sync_skipped_no_duplicate(self):
        result = svc.check_article_metadata(self.cur, 1, actor_id=1,
                                            fetch=make_fetcher(citation_count=41))
        svc.apply_article_metadata_changes(self.cur, 1, result.sync_id, actor_id=2,
                                           selected_fields=['citation_count'])
        summary = svc.bulk_apply_safe_changes(self.cur, [result.sync_id], actor_id=2)
        self.assertEqual(summary['applied'], [])
        self.assertEqual(len(summary['skipped']), 1)


if __name__ == '__main__':
    unittest.main()
