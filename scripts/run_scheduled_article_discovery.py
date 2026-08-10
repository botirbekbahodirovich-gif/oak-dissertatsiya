#!/usr/bin/env python3.12
# -*- coding: utf-8 -*-
"""Rejalashtirilgan (scheduled) maqola discovery (Phase 2D.3).

    cron / operator -> BU SKRIPT -> global lock -> due saqlangan so'rovlar
    -> bounded band qilish -> services.article_discovery.service.
    run_saved_query() -> article_discovery_runs -> admin panel.

Bu skript FAQAT "yangi" (bazada yo'q, hech qanday navbatda yo'q) DOI'larni
mavjud import navbatiga qo'yadi va uni oxirigacha ishlatadi (metadata olish)
— HECH QACHON avtomatik approve/publish qilmaydi. Barcha nomzod maqolalar
admin review navbatiga (`/admin/articles/imports`) tushadi.

Loyihaning mavjud konvensiyasi (`scripts/sync_article_metadata.py`) —
Flask'ga BOG'LIQ EMAS oddiy skript, `DATABASE_URL`ni to'g'ridan-to'g'ri
psycopg2 bilan ochadi. Gunicorn worker ichida ISHLAMAYDI — faqat OS
cron/systemd timer yoki operator qo'lda chaqiradi
(docs/article-discovery.md).

Ishlatish:
    python3.12 scripts/run_scheduled_article_discovery.py                # cron
    python3.12 scripts/run_scheduled_article_discovery.py --dry-run
    python3.12 scripts/run_scheduled_article_discovery.py --limit 3

Chiqish kodi: 0 — muvaffaqiyatli/o'chirilgan/already_running/dry-run,
1 — tsikl yakunlandi, lekin ba'zi so'rovlarda xato bor edi, 2 — konfiguratsiya
yoki argument xatosi.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2  # noqa: E402

from services.article_discovery.scheduler import (  # noqa: E402
    SchedulerAlreadyRunningError, run_due_discovery_queries)
from services.article_discovery.scheduler_config import (  # noqa: E402
    SAFETY_MAX_QUERIES, SchedulerConfigError, scheduler_config_from_env)
from services.article_discovery.scheduler_state import RUN_FAILED  # noqa: E402


def _build_parser():
    parser = argparse.ArgumentParser(
        description='Rejalashtirilgan maqola discovery (faqat "yangi" '
                    'DOI\'larni mavjud import navbatiga qo\'yadi — '
                    'avtomatik approve/publish qilmaydi).')
    parser.add_argument('--dry-run', action='store_true',
                        help='Due saqlangan so\'rovlarni ko\'rsatadi, hech '
                             'qanday provayder so\'rovi/yozuv bajarmaydi.')
    parser.add_argument('--limit', type=int, default=None,
                        help='Bitta tsikl uchun maksimal saqlangan so\'rov '
                            'soni (ARTICLE_DISCOVERY_SCHEDULER_MAX_QUERIES '
                            'ustidan, safety cap bilan cheklangan).')
    return parser


def main(argv):
    args = _build_parser().parse_args(argv[1:])

    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        print('DATABASE_URL sozlanmagan.', file=sys.stderr)
        return 2

    try:
        config = scheduler_config_from_env()
    except SchedulerConfigError as exc:
        print('Konfiguratsiya xatosi: %s' % exc, file=sys.stderr)
        return 2

    if not config.enabled and not args.dry_run:
        print('ARTICLE_DISCOVERY_SCHEDULER_ENABLED=false — rejalashtirilgan '
             'discovery o\'chirilgan, chiqilmoqda.')
        return 0

    limit = args.limit
    if limit is not None:
        if limit <= 0:
            print('--limit musbat butun son bo\'lishi kerak.', file=sys.stderr)
            return 2
        limit = min(limit, SAFETY_MAX_QUERIES)

    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    try:
        try:
            result = run_due_discovery_queries(
                conn, config=config, dry_run=args.dry_run, limit_override=limit)
        except SchedulerAlreadyRunningError:
            print('already_running: boshqa discovery scheduler tsikli davom '
                 'etmoqda, chiqilmoqda.')
            return 0
    finally:
        conn.close()

    if result.dry_run:
        print('DRY RUN: due=%d (hech qanday so\'rov/yozuv bajarilmadi)'
             % len(result.due))
        for row in result.due:
            print('  - saved_query_id=%s name=%r' % (row['id'], row['name']))
        return 0

    print('claimed=%d' % result.claimed)
    failed = 0
    for outcome in result.results:
        print('  - saved_query_id=%s name=%r status=%s run_id=%s%s'
             % (outcome.saved_query_id, outcome.saved_query_name, outcome.status,
                outcome.run_id,
                (' error=%s' % outcome.error_summary) if outcome.error_summary else ''))
        if outcome.status == RUN_FAILED:
            failed += 1

    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
