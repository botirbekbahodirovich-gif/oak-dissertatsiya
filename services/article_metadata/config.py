# -*- coding: utf-8 -*-
"""Metadata provayderlari uchun konfiguratsiya (Phase 2C.1).

Konfiguratsiya ATAYIN obyekt sifatida uzatiladi, modul darajasida
`os.environ` dan o'qilmaydi: shu tufayli testlar env'ga tegmasdan ishlaydi va
keyingi bulk importer boshqa kontakt e-mail bilan chaqira oladi (§43).

Kontakt e-mail — bitta qiymat. Crossref "polite pool" va Unpaywall ikkalasi
ham murojaat qiluvchining e-mailini so'raydi va ular bir xil bo'lishi tabiiy,
shuning uchun `SCHOLARLY_API_EMAIL` umumiy default bo'lib turadi; har bir
provayder uchun alohida qiymat kerak bo'lsa, uni ustidan yozish mumkin.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Timeoutlar. Ulanish tez bo'lishi kerak (uzoq kutish = server yotgan),
# o'qish esa Crossref sekin javob berganda ham yetarli bo'lsin (§8).
DEFAULT_CONNECT_TIMEOUT = 4.0
DEFAULT_READ_TIMEOUT = 12.0

# Qayta urinish CHEKLANGAN. Admin tugma bosib turibdi — cheksiz backoff
# bilan uni 40 soniya kutdirib bo'lmaydi.
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_BACKOFF_SECONDS = 0.6

# Uchala provayder parallel chaqiriladi; shu ham umumiy chegara.
DEFAULT_TOTAL_BUDGET = 20.0

# Kesh muddati. Bibliografik metadata soatlab o'zgarmaydi; iqtibos soni
# o'zgaradi, lekin u baribir "tekshirilgan sana" bilan saqlanadi (§27).
DEFAULT_CACHE_TTL = 12 * 3600

USER_AGENT_TEMPLATE = 'Olimlar.uz/{version} (+https://olimlar.uz; mailto:{email})'
APP_VERSION = '2C.1'


@dataclass(frozen=True)
class MetadataConfig:
    contact_email: str | None = None
    crossref_mailto: str | None = None
    unpaywall_email: str | None = None
    openalex_mailto: str | None = None
    openalex_api_key: str | None = None

    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    read_timeout: float = DEFAULT_READ_TIMEOUT
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS
    total_budget: float = DEFAULT_TOTAL_BUDGET
    cache_ttl: int = DEFAULT_CACHE_TTL

    crossref_base: str = 'https://api.crossref.org/works'
    openalex_base: str = 'https://api.openalex.org/works'
    unpaywall_base: str = 'https://api.unpaywall.org/v2'

    # ── Hosila qiymatlar ────────────────────────────────────────────────────

    @property
    def crossref_contact(self) -> str | None:
        return self.crossref_mailto or self.contact_email

    @property
    def unpaywall_contact(self) -> str | None:
        return self.unpaywall_email or self.contact_email

    @property
    def openalex_contact(self) -> str | None:
        return self.openalex_mailto or self.contact_email

    def user_agent(self, email: str | None = None) -> str:
        """Odobli identifikatsiya. E-mailsiz ham User-Agent aniq qoladi."""
        contact = email or self.contact_email
        if not contact:
            return 'Olimlar.uz/%s (+https://olimlar.uz)' % APP_VERSION
        return USER_AGENT_TEMPLATE.format(version=APP_VERSION, email=contact)

    @property
    def timeout(self) -> tuple[float, float]:
        return (self.connect_timeout, self.read_timeout)


def _float_env(name: str, default: float) -> float:
    raw = (os.environ.get(name) or '').strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _int_env(name: str, default: int) -> int:
    raw = (os.environ.get(name) or '').strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def config_from_env(environ=None) -> MetadataConfig:
    """Env'dan konfiguratsiya. Kalitlar HECH QAYERGA log qilinmaydi (§33)."""
    env = environ if environ is not None else os.environ
    shared = (env.get('SCHOLARLY_API_EMAIL') or '').strip() or None
    return MetadataConfig(
        contact_email=shared,
        crossref_mailto=(env.get('CROSSREF_MAILTO') or '').strip() or None,
        unpaywall_email=(env.get('UNPAYWALL_EMAIL') or '').strip() or None,
        # `OPENALEX_MAILTO` allaqachon mavjud (blueprints/maqolalar.py va
        # .env.example) — yangi nom o'ylab topilmaydi.
        openalex_mailto=(env.get('OPENALEX_MAILTO') or '').strip() or None,
        openalex_api_key=(env.get('OPENALEX_API_KEY') or '').strip() or None,
        connect_timeout=_float_env('METADATA_CONNECT_TIMEOUT', DEFAULT_CONNECT_TIMEOUT),
        read_timeout=_float_env('METADATA_READ_TIMEOUT', DEFAULT_READ_TIMEOUT),
        max_attempts=_int_env('METADATA_MAX_ATTEMPTS', DEFAULT_MAX_ATTEMPTS),
        total_budget=_float_env('METADATA_TOTAL_BUDGET', DEFAULT_TOTAL_BUDGET),
        cache_ttl=_int_env('METADATA_CACHE_TTL', DEFAULT_CACHE_TTL),
    )
