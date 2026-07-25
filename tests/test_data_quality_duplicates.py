"""Unit tests for data_quality.duplicate_detector — pure, no DB."""
import unittest

from data_quality.duplicate_detector import compare_pair, detect_duplicates


def rec(i, **kw):
    base = {"id": i, "olim": "", "mavzu": "", "muassasa": "", "ixtisoslik": "",
            "sana": "", "ilmiy_rahbar": "", "link": ""}
    base.update(kw)
    return base


BASE_TITLE = "Sun'iy intellekt asoslari va uni tibbiyotda qoʻllash"


class ExactTests(unittest.TestCase):
    def test_same_link(self):
        recs = [rec(1, mavzu=BASE_TITLE, olim="Aliyev V", link="http://x/1"),
                rec(2, mavzu="boshqa mavzu butunlay", olim="Boshqa", link="http://x/1")]
        cands = detect_duplicates(recs)
        self.assertTrue(any(c.match_level == "exact" for c in cands))

    def test_identical_title_author_date(self):
        recs = [rec(1, mavzu=BASE_TITLE, olim="Aliyev V", sana="10.03.2020"),
                rec(2, mavzu=BASE_TITLE, olim="Aliyev V", sana="10.03.2020")]
        cands = detect_duplicates(recs)
        self.assertTrue(any(c.match_level == "exact" for c in cands))


class StrongTests(unittest.TestCase):
    def test_strong_match(self):
        recs = [
            rec(1, mavzu=BASE_TITLE, olim="Aliyev Vali", muassasa="TATU",
                ixtisoslik="05.01.02", sana="10.03.2020"),
            rec(2, mavzu=BASE_TITLE + "i", olim="Aliyev Vali", muassasa="TATU",
                ixtisoslik="05.01.02", sana="12.03.2020"),
        ]
        cands = detect_duplicates(recs)
        self.assertTrue(any(c.match_level in ("strong", "exact") for c in cands))


class PossibleTests(unittest.TestCase):
    def test_possible_match(self):
        recs = [
            rec(1, mavzu="Iqtisodiy tahlil metodlari va ularning modellari",
                olim="Karimov Karim", ixtisoslik="08.00.01", ilmiy_rahbar="Rahbarov R"),
            rec(2, mavzu="Iqtisodiy tahlil metodlari va ularning tatbiqi",
                olim="Karimov Karim", ixtisoslik="08.00.01", ilmiy_rahbar="Rahbarov R"),
        ]
        cands = detect_duplicates(recs)
        self.assertTrue(cands)


class NoMatchTests(unittest.TestCase):
    def test_no_false_positive(self):
        recs = [
            rec(1, mavzu="Sun'iy intellekt asoslari", olim="Aliyev V",
                ixtisoslik="05.01.02", sana="01.01.2020"),
            rec(2, mavzu="Iqtisodiy tahlil metodlari", olim="Toshpulatov B",
                ixtisoslik="08.00.01", sana="01.01.2021"),
        ]
        self.assertEqual(detect_duplicates(recs), [])

    def test_self_excluded(self):
        r = rec(1, mavzu=BASE_TITLE, olim="Aliyev V", link="http://x/1")
        # a record compared with an identical-id copy must not yield a candidate
        self.assertEqual(detect_duplicates([r, dict(r)]), [])


class ThresholdBoundaryTests(unittest.TestCase):
    def test_high_threshold_blocks_weak(self):
        from data_quality.config import DataQualityConfig
        strict = DataQualityConfig(possible_threshold=0.99, strong_threshold=0.999)
        recs = [
            rec(1, mavzu="Iqtisodiy tahlil metodlari alfa",
                olim="Karimov K", ixtisoslik="08.00.01"),
            rec(2, mavzu="Iqtisodiy tahlil usullari beta",
                olim="Karimov K", ixtisoslik="08.00.01"),
        ]
        self.assertEqual(detect_duplicates(recs, strict), [])


class ComparePairTests(unittest.TestCase):
    def test_compare_pair_exact(self):
        a = rec(1, mavzu=BASE_TITLE, olim="Aliyev V", link="http://x/1")
        b = rec(2, mavzu="x", olim="y", link="http://x/1")
        cand = compare_pair(a, b)
        self.assertIsNotNone(cand)
        self.assertEqual(cand.match_level, "exact")


if __name__ == "__main__":
    unittest.main()
