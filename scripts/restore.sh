#!/bin/bash
set -e

if [ -z "$1" ]; then
    echo "Usage: restore.sh <backup_file.sql.gz>"
    ls -1t ./backups/backup_*.sql.gz 2>/dev/null | head -5
    exit 1
fi

BACKUP_FILE="$1"
DB_NAME="${POSTGRES_DB:-maxstudio}"
DB_USER="${POSTGRES_USER:-maxstudio}"
DB_HOST="${POSTGRES_HOST:-postgres}"
DB_PORT="${POSTGRES_PORT:-5432}"

echo "Restoring $DB_NAME from $BACKUP_FILE..."

gunzip -c "$BACKUP_FILE" | PGPASSWORD="${POSTGRES_PASSWORD:-maxstudio}" psql \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$DB_NAME"

echo "Restore complete!"
