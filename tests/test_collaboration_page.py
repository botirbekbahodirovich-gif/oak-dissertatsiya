"""/collaboration ("Ilmiy hamkorlik") regressions: service worker, graph JS, year range."""
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("SESSION_SECRET", "collaboration-test-secret")
# Empty values prevent python-dotenv from inheriting a parent workspace's .env.
os.environ["DATABASE_URL"] = ""
os.environ["POSTGRES_URL"] = ""

import app as app_module

SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")


class ServiceWorkerTests(unittest.TestCase):
    def test_cross_origin_requests_are_not_intercepted(self):
        fetch_handler = SW[SW.index("addEventListener('fetch'"):]
        guard = fetch_handler.index("url.origin !== self.location.origin")
        self.assertLess(guard, fetch_handler.index("event.respondWith"))

    def test_cache_name_bumped_past_broken_version(self):
        self.assertNotIn("'olimlar-v2'", SW)
        self.assertRegex(SW, r"const CACHE_NAME = 'olimlar-v\d+';")

    def test_new_worker_takes_over_immediately(self):
        install = SW[SW.index("addEventListener('install'"):SW.index("addEventListener('activate'")]
        # skipWaiting must not depend on the precache succeeding
        self.assertLess(install.index("self.skipWaiting()"), install.index("event.waitUntil"))
        self.assertIn("self.clients.claim()", SW)

    def test_sw_route_is_never_cached(self):
        response = app_module.app.test_client().get("/sw.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-cache")
        self.assertEqual(response.headers["Service-Worker-Allowed"], "/")
        response.close()


def _js():
    return (ROOT / "static" / "js" / "collaboration.js").read_text(encoding="utf-8")


def _method(js, name):
    start = js.index("CollabGraph.prototype.%s = function" % name)
    end = js.find("CollabGraph.prototype.", start + 1)
    return js[start:end if end > 0 else len(js)]


class CollaborationJsTests(unittest.TestCase):
    def test_every_fetch_checks_response_ok(self):
        js = _js()
        self.assertNotIn("r.json(); })", js)
        self.assertEqual(js.count("fetch("), 1, "all requests go through getJSON")
        self.assertIn("if (!r.ok) throw", js)

    def test_load_draw_and_empty_have_distinct_messages(self):
        js = _js()
        for kind in ("empty", "load", "draw"):
            self.assertRegex(js, r"\b%s: '" % kind)
        self.assertIn("_empty(true, 'load')", _method(js, "loadFull"))
        self.assertIn("_empty(true, 'load')", _method(js, "loadCenter"))
        self.assertIn("_empty(true, 'draw')", _method(js, "render"))
        self.assertIn("_empty(true, 'empty')", _method(js, "render"))

    def test_empty_state_does_not_need_d3(self):
        self.assertNotIn("d3", _method(_js(), "_empty"))

    def test_edge_colours_match_checkbox_dots(self):
        js = _js()
        block = js[js.index("var EDGE_COLOR"):js.index("var ROLE_COLOR")]
        edge = dict(re.findall(r"(\w+): OlimlarCharts\.tokens\.(\w+)", block))
        html = (ROOT / "templates" / "collaboration.html").read_text(encoding="utf-8")
        dots = dict(re.findall(
            r'id="cg-t-(\w+)"[^>]*><span class="dot" style="background:var\(--color-([\w-]+)\);"', html))
        css_to_token = {"success": "success", "error": "error", "action-blue": "actionBlue"}
        self.assertEqual(set(edge), {"advisor", "opponent", "institution"})
        for kind, token in edge.items():
            self.assertEqual(token, css_to_token[dots[kind]], kind)

    def test_legend_is_not_duplicated(self):
        html = (ROOT / "templates" / "collaboration.html").read_text(encoding="utf-8")
        self.assertNotIn("cg-legend", html)
        self.assertEqual(html.count("ustoz–shogird</label>"), 1)

    def test_template_has_message_slot(self):
        html = (ROOT / "templates" / "collaboration.html").read_text(encoding="utf-8")
        self.assertIn('class="cg-empty-msg"', html)


ROWS = [
    # olim, daraja, rahbar, opp1, opp2, opp3, ixtisoslik, muassasa, sana
    ("Aliyev Bobur", "PhD", "Karimov Anvar", "Rasulov Sardor", None, None,
     "05.01.01", "TATU", "2001-05-12"),
    ("Tosheva Dilnoza", "DSc", "Karimov Anvar", None, None, None,
     "05.01.01", "TATU", "12.03.2024"),
]


class FakeCursor:
    def __init__(self, year_range):
        self.queries = []
        self._year_range = year_range

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.queries.append(" ".join(sql.split()))

    def fetchall(self):
        return list(ROWS)

    def fetchone(self):
        return self._year_range


class FakeConnection:
    def __init__(self, year_range=(2001, 2024)):
        self.cur = FakeCursor(year_range)

    def cursor(self):
        return self.cur

    def close(self):
        pass


class CollabIndexTests(unittest.TestCase):
    def setUp(self):
        app_module.cache.delete("collab_index_v2")
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.cache.delete("collab_index_v2")

    def test_year_range_comes_from_sana_substring(self):
        conn = FakeConnection((2001, 2024))
        with patch("data.get_connection", return_value=conn):
            idx = app_module._collab_index()
        self.assertEqual((idx["year_min"], idx["year_max"]), (2001, 2024))
        year_sql = [q for q in conn.cur.queries if "MIN(" in q]
        self.assertEqual(len(year_sql), 1)
        self.assertIn("substring(sana::text", year_sql[0])

    def test_api_returns_year_bounds(self):
        with patch("data.get_connection", return_value=FakeConnection((1998, 2025))):
            full = self.client.get("/api/collaboration?mode=full").get_json()
            ego = self.client.get("/api/collaboration?name=Karimov%20Anvar").get_json()
            missing = self.client.get("/api/collaboration?name=Yoq%20Odam").get_json()
        for payload in (full, ego, missing):
            self.assertEqual((payload["year_min"], payload["year_max"]), (1998, 2025))
        self.assertTrue(full["nodes"])

    def test_no_hardcoded_years_in_page(self):
        js = _js()
        html = (ROOT / "templates" / "collaboration.html").read_text(encoding="utf-8")
        self.assertNotIn("DEFAULT_Y", js)
        self.assertIn("d.year_min", js)
        self.assertIn("d.year_max", js)
        slider = html[html.index('class="cg-years"'):html.index('class="cg-stage"')]
        self.assertIsNone(re.search(r"(19|20)\d\d", slider))


if __name__ == "__main__":
    unittest.main()
