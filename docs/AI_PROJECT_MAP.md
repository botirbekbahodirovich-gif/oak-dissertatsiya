# Olimlar.uz — AI project map

Reference index so an agent doesn't have to re-map the repository each
session. For rules (security, data quality, git, testing), see
`AGENTS.md` — this file is facts/locations only. If something here looks
stale, verify against the actual file before relying on it (see
"Efficiency" in `AGENTS.md`).

## Entrypoints

| Context | Command |
|---|---|
| Local dev | `python run_server.py` → binds `0.0.0.0:8000`, `FLASK_ENV=development`, imports `app:app` |
| Procfile (Heroku/Railway-style) | `gunicorn app:app --bind 0.0.0.0:$PORT` |
| Dockerfile | `gunicorn app:app ...` (see `Dockerfile`) |
| systemd (GCE VPS) | `deploy/olimlar.service` → `gunicorn app:app --bind <BIND> --workers 2 --threads 2 --timeout 120`, env from `.env` |
| WSGI target everywhere | `app:app` in `app.py` |

## Blueprint → route area

See `AGENTS.md` → "Blueprint → feature map" table for the full list of
blueprints in `blueprints/` plus the root-level modules (`auth.py`,
`data.py`, `cabinet.py`, `upload.py`, `analytics.py`) registered as
blueprints in `app.py`. `app.py` itself still holds a shrinking set of
directly-defined legacy routes alongside its `register_blueprint()`
calls — grep for `@app.route` or `register_blueprint` in `app.py` to
find the current set rather than trusting a fixed count or line range
here.

## Key tables (see AGENTS.md → "Key data entities" for details)

`users`, `olim_profiles`, `dissertations`, `institution_map`,
`notification_prefs`, `specialty_subscriptions`, `user_bookmarks` (+
`user_conference_bookmarks`), `scholar_follows`, `roadmap_plans`. Feature
tables added since the base schema live one-per-file in
`migrations/add_*.sql`; base `users`/`dissertations` schema is in
`scripts/create_tables.py`.

## External services

| Service | Used by | Env var(s) |
|---|---|---|
| Neon PostgreSQL | `data.get_connection()`, everything | `DATABASE_URL` (or `POSTGRES_URL` fallback) |
| Groq (AI topic analysis) | `analytics.py`, `blueprints/topic_analysis.py` | `GROQ_API_KEY` |
| Gemini | AI features (optional) | `GEMINI_API_KEY` |
| OpenAlex (no key required) | `blueprints/maqolalar.py` | `OPENALEX_MAILTO` |
| Google OAuth | `auth.py` login | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` |
| Telegram Login Widget / bot | `auth.py` login, `blueprints/reminders.py` + `subscriptions.py` dispatch | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` |
| OAK scraper ingest | `data.py` `import_oak` | `OAK_API_KEY` (inbound), `SITE_API_URL`/`SITE_API_KEY` (outbound from scraper) |
| Smart Eslatmalar cron | `blueprints/reminders.py` `POST /api/v1/reminders/process` | `REMINDERS_API_KEY` |
| ATMOS payments | `blueprints/payments.py` | `ATMOS_CONSUMER_KEY`, `ATMOS_CONSUMER_SECRET`, `ATMOS_STORE_ID`, `ATMOS_API_KEY`, `ATMOS_API_BASE`, `ATMOS_TEST_MODE` |

Full list with comments: `.env.example`.

## Scheduled jobs (GitHub Actions)

| Workflow | Purpose |
|---|---|
| `.github/workflows/daily.yml` | Daily OAK dissertation scraper |
| `.github/workflows/reminders.yml` | Smart Eslatmalar dispatch (needs `REMINDERS_API_KEY` secret) |
| `.github/workflows/conferences.yml` | Conference data sync |

## Test / smoke scripts (no pytest suite)

| File | What it does |
|---|---|
| `test_import.py` | Confirms `import app` succeeds |
| `test_endpoints.py` | Flask `test_client()` smoke checks on a few public routes |
| `run_smoke.py` | Hits a small hardcoded list of routes via test client, prints status/body |

## Migrations (`migrations/*.sql`, additive, applied by hand)

`add_acquisition_survey.sql`, `add_bookmark_notes.sql`,
`add_conferences.sql`, `add_dissertation_module.sql`,
`add_grants_tables.sql`, `add_payments.sql`,
`add_supervisor_to_profiles.sql`, `add_topic_analysis.sql`,
`add_university_panel.sql`, `add_university_panel_part2.sql`. No
runner/framework — apply manually against the server DB in filename
order relative to when each was written. (Any file explicitly marked
`DRAFT — DO NOT APPLY` is work in progress, not part of applied schema.)

## Maintenance scripts (`scripts/`)

Mostly dry-run-by-default, `--apply` to write:
`apply_institution_merges.py`, `flag_data_quality.py`,
`merge_universities.py`. Also: `create_tables.py` (base schema),
`daily_scraper.py`/`grant_scraper.py` (scrapers),
`scrape_conferences_intl.py`/`scrape_conferences_local.py`,
`seed_grants.py`, `backup_tables.py`, `fix_profile_links.py`,
`update_photo_urls.py`, `apply_merges.py`. Check each script's own
`--help`/argparse before running — don't assume dry-run-by-default
applies to every one of them without checking.

## High-risk files

`app.py` (monolith bootstrap, CSRF init, most blueprint registrations —
grep, never full-read), `auth.py` (external-identity linking must use
immutable provider IDs only, see `AGENTS.md`), `data.py`
(`get_connection()` is the shared DB entry point almost everything
imports), `upload.py`, `migrations/*.sql`,
`scripts/apply_institution_merges.py` / `merge_universities.py` (real
merge writes), `deploy/deploy.sh` (never run it; per the credentials
invariant in `AGENTS.md` it must never contain a real secret — flag it
if it ever does).

## Shared / high-conflict files for parallel agents

`app.py`, `data.py`, anything in `migrations/`, and shared partials in
`templates/partials/` (`cta_banner.html`, `seo_meta.html`) plus
`templates/_reminders_widget.html`. Coordinate scope before editing
these while another agent's worktree is active.

## Parallel agent worktrees

Other agents may have their own active worktrees alongside this one.
Run `git worktree list` at the start of a session to see the current
set — it changes as tasks start and finish, so don't rely on a
remembered list. Never read from or write to a worktree that isn't
yours.
