# -*- coding: utf-8 -*-
"""Rejalashtirilgan (scheduled) metadata sinxronizatsiyasi — run holati va
trigger konstantalari (Phase 2C.3C).

`services/article_sync/state.py`dagi kabi naqsh: status matni CLI/route/
docs bo'ylab qo'lda takrorlanmasin, bitta joyda saqlanadi.
"""
from __future__ import annotations

# ── Run holati (`article_metadata_sync_runs.status`) ────────────────────────
RUN_RUNNING = 'running'
RUN_COMPLETED = 'completed'
RUN_COMPLETED_WITH_ERRORS = 'completed_with_errors'
RUN_FAILED = 'failed'
RUN_STATUSES = (RUN_RUNNING, RUN_COMPLETED, RUN_COMPLETED_WITH_ERRORS, RUN_FAILED)

# ── Trigger turi ─────────────────────────────────────────────────────────────
TRIGGER_SCHEDULED = 'scheduled'
TRIGGER_MANUAL_CLI = 'manual_cli'
TRIGGER_TYPES = (TRIGGER_SCHEDULED, TRIGGER_MANUAL_CLI)

# `pg_try_advisory_lock(hashtext(...))` kaliti — global, bitta vaqtda faqat
# BITTA scheduler run ishlasin (§14). Session-scoped: process/connection
# to'xtasa Postgres o'zi bo'shatadi (crash-safe — §23: run audit qatori
# 'running'da qolib ketishi mumkin, lekin LOCK baribir bo'shaydi).
SCHEDULER_LOCK_KEY = 'article_metadata_scheduler'
