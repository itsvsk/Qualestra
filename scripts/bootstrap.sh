#!/usr/bin/env sh
set -eu
uv sync --locked --extra dev
pnpm install --frozen-lockfile
docker compose up --build --wait
