"""DB-gated integration tests for scripts/merge_duplicates.py.

SKIPPED unless TEST_DATABASE_URL is set — and it must point at a STAGING /
throwaway database, NEVER production. Applies migrations 004 and 006
(idempotent), exercises the real transactional merge against synthetic
`dissertations` rows created and torn down by each test, and never touches any
real (imported) record — every id used here is created by the test itself.

This is the part tests/test_merge_review.py cannot cover: real foreign-key
constraints, real transactions, real rollback. The pure decision logic (who
wins, what gets copied) is covered there without a database; this file only
re-proves those decisions hold once real SQL and real constraints are involved,
plus everything that is inherently transactional: FK repointing under a unique
constraint, rollback leaving zero trace, and refusing to re-merge.

Run on staging:
    TEST_DATABASE_URL=postgresql://…/staging \
        python3 -m unittest tests.test_merge_duplicates_dbgated -v
"""
from __future__ import annotations

import os
import sys
import unittest

TEST_DB = os.environ.get("TEST_DATABASE_URL")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
MIGRATION_004 = os.path.join(_REPO_ROOT, "migrations",
                             "004_create_data_quality_tables.sql")
MIGRATION_006 = os.path.join(_REPO_ROOT, "migrations",
                             "006_dissertation_merge_support.sql")


@unittest.skipUnless(TEST_DB, "TEST_DATABASE_URL not set (staging-only test)")
class MergeDbGatedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2
        cls.conn = psycopg2.connect(TEST_DB)
        cls.conn.autocommit = False
        with cls.conn.cursor() as cur:
            for path in (MIGRATION_004, MIGRATION_006):
                with open(path, encoding="utf-8") as f:
                    cur.execute(f.read())
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    # -- per-test synthetic fixtures ------------------------------------------
    def setUp(self):
        from data_quality import repository as repo
        self.repo = repo
        self._diss_ids: list = []
        self._candidate_ids: list = []
        self._run_ids: list = []
        self._bookmark_user_ids: list = []
        self._institution_ids: list = []

    def tearDown(self):
        """Delete order matters: specialty_notifications_log has a real,
        RESTRICT-by-default FK to dissertations, so it must go first; fix_log
        rows must go before their run (ON DELETE RESTRICT); user_bookmarks and
        the self-referencing merged_into_id both CASCADE/SET NULL, so they
        don't need explicit cleanup, but the delete order below is written to
        not depend on that."""
        with self.conn.cursor() as cur:
            if self._diss_ids:
                cur.execute("DELETE FROM specialty_notifications_log "
                            "WHERE dissertation_id = ANY(%s)", (self._diss_ids,))
                cur.execute("DELETE FROM user_bookmarks WHERE dissertation_id = ANY(%s)",
                            (self._diss_ids,))
                cur.execute("DELETE FROM duplicate_candidates "
                            "WHERE record_id_1 = ANY(%s) OR record_id_2 = ANY(%s)",
                            (self._diss_ids, self._diss_ids))
            if self._run_ids:
                cur.execute("DELETE FROM data_quality_fix_log WHERE run_id = ANY(%s)",
                            (self._run_ids,))
                cur.execute("DELETE FROM data_quality_runs WHERE id = ANY(%s)",
                            (self._run_ids,))
            if self._diss_ids:
                cur.execute("DELETE FROM dissertations WHERE id = ANY(%s)",
                           (self._diss_ids,))
            if self._institution_ids:
                cur.execute("DELETE FROM institutions WHERE id = ANY(%s)",
                            (self._institution_ids,))
        self.conn.commit()

    def _make_dissertation(self, **fields) -> int:
        """Commits immediately: fixture rows must survive a rollback the test
        later triggers on purpose (see test_failed_merge_leaves_no_partial_write)
        — only the merge's OWN writes should ever be undone, never the fixture
        that set the scenario up."""
        cols = list(fields.keys())
        with self.conn.cursor() as cur:
            if cols:
                placeholders = ", ".join(["%s"] * len(cols))
                cur.execute(
                    f"INSERT INTO dissertations ({', '.join(cols)}) "
                    f"VALUES ({placeholders}) RETURNING id",
                    [fields[c] for c in cols])
            else:
                cur.execute("INSERT INTO dissertations DEFAULT VALUES RETURNING id")
            new_id = cur.fetchone()[0]
        self.conn.commit()
        self._diss_ids.append(new_id)
        return new_id

    def _make_candidate(self, id1: int, id2: int, status: str = "confirmed",
                        match_level: str = "strong", score: float = 0.95) -> int:
        """Also commits — same reasoning as _make_dissertation."""
        lo, hi = (id1, id2) if id1 < id2 else (id2, id1)
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO duplicate_candidates
                       (record_type, record_id_1, record_id_2, score,
                        match_level, status, reasons)
                   VALUES ('dissertation', %s, %s, %s, %s, %s, '[]'::jsonb)
                   RETURNING id""",
                (lo, hi, score, match_level, status))
            cid = cur.fetchone()[0]
        self.conn.commit()
        self._candidate_ids.append(cid)
        return cid

    def _make_run(self) -> int:
        """Also commits — same reasoning as _make_dissertation."""
        run_id = self.repo.create_run(self.conn, "merge", 1, metadata={"test": True})
        self.conn.commit()
        self._run_ids.append(run_id)
        return run_id

    def _fetch(self, diss_id: int) -> dict:
        return self.repo.fetch_dissertation_rows(self.conn, [diss_id])[diss_id]

    # -- schema sanity ---------------------------------------------------------
    def test_migrations_applied(self):
        self.assertTrue(self.repo.dq_tables_exist(self.conn))
        with self.conn.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'dissertations' "
                        "AND column_name = 'merged_into_id'")
            self.assertIsNotNone(cur.fetchone())

    def test_known_fk_list_matches_the_live_schema(self):
        """If this ever fails, either a new table was added that references
        dissertations(id) and merge_dissertations() needs to handle it, or
        KNOWN_FK_REFERENCES_TO_DISSERTATIONS is stale — either way the merge
        script itself would refuse to run, which is the point."""
        self.repo.assert_no_unknown_fk_references(self.conn)   # must not raise
        self.conn.rollback()

    # -- field copy + winner selection, end to end ------------------------------
    def test_merge_fills_blank_fields_and_retires_the_loser(self):
        from data_quality import merge_review as review

        fuller = self._make_dissertation(mavzu="To'liq mavzu", olim="Aliyev Vali",
                                         muassasa="TATU")
        thinner = self._make_dissertation(mavzu="To'liq mavzu")
        cand_id = self._make_candidate(fuller, thinner)
        run_id = self._make_run()

        candidate = self.repo.fetch_confirmed_candidates(
            self.conn, candidate_id=cand_id)[0]
        rows = self.repo.fetch_dissertation_rows(
            self.conn, (fuller, thinner), for_update=True)
        plan, decision = review.plan_merge(candidate, rows[fuller], rows[thinner])
        self.assertTrue(decision.ok)
        self.assertEqual(plan.winner_id, fuller)
        self.assertEqual(plan.loser_id, thinner)

        result = self.repo.merge_dissertations(self.conn, run_id, plan, "test")
        self.conn.commit()

        self.assertEqual(result.fields_filled, 0)   # winner already had every field
        winner_row = self._fetch(fuller)
        loser_row = self._fetch(thinner)
        self.assertEqual(winner_row["olim"], "Aliyev Vali")   # unchanged, never overwritten
        self.assertIsNone(winner_row["merged_into_id"])
        self.assertEqual(loser_row["merged_into_id"], fuller)
        self.assertIsNotNone(loser_row["merged_at"])

    def test_merge_actually_fills_a_blank_winner_field(self):
        from data_quality import merge_review as review

        winner = self._make_dissertation(mavzu="Mavzu", olim="Aliyev Vali",
                                         muassasa=None)
        loser = self._make_dissertation(mavzu="Mavzu", muassasa="TATU")
        cand_id = self._make_candidate(winner, loser)
        run_id = self._make_run()

        candidate = self.repo.fetch_confirmed_candidates(
            self.conn, candidate_id=cand_id)[0]
        rows = self.repo.fetch_dissertation_rows(
            self.conn, (winner, loser), for_update=True)
        plan, decision = review.plan_merge(candidate, rows[winner], rows[loser],
                                           forced_winner_id=winner)
        self.assertTrue(decision.ok)

        result = self.repo.merge_dissertations(self.conn, run_id, plan, "test")
        self.conn.commit()

        self.assertEqual(result.fields_filled, 1)
        self.assertEqual(self._fetch(winner)["muassasa"], "TATU")

        # And the fix log has exactly one dissertations.muassasa row for it.
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT value_before, value_after FROM data_quality_fix_log "
                "WHERE run_id = %s AND record_id = %s AND table_name = 'dissertations' "
                "AND column_name = 'muassasa'", (run_id, winner))
            row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertIsNone(row[0])
        self.assertEqual(row[1], "TATU")

    # -- FK repointing -----------------------------------------------------------
    def test_bookmarks_are_repointed(self):
        winner = self._make_dissertation(mavzu="A")
        loser = self._make_dissertation(mavzu="A")
        cand_id = self._make_candidate(winner, loser)
        run_id = self._make_run()

        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO user_bookmarks (user_id, dissertation_id) "
                       "VALUES (777001, %s)", (loser,))
        self.conn.commit()

        self._do_merge(cand_id, run_id, forced_winner=winner)

        with self.conn.cursor() as cur:
            cur.execute("SELECT dissertation_id FROM user_bookmarks "
                       "WHERE user_id = 777001")
            self.assertEqual(cur.fetchone()[0], winner)

    def test_bookmark_conflict_drops_the_redundant_row_not_both(self):
        """A user bookmarked BOTH records in the pair. UNIQUE(user_id,
        dissertation_id) would reject a plain repoint; the loser-side row must
        be dropped, and the winner-side bookmark must survive untouched."""
        winner = self._make_dissertation(mavzu="A")
        loser = self._make_dissertation(mavzu="A")
        cand_id = self._make_candidate(winner, loser)
        run_id = self._make_run()

        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO user_bookmarks (user_id, dissertation_id) "
                       "VALUES (777002, %s), (777002, %s)", (winner, loser))
        self.conn.commit()

        result = self._do_merge(cand_id, run_id, forced_winner=winner)
        self.assertEqual(result.bookmarks_dropped, 1)
        self.assertEqual(result.bookmarks_repointed, 0)

        with self.conn.cursor() as cur:
            cur.execute("SELECT dissertation_id FROM user_bookmarks "
                       "WHERE user_id = 777002")
            rows = [r[0] for r in cur.fetchall()]
        self.assertEqual(rows, [winner])   # exactly one row left, on the winner

    def test_specialty_notifications_log_is_repointed(self):
        winner = self._make_dissertation(mavzu="A")
        loser = self._make_dissertation(mavzu="A")
        cand_id = self._make_candidate(winner, loser)
        run_id = self._make_run()

        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO specialty_notifications_log "
                       "(dissertation_id) VALUES (%s)", (loser,))
        self.conn.commit()

        result = self._do_merge(cand_id, run_id, forced_winner=winner)
        self.assertEqual(result.subscriptions_repointed, 1)

        with self.conn.cursor() as cur:
            cur.execute("SELECT dissertation_id FROM specialty_notifications_log "
                       "WHERE dissertation_id = %s", (winner,))
            self.assertIsNotNone(cur.fetchone())

    # -- dissertation_institutions (migration 021) -----------------------------
    def _make_institution(self, name: str) -> int:
        import uuid
        key = "test-merge-%s-%s" % (name, uuid.uuid4().hex[:8])
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO institutions (canonical_name, normalized_name) "
                        "VALUES (%s, %s) RETURNING id", (key, key))
            iid = cur.fetchone()[0]
        self.conn.commit()
        self._institution_ids.append(iid)
        return iid

    def _link(self, diss_id: int, institution_id: int, source: str = "admin") -> None:
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO dissertation_institutions "
                        "(dissertation_id, institution_id, source) VALUES (%s, %s, %s)",
                        (diss_id, institution_id, source))
        self.conn.commit()

    def _links(self, diss_id: int) -> dict:
        with self.conn.cursor() as cur:
            cur.execute("SELECT institution_id, source FROM dissertation_institutions "
                        "WHERE dissertation_id = %s", (diss_id,))
            rows = cur.fetchall()
        self.conn.rollback()
        return dict(rows)

    def test_institution_links_move_to_the_winner(self):
        winner = self._make_dissertation(mavzu="A")
        loser = self._make_dissertation(mavzu="A")
        x = self._make_institution("x")
        self._link(loser, x, source="backfill")
        cand_id = self._make_candidate(winner, loser)
        run_id = self._make_run()

        result = self._do_merge(cand_id, run_id, forced_winner=winner)

        self.assertEqual((result.institution_links_repointed,
                          result.institution_links_dropped), (1, 0))
        self.assertEqual(self._links(winner), {x: "backfill"})   # source kept
        self.assertEqual(self._links(loser), {})
        with self.conn.cursor() as cur:
            cur.execute("""SELECT record_id, value_before, value_after, issue_code
                             FROM data_quality_fix_log
                            WHERE run_id = %s AND table_name = 'dissertation_institutions'""",
                        (run_id,))
            self.assertEqual(cur.fetchall(),
                             [(x, str(loser), str(winner), self.repo.MERGE_LINK_REPOINTED)])
        self.conn.rollback()

    def test_shared_institution_link_is_not_duplicated(self):
        """Both records name co-owner X (PK collision): the winner keeps one X,
        the loser's other link Y moves, nothing is left on the loser."""
        winner = self._make_dissertation(mavzu="A")
        loser = self._make_dissertation(mavzu="A")
        x = self._make_institution("x")
        y = self._make_institution("y")
        self._link(winner, x, source="admin")
        self._link(loser, x, source="backfill")
        self._link(loser, y)
        cand_id = self._make_candidate(winner, loser)

        result = self._do_merge(cand_id, self._make_run(), forced_winner=winner)

        self.assertEqual((result.institution_links_repointed,
                          result.institution_links_dropped), (1, 1))
        self.assertEqual(self._links(winner), {x: "admin", y: "admin"})
        self.assertEqual(self._links(loser), {})

    def test_link_to_the_winners_primary_institution_is_not_copied(self):
        """A co-owner equal to the winner's own primary institution would list
        the same organisation twice; institution_backfill never stores that."""
        x = self._make_institution("x")
        winner = self._make_dissertation(mavzu="A", institution_id=x)
        loser = self._make_dissertation(mavzu="A")
        self._link(loser, x)
        cand_id = self._make_candidate(winner, loser)

        result = self._do_merge(cand_id, self._make_run(), forced_winner=winner)

        self.assertEqual(result.institution_links_dropped, 1)
        self.assertEqual(self._links(winner), {})
        self.assertEqual(self._links(loser), {})

    def test_other_duplicate_candidate_rows_are_repointed(self):
        """(loser, third) becomes (winner, third) once loser is retired."""
        winner = self._make_dissertation(mavzu="A")
        loser = self._make_dissertation(mavzu="A")
        third = self._make_dissertation(mavzu="C")
        cand_id = self._make_candidate(winner, loser)
        other_id = self._make_candidate(loser, third, status="pending")
        run_id = self._make_run()

        self._do_merge(cand_id, run_id, forced_winner=winner)

        with self.conn.cursor() as cur:
            cur.execute("SELECT record_id_1, record_id_2, status "
                       "FROM duplicate_candidates WHERE id = %s", (other_id,))
            id1, id2, status = cur.fetchone()
        self.assertEqual({id1, id2}, {winner, third})
        self.assertEqual(status, "pending")   # only the identity moved, not the review

    def test_duplicate_candidate_conflict_drops_the_redundant_row(self):
        """A (winner, third) row already exists; (loser, third) becomes
        redundant once loser retires into winner, and must be dropped rather
        than violating UNIQUE(record_type, record_id_1, record_id_2)."""
        winner = self._make_dissertation(mavzu="A")
        loser = self._make_dissertation(mavzu="A")
        third = self._make_dissertation(mavzu="C")
        cand_id = self._make_candidate(winner, loser)
        self._make_candidate(winner, third, status="rejected")
        redundant_id = self._make_candidate(loser, third, status="pending")
        run_id = self._make_run()

        result = self._do_merge(cand_id, run_id, forced_winner=winner)
        self.assertEqual(result.duplicate_rows_dropped, 1)

        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM duplicate_candidates WHERE id = %s",
                       (redundant_id,))
            self.assertIsNone(cur.fetchone())

    def test_candidate_itself_is_marked_merged(self):
        winner = self._make_dissertation(mavzu="A")
        loser = self._make_dissertation(mavzu="A")
        cand_id = self._make_candidate(winner, loser)
        run_id = self._make_run()

        self._do_merge(cand_id, run_id, forced_winner=winner)

        with self.conn.cursor() as cur:
            cur.execute("SELECT status, reviewed_at FROM duplicate_candidates "
                       "WHERE id = %s", (cand_id,))
            status, reviewed_at = cur.fetchone()
        self.assertEqual(status, "merged")
        self.assertIsNotNone(reviewed_at)

    # -- rollback ------------------------------------------------------------
    def test_failed_merge_leaves_no_partial_write(self):
        """Force a failure partway through by handing merge_dissertations a
        plan whose winner_id does not exist (violates the self-referencing
        merged_into_id FK on step 5) — everything from steps 1-4 in that same
        call must be rolled back too, since it is all one transaction."""
        from data_quality.merge_review import FieldFill, MergePlan

        winner = self._make_dissertation(mavzu="A")   # will be deleted, see below
        loser = self._make_dissertation(mavzu="A", olim=None)
        cand_id = self._make_candidate(winner, loser)
        run_id = self._make_run()

        bogus_plan = MergePlan(candidate_id=cand_id, winner_id=winner,
                               loser_id=loser, reason="test",
                               fills=[FieldFill("olim", None, "Injected Value")])

        # Delete the winner row out from under the plan (still inside a live
        # transaction) so step 5's UPDATE ... merged_into_id = winner fails its
        # foreign key check, and step 1's field write must not survive that.
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM specialty_notifications_log "
                       "WHERE dissertation_id = %s", (winner,))
            cur.execute("DELETE FROM dissertations WHERE id = %s", (winner,))

        with self.assertRaises(Exception):
            self.repo.merge_dissertations(self.conn, run_id, bogus_plan, "test")
        self.conn.rollback()

        # winner/loser were committed by _make_dissertation before this test
        # started tampering, so both ids are already in self._diss_ids and
        # tearDown will clean them up regardless of what the rollback did or
        # didn't undo. The manual DELETE above must itself be undone by the
        # rollback — the winner row must exist again, unmerged.
        row = self.repo.fetch_dissertation_rows(self.conn, [winner]).get(winner)
        self.assertIsNotNone(row, "the winner row must still exist — rollback failed")
        self.assertIsNone(row["merged_into_id"])

        loser_row = self.repo.fetch_dissertation_rows(self.conn, [loser])[loser]
        self.assertIsNone(loser_row["merged_into_id"],
                          "loser must not be retired if the merge failed")
        self.assertIsNone(loser_row["olim"],
                          "the field fill must not survive a rolled-back merge")

        with self.conn.cursor() as cur:
            cur.execute("SELECT status FROM duplicate_candidates WHERE id = %s",
                       (cand_id,))
            self.assertEqual(cur.fetchone()[0], "confirmed",
                             "the candidate must not be marked merged if the "
                             "transaction rolled back")
            cur.execute("SELECT COUNT(*) FROM data_quality_fix_log WHERE run_id = %s",
                       (run_id,))
            self.assertEqual(cur.fetchone()[0], 0,
                             "step 1's fix_log insert must not survive a "
                             "rolled-back merge either")

    # -- chains: an admin's review covers only the exact pair they saw ---------
    def _candidate(self, cand_id: int):
        with self.conn.cursor() as cur:
            cur.execute("SELECT record_id_1, record_id_2, status, reviewed_at "
                        "FROM duplicate_candidates WHERE id = %s", (cand_id,))
            row = cur.fetchone()
        self.conn.rollback()
        if row is None:
            return None
        return {"ids": {row[0], row[1]}, "status": row[2], "reviewed_at": row[3]}

    def _mark_reviewed(self, *cand_ids: int) -> None:
        with self.conn.cursor() as cur:
            cur.execute("UPDATE duplicate_candidates SET reviewed_at = NOW() "
                        "WHERE id = ANY(%s)", (list(cand_ids),))
        self.conn.commit()

    def _log_codes(self, run_id: int, cand_id: int) -> dict:
        with self.conn.cursor() as cur:
            cur.execute("""SELECT issue_code, column_name, value_before, value_after
                             FROM data_quality_fix_log
                            WHERE run_id = %s AND table_name = 'duplicate_candidates'
                              AND record_id = %s ORDER BY id""", (run_id, cand_id))
            rows = cur.fetchall()
        self.conn.rollback()
        return {r[0]: r[1:] for r in rows}

    def _plan(self, cand_id: int):
        from data_quality import merge_review as review
        candidate = self.repo.fetch_confirmed_candidates(
            self.conn, candidate_id=cand_id)[0]
        rows = self.repo.fetch_dissertation_rows(
            self.conn, (candidate["record_id_1"], candidate["record_id_2"]))
        plan, decision = review.plan_merge(
            candidate, rows[candidate["record_id_1"]], rows[candidate["record_id_2"]])
        self.conn.rollback()
        return plan, decision

    @staticmethod
    def _pair(x: int, y: int) -> str:
        return "%d,%d" % (min(x, y), max(x, y))

    def test_chain_repointed_pair_goes_back_to_pending(self):
        """(A,B)+(B,C) confirmed; merging B into A turns (B,C) into (A,C),
        which nobody reviewed, so it must not stay confirmed."""
        a = self._make_dissertation(mavzu="A")
        b = self._make_dissertation(mavzu="A")
        c = self._make_dissertation(mavzu="A")
        cand_ab = self._make_candidate(a, b)
        cand_bc = self._make_candidate(b, c)
        self._mark_reviewed(cand_ab, cand_bc)
        run_id = self._make_run()

        result = self._do_merge(cand_ab, run_id, forced_winner=a)

        self.assertEqual(result.duplicate_rows_requeued, 1)
        row = self._candidate(cand_bc)
        self.assertEqual(row["ids"], {a, c})
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["reviewed_at"])
        log = self._log_codes(run_id, cand_bc)
        self.assertEqual(log[self.repo.MERGE_PAIR_REPOINTED],
                         ("record_id_1/record_id_2", self._pair(b, c), self._pair(a, c)))
        self.assertEqual(log[self.repo.MERGE_PAIR_REQUEUED],
                         ("status", "confirmed", "pending"))
        plan, decision = self._plan(cand_bc)
        self.assertIsNone(plan)
        self.assertFalse(decision.ok)
        self.assertIn("'pending'", decision.reason)

    def test_chain_in_one_cli_run_does_not_merge_the_repointed_pair(self):
        """The CLI lists every confirmed candidate up front; execute_one
        re-reads each one under lock, so (A,C) is skipped in the same run."""
        import merge_duplicates as MD
        a = self._make_dissertation(mavzu="A")
        b = self._make_dissertation(mavzu="A")
        c = self._make_dissertation(mavzu="A")
        cand_ab = self._make_candidate(a, b)
        cand_bc = self._make_candidate(b, c)
        run_id = self._make_run()

        first = MD.execute_one(self.conn, run_id, cand_ab, a, "test")
        self.assertTrue(first.merged, first.skipped_reason)
        self.conn.commit()
        second = MD.execute_one(self.conn, run_id, cand_bc, None, "test")
        self.conn.rollback()
        self.assertFalse(second.merged)
        self.assertIn("'pending'", second.skipped_reason)
        self.assertIsNone(self._fetch(c)["merged_into_id"])

    def test_triangle_keeps_the_pair_the_admin_actually_reviewed(self):
        """(A,B)+(B,C)+(A,C) confirmed: after B -> A, (B,C) duplicates the
        reviewed (A,C) and is dropped; (A,C) keeps its own confirmation."""
        a = self._make_dissertation(mavzu="A")
        b = self._make_dissertation(mavzu="A")
        c = self._make_dissertation(mavzu="A")
        cand_ab = self._make_candidate(a, b)
        cand_bc = self._make_candidate(b, c)
        cand_ac = self._make_candidate(a, c)
        run_id = self._make_run()

        result = self._do_merge(cand_ab, run_id, forced_winner=a)

        self.assertEqual(result.duplicate_rows_dropped, 1)
        self.assertEqual(result.duplicate_rows_requeued, 0)
        self.assertIsNone(self._candidate(cand_bc))
        dropped = self._log_codes(run_id, cand_bc)[self.repo.MERGE_PAIR_DROPPED]
        self.assertIn("status=confirmed", dropped[1])
        self.assertIn("#%d" % cand_ac, dropped[2])
        self.assertEqual(self._candidate(cand_ac)["status"], "confirmed")
        _plan, decision = self._plan(cand_ac)
        self.assertTrue(decision.ok, decision.reason)
        self._do_merge(cand_ac, self._make_run(), forced_winner=a)
        self.assertEqual(self._fetch(c)["merged_into_id"], a)

    def test_reverse_order_winner_keeps_the_other_pair_untouched(self):
        """(B,C) merged first with B surviving: (A,B) did not change, so the
        admin's confirmation still applies and it merges normally."""
        a = self._make_dissertation(mavzu="A")
        b = self._make_dissertation(mavzu="A")
        c = self._make_dissertation(mavzu="A")
        cand_ab = self._make_candidate(a, b)
        cand_bc = self._make_candidate(b, c)

        result = self._do_merge(cand_bc, self._make_run(), forced_winner=b)

        self.assertEqual(result.duplicate_rows_requeued, 0)
        row = self._candidate(cand_ab)
        self.assertEqual((row["ids"], row["status"]), ({a, b}, "confirmed"))
        self._do_merge(cand_ab, self._make_run(), forced_winner=a)
        self.assertEqual(self._fetch(b)["merged_into_id"], a)

    def test_reverse_order_loser_requeues_the_other_pair(self):
        """(B,C) merged first with C surviving: (A,B) becomes (A,C) and goes
        back to pending."""
        a = self._make_dissertation(mavzu="A")
        b = self._make_dissertation(mavzu="A")
        c = self._make_dissertation(mavzu="A")
        cand_ab = self._make_candidate(a, b)
        cand_bc = self._make_candidate(b, c)
        run_id = self._make_run()

        self._do_merge(cand_bc, run_id, forced_winner=c)

        row = self._candidate(cand_ab)
        self.assertEqual((row["ids"], row["status"]), ({a, c}, "pending"))
        self.assertIn(self.repo.MERGE_PAIR_REQUEUED, self._log_codes(run_id, cand_ab))
        self.assertFalse(self._plan(cand_ab)[1].ok)

    def test_rejected_pair_whose_members_change_goes_back_to_pending(self):
        """A rejection, like a confirmation, was about the exact pair seen."""
        a = self._make_dissertation(mavzu="A")
        b = self._make_dissertation(mavzu="A")
        c = self._make_dissertation(mavzu="C")
        cand_ab = self._make_candidate(a, b)
        cand_bc = self._make_candidate(b, c, status="rejected")
        run_id = self._make_run()

        self._do_merge(cand_ab, run_id, forced_winner=a)

        self.assertEqual(self._candidate(cand_bc)["status"], "pending")
        self.assertEqual(self._log_codes(run_id, cand_bc)[self.repo.MERGE_PAIR_REQUEUED],
                         ("status", "rejected", "pending"))

    def test_reconfirming_a_merged_candidate_id_is_refused(self):
        winner = self._make_dissertation(mavzu="A")
        loser = self._make_dissertation(mavzu="A")
        cand_id = self._make_candidate(winner, loser)
        run_id = self._make_run()
        self._do_merge(cand_id, run_id, forced_winner=winner)

        # Simulate a second, later invocation of the CLI over the same pair —
        # plan_merge alone is enough to prove the refusal; no new run row needed.
        candidate = self.repo.fetch_confirmed_candidates(
            self.conn, candidate_id=cand_id)[0]
        self.assertEqual(candidate["status"], "merged")
        rows = self.repo.fetch_dissertation_rows(
            self.conn, (candidate["record_id_1"], candidate["record_id_2"]),
            for_update=True)
        from data_quality import merge_review as review
        plan, decision = review.plan_merge(
            candidate, rows[candidate["record_id_1"]], rows[candidate["record_id_2"]])
        self.conn.rollback()
        self.assertIsNone(plan)
        self.assertFalse(decision.ok)
        self.assertIn("'merged'", decision.reason)

    # -- helper ----------------------------------------------------------------
    def _do_merge(self, candidate_id: int, run_id: int, forced_winner=None):
        from data_quality import merge_review as review
        candidate = self.repo.fetch_confirmed_candidates(
            self.conn, candidate_id=candidate_id)[0]
        rows = self.repo.fetch_dissertation_rows(
            self.conn, (candidate["record_id_1"], candidate["record_id_2"]),
            for_update=True)
        plan, decision = review.plan_merge(
            candidate, rows[candidate["record_id_1"]], rows[candidate["record_id_2"]],
            forced_winner_id=forced_winner)
        self.assertTrue(decision.ok, decision.reason)
        result = self.repo.merge_dissertations(self.conn, run_id, plan, "test")
        self.conn.commit()
        return result


if __name__ == "__main__":
    unittest.main()
