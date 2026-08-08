# -*- coding: utf-8 -*-
"""Diff engine testlari (Phase 2C.3A, §26)."""
import datetime
import unittest

from services.article_sync.diff import compare_article_metadata
from services.article_sync.models import CRITICAL, REVIEW, SAFE
from services.article_metadata.models import (AuthorCandidate, ExternalLocation,
                                              LicenseCandidate,
                                              NormalizedArticleMetadata)


def base_article(**overrides):
    row = {
        'id': 1, 'title': 'AI in Education', 'slug': 'ai-in-education',
        'abstract': 'Annotatsiya.', 'language': 'en', 'publication_year': 2020,
        'publication_date': datetime.date(2020, 1, 1), 'journal_name': 'J',
        'volume': '1', 'issue': '2', 'pages': '10-20', 'doi': '10.1000/abc',
        'external_url': None, 'openalex_id': None, 'crossref_member_id': None,
        'primary_issn': None, 'is_retracted': False, 'citation_count': 24,
        'citation_count_source': 'openalex', 'citation_count_checked_at': None,
        'metadata_source_summary': None,
        'authors': [{'author_name': 'John Smith', 'orcid': None,
                    'affiliation': None, 'openalex_author_id': None}],
        'updated_at': datetime.datetime(2026, 1, 1),
    }
    row.update(overrides)
    return row


def base_fresh(**overrides):
    kwargs = dict(
        doi='10.1000/abc', title='AI in Education', journal_name='J',
        publication_year=2020, publication_date=datetime.date(2020, 1, 1),
        volume='1', issue='2', pages='10-20', language='en',
        citation_count=24, citation_count_source='openalex', is_retracted=False,
        authors=[AuthorCandidate(name='John Smith')])
    kwargs.update(overrides)
    return NormalizedArticleMetadata(**kwargs)


class NoChangeTests(unittest.TestCase):
    def test_identical_metadata_yields_no_diffs(self):
        diffs, mismatch = compare_article_metadata(base_article(), base_fresh())
        self.assertEqual(diffs, [])
        self.assertFalse(mismatch)


class CitationCountTests(unittest.TestCase):
    def test_increase_is_safe(self):
        diffs, _ = compare_article_metadata(
            base_article(citation_count=24), base_fresh(citation_count=41))
        d = next(d for d in diffs if d.field == 'citation_count')
        self.assertEqual(d.severity, SAFE)
        self.assertTrue(d.default_selected)

    def test_decrease_is_review_not_blind_overwrite(self):
        diffs, _ = compare_article_metadata(
            base_article(citation_count=41), base_fresh(citation_count=36))
        d = next(d for d in diffs if d.field == 'citation_count')
        self.assertEqual(d.severity, REVIEW)
        self.assertFalse(d.default_selected)

    def test_no_fresh_citation_count_is_not_a_diff(self):
        diffs, _ = compare_article_metadata(
            base_article(citation_count=24), base_fresh(citation_count=None))
        self.assertFalse(any(d.field == 'citation_count' for d in diffs))


class TitleTests(unittest.TestCase):
    def test_whitespace_only_difference_is_not_a_diff(self):
        diffs, _ = compare_article_metadata(
            base_article(title='AI  in   Education'),
            base_fresh(title='AI in Education'))
        self.assertFalse(any(d.field == 'title' for d in diffs))

    def test_real_difference_is_review(self):
        diffs, _ = compare_article_metadata(
            base_article(title='AI in Education'),
            base_fresh(title='Artificial Intelligence in Education'))
        d = next(d for d in diffs if d.field == 'title')
        self.assertEqual(d.severity, REVIEW)
        self.assertFalse(d.default_selected)


class AuthorDiffTests(unittest.TestCase):
    def test_orcid_enrichment_is_review(self):
        diffs, _ = compare_article_metadata(
            base_article(authors=[{'author_name': 'John Smith', 'orcid': None,
                                   'affiliation': None, 'openalex_author_id': None}]),
            base_fresh(authors=[AuthorCandidate(name='John Smith', orcid='0000-1')]))
        d = next(d for d in diffs if d.field == 'authors')
        self.assertEqual(d.severity, REVIEW)
        self.assertEqual(d.new_value[0]['orcid'], '0000-1')

    def test_author_list_change_is_review(self):
        diffs, _ = compare_article_metadata(
            base_article(),
            base_fresh(authors=[AuthorCandidate(name='Jane Doe')]))
        d = next(d for d in diffs if d.field == 'authors')
        self.assertEqual(d.severity, REVIEW)

    def test_identical_authors_no_diff(self):
        diffs, _ = compare_article_metadata(base_article(), base_fresh())
        self.assertFalse(any(d.field == 'authors' for d in diffs))


class InformationalDiffTests(unittest.TestCase):
    def test_oa_status_change_is_informational_not_applicable(self):
        diffs, _ = compare_article_metadata(base_article(), base_fresh(oa_status='gold'))
        d = next(d for d in diffs if d.field == 'oa_status')
        self.assertFalse(d.applicable)

    def test_licence_change_is_informational_not_applicable(self):
        diffs, _ = compare_article_metadata(
            base_article(),
            base_fresh(license_candidates=[LicenseCandidate(source='crossref',
                                                             license_type='cc_by')]))
        d = next(d for d in diffs if d.field == 'license_candidates')
        self.assertFalse(d.applicable)

    def test_candidate_pdf_change_is_informational_not_applicable(self):
        diffs, _ = compare_article_metadata(
            base_article(),
            base_fresh(external_locations=[
                ExternalLocation(source='unpaywall', pdf_url='https://x/y.pdf')]))
        d = next(d for d in diffs if d.field == 'candidate_pdf_url')
        self.assertFalse(d.applicable)
        self.assertEqual(d.new_value, 'https://x/y.pdf')


class RetractionTests(unittest.TestCase):
    def test_false_to_true_is_critical(self):
        diffs, _ = compare_article_metadata(
            base_article(is_retracted=False), base_fresh(is_retracted=True))
        d = next(d for d in diffs if d.field == 'is_retracted')
        self.assertEqual(d.severity, CRITICAL)
        self.assertFalse(d.default_selected)

    def test_true_to_false_is_review_not_silently_applied(self):
        diffs, _ = compare_article_metadata(
            base_article(is_retracted=True), base_fresh(is_retracted=False))
        d = next(d for d in diffs if d.field == 'is_retracted')
        self.assertEqual(d.severity, REVIEW)
        self.assertFalse(d.default_selected)

    def test_no_change_no_diff(self):
        diffs, _ = compare_article_metadata(
            base_article(is_retracted=True), base_fresh(is_retracted=True))
        self.assertFalse(any(d.field == 'is_retracted' for d in diffs))


class DoiMismatchTests(unittest.TestCase):
    def test_mismatch_flagged_as_critical_and_not_applicable(self):
        diffs, mismatch = compare_article_metadata(
            base_article(doi='10.1000/abc'), base_fresh(doi='10.9999/other'))
        self.assertTrue(mismatch)
        d = next(d for d in diffs if d.field == 'doi')
        self.assertEqual(d.severity, CRITICAL)
        self.assertFalse(d.applicable)

    def test_matching_doi_no_mismatch(self):
        _diffs, mismatch = compare_article_metadata(base_article(), base_fresh())
        self.assertFalse(mismatch)


class ExternalIdTests(unittest.TestCase):
    def test_empty_slot_filled_is_safe(self):
        diffs, _ = compare_article_metadata(
            base_article(openalex_id=None), base_fresh(openalex_id='W123'))
        d = next(d for d in diffs if d.field == 'openalex_id')
        self.assertEqual(d.severity, SAFE)
        self.assertTrue(d.default_selected)

    def test_conflicting_value_is_review(self):
        diffs, _ = compare_article_metadata(
            base_article(openalex_id='W1'), base_fresh(openalex_id='W2'))
        d = next(d for d in diffs if d.field == 'openalex_id')
        self.assertEqual(d.severity, REVIEW)
        self.assertFalse(d.default_selected)


if __name__ == '__main__':
    unittest.main()
