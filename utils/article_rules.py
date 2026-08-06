# -*- coding: utf-8 -*-
"""Ilmiy maqolalar moduli — domen qoidalari (DB'siz, sof Python).

Bu modul ataylab `blueprints/articles.py` dan ajratilgan: PDF'ni ommaga
ko'rsatish huquqi, litsenziya, DOI normalizatsiyasi va status o'tishlari
Postgres'siz test qilinishi kerak (lokal DB stale — docs/articles-module.md).

ENG MUHIM QOIDA (spetsifikatsiya §23):
    PDF fayl mavjudligi uni ko'rsatishga huquq bermaydi.
    Yagona haqiqat manbai — `can_read_pdf()`. Shablon ham, route ham,
    admin preview ham AYNAN shu funksiyani chaqiradi; qoida nusxalanmaydi.
"""
from __future__ import annotations

import re
import unicodedata

# ── Kirish turi (access_type) ────────────────────────────────────────────────
ACCESS_TYPES = {
    'open_access': 'Ochiq kirish',
    'author_permission': 'Muallif ruxsati bilan',
    'public_domain': 'Umumiy mulk (public domain)',
    'repository_copy': 'Repozitoriy nusxasi',
    'metadata_only': 'Faqat metadata',
    'restricted': 'Cheklangan',
}

# Serverda PDF saqlash va ko'rsatishga ruxsat etilgan kirish turlari.
# `metadata_only` va `restricted` — PDF umuman yuklanmaydi (§9).
PDF_ALLOWED_ACCESS_TYPES = frozenset({
    'open_access', 'author_permission', 'public_domain', 'repository_copy',
})

# ── Maqola versiyasi (version_type) ──────────────────────────────────────────
VERSION_TYPES = {
    'version_of_record': 'Nashr etilgan yakuniy versiya',
    'accepted_manuscript': 'Qabul qilingan qo‘lyozma',
    'preprint': 'Preprint',
    'author_copy': 'Muallif nusxasi',
    'repository_copy': 'Repozitoriy nusxasi',
    'other': 'Boshqa',
}

# ── Manba turi (source_type) ─────────────────────────────────────────────────
SOURCE_TYPES = {
    'open_journal': 'Ochiq kirishli jurnal',
    'university_repository': 'Universitet repozitoriysi',
    'subject_repository': 'Soha repozitoriysi',
    'author_website': 'Muallif sayti',
    'author_submission': 'Muallif taqdim etgan',
    'public_domain': 'Umumiy mulk',
    'other_verified_source': 'Boshqa tekshirilgan manba',
}

# ── Tekshiruv holati (verification_status) ───────────────────────────────────
VERIFICATION_STATUSES = {
    'draft': 'Qoralama',
    'pending_review': 'Tekshiruv kutilmoqda',
    'verified': 'Tekshirilgan',
    'rejected': 'Rad etilgan',
    'copyright_hold': 'Mualliflik huquqi bo‘yicha to‘xtatilgan',
}

# ── Nashr holati (publication_status) ────────────────────────────────────────
PUBLICATION_STATUSES = {
    'draft': 'Qoralama',
    'published': 'Nashr etilgan',
    'archived': 'Arxivlangan',
}

# ── Litsenziya ───────────────────────────────────────────────────────────────
# `unknown` — ataylab default: litsenziya aniqlanmaguncha PDF ommaga chiqmaydi.
LICENSES = {
    'cc_by': 'CC BY',
    'cc_by_sa': 'CC BY-SA',
    'cc_by_nc': 'CC BY-NC',
    'cc_by_nc_sa': 'CC BY-NC-SA',
    'cc0': 'CC0',
    'public_domain': 'Public Domain',
    'author_permission': 'Author Permission',
    'repository_permission': 'Repository Permission',
    'other': 'Other',
    'unknown': 'Unknown',
}

UNKNOWN_LICENSE = 'unknown'

# Creative Commons litsenziyalarining kanonik havolalari — admin qo'lda
# kiritmasa shu ishlatiladi (license_url bo'sh qolsa).
LICENSE_URLS = {
    'cc_by': 'https://creativecommons.org/licenses/by/4.0/',
    'cc_by_sa': 'https://creativecommons.org/licenses/by-sa/4.0/',
    'cc_by_nc': 'https://creativecommons.org/licenses/by-nc/4.0/',
    'cc_by_nc_sa': 'https://creativecommons.org/licenses/by-nc-sa/4.0/',
    'cc0': 'https://creativecommons.org/publicdomain/zero/1.0/',
}

LANGUAGES = {
    'uz': "O‘zbek",
    'ru': 'Rus',
    'en': 'Ingliz',
    'kaa': 'Qoraqalpoq',
    'other': 'Boshqa',
}


class ArticleValidationError(ValueError):
    """Foydalanuvchiga ko'rsatiladigan validatsiya xatosi (texnik trace emas)."""

    def __init__(self, public_message, field=None):
        self.public_message = public_message
        self.field = field
        super().__init__(public_message)


# ── PDF ommaviyligi ──────────────────────────────────────────────────────────

def pdf_block_reason(article):
    """PDF nega ochiq emasligini qaytaradi (yoki None — ochiq).

    `article` — dict yoki dict kabi obyekt (DB qatori). Yagona qoida manbai;
    `can_read_pdf()` shundan hosila. Sabab qaytarilishi admin UI uchun kerak:
    admin nima yetishmayotganini ko'rishi shart, foydalanuvchi esa faqat
    tugmaning yo'qligini ko'radi.
    """
    get = article.get if hasattr(article, 'get') else lambda k, d=None: getattr(article, k, d)

    if not (get('pdf_path') or ''):
        return 'PDF fayl yuklanmagan'
    if get('publication_status') != 'published':
        return 'Maqola nashr etilmagan'
    if get('verification_status') == 'copyright_hold':
        return 'Mualliflik huquqi bo‘yicha to‘xtatilgan'
    if get('verification_status') != 'verified':
        return 'Maqola tekshiruvdan o‘tmagan'
    if get('access_type') not in PDF_ALLOWED_ACCESS_TYPES:
        return 'Kirish turi PDF ko‘rsatishga ruxsat bermaydi'
    license_type = (get('license_type') or UNKNOWN_LICENSE)
    if license_type == UNKNOWN_LICENSE or license_type not in LICENSES:
        return 'Litsenziya aniqlanmagan'
    if not (get('source_name') or '').strip():
        return 'Manba ko‘rsatilmagan'
    return None


def can_read_pdf(article):
    """PDF'ni ommaga ko'rsatish mumkinmi (§23). Backend ham, shablon ham shuni chaqiradi."""
    return pdf_block_reason(article) is None


def can_store_pdf(access_type):
    """Bu kirish turi uchun serverga PDF yuklash mumkinmi (§9)."""
    return access_type in PDF_ALLOWED_ACCESS_TYPES


# ── DOI ──────────────────────────────────────────────────────────────────────

_DOI_PREFIX_RE = re.compile(r'^\s*(?:https?://)?(?:dx\.)?(?:doi\.org/|doi:)\s*', re.I)
_DOI_RE = re.compile(r'^10\.\d{4,9}/[-._;()/:a-z0-9<>\[\]+]+$', re.I)


def normalize_doi(raw):
    """"https://doi.org/10.1234/ABC" -> "10.1234/abc"; bo'sh -> None.

    DOI registri prefiksni katta-kichik harfga sezgir emas deb belgilaydi,
    shuning uchun taqqoslash va UNIQUE indeks uchun lowercase saqlanadi.
    """
    if raw is None:
        return None
    value = _DOI_PREFIX_RE.sub('', str(raw)).strip().strip('.').strip()
    return value.lower() or None


def validate_doi(raw):
    """(doi_or_None). DOI ixtiyoriy; berilgan bo'lsa formati to'g'ri bo'lishi shart.

    Noto'g'ri format 500 emas, ArticleValidationError beradi (§27).
    """
    doi = normalize_doi(raw)
    if doi is None:
        return None
    if len(doi) > 255 or not _DOI_RE.match(doi):
        raise ArticleValidationError(
            'DOI formati noto‘g‘ri. Namuna: 10.1234/abcd.2024.01', 'doi')
    return doi


# ── Slug ─────────────────────────────────────────────────────────────────────

_SLUG_STRIP = "'ʻʼ‘’`"


def make_slug(text, fallback='maqola', max_len=180):
    """Kirill/lotin sarlavhadan ASCII slug. Faqat raqamdan iborat bo'lmaydi."""
    from institutions import transliterate

    value = transliterate((text or '').lower())
    for ch in _SLUG_STRIP:
        value = value.replace(ch, '')
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode()
    value = re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')[:max_len].strip('-')
    if not value or value.replace('-', '').isdigit():
        value = f'{fallback}-{value}'.strip('-')
    return value


# ── Umumiy tanlov validatsiyasi ──────────────────────────────────────────────

def validate_choice(value, allowed, field, label):
    """Cheklangan qiymatlar (Python enum o'rniga validated string — loyiha uslubi)."""
    if value not in allowed:
        raise ArticleValidationError(
            f'{label} qiymati noto‘g‘ri: {value!r}', field)
    return value


def validate_article_rights(access_type, license_type, pdf_present, rights_confirmed):
    """Huquqiy izchillikni tekshiradi — PDF saqlashdan OLDIN chaqiriladi (§9, §14)."""
    validate_choice(access_type, ACCESS_TYPES, 'access_type', 'Kirish turi')
    validate_choice(license_type, LICENSES, 'license_type', 'Litsenziya')
    if pdf_present and not can_store_pdf(access_type):
        raise ArticleValidationError(
            '"%s" kirish turida PDF fayl saqlanmaydi. Faqat metadata kiriting.'
            % ACCESS_TYPES[access_type], 'access_type')
    if pdf_present and not rights_confirmed:
        raise ArticleValidationError(
            'PDF yuklash uchun huquqiy tasdiqni belgilang.', 'rights_confirmed')
    return True
