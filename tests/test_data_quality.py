"""Unit tests for the Phase 1 data-quality helpers (utils/data_quality/).

Run with:  python3 -m unittest tests.test_data_quality -v
(stdlib unittest only — no pytest dependency added.)
"""
import unittest

from utils.data_quality.names import (normalize_person_name, initials_match,
                                       is_broken_name)
from utils.data_quality.specialties import (extract_specialty_code,
                                             all_specialty_codes,
                                             split_specialty_text,
                                             validate_code_format, branch_of)
from utils.data_quality.stopwords import is_stopword, filter_keywords
from utils.data_quality.gender import detect_gender_scored
from utils.data_quality.location import (detect_region_scored,
                                          flag_location_mismatch,
                                          detect_filial_region_conflict)
from utils.data_quality.confidence import score_candidate_pair, confidence_band


class TestNormalizePersonName(unittest.TestCase):
    def test_case_insensitive(self):
        self.assertEqual(normalize_person_name("Aziz Karimov"),
                          normalize_person_name("aziz karimov"))

    def test_extra_whitespace(self):
        self.assertEqual(normalize_person_name("Aziz   Karimov"),
                          normalize_person_name("Aziz Karimov"))
        self.assertEqual(normalize_person_name("  Aziz Karimov  "),
                          normalize_person_name("Aziz Karimov"))

    def test_apostrophe_variants(self):
        # o' written with straight quote, curly quote, backtick, grave accent
        variants = ["G'ulomov", "G‘ulomov", "G`ulomov", "Gʼulomov"]
        keys = {normalize_person_name(v) for v in variants}
        self.assertEqual(len(keys), 1, f"apostrophe variants diverged: {keys}")

    def test_name_order_tolerant(self):
        self.assertEqual(normalize_person_name("Karimov Aziz Botirovich"),
                          normalize_person_name("Aziz Botirovich Karimov"))

    def test_cyrillic_yo_and_y_unified(self):
        # ё/е spelling variants of the same patronymic
        self.assertEqual(normalize_person_name("Фёдоров"),
                          normalize_person_name("Федоров"))
        # й/и spelling variants mid-word (common transliteration drift)
        self.assertEqual(normalize_person_name("Хайдаров"),
                          normalize_person_name("Хаидаров"))

    def test_initials_match(self):
        self.assertTrue(initials_match("Каримов Азиз Ботирович", "Каримов А.Б."))
        self.assertFalse(initials_match("Каримов Азиз Ботирович", "Тошев А.Б."))
        self.assertFalse(initials_match("Karimov Aziz", "Karimov A. B."))  # count mismatch


class TestBrokenNames(unittest.TestCase):
    def test_valid_name_passes(self):
        broken, reason = is_broken_name("Karimov Aziz Botirovich")
        self.assertFalse(broken)
        self.assertIsNone(reason)

    def test_empty(self):
        for bad in (None, "", "   "):
            broken, reason = is_broken_name(bad)
            self.assertTrue(broken)
            self.assertEqual(reason, "empty")

    def test_bracket_residue(self):
        broken, reason = is_broken_name("[unknown author]")
        self.assertTrue(broken)
        self.assertEqual(reason, "bracket_residue")

    def test_html_residue(self):
        broken, reason = is_broken_name("Karimov <b>Aziz</b>")
        self.assertTrue(broken)
        self.assertEqual(reason, "html_residue")
        broken, reason = is_broken_name("Karimov&nbsp;Aziz")
        self.assertTrue(broken)
        self.assertEqual(reason, "html_residue")

    def test_placeholder_fish(self):
        broken, reason = is_broken_name("ининг Ф.И.Ш")
        self.assertTrue(broken)
        self.assertEqual(reason, "placeholder")

    def test_no_letters(self):
        broken, reason = is_broken_name("12345 --- ...")
        self.assertTrue(broken)
        self.assertEqual(reason, "no_letters")

    def test_too_short(self):
        broken, reason = is_broken_name("A.")
        self.assertTrue(broken)
        self.assertEqual(reason, "too_short")


class TestSpecialtyCodes(unittest.TestCase):
    def test_validate_code_format(self):
        self.assertTrue(validate_code_format("05.01.01"))
        self.assertFalse(validate_code_format("5.1.1"))
        self.assertFalse(validate_code_format("05.01.01 - Mashinasozlik"))
        self.assertFalse(validate_code_format(None))

    def test_extract_single_code(self):
        self.assertEqual(extract_specialty_code("Mavzu matni 05.01.01"), "05.01.01")
        self.assertIsNone(extract_specialty_code("kod yoq"))

    def test_all_codes_multi(self):
        codes = all_specialty_codes("05.01.01, 05.13.01 ixtisospiklari boyicha")
        self.assertEqual(codes, ["05.01.01", "05.13.01"])

    def test_all_codes_dedupes(self):
        codes = all_specialty_codes("05.01.01 ... 05.01.01 ...")
        self.assertEqual(codes, ["05.01.01"])

    def test_split_code_trailing(self):
        topic, code = split_specialty_text(
            "Mashinasozlikda raqamli boshqaruv tizimlari - 05.01.01")
        self.assertEqual(code, "05.01.01")
        self.assertTrue(topic.lower().startswith("mashinasozlikda"))

    def test_split_code_leading(self):
        topic, code = split_specialty_text(
            "05.01.01 - Mashinasozlikda raqamli boshqaruv tizimlari")
        self.assertEqual(code, "05.01.01")
        self.assertTrue(topic.lower().startswith("mashinasozlikda"))

    def test_split_no_code(self):
        topic, code = split_specialty_text("Kod mavjud emas mavzu")
        self.assertIsNone(code)
        self.assertEqual(topic, "Kod mavjud emas mavzu")

    def test_branch_of(self):
        self.assertEqual(branch_of("05.01.01"), "05.00.00")
        self.assertIsNone(branch_of("bad-code"))


class TestStopwords(unittest.TestCase):
    def test_uzbek_stopwords(self):
        self.assertTrue(is_stopword("va"))
        self.assertTrue(is_stopword("ва"))

    def test_reported_russian_pollution_words(self):
        # The exact words the audit named as polluting clusters.
        self.assertTrue(is_stopword("ишлаб"))
        self.assertTrue(is_stopword("технологиясини"))

    def test_english_stopwords(self):
        self.assertTrue(is_stopword("the"))
        self.assertTrue(is_stopword("and"))

    def test_non_stopword_survives(self):
        self.assertFalse(is_stopword("мехатроника"))
        self.assertFalse(is_stopword("algorithm"))

    def test_filter_keywords_drops_stopwords_short_and_dupes(self):
        tokens = ["ишлаб", "технологиясини", "мехатроника", "био", "мехатроника"]
        result = filter_keywords(tokens, min_len=4)
        self.assertEqual(result, ["мехатроника"])


class TestGender(unittest.TestCase):
    def test_patronymic_female(self):
        result = detect_gender_scored("Каримова Нилуфар Азизовна")
        self.assertEqual(result["gender"], "female")
        self.assertTrue(result["is_estimated"])
        self.assertGreaterEqual(result["confidence"], 0.9)

    def test_patronymic_male(self):
        result = detect_gender_scored("Каримов Азиз Ботирович")
        self.assertEqual(result["gender"], "male")
        self.assertTrue(result["is_estimated"])
        self.assertGreaterEqual(result["confidence"], 0.9)

    def test_unknown_has_zero_confidence_and_not_estimated(self):
        result = detect_gender_scored("Xyzq Qwerty")
        self.assertEqual(result["gender"], "unknown")
        self.assertEqual(result["confidence"], 0.0)
        self.assertFalse(result["is_estimated"])

    def test_empty_name_is_unknown(self):
        result = detect_gender_scored("")
        self.assertEqual(result["gender"], "unknown")

    def test_confidence_ordering_across_tiers(self):
        patronymic = detect_gender_scored("Каримова Азизовна")
        surname_only = detect_gender_scored("Каримова")
        self.assertGreaterEqual(patronymic["confidence"], surname_only["confidence"])


class TestLocation(unittest.TestCase):
    def test_known_city_detected(self):
        region, confidence, kw = detect_region_scored("Samarqand davlat universiteti")
        self.assertEqual(region, "Samarqand")
        self.assertEqual(confidence, 1.0)

    def test_denov_regression_no_longer_silently_tashkent(self):
        # This is the exact bug from the audit: app.py's detect_uni_city_region()
        # and detect_region() both default to Toshkent when no keyword matches,
        # and neither had a "denov" keyword. Our table adds it under Surxondaryo.
        region, confidence, _ = detect_region_scored("Denov tadbirkorlik instituti")
        self.assertEqual(region, "Surxondaryo")
        self.assertGreater(confidence, 0.0)

    def test_truly_unknown_city_returns_none_not_tashkent(self):
        region, confidence, kw = detect_region_scored("Notanish shahar instituti")
        self.assertIsNone(region)
        self.assertEqual(confidence, 0.0)

    def test_flag_location_mismatch(self):
        issue = flag_location_mismatch("Urganch davlat universiteti", "Toshkent")
        self.assertIsNotNone(issue)
        self.assertEqual(issue["detected_region"], "Xorazm")

    def test_flag_location_no_mismatch_when_consistent(self):
        issue = flag_location_mismatch("Urganch davlat universiteti", "Xorazm")
        self.assertIsNone(issue)

    def test_flag_location_none_when_undetectable(self):
        issue = flag_location_mismatch("Notanish shahar instituti", "Toshkent")
        self.assertIsNone(issue)

    def test_filial_region_conflict_detected(self):
        conflict = detect_filial_region_conflict(
            "Toshkent davlat iqtisodiyot universiteti Urganch filiali")
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict["parent_region"], "Toshkent")
        self.assertEqual(conflict["branch_region"], "Xorazm")

    def test_filial_no_conflict_when_same_region(self):
        conflict = detect_filial_region_conflict(
            "Samarqand davlat universiteti Kattaqorgon filiali")
        self.assertIsNone(conflict)


class TestConfidenceScoring(unittest.TestCase):
    def test_identical_everything_scores_high(self):
        a = {"name": "Karimov Aziz Botirovich", "institution_id": "TATU",
             "specialty_code": "05.01.01", "year": 2020}
        b = {"name": "Karimov Aziz Botirovich", "institution_id": "TATU",
             "specialty_code": "05.01.01", "year": 2021}
        score = score_candidate_pair(a, b)
        self.assertEqual(confidence_band(score), "high")

    def test_different_names_and_context_scores_low(self):
        a = {"name": "Karimov Aziz Botirovich", "institution_id": "TATU",
             "specialty_code": "05.01.01", "year": 2020}
        b = {"name": "Rashidova Malika Sobirovna", "institution_id": "SamDU",
             "specialty_code": "10.02.02", "year": 1998}
        score = score_candidate_pair(a, b)
        self.assertEqual(confidence_band(score), "low")

    def test_low_confidence_never_auto_merges(self):
        """Per project constraint: low-confidence candidates must never be
        presented as a mergeable action. This module exposes no merge/apply
        function at all — scoring is purely informational."""
        import utils.data_quality.confidence as confidence_module
        for forbidden in ("merge", "apply_merge", "auto_merge", "commit_merge"):
            self.assertFalse(hasattr(confidence_module, forbidden),
                              f"confidence.py must not expose a '{forbidden}' function")

    def test_band_thresholds(self):
        self.assertEqual(confidence_band(0.95), "high")
        self.assertEqual(confidence_band(0.90), "high")
        self.assertEqual(confidence_band(0.89), "medium")
        self.assertEqual(confidence_band(0.60), "medium")
        self.assertEqual(confidence_band(0.59), "low")
        self.assertEqual(confidence_band(0.0), "low")

    def test_name_order_swap_still_scores_high_on_name_alone(self):
        a = {"name": "Karimov Aziz Botirovich"}
        b = {"name": "Aziz Botirovich Karimov"}
        score = score_candidate_pair(a, b)
        self.assertGreaterEqual(score, 0.45)  # name signal alone ~= full 0.50 weight


class TestOriginalValuePreservation(unittest.TestCase):
    """source_records (see docs/data-quality/architecture.md §2.9) is
    designed as first-seen-wins: INSERT ... ON CONFLICT DO NOTHING, never
    UPDATE. This test simulates that contract against a plain dict to pin
    the invariant independent of any live database."""

    def _insert_or_ignore(self, store, key, value):
        if key not in store:
            store[key] = value
        return store

    def test_first_value_wins_on_conflict(self):
        store = {}
        self._insert_or_ignore(store, ("dissertations", 1, "olim"), "Broken [Name]")
        self._insert_or_ignore(store, ("dissertations", 1, "olim"), "Cleaned Name")
        self.assertEqual(store[("dissertations", 1, "olim")], "Broken [Name]")

    def test_normalization_functions_do_not_mutate_input(self):
        original = "  Karimov   Aziz  "
        original_copy = str(original)
        normalize_person_name(original)
        is_broken_name(original)
        self.assertEqual(original, original_copy)


if __name__ == "__main__":
    unittest.main()
