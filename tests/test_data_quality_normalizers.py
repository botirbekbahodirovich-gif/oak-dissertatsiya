"""Unit tests for data_quality.normalizers — pure, no DB.

Run:  python3 -m unittest test_data_quality_normalizers
"""
import unittest

from data_quality import normalizers as N


class WhitespaceTests(unittest.TestCase):
    def test_collapse_and_strip(self):
        self.assertEqual(N.normalize_whitespace("  a\t b\n c "), "a b c")

    def test_nbsp(self):
        self.assertEqual(N.normalize_whitespace("a b"), "a b")

    def test_idempotent(self):
        once = N.normalize_whitespace("  x   y ")
        self.assertEqual(once, N.normalize_whitespace(once))

    def test_empty(self):
        self.assertEqual(N.normalize_whitespace(""), "")
        self.assertEqual(N.normalize_whitespace(None), "")


class ApostropheTests(unittest.TestCase):
    def test_uzbek_tutuq(self):
        self.assertEqual(N.normalize_apostrophes("o'zbek g`alaba"), "oʻzbek gʻalaba")

    def test_curly_after_O(self):
        self.assertEqual(N.normalize_apostrophes("O’rin"), "Oʻrin")

    def test_generic_quote_becomes_ascii(self):
        # An apostrophe not after o/g collapses to a straight quote.
        self.assertEqual(N.normalize_apostrophes("D’Artagnan"), "D'Artagnan")

    def test_idempotent(self):
        once = N.normalize_apostrophes("o'zbekiston o‘quv")
        self.assertEqual(once, N.normalize_apostrophes(once))


class UnicodeTests(unittest.TestCase):
    def test_nfc(self):
        decomposed = "é"          # e + combining acute
        self.assertEqual(N.normalize_unicode(decomposed), "é")

    def test_cyrillic_preserved(self):
        self.assertEqual(N.normalize_unicode("Ўзбекистон"), "Ўзбекистон")


class TransliterationTests(unittest.TestCase):
    def test_uzbek_specific_letters_and_digraphs(self):
        self.assertEqual(
            N.to_latin("Ўзбекистон ғалабаси: Қўчқор Ҳамроев, Шаҳноза"),
            "Oʻzbekiston gʻalabasi: Qoʻchqor Hamroyev, Shahnoza",
        )

    def test_ambiguous_letters_use_uzbek_forms(self):
        self.assertEqual(
            N.to_latin("Янги юридик ёндашув ва жаҳон"),
            "Yangi yuridik yondashuv va jahon",
        )
        self.assertEqual(N.to_latin("Ер ва диета"), "Yer va diyeta")

    def test_hard_and_soft_signs(self):
        self.assertEqual(N.to_latin("таълим объекти"), "taʼlim obʼyekti")
        self.assertEqual(N.to_latin("альбом"), "albom")

    def test_latin_is_unchanged_and_idempotent(self):
        latin = "Oʻzbekiston: sunʼiy intellekt va JavaScript"
        self.assertEqual(N.to_latin(latin), latin)
        once = N.to_latin("Қорақалпоғистон")
        self.assertEqual(N.to_latin(once), once)

    def test_empty_and_non_string_values(self):
        self.assertEqual(N.to_latin(""), "")
        self.assertEqual(N.to_latin(None), "")
        self.assertEqual(N.to_latin(123), "123")


class DegreeTests(unittest.TestCase):
    def test_phd_variants(self):
        for v in ("PhD", "phd", "Ph.D", "ph.d.", "falsafa doktori (PhD)",
                  "Фалсафа доктори"):
            self.assertEqual(N.normalize_degree(v), "PhD", v)

    def test_dsc_variants(self):
        for v in ("DSc", "dsc", "D.Sc", "fan doktori", "fanlari doktori",
                  "Фан доктори"):
            self.assertEqual(N.normalize_degree(v), "DSc", v)

    def test_unknown_returned_cleaned(self):
        self.assertEqual(N.normalize_degree("aspirant"), "aspirant")

    def test_dsc_not_shadowed_by_phd(self):
        self.assertEqual(N.normalize_degree("фан доктори"), "DSc")


class SpecialtyTests(unittest.TestCase):
    def test_format(self):
        self.assertEqual(N.normalize_specialty_code("01 . 01-01"), "01.01.01")

    def test_already_clean_idempotent(self):
        self.assertEqual(N.normalize_specialty_code("05.01.02"), "05.01.02")

    def test_non_code_untouched(self):
        self.assertEqual(N.normalize_specialty_code("matematika"), "matematika")


class DateTests(unittest.TestCase):
    def test_dotted(self):
        self.assertEqual(N.normalize_date("15.03.2020"), "2020-03-15")

    def test_iso_passthrough(self):
        self.assertEqual(N.normalize_date("2020-03-15"), "2020-03-15")

    def test_invalid_month(self):
        self.assertIsNone(N.normalize_date("2020-13-01"))

    def test_impossible_day(self):
        self.assertIsNone(N.normalize_date("31.02.2020"))

    def test_garbage(self):
        self.assertIsNone(N.normalize_date("himoya boʻlmagan"))


class UrlTests(unittest.TestCase):
    def test_adds_scheme(self):
        self.assertEqual(N.normalize_url("example.com/x"), "https://example.com/x")

    def test_keeps_scheme(self):
        self.assertEqual(N.normalize_url("http://a.uz"), "http://a.uz")

    def test_non_url_untouched(self):
        self.assertEqual(N.normalize_url("not a url"), "not a url")


class HtmlControlTests(unittest.TestCase):
    def test_strip_html(self):
        self.assertNotIn("<", N.strip_html("<b>Ilmiy</b> ish"))
        self.assertIn("Ilmiy", N.strip_html("<b>Ilmiy</b> ish"))

    def test_strip_control(self):
        self.assertEqual(N.strip_control_chars("a\x00b\x07c"), "abc")


class ScriptSafetyTests(unittest.TestCase):
    def test_russian_and_uzbek_titles_survive(self):
        for txt in ("Информационные технологии в образовании",
                    "Sun'iy intellekt asoslari",
                    "Ахборот-коммуникация технологиялари"):
            out = N.normalize_title(txt)
            # content preserved (only apostrophe/space normalization allowed)
            self.assertTrue(len(out) >= len(txt) - 2)

    def test_acronyms_preserved(self):
        self.assertIn("TATU", N.normalize_organization_name("TATU  huzuridagi"))


if __name__ == "__main__":
    unittest.main()
