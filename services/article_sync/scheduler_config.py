# -*- coding: utf-8 -*-
"""Rejalashtirilgan metadata sinxronizatsiyasi konfiguratsiyasi (Phase 2C.3C).

`services/article_metadata/config.py`dagi kabi obyekt sifatida uzatiladi —
testlar `os.environ`ga tegmaydi. ATAYIN FARQ: u yerda noto'g'ri sonli qiymat
jim DEFAULTga qaytariladi (§ optional tuning), bu yerda esa BALAND OVOZDA
`SchedulerConfigError` ko'tariladi — chunki noto'g'ri interval/batch sukut
bo'yicha "hech kim tekshirilmaydi" yoki "hammasi bir vaqtda so'raladi" kabi
jim, kutilmagan production oqibatiga olib kelishi mumkin (vazifa §28).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_INTERVAL_DAYS = 30
DEFAULT_BATCH_SIZE = 50

# Operator `--limit` bilan konfiguratsiya batch'idan yuqoriga chiqa olmaydi —
# bitta run minglab maqolani birdan API'ga yubormasin (§11, §25).
SAFETY_MAX_BATCH_SIZE = 500


class SchedulerConfigError(ValueError):
    """Konfiguratsiya noto'g'ri — operator/cron buni DARHOL ko'rishi kerak."""


@dataclass(frozen=True)
class SchedulerConfig:
    enabled: bool = True
    interval_days: int = DEFAULT_INTERVAL_DAYS
    batch_size: int = DEFAULT_BATCH_SIZE


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


def _positive_int_env(name, default, env):
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
    return value


def scheduler_config_from_env(environ=None) -> SchedulerConfig:
    """Env'dan konfiguratsiya (`ARTICLE_METADATA_SYNC_*`, .env.example)."""
    env = environ if environ is not None else os.environ
    return SchedulerConfig(
        enabled=_bool_env('ARTICLE_METADATA_SYNC_ENABLED', True, env),
        interval_days=_positive_int_env(
            'ARTICLE_METADATA_SYNC_INTERVAL_DAYS', DEFAULT_INTERVAL_DAYS, env),
        batch_size=_positive_int_env(
            'ARTICLE_METADATA_SYNC_BATCH_SIZE', DEFAULT_BATCH_SIZE, env),
    )
