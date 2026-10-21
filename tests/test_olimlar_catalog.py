import re
import unittest
from pathlib import Path

from blueprints.olimlar_catalog import (
    _apply_filters,
    _compute_facets,
    _group_items,
    _sort_items,
)
from utils.olimlar_catalog_quality import (
    canonical_specialty,
    normalize_region,
    scholar_name_issue,
)


ROOT = Path(__file__).resolve().parents[1]


def scholar(name, students, specialties=(), institution="TDPU", region="Toshkent"):
    options = [{"code": code, "name": f"{code} nomi"} for code in specialties]
    return {
        "name": name,
        "display": name,
        "degree": "DSc",
        "photo_url": "",
        "total_students": students,
        "phd_students": int(students > 0),
        "dsc_students": int(students > 10),
        "first_year": "2018",
        "last_year": "2025",
        "active_years": 3,
        "institutions": [institution],
        "regions": [region],
        "specialties": list(specialties),
        "specialty_names": [item["name"] for item in options],
        "specialty_options": options,
        "yearly": {},
        "opponent_count": 0,
        "next_gen_advisors": 0,
        "publications_count": 0,
        "is_claimed": False,
        "has_orcid": False,
        "has_google_scholar": False,
        "google_scholar_url": "",
    }


class CatalogueBackendTests(unittest.TestCase):
    def test_specialty_facet_count_matches_filter_result(self):
        rows = [
            scholar("Ali Valiyev", 8, ("13.00.02",)),
            scholar("Botir Karimov", 4, ("13.00.02", "05.01.01")),
            scholar("Dilshod Salimov", 2, ("05.01.01",)),
        ]
        facet = next(
            item for item in _compute_facets(rows)["specialties"]
            if item["code"] == "13.00.02"
        )
        filtered = _apply_filters(rows, {"ixtisoslik": "13.00.02"})
        self.assertEqual(facet["count"], len(filtered))

    def test_invalid_bracket_name_is_quarantined(self):
        self.assertEqual(scholar_name_issue("["), "empty_or_punctuation_only")

    def test_students_sort_descending_and_name_tie_break(self):
        rows = [
            scholar("Zafar Olimov", 9),
            scholar("Botir Aliyev", 12),
            scholar("Anvar Aliyev", 9),
        ]
        result = _sort_items(rows, "students")
        self.assertEqual(
            [(item["total_students"], item["display"]) for item in result],
            [(12, "Botir Aliyev"), (9, "Anvar Aliyev"), (9, "Zafar Olimov")],
        )

    def test_name_sort_is_stable(self):
        rows = [scholar("Zafar Olimov", 9), scholar("Anvar Aliyev", 4)]
        self.assertEqual(
            [item["display"] for item in _sort_items(rows, "name")],
            ["Anvar Aliyev", "Zafar Olimov"],
        )

    def test_student_ranges_are_correct(self):
        values = [55, 30, 15, 8, 3, 0]
        rows = [scholar(f"Olim {index}", value) for index, value in enumerate(values)]
        groups = _group_items(rows, "students", set())
        self.assertEqual(
            [group["key"] for group in groups],
            ["51-plus", "21-50", "11-20", "6-10", "1-5", "zero"],
        )
        self.assertTrue(all(group["scholar_count"] == 1 for group in groups))

    def test_region_aliases_are_normalized(self):
        self.assertEqual(normalize_region("Fargona"), "Farg'ona")
        self.assertEqual(normalize_region("Qoraqalpogiston"), "Qoraqalpog'iston")

    def test_canonical_specialty_replaces_bad_source_name(self):
        code, name, reasons = canonical_specialty(
            "13.00.02",
            ". Dissertatsiya mavzusidan noto'g'ri parcha",
            {"13.00.02": "Ta'lim va tarbiya nazariyasi va metodikasi"},
        )
        self.assertEqual(code, "13.00.02")
        self.assertEqual(name, "Ta'lim va tarbiya nazariyasi va metodikasi")
        self.assertIn("specialty_name_classifier_mismatch", reasons)


class CatalogueFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "templates/olimlar_catalog.html").read_text(encoding="utf-8")
        cls.script = (ROOT / "static/js/olimlar_catalog.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "static/css/olimlar_catalog.css").read_text(encoding="utf-8")

    def test_catalog_does_not_add_second_main(self):
        self.assertNotIn("<main", self.template)

    def test_zero_results_hide_ranking(self):
        self.assertIn("renderRanking(data.total?data.top:[])", self.script)
        self.assertIn("els.ranking.hidden=true", self.script)

    def test_orcid_filter_is_hidden(self):
        self.assertNotIn('id="oc-orcid"', self.template)

    def test_search_requests_are_abortable_and_sequence_guarded(self):
        self.assertIn("new AbortController()", self.script)
        self.assertIn("activeController.abort()", self.script)
        self.assertIn("sequence!==requestSequence", self.script)

    def test_grouped_hard_refresh_state_is_supported(self):
        self.assertIn("p.get('view')==='grouped'?'grouped':'list'", self.script)
        self.assertIn("view not in ('list', 'grouped')", (
            ROOT / "blueprints/olimlar_catalog.py"
        ).read_text(encoding="utf-8"))

    def test_desktop_card_height_is_compact(self):
        match = re.search(r"\.oc-card\{[^}]*height:(\d+)px", self.css)
        self.assertIsNotNone(match)
        self.assertLessEqual(int(match.group(1)), 190)

    def test_view_toggle_has_pressed_state(self):
        self.assertGreaterEqual(self.template.count("aria-pressed="), 2)

    def test_filtered_facets_refresh_visible_counts(self):
        self.assertIn("updateFacets(data.facets)", self.script)
        self.assertIn('data-facet="specialty"', self.template)
        self.assertIn('data-facet="region"', self.template)

    def test_search_does_not_block_tab(self):
        self.assertNotRegex(
            self.script,
            r"oc-q[^;]{0,200}keydown[^;]{0,200}preventDefault",
        )


if __name__ == "__main__":
    unittest.main()
