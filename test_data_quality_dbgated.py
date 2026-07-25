"""DB-gated integration tests for the Data Quality tables + repository.

SKIPPED unless TEST_DATABASE_URL is set — and it must point at a STAGING /
throwaway database, never production. Applies migration 004 (idempotent),
exercises run/issue/duplicate persistence through the repository, then cleans up
the synthetic run it created (CASCADE removes its issues).

Run on staging:
    TEST_DATABASE_URL=postgresql://…/staging python3 -m unittest test_data_quality_dbgated
"""
import os
import unittest

TEST_DB = os.environ.get("TEST_DATABASE_URL")

MIGRATION = os.path.join(os.path.dirname(__file__), "migrations",
                         "004_create_data_quality_tables.sql")


@unittest.skipUnless(TEST_DB, "TEST_DATABASE_URL not set (staging-only test)")
class DbGatedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2
        cls.conn = psycopg2.connect(TEST_DB)
        cls.conn.autocommit = False
        with open(MIGRATION, encoding="utf-8") as f:
            sql = f.read()
        with cls.conn.cursor() as cur:
            cur.execute(sql)      # migration is its own BEGIN/COMMIT
        cls.conn.commit()
        cls._run_ids = []

    @classmethod
    def tearDownClass(cls):
        # Remove only the synthetic runs we created (CASCADE clears their issues).
        if getattr(cls, "_run_ids", None):
            with cls.conn.cursor() as cur:
                cur.execute("DELETE FROM data_quality_runs WHERE id = ANY(%s)",
                            (cls._run_ids,))
            cls.conn.commit()
        cls.conn.close()

    def test_migration_created_tables(self):
        from data_quality import repository as repo
        self.assertTrue(repo.dq_tables_exist(self.conn))

    def test_migration_idempotent(self):
        # Re-applying must not error.
        with open(MIGRATION, encoding="utf-8") as f:
            sql = f.read()
        with self.conn.cursor() as cur:
            cur.execute(sql)
        self.conn.commit()

    def test_run_issue_roundtrip(self):
        from data_quality import models as M
        from data_quality import repository as repo

        run_id = repo.create_run(self.conn, "test", total_records=3,
                                 metadata={"mode": "test"})
        self.__class__._run_ids.append(run_id)
        issues = [
            M.Issue("dissertation", 101, "olim", M.MISSING_AUTHOR,
                    M.SEVERITY_CRITICAL, "empty"),
            M.Issue("dissertation", 102, "daraja", M.INCONSISTENT_DEGREE,
                    M.SEVERITY_INFO, "phd->PhD", original_value="phd",
                    normalized_value="PhD"),
        ]
        n = repo.insert_issues(self.conn, run_id, issues)
        repo.complete_run(self.conn, run_id, status="completed",
                          checked_records=3, issue_count=n, duplicate_count=0)
        self.conn.commit()

        self.assertEqual(n, 2)
        breakdown = repo.issue_breakdown(self.conn, run_id)
        codes = {b["issue_code"] for b in breakdown}
        self.assertIn(M.MISSING_AUTHOR, codes)
        listed = repo.list_issues(self.conn, {"run_id": run_id}, 50, 0)
        self.assertEqual(len(listed), 2)

    def test_duplicate_upsert_idempotent(self):
        from data_quality import repository as repo
        from data_quality.models import DuplicateCandidate

        cand = DuplicateCandidate(900001, 900002, 0.95, "strong", ["test"])
        repo.insert_duplicates(self.conn, [cand])
        repo.insert_duplicates(self.conn, [cand])  # ON CONFLICT — no error, no dup row
        self.conn.commit()
        rows = repo.list_duplicates(self.conn, "pending", 100, 0)
        pairs = [(r["record_id_1"], r["record_id_2"]) for r in rows]
        self.assertEqual(pairs.count((900001, 900002)), 1)
        # cleanup
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM duplicate_candidates WHERE record_id_1 = 900001")
        self.conn.commit()


if __name__ == "__main__":
    unittest.main()
