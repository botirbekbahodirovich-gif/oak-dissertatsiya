"""/collaboration ("Ilmiy hamkorlik") regressions: service worker, graph JS, year range."""
import os
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
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


if __name__ == "__main__":
    unittest.main()
