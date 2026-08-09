# -*- coding: utf-8 -*-
"""Discovery — Flask'dan mustaqil orkestratsiya (Phase 2D.1).

`services/article_import/service.py` bilan bir xil naqsh: barcha funksiyalar
ochiq psycopg2 kursurini oladi, tranzaksiya chegarasi CHAQIRUVCHIDA
(`blueprints/article_discovery.py`). Bu modul HAM Flask'ga bog'liq emas.

Oqim (§1, §22-§24):
  1. `validate_query()` — server-side, klient validatsiyasiga ISHONILMAYDI.
  2. `run_discovery_search()` — OpenAlex qidiradi, SAME-RESPONSE + DB + FAOL
     NAVBAT dublikatlarini klassifikatsiya qiladi, natijani serverda
     (Flask cache) saqlaydi va TOKEN qaytaradi.
  3. `load_preview()` / `consume_preview()` — enqueue vaqtida klient FAQAT
     tokenni yuboradi, DOI ro'yxatini EMAS (§23).
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone

from services.article_discovery import openalex
from services.article_discovery.config import config_from_env
from services.article_discovery.models import (ALREADY_QUEUED, DUPLICATE_IN_RESULT,
                                                EXISTING_ARTICLE, NEW,
                                                ArticleDiscoveryQuery,
                                                DiscoveryPreviewResult,
                                                DiscoveryValidationError)
from services.article_import import deduplicator
from services.article_metadata.models import NOT_CONFIGURED, OK

_TOKEN_PREFIX = 'article_discovery:preview:'
_RATE_PREFIX = 'article_discovery:rate:'


# ── Validatsiya (§8) ─────────────────────────────────────────────────────────

def validate_query(raw, config=None) -> ArticleDiscoveryQuery:
    """`raw` — request.form/JSON kabi oddiy `dict` (string qiymatlar).

    Bu funksiya klient validatsiyasidan MUSTAQIL — barcha tekshiruv serverda
    (§8 — "Client validationga ishonma").
    """
    config = config or config_from_env()

    keyword = (raw.get('keyword') or '').strip()
    if not (config.keyword_min_length <= len(keyword) <= config.keyword_max_length):
        raise DiscoveryValidationError(
            'invalid_keyword',
            "Kalit so'z %d–%d belgi orasida bo'lishi kerak." % (
                config.keyword_min_length, config.keyword_max_length))

    today = date.today()
    from_date = _parse_date(raw.get('from_date'))
    to_date = _parse_date(raw.get('to_date'))
    if from_date is None and to_date is None:
        to_date = today
        from_date = today - timedelta(days=config.default_window_days)
    elif from_date is None or to_date is None:
        raise DiscoveryValidationError(
            'invalid_date_range',
            "Boshlanish va tugash sanasi ikkalasi ham ko'rsatilishi kerak.")

    if from_date > to_date:
        raise DiscoveryValidationError(
            'reversed_date_range',
            "Boshlanish sanasi tugash sanasidan keyin bo'la olmaydi.")
    if to_date > today:
        to_date = today
    if (to_date - from_date).days > config.max_window_days:
        raise DiscoveryValidationError(
            'window_too_wide',
            "Sana oralig'i %d kundan (taxminan 12 oy) oshmasligi kerak."
            % config.max_window_days)

    limit = _parse_int(raw.get('limit'), config.default_result_limit)
    if limit < 1:
        limit = 1
    if limit > config.hard_result_limit:
        limit = config.hard_result_limit

    oak_field_id = _parse_int(raw.get('oak_field_id'), None)

    return ArticleDiscoveryQuery(keyword=keyword, from_date=from_date,
                                 to_date=to_date, oak_field_id=oak_field_id,
                                 limit=limit)


def _parse_date(raw):
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        raise DiscoveryValidationError('invalid_date', "Sana formati noto'g'ri.")


def _parse_int(raw, default):
    if raw in (None, ''):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


# ── Qidiruv + klassifikatsiya (§13) ──────────────────────────────────────────

def run_discovery_search(cur, query, *, config=None, search=openalex.search):
    """OpenAlex qidiradi va uchta bosqichli klassifikatsiyani (§13) qo'llaydi.

    Metadata FETCH QILINMAYDI bu yerda (§9) — faqat preview maydonlari.
    """
    config = config or config_from_env()
    result = search(query, config)

    if result.status == NOT_CONFIGURED:
        return DiscoveryPreviewResult(status=NOT_CONFIGURED, query=query)
    if result.status != OK:
        return DiscoveryPreviewResult(status=result.status, query=query,
                                      error_code=result.error_code)

    candidates = list(result.candidates)

    # 1) Same-response dedup — ikkinchi marta uchragan DOI (§13-1).
    seen = set()
    for candidate in candidates:
        if candidate.doi in seen:
            candidate.classification = DUPLICATE_IN_RESULT
        else:
            seen.add(candidate.doi)

    unique_dois = sorted(seen)

    # 2) Mavjud `scientific_articles` (§13-2) — bitta batch SQL.
    existing_map = deduplicator.find_existing_db_duplicates(cur, unique_dois)
    # 3) Faol import navbati (§13-3, §14) — bitta batch SQL.
    active_map = deduplicator.find_active_import_dois(cur, unique_dois)

    for candidate in candidates:
        if candidate.classification == DUPLICATE_IN_RESULT:
            continue
        existing = existing_map.get(candidate.doi)
        if existing:
            candidate.classification = EXISTING_ARTICLE
            candidate.existing_article_id = existing['id']
            candidate.existing_article_slug = existing['slug']
            continue
        if candidate.doi in active_map:
            candidate.classification = ALREADY_QUEUED
            continue
        candidate.classification = NEW

    return DiscoveryPreviewResult(
        status=OK, query=query, candidates=candidates,
        total_count=result.total_count, inspected_count=len(result.candidates),
        excluded_no_doi_count=result.excluded_no_doi_count)


# ── Preview token (§22-§24) ──────────────────────────────────────────────────

def _token_key(token):
    return _TOKEN_PREFIX + token


def store_preview(cache_set, admin_id, preview, config=None):
    """Natijani serverda saqlaydi va actor-bound tokenni qaytaradi.

    Cache entry: `admin_id`, canonical so'rov, nomzod ro'yxati (§22).
    """
    config = config or config_from_env()
    token = secrets.token_urlsafe(32)
    entry = {
        'admin_id': admin_id,
        'query': preview.query.canonical(),
        'candidates': [c.to_dict() for c in preview.candidates],
        'total_count': preview.total_count,
        'inspected_count': preview.inspected_count,
        'excluded_no_doi_count': preview.excluded_no_doi_count,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'consumed': False,
        'batch_id': None,
    }
    cache_set(_token_key(token), entry, config.preview_ttl_seconds)
    return token


def load_preview(cache_get, token, admin_id):
    """Tokenni o'qiydi — YO'Q/muddati o'tgan/BOSHQA adminga tegishli bo'lsa
    `None` (§23 — tampered token'ga xavfsiz javob, 500 emas)."""
    if not token:
        return None
    entry = cache_get(_token_key(token))
    if not isinstance(entry, dict):
        return None
    if entry.get('admin_id') != admin_id:
        return None
    return entry


def mark_preview_consumed(cache_set, token, batch_id, config=None):
    """Enqueue muvaffaqiyatli bo'lgandan keyin — takroriy POST (double-click,
    §24) endi YANGI batch yaratmaydi, xuddi shu batch'ga qaytadi."""
    config = config or config_from_env()
    entry = {'consumed': True, 'batch_id': batch_id}
    cache_set(_token_key(token) + ':consumed', entry, config.preview_consumed_ttl_seconds)


def load_consumed(cache_get, token):
    return cache_get(_token_key(token) + ':consumed')


# ── Rate limit (§32) ──────────────────────────────────────────────────────────

def check_rate_limit(cache_get, cache_set, admin_id, config=None):
    """Oddiy sliding-minute counter — Flask-Caching FileSystemCache orqali
    (gunicorn worker'lari orasida bo'linadi). `True` — ruxsat, `False` —
    429 chiqarilishi kerak."""
    config = config or config_from_env()
    bucket = datetime.now(timezone.utc).strftime('%Y%m%d%H%M')
    key = '%s%s:%s' % (_RATE_PREFIX, admin_id, bucket)
    current = cache_get(key) or 0
    if current >= config.searches_per_minute:
        return False
    cache_set(key, current + 1, 70)
    return True
