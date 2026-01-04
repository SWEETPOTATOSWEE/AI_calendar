#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_HOST="${FRONTEND_HOST:-0.0.0.0}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

cleanup() {
  if [[ -n "${BACKEND_PID:-}" ]]; then
    echo "Stopping backend..."
    kill "${BACKEND_PID}" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

echo "Starting backend on ${BACKEND_HOST}:${BACKEND_PORT}..."
HOST="${BACKEND_HOST}" PORT="${BACKEND_PORT}" "${ROOT_DIR}/scripts/restart-backend.sh" &
BACKEND_PID=$!

echo "Starting frontend on ${FRONTEND_HOST}:${FRONTEND_PORT}..."
HOST="${FRONTEND_HOST}" PORT="${FRONTEND_PORT}" "${ROOT_DIR}/scripts/restart-frontend.sh"
