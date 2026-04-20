#!/usr/bin/env bash
# Bootstrap legi-ai development environment.
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v uv &>/dev/null; then
    echo "[setup] installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

echo "[setup] syncing dependencies"
uv sync --all-extras

if [ ! -f .env ]; then
    cp .env.example .env
    echo "[setup] created .env — please fill in API keys"
fi

echo "[setup] running smoke tests"
uv run pytest tests/test_smoke.py -q

echo "[setup] done"
