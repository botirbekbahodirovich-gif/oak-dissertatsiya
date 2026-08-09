# -*- coding: utf-8 -*-
"""Saqlangan qidiruv so'rovlari (Phase 2D.2, §9-§11).

"Saqlash" FAQAT "shu qidiruv konfiguratsiyasini eslab qol" degani —
"avtomatik ishga tushir" EMAS (rejalashtirilgan/cron discovery — Phase 2D.3,
BU YERDA YO'Q). Ro'yxatni yuklash (`list_active`/`list_all`) hech qachon
tashqi API'ga chiqmaydi — faqat admin aniq "Run" bosganda
(`blueprints/article_discovery.py`) mavjud `run_multi_provider_search()`
chaqiriladi, xuddi oddiy qidiruv kabi (§14).

`services/article_import/persistence.py` bilan bir xil naqsh: ochiq
psycopg2 kursuri, lazy+idempotent `ensure_schema`, Flask'ga bog'liq EMAS.

Egalik siyosati (§20, audit natijasi): loyihada admin-panel resurslari
(`article_import_batches`, data-quality navbati va h.k.) HAR DOIM "bitta
`is_admin` bayrog'i — barcha adminlar ko'radi/boshqaradi" naqshiga amal
qiladi, alohida per-admin egalik devori yo'q. Saqlangan so'rovlar ham shu
konvensiyani davom ettiradi: `created_by` FAQAT audit/provenance uchun,
kirish nazorati EMAS — istalgan admin istalgan saqlangan so'rovni
ko'radi/ishga tushiradi/tahrirlaydi/faolsizlantiradi (xuddi import
partiyalari kabi).
"""
from __future__ import annotations

import json

from services.article_discovery.models import (ArticleDiscoveryQuery,
                                                DiscoveryValidationError)

NAME_MIN_LENGTH = 1
NAME_MAX_LENGTH = 120

_schema_ready = False


def ensure_schema(cur):
    """migrations/017 bilan AYNAN bir xil DDL, idempotent (parity test)."""
    global _schema_ready
    if _schema_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS article_discovery_saved_queries (
            id              SERIAL PRIMARY KEY,
            name            VARCHAR(120) NOT NULL,
            created_by      INTEGER      NOT NULL,
            keyword         VARCHAR(300) NOT NULL,
            from_date       DATE         NOT NULL,
            to_date         DATE         NOT NULL,
            oak_field_id    INTEGER      REFERENCES scientific_fields(id) ON DELETE SET NULL,
            providers       JSONB        NOT NULL DEFAULT '["openalex", "crossref"]'::jsonb,
            result_limit    INTEGER      NOT NULL DEFAULT 50,
            is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
            last_used_at    TIMESTAMP
        )""")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_article_discovery_saved_queries_created_by "
        "ON article_discovery_saved_queries (created_by, created_at DESC)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_article_discovery_saved_queries_active "
        "ON article_discovery_saved_queries (is_active, created_at DESC) "
        "WHERE is_active")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_article_discovery_saved_queries_oak_field "
        "ON article_discovery_saved_queries (oak_field_id) "
        "WHERE oak_field_id IS NOT NULL")
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


# ── Validatsiya (§20) ─────────────────────────────────────────────────────────

def validate_name(raw):
    """Foydalanuvchi ko'rsatadigan nom — 1-120 belgi, bo'sh emas."""
    name = (raw or '').strip()
    if not (NAME_MIN_LENGTH <= len(name) <= NAME_MAX_LENGTH):
        raise DiscoveryValidationError(
            'invalid_name',
            "Nom 1-%d belgi orasida bo'lishi kerak." % NAME_MAX_LENGTH)
    return name


# ── CRUD ─────────────────────────────────────────────────────────────────────

def create(cur, created_by, name, query: ArticleDiscoveryQuery):
    """Yangi saqlangan so'rov yozadi. `query` — allaqachon
    `service.validate_query()` orqali tekshirilgan (ikkinchi validator
    YO'Q, §20 — bir xil qoidalar bir joyda)."""
    ensure_schema(cur)
    cur.execute(
        """INSERT INTO article_discovery_saved_queries
              (name, created_by, keyword, from_date, to_date, oak_field_id,
               providers, result_limit)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (name, created_by, query.keyword, query.from_date, query.to_date,
         query.oak_field_id, json.dumps(list(query.providers)), query.limit))
    return cur.fetchone()[0]


def list_all(cur, include_inactive=True, limit=100):
    """Barcha (yoki faqat faol) saqlangan so'rovlar — eng yangi birinchi.

    Admin panel kompakt bo'lishi kerak (§18) — cheklov shuning uchun bor,
    minglab saqlangan so'rov degan stsenariy yo'q.
    """
    ensure_schema(cur)
    where = '' if include_inactive else 'WHERE is_active'
    cur.execute(
        f"""SELECT * FROM article_discovery_saved_queries {where}
           ORDER BY is_active DESC, created_at DESC LIMIT %s""", (limit,))
    return _dict_rows(cur)


def get(cur, query_id):
    ensure_schema(cur)
    cur.execute("SELECT * FROM article_discovery_saved_queries WHERE id = %s",
               (query_id,))
    return _dict_row(cur)


def update(cur, query_id, name, query: ArticleDiscoveryQuery):
    """Nom/qidiruv parametrlarini yangilaydi. `False` — id topilmadi."""
    ensure_schema(cur)
    cur.execute(
        """UPDATE article_discovery_saved_queries SET
              name = %s, keyword = %s, from_date = %s, to_date = %s,
              oak_field_id = %s, providers = %s, result_limit = %s,
              updated_at = NOW()
           WHERE id = %s RETURNING id""",
        (name, query.keyword, query.from_date, query.to_date,
         query.oak_field_id, json.dumps(list(query.providers)), query.limit,
         query_id))
    return cur.fetchone() is not None


def set_active(cur, query_id, is_active):
    """Soft (de)activate — loyiha konvensiyasi bo'yicha hard DELETE yo'q."""
    ensure_schema(cur)
    cur.execute(
        """UPDATE article_discovery_saved_queries
           SET is_active = %s, updated_at = NOW() WHERE id = %s RETURNING id""",
        (is_active, query_id))
    return cur.fetchone() is not None


def touch_last_used(cur, query_id):
    ensure_schema(cur)
    cur.execute(
        """UPDATE article_discovery_saved_queries
           SET last_used_at = NOW() WHERE id = %s""", (query_id,))


def to_query(row) -> ArticleDiscoveryQuery:
    """DB qatoridan qayta ishga tushirish uchun `ArticleDiscoveryQuery`ni
    tiklaydi — saqlangan sana oralig'i O'ZGARISHSIZ qayta ishlatiladi
    (§11 — "saqlangan konfiguratsiyani eslab qol", nisbiy oyna EMAS)."""
    providers = row.get('providers')
    if isinstance(providers, str):
        providers = json.loads(providers)
    return ArticleDiscoveryQuery(
        keyword=row['keyword'], from_date=row['from_date'], to_date=row['to_date'],
        oak_field_id=row.get('oak_field_id'), limit=row['result_limit'],
        providers=tuple(providers or ()))
