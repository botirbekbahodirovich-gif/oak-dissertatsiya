# -*- coding: utf-8 -*-
"""Qalbaki psycopg2 kursuri — `services/article_discovery/saved_queries.py`
testlari uchun (Phase 2D.2). `tests/fake_article_discovery_pg.py` bilan bir
xil naqsh: SQL PREFIKSI bo'yicha dispatch, xotiradagi ro'yxat "jadval".
"""
from __future__ import annotations

import json

COLUMNS = ['id', 'name', 'created_by', 'keyword', 'from_date', 'to_date',
          'oak_field_id', 'providers', 'result_limit', 'is_active',
          'created_at', 'updated_at', 'last_used_at']


def new_store():
    return {'rows': [], 'next_id': 1}


class FakeSavedQueriesCursor:
    def __init__(self, store):
        self.store = store
        self.description = None
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        normalized = ' '.join(sql.split())
        p = params or ()
        if normalized.startswith('CREATE TABLE') or normalized.startswith('CREATE INDEX'):
            self.description = None
            self._result = []
            return
        if normalized.startswith('INSERT INTO article_discovery_saved_queries'):
            return self._insert(p)
        if normalized.startswith('SELECT * FROM article_discovery_saved_queries WHERE id'):
            return self._select_by_id(p)
        if normalized.startswith('SELECT * FROM article_discovery_saved_queries'):
            return self._select_all(normalized, p)
        if normalized.startswith('UPDATE article_discovery_saved_queries SET name'):
            return self._update(p)
        if normalized.startswith('UPDATE article_discovery_saved_queries SET is_active'):
            return self._set_active(p)
        if normalized.startswith('UPDATE article_discovery_saved_queries SET last_used_at'):
            return self._touch_last_used(p)
        raise AssertionError('unhandled SQL in fake cursor: %r' % normalized)

    def _row_by_id(self, query_id):
        return next((r for r in self.store['rows'] if r['id'] == query_id), None)

    def _insert(self, p):
        (name, created_by, keyword, from_date, to_date, oak_field_id,
         providers, result_limit) = p
        row_id = self.store['next_id']
        self.store['next_id'] += 1
        row = {
            'id': row_id, 'name': name, 'created_by': created_by, 'keyword': keyword,
            'from_date': from_date, 'to_date': to_date, 'oak_field_id': oak_field_id,
            'providers': json.loads(providers), 'result_limit': result_limit,
            'is_active': True, 'created_at': None, 'updated_at': None,
            'last_used_at': None,
        }
        self.store['rows'].append(row)
        self.description = [('id',)]
        self._result = [(row_id,)]

    def _select_by_id(self, p):
        row = self._row_by_id(p[0])
        self.description = [(c,) for c in COLUMNS]
        self._result = [tuple(row[c] for c in COLUMNS)] if row else []

    def _select_all(self, normalized, p):
        rows = list(self.store['rows'])
        if 'WHERE is_active' in normalized:
            rows = [r for r in rows if r['is_active']]
        rows.sort(key=lambda r: (not r['is_active'], -r['id']))
        limit = p[-1] if p else len(rows)
        rows = rows[:limit]
        self.description = [(c,) for c in COLUMNS]
        self._result = [tuple(r[c] for c in COLUMNS) for r in rows]

    def _update(self, p):
        (name, keyword, from_date, to_date, oak_field_id, providers,
         result_limit, query_id) = p
        row = self._row_by_id(query_id)
        self.description = [('id',)]
        if row is None:
            self._result = []
            return
        row.update(name=name, keyword=keyword, from_date=from_date, to_date=to_date,
                  oak_field_id=oak_field_id, providers=json.loads(providers),
                  result_limit=result_limit)
        self._result = [(query_id,)]

    def _set_active(self, p):
        is_active, query_id = p
        row = self._row_by_id(query_id)
        self.description = [('id',)]
        if row is None:
            self._result = []
            return
        row['is_active'] = is_active
        self._result = [(query_id,)]

    def _touch_last_used(self, p):
        (query_id,) = p
        row = self._row_by_id(query_id)
        self.description = None
        self._result = []
        if row is not None:
            row['last_used_at'] = 'touched'

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)

    def close(self):
        return None


class FakeSavedQueriesConnection:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return FakeSavedQueriesCursor(self.store)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        return None


__all__ = ['FakeSavedQueriesCursor', 'FakeSavedQueriesConnection', 'new_store']
