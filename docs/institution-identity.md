# Canonical institution IDs

The OAK value stays untouched in `dissertations.muassasa`; the ranking owner is
`dissertations.institution_id`, and co-owners of a joint defence live in
`dissertation_institutions` (migration 021).

## Safe rollout

1. Take a database backup (`pg_dump`).
2. Apply migration 021 (`python3 scripts/migrate.py apply --yes`).
   Rollback: `psql -f migrations/rollback/021_institution_links.down.sql`.
3. Dry run - read-only session, SELECTs only, writes nothing but the CSV:

   `python3 scripts/backfill_institution_ids.py --csv /tmp/canonical_dryrun.csv`

   Columns: eski_guruh, variantlar, dissertatsiyalar, taklif_id (`yangi:N` for
   a new ID, `A+B` for a joint defence), parent_id, kategoriya, qoida
   (single/child/multiple/mapda_yoq), muammo.
4. Apply only after review, naming the database explicitly:

   `python3 scripts/backfill_institution_ids.py --apply --expect-db <db>`

5. Compare the legacy text chain with IDs (read-only):

   `python3 scripts/compare_institution_modes.py --out /tmp/canonical_compare`

6. Open `/admin/institutions#identity-review` and resolve pending names
   (`source='category_tie'` rows are groups whose category vote was a tie).

`/reyting/ball` switches to IDs automatically only when at least
`_IDENTITY_MIN_COVERAGE` (95%) of defences are linked; below that it keeps the
legacy `institution_map` chain.

## Rules (institution_backfill.py)

- Every active `institution_map` variant becomes an approved alias of its group
  (`source='legacy_merge'`), so the manual merges carry over.
- Category and region are majority votes over the group's variants; a category
  tie becomes `boshqa` and is queued.
- "X huzuridagi Y" and "... Fanlar akademiyasi ... instituti" are ONE
  organisation; `parent_id` points at X only if X already has an ID (otherwise
  the CSV says `ota_topilmadi`; parents are never invented).
- "A hamda B" or two full names joined by "va"/"," are TWO organisations: the
  primary is `institution_id`, the others are `dissertation_institutions`
  rows, and the ranking gives each owner the full score.
- OAK names absent from `institution_map` are linked only on an exact key
  match; the rest are queued.
- Do not run `migrate_institutions.py --apply` after manual review; it drops and
  rebuilds the legacy map.

## Known gaps

- New scraper/import rows get only the primary ID (approved alias); joint
  defences imported later need their `dissertation_institutions` row from the
  admin queue.
- Undoing the backfill itself is restore-from-backup, or:

  ```sql
  BEGIN;
  DELETE FROM dissertation_institutions WHERE source = 'legacy_merge';
  UPDATE dissertations d SET institution_id = NULL FROM institution_aliases a
   WHERE a.source = 'legacy_merge' AND d.institution_id = a.institution_id
     AND TRIM(d.muassasa) = a.raw_name;
  DELETE FROM institution_aliases WHERE source = 'legacy_merge';
  COMMIT;
  ```
