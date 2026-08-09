#!/usr/bin/env python3.12
# -*- coding: utf-8 -*-
"""Haqiqiy OpenAlex `/works` qidiruvi bilan developer smoke testi
(Phase 2D.1, `scripts/smoke_article_metadata.py` bilan bir xil naqsh).

Bu skript unittest to'plamiga KIRMAYDI: u tashqi tarmoqqa chiqadi, demak
OpenAlex vaqtincha yotganda "kod buzildi" degan yolg'on signal berardi.
Qo'lda, sozlamalarni tekshirish uchun ishga tushiring:

    OPENALEX_API_KEY=... python3.12 scripts/smoke_article_discovery.py
    # yoki
    OPENALEX_MAILTO=siz@example.uz python3.12 scripts/smoke_article_discovery.py

    # ixtiyoriy: kalit so'z va limit
    python3.12 scripts/smoke_article_discovery.py "artificial intelligence" 5

Chiqishda API kaliti yoki e-mail HECH QACHON ko'rsatilmaydi — faqat
sozlangan/sozlanmagan holati va qaysi usul (key/mailto) ishlatilgani.
Hech qanday javob repozitoriyga saqlanmaydi, hech narsa navbatga
yuborilmaydi (bu faqat qidiruv, discovery service.run_discovery_search()
bilan bir xil — DB'ga yozmaydi).
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.article_discovery import openalex  # noqa: E402
from services.article_discovery.config import config_from_env  # noqa: E402
from services.article_discovery.models import ArticleDiscoveryQuery  # noqa: E402
from services.article_metadata.models import NOT_CONFIGURED, OK  # noqa: E402

DEFAULT_KEYWORD = 'artificial intelligence'
DEFAULT_LIMIT = 5
DEFAULT_WINDOW_DAYS = 90  # so'nggi 90 kun — kichik, deterministik, ochiq mavzu


def main(argv):
    keyword = argv[1] if len(argv) > 1 else DEFAULT_KEYWORD
    limit = int(argv[2]) if len(argv) > 2 else DEFAULT_LIMIT

    config = config_from_env()
    print('Sozlamalar (qiymatlar ko‘rsatilmaydi):')
    print('  OpenAlex API kaliti: %s' % ('sozlangan' if config.openalex_api_key else 'YO‘Q'))
    print('  OpenAlex mailto:     %s' % ('sozlangan' if config.openalex_mailto else 'YO‘Q'))
    print('  Ishlatiladigan usul: %s' % (
        'api_key' if config.openalex_api_key else
        ('mailto/polite-pool' if config.openalex_contact else 'YO‘Q — not_configured')))
    print()

    if not config.is_configured:
        print('OPENALEX_API_KEY yoki OPENALEX_MAILTO topilmadi — smoke o‘tkazib '
             'yuborildi (bu XATO emas, sozlama yo‘q holati).')
        return 0

    query = ArticleDiscoveryQuery(
        keyword=keyword, from_date=date.today() - timedelta(days=DEFAULT_WINDOW_DAYS),
        to_date=date.today(), oak_field_id=None, limit=limit)
    print('So‘rov: keyword=%r, %s..%s, limit=%d'
         % (query.keyword, query.from_date, query.to_date, query.limit))

    result = openalex.search(query, config)

    if result.status == NOT_CONFIGURED:
        print('KUTILMAGAN: not_configured (yuqorida sozlangan deb ko‘rsatilgan edi)')
        return 1
    if result.status != OK:
        print('XATO: provider status=%s http_status=%s detail=%s'
             % (result.status, result.http_status, result.error_code))
        return 1

    print('OK — auth ishladi, javob parse qilindi.')
    print('  Manbada mos (provider total): %d' % result.total_count)
    print('  Bu so‘rovda ko‘rib chiqildi (bounded, <=limit): %d' % len(result.candidates))
    print('  DOI‘siz (tashlab yuborilgan):                 %d' % result.excluded_no_doi_count)
    print()
    for candidate in result.candidates:
        print('  DOI: %-30s  %s' % (candidate.doi, candidate.title or '—'))
        print('       manba: %-30s  sana: %s  retracted: %s'
             % (candidate.source_name or '—', candidate.publication_date,
                candidate.is_retracted))
        print('       OpenAlex ID: %s' % (candidate.external_source_id or '—'))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
