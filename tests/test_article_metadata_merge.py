# -*- coding: utf-8 -*-
"""Normalizator, merger va orkestratsiya (Phase 2C.1, §35–§36).

Bu yerda tarmoq umuman yo'q: klientlar `fetchers` argumenti orqali
almashtiriladi. Tekshiriladigan narsa — ustuvorlik, konflikt va muallif
moslashtirish qoidalari, ya'ni tizimning eng nozik qismi.
"""
import unittest
from datetime import date, datetime, timezone

from services.article_metadata import (LOOKUP_FAILED, LOOKUP_NOT_FOUND, LOOKUP_OK,
                                       LOOKUP_PARTIAL, MetadataLookupError,
                                       fetch_article_metadata)
from services.article_metadata.config import MetadataConfig
from services.article_metadata.crossref import extract as crossref_extract
from services.article_metadata.merger import merge, merge_authors, merge_licenses
from services.article_metadata.models import (CROSSREF, NOT_CONFIGURED, NOT_FOUND,
                                              OK, OPENALEX, TIMEOUT, UNAVAILABLE,
                                              UNPAYWALL, ProviderResult)
from services.article_metadata.normalizer import (clean_abstract, clean_text,
                                                  date_from_parts, map_license_label,
                                                  map_license_url, name_key,
                                                  normalize_issn, normalize_orcid,
                                                  openalex_author_id,
                                                  openalex_short_id)
from services.article_metadata.openalex import extract as openalex_extract
from services.article_metadata.unpaywall import extract as unpaywall_extract
from tests.fake_scholarly_api import (crossref_work, openalex_work,
                                      unpaywall_record)

CONFIG = MetadataConfig(contact_email='test@olimlar.uz')
DOI = '10.1234/abcd.2024.01'


def result(provider, data, status=OK, fetched_at=None):
    return ProviderResult(provider=provider, status=status, data=data,
                          fetched_at=fetched_at or datetime(2026, 8, 7,
                                                            tzinfo=timezone.utc))


def all_three(crossref_overrides=None, openalex_overrides=None,
              unpaywall_overrides=None):
    crossref_data, _ = crossref_extract(
        crossref_work(**(crossref_overrides or {}))['message'])
    openalex_data, _ = openalex_extract(openalex_work(**(openalex_overrides or {})))
    unpaywall_data, _ = unpaywall_extract(unpaywall_record(**(unpaywall_overrides or {})))
    return {
        CROSSREF: result(CROSSREF, crossref_data),
        OPENALEX: result(OPENALEX, openalex_data),
        UNPAYWALL: result(UNPAYWALL, unpaywall_data),
    }


# ── Normalizator ────────────────────────────────────────────────────────────

class NormalizerTests(unittest.TestCase):
    def test_doi_normalisation_strips_the_resolver_prefix(self):
        from utils.article_rules import normalize_doi
        self.assertEqual(normalize_doi('https://doi.org/10.1234/ABC'), '10.1234/abc')
        self.assertEqual(normalize_doi('doi:10.1234/abc'), '10.1234/abc')

    def test_orcid_accepts_the_checksum_x(self):
        self.assertEqual(normalize_orcid('https://orcid.org/0000-0002-1694-233X'),
                         '0000-0002-1694-233X')
        self.assertIsNone(normalize_orcid('not-an-orcid'))

    def test_issn_format_is_enforced(self):
        self.assertEqual(normalize_issn('1234-5678'), '1234-5678')
        self.assertEqual(normalize_issn('1234-567x'), '1234-567X')
        self.assertIsNone(normalize_issn('12345678'))

    def test_openalex_ids_are_shortened(self):
        self.assertEqual(openalex_short_id('https://openalex.org/W2741809807'),
                         'W2741809807')
        self.assertEqual(openalex_author_id('https://openalex.org/A5023888391'),
                         'A5023888391')
        self.assertIsNone(openalex_short_id('https://example.org/nope'))

    def test_incomplete_date_parts_never_invent_a_day(self):
        self.assertEqual(date_from_parts([[2024, 3, 15]]), date(2024, 3, 15))
        self.assertIsNone(date_from_parts([[2024, 3]]))
        self.assertIsNone(date_from_parts([[2024]]))
        self.assertIsNone(date_from_parts(None))

    def test_markup_is_stripped_from_text_and_abstract(self):
        self.assertEqual(clean_text('<b>Salom</b>   dunyo'), 'Salom dunyo')
        cleaned = clean_abstract('<jats:p>Bir<jats:italic>ikki</jats:italic></jats:p>')
        self.assertNotIn('<', cleaned)

    def test_html_entities_are_decoded_not_left_raw(self):
        self.assertEqual(clean_text('A &amp; B'), 'A & B')

    def test_licence_urls_map_to_project_keys(self):
        self.assertEqual(map_license_url('https://creativecommons.org/licenses/by/4.0/')[0],
                         'cc_by')
        self.assertEqual(map_license_url('https://creativecommons.org/licenses/by-nc-sa/4.0/')[0],
                         'cc_by_nc_sa')
        self.assertEqual(map_license_url('https://creativecommons.org/publicdomain/zero/1.0/')[0],
                         'cc0')

    def test_unmappable_licences_stay_unmapped(self):
        """Tanib bo'lmagan litsenziya ruxsat beruvchi kalitga aylanmaydi."""
        for url in ('https://creativecommons.org/licenses/by-nd/4.0/',
                    'https://creativecommons.org/licenses/by-nc-nd/4.0/',
                    'https://www.elsevier.com/tdm/userlicense/1.0/'):
            self.assertIsNone(map_license_url(url)[0], url)
        self.assertIsNone(map_license_label('cc-by-nd')[0])

    def test_name_key_is_exact_not_fuzzy(self):
        self.assertEqual(name_key('Sardor Karimov'), name_key('sardor  KARIMOV'))
        self.assertEqual(name_key('Ö. Karímov'), 'o karimov')
        self.assertNotEqual(name_key('S. Karimov'), name_key('Sardor Karimov'))


# ── Ustuvorlik ──────────────────────────────────────────────────────────────

class PrecedenceTests(unittest.TestCase):
    def test_when_everyone_agrees_there_are_no_conflicts(self):
        metadata, conflicts, _warnings = merge(all_three())
        self.assertEqual(conflicts, [])
        self.assertEqual(metadata.title,
                         'Raqamli ta‘lim muhitida talabalar motivatsiyasi')

    def test_crossref_wins_the_bibliographic_fields(self):
        metadata, _c, _w = merge(all_three(
            crossref_overrides={'container-title': ['Journal of A']},
            openalex_overrides={'primary_location': {
                'landing_page_url': 'https://x', 'source': {'display_name': 'Journal A'}}}))
        self.assertEqual(metadata.journal_name, 'Journal of A')
        self.assertEqual(metadata.source_summary['journal_name'], CROSSREF)

    def test_openalex_fills_in_when_crossref_has_no_title(self):
        data = all_three()
        data[CROSSREF].data['title'] = None
        metadata, _c, _w = merge(data)
        self.assertEqual(metadata.title,
                         'Raqamli ta‘lim muhitida talabalar motivatsiyasi')
        self.assertEqual(metadata.source_summary['title'], OPENALEX)

    def test_identifiers_come_from_their_only_owner(self):
        metadata, _c, _w = merge(all_three())
        self.assertEqual(metadata.openalex_id, 'W2741809807')
        self.assertEqual(metadata.crossref_member_id, '286')
        self.assertEqual(metadata.source_summary['openalex_id'], OPENALEX)

    def test_open_access_comes_from_unpaywall_first(self):
        metadata, _c, _w = merge(all_three(
            unpaywall_overrides={'oa_status': 'green'}))
        self.assertEqual(metadata.oa_status, 'green')
        self.assertEqual(metadata.source_summary['oa_status'], UNPAYWALL)

    def test_a_partial_provider_set_still_produces_metadata(self):
        crossref_data, _ = crossref_extract(crossref_work()['message'])
        metadata, _c, _w = merge({
            CROSSREF: result(CROSSREF, crossref_data),
            OPENALEX: ProviderResult(provider=OPENALEX, status=TIMEOUT),
            UNPAYWALL: ProviderResult(provider=UNPAYWALL, status=UNAVAILABLE),
        })
        self.assertEqual(metadata.journal_name, 'Journal of Educational Research')
        self.assertIsNone(metadata.openalex_id)
        self.assertIsNone(metadata.oa_status)

    def test_all_providers_failing_yields_an_empty_but_valid_result(self):
        metadata, conflicts, _w = merge({
            CROSSREF: ProviderResult(provider=CROSSREF, status=TIMEOUT),
            OPENALEX: ProviderResult(provider=OPENALEX, status=NOT_FOUND),
        })
        self.assertIsNone(metadata.title)
        self.assertEqual(conflicts, [])
        self.assertEqual(metadata.filled_field_count(), 0)


# ── Konfliktlar ─────────────────────────────────────────────────────────────

class ConflictTests(unittest.TestCase):
    def conflict(self, conflicts, field):
        return next((c for c in conflicts if c.field == field), None)

    def test_a_year_disagreement_is_surfaced_not_silently_resolved(self):
        _m, conflicts, _w = merge(all_three(
            openalex_overrides={'publication_year': 2023}))
        conflict = self.conflict(conflicts, 'publication_year')
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict.preferred, 2024)
        self.assertEqual(conflict.preferred_source, CROSSREF)
        self.assertEqual(conflict.alternatives,
                         [{'source': OPENALEX, 'value': 2023}])

    def test_case_and_spacing_differences_are_not_conflicts(self):
        _m, conflicts, _w = merge(all_three(
            unpaywall_overrides={'journal_name': 'JOURNAL  OF EDUCATIONAL RESEARCH'}))
        self.assertIsNone(self.conflict(conflicts, 'journal_name'))

    def test_a_licence_disagreement_becomes_a_conflict_and_a_warning(self):
        metadata, conflicts, warnings = merge(all_three(
            unpaywall_overrides={'best_oa_location': dict(
                unpaywall_record()['best_oa_location'], license='cc-by-nc')}))
        conflict = self.conflict(conflicts, 'license_type')
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict.preferred, 'cc_by')
        self.assertEqual(conflict.alternatives,
                         [{'source': UNPAYWALL, 'value': 'cc_by_nc'}])
        self.assertTrue(any(w.code == 'license_conflict' for w in warnings))
        # Ikkala nomzod ham saqlanadi — biri o'chirilmaydi.
        self.assertEqual(len(metadata.license_candidates), 2)

    def test_a_single_licence_source_is_a_candidate_without_a_conflict(self):
        candidates, conflict = merge_licenses({
            CROSSREF: {'licenses': [{'license_type': 'cc_by', 'url': 'u',
                                     'label': 'CC BY', 'applies_to': 'vor'}]}})
        self.assertIsNone(conflict)
        self.assertEqual(candidates[0].license_type, 'cc_by')

    def test_conflicts_serialise_dates_for_the_browser(self):
        _m, conflicts, _w = merge(all_three(
            openalex_overrides={'publication_date': '2023-01-02'}))
        conflict = self.conflict(conflicts, 'publication_date')
        self.assertEqual(conflict.to_dict()['preferred'], '2024-03-15')
        self.assertEqual(conflict.to_dict()['alternatives'][0]['value'], '2023-01-02')


# ── Mualliflar ──────────────────────────────────────────────────────────────

class AuthorMergeTests(unittest.TestCase):
    def merged(self, crossref_authors, openalex_authors):
        authors, unmatched, warnings = merge_authors(
            {CROSSREF: {'authors': crossref_authors},
             OPENALEX: {'authors': openalex_authors}})
        self.unmatched = unmatched
        return authors, warnings

    def test_orcid_is_the_strongest_match(self):
        authors, _w = self.merged(
            [{'name': 'S. Karimov', 'orcid': '0000-0002-1825-0097', 'order': 1}],
            [{'name': 'Sardor Karimov', 'orcid': '0000-0002-1825-0097',
              'openalex_author_id': 'A1', 'institutions': ['TDPU'], 'order': 1}])
        self.assertEqual(len(authors), 1)
        self.assertEqual(authors[0].match_basis, 'orcid')
        self.assertEqual(authors[0].openalex_author_id, 'A1')
        # Crossref nomi saqlanadi — bibliografik manba u.
        self.assertEqual(authors[0].name, 'S. Karimov')
        self.assertEqual(sorted(authors[0].sources), [CROSSREF, OPENALEX])

    def test_an_exact_full_name_match_is_accepted(self):
        authors, _w = self.merged(
            [{'name': 'Nilufar Yusupova', 'order': 1}],
            [{'name': 'Nilufar Yusupova', 'openalex_author_id': 'A2', 'order': 1}])
        self.assertEqual(len(authors), 1)
        self.assertEqual(authors[0].match_basis, 'name')

    def test_an_initial_is_not_merged_with_a_full_name(self):
        """Fuzzy moslashtirish yo'q: "J. Smith" va "John Smith" birlashmaydi.

        Lekin moslanmagani ro'yxatga ham QO'SHILMAYDI — u alohida chiqadi,
        aks holda bir odam ikki qator bo'lib qolardi (haqiqiy Crossref/
        OpenAlex juftliklarida bu doimiy holat).
        """
        authors, warnings = self.merged(
            [{'name': 'J. Smith', 'order': 1}],
            [{'name': 'John Smith', 'openalex_author_id': 'A3', 'order': 1}])
        self.assertEqual([a.name for a in authors], ['J. Smith'])
        self.assertEqual([a.name for a in self.unmatched], ['John Smith'])
        self.assertTrue(any(w.code == 'authors_unmatched' for w in warnings))

    def test_conflicting_orcids_block_a_name_merge(self):
        authors, _w = self.merged(
            [{'name': 'Ali Valiyev', 'orcid': '0000-0002-1825-0097', 'order': 1}],
            [{'name': 'Ali Valiyev', 'orcid': '0000-0003-1111-2222',
              'openalex_author_id': 'A4', 'order': 1}])
        self.assertEqual(len(authors), 1)
        self.assertEqual(authors[0].orcid, '0000-0002-1825-0097')
        self.assertEqual(len(self.unmatched), 1)

    def test_a_duplicated_name_is_never_auto_matched(self):
        authors, _w = self.merged(
            [{'name': 'Ali Valiyev', 'order': 1}, {'name': 'Ali Valiyev', 'order': 2}],
            [{'name': 'Ali Valiyev', 'openalex_author_id': 'A5', 'order': 1}])
        self.assertEqual(len(authors), 2)
        self.assertEqual(len(self.unmatched), 1)
        self.assertIsNone(authors[0].openalex_author_id)

    def test_the_crossref_list_never_grows_from_enrichment(self):
        """Boyitish ro'yxat UZUNLIGINI o'zgartirmaydi — bu invariant."""
        crossref_authors = [{'name': 'Heather A. Piwowar', 'order': 1},
                            {'name': 'Roger S. Day', 'order': 2}]
        authors, _w = self.merged(crossref_authors, [
            {'name': 'Heather Piwowar', 'orcid': '0000-0003-1613-5981',
             'openalex_author_id': 'A1', 'order': 1},
            {'name': 'Roger Day', 'openalex_author_id': 'A2', 'order': 2}])
        self.assertEqual(len(authors), len(crossref_authors))
        self.assertEqual(len(self.unmatched), 2)

    def test_crossref_order_is_preserved_and_renumbered(self):
        authors, _w = self.merged(
            [{'name': 'Bir', 'order': 1}, {'name': 'Ikki', 'order': 2}],
            [{'name': 'Ikki', 'openalex_author_id': 'A6', 'order': 1}])
        self.assertEqual([a.name for a in authors], ['Bir', 'Ikki'])
        self.assertEqual([a.order for a in authors], [1, 2])

    def test_openalex_only_authors_are_used_when_crossref_has_none(self):
        authors, warnings = self.merged(
            [], [{'name': 'Yolg‘iz Muallif', 'openalex_author_id': 'A7', 'order': 1}])
        self.assertEqual(len(authors), 1)
        self.assertEqual(authors[0].sources, [OPENALEX])
        self.assertEqual(self.unmatched, [])
        self.assertEqual(warnings, [])

    def test_enrichment_never_overwrites_a_crossref_value(self):
        authors, _w = self.merged(
            [{'name': 'A B', 'affiliation': 'Crossref tashkiloti',
              'orcid': '0000-0002-1825-0097', 'order': 1}],
            [{'name': 'A B', 'orcid': '0000-0002-1825-0097',
              'affiliation': 'OpenAlex tashkiloti', 'openalex_author_id': 'A8',
              'order': 1}])
        self.assertEqual(authors[0].affiliation, 'Crossref tashkiloti')


# ── Semantik signallar ──────────────────────────────────────────────────────

class SemanticWarningTests(unittest.TestCase):
    def codes(self, warnings):
        return {w.code for w in warnings}

    def test_retraction_is_never_dropped_silently(self):
        metadata, _c, warnings = merge(all_three(
            openalex_overrides={'is_retracted': True}))
        self.assertTrue(metadata.is_retracted)
        self.assertIn('retracted', self.codes(warnings))

    def test_a_crossref_retraction_notice_is_reported(self):
        _m, _c, warnings = merge(all_three(crossref_overrides={'update-to': [
            {'type': 'retraction', 'DOI': '10.1234/r', 'label': 'Retraction'}]}))
        self.assertIn('crossref_update_notice', self.codes(warnings))

    def test_oa_status_is_flagged_as_not_being_the_access_type(self):
        _m, _c, warnings = merge(all_three())
        self.assertIn('oa_status_is_not_access_type', self.codes(warnings))

    def test_a_pdf_url_is_announced_as_a_candidate_only(self):
        metadata, _c, warnings = merge(all_three())
        self.assertIn('pdf_candidate_only', self.codes(warnings))
        self.assertTrue(any(l.pdf_url for l in metadata.external_locations))

    def test_citation_count_always_carries_its_source_and_timestamp(self):
        metadata, _c, _w = merge(all_three())
        self.assertEqual(metadata.citation_count, 11)
        self.assertEqual(metadata.citation_count_source, OPENALEX)
        self.assertIsNotNone(metadata.citation_count_checked_at)

    def test_a_preprint_type_is_flagged_for_the_version_field(self):
        _m, _c, warnings = merge(all_three(
            crossref_overrides={'type': 'posted-content'}))
        self.assertIn('preprint', self.codes(warnings))


# ── Orkestratsiya ───────────────────────────────────────────────────────────

class OrchestrationTests(unittest.TestCase):
    def fetchers(self, **statuses):
        def make(provider, extractor, payload):
            def fetch(doi, config, **kwargs):
                status = statuses.get(provider, OK)
                if status != OK:
                    return ProviderResult(provider=provider, status=status)
                data, _warnings = extractor(payload)
                return result(provider, data)
            return fetch
        return {
            CROSSREF: make(CROSSREF, crossref_extract, crossref_work()['message']),
            OPENALEX: make(OPENALEX, openalex_extract, openalex_work()),
            UNPAYWALL: make(UNPAYWALL, unpaywall_extract, unpaywall_record()),
        }

    def lookup(self, doi=DOI, **statuses):
        return fetch_article_metadata(doi, config=CONFIG,
                                      fetchers=self.fetchers(**statuses))

    def test_all_providers_ok_is_a_clean_result(self):
        outcome = self.lookup()
        self.assertEqual(outcome.status, LOOKUP_OK)
        self.assertEqual(outcome.doi, DOI)
        self.assertEqual(outcome.metadata.openalex_id, 'W2741809807')

    def test_one_failing_provider_is_a_partial_success(self):
        outcome = self.lookup(unpaywall=TIMEOUT)
        self.assertEqual(outcome.status, LOOKUP_PARTIAL)
        self.assertEqual(outcome.providers[UNPAYWALL].status, TIMEOUT)
        self.assertEqual(outcome.metadata.title,
                         'Raqamli ta‘lim muhitida talabalar motivatsiyasi')
        self.assertTrue(any(w.code == 'partial_success' for w in outcome.warnings))

    def test_a_not_configured_provider_does_not_count_as_a_failure(self):
        outcome = self.lookup(openalex=NOT_CONFIGURED)
        self.assertEqual(outcome.status, LOOKUP_OK)
        self.assertEqual(outcome.providers[OPENALEX].status, NOT_CONFIGURED)

    def test_everyone_saying_not_found_is_not_found(self):
        outcome = self.lookup(crossref=NOT_FOUND, openalex=NOT_FOUND,
                              unpaywall=NOT_FOUND)
        self.assertEqual(outcome.status, LOOKUP_NOT_FOUND)
        self.assertTrue(any(w.code == 'not_found' for w in outcome.warnings))

    def test_everyone_failing_is_a_failure_with_a_readable_warning(self):
        outcome = self.lookup(crossref=TIMEOUT, openalex=UNAVAILABLE,
                              unpaywall=TIMEOUT)
        self.assertEqual(outcome.status, LOOKUP_FAILED)
        self.assertTrue(any(w.code == 'all_providers_failed' for w in outcome.warnings))

    def test_an_unexpected_provider_exception_does_not_break_the_lookup(self):
        fetchers = self.fetchers()
        def boom(doi, config, **kwargs):
            raise RuntimeError('kutilmagan')
        fetchers[OPENALEX] = boom
        outcome = fetch_article_metadata(DOI, config=CONFIG, fetchers=fetchers)
        self.assertEqual(outcome.status, LOOKUP_PARTIAL)
        self.assertEqual(outcome.providers[OPENALEX].status, UNAVAILABLE)

    def test_an_invalid_doi_is_rejected_before_any_network_call(self):
        calls = []
        def spy(doi, config, **kwargs):
            calls.append(doi)
            return ProviderResult(provider=CROSSREF, status=OK)
        with self.assertRaises(MetadataLookupError):
            fetch_article_metadata('not a doi', config=CONFIG,
                                   fetchers={CROSSREF: spy})
        self.assertEqual(calls, [])

    def test_the_doi_is_normalised_before_lookup(self):
        seen = []
        def spy(doi, config, **kwargs):
            seen.append(doi)
            return ProviderResult(provider=CROSSREF, status=OK)
        fetch_article_metadata('https://doi.org/10.1234/ABCD.2024.01',
                               config=CONFIG, fetchers={CROSSREF: spy})
        self.assertEqual(seen, ['10.1234/abcd.2024.01'])

    def test_cached_provider_results_skip_the_network(self):
        store = {}
        calls = {'n': 0}
        fetchers = self.fetchers()
        original = fetchers[CROSSREF]
        def counting(doi, config, **kwargs):
            calls['n'] += 1
            return original(doi, config, **kwargs)
        fetchers[CROSSREF] = counting

        def cache_get(key):
            return store.get(key)

        def cache_set(key, value, ttl):
            store[key] = value

        fetch_article_metadata(DOI, config=CONFIG, fetchers=fetchers,
                               cache_get=cache_get, cache_set=cache_set)
        second = fetch_article_metadata(DOI, config=CONFIG, fetchers=fetchers,
                                        cache_get=cache_get, cache_set=cache_set)
        self.assertEqual(calls['n'], 1)
        self.assertIn(CROSSREF, second.from_cache)
        self.assertEqual(second.metadata.title,
                         'Raqamli ta‘lim muhitida talabalar motivatsiyasi')

    def test_a_broken_cache_never_blocks_the_lookup(self):
        def boom(_key):
            raise RuntimeError('kesh yotdi')
        outcome = fetch_article_metadata(DOI, config=CONFIG,
                                         fetchers=self.fetchers(), cache_get=boom)
        self.assertEqual(outcome.status, LOOKUP_OK)

    def test_failed_results_are_never_cached(self):
        store = {}
        fetch_article_metadata(DOI, config=CONFIG,
                               fetchers=self.fetchers(crossref=TIMEOUT),
                               cache_get=store.get,
                               cache_set=lambda k, v, t: store.__setitem__(k, v))
        self.assertFalse(any('crossref' in key for key in store))

    def test_the_service_needs_no_flask_or_database(self):
        """Keyingi bulk importer va agent ayni shu funksiyani chaqiradi (§43).

        Izohlar emas, HAQIQIY importlar tekshiriladi (AST): paket Flask'ga
        yoki DB'ga bog'lanib qolsa, uni UI'dan tashqarida chaqirib bo'lmaydi.
        """
        import ast
        import os

        from services import article_metadata

        package_dir = os.path.dirname(article_metadata.__file__)
        forbidden = ('flask', 'flask_login', 'data', 'psycopg2', 'app',
                     'extensions')
        for name in sorted(os.listdir(package_dir)):
            if not name.endswith('.py'):
                continue
            with open(os.path.join(package_dir, name), encoding='utf-8') as fh:
                tree = ast.parse(fh.read(), filename=name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or '']
                else:
                    continue
                for module in modules:
                    self.assertNotIn(module.split('.')[0], forbidden,
                                     '%s: %s' % (name, module))


class PublicPayloadTests(unittest.TestCase):
    def test_raw_provider_data_never_reaches_the_browser(self):
        outcome = fetch_article_metadata(
            DOI, config=CONFIG,
            fetchers={CROSSREF: lambda d, c, **k: result(
                CROSSREF, crossref_extract(crossref_work()['message'])[0])})
        body = outcome.to_public_dict()
        self.assertNotIn('data', body['providers'][CROSSREF])
        self.assertEqual(set(body['providers'][CROSSREF]),
                         {'status', 'label', 'status_label', 'fetched_at', 'warnings'})

    def test_the_payload_is_json_serialisable(self):
        import json
        outcome = fetch_article_metadata(DOI, config=CONFIG, fetchers={
            CROSSREF: lambda d, c, **k: result(
                CROSSREF, crossref_extract(crossref_work()['message'])[0]),
            OPENALEX: lambda d, c, **k: result(
                OPENALEX, openalex_extract(openalex_work())[0]),
            UNPAYWALL: lambda d, c, **k: result(
                UNPAYWALL, unpaywall_extract(unpaywall_record())[0]),
        })
        encoded = json.dumps(outcome.to_public_dict())
        self.assertIn('"W2741809807"', encoded)
        self.assertIn('"conflicts"', encoded)


if __name__ == '__main__':
    unittest.main()
