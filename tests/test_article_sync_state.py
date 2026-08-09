# -*- coding: utf-8 -*-
"""Dashboard queue holati va actor semantikasi (Phase 2C.3B, §6, §29)."""
import unittest

from services.article_sync import state


def sync_row(**overrides):
    row = {'status': 'checked', 'change_count': 0, 'critical_change_count': 0,
          'review_change_count': 0, 'safe_change_count': 0}
    row.update(overrides)
    return row


class DeriveQueueStateTests(unittest.TestCase):
    def test_applied_status_wins(self):
        row = sync_row(status='applied', critical_change_count=3)
        self.assertEqual(state.derive_queue_state(row), state.APPLIED_STATE)

    def test_dismissed_status_wins(self):
        row = sync_row(status='dismissed', critical_change_count=3)
        self.assertEqual(state.derive_queue_state(row), state.DISMISSED_STATE)

    def test_stale_overrides_severity(self):
        row = sync_row(status='checked', critical_change_count=1)
        self.assertEqual(state.derive_queue_state(row, is_stale=True), state.STALE)

    def test_critical_when_checked_and_not_stale(self):
        row = sync_row(status='checked', critical_change_count=1, change_count=1)
        self.assertEqual(state.derive_queue_state(row), state.CRITICAL)

    def test_no_changes_when_change_count_zero(self):
        row = sync_row(status='checked', change_count=0)
        self.assertEqual(state.derive_queue_state(row), state.NO_CHANGES)

    def test_needs_review_when_changed_and_not_critical(self):
        row = sync_row(status='checked', change_count=2, review_change_count=2)
        self.assertEqual(state.derive_queue_state(row), state.NEEDS_REVIEW)


class IsActionableTests(unittest.TestCase):
    def test_needs_review_is_actionable(self):
        row = sync_row(status='checked', change_count=1)
        self.assertTrue(state.is_actionable(row))

    def test_critical_is_actionable(self):
        row = sync_row(status='checked', change_count=1, critical_change_count=1)
        self.assertTrue(state.is_actionable(row))

    def test_no_changes_is_not_actionable(self):
        row = sync_row(status='checked', change_count=0)
        self.assertFalse(state.is_actionable(row))

    def test_applied_is_not_actionable(self):
        row = sync_row(status='applied', change_count=1)
        self.assertFalse(state.is_actionable(row))

    def test_dismissed_is_not_actionable(self):
        row = sync_row(status='dismissed', change_count=1)
        self.assertFalse(state.is_actionable(row))

    def test_stale_is_not_actionable(self):
        row = sync_row(status='checked', change_count=1)
        self.assertFalse(state.is_actionable(row, is_stale=True))


class ActorValidationTests(unittest.TestCase):
    def test_admin_actor_requires_actor_id(self):
        with self.assertRaises(ValueError):
            state.validate_actor(state.ACTOR_ADMIN, None)
        state.validate_actor(state.ACTOR_ADMIN, 1)   # ko'tarilmasligi kerak

    def test_system_actor_allows_null_actor_id(self):
        state.validate_actor(state.ACTOR_SYSTEM, None)   # ko'tarilmasligi kerak

    def test_agent_actor_allows_null_actor_id(self):
        state.validate_actor(state.ACTOR_AGENT, None)

    def test_invalid_actor_type_rejected(self):
        with self.assertRaises(ValueError):
            state.validate_actor('hacker', 1)

    def test_actor_types_tuple_is_exactly_three(self):
        self.assertEqual(state.ACTOR_TYPES, ('admin', 'system', 'agent'))


class BulkSafeQueueStatesTests(unittest.TestCase):
    def test_only_needs_review_is_bulk_safe(self):
        self.assertEqual(state.BULK_SAFE_QUEUE_STATES, frozenset({state.NEEDS_REVIEW}))

    def test_critical_and_stale_are_not_bulk_safe(self):
        self.assertNotIn(state.CRITICAL, state.BULK_SAFE_QUEUE_STATES)
        self.assertNotIn(state.STALE, state.BULK_SAFE_QUEUE_STATES)
        self.assertNotIn(state.APPLIED_STATE, state.BULK_SAFE_QUEUE_STATES)
        self.assertNotIn(state.DISMISSED_STATE, state.BULK_SAFE_QUEUE_STATES)


if __name__ == '__main__':
    unittest.main()
