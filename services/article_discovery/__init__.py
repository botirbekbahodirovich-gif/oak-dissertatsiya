# -*- coding: utf-8 -*-
"""Manual OpenAlex Article Discovery (Phase 2D.1).

Deterministic scholarly discovery pipeline: admin keyword/date so'rov beradi,
OpenAlex'dan DOI-only nomzod ro'yxati topiladi, mavjud dublikat/navbat bilan
klassifikatsiya qilinadi va FAQAT yangi nomzodlar mavjud
`services.article_import` navbatiga qo'shiladi.

Discovery ≠ article creation. Discovery ≠ publication. Discovery ≠ metadata
authority (metadata FAQAT `services.article_metadata.fetch_article_metadata`
orqali keladi, import processor bosqichida). Discovery ≠ AI/agent.

Bu modul Flask'ga BOG'LIQ EMAS — barcha funksiyalar ochiq psycopg2 kursurini
oladi, xuddi `services.article_import` bilan bir xil (docs/article-discovery.md).
"""
from __future__ import annotations

from services.article_discovery.config import (ArticleDiscoveryConfig,
                                                config_from_env)
from services.article_discovery.models import (ALREADY_QUEUED,
                                                DUPLICATE_IN_RESULT,
                                                EXISTING_ARTICLE, INVALID_DOI,
                                                NEW, NO_DOI,
                                                ArticleDiscoveryQuery,
                                                DiscoveryCandidate,
                                                DiscoveryPreviewResult,
                                                DiscoveryValidationError)

__all__ = [
    'ArticleDiscoveryConfig', 'config_from_env',
    'ArticleDiscoveryQuery', 'DiscoveryCandidate', 'DiscoveryPreviewResult',
    'DiscoveryValidationError',
    'NEW', 'EXISTING_ARTICLE', 'ALREADY_QUEUED', 'DUPLICATE_IN_RESULT',
    'INVALID_DOI', 'NO_DOI',
]
