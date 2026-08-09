# -*- coding: utf-8 -*-
"""Xom qatorlarni DOI'ga normalizatsiya qiladi va batch limitini tekshiradi.

DOI formatini tekshirish `utils.article_rules` orqali — bulk importer
ALOHIDA regex/format qoidasi YARATMAYDI (§15). Bu modul faqat shu funksiyani
har bir qatorga qo'llaydi va natijani klassifikatsiya qiladi.
"""
from __future__ import annotations

from dataclasses import dataclass

from services.article_import.parser import ImportParseError
from utils.article_rules import ArticleValidationError, validate_doi


class ImportLimitError(ImportParseError):
    """Batch max_items limitidan oshganda (§14)."""


@dataclass
class NormalizedRow:
    row_number: int
    raw_input: str
    normalized_doi: str | None    # None — format noto'g'ri
    is_valid: bool
    error_message: str | None = None


def enforce_max_items(row_count, max_items):
    if row_count > max_items:
        raise ImportLimitError(
            "Import %d ta yozuvdan iborat, lekin ruxsat etilgan maksimum — "
            "%d ta. Faylni bo'lib yuboring yoki ARTICLE_IMPORT_MAX_ITEMS "
            "sozlamasini oshiring." % (row_count, max_items),
            code='max_items_exceeded')
    if row_count == 0:
        raise ImportParseError("Import uchun hech qanday qator topilmadi.",
                               code='empty_input')


def normalize_rows(rows):
    """`[(row_number, raw_text), ...]` -> `[NormalizedRow, ...]` (§15, §64)."""
    out = []
    for row_number, raw in rows:
        try:
            doi = validate_doi(raw)
        except ArticleValidationError as exc:
            out.append(NormalizedRow(row_number=row_number, raw_input=raw,
                                     normalized_doi=None, is_valid=False,
                                     error_message=exc.public_message))
            continue
        if not doi:
            out.append(NormalizedRow(row_number=row_number, raw_input=raw,
                                     normalized_doi=None, is_valid=False,
                                     error_message='DOI bo\'sh yoki aniqlanmadi.'))
            continue
        out.append(NormalizedRow(row_number=row_number, raw_input=raw,
                                 normalized_doi=doi, is_valid=True))
    return out
