# -*- coding: utf-8 -*-
"""Qalbaki psycopg2 kursuri — scheduler orkestratsiyasi uchun
(Phase 2C.3C). `tests/fake_article_sync_pg.py`ni KENGAYTIRADI (qayta
yozilmaydi): article/sync dispatch mavjud fake'dan meros, faqat scheduler'ga
xos SQL (advisory lock, `article_metadata_sync_runs`) qo'shiladi — shu
tufayli "check() haqiqiy `check_article_metadata()` orqali, article
mutatsiya qilinmaydi" kabi integratsiya testlari ham ishlaydi (§36).
"""
from __future__ import annotations

from tests.fake_article_sync_pg import FakeSyncConnection, FakeSyncCursor


class SchedulerFakeCursor(FakeSyncCursor):
    def _dispatch(self, sql, p):
        if sql.startswith('SELECT pg_try_advisory_lock'):
            held = self.store.get('lock_held', False)
            if held:
                return [(False,)], [('pg_try_advisory_lock',)]
            self.store['lock_held'] = True
            return [(True,)], [('pg_try_advisory_lock',)]

        if sql.startswith('SELECT pg_advisory_unlock'):
            self.store['lock_held'] = False
            return [(True,)], [('pg_advisory_unlock',)]

        if sql.startswith('INSERT INTO article_metadata_sync_runs'):
            runs = self.store.setdefault('runs', {})
            next_id = self.store.get('next_run_id', 1)
            self.store['next_run_id'] = next_id + 1
            status, trigger_type, total_selected = p
            runs[next_id] = {
                'id': next_id, 'status': status, 'trigger_type': trigger_type,
                'total_selected': total_selected, 'processed_count': 0,
                'success_count': 0, 'partial_count': 0, 'failed_count': 0,
                'skipped_count': 0,
            }
            return [(next_id,)], [('id',)]

        if sql.startswith('UPDATE article_metadata_sync_runs SET'):
            status, processed, success, partial, failed, skipped, run_id = p
            row = self.store['runs'][run_id]
            row.update({'status': status, 'processed_count': processed,
                       'success_count': success, 'partial_count': partial,
                       'failed_count': failed, 'skipped_count': skipped})
            return [], None

        return super()._dispatch(sql, p)


class SchedulerFakeConnection(FakeSyncConnection):
    def cursor(self):
        return SchedulerFakeCursor(self.store)
