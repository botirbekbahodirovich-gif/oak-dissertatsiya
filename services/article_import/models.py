# -*- coding: utf-8 -*-
"""Import batch/element uchun DB'dan mustaqil tiplar (Phase 2C.2).

`services/article_metadata/models.py` bilan bir xil naqsh: DB qatorlari shu
dataclass'larga aylantiriladi, shablon va JSON javob shulardan o'qiydi.
Holat qiymatlari — `services.article_import.state` (yagona haqiqat manbai).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from services.article_import.state import (BATCH_STATUS_LABELS,
                                            ITEM_STATUS_LABELS)

__all__ = ['ITEM_STATUS_LABELS', 'BATCH_STATUS_LABELS', 'ImportBatch', 'ImportItem']

BATCH_COUNT_FIELDS = (
    'total_items', 'pending_count', 'processing_count', 'review_count',
    'approved_count', 'rejected_count', 'failed_count', 'duplicate_count',
    'skipped_count',
)


@dataclass
class ImportBatch:
    id: int
    created_by: int
    source_type: str
    status: str
    original_filename: str | None = None
    total_items: int = 0
    pending_count: int = 0
    processing_count: int = 0
    review_count: int = 0
    approved_count: int = 0
    rejected_count: int = 0
    failed_count: int = 0
    duplicate_count: int = 0
    skipped_count: int = 0
    cancel_requested: bool = False
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict) -> 'ImportBatch':
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in row.items() if k in known})

    def to_dict(self) -> dict:
        return {
            'id': self.id, 'created_by': self.created_by,
            'source_type': self.source_type, 'status': self.status,
            'status_label': BATCH_STATUS_LABELS.get(self.status, self.status),
            'original_filename': self.original_filename,
            'total_items': self.total_items,
            'pending_count': self.pending_count,
            'processing_count': self.processing_count,
            'review_count': self.review_count,
            'approved_count': self.approved_count,
            'rejected_count': self.rejected_count,
            'failed_count': self.failed_count,
            'duplicate_count': self.duplicate_count,
            'skipped_count': self.skipped_count,
            'cancel_requested': self.cancel_requested,
            'created_at': _iso(self.created_at),
            'started_at': _iso(self.started_at),
            'completed_at': _iso(self.completed_at),
            'updated_at': _iso(self.updated_at),
        }


@dataclass
class ImportItem:
    id: int
    batch_id: int
    row_number: int
    raw_input: str
    status: str
    normalized_doi: str | None = None
    duplicate_of_item_id: int | None = None
    existing_article_id: int | None = None
    created_article_id: int | None = None
    metadata_candidate: dict | None = None
    provider_status_summary: dict | None = None
    conflicts: list | None = None
    warnings: list | None = None
    is_retracted: bool = False
    oa_status: str | None = None
    candidate_pdf_url: str | None = None
    attempt_count: int = 0
    last_error_code: str | None = None
    last_error_message: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    processed_at: datetime | None = None
    reviewed_at: datetime | None = None
    reviewed_by: int | None = None

    @classmethod
    def from_row(cls, row: dict) -> 'ImportItem':
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in row.items() if k in known})

    def to_dict(self) -> dict:
        return {
            'id': self.id, 'batch_id': self.batch_id, 'row_number': self.row_number,
            'raw_input': self.raw_input, 'status': self.status,
            'status_label': ITEM_STATUS_LABELS.get(self.status, self.status),
            'normalized_doi': self.normalized_doi,
            'duplicate_of_item_id': self.duplicate_of_item_id,
            'existing_article_id': self.existing_article_id,
            'created_article_id': self.created_article_id,
            'metadata_candidate': self.metadata_candidate,
            'provider_status_summary': self.provider_status_summary,
            'conflicts': self.conflicts or [],
            'warnings': self.warnings or [],
            'is_retracted': self.is_retracted,
            'oa_status': self.oa_status,
            'candidate_pdf_url': self.candidate_pdf_url,
            'attempt_count': self.attempt_count,
            'last_error_code': self.last_error_code,
            'last_error_message': self.last_error_message,
            'created_at': _iso(self.created_at),
            'processed_at': _iso(self.processed_at),
            'reviewed_at': _iso(self.reviewed_at),
            'reviewed_by': self.reviewed_by,
        }


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None
