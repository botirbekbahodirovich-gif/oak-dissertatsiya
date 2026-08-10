# -*- coding: utf-8 -*-
"""DB qatlami — scheduled article discovery (Phase 2D.3).

`services/article_import/persistence.py` / `services/article_sync/
scheduler_persistence.py` bilan bir xil naqsh: xom psycopg2 kursor, ORM
yo'q, sxema lazy + idempotent (migrations/018 bilan AYNAN bir xil DDL).
"""
from __future__ import annotations

import calendar
import json
from datetime import datetime, timedelta, timezone

from services.article_discovery.scheduler_state import (FREQUENCIES,
                                                         FREQUENCY_DAILY,
                                                         FREQUENCY_MONTHLY,
                                                         FREQUENCY_WEEKLY,
                                                         RUN_RUNNING)

_schema_ready = False

# Band qilingan (`FOR UPDATE SKIP LOCKED`) qatorga vaqtincha qo'yiladigan
# "in-flight" belgisi — agar jarayon ijro paytida halokatga uchrasa
# (masalan gunicorn worker o'ldirilsa), qator abadiy "doim due" bo'lib
# QOLMAYDI, lekin darhol qayta ham olinmaydi: keyingi tsikl(lar) bu vaqt
# o'tgach tabiiy ravishda uni qayta ko'rib chiqadi (§5 crash-safety).
_CLAIM_HOLD_MINUTES = 60


def ensure_schema(cur):
    """migrations/018 bilan AYNAN bir xil DDL, idempotent (parity test).

    `article_discovery_saved_queries`ning rejalashtirish ustunlari
    `saved_queries.ensure_schema()`ning O'ZIDA (u jadvalning yagona egasi,
    §18 — dublikat DDL yo'q) — bu funksiya avval o'shani chaqiradi (bazaviy
    jadval mavjudligini kafolatlash uchun, `ALTER TABLE ... ADD COLUMN`
    jadval yo'q bo'lsa xato beradi), keyin FAQAT o'zining yangi
    `article_discovery_runs` jadvalini yaratadi.
    """
    global _schema_ready
    if _schema_ready:
        return
    from services.article_discovery import saved_queries
    saved_queries.ensure_schema(cur)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS article_discovery_runs (
            id                SERIAL PRIMARY KEY,
            saved_query_id    INTEGER      NOT NULL
                REFERENCES article_discovery_saved_queries(id) ON DELETE CASCADE,
            trigger_type      VARCHAR(16)  NOT NULL DEFAULT 'manual',
            status            VARCHAR(24)  NOT NULL DEFAULT 'running',
            started_at        TIMESTAMP    NOT NULL DEFAULT NOW(),
            finished_at       TIMESTAMP,
            providers         JSONB,
            found_count       INTEGER      NOT NULL DEFAULT 0,
            new_count         INTEGER      NOT NULL DEFAULT 0,
            queued_count      INTEGER      NOT NULL DEFAULT 0,
            duplicate_count   INTEGER      NOT NULL DEFAULT 0,
            no_doi_count      INTEGER      NOT NULL DEFAULT 0,
            error_summary     TEXT,
            import_batch_id   INTEGER
                REFERENCES article_import_batches(id) ON DELETE SET NULL
        )""")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_article_discovery_runs_saved_query "
        "ON article_discovery_runs (saved_query_id, started_at DESC)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_article_discovery_runs_started "
        "ON article_discovery_runs (started_at DESC)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_article_discovery_runs_status "
        "ON article_discovery_runs (status)")
    _schema_ready = True


def _dict_rows(cur):
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _dict_row(cur):
    if cur.description is None:
        return None
    row = cur.fetchone()
    if row is None:
        return None
    cols = [c[0] for c in cur.description]
    return dict(zip(cols, row))


# ── Next-run hisoblash (§8) ───────────────────────────────────────────────────

def compute_next_run_at(frequency, *, from_time=None):
    """Keyingi rejalashtirilgan vaqt — DOIM `from_time`dan (odatda "hozir",
    run TUGAGANDAN keyin) keyingi BIRINCHI voqea.

    "Catch-up storm" oldini olish (§8): agar scheduler kunlab/haftalab
    o'chiq bo'lgan bo'lsa ham, o'tkazib yuborilgan har bir voqea QAYTA
    ISHLANMAYDI — navbatdagi FUTURE voqea shu funksiya chaqirilgan real
    vaqtdan hisoblanadi, o'tmishdagi `next_run_at`dan EMAS. Bu bir marta
    ishlaydi, keyin kelajakka intervalni qo'shadi — hech qachon zanjir bilan
    orqaga qolgan voqealarni bittalab bajarmaydi.

    Oylik chastota KALENDAR oyi bo'yicha (+30 kun EMAS) — bir necha oy
    davomida sana siljib ketishining (drift) oldini oladi; oy oxiri
    kunlarida qisqaroq oyga moslashadi (31-yanvar -> 28/29-fevral).
    """
    if frequency not in FREQUENCIES:
        raise ValueError('unknown frequency: %r' % frequency)
    base = from_time or datetime.now(timezone.utc)
    if frequency == FREQUENCY_DAILY:
        return base + timedelta(days=1)
    if frequency == FREQUENCY_WEEKLY:
        return base + timedelta(weeks=1)
    return _add_calendar_month(base)


def _add_calendar_month(dt):
    year = dt.year + (dt.month // 12)
    month = dt.month % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


# ── Rejalashtirish maydonlari (saved_queries jadvalida) ─────────────────────

def set_schedule(cur, query_id, *, enabled, frequency):
    """Rejalashtirishni yoqadi/o'chiradi. Yoqilganda `next_run_at = NOW()`
    (darhol due) — admin "Enable" dan keyin operator qo'lda scheduler'ni bir
    marta ishga tushirib tekshirishi mumkin (§25 rollout jarayoni), keyingi
    haqiqiy tsikllar tabiiy `next_run_at`ni oldinga suradi.

    O'chirilganda `schedule_frequency`/`next_run_at` NULL qilinadi — "off"
    holati chalkash bo'lmasin (masalan eski chastota qolib ketmasin)."""
    ensure_schema(cur)
    if enabled:
        cur.execute(
            """UPDATE article_discovery_saved_queries SET
                  schedule_enabled = TRUE, schedule_frequency = %s,
                  next_run_at = NOW(), scheduled_updated_at = NOW()
               WHERE id = %s RETURNING id""",
            (frequency, query_id))
    else:
        cur.execute(
            """UPDATE article_discovery_saved_queries SET
                  schedule_enabled = FALSE, schedule_frequency = NULL,
                  next_run_at = NULL, scheduled_updated_at = NOW()
               WHERE id = %s RETURNING id""",
            (query_id,))
    return cur.fetchone() is not None


def record_run_outcome(cur, query_id, *, trigger_type, status, error_summary,
                       next_run_at):
    """Run tugagandan keyin saqlangan so'rov qatoridagi "tezkor ko'rish"
    maydonlarini yangilaydi (`last_run_status`/`last_run_error`, va FAQAT
    scheduled trigger uchun `last_scheduled_run_at`/`next_run_at` — §9:
    qo'lda "Run" `next_run_at`ni kutilmagan holda surmasligi kerak)."""
    from services.article_discovery.scheduler_state import TRIGGER_SCHEDULED
    if trigger_type == TRIGGER_SCHEDULED:
        cur.execute(
            """UPDATE article_discovery_saved_queries SET
                  last_run_status = %s, last_run_error = %s,
                  last_scheduled_run_at = NOW(), next_run_at = %s
               WHERE id = %s""",
            (status, error_summary, next_run_at, query_id))
    else:
        cur.execute(
            """UPDATE article_discovery_saved_queries SET
                  last_run_status = %s, last_run_error = %s
               WHERE id = %s""",
            (status, error_summary, query_id))


# ── Claiming (§5) ────────────────────────────────────────────────────────────

def claim_due_queries(cur, limit):
    """Due (`schedule_enabled AND is_active AND next_run_at <= NOW()`)
    saqlangan so'rovlarni ATOMIK band qiladi.

    `FOR UPDATE SKIP LOCKED` — `services/article_import/
    persistence.py:fetch_pending_chunk` bilan AYNAN bir xil naqsh: ikkita
    parallel worker/tsikl BIR XIL qatorni bir vaqtda band qila olmaydi,
    ikkinchisi shunchaki uni ro'yxatdan o'tkazib yuboradi (SKIP), bloklanib
    kutmaydi (§5 — "Two workers must NOT execute the same scheduled query
    simultaneously").

    Band qilingandan so'ng `next_run_at` DARHOL uzoq kelajakka (`_CLAIM_HOLD_
    MINUTES`) suriladi — shu tranzaksiya COMMIT bo'lgach, boshqa worker endi
    bu qatorni "due" deb topmaydi, hatto haqiqiy provider so'rovi hali
    boshlanmagan bo'lsa ham. Haqiqiy natija (`record_run_outcome`) keyinroq,
    ALOHIDA tranzaksiyada, bu vaqtinchalik qiymatni YAKUNIY hisoblangan
    `next_run_at` bilan almashtiradi.
    """
    ensure_schema(cur)
    cur.execute(
        """SELECT id FROM article_discovery_saved_queries
           WHERE schedule_enabled AND is_active AND next_run_at <= NOW()
           ORDER BY next_run_at ASC
           LIMIT %s FOR UPDATE SKIP LOCKED""", (limit,))
    ids = [row[0] for row in cur.fetchall()]
    if ids:
        cur.execute(
            """UPDATE article_discovery_saved_queries
               SET next_run_at = NOW() + (%s || ' minutes')::interval
               WHERE id = ANY(%s)""",
            (_CLAIM_HOLD_MINUTES, ids))
    return ids


def list_due_queries_preview(cur, limit):
    """Faqat KO'RISH — band qilmaydi, hech narsa yozmaydi (dry-run uchun
    xavfsiz, operator scheduler ishga tushirmasdan "kim due" ko'rishi
    mumkin)."""
    ensure_schema(cur)
    cur.execute(
        """SELECT id, name FROM article_discovery_saved_queries
           WHERE schedule_enabled AND is_active AND next_run_at <= NOW()
           ORDER BY next_run_at ASC LIMIT %s""", (limit,))
    return _dict_rows(cur)


# ── Run audit (article_discovery_runs) ───────────────────────────────────────

def create_run(cur, saved_query_id, trigger_type, providers):
    ensure_schema(cur)
    cur.execute(
        """INSERT INTO article_discovery_runs
              (saved_query_id, trigger_type, status, providers)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (saved_query_id, trigger_type, RUN_RUNNING, json.dumps(list(providers or []))))
    return cur.fetchone()[0]


def finalize_run(cur, run_id, outcome):
    """`outcome` — `services.article_discovery.service.SavedQueryRunResult`."""
    ensure_schema(cur)
    cur.execute(
        """UPDATE article_discovery_runs SET
              status = %s, finished_at = NOW(), found_count = %s, new_count = %s,
              queued_count = %s, duplicate_count = %s, no_doi_count = %s,
              error_summary = %s, import_batch_id = %s
           WHERE id = %s""",
        (outcome.status, outcome.found_count, outcome.new_count,
         outcome.queued_count, outcome.duplicate_count, outcome.no_doi_count,
         _bounded(outcome.error_summary), outcome.import_batch_id, run_id))


def list_recent_runs(cur, *, saved_query_id=None, limit=10):
    ensure_schema(cur)
    if saved_query_id is not None:
        cur.execute(
            """SELECT r.*, q.name AS saved_query_name
                 FROM article_discovery_runs r
                 JOIN article_discovery_saved_queries q ON q.id = r.saved_query_id
                WHERE r.saved_query_id = %s
                ORDER BY r.started_at DESC LIMIT %s""",
            (saved_query_id, limit))
    else:
        cur.execute(
            """SELECT r.*, q.name AS saved_query_name
                 FROM article_discovery_runs r
                 JOIN article_discovery_saved_queries q ON q.id = r.saved_query_id
                ORDER BY r.started_at DESC LIMIT %s""",
            (limit,))
    return _dict_rows(cur)


def _bounded(text, limit=500):
    """Xato matni — cheklangan, sirlarsiz (mavjud `article_import/
    persistence.py:_bounded` bilan bir xil naqsh)."""
    if not text:
        return None
    return str(text)[:limit]
