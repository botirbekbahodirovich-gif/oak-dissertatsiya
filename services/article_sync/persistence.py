# -*- coding: utf-8 -*-
"""DB qatlami — `article_metadata_syncs` + `scientific_articles` apply
(Phase 2C.3A). Konvensiya `services/article_import/persistence.py` bilan
bir xil: xom psycopg2 kursor, ORM yo'q, sxema lazy + idempotent.
"""
from __future__ import annotations

import json
from datetime import date, datetime

_schema_ready = False


def ensure_schema(cur):
    """migrations/011 bilan bir xil DDL, idempotent."""
    global _schema_ready
    if _schema_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS article_metadata_syncs (
            id                          SERIAL PRIMARY KEY,
            article_id                  INTEGER      NOT NULL REFERENCES scientific_articles(id) ON DELETE CASCADE,
            status                      VARCHAR(24)  NOT NULL DEFAULT 'checked',
            checked_at                  TIMESTAMP    NOT NULL DEFAULT NOW(),
            checked_by                  INTEGER      NOT NULL,
            provider_summary            JSONB,
            diff_summary                JSONB,
            change_count                INTEGER      NOT NULL DEFAULT 0,
            safe_change_count           INTEGER      NOT NULL DEFAULT 0,
            review_change_count         INTEGER      NOT NULL DEFAULT 0,
            critical_change_count       INTEGER      NOT NULL DEFAULT 0,
            article_updated_at_snapshot TIMESTAMP,
            applied_at                  TIMESTAMP,
            applied_by                  INTEGER,
            applied_fields               JSONB,
            created_at                  TIMESTAMP    NOT NULL DEFAULT NOW()
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_metadata_syncs_article "
                "ON article_metadata_syncs (article_id, checked_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_metadata_syncs_status "
                "ON article_metadata_syncs (status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_metadata_syncs_checked_by "
                "ON article_metadata_syncs (checked_by)")
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


# ── Maqola o'qish ────────────────────────────────────────────────────────────

def get_article_for_sync(cur, article_id):
    """Sync uchun yetarli maydonlar — `blueprints/articles.py:ARTICLE_COLUMNS`
    bilan bir xil ustun nomlari (servis mustaqil qolishi uchun mustaqil
    SELECT, lekin BIR XIL nom/tип)."""
    cur.execute("""
        SELECT id, title, slug, abstract, language, publication_year,
               publication_date, journal_name, volume, issue, pages, doi,
               external_url, openalex_id, crossref_member_id, primary_issn,
               is_retracted, citation_count, citation_count_source,
               citation_count_checked_at, metadata_source_summary, updated_at
          FROM scientific_articles WHERE id = %s""", (article_id,))
    article = _dict_row(cur)
    if article is None:
        return None
    cur.execute("""SELECT author_name, author_order, orcid, affiliation,
                          linked_user_id, linked_scientist_id, is_corresponding,
                          openalex_author_id
                     FROM article_authors WHERE article_id = %s
                    ORDER BY author_order, id""", (article_id,))
    article['authors'] = _dict_rows(cur)
    return article


# ── Sync yozuvlari ───────────────────────────────────────────────────────────

def create_sync_record(cur, article_id, checked_by, provider_summary, diffs,
                       article_updated_at_snapshot):
    from services.article_sync.models import CRITICAL, REVIEW, SAFE
    safe = sum(1 for d in diffs if d.severity == SAFE)
    review = sum(1 for d in diffs if d.severity == REVIEW)
    critical = sum(1 for d in diffs if d.severity == CRITICAL)
    cur.execute(
        """INSERT INTO article_metadata_syncs
              (article_id, status, checked_by, provider_summary, diff_summary,
               change_count, safe_change_count, review_change_count,
               critical_change_count, article_updated_at_snapshot)
           VALUES (%s, 'checked', %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id""",
        (article_id, checked_by, _json(provider_summary),
         _json([d.to_dict() for d in diffs]), len(diffs), safe, review, critical,
         article_updated_at_snapshot))
    return cur.fetchone()[0]


def get_sync(cur, sync_id):
    cur.execute("SELECT * FROM article_metadata_syncs WHERE id = %s", (sync_id,))
    return _dict_row(cur)


def list_syncs_for_article(cur, article_id, limit=10):
    cur.execute(
        """SELECT * FROM article_metadata_syncs WHERE article_id = %s
           ORDER BY checked_at DESC LIMIT %s""", (article_id, limit))
    return _dict_rows(cur)


def mark_sync_applied(cur, sync_id, applied_by, applied_fields):
    cur.execute(
        """UPDATE article_metadata_syncs SET status = 'applied',
              applied_at = NOW(), applied_by = %s, applied_fields = %s
           WHERE id = %s AND status = 'checked' RETURNING id""",
        (applied_by, _json(applied_fields), sync_id))
    return cur.fetchone() is not None


# ── Apply — scientific_articles / article_authors ───────────────────────────

_SCALAR_COLUMNS = {'title', 'abstract', 'journal_name', 'publication_year',
                   'publication_date', 'volume', 'issue', 'pages', 'language',
                   'external_url', 'openalex_id', 'crossref_member_id',
                   'primary_issn'}


def apply_field_changes(cur, article_id, diffs_by_field, selected_fields,
                        actor_id):
    """Tanlangan maydonlarni BITTA UPDATE + (kerak bo'lsa) authors
    almashtirish bilan yozadi. Faqat `selected_fields` ichidagilar — allowlist
    chaqiruvchida (`service.py`) allaqachon tekshirilgan (§16).

    `diffs_by_field` — SAQLANGAN sync yozuvining `diff_summary`'sidan qayta
    o'qilgan oddiy dict'lar (`MetadataFieldDiff.to_dict()` shakli), CLIENT
    yuborgan qiymat EMAS — server o'zi tanlagan maydon nomiga mos KO'CHIRMA
    qiymatni ishlatadi (§16 — arbitrary client value bilan yozib bo'lmaydi).
    """
    assignments, values = [], []
    authors_applied = False

    for field_name in selected_fields:
        diff = diffs_by_field.get(field_name)
        if diff is None:
            continue
        new_value = diff.get('new_value')
        source = diff.get('source')
        if field_name == 'citation_count':
            assignments += ['citation_count = %s', 'citation_count_source = %s',
                            'citation_count_checked_at = %s']
            values += [new_value, source, datetime.now()]
        elif field_name == 'is_retracted':
            assignments.append('is_retracted = %s')
            values.append(bool(new_value))
        elif field_name == 'authors':
            authors_applied = True
        elif field_name == 'publication_date':
            assignments.append('publication_date = %s')
            values.append(_parse_date(new_value))
        elif field_name in _SCALAR_COLUMNS:
            assignments.append(f'{field_name} = %s')
            values.append(new_value)

    if assignments:
        assignments.append('metadata_last_synced_at = NOW()')
        assignments.append('updated_at = NOW()')
        cur.execute(
            f"UPDATE scientific_articles SET {', '.join(assignments)} WHERE id = %s",
            values + [article_id])
    elif authors_applied:
        # Faqat mualliflar tanlangan bo'lsa ham "sinxronlangan" vaqti yangilanadi.
        cur.execute(
            "UPDATE scientific_articles SET metadata_last_synced_at = NOW(), "
            "updated_at = NOW() WHERE id = %s", (article_id,))

    if authors_applied:
        diff = diffs_by_field.get('authors')
        _replace_authors(cur, article_id, (diff or {}).get('new_value') or [])
    return authors_applied


def _replace_authors(cur, article_id, fresh_authors):
    """`fresh_authors` — oddiy dict ro'yxati (SERVER-side saqlangan sync
    yozuvidan, `diff.new_value`), `AuthorCandidate` obyekti EMAS — chunki
    apply check'dan BUTUNLAY boshqa so'rovda bo'lishi mumkin (§16)."""
    cur.execute("DELETE FROM article_authors WHERE article_id = %s", (article_id,))
    for order, author in enumerate(fresh_authors, start=1):
        cur.execute(
            """INSERT INTO article_authors
                  (article_id, author_name, author_order, orcid, affiliation,
                   is_corresponding, openalex_author_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (article_id, (author.get('name') or '')[:300], order,
             author.get('orcid') or None, author.get('affiliation') or None,
             bool(author.get('is_corresponding')),
             author.get('openalex_author_id')))


def _parse_date(value):
    if value in (None, ''):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def get_article_updated_at(cur, article_id):
    cur.execute("SELECT updated_at FROM scientific_articles WHERE id = %s",
               (article_id,))
    row = cur.fetchone()
    return row[0] if row else None


def _json(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=_default)
    except (TypeError, ValueError):
        return None


def _default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError
