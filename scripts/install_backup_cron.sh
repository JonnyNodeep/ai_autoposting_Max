#!/bin/bash
# Install nightly full backup cron (run once on the host as root).
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRON_LINE="0 3 * * * cd $PROJECT_ROOT && ./scripts/backup_full.sh >> /var/log/maxstudio-backup.log 2>&1"

touch /var/log/maxstudio-backup.log
chmod 644 /var/log/maxstudio-backup.log

( crontab -l 2>/dev/null | grep -v 'backup_full.sh' ; echo "$CRON_LINE" ) | crontab -

echo "Installed cron:"
crontab -l | grep backup_full.sh
