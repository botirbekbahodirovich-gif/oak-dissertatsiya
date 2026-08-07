# -*- coding: utf-8 -*-
"""Provayder qiymatlarini tozalash va bizning lug'atlarimizga o'girish.

Bu yerdagi hamma narsa tashqi matnni ISHONCHSIZ deb qaraydi (§31): HTML/JATS
belgilash olib tashlanadi, bo'shliqlar yig'iladi, uzunlik cheklanadi. Shablon
hech qachon `|safe` ishlatmaydi, lekin himoya bitta qatlamda qolmasligi kerak.

Litsenziya o'girish `utils.article_rules.LICENSES` kalitlariga bog'langan —
ikkinchi litsenziya lug'ati yaratilmaydi.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from utils.article_rules import LICENSES

# Maydon uzunliklari — DB ustunlari bilan bir xil (blueprints/articles.py).
MAX_TITLE = 500
MAX_ABSTRACT = 20000
MAX_JOURNAL = 300
MAX_SHORT = 50
MAX_NAME = 300
MAX_AFFILIATION = 400

_WS_RE = re.compile(r'\s+')
_TAG_RE = re.compile(r'<[^>]*>')
_ORCID_RE = re.compile(r'(\d{4}-\d{4}-\d{4}-\d{3}[\dXx])')
_ISSN_RE = re.compile(r'^\d{4}-\d{3}[\dXx]$')
_OPENALEX_WORK_RE = re.compile(r'W\d{4,15}')
_OPENALEX_AUTHOR_RE = re.compile(r'A\d{4,15}')
_OPENALEX_INST_RE = re.compile(r'I\d{4,15}')

# JATS abstraktlarida uchraydigan bo'lim sarlavhalari: matn sifatida qolsa
# "AbstractBackgroundNatijalar" kabi yopishib ketadi, shuning uchun teg
# o'rniga bo'shliq qo'yiladi.
_BLOCK_TAGS = re.compile(r'</?(?:p|div|sec|title|br|li|ul|ol|h\d)[^>]*>', re.I)
_SCRIPT_RE = re.compile(r'<(script|style)\b[^>]*>.*?</\1\s*>', re.I | re.S)


def clean_text(raw, max_length=None):
    """Bir qatorli matn: belgilash yo'q, bo'shliqlar yig'ilgan."""
    if raw is None:
        return None
    text = str(raw)
    text = _BLOCK_TAGS.sub(' ', text)
    text = _TAG_RE.sub('', text)
    text = _unescape(text)
    text = _WS_RE.sub(' ', text).strip()
    if not text:
        return None
    return text[:max_length] if max_length else text


def clean_abstract(raw, max_length=MAX_ABSTRACT):
    """Crossref abstrakti JATS XML bo'lishi mumkin — sof matnga aylantiriladi.

    `bleach` (loyihada mavjud) barcha teglarni olib tashlaydi: allowlist EMAS,
    chunki annotatsiya bizning shablonimizda oddiy matn sifatida ko'rsatiladi
    va hech qanday tashqi belgilash saqlanishi shart emas. Shu bilan
    `<script>`, `<img onerror>` va boshqa hech narsa o'tmaydi.
    """
    if raw is None:
        return None
    text = str(raw)
    # Crossref ba'zan "Abstract" so'zini <title> ichida beradi — u annotatsiya
    # matnining bir qismi emas, sarlavhasi.
    text = re.sub(r'<jats:title>\s*Abstract\s*</jats:title>', ' ', text, flags=re.I)
    # `bleach` teg ichidagi matnni SAQLAB qoladi (strip=True). Skript va uslub
    # bloklarining mazmuni annotatsiya emas, shuning uchun ular butunlay
    # olib tashlanadi — aks holda "alert(1)" matn bo'lib qolardi.
    text = _SCRIPT_RE.sub(' ', text)
    text = _BLOCK_TAGS.sub('\n', text)
    try:
        import bleach
        text = bleach.clean(text, tags=[], attributes={}, strip=True)
    except ImportError:                     # bleach bo'lmasa ham xom HTML qolmasin
        text = _TAG_RE.sub('', text)
    text = _unescape(text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = '\n'.join(line.strip() for line in text.split('\n')).strip()
    return text[:max_length] if text else None


def _unescape(text):
    import html
    return html.unescape(text)


def normalize_orcid(raw):
    """"https://orcid.org/0000-0002-...' -> '0000-0002-...'. Xato format -> None."""
    if not raw:
        return None
    match = _ORCID_RE.search(str(raw))
    return match.group(1).upper() if match else None


def normalize_issn(raw):
    if not raw:
        return None
    value = str(raw).strip().upper()
    return value if _ISSN_RE.match(value) else None


def openalex_short_id(raw, pattern=_OPENALEX_WORK_RE):
    """"https://openalex.org/W123" -> "W123". Kutilmagan format -> None."""
    if not raw:
        return None
    match = pattern.search(str(raw))
    return match.group(0) if match else None


def openalex_author_id(raw):
    return openalex_short_id(raw, _OPENALEX_AUTHOR_RE)


def openalex_institution_id(raw):
    return openalex_short_id(raw, _OPENALEX_INST_RE)


def date_from_parts(parts):
    """Crossref `date-parts`: [[2024, 3, 15]] -> date(2024, 3, 15).

    Crossref tez-tez faqat yil, ba'zan yil+oy beradi. To'liq sana yig'ilmasa
    `None` qaytadi — o'ylab topilgan 1-yanvar DB'ga tushmaydi.
    """
    if not parts:
        return None
    if isinstance(parts, dict):
        parts = parts.get('date-parts')
    if not isinstance(parts, list) or not parts:
        return None
    first = parts[0]
    if not isinstance(first, list) or len(first) < 3:
        return None
    try:
        return date(int(first[0]), int(first[1]), int(first[2]))
    except (TypeError, ValueError):
        return None


def year_from_parts(parts):
    """Faqat yil — sana to'liq bo'lmasa ham foydali."""
    if isinstance(parts, dict):
        parts = parts.get('date-parts')
    if not isinstance(parts, list) or not parts:
        return None
    first = parts[0]
    if not isinstance(first, list) or not first:
        return None
    return _valid_year(first[0])


def parse_iso_date(raw):
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _valid_year(value):
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if 1500 <= year <= date.today().year + 2 else None


valid_year = _valid_year


# ── Litsenziya ──────────────────────────────────────────────────────────────
# Creative Commons havolasidan bizning kalitga. Diqqat: `nd` (NoDerivatives)
# variantlari bizning lug'atda YO'Q va ular ruxsat beruvchi kalitga
# o'girilmaydi — aks holda qayta tarqatish huquqi bo'lmagan maqola CC BY
# bo'lib qolardi.
_CC_PATTERNS = (
    ('by-nc-nd', None),
    ('by-nd', None),
    ('by-nc-sa', 'cc_by_nc_sa'),
    ('by-nc', 'cc_by_nc'),
    ('by-sa', 'cc_by_sa'),
    ('by', 'cc_by'),
)


def map_license_url(url):
    """(license_type_or_None, label). Tanimagan havola -> (None, xom matn)."""
    if not url:
        return None, None
    value = str(url).strip()
    lowered = value.lower()

    if 'creativecommons.org/publicdomain/zero' in lowered:
        return 'cc0', 'CC0'
    if 'creativecommons.org/publicdomain/mark' in lowered:
        return 'public_domain', 'Public Domain Mark'
    if 'creativecommons.org/licenses/' in lowered:
        tail = lowered.split('creativecommons.org/licenses/', 1)[1]
        for token, key in _CC_PATTERNS:
            if tail.startswith(token + '/') or tail == token:
                label = 'CC ' + token.upper()
                # `nd` variantlari bizda yo'q — `other` sifatida qoladi va
                # admin qo'lda hal qiladi.
                return (key if key in LICENSES else None), label
        return None, 'Creative Commons (aniqlanmagan)'
    return None, None


def map_license_label(label):
    """Unpaywall `license` maydoni: "cc-by", "cc0", "public-domain"."""
    if not label:
        return None, None
    token = str(label).strip().lower()
    if token in ('cc0', 'cc-0'):
        return 'cc0', 'CC0'
    if token in ('public-domain', 'pd'):
        return 'public_domain', 'Public Domain'
    if token.startswith('cc-'):
        tail = token[3:]
        for pattern, key in _CC_PATTERNS:
            if tail == pattern:
                return (key if key in LICENSES else None), 'CC ' + tail.upper()
        return None, 'CC ' + tail.upper()
    return None, str(label).strip()[:80]


# ── Muallif ismi ────────────────────────────────────────────────────────────

def author_display_name(given, family, fallback=None):
    parts = [p for p in (clean_text(given, MAX_NAME), clean_text(family, MAX_NAME)) if p]
    if parts:
        return ' '.join(parts)[:MAX_NAME]
    return clean_text(fallback, MAX_NAME)


def name_key(name):
    """Ikki provayderdagi ismni QAT'IY solishtirish uchun kalit.

    Faqat aksentlar, tinish belgilari va harf registri e'tibordan
    chiqariladi. Bu FUZZY emas: "J. Smith" va "John Smith" har xil kalit
    beradi va birlashtirilmaydi (§17) — ular boshqa odam bo'lishi mumkin.
    """
    if not name:
        return ''
    value = unicodedata.normalize('NFKD', str(name))
    value = ''.join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r'[^\w\s]', ' ', value.lower(), flags=re.UNICODE)
    return _WS_RE.sub(' ', value).strip()
