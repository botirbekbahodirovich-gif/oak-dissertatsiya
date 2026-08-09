# -*- coding: utf-8 -*-
"""Qalbaki psycopg2 kursuri — `services/article_discovery` bridge testlari
uchun (Phase 2D.1/2D.2).

`tests/fake_article_import_pg.py:FakeImportCursor`ni KENGAYTIRADI (bridge
AYNAN o'sha jadvallarga yozadi) — faqat Phase 2D.1'da qo'shilgan yangi SQL
naqshlarini (`source_provider` bilan batch, `ON CONFLICT` bilan item,
faol-DOI qidiruvi) qo'shimcha talqin qiladi, qolgani bazaviy klassga
tushiriladi (dedup, batch counts va h.k. — takrorlanmaydi).

Phase 2D.2: `article_discovery_saved_queries` (mustaqil, YANGI jadval)
dispatch'i ham shu yerga qo'shildi — `blueprints/article_discovery.py`ning
HAR BIR GET/POST route'i endi saqlangan so'rovlar ro'yxatini o'qiydi
(`_saved_queries()`), shuning uchun bu jadvalni tushunmaydigan eski qalbaki
kursur `admin/article_discovery.html` render qiladigan HAR QANDAY testni
`cur.description is None` bilan yiqitardi. `new_store()` shu yerda mahalliy
o'raladi — bazaviy `fake_article_import_pg.py`ga (ko'plab boshqa testlar
ishlatadi) TEGILMAYDI, faqat qo'shimcha kalitlar qo'shiladi.
"""
from __future__ import annotations

import json

from services.article_import.state import ACTIVE_DOI_ITEM_STATUSES
from tests.fake_article_import_pg import (BATCH_COLUMNS, ITEM_COLUMNS,
                                          FakeImportConnection, FakeImportCursor)
from tests.fake_article_import_pg import new_store as _base_new_store

DISCOVERY_BATCH_COLUMNS = BATCH_COLUMNS + [
    'source_provider', 'source_query', 'source_oak_field_id',
    'source_result_count', 'excluded_no_doi_count', 'source_request_id',
]
DISCOVERY_ITEM_COLUMNS = ITEM_COLUMNS + ['external_source_id']

SAVED_QUERY_COLUMNS = ['id', 'name', 'created_by', 'keyword', 'from_date', 'to_date',
                       'oak_field_id', 'providers', 'result_limit', 'is_active',
                       'created_at', 'updated_at', 'last_used_at']


def new_store():
    store = _base_new_store()
    store['saved_queries'] = []
    store['next_saved_query_id'] = 1
    return store


class FakeDiscoveryCursor(FakeImportCursor):
    def _dispatch(self, sql, p):
        if sql.startswith('INSERT INTO article_import_batches (created_by, '
                          'source_type, status, source_provider'):
            return self._insert_discovery_batch(p)
        if sql.startswith('INSERT INTO article_import_items (batch_id, row_number, '
                          'raw_input, normalized_doi, status, external_source_id)'):
            return self._insert_discovery_item(p)
        if sql.startswith('SELECT normalized_doi, id, batch_id FROM '
                          'article_import_items WHERE normalized_doi = ANY'):
            return self._find_active_dois(p)
        if sql.startswith('SELECT id FROM article_import_batches WHERE '
                          "source_type = 'discovery' AND source_request_id"):
            return self._find_discovery_batch_by_request_id(p)
        if 'article_discovery_saved_queries' in sql:
            return self._dispatch_saved_queries(sql, p)
        return super()._dispatch(sql, p)

    # ── article_discovery_saved_queries (Phase 2D.2) ────────────────────────

    def _dispatch_saved_queries(self, sql, p):
        if sql.startswith('INSERT INTO article_discovery_saved_queries'):
            return self._insert_saved_query(p)
        if sql.startswith('SELECT * FROM article_discovery_saved_queries WHERE id'):
            return self._get_saved_query(p)
        if sql.startswith('SELECT * FROM article_discovery_saved_queries'):
            return self._list_saved_queries(sql, p)
        if sql.startswith('UPDATE article_discovery_saved_queries SET name'):
            return self._update_saved_query(p)
        if sql.startswith('UPDATE article_discovery_saved_queries SET is_active'):
            return self._set_saved_query_active(p)
        if sql.startswith('UPDATE article_discovery_saved_queries SET last_used_at'):
            return self._touch_saved_query(p)
        return [], None       # CREATE TABLE/INDEX (ensure_schema) — no-op

    def _saved_query_by_id(self, query_id):
        return next((r for r in self.store['saved_queries'] if r['id'] == query_id), None)

    def _insert_saved_query(self, p):
        (name, created_by, keyword, from_date, to_date, oak_field_id,
         providers, result_limit) = p
        row_id = self.store['next_saved_query_id']
        self.store['next_saved_query_id'] += 1
        row = {
            'id': row_id, 'name': name, 'created_by': created_by, 'keyword': keyword,
            'from_date': from_date, 'to_date': to_date, 'oak_field_id': oak_field_id,
            'providers': _loads(providers), 'result_limit': result_limit,
            'is_active': True, 'created_at': None, 'updated_at': None,
            'last_used_at': None,
        }
        self.store['saved_queries'].append(row)
        return [(row_id,)], [('id',)]

    def _get_saved_query(self, p):
        row = self._saved_query_by_id(p[0])
        cols = [(c,) for c in SAVED_QUERY_COLUMNS]
        if row is None:
            return [], cols
        return [tuple(row[c] for c in SAVED_QUERY_COLUMNS)], cols

    def _list_saved_queries(self, sql, p):
        rows = list(self.store['saved_queries'])
        if 'WHERE is_active' in sql:
            rows = [r for r in rows if r['is_active']]
        rows.sort(key=lambda r: (not r['is_active'], -r['id']))
        limit = p[-1] if p else len(rows)
        rows = rows[:limit]
        cols = [(c,) for c in SAVED_QUERY_COLUMNS]
        return [tuple(r[c] for c in SAVED_QUERY_COLUMNS) for r in rows], cols

    def _update_saved_query(self, p):
        (name, keyword, from_date, to_date, oak_field_id, providers,
         result_limit, query_id) = p
        row = self._saved_query_by_id(query_id)
        if row is None:
            return [], [('id',)]
        row.update(name=name, keyword=keyword, from_date=from_date, to_date=to_date,
                  oak_field_id=oak_field_id, providers=_loads(providers),
                  result_limit=result_limit)
        return [(query_id,)], [('id',)]

    def _set_saved_query_active(self, p):
        is_active, query_id = p
        row = self._saved_query_by_id(query_id)
        if row is None:
            return [], [('id',)]
        row['is_active'] = is_active
        return [(query_id,)], [('id',)]

    def _touch_saved_query(self, p):
        (query_id,) = p
        row = self._saved_query_by_id(query_id)
        if row is not None:
            row['last_used_at'] = 'touched'
        return [], None

    def _insert_discovery_batch(self, p):
        (created_by, status, source_provider, source_query, source_oak_field_id,
         source_result_count, excluded_no_doi_count, source_request_id) = p
        if source_request_id is not None:
            conflict = any(
                r['source_type'] == 'discovery' and r['source_request_id'] == source_request_id
                for r in self.store['batches'])
            if conflict:
                return [], [('id',)]            # ON CONFLICT DO NOTHING
        batch_id = self.store['next_batch_id']
        self.store['next_batch_id'] += 1
        row = {c: None for c in DISCOVERY_BATCH_COLUMNS}
        row.update({
            'id': batch_id, 'created_by': created_by, 'source_type': 'discovery',
            'status': status, 'source_provider': source_provider,
            'source_query': _loads(source_query),
            'source_oak_field_id': source_oak_field_id,
            'source_result_count': source_result_count,
            'excluded_no_doi_count': excluded_no_doi_count,
            'source_request_id': source_request_id,
            'total_items': 0, 'pending_count': 0, 'processing_count': 0,
            'review_count': 0, 'approved_count': 0, 'rejected_count': 0,
            'failed_count': 0, 'duplicate_count': 0, 'skipped_count': 0,
            'cancel_requested': False,
        })
        self.store['batches'].append(row)
        return [(batch_id,)], [('id',)]

    def _find_discovery_batch_by_request_id(self, p):
        source_request_id = p[0]
        row = next((r for r in self.store['batches']
                    if r['source_type'] == 'discovery'
                    and r['source_request_id'] == source_request_id), None)
        if row is None:
            return [], [('id',)]
        return [(row['id'],)], [('id',)]

    def _insert_discovery_item(self, p):
        batch_id, row_number, raw_input, normalized_doi, external_source_id = p
        conflict = any(
            r['normalized_doi'] == normalized_doi and r['status'] in ACTIVE_DOI_ITEM_STATUSES
            for r in self.store['items'])
        if conflict:
            return [], [('id',)]                # ON CONFLICT DO NOTHING
        item_id = self.store['next_item_id']
        self.store['next_item_id'] += 1
        row = {c: None for c in DISCOVERY_ITEM_COLUMNS}
        row.update({'id': item_id, 'batch_id': batch_id, 'row_number': row_number,
                   'raw_input': raw_input, 'normalized_doi': normalized_doi,
                   'status': 'pending', 'external_source_id': external_source_id,
                   'is_retracted': False, 'attempt_count': 0,
                   'conflicts': None, 'warnings': None})
        self.store['items'].append(row)
        return [(item_id,)], [('id',)]

    def _find_active_dois(self, p):
        wanted = set(p[0])
        rows = sorted(
            (r for r in self.store['items']
             if r['normalized_doi'] in wanted and r['status'] in ACTIVE_DOI_ITEM_STATUSES),
            key=lambda r: r['id'])
        return ([(r['normalized_doi'], r['id'], r['batch_id']) for r in rows],
               [('normalized_doi',), ('id',), ('batch_id',)])


class FakeDiscoveryConnection(FakeImportConnection):
    def cursor(self):
        return FakeDiscoveryCursor(self.store)


def _loads(value):
    if value in (None, ''):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


__all__ = ['FakeDiscoveryCursor', 'FakeDiscoveryConnection', 'new_store']
