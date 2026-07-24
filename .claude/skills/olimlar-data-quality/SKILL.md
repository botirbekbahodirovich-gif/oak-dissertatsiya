---
name: olimlar-data-quality
description: Rules for researcher/institution normalization, duplicate detection, canonical IDs, aliases, region/specialty handling, and statistics consistency.
---

# olimlar-data-quality

## When to use

Task touches: researcher or institution name normalization/matching,
duplicate/merge detection, `institution_map`, region or ixtisoslik
(specialty) assignment, clustering logic, or anything computing
site-wide statistics/counts.

## Read first

- `AGENTS.md` → "Key data entities" and "Data quality rules" sections.
- `scripts/flag_data_quality.py` — current flagging logic and its
  `--apply` semantics (report-only even with `--apply`; see comment at
  the top of the file).
- `scripts/apply_institution_merges.py` — the sanctioned merge path
  (dry-run default, `--apply` to write, admin-history-tracked).
- `utils/transliterate.py` — before writing any Latin/Cyrillic
  comparison; `h≠ҳ` but `x=х`, already handled here.

## Invariants

- Preserve the original scraped/source value. Normalize into a
  separate/derived field or at read time — don't overwrite raw source
  text in place.
- No automatic merging of researchers or institutions, ever, regardless
  of confidence score. A confidence score creates a *review candidate*,
  not a merge.
- The only sanctioned institution-merge path is
  `scripts/apply_institution_merges.py` against a reviewed CSV plan
  (`scripts/institution_merge_plan.csv` /
  `institution_merge_uncertain.csv` are examples of the input shape).
  **Never `migrate_institutions.py --apply`** — it drops the merge
  table.
- Merges must be reversible and audit-tracked (this is why the
  apply-merges path writes to an admin-history table instead of doing a
  destructive rename/delete).
- Resolve institution identity through `institution_map`
  (`canonical_name`/`cyrillic_name`/`latin_name`), not ad-hoc string
  `==`/`ILIKE` matching, so Latin/Cyrillic + apostrophe + case variants
  resolve to the same entity.
- Don't default an unmatched region to "Toshkent" (or any other
  specific value) — leave it null/unset and let it surface as a gap to
  review, since a silent default corrupts regional statistics.
- Any statistic shown to users (counts, rankings, H-index, etc.) should
  read from one source-of-truth query/view, not be recomputed
  differently in two places — check `ranking.py`/`reyting.py` and
  `olimlar_catalog.py` for the existing aggregation pattern before
  adding a new one.

## Steps

1. Confirm whether the change is a *detection* change (flagging logic)
   or a *merge* change (writing to the DB) — they have very different
   risk profiles.
2. For detection: run the flagging script without `--apply` first and
   inspect the report.
3. For merges: always dry-run `apply_institution_merges.py` against the
   plan CSV before suggesting `--apply`, and never run `--apply`
   yourself unless explicitly instructed — this writes to the real DB.
4. Cross-check any confidence threshold change against a sample of
   real rows, not just unit logic.

## Verification

- Dry-run: `python3.12 scripts/flag_data_quality.py` (no `--apply`) —
  needs DB, writes nothing.
- Dry-run: `python3.12 scripts/apply_institution_merges.py
  scripts/institution_merge_plan.csv` (no `--apply`) — needs DB, writes
  nothing.
- `python3.12 -m py_compile <changed files>` for a fast syntax check
  with no DB needed.

## Do not

- Don't call any script with `--apply` from an agent task unless the
  user explicitly asked for that specific write.
- Don't add a new auto-merge code path "for convenience."
- Don't hardcode a region default.
- Don't treat a local DB connection failure as "no data-quality issues
  found" — local Neon creds are stale (see `AGENTS.md`), so a script
  that can't connect isn't validating anything.
