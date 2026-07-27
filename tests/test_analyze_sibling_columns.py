"""Tests for scripts/analyze_sibling_columns.py — pure counting/sampling logic.

Offline by design: no socket, no database. analyze() takes plain dicts, so the
whole tally and report-building path is tested against synthetic rows. A guard
test asserts the module contains no write SQL, mirroring
tests/test_rescrape_broken_records.py's Phase-1 safety checks — this module
must stay report-only.

Run:
    python3 -m unittest tests.test_analyze_sibling_columns -v
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))


def _load_script(name):
    path = os.path.join(_REPO_ROOT, "scripts", f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_script_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


A = _load_script("analyze_sibling_columns")


def row(**kw):
    base = {c: "" for c in A.SELECT_COLUMNS}
    base["id"] = kw.pop("id", 1)
    base.update(kw)
    return base


# ── blank / placeholder detection ───────────────────────────────────────────
class BlankDetectionTests(unittest.TestCase):
    def test_none_and_whitespace_are_blank(self):
        for v in (None, "", "   ", "\n"):
            self.assertTrue(A._is_effectively_blank("muassasa", v))

    def test_real_value_is_not_blank(self):
        self.assertFalse(A._is_effectively_blank("muassasa", "ToshDTU"))

    def test_supervisor_placeholder_counts_as_blank(self):
        for v in ("Ф.И.Ш.", "f.i.sh", "нинг ф.и.ш"):
            self.assertTrue(A._is_effectively_blank("ilmiy_rahbar", v))

    def test_placeholder_rule_is_specific_to_supervisor_field(self):
        # The same literal text in another field is NOT a placeholder there.
        self.assertFalse(A._is_effectively_blank("muassasa", "Ф.И.Ш."))

    def test_real_supervisor_name_is_not_blank(self):
        self.assertFalse(A._is_effectively_blank("ilmiy_rahbar", "Юсупов Равшан"))


# ── degree-text-in-name detection ───────────────────────────────────────────
class DegreeTextHintTests(unittest.TestCase):
    def test_dsc_parenthetical_is_flagged(self):
        self.assertTrue(A.looks_like_degree_text(
            "педагогика фанлари доктори (DSc)"))

    def test_phd_is_flagged(self):
        self.assertTrue(A.looks_like_degree_text("PhD"))

    def test_nomzodi_is_flagged(self):
        self.assertTrue(A.looks_like_degree_text("фанлари номзоди"))

    def test_real_name_is_not_flagged(self):
        for name in ("Юсупов Равшан Тошпўлатович", "А.Б. Каримов",
                     "Rashidov Vali Nabiyevich"):
            self.assertFalse(A.looks_like_degree_text(name))

    def test_blank_is_not_flagged(self):
        self.assertFalse(A.looks_like_degree_text(""))
        self.assertFalse(A.looks_like_degree_text(None))

    def test_placeholder_is_not_flagged(self):
        # Already counted as "blank" elsewhere — must not double-count here.
        self.assertFalse(A.looks_like_degree_text("Ф.И.Ш."))


# ── date hint ────────────────────────────────────────────────────────────────
class DateHintTests(unittest.TestCase):
    def test_full_date_is_detected(self):
        self.assertEqual(A.date_hint("Himoya 14.02.2020 boldi"), "14.02.2020")

    def test_full_date_wins_over_bare_year_fallback(self):
        self.assertEqual(A.date_hint("2020-yil 14.02.2020 himoya"), "14.02.2020")

    def test_bare_year_is_detected_as_fallback(self):
        self.assertEqual(A.date_hint("В2026.2.PhD/Iqt4897"), "2026")

    def test_no_date_returns_none(self):
        self.assertIsNone(A.date_hint("Iqt4897"))
        self.assertIsNone(A.date_hint(""))


# ── analyze(): sibling counting ─────────────────────────────────────────────
class AnalyzeSiblingCountingTests(unittest.TestCase):
    def test_counts_only_blank_target_rows(self):
        rows = [
            row(id=1, muassasa="", ilmiy_kengash="ToshDTU"),          # blank -> counts
            row(id=2, muassasa="Boshqa", ilmiy_kengash="ToshDTU"),    # not blank -> excluded
        ]
        result = A.analyze(rows)
        self.assertEqual(result["blank_count"]["muassasa"], 1)
        self.assertEqual(result["sibling_nonblank"]["muassasa"]["ilmiy_kengash"], 1)

    def test_blank_sibling_is_not_counted(self):
        rows = [row(id=1, muassasa="", ilmiy_kengash="", yetakchi_tashkilot="")]
        result = A.analyze(rows)
        self.assertEqual(result["blank_count"]["muassasa"], 1)
        self.assertEqual(result["sibling_nonblank"]["muassasa"]["ilmiy_kengash"], 0)
        self.assertEqual(result["sibling_nonblank"]["muassasa"]["yetakchi_tashkilot"], 0)

    def test_each_sibling_column_counted_independently(self):
        rows = [row(id=1, muassasa="", ilmiy_kengash="X", yetakchi_tashkilot="")]
        result = A.analyze(rows)
        self.assertEqual(result["sibling_nonblank"]["muassasa"]["ilmiy_kengash"], 1)
        self.assertEqual(result["sibling_nonblank"]["muassasa"]["yetakchi_tashkilot"], 0)

    def test_ixtisoslik_checks_its_own_siblings(self):
        rows = [row(id=1, ixtisoslik="", ixtisoslik_nomi="Iqtisodiyot",
                    fan_tarmoqi="Iqtisod fanlari")]
        result = A.analyze(rows)
        self.assertEqual(result["sibling_nonblank"]["ixtisoslik"]["ixtisoslik_nomi"], 1)
        self.assertEqual(result["sibling_nonblank"]["ixtisoslik"]["fan_tarmoqi"], 1)

    def test_sana_date_like_is_a_subset_of_nonblank(self):
        rows = [
            row(id=1, sana="", mavzu_raqami="В2026.2.PhD/Iqt4897"),   # year-like
            row(id=2, sana="", mavzu_raqami="no-date-here"),          # non-blank, not date-like
        ]
        result = A.analyze(rows)
        self.assertEqual(result["sibling_nonblank"]["sana"]["mavzu_raqami"], 2)
        self.assertEqual(result["sibling_date_like"]["mavzu_raqami"], 1)

    def test_placeholder_supervisor_counted_as_blank_for_sibling_check(self):
        rows = [row(id=1, ilmiy_rahbar="Ф.И.Ш.", ilmiy_rahbar_daraja="PhD")]
        result = A.analyze(rows)
        self.assertEqual(result["blank_count"]["ilmiy_rahbar"], 1)
        self.assertEqual(result["sibling_nonblank"]["ilmiy_rahbar"]["ilmiy_rahbar_daraja"], 1)

    def test_samples_are_capped(self):
        rows = [row(id=i, muassasa="", ilmiy_kengash=f"Org{i}") for i in range(50)]
        result = A.analyze(rows, sample_cap=5)
        self.assertEqual(len(result["samples"]["muassasa"]["ilmiy_kengash"]), 5)
        # Counting is not capped, only the sample list.
        self.assertEqual(result["sibling_nonblank"]["muassasa"]["ilmiy_kengash"], 50)

    def test_sample_records_id_and_value(self):
        rows = [row(id=42, muassasa="", ilmiy_kengash="ToshDTU")]
        result = A.analyze(rows)
        sample = result["samples"]["muassasa"]["ilmiy_kengash"][0]
        self.assertEqual(sample["id"], 42)
        self.assertEqual(sample["value"], "ToshDTU")

    def test_total_counts_every_row(self):
        rows = [row(id=1), row(id=2), row(id=3)]
        self.assertEqual(A.analyze(rows)["total"], 3)

    def test_empty_input_does_not_crash(self):
        result = A.analyze([])
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["blank_count"]["muassasa"], 0)


# ── analyze(): degree-in-name detection ─────────────────────────────────────
class AnalyzeDegreeInNameTests(unittest.TestCase):
    def test_flagged_and_daraja_blank(self):
        rows = [row(id=1, ilmiy_rahbar="педагогика фанлари доктори (DSc)",
                    ilmiy_rahbar_daraja="")]
        result = A.analyze(rows)
        self.assertEqual(result["degree_in_name"], 1)
        self.assertEqual(result["degree_in_name_daraja_blank"], 1)
        self.assertEqual(result["degree_in_name_daraja_filled"], 0)

    def test_flagged_and_daraja_also_filled(self):
        rows = [row(id=1, ilmiy_rahbar="PhD", ilmiy_rahbar_daraja="PhD")]
        result = A.analyze(rows)
        self.assertEqual(result["degree_in_name"], 1)
        self.assertEqual(result["degree_in_name_daraja_filled"], 1)
        self.assertEqual(result["degree_in_name_daraja_blank"], 0)

    def test_real_name_is_not_counted(self):
        rows = [row(id=1, ilmiy_rahbar="Юсупов Равшан", ilmiy_rahbar_daraja="")]
        result = A.analyze(rows)
        self.assertEqual(result["degree_in_name"], 0)

    def test_blank_supervisor_is_not_counted_here(self):
        # It belongs to the blank/MISSING_SUPERVISOR bucket, not this one.
        rows = [row(id=1, ilmiy_rahbar="", ilmiy_rahbar_daraja="PhD")]
        result = A.analyze(rows)
        self.assertEqual(result["degree_in_name"], 0)
        self.assertEqual(result["blank_count"]["ilmiy_rahbar"], 1)

    def test_degree_samples_are_capped_independently(self):
        rows = [row(id=i, ilmiy_rahbar="PhD") for i in range(30)]
        result = A.analyze(rows, sample_cap=4)
        self.assertEqual(len(result["degree_samples"]), 4)
        self.assertEqual(result["degree_in_name"], 30)

    def test_degree_sample_carries_both_values(self):
        rows = [row(id=7, ilmiy_rahbar="DSc", ilmiy_rahbar_daraja="")]
        result = A.analyze(rows)
        s = result["degree_samples"][0]
        self.assertEqual(s["id"], 7)
        self.assertEqual(s["ilmiy_rahbar"], "DSc")
        self.assertEqual(s["ilmiy_rahbar_daraja"], "")


# ── report ──────────────────────────────────────────────────────────────────
class ReportTests(unittest.TestCase):
    def _result(self):
        rows = [
            row(id=1, muassasa="", ilmiy_kengash="ToshDTU"),
            row(id=2, ilmiy_rahbar="PhD", ilmiy_rahbar_daraja=""),
            row(id=3, sana="", mavzu_raqami="В2026.2.PhD/Iqt4897"),
        ]
        return A.analyze(rows)

    def test_report_states_analysis_only_no_writes(self):
        md = A.build_report(self._result(), "host/staging")
        self.assertIn("FAQAT TAHLIL", md)
        self.assertIn("hech narsa yozilmadi", md)
        self.assertIn("TANLANMAGAN", md)

    def test_report_has_a_section_per_target_field(self):
        md = A.build_report(self._result(), "host/staging")
        for label in A.FIELD_LABELS.values():
            self.assertIn(label, md)

    def test_report_shows_sibling_sample(self):
        md = A.build_report(self._result(), "host/staging")
        self.assertIn("ToshDTU", md)
        self.assertIn("ilmiy_kengash", md)

    def test_report_has_degree_in_name_section_with_counts(self):
        md = A.build_report(self._result(), "host/staging")
        self.assertIn("ilmiy_rahbar", md)
        self.assertIn("daraja matni", md)
        self.assertIn("**1**", md)   # degree_in_name count

    def test_report_never_proposes_a_fill_rule(self):
        # The report must stay descriptive — no "should"/"recommend" language.
        md = A.build_report(self._result(), "host/staging").lower()
        for banned in ("tavsiya etamiz", "tavsiya qilinadi", "shu qoida bilan to'ldiring"):
            self.assertNotIn(banned, md)

    def test_pipe_in_value_cannot_break_the_table(self):
        cell = A._md_cell("a | b")
        self.assertNotIn(" | ", cell.replace("\\|", ""))

    def test_empty_samples_section_says_nothing_found(self):
        result = A.analyze([row(id=1)])   # nothing blank, nothing flagged
        md = A.build_report(result, "host/staging")
        self.assertIn("Bunday holat topilmadi", md)


# ── read-only safety (mirrors test_rescrape_broken_records.py) ─────────────
def _code_string_literals(source: str):
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


class ReadOnlyTests(unittest.TestCase):
    def test_module_contains_no_write_sql(self):
        import re
        verb = re.compile(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|CREATE)\b",
                          re.IGNORECASE)
        sqlish = re.compile(r"\b(FROM|INTO|SET|TABLE|VALUES)\b", re.IGNORECASE)
        with open(A.__file__, encoding="utf-8") as f:
            source = f.read()
        for literal in _code_string_literals(source):
            if verb.search(literal) and sqlish.search(literal):
                self.fail(f"write SQL found in a string literal: {literal!r}")

    def test_only_select_statements_are_executed(self):
        import re
        with open(A.__file__, encoding="utf-8") as f:
            source = f.read()
        executed = re.findall(r"cur\.execute\(\s*\n?\s*f?[\"']([^\"']+)", source)
        self.assertTrue(executed, "expected to find the SELECT statements")
        for sql in executed:
            self.assertTrue(sql.strip().upper().startswith("SELECT"), sql)

    def test_no_commit_is_ever_called(self):
        with open(A.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn(".commit(", source)

    def test_selected_columns_are_only_id_and_known_fields(self):
        # Guards against a typo silently widening the SELECT.
        expected = {"id"} | set(A.TARGET_FIELDS) | set(A.ALL_SIBLING_COLUMNS)
        self.assertEqual(set(A.SELECT_COLUMNS), expected)


if __name__ == "__main__":
    unittest.main()
