# -*- coding: utf-8 -*-
"""Diff modeli — DB'dan mustaqil (Phase 2C.3A).

`services/article_metadata/models.py` bilan bir xil naqsh: xom taqqoslash
natijasi shu dataclass'ga aylanadi, DB va JSON javob shundan o'qiydi.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# ── Severity ─────────────────────────────────────────────────────────────────
UNCHANGED = 'unchanged'
SAFE = 'safe'
REVIEW = 'review'
CRITICAL = 'critical'

SEVERITY_LABELS = {
    UNCHANGED: "O'zgarmagan",
    SAFE: 'Xavfsiz',
    REVIEW: "Ko'rib chiqish kerak",
    CRITICAL: 'MUHIM',
}

# ── Tavsiya etilgan amal ─────────────────────────────────────────────────────
IGNORE = 'ignore'
AUTO_UPDATE_CANDIDATE = 'auto_update_candidate'
REVIEW_REQUIRED = 'review_required'
BLOCK_UNTIL_REVIEW = 'block_until_review'


@dataclass
class MetadataFieldDiff:
    """Bitta maydon bo'yicha eski/yangi qiymat solishtiruvi.

    `applicable` — bu maydon "Tanlangan o'zgarishlarni qo'llash" ro'yxatida
    checkbox sifatida ko'rinadimi (litsenziya/OA/candidate PDF/DOI kabi
    INFORMATIONAL maydonlar — YO'Q, §8-10).
    """
    field: str
    label: str
    old_value: Any
    new_value: Any
    source: str | None
    changed: bool
    severity: str
    recommended_action: str
    applicable: bool = True
    default_selected: bool = False
    note: str | None = None

    def to_dict(self) -> dict:
        return {
            'field': self.field, 'label': self.label,
            'old_value': _jsonable(self.old_value),
            'new_value': _jsonable(self.new_value),
            'source': self.source, 'changed': self.changed,
            'severity': self.severity,
            'severity_label': SEVERITY_LABELS.get(self.severity, self.severity),
            'recommended_action': self.recommended_action,
            'applicable': self.applicable,
            'default_selected': self.default_selected,
            'note': self.note,
        }


@dataclass
class MetadataSyncResult:
    """`check_article_metadata()` qaytaradigan yagona natija."""
    article_id: int
    sync_id: int | None
    diffs: list[MetadataFieldDiff] = field(default_factory=list)
    provider_summary: dict = field(default_factory=dict)
    warnings: list[dict] = field(default_factory=list)
    doi_mismatch: bool = False
    article_updated_at_snapshot: datetime | None = None

    @property
    def change_count(self) -> int:
        return len(self.diffs)

    @property
    def safe_count(self) -> int:
        return sum(1 for d in self.diffs if d.severity == SAFE)

    @property
    def review_count(self) -> int:
        return sum(1 for d in self.diffs if d.severity == REVIEW)

    @property
    def critical_count(self) -> int:
        return sum(1 for d in self.diffs if d.severity == CRITICAL)

    def to_public_dict(self) -> dict:
        return {
            'article_id': self.article_id, 'sync_id': self.sync_id,
            'diffs': [d.to_dict() for d in self.diffs],
            'provider_summary': self.provider_summary,
            'warnings': self.warnings,
            'doi_mismatch': self.doi_mismatch,
            'change_count': self.change_count,
            'safe_count': self.safe_count,
            'review_count': self.review_count,
            'critical_count': self.critical_count,
        }


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value
