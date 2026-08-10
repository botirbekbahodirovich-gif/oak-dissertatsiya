# -*- coding: utf-8 -*-
"""Scheduled article discovery orkestratsiyasi (Phase 2D.3).

    cron / operator -> scripts/run_scheduled_article_discovery.py ->
    BU MODUL -> global advisory lock -> due saqlangan so'rovlarni
    FOR UPDATE SKIP LOCKED bilan band qilish -> har biri uchun
    services.article_discovery.service.run_saved_query() (mavjud
    multi-provider qidiruv + klassifikatsiya + enqueue + import chunk
    drayveri) -> article_discovery_runs + saved_queries schedule maydonlari.

Flask'ga BOG'LIQ EMAS — xom psycopg2 connection oladi
(`services/article_sync/scheduler.py` bilan bir xil naqsh). PARALLEL
discovery/import pipeline YO'Q — bu modul FAQAT mavjud
`service.run_saved_query()`ni orkestratsiya qiladi (§4, §11).

Ikki qatlamli concurrency himoyasi (§5):
  1. Global `pg_try_advisory_lock` — bitta vaqtda faqat BITTA scheduler
     TSIKLI ishlaydi (`services/article_sync/scheduler.py` bilan bir xil
     konvensiya, boshqa lock kaliti bilan).
  2. `FOR UPDATE SKIP LOCKED` band qilish (`scheduler_persistence.
     claim_due_queries`) — `services/article_import/persistence.py:
     fetch_pending_chunk` bilan bir xil naqsh, ikkinchi mustaqil himoya
     qatlami (masalan lock kutilmagan holda bo'shab qolsa ham).

Scheduler != auto-approve, != publisher, != PDF downloader (§10): bu yerda
review-navbatidan tashqariga chiqadigan HECH QANDAY yo'l yo'q.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from services.article_discovery import saved_queries as sq_module
from services.article_discovery import scheduler_persistence
from services.article_discovery.scheduler_config import (DiscoverySchedulerConfig,
                                                          scheduler_config_from_env)
from services.article_discovery.scheduler_state import (RUN_FAILED, RUN_SKIPPED,
                                                         SCHEDULER_LOCK_KEY,
                                                         TRIGGER_SCHEDULED)
from services.article_discovery.service import run_saved_query


class SchedulerAlreadyRunningError(RuntimeError):
    """Boshqa scheduler tsikli allaqachon global lock'ni ushlab turibdi (§5)."""


@dataclass
class QueryRunOutcome:
    saved_query_id: int
    saved_query_name: str | None
    status: str
    run_id: int | None = None
    error_summary: str | None = None


@dataclass
class SchedulerCycleResult:
    dry_run: bool
    due: list = field(default_factory=list)          # dry-run: [{'id','name'}]
    claimed: int = 0
    results: list = field(default_factory=list)       # [QueryRunOutcome, ...]


def _try_acquire_lock(cur):
    cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (SCHEDULER_LOCK_KEY,))
    return bool(cur.fetchone()[0])


def _release_lock(cur):
    cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (SCHEDULER_LOCK_KEY,))


def select_due_saved_queries(conn, config=None):
    """Faqat due ro'yxatni ko'rsatadi — band qilmaydi, hech narsa yozmaydi
    (dry-run va operator ko'rish uchun xavfsiz)."""
    config = config or scheduler_config_from_env()
    with conn.cursor() as cur:
        scheduler_persistence.ensure_schema(cur)
        conn.commit()
        due = scheduler_persistence.list_due_queries_preview(cur, config.max_queries)
    return due


def run_due_discovery_queries(conn, *, config=None, dry_run=False,
                              limit_override=None, searchers=None,
                              import_config=None, fetch=None):
    """Bitta scheduler tsikli — bounded, bitta due-so'rov muvaffaqiyatsiz
    bo'lsa ham navbatdagilar davom etadi (§7 — failure isolation).

    Har saqlangan so'rov ALOHIDA tranzaksiyada ishlanadi (band qilish ham
    o'z tranzaksiyasida, keyin darhol COMMIT — §5: HTTP so'rov paytida
    qulf/DB tranzaksiya ochiq TURMAYDI).
    """
    config = config or scheduler_config_from_env()
    effective_config = config
    if limit_override is not None:
        effective_config = DiscoverySchedulerConfig(
            enabled=config.enabled, interval_minutes=config.interval_minutes,
            max_queries=limit_override)

    if dry_run:
        due = select_due_saved_queries(conn, effective_config)
        return SchedulerCycleResult(dry_run=True, due=due)

    with conn.cursor() as cur:
        acquired = _try_acquire_lock(cur)
        conn.commit()
    if not acquired:
        raise SchedulerAlreadyRunningError(
            "Boshqa discovery scheduler tsikli allaqachon ishlamoqda.")

    try:
        with conn.cursor() as cur:
            claimed_ids = scheduler_persistence.claim_due_queries(
                cur, effective_config.max_queries)
            conn.commit()

        results = []
        for query_id in claimed_ids:
            results.append(_run_one_claimed_query(
                conn, query_id, searchers=searchers, import_config=import_config,
                fetch=fetch))
        return SchedulerCycleResult(dry_run=False, claimed=len(claimed_ids),
                                    results=results)
    finally:
        with conn.cursor() as cur:
            _release_lock(cur)
            conn.commit()


def _run_one_claimed_query(conn, query_id, *, searchers=None, import_config=None,
                           fetch=None):
    """Bitta band qilingan so'rovni ishlaydi. Har qanday xato shu funksiya
    ICHIDA ushlanadi va keyingi so'rovga o'tishga to'sqinlik qilmaydi (§7)."""
    try:
        with conn.cursor() as cur:
            row = sq_module.get(cur, query_id)
            if row is None or not row['is_active'] or not row['schedule_enabled']:
                # Poyga holati: band qilingandan keyin, ishga tushirilgunga
                # qadar so'rov faolsizlantirilgan/rejalashtirish o'chirilgan
                # bo'lishi mumkin — bu xato EMAS, shunchaki o'tkazib
                # yuborish (§7 status='skipped').
                conn.commit()
                return QueryRunOutcome(saved_query_id=query_id, saved_query_name=None,
                                       status=RUN_SKIPPED)
            run_id = scheduler_persistence.create_run(
                cur, query_id, TRIGGER_SCHEDULED, row.get('providers'))
            conn.commit()
    except Exception:
        conn.rollback()
        return QueryRunOutcome(saved_query_id=query_id, saved_query_name=None,
                               status=RUN_FAILED, error_summary='ichki xato (band qilish)')

    try:
        with conn.cursor() as cur:
            outcome = run_saved_query(cur, row, trigger_type=TRIGGER_SCHEDULED,
                                      searchers=searchers, run_id=run_id,
                                      import_config=import_config, fetch=fetch)
            scheduler_persistence.finalize_run(cur, run_id, outcome)
            next_run_at = scheduler_persistence.compute_next_run_at(
                row['schedule_frequency'])
            scheduler_persistence.record_run_outcome(
                cur, query_id, trigger_type=TRIGGER_SCHEDULED, status=outcome.status,
                error_summary=outcome.error_summary, next_run_at=next_run_at)
            conn.commit()
        return QueryRunOutcome(saved_query_id=query_id, saved_query_name=row['name'],
                               run_id=run_id, status=outcome.status,
                               error_summary=outcome.error_summary)
    except Exception:
        conn.rollback()
        # Qulf bo'shashi kerak — keyingi tsikl bu so'rovni qayta ko'radi,
        # lekin darhol emas (`_CLAIM_HOLD_MINUTES`, scheduler_persistence.py).
        try:
            with conn.cursor() as cur:
                next_run_at = scheduler_persistence.compute_next_run_at(
                    row['schedule_frequency'])
                scheduler_persistence.finalize_run(
                    cur, run_id,
                    _FailedOutcome('ichki xato (bajarilish)'))
                scheduler_persistence.record_run_outcome(
                    cur, query_id, trigger_type=TRIGGER_SCHEDULED, status=RUN_FAILED,
                    error_summary='ichki xato (bajarilish)', next_run_at=next_run_at)
                conn.commit()
        except Exception:
            conn.rollback()
        return QueryRunOutcome(saved_query_id=query_id, saved_query_name=row['name'],
                               run_id=run_id, status=RUN_FAILED,
                               error_summary='ichki xato (bajarilish)')


class _FailedOutcome:
    """`finalize_run` `SavedQueryRunResult`ning shape'ini kutadi — kutilmagan
    istisno holatida haqiqiy natija yo'q, faqat minimal placeholder kerak."""

    def __init__(self, error_summary):
        self.status = RUN_FAILED
        self.found_count = 0
        self.new_count = 0
        self.queued_count = 0
        self.duplicate_count = 0
        self.no_doi_count = 0
        self.error_summary = error_summary
        self.import_batch_id = None


def list_recent_runs(conn_or_cur, *, saved_query_id=None, limit=10):
    """Admin UI "So'nggi run'lar" bo'limi uchun (§16). Kursur yoki
    connection ikkalasini ham qabul qiladi."""
    cur = conn_or_cur
    scheduler_persistence.ensure_schema(cur)
    return scheduler_persistence.list_recent_runs(
        cur, saved_query_id=saved_query_id, limit=limit)
