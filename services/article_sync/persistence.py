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
    """migrations/012 + migrations/013 bilan bir xil DDL, idempotent."""
    global _schema_ready
    if _schema_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS article_metadata_syncs (
            id                          SERIAL PRIMARY KEY,
            article_id                  INTEGER      NOT NULL REFERENCES scientific_articles(id) ON DELETE CASCADE,
            status                      VARCHAR(24)  NOT NULL DEFAULT 'checked',
            checked_at                  TIMESTAMP    NOT NULL DEFAULT NOW(),
            checked_by                  INTEGER,
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
            actor_type                  VARCHAR(16)  NOT NULL DEFAULT 'admin',
            dismissed_at                TIMESTAMP,
            dismissed_by                INTEGER,
            dismissed_reason            TEXT,
            has_retraction_change       BOOLEAN      NOT NULL DEFAULT FALSE,
            has_citation_change         BOOLEAN      NOT NULL DEFAULT FALSE,
            has_author_change           BOOLEAN      NOT NULL DEFAULT FALSE,
            has_provider_issue          BOOLEAN      NOT NULL DEFAULT FALSE,
            created_at                  TIMESTAMP    NOT NULL DEFAULT NOW()
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_metadata_syncs_article "
                "ON article_metadata_syncs (article_id, checked_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_metadata_syncs_status "
                "ON article_metadata_syncs (status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_metadata_syncs_checked_by "
                "ON article_metadata_syncs (checked_by)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_metadata_syncs_actor_type "
                "ON article_metadata_syncs (actor_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_metadata_syncs_critical "
                "ON article_metadata_syncs (checked_at DESC) "
                "WHERE critical_change_count > 0")
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
                       article_updated_at_snapshot, *, actor_type='admin'):
    from services.article_sync.models import CRITICAL, REVIEW, SAFE
    safe = sum(1 for d in diffs if d.severity == SAFE)
    review = sum(1 for d in diffs if d.severity == REVIEW)
    critical = sum(1 for d in diffs if d.severity == CRITICAL)
    fields = {d.field for d in diffs}
    has_retraction = 'is_retracted' in fields
    has_citation = 'citation_count' in fields
    has_authors = 'authors' in fields
    has_provider_issue = any(
        (r or {}).get('status') != 'ok' for r in (provider_summary or {}).values())
    cur.execute(
        """INSERT INTO article_metadata_syncs
              (article_id, status, checked_by, actor_type, provider_summary,
               diff_summary, change_count, safe_change_count, review_change_count,
               critical_change_count, article_updated_at_snapshot,
               has_retraction_change, has_citation_change, has_author_change,
               has_provider_issue)
           VALUES (%s, 'checked', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id""",
        (article_id, checked_by, actor_type, _json(provider_summary),
         _json([d.to_dict() for d in diffs]), len(diffs), safe, review, critical,
         article_updated_at_snapshot, has_retraction, has_citation, has_authors,
         has_provider_issue))
    return cur.fetchone()[0]


def get_sync(cur, sync_id):
    cur.execute("SELECT * FROM article_metadata_syncs WHERE id = %s", (sync_id,))
    return _dict_row(cur)


def list_syncs_for_article(cur, article_id, limit=10, offset=0):
    cur.execute("SELECT COUNT(*) FROM article_metadata_syncs WHERE article_id = %s",
               (article_id,))
    total = cur.fetchone()[0]
    cur.execute(
        """SELECT * FROM article_metadata_syncs WHERE article_id = %s
           ORDER BY checked_at DESC LIMIT %s OFFSET %s""",
        (article_id, limit, offset))
    return _dict_rows(cur), total


def mark_sync_applied(cur, sync_id, applied_by, applied_fields):
    cur.execute(
        """UPDATE article_metadata_syncs SET status = 'applied',
              applied_at = NOW(), applied_by = %s, applied_fields = %s
           WHERE id = %s AND status = 'checked' RETURNING id""",
        (applied_by, _json(applied_fields), sync_id))
    return cur.fetchone() is not None


def dismiss_sync(cur, sync_id, dismissed_by, reason=None):
    """`checked` -> `dismissed` (CAS, §12). Maqolani RAD ETMAYDI — faqat shu
    sync candidate'ni yopadi, tarix saqlanadi."""
    cur.execute(
        """UPDATE article_metadata_syncs SET status = 'dismissed',
              dismissed_at = NOW(), dismissed_by = %s, dismissed_reason = %s
           WHERE id = %s AND status = 'checked' RETURNING id""",
        (dismissed_by, (reason or None), sync_id))
    return cur.fetchone() is not None


# ── Dashboard — "eng so'nggi sync har maqola uchun" (§7) ────────────────────
# `DISTINCT ON` — Postgres-only konstruksiya; loyiha faqat PostgreSQL
# ishlatadi (docs/article-bulk-import.md §1/§2 — SQLite yo'q). N+1 emas:
# butun dashboard sahifasi bitta COUNT + bitta SELECT bilan (§7, §23).
_LATEST_SYNC_CTE = """
    WITH latest AS (
        SELECT DISTINCT ON (s.article_id)
            s.id, s.article_id, s.status, s.checked_at, s.checked_by,
            s.actor_type, s.change_count, s.safe_change_count,
            s.review_change_count, s.critical_change_count,
            s.article_updated_at_snapshot, s.applied_at, s.applied_by,
            s.dismissed_at, s.dismissed_by, s.dismissed_reason,
            s.has_retraction_change, s.has_citation_change,
            s.has_author_change, s.has_provider_issue,
            a.title, a.slug, a.doi, a.publication_status,
            a.updated_at AS article_updated_at,
            (a.updated_at IS DISTINCT FROM s.article_updated_at_snapshot) AS is_stale
        FROM article_metadata_syncs s
        JOIN scientific_articles a ON a.id = s.article_id
        ORDER BY s.article_id, s.checked_at DESC, s.id DESC
    )
"""

_SORT_OPTIONS = {
    'critical_first': 'critical_change_count DESC, checked_at DESC',
    'newest': 'checked_at DESC',
    'oldest': 'checked_at ASC',
    'most_changes': 'change_count DESC, checked_at DESC',
}
DEFAULT_SORT = 'critical_first'


def _dashboard_where(queue_status, has_retraction, has_citation_only,
                     has_provider_issue, publication_status, actor_type):
    """(where_sql, params) — dashboard filtrlari BITTA joyda (§6, §8).

    `queue_status=None` — DEFAULT: faqat actionable (needs_review + critical,
    eskirmagan) — §7/§18 talabi. Boshqa qiymatlar butun tarixni ko'radi.
    """
    clauses, params = [], []

    if queue_status in (None, 'actionable'):
        clauses.append("status = 'checked' AND NOT is_stale AND change_count > 0")
    elif queue_status == 'needs_review':
        clauses.append("status = 'checked' AND NOT is_stale "
                       "AND critical_change_count = 0 AND change_count > 0")
    elif queue_status == 'critical':
        clauses.append("status = 'checked' AND NOT is_stale AND critical_change_count > 0")
    elif queue_status == 'no_changes':
        clauses.append("status = 'checked' AND NOT is_stale AND change_count = 0")
    elif queue_status == 'stale':
        clauses.append("status = 'checked' AND is_stale")
    elif queue_status == 'applied':
        clauses.append("status = 'applied'")
    elif queue_status == 'dismissed':
        clauses.append("status = 'dismissed'")
    # queue_status == 'all' — cheklov yo'q.

    if has_retraction:
        clauses.append('has_retraction_change')
    if has_citation_only:
        clauses.append('has_citation_change AND change_count = 1')
    if has_provider_issue:
        clauses.append('has_provider_issue')
    if publication_status:
        clauses.append('publication_status = %s')
        params.append(publication_status)
    if actor_type:
        clauses.append('actor_type = %s')
        params.append(actor_type)

    where_sql = ' AND '.join(f'({c})' for c in clauses) if clauses else 'TRUE'
    return where_sql, params


def list_dashboard(cur, *, queue_status=None, has_retraction=False,
                   has_citation_only=False, has_provider_issue=False,
                   publication_status=None, actor_type=None,
                   sort=DEFAULT_SORT, limit=25, offset=0):
    """(rows, total) — eng so'nggi sync har maqola uchun, filtrlangan/
    saralangan/sahifalangan (§7, §8, §9, §22)."""
    where_sql, where_params = _dashboard_where(
        queue_status, has_retraction, has_citation_only, has_provider_issue,
        publication_status, actor_type)
    order_sql = _SORT_OPTIONS.get(sort, _SORT_OPTIONS[DEFAULT_SORT])

    cur.execute(_LATEST_SYNC_CTE + f"SELECT COUNT(*) FROM latest WHERE {where_sql}",
               where_params)
    total = cur.fetchone()[0]

    cur.execute(
        _LATEST_SYNC_CTE +
        f"SELECT * FROM latest WHERE {where_sql} ORDER BY {order_sql} "
        f"LIMIT %s OFFSET %s",
        where_params + [limit, offset])
    return _dict_rows(cur), total


def get_dashboard_summary(cur):
    """Xulosaviy kartochkalar (§4) — bitta so'rov, eng so'nggi sync ustida."""
    cur.execute(_LATEST_SYNC_CTE + """
        SELECT
            COUNT(*) AS total_checked,
            COUNT(*) FILTER (WHERE status = 'checked' AND change_count > 0) AS with_changes,
            COUNT(*) FILTER (WHERE status = 'checked' AND NOT is_stale
                             AND critical_change_count = 0 AND change_count > 0) AS needs_review,
            COUNT(*) FILTER (WHERE status = 'checked' AND NOT is_stale
                             AND critical_change_count > 0) AS critical,
            COUNT(*) FILTER (WHERE status = 'checked' AND change_count = 0) AS no_changes,
            COUNT(*) FILTER (WHERE has_provider_issue) AS provider_issue,
            COUNT(*) FILTER (WHERE has_retraction_change AND status = 'checked'
                             AND NOT is_stale) AS retraction,
            COUNT(*) FILTER (WHERE status = 'checked' AND is_stale) AS stale
        FROM latest""")
    return _dict_row(cur) or {}


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
