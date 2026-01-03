#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8000}"

echo "Freeing port ${PORT} and restarting backend..."
PORT="${PORT}" "${ROOT_DIR}/scripts/restart-backend.sh"
