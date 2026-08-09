# -*- coding: utf-8 -*-
"""OpenAlex `/works` RO'YXAT/QIDIRUV klienti — discovery uchun (Phase 2D.1).

Bu `services/article_metadata/openalex.py`dan MUSTAQIL: o'sha modul BITTA
DOI'ni `/works/doi:...` orqali BOYITADI (identifikator/kontekst, §10 —
authoritative enrichment). Bu yerdagi vazifa boshqa: `/works?search=...`
orqali KO'PLAB nomzodni TOPISH — natija HECH QACHON to'g'ridan-to'g'ri
`scientific_articles`ga yozilmaydi (§9, §29).

`blueprints/maqolalar.py`dagi eski private OpenAlex helperlar (process-local
kesh, broad exception handling, eski auth taxmin) BU YERGA IMPORT QILINMAYDI —
faqat umumiy, Flask'dan mustaqil `services/article_metadata/http.py:fetch_json`
va `services/article_metadata/normalizer.py` qayta ishlatiladi (§6).

KALIT SIYOSATI — `services/article_metadata/openalex.py` bilan bir xil (§7):
  * `OPENALEX_API_KEY` bo'lsa — ishlatiladi;
  * bo'lmasa, lekin kontakt e-mail bo'lsa — polite pool;
  * ikkalasi ham bo'lmasa — `not_configured` (metadata autofill buzilmaydi,
    faqat discovery o'zi ishlamaydi).
"""
from __future__ import annotations

from services.article_discovery.models import DiscoveryCandidate
from services.article_metadata.http import fetch_json
from services.article_metadata.models import (INVALID_RESPONSE, NOT_CONFIGURED,
                                              OK, OPENALEX)
from services.article_metadata.normalizer import (clean_text, openalex_short_id,
                                                  parse_iso_date)
from utils.article_rules import normalize_doi

MAX_TOPICS = 3

# `select=` — faqat preview uchun kerakli, arzon maydonlar (§9). Xom metadata
# candidate'ga yozilmaydi (§10).
SELECT_FIELDS = ('id,doi,title,display_name,publication_date,'
                 'primary_location,is_retracted,primary_topic')


class DiscoverySearchResult:
    """`ProviderResult` (article_metadata) bilan bir xil naqsh — lekin
    ro'yxat natijasi uchun."""

    __slots__ = ('status', 'candidates', 'total_count', 'excluded_no_doi_count',
                'http_status', 'error_code')

    def __init__(self, status, candidates=None, total_count=0,
                excluded_no_doi_count=0, http_status=None, error_code=None):
        self.status = status
        self.candidates = candidates or []
        self.total_count = total_count
        self.excluded_no_doi_count = excluded_no_doi_count
        self.http_status = http_status
        self.error_code = error_code

    @property
    def ok(self):
        return self.status == OK


def search(query, config, *, sleep=None):
    """`ArticleDiscoveryQuery` -> `DiscoverySearchResult`. Istisno tashlamaydi.

    Faqat BOUNDED, bitta sahifali so'rov (§33) — cursor bilan minglab
    natijani avtomatik yig'ish YO'Q.
    """
    if not config.is_configured:
        return DiscoverySearchResult(status=NOT_CONFIGURED)

    per_page = max(1, min(query.limit, config.hard_result_limit))
    params = {
        'search': query.keyword,
        'filter': ','.join([
            'has_doi:true',
            'type:article',
            'from_publication_date:%s' % query.from_date.isoformat(),
            'to_publication_date:%s' % query.to_date.isoformat(),
        ]),
        'per_page': str(per_page),
        'page': '1',
        'select': SELECT_FIELDS,
    }
    if config.openalex_api_key:
        params['api_key'] = config.openalex_api_key
    elif config.openalex_contact:
        params['mailto'] = config.openalex_contact

    kwargs = {'sleep': sleep} if sleep is not None else {}
    outcome = fetch_json(config.openalex_base, provider=OPENALEX, config=config,
                         params=params,
                         headers={'User-Agent': config.user_agent()}, **kwargs)

    if not outcome.ok:
        return DiscoverySearchResult(status=outcome.status,
                                     http_status=outcome.http_status,
                                     error_code=outcome.detail)

    payload = outcome.payload or {}
    results = payload.get('results')
    if not isinstance(results, list):
        return DiscoverySearchResult(status=INVALID_RESPONSE,
                                     http_status=outcome.http_status,
                                     error_code="'results' ro'yxat emas")

    total_count = _int_or_zero((payload.get('meta') or {}).get('count'))

    candidates, excluded_no_doi = [], 0
    for work in results:
        if not isinstance(work, dict):
            continue
        candidate = _extract_candidate(work)
        if candidate is None:
            excluded_no_doi += 1
            continue
        candidates.append(candidate)

    return DiscoverySearchResult(status=OK, candidates=candidates,
                                 total_count=total_count,
                                 excluded_no_doi_count=excluded_no_doi,
                                 http_status=outcome.http_status)


def _extract_candidate(work):
    """DOI'siz OpenAlex work -> `None` (§11 — DOI-only policy, statistikada
    sanaladi lekin candidate YARATILMAYDI)."""
    doi = normalize_doi(work.get('doi'))
    if not doi:
        return None

    primary_location = work.get('primary_location') or {}
    source = primary_location.get('source') or {}
    if not isinstance(source, dict):
        source = {}
    topic = work.get('primary_topic')
    topics = []
    if isinstance(topic, dict):
        name = clean_text(topic.get('display_name'), 200)
        if name:
            topics.append(name)

    return DiscoveryCandidate(
        doi=doi,
        external_source_id=openalex_short_id(work.get('id')),
        title=clean_text(work.get('title') or work.get('display_name'), 500),
        publication_date=parse_iso_date(work.get('publication_date')),
        source_name=clean_text(source.get('display_name'), 300),
        is_retracted=bool(work.get('is_retracted')),
        topics=topics[:MAX_TOPICS],
    )


def _int_or_zero(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
