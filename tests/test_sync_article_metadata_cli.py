# -*- coding: utf-8 -*-
"""`scripts/sync_article_metadata.py` — CLI argument/exit-code testlari
(Phase 2C.3C, §39). DB'ga ULANMAYDI — `run_scheduled_metadata_sync`
`unittest.mock.patch` bilan almashtiriladi.
"""
import importlib
import os
import sys
import unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

cli = importlib.import_module('scripts.sync_article_metadata')

from services.article_sync.scheduler import SchedulerAlreadyRunningError, \
    SchedulerRunResult


class _FakeConn:
    def close(self):
        pass


class MissingDatabaseUrlTests(unittest.TestCase):
    def test_exits_2_without_database_url(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(cli.main(['sync_article_metadata.py']), 2)


class InvalidConfigTests(unittest.TestCase):
    def test_exits_2_on_invalid_config(self):
        env = {'DATABASE_URL': 'postgres://x',
              'ARTICLE_METADATA_SYNC_BATCH_SIZE': '-1'}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(cli.main(['sync_article_metadata.py']), 2)


class InvalidLimitTests(unittest.TestCase):
    def test_exits_2_on_non_positive_limit(self):
        env = {'DATABASE_URL': 'postgres://x'}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                cli.main(['sync_article_metadata.py', '--limit', '0']), 2)


class DisabledSchedulerTests(unittest.TestCase):
    def test_exits_0_when_disabled_and_not_dry_run(self):
        env = {'DATABASE_URL': 'postgres://x',
              'ARTICLE_METADATA_SYNC_ENABLED': 'false'}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch('psycopg2.connect') as connect:
                code = cli.main(['sync_article_metadata.py'])
        connect.assert_not_called()
        self.assertEqual(code, 0)


class AlreadyRunningTests(unittest.TestCase):
    def test_exits_0_when_another_run_holds_the_lock(self):
        env = {'DATABASE_URL': 'postgres://x'}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch('psycopg2.connect', return_value=_FakeConn()):
                with mock.patch.object(
                        cli, 'run_scheduled_metadata_sync',
                        side_effect=SchedulerAlreadyRunningError('busy')):
                    code = cli.main(['sync_article_metadata.py'])
        self.assertEqual(code, 0)


class SuccessAndFailureExitCodeTests(unittest.TestCase):
    def _run_with_result(self, result):
        env = {'DATABASE_URL': 'postgres://x'}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch('psycopg2.connect', return_value=_FakeConn()):
                with mock.patch.object(cli, 'run_scheduled_metadata_sync',
                                       return_value=result):
                    return cli.main(['sync_article_metadata.py'])

    def test_exits_0_on_clean_completion(self):
        result = SchedulerRunResult(
            dry_run=False, trigger_type='scheduled', total_selected=5,
            run_id=1, status='completed', processed=5, success=5)
        self.assertEqual(self._run_with_result(result), 0)

    def test_exits_1_on_completed_with_errors(self):
        result = SchedulerRunResult(
            dry_run=False, trigger_type='scheduled', total_selected=5,
            run_id=1, status='completed_with_errors', processed=5, success=4,
            failed=1)
        self.assertEqual(self._run_with_result(result), 1)


class DryRunOutputTests(unittest.TestCase):
    def test_dry_run_exits_0_and_does_not_check_enabled_flag(self):
        result = SchedulerRunResult(dry_run=True, trigger_type='manual_cli',
                                    total_selected=2, article_ids=[1, 2])
        env = {'DATABASE_URL': 'postgres://x',
              'ARTICLE_METADATA_SYNC_ENABLED': 'false'}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch('psycopg2.connect', return_value=_FakeConn()):
                with mock.patch.object(cli, 'run_scheduled_metadata_sync',
                                       return_value=result):
                    code = cli.main(['sync_article_metadata.py', '--dry-run'])
        self.assertEqual(code, 0)


if __name__ == '__main__':
    unittest.main()
