"""Tests for scripts/rescrape_broken_records.py — parsing + comparison.

Offline by design: every test runs against saved HTML in tests/fixtures/ and no
test opens a socket or a database connection. A guard test asserts the module
contains no write SQL at all, because Phase 1 must be read-only.

NOTE on the fixtures: they encode the OAK announcement *label* structure that
scripts/../scraper.py already parses (Диссертация мавзуси…, Илмий раҳбар…), not
a byte-for-byte copy of a live page — nobody could reach oak.uz from the machine
this was written on. Validate the parser against real pages with

    python3 scripts/rescrape_broken_records.py --inspect <page_id>

after the first fetch, and extend these fixtures with anything that surprises.

Run:
    python3 -m unittest tests.test_rescrape_broken_records -v
"""
from __future__ import annotations

import csv
import io
import os
import sys
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))

import rescrape_broken_records as R  # noqa: E402

FIXTURES = os.path.join(_REPO_ROOT, "tests", "fixtures")


def fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


FULL_PAGE = fixture("oak_page_full.html")
EMPTY_PAGE = fixture("oak_page_empty.html")


def db_row(**kw):
    base = {"id": 1, "sana": "", "daraja": "", "olim": "", "mavzu": "",
            "ixtisoslik": "", "muassasa": "", "ilmiy_rahbar": "",
            "link": "https://oak.uz/pages/4321"}
    base.update(kw)
    return base


# ── parsing ─────────────────────────────────────────────────────────────────
class ParsePageTests(unittest.TestCase):
    def setUp(self):
        self.values = R.parse_page(FULL_PAGE)

    def test_title_is_taken_from_heading(self):
        self.assertIn("Каримов Карим", self.values["_title"])

    def test_author_extracted_from_title(self):
        self.assertEqual(self.values["olim"], "Каримов Карим Каримович")

    def test_topic_extracted(self):
        self.assertIn("нейрон тармоқлар", self.values["mavzu"])

    def test_supervisor_extracted(self):
        self.assertIn("Юсупов Равшан", self.values["ilmiy_rahbar"])

    def test_organization_extracted(self):
        self.assertIn("ахборот технологиялари", self.values["muassasa"])

    def test_specialty_codes_extracted(self):
        self.assertIn("05.01.04", self.values["ixtisoslik"])

    def test_degree_detected(self):
        self.assertEqual(self.values["daraja"], "PhD")

    def test_labelled_date_wins_over_footer_date(self):
        """The footer carries 01.01.2000; the labelled defence date must win."""
        self.assertEqual(self.values["sana"], "14.02.2020")

    def test_footer_and_nav_are_stripped(self):
        text = R.page_text(FULL_PAGE)
        self.assertNotIn("Барча ҳуқуқлар", text)
        self.assertNotIn("Бош саҳифа", text)


class DegeneratePageTests(unittest.TestCase):
    def test_empty_page_yields_empty_fields_without_raising(self):
        values = R.parse_page(EMPTY_PAGE)
        for column, key, _norm, _label in R.FIELDS:
            self.assertEqual(values.get(key, ""), "",
                             f"{column} should be empty on a contentless page")

    def test_garbage_html_does_not_raise(self):
        for html in ("", "<html>", "not html at all", "<p>" * 500):
            with self.subTest(html=html[:20]):
                R.parse_page(html)


class DateExtractionTests(unittest.TestCase):
    def test_prefers_labelled_date(self):
        text = "Эълон 05.05.2019\nҲимоя санаси: 14.02.2020\nБошқа 01.01.2000"
        self.assertEqual(R.extract_defense_date(text), "14.02.2020")

    def test_falls_back_to_first_date(self):
        self.assertEqual(R.extract_defense_date("Матн 09.09.2021 давоми"),
                         "09.09.2021")

    def test_no_date(self):
        self.assertEqual(R.extract_defense_date("сана йўқ"), "")


class PageIdTests(unittest.TestCase):
    def test_valid_links(self):
        for link in ("https://oak.uz/pages/4321",
                     "http://oak.uz/pages/4321",
                     "https://www.oak.uz/pages/4321",
                     "https://oak.uz/pages/4321?x=1"):
            with self.subTest(link=link):
                self.assertEqual(R.page_id_from_link(link), "4321")

    def test_rejects_other_hosts(self):
        """The fetcher must never be pointed at a host we did not intend."""
        for link in ("https://evil.example/pages/4321",
                     "https://oak.uz.evil.example/pages/1",
                     "https://olimlar.uz/pages/4321",
                     "/pages/4321", "", None):
            with self.subTest(link=link):
                self.assertIsNone(R.page_id_from_link(link))


# ── comparison ──────────────────────────────────────────────────────────────
def status_for(field, findings):
    return next(f["status"] for f in findings if f["field"] == field)


class CompareTests(unittest.TestCase):
    def test_fill_when_db_empty_and_page_has_value(self):
        findings = R.compare_record(db_row(), R.parse_page(FULL_PAGE))
        self.assertEqual(status_for("mavzu", findings), R.FILL)
        self.assertEqual(status_for("olim", findings), R.FILL)
        self.assertEqual(status_for("sana", findings), R.FILL)

    def test_match_when_identical(self):
        page = R.parse_page(FULL_PAGE)
        findings = R.compare_record(db_row(mavzu=page["mavzu"]), page)
        self.assertEqual(status_for("mavzu", findings), R.MATCH)

    def test_conflict_when_both_present_and_different(self):
        page = R.parse_page(FULL_PAGE)
        findings = R.compare_record(db_row(mavzu="Butunlay boshqa mavzu nomi"), page)
        self.assertEqual(status_for("mavzu", findings), R.CONFLICT)

    def test_missing_on_page(self):
        findings = R.compare_record(db_row(mavzu="Bazadagi mavzu"),
                                    R.parse_page(EMPTY_PAGE))
        self.assertEqual(status_for("mavzu", findings), R.MISSING_ON_PAGE)

    def test_empty_both(self):
        findings = R.compare_record(db_row(), R.parse_page(EMPTY_PAGE))
        self.assertEqual(status_for("mavzu", findings), R.EMPTY_BOTH)

    def test_cosmetic_difference_is_not_a_conflict(self):
        """Apostrophe and whitespace variants must not show up as CONFLICT."""
        page = dict(R.parse_page(FULL_PAGE), mavzu="Sun'iy   intellekt asoslari")
        findings = R.compare_record(db_row(mavzu="Sunʼiy intellekt asoslari"), page)
        self.assertEqual(status_for("mavzu", findings), R.MATCH)

    def test_alphabet_difference_is_not_a_conflict(self):
        """The DB is mostly Cyrillic; a Latin row against a Cyrillic page is a
        script difference, not a disagreement."""
        page = dict(R.parse_page(FULL_PAGE), ilmiy_rahbar="Юсупов Равшан")
        findings = R.compare_record(db_row(ilmiy_rahbar="Yusupov Ravshan"), page)
        finding = next(f for f in findings if f["field"] == "ilmiy_rahbar")
        self.assertEqual(finding["status"], R.MATCH)
        self.assertIn("alifbo", finding["note"])

    def test_date_format_difference_is_not_a_conflict(self):
        page = dict(R.parse_page(FULL_PAGE), sana="14.02.2020")
        findings = R.compare_record(db_row(sana="2020-02-14"), page)
        self.assertEqual(status_for("sana", findings), R.MATCH)

    def test_specialty_order_difference_is_not_a_conflict(self):
        page = dict(R.parse_page(FULL_PAGE), ixtisoslik="05.01.04, 05.01.02")
        findings = R.compare_record(db_row(ixtisoslik="05.01.02; 05.01.04"), page)
        self.assertEqual(status_for("ixtisoslik", findings), R.MATCH)

    def test_placeholder_supervisor_counts_as_empty(self):
        """PLACEHOLDER_SUPERVISOR rows are the point of the exercise — a
        template must read as FILL, never CONFLICT."""
        page = dict(R.parse_page(FULL_PAGE), ilmiy_rahbar="Юсупов Равшан")
        findings = R.compare_record(db_row(ilmiy_rahbar="Ф.И.Ш"), page)
        self.assertEqual(status_for("ilmiy_rahbar", findings), R.FILL)

    def test_every_field_is_reported_once(self):
        findings = R.compare_record(db_row(), R.parse_page(FULL_PAGE))
        fields = [f["field"] for f in findings]
        self.assertEqual(sorted(fields), sorted(c for c, _k, _n, _l in R.FIELDS))
        self.assertEqual(len(fields), len(set(fields)))


# ── cache / resume ──────────────────────────────────────────────────────────
class CacheTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig_cache = R.CACHE_DIR
        R.CACHE_DIR = self._tmp.name
        self.addCleanup(lambda: setattr(R, "CACHE_DIR", self._orig_cache))

    def test_cached_page_is_not_refetched(self):
        with open(R.cache_path("999"), "w", encoding="utf-8") as f:
            f.write(FULL_PAGE)

        def explode(*a, **kw):                       # any network use fails here
            raise AssertionError("network was used despite a warm cache")

        orig = R.requests.get
        R.requests.get = explode
        self.addCleanup(lambda: setattr(R.requests, "get", orig))

        html, error = R.fetch_page("999", {}, refresh=False)
        self.assertIsNone(error)
        self.assertIn("Каримов", html)

    def test_permanent_error_is_not_retried(self):
        def explode(*a, **kw):
            raise AssertionError("a permanent 404 must not be re-fetched")

        orig = R.requests.get
        R.requests.get = explode
        self.addCleanup(lambda: setattr(R.requests, "get", orig))

        log = {"888": {"status": 404, "permanent": True, "error": "HTTP 404"}}
        html, error = R.fetch_page("888", log, refresh=False)
        self.assertIsNone(html)
        self.assertIn("404", error)

    def test_fetch_log_roundtrip(self):
        entries = {"1": {"status": "ok", "permanent": False}}
        R.FETCH_LOG = os.path.join(self._tmp.name, "_fetch_log.json")
        R.save_fetch_log(entries)
        self.assertEqual(R.load_fetch_log(), entries)


# ── report ──────────────────────────────────────────────────────────────────
class ReportTests(unittest.TestCase):
    def _findings(self):
        page = R.parse_page(FULL_PAGE)
        out = R.compare_record(db_row(id=11), page)
        out += R.compare_record(db_row(id=22, mavzu="Butunlay boshqa mavzu"), page)
        for f in out:
            f["page_id"] = "4321"
        return out

    def test_markdown_report_has_the_required_sections(self):
        md = R.build_report(self._findings(),
                            [{"record_id": 33, "page_id": "9", "reason": "HTTP 404"}],
                            {"selected": 3, "with_page": 3, "ok": 2,
                             "from_cache": 1, "downloaded": 1,
                             "codes": ["MISSING_TITLE"]},
                            run_id=7, target="host/staging")
        self.assertIn("1-FAZA", md)
        self.assertIn("run_id=7", md)
        self.assertIn("CONFLICT", md)
        self.assertIn("Tiklab bo'lmaganlar", md)
        self.assertIn("HTTP 404", md)
        self.assertIn("33", md)

    def test_conflicting_values_are_listed_with_both_sides(self):
        md = R.build_report(self._findings(), [],
                            {"selected": 2, "with_page": 2, "ok": 2,
                             "from_cache": 0, "downloaded": 2, "codes": []},
                            run_id=7, target="host/staging")
        self.assertIn("Butunlay boshqa mavzu", md)      # eski
        self.assertIn("нейрон тармоқлар", md)           # yangi

    def test_pipe_in_value_cannot_break_the_table(self):
        cell = R._md_cell("a | b")
        self.assertNotIn(" | ", cell.replace("\\|", ""))

    def test_csv_has_a_row_per_field_plus_errors(self):
        import tempfile
        findings = self._findings()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.csv")
            R.write_csv(path, findings,
                        [{"record_id": 33, "page_id": "9", "reason": "timeout"}])
            with open(path, encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), len(findings) + 1)
        self.assertEqual(rows[0]["record_id"], "11")
        self.assertEqual(rows[-1]["status"], "FETCH_ERROR")
        self.assertEqual(rows[-1]["note"], "timeout")


# ── SQL safety ──────────────────────────────────────────────────────────────
def _code_string_literals(source: str):
    """Every string constant in the module except docstrings.

    Docstrings are excluded on purpose: this module's docstring quotes the
    SELECT it runs and the words UPDATE/INSERT while explaining where they
    happen, and prose must not trip a check aimed at executable SQL.
    """
    import ast
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value not in docstrings:
                yield node.value


class NoInlineWriteSqlTests(unittest.TestCase):
    """Phase 2 added a write path, but the module still holds no SQL that
    writes: every UPDATE/INSERT goes through data_quality/repository.py, where
    the target table and column are checked against the SCOPE_RESCRAPE
    allowlist. These tests fail the moment someone hand-rolls a write here."""

    def test_module_contains_no_write_sql(self):
        import re
        verb = re.compile(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|CREATE)\b",
                          re.IGNORECASE)
        # A bare verb is not enough — `truncate()` is a display helper. Require
        # it to sit next to something that makes it SQL.
        sqlish = re.compile(r"\b(FROM|INTO|SET|TABLE|VALUES)\b", re.IGNORECASE)
        with open(R.__file__, encoding="utf-8") as f:
            source = f.read()
        for literal in _code_string_literals(source):
            if verb.search(literal) and sqlish.search(literal):
                self.fail(f"write SQL found in a string literal: {literal!r}")

    def test_only_select_statements_are_executed(self):
        import re
        with open(R.__file__, encoding="utf-8") as f:
            source = f.read()
        executed = re.findall(r"cur\.execute\(\s*\n?\s*[\"']([^\"']+)", source)
        self.assertTrue(executed, "expected to find the SELECT statements")
        for sql in executed:
            self.assertTrue(sql.strip().upper().startswith("SELECT"), sql)

    def test_writes_only_reach_the_db_through_the_repository(self):
        """The only names that may mutate a record are the repository helpers
        (which enforce the allowlist), never a raw cursor in this module."""
        import re
        with open(R.__file__, encoding="utf-8") as f:
            source = f.read()
        writers = re.findall(r"\brepo\.(\w+)\(", source)
        allowed = {"create_run", "complete_run", "log_fix", "update_record_field",
                   "fetch_current_value", "record_issue_values", "mark_issue_fixed"}
        self.assertTrue(writers, "expected the apply path to call the repository")
        self.assertTrue(set(writers) <= allowed,
                        f"unexpected repository call(s): {set(writers) - allowed}")

if __name__ == "__main__":
    unittest.main()
