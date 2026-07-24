---
name: olimlar-release
description: Checklist for preparing a branch for release review — divergence from main, migration order, secrets scan, rollback notes. Does not deploy.
---

# olimlar-release

## When to use

Task asks to prepare a branch for merge/release, produce a merge/staging
checklist, or review a branch before it goes to the maintainer for
deploy. This skill never deploys, merges, or pushes anything itself.

## Read first

- `AGENTS.md` → "Git & parallel agents" and "Database & migrations"
  sections.
- `git log main..<branch> --oneline` and `git diff main...<branch>` (or
  the equivalent for the branch in question) to see the actual change
  set — don't rely on memory of what the task touched.
- Any `migrations/*.sql` added or modified on the branch.

## Steps

1. Check divergence from latest `main`:
   `git fetch origin main --quiet` (network — fine, read-only) then
   `git log main..HEAD --oneline` and `git merge-base` to see how stale
   the branch is.
2. Preview conflicts against `main` without merging:
   `git merge-tree $(git merge-base main HEAD) main HEAD` (dry preview,
   writes nothing) — flag any hits in `app.py`, `data.py`,
   `migrations/`, or shared `templates/partials/`.
3. Get the exact commit SHA the review covers
   (`git rev-parse HEAD`) and state it in the report.
4. Secrets scan the diff: `git diff main...HEAD | grep -iE
   "password|secret|api_key|token|DATABASE_URL"` (read-only) — flag
   anything that looks like a real credential rather than a
   `.env.example` placeholder. Per the credentials invariant in
   `AGENTS.md`, no tracked file should ever contain one — if the scan
   turns one up anywhere (including `deploy/`), flag it in the report
   rather than assuming it's already known/acceptable.
5. If the branch adds/changes a `migrations/*.sql` file: confirm it's
   additive (no `DROP`/destructive `ALTER` without an explicit
   rollback note), confirm ordering relative to any other pending
   migration, and confirm it is NOT marked "DRAFT — DO NOT APPLY" while
   being referenced as already-applied.
6. List manual steps the maintainer needs to run on the server
   (migration apply order, new env vars per `.env.example`, any
   `pip install` for a new dependency).
7. Note what smoke tests were run (see `olimlar-testing`) and what
   wasn't.

## Verification

- `python3.12 -m py_compile` across changed `.py` files.
- `python3.12 -c "import app"` — app still boots.
- `python3.12 -u test_endpoints.py` — basic smoke.
- No DB write, no deploy, no push, no merge — this skill only produces
  a report.

## Do not

- Don't merge, push, deploy, restart a service, or apply a migration —
  ever, even if everything looks clean. That decision belongs to the
  user.
- Don't take the branch's own commit messages as a substitute for
  reading the actual diff.
- Don't skip the secrets scan because "it's just docs" or "just
  templates" — templates can leak API keys in inline scripts too.
