"""Unit tests for data_quality.merge_review — pure, no DB.

These cover the two decisions that actually matter for correctness (who
survives, what gets copied) plus the guards that stop a bad merge before any
SQL is written. The DB-level mechanics (FK repointing, transactions, rollback,
refusing to re-merge) are covered separately by
tests/test_merge_duplicates_dbgated.py, which needs a real staging schema.
"""
import unittest

from data_quality import merge_review as M


def rec(id, **kw):
    base = {f: None for f in M.COMPLETENESS_FIELDS}
    base["id"] = id
    base.update(kw)
    return base


class BlankTests(unittest.TestCase):
    def test_none_and_empty_and_whitespace_are_blank(self):
        for v in (None, "", "   ", "\t\n"):
            self.assertTrue(M.is_blank(v), repr(v))

    def test_real_values_are_not_blank(self):
        for v in ("x", "0", " x ", "05.01.02"):
            self.assertFalse(M.is_blank(v), repr(v))


class CompletenessScoreTests(unittest.TestCase):
    def test_empty_record_scores_zero(self):
        self.assertEqual(M.completeness_score(rec(1)), 0)

    def test_full_record_scores_all_fields(self):
        full = rec(1, **{f: "x" for f in M.COMPLETENESS_FIELDS})
        self.assertEqual(M.completeness_score(full), len(M.COMPLETENESS_FIELDS))

    def test_oak_id_counts_toward_completeness(self):
        """oak_id is excluded from copying but must still count here — see
        docs/data_quality_dissertation_fk_inventory.md §4."""
        with_oak = rec(1, oak_id="4321")
        without_oak = rec(1, oak_id=None)
        self.assertEqual(M.completeness_score(with_oak) -
                         M.completeness_score(without_oak), 1)

    def test_blank_string_does_not_count(self):
        self.assertEqual(M.completeness_score(rec(1, mavzu="   ")), 0)


class ChooseWinnerTests(unittest.TestCase):
    def test_more_complete_record_wins(self):
        fuller = rec(1, mavzu="Mavzu", olim="Aliyev")
        thinner = rec(2, mavzu="Mavzu")
        choice = M.choose_winner(fuller, thinner)
        self.assertEqual(choice.winner_id, 1)
        self.assertEqual(choice.loser_id, 2)
        self.assertEqual(choice.reason, "more complete")

    def test_winner_can_be_the_second_argument(self):
        thinner = rec(1, mavzu="Mavzu")
        fuller = rec(2, mavzu="Mavzu", olim="Aliyev")
        choice = M.choose_winner(thinner, fuller)
        self.assertEqual(choice.winner_id, 2)
        self.assertEqual(choice.loser_id, 1)

    def test_tie_goes_to_smaller_id(self):
        a = rec(50, mavzu="Mavzu")
        b = rec(7, mavzu="Mavzu")
        choice = M.choose_winner(a, b)
        self.assertEqual(choice.winner_id, 7)
        self.assertEqual(choice.loser_id, 50)
        self.assertEqual(choice.reason, "tie — smaller id")

    def test_tie_smaller_id_wins_regardless_of_argument_order(self):
        a = rec(50, mavzu="Mavzu")
        b = rec(7, mavzu="Mavzu")
        self.assertEqual(M.choose_winner(a, b).winner_id, 7)
        self.assertEqual(M.choose_winner(b, a).winner_id, 7)

    def test_both_empty_is_still_a_deterministic_tie(self):
        choice = M.choose_winner(rec(9), rec(3))
        self.assertEqual(choice.winner_id, 3)

    def test_self_merge_is_rejected(self):
        with self.assertRaises(ValueError):
            M.choose_winner(rec(5, mavzu="a"), rec(5, mavzu="b"))


class BuildFillPlanTests(unittest.TestCase):
    def test_blank_winner_field_is_filled_from_loser(self):
        winner = rec(1, mavzu="Mavzu", olim=None)
        loser = rec(2, mavzu="Boshqa", olim="Aliyev Vali")
        plan = M.build_fill_plan(winner, loser)
        fields = {f.field: f.new_value for f in plan}
        self.assertEqual(fields.get("olim"), "Aliyev Vali")

    def test_non_blank_winner_field_is_never_overwritten(self):
        """The invariant that matters most: a real value on the winner is
        never touched, even when the loser disagrees."""
        winner = rec(1, mavzu="Winner mavzu")
        loser = rec(2, mavzu="Loser mavzu — butunlay boshqa")
        plan = M.build_fill_plan(winner, loser)
        self.assertEqual(plan, [])

    def test_blank_loser_field_fills_nothing(self):
        winner = rec(1, muassasa=None)
        loser = rec(2, muassasa=None)
        plan = M.build_fill_plan(winner, loser)
        self.assertEqual(plan, [])

    def test_oak_id_is_never_proposed_for_copying(self):
        """oak_id is UNIQUE — copying it would violate the constraint while
        the loser still holds the same value. See merge_review.MERGE_FIELDS."""
        winner = rec(1, oak_id=None)
        loser = rec(2, oak_id="9999")
        plan = M.build_fill_plan(winner, loser)
        self.assertNotIn("oak_id", {f.field for f in plan})

    def test_every_merge_field_is_eligible(self):
        winner = rec(1)
        loser = rec(2, **{f: f"value-{f}" for f in M.MERGE_FIELDS})
        plan = M.build_fill_plan(winner, loser)
        self.assertEqual({f.field for f in plan}, set(M.MERGE_FIELDS))

    def test_whitespace_only_winner_value_counts_as_blank_and_gets_filled(self):
        winner = rec(1, muassasa="   ")
        loser = rec(2, muassasa="TATU")
        plan = M.build_fill_plan(winner, loser)
        self.assertEqual({f.field: f.new_value for f in plan}["muassasa"], "TATU")


class CanMergeTests(unittest.TestCase):
    def test_confirmed_pair_with_neither_merged_is_ok(self):
        d = M.can_merge("confirmed", rec(1), rec(2))
        self.assertTrue(d.ok)

    def test_pending_status_is_refused(self):
        d = M.can_merge("pending", rec(1), rec(2))
        self.assertFalse(d.ok)
        self.assertIn("pending", d.reason)

    def test_rejected_status_is_refused(self):
        d = M.can_merge("rejected", rec(1), rec(2))
        self.assertFalse(d.ok)

    def test_already_merged_status_is_refused(self):
        d = M.can_merge("merged", rec(1), rec(2))
        self.assertFalse(d.ok)

    def test_self_pair_is_refused(self):
        d = M.can_merge("confirmed", rec(5), rec(5))
        self.assertFalse(d.ok)

    def test_winner_already_merged_elsewhere_is_refused(self):
        winner = rec(1, merged_into_id=99)
        d = M.can_merge("confirmed", winner, rec(2))
        self.assertFalse(d.ok)
        self.assertIn("#1", d.reason)
        self.assertIn("#99", d.reason)

    def test_loser_already_merged_elsewhere_is_refused(self):
        loser = rec(2, merged_into_id=99)
        d = M.can_merge("confirmed", rec(1), loser)
        self.assertFalse(d.ok)
        self.assertIn("#2", d.reason)

    def test_this_guard_rules_out_merge_chains_by_construction(self):
        """A already merged into B; someone confirms (A, C). Refusing here
        means a chain A->B and A->C can never form."""
        a = rec(1, merged_into_id=2)
        c = rec(3)
        self.assertFalse(M.can_merge("confirmed", a, c).ok)
        self.assertFalse(M.can_merge("confirmed", c, a).ok)


class PlanMergeTests(unittest.TestCase):
    def test_full_plan_for_a_confirmed_pair(self):
        candidate = {"id": 77, "status": "confirmed"}
        fuller = rec(1, mavzu="Mavzu", olim="Aliyev")
        thinner = rec(2, mavzu="Mavzu")
        plan, decision = M.plan_merge(candidate, thinner, fuller)
        self.assertTrue(decision.ok)
        self.assertEqual(plan.candidate_id, 77)
        self.assertEqual(plan.winner_id, 1)
        self.assertEqual(plan.loser_id, 2)

    def test_plan_is_none_when_guard_fails(self):
        candidate = {"id": 77, "status": "pending"}
        plan, decision = M.plan_merge(candidate, rec(1), rec(2))
        self.assertIsNone(plan)
        self.assertFalse(decision.ok)

    def test_forced_winner_overrides_automatic_choice(self):
        candidate = {"id": 77, "status": "confirmed"}
        fuller = rec(1, mavzu="Mavzu", olim="Aliyev")   # would auto-win
        thinner = rec(2, mavzu="Mavzu")
        plan, decision = M.plan_merge(candidate, thinner, fuller,
                                      forced_winner_id=2)
        self.assertTrue(decision.ok)
        self.assertEqual(plan.winner_id, 2)
        self.assertEqual(plan.loser_id, 1)
        self.assertEqual(plan.reason, "operator override")

    def test_forced_winner_not_in_pair_is_refused(self):
        candidate = {"id": 77, "status": "confirmed"}
        plan, decision = M.plan_merge(candidate, rec(1), rec(2),
                                      forced_winner_id=999)
        self.assertIsNone(plan)
        self.assertFalse(decision.ok)
        self.assertIn("999", decision.reason)

    def test_forced_winner_still_computes_a_fill_plan(self):
        candidate = {"id": 77, "status": "confirmed"}
        a = rec(1, mavzu=None)
        b = rec(2, mavzu="Boshqa mavzu")
        plan, decision = M.plan_merge(candidate, a, b, forced_winner_id=1)
        self.assertTrue(decision.ok)
        self.assertEqual({f.field for f in plan.fills}, {"mavzu"})


if __name__ == "__main__":
    unittest.main()
