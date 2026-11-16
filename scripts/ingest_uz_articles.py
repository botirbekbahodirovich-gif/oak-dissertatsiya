#!/usr/bin/env python3.12
# -*- coding: utf-8 -*-
"""O'zbekiston mualliflarining maqolalarini ommaviy import qilish.

Kontent (qabul qilingan qaror): OpenAlex'dagi DOI'li `type:article`, kamida
bitta muallifi O'zbekiston muassasasidan, `primary_location.source.is_core`
jurnallarda, `--from-date` dan keyin.

Yangi ingestion yo'li YO'Q — mavjud pipeline qayta ishlatiladi:

    OpenAlex cursor (services/article_discovery/openalex.iter_cursor)
      -> dedup klassifikatsiya (service._classify_candidates — bazadagi va
         navbatdagi DOI'lar o'tkazib yuboriladi)
      -> bridge.enqueue_discovery_candidates (source_type='discovery',
         500 talik batchlar, request_id = DOI'lar xeshi)
      -> process_import_batch (fetch_article_metadata: Crossref/OpenAlex/
         Unpaywall)
      -> bulk_approve_safe_items (FAQAT ready_for_review; draft +
         pending_review maqola; litsenziya kelishuvi va high OAK moslik
         avtomatik)

HECH QACHON nashr qilmaydi va PDF yuklamaydi (PDF — scripts/fetch_open_pdfs.py,
nashr — admin "Nashrga tayyor" tugmasi).

Defaultda DRY-RUN: bazaga yozmaydi (read-only sessiya), faqat OpenAlex'dan
son va nomzodlarni o'qiydi, budjetni hisoblaydi. Yozish faqat `--execute`.
Idempotent: qayta ishga tushirilsa bazadagi/navbatdagi DOI'lar qayta
qo'shilmaydi, to'xtagan batchlar (`pending`) davom ettiriladi.

    python3.12 scripts/ingest_uz_articles.py --from-date 2024-01-01 --country UZ \\
        --core-only --limit 200                                   # dry-run
    python3.12 scripts/ingest_uz_articles.py --from-date 2024-01-01 --country UZ \\
        --core-only --limit 200 --execute --admin-id 1

Chiqish kodi: 0 — tugadi, 1 — ish xato bilan to'xtadi, 2 — argument /
konfiguratsiya xatosi (masalan OPENALEX_API_KEY yo'q).
"""
import argparse
import hashlib
import json
import math
import os
import sys
from collections import Counter
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2  # noqa: E402

from services.article_discovery import bridge, openalex  # noqa: E402
from services.article_discovery.config import config_from_env  # noqa: E402
from services.article_discovery.models import NEW, ArticleDiscoveryQuery  # noqa: E402
from services.article_discovery.service import _classify_candidates  # noqa: E402
from services.article_import import service as import_service  # noqa: E402
from services.article_import.config import config_from_env as import_config_from_env  # noqa: E402
from services.article_metadata.http import fetch_json  # noqa: E402
from services.article_metadata.models import OPENALEX  # noqa: E402
from utils.article_rules import auto_pdf_licenses  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRIGGER = 'cli_ingest_uz'
DEFAULT_BATCH_SIZE = 500
APPROVE_GROUP = 100

# OpenAlex narxi (2026, developers.openalex.org): kalit bilan kuniga $1;
# singleton (DOI bo'yicha) $0, ro'yxat so'rovi $0.0001. Eski "credits"
# modeli (100 000/kun; singleton 1, ro'yxat 10) ham hisoblanadi — qaysi biri
# amalda ekanini `/rate-limit` javobi ko'rsatadi.
USD_DAILY = float(os.environ.get('OPENALEX_DAILY_BUDGET_USD') or 1.0)
USD_LIST, USD_SINGLETON = 0.0001, 0.0
CREDITS_DAILY, CREDITS_LIST, CREDITS_SINGLETON = 100_000, 10, 1
UNPAYWALL_DAILY = 100_000


def build_parser():
    p = argparse.ArgumentParser(description="O'zbekiston maqolalarini import qilish "
                                            "(defaultda dry-run).")
    p.add_argument('--from-date', type=date.fromisoformat, required=True)
    p.add_argument('--to-date', type=date.fromisoformat, default=None)
    p.add_argument('--country', default='UZ')
    p.add_argument('--core-only', action='store_true')
    p.add_argument('--limit', type=int, required=True,
                   help="OpenAlex'dan ko'riladigan ishlar soni — qat'iy chegara.")
    p.add_argument('--execute', action='store_true', help='Bazaga yozish.')
    p.add_argument('--admin-id', type=int, default=None,
                   help='Batch/maqola created_by (--execute bilan majburiy).')
    p.add_argument('--batch-size', type=int, default=DEFAULT_BATCH_SIZE)
    p.add_argument('--stages', default='discover,process,approve',
                   help='Vergul bilan: discover, process, approve.')
    p.add_argument('--report', default=None)
    return p


def _request_id(dois):
    raw = '%s:%s' % (TRIGGER, ','.join(sorted(dois)))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _rate_limit_status(config):
    """`GET /rate-limit` — kalit egasining joriy budjeti (best-effort).
    Kalit qiymati hech qayerga chiqmaydi."""
    outcome = fetch_json('https://api.openalex.org/rate-limit', provider=OPENALEX,
                         config=config, params={'api_key': config.openalex_api_key},
                         headers={'User-Agent': config.user_agent()})
    if not outcome.ok:
        return {'status': outcome.status, 'http_status': outcome.http_status}
    payload = outcome.payload or {}
    payload.pop('api_key', None)
    return payload


def _total_count(config, args, to_date):
    query = ArticleDiscoveryQuery(keyword='', from_date=args.from_date, to_date=to_date,
                                  oak_field_id=None, limit=1, providers=(OPENALEX,),
                                  country_code=args.country, core_only=args.core_only)
    result = openalex.search(query, config)
    return result.total_count if result.ok else None


def budget_plan(total, limit, per_page=openalex.CURSOR_PER_PAGE):
    n = min(total if total is not None else limit, limit)
    pages = math.ceil(n / per_page) + 1          # +1 — sanash so'rovi
    usd = pages * USD_LIST + n * USD_SINGLETON
    credits = pages * CREDITS_LIST + n * CREDITS_SINGLETON
    days = max(math.ceil(usd / USD_DAILY) if USD_DAILY else 1,
               math.ceil(credits / CREDITS_DAILY),
               math.ceil(n / UNPAYWALL_DAILY), 1)
    return {'works': n, 'list_requests': pages, 'singleton_requests': n,
            'usd_estimate': round(usd, 4), 'usd_daily_budget': USD_DAILY,
            'credits_estimate': credits, 'credits_daily': CREDITS_DAILY,
            'unpaywall_requests': n, 'crossref_requests': n,
            'fits_in_one_day': days == 1, 'days_needed': days,
            'per_day_limit_suggestion': (None if days == 1 else math.ceil(n / days))}


def _our_batches(cur):
    cur.execute("""SELECT id FROM article_import_batches
                    WHERE source_type = 'discovery'
                      AND source_query -> 'scheduled' ->> 'trigger_type' = %s
                    ORDER BY id""", (TRIGGER,))
    return [r[0] for r in cur.fetchall()]


def _previously_decided(cur, dois):
    """Admin `rejected`/`skipped` qilgan DOI'lar (har qanday batchda)."""
    if not dois:
        return set()
    cur.execute("""SELECT DISTINCT normalized_doi FROM article_import_items
                    WHERE normalized_doi = ANY(%s) AND status IN ('rejected', 'skipped')""",
                (sorted(set(dois)),))
    return {r[0] for r in cur.fetchall()}


def stage_discover(conn, cur, config, args, to_date, execute, report):
    query = ArticleDiscoveryQuery(keyword='', from_date=args.from_date, to_date=to_date,
                                  oak_field_id=None, limit=args.limit, providers=(OPENALEX,),
                                  country_code=args.country, core_only=args.core_only)
    counts = Counter()
    pending_new = []

    def flush(final=False):
        while pending_new and (final or len(pending_new) >= args.batch_size):
            chunk = pending_new[:args.batch_size]
            del pending_new[:args.batch_size]
            if not execute:
                counts['would_enqueue'] += len(chunk)
                continue
            result = bridge.enqueue_discovery_candidates(
                cur, args.admin_id, query, chunk, total_count=report.get('total_count') or 0,
                excluded_no_doi_count=0, request_id=_request_id([c.doi for c in chunk]),
                extra_provenance={'trigger_type': TRIGGER, 'saved_query_id': None,
                                  'saved_query_name': 'scripts/ingest_uz_articles.py'})
            conn.commit()
            counts['enqueued'] += len(result['queued'])
            counts['enqueue_conflicts'] += len(result['conflicted'])
            if result['batch_id']:
                report.setdefault('batches_created', []).append(result['batch_id'])

    for page, _cursor in openalex.iter_cursor(
            config, from_date=args.from_date, to_date=to_date, limit=args.limit,
            country_code=args.country, core_only=args.core_only):
        if not page.ok:
            report['discover_error'] = page.status
            break
        counts['found'] += len(page.candidates)
        counts['no_doi'] += page.excluded_no_doi_count
        classified = _classify_candidates(cur, list(page.candidates))
        decided = _previously_decided(cur, [c.doi for c in classified
                                             if c.classification == NEW])
        if not execute:
            conn.rollback()                       # read-only sessiya, hech narsa yozilmaydi
        for c in classified:
            if c.classification == NEW and c.doi in decided:
                # Admin avval rad etgan / o'tkazib yuborgan DOI qayta navbatga
                # QO'YILMAYDI (idempotentlik admin qarorini ham hurmat qiladi).
                counts['class_previously_rejected_or_skipped'] += 1
                continue
            counts['class_' + c.classification] += 1
            if c.classification == NEW:
                pending_new.append(c)
        flush()
    flush(final=True)
    report['discover'] = dict(counts)


def stage_process(conn, cur, report):
    import_config = import_config_from_env()
    counts = Counter()
    for batch_id in _our_batches(cur):
        cur.execute("SELECT COUNT(*) FROM article_import_items "
                    "WHERE batch_id = %s AND status = 'pending'", (batch_id,))
        if not cur.fetchone()[0]:
            continue
        for _ in range(100000):
            result = import_service.process_import_batch(cur, batch_id, config=import_config)
            conn.commit()
            for _item_id, status in result.get('items') or []:
                counts[status] += 1
            if result['processed'] == 0:
                break
    report['process'] = dict(counts)


def stage_approve(conn, cur, args, report):
    counts = Counter()
    for batch_id in _our_batches(cur):
        cur.execute("""SELECT id FROM article_import_items
                        WHERE batch_id = %s AND status = 'ready_for_review'
                          AND NOT is_retracted ORDER BY row_number""", (batch_id,))
        ids = [r[0] for r in cur.fetchall()]
        for start in range(0, len(ids), APPROVE_GROUP):
            group = ids[start:start + APPROVE_GROUP]
            result = import_service.bulk_approve_safe_items(cur, batch_id, args.admin_id, group)
            conn.commit()
            counts['approved'] += len(result['approved'])
            counts['duplicates'] += len(result['duplicates'])
            counts['skipped_unsafe'] += len(result['skipped_unsafe'])
    report['approve'] = dict(counts)


def summary_from_db(cur):
    """Barcha CLI batchlar bo'yicha yakuniy raqamlar (qayta ishga tushirishda
    ham to'liq manzara)."""
    batches = _our_batches(cur)
    out = {'batches': len(batches)}
    if not batches:
        return out
    cur.execute("""SELECT status, COUNT(*) FROM article_import_items
                    WHERE batch_id = ANY(%s) GROUP BY status""", (batches,))
    out['items_by_status'] = dict(cur.fetchall())
    cur.execute("""SELECT COALESCE(last_error_code, '-'), COUNT(*) FROM article_import_items
                    WHERE batch_id = ANY(%s) AND status IN ('failed', 'provider_failed',
                          'metadata_not_found') GROUP BY 1""", (batches,))
    out['errors_by_code'] = dict(cur.fetchall())
    article_ids_sql = """SELECT created_article_id FROM article_import_items
                          WHERE batch_id = ANY(%s) AND created_article_id IS NOT NULL"""
    cur.execute(f"""SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE (ingest_evidence -> 'oak_field' ->> 'applied') = 'true'),
                       COUNT(*) FILTER (WHERE ingest_evidence -> 'oak_field' ->> 'confidence' = 'low'),
                       COUNT(*) FILTER (WHERE ingest_evidence -> 'oak_field' ->> 'oak_code' IS NULL),
                       COUNT(*) FILTER (WHERE ingest_evidence -> 'license' ->> 'status' = 'conflict'),
                       COUNT(*) FILTER (WHERE COALESCE(pdf_path, '') <> '')
                      FROM scientific_articles WHERE id IN ({article_ids_sql})""", (batches,))
    total, field_high, field_low, field_none, lic_conflict, with_pdf = cur.fetchone()
    out['articles'] = {'created': total, 'field_auto_high': field_high,
                       'field_low_needs_review': field_low, 'field_unmapped': field_none,
                       'license_conflict': lic_conflict, 'with_pdf': with_pdf}
    cur.execute(f"""SELECT license_type, COUNT(*) FROM scientific_articles
                     WHERE id IN ({article_ids_sql}) GROUP BY 1 ORDER BY 2 DESC""", (batches,))
    out['articles_by_license'] = dict(cur.fetchall())
    cur.execute(f"""SELECT COUNT(*) FROM scientific_articles a
                     JOIN LATERAL (SELECT metadata_candidate FROM article_import_items it
                                    WHERE it.created_article_id = a.id
                                    ORDER BY it.id DESC LIMIT 1) i ON TRUE
                     WHERE a.id IN ({article_ids_sql}) AND a.license_type = ANY(%s)
                       AND EXISTS (SELECT 1 FROM jsonb_array_elements(
                               COALESCE(i.metadata_candidate -> 'external_locations', '[]'::jsonb)) l
                            WHERE COALESCE(l ->> 'pdf_url', '') <> '')""",
                (batches, sorted(auto_pdf_licenses())))
    out['articles']['pdf_candidates'] = cur.fetchone()[0]
    cur.execute(f"""SELECT f.code, f.name_uz, COUNT(*) FROM scientific_articles a
                     JOIN article_scientific_fields x ON x.article_id = a.id AND x.is_primary
                     JOIN scientific_fields f ON f.id = x.scientific_field_id
                     WHERE a.id IN ({article_ids_sql})
                     GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40""", (batches,))
    out['articles_by_primary_field'] = [list(r) for r in cur.fetchall()]
    return out


def main(argv):
    args = build_parser().parse_args(argv[1:])
    to_date = args.to_date or date.today()
    stages = {s.strip() for s in args.stages.split(',') if s.strip()}
    if args.limit <= 0 or args.batch_size <= 0 or args.batch_size > 500:
        print("--limit > 0 va 1 <= --batch-size <= 500 bo'lishi kerak.", file=sys.stderr)
        return 2
    if not stages <= {'discover', 'process', 'approve'}:
        print('Noma\'lum bosqich: %s' % ', '.join(sorted(stages)), file=sys.stderr)
        return 2
    if args.execute and not args.admin_id:
        print('--execute uchun --admin-id majburiy.', file=sys.stderr)
        return 2
    config = config_from_env()
    if not config.openalex_api_key:
        print("OPENALEX_API_KEY sozlanmagan. Kalitsiz OpenAlex kunlik limiti juda kichik "
              "— .env ga OPENALEX_API_KEY qo'shing (https://openalex.org/settings/api).",
              file=sys.stderr)
        return 2
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        print('DATABASE_URL sozlanmagan.', file=sys.stderr)
        return 2

    execute = args.execute
    report = {'mode': 'EXECUTE' if execute else 'DRY-RUN',
              'started_at': datetime.now(timezone.utc).isoformat(),
              'filter': {'from_date': args.from_date.isoformat(), 'to_date': to_date.isoformat(),
                         'country': args.country, 'core_only': args.core_only,
                         'limit': args.limit}}
    report['total_count'] = _total_count(config, args, to_date)
    report['budget'] = budget_plan(report['total_count'], args.limit)
    report['rate_limit'] = _rate_limit_status(config)

    print('[%s] OpenAlex mos ishlar: %s; limit: %d'
          % (report['mode'], report['total_count'], args.limit))
    print('Budjet: %s' % json.dumps(report['budget'], ensure_ascii=False))
    print('OpenAlex /rate-limit: %s' % json.dumps(report['rate_limit'], ensure_ascii=False,
                                                    default=str)[:600])
    if not report['budget']['fits_in_one_day']:
        print('DIQQAT: kunlik budjetga sig\'maydi — %d kunga bo\'ling (kuniga ~%d).'
              % (report['budget']['days_needed'], report['budget']['per_day_limit_suggestion']))

    exit_code = 0
    conn = psycopg2.connect(db_url)
    try:
        if not execute:
            conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute("""SELECT 1 FROM information_schema.columns
                            WHERE table_name = 'scientific_articles'
                              AND column_name = 'ingest_evidence'""")
            if cur.fetchone() is None:
                print('migrations/025_article_uz_ingest.sql qo\'llanmagan (ingest_evidence yo\'q).',
                      file=sys.stderr)
                return 2
            try:
                if 'discover' in stages:
                    stage_discover(conn, cur, config, args, to_date, execute, report)
                    print('Discovery: %s' % report['discover'])
                if execute and 'process' in stages:
                    stage_process(conn, cur, report)
                    print('Metadata: %s' % report['process'])
                if execute and 'approve' in stages:
                    stage_approve(conn, cur, args, report)
                    print('Approve: %s' % report['approve'])
                report['summary'] = summary_from_db(cur)
                if execute:
                    conn.commit()
                else:
                    conn.rollback()
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                report['error'] = '%s: %s' % (type(exc).__name__, str(exc)[:300])
                print('XATO: %s' % report['error'], file=sys.stderr)
                exit_code = 1
    finally:
        conn.close()

    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    path = args.report or os.path.join(
        ROOT, 'reports', 'ingest_uz_%s.json' % datetime.now().strftime('%Y%m%d_%H%M%S'))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1, default=str)
    print('\n== Yakuniy holat (barcha CLI batchlar) ==')
    print(json.dumps(report.get('summary') or {}, ensure_ascii=False, indent=1, default=str))
    print('JSON: %s' % path)
    return exit_code


if __name__ == '__main__':
    sys.exit(main(sys.argv))
