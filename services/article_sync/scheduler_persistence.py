# -*- coding: utf-8 -*-
"""DB qatlami — `article_metadata_sync_runs` va due-article tanlash
(Phase 2C.3C). Konvensiya `services/article_sync/persistence.py` bilan bir
xil: xom psycopg2 kursor, ORM yo'q, sxema lazy + idempotent.
"""
from __future__ import annotations

from services.article_sync.scheduler_state import RUN_RUNNING

_schema_ready = False


def ensure_schema(cur):
    """migrations/013 bilan bir xil DDL, idempotent."""
    global _schema_ready
    if _schema_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS article_metadata_sync_runs (
            id                  SERIAL PRIMARY KEY,
            started_at          TIMESTAMP    NOT NULL DEFAULT NOW(),
            finished_at         TIMESTAMP,
            status              VARCHAR(24)  NOT NULL DEFAULT 'running',
            trigger_type        VARCHAR(16)  NOT NULL DEFAULT 'manual_cli',
            total_selected      INTEGER      NOT NULL DEFAULT 0,
            processed_count     INTEGER      NOT NULL DEFAULT 0,
            success_count       INTEGER      NOT NULL DEFAULT 0,
            partial_count       INTEGER      NOT NULL DEFAULT 0,
            failed_count        INTEGER      NOT NULL DEFAULT 0,
            skipped_count       INTEGER      NOT NULL DEFAULT 0,
            created_at          TIMESTAMP    NOT NULL DEFAULT NOW()
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_metadata_sync_runs_started "
                "ON article_metadata_sync_runs (started_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_metadata_sync_runs_status "
                "ON article_metadata_sync_runs (status)")
    _schema_ready = True


def _dict_row(cur):
    if cur.description is None:
        return None
    row = cur.fetchone()
    if row is None:
        return None
    cols = [c[0] for c in cur.description]
    return dict(zip(cols, row))


def _dict_rows(cur):
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── Due-article tanlash (§7-§13) ─────────────────────────────────────────────

def select_due_article_ids(cur, interval_days, batch_size, *, article_id=None):
    """DOI bor, arxivlanmagan maqolalar — hech qachon tekshirilmagan
    BIRINCHI, keyin eng ESKI tekshirilgan (`NULLS FIRST`, keyin
    `checked_at ASC`, `article_id ASC` deterministik tie-break — §12, §13).

    Manba — `article_metadata_syncs.checked_at` (har maqola uchun ENG
    SO'NGGI qator, status'idan qat'i nazar): bu "oxirgi URINILGAN tekshiruv".
    `scientific_articles.metadata_last_synced_at` ATAYIN ishlatilmaydi — u
    faqat MUVAFFAQIYATLI `apply`da yangilanadi (`persistence.apply_field_
    changes`), ya'ni "oxirgi MUVAFFAQIYATLI YOZUV" — o'zgarish topilmagan yoki
    hali qo'llanilmagan tekshiruvlar bu ustunga umuman ta'sir qilmaydi. Ikkala
    tushuncha ATAYIN farqlanadi (§8).

    `article_id` berilsa — faqat shu maqola (dev/debug rejimi, §26); due-oyna
    E'TIBORGA OLINMAYDI, lekin DOI/arxiv eligibility filtri baribir qo'llanadi.
    """
    if article_id is not None:
        cur.execute(
            """SELECT a.id
                 FROM scientific_articles a
                WHERE a.id = %s
                  AND a.doi IS NOT NULL AND a.doi <> ''
                  AND a.publication_status <> 'archived'""",
            (article_id,))
        return [row[0] for row in cur.fetchall()]

    cur.execute(
        """SELECT a.id
             FROM scientific_articles a
             LEFT JOIN LATERAL (
                 SELECT MAX(s.checked_at) AS last_checked_at
                   FROM article_metadata_syncs s
                  WHERE s.article_id = a.id
             ) latest ON TRUE
            WHERE a.doi IS NOT NULL AND a.doi <> ''
              AND a.publication_status <> 'archived'
              AND (latest.last_checked_at IS NULL
                   OR latest.last_checked_at <= NOW() - (%s || ' days')::interval)
            ORDER BY latest.last_checked_at ASC NULLS FIRST, a.id ASC
            LIMIT %s""",
        (interval_days, batch_size))
    return [row[0] for row in cur.fetchall()]


# ── Run audit (§22-§23) ──────────────────────────────────────────────────────

def create_run(cur, trigger_type, total_selected):
    cur.execute(
        """INSERT INTO article_metadata_sync_runs
              (status, trigger_type, total_selected)
           VALUES (%s, %s, %s) RETURNING id""",
        (RUN_RUNNING, trigger_type, total_selected))
    return cur.fetchone()[0]


def finalize_run(cur, run_id, status, counts):
    cur.execute(
        """UPDATE article_metadata_sync_runs
              SET status = %s, finished_at = NOW(),
                  processed_count = %s, success_count = %s,
                  partial_count = %s, failed_count = %s, skipped_count = %s
            WHERE id = %s""",
        (status, counts['processed'], counts['success'], counts['partial'],
         counts['failed'], counts['skipped'], run_id))


def list_recent_runs(cur, limit=5):
    """Eng so'nggi run'lar — dashboard "Oxirgi avtomatik tekshiruv" kartasi
    uchun (§27), eng yangisi birinchi."""
    cur.execute(
        """SELECT * FROM article_metadata_sync_runs
            ORDER BY started_at DESC LIMIT %s""", (limit,))
    return _dict_rows(cur)
