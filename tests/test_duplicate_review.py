"""Unit tests for data_quality.duplicate_review — pure, no DB, no Flask."""
import unittest

from data_quality import duplicate_review as R


def rec(**kw):
    base = {"mavzu": "", "olim": "", "ilmiy_rahbar": "", "muassasa": "",
            "ixtisoslik": "", "sana": "", "link": ""}
    base.update(kw)
    return base


def status_of(fields, name):
    return next(f["status"] for f in fields if f["field"] == name)


class CompareFieldTests(unittest.TestCase):
    def test_identical_values_are_same(self):
        a = rec(mavzu="Sun'iy intellekt asoslari", olim="Aliyev Vali")
        fields = R.compare_fields(a, dict(a))
        self.assertEqual(status_of(fields, "mavzu"), R.SAME)
        self.assertEqual(status_of(fields, "olim"), R.SAME)

    def test_real_difference_is_flagged(self):
        fields = R.compare_fields(rec(mavzu="Sun'iy intellekt asoslari"),
                                  rec(mavzu="Iqtisodiy tahlil metodlari"))
        self.assertEqual(status_of(fields, "mavzu"), R.DIFFERS)

    def test_cosmetic_difference_is_minor_not_differs(self):
        """An apostrophe or spacing variant must not read as a real conflict —
        475 of the candidates are 'same title, near author'."""
        fields = R.compare_fields(rec(olim="G`ulom  Aliyev"),
                                  rec(olim="Gʻulom Aliyev"))
        self.assertEqual(status_of(fields, "olim"), R.MINOR)

    def test_date_format_difference_is_minor(self):
        fields = R.compare_fields(rec(sana="10.03.2020"), rec(sana="2020-03-10"))
        self.assertEqual(status_of(fields, "sana"), R.MINOR)

    def test_specialty_order_is_minor(self):
        fields = R.compare_fields(rec(ixtisoslik="05.01.02; 05.01.04"),
                                  rec(ixtisoslik="05.01.04, 05.01.02"))
        self.assertEqual(status_of(fields, "ixtisoslik"), R.MINOR)

    def test_specialty_real_difference_still_differs(self):
        fields = R.compare_fields(rec(ixtisoslik="05.01.02"),
                                  rec(ixtisoslik="08.00.01"))
        self.assertEqual(status_of(fields, "ixtisoslik"), R.DIFFERS)

    def test_one_sided_when_only_one_record_has_a_value(self):
        fields = R.compare_fields(rec(muassasa="TATU"), rec(muassasa=""))
        self.assertEqual(status_of(fields, "muassasa"), R.ONE_SIDED)

    def test_both_empty(self):
        fields = R.compare_fields(rec(), rec())
        self.assertEqual(status_of(fields, "ilmiy_rahbar"), R.BOTH_EMPTY)

    def test_none_is_treated_as_empty(self):
        fields = R.compare_fields({"mavzu": None}, {"mavzu": None})
        self.assertEqual(status_of(fields, "mavzu"), R.BOTH_EMPTY)

    def test_every_compare_field_is_present_once(self):
        fields = R.compare_fields(rec(), rec())
        names = [f["field"] for f in fields]
        self.assertEqual(names, [f for f, _ in R.COMPARE_FIELDS])

    def test_raw_values_are_preserved_for_display(self):
        """The reviewer must see the original text, not the normalized form."""
        fields = R.compare_fields(rec(olim="G`ulom Aliyev"), rec(olim="Gʻulom Aliyev"))
        olim = next(f for f in fields if f["field"] == "olim")
        self.assertEqual(olim["left"], "G`ulom Aliyev")
        self.assertEqual(olim["right"], "Gʻulom Aliyev")


class DifferenceSummaryTests(unittest.TestCase):
    def test_counts_add_up(self):
        fields = R.compare_fields(
            rec(mavzu="A mavzu nomi", olim="Aliyev Vali", muassasa="TATU"),
            rec(mavzu="B mavzu nomi", olim="Aliyev Vali"))
        counts = R.difference_summary(fields)
        self.assertEqual(sum(counts.values()), len(R.COMPARE_FIELDS))
        self.assertEqual(counts[R.DIFFERS], 1)
        self.assertEqual(counts[R.SAME], 1)
        self.assertEqual(counts[R.ONE_SIDED], 1)


class BucketTests(unittest.TestCase):
    def test_exact_and_strong_are_always_high_yield(self):
        self.assertEqual(R.bucket_of("exact", 0.1), R.BUCKET_HIGH)
        self.assertEqual(R.bucket_of("strong", 0.0), R.BUCKET_HIGH)

    def test_possible_splits_on_score(self):
        self.assertEqual(R.bucket_of("possible", 1.0), R.BUCKET_HIGH)
        self.assertEqual(R.bucket_of("possible", R.HIGH_YIELD_MIN_SCORE), R.BUCKET_HIGH)
        self.assertEqual(R.bucket_of("possible", 0.86), R.BUCKET_NOISY)
        self.assertEqual(R.bucket_of("possible", 0.72), R.BUCKET_NOISY)

    def test_unparseable_score_is_noisy_not_a_crash(self):
        self.assertEqual(R.bucket_of("possible", None), R.BUCKET_NOISY)
        self.assertEqual(R.bucket_of("possible", "x"), R.BUCKET_NOISY)


class OrderTests(unittest.TestCase):
    def test_review_order_is_exact_then_strong_then_possible(self):
        self.assertEqual(R.LEVEL_ORDER, ("exact", "strong", "possible"))
        self.assertLess(R.LEVEL_RANK["exact"], R.LEVEL_RANK["strong"])
        self.assertLess(R.LEVEL_RANK["strong"], R.LEVEL_RANK["possible"])

    def test_merge_is_not_a_reviewable_status(self):
        """This screen marks status only; merging records is a later job."""
        self.assertNotIn("merged", R.REVIEWABLE_STATUSES)


class ReasonTests(unittest.TestCase):
    def test_known_reasons_are_translated(self):
        out = R.humanize_reasons(["same author", "same source link"])
        self.assertEqual(out, ["bir xil muallif", "bir xil manba havolasi"])

    def test_title_similarity_keeps_its_number(self):
        self.assertEqual(R.humanize_reasons(["title similarity 0.94"]),
                         ["mavzu o‘xshashligi 0.94"])

    def test_unknown_reason_passes_through(self):
        """A new detector reason must never silently vanish from the UI."""
        self.assertEqual(R.humanize_reasons(["brand new reason"]),
                         ["brand new reason"])

    def test_json_string_is_parsed(self):
        self.assertEqual(R.humanize_reasons('["same author"]'), ["bir xil muallif"])

    def test_empty_and_garbage(self):
        self.assertEqual(R.humanize_reasons(None), [])
        self.assertEqual(R.humanize_reasons([]), [])
        self.assertEqual(R.humanize_reasons("not json"), ["not json"])


class DecorateTests(unittest.TestCase):
    def _candidate(self, **kw):
        base = {
            "id": 5, "record_id_1": 11, "record_id_2": 22, "score": 0.94,
            "match_level": "strong", "status": "pending",
            "reasons": ["same author", "title similarity 0.94"],
            "r1_mavzu": "Sun'iy intellekt asoslari", "r2_mavzu": "Sun'iy intellekt asoslari",
            "r1_olim": "Aliyev Vali", "r2_olim": "Aliev Vali",
            "r1_ilmiy_rahbar": "", "r2_ilmiy_rahbar": "",
            "r1_muassasa": "TATU", "r2_muassasa": "",
            "r1_ixtisoslik": "05.01.02", "r2_ixtisoslik": "05.01.02",
            "r1_sana": "10.03.2020", "r2_sana": "10.03.2020",
            "r1_link": "", "r2_link": "",
        }
        base.update(kw)
        return base

    def test_decorate_adds_everything_the_template_needs(self):
        d = R.decorate(self._candidate())
        for key in ("fields", "diff_counts", "reasons_text", "bucket",
                    "level_label", "status_label"):
            self.assertIn(key, d)
        self.assertEqual(d["bucket"], R.BUCKET_HIGH)
        self.assertEqual(d["level_label"], "Kuchli")
        self.assertEqual(d["status_label"], "Ko‘rilmagan")

    def test_decorate_keeps_the_original_columns(self):
        d = R.decorate(self._candidate())
        self.assertEqual(d["id"], 5)
        self.assertEqual(d["record_id_1"], 11)
        self.assertEqual(d["score"], 0.94)

    def test_decorate_maps_r1_r2_prefixes_to_the_two_sides(self):
        d = R.decorate(self._candidate())
        muassasa = next(f for f in d["fields"] if f["field"] == "muassasa")
        self.assertEqual(muassasa["left"], "TATU")
        self.assertEqual(muassasa["right"], "")
        self.assertEqual(muassasa["status"], R.ONE_SIDED)

    def test_near_author_reads_as_a_real_difference(self):
        """Aliyev/Aliev is the 475-candidate case — a spelling difference that
        is NOT merely cosmetic, so the reviewer must see it flagged."""
        d = R.decorate(self._candidate())
        olim = next(f for f in d["fields"] if f["field"] == "olim")
        self.assertEqual(olim["status"], R.DIFFERS)

    def test_missing_joined_record_does_not_crash(self):
        """A LEFT JOIN can yield NULLs if a record was deleted underneath."""
        candidate = self._candidate()
        for key in list(candidate):
            if key.startswith("r2_"):
                candidate[key] = None
        d = R.decorate(candidate)
        self.assertEqual(len(d["fields"]), len(R.COMPARE_FIELDS))


class ProgressTests(unittest.TestCase):
    ROWS = [
        {"match_level": "exact", "status": "confirmed", "count": 100},
        {"match_level": "exact", "status": "pending", "count": 8},
        {"match_level": "strong", "status": "pending", "count": 181},
        {"match_level": "possible", "status": "rejected", "count": 50},
        {"match_level": "possible", "status": "pending", "count": 1000},
    ]

    def test_totals(self):
        p = R.progress_view(self.ROWS)
        self.assertEqual(p["total"], 1339)
        self.assertEqual(p["reviewed"], 150)
        self.assertEqual(p["pending"], 1189)
        self.assertEqual(p["confirmed"], 100)
        self.assertEqual(p["rejected"], 50)

    def test_levels_are_in_review_order(self):
        p = R.progress_view(self.ROWS)
        self.assertEqual([l["level"] for l in p["levels"]],
                         ["exact", "strong", "possible"])

    def test_per_level_percent(self):
        p = R.progress_view(self.ROWS)
        exact = p["levels"][0]
        self.assertEqual(exact["total"], 108)
        self.assertEqual(exact["reviewed"], 100)
        self.assertAlmostEqual(exact["percent"], 92.6, places=1)

    def test_empty_input_does_not_divide_by_zero(self):
        p = R.progress_view([])
        self.assertEqual(p["total"], 0)
        self.assertEqual(p["percent"], 0.0)
        self.assertEqual(p["levels"], [])

    def test_unknown_level_is_ignored_in_the_breakdown_but_not_fatal(self):
        p = R.progress_view([{"match_level": "weird", "status": "pending", "count": 3}])
        self.assertEqual(p["levels"], [])
        self.assertEqual(p["total"], 0)


class FilterSqlTests(unittest.TestCase):
    """repository._duplicate_filter_sql is pure — no DB needed to test it."""

    def setUp(self):
        from data_quality import repository as repo
        self.build = repo._duplicate_filter_sql

    def test_no_filters_means_no_where_clause(self):
        self.assertEqual(self.build({}), ("", []))
        self.assertEqual(self.build(None), ("", []))

    def test_values_are_parameterized_never_interpolated(self):
        where, params = self.build({"status": "pending", "match_level": "exact"})
        self.assertNotIn("pending", where)
        self.assertNotIn("exact", where)
        self.assertEqual(where.count("%s"), 2)
        self.assertEqual(params, ["pending", "exact"])

    def test_all_means_no_constraint(self):
        self.assertEqual(self.build({"status": "all", "match_level": "all"}),
                         ("", []))

    def test_bucket_uses_the_shared_threshold(self):
        from data_quality.config import HIGH_YIELD_MIN_SCORE
        for bucket in ("high", "noisy"):
            with self.subTest(bucket=bucket):
                _where, params = self.build({"bucket": bucket})
                self.assertEqual(params, [HIGH_YIELD_MIN_SCORE])

    def test_high_bucket_keeps_exact_and_strong_regardless_of_score(self):
        where, _ = self.build({"bucket": "high"})
        self.assertIn("match_level IN ('exact', 'strong')", where)

    def test_noisy_bucket_is_only_low_scoring_possible(self):
        where, _ = self.build({"bucket": "noisy"})
        self.assertIn("match_level = 'possible'", where)
        self.assertIn("score <", where)

    def test_invalid_values_raise_rather_than_silently_widening(self):
        for bad in ({"status": "'; DROP TABLE x --"},
                    {"match_level": "anything"},
                    {"bucket": "everything"}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.build(bad)


if __name__ == "__main__":
    unittest.main()
