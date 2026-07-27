"""Pinned regression fixture for the data-quality scan on staging.

The duplicate-detection hotfix (pre-filters + block splitting) must not change a
single number a scan reports. This test freezes the known-good output of

    python3 scripts/run_data_quality_scan.py --mode report --limit 1000

taken on `olimlar_staging_db` (production backup, 29 551 dissertations). The
duplicate count was pinned from a pre-hotfix run and re-confirmed after it. If
it ever moves, the optimisation is wrong, not the fixture.

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
EXPECTED_DUPLICATES = 27
EXPECTED_ISSUES = 159
EXPECTED_QUALITY = 99.8
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
    def test_duplicate_count_matches_fixture(self):
        """The load-bearing assertion: the hotfix must not move this number."""
        self.assertEqual(self.result.summary.duplicate_count, EXPECTED_DUPLICATES,
                         "duplicate count changed — the optimisation dropped or "
                         "invented candidates")

    def test_checked_records_match_fixture(self):
        self.assertEqual(self.result.summary.checked_records, EXPECTED_CHECKED)

    def test_issue_breakdown_matches_fixture(self):
        self.assertEqual(dict(self.result.summary.issues_by_code), EXPECTED_BY_CODE)

    def test_issue_total_matches_fixture(self):
        self.assertEqual(self.result.summary.issue_count, EXPECTED_ISSUES)
        self.assertEqual(sum(EXPECTED_BY_CODE.values()), EXPECTED_ISSUES,
                         "fixture is internally inconsistent")

    def test_quality_score_matches_fixture(self):
        self.assertAlmostEqual(self.result.summary.quality_score,
                               EXPECTED_QUALITY, places=1)

    # -- properties that must hold regardless of the fixture -----------------
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
        self.conn.rollback()
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
