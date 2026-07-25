# Data Quality — Performance

Targets: work comfortably on today's ~28k records and scale toward 100k+.

## Memory
- Row reads use a **server-side (named) cursor** with `itersize =
  DATA_QUALITY_BATCH_SIZE` (`repository.iter_dissertations`), so a full scan
  streams instead of loading the table.
- Issue inserts are **buffered and flushed** every batch via
  `psycopg2.extras.execute_values` (`repository.insert_issues`).
- CSV export streams through a reused `io.StringIO` and a server-side cursor,
  capped by `DATA_QUALITY_MAX_EXPORT_ROWS`.
- Duplicate detection holds the scanned records in memory to build blocks. Each
  signature is ~9 short strings; 28k ≈ a few MB, 100k ≈ tens of MB — acceptable.
  If this ever grows, run duplicate detection per blocking partition (e.g. per
  year) via `--limit` windows.

## Avoiding O(n²) duplicate comparison
Never compare all pairs. Records are grouped into **blocks** and only records
sharing a block are compared (`duplicate_detector._build_blocks`):

| Block key | Rationale |
|---|---|
| source `link` | same URL ⇒ exact re-import |
| `(year, specialty)` | same defense year + field |
| first author token | same surname |
| title first-8-chars prefix | near-identical titles cluster |

Oversized blocks (`> _MAX_BLOCK_SIZE = 400`, e.g. thousands of empty-year rows)
are **skipped** so one weak key can't dominate the run. Within a block the work
is `k²` for block size `k`, which stays small.

## Similarity cost
`difflib.SequenceMatcher.ratio()` is `O(len·len)` on short strings (titles), plus
a cheap token-Jaccard. Both are stdlib — no external dependency, no C build step.

## Indexes
Migration 004 adds:
- `data_quality_runs (started_at DESC)`, `(status)`
- `data_quality_issues (run_id)`, `(issue_code)`, `(severity)`, `(status)`, `(record_type, record_id)`
- `duplicate_candidates (status)`, `(match_level)`, `(record_type, record_id_1)`, `(record_type, record_id_2)`, unique `(record_type, record_id_1, record_id_2)`

These back the dashboard filters, pagination, breakdown aggregation, and export
without sequential scans. The pre-existing `idx_dissertations_quality` is reused.

## Optional pg_trgm
If `pg_trgm` is **already installed**, `DuplicateDetectionService.uses_pg_trgm()`
reports it and a trigram-accelerated candidate query can be added later. The
extension is **never auto-created** (spec requirement); the Python blocking path
is the default and needs no server privileges.

## Rough budget (100k target)
Blocking is near-linear in row count; comparisons are bounded by block size. A
full persist scan is expected to be I/O-bound on issue inserts, mitigated by
`execute_values` batching. For routine dashboards, prefer bounded scans
(`--limit`) and schedule full persist scans off-peak.
