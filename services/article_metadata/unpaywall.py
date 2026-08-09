# -*- coding: utf-8 -*-
"""Unpaywall klienti — ochiq kirish holatining BIRLAMCHI manbai (§10).

Unpaywall bibliografiya uchun ishlatilmaydi (u ham Crossref'dan oladi):
bizga undan `is_oa`, `oa_status`, eng yaxshi ochiq nusxa va litsenziya
nomzodi kerak.

MUHIM (§24): `best_oa_location.pdf_url` topilishi PDF'ni yuklab olish huquqi
EMAS. U faqat NOMZOD sifatida saqlanadi va admin ko'rigida ko'rsatiladi;
hech qachon avtomatik yuklanmaydi, `pdf_path` ga yozilmaydi va
`can_read_pdf()` ga ta'sir qilmaydi.

Unpaywall e-mail SO'RAYDI: e-mailsiz API 422 qaytaradi. Shu sababli e-mail
yo'q bo'lsa provayder `not_configured` bo'ladi — bu Crossref'dan farqi.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from services.article_metadata.http import fetch_json
from services.article_metadata.models import (NOT_CONFIGURED, OK, UNPAYWALL,
                                              ProviderResult)
from services.article_metadata.normalizer import (MAX_JOURNAL, MAX_TITLE,
                                                  clean_text, map_license_label,
                                                  parse_iso_date, valid_year)

MAX_LOCATIONS = 6


def fetch(doi, config, sleep=None):
    """Bitta DOI uchun `ProviderResult`. Istisno tashlamaydi."""
    email = config.unpaywall_contact
    if not email:
        return ProviderResult(provider=UNPAYWALL, status=NOT_CONFIGURED,
                              error_code='UNPAYWALL_EMAIL yoki kontakt e-mail yo‘q')

    url = '%s/%s' % (config.unpaywall_base.rstrip('/'), quote(doi, safe='/'))
    kwargs = {'sleep': sleep} if sleep is not None else {}
    outcome = fetch_json(url, provider=UNPAYWALL, config=config,
                         params={'email': email},
                         headers={'User-Agent': config.user_agent(email)}, **kwargs)

    if not outcome.ok:
        return ProviderResult(provider=UNPAYWALL, status=outcome.status,
                              http_status=outcome.http_status,
                              retry_after=outcome.retry_after,
                              elapsed_ms=outcome.elapsed_ms,
                              error_code=outcome.detail)

    data, warnings = extract(outcome.payload or {})
    return ProviderResult(provider=UNPAYWALL, status=OK,
                          fetched_at=datetime.now(timezone.utc), data=data,
                          warnings=warnings, http_status=outcome.http_status,
                          elapsed_ms=outcome.elapsed_ms)


def extract(payload):
    """Unpaywall javobi -> kesilgan dict."""
    warnings = []
    best = payload.get('best_oa_location')
    if payload.get('is_oa') and not isinstance(best, dict):
        warnings.append('Unpaywall ochiq deb belgiladi, lekin eng yaxshi nusxa '
                        'ko‘rsatilmagan.')

    locations = []
    if isinstance(best, dict):
        locations.append(_location(best, is_best=True))
    for entry in (payload.get('oa_locations') or [])[:MAX_LOCATIONS]:
        if not isinstance(entry, dict):
            continue
        location = _location(entry, is_best=False)
        if location['landing_page_url'] and any(
                location['landing_page_url'] == existing['landing_page_url']
                for existing in locations):
            continue
        locations.append(location)

    license_candidate = None
    if isinstance(best, dict) and best.get('license'):
        license_type, label = map_license_label(best.get('license'))
        license_candidate = {
            'license_type': license_type,
            'label': label,
            'url': None,
            'applies_to': clean_text(best.get('version'), 40),
        }

    return {
        'doi': clean_text(payload.get('doi'), 255),
        'title': clean_text(payload.get('title'), MAX_TITLE),
        'journal_name': clean_text(payload.get('journal_name'), MAX_JOURNAL),
        'publisher': clean_text(payload.get('publisher'), MAX_JOURNAL),
        'publication_year': valid_year(payload.get('year')),
        'publication_date': _iso(parse_iso_date(payload.get('published_date'))),
        'genre': clean_text(payload.get('genre'), 50),
        'is_oa': _bool_or_none(payload.get('is_oa')),
        'oa_status': clean_text(payload.get('oa_status'), 24),
        'journal_is_oa': _bool_or_none(payload.get('journal_is_oa')),
        'journal_is_in_doaj': _bool_or_none(payload.get('journal_is_in_doaj')),
        'is_paratext': _bool_or_none(payload.get('is_paratext')),
        'locations': locations,
        'license': license_candidate,
    }, warnings


def _location(entry, is_best):
    """Ochiq nusxa. `pdf_url` — faqat nomzod, yuklab olish buyrug'i emas."""
    return {
        'landing_page_url': clean_text(entry.get('url_for_landing_page')
                                       or entry.get('url'), 1000),
        'pdf_url': clean_text(entry.get('url_for_pdf'), 1000),
        'host_type': clean_text(entry.get('host_type'), 40),
        'version': clean_text(entry.get('version'), 40),
        'license': clean_text(entry.get('license'), 60),
        'is_best': is_best,
    }


def _iso(value):
    return value.isoformat() if value else None


def _bool_or_none(value):
    return bool(value) if value is not None else None
