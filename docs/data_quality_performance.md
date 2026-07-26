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

The title prefix stays at **8** characters. Uzbek is suffix-rich (`ta'lim
tizimi` / `ta'limi tizimida`), so real duplicate titles routinely diverge inside
characters 9–16; a 16-character primary key silently loses those pairs. 16 is
used as the *first secondary key* instead — see below.

### Bounded blocks instead of skipped blocks
Blocks over `_MAX_BLOCK_SIZE = 500` are **split**, not dropped. `_split_block`
walks a secondary-key ladder — 16-char title prefix → first author token → year
→ specialty — until every work unit fits the cap. A key with no discriminating
power inside that block is skipped and the next one is tried. Anything still
oversized after the whole ladder is compared anyway up to
`_HARD_BLOCK_CAP = 5000` with a `WARNING`, and only beyond that cap is it left
out — again with a `WARNING` and a counter, never silently.

This replaced the old behaviour, where a block over 400 records was skipped
outright. Splitting can therefore only *add* candidates, never remove them.

## Three filters before SequenceMatcher
Within a block, `_scan_block` drops pairs through a ladder of filters that are
each a provable **upper bound** on the score `_classify` would compute — so the
candidate set is identical to the unfiltered one, only reached faster.

| Layer | Cost | What it rejects |
|---|---|---|
| `seen` pair key | one set lookup | pairs already evaluated in another block |
| `_max_possible_title_sim` | a few int ops | `max(2·Lmin/(Lmin+Lmax), Tmin/Tmax) < threshold` |
| rapidfuzz `process.cdist` | one C call per block | Indel ratio below the cutoff *and* token Jaccard below it |
| `_classify` | 1–3 `SequenceMatcher` | nothing — this is the decision |

Why the rapidfuzz layer is safe: `fuzz.ratio` is the normalized **Indel**
similarity, `2·LCS/(La+Lb)`, while `difflib.SequenceMatcher.ratio` is
`2·M/(La+Lb)` where `M` is the Ratcliff/Obershelp match size, and `M <= LCS`
always. So `rapidfuzz.ratio >= difflib.ratio` for every pair, and a pair under
the rapidfuzz cutoff can never clear the difflib threshold. Verified
empirically over 300 000 random pairs (0 violations) and re-checked on real
titles by `tests/test_data_quality_duplicate_perf.py`
(`test_rapidfuzz_dominates_difflib_on_corpus_pairs`).

Because `_title_similarity` is `max(difflib_ratio, token_jaccard)`, a pair the
rapidfuzz layer rejects is still checked against the token Jaccard — otherwise
word-reordered duplicates would be lost.

`_PREFILTER_MARGIN = 0.005` is subtracted from the cutoff so float rounding can
never cost a pair.

### rapidfuzz is optional
`RAPIDFUZZ_AVAILABLE` guards both the import and the `numpy` that `cdist` needs.
Without them the cheap bounds still apply and the output is unchanged — just
slower. `test_identical_without_rapidfuzz` covers that path, so a host that has
not run `pip install -r requirements.txt` yet is degraded, not broken.

## Similarity cost
`difflib.SequenceMatcher.ratio()` is `O(len·len)` on short strings (titles), plus
a cheap token-Jaccard. It remains the decision function; `rapidfuzz` only
pre-filters.

## Progress and blocking visibility
`detect_duplicates_with_stats` returns a `DetectionStats` alongside the
candidates (pairs seen, pairs dropped per layer, pairs classified, work units,
the 10 largest blocks). The detector logs a progress line every 5 seconds:

    duplicates: blocks 340/1,200 | pairs compared: 84,000 | classified: 3,100 |
    candidates: 12 | 15s

and, at the end, a one-line summary plus the 10 largest blocks. The scan CLI
already configures logging at `INFO`, so a `nohup`ed full scan shows progress in
its log file.

## Benchmarking
`scripts/benchmark_duplicate_detection.py` (read-only, `TEST_DATABASE_URL`)
times the read / validate / duplicate phases at several limits and prints a
markdown table. `--variants full,no-rapidfuzz,legacy` attributes the speedup to
each optimisation; `legacy` is the pre-hotfix algorithm and should only be run
at small limits.

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
