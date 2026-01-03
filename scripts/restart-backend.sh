#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

find_listening_pids() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true
    return
  fi
  if command -v fuser >/dev/null 2>&1; then
    fuser -n tcp "${PORT}" 2>/dev/null || true
    return
  fi
  pgrep -f "uvicorn backend.app:app" || true
}

PIDS="$(find_listening_pids)"
if [[ -n "${PIDS}" ]]; then
  echo "Stopping processes on port ${PORT} (pid: ${PIDS})..."
  kill ${PIDS} || true
  sleep 1
fi

echo "Starting backend on ${HOST}:${PORT}..."
exec python -m uvicorn backend.app:app --host "${HOST}" --port "${PORT}"
