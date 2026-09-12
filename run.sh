#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

fail() {
    printf 'Error: %s\n' "$1" >&2
    exit 1
}

command -v uv >/dev/null 2>&1 || fail "uv is not installed. Install it from https://docs.astral.sh/uv/"
command -v docker >/dev/null 2>&1 || fail "Docker is not installed. Install Docker Desktop."
docker info >/dev/null 2>&1 || fail "Docker is not running. Start Docker Desktop and try again."

if [[ ! -f .env ]]; then
    [[ -f .env.example ]] || fail "Missing both .env and .env.example."
    printf 'Creating .env from .env.example...\n'
    cp .env.example .env
fi

printf 'Starting PostgreSQL...\n'
docker compose up -d --wait

printf 'Syncing Python environment...\n'
uv sync

printf 'Starting API at http://localhost:8000/docs\n'
exec uv run uvicorn app.server:app --reload
