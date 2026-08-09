# -*- coding: utf-8 -*-
"""Import elementi/batch holat mashinasi testlari (§65 qisman, §10)."""
import unittest

from services.article_import import state as st


class ItemTransitionTests(unittest.TestCase):
    def test_pending_to_processing_allowed(self):
        self.assertTrue(st.can_transition(st.PENDING, st.PROCESSING))

    def test_processing_to_terminal_reviewish_allowed(self):
        for target in (st.READY_FOR_REVIEW, st.DUPLICATE_IN_BATCH, st.ALREADY_EXISTS,
                      st.INVALID_DOI, st.METADATA_NOT_FOUND, st.PARTIAL_METADATA,
                      st.PROVIDER_FAILED, st.RETRACTED, st.FAILED):
            self.assertTrue(st.can_transition(st.PROCESSING, target), target)

    def test_approved_is_terminal(self):
        for target in st.ITEM_STATUSES:
            self.assertFalse(st.can_transition(st.APPROVED, target), target)
        self.assertIn(st.APPROVED, st.TERMINAL_ITEM_STATUSES)

    def test_rejected_and_skipped_are_terminal(self):
        self.assertIn(st.REJECTED, st.TERMINAL_ITEM_STATUSES)
        self.assertIn(st.SKIPPED, st.TERMINAL_ITEM_STATUSES)

    def test_review_bucket_can_approve_reject_skip(self):
        for source in (st.READY_FOR_REVIEW, st.PARTIAL_METADATA, st.RETRACTED):
            for target in (st.APPROVED, st.REJECTED, st.SKIPPED):
                self.assertTrue(st.can_transition(source, target), (source, target))

    def test_already_approved_cannot_reapprove(self):
        self.assertFalse(st.can_transition(st.APPROVED, st.APPROVED))

    def test_invalid_doi_can_only_skip(self):
        self.assertEqual(st.ALLOWED_TRANSITIONS[st.INVALID_DOI], {st.SKIPPED})

    def test_already_exists_can_only_skip(self):
        self.assertEqual(st.ALLOWED_TRANSITIONS[st.ALREADY_EXISTS], {st.SKIPPED})

    def test_provider_failed_can_retry_or_skip(self):
        self.assertEqual(st.ALLOWED_TRANSITIONS[st.PROVIDER_FAILED],
                         {st.PENDING, st.SKIPPED})

    def test_assert_transition_raises_on_invalid(self):
        with self.assertRaises(st.InvalidTransition):
            st.assert_transition(st.APPROVED, st.PENDING)

    def test_assert_transition_passes_on_valid(self):
        st.assert_transition(st.PENDING, st.PROCESSING)   # ko'tarilmasligi kerak

    def test_every_status_has_a_transition_entry(self):
        for status in st.ITEM_STATUSES:
            self.assertIn(status, st.ALLOWED_TRANSITIONS, status)


class BulkApproveSafetyTests(unittest.TestCase):
    def test_only_ready_for_review_is_bulk_safe(self):
        self.assertEqual(st.BULK_APPROVE_SAFE_STATUSES, frozenset({st.READY_FOR_REVIEW}))

    def test_retracted_and_partial_are_not_bulk_safe(self):
        self.assertNotIn(st.RETRACTED, st.BULK_APPROVE_SAFE_STATUSES)
        self.assertNotIn(st.PARTIAL_METADATA, st.BULK_APPROVE_SAFE_STATUSES)
        self.assertNotIn(st.PROVIDER_FAILED, st.BULK_APPROVE_SAFE_STATUSES)
        self.assertNotIn(st.ALREADY_EXISTS, st.BULK_APPROVE_SAFE_STATUSES)
        self.assertNotIn(st.INVALID_DOI, st.BULK_APPROVE_SAFE_STATUSES)


class BatchStatusDerivationTests(unittest.TestCase):
    def test_empty_batch_is_draft(self):
        self.assertEqual(st.derive_batch_status({}, False, None), st.BATCH_DRAFT)

    def test_not_started_is_queued(self):
        counts = {st.PENDING: 5}
        self.assertEqual(st.derive_batch_status(counts, False, None), st.BATCH_QUEUED)

    def test_in_flight_is_processing(self):
        counts = {st.PENDING: 2, st.PROCESSING: 1, st.APPROVED: 3}
        self.assertEqual(st.derive_batch_status(counts, False, 'STARTED'), st.BATCH_PROCESSING)

    def test_review_bucket_nonempty_is_needs_review(self):
        counts = {st.READY_FOR_REVIEW: 2, st.APPROVED: 1}
        self.assertEqual(st.derive_batch_status(counts, False, 'STARTED'), st.BATCH_NEEDS_REVIEW)

    def test_only_failures_left_is_completed_with_errors(self):
        counts = {st.APPROVED: 1, st.FAILED: 2}
        self.assertEqual(st.derive_batch_status(counts, False, 'STARTED'),
                         st.BATCH_COMPLETED_WITH_ERRORS)

    def test_all_terminal_no_failures_is_completed(self):
        counts = {st.APPROVED: 3, st.REJECTED: 1, st.SKIPPED: 1}
        self.assertEqual(st.derive_batch_status(counts, False, 'STARTED'), st.BATCH_COMPLETED)

    def test_cancel_requested_with_no_in_flight_is_cancelled(self):
        counts = {st.APPROVED: 1, st.SKIPPED: 3}
        self.assertEqual(st.derive_batch_status(counts, True, 'STARTED'), st.BATCH_CANCELLED)

    def test_cancel_requested_while_in_flight_still_processing(self):
        counts = {st.PROCESSING: 1, st.PENDING: 3}
        self.assertEqual(st.derive_batch_status(counts, True, 'STARTED'), st.BATCH_PROCESSING)


if __name__ == '__main__':
    unittest.main()
