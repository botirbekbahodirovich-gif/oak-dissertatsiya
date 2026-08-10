# -*- coding: utf-8 -*-
"""`scripts/run_scheduled_article_discovery.py` — CLI argument/exit-code
testlari (Phase 2D.3). DB'ga ULANMAYDI —
`run_due_discovery_queries` `unittest.mock.patch` bilan almashtiriladi.
"""
import importlib
import os
import sys
import unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

cli = importlib.import_module('scripts.run_scheduled_article_discovery')

from services.article_discovery.scheduler import (QueryRunOutcome,
                                                   SchedulerAlreadyRunningError,
                                                   SchedulerCycleResult)
from services.article_discovery.scheduler_state import RUN_FAILED, RUN_SUCCESS


class _FakeConn:
    def close(self):
        pass


class MissingDatabaseUrlTests(unittest.TestCase):
    def test_exits_2_without_database_url(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(cli.main(['run_scheduled_article_discovery.py']), 2)


class InvalidConfigTests(unittest.TestCase):
    def test_exits_2_on_invalid_config(self):
        env = {'DATABASE_URL': 'postgres://x',
              'ARTICLE_DISCOVERY_SCHEDULER_MAX_QUERIES': '-1'}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(cli.main(['run_scheduled_article_discovery.py']), 2)


class InvalidLimitTests(unittest.TestCase):
    def test_exits_2_on_non_positive_limit(self):
        # ENABLED=true kerak — aks holda o'chirilgan-scheduler yo'li (§13
        # sukut FALSE) --limit tekshiruvidan OLDIN 0 bilan chiqadi (xuddi
        # `sync_article_metadata.py`dagi bilan bir xil tartib).
        env = {'DATABASE_URL': 'postgres://x',
              'ARTICLE_DISCOVERY_SCHEDULER_ENABLED': 'true'}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                cli.main(['run_scheduled_article_discovery.py', '--limit', '0']), 2)


class DisabledSchedulerTests(unittest.TestCase):
    def test_exits_0_when_disabled_and_not_dry_run(self):
        """§13/§25 — production-safe default: ENABLED yo'q bo'lsa
        ULANMAYDI ham."""
        env = {'DATABASE_URL': 'postgres://x'}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch('psycopg2.connect') as connect:
                code = cli.main(['run_scheduled_article_discovery.py'])
        connect.assert_not_called()
        self.assertEqual(code, 0)

    def test_dry_run_works_even_when_disabled(self):
        result = SchedulerCycleResult(dry_run=True, due=[])
        env = {'DATABASE_URL': 'postgres://x'}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch('psycopg2.connect', return_value=_FakeConn()):
                with mock.patch.object(cli, 'run_due_discovery_queries',
                                       return_value=result):
                    code = cli.main(['run_scheduled_article_discovery.py', '--dry-run'])
        self.assertEqual(code, 0)


class AlreadyRunningTests(unittest.TestCase):
    def test_exits_0_when_another_cycle_holds_the_lock(self):
        env = {'DATABASE_URL': 'postgres://x', 'ARTICLE_DISCOVERY_SCHEDULER_ENABLED': 'true'}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch('psycopg2.connect', return_value=_FakeConn()):
                with mock.patch.object(
                        cli, 'run_due_discovery_queries',
                        side_effect=SchedulerAlreadyRunningError('busy')):
                    code = cli.main(['run_scheduled_article_discovery.py'])
        self.assertEqual(code, 0)


class SuccessAndFailureExitCodeTests(unittest.TestCase):
    def _run_with_result(self, result):
        env = {'DATABASE_URL': 'postgres://x', 'ARTICLE_DISCOVERY_SCHEDULER_ENABLED': 'true'}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch('psycopg2.connect', return_value=_FakeConn()):
                with mock.patch.object(cli, 'run_due_discovery_queries',
                                       return_value=result):
                    return cli.main(['run_scheduled_article_discovery.py'])

    def test_exits_0_when_all_succeed(self):
        result = SchedulerCycleResult(
            dry_run=False, claimed=2,
            results=[QueryRunOutcome(saved_query_id=1, saved_query_name='A',
                                     status=RUN_SUCCESS),
                    QueryRunOutcome(saved_query_id=2, saved_query_name='B',
                                     status=RUN_SUCCESS)])
        self.assertEqual(self._run_with_result(result), 0)

    def test_exits_1_when_any_query_failed(self):
        result = SchedulerCycleResult(
            dry_run=False, claimed=2,
            results=[QueryRunOutcome(saved_query_id=1, saved_query_name='A',
                                     status=RUN_SUCCESS),
                    QueryRunOutcome(saved_query_id=2, saved_query_name='B',
                                     status=RUN_FAILED, error_summary='timeout')])
        self.assertEqual(self._run_with_result(result), 1)

    def test_exits_0_when_nothing_was_due(self):
        result = SchedulerCycleResult(dry_run=False, claimed=0, results=[])
        self.assertEqual(self._run_with_result(result), 0)


class DryRunOutputTests(unittest.TestCase):
    def test_dry_run_exits_0_and_does_not_check_enabled_flag(self):
        result = SchedulerCycleResult(
            dry_run=True, due=[{'id': 1, 'name': 'X'}, {'id': 2, 'name': 'Y'}])
        env = {'DATABASE_URL': 'postgres://x',
              'ARTICLE_DISCOVERY_SCHEDULER_ENABLED': 'false'}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch('psycopg2.connect', return_value=_FakeConn()):
                with mock.patch.object(cli, 'run_due_discovery_queries',
                                       return_value=result):
                    code = cli.main(['run_scheduled_article_discovery.py', '--dry-run'])
        self.assertEqual(code, 0)


if __name__ == '__main__':
    unittest.main()
