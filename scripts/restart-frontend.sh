#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="${ROOT_DIR}/frontend-next"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-3000}"

find_listening_pids() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true
    return
  fi
  if command -v fuser >/dev/null 2>&1; then
    fuser -n tcp "${PORT}" 2>/dev/null || true
    return
  fi
  pgrep -f "next dev" || true
}

PIDS="$(find_listening_pids)"
if [[ -n "${PIDS}" ]]; then
  echo "Stopping processes on port ${PORT} (pid: ${PIDS})..."
  kill ${PIDS} || true
  sleep 1
fi

echo "Starting frontend on ${HOST}:${PORT}..."
cd "${FRONTEND_DIR}"
exec npm run dev -- --hostname "${HOST}" --port "${PORT}"
