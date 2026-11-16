#!/usr/bin/env python3.12
# -*- coding: utf-8 -*-
"""Ochiq litsenziyali maqolalarning PDF'ini serverga olish.

Defaultda DRY-RUN: hech narsa yuklanmaydi va bazaga yozilmaydi — faqat
qaysi maqola uchun qaysi URL olinishi ko'rsatiladi. Haqiqiy ish faqat
`--execute` bilan. `--limit` MAJBURIY.

    python3.12 scripts/fetch_open_pdfs.py --limit 50                        # dry-run
    python3.12 scripts/fetch_open_pdfs.py --limit 50 --execute --admin-id 1

Qoidalar va xavfsizlik: services/article_pdf_fetch.py docstring'i.
Skript maqolani NASHR QILMAYDI. Fayllar `storage/articles/YYYY/MM/` ga
yoziladi — servis foydalanuvchisi (gunicorn) nomidan ishga tushiring, aks
holda fayllarni o'qiy olmaydi.

Chiqish kodi: 0 — tugadi, 1 — disk to'ldi / kutilmagan xato bilan to'xtadi,
2 — argument/konfiguratsiya xatosi.
"""
import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2  # noqa: E402

from services.article_pdf_fetch import (DiskSpaceLow, HostThrottle,  # noqa: E402
                                        PdfFetchConfig, ensure_disk_space,
                                        fetch_one, load_candidates)
from utils.article_rules import auto_pdf_licenses  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def storage_dir():
    """`blueprints/articles.py:storage_dir()` bilan bir xil qoida (Flask'siz)."""
    return os.environ.get('ARTICLE_STORAGE_DIR') or os.path.join(ROOT, 'storage', 'articles')


def build_parser():
    p = argparse.ArgumentParser(description='Ochiq litsenziyali maqolalar PDF\'ini olish '
                                            '(defaultda dry-run).')
    p.add_argument('--limit', type=int, required=True,
                   help='Ko\'pi bilan shuncha maqola ko\'riladi (majburiy).')
    p.add_argument('--execute', action='store_true',
                   help='Haqiqatan yuklash va bazaga yozish.')
    p.add_argument('--admin-id', type=int, default=None,
                   help='rights_confirmed_by ga yoziladigan admin id (--execute bilan majburiy).')
    p.add_argument('--report', default=None,
                   help='JSON hisobot yo\'li (default: reports/fetch_open_pdfs_<vaqt>.json).')
    return p


def main(argv):
    args = build_parser().parse_args(argv[1:])
    if args.limit <= 0:
        print('--limit musbat bo\'lishi kerak.', file=sys.stderr)
        return 2
    if args.execute and not args.admin_id:
        print('--execute uchun --admin-id majburiy (rights_confirmed_by).', file=sys.stderr)
        return 2
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        print('DATABASE_URL sozlanmagan.', file=sys.stderr)
        return 2

    contact = (os.environ.get('SCHOLARLY_API_EMAIL') or '').strip() or None
    if args.execute and not contact:
        print('SCHOLARLY_API_EMAIL sozlanmagan — User-Agent\'da kontakt email shart.',
              file=sys.stderr)
        return 2
    config = PdfFetchConfig(contact_email=contact, allowed_licenses=auto_pdf_licenses())
    target = storage_dir()

    mode = 'EXECUTE' if args.execute else 'DRY-RUN'
    print('[%s] litsenziyalar: %s; limit=%d; storage=%s'
          % (mode, ', '.join(sorted(config.allowed_licenses)), args.limit, target))
    if args.execute:
        try:
            free = ensure_disk_space(target, config.min_free_bytes)
        except DiskSpaceLow as exc:
            print('TO\'XTADI: %s' % exc, file=sys.stderr)
            return 1
        print('Bo\'sh joy: %.1f GB' % (free / 1024 ** 3))

    import requests
    session = requests.Session()
    throttle = HostThrottle(config.per_host_interval)
    outcomes, reasons, rows_out = Counter(), Counter(), []
    exit_code = 0

    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            candidates = load_candidates(cur, config, args.limit)
            conn.commit()
            print('Nomzodlar: %d' % len(candidates))
            for article in candidates:
                try:
                    status, reason, info = fetch_one(
                        cur, article, config=config, session=session, throttle=throttle,
                        storage_dir=target, admin_id=args.admin_id, execute=args.execute)
                    conn.commit()
                except DiskSpaceLow as exc:
                    conn.rollback()
                    print('TO\'XTADI: %s' % exc, file=sys.stderr)
                    exit_code = 1
                    break
                except Exception as exc:  # noqa: BLE001 — bitta maqola butun ishni yiqitmasin
                    conn.rollback()
                    status, reason, info = 'error', type(exc).__name__, None
                outcomes[status] += 1
                if reason:
                    reasons[reason] += 1
                rows_out.append({'article_id': article['id'], 'doi': article.get('doi'),
                                 'license': article.get('license_type'), 'status': status,
                                 'reason': reason,
                                 'pdf_url': (info or {}).get('pdf_url'),
                                 'version': (info or {}).get('version_type')})
                print('#%-7s %-12s %-22s %s' % (article['id'], status, reason or '',
                                                 (info or {}).get('pdf_url') or ''))
    finally:
        conn.close()
        session.close()

    report = {
        'mode': mode, 'finished_at': datetime.now(timezone.utc).isoformat(),
        'limit': args.limit, 'licenses': sorted(config.allowed_licenses),
        'outcomes': dict(outcomes), 'skip_reasons': dict(reasons),
        'blocked_hosts': dict(throttle.blocked), 'items': rows_out,
    }
    path = args.report or os.path.join(
        ROOT, 'reports', 'fetch_open_pdfs_%s.json' % datetime.now().strftime('%Y%m%d_%H%M%S'))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1, default=str)

    print('\n== Hisobot (%s) ==' % mode)
    for key in ('stored', 'would_fetch', 'skipped', 'error'):
        print('  %-12s %d' % (key, outcomes.get(key, 0)))
    if reasons:
        print('  Sabablar:')
        for key, count in reasons.most_common():
            print('    %-24s %d' % (key, count))
    if throttle.blocked:
        print('  Bloklangan domenlar: %s' % ', '.join(sorted(throttle.blocked)))
    print('  JSON: %s' % path)
    return exit_code


if __name__ == '__main__':
    sys.exit(main(sys.argv))
