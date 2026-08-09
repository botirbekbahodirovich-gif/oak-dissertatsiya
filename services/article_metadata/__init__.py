# -*- coding: utf-8 -*-
"""Ilmiy maqola metadata provayderlari (Phase 2C.1).

Ommaviy API — bitta funksiya:

    from services.article_metadata import fetch_article_metadata
    result = fetch_article_metadata('10.1234/abcd')

`result.metadata` — NOMZOD ma'lumot, DB qatori emas. Uni saqlash qarorini
faqat odam qabul qiladi (admin formani yuborgach); service hech qachon
`scientific_articles` ga yozmaydi va hech qanday faylni yuklab olmaydi.

To'liq hujjat: docs/article-metadata-integration.md
"""
from services.article_metadata.config import MetadataConfig, config_from_env
from services.article_metadata.models import (CROSSREF, LOOKUP_FAILED,
                                              LOOKUP_NOT_FOUND, LOOKUP_OK,
                                              LOOKUP_PARTIAL, OPENALEX, PROVIDERS,
                                              UNPAYWALL, MetadataLookupResult,
                                              NormalizedArticleMetadata,
                                              ProviderResult)
from services.article_metadata.service import (MetadataLookupError,
                                               describe_configuration,
                                               fetch_article_metadata,
                                               flask_cache_adapters)

__all__ = [
    'fetch_article_metadata',
    'describe_configuration',
    'flask_cache_adapters',
    'MetadataLookupError',
    'MetadataConfig',
    'config_from_env',
    'MetadataLookupResult',
    'NormalizedArticleMetadata',
    'ProviderResult',
    'PROVIDERS', 'CROSSREF', 'OPENALEX', 'UNPAYWALL',
    'LOOKUP_OK', 'LOOKUP_PARTIAL', 'LOOKUP_NOT_FOUND', 'LOOKUP_FAILED',
]
