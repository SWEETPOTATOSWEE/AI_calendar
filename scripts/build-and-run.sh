#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="${ROOT_DIR}/frontend-next"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"

echo "Building frontend..."
cd "${FRONTEND_DIR}"
npm run build

echo "Starting backend on ${HOST}:${PORT}..."
cd "${ROOT_DIR}"
if [[ -x "${PYTHON_BIN}" ]]; then
  exec "${PYTHON_BIN}" -m uvicorn backend.app:app --host "${HOST}" --port "${PORT}"
fi
exec python -m uvicorn backend.app:app --host "${HOST}" --port "${PORT}"
