#!/usr/bin/env bash
# Google OAuth Redirect URI Check Script

set -euo pipefail

FRONTEND_PORT="${FRONTEND_PORT:-3000}"

if [[ -n "${CODESPACE_NAME:-}" ]] && [[ -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]]; then
  REDIRECT_URI="https://${CODESPACE_NAME}-${FRONTEND_PORT}.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}/auth/google/callback"
  
  echo "=========================================="
  echo "Current Codespace OAuth Redirect URI"
  echo "=========================================="
  echo ""
  echo "${REDIRECT_URI}"
  echo ""
  echo "=========================================="
  echo "How to Configure Google Cloud Console"
  echo "=========================================="
  echo ""
  echo "1. Go to https://console.cloud.google.com/"
  echo "2. Select APIs & Services → Credentials"
  echo "3. Edit your OAuth 2.0 Client ID"
  echo "4. Add the above URL to Authorized redirect URIs"
  echo "5. Save and wait about 5 minutes"
  echo ""
  echo "See GOOGLE_CLOUD_SETUP.md for more details"
  echo ""
else
  REDIRECT_URI="http://127.0.0.1:${FRONTEND_PORT}/auth/google/callback"
  
  echo "=========================================="
  echo "Local Environment OAuth Redirect URI"
  echo "=========================================="
  echo ""
  echo "${REDIRECT_URI}"
  echo ""
  echo "=========================================="
fi
