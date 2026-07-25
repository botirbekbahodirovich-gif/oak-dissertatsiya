# Data Quality — Operations Runbook

> **Golden rules**
> - Default mode is always non-destructive (report / preview).
> - Persist and apply are opt-in and gated by config.
> - Never run persist/apply against the production DB from a laptop. Point
>   `DATABASE_URL` at the target explicitly; the CLI prints `host/db` on start.
> - Duplicates are never auto-deleted or auto-merged in Phase 1.

## Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `DATA_QUALITY_SCAN_ENABLED` | `1` | allow persist scans |
| `DATA_QUALITY_AUTO_FIX_ENABLED` | `0` | allow `apply_data_quality_fixes.py --apply` |
| `DATA_QUALITY_IMPORT_PRECHECK` | `0` | add DQ summary to `import_oak` response |
| `DATA_QUALITY_BATCH_SIZE` | `1000` | scan batch / cursor itersize |
| `DATA_QUALITY_MAX_EXPORT_ROWS` | `50000` | CSV export hard cap |
| `DATA_QUALITY_EXACT/STRONG/POSSIBLE_THRESHOLD` | `0.98/0.88/0.72` | duplicate similarity thresholds |
| `TEST_DATABASE_URL` / `DATA_QUALITY_DATABASE_URL` | — | preferred CLI target (overrides `DATABASE_URL`) |

Missing variables fall back to the safe defaults above.

## 1. Apply migration 004 (staging)
```bash
export DATABASE_URL="postgresql://…/staging_db"
psql "$DATABASE_URL" -f migrations/004_create_data_quality_tables.sql
```
Idempotent — safe to re-run. Verifies three additive tables; touches no existing data.

## 2. Scan
```bash
# read-only aggregate report (writes nothing)
python scripts/run_data_quality_scan.py --mode report

# sample / single record
python scripts/run_data_quality_scan.py --limit 1000
python scripts/run_data_quality_scan.py --record-id 123

# see proposed normalizations only
python scripts/run_data_quality_scan.py --mode normalize-preview

# record a run + issues + duplicate candidates (needs migration 004)
DATA_QUALITY_SCAN_ENABLED=1 python scripts/run_data_quality_scan.py --mode persist
```

## 3. Preview & apply fixes
```bash
# always preview first (writes nothing)
python scripts/apply_data_quality_fixes.py --preview --issue-code NORMALIZE_DEGREE

# apply — allowlisted codes on safe columns only (daraja/link)
DATA_QUALITY_AUTO_FIX_ENABLED=1 \
  python scripts/apply_data_quality_fixes.py --apply --issue-code NORMALIZE_DEGREE
DATA_QUALITY_AUTO_FIX_ENABLED=1 \
  python scripts/apply_data_quality_fixes.py --apply --run-id 12
```
Codes: `NORMALIZE_DEGREE`, `NORMALIZE_WHITESPACE`, `NORMALIZE_APOSTROPHE`,
`NORMALIZE_URL`. Title/author/org/supervisor are **never** auto-fixed. Each fix
runs in its own transaction; the before/after is audited onto the issue row and
the issue is marked `fixed`. (`dissertations` has no `updated_at`, so none is bumped.)

## 4. Admin dashboard
`/admin/data-quality` (admin-only). Summary cards, issue breakdown, side-by-side
duplicate candidates (confirm/reject; **merge disabled → Phase 2**), server-side
filters + pagination, "Skaner ishga tushirish" (bounded ≤ 3000 rows — full scans
use the CLI), issue resolve/ignore, and `export.csv`.

## 5. CSV export
`/admin/data-quality/export.csv` (admin-only, streaming, formula-injection-safe,
capped by `DATA_QUALITY_MAX_EXPORT_ROWS`). Filters carry over from the dashboard query string.

## 6. Import pre-check (optional)
Set `DATA_QUALITY_IMPORT_PRECHECK=1` in the server `.env`. The `import_oak`
response then gains a `precheck` object `{total, valid, warning, invalid,
duplicate, duplicate_pairs, …}`. **Partial-import policy:** the pre-check is
report-only — it never blocks or skips rows; the existing import proceeds exactly
as before, and admins review the summary + duplicate reasons. Turning the flag
off restores byte-for-byte legacy behaviour.

## 7. Duplicate review
Confirm/reject in the dashboard, or query `duplicate_candidates` directly. A
`pending` pair whose score/level/reasons change on re-scan is refreshed; a
reviewed pair is left untouched.

## 8. Rollback
```sql
BEGIN;
  DROP TABLE IF EXISTS duplicate_candidates;
  DROP TABLE IF EXISTS data_quality_issues;
  DROP TABLE IF EXISTS data_quality_runs;
COMMIT;
```
These tables hold only derived data — dropping them loses no source records.
Applied field fixes (degree/url normalisation) are not reverted by the drop;
restore from a DB backup if a fix must be undone.

## 9. Incident handling
- A persist scan that dies mid-run leaves the `data_quality_runs` row in
  `failed` with `error_message`; issues already flushed stay, and a re-run is safe
  (ON CONFLICT for duplicates; issues accumulate under a new run_id).
- Structured logs: grep `data_quality.scan` / `data_quality.duplicates`, or the
  app logger `500 ERROR` per the error-logging memo.

## 10. Production deployment
1. Merge `feature/data-quality-phase-1` after review.
2. `pip install -r requirements.txt` (no new deps added — stdlib only).
3. Apply migration 004 on the production DB during a maintenance window.
4. Leave `DATA_QUALITY_AUTO_FIX_ENABLED` **unset** (off). Optionally set
   `DATA_QUALITY_IMPORT_PRECHECK=1` after validating on staging.
5. Restart Gunicorn/systemd. The new blueprint and routes load on boot.
