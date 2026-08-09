# -*- coding: utf-8 -*-
"""Discovery -> mavjud import navbati ko'prigi (Phase 2D.1, §16, §27-§29).

BU MODUL MAQOLA YARATMAYDI. Faqat `article_import_batches` /
`article_import_items`ga (`source_type='discovery'`) yozadi — hammasi
bundan keyin mavjud `services.article_import.service.process_import_batch()`
va `fetch_article_metadata()` orqali ishlanadi. Faqat `NEW` deb
klassifikatsiya qilingan nomzodlar navbatga yuboriladi (§27) — bazadagi,
navbatdagi va takroriy nomzodlar YO'Q ORTIQCHA faol element yaratmaydi (§13,
§28).
"""
from __future__ import annotations

from services.article_discovery.models import NEW, DiscoveryCandidate
from services.article_import import persistence


def enqueue_discovery_candidates(cur, admin_id, query, candidates, *,
                                 total_count, excluded_no_doi_count):
    """Faqat `NEW` nomzodlarni navbatga qo'yadi.

    Qaytaradi: `{'batch_id':…, 'queued': [...], 'conflicted': [...]}`.
    `conflicted` — DB darajasidagi `ON CONFLICT DO NOTHING` (§16) tutgan
    DOI'lar: boshqa parallel so'rov ular bilan ULGURGAN, 500 EMAS.
    """
    persistence.ensure_schema(cur)

    new_candidates = [c for c in candidates if c.classification == NEW]

    batch_id = persistence.create_discovery_batch(
        cur, admin_id, source_provider='openalex', source_query=query.canonical(),
        source_oak_field_id=query.oak_field_id, source_result_count=total_count,
        excluded_no_doi_count=excluded_no_doi_count)

    queued, conflicted = [], []
    for row_number, candidate in enumerate(new_candidates, start=1):
        item_id = persistence.insert_discovery_item(
            cur, batch_id, row_number, candidate.doi, candidate.external_source_id)
        if item_id is None:
            # Boshqa (parallel) so'rov shu DOI bilan ULGURDI — final DB himoya
            # (uq_article_import_items_active_doi, migrations/014) ishladi.
            conflicted.append(candidate.doi)
        else:
            queued.append({'item_id': item_id, 'doi': candidate.doi})

    persistence.recompute_batch_counts(cur, batch_id)
    return {'batch_id': batch_id, 'queued': queued, 'conflicted': conflicted}


def candidates_from_cache(entries):
    return [DiscoveryCandidate.from_dict(e) for e in (entries or [])]
