#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -d .venv ]]; then
  uv sync --frozen
fi

docker compose up -d --wait postgres redis
uv run python manage.py migrate --noinput
uv run python manage.py check
