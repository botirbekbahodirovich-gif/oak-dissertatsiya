"""Stability regressions: pooled connections, empty-result caching, CSP sources."""
import os
import unittest
from unittest.mock import patch


os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("SESSION_SECRET", "barqarorlik-test-secret")
# Empty values prevent python-dotenv from inheriting a parent workspace's .env.
os.environ["DATABASE_URL"] = ""
os.environ["POSTGRES_URL"] = ""

import app as app_module


class _FailingCursor:
    def execute(self, *args, **kwargs):
        raise RuntimeError("cannot execute INSERT in a read-only transaction")

    def close(self):
        pass


class _TrackedConn:
    def __init__(self):
        self.closed_calls = 0
        self.rollbacks = 0

    def cursor(self, *args, **kwargs):
        return _FailingCursor()

    def commit(self):
        raise AssertionError("commit after a failed INSERT")

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed_calls += 1


class TrackVisitTests(unittest.TestCase):
    def test_failed_insert_returns_connection_to_pool(self):
        conns = []

        def fake_get_connection():
            conns.append(_TrackedConn())
            return conns[-1]

        with app_module.app.test_request_context("/robots.txt"):
            from flask import request
            request.url_rule = app_module.app.url_map.bind("localhost").match(
                "/robots.txt", return_rule=True)[0]
            with patch("data.get_connection", fake_get_connection):
                self.assertIsNone(app_module.track_visit())

        self.assertEqual(len(conns), 1)
        self.assertEqual(conns[0].rollbacks, 1)
        self.assertEqual(conns[0].closed_calls, 1)


if __name__ == "__main__":
    unittest.main()
