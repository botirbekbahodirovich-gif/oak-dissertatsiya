---
name: olimlar-security
description: Security checklist for auth, CSRF, uploads, admin permissions, cookies, headers, secrets, and exception handling in Olimlar.uz.
---

# olimlar-security

## When to use

Task touches: `auth.py`, `is_admin` checks, `CSRFProtect`/`csrf.exempt`,
file upload code (`upload.py`, `cabinet.py` photo upload), session/cookie
config, API-key-gated endpoints (`OAK_API_KEY`, `REMINDERS_API_KEY`),
secret/env handling, or exception messages returned to the client.

## Read first

- `AGENTS.md` → "Authentication & authorization" section (identity-linking
  and CSRF invariants).
- `auth.py` — login/register/OAuth/Telegram login (grep for the route
  first, e.g. `telegram_login`, `google_callback`).
- `app.py` — grep for `CSRFProtect`, `csrf.exempt`, `_log_silent`,
  `session_secret` (don't read the whole file).

## Invariants

- Server-side authorization only. Never trust a hidden UI element as the
  sole gate.
- Admin checks: `if not getattr(current_user, 'is_admin', False):
  abort(403)`, inline per-route — match this pattern for new admin
  routes.
- CSRF: `CSRFProtect` is on by default for all POST routes. Exempt a
  *specific route*, never a whole new blueprint. Any new
  `@csrf.exempt` needs a one-line reason in the PR/report (e.g. "external
  webhook, verified by its own signature check").
- External-identity login (Telegram, Google, any future provider) must
  key off that provider's immutable numeric account id, never fall back
  to matching by `username`/display name/guessed email. This is now
  **implemented**: `auth.py` `telegram_login()` and `cabinet.py`
  `telegram()`/`google_callback()` link accounts only by the immutable
  `users.telegram_id` (unique partial index `uq_users_telegram_id`,
  migration `001_add_users_telegram_id.sql`); the username/email fallback
  was removed and is covered by `tests/test_security_phase0.py` and
  `tests/test_onboarding_identity.py`. Treat any reintroduced username/email
  fallback as a regression to fix — don't replicate it.
- File uploads: validate extension via `secure_filename` +
  an allow-list (see `cabinet.py` photo upload: `.jpg/.jpeg/.png/.webp`)
  at minimum. If a task adds a new upload surface, prefer also checking
  actual content (e.g. `PIL.Image.open` round-trip for images) over
  extension alone — the existing CSV upload in `upload.py` only checks
  the filename suffix and required columns, which is a known gap, not a
  pattern to replicate for new binary uploads.
- Machine-to-machine endpoints use header API keys
  (`OAK_API_KEY`, `REMINDERS_API_KEY` via `X-API-Key`/`X-API-KEY`),
  compared with `hmac.compare_digest` or equivalent constant-time check
  where feasible — not plain `==` against a user-suppliable value if
  timing matters for the specific check.
- Secrets: `.env` only, never hardcoded anywhere — not in application
  code, `deploy/` scripts, migrations, or docs. If you ever find a
  hardcoded credential in a tracked file, don't copy or quote its value
  anywhere, including your own report — flag its location and move on.
- Don't leak internal exception detail to the client. `app.py` has
  `_log_silent(exc)` and a 500 handler that logs
  `traceback.format_exc()` server-side via `app.logger` — return a
  generic message/status to the client, log the real detail.
- `SESSION_SECRET`/`SECRET_KEY`: `app.py` falls back to an ephemeral key
  with a warning if unset. That's fine for local dev; flag if a task
  implies production startup without it set.

## Minimal tests

- `python3.12 -c "import app"` — confirms CSRF init and blueprint
  registration still succeed.
- `python3.12 -u test_endpoints.py` — checks `/login`/`/register` still
  return CSRF tokens.
- `APP_ENV=testing python3.12 -m unittest tests.test_security_phase0 tests.test_onboarding_identity`
  — the Phase 0 security + Telegram-identity/onboarding regression suites
  (CSRF enforcement, immutable-id linking, exception sanitization).
- For an auth-flow change, manually trace: does this path check
  `is_admin` correctly, is CSRF still enforced, is the identifier used
  for lookup immutable?

## Release blockers

- Any new blanket `csrf.exempt(<blueprint>)`.
- Any new user-lookup keyed on a mutable/attacker-influenced field
  (username, display name, email-guessed-from-name) for auth linking.
- Any secret committed to a file (`.env` values, API keys, DB
  credentials) outside `.env`/`.env.example`.
- Any admin route missing the `is_admin` check.
- Client-visible stack traces or raw exception text.

## Do not

- Don't add a shared `@admin_required` decorator refactor across all
  routes unless the task specifically asks for that — it's a big diff
  for a convention that's currently intentionally inline; note it as an
  observation instead.
- Don't silently "fix" an identity-linking issue as a drive-by inside an
  unrelated task — scope it explicitly and check for other agents'
  worktrees first.
- Don't run or edit `deploy/deploy.sh`.
