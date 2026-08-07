# -*- coding: utf-8 -*-
"""OpenAlex klienti — identifikator va kontekst BOYITISHI (§10).

OpenAlex bu yerda bibliografiyani qayta yozmaydi: uning vazifasi — OpenAlex
ID'lari, muallif ORCID/ID'lari, institutlar (ROR), mavzular, ochiq kirish
signali, iqtibos soni va chaqirib olish (retraction) bayrog'i.

KALIT SIYOSATI. Vazifa matni OpenAlex uchun API kalitini talab qiladi, lekin
loyihada OpenAlex allaqachon KALITSIZ ishlaydi: `blueprints/maqolalar.py`
`mailto` bilan "polite pool"dan foydalanadi va `.env.example` da ham shunday
yozilgan. Shu sababli:

  * `OPENALEX_API_KEY` bo'lsa — ishlatiladi (yuqori limit);
  * bo'lmasa, lekin kontakt e-mail bo'lsa — polite pool (mavjud xatti-harakat
    saqlanadi, regressiya yo'q);
  * ikkalasi ham bo'lmasa — `not_configured`, va Crossref bilan Unpaywall
    ishlashda davom etadi (§23).

Kalit qiymati HECH QACHON log'ga, javobga yoki brauzerga chiqmaydi.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from services.article_metadata.http import fetch_json
from services.article_metadata.models import (INVALID_RESPONSE, NOT_CONFIGURED,
                                              OK, OPENALEX, ProviderResult)
from services.article_metadata.normalizer import (MAX_AFFILIATION, MAX_JOURNAL,
                                                  MAX_NAME, MAX_TITLE, clean_text,
                                                  normalize_issn, normalize_orcid,
                                                  openalex_author_id,
                                                  openalex_institution_id,
                                                  openalex_short_id, parse_iso_date,
                                                  valid_year)

MAX_AUTHORS = 60
MAX_TOPICS = 10
MAX_KEYWORDS = 15
MAX_RELATED = 10


def fetch(doi, config, sleep=None):
    """Bitta DOI uchun `ProviderResult`. Istisno tashlamaydi."""
    if not config.openalex_api_key and not config.openalex_contact:
        return ProviderResult(provider=OPENALEX, status=NOT_CONFIGURED,
                              error_code='OPENALEX_API_KEY yoki kontakt e-mail yo‘q')

    params = {}
    if config.openalex_api_key:
        params['api_key'] = config.openalex_api_key
    elif config.openalex_contact:
        params['mailto'] = config.openalex_contact

    url = '%s/doi:%s' % (config.openalex_base.rstrip('/'), quote(doi, safe='/'))
    kwargs = {'sleep': sleep} if sleep is not None else {}
    outcome = fetch_json(url, provider=OPENALEX, config=config, params=params,
                         headers={'User-Agent': config.user_agent(config.openalex_contact)},
                         **kwargs)

    if not outcome.ok:
        return ProviderResult(provider=OPENALEX, status=outcome.status,
                              http_status=outcome.http_status,
                              retry_after=outcome.retry_after,
                              elapsed_ms=outcome.elapsed_ms,
                              error_code=outcome.detail)

    work = outcome.payload or {}
    if not work.get('id'):
        return ProviderResult(provider=OPENALEX, status=INVALID_RESPONSE,
                              http_status=outcome.http_status,
                              elapsed_ms=outcome.elapsed_ms,
                              error_code='work id yo‘q')

    data, warnings = extract(work)
    return ProviderResult(provider=OPENALEX, status=OK,
                          fetched_at=datetime.now(timezone.utc), data=data,
                          warnings=warnings, http_status=outcome.http_status,
                          elapsed_ms=outcome.elapsed_ms)


def extract(work):
    """OpenAlex `work` -> kesilgan, tozalangan dict.

    ANNOTATSIYA ATAYIN OLINMAYDI. OpenAlex uni faqat `abstract_inverted_index`
    ko'rinishida beradi; uni qayta tiklash — nashriyot annotatsiyasining hosila
    nusxasini yaratish, va bizga bunday huquq berilganini tekshirmaganmiz.
    Crossref annotatsiyani ochiq matn sifatida beradi va u birlamchi manba (§6).
    """
    warnings = []
    primary_location = work.get('primary_location') or {}
    source = primary_location.get('source') or {}
    open_access = work.get('open_access') or {}
    best_oa = work.get('best_oa_location') or {}

    issn = []
    for value in (source.get('issn') or []):
        normalized = normalize_issn(value)
        if normalized and normalized not in issn:
            issn.append(normalized)
    primary_issn = normalize_issn(source.get('issn_l')) or (issn[0] if issn else None)

    is_retracted = work.get('is_retracted')
    if is_retracted:
        warnings.append('OpenAlex bu maqolani CHAQIRIB OLINGAN (retracted) deb belgilagan.')

    authors, author_warnings = _authorships(work.get('authorships'))
    warnings.extend(author_warnings)

    if work.get('abstract_inverted_index'):
        warnings.append('OpenAlex annotatsiyani faqat teskari indeks sifatida '
                        'beradi — u olinmadi (Crossref birlamchi manba).')

    return {
        'openalex_id': openalex_short_id(work.get('id')),
        'doi': clean_text((work.get('doi') or '').replace('https://doi.org/', ''), 255),
        'title': clean_text(work.get('title') or work.get('display_name'), MAX_TITLE),
        'publication_year': valid_year(work.get('publication_year')),
        'publication_date': _iso(parse_iso_date(work.get('publication_date'))),
        'work_type': clean_text(work.get('type'), 50),
        'language': clean_text(work.get('language'), 8),
        'cited_by_count': _int_or_none(work.get('cited_by_count')),
        'is_retracted': bool(is_retracted) if is_retracted is not None else None,
        'is_paratext': bool(work.get('is_paratext')) if work.get('is_paratext') is not None else None,
        'journal_name': clean_text(source.get('display_name'), MAX_JOURNAL),
        'publisher': clean_text(source.get('host_organization_name'), MAX_JOURNAL),
        'issn': issn,
        'primary_issn': primary_issn,
        'source_is_oa': _bool_or_none(source.get('is_oa')),
        'source_in_doaj': _bool_or_none(source.get('is_in_doaj')),
        'volume': clean_text((work.get('biblio') or {}).get('volume'), 50),
        'issue': clean_text((work.get('biblio') or {}).get('issue'), 50),
        'pages': _pages(work.get('biblio')),
        'is_oa': _bool_or_none(open_access.get('is_oa')),
        'oa_status': clean_text(open_access.get('oa_status'), 24),
        'oa_url': clean_text(open_access.get('oa_url'), 1000),
        'landing_page_url': clean_text(primary_location.get('landing_page_url'), 1000),
        'best_oa_location': _location(best_oa),
        'authors': authors,
        'topics': _topics(work.get('topics'), work.get('primary_topic')),
        'keywords': _keywords(work.get('keywords')),
        'related_works': [openalex_short_id(r) for r in (work.get('related_works') or [])[:MAX_RELATED]
                          if openalex_short_id(r)],
    }, warnings


def _iso(value):
    return value.isoformat() if value else None


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value):
    return bool(value) if value is not None else None


def _pages(biblio):
    if not isinstance(biblio, dict):
        return None
    first = clean_text(biblio.get('first_page'), 24)
    last = clean_text(biblio.get('last_page'), 24)
    if first and last:
        return ('%s-%s' % (first, last))[:50]
    return first or last


def _location(location):
    if not isinstance(location, dict) or not location:
        return None
    return {
        'landing_page_url': clean_text(location.get('landing_page_url'), 1000),
        'pdf_url': clean_text(location.get('pdf_url'), 1000),
        'host_type': clean_text((location.get('source') or {}).get('type'), 40),
        'version': clean_text(location.get('version'), 40),
        'license': clean_text(location.get('license'), 60),
    }


def _authorships(entries):
    warnings = []
    authors = []
    if not isinstance(entries, list):
        return authors, warnings
    if len(entries) > MAX_AUTHORS:
        warnings.append('OpenAlex %d ta muallif qaytardi; birinchi %d tasi olindi.'
                        % (len(entries), MAX_AUTHORS))
    for index, entry in enumerate(entries[:MAX_AUTHORS]):
        if not isinstance(entry, dict):
            continue
        author = entry.get('author') or {}
        name = clean_text(author.get('display_name'), MAX_NAME)
        if not name:
            continue
        institutions, ror = [], []
        for inst in (entry.get('institutions') or [])[:5]:
            if not isinstance(inst, dict):
                continue
            label = clean_text(inst.get('display_name'), MAX_AFFILIATION)
            if label:
                institutions.append(label)
            identifier = openalex_institution_id(inst.get('id'))
            if identifier:
                ror.append(identifier)
        raw_affiliation = None
        raw_list = entry.get('raw_affiliation_strings') or []
        if raw_list:
            raw_affiliation = clean_text(raw_list[0], MAX_AFFILIATION)
        authors.append({
            'name': name,
            'orcid': normalize_orcid(author.get('orcid')),
            'openalex_author_id': openalex_author_id(author.get('id')),
            'institutions': institutions,
            'institution_ids': ror,
            'ror': [clean_text((i or {}).get('ror'), 120)
                    for i in (entry.get('institutions') or [])[:5]
                    if isinstance(i, dict) and i.get('ror')],
            'affiliation': institutions[0] if institutions else raw_affiliation,
            'is_corresponding': bool(entry.get('is_corresponding')),
            'order': index + 1,
        })
    return authors, warnings


def _topics(topics, primary):
    """Mavzular — OAK yo'nalishi EMAS (§25), faqat adminga ko'rsatiladi."""
    primary_id = (primary or {}).get('id') if isinstance(primary, dict) else None
    out = []
    for entry in (topics or [])[:MAX_TOPICS]:
        if not isinstance(entry, dict):
            continue
        name = clean_text(entry.get('display_name'), 200)
        if not name:
            continue
        out.append({
            'name': name,
            'score': _score(entry.get('score')),
            'is_primary': bool(primary_id and entry.get('id') == primary_id),
        })
    if primary_id and not any(t['is_primary'] for t in out):
        name = clean_text(primary.get('display_name'), 200)
        if name:
            out.insert(0, {'name': name, 'score': _score(primary.get('score')),
                           'is_primary': True})
    return out


def _keywords(entries):
    out = []
    for entry in (entries or [])[:MAX_KEYWORDS]:
        if isinstance(entry, dict):
            name = clean_text(entry.get('display_name') or entry.get('keyword'), 120)
            score = _score(entry.get('score'))
        else:
            name, score = clean_text(entry, 120), None
        if name:
            out.append({'name': name, 'score': score})
    return out


def _score(value):
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None
