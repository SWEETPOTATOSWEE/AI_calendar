#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="${ROOT_DIR}/frontend-next"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

echo "Building frontend..."
cd "${FRONTEND_DIR}"
npm run build

echo "Starting backend on ${HOST}:${PORT}..."
cd "${ROOT_DIR}"
python -m uvicorn backend.app:app --host "${HOST}" --port "${PORT}"
