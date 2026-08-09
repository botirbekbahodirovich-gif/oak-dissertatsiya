# -*- coding: utf-8 -*-
"""Rejalashtirilgan (scheduled) metadata sinxronizatsiyasi orkestratsiyasi
(Phase 2C.3C).

    cron / operator -> scripts/sync_article_metadata.py -> BU MODUL ->
    global lock -> due DOI maqolalar -> bounded batch ->
    check_article_metadata(actor_type='system') -> article_metadata_syncs ->
    2C.3B dashboard.

Flask'ga BOG'LIQ EMAS — xom psycopg2 connection oladi (`scripts/
sync_article_metadata.py` buni to'g'ridan-to'g'ri chaqiradi, xuddi admin route
`check_article_metadata()`ni chaqirgani kabi). PARALLEL metadata/diff pipeline
YO'Q — bu modul faqat mavjud `services.article_sync.service.
check_article_metadata()`ni orkestratsiya qiladi (§3, §36).

Scheduler != auto-apply, != publisher, != PDF downloader: bu yerda
`apply_article_metadata_changes()` HECH QACHON chaqirilmaydi — faqat check.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from services.article_sync import persistence as sync_persistence
from services.article_sync import scheduler_persistence
from services.article_sync.scheduler_config import (SchedulerConfig,
                                                     scheduler_config_from_env)
from services.article_sync.scheduler_state import (RUN_COMPLETED,
                                                    RUN_COMPLETED_WITH_ERRORS,
                                                    RUN_FAILED, SCHEDULER_LOCK_KEY,
                                                    TRIGGER_SCHEDULED)
from services.article_sync.service import ArticleSyncError, check_article_metadata
from services.article_sync.state import ACTOR_SYSTEM
from services.article_metadata import fetch_article_metadata


class SchedulerAlreadyRunningError(RuntimeError):
    """Boshqa scheduler run allaqachon global lock'ni ushlab turibdi (§14)."""


@dataclass
class SchedulerRunResult:
    dry_run: bool
    trigger_type: str
    total_selected: int
    article_ids: list = field(default_factory=list)
    run_id: int | None = None
    status: str | None = None
    processed: int = 0
    success: int = 0
    partial: int = 0
    failed: int = 0
    skipped: int = 0


def _try_acquire_lock(cur):
    cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (SCHEDULER_LOCK_KEY,))
    return bool(cur.fetchone()[0])


def _release_lock(cur):
    cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (SCHEDULER_LOCK_KEY,))


def select_due_articles(conn, config=None, *, article_id=None):
    """Faqat due tanlash — lock/run yozuvisiz (dry-run va operator ko'rish
    uchun xavfsiz, hech narsa yozmaydi)."""
    config = config or scheduler_config_from_env()
    with conn.cursor() as cur:
        sync_persistence.ensure_schema(cur)
        conn.commit()
        due_ids = scheduler_persistence.select_due_article_ids(
            cur, config.interval_days, config.batch_size, article_id=article_id)
    return due_ids


def _classify(result):
    """(sync natijasi) -> 'success' | 'partial' | 'failed' — provayder
    holatidan, DB'ga qayta murojaat qilmasdan (§17, §18). Mavjud
    `provider_summary`ning `status` maydonidan (`services.article_metadata.
    models.ProviderResult` — 'ok' | 'not_found' | 'timeout' | ...) —
    scheduler o'ziga xos error model YARATMAYDI, faqat mavjudini o'qiydi."""
    statuses = [(v or {}).get('status') for v in (result.provider_summary or {}).values()]
    if not statuses or all(s == 'ok' for s in statuses):
        return 'success'
    if any(s == 'ok' for s in statuses):
        return 'partial'
    return 'failed'


def run_scheduled_metadata_sync(conn, *, config=None, dry_run=False,
                                limit_override=None, article_id=None,
                                trigger_type=TRIGGER_SCHEDULED,
                                fetch=fetch_article_metadata,
                                check=check_article_metadata,
                                select_due=select_due_articles):
    """Bitta scheduler yugurishi.

    Har maqola ALOHIDA transaction'da commit qilinadi (§31) — bitta
    timeout/xato qolgan maqolalarni to'xtatmaydi (§17, item-level isolation).
    Hisob invarianti: `total_selected == processed + skipped` va
    `processed == success + partial + failed` (§22) — `skipped` faqat
    tanlangandan keyin maqola ELIGIBLE bo'lmay qolgan holatlar uchun
    (masalan DOI olib tashlangan) — `ArticleSyncError`; `failed` esa
    `check_article_metadata()` TUGALLANGAN, lekin provayderlar/kutilmagan
    xato bilan (server hali ham javob beradi, keyingi maqolaga o'tadi).

    `select_due`/`check`/`fetch` — test uchun almashtiriladigan bog'liqliklar
    (xuddi `check_article_metadata(..., fetch=...)`dagi kabi naqsh).
    """
    config = config or scheduler_config_from_env()
    effective_config = config
    if limit_override is not None:
        effective_config = SchedulerConfig(enabled=config.enabled,
                                           interval_days=config.interval_days,
                                           batch_size=limit_override)

    due_ids = select_due(conn, effective_config, article_id=article_id)

    if dry_run:
        return SchedulerRunResult(dry_run=True, trigger_type=trigger_type,
                                  total_selected=len(due_ids), article_ids=due_ids)

    with conn.cursor() as cur:
        acquired = _try_acquire_lock(cur)
        conn.commit()
    if not acquired:
        raise SchedulerAlreadyRunningError(
            "Boshqa metadata sinxronizatsiya jarayoni allaqachon ishlamoqda.")

    counts = {'processed': 0, 'success': 0, 'partial': 0, 'failed': 0, 'skipped': 0}
    run_id = None
    try:
        with conn.cursor() as cur:
            scheduler_persistence.ensure_schema(cur)
            run_id = scheduler_persistence.create_run(cur, trigger_type, len(due_ids))
            conn.commit()

        for aid in due_ids:
            try:
                with conn.cursor() as cur:
                    result = check(cur, aid, None, actor_type=ACTOR_SYSTEM, fetch=fetch)
                    conn.commit()
                outcome = _classify(result)
            except ArticleSyncError:
                conn.rollback()
                counts['skipped'] += 1
                continue
            except Exception:
                conn.rollback()
                counts['processed'] += 1
                counts['failed'] += 1
                continue
            counts['processed'] += 1
            counts[outcome] += 1

        status = RUN_COMPLETED_WITH_ERRORS if counts['failed'] else RUN_COMPLETED
        with conn.cursor() as cur:
            scheduler_persistence.finalize_run(cur, run_id, status, counts)
            conn.commit()
        return SchedulerRunResult(dry_run=False, trigger_type=trigger_type,
                                  total_selected=len(due_ids), article_ids=due_ids,
                                  run_id=run_id, status=status, **counts)
    except Exception:
        if run_id is not None:
            try:
                with conn.cursor() as cur:
                    scheduler_persistence.finalize_run(cur, run_id, RUN_FAILED, counts)
                    conn.commit()
            except Exception:
                conn.rollback()
        raise
    finally:
        with conn.cursor() as cur:
            _release_lock(cur)
            conn.commit()


def list_recent_runs(conn_or_cur, limit=5):
    """Dashboard "Oxirgi avtomatik tekshiruv" kartasi uchun (§27). Kursur
    yoki connection ikkalasini ham qabul qiladi (route bilan bir xil ochiq
    kursur naqshiga mos)."""
    cur = conn_or_cur
    scheduler_persistence.ensure_schema(cur)
    return scheduler_persistence.list_recent_runs(cur, limit=limit)
