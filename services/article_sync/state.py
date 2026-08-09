# -*- coding: utf-8 -*-
"""Dashboard queue holati — yagona haqiqat manbai (Phase 2C.3B, §6).

`article_metadata_syncs.status` xom ustuni faqat 3 qiymat oladi:
`checked | applied | dismissed` (§12 — yozuvchi tomon). Bulardan tashqari
"no_changes", "needs_review", "critical", "stale", "provider_failed" —
DB'da SAQLANMAYDI, chunki ular allaqachon saqlangan hisoblagichlar
(`change_count`, `critical_change_count`, `has_provider_issue`) va
`article.updated_at` solishtiruvidan DETERMINISTIK chiqariladi (xuddi
`services/article_import/state.py:derive_batch_status` bilan bir xil
naqsh — drift bo'lmasin uchun ikki marta saqlanmaydi).

Route/shablon ushbu modulning `derive_queue_state()`/`QUEUE_STATE_LABELS`
dan TASHQARI hech qanday holat matnini o'zi yaratmaydi.
"""
from __future__ import annotations

# ── Xom DB ustuni (`article_metadata_syncs.status`) ─────────────────────────
CHECKED = 'checked'
APPLIED = 'applied'
DISMISSED = 'dismissed'
RAW_STATUSES = (CHECKED, APPLIED, DISMISSED)

# ── Actor turi ───────────────────────────────────────────────────────────────
ACTOR_ADMIN = 'admin'
ACTOR_SYSTEM = 'system'
ACTOR_AGENT = 'agent'
ACTOR_TYPES = (ACTOR_ADMIN, ACTOR_SYSTEM, ACTOR_AGENT)

# ── Dashboard queue holati — DERIVED, DB'da yo'q ─────────────────────────────
NO_CHANGES = 'no_changes'
NEEDS_REVIEW = 'needs_review'
CRITICAL = 'critical'
APPLIED_STATE = 'applied'
DISMISSED_STATE = 'dismissed'
STALE = 'stale'

QUEUE_STATE_LABELS = {
    NO_CHANGES: "O'zgarishsiz",
    NEEDS_REVIEW: "Ko'rib chiqish kerak",
    CRITICAL: 'MUHIM',
    APPLIED_STATE: "Qo'llangan",
    DISMISSED_STATE: "Yopilgan (ko'rib chiqilgan)",
    STALE: 'Eskirgan',
}

# Bulk-safe-apply (§16) uchun XAVFSIZ hisoblanadigan queue holati.
BULK_SAFE_QUEUE_STATES = frozenset({NEEDS_REVIEW})


def derive_queue_state(sync_row, *, is_stale=False):
    """Bitta sync qatoridan (dict — DB ustun nomlari bilan) dashboard queue
    holatini chiqaradi. `is_stale` — chaqiruvchi hisoblagan (article.updated_at
    solishtiruvi, servis darajasida — bu funksiya DB'ga tegmaydi)."""
    status = sync_row.get('status')
    if status == APPLIED:
        return APPLIED_STATE
    if status == DISMISSED:
        return DISMISSED_STATE
    # status == 'checked' dan pastda.
    if is_stale:
        return STALE
    if (sync_row.get('critical_change_count') or 0) > 0:
        return CRITICAL
    if (sync_row.get('change_count') or 0) == 0:
        return NO_CHANGES
    return NEEDS_REVIEW


def is_actionable(sync_row, *, is_stale=False):
    """Default review queue'ga kiradimi (§7, §18) — checked, o'zgarishi bor,
    eskirmagan."""
    queue_state = derive_queue_state(sync_row, is_stale=is_stale)
    return queue_state in (NEEDS_REVIEW, CRITICAL)


def validate_actor(actor_type, actor_id):
    """(actor_type, actor_id) — noto'g'ri kombinatsiya bo'lsa ValueError.

    `system`/`agent` uchun `actor_id` odatda `None` (haqiqiy foydalanuvchi
    yo'q, §3); `admin` uchun haqiqiy foydalanuvchi ID majburiy.
    """
    if actor_type not in ACTOR_TYPES:
        raise ValueError("actor_type noto'g'ri: %r" % actor_type)
    if actor_type == ACTOR_ADMIN and not actor_id:
        raise ValueError("actor_type='admin' uchun actor_id majburiy")
    return True
