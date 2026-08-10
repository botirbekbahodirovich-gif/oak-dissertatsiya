# -*- coding: utf-8 -*-
"""Qalbaki psycopg2 kursuri — scheduled article discovery uchun
(Phase 2D.3). `tests/fake_article_discovery_pg.py:FakeDiscoveryCursor`ni
KENGAYTIRADI (u allaqachon `article_discovery_saved_queries` SQL'ini
tushunadi, Phase 2D.2) — faqat rejalashtirishga xos SQL'ni (advisory lock,
`FOR UPDATE SKIP LOCKED` band qilish, `article_discovery_runs`) qo'shadi.

`next_run_at <= NOW()` kabi vaqt taqqoslash — Postgres-ga xos EMAS (oddiy
qator filtri), shuning uchun bu yerda Python tomonida ishonchli
simulyatsiya qilinadi (metadata-sync scheduler fixture'idagi `LEFT JOIN
LATERAL`dan farqli, u haqiqatan Postgres-ga xos edi va fake qilinmagan edi).
Haqiqiy PARALLEL `FOR UPDATE SKIP LOCKED` poyga xatti-harakati esa baribir
FAQAT haqiqiy Postgres ustida (`tests/test_article_discovery_scheduler_
dbgated.py`) tekshiriladi — bu yerdagi fake band qilish DETERMINISTIK
(bitta jarayon ichida), poyga holatini isbotlamaydi.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from tests.fake_article_discovery_pg import FakeDiscoveryConnection, FakeDiscoveryCursor
from tests.fake_article_discovery_pg import new_store as _base_new_store

RUN_COLUMNS = ['id', 'saved_query_id', 'trigger_type', 'status', 'started_at',
              'finished_at', 'providers', 'found_count', 'new_count',
              'queued_count', 'duplicate_count', 'no_doi_count', 'error_summary',
              'import_batch_id']


def new_store():
    store = _base_new_store()
    store['runs'] = []
    store['next_run_id'] = 1
    store['lock_held'] = False
    return store


def _now():
    return datetime.now(timezone.utc)


class DiscoverySchedulerCursor(FakeDiscoveryCursor):
    def _dispatch(self, sql, p):
        if sql.startswith('SELECT pg_try_advisory_lock'):
            if self.store.get('lock_held', False):
                return [(False,)], [('pg_try_advisory_lock',)]
            self.store['lock_held'] = True
            return [(True,)], [('pg_try_advisory_lock',)]
        if sql.startswith('SELECT pg_advisory_unlock'):
            self.store['lock_held'] = False
            return [(True,)], [('pg_advisory_unlock',)]

        # Faqat HAQIQIY scheduler-ga xos naqshlar shu yerda (§claim/due/lock)
        # — oddiy schedule_enabled/disabled/record_run_outcome UPDATE'lari
        # endi bazaviy `FakeDiscoveryCursor`da (`fake_article_discovery_pg.
        # py`) — admin route testlari HAM shularni ishlatadi, dublikat
        # DDL/mantiq YO'Q.
        if ('article_discovery_saved_queries' in sql
                and 'schedule_enabled AND is_active AND next_run_at' in sql):
            return self._due_queries(sql, p)
        if sql.startswith('UPDATE article_discovery_saved_queries SET '
                          'next_run_at = NOW() +'):
            return self._claim_hold(p)

        if sql.startswith('INSERT INTO article_discovery_runs'):
            return self._insert_run(p)
        if sql.startswith('UPDATE article_discovery_runs SET'):
            return self._finalize_run(p)
        if sql.startswith('SELECT r.*, q.name AS saved_query_name'):
            return self._list_runs(sql, p)

        return super()._dispatch(sql, p)

    # ── due / claim ──────────────────────────────────────────────────────

    def _due_rows(self):
        now = _now()
        return [r for r in self.store['saved_queries']
               if r.get('schedule_enabled') and r.get('is_active')
               and r.get('next_run_at') is not None and r['next_run_at'] <= now]

    def _due_queries(self, sql, p):
        rows = sorted(self._due_rows(), key=lambda r: r['next_run_at'])
        limit = p[-1]
        rows = rows[:limit]
        if 'name' in sql and 'FOR UPDATE' not in sql:
            return [(r['id'], r['name']) for r in rows], [('id',), ('name',)]
        return [(r['id'],) for r in rows], [('id',)]

    def _claim_hold(self, p):
        minutes, ids = p
        far_future = _now().replace(year=_now().year + 1)
        for row in self.store['saved_queries']:
            if row['id'] in ids:
                row['next_run_at'] = far_future
        return [], None

    # ── article_discovery_runs ───────────────────────────────────────────

    def _insert_run(self, p):
        saved_query_id, trigger_type, status, providers = p
        run_id = self.store['next_run_id']
        self.store['next_run_id'] += 1
        row = {c: None for c in RUN_COLUMNS}
        row.update(id=run_id, saved_query_id=saved_query_id, trigger_type=trigger_type,
                  status=status, started_at=_now(), providers=json.loads(providers),
                  found_count=0, new_count=0, queued_count=0, duplicate_count=0,
                  no_doi_count=0)
        self.store['runs'].append(row)
        return [(run_id,)], [('id',)]

    def _finalize_run(self, p):
        (status, found, new, queued, duplicate, no_doi, error_summary,
         import_batch_id, run_id) = p
        row = next((r for r in self.store['runs'] if r['id'] == run_id), None)
        if row is not None:
            row.update(status=status, finished_at=_now(), found_count=found,
                      new_count=new, queued_count=queued, duplicate_count=duplicate,
                      no_doi_count=no_doi, error_summary=error_summary,
                      import_batch_id=import_batch_id)
        return [], None

    def _list_runs(self, sql, p):
        names = {r['id']: r['name'] for r in self.store['saved_queries']}
        rows = list(self.store['runs'])
        if 'WHERE r.saved_query_id' in sql:
            saved_query_id, limit = p
            rows = [r for r in rows if r['saved_query_id'] == saved_query_id]
        else:
            (limit,) = p
        rows.sort(key=lambda r: r['started_at'], reverse=True)
        rows = rows[:limit]
        cols = RUN_COLUMNS + ['saved_query_name']
        out = []
        for r in rows:
            values = [r[c] for c in RUN_COLUMNS] + [names.get(r['saved_query_id'])]
            out.append(tuple(values))
        return out, [(c,) for c in cols]


class DiscoverySchedulerConnection(FakeDiscoveryConnection):
    def cursor(self):
        return DiscoverySchedulerCursor(self.store)


__all__ = ['DiscoverySchedulerCursor', 'DiscoverySchedulerConnection', 'new_store']
