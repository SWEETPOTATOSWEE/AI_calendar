#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="${ROOT_DIR}/frontend-next"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]] && [[ -x "${ROOT_DIR}/.venv/Scripts/python.exe" ]]; then
  PYTHON_BIN="${ROOT_DIR}/.venv/Scripts/python.exe"
fi

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "${ROOT_DIR}/.env"
  set +a
fi

# Codespaces 환경 자동 감지 및 환경 변수 자동 설정
if [[ -n "${CODESPACE_NAME:-}" ]] && [[ -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]]; then
  # Codespaces 환경
  export BACKEND_PUBLIC_BASE="${BACKEND_PUBLIC_BASE:-https://${CODESPACE_NAME}-${PORT}.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}}"
  export FRONTEND_BASE_URL="${FRONTEND_BASE_URL:-https://${CODESPACE_NAME}-${PORT}.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}}"
  export COOKIE_SECURE="${COOKIE_SECURE:-1}"
  export CORS_ALLOW_ORIGINS="${CORS_ALLOW_ORIGINS:-https://${CODESPACE_NAME}-${PORT}.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}}"
else
  # 로컬 환경
  export BACKEND_PUBLIC_BASE="${BACKEND_PUBLIC_BASE:-http://127.0.0.1:${PORT}}"
  export FRONTEND_BASE_URL="${FRONTEND_BASE_URL:-http://127.0.0.1:${PORT}}"
  export COOKIE_SECURE="${COOKIE_SECURE:-0}"
  export CORS_ALLOW_ORIGINS="${CORS_ALLOW_ORIGINS:-http://127.0.0.1:${PORT}}"
fi

# URL 기반 자동 설정 (Codespaces/로컬 공통)
export GOOGLE_REDIRECT_URI="${GOOGLE_REDIRECT_URI:-${FRONTEND_BASE_URL}/auth/google/callback}"
export GOOGLE_WEBHOOK_URL="${GOOGLE_WEBHOOK_URL:-${BACKEND_PUBLIC_BASE}/auth/google/webhook}"
export NEXT_PUBLIC_API_BASE="${NEXT_PUBLIC_API_BASE:-/api}"
export BACKEND_INTERNAL_URL="${BACKEND_INTERNAL_URL:-http://127.0.0.1:${PORT}}"
# 빌드 시에는 상대 경로를 사용하도록 비워둠 (동일 포트 서빙)
export NEXT_PUBLIC_BACKEND_DIRECT=""

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

echo "Building frontend..."
cd "${FRONTEND_DIR}"
npm run build

echo "Starting backend on ${HOST}:${PORT}..."
cd "${ROOT_DIR}"
if [[ -x "${PYTHON_BIN}" ]]; then
  exec "${PYTHON_BIN}" -m uvicorn backend.app:app --host "${HOST}" --port "${PORT}"
fi
exec python -m uvicorn backend.app:app --host "${HOST}" --port "${PORT}"
