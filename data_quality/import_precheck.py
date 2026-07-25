"""Flag-gated Data Quality pre-check for the OAK import flow.

Pure logic (no DB): given the batch of records about to be imported, it returns
a summary {total, valid, invalid, duplicate, warning, ...} plus small,
PII-free samples. It NEVER mutates the import, never skips a row, and never
raises into the caller (the import wraps it in try/except regardless).

It is only invoked when DATA_QUALITY_IMPORT_PRECHECK is enabled; otherwise the
import behaves byte-for-byte as before. See docs/data_quality_operations.md for
the partial-import policy.
"""

from __future__ import annotations

from typing import Dict, List, Mapping

from data_quality import models as M
from data_quality.config import DataQualityConfig, get_config
from data_quality.duplicate_detector import detect_duplicates
from data_quality.validators import validate_record

_SAMPLE_CAP = 20


def summarize_precheck(records: List[Mapping],
                       config: DataQualityConfig | None = None) -> Dict:
    """Classify each incoming record and find intra-batch duplicates.

    A record is 'invalid' if it has a CRITICAL issue (e.g. missing author/title),
    'warning' if it only has non-critical issues, else 'valid'. Duplicate rows
    are reported (with reasons) but never silently dropped — that decision stays
    with the admin.
    """
    config = config or get_config()
    total = len(records)
    valid = warning = invalid = 0
    invalid_samples: List[Dict] = []
    warning_samples: List[Dict] = []

    # Ensure every record has a stable id for duplicate reporting even if the
    # source omits one (use its batch index).
    indexed = []
    for i, rec in enumerate(records):
        r = dict(rec)
        if r.get("id") in (None, ""):
            r["id"] = f"row-{i}"
        indexed.append(r)

    for r in indexed:
        issues = validate_record(r)
        has_critical = any(i.severity == M.SEVERITY_CRITICAL for i in issues)
        has_warning = any(i.severity == M.SEVERITY_WARNING for i in issues)
        if has_critical:
            invalid += 1
            if len(invalid_samples) < _SAMPLE_CAP:
                invalid_samples.append(_sample(r, issues))
        elif has_warning:
            warning += 1
            if len(warning_samples) < _SAMPLE_CAP:
                warning_samples.append(_sample(r, issues))
        else:
            valid += 1

    # Intra-batch duplicates. Records without an integer id can't be compared by
    # the detector (it needs orderable ids), so only compare rows that have one.
    comparable = [r for r in indexed if isinstance(r.get("id"), int)]
    candidates = detect_duplicates(comparable, config) if len(comparable) > 1 else []
    dup_ids = set()
    for c in candidates:
        dup_ids.add(c.record_id_1)
        dup_ids.add(c.record_id_2)

    return {
        "total": total,
        "valid": valid,
        "warning": warning,
        "invalid": invalid,
        "duplicate": len(dup_ids),
        "duplicate_pairs": [
            {"a": c.record_id_1, "b": c.record_id_2,
             "level": c.match_level, "score": c.score, "reasons": c.reasons}
            for c in candidates[:_SAMPLE_CAP]
        ],
        "invalid_samples": invalid_samples,
        "warning_samples": warning_samples,
    }


def _sample(record: Mapping, issues) -> Dict:
    """A compact, PII-free issue sample for the import response."""
    return {
        "id": record.get("id"),
        "codes": sorted({i.issue_code for i in issues}),
    }
