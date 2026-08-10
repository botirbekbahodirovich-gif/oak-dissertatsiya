# -*- coding: utf-8 -*-
"""Scheduled article discovery — run holati, trigger va chastota
konstantalari (Phase 2D.3).

`services/article_sync/scheduler_state.py` bilan bir xil naqsh: status/
trigger matni CLI/route/shablon bo'ylab qo'lda takrorlanmasin, bitta joyda
saqlanadi.
"""
from __future__ import annotations

# ── Run holati (`article_discovery_runs.status`,
#    `article_discovery_saved_queries.last_run_status`) ────────────────────
RUN_RUNNING = 'running'
RUN_SUCCESS = 'success'
RUN_PARTIAL = 'partial'
RUN_FAILED = 'failed'
RUN_SKIPPED = 'skipped'
RUN_STATUSES = (RUN_RUNNING, RUN_SUCCESS, RUN_PARTIAL, RUN_FAILED, RUN_SKIPPED)

RUN_STATUS_LABELS = {
    RUN_RUNNING: 'Jarayonda',
    RUN_SUCCESS: 'Muvaffaqiyatli',
    RUN_PARTIAL: "Qisman muvaffaqiyatli",
    RUN_FAILED: 'Muvaffaqiyatsiz',
    RUN_SKIPPED: "O'tkazib yuborildi",
}

# ── Trigger turi ─────────────────────────────────────────────────────────────
TRIGGER_MANUAL = 'manual'
TRIGGER_SCHEDULED = 'scheduled'
TRIGGER_TYPES = (TRIGGER_MANUAL, TRIGGER_SCHEDULED)

# ── Chastota — FAQAT shu uchtasi (§2: ixtiyoriy cron ifodasi YO'Q) ──────────
FREQUENCY_DAILY = 'daily'
FREQUENCY_WEEKLY = 'weekly'
FREQUENCY_MONTHLY = 'monthly'
FREQUENCIES = (FREQUENCY_DAILY, FREQUENCY_WEEKLY, FREQUENCY_MONTHLY)

FREQUENCY_LABELS = {
    FREQUENCY_DAILY: 'Kunlik',
    FREQUENCY_WEEKLY: 'Haftalik',
    FREQUENCY_MONTHLY: 'Oylik',
}

# `pg_try_advisory_lock(hashtext(...))` kaliti — `services/article_sync/
# scheduler_state.py:SCHEDULER_LOCK_KEY`dan ATAYIN BOSHQA qiymat (ikkita
# mustaqil scheduler bir-birini bloklamasligi kerak — metadata sync va
# discovery scheduler alohida resurslar ustida ishlaydi). Global, bitta
# vaqtda faqat BITTA discovery scheduler tsikli ishlasin (§5). Session-
# scoped: process/connection to'xtasa Postgres o'zi bo'shatadi.
SCHEDULER_LOCK_KEY = 'article_discovery_scheduler'
