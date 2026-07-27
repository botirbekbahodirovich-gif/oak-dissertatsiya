"""Decision logic for executing a confirmed duplicate merge — pure, no DB.

``scripts/merge_duplicates.py`` does the actual writing (repository.py holds all
the SQL, as everywhere else in this package); this module only decides *what*
should happen, so the riskiest decisions — who survives, what gets copied — are
unit-testable without a database and without ever risking real data.

Terminology: the ``winner`` is the surviving record; the ``loser`` is the record
that is retained but marked ``merged_into_id`` — never deleted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Mapping, Optional, Tuple

# Fields eligible to be copied from the loser onto the winner when the winner's
# copy is blank. This is every free-text descriptive column on `dissertations`
# EXCEPT identity/bookkeeping columns (id, created_at, data_quality, oak_id,
# merged_into_id, merged_at) and photo URLs (a photo is picked, not merged text
# — copying is still fine for those, they behave like any other text field, so
# they ARE included below).
MERGE_FIELDS: Tuple[str, ...] = (
    "sana", "daraja", "olim", "mavzu", "ixtisoslik", "muassasa", "ilmiy_rahbar",
    "link", "fan_tarmoqi", "ixtisoslik_nomi", "mavzu_raqami", "ilmiy_kengash",
    "ilmiy_kengash_raqami", "opponent_1", "opponent_2", "opponent_3",
    "yetakchi_tashkilot", "ilmiy_rahbar_daraja", "yonalish", "photo_url",
    "ilmiy_rahbar_photo_url",
)

# oak_id is UNIQUE — copying it while the source row keeps the same value would
# violate the constraint, and clearing it from the loser would destroy that
# row's own link back to its OAK announcement. It is excluded from copying, but
# still counts toward completeness: a record with a real oak_id is more
# traceable than one without, so it should still weigh in on who survives.
# See docs/data_quality_dissertation_fk_inventory.md §4.
COMPLETENESS_FIELDS: Tuple[str, ...] = MERGE_FIELDS + ("oak_id",)


def is_blank(value) -> bool:
    return value is None or str(value).strip() == ""


def completeness_score(record: Mapping) -> int:
    """Count of non-blank fields among COMPLETENESS_FIELDS. Higher is fuller."""
    return sum(1 for f in COMPLETENESS_FIELDS if not is_blank(record.get(f)))


@dataclass
class WinnerChoice:
    winner_id: int
    loser_id: int
    reason: str
    winner_score: int
    loser_score: int


def choose_winner(record_a: Mapping, record_b: Mapping) -> WinnerChoice:
    """Pick the surviving record: fewer blank fields wins; a tie goes to the
    smaller id. Deterministic and independent of argument order.

    Both records must carry an 'id' key. This function never touches a
    database — it is a pure decision over two already-fetched rows, so a
    reviewer or a test can predict the outcome without a live connection.
    """
    id_a, id_b = record_a["id"], record_b["id"]
    if id_a == id_b:
        raise ValueError("a record cannot be merged with itself")
    score_a = completeness_score(record_a)
    score_b = completeness_score(record_b)

    if score_a > score_b:
        return WinnerChoice(id_a, id_b, "more complete", score_a, score_b)
    if score_b > score_a:
        return WinnerChoice(id_b, id_a, "more complete", score_b, score_a)
    # Tie: smaller id wins (deterministic, cheap, order-independent).
    if id_a < id_b:
        return WinnerChoice(id_a, id_b, "tie — smaller id", score_a, score_b)
    return WinnerChoice(id_b, id_a, "tie — smaller id", score_b, score_a)


@dataclass
class FieldFill:
    field: str
    old_value: Optional[str]   # always blank/None-ish — kept for the log/report
    new_value: str


def build_fill_plan(winner: Mapping, loser: Mapping) -> List[FieldFill]:
    """Which winner fields get filled from the loser, and with what.

    Invariant that must never be violated: a field that already has a
    non-blank value on the winner is NEVER touched, even if the loser's value
    differs. This function only fills gaps — it never overwrites a real value,
    and it never proposes anything for `oak_id` (see COMPLETENESS_FIELDS docs).
    """
    plan: List[FieldFill] = []
    for f in MERGE_FIELDS:
        winner_value = winner.get(f)
        loser_value = loser.get(f)
        if is_blank(winner_value) and not is_blank(loser_value):
            plan.append(FieldFill(field=f, old_value=winner_value,
                                  new_value=str(loser_value)))
    return plan


@dataclass
class MergeDecision:
    ok: bool
    reason: str = ""


def can_merge(candidate_status: str, winner_row: Mapping,
              loser_row: Mapping) -> MergeDecision:
    """Guards that must hold before a single write happens.

    Kept separate from choose_winner/build_fill_plan so the CLI can print a
    clear, specific refusal reason instead of a generic failure.
    """
    if candidate_status != "confirmed":
        return MergeDecision(False, f"candidate status is {candidate_status!r}, "
                                    "not 'confirmed' — review it first")
    if winner_row["id"] == loser_row["id"]:
        return MergeDecision(False, "winner and loser are the same record")
    if winner_row.get("merged_into_id") is not None:
        return MergeDecision(False, f"winner #{winner_row['id']} was already "
                                    f"merged into #{winner_row['merged_into_id']}")
    if loser_row.get("merged_into_id") is not None:
        return MergeDecision(False, f"loser #{loser_row['id']} was already "
                                    f"merged into #{loser_row['merged_into_id']}")
    return MergeDecision(True)


@dataclass
class MergePlan:
    candidate_id: int
    winner_id: int
    loser_id: int
    reason: str
    fills: List[FieldFill] = field(default_factory=list)


def plan_merge(candidate: Mapping, record_a: Mapping, record_b: Mapping,
               forced_winner_id: Optional[int] = None
               ) -> Tuple[Optional[MergePlan], MergeDecision]:
    """End-to-end pure planning for one candidate: guard, choose, fill.

    ``record_a``/``record_b`` are the pair's two rows — neither is assumed to
    be the winner going in, that is exactly what this function decides.
    ``forced_winner_id`` lets an operator override the automatic choice for one
    candidate (the CLI's ``--winner`` flag) — the guard and fill-plan logic are
    identical either way, only the winner/loser assignment changes.
    Returns (plan, decision). plan is None when decision.ok is False.
    """
    decision = can_merge(candidate.get("status"), record_a, record_b)
    if not decision.ok:
        return None, decision

    if forced_winner_id is not None:
        ids = {record_a["id"], record_b["id"]}
        if forced_winner_id not in ids:
            return None, MergeDecision(
                False, f"--winner {forced_winner_id} is not one of the "
                       f"candidate's two records {sorted(ids)}")
        if forced_winner_id == record_a["id"]:
            winner, loser, reason = record_a, record_b, "operator override"
        else:
            winner, loser, reason = record_b, record_a, "operator override"
    else:
        choice = choose_winner(record_a, record_b)
        if choice.winner_id == record_a["id"]:
            winner, loser = record_a, record_b
        else:
            winner, loser = record_b, record_a
        reason = choice.reason

    fills = build_fill_plan(winner, loser)
    plan = MergePlan(candidate_id=candidate["id"], winner_id=winner["id"],
                     loser_id=loser["id"], reason=reason, fills=fills)
    return plan, MergeDecision(True)
