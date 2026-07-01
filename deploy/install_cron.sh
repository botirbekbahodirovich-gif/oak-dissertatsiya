#!/usr/bin/env bash
#
# Fix 5.5: install the daily OAK scraper into the host's cron daemon.
# Runs scripts/daily_scraper.py every midnight (0 0 * * *).
#
# Usage:  sudo bash deploy/install_cron.sh
#
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/olimlar-uz}"
CRON_DST="/etc/cron.d/oak-scraper"
DEPLOY_USER="${SUDO_USER:-$(id -un)}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo: sudo bash deploy/install_cron.sh" >&2
  exit 1
fi

# Pull DATABASE_URL from the app .env so the scraper can connect.
DB_URL=""
if [ -f "$APP_DIR/.env" ]; then
  DB_URL="$(grep -E '^DATABASE_URL=' "$APP_DIR/.env" | head -1 | cut -d= -f2-)"
fi

install -d -m 755 "$(dirname "$CRON_DST")"
cat > "$CRON_DST" <<CRON
# OAK registry daily scraper — midnight execution (managed by install_cron.sh).
SHELL=/bin/bash
DATABASE_URL=${DB_URL}
0 0 * * * ${DEPLOY_USER} cd ${APP_DIR} && ${APP_DIR}/venv/bin/python scripts/daily_scraper.py >> /var/log/oak-scraper.log 2>&1
CRON
chmod 644 "$CRON_DST"

# Grant scraper — daily at 01:00 (offset from the OAK scraper).
GRANT_DST="/etc/cron.d/grant-scraper"
cat > "$GRANT_DST" <<CRON
# Scientific grant scraper — daily 01:00 (managed by install_cron.sh).
SHELL=/bin/bash
DATABASE_URL=${DB_URL}
0 1 * * * ${DEPLOY_USER} cd ${APP_DIR} && ${APP_DIR}/venv/bin/python scripts/grant_scraper.py >> /var/log/grant-scraper.log 2>&1
CRON
chmod 644 "$GRANT_DST"

touch /var/log/oak-scraper.log /var/log/grant-scraper.log
chown "$DEPLOY_USER":"$DEPLOY_USER" /var/log/oak-scraper.log /var/log/grant-scraper.log || true

# Reload cron so the new job is picked up immediately.
service cron reload 2>/dev/null || systemctl reload cron 2>/dev/null || true

echo "Installed $CRON_DST (daily 00:00). Log: /var/log/oak-scraper.log"
echo "Installed $GRANT_DST (daily 01:00). Log: /var/log/grant-scraper.log"
