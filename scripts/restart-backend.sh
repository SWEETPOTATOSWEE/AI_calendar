#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
PYTHON_BIN=""
if [[ -f "${ROOT_DIR}/.venv/Scripts/python.exe" ]]; then
  PYTHON_BIN="${ROOT_DIR}/.venv/Scripts/python.exe"
elif [[ -f "${ROOT_DIR}/.venv/bin/python" ]]; then
  PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python)"
fi
BACKEND_PUBLIC_BASE="${BACKEND_PUBLIC_BASE:-}"
FRONTEND_BASE_URL="${FRONTEND_BASE_URL:-}"

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "${ROOT_DIR}/.env"
  set +a
fi

# Codespaces 환경 자동 감지 및 환경 변수 자동 설정
if [[ -z "${FRONTEND_BASE_URL}" ]] && [[ -n "${CODESPACE_NAME:-}" ]] && [[ -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]]; then
  export BACKEND_PUBLIC_BASE="${BACKEND_PUBLIC_BASE:-https://${CODESPACE_NAME}-${PORT}.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}}"
  export FRONTEND_BASE_URL="${FRONTEND_BASE_URL:-https://${CODESPACE_NAME}-3000.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}}"
  export COOKIE_SECURE="${COOKIE_SECURE:-1}"
  export CORS_ALLOW_ORIGINS="${CORS_ALLOW_ORIGINS:-https://${CODESPACE_NAME}-3000.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}}"
elif [[ -z "${FRONTEND_BASE_URL}" ]]; then
  export BACKEND_PUBLIC_BASE="${BACKEND_PUBLIC_BASE:-http://127.0.0.1:${PORT}}"
  export FRONTEND_BASE_URL="${FRONTEND_BASE_URL:-http://127.0.0.1:3000}"
  export COOKIE_SECURE="${COOKIE_SECURE:-0}"
  export CORS_ALLOW_ORIGINS="${CORS_ALLOW_ORIGINS:-http://127.0.0.1:3000}"
fi

# URL 기반 자동 설정 (Codespaces/로컬 공통)
export GOOGLE_REDIRECT_URI="${GOOGLE_REDIRECT_URI:-${FRONTEND_BASE_URL}/auth/google/callback}"
export GOOGLE_WEBHOOK_URL="${GOOGLE_WEBHOOK_URL:-${BACKEND_PUBLIC_BASE}/auth/google/webhook}"

# 기본값 설정
export ENABLE_GCAL="${ENABLE_GCAL:-1}"
export LLM_DEBUG="${LLM_DEBUG:-0}"
export GOOGLE_TOKEN_FILE="${GOOGLE_TOKEN_FILE:-${ROOT_DIR}/backend/.gcal_tokens}"
export GOOGLE_CALENDAR_ID="${GOOGLE_CALENDAR_ID:-primary}"

echo "=== Environment Variables ==="
echo "BACKEND_PUBLIC_BASE: ${BACKEND_PUBLIC_BASE}"
echo "FRONTEND_BASE_URL: ${FRONTEND_BASE_URL}"
echo "GOOGLE_REDIRECT_URI: ${GOOGLE_REDIRECT_URI}"
echo "COOKIE_SECURE: ${COOKIE_SECURE}"
echo "============================="

find_listening_pids() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true
    return
  fi
  if command -v fuser >/dev/null 2>&1; then
    fuser -n tcp "${PORT}" 2>/dev/null || true
    return
  fi
  if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -Command \
      "Get-NetTCPConnection -LocalPort ${PORT} -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique" 2>/dev/null \
      | tr -d '\r' \
      | awk '/^[0-9]+$/ {print}' || true
    return
  fi
  if command -v netstat >/dev/null 2>&1; then
    netstat -ano -p tcp 2>/dev/null \
      | grep -E "[:.]${PORT}[[:space:]].*LISTEN" \
      | awk '{print $NF}' \
      | awk '/^[0-9]+$/ {print}' \
      | sort -u || true
    return
  fi
  if command -v pgrep >/dev/null 2>&1; then
    pgrep -f "uvicorn backend.app:app" || true
  fi
}

stop_pids() {
  local pids_text="$1"
  if [[ -z "${pids_text}" ]]; then
    return
  fi

  if command -v taskkill.exe >/dev/null 2>&1; then
    while IFS= read -r pid; do
      if [[ -z "${pid}" ]]; then
        continue
      fi
      taskkill.exe //PID "${pid}" //F >/dev/null 2>&1 || true
    done <<< "${pids_text}"
    return
  fi

  # shellcheck disable=SC2086
  kill ${pids_text} 2>/dev/null || true
}

PIDS="$(find_listening_pids)"
if [[ -n "${PIDS}" ]]; then
  echo "Stopping processes on port ${PORT} (pid: ${PIDS})..."
  stop_pids "${PIDS}"
  sleep 1
fi

echo "Starting backend on ${HOST}:${PORT}..."
if [[ -n "${PYTHON_BIN}" ]]; then
  echo "Using Python: ${PYTHON_BIN}"
  exec "${PYTHON_BIN}" -m uvicorn backend.app:app --host "${HOST}" --port "${PORT}"
fi
echo "No Python interpreter found. Backend cannot start." >&2
exit 1
