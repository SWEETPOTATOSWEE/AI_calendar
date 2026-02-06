from __future__ import annotations

import os
import pathlib
import re
from zoneinfo import ZoneInfo

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SEOUL = ZoneInfo("Asia/Seoul")
LLM_DEBUG = os.getenv("LLM_DEBUG", "0") == "1"

ISO_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ISO_DATETIME_24_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T24:00$")
DATETIME_FLEX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::\d{2})?$")

# -------------------------
# Google Calendar 설정
# -------------------------
ENABLE_GCAL = os.getenv("ENABLE_GCAL", "0") == "1"
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
GCAL_SCOPES = [
    "openid",
    "profile",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/tasks",
]


BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
GOOGLE_TOKEN_DIR = pathlib.Path(
    os.getenv("GOOGLE_TOKEN_DIR", str(BASE_DIR / "gcal_tokens")))
GOOGLE_WEBHOOK_URL = os.getenv("GOOGLE_WEBHOOK_URL", "").strip()
GOOGLE_WEBHOOK_TOKEN = os.getenv("GOOGLE_WEBHOOK_TOKEN", "").strip()
GCAL_WATCH_STATE_PATH = pathlib.Path(
    os.getenv("GCAL_WATCH_STATE_PATH", str(GOOGLE_TOKEN_DIR / "gcal_watch_state.json")))
GCAL_WATCH_LEEWAY_SECONDS = int(
    os.getenv("GCAL_WATCH_LEEWAY_SECONDS", "3600"))
SESSION_COOKIE_NAME = "gcal_session"
OAUTH_STATE_COOKIE_NAME = "gcal_oauth_state"
SESSION_COOKIE_MAX_AGE_SECONDS = int(
    os.getenv("GCAL_SESSION_MAX_AGE_SECONDS", str(60 * 60 * 24 * 30)))
OAUTH_STATE_MAX_AGE_SECONDS = int(
    os.getenv("GCAL_OAUTH_STATE_MAX_AGE_SECONDS", "600"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"
API_BASE = os.getenv("API_BASE", "/api")
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "").rstrip("/")
NEXT_FRONTEND_DIR = BASE_DIR / "frontend-next" / "out"
LEGACY_FRONTEND_DIR = BASE_DIR / "frontend"
USE_NEXT_FRONTEND = (NEXT_FRONTEND_DIR / "index.html").exists()
FRONTEND_DIR = NEXT_FRONTEND_DIR if USE_NEXT_FRONTEND else LEGACY_FRONTEND_DIR
FRONTEND_STATIC_DIR = NEXT_FRONTEND_DIR if USE_NEXT_FRONTEND else None
EVENTS_DATA_FILE = pathlib.Path(
    os.getenv("EVENTS_DATA_FILE", str(BASE_DIR / "events_data.json")))

CORS_ALLOW_ORIGINS = os.getenv("CORS_ALLOW_ORIGINS", "")
CORS_ALLOW_ORIGIN_REGEX = os.getenv("CORS_ALLOW_ORIGIN_REGEX", "").strip()
cors_origins: list[str] = []
if FRONTEND_BASE_URL:
    cors_origins.append(FRONTEND_BASE_URL)
if CORS_ALLOW_ORIGINS:
    cors_origins.extend(
        [origin.strip() for origin in CORS_ALLOW_ORIGINS.split(",") if origin.strip()])

if not CORS_ALLOW_ORIGIN_REGEX:
    codespaces_domain = os.getenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "").strip()
    if codespaces_domain:
        CORS_ALLOW_ORIGIN_REGEX = rf"^https://.*\.{re.escape(codespaces_domain)}$"

# -------------------------
# 런타임 제한/기본값
# -------------------------
UNDO_RETENTION_DAYS = 14
GOOGLE_RECENT_DAYS = 14
MAX_SCOPE_DAYS = 365
MAX_CONTEXT_DAYS = 180
DEFAULT_CONTEXT_DAYS = 120
MAX_CONTEXT_EVENTS = 200
MAX_CONTEXT_SLICES = 4
MAX_CONTEXT_DATES = 8
MAX_RECURRENCE_EXPANSION_DAYS = 365
MAX_RECURRENCE_OCCURRENCES = 400
RECURRENCE_OCCURRENCE_SCALE = 10000
MAX_IMAGE_ATTACHMENTS = 5
MAX_IMAGE_DATA_URL_CHARS = 4_500_000  # 약 3.4MB base64
IMAGE_TOO_LARGE_MESSAGE = "첨부한 이미지가 너무 큽니다. 이미지는 약 3MB 이하로 축소해 주세요."
ALLOWED_REASONING_EFFORTS = {"low", "medium", "high"}
ALLOWED_ASSISTANT_MODELS = {"nano": "gpt-5-nano", "mini": "gpt-5-mini"}
DEFAULT_TEXT_MODEL = "gpt-5-nano"
DEFAULT_MULTIMODAL_MODEL = "gpt-5-mini"
DEFAULT_TEXT_REASONING_EFFORT = "low"
DEFAULT_MULTIMODAL_REASONING_EFFORT = "medium"

USD_TO_KRW = 1450.0
MODEL_PRICING = {
    "gpt-5-nano": {
        "input_per_m": 0.05,
        "cached_input_per_m": 0.01,
        "output_per_m": 0.4,
    },
    "gpt-5-mini": {
        "input_per_m": 0.25,
        "cached_input_per_m": 0.03,
        "output_per_m": 2.0,
    },
}
