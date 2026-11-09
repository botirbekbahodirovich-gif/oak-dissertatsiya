# Canonical institution IDs

This migration keeps the original OAK value in `dissertations.muassasa` and adds
one nullable ranking owner in `dissertations.institution_id`.

## Safe rollout

1. Take a database backup.
2. Preview only (the whole transaction is rolled back):

   `python3 scripts/backfill_institution_ids.py`

3. Review the printed counts.
4. Apply only after approval:

   `python3 scripts/backfill_institution_ids.py --apply`

5. Open `/admin/institutions#identity-review` and resolve pending names.

The backfill auto-links only conservative exact equivalents. Fuzzy, historical
and clear multi-organisation values remain pending and do not affect ranking.

## Rules

- One dissertation has at most one ranking `institution_id`.
- An approved alias is reused by every later scraper/import.
- Unknown and multi-organisation values are queued.
- Raw OAK text is never rewritten.
- Canonical ID merges are audited and can be undone in the admin page.
- Do not run `migrate_institutions.py --apply` after manual review; it drops and
  rebuilds the legacy map.
