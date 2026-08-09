# -*- coding: utf-8 -*-
"""Discovery konfiguratsiyasi (Phase 2D.1).

`services/article_metadata/config.py` bilan bir xil naqsh: ATAYIN obyekt
sifatida uzatiladi, modul darajasida `os.environ` o'qilmaydi (testlar env'ga
tegmasdan ishlaydi). Metadata provayder konfiguratsiyasi bilan ATAYIN
ARALASHTIRILMAYDI (§7) — bu alohida, kichikroq dataclass, lekin `OPENALEX_API_KEY`
/ `OPENALEX_MAILTO` bir xil env nomlarini o'qiydi (loyihada ular allaqachon
`.env.example`da bor — yangi ikkinchi nom o'ylab topilmaydi).

`fetch_json` (`services/article_metadata/http.py`) bilan qayta ishlatish
uchun bu dataclass ATAYIN bir xil "shape"ga ega: `.timeout` (tuple),
`.max_attempts`, `.backoff_seconds`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# ── Tashqi so'rov sozlamalari (OpenAlex /works qidiruv) ────────────────────
DEFAULT_CONNECT_TIMEOUT = 4.0
DEFAULT_READ_TIMEOUT = 12.0
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_BACKOFF_SECONDS = 0.6
# Ikkala provayder PARALLEL chaqirilganda (§13) umumiy chegara — bitta
# sekin/osilib qolgan provayder ikkinchisining natijasini kutdirmaydi
# (services/article_metadata/config.py bilan bir xil qiymat/naqsh).
DEFAULT_TOTAL_BUDGET = 20.0

# OpenAlex/Crossref bazalari — kod tomonidan qattiq belgilangan (§35 SSRF:
# admin host bera olmaydi). Crossref manzili `services/article_metadata/
# config.py:crossref_base` bilan AYNAN bir xil — rasmiy hujjat (2026-08,
# github.com/CrossRef/rest-api-doc va crossref.org/documentation) tasdiqlagan
# yagona host.
OPENALEX_WORKS_URL = 'https://api.openalex.org/works'
CROSSREF_WORKS_URL = 'https://api.crossref.org/works'

APP_VERSION = '2D.2'
USER_AGENT_TEMPLATE = 'Olimlar.uz/{version} (+https://olimlar.uz; mailto:{email})'

# ── So'rov validatsiyasi (§8) ────────────────────────────────────────────────
KEYWORD_MIN_LENGTH = 3
KEYWORD_MAX_LENGTH = 300
DEFAULT_WINDOW_DAYS = 30
# "12 oy" — kalendar oyi emas, doimiy 366 kunlik chegara (kabisa yiliga ham
# yetadi) sifatida talqin qilinadi; oddiy va deterministik.
MAX_WINDOW_DAYS = 366
DEFAULT_RESULT_LIMIT = 50
HARD_RESULT_LIMIT = 100

# ── Preview token (§22) ──────────────────────────────────────────────────────
DEFAULT_PREVIEW_TTL_SECONDS = 900          # 15 daqiqa
DEFAULT_PREVIEW_CONSUMED_TTL_SECONDS = 900  # double-click idempotency oynasi

# ── Rate limit (§32) ─────────────────────────────────────────────────────────
DEFAULT_SEARCHES_PER_MINUTE = 10


@dataclass(frozen=True)
class ArticleDiscoveryConfig:
    openalex_api_key: str | None = None
    openalex_mailto: str | None = None
    # Phase 2D.2 — Crossref uchun alohida mailto, `services/article_metadata/
    # config.py:crossref_mailto` bilan bir xil env nomi (`CROSSREF_MAILTO`,
    # ticket §4: ikkinchi nom o'ylab topilmaydi).
    crossref_mailto: str | None = None
    contact_email: str | None = None

    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    read_timeout: float = DEFAULT_READ_TIMEOUT
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS

    keyword_min_length: int = KEYWORD_MIN_LENGTH
    keyword_max_length: int = KEYWORD_MAX_LENGTH
    default_window_days: int = DEFAULT_WINDOW_DAYS
    max_window_days: int = MAX_WINDOW_DAYS
    default_result_limit: int = DEFAULT_RESULT_LIMIT
    hard_result_limit: int = HARD_RESULT_LIMIT

    preview_ttl_seconds: int = DEFAULT_PREVIEW_TTL_SECONDS
    preview_consumed_ttl_seconds: int = DEFAULT_PREVIEW_CONSUMED_TTL_SECONDS
    searches_per_minute: int = DEFAULT_SEARCHES_PER_MINUTE

    openalex_base: str = OPENALEX_WORKS_URL
    crossref_base: str = CROSSREF_WORKS_URL
    # Ikkala provayder concurrent chaqirilganda umumiy vaqt byudjeti (§13) —
    # `services/article_metadata/config.py:DEFAULT_TOTAL_BUDGET` bilan bir
    # xil naqsh (ThreadPoolExecutor + future.result(timeout=...)).
    total_budget: float = DEFAULT_TOTAL_BUDGET

    @property
    def openalex_contact(self) -> str | None:
        return self.openalex_mailto or self.contact_email

    @property
    def crossref_contact(self) -> str | None:
        return self.crossref_mailto or self.contact_email

    @property
    def timeout(self) -> tuple[float, float]:
        return (self.connect_timeout, self.read_timeout)

    def user_agent(self, email: str | None = None) -> str:
        contact = email or self.openalex_contact
        if not contact:
            return 'Olimlar.uz/%s (+https://olimlar.uz)' % APP_VERSION
        return USER_AGENT_TEMPLATE.format(version=APP_VERSION, email=contact)

    @property
    def is_configured(self) -> bool:
        """OpenAlex uchun — kalit yoki mailto talab qiladi (§7, o'zgarmagan).

        Crossref BUNGA kirmaydi: rasmiy hujjat (2026-08 tekshirilgan)
        bo'yicha /works uchun kalit SHART EMAS va mailto'siz ham ishlaydi
        (sekinroq navbat, xato EMAS) — xuddi mavjud
        `services/article_metadata/crossref.py` bilan bir xil siyosat.
        Crossref provayderining o'zi hech qachon `not_configured`
        qaytarmaydi (`crossref.py:search`)."""
        return bool(self.openalex_api_key or self.openalex_contact)


def _float_env(name, default, environ):
    raw = (environ.get(name) or '').strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _int_env(name, default, environ):
    raw = (environ.get(name) or '').strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def config_from_env(environ=None) -> ArticleDiscoveryConfig:
    """Env'dan konfiguratsiya. Kalit qiymati HECH QAYERGA log/UI'ga chiqmaydi."""
    env = environ if environ is not None else os.environ
    return ArticleDiscoveryConfig(
        openalex_api_key=(env.get('OPENALEX_API_KEY') or '').strip() or None,
        openalex_mailto=(env.get('OPENALEX_MAILTO') or '').strip() or None,
        crossref_mailto=(env.get('CROSSREF_MAILTO') or '').strip() or None,
        contact_email=(env.get('SCHOLARLY_API_EMAIL') or '').strip() or None,
        connect_timeout=_float_env('METADATA_CONNECT_TIMEOUT', DEFAULT_CONNECT_TIMEOUT, env),
        read_timeout=_float_env('METADATA_READ_TIMEOUT', DEFAULT_READ_TIMEOUT, env),
        total_budget=_float_env('ARTICLE_DISCOVERY_TOTAL_BUDGET',
                                DEFAULT_TOTAL_BUDGET, env),
        searches_per_minute=_int_env('ARTICLE_DISCOVERY_SEARCHES_PER_MINUTE',
                                     DEFAULT_SEARCHES_PER_MINUTE, env),
        preview_ttl_seconds=_int_env('ARTICLE_DISCOVERY_PREVIEW_TTL_SECONDS',
                                     DEFAULT_PREVIEW_TTL_SECONDS, env),
    )
