# Data Quality — Quality Score

A single deterministic 0–100 score, plus a per-dimension breakdown, produced by
`data_quality/scoring.py::compute_score`. The **same** function feeds the CLI
scanner and the admin dashboard, so the two can never disagree.

## Dimensions and weights

| Dimension | Default weight | Driven by |
|---|---|---|
| completeness | 0.30 | MISSING_* issues |
| validity | 0.25 | INVALID_*/FUTURE_DATE/SHORT_TITLE/TITLE_IS_CODE/HTML_RESIDUE/CONTROL_CHARS/PLACEHOLDER_* |
| consistency | 0.20 | INCONSISTENT_* issues |
| uniqueness | 0.15 | duplicate candidate count |
| integrity | 0.10 | integrity issues (orphans; extensible) |

Weights come from `DataQualityConfig.score_weights`
(`DATA_QUALITY_WEIGHT_*` is reserved for future exposure) and are normalised to
sum to 1 at use time.

## Formula

Severity weighting:

```
critical = 3.0    warning = 1.0    info = 0.4
```

A run-wide severity multiplier shifts penalties up when criticals dominate and
down when everything is info-level (deterministic, within `[0.4, 3.0]`):

```
sev_multiplier = Σ(weight(sev) · count(sev)) / Σ count(sev)
```

Per dimension (except uniqueness):

```
penalty_d = min(1, issue_count_d · sev_multiplier / total_records)
score_d   = 100 · (1 − penalty_d)
```

Uniqueness uses the duplicate count instead of issues:

```
penalty_uniqueness = min(1, duplicate_count / total_records)
```

Overall is the normalised weighted average:

```
overall = Σ(weight_d · score_d) / Σ weight_d
```

## Properties
- **Deterministic** — identical input always yields identical output (unit-tested).
- **Bounded** — every dimension and the overall stay within `[0, 100]`.
- **Critical-sensitive** — a batch of critical issues scores strictly lower than
  the same count of info issues (unit-tested).
- **Scale-relative** — penalties divide by `total_records`, so the score is
  comparable across a 1k sample and the full 28k+ set.

## Worked example
100 records, 10 `MISSING_AUTHOR` (critical), 5 duplicate rows →
completeness penalised (~0.3 with sev multiplier), uniqueness penalty 0.05 →
overall ≈ low-90s. See `test_data_quality_scoring.py` for concrete assertions.
