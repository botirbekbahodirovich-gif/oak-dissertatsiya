"""Unit tests for data_quality.validators — pure, no DB."""
import unittest
from datetime import date

from data_quality import models as M
from data_quality.validators import validate_record


def codes(record, today=None):
    return {i.issue_code for i in validate_record(record, today=today)}


class CompletenessTests(unittest.TestCase):
    def test_missing_author_and_title(self):
        c = codes({"id": 1, "olim": "", "mavzu": ""})
        self.assertIn(M.MISSING_AUTHOR, c)
        self.assertIn(M.MISSING_TITLE, c)

    def test_full_record_no_completeness_issue(self):
        c = codes({"id": 1, "olim": "Aliyev Vali",
                   "mavzu": "Sun'iy intellekt asoslari va tatbiqi",
                   "muassasa": "TATU", "ixtisoslik": "05.01.02",
                   "sana": "10.03.2020", "ilmiy_rahbar": "Karimov K.",
                   "link": "https://a.uz/1", "daraja": "PhD"})
        self.assertNotIn(M.MISSING_AUTHOR, c)
        self.assertNotIn(M.MISSING_TITLE, c)


class ValidityTests(unittest.TestCase):
    def test_invalid_date(self):
        self.assertIn(M.INVALID_DATE, codes({"id": 1, "sana": "99.99.9999"}))

    def test_future_date(self):
        c = codes({"id": 1, "sana": "01.01.2099"}, today=date(2026, 7, 25))
        self.assertIn(M.FUTURE_DATE, c)

    def test_short_title(self):
        self.assertIn(M.SHORT_TITLE, codes({"id": 1, "mavzu": "qisqa"}))

    def test_title_is_registry_code(self):
        """OAK registry codes land in the title column; they are 19 chars, so
        SHORT_TITLE cannot catch them."""
        c = codes({"id": 1, "mavzu": "В2026.2.PhD/Iqt4897"})
        self.assertIn(M.TITLE_IS_CODE, c)
        self.assertNotIn(M.SHORT_TITLE, c)

    def test_title_is_code_severity_is_critical(self):
        issues = validate_record({"id": 1, "mavzu": "В2026.2.PhD/Iqt4897"})
        found = [i for i in issues if i.issue_code == M.TITLE_IS_CODE]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, M.SEVERITY_CRITICAL)
        self.assertEqual(found[0].field_name, "mavzu")
        self.assertEqual(found[0].original_value, "В2026.2.PhD/Iqt4897")

    def test_title_is_code_variants(self):
        for title in ("В2026.2.PhD/Iqt4897",      # Cyrillic prefix
                      "B2026.2.DSc/Tex112",       # Latin prefix, DSc
                      "2026.2.PhD/Ped9",          # no prefix
                      "В2026.12.PhD/Fil7",        # two-digit middle group
                      "в2026.2.phd/iqt4897",      # lowercase
                      "  В2026.2.PhD/Iqt4897  "):  # padded
            with self.subTest(title=title):
                self.assertIn(M.TITLE_IS_CODE, codes({"id": 1, "mavzu": title}))

    def test_title_is_code_is_never_auto_fixable(self):
        """A code in the title column needs a human to supply the real title —
        the safe fixer must refuse to touch it."""
        self.assertNotIn(M.TITLE_IS_CODE, M.AUTO_FIX_ALLOWLIST)
        issues = validate_record({"id": 1, "mavzu": "В2026.2.PhD/Iqt4897"})
        found = next(i for i in issues if i.issue_code == M.TITLE_IS_CODE)
        self.assertFalse(found.is_auto_fixable)

    def test_real_titles_are_not_flagged_as_codes(self):
        for title in ("Sun'iy intellekt asoslari va uni tibbiyotda qo‘llash",
                      "Iqtisodiy tahlil metodlari va ularning modellari",
                      "2026 yilgi islohotlar tahlili",       # starts with a year
                      "PhD/DSc dasturlarini takomillashtirish",
                      "В2026 yilgi natijalar"):
            with self.subTest(title=title):
                self.assertNotIn(M.TITLE_IS_CODE, codes({"id": 1, "mavzu": title}))

    def test_html_residue(self):
        self.assertIn(M.HTML_RESIDUE,
                      codes({"id": 1, "mavzu": "<b>Katta mavzu nomi bu yerda</b>"}))

    def test_invalid_degree(self):
        self.assertIn(M.INVALID_DEGREE, codes({"id": 1, "daraja": "aspirant"}))

    def test_valid_degree_ok(self):
        self.assertNotIn(M.INVALID_DEGREE, codes({"id": 1, "daraja": "falsafa doktori"}))

    def test_placeholder_supervisor(self):
        self.assertIn(M.PLACEHOLDER_SUPERVISOR, codes(
            {"id": 1, "ilmiy_rahbar": "педагогика фанлари доктори (DSc)"}))
        self.assertIn(M.PLACEHOLDER_SUPERVISOR,
                      codes({"id": 1, "ilmiy_rahbar": "Ф.И.Ш"}))

    def test_malformed_url(self):
        self.assertIn(M.MALFORMED_URL, codes({"id": 1, "link": "not a url"}))

    def test_invalid_specialty(self):
        self.assertIn(M.INVALID_SPECIALTY_CODE, codes({"id": 1, "ixtisoslik": "5-1-2 xyz"}))


class ConsistencyTests(unittest.TestCase):
    def test_degree_inconsistency_emitted(self):
        c = codes({"id": 1, "daraja": "phd"})
        self.assertIn(M.INCONSISTENT_DEGREE, c)

    def test_no_consistency_when_already_canonical(self):
        c = codes({"id": 1, "daraja": "PhD"})
        self.assertNotIn(M.INCONSISTENT_DEGREE, c)

    def test_apostrophe_inconsistency(self):
        issues = validate_record({"id": 1, "olim": "G`ulom Aliyev"})
        appos = [i for i in issues if i.issue_code == M.INCONSISTENT_APOSTROPHE]
        self.assertTrue(appos)
        self.assertEqual(appos[0].normalized_value, "Gʻulom Aliyev")


class SeverityTests(unittest.TestCase):
    def test_missing_author_is_critical(self):
        issues = validate_record({"id": 1, "olim": ""})
        crit = [i for i in issues if i.issue_code == M.MISSING_AUTHOR]
        self.assertEqual(crit[0].severity, M.SEVERITY_CRITICAL)


if __name__ == "__main__":
    unittest.main()
