# -*- coding: utf-8 -*-
"""DOI ro'yxati va CSV faylini xom kirish qatorlariga aylantiradi (§12–§13).

Bu modul DOI FORMATINI o'zi tekshirmaydi — faqat matn/faylni alohida "xom
qiymat"larga bo'ladi. DOI normalizatsiyasi va validatsiyasi butunlay
`utils.article_rules.normalize_doi`/`validate_doi` orqali (§15) —
`validator.py` shu yerdan keyingi bosqichda chaqiradi.

Flask'ga bog'liq emas: `parse_csv` oddiy `bytes` oladi, `request.files`ni
o'zi o'qimaydi (chaqiruvchi — blueprint — buni beradi).
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

MAX_CSV_ROWS_HARD_CAP = 20_000   # oxirgi chegara — konfiguratsiyadan qat'iy nazar
DOI_COLUMN_NAMES = {'doi'}       # case-insensitive taqqoslanadi


class ImportParseError(ValueError):
    """Foydalanuvchiga ko'rsatiladigan parsing xatosi (texnik trace emas)."""

    def __init__(self, public_message, code='parse_error'):
        self.public_message = public_message
        self.code = code
        super().__init__(public_message)


@dataclass
class ParsedInput:
    """Parsingdan keyingi, hali DOI-validatsiya qilinmagan xom qatorlar.

    `rows` — [(row_number, raw_text), ...] — bo'sh qatorlar allaqachon
    tashlab yuborilgan (§12: "Bo'sh qatorlarni tashla").
    """
    rows: list[tuple[int, str]] = field(default_factory=list)
    total_lines_seen: int = 0
    blank_lines_skipped: int = 0


def parse_doi_list(text, max_rows=None):
    """Textarea'dan — har qatordan bitta xom DOI (§12.A).

    `10.xxxx/aaa`, `https://doi.org/10.xxxx/bbb`, `doi:10.xxxx/ccc` — barchasi
    shu bosqichda XOM matn sifatida saqlanadi; normalizatsiya keyingi
    bosqichda (`utils.article_rules.normalize_doi`).
    """
    if text is None:
        text = ''
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    rows: list[tuple[int, str]] = []
    blanks = 0
    row_number = 0
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            blanks += 1
            continue
        row_number += 1
        rows.append((row_number, line[:1000]))
        if max_rows and len(rows) > max_rows:
            raise ImportParseError(
                "Ro'yxatda ruxsat etilgandan ko'p qator bor (maksimum %d)."
                % max_rows, code='too_many_rows')
    return ParsedInput(rows=rows, total_lines_seen=len(lines),
                       blank_lines_skipped=blanks)


def _sniff_delimiter(sample: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
        return dialect.delimiter
    except csv.Error:
        return ','


def _decode_csv_bytes(raw: bytes) -> str:
    """UTF-8 (BOM bilan yoki bo'lmasa) — boshqa kodировка qo'llab-quvvatlanmaydi (§12.B)."""
    if not raw:
        raise ImportParseError("CSV fayl bo'sh.", code='empty_file')
    try:
        if raw.startswith(b'\xef\xbb\xbf'):
            return raw.decode('utf-8-sig')
        return raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ImportParseError(
            "CSV fayl UTF-8 kodировkada emas.", code='invalid_encoding') from exc


def parse_csv(raw_bytes, max_rows=None):
    """CSV baytlaridan `doi` ustunini o'qiydi (§12.B, §13).

    Talablar:
      * UTF-8 (BOM ixtiyoriy);
      * `,` yoki `;` ajratuvchi (avtomatik aniqlanadi);
      * `doi`/`DOI`/`Doi` ustun nomi — case-insensitive;
      * bo'sh fayl, ustun yo'q, qator juda ko'p — aniq xato.
    """
    text = _decode_csv_bytes(raw_bytes)
    if not text.strip():
        raise ImportParseError("CSV fayl bo'sh.", code='empty_file')

    sample = text[:4096]
    delimiter = _sniff_delimiter(sample)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        raise ImportParseError("CSV fayl bo'sh.", code='empty_file')
    except csv.Error as exc:
        raise ImportParseError("CSV fayl formati noto'g'ri.",
                               code='malformed_csv') from exc

    header_norm = [(h or '').strip().lower() for h in header]
    if len(set(header_norm)) != len(header_norm):
        raise ImportParseError(
            "CSV sarlavhasida takrorlangan ustun nomlari bor.",
            code='duplicate_headers')

    doi_col = None
    for idx, name in enumerate(header_norm):
        if name in DOI_COLUMN_NAMES:
            doi_col = idx
            break
    if doi_col is None:
        raise ImportParseError(
            "CSV faylida 'doi' ustuni topilmadi.", code='missing_doi_column')

    effective_max = min(max_rows or MAX_CSV_ROWS_HARD_CAP, MAX_CSV_ROWS_HARD_CAP)
    rows: list[tuple[int, str]] = []
    blanks = 0
    seen_lines = 0
    row_number = 0
    # `csv.reader` — LAZY iterator: xato (masalan, bitta maydon
    # `csv.field_size_limit()`dan katta) istalgan qatorda, nafaqat
    # sarlavhada, chiqishi mumkin. `for` sikli o'rniga qo'lda `next()`
    # ishlatiladi — shunda har bir qatorni o'qishda `csv.Error` ADMIN'GA
    # 500 sifatida emas, aniq xato sifatida chiqadi (§13 — "malformed CSV").
    while True:
        try:
            raw_row = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            raise ImportParseError(
                "CSV fayl %d-qatordan keyin buzilgan." % (seen_lines + 1),
                code='malformed_csv') from exc
        seen_lines += 1
        if doi_col >= len(raw_row):
            blanks += 1
            continue
        value = (raw_row[doi_col] or '').strip()
        if not value:
            blanks += 1
            continue
        row_number += 1
        rows.append((row_number, value[:1000]))
        if row_number > effective_max:
            raise ImportParseError(
                "CSV faylida ruxsat etilgandan ko'p qator bor (maksimum %d)."
                % effective_max, code='too_many_rows')

    if not rows:
        raise ImportParseError(
            "CSV faylida 'doi' ustuni ostida qiymat topilmadi.",
            code='empty_file')

    return ParsedInput(rows=rows, total_lines_seen=seen_lines,
                       blank_lines_skipped=blanks)
