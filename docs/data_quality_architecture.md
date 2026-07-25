# Data Quality — Architecture

## Layering

```
                 ┌─────────────────────────────────────────────┐
  CLI / routes   │ scripts/run_data_quality_scan.py             │
                 │ scripts/apply_data_quality_fixes.py          │
                 │ blueprints/data_quality.py (admin dashboard) │
                 └───────────────┬─────────────────────────────┘
                                 │ (no business logic here)
                 ┌───────────────▼─────────────────────────────┐
  Services       │ DataQualityService / QualityReportService    │
                 │ DuplicateDetectionService                    │
                 └───────────────┬─────────────────────────────┘
                                 │
        ┌───────────────┬────────┴────────┬──────────────────┐
  Pure  │ normalizers   │ validators      │ duplicate_detector│  scoring
  logic │ (idempotent)  │ (Issue list)    │ (blocking+tiers)  │  (0..100)
        └───────────────┴─────────────────┴──────────────────┘
                                 │
                 ┌───────────────▼─────────────────────────────┐
  Data access    │ repository.py  (the ONLY module with SQL)    │
                 └───────────────┬─────────────────────────────┘
                                 │ psycopg2 (data.get_connection)
                            PostgreSQL
```

**Key rule:** the pure-logic layer has no Flask/psycopg2 imports, so it is unit
tested with no database. `repository.py` is the single place SQL lives — every
statement is parameterized and any dynamic identifier is checked against a fixed
allowlist.

## Modules

| Module | Responsibility |
|---|---|
| `data_quality/config.py` | `DataQualityConfig` from env; safe defaults (scan on, auto-fix off, import pre-check off). |
| `data_quality/models.py` | dataclasses (`Issue`, `DuplicateCandidate`, `ScanSummary`, `RunResult`) + issue-code/severity/dimension constants, `AUTO_FIX_ALLOWLIST`, `AUTO_FIX_SAFE_COLUMNS`, `FIX_TARGETS`. |
| `data_quality/normalizers.py` | deterministic, idempotent field normalizers. |
| `data_quality/validators.py` | row → `Issue` list (completeness / validity / consistency). |
| `data_quality/duplicate_detector.py` | blocking + 3-tier scoring (stdlib `difflib`). |
| `data_quality/scoring.py` | single quality-score function (CLI + dashboard). |
| `data_quality/repository.py` | all DB reads/writes. |
| `data_quality/quality_service.py` | scan orchestration + dashboard aggregation. |
| `data_quality/duplicate_service.py` | duplicate detection + persistence, optional pg_trgm awareness. |
| `data_quality/import_precheck.py` | flag-gated import summary (pure). |
| `data_quality/cli_common.py` | CLI connection + safe output helpers. |

## Database objects (migration 004)

- **data_quality_runs** — one row per scanner execution (status running/completed/failed, counts, JSONB metadata).
- **data_quality_issues** — field-level findings; `run_id`→runs ON DELETE CASCADE; polymorphic (`record_type`,`record_id`); status open/resolved/ignored/fixed.
- **duplicate_candidates** — candidate pairs; `UNIQUE(record_type, record_id_1, record_id_2)`; status pending/confirmed/rejected/merged (merge = Phase 2).

Polymorphic `record_id` has **no hard FK** to `dissertations` (the column also
targets future record types); referential integrity is checked in code.

## Request/CLI flows

- **Scan (CLI):** count → (persist: create run) → stream rows (server-side cursor)
  → validate → buffer/flush issues in batches → duplicate detection over the
  in-memory set → persist candidates (ON CONFLICT keeps re-scans idempotent) →
  compute score → complete run. Any error → run marked `failed`.
- **Dashboard:** `QualityReportService.dashboard_summary` rebuilds a `ScanSummary`
  from persisted aggregates and calls the same `scoring.compute_score`, so the UI
  score always matches the CLI.
- **Fix (CLI):** load open allowlisted issues → recompute normalized value from
  the *current* DB value → update only `daraja`/`link` → per-fix transaction +
  affected-row check + audit → mark issue `fixed`.

## Security posture
Admin dashboard reuses `security.admin_required` + Flask-Login `@login_required`
(no new auth); app-wide `CSRFProtect` covers all POSTs; CSV export escapes
formula-injection; no Telegram/user-private data is read or shown; destructive
behaviour (persist, apply) is off by default and explicit.
