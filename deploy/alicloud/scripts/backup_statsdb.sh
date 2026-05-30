#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/sydney-fishing}
BACKUP_DIR=${BACKUP_DIR:-/opt/sydney-fishing/backups}
DATA_DIR=${DATA_DIR:-$APP_DIR/data}
DB_FILE="$DATA_DIR/stats.db"

mkdir -p "$BACKUP_DIR"

if [ -f "$DB_FILE" ]; then
  ts=$(date +%Y%m%d-%H%M%S)
  cp "$DB_FILE" "$BACKUP_DIR/stats-$ts.db"
  find "$BACKUP_DIR" -type f -name 'stats-*.db' -mtime +14 -delete
  echo "Backup done: $BACKUP_DIR/stats-$ts.db"
else
  echo "No db found at $DB_FILE"
fi
