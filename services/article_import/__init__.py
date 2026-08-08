# -*- coding: utf-8 -*-
"""Ommaviy DOI/CSV import xizmati (Phase 2C.2).

Ommaviy API — `services/article_import/service.py`:

    create_import_batch(cur, created_by, source_type, raw_text=None,
                        csv_bytes=None, original_filename=None, config=None)
    process_import_batch(cur, batch_id, config=None, fetch=fetch_article_metadata)
    approve_import_item(cur, item_id, actor_id, primary_field_id=None,
                        extra_field_ids=None, confirm_retracted=False)
    reject_import_item(cur, item_id, actor_id)
    skip_import_item(cur, item_id, actor_id)
    retry_import_item(cur, item_id)

Bu paket Flask'ga BOG'LIQ EMAS: barcha funksiyalar psycopg2 kursor va oddiy
Python qiymatlarni qabul qiladi (services/article_metadata bilan bir xil
konvensiya, §77). Shu sababli admin UI, keyingi CSV importer va Phase 2D
agenti AYNAN shu funksiyalarni chaqiradi — parallel pipeline yaratilmaydi.

Metadata qidiruvi FAQAT `services.article_metadata.fetch_article_metadata`
orqali bajariladi; Crossref/OpenAlex/Unpaywall mantig'i bu yerda QAYTA
YOZILMAYDI (§6).
"""
from services.article_import.config import (ArticleImportConfig,
                                             config_from_env)
from services.article_import.models import (ITEM_STATUS_LABELS,
                                             BATCH_STATUS_LABELS,
                                             ImportBatch, ImportItem)
from services.article_import.parser import (ImportParseError, ParsedInput,
                                            parse_csv, parse_doi_list)
from services.article_import.service import (DuplicateArticleError,
                                              ImportServiceError,
                                              approve_import_item,
                                              bulk_approve_safe_items,
                                              cancel_import_batch,
                                              create_import_batch,
                                              get_batch, get_item,
                                              list_batches, list_items,
                                              preview_import_input,
                                              process_import_batch,
                                              reject_import_item,
                                              resolve_duplicate_after_conflict,
                                              retry_import_item,
                                              skip_import_item)

__all__ = [
    'ArticleImportConfig', 'config_from_env',
    'ITEM_STATUS_LABELS', 'BATCH_STATUS_LABELS', 'ImportBatch', 'ImportItem',
    'ImportParseError', 'ParsedInput', 'parse_csv', 'parse_doi_list',
    'ImportServiceError', 'DuplicateArticleError', 'approve_import_item',
    'bulk_approve_safe_items', 'cancel_import_batch', 'create_import_batch',
    'get_batch', 'get_item', 'list_batches', 'list_items',
    'preview_import_input', 'process_import_batch', 'reject_import_item',
    'resolve_duplicate_after_conflict', 'retry_import_item', 'skip_import_item',
]
