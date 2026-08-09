#!/usr/bin/env python3.12
# -*- coding: utf-8 -*-
"""Rejalashtirilgan maqola metadata sinxronizatsiyasi (Phase 2C.3C).

    cron / operator -> BU SKRIPT -> global lock -> due DOI maqolalar ->
    bounded batch -> check_article_metadata(actor_type='system') ->
    article_metadata_syncs -> 2C.3B dashboard.

Bu skript FAQAT tekshiradi — hech qachon `scientific_articles`ga avtomatik
yozmaydi (apply/publish/PDF/OA emas). Barcha o'zgarish nomzodlari admin
review navbatiga (`/admin/articles/metadata-sync`) tushadi.

Loyihaning mavjud konvensiyasi (`scripts/daily_scraper.py`,
`scripts/grant_scraper.py`) — Flask'ga BOG'LIQ EMAS oddiy skript,
`DATABASE_URL`ni to'g'ridan-to'g'ri psycopg2 bilan ochadi. Gunicorn worker
ichida ISHLAMAYDI — faqat OS cron/systemd timer yoki operator qo'lda
chaqiradi (docs/article-metadata-scheduler.md).

Ishlatish:
    python3.12 scripts/sync_article_metadata.py                # cron/scheduled
    python3.12 scripts/sync_article_metadata.py --dry-run
    python3.12 scripts/sync_article_metadata.py --limit 10
    python3.12 scripts/sync_article_metadata.py --article-id 42
    python3.12 scripts/sync_article_metadata.py --manual

Chiqish kodi: 0 — muvaffaqiyatli/o'chirilgan/already_running/dry-run,
1 — run yakunlandi, lekin ba'zi maqolalarda xato bor edi, 2 — konfiguratsiya
yoki argument xatosi.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2  # noqa: E402

from services.article_sync.scheduler import (  # noqa: E402
    RUN_COMPLETED_WITH_ERRORS, SchedulerAlreadyRunningError,
    run_scheduled_metadata_sync)
from services.article_sync.scheduler_config import (  # noqa: E402
    SAFETY_MAX_BATCH_SIZE, SchedulerConfigError, scheduler_config_from_env)
from services.article_sync.scheduler_state import (  # noqa: E402
    TRIGGER_MANUAL_CLI, TRIGGER_SCHEDULED)


def _build_parser():
    parser = argparse.ArgumentParser(
        description='Rejalashtirilgan maqola metadata sinxronizatsiyasi '
                    '(faqat tekshiradi — avtomatik qo\'llamaydi).')
    parser.add_argument('--dry-run', action='store_true',
                        help='Due maqolalarni ko\'rsatadi, hech qanday '
                             'provayder so\'rovi/yozuv bajarmaydi.')
    parser.add_argument('--limit', type=int, default=None,
                        help='Bitta run uchun maksimal maqola soni '
                            '(ARTICLE_METADATA_SYNC_BATCH_SIZE ustidan, '
                            'safety cap bilan cheklangan).')
    parser.add_argument('--article-id', type=int, default=None,
                        help='Dev/debug: faqat shu maqolani majburan '
                            'tekshiradi (due-oyna e\'tiborga olinmaydi).')
    parser.add_argument('--manual', action='store_true',
                        help='Run\'ni trigger_type=manual_cli deb belgilaydi '
                            '(operator qo\'lda ishga tushirganda).')
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
        print('ARTICLE_METADATA_SYNC_ENABLED=false — sinxronizatsiya '
             'o\'chirilgan, chiqilmoqda.')
        return 0

    limit = args.limit
    if limit is not None:
        if limit <= 0:
            print('--limit musbat butun son bo\'lishi kerak.', file=sys.stderr)
            return 2
        limit = min(limit, SAFETY_MAX_BATCH_SIZE)

    trigger_type = (TRIGGER_MANUAL_CLI
                    if (args.manual or args.dry_run or args.article_id)
                    else TRIGGER_SCHEDULED)

    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    try:
        try:
            result = run_scheduled_metadata_sync(
                conn, config=config, dry_run=args.dry_run, limit_override=limit,
                article_id=args.article_id, trigger_type=trigger_type)
        except SchedulerAlreadyRunningError:
            print('already_running: boshqa sinxronizatsiya jarayoni davom '
                 'etmoqda, chiqilmoqda.')
            return 0
    finally:
        conn.close()

    if result.dry_run:
        print('DRY RUN: due=%d (hech qanday so\'rov/yozuv bajarilmadi)'
             % result.total_selected)
        for article_id in result.article_ids:
            print('  - article_id=%s' % article_id)
        return 0

    print('run_id=%s trigger=%s status=%s'
         % (result.run_id, result.trigger_type, result.status))
    print('selected=%d processed=%d success=%d partial=%d failed=%d skipped=%d'
         % (result.total_selected, result.processed, result.success,
            result.partial, result.failed, result.skipped))

    return 1 if result.status == RUN_COMPLETED_WITH_ERRORS else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
