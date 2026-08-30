#!/bin/bash
# Full snapshot: Postgres + uploads + Redis + manifest.
# Copies to /var/backups/ai-content-studio/ and rotates last 7 snapshots.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SNAPSHOT_NAME="snapshot_${TIMESTAMP}"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups}"
SNAPSHOT_PATH="$BACKUP_DIR/$SNAPSHOT_NAME"
REMOTE_BACKUP_DIR="${REMOTE_BACKUP_DIR:-/var/backups/ai-content-studio}"
KEEP_SNAPSHOTS="${KEEP_SNAPSHOTS:-7}"

DB_NAME="${POSTGRES_DB:-maxstudio}"
DB_USER="${POSTGRES_USER:-maxstudio}"

mkdir -p "$SNAPSHOT_PATH"

echo "==> Snapshot: $SNAPSHOT_PATH"

echo "==> Postgres dump..."
docker compose exec -T postgres pg_dump \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    --no-owner \
    --no-acl \
    | gzip > "$SNAPSHOT_PATH/postgres.sql.gz"

echo "==> Uploads archive..."
tar -czf "$SNAPSHOT_PATH/uploads.tar.gz" uploads/

echo "==> Redis RDB..."
docker compose exec -T redis redis-cli BGSAVE >/dev/null
sleep 1
REDIS_CID=$(docker compose ps -q redis)
docker cp "${REDIS_CID}:/data/dump.rdb" "$SNAPSHOT_PATH/redis.rdb" 2>/dev/null || \
    docker compose exec -T redis redis-cli --rdb /data/backup.rdb >/dev/null && \
    docker cp "${REDIS_CID}:/data/backup.rdb" "$SNAPSHOT_PATH/redis.rdb" || \
    echo "warning: redis dump skipped"

CHANNELS=$(docker compose exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT COUNT(*) FROM channels;" | tr -d '[:space:]')
ACTIVE_PIPELINES=$(docker compose exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT COUNT(*) FROM pipeline_runs WHERE status='active';" | tr -d '[:space:]')
PIPELINE_RUNS=$(docker compose exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT COUNT(*) FROM pipeline_runs;" | tr -d '[:space:]')
UPLOADS_BYTES=$(du -sb uploads 2>/dev/null | cut -f1 || echo 0)

cat > "$SNAPSHOT_PATH/manifest.json" <<EOF
{
  "timestamp": "$TIMESTAMP",
  "channels": $CHANNELS,
  "active_pipelines": $ACTIVE_PIPELINES,
  "pipeline_runs": $PIPELINE_RUNS,
  "uploads_bytes": $UPLOADS_BYTES
}
EOF

echo "==> Copy to $REMOTE_BACKUP_DIR ..."
mkdir -p "$REMOTE_BACKUP_DIR"
cp -a "$SNAPSHOT_PATH" "$REMOTE_BACKUP_DIR/"

_rotate() {
    local dir="$1"
  ls -1dt "$dir"/snapshot_* 2>/dev/null | tail -n +$((KEEP_SNAPSHOTS + 1)) | xargs -r rm -rf
}

_rotate "$BACKUP_DIR"
_rotate "$REMOTE_BACKUP_DIR"

echo "==> Done: $SNAPSHOT_PATH"
du -sh "$SNAPSHOT_PATH"/* | sed 's/^/    /'
