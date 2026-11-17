# -*- coding: utf-8 -*-
"""O'zbekiston maqolalari ingestion'i — DB'siz birlik testlari.

Qamrov: UZ/core filtr parametrlari, CLI cursor pagination limiti, OpenAlex ->
OAK xaritasi (high/low/yo'q), litsenziya kelishuvi (Crossref VOR, ikki
manba, ziddiyat), ND litsenziyalar, can_show_abstract, publish_blockers,
PDF olish (ruxsat etilmagan litsenziya, 403, hajm, magic bytes, captcha).
"""
import json
import os
import tempfile
import unittest
from datetime import date
from unittest import mock

from services.article_discovery import openalex
from services.article_discovery.config import ArticleDiscoveryConfig
from services.article_discovery.models import (ArticleDiscoveryQuery,
                                                DiscoveryValidationError)
from services.article_discovery.saved_queries import to_query
from services.article_discovery.service import validate_query
from services.article_metadata.http import HttpOutcome
from services.article_metadata.models import OK
from services import article_pdf_fetch as pf
from utils import article_rules as rules
from utils.article_license import evidence_note, resolve_license
from utils.article_publish_rules import (BLOCKER_AUTHORS, BLOCKER_DOI,
                                         BLOCKER_FIELD, BLOCKER_JOURNAL,
                                         BLOCKER_RETRACTED, publish_blockers,
                                         review_blockers)
from utils.oak_mapping import load_mapping, suggest_oak_field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = ArticleDiscoveryConfig(openalex_api_key='TEST-KEY')


def _work(n):
    return {'id': 'https://openalex.org/W%d' % n, 'doi': 'https://doi.org/10.5555/uz.%d' % n,
            'title': 'Work %d' % n, 'publication_date': '2024-05-01',
            'primary_location': {'source': {'display_name': 'J'}}}


# ── 1. UZ / core filtrlari ──────────────────────────────────────────────────

class ScopeFilterTests(unittest.TestCase):
    def test_build_filter_adds_country_and_core(self):
        value = openalex.build_filter(date(2024, 1, 1), date(2024, 12, 31),
                                      country_code='uz', core_only=True)
        self.assertIn('authorships.institutions.country_code:UZ', value)
        self.assertIn('primary_location.source.is_core:true', value)
        self.assertIn('has_doi:true', value)
        self.assertIn('type:article', value)

    def test_build_filter_without_scope_is_unchanged(self):
        value = openalex.build_filter(date(2024, 1, 1), date(2024, 2, 1))
        self.assertNotIn('country_code', value)
        self.assertNotIn('is_core', value)

    def test_bad_country_code_is_rejected(self):
        with self.assertRaises(ValueError):
            openalex.build_filter(date(2024, 1, 1), date(2024, 2, 1), country_code='UZB')

    def test_search_without_keyword_sends_no_search_param(self):
        captured = {}

        def fake_fetch(url, **kwargs):
            captured.update(kwargs['params'])
            return HttpOutcome(status=OK, payload={'results': [], 'meta': {'count': 0}},
                               http_status=200)
        query = ArticleDiscoveryQuery(keyword='', from_date=date(2024, 1, 1),
                                      to_date=date(2024, 3, 1), oak_field_id=None,
                                      limit=10, country_code='UZ', core_only=True)
        with mock.patch.object(openalex, 'fetch_json', fake_fetch):
            result = openalex.search(query, CFG)
        self.assertTrue(result.ok)
        self.assertNotIn('search', captured)
        self.assertIn('authorships.institutions.country_code:UZ', captured['filter'])
        self.assertEqual(captured['page'], '1')         # admin UI — bitta sahifa (§33)

    def test_keyword_optional_only_with_scope(self):
        with self.assertRaises(DiscoveryValidationError):
            validate_query({'keyword': '', 'providers': ['openalex']}, CFG)
        query = validate_query({'keyword': '', 'uz_only': '1', 'core_only': 'on',
                                'providers': ['openalex']}, CFG)
        self.assertEqual(query.country_code, 'UZ')
        self.assertTrue(query.core_only)
        self.assertEqual(query.canonical()['country_code'], 'UZ')
        self.assertTrue(query.canonical()['core_only'])

    def test_short_keyword_still_rejected_with_scope(self):
        with self.assertRaises(DiscoveryValidationError):
            validate_query({'keyword': 'ab', 'uz_only': '1', 'providers': ['openalex']}, CFG)

    def test_scope_with_crossref_is_rejected(self):
        with self.assertRaises(DiscoveryValidationError) as ctx:
            validate_query({'keyword': 'education', 'uz_only': '1',
                            'providers': ['openalex', 'crossref']}, CFG)
        self.assertEqual(ctx.exception.code, 'scope_requires_openalex')

    def test_canonical_without_scope_keeps_old_shape(self):
        query = validate_query({'keyword': 'education', 'providers': ['openalex']}, CFG)
        self.assertNotIn('country_code', query.canonical())
        self.assertNotIn('core_only', query.canonical())

    def test_saved_query_row_round_trips_scope(self):
        row = {'keyword': '', 'from_date': date(2024, 1, 1), 'to_date': date(2024, 6, 1),
               'oak_field_id': None, 'result_limit': 100, 'providers': '["openalex"]',
               'country_code': 'UZ', 'core_only': True}
        query = to_query(row)
        self.assertEqual(query.country_code, 'UZ')
        self.assertTrue(query.core_only)


# ── 2. Cursor pagination ────────────────────────────────────────────────────

class CursorPaginationTests(unittest.TestCase):
    def _fake_fetch(self, total_pages):
        calls = []

        def fetch(url, **kwargs):
            params = kwargs['params']
            calls.append(dict(params))
            index = len(calls)
            per_page = int(params['per_page'])
            start = (index - 1) * 200
            results = [_work(start + i) for i in range(per_page)]
            next_cursor = 'c%d' % index if index < total_pages else None
            return HttpOutcome(status=OK, payload={
                'results': results, 'meta': {'count': 99999, 'next_cursor': next_cursor}},
                http_status=200)
        return fetch, calls

    def test_limit_is_a_strict_total_cap(self):
        fetch, calls = self._fake_fetch(total_pages=100)
        pages = list(openalex.iter_cursor(CFG, from_date=date(2024, 1, 1),
                                          to_date=date(2024, 12, 31), limit=450,
                                          country_code='UZ', core_only=True, fetch=fetch))
        got = sum(len(p.candidates) for p, _ in pages)
        self.assertEqual(got, 450)
        self.assertEqual([c['per_page'] for c in calls], ['200', '200', '50'])
        self.assertEqual(calls[0]['cursor'], '*')
        self.assertEqual(calls[1]['cursor'], 'c1')
        self.assertNotIn('page', calls[0])

    def test_stops_when_provider_has_no_next_cursor(self):
        fetch, calls = self._fake_fetch(total_pages=2)
        pages = list(openalex.iter_cursor(CFG, from_date=date(2024, 1, 1),
                                          to_date=date(2024, 12, 31), limit=10000,
                                          fetch=fetch))
        self.assertEqual(len(calls), 2)
        self.assertEqual(sum(len(p.candidates) for p, _ in pages), 400)

    def test_limit_is_mandatory(self):
        with self.assertRaises(ValueError):
            list(openalex.iter_cursor(CFG, from_date=date(2024, 1, 1),
                                      to_date=date(2024, 2, 1), limit=0))

    def test_per_page_never_exceeds_200(self):
        fetch, calls = self._fake_fetch(total_pages=1)
        list(openalex.iter_cursor(CFG, from_date=date(2024, 1, 1), to_date=date(2024, 2, 1),
                                  limit=5000, per_page=1000, fetch=fetch))
        self.assertEqual(calls[0]['per_page'], '200')

    def test_error_page_ends_iteration(self):
        def fetch(url, **kwargs):
            return HttpOutcome(status='rate_limited', http_status=429)
        pages = list(openalex.iter_cursor(CFG, from_date=date(2024, 1, 1),
                                          to_date=date(2024, 2, 1), limit=10, fetch=fetch))
        self.assertEqual(len(pages), 1)
        self.assertFalse(pages[0][0].ok)


# ── 3. OpenAlex -> OAK xaritasi ─────────────────────────────────────────────

def _meta_with_topic(subfield_id=None, field_id=None, primary=True):
    return {'topics': [{'name': 'T', 'is_primary': primary,
                        'subfield_id': subfield_id, 'field_id': field_id}]}


class OakMappingTests(unittest.TestCase):
    def test_high_confidence_subfield(self):
        result = suggest_oak_field(_meta_with_topic('2730', '27'))     # Oncology
        self.assertEqual((result['oak_code'], result['confidence']), ('14.00.14', 'high'))
        self.assertEqual(result['basis'], 'subfield')

    def test_low_confidence_subfield(self):
        result = suggest_oak_field(_meta_with_topic('2303', '23'))     # Ecology (env.)
        self.assertEqual(result['confidence'], 'low')

    def test_unmapped_subfield_is_left_empty(self):
        result = suggest_oak_field(_meta_with_topic('3300', '33'))     # General Social Sci
        self.assertIsNone(result['oak_code'])
        self.assertIsNone(result['confidence'])

    def test_field_fallback_when_no_subfield(self):
        result = suggest_oak_field(_meta_with_topic(None, '26'))       # Mathematics
        self.assertEqual((result['oak_code'], result['basis']), ('01.01.00', 'field'))

    def test_no_primary_topic(self):
        self.assertIsNone(suggest_oak_field(_meta_with_topic('2730', '27', primary=False))
                          ['oak_code'])
        self.assertIsNone(suggest_oak_field({})['oak_code'])

    def test_every_mapped_code_exists_in_the_oak_taxonomy(self):
        codes = set()
        with open(os.path.join(ROOT, 'data', 'scientific_fields.json'), encoding='utf-8') as fh:
            codes |= {f['code'] for f in json.load(fh)['fields']}
        with open(os.path.join(ROOT, 'data', 'oak', 'normalized',
                               'oak_specialties_approved.json'), encoding='utf-8') as fh:
            for rec in json.load(fh)['records']:
                codes.add(rec['code'])
                codes.add(rec['parent_code'])
        mapping = load_mapping()
        for section in ('subfields', 'fields'):
            for key, row in mapping[section].items():
                if row['oak_code']:
                    self.assertIn(row['oak_code'], codes, '%s %s' % (section, key))
                    self.assertIn(row['confidence'], ('high', 'low'))
                else:
                    self.assertIsNone(row['confidence'])

    def test_every_openalex_subfield_has_a_row(self):
        self.assertEqual(len(load_mapping()['subfields']), 252)


# ── 4. Litsenziya kelishuvi ──────────────────────────────────────────────────

def _lic(source, license_type, applies_to=None, start=None, label=None):
    return {'source': source, 'license_type': license_type, 'applies_to': applies_to,
            'start_date': start, 'label': label or license_type, 'url': None}


class LicenseResolutionTests(unittest.TestCase):
    def meta(self, *candidates, pub='2024-03-15'):
        return {'publication_date': pub, 'license_candidates': list(candidates)}

    def test_crossref_vor_deposited_by_publisher_is_enough(self):
        result = resolve_license(self.meta(_lic('crossref', 'cc_by', 'vor', '2024-03-01')))
        self.assertEqual((result['status'], result['license_type'], result['basis']),
                         ('resolved', 'cc_by', 'crossref_vor'))
        self.assertEqual(result['license_url'], rules.LICENSE_URLS['cc_by'])

    def test_crossref_vor_starting_after_publication_needs_openalex_or_unpaywall(self):
        late = _lic('crossref', 'cc_by', 'vor', '2024-09-01')
        result = resolve_license(self.meta(late), today=date(2025, 1, 1))
        self.assertEqual(result['license_type'], 'unknown')
        for oa in ('openalex', 'unpaywall'):
            result = resolve_license(self.meta(late, _lic(oa, 'cc_by')), today=date(2025, 1, 1))
            self.assertEqual((result['license_type'], result['basis']),
                             ('cc_by', 'crossref_and_oa'), oa)

    def test_crossref_vor_without_start_date_plus_unpaywall(self):
        result = resolve_license(self.meta(_lic('crossref', 'cc_by_sa', 'vor'),
                                           _lic('unpaywall', 'cc_by_sa')))
        self.assertEqual((result['status'], result['basis']), ('resolved', 'crossref_and_oa'))

    def test_openalex_and_unpaywall_count_as_one_source(self):
        # Ikkalasi ham OurResearch ma'lumoti — mustaqil tasdiq emas.
        result = resolve_license(self.meta(_lic('openalex', 'cc_by_sa'),
                                           _lic('unpaywall', 'cc_by_sa')))
        self.assertEqual((result['status'], result['license_type'], result['basis']),
                         ('insufficient', 'unknown', None))
        self.assertIn('bitta manba', result['reason'])

    def test_crossref_non_vor_does_not_join_openalex(self):
        result = resolve_license(self.meta(_lic('crossref', 'cc_by', 'am', '2024-01-01'),
                                           _lic('openalex', 'cc_by')))
        self.assertEqual(result['license_type'], 'unknown')

    def test_evidence_note_names_the_new_basis(self):
        late = _lic('crossref', 'cc_by', 'vor', '2024-09-01')
        note = evidence_note(resolve_license(self.meta(late, _lic('openalex', 'cc_by')),
                                             today=date(2025, 1, 1)))
        self.assertIn('Crossref VOR + OpenAlex/Unpaywall', note)

    def test_single_non_crossref_source_is_insufficient(self):
        result = resolve_license(self.meta(_lic('unpaywall', 'cc_by')))
        self.assertEqual((result['status'], result['license_type']), ('insufficient', 'unknown'))

    def test_conflict_keeps_unknown(self):
        result = resolve_license(self.meta(_lic('crossref', 'cc_by', 'vor', '2024-03-01'),
                                           _lic('unpaywall', 'cc_by_nc')))
        self.assertEqual((result['status'], result['license_type']), ('conflict', 'unknown'))

    def test_unrecognized_label_is_a_conflict_not_a_pass(self):
        result = resolve_license(self.meta(
            _lic('crossref', 'cc_by', 'vor', '2024-03-01'),
            _lic('openalex', None, label='publisher-specific-oa')))
        self.assertEqual(result['status'], 'conflict')

    def test_crossref_tdm_and_am_licences_are_ignored(self):
        result = resolve_license(self.meta(_lic('crossref', 'cc_by', 'tdm', '2024-01-01'),
                                           _lic('crossref', 'cc_by', 'am', '2024-01-01')))
        self.assertEqual(result['license_type'], 'unknown')

    def test_no_derivatives_resolves_to_its_own_key(self):
        result = resolve_license(self.meta(_lic('crossref', 'cc_by_nd', 'vor', '2024-01-01')))
        self.assertEqual(result['license_type'], 'cc_by_nd')

    def test_year_only_publication_date(self):
        result = resolve_license({'publication_year': 2024, 'license_candidates': [
            _lic('crossref', 'cc0', 'vor', '2024-06-01')]})
        self.assertEqual(result['license_type'], 'cc0')

    def test_evidence_lists_every_source(self):
        result = resolve_license(self.meta(_lic('openalex', 'cc_by'), _lic('unpaywall', 'cc_by')))
        self.assertEqual({e['source'] for e in result['evidence']}, {'openalex', 'unpaywall'})


# ── 5. ND litsenziyalar, PDF litsenziya ro'yxati, annotatsiya ─────────────────

class LicenseRulesTests(unittest.TestCase):
    def test_nd_licences_are_known_with_canonical_urls(self):
        for key in ('cc_by_nd', 'cc_by_nc_nd'):
            self.assertIn(key, rules.LICENSES)
            self.assertTrue(rules.LICENSE_URLS[key].startswith('https://creativecommons.org/'))

    def test_auto_pdf_default_excludes_nc(self):
        allowed = rules.auto_pdf_licenses({})
        self.assertEqual(allowed, {'cc_by', 'cc_by_sa', 'cc_by_nd', 'cc0', 'public_domain'})
        for nc in ('cc_by_nc', 'cc_by_nc_sa', 'cc_by_nc_nd'):
            self.assertNotIn(nc, allowed)

    def test_auto_pdf_env_override_ignores_unknown_keys(self):
        allowed = rules.auto_pdf_licenses({'ARTICLE_AUTO_PDF_LICENSES': 'cc_by, unknown, other'})
        self.assertEqual(allowed, {'cc_by'})
        self.assertEqual(rules.auto_pdf_licenses({'ARTICLE_AUTO_PDF_LICENSES': 'bogus'}),
                         rules.DEFAULT_AUTO_PDF_LICENSES)

    def test_can_show_abstract_only_for_open_licences(self):
        for key in ('cc_by', 'cc_by_nc_nd', 'cc0', 'public_domain'):
            self.assertTrue(rules.can_show_abstract({'license_type': key}), key)
        for key in ('unknown', None, 'author_permission', 'repository_permission', 'other'):
            self.assertFalse(rules.can_show_abstract({'license_type': key}), key)

    def test_nd_metadata_only_is_a_valid_rights_combination(self):
        self.assertTrue(rules.validate_article_rights('metadata_only', 'cc_by_nd', False, False))


# ── 6. Nashr blokerlari ──────────────────────────────────────────────────────

def _ready_article(**overrides):
    row = {'id': 1, 'doi': '10.5555/ok.1', 'title': 'T', 'journal_name': 'J',
           'publication_year': 2024, 'authors': [{'author_name': 'A'}],
           'primary_field': {'id': 3}, 'is_retracted': False,
           'access_type': 'metadata_only', 'license_type': 'unknown', 'pdf_path': None,
           'publication_status': 'draft', 'verification_status': 'pending_review'}
    row.update(overrides)
    return row


class PublishBlockerTests(unittest.TestCase):
    def test_complete_article_has_no_blockers(self):
        self.assertEqual(publish_blockers(_ready_article()), [])

    def test_each_missing_piece_is_reported(self):
        cases = {
            BLOCKER_DOI: {'doi': 'not-a-doi'},
            BLOCKER_RETRACTED: {'is_retracted': True},
            BLOCKER_AUTHORS: {'authors': []},
            BLOCKER_JOURNAL: {'journal_name': ' '},
            BLOCKER_FIELD: {'primary_field': None},
        }
        for blocker, override in cases.items():
            self.assertIn(blocker, publish_blockers(_ready_article(**override)), blocker)

    def test_critical_conflict_and_duplicate_doi(self):
        blockers = publish_blockers(_ready_article(has_critical_conflict=True,
                                                   doi_duplicate=True))
        self.assertEqual(len(blockers), 2)

    def test_pdf_without_rights_confirmation_is_blocked(self):
        blockers = publish_blockers(_ready_article(
            pdf_path='2026/11/x.pdf', access_type='open_access', license_type='cc_by'))
        self.assertTrue(any(b.startswith('Huquq:') for b in blockers))
        self.assertEqual(publish_blockers(_ready_article(
            pdf_path='2026/11/x.pdf', access_type='open_access', license_type='cc_by',
            rights_confirmed_at='2026-11-16')), [])

    def test_pdf_with_metadata_only_access_is_blocked(self):
        blockers = publish_blockers(_ready_article(pdf_path='x.pdf',
                                                   rights_confirmed_at='2026-11-16'))
        self.assertTrue(any(b.startswith('Huquq:') for b in blockers))

    def test_review_blockers_are_field_and_author_only(self):
        self.assertEqual(review_blockers(_ready_article(doi=None)), [])
        self.assertEqual(len(review_blockers(_ready_article(authors=[], primary_field=None))), 2)


# ── 7. PDF olish ─────────────────────────────────────────────────────────────

PDF_BYTES = b'%PDF-1.7\n' + b'x' * 2000 + b'\n%%EOF\n'


class _Resp:
    def __init__(self, status=200, body=PDF_BYTES, headers=None):
        self.status_code = status
        self._body = body
        self.headers = {'Content-Type': 'application/pdf'}
        self.headers.update(headers or {})
        self.closed = False

    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def close(self):
        self.closed = True


class _Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def _public_resolver(host, port):
    return [(None, None, None, None, ('93.184.216.34', port))]


def _no_sleep(_seconds):
    return None


class _FakeCur:
    def __init__(self):
        self.sql = []
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.sql.append((' '.join(sql.split()), params))

    def fetchone(self):
        return None


def _pdf_article(**overrides):
    row = {'id': 7, 'title': 'T', 'doi': '10.5555/x', 'journal_name': 'Jurnal',
           'license_type': 'cc_by', 'pdf_path': None, 'is_retracted': False,
           'publication_status': 'draft', 'verification_status': 'pending_review',
           'ingest_evidence': {'license': {'basis': 'crossref_vor', 'evidence': []}},
           'metadata_candidate': {'external_locations': [
               {'source': 'openalex', 'pdf_url': 'https://journal.example/a.pdf',
                'landing_page_url': 'https://journal.example/a', 'version': 'publishedVersion',
                'license': 'cc-by', 'is_best': True}]}}
    row.update(overrides)
    return row


class PdfFetchTests(unittest.TestCase):
    def setUp(self):
        self.config = pf.PdfFetchConfig(contact_email='ops@example.org', min_free_bytes=0)
        self.throttle = pf.HostThrottle(0, sleep=_no_sleep)

    def test_nc_licence_is_never_downloaded(self):
        session = _Session()
        status, reason, _ = pf.fetch_one(
            _FakeCur(), _pdf_article(license_type='cc_by_nc'), config=self.config,
            session=session, throttle=self.throttle, storage_dir='/nonexistent',
            admin_id=1, execute=True, resolver=_public_resolver, sleep=_no_sleep)
        self.assertEqual((status, reason), ('skipped', 'license_not_allowed'))
        self.assertEqual(session.calls, [])

    def test_location_with_a_different_licence_is_not_used(self):
        article = _pdf_article()
        article['metadata_candidate']['external_locations'][0]['license'] = 'cc-by-nc'
        location, reason = pf.select_pdf_location(article, article['metadata_candidate'],
                                                  self.config)
        self.assertIsNone(location)
        self.assertEqual(reason, 'no_matching_location')

    def test_version_mapping(self):
        article = _pdf_article()
        article['metadata_candidate']['external_locations'][0]['version'] = 'acceptedVersion'
        location, _ = pf.select_pdf_location(article, article['metadata_candidate'], self.config)
        self.assertEqual(location['version_type'], 'accepted_manuscript')

    def test_403_is_skipped_without_retry_and_host_blocked(self):
        session = _Session(_Resp(403, b'Forbidden'))
        with self.assertRaises(pf.FetchSkip) as ctx:
            pf.download_pdf('https://journal.example/a.pdf', session, self.config,
                            self.throttle, resolver=_public_resolver, sleep=_no_sleep)
        self.assertEqual(ctx.exception.reason, 'http_403')
        self.assertEqual(len(session.calls), 1)
        with self.assertRaises(pf.FetchSkip) as ctx:
            pf.download_pdf('https://journal.example/b.pdf', _Session(), self.config,
                            self.throttle, resolver=_public_resolver, sleep=_no_sleep)
        self.assertEqual(ctx.exception.reason, 'host_blocked')

    def test_429_is_skipped(self):
        with self.assertRaises(pf.FetchSkip) as ctx:
            pf.download_pdf('https://j.example/a.pdf', _Session(_Resp(429, b'')), self.config,
                            self.throttle, resolver=_public_resolver, sleep=_no_sleep)
        self.assertEqual(ctx.exception.reason, 'http_429')

    def test_5xx_is_retried_twice(self):
        session = _Session(_Resp(503, b''), _Resp(503, b''), _Resp(200))
        data, _ctype, _url = pf.download_pdf('https://j.example/a.pdf', session, self.config,
                                             self.throttle, resolver=_public_resolver,
                                             sleep=_no_sleep)
        self.assertTrue(data.startswith(b'%PDF-'))
        self.assertEqual(len(session.calls), 3)

    def test_cloudflare_page_is_not_bypassed(self):
        page = b'<!DOCTYPE html><title>Just a moment...</title> cf-browser-verification'
        with self.assertRaises(pf.FetchSkip) as ctx:
            pf.download_pdf('https://j.example/a.pdf',
                            _Session(_Resp(200, page, {'Content-Type': 'text/html'})),
                            self.config, self.throttle, resolver=_public_resolver,
                            sleep=_no_sleep)
        self.assertEqual(ctx.exception.reason, 'challenge_page')

    def test_size_limit(self):
        config = pf.PdfFetchConfig(max_bytes=1000, min_free_bytes=0)
        with self.assertRaises(pf.FetchSkip) as ctx:
            pf.download_pdf('https://j.example/a.pdf', _Session(_Resp(200)), config,
                            self.throttle, resolver=_public_resolver, sleep=_no_sleep)
        self.assertEqual(ctx.exception.reason, 'too_large')
        with self.assertRaises(pf.FetchSkip) as ctx:
            pf.download_pdf('https://j.example/a.pdf',
                            _Session(_Resp(200, headers={'Content-Length': '999999'})),
                            config, self.throttle, resolver=_public_resolver, sleep=_no_sleep)
        self.assertEqual(ctx.exception.reason, 'too_large')

    def test_magic_bytes_and_eof_are_checked(self):
        with self.assertRaises(pf.FetchSkip) as ctx:
            pf.download_pdf('https://j.example/a.pdf',
                            _Session(_Resp(200, b'GIF89a....', {'Content-Type': 'image/gif'})),
                            self.config, self.throttle, resolver=_public_resolver,
                            sleep=_no_sleep)
        self.assertEqual(ctx.exception.reason, 'not_pdf')
        with self.assertRaises(pf.FetchSkip) as ctx:
            pf.validate_downloaded_pdf(b'%PDF-1.4 truncated', 'application/pdf', 10 ** 6)
        self.assertEqual(ctx.exception.reason, 'invalid_pdf')
        with self.assertRaises(pf.FetchSkip):
            pf.validate_downloaded_pdf(PDF_BYTES, 'text/html', 10 ** 6)
        data, checksum = pf.validate_downloaded_pdf(PDF_BYTES, 'application/pdf', 10 ** 6)
        self.assertEqual(len(checksum), 64)

    def test_private_hosts_are_refused(self):
        def private(host, port):
            return [(None, None, None, None, ('10.0.0.5', port))]
        with self.assertRaises(pf.FetchSkip) as ctx:
            pf.download_pdf('https://intranet.example/a.pdf', _Session(), self.config,
                            self.throttle, resolver=private, sleep=_no_sleep)
        self.assertEqual(ctx.exception.reason, 'non_public_host')

    def test_dry_run_downloads_nothing(self):
        session, cur = _Session(), _FakeCur()
        status, _reason, info = pf.fetch_one(
            cur, _pdf_article(), config=self.config, session=session,
            throttle=self.throttle, storage_dir='/nonexistent', admin_id=None,
            execute=False, resolver=_public_resolver, sleep=_no_sleep)
        self.assertEqual(status, 'would_fetch')
        self.assertEqual(session.calls, [])
        self.assertEqual(cur.sql, [])
        self.assertEqual(info['version_type'], 'version_of_record')

    def test_execute_stores_file_and_records_rights(self):
        with tempfile.TemporaryDirectory() as storage:
            cur = _FakeCur()
            status, _reason, info = pf.fetch_one(
                cur, _pdf_article(), config=self.config, session=_Session(_Resp(200)),
                throttle=self.throttle, storage_dir=storage, admin_id=5, execute=True,
                resolver=_public_resolver, sleep=_no_sleep)
            self.assertEqual(status, 'stored')
            self.assertTrue(os.path.isfile(os.path.join(storage, info['rel_path'])))
            update_sql, params = cur.sql[-1]
            self.assertIn("access_type = 'open_access'", update_sql)
            self.assertIn("source_type = 'open_journal'", update_sql)
            self.assertIn("COALESCE(pdf_path, '') = ''", update_sql)
            self.assertEqual(params[4], 'version_of_record')
            self.assertEqual(params[5], 'Jurnal')
            self.assertEqual(params[8], 5)                          # rights_confirmed_by
            self.assertIn('https://journal.example/a.pdf', params[9])  # permission_note

    def test_permission_note_is_appended_not_overwritten(self):
        with tempfile.TemporaryDirectory() as storage:
            cur = _FakeCur()
            pf.fetch_one(cur, _pdf_article(), config=self.config, session=_Session(_Resp(200)),
                         throttle=self.throttle, storage_dir=storage, admin_id=5, execute=True,
                         resolver=_public_resolver, sleep=_no_sleep)
            update_sql, params = cur.sql[-1]
            # Bo'sh bo'lsa — faqat dalil; aks holda eski izoh + bo'sh qator + dalil.
            self.assertIn("WHEN COALESCE(BTRIM(permission_note), '') = '' THEN %s", update_sql)
            self.assertIn("ELSE permission_note || E'\\n\\n' || %s END", update_sql)
            self.assertNotRegex(update_sql, r'permission_note = %s,')
            self.assertEqual(params[9], params[10])
            self.assertIn('SHA-256:', params[10])

    def test_failed_download_records_reason_and_writes_no_file(self):
        with tempfile.TemporaryDirectory() as storage:
            cur = _FakeCur()
            status, reason, _ = pf.fetch_one(
                cur, _pdf_article(), config=self.config, session=_Session(_Resp(403, b'')),
                throttle=self.throttle, storage_dir=storage, admin_id=5, execute=True,
                resolver=_public_resolver, sleep=_no_sleep)
            self.assertEqual((status, reason), ('skipped', 'http_403'))
            self.assertEqual(os.listdir(storage), [])
            self.assertIn('skipped_final', cur.sql[-1][1][0])

    def test_disk_space_guard(self):
        class Usage:
            free = 4 * 1024 ** 3
        with self.assertRaises(pf.DiskSpaceLow):
            pf.ensure_disk_space('/', 5 * 1024 ** 3, usage=lambda _p: Usage())

    def test_user_agent_carries_contact(self):
        self.assertIn('mailto:ops@example.org', self.config.user_agent())


# ── 7b. Import approve: OAK yo'nalishi faqat high, litsenziya kelishuvi ─────

class ApproveAutoClassificationTests(unittest.TestCase):
    """`approve_import_item` — yo'nalish FAQAT high moslikda, litsenziya FAQAT
    `resolved` kelishuvda avtomatik qo'yiladi; nashr hech qachon."""

    def _approve(self, topic_subfield, licences, primary_field_id=None):
        from services.article_import import service as svc
        from services.article_import.config import ArticleImportConfig
        from services.article_metadata.models import (AuthorCandidate, LicenseCandidate,
                                                      MetadataLookupResult,
                                                      NormalizedArticleMetadata,
                                                      TopicCandidate)
        from tests.fake_article_import_pg import FakeImportCursor, new_store

        class Cursor(FakeImportCursor):
            def _dispatch(self, sql, p):
                if sql.startswith('SELECT id FROM scientific_fields WHERE code'):
                    return ([(42,)] if p[0] == '14.00.14' else []), [('id',)]
                return super()._dispatch(sql, p)

        def fetch(doi, *, config=None, cache_get=None, cache_set=None):
            metadata = NormalizedArticleMetadata(
                doi=doi, title='Onkologiya', journal_name='J', publication_year=2024,
                publication_date=date(2024, 3, 15),
                authors=[AuthorCandidate(name='Karimov Sardor', order=1)],
                topics=[TopicCandidate(name='T', source='openalex', is_primary=True,
                                       subfield_id=topic_subfield, field_id='27')],
                license_candidates=[LicenseCandidate(**l) for l in licences])
            return MetadataLookupResult(doi=doi, status='ok', metadata=metadata,
                                        providers={}, conflicts=[], warnings=[])

        store = new_store()
        cur = Cursor(store)
        config = ArticleImportConfig(max_items=10, chunk_size=10, max_attempts=3)
        batch_id = svc.create_import_batch(cur, created_by=1, source_type='doi_list',
                                           raw_text='10.5555/onc.1', config=config)
        svc.process_import_batch(cur, batch_id, config=config, fetch=fetch)
        item_id = svc.list_items(cur, batch_id)[0][0]['id']
        svc.approve_import_item(cur, item_id, actor_id=1, primary_field_id=primary_field_id)
        insert = [(sql, p) for sql, p in store['sql']
                  if sql.startswith('INSERT INTO scientific_articles')][0]
        return store, insert

    def test_high_mapping_sets_primary_field(self):
        store, _ = self._approve('2730', [])                    # Oncology -> 14.00.14 high
        self.assertIn((1, 42, True), [tuple(l) for l in store['links']])

    def test_low_mapping_leaves_field_empty(self):
        store, insert = self._approve('2303', [])               # low
        self.assertEqual(store['links'], [])
        self.assertIn('ingest_evidence', insert[0])
        evidence = json.loads(insert[1][-1])
        self.assertEqual(evidence['oak_field']['confidence'], 'low')
        self.assertFalse(evidence['oak_field']['applied'])

    def test_admin_choice_wins_over_mapping(self):
        store, _ = self._approve('2730', [], primary_field_id=7)
        self.assertEqual([tuple(l) for l in store['links']], [(1, 7, True)])

    def test_resolved_licence_is_written_but_access_stays_metadata_only(self):
        _store, (sql, params) = self._approve('2730', [
            {'source': 'crossref', 'license_type': 'cc_by', 'url': 'https://cc/by',
             'applies_to': 'vor', 'start_date': '2024-03-01'}])
        self.assertIn('license_type', sql)
        self.assertIn('cc_by', params)
        self.assertNotIn('access_type', sql)
        self.assertIn('pending_review', params)
        self.assertNotIn('published', params)

    def test_unresolved_licence_is_not_written(self):
        _store, (sql, _params) = self._approve('2730', [
            {'source': 'unpaywall', 'license_type': 'cc_by'}])
        self.assertNotIn('license_type', sql.split('VALUES')[0])


# ── 8. CLI skriptlari: xavfsiz default'lar ──────────────────────────────────

class CliSafetyTests(unittest.TestCase):
    def test_ingest_requires_api_key(self):
        from scripts import ingest_uz_articles as ingest
        with mock.patch.dict(os.environ, {'OPENALEX_API_KEY': '', 'DATABASE_URL': 'x'}):
            code = ingest.main(['x', '--from-date', '2024-01-01', '--limit', '10'])
        self.assertEqual(code, 2)

    def test_ingest_execute_requires_admin(self):
        from scripts import ingest_uz_articles as ingest
        code = ingest.main(['x', '--from-date', '2024-01-01', '--limit', '10', '--execute'])
        self.assertEqual(code, 2)

    def test_ingest_limit_is_mandatory(self):
        from scripts import ingest_uz_articles as ingest
        with self.assertRaises(SystemExit):
            ingest.main(['x', '--from-date', '2024-01-01'])

    def test_budget_plan_splits_days_when_needed(self):
        from scripts import ingest_uz_articles as ingest
        plan = ingest.budget_plan(11500, 20000)
        self.assertEqual(plan['works'], 11500)
        self.assertEqual(plan['list_requests'], 59)
        self.assertTrue(plan['fits_in_one_day'])
        big = ingest.budget_plan(250000, 250000)
        self.assertFalse(big['fits_in_one_day'])
        self.assertGreaterEqual(big['days_needed'], 3)

    def test_full_run_costs_list_pages_only(self):
        # 2026-11-17 lokal pilot: 200 ta ishning to'liq ingestion'i 2 kredit oldi
        # (singleton 0, ro'yxat 1). 11 521 ta ish = 58 sahifa + sanash = 59 kredit.
        from scripts import ingest_uz_articles as ingest
        live = {'rate_limit': {'credits_limit': 10000, 'credits_remaining': 9996,
                               'daily_budget_usd': 1, 'daily_remaining_usd': 0.9996}}
        plan = ingest.budget_plan(11521, 12000, live=live)
        self.assertEqual(plan['credits_daily'], 10000)
        self.assertEqual(plan['limits_source'], 'rate-limit')
        self.assertEqual(plan['credits_estimate'], 59)
        self.assertTrue(plan['fits_in_one_day'])
        self.assertTrue(plan['fits_today'])

    def test_execute_is_refused_when_today_remaining_is_too_small(self):
        from scripts import ingest_uz_articles as ingest
        live = {'rate_limit': {'credits_limit': 10000, 'credits_remaining': 20,
                               'daily_budget_usd': 1, 'daily_remaining_usd': 0.002}}
        plan = ingest.budget_plan(11521, 12000, live=live)
        self.assertFalse(plan['fits_today'])
        self.assertEqual(plan['today_limit_suggestion'], 19 * 200)
        ok = ingest.budget_plan(11521, 200, live=live)
        self.assertTrue(ok['fits_today'])
        self.assertIsNone(ok['today_limit_suggestion'])

    def test_budget_plan_falls_back_to_defaults_without_rate_limit(self):
        from scripts import ingest_uz_articles as ingest
        plan = ingest.budget_plan(200, 200, live={'status': 'http_error'})
        self.assertEqual(plan['credits_daily'], ingest.CREDITS_DAILY)
        self.assertEqual(plan['limits_source'], 'default')
        self.assertTrue(plan['fits_today'])

    def test_fetch_requires_limit_and_admin(self):
        from scripts import fetch_open_pdfs as fetch
        with self.assertRaises(SystemExit):
            fetch.main(['x'])
        self.assertEqual(fetch.main(['x', '--limit', '5', '--execute']), 2)


if __name__ == '__main__':
    unittest.main()
