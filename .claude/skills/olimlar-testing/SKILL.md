---
name: olimlar-testing
description: How to verify a Python change in this repo — no pytest suite, ad-hoc smoke scripts, WSL python3.12, no APP_ENV=testing DB guard.
---

# olimlar-testing

## When to use

After any code change, before reporting a task done. Also read before
proposing a "run the test suite" step — there isn't a formal one.

## Read first

- `AGENTS.md` → "Testing & verification" section for the full command
  table and caveats.

## Steps (targeted first, then broader only if warranted)

1. Fast syntax check on exactly the files you changed:
   `python3.12 -m py_compile <file1> <file2> ...`
2. Confirm the app still imports/boots (catches blueprint registration
   and top-level import errors):
   `python3.12 -c "import app"` or `python3.12 test_import.py`
3. If the change touches a route reachable without a DB write, run:
   `python3.12 -u test_endpoints.py` and read its PASS/FAIL lines — it's
   a short ad-hoc script (`test_endpoints.py`), not a real suite;
   individual assertions can fail for reasons unrelated to your change
   (e.g. a route that needs the real DB), so read the failure, don't
   just trust the exit code.
4. Only reach for a broader manual check (multiple routes, DB-backed
   flow) when the change touches shared/high-risk code (`app.py`,
   `data.py`, `auth.py`, a migration, a shared template) — not for every
   small change.
5. `git diff --check` for whitespace/line-ending problems before
   finishing.

## Environment

- Run Python via WSL's system interpreter, not this repo's own venvs:
  `wsl.exe -d Ubuntu-24.04 -- bash -c 'cd /home/botirbek/oak-dissertatsiya/<dir> && python3.12 ...'`
  `venv/`, `.venv/`, `wsl_venv/` are all broken for this project
  (Windows Python 3.14 has no pandas wheel; `wsl_venv/` has no pip).
- There is no `APP_ENV=testing` guard and no test-only DB — any script
  or route that calls `data.get_connection()` hits the real configured
  Postgres. Don't run DB-writing scripts/routes as part of "testing"
  unless that's explicitly the point of the task and explicitly
  instructed.
- Local `.env` Neon credentials are known stale — `import app` succeeds
  locally, but anything that actually queries the DB will fail with an
  auth error there. That failure means "needs the real server," not
  "the code is broken" — say so rather than debugging it as a code bug.

## Non-zero exit / failure handling

- `py_compile` and `import app` both raise/exit non-zero on a real
  error — treat that as a hard stop, not something to work around.
- `test_endpoints.py`/`test_import.py` print PASS/FAIL per check but
  may still exit 0 overall — read the printed lines, don't just check
  the exit code.

## Do not

- Don't invent a pytest/unittest command — there is no pytest suite in
  this repo.
- Don't invent lint/type-check/build commands — none are configured
  (no `pyproject.toml`, `.flake8`, or `mypy.ini`).
- Don't run a DB-writing script "just to see" during verification.
- Don't claim a route "works" based only on `py_compile`/import success
  — that only proves it parses and imports, not that it behaves
  correctly.
- Don't silently skip verification and report success — if something
  wasn't run (e.g. no DB access), say so explicitly in the report.
