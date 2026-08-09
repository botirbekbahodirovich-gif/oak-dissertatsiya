# -*- coding: utf-8 -*-
"""Crossref `/works` RO'YXAT/QIDIRUV klienti — discovery uchun (Phase 2D.2).

`services/article_metadata/crossref.py`dan MUSTAQIL: o'sha modul BITTA DOI'ni
`/works/{doi}` orqali BOYITADI (authoritative enrichment, import processor
bosqichida). Bu yerdagi vazifa boshqa: `/works?query.bibliographic=...` orqali
KO'PLAB nomzodni TOPISH — natija HECH QACHON to'g'ridan-to'g'ri
`scientific_articles`ga yozilmaydi (`services/article_discovery/openalex.py`
bilan bir xil ajratish, §9/§29 o'sha hujjatdagi kabi).

`openalex.py` bilan AYNAN bir xil kontrakt: `search(query, config, *,
sleep=None) -> DiscoverySearchResult`. Bu — provider-neutral interfeys (§3):
alohida abstrakt klass yo'q, ikkalasi ham shu funksiya imzosiga mos keladi.

RASMIY HUJJAT (2026-08 tekshirilgan, docs/article-discovery.md §Crossref
bo'limiga qarang):
  * Manba — github.com/CrossRef/rest-api-doc (api_tips.md) va
    crossref.org/documentation/retrieve-metadata/rest-api/{,rest-api-filters}/
    (eski rest_api.md GitHub'da DEPRECATED deb belgilangan — joriy hujjat
    shu ikki manba).
  * Qidiruv uchun umumiy `query` EMAS, `query.bibliographic` tavsiya
    etiladi — sabab: "Most additional parameters, filters and facets will
    make the query slower *and* less accurate" va oddiy `query` butun
    yozuvni (jumladan bibliografik bo'lmagan maydonlarni) qidiradi, ko'proq
    yolg'on-musbat beradi (api_tips.md).
  * Sana filtrlari: `from-pub-date` / `until-pub-date` (chiziqcha bilan,
    OpenAlex'ning `from_publication_date`dan farqli).
  * `rows` uchun hujjatda qattiq raqamli cheklov YO'Q, lekin loyiha siyosati
    (Phase 2D.1 bilan bir xil, ticket §4) 100 dan oshirmaydi — bu HAR QANDAY
    ma'lum Crossref chegarasidan ancha past.
  * `offset`/chuqur sahifalash ATAYIN ishlatilmaydi — api_tips.md buni
    "expensive and slow" deb ochiq tavsiya qilmaydi ("use cursors instead").
    Bu yerda faqat BITTA chegaralangan sahifa (`rows<=100`, offset yo'q/0)
    so'raladi — chuqur sahifalash emas, xuddi OpenAlex'dagi
    `per_page<=100&page=1` bilan bir xil siyosat.
  * `mailto` — polite pool, query parametr sifatida.
  * API kaliti SHART EMAS (rasmiy: "No sign-up is required to use the REST
    API") — shuning uchun Crossref hech qachon `not_configured` qaytarmaydi
    (mavjud `services/article_metadata/crossref.py` bilan bir xil siyosat).
  * Javob konverti: `{"status":"ok","message-type":"work-list",
    "message":{"total-results":N,"items":[...]}}` — har bir `items[i]` bitta
    DOI'ning `/works/{doi}` javobidagi `message`si bilan bir xil sxemaga ega.
  * Chaqirib olish (retraction) belgisi — `update-to[].type == "retraction"`
    (rasmiy: Retraction Watch integratsiyasi, `source` maydoni
    `publisher`/`retraction-watch`).
"""
from __future__ import annotations

from services.article_discovery.models import DiscoveryCandidate, DiscoverySearchResult
from services.article_metadata.http import fetch_json
from services.article_metadata.models import CROSSREF, INVALID_RESPONSE, OK
from services.article_metadata.normalizer import clean_text, date_from_parts
from utils.article_rules import ArticleValidationError, validate_doi

# Crossref "type" taksonomiyasi ko'p (journal-article, proceedings-article,
# posted-content, book-chapter, ...). OpenAlex'ning `type:article` filtri
# bilan MOSLASHTIRISH uchun eng yaqin ekvivalenti — jurnal maqolalari.
# CHEGARA (§5, hujjatlashtirilgan): Crossref'da OpenAlex'ning "article" bilan
# 1:1 mos keladigan yagona qiymati yo'q — proceedings/preprint kabi turlar
# shu bilan ATAYIN chiqarib tashlanadi (kelajakda kengaytirish mumkin).
WORK_TYPE_FILTER = 'journal-article'


def search(query, config, *, sleep=None):
    """`ArticleDiscoveryQuery` -> `DiscoverySearchResult`. Istisno tashlamaydi.

    OpenAlex'dan farqli, Crossref hech qachon `not_configured` qaytarmaydi —
    mailto ixtiyoriy (rasmiy hujjat, sinf docstring'iga qarang).
    """
    rows = max(1, min(query.limit, config.hard_result_limit))
    email = config.crossref_contact
    params = {
        'query.bibliographic': query.keyword,
        'filter': ','.join([
            'type:%s' % WORK_TYPE_FILTER,
            'from-pub-date:%s' % query.from_date.isoformat(),
            'until-pub-date:%s' % query.to_date.isoformat(),
        ]),
        'rows': str(rows),
    }
    if email:
        params['mailto'] = email

    kwargs = {'sleep': sleep} if sleep is not None else {}
    outcome = fetch_json(config.crossref_base, provider=CROSSREF, config=config,
                         params=params,
                         headers={'User-Agent': config.user_agent(email)}, **kwargs)

    if not outcome.ok:
        return DiscoverySearchResult(status=outcome.status,
                                     http_status=outcome.http_status,
                                     error_code=outcome.detail)

    payload = outcome.payload or {}
    message = payload.get('message')
    if not isinstance(message, dict):
        return DiscoverySearchResult(status=INVALID_RESPONSE,
                                     http_status=outcome.http_status,
                                     error_code="'message' maydoni yo'q")
    items = message.get('items')
    if not isinstance(items, list):
        return DiscoverySearchResult(status=INVALID_RESPONSE,
                                     http_status=outcome.http_status,
                                     error_code="'message.items' ro'yxat emas")

    total_count = _int_or_zero(message.get('total-results'))

    candidates, excluded_no_doi = [], 0
    for work in items:
        if not isinstance(work, dict):
            continue
        candidate = _extract_candidate(work)
        if candidate is None:
            excluded_no_doi += 1
            continue
        candidates.append(candidate)

    return DiscoverySearchResult(status=OK, candidates=candidates,
                                 total_count=total_count,
                                 excluded_no_doi_count=excluded_no_doi,
                                 http_status=outcome.http_status)


def _extract_candidate(work):
    """DOI'siz YOKI formati NOTO'G'RI Crossref work -> `None` (§6 — DOI-only
    siyosat, statistikada sanaladi lekin candidate YARATILMAYDI).

    `validate_doi` (`utils.article_rules`) ishlatiladi — oddiy `normalize_doi`
    emas — chunki Crossref natijalari OpenAlex'dan farqli, kurator
    tekshiruvidan o'tmagan xom deposit ma'lumoti bo'lishi mumkin; import
    bulk'dagi bir xil qattiqroq tekshiruv (`services/article_import/
    validator.py`) shu yerda ham qo'llaniladi — ikkinchi regex/qoida
    o'ylab topilmaydi.
    """
    try:
        doi = validate_doi(work.get('DOI'))
    except ArticleValidationError:
        return None
    if not doi:
        return None

    title = _first_string(work.get('title'), 500)
    journal = _first_string(work.get('container-title'), 300)

    published = (work.get('published')
                or work.get('published-print')
                or work.get('published-online')
                or work.get('issued'))
    publication_date = date_from_parts(published)

    is_retracted = any(
        isinstance(entry, dict) and entry.get('type') == 'retraction'
        for entry in (work.get('update-to') or []))

    return DiscoveryCandidate(
        doi=doi,
        # Crossref'da OpenAlex'ning alohida "work ID"siga teng identifikator
        # yo'q — DOI'ning o'zi identifikator (§8: takrorlashdan qochish
        # uchun `external_source_id` bu yerda ATAYIN bo'sh qoldiriladi,
        # DOI maydoni allaqachon bor).
        external_source_id=None,
        title=title,
        publication_date=publication_date,
        source_name=journal,
        is_retracted=is_retracted,
        topics=[],
        providers=[CROSSREF],
    )


def _first_string(value, max_length=500):
    if isinstance(value, list):
        value = value[0] if value else None
    return clean_text(value, max_length)


def _int_or_zero(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
