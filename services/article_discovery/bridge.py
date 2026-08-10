# -*- coding: utf-8 -*-
"""Discovery -> mavjud import navbati ko'prigi (Phase 2D.1/2D.1.1, §16, §27-§29).

BU MODUL MAQOLA YARATMAYDI. Faqat `article_import_batches` /
`article_import_items`ga (`source_type='discovery'`) yozadi — hammasi
bundan keyin mavjud `services.article_import.service.process_import_batch()`
va `fetch_article_metadata()` orqali ishlanadi. Faqat `NEW` deb
klassifikatsiya qilingan nomzodlar navbatga yuboriladi (§27) — bazadagi,
navbatdagi va takroriy nomzodlar YO'Q ORTIQCHA faol element yaratmaydi (§13,
§28).

ENQUEUE IDEMPOTENCY (Phase 2D.1.1). Item-level `uq_article_import_items_active_doi`
(migrations/015) bitta DOI'ni ikki marta navbatga qo'yishning oldini oladi,
lekin BATCH darajasida hech narsa yo'q edi — ikkinchi (double-click yoki
parallel) enqueue so'rovi o'zining (odatda bo'sh) batch'ini yaratardi. Endi
`request_id` (chaqiruvchidan — preview token hash'i) batch darajasida ham
`ON CONFLICT ... DO NOTHING` (migrations/016) orqali bir xil so'rov uchun
FAQAT BITTA batch kafolatlaydi.
"""
from __future__ import annotations

from services.article_discovery.models import NEW, DiscoveryCandidate
from services.article_import import persistence


def enqueue_discovery_candidates(cur, admin_id, query, candidates, *,
                                 total_count, excluded_no_doi_count,
                                 request_id=None, extra_provenance=None):
    """Faqat `NEW` nomzodlarni navbatga qo'yadi, `request_id` bo'yicha
    idempotent (§5-§9).

    `extra_provenance` (Phase 2D.3, §12) — ixtiyoriy kichik dict (masalan
    `{'trigger_type': 'scheduled', 'saved_query_id': 7, 'saved_query_name':
    'Onkologiya', 'run_id': 42}`), mavjud `source_query` JSONB ichiga
    `'scheduled'` kalit ostida qo'shiladi. YANGI USTUN YO'Q —
    `article_import_batches` sxemasi o'zgarmagan; xom provider javobi yoki
    sir HECH QACHON bu yerga tushmaydi (chaqiruvchi mas'ul, faqat oddiy
    skalyar qiymatlar).

    Qaytaradi: `{'batch_id':…, 'queued': [...], 'conflicted': [...],
    'created': bool}`.

    * `batch_id=None` — hech qanday yangi DOI yo'q EDI va shu `request_id`
      uchun avval yaratilgan batch ham yo'q: hech narsa YARATILMADI (§9 —
      bo'sh "orphan" batch yo'q).
    * `created=False`, `batch_id` bor — bu `request_id` uchun batch
      ALLAQACHON mavjud edi (ketma-ket YOKI parallel takroriy so'rov):
      itemlar QAYTA yozilmadi, mavjud batch qaytarildi.
    * `conflicted` — item darajasidagi `ON CONFLICT DO NOTHING` (§16, boshqa
      DOI allaqachon boshqa faol elementda) tutgan DOI'lar — 500 EMAS.
    """
    persistence.ensure_schema(cur)

    new_candidates = [c for c in candidates if c.classification == NEW]

    if not new_candidates:
        existing_batch_id = (persistence.find_discovery_batch_by_request_id(
            cur, request_id) if request_id else None)
        return {'batch_id': existing_batch_id, 'queued': [], 'conflicted': [],
               'created': False}

    source_query = query.canonical()
    if extra_provenance:
        source_query['scheduled'] = dict(extra_provenance)

    batch_id, created = persistence.get_or_create_discovery_batch(
        cur, admin_id, source_provider=query.source_provider_label,
        source_query=source_query,
        source_oak_field_id=query.oak_field_id, source_result_count=total_count,
        excluded_no_doi_count=excluded_no_doi_count, source_request_id=request_id)

    if not created:
        # Idempotent replay: boshqa (oldingi yoki hozir parallel g'olib
        # chiqqan) so'rov shu batch'ni ALLAQACHON to'liq yozib bo'lgan
        # (§6 — Postgres unique-index qulfi ikkinchi tranzaksiyani
        # BIRINCHISI COMMIT bo'lgunicha bloklaydi, shuning uchun bu yerga
        # yetguncha itemlar allaqachon yozilgan bo'ladi). Qayta yozish YO'Q.
        return {'batch_id': batch_id, 'queued': [], 'conflicted': [],
               'created': False}

    queued, conflicted = [], []
    for row_number, candidate in enumerate(new_candidates, start=1):
        item_id = persistence.insert_discovery_item(
            cur, batch_id, row_number, candidate.doi, candidate.external_source_id)
        if item_id is None:
            # Boshqa (parallel) so'rov shu DOI bilan ULGURDI — final DB himoya
            # (uq_article_import_items_active_doi, migrations/015) ishladi.
            conflicted.append(candidate.doi)
        else:
            queued.append({'item_id': item_id, 'doi': candidate.doi})

    persistence.recompute_batch_counts(cur, batch_id)
    return {'batch_id': batch_id, 'queued': queued, 'conflicted': conflicted,
           'created': True}


def candidates_from_cache(entries):
    return [DiscoveryCandidate.from_dict(e) for e in (entries or [])]
