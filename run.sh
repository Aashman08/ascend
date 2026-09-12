#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

fail() {
    printf 'Error: %s\n' "$1" >&2
    exit 1
}

command -v uv >/dev/null 2>&1 || fail "uv is not installed. See https://docs.astral.sh/uv/"
command -v docker >/dev/null 2>&1 || fail "Docker is not installed."
docker info >/dev/null 2>&1 || fail "Docker is not running. Start Docker Desktop and try again."

[[ -f .env ]] || fail "Missing .env. Add DATABASE_URL and TEST_DATABASE_URL first."

printf 'Starting PostgreSQL...\n'
docker compose up -d --wait

printf 'Syncing Python environment...\n'
uv sync

printf 'Starting API at http://localhost:8000/docs\n'
exec uv run uvicorn app.server:app --reload
