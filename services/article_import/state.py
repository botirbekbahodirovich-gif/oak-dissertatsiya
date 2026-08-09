# -*- coding: utf-8 -*-
"""Import elementi holat mashinasi (Phase 2C.2, §9–§10).

Yagona haqiqat manbai shu yerda: statuslar istalgancha almashtirilmaydi,
route yoki servis ichida qo'lda string bilan yozilmaydi — har bir o'tish
`transition()` orqali va shu yerdagi jadvalga qarshi tekshiriladi.
"""
from __future__ import annotations

# ── Element (item) statuslari ────────────────────────────────────────────────
PENDING = 'pending'
PROCESSING = 'processing'
READY_FOR_REVIEW = 'ready_for_review'
DUPLICATE_IN_BATCH = 'duplicate_in_batch'
ALREADY_EXISTS = 'already_exists'
INVALID_DOI = 'invalid_doi'
METADATA_NOT_FOUND = 'metadata_not_found'
PARTIAL_METADATA = 'partial_metadata'
PROVIDER_FAILED = 'provider_failed'
RETRACTED = 'retracted'
APPROVED = 'approved'
REJECTED = 'rejected'
SKIPPED = 'skipped'
FAILED = 'failed'

ITEM_STATUSES = (
    PENDING, PROCESSING, READY_FOR_REVIEW, DUPLICATE_IN_BATCH, ALREADY_EXISTS,
    INVALID_DOI, METADATA_NOT_FOUND, PARTIAL_METADATA, PROVIDER_FAILED,
    RETRACTED, APPROVED, REJECTED, SKIPPED, FAILED,
)

# UI matn — status RANG bilan emas, matn bilan ko'rsatiladi (dizayn tizimi §9).
ITEM_STATUS_LABELS = {
    PENDING: 'Navbatda',
    PROCESSING: 'Ishlanmoqda',
    READY_FOR_REVIEW: 'Tekshirishga tayyor',
    DUPLICATE_IN_BATCH: "Ro'yxatda takror",
    ALREADY_EXISTS: 'Bazada mavjud',
    INVALID_DOI: "DOI noto'g'ri",
    METADATA_NOT_FOUND: 'Metadata topilmadi',
    PARTIAL_METADATA: "Qisman ma'lumot",
    PROVIDER_FAILED: 'Manba javob bermadi',
    RETRACTED: "Chaqirib olingan (retracted)",
    APPROVED: 'Tasdiqlangan',
    REJECTED: 'Rad etilgan',
    SKIPPED: "O'tkazib yuborilgan",
    FAILED: 'Xato',
}

# Qayta urinish mumkin bo'lgan statuslar — faqat tarmoq/vaqtinchalik xato.
# Noto'g'ri DOI yoki allaqachon mavjud maqolani "qayta urinish" o'zgartirmaydi.
RETRYABLE_ITEM_STATUSES = frozenset({PROVIDER_FAILED, FAILED})

# "Ko'rib chiqish" navbatida hisoblangan statuslar — batch.review_count shular.
REVIEW_BUCKET_STATUSES = frozenset({READY_FOR_REVIEW, PARTIAL_METADATA, RETRACTED})

# Umuman qayta ishlanadigan (hali yakunlanmagan) statuslar.
IN_FLIGHT_STATUSES = frozenset({PENDING, PROCESSING})

# Bulk "tanlanganlarni tasdiqlash" uchun XAVFSIZ nomzodlar (§37). Retracted,
# litsenziya/kritik konflikt, invalid, already_exists, provider_failed —
# barchasi CHIQARIB tashlanadi; ular faqat bitta-bitta ko'rib chiqiladi.
BULK_APPROVE_SAFE_STATUSES = frozenset({READY_FOR_REVIEW})

# Bitta DOI hali "faol" hisoblanadigan holatlar — ya'ni ular hali BITTA yangi
# maqola yaratishga OLIB KELISHI mumkin (Phase 2D.1 discovery cross-batch
# idempotency, §14-§16). `duplicate_in_batch` / `already_exists` / `invalid_doi`
# ATAYIN chiqarib tashlangan: ular hech qachon endi article yaratmaydi (yoki
# normalized_doi umuman yo'q) — shuning uchun boshqa batch'dagi haqiqiy
# ishlanayotgan nomzod bilan TO'QNASHMASLIGI kerak.
#
# Bu yagona haqiqat manbai: `migrations/014_article_discovery.sql`dagi partial
# unique index predicate'i VA `services/article_import/persistence.py:ensure_schema`
# shu YIG'INDIGA mos bo'lishi shart (parity test —
# `tests/test_article_discovery_migration.py`). Bu yerda o'zgartirilsa, ikkalasi
# ham qo'lda yangilanishi kerak.
ACTIVE_DOI_ITEM_STATUSES = frozenset(
    IN_FLIGHT_STATUSES | REVIEW_BUCKET_STATUSES | RETRYABLE_ITEM_STATUSES
    | {METADATA_NOT_FOUND})

# Har bir statusdan qaysi statusga o'tish mumkin. Approve/reject/skip —
# bularni chaqiruvchi funksiya aniqlaydi (approve faqat REVIEW_BUCKET dan,
# reject/skip har qanday terminal-bo'lmagan statusdan).
ALLOWED_TRANSITIONS = {
    PENDING: {PROCESSING},
    PROCESSING: {READY_FOR_REVIEW, DUPLICATE_IN_BATCH, ALREADY_EXISTS,
                 INVALID_DOI, METADATA_NOT_FOUND, PARTIAL_METADATA,
                 PROVIDER_FAILED, RETRACTED, FAILED},
    READY_FOR_REVIEW: {APPROVED, REJECTED, SKIPPED},
    PARTIAL_METADATA: {APPROVED, REJECTED, SKIPPED},
    RETRACTED: {APPROVED, REJECTED, SKIPPED},
    METADATA_NOT_FOUND: {REJECTED, SKIPPED},
    ALREADY_EXISTS: {SKIPPED},
    INVALID_DOI: {SKIPPED},
    DUPLICATE_IN_BATCH: {SKIPPED},
    PROVIDER_FAILED: {PENDING, SKIPPED},
    FAILED: {PENDING, SKIPPED},
    # Terminal — hech qayerga o'tmaydi.
    APPROVED: set(),
    REJECTED: set(),
    SKIPPED: set(),
}

TERMINAL_ITEM_STATUSES = frozenset(
    status for status, targets in ALLOWED_TRANSITIONS.items() if not targets)


class InvalidTransition(ValueError):
    """Ruxsat etilmagan holat o'tishi so'ralganda."""


def can_transition(current, target):
    return target in ALLOWED_TRANSITIONS.get(current, set())


def assert_transition(current, target):
    """`target` `current`dan ruxsat etilganini tekshiradi, aks holda ko'taradi.

    Bu funksiya route/servis ichida statusni qo'lda string bilan
    almashtirishning oldini oladi (§10) — har bir yozuvchi shu orqali o'tadi.
    """
    if not can_transition(current, target):
        raise InvalidTransition(
            "%r holatidan %r holatiga o'tib bo'lmaydi" % (current, target))


# ── Batch statuslari ─────────────────────────────────────────────────────────
BATCH_DRAFT = 'draft'
BATCH_QUEUED = 'queued'
BATCH_PROCESSING = 'processing'
BATCH_NEEDS_REVIEW = 'needs_review'
BATCH_COMPLETED = 'completed'
BATCH_COMPLETED_WITH_ERRORS = 'completed_with_errors'
BATCH_CANCELLED = 'cancelled'
BATCH_FAILED = 'failed'

BATCH_STATUSES = (BATCH_DRAFT, BATCH_QUEUED, BATCH_PROCESSING, BATCH_NEEDS_REVIEW,
                  BATCH_COMPLETED, BATCH_COMPLETED_WITH_ERRORS, BATCH_CANCELLED,
                  BATCH_FAILED)

BATCH_STATUS_LABELS = {
    BATCH_DRAFT: 'Qoralama',
    BATCH_QUEUED: 'Navbatda',
    BATCH_PROCESSING: 'Ishlanmoqda',
    BATCH_NEEDS_REVIEW: 'Tekshirish kerak',
    BATCH_COMPLETED: 'Yakunlangan',
    BATCH_COMPLETED_WITH_ERRORS: 'Xatolar bilan yakunlangan',
    BATCH_CANCELLED: 'Bekor qilingan',
    BATCH_FAILED: 'Muvaffaqiyatsiz',
}

BATCH_TERMINAL_STATUSES = frozenset({BATCH_COMPLETED, BATCH_COMPLETED_WITH_ERRORS,
                                     BATCH_CANCELLED, BATCH_FAILED})


def derive_batch_status(counts, cancel_requested, started):
    """Batch holatini ELEMENTLAR holatidan DETERMINISTIK hisoblaydi (§7).

    `counts` — {status: soni} lug'ati (item.status bo'yicha GROUP BY natijasi).
    Hisoblagichlarning o'zi ham shu yerdan kelib chiqadi — ular alohida
    increment QILINMAYDI, har bir o'tishdan keyin shu funksiya orqali
    qayta hisoblanadi (drift bo'lmasligi uchun, §7).
    """
    total = sum(counts.values())
    if total == 0:
        return BATCH_DRAFT

    # Bekor qilish — faqat HOZIR bajarilayotgan chunk yo'q bo'lsa darhol
    # kuchga kiradi. `pending` elementlar "navbatda kutmoqda", hali hech
    # narsa ular ustida ISHLAMAYAPTI — shuning uchun ular cancel'ni to'sib
    # turmaydi (§24: joriy elementni majburan to'xtatmaydi, keyingisini
    # boshlamaydi).
    if cancel_requested and counts.get(PROCESSING, 0) == 0:
        return BATCH_CANCELLED
    if not started:
        return BATCH_QUEUED

    in_flight = sum(counts.get(s, 0) for s in IN_FLIGHT_STATUSES)
    review = sum(counts.get(s, 0) for s in REVIEW_BUCKET_STATUSES)
    failed = counts.get(FAILED, 0) + counts.get(PROVIDER_FAILED, 0)

    if in_flight > 0:
        return BATCH_PROCESSING
    if review > 0:
        return BATCH_NEEDS_REVIEW
    if failed > 0:
        return BATCH_COMPLETED_WITH_ERRORS
    return BATCH_COMPLETED
