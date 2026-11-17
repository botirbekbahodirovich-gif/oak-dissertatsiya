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

from services.article_discovery.models import DiscoveryCandidate, DiscoverySearchResult
from services.article_metadata.http import fetch_json
from services.article_metadata.models import (INVALID_RESPONSE, NOT_CONFIGURED,
                                              OK, OPENALEX)
from services.article_metadata.normalizer import (clean_text, clean_title,
                                                  openalex_short_id,
                                                  parse_iso_date)
from utils.article_rules import normalize_doi

MAX_TOPICS = 3

# `select=` — faqat preview uchun kerakli, arzon maydonlar (§9). Xom metadata
# candidate'ga yozilmaydi (§10).
SELECT_FIELDS = ('id,doi,title,display_name,publication_date,'
                 'primary_location,is_retracted,primary_topic')


def search(query, config, *, sleep=None):
    """`ArticleDiscoveryQuery` -> `DiscoverySearchResult`. Istisno tashlamaydi.

    Faqat BOUNDED, bitta sahifali so'rov (§33) — cursor bilan minglab
    natijani avtomatik yig'ish YO'Q.
    """
    if not config.is_configured:
        return DiscoverySearchResult(status=NOT_CONFIGURED)

    per_page = max(1, min(query.limit, config.hard_result_limit))
    params = {
        'filter': build_filter(query.from_date, query.to_date,
                               country_code=getattr(query, 'country_code', None),
                               core_only=getattr(query, 'core_only', False)),
        'per_page': str(per_page),
        'page': '1',
        'select': SELECT_FIELDS,
    }
    # Kalit so'z faqat berilgan bo'lsa (UZ/core filtrida ixtiyoriy).
    if query.keyword:
        params = dict({'search': query.keyword}, **params)
    _auth_params(params, config)

    kwargs = {'sleep': sleep} if sleep is not None else {}
    outcome = fetch_json(config.openalex_base, provider=OPENALEX, config=config,
                         params=params,
                         headers={'User-Agent': config.user_agent()}, **kwargs)
    return _result_from_outcome(outcome)


def build_filter(from_date, to_date, *, country_code=None, core_only=False):
    """OpenAlex `filter=` qiymati. Tartib deterministik (testlar va kesh)."""
    parts = [
        'has_doi:true',
        'type:article',
        'from_publication_date:%s' % from_date.isoformat(),
        'to_publication_date:%s' % to_date.isoformat(),
    ]
    if country_code:
        code = str(country_code).strip().upper()
        if not (len(code) == 2 and code.isalpha()):
            raise ValueError('country_code ikki harfli ISO kod bo\'lishi kerak')
        parts.append('authorships.institutions.country_code:%s' % code)
    if core_only:
        parts.append('primary_location.source.is_core:true')
    return ','.join(parts)


def _auth_params(params, config):
    if config.openalex_api_key:
        params['api_key'] = config.openalex_api_key
    elif config.openalex_contact:
        params['mailto'] = config.openalex_contact


CURSOR_PER_PAGE = 200          # OpenAlex ruxsat etgan maksimal sahifa hajmi


def iter_cursor(config, *, from_date, to_date, limit, country_code=None,
                core_only=False, keyword=None, per_page=CURSOR_PER_PAGE,
                sleep=None, fetch=fetch_json):
    """CLI uchun cursor pagination (`cursor=*`). Admin UI bitta sahifali
    `search()` dan foydalanadi (§33 o'zgarmagan) — bu funksiya faqat
    `scripts/ingest_uz_articles.py` uchun.

    `limit` — QAT'IY umumiy chegara (majburiy, > 0): shu sondan ortiq
    nomzod hech qachon qaytarilmaydi va keyingi sahifa so'ralmaydi.

    Har sahifa uchun bitta `(page_result, next_cursor)` beradi;
    `page_result` — `DiscoverySearchResult` (status, candidates,
    total_count, excluded_no_doi_count). Xato sahifasida iteratsiya
    to'xtaydi (xato natija oxirgi element bo'ladi).
    """
    if not limit or int(limit) <= 0:
        raise ValueError('limit majburiy va musbat bo\'lishi kerak')
    if not config.is_configured:
        yield DiscoverySearchResult(status=NOT_CONFIGURED), None
        return
    limit = int(limit)
    per_page = max(1, min(int(per_page), CURSOR_PER_PAGE))
    cursor = '*'
    remaining = limit
    while cursor and remaining > 0:
        params = {
            'filter': build_filter(from_date, to_date, country_code=country_code,
                                   core_only=core_only),
            'per_page': str(min(per_page, remaining)),
            'cursor': cursor,
            'select': SELECT_FIELDS,
        }
        if keyword:
            params = dict({'search': keyword}, **params)
        _auth_params(params, config)
        kwargs = {'sleep': sleep} if sleep is not None else {}
        outcome = fetch(config.openalex_base, provider=OPENALEX, config=config,
                        params=params, headers={'User-Agent': config.user_agent()},
                        **kwargs)
        result = _result_from_outcome(outcome)
        if not result.ok:
            yield result, None
            return
        # Qat'iy chegara: sahifa kutilganidan ko'p qaytarsa ham kesiladi.
        if len(result.candidates) > remaining:
            result.candidates = result.candidates[:remaining]
        remaining -= len(result.candidates) + result.excluded_no_doi_count
        next_cursor = ((outcome.payload or {}).get('meta') or {}).get('next_cursor')
        raw_results = (outcome.payload or {}).get('results') or []
        if not raw_results:
            next_cursor = None
        yield result, next_cursor
        cursor = next_cursor


def _result_from_outcome(outcome):
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
        title=clean_title(work.get('title') or work.get('display_name'), 500),
        publication_date=parse_iso_date(work.get('publication_date')),
        source_name=clean_text(source.get('display_name'), 300),
        is_retracted=bool(work.get('is_retracted')),
        topics=topics[:MAX_TOPICS],
        providers=[OPENALEX],
    )


def _int_or_zero(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
