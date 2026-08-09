# -*- coding: utf-8 -*-
"""Crossref / OpenAlex / Unpaywall uchun soxta HTTP qatlami va fixture'lar.

Testlar TASHQI TARMOQQA CHIQMAYDI (§34). Bu yerdagi `FakeSession`
`services.article_metadata.http.get_session` o'rniga qo'yiladi, shuning uchun
haqiqiy retry/backoff/status mantiqi ishlaydi — faqat soket almashadi.

Fixture'lar haqiqiy API javoblarining tuzilishini takrorlaydi (kesilgan,
lekin maydon nomlari va ichma-ich joylashuvi asl). Ular hech qanday kalit
yoki e-mail saqlamaydi.
"""
from __future__ import annotations

import copy

import requests

# ── Crossref ────────────────────────────────────────────────────────────────

CROSSREF_WORK = {
    'status': 'ok',
    'message-type': 'work',
    'message': {
        'DOI': '10.1234/abcd.2024.01',
        'type': 'journal-article',
        'title': ['Raqamli ta‘lim muhitida talabalar motivatsiyasi'],
        'subtitle': ['Uzunlamasına tadqiqot'],
        'container-title': ['Journal of Educational Research'],
        'publisher': 'Example Academic Press',
        'member': '286',
        'volume': '42',
        'issue': '3',
        'page': '215-231',
        'language': 'en',
        'URL': 'https://doi.org/10.1234/abcd.2024.01',
        'abstract': '<jats:p><jats:title>Abstract</jats:title>'
                    'Ushbu <jats:italic>tadqiqot</jats:italic> raqamli '
                    'muhitni o‘rganadi.</jats:p>',
        'ISSN': ['1234-5678', '8765-4321'],
        'issn-type': [
            {'type': 'print', 'value': '1234-5678'},
            {'type': 'electronic', 'value': '8765-4321'},
        ],
        'published': {'date-parts': [[2024, 3, 15]]},
        'published-online': {'date-parts': [[2024, 2, 28]]},
        'is-referenced-by-count': 7,
        'license': [
            {'URL': 'http://creativecommons.org/licenses/by/4.0/',
             'content-version': 'vor',
             'start': {'date-parts': [[2024, 3, 15]]}},
        ],
        'author': [
            {'given': 'Sardor', 'family': 'Karimov',
             'ORCID': 'http://orcid.org/0000-0002-1825-0097',
             'sequence': 'first', 'authenticated-orcid': True,
             'affiliation': [{'name': 'Toshkent davlat pedagogika universiteti'}]},
            {'given': 'Nilufar', 'family': 'Yusupova', 'sequence': 'additional',
             'affiliation': []},
        ],
    },
}


def crossref_work(**overrides):
    payload = copy.deepcopy(CROSSREF_WORK)
    payload['message'].update(overrides)
    return payload


# ── OpenAlex ────────────────────────────────────────────────────────────────

OPENALEX_WORK = {
    'id': 'https://openalex.org/W2741809807',
    'doi': 'https://doi.org/10.1234/abcd.2024.01',
    'title': 'Raqamli ta‘lim muhitida talabalar motivatsiyasi',
    'display_name': 'Raqamli ta‘lim muhitida talabalar motivatsiyasi',
    'publication_year': 2024,
    'publication_date': '2024-03-15',
    'type': 'article',
    'language': 'en',
    'cited_by_count': 11,
    'is_retracted': False,
    'is_paratext': False,
    'abstract_inverted_index': {'Ushbu': [0], 'tadqiqot': [1]},
    'biblio': {'volume': '42', 'issue': '3', 'first_page': '215', 'last_page': '231'},
    'primary_location': {
        'landing_page_url': 'https://journal.example.org/articles/42-3-215',
        'source': {
            'display_name': 'Journal of Educational Research',
            'host_organization_name': 'Example Academic Press',
            'issn_l': '8765-4321',
            'issn': ['1234-5678', '8765-4321'],
            'is_oa': True,
            'is_in_doaj': True,
            'type': 'journal',
        },
    },
    'open_access': {
        'is_oa': True,
        'oa_status': 'gold',
        'oa_url': 'https://journal.example.org/pdf/42-3-215.pdf',
    },
    'best_oa_location': {
        'landing_page_url': 'https://journal.example.org/articles/42-3-215',
        'pdf_url': 'https://journal.example.org/pdf/42-3-215.pdf',
        'version': 'publishedVersion',
        'license': 'cc-by',
        'source': {'type': 'journal'},
    },
    'authorships': [
        {'author': {'id': 'https://openalex.org/A5023888391',
                    'display_name': 'Sardor Karimov',
                    'orcid': 'https://orcid.org/0000-0002-1825-0097'},
         'institutions': [{'id': 'https://openalex.org/I1234567',
                           'display_name': 'Tashkent State Pedagogical University',
                           'ror': 'https://ror.org/01abcd234'}],
         'raw_affiliation_strings': ['Tashkent State Pedagogical University'],
         'is_corresponding': True},
        {'author': {'id': 'https://openalex.org/A5099887766',
                    'display_name': 'Nilufar Yusupova', 'orcid': None},
         'institutions': [],
         'raw_affiliation_strings': ['Independent researcher'],
         'is_corresponding': False},
    ],
    'primary_topic': {'id': 'https://openalex.org/T10123',
                      'display_name': 'Digital Learning Environments',
                      'score': 0.9812},
    'topics': [
        {'id': 'https://openalex.org/T10123',
         'display_name': 'Digital Learning Environments', 'score': 0.9812},
        {'id': 'https://openalex.org/T10456',
         'display_name': 'Student Motivation', 'score': 0.8421},
    ],
    'keywords': [
        {'display_name': 'motivation', 'score': 0.61},
        {'display_name': 'e-learning', 'score': 0.55},
    ],
    'related_works': ['https://openalex.org/W111111', 'https://openalex.org/W222222'],
}


def openalex_work(**overrides):
    payload = copy.deepcopy(OPENALEX_WORK)
    payload.update(overrides)
    return payload


# ── Unpaywall ───────────────────────────────────────────────────────────────

UNPAYWALL_RECORD = {
    'doi': '10.1234/abcd.2024.01',
    'title': 'Raqamli ta‘lim muhitida talabalar motivatsiyasi',
    'genre': 'journal-article',
    'is_oa': True,
    'oa_status': 'gold',
    'journal_name': 'Journal of Educational Research',
    'journal_is_oa': True,
    'journal_is_in_doaj': True,
    'publisher': 'Example Academic Press',
    'published_date': '2024-03-15',
    'year': 2024,
    'is_paratext': False,
    'best_oa_location': {
        'url': 'https://journal.example.org/articles/42-3-215',
        'url_for_landing_page': 'https://journal.example.org/articles/42-3-215',
        'url_for_pdf': 'https://journal.example.org/pdf/42-3-215.pdf',
        'host_type': 'publisher',
        'version': 'publishedVersion',
        'license': 'cc-by',
    },
    'oa_locations': [
        {'url_for_landing_page': 'https://journal.example.org/articles/42-3-215',
         'url_for_pdf': 'https://journal.example.org/pdf/42-3-215.pdf',
         'host_type': 'publisher', 'version': 'publishedVersion', 'license': 'cc-by'},
        {'url_for_landing_page': 'https://repo.example.edu/handle/1',
         'url_for_pdf': 'https://repo.example.edu/bitstream/1/am.pdf',
         'host_type': 'repository', 'version': 'acceptedVersion', 'license': None},
    ],
}


def unpaywall_record(**overrides):
    payload = copy.deepcopy(UNPAYWALL_RECORD)
    payload.update(overrides)
    return payload


# ── Soxta HTTP ──────────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self._text = text

    def json(self):
        if self._text is not None:
            raise ValueError('JSON emas')
        return self._payload


class Timeout(requests.exceptions.Timeout):
    pass


class ConnectionFailure(requests.exceptions.ConnectionError):
    pass


class FakeSession:
    """Har bir host uchun oldindan yozilgan javoblar.

    `routes` — URL'ning bir qismi -> javob, javoblar ro'yxati (ketma-ket
    urinishlar uchun) yoki istisno klassi/namunasi.
    """

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({'url': url, 'params': dict(params or {}),
                           'headers': dict(headers or {}), 'timeout': timeout})
        for fragment, response in self.routes.items():
            if fragment in url:
                return self._resolve(fragment, response)
        return FakeResponse(404, {})

    def _resolve(self, fragment, response):
        if isinstance(response, list):
            current = response.pop(0) if len(response) > 1 else response[0]
            return self._resolve(fragment, current)
        if isinstance(response, type) and issubclass(response, BaseException):
            raise response()
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            return response()
        return response


def no_sleep(_seconds):
    """Backoff testlarni sekinlashtirmasin."""
    return None
