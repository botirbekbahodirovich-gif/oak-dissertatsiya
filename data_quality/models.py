"""Plain data structures shared across the Data Quality subsystem.

These are intentionally dependency-free (no Flask, no psycopg2) so the pure
logic can be unit-tested without a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# ── Severity levels ────────────────────────────────────────────────────────
SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

SEVERITIES = (SEVERITY_CRITICAL, SEVERITY_WARNING, SEVERITY_INFO)


# ── Quality dimensions ─────────────────────────────────────────────────────
DIM_COMPLETENESS = "completeness"
DIM_VALIDITY = "validity"
DIM_CONSISTENCY = "consistency"
DIM_UNIQUENESS = "uniqueness"
DIM_INTEGRITY = "integrity"


# ── Issue codes ────────────────────────────────────────────────────────────
# Completeness
MISSING_AUTHOR = "MISSING_AUTHOR"
MISSING_TITLE = "MISSING_TITLE"
MISSING_ORG = "MISSING_ORG"
MISSING_SPECIALTY = "MISSING_SPECIALTY"
MISSING_DATE = "MISSING_DATE"
MISSING_SUPERVISOR = "MISSING_SUPERVISOR"
MISSING_URL = "MISSING_URL"
# Validity
INVALID_DATE = "INVALID_DATE"
FUTURE_DATE = "FUTURE_DATE"
INVALID_DEGREE = "INVALID_DEGREE"
INVALID_SPECIALTY_CODE = "INVALID_SPECIALTY_CODE"
MALFORMED_URL = "MALFORMED_URL"
SHORT_TITLE = "SHORT_TITLE"
HTML_RESIDUE = "HTML_RESIDUE"
CONTROL_CHARS = "CONTROL_CHARS"
PLACEHOLDER_SUPERVISOR = "PLACEHOLDER_SUPERVISOR"
# Consistency (normalized value differs from original — safe to preview/fix)
INCONSISTENT_DEGREE = "INCONSISTENT_DEGREE"
INCONSISTENT_APOSTROPHE = "INCONSISTENT_APOSTROPHE"
INCONSISTENT_WHITESPACE = "INCONSISTENT_WHITESPACE"
INCONSISTENT_ORG = "INCONSISTENT_ORG"
INCONSISTENT_CODE_FORMAT = "INCONSISTENT_CODE_FORMAT"
INCONSISTENT_URL = "INCONSISTENT_URL"

# Which dimension each issue code contributes to (used by the scorer).
ISSUE_DIMENSION = {
    MISSING_AUTHOR: DIM_COMPLETENESS,
    MISSING_TITLE: DIM_COMPLETENESS,
    MISSING_ORG: DIM_COMPLETENESS,
    MISSING_SPECIALTY: DIM_COMPLETENESS,
    MISSING_DATE: DIM_COMPLETENESS,
    MISSING_SUPERVISOR: DIM_COMPLETENESS,
    MISSING_URL: DIM_COMPLETENESS,
    INVALID_DATE: DIM_VALIDITY,
    FUTURE_DATE: DIM_VALIDITY,
    INVALID_DEGREE: DIM_VALIDITY,
    INVALID_SPECIALTY_CODE: DIM_VALIDITY,
    MALFORMED_URL: DIM_VALIDITY,
    SHORT_TITLE: DIM_VALIDITY,
    HTML_RESIDUE: DIM_VALIDITY,
    CONTROL_CHARS: DIM_VALIDITY,
    PLACEHOLDER_SUPERVISOR: DIM_VALIDITY,
    INCONSISTENT_DEGREE: DIM_CONSISTENCY,
    INCONSISTENT_APOSTROPHE: DIM_CONSISTENCY,
    INCONSISTENT_WHITESPACE: DIM_CONSISTENCY,
    INCONSISTENT_ORG: DIM_CONSISTENCY,
    INCONSISTENT_CODE_FORMAT: DIM_CONSISTENCY,
    INCONSISTENT_URL: DIM_CONSISTENCY,
}

# Issue codes that the safe fixer (apply) is allowed to auto-correct. Anything
# not on this allowlist is *review only* — the fixer must refuse to touch it.
AUTO_FIX_ALLOWLIST = frozenset({
    INCONSISTENT_DEGREE,
    INCONSISTENT_WHITESPACE,
    INCONSISTENT_APOSTROPHE,
    INCONSISTENT_URL,
})

# Map an allowlisted consistency issue -> (column, normalizer name). The fixer
# uses this so it never applies a normalizer to a high-risk free-text field.
FIX_TARGETS = {
    INCONSISTENT_DEGREE: ("daraja", "normalize_degree"),
    INCONSISTENT_WHITESPACE: ("__field__", "normalize_whitespace"),
    INCONSISTENT_APOSTROPHE: ("__field__", "normalize_apostrophes"),
    INCONSISTENT_URL: ("link", "normalize_url"),
}


# ── Dataclasses ────────────────────────────────────────────────────────────
@dataclass
class Issue:
    """One data-quality finding against a single field of a single record."""
    record_type: str
    record_id: int
    field_name: str
    issue_code: str
    severity: str
    description: str
    original_value: Optional[str] = None
    normalized_value: Optional[str] = None

    @property
    def dimension(self) -> str:
        return ISSUE_DIMENSION.get(self.issue_code, DIM_VALIDITY)

    @property
    def is_auto_fixable(self) -> bool:
        return self.issue_code in AUTO_FIX_ALLOWLIST


@dataclass
class DuplicateCandidate:
    record_id_1: int
    record_id_2: int
    score: float
    match_level: str           # 'exact' | 'strong' | 'possible'
    reasons: list = field(default_factory=list)

    def ordered(self) -> "DuplicateCandidate":
        """Return a copy with (id1, id2) sorted so pairs are stored canonically."""
        a, b = self.record_id_1, self.record_id_2
        if a <= b:
            return self
        return DuplicateCandidate(b, a, self.score, self.match_level, self.reasons)


@dataclass
class ScanSummary:
    total_records: int = 0
    checked_records: int = 0
    issue_count: int = 0
    duplicate_count: int = 0
    issues_by_code: dict = field(default_factory=dict)
    issues_by_severity: dict = field(default_factory=dict)
    quality_score: Optional[float] = None
    sample_issues: list = field(default_factory=list)   # capped list[Issue]

    def add_issue(self, issue: Issue, sample_cap: int = 50) -> None:
        self.issue_count += 1
        self.issues_by_code[issue.issue_code] = (
            self.issues_by_code.get(issue.issue_code, 0) + 1)
        self.issues_by_severity[issue.severity] = (
            self.issues_by_severity.get(issue.severity, 0) + 1)
        if len(self.sample_issues) < sample_cap:
            self.sample_issues.append(issue)


@dataclass
class RunResult:
    run_id: Optional[int]
    run_type: str
    status: str                # 'running' | 'completed' | 'failed'
    summary: ScanSummary
    metadata: dict = field(default_factory=dict)
    error_message: Optional[str] = None
