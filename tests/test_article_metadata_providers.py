# -*- coding: utf-8 -*-
"""Provayder klientlari va HTTP qatlami (Phase 2C.1, §34).

Tashqi tarmoqqa CHIQILMAYDI: `services.article_metadata.http.get_session`
soxta sessiya bilan almashtiriladi, shuning uchun haqiqiy retry, backoff,
429/5xx va sxema mantiqi ishlaydi — faqat soket boshqa.

Har bir provayder MUSTAQIL tekshiriladi: bittasi yiqilsa qolganlari ishlashda
davom etishi kerak (§4).
"""
import unittest
from unittest import mock

from services.article_metadata import crossref, openalex, unpaywall
from services.article_metadata.config import MetadataConfig
from services.article_metadata.models import (INVALID_RESPONSE, NOT_CONFIGURED,
                                              NOT_FOUND, OK, RATE_LIMITED,
                                              TIMEOUT, UNAVAILABLE)
from tests.fake_scholarly_api import (ConnectionFailure, FakeResponse, FakeSession,
                                      Timeout, crossref_work, no_sleep,
                                      openalex_work, unpaywall_record)

CONFIG = MetadataConfig(contact_email='test@olimlar.uz', max_attempts=2,
                        backoff_seconds=0.0)
DOI = '10.1234/abcd.2024.01'


class ProviderTestCase(unittest.TestCase):
    def run_provider(self, module, routes, config=CONFIG):
        session = FakeSession(routes)
        with mock.patch('services.article_metadata.http.get_session',
                        return_value=session):
            result = module.fetch(DOI, config, sleep=no_sleep)
        self.session = session
        return result


# ── Crossref ────────────────────────────────────────────────────────────────

class CrossrefTests(ProviderTestCase):
    def ok(self, payload=None, config=CONFIG):
        return self.run_provider(
            crossref, {'api.crossref.org': FakeResponse(200, payload or crossref_work())},
            config=config)

    def test_success_extracts_the_bibliographic_core(self):
        result = self.ok()
        self.assertEqual(result.status, OK)
        self.assertEqual(result.data['title'],
                         'Raqamli ta‘lim muhitida talabalar motivatsiyasi')
        self.assertEqual(result.data['journal_name'], 'Journal of Educational Research')
        self.assertEqual(result.data['publication_date'], '2024-03-15')
        self.assertEqual(result.data['publication_year'], 2024)
        self.assertEqual(result.data['volume'], '42')
        self.assertEqual(result.data['pages'], '215-231')
        self.assertEqual(result.data['crossref_member_id'], '286')

    def test_jats_abstract_becomes_plain_text(self):
        abstract = self.ok().data['abstract']
        self.assertNotIn('<', abstract)
        self.assertNotIn('jats', abstract.lower())
        # "Abstract" sarlavhasi matnga qo'shilib ketmaydi.
        self.assertFalse(abstract.lower().startswith('abstract'))
        self.assertIn('tadqiqot', abstract)

    def test_script_content_never_survives_the_abstract_cleaner(self):
        payload = crossref_work(
            abstract='<jats:p>Matn</jats:p><script>alert(1)</script>')
        abstract = self.ok(payload).data['abstract']
        self.assertNotIn('alert', abstract)
        self.assertIn('Matn', abstract)

    def test_orcid_is_normalised_and_affiliation_kept(self):
        author = self.ok().data['authors'][0]
        self.assertEqual(author['orcid'], '0000-0002-1825-0097')
        self.assertEqual(author['affiliation'],
                         'Toshkent davlat pedagogika universiteti')
        self.assertEqual(author['order'], 1)

    def test_electronic_issn_is_preferred_as_primary(self):
        data = self.ok().data
        self.assertEqual(data['primary_issn'], '8765-4321')
        self.assertEqual(data['issn'], ['1234-5678', '8765-4321'])

    def test_licence_url_maps_to_a_project_licence_key(self):
        licences = self.ok().data['licenses']
        self.assertEqual(licences[0]['license_type'], 'cc_by')
        self.assertEqual(licences[0]['applies_to'], 'vor')

    def test_no_derivatives_licence_is_not_mapped_to_a_permissive_key(self):
        """CC BY-ND qayta tarqatishga ruxsat bermaydi — u CC BY bo'lib qolmasin."""
        payload = crossref_work(license=[
            {'URL': 'http://creativecommons.org/licenses/by-nc-nd/4.0/',
             'content-version': 'vor'}])
        licence = self.ok(payload).data['licenses'][0]
        self.assertIsNone(licence['license_type'])
        self.assertEqual(licence['label'], 'CC BY-NC-ND')

    def test_article_number_is_used_when_there_are_no_pages(self):
        payload = crossref_work(page=None, **{'article-number': 'e12345'})
        self.assertEqual(self.ok(payload).data['pages'], 'e12345')

    def test_partial_date_does_not_invent_a_day(self):
        payload = crossref_work(published={'date-parts': [[2024]]},
                                **{'published-online': {'date-parts': [[2024]]}})
        data = self.ok(payload).data
        self.assertIsNone(data['publication_date'])
        self.assertEqual(data['publication_year'], 2024)

    def test_retraction_notice_is_carried_through(self):
        payload = crossref_work(**{'update-to': [
            {'type': 'retraction', 'DOI': '10.1234/retraction', 'label': 'Retraction'}]})
        self.assertEqual(self.ok(payload).data['update_to'][0]['type'], 'retraction')

    def test_404_is_not_found_and_is_not_retried(self):
        result = self.run_provider(crossref, {'api.crossref.org': FakeResponse(404, {})})
        self.assertEqual(result.status, NOT_FOUND)
        self.assertEqual(len(self.session.calls), 1)

    def test_malformed_payload_is_reported_not_crashed(self):
        result = self.run_provider(crossref,
                                   {'api.crossref.org': FakeResponse(200, {'ok': True})})
        self.assertEqual(result.status, INVALID_RESPONSE)

    def test_non_json_body_is_reported(self):
        result = self.run_provider(
            crossref, {'api.crossref.org': FakeResponse(200, text='<html>')})
        self.assertEqual(result.status, INVALID_RESPONSE)

    def test_timeout_is_retried_then_reported(self):
        result = self.run_provider(crossref, {'api.crossref.org': Timeout})
        self.assertEqual(result.status, TIMEOUT)
        self.assertEqual(len(self.session.calls), CONFIG.max_attempts)

    def test_rate_limit_keeps_retry_after(self):
        result = self.run_provider(crossref, {'api.crossref.org': FakeResponse(
            429, {}, headers={'Retry-After': '3'})})
        self.assertEqual(result.status, RATE_LIMITED)
        self.assertEqual(result.retry_after, 3)

    def test_server_error_is_retried_and_then_unavailable(self):
        result = self.run_provider(crossref, {'api.crossref.org': FakeResponse(503, {})})
        self.assertEqual(result.status, UNAVAILABLE)
        self.assertEqual(len(self.session.calls), CONFIG.max_attempts)

    def test_a_transient_failure_recovers_on_the_second_attempt(self):
        result = self.run_provider(crossref, {'api.crossref.org': [
            FakeResponse(503, {}), FakeResponse(200, crossref_work())]})
        self.assertEqual(result.status, OK)
        self.assertEqual(len(self.session.calls), 2)

    def test_connection_failure_does_not_raise(self):
        result = self.run_provider(crossref, {'api.crossref.org': ConnectionFailure})
        self.assertEqual(result.status, UNAVAILABLE)

    def test_polite_pool_identification_is_sent(self):
        self.ok()
        call = self.session.calls[0]
        self.assertEqual(call['params']['mailto'], 'test@olimlar.uz')
        self.assertIn('Olimlar.uz/', call['headers']['User-Agent'])
        self.assertIn('mailto:test@olimlar.uz', call['headers']['User-Agent'])

    def test_it_still_works_without_a_contact_email(self):
        """Crossref e-mailsiz ham javob beradi — sekinroq navbatda."""
        config = MetadataConfig(max_attempts=1)
        result = self.ok(config=config)
        self.assertEqual(result.status, OK)
        self.assertNotIn('mailto', self.session.calls[0]['params'])


# ── OpenAlex ────────────────────────────────────────────────────────────────

class OpenAlexTests(ProviderTestCase):
    def ok(self, payload=None, config=CONFIG):
        return self.run_provider(
            openalex, {'api.openalex.org': FakeResponse(200, payload or openalex_work())},
            config=config)

    def test_success_extracts_identifiers_and_context(self):
        data = self.ok().data
        self.assertEqual(data['openalex_id'], 'W2741809807')
        self.assertEqual(data['cited_by_count'], 11)
        self.assertEqual(data['oa_status'], 'gold')
        self.assertEqual(data['primary_issn'], '8765-4321')
        self.assertEqual(data['pages'], '215-231')

    def test_author_ids_and_institutions_are_kept(self):
        author = self.ok().data['authors'][0]
        self.assertEqual(author['openalex_author_id'], 'A5023888391')
        self.assertEqual(author['orcid'], '0000-0002-1825-0097')
        self.assertEqual(author['institutions'],
                         ['Tashkent State Pedagogical University'])
        self.assertEqual(author['ror'], ['https://ror.org/01abcd234'])

    def test_topics_mark_the_primary_one(self):
        topics = self.ok().data['topics']
        self.assertTrue(topics[0]['is_primary'])
        self.assertEqual(topics[0]['name'], 'Digital Learning Environments')

    def test_the_inverted_abstract_is_never_reconstructed(self):
        """OpenAlex annotatsiyani faqat teskari indeks sifatida beradi —
        uni tiklash nashriyot matnining hosila nusxasi bo'lardi."""
        result = self.ok()
        self.assertNotIn('abstract', result.data)
        self.assertTrue(any('teskari indeks' in w for w in result.warnings))

    def test_retracted_work_raises_a_warning(self):
        result = self.ok(openalex_work(is_retracted=True))
        self.assertTrue(result.data['is_retracted'])
        self.assertTrue(any('retracted' in w.lower() for w in result.warnings))

    def test_not_configured_when_neither_key_nor_email_exists(self):
        result = self.run_provider(openalex, {}, config=MetadataConfig())
        self.assertEqual(result.status, NOT_CONFIGURED)
        self.assertEqual(self.session.calls, [])

    def test_the_api_key_is_used_when_configured(self):
        config = MetadataConfig(contact_email='test@olimlar.uz',
                                openalex_api_key='secret-key', max_attempts=1)
        self.ok(config=config)
        self.assertEqual(self.session.calls[0]['params']['api_key'], 'secret-key')

    def test_the_polite_pool_is_used_when_there_is_no_key(self):
        self.ok()
        params = self.session.calls[0]['params']
        self.assertEqual(params['mailto'], 'test@olimlar.uz')
        self.assertNotIn('api_key', params)

    def test_the_doi_lookup_form_is_used(self):
        self.ok()
        self.assertIn('/doi:10.1234', self.session.calls[0]['url'])

    def test_404_is_not_found(self):
        result = self.run_provider(openalex, {'api.openalex.org': FakeResponse(404, {})})
        self.assertEqual(result.status, NOT_FOUND)

    def test_payload_without_an_id_is_invalid(self):
        result = self.run_provider(openalex,
                                   {'api.openalex.org': FakeResponse(200, {'doi': 'x'})})
        self.assertEqual(result.status, INVALID_RESPONSE)

    def test_rate_limit_and_timeout_are_distinct_statuses(self):
        self.assertEqual(
            self.run_provider(openalex,
                              {'api.openalex.org': FakeResponse(429, {})}).status,
            RATE_LIMITED)
        self.assertEqual(
            self.run_provider(openalex, {'api.openalex.org': Timeout}).status, TIMEOUT)


# ── Unpaywall ───────────────────────────────────────────────────────────────

class UnpaywallTests(ProviderTestCase):
    def ok(self, payload=None, config=CONFIG):
        return self.run_provider(
            unpaywall,
            {'api.unpaywall.org': FakeResponse(200, payload or unpaywall_record())},
            config=config)

    def test_open_access_record_is_extracted(self):
        data = self.ok().data
        self.assertTrue(data['is_oa'])
        self.assertEqual(data['oa_status'], 'gold')
        self.assertTrue(data['journal_is_in_doaj'])
        self.assertEqual(data['license']['license_type'], 'cc_by')

    def test_the_best_location_comes_first_and_keeps_its_pdf_candidate(self):
        locations = self.ok().data['locations']
        self.assertTrue(locations[0]['is_best'])
        self.assertEqual(locations[0]['pdf_url'],
                         'https://journal.example.org/pdf/42-3-215.pdf')
        self.assertEqual(locations[0]['host_type'], 'publisher')

    def test_closed_record_has_no_locations_and_no_licence(self):
        data = self.ok(unpaywall_record(is_oa=False, oa_status='closed',
                                        best_oa_location=None, oa_locations=[])).data
        self.assertFalse(data['is_oa'])
        self.assertEqual(data['oa_status'], 'closed')
        self.assertEqual(data['locations'], [])
        self.assertIsNone(data['license'])

    def test_missing_best_location_on_an_oa_record_warns(self):
        result = self.ok(unpaywall_record(is_oa=True, best_oa_location=None,
                                          oa_locations=[]))
        self.assertTrue(any('eng yaxshi nusxa' in w for w in result.warnings))

    def test_not_configured_without_an_email(self):
        """Unpaywall e-mailsiz 422 beradi — chaqirishning ma'nosi yo'q."""
        result = self.run_provider(unpaywall, {}, config=MetadataConfig())
        self.assertEqual(result.status, NOT_CONFIGURED)
        self.assertEqual(self.session.calls, [])

    def test_the_email_goes_in_the_query_as_the_api_requires(self):
        self.ok()
        self.assertEqual(self.session.calls[0]['params']['email'], 'test@olimlar.uz')

    def test_404_timeout_and_rate_limit_are_reported_separately(self):
        self.assertEqual(
            self.run_provider(unpaywall,
                              {'api.unpaywall.org': FakeResponse(404, {})}).status,
            NOT_FOUND)
        self.assertEqual(
            self.run_provider(unpaywall, {'api.unpaywall.org': Timeout}).status, TIMEOUT)
        self.assertEqual(
            self.run_provider(unpaywall,
                              {'api.unpaywall.org': FakeResponse(429, {})}).status,
            RATE_LIMITED)


class HttpPolicyTests(ProviderTestCase):
    def test_retries_are_bounded_by_configuration(self):
        config = MetadataConfig(contact_email='a@b.uz', max_attempts=3,
                                backoff_seconds=0.0)
        self.run_provider(crossref, {'api.crossref.org': FakeResponse(500, {})},
                          config=config)
        self.assertEqual(len(self.session.calls), 3)

    def test_a_long_retry_after_is_not_waited_out(self):
        """Admin tugma bosib turibdi — 300 soniya kutilmaydi."""
        result = self.run_provider(crossref, {'api.crossref.org': FakeResponse(
            429, {}, headers={'Retry-After': '300'})})
        self.assertEqual(result.status, RATE_LIMITED)
        self.assertEqual(len(self.session.calls), 1)

    def test_both_timeouts_are_passed_to_the_transport(self):
        self.run_provider(crossref, {'api.crossref.org': FakeResponse(200, crossref_work())})
        connect, read = self.session.calls[0]['timeout']
        self.assertGreater(connect, 0)
        self.assertGreater(read, connect)


if __name__ == '__main__':
    unittest.main()
