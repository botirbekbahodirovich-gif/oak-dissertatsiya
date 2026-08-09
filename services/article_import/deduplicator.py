# -*- coding: utf-8 -*-
"""Ikki bosqichli duplicate aniqlash — batch ichida va mavjud bazada (§16–§17).

Ikkalasi ham METADATA API'GA chiqishdan OLDIN ishlaydi: bitta DOI ikki marta
kiritilsa yoki bazada allaqachon bo'lsa, tashqi provayderga bekorga
so'rov ketmaydi.
"""
from __future__ import annotations


def find_in_batch_duplicates(normalized_rows):
    """Faqat VALID (normalized_doi bor) qatorlar orasida.

    Qaytaradi: `{row_number: first_row_number}` — faqat TAKRORLANGAN
    qatorlar uchun (birinchi uchrashuv xaritada yo'q — u "original").
    """
    seen: dict[str, int] = {}
    duplicates: dict[int, int] = {}
    for row in normalized_rows:
        if not row.is_valid or not row.normalized_doi:
            continue
        first = seen.get(row.normalized_doi)
        if first is None:
            seen[row.normalized_doi] = row.row_number
        else:
            duplicates[row.row_number] = first
    return duplicates


def find_existing_db_duplicates(cur, normalized_dois):
    """Bazadagi mos DOI'larni bitta so'rovda topadi (N+1 emas, §62).

    Qaytaradi: `{normalized_doi: {'id':…, 'title':…, 'slug':…}}`.
    """
    unique = sorted({d for d in normalized_dois if d})
    if not unique:
        return {}
    cur.execute(
        "SELECT id, title, slug, doi FROM scientific_articles WHERE doi = ANY(%s)",
        (unique,))
    out = {}
    for row_id, title, slug, doi in cur.fetchall():
        out[doi] = {'id': row_id, 'title': title, 'slug': slug}
    return out


def find_active_import_dois(cur, normalized_dois):
    """Boshqa (yoki shu) batch'larda hali "faol" bo'lgan DOI'larni bitta
    so'rovda topadi (Phase 2D.1 discovery, §14, §17 — N+1 emas).

    "Faol" — `services.article_import.state.ACTIVE_DOI_ITEM_STATUSES` (yagona
    haqiqat manbai). Qaytaradi: `{normalized_doi: {'item_id':…, 'batch_id':…}}`
    — bir nechta mos elementdan ENG YANGISI (eng katta id) olinadi.
    """
    from services.article_import.state import ACTIVE_DOI_ITEM_STATUSES

    unique = sorted({d for d in normalized_dois if d})
    if not unique:
        return {}
    cur.execute(
        """SELECT normalized_doi, id, batch_id FROM article_import_items
           WHERE normalized_doi = ANY(%s) AND status = ANY(%s)
           ORDER BY id""",
        (unique, list(ACTIVE_DOI_ITEM_STATUSES)))
    out = {}
    for doi, item_id, batch_id in cur.fetchall():
        out[doi] = {'item_id': item_id, 'batch_id': batch_id}
    return out
