"""Unit tests for data_quality.duplicate_detector — pure, no DB."""
import unittest

from data_quality import duplicate_detector as D
from data_quality import normalizers as N
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


class CrossScriptTests(unittest.TestCase):
    PAIRS = (
        (
            "Талабаларнинг рақамли таълимдаги фаоллиги",
            "Talabalarning raqamli ta'limdagi faolligi",
            "Жўраев Ёқуб",
            "Joʻrayev Yoqub",
        ),
        (
            "Қорақалпоғистонда маҳаллий туризмни ривожлантириш",
            "Qoraqalpogʻistonda mahalliy turizmni rivojlantirish",
            "Яҳё Юлдош",
            "Yahyo Yuldosh",
        ),
        (
            "Маҳаллий бошқарув тизимини такомиллаштириш",
            "Mahalliy boshqaruv tizimini takomillashtirish",
            "Шарипов Чори",
            "Sharipov Chori",
        ),
    )

    def _records(self, pair):
        cyr_title, lat_title, cyr_author, lat_author = pair
        return [
            rec(1, mavzu=cyr_title, olim=cyr_author, sana="10.03.2020"),
            rec(2, mavzu=lat_title, olim=lat_author, sana="10.03.2020"),
        ]

    def test_cyrillic_latin_pairs_share_title_and_author_blocks(self):
        for pair in self.PAIRS:
            with self.subTest(title=pair[0]):
                sigs = [D._Sig(r) for r in self._records(pair)]
                blocks = D._build_blocks(sigs)
                shared = {
                    key[0] for key, indexes in blocks.items()
                    if indexes == [0, 1]
                }
                self.assertIn("tt", shared)
                self.assertIn("au", shared)

    def test_cyrillic_latin_pairs_are_candidates(self):
        for pair in self.PAIRS:
            with self.subTest(title=pair[0]):
                candidates = detect_duplicates(self._records(pair))
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0].match_level, "exact")

    def test_original_records_are_not_mutated(self):
        records = self._records(self.PAIRS[0])
        before = [dict(record) for record in records]
        detect_duplicates(records)
        self.assertEqual(records, before)

    def test_old_27_candidates_are_a_subset_and_new_ones_may_be_added(self):
        # Twenty-seven pre-existing candidates are isolated by unique links.
        # The last pair has no shared Latin block before transliteration, but is
        # an exact cross-script match afterwards.
        records = []
        for i in range(27):
            link = f"https://example.test/old/{i}"
            records.extend([
                rec(2 * i + 1, mavzu=f"Eski mavzu {i} alfa", link=link),
                rec(2 * i + 2, mavzu=f"Boshqa matn {i} beta", link=link),
            ])
        records.extend([
            rec(1001, mavzu="Маҳаллий бошқарув тизими", olim="Жўраев Ёқуб",
                sana="10.03.2020"),
            rec(1002, mavzu="Mahalliy boshqaruv tizimi", olim="Joʻrayev Yoqub",
                sana="10.03.2020"),
        ])

        original = N.to_latin
        try:
            N.to_latin = lambda value: "" if value is None else str(value)
            old = detect_duplicates(records)
        finally:
            N.to_latin = original
        new = detect_duplicates(records)

        old_pairs = {(c.record_id_1, c.record_id_2) for c in old}
        new_pairs = {(c.record_id_1, c.record_id_2) for c in new}
        self.assertEqual(len(old_pairs), 27)
        self.assertTrue(old_pairs <= new_pairs)
        self.assertIn((1001, 1002), new_pairs - old_pairs)

if __name__ == "__main__":
    unittest.main()
