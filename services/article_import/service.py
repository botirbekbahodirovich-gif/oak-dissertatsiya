# -*- coding: utf-8 -*-
"""Ommaviy import — ommaviy orkestratsiya API (Phase 2C.2).

Bu modul HAM Flask'ga bog'liq emas: barcha funksiyalar ochiq psycopg2
kursurini oladi. Transaction chegarasi (`conn.commit()`/`rollback()`) —
CHAQIRUVCHIDA (`blueprints/article_import.py`), xuddi
`blueprints/articles.py`dagi admin route'lar bilan bir xil naqsh. Shu
tufayli Phase 2D agenti ham, keyingi CSV importer ham bu funksiyalarni
Flask'siz sinab ko'rishi/chaqirishi mumkin (§86).
"""
from __future__ import annotations

import json

import psycopg2

from services.article_import import deduplicator, parser, persistence, processor
from services.article_import.config import ArticleImportConfig, config_from_env
from services.article_import.state import (ALREADY_EXISTS, APPROVED,
                                            BULK_APPROVE_SAFE_STATUSES,
                                            DUPLICATE_IN_BATCH, FAILED,
                                            INVALID_DOI, METADATA_NOT_FOUND,
                                            PROVIDER_FAILED, REJECTED,
                                            REVIEW_BUCKET_STATUSES, RETRACTED,
                                            SKIPPED)
from services.article_import.validator import (enforce_max_items,
                                                normalize_rows)
from services.article_metadata import fetch_article_metadata

# Skip — deyarli har qanday tugallangan-bo'lmagan holatdan mumkin (§23-jadval).
SKIPPABLE_STATUSES = frozenset({
    'ready_for_review', 'partial_metadata', RETRACTED, METADATA_NOT_FOUND,
    ALREADY_EXISTS, INVALID_DOI, DUPLICATE_IN_BATCH, PROVIDER_FAILED, FAILED,
})
REJECTABLE_STATUSES = frozenset({'ready_for_review', 'partial_metadata',
                                 RETRACTED, METADATA_NOT_FOUND})


class ImportServiceError(ValueError):
    """Foydalanuvchiga ko'rsatiladigan xato (texnik trace emas)."""

    def __init__(self, code, public_message):
        self.code = code
        self.public_message = public_message
        super().__init__(public_message)


class DuplicateArticleError(ImportServiceError):
    """Approve paytida DOI UNIQUE constraint to'qnashdi (§33 — race condition).

    Bu XATO emas, race condition signali: chaqiruvchi (route) tranzaksiyani
    ROLLBACK qilib, itemni `already_exists`ga yangi tranzaksiyada o'tkazadi.
    """

    def __init__(self, doi):
        self.doi = doi
        super().__init__('duplicate_doi',
                         'Bu DOI bilan maqola approve paytida allaqachon '
                         "yaratilgan (parallel so'rov). Element mos "
                         "ravishda yangilandi.")


def _load_json(value):
    if value in (None, ''):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _decorate_item(row):
    """DB qatoridagi JSON ustunlarini Python obyektiga ochadi (real Postgres
    JSONB'ni allaqachon parse qiladi; qalbaki kursor uchun ham xavfsiz)."""
    if row is None:
        return None
    row = dict(row)
    for key in ('metadata_candidate', 'provider_status_summary', 'conflicts',
                'warnings'):
        row[key] = _load_json(row.get(key))
    return row


# ── Yaratish ─────────────────────────────────────────────────────────────────

def preview_import_input(cur, source_type, *, raw_text=None, csv_bytes=None,
                         config=None):
    """Commit qilmasdan — nechta valid/invalid/duplicate borligini ko'rsatadi
    (§51: CSV preview). O'qishdan tashqari hech narsa yozmaydi."""
    config = config or config_from_env()
    rows = _parse_source(source_type, raw_text, csv_bytes, config)
    normalized = normalize_rows(rows.rows)
    in_batch = deduplicator.find_in_batch_duplicates(normalized)
    existing = deduplicator.find_existing_db_duplicates(
        cur, [r.normalized_doi for r in normalized])
    invalid = sum(1 for r in normalized if not r.is_valid)
    duplicate_in_batch = len(in_batch)
    already_exists = sum(1 for r in normalized
                         if r.is_valid and r.normalized_doi in existing
                         and r.row_number not in in_batch)
    valid_new = len(normalized) - invalid - duplicate_in_batch - already_exists
    return {
        'total_rows': len(normalized),
        'blank_lines_skipped': rows.blank_lines_skipped,
        'valid_new': valid_new,
        'invalid': invalid,
        'duplicate_in_batch': duplicate_in_batch,
        'already_exists': already_exists,
    }


def _parse_source(source_type, raw_text, csv_bytes, config):
    if source_type == 'csv':
        return parser.parse_csv(csv_bytes, max_rows=config.max_items)
    return parser.parse_doi_list(raw_text, max_rows=config.max_items)


def create_import_batch(cur, created_by, source_type, *, raw_text=None,
                        csv_bytes=None, original_filename=None, config=None):
    """Batch + har bir element yaratadi (§7, §8, §16, §17). Metadata SO'ROV
    QILINMAYDI bu yerda — faqat parsing/dedup/persist (§72 bilan tekshiriladi:
    processing DB'ga article yozmaydi)."""
    config = config or config_from_env()
    parsed = _parse_source(source_type, raw_text, csv_bytes, config)
    enforce_max_items(len(parsed.rows), config.max_items)

    normalized = normalize_rows(parsed.rows)
    in_batch_dups = deduplicator.find_in_batch_duplicates(normalized)
    existing_map = deduplicator.find_existing_db_duplicates(
        cur, [r.normalized_doi for r in normalized])

    persistence.ensure_schema(cur)
    batch_id = persistence.create_batch(cur, created_by, source_type,
                                        original_filename)
    persistence.insert_items(cur, batch_id, normalized, in_batch_dups, existing_map)
    persistence.recompute_batch_counts(cur, batch_id)
    return batch_id


# ── Ishlash ──────────────────────────────────────────────────────────────────

def process_import_batch(cur, batch_id, *, config=None, fetch=fetch_article_metadata,
                         cache_get=None, cache_set=None):
    """Bitta chunk ishlaydi (§20, §21). Har HTTP so'rov — bitta chaqiruv;
    review-navbat sahifasi buni qayta-qayta chaqirib batchni oxirigacha
    olib boradi (bir vaqtning o'zida ham progress polling, ham processing
    drayveri, §44)."""
    config = config or config_from_env()
    persistence.ensure_schema(cur)
    batch = persistence.get_batch(cur, batch_id)
    if batch is None:
        raise ImportServiceError('not_found', 'Import partiyasi topilmadi.')
    if batch['cancel_requested']:
        persistence.recompute_batch_counts(cur, batch_id)
        return {'processed': 0, 'batch': persistence.get_batch(cur, batch_id)}

    persistence.mark_batch_started(cur, batch_id)
    processed = processor.process_chunk(cur, batch_id, config, fetch=fetch,
                                        cache_get=cache_get, cache_set=cache_set)
    persistence.recompute_batch_counts(cur, batch_id)
    return {'processed': len(processed), 'items': processed,
           'batch': persistence.get_batch(cur, batch_id)}


def cancel_import_batch(cur, batch_id):
    """Yumshoq bekor qilish (§24) — joriy elementni majburan to'xtatmaydi,
    keyingi chunk boshlanmaydi."""
    batch = persistence.get_batch(cur, batch_id)
    if batch is None:
        raise ImportServiceError('not_found', 'Import partiyasi topilmadi.')
    persistence.set_batch_cancel_requested(cur, batch_id, True)
    persistence.recompute_batch_counts(cur, batch_id)
    return persistence.get_batch(cur, batch_id)


def retry_import_item(cur, item_id, *, config=None):
    config = config or config_from_env()
    item = persistence.get_item(cur, item_id)
    if item is None:
        raise ImportServiceError('not_found', 'Element topilmadi.')
    ok = persistence.reset_item_for_retry(cur, item_id, config.max_attempts)
    if not ok:
        raise ImportServiceError(
            'cannot_retry',
            "Bu element qayta urinib bo'lmaydi (holat mos emas yoki "
            "urinishlar chegarasiga yetdi).")
    persistence.recompute_batch_counts(cur, item['batch_id'])
    return persistence.get_item(cur, item_id)


# ── Ko'rib chiqish ───────────────────────────────────────────────────────────

def approve_import_item(cur, item_id, actor_id, *, primary_field_id=None,
                        extra_field_ids=None, confirm_retracted=False,
                        confirm_critical=False):
    """Candidate'ni `scientific_articles`ga yozadi (§30–§34, §46).

    Idempotent: item allaqachon `approved` bo'lsa, xato EMAS — mavjud
    `created_article_id` qaytariladi (double-click ikki maqola yaratmaydi).
    """
    item = _decorate_item(persistence.get_item(cur, item_id))
    if item is None:
        raise ImportServiceError('not_found', 'Element topilmadi.')

    if item['status'] == APPROVED:
        return {'article_id': item['created_article_id'], 'already_approved': True}
    if item['status'] not in REVIEW_BUCKET_STATUSES:
        raise ImportServiceError(
            'not_reviewable',
            "Bu element hozirgi holatda tasdiqlab bo'lmaydi (%r)." % item['status'])

    if item['is_retracted'] and not confirm_retracted:
        raise ImportServiceError(
            'retraction_confirmation_required',
            "Bu maqola chaqirib olingan (retracted) — davom etish uchun "
            "aniq tasdiqlash kerak.")

    conflicts = item.get('conflicts') or []
    if any((c.get('severity') == 'critical') for c in conflicts) and not confirm_critical:
        raise ImportServiceError(
            'critical_conflict_confirmation_required',
            "Kritik nomuvofiqlik (masalan, DOI farqi) bor — davom etish "
            "uchun aniq tasdiqlash kerak.")

    metadata = item.get('metadata_candidate') or {}
    try:
        article_id = persistence.create_article_from_metadata(
            cur, metadata, actor_id, primary_field_id=primary_field_id,
            extra_field_ids=extra_field_ids)
    except psycopg2.IntegrityError as exc:
        raise DuplicateArticleError(item['normalized_doi']) from exc

    ok = persistence.set_item_reviewed(cur, item_id, APPROVED, actor_id,
                                       REVIEW_BUCKET_STATUSES,
                                       created_article_id=article_id)
    if not ok:
        # O'ta kam ehtimolli poyga: shu tranzaksiya ichida status allaqachon
        # o'zgargan. Yaratilgan maqola baribir haqiqiy — item shunchaki
        # yangilanmadi; qayta yuklash to'g'ri holatni ko'rsatadi.
        pass
    persistence.recompute_batch_counts(cur, item['batch_id'])
    return {'article_id': article_id, 'already_approved': False}


def resolve_duplicate_after_conflict(cur, item_id, doi, batch_id):
    """`DuplicateArticleError`dan KEYIN, YANGI tranzaksiyada chaqiriladi
    (route — conn.rollback() dan keyin). Itemni `already_exists`ga o'tkazadi."""
    persistence.ensure_schema(cur)
    existing = persistence.find_article_by_doi(cur, doi)
    if existing:
        persistence.set_item_already_exists(cur, item_id, existing['id'])
    persistence.recompute_batch_counts(cur, batch_id)
    return existing


def reject_import_item(cur, item_id, actor_id):
    item = persistence.get_item(cur, item_id)
    if item is None:
        raise ImportServiceError('not_found', 'Element topilmadi.')
    if item['status'] == REJECTED:
        return item
    ok = persistence.set_item_reviewed(cur, item_id, REJECTED, actor_id,
                                       REJECTABLE_STATUSES)
    if not ok:
        raise ImportServiceError(
            'cannot_reject', "Bu element hozirgi holatda rad etib bo'lmaydi.")
    persistence.recompute_batch_counts(cur, item['batch_id'])
    return persistence.get_item(cur, item_id)


def skip_import_item(cur, item_id, actor_id):
    item = persistence.get_item(cur, item_id)
    if item is None:
        raise ImportServiceError('not_found', 'Element topilmadi.')
    if item['status'] == SKIPPED:
        return item
    ok = persistence.set_item_reviewed(cur, item_id, SKIPPED, actor_id,
                                       SKIPPABLE_STATUSES)
    if not ok:
        raise ImportServiceError(
            'cannot_skip', "Bu elementni o'tkazib bo'lmaydi.")
    persistence.recompute_batch_counts(cur, item['batch_id'])
    return persistence.get_item(cur, item_id)


def bulk_approve_safe_items(cur, batch_id, actor_id, item_ids, *,
                            primary_field_id=None, extra_field_ids=None):
    """"Tanlanganlarni tasdiqlash" — faqat XAVFSIZ nomzodlar (§37).

    Retracted, kritik konflikt, invalid, already_exists, provider_failed —
    bulk ro'yxatga UMUMAN kirmaydi (chaqiruvchi UI shularni ko'rsatmasligi
    kerak, lekin bu funksiya ham ikkinchi marta tekshiradi — himoya
    ikki qatlamli).
    """
    approved, duplicates, skipped_unsafe = [], [], []
    for item_id in item_ids:
        item = persistence.get_item(cur, item_id)
        if item is None or item['batch_id'] != batch_id:
            continue
        if item['status'] not in BULK_APPROVE_SAFE_STATUSES or item['is_retracted']:
            skipped_unsafe.append(item_id)
            continue
        try:
            result = approve_import_item(cur, item_id, actor_id,
                                         primary_field_id=primary_field_id,
                                         extra_field_ids=extra_field_ids)
            approved.append({'item_id': item_id, 'article_id': result['article_id']})
        except DuplicateArticleError:
            resolve_duplicate_after_conflict(cur, item_id, item['normalized_doi'],
                                             batch_id)
            duplicates.append(item_id)
        except ImportServiceError:
            skipped_unsafe.append(item_id)
    return {'approved': approved, 'duplicates': duplicates,
           'skipped_unsafe': skipped_unsafe}


# ── O'qish ───────────────────────────────────────────────────────────────────

def get_batch(cur, batch_id):
    persistence.ensure_schema(cur)
    return persistence.get_batch(cur, batch_id)


def list_batches(cur, limit=25, offset=0):
    persistence.ensure_schema(cur)
    return persistence.list_batches(cur, limit=limit, offset=offset)


def get_item(cur, item_id):
    persistence.ensure_schema(cur)
    return _decorate_item(persistence.get_item(cur, item_id))


def list_items(cur, batch_id, status=None, limit=50, offset=0):
    persistence.ensure_schema(cur)
    rows, total = persistence.list_items(cur, batch_id, status=status,
                                         limit=limit, offset=offset)
    return [_decorate_item(r) for r in rows], total
