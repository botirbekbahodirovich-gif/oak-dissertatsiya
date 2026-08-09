# Articles v1 — Staging Release Runbook

Operator steps for deploying `integrate/articles-v1-staging` to
`staging.olimlar.uz`. This is a staging-only runbook; production deploy is a
separate, later decision.

Scope: article catalog, OAK scientific-field taxonomy, private PDF storage,
PDF.js viewer, DOI autofill (Crossref/OpenAlex/Unpaywall), DOI/CSV bulk
import, import review queue, manual metadata sync/diff, sync dashboard,
scheduled metadata sync, OpenAlex discovery, discovery→import bridge.

## 1. Environment

Set on the staging host's `.env` (never commit, never print values):

- `APP_ENV=production` (staging still runs the "production" config path;
  `check_staging_environment.py` enforces a staging-marked `DATABASE_URL`)
- `DATABASE_URL` — staging Postgres DSN, host/db name must contain an
  explicit `staging`/`stage`/`stg` marker (enforced by the check script)
- `SESSION_SECRET` — 32+ chars
- `OPENALEX_API_KEY` — optional (discovery/autofill work without it via
  `OPENALEX_MAILTO`/`SCHOLARLY_API_EMAIL`, but rate limits are lower)
- `SCHOLARLY_API_EMAIL`, `CROSSREF_MAILTO` (optional override),
  `UNPAYWALL_EMAIL` (optional override)
- `ARTICLE_STORAGE_DIR` — optional; defaults to `<app_root>/storage/articles`
- `ARTICLE_IMPORT_MAX_ITEMS`, `ARTICLE_IMPORT_CHUNK_SIZE`,
  `ARTICLE_IMPORT_MAX_ATTEMPTS` — optional, sane defaults ship in code
- `ARTICLE_METADATA_SYNC_ENABLED`, `ARTICLE_METADATA_SYNC_INTERVAL_DAYS`,
  `ARTICLE_METADATA_SYNC_BATCH_SIZE` — optional, keep the scheduler disabled
  (`ARTICLE_METADATA_SYNC_ENABLED=false`) until the manual dry-run in step 8
  has been reviewed

Verify presence only:

```bash
python3.12 scripts/check_staging_environment.py --config-only
```

## 2. Database backup

Take a Postgres backup/snapshot of the staging database before touching
anything (`pg_dump` or the hosting provider's snapshot feature). Confirm the
backup completed before proceeding.

## 3. Code deploy

Deploy via the existing staging script (dry-run by default, `--execute` to
apply). It never runs migrations itself — see step 4.

```bash
deploy/deploy-staging.sh.example --sha <reviewed-40-char-sha>
deploy/deploy-staging.sh.example --sha <reviewed-40-char-sha> --execute --restart
```

## 4. Migrations

The project has no standalone migration-apply script. Schema for the
`migrations/007`–`015` range is applied lazily by each module's idempotent
`_ensure_schema()` / `ensure_schema()` function (identical DDL to the
matching `migrations/*.sql` file, `CREATE TABLE/INDEX IF NOT EXISTS` and
`ADD COLUMN IF NOT EXISTS`), invoked on first DB access after the app
restarts. No manual `psql -f` step against `migrations/007+` is required or
expected; do not hand-run those files.

Order: current DB state → app restart (step 3) triggers schema ensure calls
on first request → verify with step 5.

## 5. Post-migration verification

```bash
python3.12 scripts/check_staging_environment.py
```

This runs a read-only session (`default_transaction_read_only=on`),
confirms DB identity matches the configured DSN, and confirms
`users`/`dissertations`/`import_audit_log` exist. For the article tables
specifically, confirm via the admin UI (step 6) rather than manual SQL.

## 6. Web / app smoke

- Admin login works
- `/maqolalar` (catalog) renders
- Article detail page renders
- `/admin/articles` list and create-article form render

## 7. OpenAlex / Crossref / Unpaywall smoke

Only if `OPENALEX_API_KEY` (or `SCHOLARLY_API_EMAIL`) is configured. Never
print the key to stdout/logs.

```bash
python3.12 scripts/smoke_article_discovery.py
```

Use a small, bounded search. This script does real network calls — do not
add it to the default `unittest discover` suite.

## 8. Scheduler dry-run, then manual run

```bash
python3.12 scripts/sync_article_metadata.py --dry-run
```

Review the due-item list. Then run a small manual (non-dry-run) batch and
check `article_metadata_sync_runs` / the sync dashboard for the result
before enabling cron.

## 9. Cron enable

Only after steps 3–8 are all green:

```bash
sudo bash deploy/install_cron.sh
```

This installs `/etc/cron.d/article-metadata-sync` (daily 02:15 UTC) alongside
the existing oak-scraper/grant-scraper/photo-urls crons, and creates its log
file. Do not enable this before the manual scheduler run in step 8.

## Order summary

```
env → db backup → code deploy → app restart (schema ensure) →
post-restart verification → web smoke → OpenAlex/Crossref/Unpaywall smoke →
scheduler dry-run → manual scheduler run → cron enable
```

## 10. Rollback

- App: `deploy/deploy-staging.sh.example --sha <previous-reviewed-sha> --execute --restart`
  (checkout is git-based; no destructive migration ever ran, so no schema
  rollback is needed — new columns/tables added by `_ensure_schema()` are
  additive and safe to leave in place even if the app is rolled back)
- Cron: remove `/etc/cron.d/article-metadata-sync` if the scheduler needs to
  be paused
- Database: restore from the step-2 backup only if data corruption is
  suspected; this has not been necessary in any of the additive migrations
  007–015
