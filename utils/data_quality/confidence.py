"""Weighted confidence scoring for candidate duplicate researcher pairs.

Implements docs/data-quality/architecture.md §4.1: four independent signals
combined into [0, 1]. Per the project's explicit constraint, a score is
*informational only* — nothing in this module merges anything; it only
labels a candidate pair for the human review queue (see
scripts/data_quality_audit.py). "Low confidence" candidates are logged for
visibility, never presented as mergeable.
"""
from difflib import SequenceMatcher

from utils.data_quality.names import normalize_person_name

WEIGHT_NAME = 0.50
WEIGHT_INSTITUTION = 0.20
WEIGHT_SPECIALTY = 0.15
WEIGHT_YEAR = 0.15

BAND_HIGH = 0.90
BAND_MEDIUM = 0.60

_NEUTRAL = 0.5  # score contribution when a signal's inputs are unknown


def _name_similarity(key_a, key_b):
    if not key_a or not key_b:
        return 0.0
    if key_a == key_b:
        return 1.0
    tokens_a, tokens_b = key_a.split(), key_b.split()
    if not tokens_a or not tokens_b:
        return 0.0
    shorter, longer = (tokens_a, tokens_b) if len(tokens_a) <= len(tokens_b) else (tokens_b, tokens_a)
    scores = []
    for tok in shorter:
        best = max((SequenceMatcher(None, tok, other).ratio() for other in longer), default=0.0)
        scores.append(best)
    return sum(scores) / len(scores)


def _shared_signal(value_a, value_b):
    """1.0 if equal and both known, 0.0 if both known but different, neutral
    (0.5) if either is unknown — absence of data shouldn't count against a
    candidate pair the way a confirmed mismatch does."""
    if value_a is None or value_b is None:
        return _NEUTRAL
    return 1.0 if value_a == value_b else 0.0


def _year_proximity(year_a, year_b):
    if year_a is None or year_b is None:
        return _NEUTRAL
    diff = abs(year_a - year_b)
    if diff <= 5:
        return 1.0
    if diff >= 15:
        return 0.0
    return (15 - diff) / 10.0


def score_candidate_pair(a, b):
    """a, b: dicts with optional keys 'name', 'normalized_key',
    'institution_id', 'specialty_code' (or 'branch'), 'year'.
    Returns float in [0, 1]."""
    key_a = a.get("normalized_key") or normalize_person_name(a.get("name"))
    key_b = b.get("normalized_key") or normalize_person_name(b.get("name"))

    name_sim = _name_similarity(key_a, key_b)
    inst_sim = _shared_signal(a.get("institution_id"), b.get("institution_id"))
    specialty_a = a.get("specialty_code") or a.get("branch")
    specialty_b = b.get("specialty_code") or b.get("branch")
    specialty_sim = _shared_signal(specialty_a, specialty_b)
    year_sim = _year_proximity(a.get("year"), b.get("year"))

    score = (WEIGHT_NAME * name_sim + WEIGHT_INSTITUTION * inst_sim
             + WEIGHT_SPECIALTY * specialty_sim + WEIGHT_YEAR * year_sim)
    return max(0.0, min(1.0, score))


def confidence_band(score):
    """'high' | 'medium' | 'low' — informational label only, see module
    docstring. Only 'high' ever pre-fills a suggested merge action; nothing
    auto-applies it."""
    if score >= BAND_HIGH:
        return "high"
    if score >= BAND_MEDIUM:
        return "medium"
    return "low"
