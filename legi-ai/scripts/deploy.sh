#!/usr/bin/env bash
# Deploy legi-ai via docker compose (prod).
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env.prod ]; then
    echo "[deploy] .env.prod missing — copy from .env.prod.example and fill keys"
    exit 1
fi

echo "[deploy] building images"
docker compose -f docker-compose.prod.yml build

echo "[deploy] starting stack"
docker compose -f docker-compose.prod.yml up -d

echo "[deploy] waiting for healthcheck"
for i in {1..30}; do
    if docker compose -f docker-compose.prod.yml exec -T legi-ai-api \
        curl -sf http://localhost:8000/health >/dev/null 2>&1; then
        echo "[deploy] healthy"
        break
    fi
    sleep 2
done

docker compose -f docker-compose.prod.yml ps
