"""Pinned regression fixture for the data-quality scan on staging.

The duplicate-detection hotfix (pre-filters + block splitting) must not change a
single number a scan reports. This test freezes the known-good output of

    python3 scripts/run_data_quality_scan.py --mode report --limit 1000

taken on `olimlar_staging_db` (production backup, 29 551 dissertations) BEFORE
the hotfix. If the duplicate count moves, the optimisation is wrong, not the
fixture.

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
EXPECTED_DUPLICATES = 27
# Issue totals as captured before TITLE_IS_CODE was added. That validator is a
# new finding, not a regression, so it is accounted for separately below rather
# than folded into these numbers.
EXPECTED_ISSUES_EXCLUDING_NEW = 153
EXPECTED_BY_CODE = {
    "MISSING_ORG": 78,
    "MISSING_SUPERVISOR": 62,
    "MISSING_SPECIALTY": 8,
    "SHORT_TITLE": 3,
    "INCONSISTENT_APOSTROPHE": 2,
}
# Codes introduced after the fixture was taken. Set the expected count once it
# has been measured on staging; until then the affected assertions report the
# observed values instead of failing on a change that is intended.
NEW_CODES = {
    "TITLE_IS_CODE": None,     # ← pin the observed count here
}
# Quality score before TITLE_IS_CODE existed. Only meaningful while the new
# codes contribute nothing; re-pin together with NEW_CODES.
EXPECTED_QUALITY_EXCLUDING_NEW = 99.8


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

    # -- helpers -------------------------------------------------------------
    def _new_code_counts(self):
        by_code = dict(self.result.summary.issues_by_code)
        return {code: by_code.get(code, 0) for code in NEW_CODES}

    def _pinned_new_total(self):
        """Expected total from the new codes, or None while unpinned."""
        if any(v is None for v in NEW_CODES.values()):
            return None
        return sum(NEW_CODES.values())

    # -- the fixture ---------------------------------------------------------
    def test_duplicate_count_matches_fixture(self):
        """The load-bearing assertion: the hotfix must not move this number."""
        self.assertEqual(self.result.summary.duplicate_count, EXPECTED_DUPLICATES,
                         "duplicate count changed — the optimisation dropped or "
                         "invented candidates")

    def test_checked_records_match_fixture(self):
        self.assertEqual(self.result.summary.checked_records, EXPECTED_CHECKED)

    def test_pre_existing_issue_codes_are_unchanged(self):
        """Every code that existed when the fixture was taken keeps its count."""
        by_code = dict(self.result.summary.issues_by_code)
        for code in NEW_CODES:
            by_code.pop(code, None)
        self.assertEqual(by_code, EXPECTED_BY_CODE)

    def test_issue_total_matches_fixture(self):
        new_total = sum(self._new_code_counts().values())
        self.assertEqual(self.result.summary.issue_count - new_total,
                         EXPECTED_ISSUES_EXCLUDING_NEW)

    def test_new_codes_match_pinned_counts(self):
        observed = self._new_code_counts()
        if self._pinned_new_total() is None:
            self.skipTest(
                "NEW_CODES not pinned yet — observed on this database: "
                f"{observed}, quality={self.result.summary.quality_score}. "
                "Paste those into NEW_CODES / EXPECTED_QUALITY_EXCLUDING_NEW "
                "to freeze them.")
        self.assertEqual(observed, dict(NEW_CODES))

    def test_quality_score_matches_fixture(self):
        observed = self.result.summary.quality_score
        if sum(self._new_code_counts().values()) == 0:
            self.assertAlmostEqual(observed, EXPECTED_QUALITY_EXCLUDING_NEW, places=1)
            return
        if self._pinned_new_total() is None:
            self.skipTest(
                f"quality={observed} reflects the new codes; re-pin "
                "EXPECTED_QUALITY_EXCLUDING_NEW once NEW_CODES is set.")
        self.assertAlmostEqual(observed, EXPECTED_QUALITY_EXCLUDING_NEW, places=1)

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
