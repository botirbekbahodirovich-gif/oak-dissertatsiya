"""Pinned regression fixture for the data-quality scan on staging.

The duplicate-detection hotfix (pre-filters + block splitting) must not change a
single number a scan reports. This test freezes the known-good output of

    python3 scripts/run_data_quality_scan.py --mode report --limit 1000

taken on `olimlar_staging_db` (production backup, 29 551 dissertations) BEFORE
the hotfix. If any count moves — especially if `duplicates` drops — the
optimisation is wrong, not the fixture.

SKIPPED unless TEST_DATABASE_URL is set, and it must point at staging or a
throwaway copy, never production. The scan runs in `report` mode, which writes
nothing.

Run on staging:
    TEST_DATABASE_URL=postgresql://…/olimlar_staging_db \
        python3 -m unittest tests.test_data_quality_scan_regression -v
"""
from __future__ import annotations

import os
import unittest

TEST_DB = os.environ.get("TEST_DATABASE_URL")

# ── the frozen fixture ──────────────────────────────────────────────────────
SCAN_LIMIT = 1000
EXPECTED_CHECKED = 1000
EXPECTED_ISSUES = 153
EXPECTED_DUPLICATES = 27
EXPECTED_QUALITY = 99.8
EXPECTED_BY_CODE = {
    "MISSING_ORG": 78,
    "MISSING_SUPERVISOR": 62,
    "MISSING_SPECIALTY": 8,
    "SHORT_TITLE": 3,
    "INCONSISTENT_APOSTROPHE": 2,
}


@unittest.skipUnless(TEST_DB, "TEST_DATABASE_URL not set (staging-only test)")
class ScanRegressionTests(unittest.TestCase):
    """The scan is read-only here; each test opens its own connection."""

    @classmethod
    def setUpClass(cls):
        import psycopg2
        cls.conn = psycopg2.connect(TEST_DB)
        cls.conn.autocommit = True     # nothing is written, but never hold a tx
        cls.result = cls._scan(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    @staticmethod
    def _scan(conn):
        from data_quality.config import get_config
        from data_quality.quality_service import MODE_REPORT, DataQualityService
        service = DataQualityService(conn, get_config())
        result = service.scan(mode=MODE_REPORT, limit=SCAN_LIMIT)
        if result.status != "completed":
            raise AssertionError(f"scan failed: {result.error_message}")
        return result

    def test_totals_match_fixture(self):
        s = self.result.summary
        self.assertEqual(s.checked_records, EXPECTED_CHECKED)
        self.assertEqual(s.issue_count, EXPECTED_ISSUES)
        self.assertEqual(s.duplicate_count, EXPECTED_DUPLICATES,
                         "duplicate count changed — the optimisation dropped or "
                         "invented candidates")
        self.assertAlmostEqual(s.quality_score, EXPECTED_QUALITY, places=1)

    def test_issue_breakdown_matches_fixture(self):
        by_code = dict(self.result.summary.issues_by_code)
        self.assertEqual(by_code, EXPECTED_BY_CODE)

    def test_scan_is_deterministic(self):
        second = self._scan(self.conn).summary
        first = self.result.summary
        self.assertEqual(second.checked_records, first.checked_records)
        self.assertEqual(second.issue_count, first.issue_count)
        self.assertEqual(second.duplicate_count, first.duplicate_count)
        self.assertEqual(second.quality_score, first.quality_score)
        self.assertEqual(dict(second.issues_by_code), dict(first.issues_by_code))

    def test_duplicate_candidates_are_stable_and_ordered(self):
        """Same pairs, same order, on a repeated detection pass."""
        from data_quality import repository as repo
        from data_quality.config import get_config
        from data_quality.duplicate_detector import detect_duplicates

        records = list(repo.iter_dissertations(self.conn, limit=SCAN_LIMIT))
        config = get_config()
        first = detect_duplicates(records, config)
        second = detect_duplicates(records, config)

        shape = [(c.record_id_1, c.record_id_2, c.score, c.match_level)
                 for c in first]
        self.assertEqual(
            shape,
            [(c.record_id_1, c.record_id_2, c.score, c.match_level) for c in second])
        self.assertEqual(len(first), EXPECTED_DUPLICATES)
        # canonical ordering: id1 < id2, and sorted by descending score
        for c in first:
            self.assertLess(c.record_id_1, c.record_id_2)
        self.assertEqual([c.score for c in first],
                         sorted([c.score for c in first], reverse=True))

    def test_report_mode_wrote_nothing(self):
        """`report` must leave the data-quality tables untouched."""
        from data_quality import repository as repo
        if not repo.dq_tables_exist(self.conn):
            self.skipTest("migration 004 not applied on this database")
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM data_quality_runs")
            runs_before = cur.fetchone()[0]
        self._scan(self.conn)
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM data_quality_runs")
            self.assertEqual(cur.fetchone()[0], runs_before)


if __name__ == "__main__":
    unittest.main()
