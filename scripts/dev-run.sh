#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "${ROOT_DIR}/.env"
  set +a
fi

# Codespaces 환경 자동 감지 및 환경 변수 자동 설정
if [[ -n "${CODESPACE_NAME:-}" ]] && [[ -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]]; then
  # Codespaces 환경
  export BACKEND_PUBLIC_BASE="${BACKEND_PUBLIC_BASE:-https://${CODESPACE_NAME}-${BACKEND_PORT}.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}}"
  export FRONTEND_BASE_URL="${FRONTEND_BASE_URL:-https://${CODESPACE_NAME}-${FRONTEND_PORT}.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}}"
  export COOKIE_SECURE="${COOKIE_SECURE:-1}"
  export CORS_ALLOW_ORIGINS="${CORS_ALLOW_ORIGINS:-https://${CODESPACE_NAME}-${FRONTEND_PORT}.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}}"
else
  # 로컬 환경
  export BACKEND_PUBLIC_BASE="${BACKEND_PUBLIC_BASE:-http://127.0.0.1:${BACKEND_PORT}}"
  export FRONTEND_BASE_URL="${FRONTEND_BASE_URL:-http://127.0.0.1:${FRONTEND_PORT}}"
  export COOKIE_SECURE="${COOKIE_SECURE:-0}"
  export CORS_ALLOW_ORIGINS="${CORS_ALLOW_ORIGINS:-http://127.0.0.1:${FRONTEND_PORT}}"
fi

# URL 기반 자동 설정 (Codespaces/로컬 공통)
export GOOGLE_REDIRECT_URI="${GOOGLE_REDIRECT_URI:-${FRONTEND_BASE_URL}/auth/google/callback}"
export GOOGLE_WEBHOOK_URL="${GOOGLE_WEBHOOK_URL:-${BACKEND_PUBLIC_BASE}/auth/google/webhook}"
export NEXT_PUBLIC_API_BASE="${NEXT_PUBLIC_API_BASE:-/api}"
export BACKEND_INTERNAL_URL="${BACKEND_INTERNAL_URL:-http://127.0.0.1:${BACKEND_PORT}}"
export NEXT_PUBLIC_BACKEND_DIRECT="${BACKEND_PUBLIC_BASE}"

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

cleanup() {
  if [[ -n "${BACKEND_PID:-}" ]]; then
    echo "Stopping backend..."
    kill "${BACKEND_PID}" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

echo "Starting backend on ${BACKEND_HOST}:${BACKEND_PORT}..."
HOST="${BACKEND_HOST}" PORT="${BACKEND_PORT}" \
  FRONTEND_BASE_URL="${FRONTEND_BASE_URL}" \
  "${ROOT_DIR}/scripts/restart-backend.sh" &
BACKEND_PID=$!

# Fail fast if backend exits right after launch.
sleep 2
if ! kill -0 "${BACKEND_PID}" 2>/dev/null; then
  echo "Backend failed to start. Check backend logs above."
  wait "${BACKEND_PID}" || true
  exit 1
fi

echo "Starting frontend on ${FRONTEND_HOST}:${FRONTEND_PORT}..."
HOST="${FRONTEND_HOST}" PORT="${FRONTEND_PORT}" \
  BACKEND_BASE_URL="${BACKEND_PUBLIC_BASE}" \
  "${ROOT_DIR}/scripts/restart-frontend.sh"
