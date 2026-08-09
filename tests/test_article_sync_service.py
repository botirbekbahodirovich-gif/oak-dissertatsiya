# -*- coding: utf-8 -*-
"""Servis qatlami testlari — apply, idempotency, stale-himoya, transaction
(Phase 2C.3A, §27).
"""
import datetime
import unittest

from services.article_sync import persistence, service as svc
from services.article_sync.policy import APPLICABLE_FIELDS
from services.article_metadata.models import (CROSSREF, LOOKUP_OK,
                                              LOOKUP_PARTIAL, OPENALEX,
                                              UNAVAILABLE, UNPAYWALL,
                                              AuthorCandidate,
                                              MetadataLookupResult,
                                              NormalizedArticleMetadata,
                                              ProviderResult)
from tests.fake_article_sync_pg import FakeSyncCursor, new_store


def base_article_row(**overrides):
    row = {
        'id': 1, 'title': 'Old Title', 'slug': 'old-title', 'abstract': None,
        'language': 'en', 'publication_year': 2020, 'publication_date': None,
        'journal_name': 'J', 'volume': None, 'issue': None, 'pages': None,
        'doi': '10.1000/abc', 'external_url': None, 'openalex_id': None,
        'crossref_member_id': None, 'primary_issn': None, 'is_retracted': False,
        'citation_count': 24, 'citation_count_source': 'openalex',
        'citation_count_checked_at': None, 'metadata_source_summary': None,
        'updated_at': 1,
    }
    row.update(overrides)
    return row


def make_fetcher(title='New Title', citation_count=41, is_retracted=False,
                 status=LOOKUP_OK, doi_override=None):
    def fetch(doi, *, config=None, cache_get=None, cache_set=None):
        metadata = NormalizedArticleMetadata(
            doi=doi_override or doi, title=title, citation_count=citation_count,
            citation_count_source='openalex', is_retracted=is_retracted,
            authors=[AuthorCandidate(name='John Smith')])
        providers = {CROSSREF: ProviderResult(provider=CROSSREF, status='ok'),
                    OPENALEX: ProviderResult(provider=OPENALEX, status='ok'),
                    UNPAYWALL: ProviderResult(provider=UNPAYWALL, status='ok')}
        return MetadataLookupResult(doi=doi, status=status, metadata=metadata,
                                    providers=providers, conflicts=[], warnings=[])
    return fetch


class CheckDoesNotMutateArticleTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store(base_article_row())
        self.cur = FakeSyncCursor(self.store)

    def test_check_creates_sync_record_only(self):
        result = svc.check_article_metadata(self.cur, 1, actor_id=1,
                                            fetch=make_fetcher())
        self.assertIsNotNone(result.sync_id)
        self.assertEqual(self.store['articles'][1]['title'], 'Old Title')
        self.assertEqual(self.store['articles'][1]['citation_count'], 24)
        self.assertEqual(len(self.store['syncs']), 1)
        self.assertEqual(self.store['syncs'][result.sync_id]['status'], 'checked')

    def test_check_missing_doi_raises(self):
        self.store['articles'][1]['doi'] = None
        with self.assertRaises(svc.ArticleSyncError) as ctx:
            svc.check_article_metadata(self.cur, 1, actor_id=1, fetch=make_fetcher())
        self.assertEqual(ctx.exception.code, 'missing_doi')

    def test_check_missing_article_raises_not_found(self):
        with self.assertRaises(svc.ArticleSyncError) as ctx:
            svc.check_article_metadata(self.cur, 999, actor_id=1, fetch=make_fetcher())
        self.assertEqual(ctx.exception.code, 'not_found')

    def test_provider_partial_failure_still_produces_diffs(self):
        def partial_fetch(doi, *, config=None, cache_get=None, cache_set=None):
            metadata = NormalizedArticleMetadata(
                doi=doi, title='New Title', citation_count=41,
                citation_count_source='openalex')
            providers = {
                CROSSREF: ProviderResult(provider=CROSSREF, status='ok'),
                OPENALEX: ProviderResult(provider=OPENALEX, status='ok'),
                UNPAYWALL: ProviderResult(provider=UNPAYWALL, status=UNAVAILABLE),
            }
            return MetadataLookupResult(doi=doi, status=LOOKUP_PARTIAL,
                                        metadata=metadata, providers=providers,
                                        conflicts=[], warnings=[])
        result = svc.check_article_metadata(self.cur, 1, actor_id=1,
                                            fetch=partial_fetch)
        self.assertTrue(any(d.field == 'title' for d in result.diffs))
        self.assertEqual(result.provider_summary[UNPAYWALL]['status'], UNAVAILABLE)


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store(base_article_row())
        self.cur = FakeSyncCursor(self.store)
        self.result = svc.check_article_metadata(self.cur, 1, actor_id=1,
                                                  fetch=make_fetcher())

    def test_safe_field_apply(self):
        out = svc.apply_article_metadata_changes(
            self.cur, 1, self.result.sync_id, actor_id=2,
            selected_fields=['citation_count'])
        self.assertEqual(out['applied_fields'], ['citation_count'])
        self.assertEqual(self.store['articles'][1]['citation_count'], 41)
        self.assertEqual(self.store['syncs'][self.result.sync_id]['status'], 'applied')

    def test_unselected_field_unchanged(self):
        svc.apply_article_metadata_changes(
            self.cur, 1, self.result.sync_id, actor_id=2,
            selected_fields=['citation_count'])
        self.assertEqual(self.store['articles'][1]['title'], 'Old Title')

    def test_review_field_requires_explicit_selection(self):
        out = svc.apply_article_metadata_changes(
            self.cur, 1, self.result.sync_id, actor_id=2, selected_fields=['title'])
        self.assertEqual(out['applied_fields'], ['title'])
        self.assertEqual(self.store['articles'][1]['title'], 'New Title')

    def test_no_fields_selected_raises(self):
        with self.assertRaises(svc.ArticleSyncError) as ctx:
            svc.apply_article_metadata_changes(
                self.cur, 1, self.result.sync_id, actor_id=2, selected_fields=[])
        self.assertEqual(ctx.exception.code, 'no_fields_selected')

    def test_non_applicable_field_is_ignored_not_applied(self):
        # 'doi' hech qachon applicable emas — allowlist tashqarisida.
        with self.assertRaises(svc.ArticleSyncError) as ctx:
            svc.apply_article_metadata_changes(
                self.cur, 1, self.result.sync_id, actor_id=2, selected_fields=['doi'])
        self.assertEqual(ctx.exception.code, 'no_fields_selected')

    def test_apply_twice_is_blocked(self):
        svc.apply_article_metadata_changes(
            self.cur, 1, self.result.sync_id, actor_id=2,
            selected_fields=['citation_count'])
        with self.assertRaises(svc.ArticleSyncError) as ctx:
            svc.apply_article_metadata_changes(
                self.cur, 1, self.result.sync_id, actor_id=2,
                selected_fields=['title'])
        self.assertEqual(ctx.exception.code, 'already_applied')
        # Ikkinchi urinish hech narsa yozmagan.
        self.assertEqual(self.store['articles'][1]['title'], 'Old Title')

    def test_stale_sync_is_blocked(self):
        # Boshqa admin orada maqolani tahrirlaydi (updated_at o'zgaradi).
        self.cur.execute("UPDATE scientific_articles SET title = %s WHERE id = %s",
                         ('Manually edited', 1))
        with self.assertRaises(svc.ArticleSyncError) as ctx:
            svc.apply_article_metadata_changes(
                self.cur, 1, self.result.sync_id, actor_id=2,
                selected_fields=['citation_count'])
        self.assertEqual(ctx.exception.code, 'stale')
        self.assertEqual(self.store['articles'][1]['citation_count'], 24)

    def test_wrong_article_id_for_sync_raises_not_found(self):
        self.store['articles'][2] = base_article_row(id=2, doi='10.1000/other')
        with self.assertRaises(svc.ArticleSyncError) as ctx:
            svc.apply_article_metadata_changes(
                self.cur, 2, self.result.sync_id, actor_id=2,
                selected_fields=['citation_count'])
        self.assertEqual(ctx.exception.code, 'not_found')


class RetractionApplyTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store(base_article_row(is_retracted=False))
        self.cur = FakeSyncCursor(self.store)
        self.result = svc.check_article_metadata(
            self.cur, 1, actor_id=1, fetch=make_fetcher(is_retracted=True))

    def test_without_confirmation_blocked(self):
        with self.assertRaises(svc.ArticleSyncError) as ctx:
            svc.apply_article_metadata_changes(
                self.cur, 1, self.result.sync_id, actor_id=2,
                selected_fields=['is_retracted'])
        self.assertEqual(ctx.exception.code, 'retraction_confirmation_required')
        self.assertFalse(self.store['articles'][1]['is_retracted'])

    def test_with_confirmation_succeeds(self):
        svc.apply_article_metadata_changes(
            self.cur, 1, self.result.sync_id, actor_id=2,
            selected_fields=['is_retracted'], confirm_retraction=True)
        self.assertTrue(self.store['articles'][1]['is_retracted'])


class DoiMismatchApplyTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store(base_article_row(doi='10.1000/abc'))
        self.cur = FakeSyncCursor(self.store)
        self.result = svc.check_article_metadata(
            self.cur, 1, actor_id=1,
            fetch=make_fetcher(doi_override='10.9999/other'))

    def test_without_confirmation_blocked(self):
        with self.assertRaises(svc.ArticleSyncError) as ctx:
            svc.apply_article_metadata_changes(
                self.cur, 1, self.result.sync_id, actor_id=2,
                selected_fields=['citation_count'])
        self.assertEqual(ctx.exception.code, 'doi_mismatch_confirmation_required')

    def test_with_confirmation_allows_other_fields(self):
        svc.apply_article_metadata_changes(
            self.cur, 1, self.result.sync_id, actor_id=2,
            selected_fields=['citation_count'], confirm_doi_mismatch=True)
        self.assertEqual(self.store['articles'][1]['citation_count'], 41)
        # DOI o'zi HECH QACHON o'zgarmaydi.
        self.assertEqual(self.store['articles'][1]['doi'], '10.1000/abc')


class AuthorsApplyTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store(base_article_row())
        self.store['authors'][1] = [{'author_name': 'Old Author', 'author_order': 1,
                                     'orcid': None, 'affiliation': None,
                                     'linked_user_id': None,
                                     'linked_scientist_id': None,
                                     'is_corresponding': False,
                                     'openalex_author_id': None}]
        self.cur = FakeSyncCursor(self.store)
        fetch = make_fetcher()
        # `make_fetcher` "John Smith" beradi — 'Old Author'dan farq qiladi.
        self.result = svc.check_article_metadata(self.cur, 1, actor_id=1, fetch=fetch)

    def test_authors_replaced_atomically(self):
        self.assertTrue(any(d.field == 'authors' for d in self.result.diffs))
        svc.apply_article_metadata_changes(
            self.cur, 1, self.result.sync_id, actor_id=2, selected_fields=['authors'])
        names = [a['author_name'] for a in self.store['authors'][1]]
        self.assertEqual(names, ['John Smith'])

    def test_authors_not_selected_stays_unchanged(self):
        svc.apply_article_metadata_changes(
            self.cur, 1, self.result.sync_id, actor_id=2,
            selected_fields=['citation_count'])
        names = [a['author_name'] for a in self.store['authors'][1]]
        self.assertEqual(names, ['Old Author'])


class NoAutoOverwritePolicyTests(unittest.TestCase):
    """§27 — OA/litsenziya/candidate PDF hech qachon applicable emas."""

    def test_oa_status_not_in_applicable_fields(self):
        self.assertNotIn('oa_status', APPLICABLE_FIELDS)

    def test_license_candidates_not_in_applicable_fields(self):
        self.assertNotIn('license_candidates', APPLICABLE_FIELDS)

    def test_candidate_pdf_url_not_in_applicable_fields(self):
        self.assertNotIn('candidate_pdf_url', APPLICABLE_FIELDS)

    def test_doi_not_in_applicable_fields(self):
        self.assertNotIn('doi', APPLICABLE_FIELDS)

    def test_access_type_never_referenced_by_apply(self):
        import inspect
        source = inspect.getsource(persistence.apply_field_changes)
        self.assertNotIn('access_type', source)
        self.assertNotIn('pdf_path', source)
        self.assertNotIn('license_type', source)


if __name__ == '__main__':
    unittest.main()
