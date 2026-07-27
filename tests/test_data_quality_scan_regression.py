"""Pinned lower-bound regression fixture for the data-quality scan on staging.

The duplicate-detection hotfix (pre-filters + block splitting) must not change a
single pre-existing candidate. This test freezes the known-good baseline of

    python3 scripts/run_data_quality_scan.py --mode report --limit 1000

taken on `olimlar_staging_db` (production backup, 29 551 dissertations). The
duplicate count was pinned from a pre-hotfix run and re-confirmed after it. If
it ever falls below that baseline, the optimisation is wrong, not the fixture.

Transliteration legitimately *raises* the duplicate count by matching
cross-script pairs, so the duplicate assertion is a floor, not an equality.
The set-level guarantee — every one of the original 27 pairs survives — lives
in tests/test_data_quality_duplicates.py.

Changing a validator legitimately moves the *issue* numbers — when that happens,
re-measure on staging and re-pin here in the same commit, so the diff shows the
before and after.

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
# Measured on olimlar_staging_db. The pre-hotfix baseline was
# issues=153 / duplicates=27 / quality=99.8 across five codes; TITLE_IS_CODE
# added 6 findings in this window (107 across the whole table) and the score
# still rounds to 99.8.
SCAN_LIMIT = 1000
EXPECTED_CHECKED = 1000
EXPECTED_ISSUES = 159
EXPECTED_BASELINE_DUPLICATES = 27
EXPECTED_BASELINE_QUALITY = 99.8
EXPECTED_BY_CODE = {
    "MISSING_ORG": 78,
    "MISSING_SUPERVISOR": 62,
    "MISSING_SPECIALTY": 8,
    "TITLE_IS_CODE": 6,
    "SHORT_TITLE": 3,
    "INCONSISTENT_APOSTROPHE": 2,
}


@unittest.skipUnless(TEST_DB, "TEST_DATABASE_URL not set (staging-only test)")
class ScanRegressionTests(unittest.TestCase):
    """The scan is read-only here; one connection is shared by the class."""

    @classmethod
    def setUpClass(cls):
        import psycopg2
        # Deliberately NOT autocommit: repository.iter_dissertations opens a
        # named (server-side) cursor, and Postgres only allows those inside a
        # transaction. Every read below ends with a rollback instead, which
        # both releases the snapshot and frees the cursor name for reuse.
        cls.conn = psycopg2.connect(TEST_DB)
        cls.result = cls._scan(cls.conn)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.conn.rollback()
        finally:
            cls.conn.close()

    @staticmethod
    def _scan(conn):
        from data_quality.config import get_config
        from data_quality.quality_service import MODE_REPORT, DataQualityService
        service = DataQualityService(conn, get_config())
        result = service.scan(mode=MODE_REPORT, limit=SCAN_LIMIT)
        conn.rollback()          # report mode writes nothing; end the read tx
        if result.status != "completed":
            raise AssertionError(f"scan failed: {result.error_message}")
        return result

    # -- the fixture ---------------------------------------------------------
    def test_totals_match_fixture(self):
        s = self.result.summary
        self.assertEqual(s.checked_records, EXPECTED_CHECKED)
        self.assertEqual(s.issue_count, EXPECTED_ISSUES)
        self.assertGreaterEqual(
            s.duplicate_count, EXPECTED_BASELINE_DUPLICATES,
            "duplicate count fell below the pre-transliteration baseline")
        # Additional candidates may lower (but cannot raise) uniqueness quality.
        self.assertLessEqual(s.quality_score, EXPECTED_BASELINE_QUALITY)

    def test_issue_breakdown_matches_fixture(self):
        self.assertEqual(dict(self.result.summary.issues_by_code), EXPECTED_BY_CODE)

    def test_issue_total_matches_fixture(self):
        self.assertEqual(self.result.summary.issue_count, EXPECTED_ISSUES)
        self.assertEqual(sum(EXPECTED_BY_CODE.values()), EXPECTED_ISSUES,
                         "fixture is internally inconsistent")

    # -- properties that must hold regardless of the fixture -----------------
    def test_scan_is_deterministic(self):
        second = self._scan(self.conn).summary
        first = self.result.summary
        self.assertEqual(second.checked_records, first.checked_records)
        self.assertEqual(second.issue_count, first.issue_count)
        self.assertEqual(second.duplicate_count, first.duplicate_count)
        self.assertEqual(second.quality_score, first.quality_score)
        self.assertEqual(dict(second.issues_by_code), dict(first.issues_by_code))

    def test_duplicate_candidates_preserve_baseline_and_are_ordered(self):
        """Every old pair remains; transliteration may add new pairs."""
        from unittest.mock import patch
        from data_quality import normalizers as normalizers
        from data_quality import repository as repo
        from data_quality.config import get_config
        from data_quality.duplicate_detector import detect_duplicates

        records = list(repo.iter_dissertations(self.conn, limit=SCAN_LIMIT))
        self.conn.rollback()
        config = get_config()
        with patch.object(
                normalizers, "to_latin",
                side_effect=lambda value: "" if value is None else str(value)):
            baseline = detect_duplicates(records, config)
        current = detect_duplicates(records, config)

        baseline_pairs = {(c.record_id_1, c.record_id_2) for c in baseline}
        current_pairs = {(c.record_id_1, c.record_id_2) for c in current}
        self.assertEqual(len(baseline_pairs), EXPECTED_BASELINE_DUPLICATES)
        self.assertTrue(
            baseline_pairs <= current_pairs,
            f"{len(baseline_pairs - current_pairs)} old candidate(s) disappeared")
        # canonical ordering: id1 < id2, and sorted by descending score
        for c in current:
            self.assertLess(c.record_id_1, c.record_id_2)
        self.assertEqual([c.score for c in current],
                         sorted([c.score for c in current], reverse=True))

    def test_report_mode_wrote_nothing(self):
        """`report` must leave the data-quality tables untouched."""
        from data_quality import repository as repo
        if not repo.dq_tables_exist(self.conn):
            self.conn.rollback()
            self.skipTest("migration 004 not applied on this database")

        def run_count():
            with self.conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM data_quality_runs")
                n = cur.fetchone()[0]
            self.conn.rollback()
            return n

        before = run_count()
        self._scan(self.conn)
        self.assertEqual(run_count(), before)


if __name__ == "__main__":
    unittest.main()
