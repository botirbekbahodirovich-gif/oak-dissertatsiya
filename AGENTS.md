# Olimlar.uz — AI coding agent guide

This is the primary reference for any AI coding agent (Claude Code, Codex,
or other) working in this repository. `CLAUDE.md` is a short pointer to
this file plus Claude-specific fast-start steps — read this one for the
actual rules. Skim this once per session; don't re-audit the repo from
scratch every task (see "Efficiency" at the end).

## What this platform is

Olimlar.uz (repo name `oak-dissertatsiya`, product name "OAK AI") is a
Flask web platform that indexes Uzbek dissertations scraped from oak.uz
and layers researcher/institution intelligence on top: scholar profiles,
academic lineage graphs, advisor discovery, grants, journals, conferences,
smart deadline reminders, a collaborative dissertation workspace
("Konstruktor"), AI document generation for kafedra admin work, and a
university B2B analytics panel. Primary users are Uzbek/CIS doctoral
researchers ("olim"), their advisors, university/kafedra staff, and site
admins. UI language is Uzbek throughout.

Stack: Flask (`app.py` monolith + a `blueprints/` package of feature
blueprints), PostgreSQL via `psycopg2`
(primary store; `users.db` SQLite is legacy/being phased out), pandas for
CSV/data wrangling, Jinja2 + Bootstrap, Groq/Gemini for AI features,
Gunicorn + nginx + systemd on a GCE VPS (`deploy/`), also has
Dockerfile/railway.json/vercel.json for alternate hosting.

## Baseline repo hygiene

- Secrets live only in `.env` (gitignored), keyed off `.env.example` —
  never commit a secret value into code, docs, or a migration file.
- Never commit `venv/`, `.venv/`, `wsl_venv/`, `get-pip.py`, or any
  `*.db` file (`users.db` holds PII) — all are already gitignored; don't
  work around that.
- Line endings are normalized to LF repo-wide via `.gitattributes`
  (`* text=auto eol=lf`). Editing through a Windows path (`\\wsl$\...`)
  can reintroduce CRLF — if a diff shows whole-file line-ending churn
  instead of a real content change, that's the cause; fix it before
  committing.

## Repository architecture

| Path | Role | Care level |
|---|---|---|
| `app.py` | Legacy monolith — by far the largest single file in the repo. Bootstrap, Flask app + CSRFProtect init, session config, most `register_blueprint()` calls, plus a shrinking set of routes still defined directly here (migrating out to `blueprints/` over time) | **Never read or rewrite the whole file.** Use `rg`/grep for the route, function, or config block you need, then read only that line range. |
| `auth.py` | Login/register/logout, Google OAuth, Telegram Login Widget auth | High — see "Authentication & authorization" for the identity-linking invariant |
| `cabinet.py` | User cabinet / researcher self-registration; owns scholar attributes (degree, region, ixtisoslik, photo) via `olim_profiles` | Never duplicate scholar attributes onto `users` |
| `data.py` | CSV/DB data loading, caching, dissertation filtering, CSV export, `get_connection()` (the hardened pooled PG connection other modules import), `import_oak` (OAK scraper ingest API) | High — most other modules call into `data.get_connection()` |
| `upload.py` | Manual CSV upload for dissertations (`/upload`), column validation only (extension + required-column check, no CSV content sanitization) | Medium |
| `analytics.py` | Groq AI topic analysis, heatmaps, clustering, comparison, trends endpoints | Medium |
| `utils/` | `search_helper.py` (universal fuzzy/trgm search, `build_search_clause`), `transliterate.py` (Latin/Cyrillic, h≠ҳ vs x=х), `jinja_filters.py`, `username.py`, `links.py` | Reuse these before writing new search/translit logic |
| `blueprints/` | Feature blueprints, one file per feature area — see table below | Varies per blueprint |
| `services/` | `document_generator.py`, `document_prompts.py`, `docx_builder.py` — Kafedra AI document generation | Medium |
| `templates/` | 50+ Jinja2 templates; shared partials in `templates/partials/` (e.g. `cta_banner.html`, `seo_meta.html`) and `_reminders_widget.html` | Shared templates = high conflict risk with parallel agents |
| `static/` | JS (`genealogy.js`, heatmap, collaboration graph), PWA assets (`manifest.json`, `sw.js`), uploads, brand assets | — |
| `data/dissertatsiyalar.csv` + `data/konferensiyalar_*.csv` | Source CSVs | Don't hand-edit; these are scraper/import output |
| `scripts/` | One-off/maintenance scripts: `create_tables.py`, `apply_institution_merges.py`, `flag_data_quality.py`, `merge_universities.py`, `daily_scraper.py`, `grant_scraper.py`, `seed_grants.py`, scraper scripts | Most are DB-write scripts — check for `--apply`/dry-run flags before running (see DB section) |
| `migrations/` | Additive `.sql` files, one per feature (`add_conferences.sql`, `add_payments.sql`, etc.) — no migration runner/framework, applied manually | Never apply from a task unless explicitly asked; additive only |
| `deploy/` | `deploy.sh` (GCE provisioning), `olimlar.service` (systemd), `olimlar.uz.conf` (nginx), cron files | Never run or edit for a task unless explicitly asked. **Invariant: no deploy script, git remote, or other tracked file may ever contain a real credential** — secrets are environment variables only (`.env`, gitignored). If you ever find a hardcoded credential in a tracked file (here or elsewhere), don't copy or quote its value anywhere, including in your own report — flag its location as a finding instead. |
| `test_endpoints.py`, `test_import.py`, `run_smoke.py` | Ad-hoc smoke scripts using Flask's `test_client()` — no pytest suite exists | See Testing section |
| `.github/workflows/` | `daily.yml` (scraper), `reminders.yml` (Smart Eslatmalar dispatch, needs `REMINDERS_API_KEY`), `conferences.yml` | Don't edit without being asked |
| `requirements.txt` | Flask 3.1, Flask-Caching, pandas 2.2.3, flask-login, bcrypt, Flask-WTF (CSRF), psycopg2-binary, groq, gunicorn, bleach, python-docx, Pillow | No lockfile beyond this; check before adding a new dependency |

New feature code: prefer a new file in `blueprints/` over adding to
`app.py`. New scholar/profile fields go on `olim_profiles`
(`cabinet.py`), never on `users`.

### Blueprint → feature map

| Blueprint | Feature |
|---|---|
| `auth` (in `auth.py`) | login/register/OAuth |
| `data`, `cabinet`, `upload`, `analytics` (root-level modules, registered as blueprints) | core data/profile/upload/analytics |
| `admin.py` | admin dashboard: user blocking, broadcasts, surveys, content CRUD |
| `advisors.py` | `/rahbar-topish` directory + `/rahbar/<slug>` profiles |
| `subscriptions.py` | `specialty_subscriptions` ("ixtisoslik obunasi"), new-defense alerts |
| `reminders.py` | Smart Eslatmalar — deadline alerts, site + Telegram, cron-driven |
| `notifications.py` | `notification_prefs` per-user toggles |
| `grants.py` | Grants listing/tracking/admin CRUD |
| `content.py` | Blog, news, vacancies, courses, journals, university profiles |
| `dissertation.py` | "Konstruktor" — collaborative dissertation workspace: advisor invites, collaborators, `/workspace`, project blocks |
| `roadmap.py` | `/reja` roadmap planner, links to Konstruktor via `roadmap_plans.diss_project_id` |
| `councils.py` | `/councils` doctoral council directory |
| `conferences.py` | Conference directory, `user_conference_bookmarks` (deliberate exception to the single-bookmark-table rule) |
| `ranking.py`, `reyting.py` | `/reyting` H-index ranking (current) + `/reyting/ball` legacy ball-rating |
| `olimlar_catalog.py` | `/olimlar` scholar catalog 2.0, `scholar_follows` |
| `univer.py` | `/univer` university B2B panel (owner/staff/viewer roles, licenses) — metrics-only, never exposes underlying content |
| `payments.py` | ATMOS payment integration — `user_has_premium`/`consume_credit` are the sole paywall source of truth |
| `maqolalar.py` | `/maqolalar-tahlili` OpenAlex-backed publication gap analysis |
| `share.py` | Public share pages (`/olim/<name>/daraxt`) + OG image generation |
| `kafedra.py` | `/kafedra` AI document generator (9 doc types), via `services/` |
| `map.py`, `xarita.py` | Map/geography views |
| `journal_check.py` | Journal verification tooling |
| `messages.py` | In-app messaging |
| `acquisition_survey.py` | Onboarding/acquisition survey gating |
| `legal.py` | Legal/static pages |
| `topic_analysis.py` | Groq-backed topic analysis endpoints |
| `saved.py` | `user_bookmarks` — the single generic bookmark table; extend this, don't create a parallel one (conferences is the one sanctioned exception) |

## Key data entities

- **`users`** (Postgres) — `id` (canonical), `username`, `email`,
  `password_hash`, `is_admin`, `telegram_chat_id`, `visit_count`.
  Telegram/Google accounts are provisioned as rows here too.
- **`olim_profiles`** — the home of all scholar attributes (degree,
  region, ixtisoslik, photo, `slug` for `/@slug` vanity URLs). Linked to
  `users` by user id. `username_history` tracks slug changes.
- **`dissertations`** — columns per the CSV import contract in
  `upload.py`: `Sana, Daraja, Olim, Mavzu, Ixtisoslik, Muassasa,
  Ilmiy_rahbar, Link` (stored lowercase). Populated by the daily OAK
  scraper (`import_oak` in `data.py`) and manual `/upload`.
  Supervisor/student relationships here drive the genealogy graph.
- **`institution_map`** — canonical/alias bridge for university names
  (Cyrillic ↔ Latin, `cyrillic_name`/`canonical_name`/`latin_name`);
  `/universities` and profile pages resolve through this, not raw text
  matches. Merges are admin-history-tracked and reversible via
  `scripts/apply_institution_merges.py` — never
  `migrate_institutions.py --apply` (drops the merge table).
- **`notification_prefs`** — per-user key/value ON/OFF toggles.
  **`specialty_subscriptions`** — max 5/user, site+Telegram toggle pair.
  **`user_bookmarks`** — single generic bookmark table (+
  `user_conference_bookmarks` as the one sanctioned parallel table).
  **`scholar_follows`** — follow relationships for `/olimlar` catalog.
  **`roadmap_plans`** — links to Konstruktor via `diss_project_id`.
- Text-based (not yet canonical-ID) linkage still exists between
  dissertations and institutions/advisors in places — treat fuzzy/text
  matches as provisional, not authoritative, when writing new logic.

## Authentication & authorization

- Session auth via `flask_login` (`current_user`, `login_required`).
  `is_admin` is a boolean column on `users`; admin checks are inline —
  `if not getattr(current_user, 'is_admin', False): abort(403)` —
  repeated per-route in `blueprints/admin.py` and elsewhere, not a shared
  decorator. Follow the same inline pattern for new admin routes, or
  factor a decorator out only if the task calls for touching many routes
  at once.
- **Google OAuth** and **Telegram Login Widget** both route through
  `auth.py`, provisioning/matching rows in `users`. **Invariant: an
  external-identity login (Telegram, Google, or any future provider)
  must be linked and re-matched only by that provider's immutable
  numeric account id** (or a column derived 1:1 from it) — never by
  `username`, display name, or an email guessed from either. Matching on
  a mutable, attacker-influenceable field lets one user's login attach
  to a different person's account. If you find code that falls back to
  a username/email match when the id lookup misses, that's a bug to fix
  or flag, not a pattern to extend.
- **CSRF**: `Flask-WTF`'s `CSRFProtect` is initialized once on `app` in
  `app.py`, with `generate_csrf()` injected into templates as
  `csrf_token()`. **Invariant: a browser write route (anything a
  logged-in user's own browser POSTs to) must never be CSRF-exempt at
  the blueprint level.** Only individual machine-to-machine
  endpoints — a webhook verified by its own signature, or an
  API-key-gated ingest endpoint — may carry a per-route `@csrf.exempt`,
  each with a one-line reason next to it. If you find a blueprint-wide
  `csrf.exempt(<blueprint>)` in the code, treat it as existing debt to
  flag, not a precedent for new exemptions.
- **Machine-to-machine endpoints** use bearer/header API keys, not user
  sessions: `import_oak` (OAK scraper → site) checks `OAK_API_KEY`;
  Smart Eslatmalar's `POST /api/v1/reminders/process` checks
  `REMINDERS_API_KEY` via `X-API-Key`. Never make these session- or
  cookie-authenticated instead.
- Authorization must be enforced server-side on every route that needs
  it — never rely on hiding a button or link in the template as the only
  gate.

## Data quality rules (target state — apply going forward)

These reflect the intended direction of `scripts/flag_data_quality.py`
and the institution-merge pipeline; treat anything not already reflected
in that code as **planned**, not already enforced everywhere:

- Preserve the original scraped/source value; normalization must be
  additive, not destructive (no in-place overwrite of raw text fields).
- No automatic merge of researchers or institutions. `flag_data_quality.py
  --apply` writes *flags/review candidates* to the DB; it does not merge
  anything even with `--apply` ("3-qoida — qisqa ism — --apply bilan ham
  yozilmaydi, faqat hisobot" — short-name matches are report-only,
  never auto-applied).
- Institution merges go through `scripts/apply_institution_merges.py`
  (dry-run by default, `--apply` to write), which records merges in an
  admin-history table so they're reversible. This is the only sanctioned
  merge path — never `migrate_institutions.py --apply`.
- `institution_map` is the canonical/alias layer for institution names;
  prefer resolving through it over ad-hoc string matching, and account
  for Latin/Cyrillic + apostrophe + case variants
  (`utils/transliterate.py` already handles h≠ҳ vs x=х).
- Don't default an unmatched region to "Toshkent" or any other specific
  value — leave it unset/null and surface it as a gap.

## Task workflow

For every task:

1. Confirm current git branch/worktree (`git status --short --branch`,
   `git worktree list`) and that you're not on `main` or inside another
   agent's worktree.
2. Read this file (already done if you're reading it) and, if the task
   matches one, the relevant `.claude/skills/*/SKILL.md`.
3. Read only the code relevant to the task — grep/rg first in large
   files (`app.py` especially), then read the specific line range.
4. Find the root cause before editing.
5. Make the smallest safe change; reuse existing helpers
   (`utils/search_helper.py`, `utils/transliterate.py`,
   `data.get_connection()`, etc.) instead of writing new ones.
6. Add/update a targeted test if one is warranted.
7. Run the targeted verification first, then broader checks only if the
   change touches shared/high-risk code (see Verification below).
8. Run `git diff --check` and scan your own diff for secrets before
   reporting done.
9. State explicitly what you did **not** verify.

You do not need to re-audit the whole repository each task — this file
plus the relevant skill is normally enough context to start from.

## Large files

`app.py` is a very large single file — by far the biggest in the repo,
and growing legacy debt rather than the active pattern for new code.
Don't open it end-to-end. Use `rg` for the route decorator, function
name, or config block, then `Read` with an `offset`/`limit` around the
match. The same applies to any other file over a few hundred lines —
search first, read narrowly.

## Frontend & UX

- UI copy is Uzbek — keep new copy natural Uzbek, not machine-translated
  phrasing.
- Reuse existing template/CSS patterns (see `templates/partials/` for
  shared banners/SEO meta) rather than inventing a new design language.
- Check both desktop and a ~390px mobile width; no horizontal overflow;
  touch targets sized for mobile.
- Cover loading, empty, error, disabled, and success states — not just
  the happy path.
- Don't add a new nav entry or page that's empty/placeholder.
- Don't build a near-duplicate of an existing analytics/map page; check
  `blueprints/` and `templates/` for something already covering the
  need.

### Browser verification

If a browser tool is available for a UI task: start the dev server with
`python run_server.py` (binds `0.0.0.0:8000`, `FLASK_ENV=development`,
debug off), then check desktop and 390px widths, browser console
errors, and the relevant flow (login, search, scholar profile, cabinet,
admin, form validation, 401/403 responses, loading/empty states). If no
browser tool is available, say so — do not claim you verified visually
when you didn't.

## Database & migrations

- No migration framework/runner — `migrations/*.sql` are applied
  manually against the server DB. Never wire one into app startup.
- New schema changes: write an additive `.sql` file following the
  existing naming (`add_<feature>.sql`), with explicit
  `BEGIN;`/`COMMIT;`, and note the rollback (`DROP COLUMN`/`DROP TABLE`
  as applicable) in a comment. Prefer nullable additive columns over
  altering/dropping existing ones.
- Check for existing unique constraints/FKs/indexes on a table before
  adding new ones (`scripts/create_tables.py` has the base schema for
  `users`/`dissertations`; feature tables are in their own
  `migrations/add_*.sql`).
- Never apply a migration, run a DB-writing script, or connect to the
  production/staging DB from a task unless explicitly instructed to.
- Local `.env` Neon credentials are known to be stale — `import app`
  works locally, but anything hitting `get_connection()` (routes,
  scripts) needs the real server. Don't interpret a local DB auth
  failure as a code bug without checking this first.
- If a migration draft is explicitly marked "DRAFT — DO NOT APPLY",
  leave it untouched and don't reference it elsewhere as already-applied
  schema.

## Testing & verification

There is no pytest suite — verification is ad-hoc scripts plus manual
checks. Real, working commands (run from the repo root inside WSL, since
the Windows-side Python venvs in this repo are broken for this project —
see below):

| Command | When | Needs DB? | Writes? |
|---|---|---|---|
| `python3.12 -m py_compile <file(s)>` | After any Python edit, fast syntax check | No | No |
| `python3.12 -c "import app"` | Confirms the app still boots (imports, blueprint registration, CSRF init) | No (import-time only; routes hitting the DB will fail separately) | No |
| `python3.12 test_import.py` | Same import check, prints pass/fail | No | No |
| `python3.12 -u test_endpoints.py` | Flask `test_client()` smoke test of a few public routes (`/login`, `/register`, `/`, `/dashboard`) | Partially — some assertions may fail/short-circuit if a route touches the DB | No |
| `python3.12 scripts/flag_data_quality.py` (no `--apply`) | Dry-run report for data-quality changes | Yes | No (report only) |
| `python3.12 scripts/apply_institution_merges.py <plan.csv>` (no `--apply`) | Dry-run preview of an institution merge plan | Yes | No |

Run Python via WSL, not the repo's own venvs:
`wsl.exe -d Ubuntu-24.04 -- bash -c 'cd /home/botirbek/oak-dissertatsiya/<worktree-or-repo-dir> && python3.12 ...'`
(`venv/`, `.venv/`, `wsl_venv/` are all broken for this project — Windows
Python 3.14 has no pandas wheel, and `wsl_venv/` has no pip). The system
`python3.12` already has flask/pandas/psycopg2/dotenv etc. installed.

There's no configured `APP_ENV=testing` DB guard — treat any script or
route that calls `get_connection()` as touching the real configured DB,
and don't run it unless that's intended and the credentials are known
(local `.env` creds are stale; assume server-only unless told
otherwise).

No lint/format/type-check tooling is configured in this repo (no
`pyproject.toml`, no `.flake8`, no `mypy.ini`) — don't invent commands
for these.

## Git & parallel agents

Multiple agents may be working in this repo concurrently, each in its
own worktree. **Run `git worktree list` at the start of a session** to
see the current set — don't rely on memory of a prior session, since
worktrees are created and removed as tasks start and finish. Do not
read from or write to a worktree that isn't yours, and do not
amend/reset/rebase another agent's commits, whatever it's named.

- Work in your own branch + worktree; never commit directly on `main`.
- `app.py`, `data.py`, `migrations/`, and shared `templates/partials/`
  are high-conflict — coordinate scope before editing them in parallel
  with another agent.
- Don't merge parallel branches together automatically when a task
  finishes; that's a human/explicit-instruction decision. If asked to
  reconcile, security branches land before data-quality branches.
- Never commit, push, merge, rebase, force-push, deploy, restart a
  service, or rewrite history without the user explicitly asking for
  that specific action in that specific task. (The repo's older
  single-agent convention was "commit after each task" — with multiple
  agents now running in parallel worktrees, that default is superseded:
  ask before committing, same as any other write action here.)

## Dependencies

- Check `requirements.txt` (a small, explicitly pinned dependency list)
  before adding anything — prefer solving with what's already there.
- If a new dependency is genuinely needed, say why in the report and add
  it to `requirements.txt` alongside the code that needs it, not as a
  drive-by addition.
- Don't add a frontend framework/build step for what Bootstrap +
  vanilla JS already covers.
- For security- or file-parsing-related dependencies, note the
  package's maintenance status in the report.

## Report format

At the end of a task, report concisely:

- Branch/worktree used.
- Files changed and why.
- Tests run vs. not run.
- Whether DB or deploy was touched (should almost always be "no").
- Remaining risks / known-issues touched but not fixed.
- Any manual follow-up the user needs to do.

Don't fix out-of-scope issues you notice mid-task — note them instead.

## Efficiency

- Don't re-audit the whole repository each task; start from this file
  and the relevant skill.
- Read only the code that's actually changing plus its direct
  dependents/tests.
- Don't repeat an identical grep or full-file read you already did this
  session.
- Run the targeted test before any broader suite; only run broader
  checks when shared or high-risk code changed.
- Prefer a minimal diff over a broad automatic refactor.
- Give status updates only for blockers, risks, or when you need
  permission — not a running narration.
