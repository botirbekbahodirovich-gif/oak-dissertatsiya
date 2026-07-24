# Olimlar.uz staging runbook

This runbook prepares an environment that is deliberately separate from
production. Commands are examples: review placeholders and obtain approval
before any server, database, migration, service, or DNS change.

## Isolation contract

| Resource | Staging value |
|---|---|
| URL | `staging.olimlar.uz` |
| application directory | `/opt/olimlar-staging` |
| systemd service | `olimlar-staging` |
| Gunicorn bind | `127.0.0.1:8001` |
| environment file | `/opt/olimlar-staging/.env.staging` |
| logs | `/var/log/olimlar-staging` |
| backups | `/var/backups/olimlar-staging` |
| runtime mode | `APP_ENV=production` |

Never reuse the production database, session secret, OAuth clients, Telegram
bot, payment credentials, service name, app directory, or backup directory.
Unset `POSTGRES_URL` on staging so it cannot act as a fallback to another DB.

## 1. Server, DNS, and TLS prerequisites

- Use a dedicated staging VM/container or an explicitly isolated staging
  account.
- Point only `staging.olimlar.uz` to that environment.
- Keep inbound PostgreSQL closed except to approved staging sources.
- Install Python 3.11+, PostgreSQL client tools, Git, Nginx, and systemd.
- Obtain a certificate for `staging.olimlar.uz` manually. Do not request or
  replace production certificates.
- Replace `__STAGING_FULLCHAIN_PATH__` and `__STAGING_PRIVATE_KEY_PATH__` in
  `deploy/nginx-staging.conf.example` only after the staging certificate exists.

Create isolated directories after replacing the user/group placeholders:

```bash
sudo install -d -m 0750 -o __STAGING_USER__ -g __STAGING_GROUP__ \
  /opt/olimlar-staging \
  /var/log/olimlar-staging \
  /var/backups/olimlar-staging
```

The application stores user-generated images/documents under
`static/uploads/` and `static/generated_docs/`; these directories must never
be shared with production.

## 2. Create a separate staging database

Provision a new database whose host or database name contains an explicit
`staging`, `stage`, or `stg` marker. Use a dedicated least-privilege database
role. Do not clone production personal data unless a separately approved,
sanitized process exists.

After the reviewed repository is checked out in the next section, copy
deploy/staging.env.example to /opt/olimlar-staging/.env.staging, populate it
directly on the server, and set mode 0600. Required values are DATABASE_URL,
a newly generated staging-only SESSION_SECRET, and APP_ENV=production. OAuth
callback URLs and all third-party credentials must be staging-specific. Never
print the populated environment file.

Credential confirmation:

- [ ] old PostgreSQL/Neon credentials rotated;
- [ ] exposed or historical GitHub PATs revoked;
- [ ] staging `SESSION_SECRET` generated independently;
- [ ] any admin account once created with `admin123` reset;
- [ ] staging OAuth, Telegram, scraper, reminder, and payment credentials are
      separate or intentionally disabled;
- [ ] `.env.staging` is mode `0600` and untracked.

## 3. Check out the exact reviewed commit

The staging infrastructure was prepared on top of this Phase 0 base commit:

```text
2d5ef29b1f1e28a7d8fd379165e93752fc3088bb
```

After the staging configuration commit is reviewed, record its full 40
character SHA as REVIEWED_SHA. Do not deploy by branch name. Fetch the
reviewed object, verify it, and keep the staging checkout clean:

```bash
sudo -u __STAGING_USER__ git clone __STAGING_REPOSITORY_URL__ /opt/olimlar-staging
REVIEWED_SHA=__FULL_40_CHARACTER_REVIEWED_SHA__
git -C /opt/olimlar-staging fetch --no-tags origin "$REVIEWED_SHA"
git -C /opt/olimlar-staging cat-file -e "${REVIEWED_SHA}^{commit}"
test -z "$(git -C /opt/olimlar-staging status --porcelain)"
```
Create isolated writable directories and the secret file after checkout:

~~~bash
sudo install -d -m 0750 -o __STAGING_USER__ -g __STAGING_GROUP__ /opt/olimlar-staging/static/uploads /opt/olimlar-staging/static/generated_docs /opt/olimlar-staging/flask_cache
sudo install -m 0600 -o __STAGING_USER__ -g __STAGING_GROUP__ /opt/olimlar-staging/deploy/staging.env.example /opt/olimlar-staging/.env.staging
sudoedit /opt/olimlar-staging/.env.staging
~~~

The staging deploy example is dry-run by default:

```bash
sudo --preserve-env=APP_ENV,DATABASE_URL,SESSION_SECRET \
  bash deploy/deploy-staging.sh.example --sha "$REVIEWED_SHA"
```

Only after review and approval may `--execute` be added. `--restart` is a
second, independent approval. The script never runs migrations.

## 4. Dependencies and pre-flight

The repository confirms the Gunicorn entry point as `app:app`. Existing
production configuration uses two workers, two threads, and a 120-second
timeout; the staging sample preserves those settings on isolated port `8001`.

Create a staging-only virtual environment and install the reviewed requirements:

~~~bash
sudo -u __STAGING_USER__ python3 -m venv /opt/olimlar-staging/venv
sudo -u __STAGING_USER__ /opt/olimlar-staging/venv/bin/pip install -r /opt/olimlar-staging/requirements.txt
~~~
Before changing the deployed checkout:

```bash
APP_ENV=testing python3 -m unittest -v tests.test_security_phase0
APP_ENV=testing python3 -m unittest -v tests.test_create_admin
APP_ENV=testing python3 test_endpoints.py
APP_ENV=testing python3 test_import.py
git diff --check
git ls-files -z '*.py' | xargs -0 python3 -m py_compile
bash -n deploy/deploy-staging.sh.example
python3 scripts/check_staging_environment.py --config-only
```

The last command validates isolation without opening a DB connection. The full
checker used after migration performs only SELECT operations and suppresses
driver details that might expose DSN metadata.

## 5. Backup before migration

First confirm the DB identity without displaying the DSN:

```bash
psql -X "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -c "SELECT current_database(), current_user;"
```

Then create a custom-format staging backup outside the repository:

```bash
BACKUP_DIR=/var/backups/olimlar-staging
BACKUP_FILE="${BACKUP_DIR}/pre_phase0_$(date +%Y%m%d_%H%M%S).dump"
umask 077
pg_dump "$DATABASE_URL" --format=custom --file="$BACKUP_FILE"
test -s "$BACKUP_FILE"
```

Stop if the identity is ambiguous or the backup is missing/empty.

## 6. Preview and apply migrations manually

Record the existing schema before applying anything:

```bash
psql -X "$DATABASE_URL" -v ON_ERROR_STOP=1 <<'SQL'
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'users'
  AND column_name = 'telegram_id';

SELECT indexname
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname = 'uq_users_telegram_id';

SELECT to_regclass('public.import_audit_log');
SQL
```

After backup verification and explicit approval:

```bash
psql -X "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f migrations/001_add_users_telegram_id.sql

psql -X "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f migrations/002_create_import_audit_log.sql
```

Verify:

```bash
psql -X "$DATABASE_URL" -v ON_ERROR_STOP=1 <<'SQL'
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'users' AND column_name = 'telegram_id';

SELECT indexname
FROM pg_indexes
WHERE indexname = 'uq_users_telegram_id';

SELECT to_regclass('public.import_audit_log');

SELECT indexname
FROM pg_indexes
WHERE tablename = 'import_audit_log';
SQL
```
After migration verification, run the complete SELECT-only checker:

~~~bash
python3 scripts/check_staging_environment.py
~~~

If migration 001 succeeds and 002 fails, record the schema state and stop the
application deployment. Do not perform an automatic rollback.

## 7. Install staging service and Nginx samples

Replace only documented placeholders, then inspect diffs:

```bash
sudo install -m 0644 deploy/olimlar-staging.service.example \
  /etc/systemd/system/olimlar-staging.service
sudo install -m 0644 deploy/nginx-staging.conf.example \
  /etc/nginx/sites-available/staging.olimlar.uz
sudo nginx -t
sudo systemctl daemon-reload
```

Do not enable, start, restart, or reload anything until the exact service and
Nginx files have been reviewed. Never modify the production service or site.

## 8. Backend and browser smoke tests

After an approved staging-only start/restart:

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' https://staging.olimlar.uz/
curl -fsS -o /dev/null -w '%{http_code}\n' https://staging.olimlar.uz/login
curl -fsS -o /dev/null -w '%{http_code}\n' https://staging.olimlar.uz/dissertatsiyalar
curl -fsS -o /dev/null -w '%{http_code}\n' https://staging.olimlar.uz/olimlar
curl -fsS -o /dev/null -w '%{http_code}\n' https://staging.olimlar.uz/stats
```

Check `/cabinet`, grants, admin upload authorization, POST logout with CSRF,
CSRF rejection without a token, request IDs, secure cookies, HSTS, CSP,
`X-Content-Type-Options`, `Referrer-Policy`, and HTTP-to-HTTPS redirect.

In a real browser test desktop and 390px layouts, login, cabinet, admin upload,
grant tracking, notification dismissal, console errors, 4xx/5xx responses,
redirect loops, and mixed content. Use dedicated staging test accounts and
marked test data only; skip write tests without an approved cleanup plan.

## 9. Logs and monitoring

Inspect only staging logs:

```bash
sudo journalctl -u olimlar-staging --since "30 minutes ago"
sudo tail -n 200 /var/log/olimlar-staging/app.log
sudo tail -n 200 /var/log/olimlar-staging/error.log
```

Look for uncaught exceptions, CSRF/DB/migration errors, repeated 500s, missing
request IDs, and secret/DSN/token leakage. Never paste secret-bearing log lines
into a ticket or report.

## 10. Rollback

Before deployment, record the current staging SHA:

```bash
git -C /opt/olimlar-staging rev-parse HEAD
```

Application rollback uses a previously reviewed staging SHA and the same
staging-only deploy script. Schema rollback is manual:

- migration 002: `DROP TABLE IF EXISTS import_audit_log`;
- migration 001: drop `uq_users_telegram_id`, then drop `users.telegram_id`.

Dropping `telegram_id` permanently removes Telegram account bindings. Prefer
restoring into a separate verification database first:

```bash
pg_restore --no-owner --no-privileges \
  --dbname="$RESTORE_VERIFICATION_DATABASE_URL" "$BACKUP_FILE"
```

A destructive in-place restore requires separate approval and a maintenance
window:

```bash
pg_restore --clean --if-exists --no-owner --no-privileges \
  --dbname="$DATABASE_URL" "$BACKUP_FILE"
```

Never point either command at production.
