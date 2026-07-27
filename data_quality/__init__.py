"""Data Quality Phase 1 — normalization, validation, duplicate detection,
scanning and safe cleanup for the Olimlar.uz scientific-record dataset.

This package is deliberately layered so that the *pure logic* (normalizers,
validators, duplicate scoring, quality scoring) has **no** database or Flask
dependency and can be unit-tested locally without a Postgres connection.

Layers
------
    config              -- env-driven configuration with safe defaults
    models              -- plain dataclasses (Issue, DuplicateCandidate, ...)
    normalizers         -- deterministic, idempotent string normalizers
    validators          -- field validators -> Issue list
    scoring             -- 0-100 quality score (shared by CLI + dashboard)
    duplicate_detector  -- blocking + 3-tier duplicate scoring (difflib decides;
                           rapidfuzz is an optional pre-filter only)
    repository          -- parameterized DB access (the ONLY SQL lives here)
    quality_service     -- scan orchestration (DataQualityService / reports)
    duplicate_service   -- duplicate candidate generation + persistence
    import_precheck      -- flag-gated pre-check for the OAK import flow

Nothing in this package auto-creates Postgres extensions, writes to the
production database on import, or performs a destructive operation by default.
"""

from data_quality.config import get_config  # noqa: F401

__all__ = ["get_config"]

__version__ = "1.0.0-phase1"
