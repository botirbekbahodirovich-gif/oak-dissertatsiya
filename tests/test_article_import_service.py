# -*- coding: utf-8 -*-
"""Servis qatlami testlari — batch yaratish, chunk processing, approve
idempotentligi, poyga holati va transaction xavfsizligi (§65-§69, §72-§76).

Qalbaki DB — tests/fake_article_import_pg.py (haqiqatan filtrlaydigan,
DOI UNIQUE cheklovini simulyatsiya qiluvchi kursor; sabab shu faylning
docstringida).
"""
import unittest

from services.article_import import persistence, service as svc
from services.article_import.config import ArticleImportConfig
from services.article_import.parser import ImportParseError
from services.article_import.state import (ALREADY_EXISTS, APPROVED,
                                            DUPLICATE_IN_BATCH, FAILED,
                                            INVALID_DOI, METADATA_NOT_FOUND,
                                            PENDING, PROVIDER_FAILED,
                                            READY_FOR_REVIEW, REJECTED,
                                            RETRACTED, SKIPPED)
from services.article_metadata.models import (CROSSREF, LOOKUP_FAILED,
                                              LOOKUP_NOT_FOUND, LOOKUP_OK,
                                              AuthorCandidate,
                                              MetadataLookupResult,
                                              NormalizedArticleMetadata)
from tests.fake_article_import_pg import FakeImportCursor, new_store

TEST_CONFIG = ArticleImportConfig(max_items=500, chunk_size=10, max_attempts=3)


def make_fetcher(status=LOOKUP_OK, title='Sample Title', is_retracted=False,
                 doi_override=None):
    """`fetch_article_metadata` o'rnini bosuvchi — tarmoqqa chiqmaydi."""
    def fetch(doi, *, config=None, cache_get=None, cache_set=None):
        metadata = NormalizedArticleMetadata(
            doi=doi_override or doi, title=title, is_retracted=is_retracted,
            journal_name='Test Journal', publication_year=2024,
            authors=[AuthorCandidate(name='Karimov Sardor', order=1)])
        return MetadataLookupResult(doi=doi, status=status, metadata=metadata,
                                    providers={}, conflicts=[], warnings=[])
    return fetch


class CreateBatchTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store()
        self.cur = FakeImportCursor(self.store)

    def test_creates_batch_and_items(self):
        batch_id = svc.create_import_batch(
            self.cur, created_by=1, source_type='doi_list',
            raw_text='10.1000/a\n10.1000/b', config=TEST_CONFIG)
        batch = svc.get_batch(self.cur, batch_id)
        self.assertEqual(batch['total_items'], 2)
        self.assertEqual(batch['pending_count'], 2)
        self.assertEqual(batch['status'], 'queued')

    def test_in_batch_duplicate_gets_flagged(self):
        batch_id = svc.create_import_batch(
            self.cur, created_by=1, source_type='doi_list',
            raw_text='10.1000/a\n10.1000/a', config=TEST_CONFIG)
        items, _ = svc.list_items(self.cur, batch_id)
        statuses = sorted(i['status'] for i in items)
        self.assertEqual(statuses, [DUPLICATE_IN_BATCH, PENDING])

    def test_invalid_doi_flagged_without_network(self):
        batch_id = svc.create_import_batch(
            self.cur, created_by=1, source_type='doi_list',
            raw_text='not-a-doi', config=TEST_CONFIG)
        items, _ = svc.list_items(self.cur, batch_id)
        self.assertEqual(items[0]['status'], INVALID_DOI)

    def test_existing_article_flagged_as_already_exists(self):
        self.store['articles'].append({'id': 42, 'title': 'Existing', 'slug': 'x', 'doi': '10.1000/a'})
        batch_id = svc.create_import_batch(
            self.cur, created_by=1, source_type='doi_list',
            raw_text='10.1000/a', config=TEST_CONFIG)
        items, _ = svc.list_items(self.cur, batch_id)
        self.assertEqual(items[0]['status'], ALREADY_EXISTS)
        self.assertEqual(items[0]['existing_article_id'], 42)

    def test_max_items_enforced(self):
        text = '\n'.join('10.1000/%d' % i for i in range(5))
        with self.assertRaises(ImportParseError):
            svc.create_import_batch(
                self.cur, created_by=1, source_type='doi_list', raw_text=text,
                config=ArticleImportConfig(max_items=3, chunk_size=10, max_attempts=3))

    def test_csv_source(self):
        csv_bytes = b'doi\n10.1000/a\n10.1000/b\n'
        batch_id = svc.create_import_batch(
            self.cur, created_by=1, source_type='csv', csv_bytes=csv_bytes,
            original_filename='x.csv', config=TEST_CONFIG)
        batch = svc.get_batch(self.cur, batch_id)
        self.assertEqual(batch['total_items'], 2)
        self.assertEqual(batch['source_type'], 'csv')


class ProcessBatchTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store()
        self.cur = FakeImportCursor(self.store)
        self.batch_id = svc.create_import_batch(
            self.cur, created_by=1, source_type='doi_list',
            raw_text='10.1000/a\n10.1000/b', config=TEST_CONFIG)

    def test_processing_never_writes_to_scientific_articles(self):
        """§72 — processing metadata topadi, lekin article YARATMAYDI."""
        svc.process_import_batch(self.cur, self.batch_id, config=TEST_CONFIG,
                                 fetch=make_fetcher())
        self.assertEqual(self.store['articles'], [])

    def test_successful_lookup_moves_to_ready_for_review(self):
        svc.process_import_batch(self.cur, self.batch_id, config=TEST_CONFIG,
                                 fetch=make_fetcher())
        items, _ = svc.list_items(self.cur, self.batch_id)
        self.assertTrue(all(i['status'] == READY_FOR_REVIEW for i in items))
        batch = svc.get_batch(self.cur, self.batch_id)
        self.assertEqual(batch['status'], 'needs_review')
        self.assertEqual(batch['review_count'], 2)

    def test_not_found_maps_to_metadata_not_found(self):
        svc.process_import_batch(
            self.cur, self.batch_id, config=TEST_CONFIG,
            fetch=make_fetcher(status=LOOKUP_NOT_FOUND))
        items, _ = svc.list_items(self.cur, self.batch_id)
        self.assertTrue(all(i['status'] == METADATA_NOT_FOUND for i in items))

    def test_failed_lookup_maps_to_provider_failed_and_is_retryable(self):
        svc.process_import_batch(
            self.cur, self.batch_id, config=TEST_CONFIG,
            fetch=make_fetcher(status=LOOKUP_FAILED))
        items, _ = svc.list_items(self.cur, self.batch_id)
        self.assertTrue(all(i['status'] == PROVIDER_FAILED for i in items))
        item_id = items[0]['id']
        svc.retry_import_item(self.cur, item_id, config=TEST_CONFIG)
        self.assertEqual(svc.get_item(self.cur, item_id)['status'], PENDING)

    def test_retracted_metadata_maps_to_retracted_status(self):
        svc.process_import_batch(
            self.cur, self.batch_id, config=TEST_CONFIG,
            fetch=make_fetcher(is_retracted=True))
        items, _ = svc.list_items(self.cur, self.batch_id)
        self.assertTrue(all(i['status'] == RETRACTED for i in items))
        self.assertTrue(all(i['is_retracted'] for i in items))

    def test_chunk_size_bounds_a_single_call(self):
        small = ArticleImportConfig(max_items=500, chunk_size=1, max_attempts=3)
        result = svc.process_import_batch(self.cur, self.batch_id, config=small,
                                          fetch=make_fetcher())
        self.assertEqual(result['processed'], 1)
        batch = svc.get_batch(self.cur, self.batch_id)
        self.assertEqual(batch['pending_count'], 1)
        self.assertEqual(batch['status'], 'processing')

    def test_resume_only_processes_remaining_pending(self):
        small = ArticleImportConfig(max_items=500, chunk_size=1, max_attempts=3)
        svc.process_import_batch(self.cur, self.batch_id, config=small, fetch=make_fetcher())
        result2 = svc.process_import_batch(self.cur, self.batch_id, config=small,
                                           fetch=make_fetcher())
        self.assertEqual(result2['processed'], 1)
        batch = svc.get_batch(self.cur, self.batch_id)
        self.assertEqual(batch['status'], 'needs_review')

    def test_one_item_failure_does_not_block_others_in_chunk(self):
        """§88 — bitta DOI xatosi butun chunkni yiqitmasin."""
        calls = {'n': 0}

        def flaky_fetch(doi, *, config=None, cache_get=None, cache_set=None):
            calls['n'] += 1
            if calls['n'] == 1:
                raise RuntimeError('kutilmagan tarmoq xatosi')
            return make_fetcher()(doi, config=config)

        svc.process_import_batch(self.cur, self.batch_id, config=TEST_CONFIG,
                                 fetch=flaky_fetch)
        items, _ = svc.list_items(self.cur, self.batch_id)
        statuses = sorted(i['status'] for i in items)
        self.assertEqual(statuses, [FAILED, READY_FOR_REVIEW])

    def test_cancel_requested_stops_new_chunks(self):
        svc.cancel_import_batch(self.cur, self.batch_id)
        result = svc.process_import_batch(self.cur, self.batch_id, config=TEST_CONFIG,
                                          fetch=make_fetcher())
        self.assertEqual(result['processed'], 0)
        batch = svc.get_batch(self.cur, self.batch_id)
        self.assertEqual(batch['status'], 'cancelled')


class ApproveIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store()
        self.cur = FakeImportCursor(self.store)
        self.batch_id = svc.create_import_batch(
            self.cur, created_by=1, source_type='doi_list', raw_text='10.1000/a',
            config=TEST_CONFIG)
        svc.process_import_batch(self.cur, self.batch_id, config=TEST_CONFIG,
                                 fetch=make_fetcher())
        self.item_id = svc.list_items(self.cur, self.batch_id)[0][0]['id']

    def test_approve_creates_exactly_one_article(self):
        result = svc.approve_import_item(self.cur, self.item_id, actor_id=1)
        self.assertFalse(result['already_approved'])
        self.assertEqual(len(self.store['articles']), 1)
        item = svc.get_item(self.cur, self.item_id)
        self.assertEqual(item['status'], APPROVED)
        self.assertEqual(item['created_article_id'], result['article_id'])

    def test_created_article_is_draft_pending_review(self):
        svc.approve_import_item(self.cur, self.item_id, actor_id=1)
        # Fake store ustunlari cheklangan — verification/publication_status
        # persistence.create_article_from_metadata ichida DOIM shu qiymatlar
        # bilan yuboriladi; buni SQL log orqali tekshiramiz.
        insert_calls = [p for sql, p in self.store['sql']
                       if sql.startswith('INSERT INTO scientific_articles')]
        self.assertEqual(len(insert_calls), 1)
        values = insert_calls[0]
        # columns order: ... 'verification_status', 'publication_status' — index 21, 22
        self.assertEqual(values[21], 'pending_review')
        self.assertEqual(values[22], 'draft')

    def test_double_approve_does_not_create_second_article(self):
        first = svc.approve_import_item(self.cur, self.item_id, actor_id=1)
        second = svc.approve_import_item(self.cur, self.item_id, actor_id=1)
        self.assertTrue(second['already_approved'])
        self.assertEqual(first['article_id'], second['article_id'])
        self.assertEqual(len(self.store['articles']), 1)

    def test_retry_after_approval_is_blocked(self):
        svc.approve_import_item(self.cur, self.item_id, actor_id=1)
        with self.assertRaises(svc.ImportServiceError):
            svc.retry_import_item(self.cur, self.item_id, config=TEST_CONFIG)

    def test_reject_after_approval_is_blocked(self):
        svc.approve_import_item(self.cur, self.item_id, actor_id=1)
        with self.assertRaises(svc.ImportServiceError):
            svc.reject_import_item(self.cur, self.item_id, actor_id=1)

    def test_authors_written_on_approve(self):
        svc.approve_import_item(self.cur, self.item_id, actor_id=1)
        self.assertEqual(len(self.store['authors']), 1)


class RetractionAndConflictGateTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store()
        self.cur = FakeImportCursor(self.store)
        self.batch_id = svc.create_import_batch(
            self.cur, created_by=1, source_type='doi_list', raw_text='10.1000/a',
            config=TEST_CONFIG)
        svc.process_import_batch(self.cur, self.batch_id, config=TEST_CONFIG,
                                 fetch=make_fetcher(is_retracted=True))
        self.item_id = svc.list_items(self.cur, self.batch_id)[0][0]['id']

    def test_approve_without_confirmation_blocked(self):
        with self.assertRaises(svc.ImportServiceError) as ctx:
            svc.approve_import_item(self.cur, self.item_id, actor_id=1)
        self.assertEqual(ctx.exception.code, 'retraction_confirmation_required')
        self.assertEqual(len(self.store['articles']), 0)

    def test_approve_with_confirmation_succeeds(self):
        result = svc.approve_import_item(self.cur, self.item_id, actor_id=1,
                                         confirm_retracted=True)
        self.assertFalse(result['already_approved'])
        self.assertEqual(len(self.store['articles']), 1)


class DuplicateRaceConditionTests(unittest.TestCase):
    """§33, §69 — DB UNIQUE cheklovi YAKUNIY himoya."""

    def setUp(self):
        self.store = new_store()
        self.cur = FakeImportCursor(self.store)
        self.batch_id = svc.create_import_batch(
            self.cur, created_by=1, source_type='doi_list', raw_text='10.1000/a',
            config=TEST_CONFIG)
        svc.process_import_batch(self.cur, self.batch_id, config=TEST_CONFIG,
                                 fetch=make_fetcher())
        self.item_id = svc.list_items(self.cur, self.batch_id)[0][0]['id']

    def test_concurrent_article_creation_raises_duplicate_error_not_500(self):
        # Boshqa jarayon xuddi shu DOI bilan maqolani ALLAQACHON yaratgan.
        self.store['articles'].append(
            {'id': 999, 'title': 'Race winner', 'slug': 'race', 'doi': '10.1000/a'})
        with self.assertRaises(svc.DuplicateArticleError) as ctx:
            svc.approve_import_item(self.cur, self.item_id, actor_id=1)
        self.assertEqual(ctx.exception.doi, '10.1000/a')
        # Faqat BITTA maqola qoladi — ikkinchisi yaratilmagan.
        self.assertEqual(len(self.store['articles']), 1)

    def test_resolve_duplicate_after_conflict_marks_item_already_exists(self):
        self.store['articles'].append(
            {'id': 999, 'title': 'Race winner', 'slug': 'race', 'doi': '10.1000/a'})
        try:
            svc.approve_import_item(self.cur, self.item_id, actor_id=1)
        except svc.DuplicateArticleError:
            pass
        svc.resolve_duplicate_after_conflict(self.cur, self.item_id, '10.1000/a',
                                             self.batch_id)
        item = svc.get_item(self.cur, self.item_id)
        self.assertEqual(item['status'], ALREADY_EXISTS)
        self.assertEqual(item['existing_article_id'], 999)


class RejectSkipTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store()
        self.cur = FakeImportCursor(self.store)
        self.batch_id = svc.create_import_batch(
            self.cur, created_by=1, source_type='doi_list', raw_text='10.1000/a',
            config=TEST_CONFIG)
        svc.process_import_batch(self.cur, self.batch_id, config=TEST_CONFIG,
                                 fetch=make_fetcher())
        self.item_id = svc.list_items(self.cur, self.batch_id)[0][0]['id']

    def test_reject_transitions_and_no_article_created(self):
        svc.reject_import_item(self.cur, self.item_id, actor_id=1)
        self.assertEqual(svc.get_item(self.cur, self.item_id)['status'], REJECTED)
        self.assertEqual(self.store['articles'], [])

    def test_skip_transitions(self):
        svc.skip_import_item(self.cur, self.item_id, actor_id=1)
        self.assertEqual(svc.get_item(self.cur, self.item_id)['status'], SKIPPED)


class BulkApproveTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store()
        self.cur = FakeImportCursor(self.store)
        self.batch_id = svc.create_import_batch(
            self.cur, created_by=1, source_type='doi_list',
            raw_text='10.1000/a\n10.1000/b', config=TEST_CONFIG)

    def test_retracted_item_excluded_from_bulk_approve(self):
        # Bitta oddiy, bitta retracted.
        def mixed_fetch(doi, *, config=None, cache_get=None, cache_set=None):
            return make_fetcher(is_retracted=(doi == '10.1000/b'))(doi, config=config)

        svc.process_import_batch(self.cur, self.batch_id, config=TEST_CONFIG,
                                 fetch=mixed_fetch)
        items, _ = svc.list_items(self.cur, self.batch_id)
        ids = [i['id'] for i in items]
        summary = svc.bulk_approve_safe_items(self.cur, self.batch_id, actor_id=1,
                                              item_ids=ids)
        self.assertEqual(len(summary['approved']), 1)
        self.assertEqual(len(summary['skipped_unsafe']), 1)
        self.assertEqual(len(self.store['articles']), 1)


if __name__ == '__main__':
    unittest.main()
