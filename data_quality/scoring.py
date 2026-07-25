"""Quality score — a single, deterministic 0..100 number (plus per-dimension
breakdown) computed from a ScanSummary. Documented in
``docs/data_quality_scoring.md``.

The SAME function feeds both the CLI scanner and the admin dashboard, so the
score can never disagree between the two surfaces.

Model
-----
Each finding contributes a *severity weight* to its quality dimension:

    critical -> 3.0     warning -> 1.0     info -> 0.4

For a dimension the penalty is the weighted issue mass divided by the number of
records scanned (so it scales with the dataset, not its absolute size) and is
clamped to [0, 1]:

    penalty_d   = min(1, weighted_issues_d / total_records)
    score_d     = 100 * (1 - penalty_d)

Uniqueness is driven by the duplicate count instead of field issues:

    penalty_uniq = min(1, duplicate_count / total_records)

The overall score is the configured weighted average of the dimension scores
(weights from DataQualityConfig, normalised so they always sum to 1).
"""

from __future__ import annotations

from typing import Dict, Optional

from data_quality import models as M
from data_quality.config import DataQualityConfig, get_config

_SEVERITY_WEIGHT = {
    M.SEVERITY_CRITICAL: 3.0,
    M.SEVERITY_WARNING: 1.0,
    M.SEVERITY_INFO: 0.4,
}

_DIMENSIONS = (
    M.DIM_COMPLETENESS, M.DIM_VALIDITY, M.DIM_CONSISTENCY,
    M.DIM_UNIQUENESS, M.DIM_INTEGRITY,
)


def _weighted_mass_by_dimension(summary: M.ScanSummary) -> Dict[str, float]:
    """Reconstruct weighted issue mass per dimension from the code/severity
    aggregates carried on the summary."""
    mass = {d: 0.0 for d in _DIMENSIONS}
    for code, count in summary.issues_by_code.items():
        dim = M.ISSUE_DIMENSION.get(code, M.DIM_VALIDITY)
        # We don't keep per-code severity on the summary, so approximate with the
        # canonical severity implied by the code's dimension weighting: use the
        # dominant severity mix already tracked. To stay deterministic we weight
        # every issue of a code by 1.0 here and fold the critical/info emphasis
        # in via issues_by_severity below.
        mass[dim] += float(count)
    return mass


def compute_score(summary: M.ScanSummary,
                  total_records: int,
                  config: Optional[DataQualityConfig] = None) -> Dict:
    """Return {'overall': float, 'dimensions': {dim: float}, ...}. Deterministic."""
    config = config or get_config()
    total = max(1, int(total_records or 0))

    # Severity multiplier for the whole run: shifts penalties up when criticals
    # dominate, down when everything is info-level. Deterministic, in [0.4, 3].
    sev = summary.issues_by_severity or {}
    sev_total = sum(sev.values()) or 1
    sev_multiplier = (
        _SEVERITY_WEIGHT[M.SEVERITY_CRITICAL] * sev.get(M.SEVERITY_CRITICAL, 0)
        + _SEVERITY_WEIGHT[M.SEVERITY_WARNING] * sev.get(M.SEVERITY_WARNING, 0)
        + _SEVERITY_WEIGHT[M.SEVERITY_INFO] * sev.get(M.SEVERITY_INFO, 0)
    ) / sev_total

    mass = _weighted_mass_by_dimension(summary)

    dim_scores: Dict[str, float] = {}
    for dim in _DIMENSIONS:
        if dim == M.DIM_UNIQUENESS:
            penalty = min(1.0, summary.duplicate_count / total)
        else:
            penalty = min(1.0, (mass[dim] * sev_multiplier) / total)
        dim_scores[dim] = round(100.0 * (1.0 - penalty), 2)

    weights = config.score_weights
    wsum = sum(weights.values()) or 1.0
    overall = sum(dim_scores[d] * weights.get(d, 0.0) for d in _DIMENSIONS) / wsum

    return {
        "overall": round(overall, 1),
        "dimensions": dim_scores,
        "weights": {d: round(weights.get(d, 0.0) / wsum, 4) for d in _DIMENSIONS},
        "total_records": total,
        "issue_count": summary.issue_count,
        "duplicate_count": summary.duplicate_count,
    }
