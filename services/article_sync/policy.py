# -*- coding: utf-8 -*-
"""Field policy — bitta joyda (Phase 2C.3A, §5). Diff engine va apply
allowlist AYNAN shu ro'yxatlardan o'qiydi — boshqa joyda takrorlanmaydi.
"""
from __future__ import annotations

import unicodedata

# ── Oddiy matn/skalyar maydonlar — REVIEW (§5) ───────────────────────────────
# Mavjud, ko'rib chiqilgan/qo'lda kiritilgan qiymat avtomatik almashtirilmaydi.
REVIEW_SCALAR_FIELDS = (
    'title', 'abstract', 'journal_name', 'publication_year', 'publication_date',
    'volume', 'issue', 'pages', 'language', 'external_url',
)

# Registr/tinish belgisiga sezgir bo'lmagan solishtirish (§12 — false-positive
# kamaytirish). Sana/yil kabi aniq qiymatlar bu yerda emas.
CASE_INSENSITIVE_FIELDS = frozenset({'title', 'abstract', 'journal_name',
                                     'volume', 'issue', 'pages', 'external_url'})

# ── Tashqi identifikatorlar — bo'sh bo'lsa SAFE, conflict bo'lsa REVIEW (§5) ─
EXTERNAL_ID_FIELDS = ('openalex_id', 'crossref_member_id', 'primary_issn')

# ── Faqat INFORMATIONAL — hech qachon applicable EMAS (§8, §9, §10) ─────────
INFORMATIONAL_FIELDS = ('oa_status', 'license_candidates', 'candidate_pdf_url')

FIELD_LABELS = {
    'title': 'Sarlavha', 'abstract': 'Annotatsiya', 'journal_name': 'Jurnal',
    'publication_year': 'Nashr yili', 'publication_date': 'Nashr sanasi',
    'volume': 'Jild', 'issue': 'Son', 'pages': 'Betlar', 'language': 'Til',
    'external_url': 'Tashqi havola',
    'openalex_id': 'OpenAlex ID', 'crossref_member_id': "Crossref a'zo ID",
    'primary_issn': 'Asosiy ISSN',
    'citation_count': 'Iqtibos soni',
    'is_retracted': 'Chaqirib olingan (retraction)',
    'authors': 'Mualliflar',
    'doi': 'DOI',
    'oa_status': 'Tashqi ochiq kirish holati',
    'license_candidates': 'Litsenziya nomzodlari',
    'candidate_pdf_url': 'Nomzod PDF havolasi',
}

# `apply_article_metadata_changes` shu ro'yxatdan tashqari HECH QANDAY
# maydonni qabul qilmaydi (§16 — allowlist, client arbitrary field yubora
# olmaydi).
APPLICABLE_FIELDS = frozenset(
    REVIEW_SCALAR_FIELDS + EXTERNAL_ID_FIELDS + ('citation_count', 'authors',
                                                  'is_retracted'))

# Safe maydonlar default tanlangan, review/critical — tanlanmagan (§22).
DEFAULT_SELECTED_SEVERITIES = frozenset({'safe'})


def normalize_text(value):
    """Trim + whitespace collapse + Unicode NFKC + bo'sh satr->None (§12).

    Taqqoslash uchungina ishlatiladi — UI original qiymatni ko'rsatadi,
    agressiv transliteratsiya qilinmaydi.
    """
    if value is None:
        return None
    text = unicodedata.normalize('NFKC', str(value))
    text = ' '.join(text.split())
    return text or None


def values_equal(field_name, old, new):
    old_norm = normalize_text(old) if isinstance(old, str) else old
    new_norm = normalize_text(new) if isinstance(new, str) else new
    if field_name in CASE_INSENSITIVE_FIELDS and isinstance(old_norm, str) \
            and isinstance(new_norm, str):
        return old_norm.casefold() == new_norm.casefold()
    return old_norm == new_norm
