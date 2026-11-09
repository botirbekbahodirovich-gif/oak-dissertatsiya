"""/collaboration ("Ilmiy hamkorlik") regressions: service worker, graph JS, year range."""
import os
from pathlib import Path
import re
import unittest


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

    def test_template_has_message_slot(self):
        html = (ROOT / "templates" / "collaboration.html").read_text(encoding="utf-8")
        self.assertIn('class="cg-empty-msg"', html)


if __name__ == "__main__":
    unittest.main()
