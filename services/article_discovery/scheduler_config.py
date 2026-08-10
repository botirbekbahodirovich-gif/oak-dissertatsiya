# -*- coding: utf-8 -*-
"""Scheduled article discovery konfiguratsiyasi (Phase 2D.3).

`services/article_sync/scheduler_config.py` bilan bir xil naqsh: obyekt
sifatida uzatiladi (testlar `os.environ`ga tegmaydi), noto'g'ri qiymat
jim defaultga qaytarilmaydi — `SchedulerConfigError` baland ovozda
ko'tariladi (noto'g'ri interval/limit jim "hech kim ishlamaydi" yoki
"cheksiz so'rov" kabi kutilmagan production oqibatiga olib kelmasin).

ATAYIN FARQ (§13, §25 — production xavfsizligi): `ARTICLE_METADATA_SYNC_
ENABLED` sukut bo'yicha `true`, lekin `ARTICLE_DISCOVERY_SCHEDULER_ENABLED`
sukut bo'yicha **FALSE**. Discovery scheduler tashqi API'ga so'rov yuboradi
VA (agar yangi DOI topilsa) avtomatik import navbatiga yozadi — bu birinchi
marta operator QOchidan tekshirmasdan yoqilmasligi kerak (ticket §13, §25:
"deploy -> manual verify -> one test saved query -> manual scheduler
invoke -> inspect -> ONLY THEN enable recurring").
"""
from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_ENABLED = False
DEFAULT_INTERVAL_MINUTES = 15
DEFAULT_MAX_QUERIES = 10

# Operator/env `max_queries`ni bundan yuqoriga chiqara olmaydi — bitta
# tsikl minglab saqlangan so'rovni birdan so'ramasin (§6, §25).
SAFETY_MAX_QUERIES = 100

# Bitta so'rovni ishlash uchun import-chunk bosqichlarining maksimal soni
# (process_import_batch() ni qayta-qayta chaqirish, §11) — cheksiz tsikl
# xavfidan himoya, ARTICLE_IMPORT_MAX_ITEMS (500) / odatiy chunk (10) dan
# ancha katta headroom bilan.
MAX_IMPORT_CHUNK_ITERATIONS = 100


class SchedulerConfigError(ValueError):
    """Konfiguratsiya noto'g'ri — operator/cron buni DARHOL ko'rishi kerak."""


@dataclass(frozen=True)
class DiscoverySchedulerConfig:
    enabled: bool = DEFAULT_ENABLED
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES
    max_queries: int = DEFAULT_MAX_QUERIES


def _bool_env(name, default, env):
    raw = (env.get(name) or '').strip().lower()
    if not raw:
        return default
    if raw in ('1', 'true', 'yes', 'on'):
        return True
    if raw in ('0', 'false', 'no', 'off'):
        return False
    raise SchedulerConfigError(
        "%s noto'g'ri qiymat: %r (true/false kutilgan)" % (name, raw))


def _positive_int_env(name, default, env, *, max_value=None):
    raw = (env.get(name) or '').strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise SchedulerConfigError(
            "%s butun son bo'lishi kerak: %r" % (name, raw)) from None
    if value <= 0:
        raise SchedulerConfigError("%s musbat bo'lishi kerak: %r" % (name, value))
    if max_value is not None and value > max_value:
        raise SchedulerConfigError(
            "%s %d dan oshmasligi kerak: %r" % (name, max_value, value))
    return value


def scheduler_config_from_env(environ=None) -> DiscoverySchedulerConfig:
    """Env'dan konfiguratsiya (`ARTICLE_DISCOVERY_SCHEDULER_*`, .env.example)."""
    env = environ if environ is not None else os.environ
    return DiscoverySchedulerConfig(
        enabled=_bool_env('ARTICLE_DISCOVERY_SCHEDULER_ENABLED', DEFAULT_ENABLED, env),
        interval_minutes=_positive_int_env(
            'ARTICLE_DISCOVERY_SCHEDULER_INTERVAL_MINUTES',
            DEFAULT_INTERVAL_MINUTES, env),
        max_queries=_positive_int_env(
            'ARTICLE_DISCOVERY_SCHEDULER_MAX_QUERIES', DEFAULT_MAX_QUERIES, env,
            max_value=SAFETY_MAX_QUERIES),
    )
