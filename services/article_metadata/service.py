# -*- coding: utf-8 -*-
"""Orkestratsiya: `fetch_article_metadata(doi)` (Phase 2C.1).

Bu funksiya UI'ga BOG'LIQ EMAS — Flask, `request` yoki DB import qilinmaydi
(§43). Admin endpointi ham, keyingi CSV/DOI bulk importeri ham, agent ham
aynan shu funksiyani chaqiradi; farqi faqat kesh adapteri va konfiguratsiya.

Provayderlar PARALLEL chaqiriladi (stdlib `ThreadPoolExecutor` — yangi
framework yo'q). Sabab oddiy: ketma-ket chaqirilsa admin uch provayderning
timeoutini QO'SHIB kutardi; parallelda umumiy vaqt eng sekinining vaqtiga
teng bo'ladi. Bitta provayder yiqilsa qolganlari davom etadi (§4).
"""
from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone

from services.article_metadata import crossref, openalex, unpaywall
from services.article_metadata.config import MetadataConfig, config_from_env
from services.article_metadata.merger import merge
from services.article_metadata.models import (CROSSREF, LOOKUP_FAILED,
                                              LOOKUP_NOT_FOUND, LOOKUP_OK,
                                              LOOKUP_PARTIAL, NOT_CONFIGURED,
                                              NOT_FOUND, OK, OPENALEX, PROVIDERS,
                                              TIMEOUT, UNPAYWALL,
                                              MetadataLookupResult, ProviderResult,
                                              Warning_)
from utils.article_rules import ArticleValidationError, validate_doi

logger = logging.getLogger(__name__)

FETCHERS = {
    CROSSREF: crossref.fetch,
    OPENALEX: openalex.fetch,
    UNPAYWALL: unpaywall.fetch,
}

CACHE_PREFIX = 'article_metadata'
CACHE_VERSION = 'v1'


class MetadataLookupError(ValueError):
    """Foydalanuvchiga ko'rsatiladigan xato (texnik trace emas)."""

    def __init__(self, public_message, code='invalid_request'):
        self.public_message = public_message
        self.code = code
        super().__init__(public_message)


def cache_key(doi, provider):
    """DOI xom holda kalitga qo'yilmaydi — u uzun va nomaqbul belgilar
    saqlaydi. Hash barqaror va fayl tizimi keshiga mos."""
    digest = hashlib.sha256(doi.encode('utf-8')).hexdigest()[:32]
    return '%s:%s:%s:%s' % (CACHE_PREFIX, CACHE_VERSION, provider, digest)


def fetch_article_metadata(doi, *, config=None, cache_get=None, cache_set=None,
                           providers=PROVIDERS, fetchers=None):
    """Bitta DOI uchun normallashtirilgan metadata.

    Argumentlar:
        doi         — xom yoki normallashtirilgan DOI. Formati tekshiriladi.
        config      — `MetadataConfig`; berilmasa env'dan o'qiladi.
        cache_get   — `fn(key) -> ProviderResult | None` (ixtiyoriy).
        cache_set   — `fn(key, ProviderResult, ttl)` (ixtiyoriy).
        providers   — chaqiriladigan provayderlar (testlar uchun cheklash).
        fetchers    — `{provider: fn(doi, config)}` (testlarda o'rniga qo'yish).

    DB'ga HECH NARSA yozmaydi va hech qanday faylni yuklab olmaydi.
    """
    config = config or config_from_env()
    fetchers = fetchers or FETCHERS

    try:
        normalized = validate_doi(doi)
    except ArticleValidationError as exc:
        raise MetadataLookupError(exc.public_message, code='invalid_doi') from exc
    if not normalized:
        raise MetadataLookupError('DOI kiriting.', code='missing_doi')

    results, from_cache = _gather(normalized, config, providers, fetchers,
                                  cache_get, cache_set)
    metadata, conflicts, warnings = merge(results)
    metadata.doi = metadata.doi or normalized

    status = _overall_status(results)
    if status == LOOKUP_NOT_FOUND:
        warnings.append(Warning_(
            code='not_found',
            message='Bu DOI bo‘yicha manbalarda yozuv topilmadi. DOI to‘g‘ri '
                    'ekanini tekshiring.', severity='notice'))
    elif status == LOOKUP_PARTIAL:
        failed = [name for name, result in results.items() if not result.ok]
        warnings.append(Warning_(
            code='partial_success',
            message='Ba’zi manbalar javob bermadi: %s. Topilgan ma’lumot to‘liq '
                    'bo‘lmasligi mumkin.' % ', '.join(sorted(failed)),
            severity='notice'))
    elif status == LOOKUP_FAILED:
        warnings.append(Warning_(
            code='all_providers_failed',
            message='Tashqi manbalarning hech biri javob bermadi. Birozdan '
                    'keyin qayta urinib ko‘ring.', severity='notice'))

    return MetadataLookupResult(doi=normalized, status=status, metadata=metadata,
                                providers=results, conflicts=conflicts,
                                warnings=warnings, from_cache=from_cache)


def _gather(doi, config, providers, fetchers, cache_get, cache_set):
    """Keshdan o'qiydi, qolganini parallel chaqiradi."""
    results = {}
    from_cache = []
    pending = []

    for provider in providers:
        if provider not in fetchers:
            continue
        cached = _cache_read(cache_get, doi, provider)
        if cached is not None:
            results[provider] = cached
            from_cache.append(provider)
        else:
            pending.append(provider)

    if pending:
        with ThreadPoolExecutor(max_workers=len(pending),
                                thread_name_prefix='article-meta') as pool:
            futures = {pool.submit(_safe_fetch, fetchers[p], doi, config, p): p
                       for p in pending}
            for future, provider in futures.items():
                try:
                    results[provider] = future.result(timeout=config.total_budget)
                except FutureTimeout:
                    # Umumiy byudjet tugadi. Boshqa provayderlarning natijasi
                    # baribir ishlatiladi — bu qisman muvaffaqiyat.
                    future.cancel()
                    results[provider] = ProviderResult(
                        provider=provider, status=TIMEOUT,
                        error_code='umumiy vaqt chegarasi')

    for provider, result in results.items():
        if result.ok and provider not in from_cache:
            _cache_write(cache_set, doi, provider, result, config.cache_ttl)
    return results, from_cache


def _safe_fetch(fetcher, doi, config, provider):
    """Klientdagi kutilmagan istisno butun lookup'ni yiqitmasin."""
    try:
        return fetcher(doi, config)
    except Exception:                       # noqa: BLE001 — chegara qatlami
        logger.exception('article-metadata provider=%s kutilmagan xato', provider)
        from services.article_metadata.models import UNAVAILABLE
        return ProviderResult(provider=provider, status=UNAVAILABLE,
                              error_code='ichki xato')


def _cache_read(cache_get, doi, provider):
    if cache_get is None:
        return None
    try:
        cached = cache_get(cache_key(doi, provider))
    except Exception:                       # kesh nosozligi lookup'ni yiqitmaydi
        logger.warning('article-metadata kesh o‘qilmadi provider=%s', provider)
        return None
    if not isinstance(cached, ProviderResult) or not cached.ok:
        return None
    return cached


def _cache_write(cache_set, doi, provider, result, ttl):
    if cache_set is None:
        return
    try:
        cache_set(cache_key(doi, provider), result, ttl)
    except Exception:
        logger.warning('article-metadata kesh yozilmadi provider=%s', provider)


def _overall_status(results):
    """Umumiy holat. Sozlanmagan provayder muvaffaqiyatsizlik hisoblanmaydi."""
    considered = {name: r for name, r in results.items()
                  if r.status != NOT_CONFIGURED}
    if not considered:
        return LOOKUP_FAILED
    ok = [name for name, r in considered.items() if r.status == OK]
    if len(ok) == len(considered):
        return LOOKUP_OK
    if ok:
        return LOOKUP_PARTIAL
    if all(r.status == NOT_FOUND for r in considered.values()):
        return LOOKUP_NOT_FOUND
    return LOOKUP_FAILED


# ── Flask keshi uchun adapter ───────────────────────────────────────────────

def flask_cache_adapters(cache):
    """(cache_get, cache_set) — mavjud Flask-Caching namunasidan.

    Loyihada `FileSystemCache` ishlatiladi (app.py), ya'ni kesh gunicorn
    worker'lari orasida fayl tizimi orqali BO'LINADI. Shu sababli bu yerda
    jarayon ichidagi lug'at ishlatilmaydi — u multi-worker muhitda
    ishonchsiz bo'lardi (§21). Yangi Redis kabi infra ham qo'shilmaydi.
    """
    return cache.get, (lambda key, value, ttl: cache.set(key, value, timeout=ttl))


def describe_configuration(config=None):
    """Sozlamalar HOLATI (qiymatlari emas) — admin UI uchun.

    API kaliti va e-mail bu yerdan hech qachon chiqmaydi (§23, §33).
    """
    config = config or config_from_env()
    return {
        CROSSREF: bool(config.crossref_contact),
        OPENALEX: bool(config.openalex_api_key or config.openalex_contact),
        UNPAYWALL: bool(config.unpaywall_contact),
    }
