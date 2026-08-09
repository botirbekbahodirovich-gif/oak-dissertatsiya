# -*- coding: utf-8 -*-
"""Discovery — Flask'dan mustaqil orkestratsiya (Phase 2D.1/2D.2).

`services/article_import/service.py` bilan bir xil naqsh: barcha funksiyalar
ochiq psycopg2 kursurini oladi, tranzaksiya chegarasi CHAQIRUVCHIDA
(`blueprints/article_discovery.py`). Bu modul HAM Flask'ga bog'liq emas.

Oqim (§1, §22-§24):
  1. `validate_query()` — server-side, klient validatsiyasiga ISHONILMAYDI.
  2. `run_multi_provider_search()` (Phase 2D.2) — tanlangan provayder(lar)ni
     PARALLEL so'raydi, natijalarni birlashtiradi, SAME-DOI (bir javob
     ichida VA provayderlar orasida) + DB + FAOL NAVBAT dublikatlarini
     klassifikatsiya qiladi, natijani serverda (Flask cache) saqlaydi va
     TOKEN qaytaradi.
     `run_discovery_search()` (Phase 2D.1) — BITTA provayderli qurilish
     bloki, o'zgarmagan (mavjud to'g'ridan-to'g'ri chaqiruvchilar/testlar
     uchun); `run_multi_provider_search()` bitta provayder tanlanganda ANIQ
     shuni chaqiradi (pastga qarang — patch-friendly delegatsiya).
  3. `load_preview()` / `consume_preview()` — enqueue vaqtida klient FAQAT
     tokenni yuboradi, DOI ro'yxatini EMAS (§23).
"""
from __future__ import annotations

import secrets
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import date, datetime, timedelta, timezone

from services.article_discovery import crossref, openalex
from services.article_discovery.config import config_from_env
from services.article_discovery.models import (ALREADY_QUEUED, DUPLICATE_IN_RESULT,
                                                EXISTING_ARTICLE, MULTI_PROVIDER, NEW,
                                                SUPPORTED_PROVIDERS,
                                                ArticleDiscoveryQuery,
                                                DiscoveryPreviewResult,
                                                DiscoverySearchResult,
                                                DiscoveryValidationError)
from services.article_import import deduplicator
from services.article_metadata.models import (CROSSREF, NOT_CONFIGURED, OK,
                                              OPENALEX, UNAVAILABLE)

_TOKEN_PREFIX = 'article_discovery:preview:'
_RATE_PREFIX = 'article_discovery:rate:'

# Provider-neutral reestr (§3) — HAR IKKALASI ham `search(query, config, *,
# sleep=None) -> DiscoverySearchResult` imzosiga mos keladi. Yangi provayder
# qo'shish shu dict + `SUPPORTED_PROVIDERS`ga bitta qator, boshqa hech narsa
# (over-engineering yo'q — plagin reestri, ABC, va h.k. shart emas).
DEFAULT_SEARCHERS = {OPENALEX: openalex.search, CROSSREF: crossref.search}


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
    providers = _parse_providers(raw)

    return ArticleDiscoveryQuery(keyword=keyword, from_date=from_date,
                                 to_date=to_date, oak_field_id=oak_field_id,
                                 limit=limit, providers=providers)


def _parse_providers(raw):
    """`raw['providers']` — `request.form.getlist('providers')` kabi ro'yxat
    (checkbox'lar) YOKI vergul bilan ajratilgan bitta satr (JSON/API
    chaqiruvchilar uchun) bo'lishi mumkin.

    MAYDON UMUMAN BERILMAGAN bo'lsa (masalan eski to'g'ridan-to'g'ri
    chaqiruv yoki test) — Phase 2D.1 xulq-atvori saqlanadi: FAQAT OpenAlex
    (§Backward compat, `ArticleDiscoveryQuery.providers` default'i bilan bir
    xil). Admin panelidagi HAQIQIY forma checkbox'lari IKKALASI HAM
    tanlangan holda ko'rsatiladi (§12 — UI default'i "Both"), shuning uchun
    haqiqiy admin so'rovi bu yerga har doim ro'yxat bilan keladi.
    """
    value = raw.get('providers')
    if value in (None, ''):
        return (OPENALEX,)
    if isinstance(value, str):
        candidates = [value]
    else:
        candidates = list(value)
    seen = []
    for item in candidates:
        name = (item or '').strip().lower()
        if name in SUPPORTED_PROVIDERS and name not in seen:
            seen.append(name)
    if not seen:
        raise DiscoveryValidationError(
            'invalid_providers',
            "Kamida bitta manba (OpenAlex yoki Crossref) tanlanishi kerak.")
    # Deterministik tartib — §7: dedup/precedence OpenAlex-birinchi.
    return tuple(p for p in SUPPORTED_PROVIDERS if p in seen)


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

def _classify_candidates(cur, candidates):
    """Uchta bosqichli klassifikatsiya (§13), provayderdan MUSTAQIL — bitta
    provayderli va ko'p-provayderli yo'l shu FUNKSIYANING O'ZINI qayta
    ishlatadi (mantiq ikki marta yozilmaydi).

    Kirish `candidates` allaqachon "bitta ro'yxat" (bitta provayder natijasi
    YOKI ko'p-provayderli birlashtirilgan/DOI-merge qilingan ro'yxat,
    §7-§8 pastdagi `_merge_multi_provider`ga qarang) — bu funksiya faqat
    SAME-LIST dublikatlarni (`DUPLICATE_IN_RESULT`) va DB/navbat
    dublikatlarini belgilaydi.
    """
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
    return candidates


def run_discovery_search(cur, query, *, config=None, search=openalex.search):
    """BITTA provayderli qidiruv (Phase 2D.1, o'zgarmagan imzo/xulq-atvor).

    Metadata FETCH QILINMAYDI bu yerda (§9) — faqat preview maydonlari.
    Mavjud to'g'ridan-to'g'ri chaqiruvchilar (testlar, kelajakdagi skriptlar)
    uchun saqlanadi; admin qidiruv route'i endi (Phase 2D.2)
    `run_multi_provider_search()`ni chaqiradi — u BITTA provayder
    tanlanganda ANIQ shu funksiyaga delegatsiya qiladi.
    """
    config = config or config_from_env()
    result = search(query, config)

    if result.status == NOT_CONFIGURED:
        return DiscoveryPreviewResult(status=NOT_CONFIGURED, query=query)
    if result.status != OK:
        return DiscoveryPreviewResult(status=result.status, query=query,
                                      error_code=result.error_code)

    candidates = _classify_candidates(cur, list(result.candidates))
    return DiscoveryPreviewResult(
        status=OK, query=query, candidates=candidates,
        total_count=result.total_count, inspected_count=len(result.candidates),
        excluded_no_doi_count=result.excluded_no_doi_count)


# ── Ko'p-provayderli qidiruv (Phase 2D.2, §3, §7, §13) ───────────────────────

def _merge_multi_provider(provider_candidates):
    """`{provider: [DiscoveryCandidate, ...]}` -> BITTA ro'yxat, DETERMINISTIK
    tartibda (§7): `SUPPORTED_PROVIDERS` tartibida (OpenAlex birinchi),
    concurrent HTTP tugash tartibidan MUSTAQIL.

    Bir xil DOI ikkala provayderda ham topilsa (§7-§8): FAQAT birinchi
    uchrashuv "canonical" hisoblanadi (keyinchalik `_classify_candidates`
    uni NEW/EXISTING/... qiladi), ikkinchisi `DUPLICATE_IN_RESULT`ga
    o'tkaziladi — xuddi bitta-provayderli same-response dublikat kabi (UI
    o'zgarishsiz). Farqi shu: ikkinchi uchrashuvning `providers` ro'yxati
    BIRINCHISIGA qo'shiladi (provenance yo'qolmaydi) va bo'sh
    ko'rsatish-maydonlari (title/source_name/publication_date/
    external_source_id/topics) BIRINCHISIDA yo'q bo'lsa, ikkinchisidan
    to'ldiriladi — "deterministik ustunlik" (§8), to'liq merger moduli EMAS.
    """
    ordered = []
    for provider in SUPPORTED_PROVIDERS:
        ordered.extend(provider_candidates.get(provider) or [])

    merged, by_doi = [], {}
    for candidate in ordered:
        primary = by_doi.get(candidate.doi)
        if primary is None:
            by_doi[candidate.doi] = candidate
            merged.append(candidate)
            continue
        for provider in candidate.providers:
            if provider not in primary.providers:
                primary.providers.append(provider)
        if candidate.is_retracted:
            primary.is_retracted = True
        for attr in ('title', 'source_name', 'publication_date', 'external_source_id'):
            if not getattr(primary, attr) and getattr(candidate, attr):
                setattr(primary, attr, getattr(candidate, attr))
        if not primary.topics and candidate.topics:
            primary.topics = candidate.topics
        merged.append(candidate)
    return merged


def _run_one_provider(provider, searcher, query, config):
    """Klientdagi kutilmagan istisno butun ko'p-provayderli qidiruvni
    yiqitmasin (`services/article_metadata/service.py:_safe_fetch` bilan bir
    xil naqsh)."""
    try:
        return searcher(query, config)
    except Exception:                       # noqa: BLE001 — chegara qatlami
        return DiscoverySearchResult(status=UNAVAILABLE, error_code='ichki xato')


def run_multi_provider_search(cur, query, *, config=None, searchers=None):
    """Tanlangan provayder(lar)ni (`query.providers`) so'raydi va bitta
    deterministik, dedup qilingan `DiscoveryPreviewResult` qaytaradi (§3,
    §13, §19).

    Bitta provayder tanlangan bo'lsa — `run_discovery_search()`ga ANIQ
    delegatsiya qiladi (modul darajasidagi ISM orqali chaqiradi, shuning
    uchun `mock.patch('services.article_discovery.service.
    run_discovery_search', ...)` bu yo'lda ham ishlaydi — Phase 2D.1 route
    testlari o'zgarishsiz qoladi).

    Ikki (yoki undan ko'p) provayder — PARALLEL chaqiriladi
    (`ThreadPoolExecutor`, `services/article_metadata/service.py` bilan bir
    xil naqsh), umumiy vaqt byudjeti bilan (§13). Bitta provayder
    muvaffaqiyatsiz bo'lsa, ikkinchisining natijasi YO'QOLMAYDI — faqat
    `provider_status`da aks etadi. Hammasi muvaffaqiyatsiz bo'lsa —
    `status != OK`, admin uchun aniq xato (§13).
    """
    config = config or config_from_env()
    searchers = searchers or DEFAULT_SEARCHERS
    providers = tuple(query.providers) or (OPENALEX,)

    if len(providers) == 1:
        return run_discovery_search(cur, query, config=config,
                                    search=searchers.get(providers[0], openalex.search))

    results = {}
    with ThreadPoolExecutor(max_workers=len(providers),
                            thread_name_prefix='article-discovery') as pool:
        futures = {pool.submit(_run_one_provider, p, searchers[p], query, config): p
                  for p in providers if p in searchers}
        for future, provider in futures.items():
            try:
                results[provider] = future.result(timeout=config.total_budget)
            except FutureTimeout:
                future.cancel()
                results[provider] = DiscoverySearchResult(
                    status='timeout', error_code='umumiy vaqt chegarasi')

    provider_status = {p: {'status': r.status, 'count': len(r.candidates)}
                       for p, r in results.items()}

    ok_results = {p: r for p, r in results.items() if r.status == OK}
    if not ok_results:
        # Hammasi muvaffaqiyatsiz — bitta vakillik status qaytariladi (§13).
        # `not_configured` faqat HAMMASI shu bo'lsa (OpenAlex kalitsiz);
        # aks holda birinchi haqiqiy xato ko'rsatiladi.
        statuses = {r.status for r in results.values()}
        overall = NOT_CONFIGURED if statuses == {NOT_CONFIGURED} else next(
            (s for s in statuses if s != NOT_CONFIGURED), 'unavailable')
        return DiscoveryPreviewResult(status=overall, query=query,
                                      provider_status=provider_status)

    provider_candidates = {p: list(r.candidates) for p, r in ok_results.items()}
    merged = _merge_multi_provider(provider_candidates)
    candidates = _classify_candidates(cur, merged)

    total_count = sum(r.total_count for r in ok_results.values())
    inspected_count = sum(len(r.candidates) for r in ok_results.values())
    excluded_no_doi_count = sum(r.excluded_no_doi_count for r in ok_results.values())

    return DiscoveryPreviewResult(
        status=OK, query=query, candidates=candidates,
        total_count=total_count, inspected_count=inspected_count,
        excluded_no_doi_count=excluded_no_doi_count,
        provider_status=provider_status)


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
