Before every task, read and follow `AGENTS.md` — it's the shared source of
truth for architecture, security, data-quality, DB, testing, and git rules
for this repo. This file is only a fast Claude Code session-start checklist.

## Session start

1. Read `AGENTS.md` (skip if already read this session).
2. `git status --short --branch` — confirm branch/worktree.
3. Confirm you're not on `main` and not inside another agent's worktree
   (see "Git & parallel agents" in `AGENTS.md`).
4. If the task matches one, read the relevant
   `.claude/skills/<name>/SKILL.md`.
5. Before opening a large file (`app.py` especially), `rg`/grep for the
   route or function first — never read it end-to-end.
6. Production DB, deploy, push, merge, or migration-apply only with
   explicit user instruction in this task.

## Repository fast map

- `app.py` — bootstrap + most blueprint registrations + shrinking legacy routes (grep, don't read whole)
- `auth.py`, `cabinet.py`, `data.py`, `upload.py`, `analytics.py` — core modules
- `blueprints/` — feature blueprints (see `AGENTS.md` table)
- `utils/` — search, transliteration, jinja filters — reuse before writing new
- `migrations/*.sql` — additive, manual, no runner
- `scripts/` — maintenance scripts, mostly dry-run-by-default with `--apply`
- `deploy/` — never touch; credentials must never be hardcoded here — env vars only

## Fast verification

Run via WSL (`wsl.exe -d Ubuntu-24.04 -- bash -c 'cd /home/botirbek/oak-dissertatsiya/<dir> && ...'`),
not the repo's own venvs (all three are broken for this project):

```
python3.12 -m py_compile <changed files>   # syntax check, no DB
python3.12 -c "import app"                 # app still boots, no DB
python3.12 -u test_endpoints.py            # smoke test a few public routes
```

Full list and caveats in `AGENTS.md` → Testing & verification.

## High-risk areas

auth.py (external-identity linking must use immutable provider IDs only
— see `AGENTS.md`), admin routes, upload.py, CSRF exemptions in app.py,
app.py startup/config, data.py `get_connection()`, migrations/,
institution/researcher merge logic, shared templates/partials.

## Do not

- Don't read or rewrite all of `app.py`.
- Don't commit, push, merge, rebase, force-push, deploy, or restart a
  service without explicit instruction for that action.
- Don't apply a migration or run a DB-writing script without explicit
  instruction.
- Don't touch another agent's worktree or amend its commits.
- Don't add a blanket `csrf.exempt()` on a new blueprint.
- Don't default an unmatched region to "Toshkent" or auto-merge
  researchers/institutions.
- Don't invent lint/type-check/build commands — none are configured.
- Don't hardcode or quote credentials anywhere in `deploy/` — env vars only.
- Don't duplicate scholar attributes onto `users` — they belong on
  `olim_profiles`.
