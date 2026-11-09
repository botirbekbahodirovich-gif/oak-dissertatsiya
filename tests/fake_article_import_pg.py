# -*- coding: utf-8 -*-
"""Qalbaki psycopg2 kursuri — `services/article_import` uchun (Phase 2C.2).

`tests/fake_pg.py`dan farqli o'laroq bu qalbaki DB haqiqatan FILTRLAYDI,
INSERT/UPDATE bajaradi va DOI UNIQUE cheklovini simulyatsiya qiladi. Sabab:
bu modulning markaziy talablari — dedup, holat mashinasi, idempotentlik,
poyga holati himoyasi (§65-§69) — soxta "hammasini qaytar" kursor bilan
mazmunli test qilib bo'lmaydi. `blueprints/articles.py` uchun qalbaki DB
ATAYIN filtrlamaydigan (fake_pg.py izohi) — u yerda maqsad boshqa (route/
shablon integratsiyasi). Bu yerda maqsad — servis qatlamining o'zi.

Haqiqiy Postgres-ga xos semantika (FOR UPDATE SKIP LOCKED concurrency,
haqiqiy JSONB round-trip) staging'da tekshiriladi (kelajakda
tests/test_article_import_dbgated.py, TEST_DATABASE_URL bilan — hozircha
yozilmagan, articles moduli namunasi bo'yicha keyingi bosqichda qo'shiladi).
"""
from __future__ import annotations

import json

import psycopg2

BATCH_COLUMNS = [
    'id', 'created_by', 'source_type', 'original_filename', 'status',
    'total_items', 'pending_count', 'processing_count', 'review_count',
    'approved_count', 'rejected_count', 'failed_count', 'duplicate_count',
    'skipped_count', 'cancel_requested', 'created_at', 'started_at',
    'completed_at', 'updated_at',
]

ITEM_COLUMNS = [
    'id', 'batch_id', 'row_number', 'raw_input', 'normalized_doi', 'status',
    'duplicate_of_item_id', 'existing_article_id', 'created_article_id',
    'metadata_candidate', 'provider_status_summary', 'conflicts', 'warnings',
    'is_retracted', 'oa_status', 'candidate_pdf_url', 'attempt_count',
    'last_error_code', 'last_error_message', 'created_at', 'started_at',
    'processed_at', 'reviewed_at', 'reviewed_by',
]


def new_store():
    return {
        'batches': [], 'items': [], 'articles': [], 'authors': [], 'links': [],
        'fields': [], 'next_batch_id': 1, 'next_item_id': 1, 'next_article_id': 1,
        'sql': [],
    }


def _norm(sql):
    return ' '.join(str(sql).split())


class FakeImportCursor:
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

    # ── dispatch ──────────────────────────────────────────────────────────

    def _dispatch(self, sql, p):
        if sql.startswith(('CREATE TABLE', 'CREATE INDEX', 'CREATE UNIQUE INDEX',
                           'ALTER TABLE')):
            return [], None

        # ── batches ──
        if sql.startswith('INSERT INTO article_import_batches'):
            batch_id = self.store['next_batch_id']
            self.store['next_batch_id'] += 1
            row = {c: None for c in BATCH_COLUMNS}
            row.update({'id': batch_id, 'created_by': p[0], 'source_type': p[1],
                       'original_filename': p[2], 'status': p[3],
                       'total_items': 0, 'pending_count': 0, 'processing_count': 0,
                       'review_count': 0, 'approved_count': 0, 'rejected_count': 0,
                       'failed_count': 0, 'duplicate_count': 0, 'skipped_count': 0,
                       'cancel_requested': False})
            self.store['batches'].append(row)
            return [(batch_id,)], [('id',)]

        if sql.startswith('SELECT * FROM article_import_batches WHERE id'):
            row = self._find_batch(p[0])
            if row is None:
                return [], [(c,) for c in BATCH_COLUMNS]
            return [tuple(row[c] for c in BATCH_COLUMNS)], [(c,) for c in BATCH_COLUMNS]

        if sql.startswith('SELECT COUNT(*) FROM article_import_batches'):
            return [(len(self.store['batches']),)], [('count',)]

        if sql.startswith('SELECT * FROM article_import_batches ORDER BY'):
            rows = sorted(self.store['batches'], key=lambda r: -r['id'])
            limit, offset = p
            page = rows[offset:offset + limit]
            return ([tuple(r[c] for c in BATCH_COLUMNS) for r in page],
                   [(c,) for c in BATCH_COLUMNS])

        if sql.startswith('UPDATE article_import_batches SET started_at'):
            row = self._find_batch(p[0])
            if row and row['started_at'] is None:
                row['started_at'] = 'STARTED'
            return [], None

        if sql.startswith('UPDATE article_import_batches SET cancel_requested'):
            row = self._find_batch(p[1])
            if row:
                row['cancel_requested'] = p[0]
            return [], None

        if sql.startswith('SELECT started_at, cancel_requested FROM article_import_batches'):
            row = self._find_batch(p[0])
            if row is None:
                return [], [('started_at',), ('cancel_requested',)]
            return [(row['started_at'], row['cancel_requested'])], \
                  [('started_at',), ('cancel_requested',)]

        if sql.startswith('UPDATE article_import_batches SET total_items'):
            (total, pending, processing, review, approved, rejected, failed,
             duplicate, skipped, status, batch_id) = p
            row = self._find_batch(batch_id)
            if row:
                row.update({'total_items': total, 'pending_count': pending,
                           'processing_count': processing, 'review_count': review,
                           'approved_count': approved, 'rejected_count': rejected,
                           'failed_count': failed, 'duplicate_count': duplicate,
                           'skipped_count': skipped, 'status': status})
            return [], None

        # ── items ──
        if sql.startswith('INSERT INTO article_import_items (batch_id, row_number'):
            item_id = self.store['next_item_id']
            self.store['next_item_id'] += 1
            row = {c: None for c in ITEM_COLUMNS}
            row.update({'id': item_id, 'batch_id': p[0], 'row_number': p[1],
                       'raw_input': p[2], 'normalized_doi': p[3], 'status': p[4],
                       'existing_article_id': p[5], 'last_error_message': p[6],
                       'is_retracted': False, 'attempt_count': 0,
                       'conflicts': None, 'warnings': None})
            self.store['items'].append(row)
            return [(item_id,)], [('id',)]

        if sql.startswith('UPDATE article_import_items SET duplicate_of_item_id'):
            row = self._find_item(p[1])
            if row:
                row['duplicate_of_item_id'] = p[0]
            return [], None

        if sql.startswith('SELECT * FROM article_import_items WHERE id'):
            row = self._find_item(p[0])
            if row is None:
                return [], [(c,) for c in ITEM_COLUMNS]
            return [tuple(row[c] for c in ITEM_COLUMNS)], [(c,) for c in ITEM_COLUMNS]

        if sql.startswith('SELECT COUNT(*) FROM article_import_items WHERE'):
            rows = self._filter_items(sql, p)
            return [(len(rows),)], [('count',)]

        if sql.startswith('SELECT * FROM article_import_items WHERE batch_id'):
            rows = self._filter_items(sql, p[:-2] if 'LIMIT' in sql else p)
            limit, offset = p[-2], p[-1]
            rows = sorted(rows, key=lambda r: r['row_number'])[offset:offset + limit]
            return ([tuple(r[c] for c in ITEM_COLUMNS) for r in rows],
                   [(c,) for c in ITEM_COLUMNS])

        if sql.startswith('SELECT status, COUNT(*) FROM article_import_items'):
            batch_id = p[0]
            counts = {}
            for row in self.store['items']:
                if row['batch_id'] == batch_id:
                    counts[row['status']] = counts.get(row['status'], 0) + 1
            return list(counts.items()), [('status',), ('count',)]

        if sql.startswith('SELECT id FROM article_import_items WHERE batch_id'):
            batch_id, chunk_size = p
            rows = [r for r in self.store['items']
                   if r['batch_id'] == batch_id and r['status'] == 'pending']
            rows = sorted(rows, key=lambda r: r['row_number'])[:chunk_size]
            return [(r['id'],) for r in rows], [('id',)]

        if sql.startswith("UPDATE article_import_items SET status = 'processing'"):
            row = self._find_item(p[0])
            if row and row['status'] == 'pending':
                row['status'] = 'processing'
                row['started_at'] = 'STARTED'
                row['attempt_count'] = (row['attempt_count'] or 0) + 1
                return [(row['id'],)], [('id',)]
            return [], [('id',)]

        if sql.startswith('UPDATE article_import_items SET status = %s, metadata_candidate'):
            (status, metadata_candidate, provider_status_summary, conflicts,
             warnings, is_retracted, oa_status, candidate_pdf_url,
             last_error_code, last_error_message, item_id) = p
            row = self._find_item(item_id)
            if row:
                row.update({'status': status,
                           'metadata_candidate': _loads(metadata_candidate),
                           'provider_status_summary': _loads(provider_status_summary),
                           'conflicts': _loads(conflicts), 'warnings': _loads(warnings),
                           'is_retracted': is_retracted, 'oa_status': oa_status,
                           'candidate_pdf_url': candidate_pdf_url,
                           'last_error_code': last_error_code,
                           'last_error_message': last_error_message,
                           'processed_at': 'PROCESSED'})
            return [], None

        if sql.startswith("UPDATE article_import_items SET status = 'pending', last_error_code"):
            item_id, max_attempts = p
            row = self._find_item(item_id)
            if (row and row['status'] in ('provider_failed', 'failed')
                    and (row['attempt_count'] or 0) < max_attempts):
                row['status'] = 'pending'
                row['last_error_code'] = None
                return [(row['id'],)], [('id',)]
            return [], [('id',)]

        if sql.startswith("UPDATE article_import_items SET status = 'already_exists'"):
            existing_article_id, item_id = p
            row = self._find_item(item_id)
            if row:
                row['status'] = 'already_exists'
                row['existing_article_id'] = existing_article_id
                row['processed_at'] = 'PROCESSED'
            return [], None

        if sql.startswith('UPDATE article_import_items SET status = %s, reviewed_at'):
            to_status, actor_id, created_article_id, item_id, from_statuses = p
            row = self._find_item(item_id)
            if row and row['status'] in from_statuses:
                row['status'] = to_status
                row['reviewed_at'] = 'REVIEWED'
                row['reviewed_by'] = actor_id
                if created_article_id is not None:
                    row['created_article_id'] = created_article_id
                return [(row['id'],)], [('id',)]
            return [], [('id',)]

        # ── scientific_articles (minimal) ──
        if sql.startswith('SELECT id, title, slug, doi FROM scientific_articles WHERE doi = ANY'):
            wanted = set(p[0])
            rows = [a for a in self.store['articles'] if a['doi'] in wanted]
            return ([(a['id'], a['title'], a['slug'], a['doi']) for a in rows],
                   [('id',), ('title',), ('slug',), ('doi',)])

        if sql.startswith('SELECT 1 FROM scientific_articles WHERE slug'):
            slug = p[0]
            return ([(1,)] if any(a['slug'] == slug for a in self.store['articles'])
                    else []), [('x',)]

        if sql.startswith('INSERT INTO scientific_articles'):
            doi = p[11]                      # 'doi' — persistence.py ustun tartibi:
                                              # title,slug,abstract,keywords,language,
                                              # publication_year,publication_date,
                                              # journal_name,volume,issue,pages,doi,…
            if doi and any(a['doi'] == doi for a in self.store['articles']):
                raise psycopg2.IntegrityError(
                    'duplicate key value violates unique constraint '
                    '"uq_scientific_articles_doi"')
            article_id = self.store['next_article_id']
            self.store['next_article_id'] += 1
            self.store['articles'].append({
                'id': article_id, 'title': p[0], 'slug': p[1],
                'abstract': p[2], 'doi': doi})
            return [(article_id,)], [('id',)]

        if sql.startswith('SELECT id, title, slug FROM scientific_articles WHERE id'):
            row = next((a for a in self.store['articles'] if a['id'] == p[0]), None)
            if row is None:
                return [], [('id',), ('title',), ('slug',)]
            return [(row['id'], row['title'], row['slug'])], [('id',), ('title',), ('slug',)]

        if sql.startswith('SELECT id, title, slug FROM scientific_articles WHERE doi'):
            row = next((a for a in self.store['articles'] if a['doi'] == p[0]), None)
            if row is None:
                return [], [('id',), ('title',), ('slug',)]
            return [(row['id'], row['title'], row['slug'])], [('id',), ('title',), ('slug',)]

        if sql.startswith('INSERT INTO article_authors'):
            self.store['authors'].append(p)
            return [], None

        if sql.startswith('INSERT INTO article_scientific_fields'):
            self.store['links'].append(p)
            return [], None

        # ── scientific_fields (article_import blueprint field-tree lookup) ──
        if sql.startswith('SELECT id, parent_id, name_uz'):
            cols = ['id', 'parent_id', 'name_uz', 'name_uz_cyrillic', 'name_ru',
                   'code', 'slug', 'level', 'description', 'sort_order',
                   'is_active', 'source_name', 'source_url', 'source_updated_at',
                   'raw_name', 'source_line', 'source_sha256', 'review_status',
                   'missing_parent_code']
            rows = [tuple(f.get(c) for c in cols) for f in self.store['fields']]
            return rows, [(c,) for c in cols]

        return [], None

    # ── yordamchilar ──

    def _find_batch(self, batch_id):
        return next((r for r in self.store['batches'] if r['id'] == batch_id), None)

    def _find_item(self, item_id):
        return next((r for r in self.store['items'] if r['id'] == item_id), None)

    def _filter_items(self, sql, p):
        params = list(p)
        batch_id = params.pop(0)
        rows = [r for r in self.store['items'] if r['batch_id'] == batch_id]
        if 'status = %s' in sql:
            status = params.pop(0)
            rows = [r for r in rows if r['status'] == status]
        return rows


class FakeImportConnection:
    def __init__(self, store):
        self.store = store
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return FakeImportCursor(self.store)

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
