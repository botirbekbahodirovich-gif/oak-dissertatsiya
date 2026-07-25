"""Unit tests for data_quality.scoring + import_precheck — pure, no DB."""
import unittest

from data_quality import models as M
from data_quality import scoring
from data_quality.import_precheck import summarize_precheck
from data_quality.validators import validate_record


class ScoringTests(unittest.TestCase):
    def test_perfect_when_no_issues(self):
        s = M.ScanSummary(total_records=100, checked_records=100)
        out = scoring.compute_score(s, 100)
        self.assertEqual(out["overall"], 100.0)

    def test_bounded_0_100(self):
        s = M.ScanSummary(total_records=10, checked_records=10)
        for _ in range(500):
            s.add_issue(M.Issue("dissertation", 1, "olim", M.MISSING_AUTHOR,
                                M.SEVERITY_CRITICAL, "x"))
        out = scoring.compute_score(s, 10)
        self.assertGreaterEqual(out["overall"], 0.0)
        self.assertLessEqual(out["overall"], 100.0)

    def test_deterministic(self):
        s = M.ScanSummary(total_records=100, checked_records=100, duplicate_count=5)
        s.add_issue(M.Issue("dissertation", 1, "sana", M.INVALID_DATE,
                            M.SEVERITY_WARNING, "x"))
        a = scoring.compute_score(s, 100)
        b = scoring.compute_score(s, 100)
        self.assertEqual(a, b)

    def test_critical_penalizes_more_than_info(self):
        crit = M.ScanSummary(total_records=100, checked_records=100)
        for _ in range(10):
            crit.add_issue(M.Issue("dissertation", 1, "olim", M.MISSING_AUTHOR,
                                   M.SEVERITY_CRITICAL, "x"))
        info = M.ScanSummary(total_records=100, checked_records=100)
        for _ in range(10):
            info.add_issue(M.Issue("dissertation", 1, "ixtisoslik",
                                   M.INVALID_SPECIALTY_CODE, M.SEVERITY_INFO, "x"))
        self.assertLess(scoring.compute_score(crit, 100)["overall"],
                        scoring.compute_score(info, 100)["overall"])

    def test_uniqueness_reflects_duplicates(self):
        s = M.ScanSummary(total_records=100, checked_records=100, duplicate_count=50)
        out = scoring.compute_score(s, 100)
        self.assertLess(out["dimensions"]["uniqueness"], 100.0)


class ImportPrecheckTests(unittest.TestCase):
    def test_classification(self):
        batch = [
            {"id": 1, "olim": "Aliyev V", "mavzu": "Katta mavzu nomi shu yerda",
             "muassasa": "TATU", "ixtisoslik": "05.01.02", "sana": "10.03.2020",
             "ilmiy_rahbar": "Karimov K", "link": "https://a.uz/1"},
            {"id": 2, "olim": "", "mavzu": "x", "muassasa": "", "ixtisoslik": "",
             "sana": "bad", "ilmiy_rahbar": "", "link": ""},
        ]
        s = summarize_precheck(batch)
        self.assertEqual(s["total"], 2)
        self.assertGreaterEqual(s["invalid"], 1)

    def test_intrabatch_duplicate(self):
        row = {"olim": "Aliyev V", "mavzu": "Sun'iy intellekt asoslari va tatbiqi",
               "muassasa": "TATU", "ixtisoslik": "05.01.02", "sana": "10.03.2020",
               "link": "https://a.uz/1", "ilmiy_rahbar": "K"}
        s = summarize_precheck([dict(row, id=1), dict(row, id=2)])
        self.assertGreaterEqual(s["duplicate"], 2)

    def test_never_raises_on_empty(self):
        self.assertEqual(summarize_precheck([])["total"], 0)


if __name__ == "__main__":
    unittest.main()
