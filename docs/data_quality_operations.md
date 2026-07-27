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

## 1. Apply migrations 004 + 005 (staging)
```bash
export DATABASE_URL="postgresql://…/staging_db"
psql "$DATABASE_URL" -f migrations/004_create_data_quality_tables.sql
psql "$DATABASE_URL" -f migrations/005_create_data_quality_fix_log.sql
```
Both idempotent — safe to re-run. 004 adds three additive tables, 005 adds
`data_quality_fix_log` (audit trail + rollback source). Neither touches existing data.

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
# always preview first (writes nothing) — --dry-run is an alias for --preview
python scripts/apply_data_quality_fixes.py --preview --issue-code NORMALIZE_DEGREE

# apply — allowlisted codes on safe columns only (daraja/link)
DATA_QUALITY_AUTO_FIX_ENABLED=1 \
  python scripts/apply_data_quality_fixes.py --apply --issue-code NORMALIZE_DEGREE
DATA_QUALITY_AUTO_FIX_ENABLED=1 \
  python scripts/apply_data_quality_fixes.py --apply --run-id 12
```
Codes: `NORMALIZE_DEGREE`, `NORMALIZE_WHITESPACE`, `NORMALIZE_APOSTROPHE`,
`NORMALIZE_URL`. Title/author/org/supervisor are **never** auto-fixed.
(`dissertations` has no `updated_at`, so none is bumped.)

Each fix runs in **one** transaction containing, in order: the
`data_quality_fix_log` row (before/after snapshot, written *first*), the UPDATE
with its affected-row count verified (`== 1`), then the issue audit + `fixed`
status. Any failure rolls the whole thing back — there is never a fix without
its log row, nor a log row without its fix.

All fixes of one invocation share a `data_quality_runs` row with
`run_type='fix'`, created lazily inside the first fix's transaction (a run with
nothing to do leaves no empty row). The CLI prints that **fix run id** plus the
ready-made rollback command; it is the unit of rollback. `--applied-by` overrides
the actor label stored in the log (default `cli:<user>`).

## 3a. Rescrape broken records from OAK (report → apply)
```bash
# 1) report only — writes nothing, caches the OAK pages under data/rescrape_cache/
python scripts/rescrape_broken_records.py --run-id 12
python scripts/rescrape_broken_records.py --run-id 12 --limit 20   # try it small first

# 2) apply the FILLs (runs off the cache; both flags are required)
python scripts/rescrape_broken_records.py --run-id 12 --apply --confirm
```
`--run-id` here is a **scan** run: the records are the ones that scan flagged
with `MISSING_TITLE`, `MISSING_AUTHOR`, `MISSING_DATE`, `MISSING_SPECIALTY`,
`PLACEHOLDER_SUPERVISOR`, `SHORT_TITLE` (override with `--codes`).

Only `FILL` is ever written — DB field empty, page has a value. `CONFLICT`
(both sides differ) is **never** written automatically; it stays in the report
for a human. Writable columns are `RESCRAPE_FILL_COLUMNS` (mavzu, olim, sana,
ixtisoslik, ilmiy_rahbar, muassasa, daraja); `link` is an input to the match,
never an output.

Guarantees, same shape as §3: the `data_quality_fix_log` row is written before
its UPDATE, **one transaction per record** (a record is never half-filled), and
the field is re-checked as still-empty inside that transaction — if someone
filled it between the report and the apply, it is skipped (`SKIPPED_NOT_EMPTY`
in the report/CSV), never overwritten. All fills share one
`run_type='rescrape'` run, and the CLI prints its rollback command. Reports go
to `docs/rescrape_apply_<date>.{md,csv}` (report mode: `rescrape_report_…`).

## 3b. Roll back applied changes
```bash
# dry-run is the DEFAULT — shows what would be restored, writes nothing
python scripts/rollback_data_quality_fixes.py --run-id 42

# actually restore the previous values
python scripts/rollback_data_quality_fixes.py --run-id 42 --confirm
```
`--run-id` is the run id printed by whichever tool wrote — `run_type='fix'`
(§3) or `run_type='rescrape'` (§3a). One CLI undoes both: the log row carries
table, column and before/after, and restores run under the union write scope.
The header prints `run_type` so you can see which batch you are undoing.
Behaviour:
- Fixes are replayed newest-first (reverse application order), so a field fixed
  more than once ends back at its original value.
- Per row the CURRENT value must still equal the logged `value_after`. If it
  differs, someone changed the field after the fix: the row is **skipped** with
  a warning (counted as `mismatched`) and left to a human — never overwritten.
- A restore, its `rolled_back_at` stamp and the reopening of the originating
  issue (`fixed` → `open`) commit together or not at all. A rescrape FILL is
  restored to the value it had before — usually empty, or the supervisor
  placeholder it replaced.
- Already-reverted rows are ignored, so re-running is safe and idempotent.
- Deliberately **not** gated by `DATA_QUALITY_AUTO_FIX_ENABLED` — you must be
  able to undo fixes after switching auto-fix back off.

Query the trail directly when needed:
```sql
SELECT id, record_id, column_name, value_before, value_after,
       applied_at, applied_by, rolled_back_at
  FROM data_quality_fix_log WHERE run_id = 42 ORDER BY id DESC;
```

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
**Data first**: undo applied field fixes with
`scripts/rollback_data_quality_fixes.py --run-id <fix run> --confirm` (§3b).
Dropping the tables does **not** revert them, and once
`data_quality_fix_log` is gone the before-values are only recoverable from a DB
backup.

**Then the schema** (only after every fix you still care about is reverted):
```sql
BEGIN;
  DROP TABLE IF EXISTS data_quality_fix_log;   -- migration 005
  DROP TABLE IF EXISTS duplicate_candidates;
  DROP TABLE IF EXISTS data_quality_issues;
  DROP TABLE IF EXISTS data_quality_runs;
COMMIT;
```
The 004 tables hold only derived data — dropping them loses no source records.
`data_quality_fix_log.run_id` is `ON DELETE RESTRICT`, so a `data_quality_runs`
row with logged fixes cannot be deleted while the audit trail references it
(delete the fix-log rows explicitly if that is really what you want).

## 9. Incident handling
- A persist scan that dies mid-run leaves the `data_quality_runs` row in
  `failed` with `error_message`; issues already flushed stay, and a re-run is safe
  (ON CONFLICT for duplicates; issues accumulate under a new run_id).
- Structured logs: grep `data_quality.scan` / `data_quality.duplicates`, or the
  app logger `500 ERROR` per the error-logging memo.

## 10. Production deployment
1. Merge `feature/data-quality-phase-1` after review.
2. `pip install -r requirements.txt` (no new deps added — stdlib only).
3. Apply migrations 004 and 005 on the production DB during a maintenance window.
4. Leave `DATA_QUALITY_AUTO_FIX_ENABLED` **unset** (off). Optionally set
   `DATA_QUALITY_IMPORT_PRECHECK=1` after validating on staging.
5. Restart Gunicorn/systemd. The new blueprint and routes load on boot.
