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


def _db_down():
    """Make every get_connection() import site raise, like a dropped DB."""
    from contextlib import ExitStack
    stack = ExitStack()
    for target in ("data.get_connection", "blueprints.advisors.get_connection",
                   "blueprints.councils.get_connection", "blueprints.ranking.get_connection"):
        stack.enter_context(patch(target, side_effect=RuntimeError("db down")))
    return stack


class EmptyResultCacheTests(unittest.TestCase):
    """A DB error must never pin an empty/partial result in flask_cache."""

    def _cases(self):
        import data
        from blueprints import advisors, councils, ranking
        return [
            ("gender_stats_v1", app_module.compute_gender_stats),
            ("trends_data_v1", app_module._trends_data),
            ("clustering_v1", app_module._clustering_build),
            ("heatmap_data_v1", app_module._compute_heatmap_data),
            ("university_stats", app_module.get_university_dissertation_stats),
            ("institution_directory", app_module.get_institution_directory),
            ("supervisor_counts", data.get_supervisor_counts),
            ("ixtisoslik_individual_count", data.count_distinct_ixtisosliklar),
            ("advisor_index", advisors._advisor_index),
            ("council_index", councils._council_index),
            ("ranking_latest_scholars_v1", lambda: ranking._latest_snapshot("scholars")),
            ("ranking_scholars_7_v1", lambda: ranking._scholars_of(7)),
            ("ranking_institutions_7_v1", lambda: ranking._institutions_of(7)),
            ("ranking_researchers_scopus_v1", lambda: ranking._ranked_researchers("scopus")),
            ("ranking_lookup_scholars_v1", lambda: ranking.get_scholar_h_index("Karimov")),
            ("ranking_lookup_institutions_v1", lambda: ranking.get_institution_h_index("TATU")),
            ("ranking_top_scholars_v1", ranking.get_top_scholars),
        ]

    def setUp(self):
        self.ctx = app_module.app.test_request_context("/")
        self.ctx.push()
        app_module.cache.clear()

    def tearDown(self):
        app_module.cache.clear()
        self.ctx.pop()

    def test_db_error_results_are_not_cached(self):
        for key, fn in self._cases():
            with self.subTest(key=key), _db_down():
                fn()
                self.assertIsNone(app_module.cache.get(key))

    def test_ixtisoslik_list_raises_instead_of_caching_empty(self):
        import data
        with _db_down():
            with self.assertRaises(RuntimeError):
                data.list_individual_ixtisosliklar()
        self.assertIsNone(app_module.cache.get("ixtisoslik_individual_list"))
        # /mavzu-tahlili already degrades to an empty dropdown
        with _db_down():
            self.assertEqual(app_module._compare_ixtisosliklar(), [])

    def test_ranking_caches_again_once_db_is_back(self):
        from blueprints import ranking
        with patch.object(ranking, "_safe_rows", return_value=[]):
            ranking._latest_snapshot("scholars")
        self.assertEqual(app_module.cache.get("ranking_latest_scholars_v1"), ())

    def test_genealogy_error_response_is_not_cached(self):
        client = app_module.app.test_client()
        with patch.object(app_module, "_genealogy_tree", side_effect=RuntimeError("db down")):
            self.assertEqual(client.get("/api/genealogy/Karimov").status_code, 500)
        with patch.object(app_module, "_genealogy_tree",
                          return_value={"name": "Karimov", "children": []}):
            self.assertEqual(client.get("/api/genealogy/Karimov").status_code, 200)


class CspAnalyticsTests(unittest.TestCase):
    def _directives(self):
        import security
        return {part.split(" ", 1)[0]: part.split(" ")[1:] for part in security._CSP.split("; ")}

    def test_cloudflare_web_analytics_allowed(self):
        d = self._directives()
        self.assertIn("https://static.cloudflareinsights.com", d["script-src"])
        self.assertIn("https://cloudflareinsights.com", d["connect-src"])

    def test_yandex_metrica_allowed(self):
        d = self._directives()
        self.assertIn("https://mc.yandex.ru", d["script-src"])
        for host in ("https://mc.yandex.ru", "https://mc.yandex.md", "https://mc.yandex.com"):
            self.assertIn(host, d["connect-src"])
        self.assertIn("https://mc.yandex.ru", d["frame-src"])

    def test_no_wildcards_added(self):
        for name, sources in self._directives().items():
            self.assertNotIn("*", " ".join(sources), name)


if __name__ == "__main__":
    unittest.main()
