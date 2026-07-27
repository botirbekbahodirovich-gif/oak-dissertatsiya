"""Environment-driven configuration for the Data Quality subsystem.

Every value has a *safe* default so that a missing environment variable never
turns on a destructive behaviour. Production posture (the defaults here):

    * scanning ......... ENABLED   (read-only by itself)
    * auto-fix ......... DISABLED  (fixes only ever run via the explicit
                                    ``apply_data_quality_fixes.py --apply`` CLI)
    * import pre-check .. DISABLED  (keeps the live OAK import byte-for-byte
                                    identical until an operator opts in)

The config object is a frozen dataclass built once from ``os.environ``. It is
deterministic and has no side effects, so it is safe to import anywhere.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


# A `possible` duplicate candidate below this title score is the noisy tail
# (0.72–0.86 on staging, ~575 of 1 597); at or above it the title is effectively
# identical and the pair is worth a reviewer's time. Lives here so the SQL
# filter (repository) and the UI label (duplicate_review) can never disagree.
HIGH_YIELD_MIN_SCORE = 0.87


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        val = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        val = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    # thresholds are similarity ratios in [0, 1]
    if 0.0 <= val <= 1.0:
        return val
    return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on", "enabled")


@dataclass(frozen=True)
class DataQualityConfig:
    # Batch / export sizing -------------------------------------------------
    batch_size: int = 1000
    max_export_rows: int = 50000

    # Duplicate-detection similarity thresholds (0..1) ----------------------
    exact_threshold: float = 0.98
    strong_threshold: float = 0.88
    possible_threshold: float = 0.72

    # Feature switches ------------------------------------------------------
    auto_fix_enabled: bool = False
    scan_enabled: bool = True
    import_precheck_enabled: bool = False

    # pg_trgm: never auto-create the extension; only USE it if already present
    use_pg_trgm_if_present: bool = True

    # Quality-score weights (must sum to ~1.0; normalised at use site) -------
    weight_completeness: float = 0.30
    weight_validity: float = 0.25
    weight_consistency: float = 0.20
    weight_uniqueness: float = 0.15
    weight_integrity: float = 0.10

    @property
    def score_weights(self) -> dict:
        return {
            "completeness": self.weight_completeness,
            "validity": self.weight_validity,
            "consistency": self.weight_consistency,
            "uniqueness": self.weight_uniqueness,
            "integrity": self.weight_integrity,
        }


def get_config() -> DataQualityConfig:
    """Build the config from the environment. Cheap; call it wherever needed."""
    return DataQualityConfig(
        batch_size=_env_int("DATA_QUALITY_BATCH_SIZE", 1000),
        max_export_rows=_env_int("DATA_QUALITY_MAX_EXPORT_ROWS", 50000),
        exact_threshold=_env_float("DATA_QUALITY_EXACT_THRESHOLD", 0.98),
        strong_threshold=_env_float("DATA_QUALITY_STRONG_THRESHOLD", 0.88),
        possible_threshold=_env_float("DATA_QUALITY_POSSIBLE_THRESHOLD", 0.72),
        auto_fix_enabled=_env_bool("DATA_QUALITY_AUTO_FIX_ENABLED", False),
        scan_enabled=_env_bool("DATA_QUALITY_SCAN_ENABLED", True),
        import_precheck_enabled=_env_bool("DATA_QUALITY_IMPORT_PRECHECK", False),
    )
