# -*- coding: utf-8 -*-
"""Ommaviy import konfiguratsiyasi (Phase 2C.2).

`services/article_metadata/config.py` bilan bir xil naqsh: konfiguratsiya
ATAYIN obyekt sifatida uzatiladi, modul darajasida `os.environ` o'qilmaydi —
testlar env'ga tegmasdan ishlaydi.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Bitta batch'da nechta DOI/qator bo'lishi mumkin. Server tasodifan
# 100 000 DOI bilan bosib yuborilmasin (§14). Aniq son emas — mavjud
# infratuzilma tekshirilib tanlangan: sinxron chunk processing + tashqi
# API budjeti (services/article_metadata/config.py: TOTAL_BUDGET=20s har
# chunk uchun) 500 ta elementni oqilona vaqt ichida ishlaydi.
DEFAULT_MAX_ITEMS = 500

# Bitta HTTP so'rovda nechta element ishlanadi (§21). Kichik bo'lishi kerak:
# har chunk o'zining metadata lookup byudjetini (provider config.total_budget,
# taxminan 20s) ketma-ket ko'paytiradi, shuning uchun request umumiy vaqti
# taxminan CHUNK_SIZE * 20s dan oshmasligi kerak — 20 emas, 10 ta odatiy.
DEFAULT_CHUNK_SIZE = 10

# Bitta element nechta marta qayta urinilishi mumkin (provider-level retry
# bundan TASHQARI — bu bulk-level, admin "Qayta urinish" bosgandan keyingi
# chegaraviy son, §22).
DEFAULT_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class ArticleImportConfig:
    max_items: int = DEFAULT_MAX_ITEMS
    chunk_size: int = DEFAULT_CHUNK_SIZE
    max_attempts: int = DEFAULT_MAX_ATTEMPTS


def _int_env(name: str, default: int) -> int:
    raw = (os.environ.get(name) or '').strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def config_from_env(environ=None) -> ArticleImportConfig:
    env = environ if environ is not None else os.environ
    return ArticleImportConfig(
        max_items=_int_env('ARTICLE_IMPORT_MAX_ITEMS', DEFAULT_MAX_ITEMS),
        chunk_size=_int_env('ARTICLE_IMPORT_CHUNK_SIZE', DEFAULT_CHUNK_SIZE),
        max_attempts=_int_env('ARTICLE_IMPORT_MAX_ATTEMPTS', DEFAULT_MAX_ATTEMPTS),
    )
