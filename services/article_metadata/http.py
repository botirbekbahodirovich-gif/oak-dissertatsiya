# -*- coding: utf-8 -*-
"""Provayderlar uchun umumiy HTTP qatlami (Phase 2C.1).

Loyihada allaqachon `requests` ishlatiladi (scraper.py, blueprints/maqolalar.py,
payments.py) — yangi async framework qo'shilmaydi. Bu yerdagi yagona vazifa:
har bir tashqi chaqiruvni bir xil qoidalar bilan o'rash va natijani
`ProviderResult` holatiga aylantirish, shunda klientlar `try/except` ni qayta
yozmaydi va bitta provayder yiqilsa qolganlari ishlashda davom etadi (§4).
"""
from __future__ import annotations

import logging
import random
import time

import requests

from services.article_metadata.models import (INVALID_RESPONSE, NOT_FOUND, OK,
                                              RATE_LIMITED, TIMEOUT, UNAVAILABLE)

logger = logging.getLogger(__name__)

# Backoff'ga tasodifiy qo'shimcha: bir necha worker bir vaqtda 429 olsa,
# ular bir xil soniyada qayta urinmasin.
_JITTER = 0.25

# `Retry-After` juda uzun bo'lsa kutilmaydi — admin tugma bosib turibdi.
MAX_HONOURED_RETRY_AFTER = 5.0

_session = None


def get_session():
    """Jarayon bo'yicha bitta `requests.Session` — ulanish qayta ishlatiladi."""
    global _session
    if _session is None:
        _session = requests.Session()
    return _session


class HttpOutcome:
    """`fetch_json()` natijasi: (status, payload, tafsilotlar)."""

    __slots__ = ('status', 'payload', 'http_status', 'retry_after',
                 'attempts', 'elapsed_ms', 'detail')

    def __init__(self, status, payload=None, http_status=None, retry_after=None,
                 attempts=1, elapsed_ms=0, detail=None):
        self.status = status
        self.payload = payload
        self.http_status = http_status
        self.retry_after = retry_after
        self.attempts = attempts
        self.elapsed_ms = elapsed_ms
        self.detail = detail

    @property
    def ok(self):
        return self.status == OK


def _parse_retry_after(response):
    raw = (response.headers.get('Retry-After') or '').strip()
    if not raw:
        return None
    try:
        return max(0, int(float(raw)))
    except ValueError:
        # HTTP-date formati ham bo'lishi mumkin — uni kutmaymiz, shunchaki
        # "chegara bor" degan faktni saqlaymiz.
        return None


def fetch_json(url, *, provider, config, params=None, headers=None,
               sleep=time.sleep):
    """JSON oladi; hech qachon istisno tashlamaydi, doim `HttpOutcome` beradi.

    Qayta urinish FAQAT vaqtinchalik xatolarda: 429, 5xx, ulanish/o'qish
    timeouti. 404 — yakuniy javob, qayta urinilmaydi. Urinishlar soni
    `config.max_attempts` bilan qattiq cheklangan (§8: cheksiz retry yo'q).
    """
    session = get_session()
    request_headers = {'Accept': 'application/json'}
    request_headers.update(headers or {})

    started = time.monotonic()
    attempt = 0
    last = HttpOutcome(UNAVAILABLE, detail='urinish bo‘lmadi')

    while attempt < max(1, config.max_attempts):
        attempt += 1
        try:
            response = session.get(url, params=params, headers=request_headers,
                                   timeout=config.timeout)
        except requests.exceptions.Timeout:
            last = HttpOutcome(TIMEOUT, attempts=attempt, detail='timeout')
        except requests.exceptions.RequestException as exc:
            # Tarmoq/DNS/TLS — istisno turini log qilamiz, matnini emas:
            # unda so'rov URL'i (va u bilan birga e-mail) bo'lishi mumkin.
            last = HttpOutcome(UNAVAILABLE, attempts=attempt,
                               detail=type(exc).__name__)
        else:
            outcome = _interpret(response, attempt)
            if outcome.status != UNAVAILABLE and outcome.status != RATE_LIMITED:
                outcome.elapsed_ms = int((time.monotonic() - started) * 1000)
                _log(provider, url, outcome)
                return outcome
            last = outcome

        if attempt >= max(1, config.max_attempts):
            break
        delay = config.backoff_seconds * (2 ** (attempt - 1))
        if last.retry_after is not None:
            if last.retry_after > MAX_HONOURED_RETRY_AFTER:
                break                      # chegara uzoq — kutmaymiz, tan olamiz
            delay = max(delay, last.retry_after)
        sleep(delay + random.uniform(0, _JITTER))

    last.attempts = attempt
    last.elapsed_ms = int((time.monotonic() - started) * 1000)
    _log(provider, url, last)
    return last


def _interpret(response, attempt):
    status_code = response.status_code
    if status_code == 404:
        return HttpOutcome(NOT_FOUND, http_status=404, attempts=attempt)
    if status_code == 429:
        return HttpOutcome(RATE_LIMITED, http_status=429, attempts=attempt,
                           retry_after=_parse_retry_after(response))
    if status_code >= 500:
        return HttpOutcome(UNAVAILABLE, http_status=status_code, attempts=attempt)
    if status_code >= 400:
        # 400/401/403 — qayta urinish yordam bermaydi (noto'g'ri so'rov yoki
        # kalit). Sozlama xatosi sifatida qaraladi, sabab log'da qoladi.
        return HttpOutcome(INVALID_RESPONSE, http_status=status_code,
                           attempts=attempt, detail='HTTP %d' % status_code)
    try:
        payload = response.json()
    except ValueError:
        return HttpOutcome(INVALID_RESPONSE, http_status=status_code,
                           attempts=attempt, detail='JSON emas')
    if not isinstance(payload, dict):
        return HttpOutcome(INVALID_RESPONSE, http_status=status_code,
                           attempts=attempt, detail='obyekt emas')
    return HttpOutcome(OK, payload=payload, http_status=status_code,
                       attempts=attempt)


def _log(provider, url, outcome):
    """Kuzatuv (§33). URL query'siz — unda e-mail va API kalit bo'ladi."""
    logger.info('article-metadata provider=%s status=%s http=%s attempts=%d '
                'elapsed_ms=%d path=%s%s',
                provider, outcome.status, outcome.http_status, outcome.attempts,
                outcome.elapsed_ms, url.split('?', 1)[0],
                ' detail=%s' % outcome.detail if outcome.detail else '')
