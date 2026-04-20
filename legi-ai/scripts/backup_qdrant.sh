#!/usr/bin/env bash
# Snapshot Qdrant + archive logs. Intended for daily cron.
set -euo pipefail

cd "$(dirname "$0")/.."

DATE=$(date -u +"%Y%m%d")
BACKUP_ROOT="${BACKUP_ROOT:-./backups}"
mkdir -p "$BACKUP_ROOT"

COLLECTION="${QDRANT_COLLECTION:-legi_documents}"

echo "[backup] creating qdrant snapshot for $COLLECTION"
SNAPSHOT=$(curl -sS -X POST "http://localhost:6333/collections/$COLLECTION/snapshots" | python -c 'import sys,json;print(json.load(sys.stdin)["result"]["name"])')
echo "[backup] snapshot: $SNAPSHOT"

curl -sS "http://localhost:6333/collections/$COLLECTION/snapshots/$SNAPSHOT" \
    -o "$BACKUP_ROOT/qdrant_${COLLECTION}_${DATE}.snapshot"

if [ -d data/logs ]; then
    tar czf "$BACKUP_ROOT/logs_${DATE}.tar.gz" data/logs
fi

find "$BACKUP_ROOT" -name "qdrant_*.snapshot" -mtime +30 -delete
find "$BACKUP_ROOT" -name "logs_*.tar.gz" -mtime +90 -delete

echo "[backup] done"
