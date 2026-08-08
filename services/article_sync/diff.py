# -*- coding: utf-8 -*-
"""Diff engine — DB'dan mustaqil, sof Python (Phase 2C.3A).

`compare_article_metadata(article, fresh)` — `article` DB qatori (dict,
`blueprints/articles.py:_load_article` shakli — `authors` biriktirilgan) va
`fresh` — `NormalizedArticleMetadata` (services.article_metadata) oladi,
FAQAT o'zgargan maydonlar ro'yxatini qaytaradi. DB'ga hech narsa yozmaydi,
Flask'ga bog'liq emas.
"""
from __future__ import annotations

from services.article_sync.models import (AUTO_UPDATE_CANDIDATE,
                                           BLOCK_UNTIL_REVIEW, CRITICAL,
                                           IGNORE, REVIEW, REVIEW_REQUIRED,
                                           SAFE, UNCHANGED, MetadataFieldDiff)
from services.article_sync.policy import (EXTERNAL_ID_FIELDS, FIELD_LABELS,
                                          REVIEW_SCALAR_FIELDS, normalize_text,
                                          values_equal)
from utils.article_rules import normalize_doi


def compare_article_metadata(article, fresh):
    """(list[MetadataFieldDiff], doi_mismatch: bool). Faqat O'ZGARGAN
    maydonlarni qaytaradi (§2 — "Changed fields")."""
    diffs = []

    for field_name in REVIEW_SCALAR_FIELDS:
        diff = _scalar_diff(field_name, article.get(field_name),
                            getattr(fresh, field_name, None),
                            fresh.source_summary.get(field_name))
        if diff:
            diffs.append(diff)

    for field_name in EXTERNAL_ID_FIELDS:
        diff = _external_id_diff(field_name, article.get(field_name),
                                 getattr(fresh, field_name, None))
        if diff:
            diffs.append(diff)

    citation_diff = _citation_diff(article, fresh)
    if citation_diff:
        diffs.append(citation_diff)

    retraction_diff = _retraction_diff(article, fresh)
    if retraction_diff:
        diffs.append(retraction_diff)

    authors_diff = _authors_diff(article.get('authors') or [], fresh.authors)
    if authors_diff:
        diffs.append(authors_diff)

    diffs.extend(_informational_diffs(fresh))

    doi_mismatch = _doi_mismatch(article.get('doi'), fresh.doi)
    if doi_mismatch:
        diffs.insert(0, MetadataFieldDiff(
            field='doi', label=FIELD_LABELS['doi'],
            old_value=article.get('doi'), new_value=fresh.doi, source=None,
            changed=True, severity=CRITICAL, recommended_action=BLOCK_UNTIL_REVIEW,
            applicable=False,
            note="Provayder javobi so'ralgan DOI'dan farq qiladi — bu "
                 "yozuv qo'llanilmaydi."))

    return diffs, doi_mismatch


def _doi_mismatch(article_doi, fresh_doi):
    a = normalize_doi(article_doi) if article_doi else None
    f = normalize_doi(fresh_doi) if fresh_doi else None
    return bool(a and f and a != f)


def _scalar_diff(field_name, old, new, source):
    if new in (None, ''):
        return None
    if values_equal(field_name, old, new):
        return None
    return MetadataFieldDiff(
        field=field_name, label=FIELD_LABELS[field_name], old_value=old,
        new_value=new, source=source, changed=True, severity=REVIEW,
        recommended_action=REVIEW_REQUIRED, applicable=True,
        default_selected=False)


def _external_id_diff(field_name, old, new):
    if not new:
        return None
    if old and normalize_text(old) == normalize_text(new):
        return None
    if not old:
        # Bo'sh joyni to'ldirish — SAFE candidate (§5).
        return MetadataFieldDiff(
            field=field_name, label=FIELD_LABELS[field_name], old_value=old,
            new_value=new, source=field_name.split('_')[0], changed=True,
            severity=SAFE, recommended_action=AUTO_UPDATE_CANDIDATE,
            applicable=True, default_selected=True)
    # Mavjud qiymat bilan TO'QNASHUV — review, avtomatik overwrite yo'q.
    return MetadataFieldDiff(
        field=field_name, label=FIELD_LABELS[field_name], old_value=old,
        new_value=new, source=None, changed=True, severity=REVIEW,
        recommended_action=REVIEW_REQUIRED, applicable=True,
        default_selected=False,
        note='Bazadagi qiymat tashqi manbadan farq qiladi.')


def _citation_diff(article, fresh):
    """Iqtibos soni — safe agar oshsa, review agar kamaysa (§7)."""
    new_count = fresh.citation_count
    if new_count is None:
        return None
    old_count = article.get('citation_count')
    if old_count == new_count:
        return None
    if old_count is None or new_count > old_count:
        severity, action, default_selected = SAFE, AUTO_UPDATE_CANDIDATE, True
        note = None
    else:
        severity, action, default_selected = REVIEW, REVIEW_REQUIRED, False
        note = ("Iqtibos soni KAMAYDI — bu provayder tuzatishi yoki "
                "indekslash o'zgarishi bo'lishi mumkin, ko'r-ko'rona "
                "qo'llanmaydi.")
    return MetadataFieldDiff(
        field='citation_count', label=FIELD_LABELS['citation_count'],
        old_value=old_count, new_value=new_count,
        source=fresh.citation_count_source, changed=True, severity=severity,
        recommended_action=action, applicable=True,
        default_selected=default_selected, note=note)


def _retraction_diff(article, fresh):
    """§6 — retraction har doim MUHIM signal, hech qachon jim update emas."""
    new_flag = bool(fresh.is_retracted) if fresh.is_retracted is not None else None
    if new_flag is None:
        return None
    old_flag = bool(article.get('is_retracted'))
    if old_flag == new_flag:
        return None
    if not old_flag and new_flag:
        # false -> true — spetsifikatsiya §5 buni aniq CRITICAL deb belgilaydi.
        return MetadataFieldDiff(
            field='is_retracted', label=FIELD_LABELS['is_retracted'],
            old_value=old_flag, new_value=new_flag, source='openalex',
            changed=True, severity=CRITICAL, recommended_action=BLOCK_UNTIL_REVIEW,
            applicable=True, default_selected=False,
            note='Manba bu maqolani CHAQIRIB OLINGAN deb belgiladi. Aniq '
                "tasdiqlashsiz qo'llanmaydi.")
    # true -> false — avtomatik "un-retract" QILINMAYDI (§6), lekin bloklanmaydi
    # ham: bu ham review — admin qo'lda ko'rib chiqadi va tanlaydi.
    return MetadataFieldDiff(
        field='is_retracted', label=FIELD_LABELS['is_retracted'],
        old_value=old_flag, new_value=new_flag, source='openalex',
        changed=True, severity=REVIEW, recommended_action=REVIEW_REQUIRED,
        applicable=True, default_selected=False,
        note="Manba endi retraction belgisini ko'rsatmayapti. Avtomatik "
             "qayta faollashtirilmaydi — qo'lda tekshiring.")


def _author_key(author_dict):
    return (author_dict.get('orcid') or None,
           normalize_text(author_dict.get('author_name') or author_dict.get('name')),
           author_dict.get('openalex_author_id') or None,
           normalize_text(author_dict.get('affiliation')))


def _authors_diff(existing_authors, fresh_authors):
    """§11 — fuzzy merge yo'q, faqat tartibli ro'yxat solishtiruvi.

    `new_value` TO'LIQ struktura bilan saqlanadi (name/orcid/affiliation/
    openalex_author_id) — §16 talabi: apply CLIENT yuborgan qiymatga emas,
    SERVER-side saqlangan sync yozuviga ishonadi. Shu tufayli apply, sync
    yaratilgan so'rovdan BUTUNLAY boshqa (keyingi) HTTP so'rovda ham, faqat
    `diff_summary`dan o'qib, to'g'ri mualliflarni yoza oladi.
    """
    if not fresh_authors:
        return None
    existing_keys = [_author_key(a) for a in existing_authors]
    fresh_keys = [(a.orcid or None, normalize_text(a.name),
                  a.openalex_author_id or None, normalize_text(a.affiliation))
                 for a in fresh_authors]
    if existing_keys == fresh_keys:
        return None
    new_value = [{'name': a.name, 'orcid': a.orcid, 'affiliation': a.affiliation,
                 'openalex_author_id': a.openalex_author_id,
                 'is_corresponding': bool(a.is_corresponding)}
                for a in fresh_authors]
    return MetadataFieldDiff(
        field='authors', label=FIELD_LABELS['authors'],
        old_value=[a.get('author_name') for a in existing_authors],
        new_value=new_value, source='crossref+openalex',
        changed=True, severity=REVIEW, recommended_action=REVIEW_REQUIRED,
        applicable=True, default_selected=False,
        note="Muallif ro'yxati farq qiladi — tanlansa TO'LIQ almashtiriladi.")


def _informational_diffs(fresh):
    """§8-10 — OA/litsenziya/candidate PDF: hech qachon applicable emas,
    faqat ko'rsatiladi."""
    out = []
    if fresh.oa_status:
        out.append(MetadataFieldDiff(
            field='oa_status', label=FIELD_LABELS['oa_status'], old_value=None,
            new_value=fresh.oa_status, source='unpaywall', changed=True,
            severity=REVIEW, recommended_action=IGNORE, applicable=False,
            note='Tashqi signal — ichki "Kirish turi"ga avtomatik '
                 "ta'sir qilmaydi."))
    if fresh.license_candidates:
        labels = [c.label or c.license_type or c.source
                 for c in fresh.license_candidates]
        out.append(MetadataFieldDiff(
            field='license_candidates', label=FIELD_LABELS['license_candidates'],
            old_value=None, new_value=labels, source=None, changed=True,
            severity=REVIEW, recommended_action=IGNORE, applicable=False,
            note='Nomzod — yakuniy litsenziya avtomatik almashtirilmaydi.'))
    pdf_url = next((loc.pdf_url for loc in fresh.external_locations if loc.pdf_url),
                   None)
    if pdf_url:
        out.append(MetadataFieldDiff(
            field='candidate_pdf_url', label=FIELD_LABELS['candidate_pdf_url'],
            old_value=None, new_value=pdf_url, source=None, changed=True,
            severity=REVIEW, recommended_action=IGNORE, applicable=False,
            note='Faqat ma\'lumot — hech qachon avtomatik yuklanmaydi.'))
    return out
