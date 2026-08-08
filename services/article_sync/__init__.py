# -*- coding: utf-8 -*-
"""Mavjud maqola metadata sinxronizatsiyasi — diff engine (Phase 2C.3A).

Ommaviy API:

    check_article_metadata(cur, article_id, actor_id, config=None, fetch=...)
    apply_article_metadata_changes(cur, article_id, sync_id, actor_id,
                                   selected_fields, confirm_retraction=False,
                                   confirm_doi_mismatch=False)
    list_sync_history(cur, article_id, limit=10)

Flask'ga BOG'LIQ EMAS (§31 — kelajakdagi scheduler AYNAN shu
`check_article_metadata`ni chaqira oladi, parallel logika yaratilmaydi).
Metadata FAQAT `services.article_metadata.fetch_article_metadata` orqali
olinadi — Crossref/OpenAlex/Unpaywall mantig'i bu yerda QAYTA YOZILMAYDI.
"""
from services.article_sync.diff import compare_article_metadata
from services.article_sync.models import (CRITICAL, REVIEW, SAFE, UNCHANGED,
                                          MetadataFieldDiff, MetadataSyncResult)
from services.article_sync.policy import APPLICABLE_FIELDS, FIELD_LABELS
from services.article_sync.service import (ArticleSyncError,
                                           apply_article_metadata_changes,
                                           check_article_metadata,
                                           get_sync_result, list_sync_history)

__all__ = [
    'compare_article_metadata', 'CRITICAL', 'REVIEW', 'SAFE', 'UNCHANGED',
    'MetadataFieldDiff', 'MetadataSyncResult', 'APPLICABLE_FIELDS',
    'FIELD_LABELS', 'ArticleSyncError', 'apply_article_metadata_changes',
    'check_article_metadata', 'get_sync_result', 'list_sync_history',
]
