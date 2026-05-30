#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/sydney-fishing}
SCRIPT="$APP_DIR/deploy/alicloud/scripts/backup_statsdb.sh"
CRON='0 3 * * * APP_DIR=/opt/sydney-fishing /opt/sydney-fishing/deploy/alicloud/scripts/backup_statsdb.sh >> /var/log/sydney-fishing-backup.log 2>&1'

( crontab -l 2>/dev/null | grep -v 'sydney-fishing/deploy/alicloud/scripts/backup_statsdb.sh' ; echo "$CRON" ) | crontab -

echo "Installed cron backup at 03:00 daily."
