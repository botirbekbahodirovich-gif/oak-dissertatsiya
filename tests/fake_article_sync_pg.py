# -*- coding: utf-8 -*-
"""Qalbaki psycopg2 kursuri — `services/article_sync` uchun (Phase 2C.3A/2C.3B).

`tests/fake_article_import_pg.py` bilan bir xil naqsh: haqiqatan
FILTRLAYDIGAN, INSERT/UPDATE bajaradigan qalbaki DB — chunki bu modulning
markaziy talablari (stale himoya, idempotency, transaction, dismiss, actor)
soxta "hammasini qaytar" kursor bilan mazmunli test qilib bo'lmaydi.

DASHBOARD CTE (`persistence._LATEST_SYNC_CTE` — `DISTINCT ON`, `FILTER
(WHERE …)`) ATAYIN bu yerda simulyatsiya QILINMAYDI: bu Postgres-ga xos
konstruksiyalarni Python bilan qayta yozish fake'ni asl SQL'dan chetlashtirib,
soxta ishonch berardi. Shu sababli:
  * `persistence._dashboard_where()` — sof funksiya, DB'siz to'g'ridan-to'g'ri
    test qilinadi (tests/test_article_sync_dashboard_query.py);
  * haqiqiy `DISTINCT ON`/filtr semantikasi — staging'da
    (tests/test_article_sync_dashboard_dbgated.py, TEST_DATABASE_URL bilan);
  * route/service darajasida dashboard natijasi `mock.patch` bilan (haqiqiy
    DB'ga ehtiyoj yo'q — faqat marshrutlash/shablon tekshiriladi).
"""
from __future__ import annotations

import json

ARTICLE_COLUMNS = [
    'id', 'title', 'slug', 'abstract', 'language', 'publication_year',
    'publication_date', 'journal_name', 'volume', 'issue', 'pages', 'doi',
    'external_url', 'openalex_id', 'crossref_member_id', 'primary_issn',
    'is_retracted', 'citation_count', 'citation_count_source',
    'citation_count_checked_at', 'metadata_source_summary', 'updated_at',
]

AUTHOR_COLUMNS = ['author_name', 'author_order', 'orcid', 'affiliation',
                  'linked_user_id', 'linked_scientist_id', 'is_corresponding',
                  'openalex_author_id']

SYNC_COLUMNS = ['id', 'article_id', 'status', 'checked_at', 'checked_by',
                'actor_type', 'provider_summary', 'diff_summary', 'change_count',
                'safe_change_count', 'review_change_count',
                'critical_change_count', 'article_updated_at_snapshot',
                'applied_at', 'applied_by', 'applied_fields',
                'dismissed_at', 'dismissed_by', 'dismissed_reason',
                'has_retraction_change', 'has_citation_change',
                'has_author_change', 'has_provider_issue', 'created_at']


def new_store(article=None):
    store = {
        'articles': {1: dict(article)} if article else {},
        'authors': {},          # article_id -> [dict, ...]
        'syncs': {},
        'next_sync_id': 1,
        'clock': 1,              # `updated_at = NOW()`ni simulyatsiya qiladi —
                                  # har UPDATE'da ortadi, stale-check testlari uchun.
        'sql': [],
    }
    return store


def add_article(store, article_id, **fields):
    """Bir nechta maqolali stsenariylar uchun (dashboard bilan bog'liq
    bo'lmagan servis testlari) qo'shimcha maqola qo'shadi."""
    from tests.test_article_sync_service import base_article_row
    row = base_article_row(**fields)
    row['id'] = article_id
    store['articles'][article_id] = row
    return row


def _norm(sql):
    return ' '.join(str(sql).split())


class FakeSyncCursor:
    def __init__(self, store):
        self.store = store
        self._rows = []
        self.description = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    def execute(self, sql, params=None):
        normalized = _norm(sql)
        self.store['sql'].append((normalized, params))
        self._rows, self.description = self._dispatch(normalized, params or ())

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def _dispatch(self, sql, p):
        if sql.startswith(('CREATE TABLE', 'CREATE INDEX', 'CREATE UNIQUE INDEX',
                           'ALTER TABLE')):
            return [], None

        if sql.startswith('SELECT id, title, slug, abstract, language'):
            article = self.store['articles'].get(p[0])
            if article is None:
                return [], [(c,) for c in ARTICLE_COLUMNS]
            return ([tuple(article.get(c) for c in ARTICLE_COLUMNS)],
                   [(c,) for c in ARTICLE_COLUMNS])

        if sql.startswith('SELECT author_name, author_order, orcid, affiliation'):
            authors = self.store['authors'].get(p[0], [])
            return ([tuple(a.get(c) for c in AUTHOR_COLUMNS) for a in authors],
                   [(c,) for c in AUTHOR_COLUMNS])

        if sql.startswith('SELECT updated_at FROM scientific_articles'):
            article = self.store['articles'].get(p[0])
            if article is None:
                return [], [('updated_at',)]
            return [(article.get('updated_at'),)], [('updated_at',)]

        if sql.startswith('UPDATE scientific_articles SET'):
            self._apply_article_update(sql, p)
            return [], None

        if sql.startswith('DELETE FROM article_authors WHERE article_id'):
            self.store['authors'][p[0]] = []
            return [], None

        if sql.startswith('INSERT INTO article_authors'):
            article_id = p[0]
            row = dict(zip(AUTHOR_COLUMNS, p[1:]))
            self.store['authors'].setdefault(article_id, []).append(row)
            return [], None

        if sql.startswith('INSERT INTO article_metadata_syncs'):
            sync_id = self.store['next_sync_id']
            self.store['next_sync_id'] += 1
            row = {c: None for c in SYNC_COLUMNS}
            (article_id, checked_by, actor_type, provider_summary, diff_summary,
             change_count, safe, review, critical, snapshot, has_retraction,
             has_citation, has_authors, has_provider_issue) = p
            row.update({'id': sync_id, 'article_id': article_id, 'status': 'checked',
                       'checked_by': checked_by, 'actor_type': actor_type,
                       'provider_summary': _loads(provider_summary),
                       'diff_summary': _loads(diff_summary),
                       'change_count': change_count, 'safe_change_count': safe,
                       'review_change_count': review,
                       'critical_change_count': critical,
                       'article_updated_at_snapshot': snapshot,
                       'has_retraction_change': has_retraction,
                       'has_citation_change': has_citation,
                       'has_author_change': has_authors,
                       'has_provider_issue': has_provider_issue,
                       'checked_at': 'CHECKED-%d' % sync_id})
            self.store['syncs'][sync_id] = row
            return [(sync_id,)], [('id',)]

        if sql.startswith('SELECT * FROM article_metadata_syncs WHERE id'):
            row = self.store['syncs'].get(p[0])
            if row is None:
                return [], [(c,) for c in SYNC_COLUMNS]
            return [tuple(row[c] for c in SYNC_COLUMNS)], [(c,) for c in SYNC_COLUMNS]

        if sql.startswith('SELECT COUNT(*) FROM article_metadata_syncs WHERE article_id'):
            article_id = p[0]
            count = sum(1 for r in self.store['syncs'].values()
                       if r['article_id'] == article_id)
            return [(count,)], [('count',)]

        if sql.startswith('SELECT * FROM article_metadata_syncs WHERE article_id'):
            article_id, limit, offset = p
            rows = [r for r in self.store['syncs'].values()
                   if r['article_id'] == article_id]
            rows = sorted(rows, key=lambda r: -r['id'])[offset:offset + limit]
            return ([tuple(r[c] for c in SYNC_COLUMNS) for r in rows],
                   [(c,) for c in SYNC_COLUMNS])

        if sql.startswith("UPDATE article_metadata_syncs SET status = 'applied'"):
            applied_by, applied_fields, sync_id = p
            row = self.store['syncs'].get(sync_id)
            if row and row['status'] == 'checked':
                row['status'] = 'applied'
                row['applied_at'] = 'APPLIED'
                row['applied_by'] = applied_by
                row['applied_fields'] = _loads(applied_fields)
                return [(sync_id,)], [('id',)]
            return [], [('id',)]

        if sql.startswith("UPDATE article_metadata_syncs SET status = 'dismissed'"):
            dismissed_by, reason, sync_id = p
            row = self.store['syncs'].get(sync_id)
            if row and row['status'] == 'checked':
                row['status'] = 'dismissed'
                row['dismissed_at'] = 'DISMISSED'
                row['dismissed_by'] = dismissed_by
                row['dismissed_reason'] = reason
                return [(sync_id,)], [('id',)]
            return [], [('id',)]

        return [], None

    def _apply_article_update(self, sql, p):
        """`SET col = %s, col2 = %s, ... WHERE id = %s` — oxirgi param id.
        `NOW()` chaqiruvlari (metadata_last_synced_at/updated_at) parametr
        EMAS, shuning uchun ular columns/values juftlashuvida yo'q — soat
        qo'lda ilgari suriladi (haqiqiy Postgres `NOW()` semantikasi)."""
        article_id = p[-1]
        article = self.store['articles'].get(article_id)
        if article is None:
            return
        values = list(p[:-1])
        assignments_sql = sql.split('SET', 1)[1].split('WHERE', 1)[0]
        columns = [part.strip().split('=')[0].strip()
                  for part in assignments_sql.split(',')]
        param_columns = [c for c in columns
                        if c not in ('metadata_last_synced_at', 'updated_at')]
        for col, val in zip(param_columns, values):
            article[col] = val
        self.store['clock'] += 1
        article['updated_at'] = self.store['clock']


class FakeSyncConnection:
    def __init__(self, store):
        self.store = store
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return FakeSyncCursor(self.store)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def _loads(value):
    if value in (None, ''):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value
