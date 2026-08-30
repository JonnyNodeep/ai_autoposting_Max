#!/bin/bash
# Restore from backup_full.sh snapshot directory.
# Usage: restore_full.sh backups/snapshot_YYYYMMDD_HHMMSS
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: restore_full.sh <snapshot_directory>"
    echo "Example: restore_full.sh backups/snapshot_20260812_120000"
    ls -1dt ./backups/snapshot_* 2>/dev/null | head -5
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

SNAPSHOT="$(cd "$1" && pwd)"
DB_NAME="${POSTGRES_DB:-maxstudio}"
DB_USER="${POSTGRES_USER:-maxstudio}"

for f in postgres.sql.gz uploads.tar.gz; do
    if [ ! -f "$SNAPSHOT/$f" ]; then
        echo "Missing required file: $SNAPSHOT/$f"
        exit 1
    fi
done

echo "==> Stopping compose (volumes kept)..."
docker compose down

echo "==> Starting postgres + redis..."
docker compose up -d postgres redis
sleep 3

echo "==> Restoring Postgres..."
gunzip -c "$SNAPSHOT/postgres.sql.gz" | docker compose exec -T postgres psql -U "$DB_USER" -d "$DB_NAME"

echo "==> Restoring uploads..."
tar -xzf "$SNAPSHOT/uploads.tar.gz" -C "$PROJECT_ROOT"

if [ -f "$SNAPSHOT/redis.rdb" ]; then
    echo "==> Restoring Redis (optional)..."
    docker compose stop redis
    REDIS_VOL=$(docker volume inspect ai_content_studio_for_max_redis_data --format '{{ .Mountpoint }}' 2>/dev/null || true)
    if [ -n "$REDIS_VOL" ] && [ -d "$REDIS_VOL" ]; then
        cp "$SNAPSHOT/redis.rdb" "$REDIS_VOL/dump.rdb"
        echo "    redis dump copied to volume"
    else
        echo "    warning: redis volume not found, skip redis restore"
    fi
fi

echo "==> Starting all services..."
docker compose up -d

echo "==> Restore complete. Verify health and restart pipelines if needed:"
echo "    curl http://localhost:8001/health"
echo "    docker compose exec -T app python /app/scripts/restart_pipelines.py"
