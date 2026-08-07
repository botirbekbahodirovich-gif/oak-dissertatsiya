# -*- coding: utf-8 -*-
"""Crossref klienti — bibliografik ma'lumotning ASOSIY manbai (§10).

Crossref DOI registratsiya agentligi: sarlavha, jurnal, sana, jild/son/betlar
va mualliflar tartibi shu yerdan olinadi. OpenAlex ular ustiga identifikator
va institut boyitishini qo'shadi, lekin bibliografiyani qayta yozmaydi.

Odobli murojaat (`polite pool`): Crossref User-Agent va `mailto` bilan kelgan
so'rovlarni alohida, barqarorroq navbatda xizmat qiladi. E-mail sozlanmagan
bo'lsa ham so'rov ishlaydi — shunchaki sekinroq navbatga tushadi, shuning
uchun bu `not_configured` sabab emas.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from services.article_metadata.http import fetch_json
from services.article_metadata.models import CROSSREF, OK, ProviderResult
from services.article_metadata.normalizer import (MAX_AFFILIATION, MAX_JOURNAL,
                                                  MAX_NAME, MAX_SHORT, MAX_TITLE,
                                                  clean_abstract, clean_text,
                                                  date_from_parts, map_license_url,
                                                  normalize_issn, normalize_orcid,
                                                  year_from_parts)

# Crossref `type` — "journal-article", "posted-content" (preprint) va h.k.
# Biz uni qayta yozmaymiz, faqat adminga ko'rsatamiz.
MAX_AUTHORS = 60
MAX_LICENSES = 8


def fetch(doi, config, sleep=None):
    """Bitta DOI uchun `ProviderResult`. Istisno tashlamaydi."""
    email = config.crossref_contact
    params = {'mailto': email} if email else None
    url = '%s/%s' % (config.crossref_base.rstrip('/'), quote(doi, safe='/'))
    kwargs = {'sleep': sleep} if sleep is not None else {}
    outcome = fetch_json(url, provider=CROSSREF, config=config, params=params,
                         headers={'User-Agent': config.user_agent(email)}, **kwargs)

    if not outcome.ok:
        return ProviderResult(provider=CROSSREF, status=outcome.status,
                              http_status=outcome.http_status,
                              retry_after=outcome.retry_after,
                              elapsed_ms=outcome.elapsed_ms,
                              error_code=outcome.detail)

    message = (outcome.payload or {}).get('message')
    if not isinstance(message, dict):
        from services.article_metadata.models import INVALID_RESPONSE
        return ProviderResult(provider=CROSSREF, status=INVALID_RESPONSE,
                              http_status=outcome.http_status,
                              elapsed_ms=outcome.elapsed_ms,
                              error_code='message maydoni yo‘q')

    data, warnings = extract(message)
    return ProviderResult(provider=CROSSREF, status=OK,
                          fetched_at=datetime.now(timezone.utc), data=data,
                          warnings=warnings, http_status=outcome.http_status,
                          elapsed_ms=outcome.elapsed_ms)


def extract(message):
    """Crossref `message` -> kesilgan, tozalangan dict.

    Butun javob saqlanmaydi (§20): bizga kerak bo'lgan maydonlargina olinadi,
    shu holda keshga ham, normalizatorga ham o'sha boradi.
    """
    warnings = []

    title = _first_string(message.get('title'), MAX_TITLE)
    subtitle = _first_string(message.get('subtitle'), MAX_TITLE)
    journal = _first_string(message.get('container-title'), MAX_JOURNAL)

    published = (message.get('published')
                 or message.get('published-print')
                 or message.get('published-online')
                 or message.get('issued'))
    publication_date = date_from_parts(published)
    publication_year = year_from_parts(published)
    online_date = date_from_parts(message.get('published-online'))
    if publication_date is None and online_date is not None:
        publication_date = online_date

    pages = clean_text(message.get('page'), MAX_SHORT)
    if not pages:
        # Elektron jurnallarda bet o'rniga maqola raqami bo'ladi.
        pages = clean_text(message.get('article-number'), MAX_SHORT)

    abstract = clean_abstract(message.get('abstract'))
    if message.get('abstract') and not abstract:
        warnings.append('Crossref annotatsiyasi tozalangandan keyin bo‘sh qoldi.')

    authors, author_warnings = _authors(message.get('author'))
    warnings.extend(author_warnings)

    licenses = _licenses(message.get('license'))

    issn = []
    for value in (message.get('ISSN') or []):
        normalized = normalize_issn(value)
        if normalized and normalized not in issn:
            issn.append(normalized)

    # `issn-type` bo'lsa elektron/bosma farqi ma'lum bo'ladi; asosiysi
    # sifatida elektron ISSN afzal (jurnal saytlari shuni ishlatadi).
    primary_issn = None
    for entry in (message.get('issn-type') or []):
        if isinstance(entry, dict) and entry.get('type') == 'electronic':
            primary_issn = normalize_issn(entry.get('value'))
            if primary_issn:
                break
    if not primary_issn and issn:
        primary_issn = issn[0]

    member = clean_text(message.get('member'), 32)

    return {
        'doi': clean_text(message.get('DOI'), 255),
        'title': title,
        'subtitle': subtitle,
        'abstract': abstract,
        'journal_name': journal,
        'publisher': clean_text(message.get('publisher'), MAX_JOURNAL),
        'publication_year': publication_year,
        'publication_date': publication_date.isoformat() if publication_date else None,
        'online_date': online_date.isoformat() if online_date else None,
        'volume': clean_text(message.get('volume'), MAX_SHORT),
        'issue': clean_text(message.get('issue'), MAX_SHORT),
        'pages': pages,
        'language': clean_text(message.get('language'), 8),
        'issn': issn,
        'primary_issn': primary_issn,
        'work_type': clean_text(message.get('type'), 50),
        'external_url': clean_text(message.get('URL'), 1000),
        'crossref_member_id': member,
        'authors': authors,
        'licenses': licenses,
        'is_referenced_by_count': _int_or_none(message.get('is-referenced-by-count')),
        'update_to': _update_to(message.get('update-to')),
    }, warnings


def _first_string(value, max_length):
    if isinstance(value, list):
        value = value[0] if value else None
    return clean_text(value, max_length)


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _update_to(entries):
    """Crossref `update-to` — chaqirib olish (retraction) kabi belgilar.

    Bu maydon signal sifatida saqlanadi: chaqirib olingan maqola jimgina
    o'tib ketmasligi kerak (§26). Yakuniy qarorni admin qabul qiladi.
    """
    out = []
    for entry in (entries or [])[:5]:
        if not isinstance(entry, dict):
            continue
        out.append({
            'type': clean_text(entry.get('type'), 50),
            'doi': clean_text(entry.get('DOI'), 255),
            'label': clean_text(entry.get('label'), 120),
        })
    return out


def _authors(entries):
    warnings = []
    authors = []
    if not isinstance(entries, list):
        return authors, warnings
    if len(entries) > MAX_AUTHORS:
        warnings.append('Crossref %d ta muallif qaytardi; birinchi %d tasi olindi.'
                        % (len(entries), MAX_AUTHORS))
    for index, entry in enumerate(entries[:MAX_AUTHORS]):
        if not isinstance(entry, dict):
            continue
        given = clean_text(entry.get('given'), MAX_NAME)
        family = clean_text(entry.get('family'), MAX_NAME)
        name = ' '.join(p for p in (given, family) if p) or clean_text(entry.get('name'), MAX_NAME)
        if not name:
            continue
        affiliations = []
        for aff in (entry.get('affiliation') or [])[:5]:
            if isinstance(aff, dict):
                text = clean_text(aff.get('name'), MAX_AFFILIATION)
                if text:
                    affiliations.append(text)
        authors.append({
            'name': name[:MAX_NAME],
            'given': given,
            'family': family,
            'orcid': normalize_orcid(entry.get('ORCID')),
            'affiliation': affiliations[0] if affiliations else None,
            'affiliations': affiliations,
            'is_corresponding': bool(entry.get('sequence') == 'first'
                                     and entry.get('authenticated-orcid')),
            'order': index + 1,
        })
    return authors, warnings


def _licenses(entries):
    """Crossref litsenziyalari — NOMZOD sifatida, tanlanmagan holda (§28)."""
    out = []
    for entry in (entries or [])[:MAX_LICENSES]:
        if not isinstance(entry, dict):
            continue
        url = clean_text(entry.get('URL'), 500)
        if not url:
            continue
        license_type, label = map_license_url(url)
        out.append({
            'license_type': license_type,
            'url': url,
            'label': label,
            # `vor` (version of record), `am` (accepted manuscript), `tdm`.
            'applies_to': clean_text(entry.get('content-version'), 40),
        })
    return out
