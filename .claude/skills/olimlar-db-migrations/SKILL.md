---
name: olimlar-db-migrations
description: Rules for writing new tables/columns/indexes/FKs as additive SQL migration files in migrations/, without a migration runner.
---

# olimlar-db-migrations

## When to use

Task needs a new table, column, index, foreign key, or other schema
change.

## Read first

- `AGENTS.md` → "Database & migrations" section.
- An existing recent migration for the naming/style convention, e.g.
  `migrations/add_payments.sql` or `migrations/add_conferences.sql`.
- `scripts/create_tables.py` for the base `users`/`dissertations` schema
  the new change might relate to.
- The table(s) being touched, to check existing constraints/indexes
  before adding new ones (grep the relevant `migrations/add_*.sql` for
  that table, don't assume).

## Steps

1. Confirm there is no migration framework/runner in this repo —
   `migrations/*.sql` files are applied by hand against the server DB.
   Never wire one into `app.py` startup.
2. Name the file `add_<feature>.sql` following the existing pattern.
3. Write it additive-first: new nullable column, new table, new index —
   avoid `DROP`/`ALTER ... TYPE`/non-nullable-without-default changes to
   existing columns unless the task explicitly requires it.
4. Wrap in an explicit transaction:
   ```sql
   BEGIN;
   -- change here
   COMMIT;
   ```
5. Add a rollback comment describing the exact reverse operation
   (`-- rollback: DROP TABLE IF EXISTS ...` /
   `-- rollback: ALTER TABLE ... DROP COLUMN ...`).
6. Check for an existing unique constraint, FK, or index on the target
   table before adding a new one that might conflict or duplicate.
7. If the migration is a work-in-progress that must not be applied yet,
   mark the top of the file clearly: `-- DRAFT — DO NOT APPLY`. Never
   reference a DRAFT migration elsewhere as already-applied schema.
8. Never touch production or staging DB from this task — write the
   `.sql` file only, don't execute it against a live connection unless
   explicitly instructed.

## Verification

- Read the SQL back for balanced `BEGIN;`/`COMMIT;` and correct table/
  column names against the actual schema (grep other migrations, don't
  guess column names).
- `git diff --check` on the new file for whitespace/line-ending issues.
- If asked to validate against a real DB, that requires the server —
  local `.env` Neon creds are stale (see `AGENTS.md`); say so rather
  than claiming it ran clean.

## Do not

- Don't add a migration runner or auto-migrate-on-startup mechanism.
- Don't write a destructive migration (`DROP COLUMN`, `DROP TABLE`,
  narrowing `ALTER ... TYPE`) without an explicit rollback note and an
  explicit ask from the user.
- Don't apply the migration yourself against any database.
- Don't use the production DB as a place to "just test" a migration.
