# -*- coding: utf-8 -*-
"""Bitta elementni ishlash — metadata olish va holatga xaritalash (§18–§19).

Metadata FAQAT `services.article_metadata.fetch_article_metadata` orqali
olinadi (§6) — Crossref/OpenAlex/Unpaywall mantiqi bu yerda YO'Q. Bu modul
faqat natijani import elementi holatiga aylantiradi.
"""
from __future__ import annotations

import logging

from services.article_import import persistence
from services.article_import.state import (ALREADY_EXISTS, FAILED,
                                            METADATA_NOT_FOUND,
                                            PARTIAL_METADATA, PROVIDER_FAILED,
                                            READY_FOR_REVIEW, RETRACTED)
from services.article_metadata import MetadataLookupError, fetch_article_metadata
from services.article_metadata.models import (LOOKUP_FAILED, LOOKUP_NOT_FOUND,
                                              LOOKUP_OK, LOOKUP_PARTIAL)

logger = logging.getLogger(__name__)


def classify_lookup(lookup_status, metadata):
    """`MetadataLookupResult.status` + retraction bayrog'ini item holatiga
    aylantiradi (§18, §36)."""
    if lookup_status == LOOKUP_NOT_FOUND:
        return METADATA_NOT_FOUND
    if lookup_status == LOOKUP_FAILED:
        return PROVIDER_FAILED
    if metadata.is_retracted:
        return RETRACTED
    if lookup_status == LOOKUP_PARTIAL:
        return PARTIAL_METADATA
    return READY_FOR_REVIEW


def _doi_mismatch_conflict(item_doi, metadata_doi):
    """DOI kiritilgan va provayder qaytargan DOI farq qilsa — CRITICAL (§39)."""
    if not metadata_doi or not item_doi:
        return None
    if metadata_doi.strip().lower() == item_doi.strip().lower():
        return None
    return {
        'field': 'doi', 'label': 'DOI', 'severity': 'critical',
        'preferred': metadata_doi, 'preferred_source': 'provider',
        'alternatives': [{'source': 'input', 'value': item_doi}],
    }


def process_item(cur, item, *, config=None, fetch=fetch_article_metadata,
                 cache_get=None, cache_set=None):
    """Bitta `pending` elementni `processing` -> yakuniy holatga o'tkazadi.

    Chaqiruvchi (`service.process_import_batch`) itemni allaqachon
    `mark_item_processing` bilan qulflagan (CAS) — bu funksiya faqat
    natijani yozadi. Bitta DOI'ning muvaffaqiyatsizligi butun chunk'ni
    yiqitmaydi (§88) — istisno shu yerda ushlanadi va `failed` deb yoziladi.
    """
    doi = item['normalized_doi']

    # Poyga himoyasi (§17, generic — faqat discovery uchun emas): preview va
    # processing orasida BOSHQA yo'l (masalan boshqa admin approve'i yoki
    # boshqa import) shu DOI bilan ARTICLE yaratgan bo'lishi mumkin. Metadata
    # providerga bekorga so'rov yubormaslik uchun `scientific_articles`ni
    # QAYTA tekshiramiz — item allaqachon batch yaratilganda `already_exists`
    # bo'lmagan bo'lishi mumkin edi (o'sha payt hali mavjud emas edi).
    existing = persistence.find_article_by_doi(cur, doi)
    if existing:
        persistence.set_item_already_exists(cur, item['id'], existing['id'])
        return ALREADY_EXISTS

    try:
        result = fetch(doi, cache_get=cache_get, cache_set=cache_set)
    except MetadataLookupError as exc:
        persistence.record_item_result(
            cur, item['id'], FAILED, last_error_code=exc.code,
            last_error_message=exc.public_message)
        return FAILED
    except Exception:                      # noqa: BLE001 — chegara qatlami (§88)
        logger.exception('article-import item=%s metadata lookup kutilmagan xato',
                         item['id'])
        persistence.record_item_result(
            cur, item['id'], FAILED, last_error_code='internal',
            last_error_message='Kutilmagan ichki xato.')
        return FAILED

    status = classify_lookup(result.status, result.metadata)
    conflicts = [c.to_dict() for c in result.conflicts]
    mismatch = _doi_mismatch_conflict(doi, result.metadata.doi)
    if mismatch:
        conflicts.append(mismatch)

    oa_status = result.metadata.oa_status
    candidate_pdf_url = None
    for location in result.metadata.external_locations:
        if location.pdf_url:
            candidate_pdf_url = location.pdf_url
            if location.is_best:
                break

    persistence.record_item_result(
        cur, item['id'], status,
        metadata_candidate=result.metadata.to_dict(),
        provider_status_summary={name: r.public_dict()
                                 for name, r in result.providers.items()},
        conflicts=conflicts,
        warnings=[w.to_dict() for w in result.warnings],
        is_retracted=bool(result.metadata.is_retracted),
        oa_status=oa_status, candidate_pdf_url=candidate_pdf_url)
    return status


def process_chunk(cur, batch_id, config, *, fetch=fetch_article_metadata,
                  cache_get=None, cache_set=None):
    """Bitta chunk (§21) — chaqiruvchi (`service.py`) buni bitta HTTP so'rov
    ichida chaqiradi va commitlaydi. Har element o'z holatini yozadi, shuning
    uchun jarayon o'rtada uzilsa ham (crash) allaqachon yozilgan elementlar
    qayta ishlanmaydi — keyingi chaqiruv faqat qolgan `pending`larni oladi
    (§20 — resume, chunk chegarasida checkpoint)."""
    item_ids = persistence.fetch_pending_chunk(cur, batch_id, config.chunk_size)
    processed = []
    for item_id in item_ids:
        if not persistence.mark_item_processing(cur, item_id):
            continue                        # boshqa worker allaqachon oldi
        item = persistence.get_item(cur, item_id)
        status = process_item(cur, item, config=config, fetch=fetch,
                              cache_get=cache_get, cache_set=cache_set)
        processed.append((item_id, status))
    return processed
