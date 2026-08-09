# -*- coding: utf-8 -*-
"""Qalbaki psycopg2 kursuri — `services/article_discovery` bridge testlari
uchun (Phase 2D.1).

`tests/fake_article_import_pg.py:FakeImportCursor`ni KENGAYTIRADI (bridge
AYNAN o'sha jadvallarga yozadi) — faqat Phase 2D.1'da qo'shilgan yangi SQL
naqshlarini (`source_provider` bilan batch, `ON CONFLICT` bilan item,
faol-DOI qidiruvi) qo'shimcha talqin qiladi, qolgani bazaviy klassga
tushiriladi (dedup, batch counts va h.k. — takrorlanmaydi).
"""
from __future__ import annotations

import json

from services.article_import.state import ACTIVE_DOI_ITEM_STATUSES
from tests.fake_article_import_pg import (BATCH_COLUMNS, ITEM_COLUMNS,
                                          FakeImportConnection, FakeImportCursor,
                                          new_store)

DISCOVERY_BATCH_COLUMNS = BATCH_COLUMNS + [
    'source_provider', 'source_query', 'source_oak_field_id',
    'source_result_count', 'excluded_no_doi_count',
]
DISCOVERY_ITEM_COLUMNS = ITEM_COLUMNS + ['external_source_id']


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
        return super()._dispatch(sql, p)

    def _insert_discovery_batch(self, p):
        (created_by, status, source_provider, source_query, source_oak_field_id,
         source_result_count, excluded_no_doi_count) = p
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
            'total_items': 0, 'pending_count': 0, 'processing_count': 0,
            'review_count': 0, 'approved_count': 0, 'rejected_count': 0,
            'failed_count': 0, 'duplicate_count': 0, 'skipped_count': 0,
            'cancel_requested': False,
        })
        self.store['batches'].append(row)
        return [(batch_id,)], [('id',)]

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
