# Data Quality — Data Inventory

Snapshot of the fields the Data Quality subsystem inspects and the problems
observed in them. The source of truth is the Postgres `dissertations` table
(created in `app.py::_run_startup_migrations`).

## Core table: `dissertations`

| Column | Type | Nullable | Source | Used by | Observed problem | Recommendation |
|---|---|---|---|---|---|---|
| id | SERIAL PK | no | auto | everything | — | — |
| oak_id | TEXT (unique) | yes | OAK import | `import_oak` ON CONFLICT key | some legacy rows lack it | keep as dedup key |
| sana | TEXT | yes | OAK/CSV | trends, stats, dedup | **free text**, mixed formats, invalid/future dates | validate → ISO; never coerce ambiguous |
| daraja | TEXT | yes | OAK/CSV | trends, filters | PhD/phd/Ph.D/falsafa doktori/DSc variants | normalize → PhD/DSc |
| olim | TEXT | yes | OAK/CSV | search, genealogy, analytics | empty; short/suspicious names; mixed apostrophes | completeness + apostrophe (review-only) |
| mavzu | TEXT | yes | OAK/CSV | search, display | empty; too short; HTML/control residue; **OAK registry code instead of the title** (`В2026.2.PhD/Iqt4897`) — 107 rows on the 29 551-row staging snapshot | completeness + validity (review-only); TITLE_IS_CODE |
| ixtisoslik | TEXT | yes | OAK/CSV | trends, filters | code format variants (spaces/dots/dashes) | normalize NN.NN.NN |
| muassasa | TEXT | yes | OAK/CSV | analytics grouping, universities bridge | Cyrillic/Latin spelling variants | consistency (review-only; see institution_map) |
| ilmiy_rahbar | TEXT | yes | OAK/CSV | genealogy, advisors | empty; `Ф.И.Ш`/`F.I.Sh` placeholder templates | placeholder + missing detection |
| link | TEXT | yes | OAK/CSV | source, dedup | empty; missing scheme | URL normalize (safe-fixable) |
| data_quality | VARCHAR(20) default 'complete' | no | flag_data_quality.py | `dissertations` filters | pre-existing coarse flag (complete/missing_author/incomplete) | kept; superseded by issue table |
| created_at | TIMESTAMP default NOW() | no | auto | ordering | — | — |
| fan_tarmoqi, ixtisoslik_nomi, mavzu_raqami, ilmiy_kengash(_raqami), opponent_1..3, yetakchi_tashkilot, ilmiy_rahbar_daraja, yonalish, photo_url, ilmiy_rahbar_photo_url | TEXT | yes | OAK/CSV | display | sparsely populated | out of Phase-1 scope |
| merged_into_id | INTEGER, self-FK (migration 006) | yes | `merge_duplicates.py` | listings should exclude rows where this is set | — | not yet excluded from any existing dashboard/scan query — see docs/data_quality_dissertation_fk_inventory.md §4 |
| merged_at | TIMESTAMPTZ (migration 006) | yes | `merge_duplicates.py` | — | — | — |

**No `updated_at`** on `dissertations` — the fixer therefore cannot bump a
modified-timestamp (noted in the operations runbook).

## Related tables

| Table | Role in DQ |
|---|---|
| `import_audit_log` (migration 002) | id, user_id→users, file_name, imported_rows, result(success/failure), created_at. Import provenance; DQ links a scan to imports by time. |
| `users` | referenced by DQ `resolved_by`/`reviewed_by` (ON DELETE SET NULL). No user PII is read or displayed by DQ. |

## New tables (migration 004)
`data_quality_runs`, `data_quality_issues`, `duplicate_candidates` — see
[data_quality_architecture.md](data_quality_architecture.md).

## Ingest / import surface
- `data.py::import_oak` (`/api/oak/import`, `/api/v1/import-oak`) — API-key auth,
  per-item extraction (`_extract_olim/_extract_mavzu/_extract_daraja/_extract_ixtisoslik`),
  `_validate_ingest_record`, ON CONFLICT(oak_id) upsert.
- `scraper.py`, `scripts/daily_scraper.py`, `send_to_site.py` — feed the import.
- `upload.py` — CSV upload/validation path.

## Columns used in search / analytics (must not be silently rewritten)
`olim`, `mavzu`, `muassasa`, `ilmiy_rahbar` drive search (`utils/search_helper.py`,
`utils/transliterate.py`) and analytics grouping (`app.py::_trends_data`,
`analytics.py`). This is why the auto-fixer's safe-column allowlist is limited to
`daraja` and `link` only.
