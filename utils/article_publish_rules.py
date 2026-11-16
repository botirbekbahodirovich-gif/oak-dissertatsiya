# -*- coding: utf-8 -*-
"""Nashrga tayyorlikni avtomatik tekshirish (DB'siz, sof Python).

`publish_blockers(article)` — maqolani `published` qilishga nima
to'sqinlik qilayotganini ro'yxat sifatida qaytaradi. Bo'sh ro'yxat —
"Nashrga tayyor". Admin ommaviy nashr tugmasi ham, bitta maqola
moderatsiya sahifasi ham AYNAN shu funksiyani chaqiradi.

`article` — dict (DB qatori + biriktirilgan maydonlar):

* `authors` — ro'yxat (`_attach_authors_and_fields`);
* `primary_field` — dict yoki None;
* `has_critical_conflict` — import elementidagi `severity='critical'`
  konflikt (chaqiruvchi hisoblaydi);
* `doi_duplicate` — boshqa maqolada shu DOI bor (chaqiruvchi hisoblaydi;
  DB'dagi partial UNIQUE indeks baribir yakuniy himoya).

Bu funksiya HECH NARSANI nashr qilmaydi — faqat sabab qaytaradi.
"""
from __future__ import annotations

from utils.article_rules import (ArticleValidationError, validate_article_rights,
                                 validate_doi)

BLOCKER_DOI = 'DOI yo‘q yoki noto‘g‘ri'
BLOCKER_DOI_DUPLICATE = 'Bu DOI boshqa maqolada ham bor'
BLOCKER_RETRACTED = 'Maqola chaqirib olingan (retracted)'
BLOCKER_CRITICAL = 'Kritik konflikt bor (masalan, DOI farqi)'
BLOCKER_TITLE = 'Sarlavha yo‘q'
BLOCKER_AUTHORS = 'Kamida bitta muallif kerak'
BLOCKER_JOURNAL = 'Jurnal nomi yo‘q'
BLOCKER_YEAR = 'Nashr yili yo‘q'
BLOCKER_FIELD = 'Asosiy fan yo‘nalishi tanlanmagan'
BLOCKER_COPYRIGHT_HOLD = 'Mualliflik huquqi bo‘yicha to‘xtatilgan'
BLOCKER_ARCHIVED = 'Maqola arxivlangan'


def _get(article, key, default=None):
    if hasattr(article, 'get'):
        return article.get(key, default)
    return getattr(article, key, default)


def review_blockers(article):
    """Bitta maqolani qo'lda `published` qilishdan oldingi MINIMAL shart
    (`/admin/articles/<id>/review`): asosiy yo'nalish va kamida bitta
    muallif. To'liq avtomatik ro'yxat — `publish_blockers`."""
    blockers = []
    if not (_get(article, 'authors') or []):
        blockers.append(BLOCKER_AUTHORS)
    if not _get(article, 'primary_field'):
        blockers.append(BLOCKER_FIELD)
    return blockers


def publish_blockers(article):
    blockers = []

    try:
        doi = validate_doi(_get(article, 'doi'))
    except ArticleValidationError:
        doi = None
    if not doi:
        blockers.append(BLOCKER_DOI)
    elif _get(article, 'doi_duplicate'):
        blockers.append(BLOCKER_DOI_DUPLICATE)

    if _get(article, 'is_retracted'):
        blockers.append(BLOCKER_RETRACTED)
    if _get(article, 'has_critical_conflict'):
        blockers.append(BLOCKER_CRITICAL)
    if _get(article, 'verification_status') == 'copyright_hold':
        blockers.append(BLOCKER_COPYRIGHT_HOLD)
    if _get(article, 'publication_status') == 'archived':
        blockers.append(BLOCKER_ARCHIVED)

    if not (_get(article, 'title') or '').strip():
        blockers.append(BLOCKER_TITLE)
    if not (_get(article, 'authors') or []):
        blockers.append(BLOCKER_AUTHORS)
    if not (_get(article, 'journal_name') or '').strip():
        blockers.append(BLOCKER_JOURNAL)
    if not _get(article, 'publication_year'):
        blockers.append(BLOCKER_YEAR)
    if not _get(article, 'primary_field'):
        blockers.append(BLOCKER_FIELD)

    pdf_present = bool(_get(article, 'pdf_path'))
    try:
        validate_article_rights(
            _get(article, 'access_type') or 'metadata_only',
            _get(article, 'license_type') or 'unknown',
            pdf_present,
            bool(_get(article, 'rights_confirmed_at')))
    except ArticleValidationError as exc:
        blockers.append('Huquq: %s' % exc.public_message)

    return blockers


def is_ready_to_publish(article):
    return not publish_blockers(article)
