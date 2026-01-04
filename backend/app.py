from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request, Query, Response
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Tuple, Union
import asyncio
import os
import json
import calendar
import re
import time
import pathlib
import urllib.parse
import copy
import hashlib
import secrets

import requests
from openai import AsyncOpenAI
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = FastAPI()

import os
print("OPENAI_API_KEY:", bool(os.getenv("OPENAI_API_KEY")))
print("ENABLE_GCAL:", os.getenv("ENABLE_GCAL"))
print("GOOGLE_CLIENT_ID:", bool(os.getenv("GOOGLE_CLIENT_ID")))
print("GOOGLE_REDIRECT_URI:", os.getenv("GOOGLE_REDIRECT_URI"))
print("PWD:", __import__("os").getcwd())

# -------------------------
# OpenAI Client
# -------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
async_client: Optional[AsyncOpenAI] = AsyncOpenAI(
    api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

SEOUL = ZoneInfo("Asia/Seoul")
LLM_DEBUG = os.getenv("LLM_DEBUG", "0") == "1"

ISO_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ISO_DATETIME_24_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T24:00$")
DATETIME_FLEX_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::\d{2})?$")


def _log_debug(message: str) -> None:
  if LLM_DEBUG:
    print(message, flush=True)


def _now_iso_minute() -> str:
  return datetime.now(SEOUL).strftime("%Y-%m-%dT%H:%M")

# -------------------------
# Google Calendar 설정
# -------------------------
ENABLE_GCAL = os.getenv("ENABLE_GCAL", "0") == "1"
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
GCAL_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

ADMIN_COOKIE_NAME = "admin"
ADMIN_COOKIE_VALUE = "1"

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
GOOGLE_TOKEN_DIR = pathlib.Path(
    os.getenv("GOOGLE_TOKEN_DIR", str(BASE_DIR / "gcal_tokens")))
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


def _load_frontend_html(filename: str) -> str:
  path = FRONTEND_DIR / filename
  try:
    return path.read_text(encoding="utf-8")
  except FileNotFoundError as exc:
    raise RuntimeError(f"Front-end file not found: {path}") from exc


if USE_NEXT_FRONTEND:
  START_HTML = _load_frontend_html("index.html")
  CALENDAR_HTML_TEMPLATE = _load_frontend_html("calendar/index.html")
  SETTINGS_HTML = _load_frontend_html("settings/index.html")
  LOGIN_HTML = _load_frontend_html("login/index.html")
else:
  START_HTML = _load_frontend_html("start.html")
  CALENDAR_HTML_TEMPLATE = _load_frontend_html("calendar.html")
  SETTINGS_HTML = _load_frontend_html("settings.html")
  LOGIN_HTML = START_HTML

# -------------------------
# 데이터 모델
# -------------------------
class Event(BaseModel):
  id: int
  title: str
  start: str  # "YYYY-MM-DDTHH:MM"
  end: Optional[str] = None
  location: Optional[str] = None
  description: Optional[str] = None
  attendees: Optional[List[str]] = None
  reminders: Optional[List[int]] = None
  visibility: Optional[str] = None
  transparency: Optional[str] = None
  meeting_url: Optional[str] = None
  color_id: Optional[str] = None
  recur: Optional[str] = None
  google_event_id: Optional[str] = None
  all_day: bool = False
  created_at: Optional[str] = None
  start_date: Optional[str] = None
  time: Optional[str] = None
  duration_minutes: Optional[int] = None
  recurrence: Optional[Dict[str, Any]] = None
  timezone: Optional[str] = "Asia/Seoul"


class EventCreate(BaseModel):
  title: str
  start: str
  end: Optional[str] = None
  location: Optional[str] = None
  description: Optional[str] = None
  attendees: Optional[List[str]] = None
  reminders: Optional[List[int]] = None
  visibility: Optional[str] = None
  transparency: Optional[str] = None
  meeting_url: Optional[str] = None
  timezone: Optional[str] = None
  color_id: Optional[str] = None
  recur: Optional[str] = None
  google_event_id: Optional[str] = None
  all_day: Optional[bool] = None
  created_at: Optional[str] = None


class EventUpdate(BaseModel):
  title: Optional[str] = None
  start: Optional[str] = None
  end: Optional[str] = None
  location: Optional[str] = None
  description: Optional[str] = None
  attendees: Optional[List[str]] = None
  reminders: Optional[List[int]] = None
  visibility: Optional[str] = None
  transparency: Optional[str] = None
  meeting_url: Optional[str] = None
  timezone: Optional[str] = None
  color_id: Optional[str] = None
  all_day: Optional[bool] = None


class NaturalText(BaseModel):
  text: str
  images: Optional[List[str]] = None
  request_id: Optional[str] = None
  reasoning_effort: Optional[str] = None
  model: Optional[str] = None


class NaturalTextWithScope(BaseModel):
  text: str
  start_date: Optional[str] = None
  end_date: Optional[str] = None
  images: Optional[List[str]] = None
  request_id: Optional[str] = None
  reasoning_effort: Optional[str] = None
  model: Optional[str] = None


class DeleteResult(BaseModel):
  ok: bool
  deleted_ids: List[Union[int, str]]
  count: int


class ApplyItems(BaseModel):
  items: List[Dict[str, Any]]


class IdsPayload(BaseModel):
  ids: List[int]


class InterruptRequest(BaseModel):
  request_id: Optional[str] = None


# 메모리 저장
events: List[Event] = []
recurring_events: List[Dict[str, Any]] = []
next_id: int = 1
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

inflight_tasks: Dict[str, Dict[str, asyncio.Task]] = {}
inflight_lock = asyncio.Lock()

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

google_events_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
context_cache: Dict[str, Dict[str, Any]] = {}
oauth_state_store: Dict[str, Dict[str, Any]] = {}


def _serialize_events_payload() -> Dict[str, Any]:
  return {
      "version": 2,
      "events": [e.dict() for e in events],
      "recurring_events": recurring_events,
  }


def _save_events_to_disk() -> None:
  try:
    payload = _serialize_events_payload()
    EVENTS_DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                encoding="utf-8")
  except Exception as exc:
    _log_debug(f"[EVENT STORE] save failed: {exc}")


def _load_events_from_disk() -> None:
  global events, recurring_events, next_id
  events.clear()
  recurring_events.clear()
  next_id = 1
  if not EVENTS_DATA_FILE.exists():
    return
  try:
    data = json.loads(EVENTS_DATA_FILE.read_text(encoding="utf-8"))
  except Exception as exc:
    _log_debug(f"[EVENT STORE] load failed: {exc}")
    return

  max_id = 0
  legacy_list: Optional[List[Any]] = None
  if isinstance(data, list):
    legacy_list = data
  elif isinstance(data, dict):
    legacy_list = data.get("events")
    recurring_raw = data.get("recurring_events") or []
    if isinstance(recurring_raw, list):
      for item in recurring_raw:
        if not isinstance(item, dict):
          continue
        rid = item.get("id")
        title = (item.get("title") or "").strip()
        start_date = item.get("start_date")
        recurrence = item.get("recurrence")
        if not isinstance(rid, int) or rid <= 0:
          continue
        if not title or not isinstance(start_date, str):
          continue
        if not isinstance(recurrence, dict):
          continue
        normalized = _normalize_recurrence_dict(recurrence)
        if not normalized:
          continue
        item = {
            "id": rid,
            "title": title,
            "start_date": start_date,
            "time": item.get("time"),
            "duration_minutes": item.get("duration_minutes"),
            "location": item.get("location"),
            "description": item.get("description"),
            "attendees": item.get("attendees"),
            "reminders": item.get("reminders"),
            "visibility": item.get("visibility"),
            "transparency": item.get("transparency"),
            "meeting_url": item.get("meeting_url"),
            "color_id": item.get("color_id"),
            "recurrence": normalized,
            "timezone": item.get("timezone") or "Asia/Seoul",
            "google_event_id": item.get("google_event_id"),
            "created_at": item.get("created_at") or _now_iso_minute(),
        }
        recurring_events.append(item)
        if rid > max_id:
          max_id = rid

  if isinstance(legacy_list, list):
    loaded: List[Event] = []
    for item in legacy_list:
      if not isinstance(item, dict):
        continue
      if not item.get("created_at"):
        item["created_at"] = _now_iso_minute()
      try:
        ev = Event(**item)
      except Exception:
        continue
      loaded.append(ev)
      if ev.id > max_id:
        max_id = ev.id
    events[:] = loaded

  next_id = max_id + 1 if max_id else 1



# -------------------------
# 공통 유틸
# -------------------------
def get_async_client() -> AsyncOpenAI:
  if async_client is None:
    raise RuntimeError("OPENAI_API_KEY is not set")
  return async_client


def normalize_text(text: str) -> str:
  t = (text or "").strip()
  t = re.sub(r"\s+", " ", t)
  return t


def _now_iso_minute() -> str:
  return datetime.now(SEOUL).strftime("%Y-%m-%dT%H:%M")


def _parse_created_at(dt_str: Optional[str]) -> datetime:
  if isinstance(dt_str, str):
    try:
      return datetime.strptime(dt_str.strip(), "%Y-%m-%dT%H:%M").replace(
          tzinfo=SEOUL)
    except Exception:
      pass
  return datetime.now(SEOUL)


def _split_iso_date_time(value: Optional[str]) -> Tuple[Optional[date], Optional[str]]:
  if not isinstance(value, str):
    return (None, None)
  raw = value.strip()
  if len(raw) < 10:
    return (None, None)
  date_part = raw[:10]
  try:
    dt = datetime.strptime(date_part, "%Y-%m-%d").date()
  except Exception:
    return (None, None)
  time_part: Optional[str] = None
  if len(raw) >= 16:
    time_part = raw[11:16]
  return (dt, time_part)


def _normalize_end_datetime(raw_end: Any) -> Optional[str]:
  if not isinstance(raw_end, str):
    return None
  candidate = raw_end.strip()
  if not candidate:
    return None
  if ISO_DATETIME_RE.match(candidate):
    return candidate
  if ISO_DATETIME_24_RE.match(candidate):
    base = candidate[:10]
    try:
      base_date = datetime.strptime(base, "%Y-%m-%d").date()
    except Exception:
      return None
    next_day = base_date + timedelta(days=1)
    return next_day.strftime("%Y-%m-%dT00:00")
  if ISO_DATE_RE.match(candidate):
    return candidate + "T23:59"
  normalized = _normalize_datetime_minute(candidate)
  if normalized:
    return normalized
  return None


def _normalize_datetime_minute(raw: str) -> Optional[str]:
  candidate = raw.strip()
  if not candidate:
    return None
  if ISO_DATETIME_RE.match(candidate):
    return candidate
  if candidate.endswith("Z"):
    candidate = candidate[:-1] + "+00:00"
  match = DATETIME_FLEX_RE.match(candidate)
  if match:
    return f"{match.group(1)}T{match.group(2)}"
  try:
    dt = datetime.fromisoformat(candidate)
  except Exception:
    return None
  return dt.strftime("%Y-%m-%dT%H:%M")


def is_all_day_span(start_iso: Optional[str],
                    end_iso: Optional[str]) -> bool:
  start_date, start_time = _split_iso_date_time(start_iso)
  if not start_date:
    return False
  if start_time not in (None, "00:00"):
    return False

  if not end_iso:
    return True

  end_date, end_time = _split_iso_date_time(end_iso)
  if not end_date:
    return True

  if end_date < start_date:
    return False

  if end_time in (None, "23:59"):
    return True

  if end_time == "00:00":
    return end_date >= start_date

  return False


def _validate_image_payload(images: Optional[List[str]]) -> List[str]:
  if not images:
    return []

  allowed_prefixes = (
      "data:image/png;base64,",
      "data:image/jpeg;base64,",
      "data:image/webp;base64,",
  )
  cleaned: List[str] = []
  for raw in images:
    if not isinstance(raw, str):
      continue
    data = raw.strip()
    if not data:
      continue
    if not any(data.startswith(prefix) for prefix in allowed_prefixes):
      raise HTTPException(status_code=400,
                          detail="이미지는 data:image/...;base64 형식이어야 합니다.")
    if len(data) > MAX_IMAGE_DATA_URL_CHARS:
      raise HTTPException(status_code=400, detail=IMAGE_TOO_LARGE_MESSAGE)
    cleaned.append(data)
    if len(cleaned) >= MAX_IMAGE_ATTACHMENTS:
      break
  return cleaned


def _compute_all_day_bounds(start_iso: str,
                            end_iso: Optional[str]) -> Tuple[date, date]:
  start_date, _ = _split_iso_date_time(start_iso)
  if not start_date:
    start_date = datetime.now(SEOUL).date()

  end_exclusive: date
  if end_iso:
    end_date, end_time = _split_iso_date_time(end_iso)
    if not end_date:
      end_exclusive = start_date + timedelta(days=1)
    else:
      if end_time == "00:00":
        if end_date <= start_date:
          end_exclusive = start_date + timedelta(days=1)
        else:
          end_exclusive = end_date + timedelta(days=1)
      else:
        end_exclusive = end_date + timedelta(days=1)
  else:
    end_exclusive = start_date + timedelta(days=1)

  if end_exclusive <= start_date:
    end_exclusive = start_date + timedelta(days=1)

  return (start_date, end_exclusive)


def _normalize_single_event_times(
    start_raw: Any,
    end_raw: Any,
) -> Tuple[Optional[str], Optional[str], bool]:
  """
    start_raw/end_raw: raw strings from LLM or client.
    Returns (start_iso, end_iso, all_day_flag).
    - Accepts date-only strings and upgrades them to 00:00 / 23:59.
    - Handles end at 24:00 by rolling to next day 00:00.
  """
  start_iso: Optional[str] = None
  end_iso: Optional[str] = None

  if isinstance(start_raw, str):
    s = start_raw.strip()
    if ISO_DATE_RE.match(s):
      start_iso = s + "T00:00"
    else:
      start_iso = _normalize_datetime_minute(s)

  if start_iso is None:
    return (None, None, False)

  end_iso = _normalize_end_datetime(end_raw)
  all_day_flag = is_all_day_span(start_iso, end_iso)
  return (start_iso, end_iso, all_day_flag)


def _coerce_patch_start(value: Any) -> Optional[str]:
  if value is None:
    return None
  if not isinstance(value, str):
    raise HTTPException(status_code=400, detail="시작 시각 형식이 잘못되었습니다.")
  candidate = value.strip()
  if not candidate:
    return None
  if not ISO_DATETIME_RE.match(candidate):
    raise HTTPException(status_code=400, detail="시작 시각 형식이 잘못되었습니다.")
  return candidate


def _coerce_patch_end(value: Any) -> Optional[str]:
  if value is None:
    return None
  if not isinstance(value, str):
    raise HTTPException(status_code=400, detail="종료 시각 형식이 잘못되었습니다.")
  candidate = value.strip()
  if not candidate:
    return None
  normalized = _normalize_end_datetime(candidate)
  if not normalized:
    raise HTTPException(status_code=400, detail="종료 시각 형식이 잘못되었습니다.")
  return normalized


def _clean_optional_str(value: Any) -> Optional[str]:
  if value is None:
    return None
  if not isinstance(value, str):
    return None
  trimmed = value.strip()
  return trimmed or None


def _parse_scope_dates(start_str: Optional[str],
                       end_str: Optional[str],
                       require: bool = False,
                       max_days: Optional[int] = MAX_SCOPE_DAYS,
                       label: Optional[str] = None) -> Optional[Tuple[date, date]]:
  scope_label = label or ("삭제" if require else "조회")
  if not start_str or not end_str:
    if require:
      raise HTTPException(status_code=400,
                          detail=f"{scope_label} 범위의 시작/종료 날짜를 모두 입력해주세요.")
    return None

  try:
    start_date = datetime.strptime(start_str.strip(), "%Y-%m-%d").date()
    end_date = datetime.strptime(end_str.strip(), "%Y-%m-%d").date()
  except Exception:
    raise HTTPException(status_code=400,
                        detail=f"{scope_label} 범위 날짜 형식이 잘못되었습니다.")

  if end_date < start_date:
    raise HTTPException(status_code=400,
                        detail=f"{scope_label} 범위 종료일이 시작일보다 빠릅니다.")

  if max_days is not None and (end_date - start_date).days > max_days:
    raise HTTPException(status_code=400,
                        detail=f"{scope_label} 범위는 최대 {max_days}일까지만 설정할 수 있습니다.")

  return (start_date, end_date)


def _event_within_scope(ev: Event, scope: Optional[Tuple[date, date]]) -> bool:
  if not scope:
    return True
  start_date, _ = _split_iso_date_time(ev.start)
  if not start_date:
    return False
  return scope[0] <= start_date <= scope[1]


def _normalize_google_timestamp(ts: Optional[str]) -> str:
  if not ts:
    return _now_iso_minute()
  try:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    dt = dt.astimezone(SEOUL)
    return dt.strftime("%Y-%m-%dT%H:%M")
  except Exception:
    return _now_iso_minute()


def _merge_description(description: Optional[str],
                       meeting_url: Optional[str]) -> Optional[str]:
  desc = (description or "").strip()
  link = (meeting_url or "").strip()
  if not link:
    return desc or None
  if link in desc:
    return desc
  if desc:
    return f"{desc}\n\n회의 링크: {link}"
  return f"회의 링크: {link}"


def _build_gcal_attendees(attendees: Optional[List[str]]) -> Optional[List[Dict[str, str]]]:
  if attendees is None:
    return None
  if len(attendees) == 0:
    return []
  results: List[Dict[str, str]] = []
  for item in attendees:
    if not isinstance(item, str):
      continue
    email = item.strip()
    if email:
      results.append({"email": email})
  return results or None


def _build_gcal_reminders(reminders: Optional[List[int]]) -> Optional[Dict[str, Any]]:
  if reminders is None:
    return None
  if not reminders:
    return {"useDefault": True}
  overrides: List[Dict[str, Any]] = []
  for raw in reminders:
    try:
      minutes = int(raw)
    except Exception:
      continue
    if minutes < 0:
      continue
    overrides.append({"method": "popup", "minutes": minutes})
  if not overrides:
    return {"useDefault": True}
  return {"useDefault": False, "overrides": overrides}


def _normalize_visibility(value: Optional[str]) -> Optional[str]:
  if value in {"default", "public", "private"}:
    return value
  return None


def _normalize_transparency(value: Optional[str]) -> Optional[str]:
  if value in {"opaque", "transparent"}:
    return value
  return None


def _normalize_color_id(value: Optional[str]) -> Optional[str]:
  if value is None:
    return None
  if isinstance(value, str):
    cleaned = value.strip()
    if cleaned.isdigit():
      number = int(cleaned)
      if 1 <= number <= 11:
        return str(number)
    if cleaned == "":
      return None
  return None


def store_event(
    title: str,
    start: str,
    end: Optional[str],
    location: Optional[str],
    recur: Optional[str] = None,
    google_event_id: Optional[str] = None,
    all_day: bool = False,
    created_at: Optional[str] = None,
    description: Optional[str] = None,
    attendees: Optional[List[str]] = None,
    reminders: Optional[List[int]] = None,
    visibility: Optional[str] = None,
    transparency: Optional[str] = None,
    meeting_url: Optional[str] = None,
    timezone_value: Optional[str] = None,
    color_id: Optional[str] = None,
) -> Event:
  global next_id, events
  created_str = created_at or _now_iso_minute()
  new_event = Event(
      id=next_id,
      title=title,
      start=start,
      end=end,
      location=location,
      description=description,
      attendees=attendees,
      reminders=reminders,
      visibility=visibility,
      transparency=transparency,
      meeting_url=meeting_url,
      color_id=color_id,
      recur=recur,
      google_event_id=google_event_id,
      all_day=bool(all_day),
      created_at=created_str,
      timezone=timezone_value or "Asia/Seoul",
  )
  next_id += 1
  events.append(new_event)
  _save_events_to_disk()
  return new_event


def store_recurring_event(title: str,
                          start_date: str,
                          time: Optional[str],
                          duration_minutes: Optional[int],
                          location: Optional[str],
                          recurrence: Dict[str, Any],
                          description: Optional[str] = None,
                          attendees: Optional[List[str]] = None,
                          reminders: Optional[List[int]] = None,
                          visibility: Optional[str] = None,
                          transparency: Optional[str] = None,
                          meeting_url: Optional[str] = None,
                          color_id: Optional[str] = None,
                          timezone_value: str = "Asia/Seoul",
                          google_event_id: Optional[str] = None) -> Dict[str, Any]:
  global next_id, recurring_events
  recurrence_copy = copy.deepcopy(recurrence)
  record = {
      "id": next_id,
      "title": title,
      "start_date": start_date,
      "time": time,
      "duration_minutes": duration_minutes,
      "location": location,
      "description": description,
      "attendees": attendees,
      "reminders": reminders,
      "visibility": visibility,
      "transparency": transparency,
      "meeting_url": meeting_url,
      "color_id": color_id,
      "recurrence": recurrence_copy,
      "timezone": timezone_value or "Asia/Seoul",
      "google_event_id": google_event_id,
      "created_at": _now_iso_minute(),
  }
  recurring_events.append(record)
  next_id += 1
  _save_events_to_disk()
  return record


def _find_recurring_event(event_id: int) -> Optional[Dict[str, Any]]:
  for item in recurring_events:
    if item.get("id") == event_id:
      return item
  return None


def _delete_recurring_event(event_id: int, persist: bool = True) -> bool:
  global recurring_events
  before = len(recurring_events)
  recurring_events = [item for item in recurring_events if item.get("id") != event_id]
  if len(recurring_events) < before:
    if persist:
      _save_events_to_disk()
    return True
  return False


def _recurring_definition_to_event(rec: Dict[str, Any]) -> Event:
  time_str = rec.get("time") or "00:00"
  all_day = not bool(rec.get("time"))
  start_value = f"{rec['start_date']}T{time_str}"
  end_value = None
  duration = rec.get("duration_minutes")
  if not all_day and isinstance(duration, (int, float)) and duration > 0:
    try:
      st = datetime.strptime(start_value, "%Y-%m-%dT%H:%M")
      end_value = (st + timedelta(minutes=int(duration))).strftime("%Y-%m-%dT%H:%M")
    except Exception:
      end_value = None
  elif all_day:
    try:
      st = datetime.strptime(rec["start_date"], "%Y-%m-%d")
      end_value = (st + timedelta(days=1)).strftime("%Y-%m-%dT00:00")
    except Exception:
      end_value = None

  return Event(
      id=rec["id"],
      title=rec["title"],
      start=start_value,
      end=end_value,
      location=rec.get("location"),
      description=rec.get("description"),
      attendees=rec.get("attendees"),
      reminders=rec.get("reminders"),
      visibility=rec.get("visibility"),
      transparency=rec.get("transparency"),
      meeting_url=rec.get("meeting_url"),
      color_id=rec.get("color_id"),
      recur="recurring",
      google_event_id=rec.get("google_event_id"),
      all_day=all_day,
      created_at=rec.get("created_at"),
      start_date=rec.get("start_date"),
      time=rec.get("time"),
      duration_minutes=rec.get("duration_minutes"),
      recurrence=rec.get("recurrence"),
      timezone=rec.get("timezone") or "Asia/Seoul",
  )


def _build_recurring_occurrence_event(rec: Dict[str, Any], occ: Dict[str, Any],
                                      occurrence_id: int) -> Event:
  return Event(
      id=occurrence_id,
      title=occ.get("title") or rec["title"],
      start=occ.get("start") or f"{rec['start_date']}T00:00",
      end=occ.get("end"),
      location=occ.get("location"),
      description=rec.get("description"),
      attendees=rec.get("attendees"),
      reminders=rec.get("reminders"),
      visibility=rec.get("visibility"),
      transparency=rec.get("transparency"),
      meeting_url=rec.get("meeting_url"),
      color_id=rec.get("color_id"),
      recur="recurring",
      google_event_id=rec.get("google_event_id"),
      all_day=bool(occ.get("all_day")),
      created_at=rec.get("created_at"),
      start_date=rec.get("start_date"),
      time=rec.get("time"),
      duration_minutes=rec.get("duration_minutes"),
      recurrence=rec.get("recurrence"),
      timezone=rec.get("timezone") or "Asia/Seoul",
  )


def _decode_occurrence_id(value: int) -> Optional[int]:
  if value >= 0:
    return None
  raw = abs(value)
  rec_id = raw // RECURRENCE_OCCURRENCE_SCALE
  if rec_id == 0:
    rec_id = raw
  return rec_id


def _collect_local_recurring_occurrences(
    scope: Optional[Tuple[date, date]] = None) -> List[Event]:
  items: List[Event] = []
  for rec in recurring_events:
    recurrence_spec = rec.get("recurrence")
    if not isinstance(recurrence_spec, dict):
      continue
    base_dict = {
        "title": rec.get("title"),
        "start_date": rec.get("start_date"),
        "time": rec.get("time"),
        "duration_minutes": rec.get("duration_minutes"),
        "location": rec.get("location"),
        "recurrence": recurrence_spec,
        "timezone": rec.get("timezone"),
    }
    expanded = _expand_recurring_item(base_dict, scope=scope)
    for idx, occ in enumerate(expanded):
      occurrence_id = -(rec["id"] * RECURRENCE_OCCURRENCE_SCALE + idx + 1)
      items.append(_build_recurring_occurrence_event(rec, occ, occurrence_id))
  return items


def _list_local_events_for_api(
    scope: Optional[Tuple[date, date]] = None) -> List[Event]:
  if scope:
    singles = [ev for ev in events if _event_within_scope(ev, scope)]
  else:
    singles = list(events)

  if not recurring_events:
    return singles

  expansion_scope = scope
  if expansion_scope is None:
    today = datetime.now(SEOUL).date()
    expansion_scope = (today - timedelta(days=30),
                       today + timedelta(days=MAX_RECURRENCE_EXPANSION_DAYS))

  rec_items = _collect_local_recurring_occurrences(scope=expansion_scope)
  return singles + rec_items


def is_admin(request: Request) -> bool:
  return request.cookies.get(ADMIN_COOKIE_NAME) == ADMIN_COOKIE_VALUE


def is_google_mode_active(request: Request, has_token: Optional[bool] = None) -> bool:
  if is_admin(request) or not ENABLE_GCAL:
    return False
  token_present = load_gcal_token_for_request(
      request) is not None if has_token is None else has_token
  return bool(token_present)


# -------------------------
# LLM 프롬프트
# -------------------------
EVENTS_SYSTEM_PROMPT_TEMPLATE = """너는 한국어 일정 문장을 구조화하는 파서다. 반드시 JSON 한 개만 반환한다. 설명 금지.
기준 정보:
- 기준 날짜: {TODAY}
- 시간대: Asia/Seoul

출력 스키마:
{
  "needs_context": true | false,
  "context_dates": ["YYYY-MM-DD"],
  "context_slices": [
    {
      "start_date": "YYYY-MM-DD",
      "end_date": "YYYY-MM-DD"
    }
  ],
  "need_more_information": true | false,
  "content": string,
  "items": [
    {
      "type": "single",
      "title": string,
      "start": "YYYY-MM-DDTHH:MM",
      "end": "YYYY-MM-DDTHH:MM" | null,
      "location": string | null
    },
    {
      "type": "recurring",
      "title": string,
      "start_date": "YYYY-MM-DD",
      "time": "HH:MM" | null,
      "duration_minutes": number | null,
      "location": string | null,
      "recurrence": {
        "freq": "DAILY" | "WEEKLY" | "MONTHLY" | "YEARLY",
        "interval": number | null,
        "byweekday": [0,1,2,3,4,5,6] | null,
        "bymonthday": [1..31, -1] | null,
        "bysetpos": number | null,
        "bymonth": [1..12] | null,
        "end": {
          "until": "YYYY-MM-DD" | null,
          "count": number | null
        } | null
      },
      "end_date": "YYYY-MM-DD" | null,
      "weekdays": [0,1,2,3,4,5,6] | null
    }
  ]
}

우선 규칙(1이 2에 우선함):
0. 필요하면 존재하는 일정 정보를 불러올 수 있으며 need_more_information보다 needs_context를 우선으로 사용한다.
1. 이미 존재하는 일정 정보가 필요하면 needs_context=true, need_more_information=false, items=[]로 바로 반환한다.
  - 특정 날짜만 필요하면 context_dates 배열로 요청한다.
  - 범위가 필요하면 context_slices 배열로 요청한다.
2. 이미 존재하는 일정 정보는 필요 없고 사용자에게 추가 질문이 필요하면 need_more_information=true, content에 질문만 작성, 이때 items=[], needs_context=false로 둔다.

규칙:
1. 반복은 recurrence를 우선 사용한다
2. 여러 일정이면 single을 여러 개로 만들고, 반복이 있으면 recurring을 사용하며 혼합이면 둘 다 포함한다.
3. weekdays: 0=월요일 … 6=일요일
4. 입력에 사용자:/assistant: 대화가 섞일 수 있으니 전체 대화를 참고해 요청을 해석한다.
5. title은 시간/장소를 넣지 않는다
6. 상대 날짜는 기준 날짜로 계산한다.
7. 시간 정보가 없으면 recurring.time과 recurring.duration_minutes는 null.
8. 종일 일정이 명확하면 질문하지 않는다. 휴가/연차/휴무/기념일/생일/공휴일 등.
9. recurrence.end는 until 또는 count 중 하나만 사용한다(동시 사용 금지).
10. 사용자의 요청이 없다면 과거 이벤트는 생성하지 않는다.
11. recurrence가 있으면 우선 사용한다. end_date/weekday는 이전 버전 호환용으로만 사용(필수 아님).
12. need_more_information=false이면 content는 빈 문자열로 둔다.
13. 마크다운은 need_more_information=true인 경우 content에서만 제한적으로 사용한다(**굵게**, *기울임*, - 리스트, 줄바꿈). 그 외 필드에는 마크다운을 쓰지 않는다.
14. needs_context와 need_more_information은 동시에 true로 두지 않는다.
15. 질문은 최대 3개까지만 작성한다.

"""


def build_events_system_prompt() -> str:
  today = datetime.now(SEOUL).date().isoformat()
  return EVENTS_SYSTEM_PROMPT_TEMPLATE.replace("{TODAY}", today)


EVENTS_SYSTEM_PROMPT_WITH_CONTEXT_TEMPLATE = """너는 한국어 일정 문장을 구조화하는 파서다. 반드시 JSON 한 개만 반환한다. 설명 금지.
입력 형식:
{
  "request": string,
  "has_images": boolean,
  "context": {
    "today": "YYYY-MM-DD",
    "timezone": "Asia/Seoul",
    "scope": {"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"},
    "scopes": [{"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"}],
    "dates": ["YYYY-MM-DD"],
    "events": [
      {
        "id": number,
        "title": string,
        "start": "YYYY-MM-DDTHH:MM",
        "end": "YYYY-MM-DDTHH:MM" | null,
        "location": string | null,
        "recur": "recurring" | null,
        "all_day": boolean
      }
    ]
  }
}

출력 스키마:
{
  "need_more_information": true | false,
  "content": string,
  "items": [
    {
      "type": "single",
      "title": string,
      "start": "YYYY-MM-DDTHH:MM",
      "end": "YYYY-MM-DDTHH:MM" | null,
      "location": string | null
    },
    {
      "type": "recurring",
      "title": string,
      "start_date": "YYYY-MM-DD",
      "time": "HH:MM" | null,
      "duration_minutes": number | null,
      "location": string | null,
      "recurrence": {
        "freq": "DAILY" | "WEEKLY" | "MONTHLY" | "YEARLY",
        "interval": number | null,
        "byweekday": [0,1,2,3,4,5,6] | null,
        "bymonthday": [1..31, -1] | null,
        "bysetpos": number | null,
        "bymonth": [1..12] | null,
        "end": {
          "until": "YYYY-MM-DD" | null,
          "count": number | null
        } | null
      },
      "end_date": "YYYY-MM-DD" | null,
      "weekdays": [0,1,2,3,4,5,6] | null
    }
  ]
}

우선 규칙:
1. 사용자에게 추가 질문이 필요하면 need_more_information=true, content에 질문만 작성하고 items=[]로 둔다.

규칙:
1. 반복은 recurrence를 우선 사용한다
2. 여러 일정이면 single을 여러 개로 만들고, 반복이 있으면 recurring을 사용하며 혼합이면 둘 다 포함한다.
3. weekdays: 0=월요일 … 6=일요일
4. 입력에 사용자:/assistant: 대화가 섞일 수 있으니 전체 대화를 참고해 요청을 해석한다.
5. title은 시간/장소를 넣지 않는다
6. 상대 날짜는 기준 날짜로 계산한다.
7. 시간 정보가 없으면 recurring.time과 recurring.duration_minutes는 null.
8. 종일 일정이 명확하면 질문하지 않는다. 휴가/연차/휴무/기념일/생일/공휴일 등.
9. recurrence.end는 until 또는 count 중 하나만 사용한다(동시 사용 금지).
10. 사용자의 요청이 없다면 과거 이벤트는 생성하지 않는다.
11. recurrence가 있으면 우선 사용한다. end_date/weekday는 이전 버전 호환용으로만 사용(필수 아님)
12. context.events는 이미 존재하는 일정이다. 요청이 기존 일정과의 관계(직후/직전/같은 시간/충돌 회피 등)를 요구할 때만 참고한다.
13. context.events 자체를 수정하거나 삭제하지 말고, 새로 추가할 일정만 만든다.
14. 이미지 또는 텍스트에 정보가 없으면 null 처리
15. 가려진 구간이나 알아볼 수 없는 내용은 제외
16. 꼭 필요한 정보만 질문한다. 문맥이나 context.events로 알 수 있으면 묻지 않는다.
17. need_more_information=false이면 content는 빈 문자열로 둔다.
18. 마크다운은 need_more_information=true인 경우 content에서만 제한적으로 사용한다(**굵게**, *기울임*, - 리스트, 줄바꿈). 그 외 필드에는 마크다운을 쓰지 않는다.
19. 질문은 최대 3개까지만 작성한다.

"""


def build_events_system_prompt_with_context() -> str:
  return EVENTS_SYSTEM_PROMPT_WITH_CONTEXT_TEMPLATE


EVENTS_MULTIMODAL_PROMPT_TEMPLATE = """너는 한국어 일정 정보를 텍스트와 이미지에서 구조화하는 파서다. 반드시 JSON 한 개만 반환한다. 설명 금지.
입력 특징:
- 사용자가 항공권/티켓/메신저/시간표 등 캡처 이미지를 제공한다.
- 검은 박스로 가린 영역이나 알아볼 수 없는 부분은 추측하지 말고 무시한다.
- 텍스트 설명이 함께 있을 수 있으므로 반드시 모두 참고한다.

기준 정보:
- 기준 날짜: {TODAY}
- 시간대: Asia/Seoul

출력 스키마:
{
  "needs_context": true | false,
  "context_dates": ["YYYY-MM-DD"],
  "context_slices": [
    {
      "start_date": "YYYY-MM-DD",
      "end_date": "YYYY-MM-DD"
    }
  ],
  "need_more_information": true | false,
  "content": string,
  "items": [
    {
      "type": "single",
      "title": string,
      "start": "YYYY-MM-DDTHH:MM",
      "end": "YYYY-MM-DDTHH:MM" | null,
      "location": string | null
    },
    {
      "type": "recurring",
      "title": string,
      "start_date": "YYYY-MM-DD",
      "time": "HH:MM" | null,
      "duration_minutes": number | null,
      "location": string | null,
      "recurrence": {
        "freq": "DAILY" | "WEEKLY" | "MONTHLY" | "YEARLY",
        "interval": number | null,
        "byweekday": [0,1,2,3,4,5,6] | null,
        "bymonthday": [1..31, -1] | null,
        "bysetpos": number | null,
        "bymonth": [1..12] | null,
        "end": {
          "until": "YYYY-MM-DD" | null,
          "count": number | null
        } | null
      },
      "end_date": "YYYY-MM-DD" | null,
      "weekdays": [0,1,2,3,4,5,6] | null
    }
  ]
}

우선 규칙(1이 2에 우선함):
0. 필요하면 존재하는 일정 정보를 불러올 수 있으며 need_more_information보다 needs_context를 우선으로 사용한다.
1. 이미 존재하는 일정 정보가 필요하면 needs_context=true, need_more_information=false, items=[]로 바로 반환한다.
  - 특정 날짜만 필요하면 context_dates 배열로 요청한다.
  - 범위가 필요하면 context_slices 배열로 요청한다.
2. 이미 존재하는 일정 정보는 필요 없고 사용자에게 추가 질문이 필요하면 need_more_information=true, content에 질문만 작성, 이때 items=[], needs_context=false로 둔다.

규칙:
1. 반복은 recurrence를 우선 사용한다
2. 여러 일정이면 single을 여러 개로 만들고, 반복이 있으면 recurring을 사용하며 혼합이면 둘 다 포함한다.
3. weekdays: 0=월요일 … 6=일요일
4. 입력에 사용자:/assistant: 대화가 섞일 수 있으니 전체 대화를 참고해 요청을 해석한다.
5. title은 시간/장소를 넣지 않는다
6. 상대 날짜는 기준 날짜로 계산한다.
7. 시간 정보가 없으면 recurring.time과 recurring.duration_minutes는 null.
8. 종일 일정이 명확하면 질문하지 않는다. 휴가/연차/휴무/기념일/생일/공휴일 등.
9. recurrence.end는 until 또는 count 중 하나만 사용한다(동시 사용 금지).
10. 사용자의 요청이 없다면 과거 이벤트는 생성하지 않는다.
11. recurrence가 있으면 우선 사용한다. end_date/weekday는 이전 버전 호환용으로만 사용(필수 아님).
12. 이미지 또는 텍스트에 정보가 없으면 null 처리
13. 가려진 구간이나 알아볼 수 없는 내용은 제외
14. need_more_information=false이면 content는 빈 문자열로 둔다.
15. 마크다운은 need_more_information=true인 경우 content에서만 제한적으로 사용한다(**굵게**, *기울임*, - 리스트, 줄바꿈). 그 외 필드에는 마크다운을 쓰지 않는다.
16. needs_context와 need_more_information은 동시에 true로 두지 않는다.
17. 질문은 최대 3개까지만 작성한다.

"""


def build_events_multimodal_prompt() -> str:
  today = datetime.now(SEOUL).date().isoformat()
  return EVENTS_MULTIMODAL_PROMPT_TEMPLATE.replace("{TODAY}", today)


EVENTS_MULTIMODAL_PROMPT_WITH_CONTEXT_TEMPLATE = """너는 한국어 일정 정보를 텍스트와 이미지에서 구조화하는 파서다. 반드시 JSON 한 개만 반환한다. 설명 금지.
입력 특징:
- 사용자가 항공권/티켓/메신저/시간표 등 캡처 이미지를 제공한다.
- 검은 박스로 가린 영역이나 알아볼 수 없는 부분은 추측하지 말고 무시한다.
- 텍스트 설명이 함께 있을 수 있으므로 반드시 모두 참고한다.

입력 형식:
{
  "request": string,
  "has_images": boolean,
  "context": {
    "today": "YYYY-MM-DD",
    "timezone": "Asia/Seoul",
    "scope": {"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"},
    "scopes": [{"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"}],
    "dates": ["YYYY-MM-DD"],
    "events": [
      {
        "id": number,
        "title": string,
        "start": "YYYY-MM-DDTHH:MM",
        "end": "YYYY-MM-DDTHH:MM" | null,
        "location": string | null,
        "recur": "recurring" | null,
        "all_day": boolean
      }
    ]
  }
}

출력 스키마:
{
  "need_more_information": true | false,
  "content": string,
  "items": [
    {
      "type": "single",
      "title": string,
      "start": "YYYY-MM-DDTHH:MM",
      "end": "YYYY-MM-DDTHH:MM" | null,
      "location": string | null
    },
    {
      "type": "recurring",
      "title": string,
      "start_date": "YYYY-MM-DD",
      "time": "HH:MM" | null,
      "duration_minutes": number | null,
      "location": string | null,
      "recurrence": {
        "freq": "DAILY" | "WEEKLY" | "MONTHLY" | "YEARLY",
        "interval": number | null,
        "byweekday": [0,1,2,3,4,5,6] | null,
        "bymonthday": [1..31, -1] | null,
        "bysetpos": number | null,
        "bymonth": [1..12] | null,
        "end": {
          "until": "YYYY-MM-DD" | null,
          "count": number | null
        } | null
      },
      "end_date": "YYYY-MM-DD" | null,
      "weekdays": [0,1,2,3,4,5,6] | null
    }
  ]
}

우선 규칙:
1. 사용자에게 추가 질문이 필요하면 need_more_information=true, content에 질문만 작성하고 items=[]로 둔다.

규칙:
1. 반복은 recurrence를 우선 사용한다
2. 여러 일정이면 single을 여러 개로 만들고, 반복이 있으면 recurring을 사용하며 혼합이면 둘 다 포함한다.
3. weekdays: 0=월요일 … 6=일요일
4. 입력에 사용자:/assistant: 대화가 섞일 수 있으니 전체 대화를 참고해 요청을 해석한다.
5. title은 시간/장소를 넣지 않는다
6. 상대 날짜는 기준 날짜로 계산한다.
7. 시간 정보가 없으면 recurring.time과 recurring.duration_minutes는 null.
8. 종일 일정이 명확하면 질문하지 않는다. 휴가/연차/휴무/기념일/생일/공휴일 등.
9. recurrence.end는 until 또는 count 중 하나만 사용한다(동시 사용 금지).
10. 사용자의 요청이 없다면 과거 이벤트는 생성하지 않는다.
11. recurrence가 있으면 우선 사용한다. end_date/weekday는 이전 버전 호환용으로만 사용(필수 아님)
12. context.events는 이미 존재하는 일정이다. 요청이 기존 일정과의 관계(직후/직전/같은 시간/충돌 회피 등)를 요구할 때만 참고한다.
13. context.events 자체를 수정하거나 삭제하지 말고, 새로 추가할 일정만 만든다.
14. 이미지 또는 텍스트에 정보가 없으면 null 처리
15. 가려진 구간이나 알아볼 수 없는 내용은 제외
16. 꼭 필요한 정보만 질문한다. 문맥이나 context.events로 알 수 있으면 묻지 않는다.
17. need_more_information=false이면 content는 빈 문자열로 둔다.
18. 마크다운은 need_more_information=true인 경우 content에서만 제한적으로 사용한다(**굵게**, *기울임*, - 리스트, 줄바꿈). 그 외 필드에는 마크다운을 쓰지 않는다.
19. 질문은 최대 3개까지만 작성한다.

"""


def build_events_multimodal_prompt_with_context() -> str:
  return EVENTS_MULTIMODAL_PROMPT_WITH_CONTEXT_TEMPLATE


def _build_events_user_payload(text: str,
                               has_images: bool,
                               context: Optional[Dict[str, Any]] = None
                               ) -> str:
  if context is not None:
    payload: Dict[str, Any] = {
        "request": text or "",
        "has_images": bool(has_images),
        "context": context,
    }
    return json.dumps(payload, ensure_ascii=False)

  lines = []
  if text:
    lines.append(f"문장: {text}")
  else:
    lines.append("문장: (제공되지 않음)")
  if has_images:
    lines.append("첨부 이미지를 참고해서 일정 정보를 추출해줘.")
  return "\n".join(lines)


def _sanitize_reasoning_effort(value: Optional[str]) -> Optional[str]:
  if not value or not isinstance(value, str):
    return None
  normalized = value.strip().lower()
  if normalized in ALLOWED_REASONING_EFFORTS:
    return normalized
  return None


def _sanitize_model(value: Optional[str]) -> Optional[str]:
  if not value or not isinstance(value, str):
    return None
  normalized = value.strip().lower()
  if normalized in ALLOWED_ASSISTANT_MODELS:
    return ALLOWED_ASSISTANT_MODELS[normalized]
  if normalized in ALLOWED_ASSISTANT_MODELS.values():
    return normalized
  return None


def _resolve_request_reasoning_effort(request: Request,
                                      requested: Optional[str]) -> Optional[str]:
  if not requested:
    return None
  return _sanitize_reasoning_effort(requested)


def _resolve_request_model(request: Request,
                           requested: Optional[str]) -> Optional[str]:
  if not requested:
    return None
  return _sanitize_model(requested)


def _pick_reasoning_effort(value: Optional[str], default_value: str) -> str:
  sanitized = _sanitize_reasoning_effort(value)
  return sanitized or default_value


def _sanitize_context_days(value: Any) -> int:
  try:
    days = int(value)
  except (TypeError, ValueError):
    return 0
  if days < 0:
    return 0
  return min(days, MAX_CONTEXT_DAYS)


def _parse_iso_date(value: Any) -> Optional[date]:
  if not isinstance(value, str):
    return None
  candidate = value.strip()
  if len(candidate) < 10:
    return None
  date_part = candidate[:10]
  if not ISO_DATE_RE.match(date_part):
    return None
  try:
    return datetime.strptime(date_part, "%Y-%m-%d").date()
  except Exception:
    return None


def _normalize_context_dates(value: Any) -> List[date]:
  if not isinstance(value, list):
    return []
  out: List[date] = []
  seen: set[str] = set()
  for raw in value:
    dt = _parse_iso_date(raw)
    if not dt:
      continue
    key = dt.isoformat()
    if key in seen:
      continue
    seen.add(key)
    out.append(dt)
    if len(out) >= MAX_CONTEXT_DATES:
      break
  return out


def _normalize_context_slices(value: Any) -> List[Tuple[date, date]]:
  if not isinstance(value, list):
    return []
  out: List[Tuple[date, date]] = []
  seen: set[Tuple[str, str]] = set()
  for raw in value:
    if not isinstance(raw, dict):
      continue
    start = _parse_iso_date(raw.get("start_date"))
    end = _parse_iso_date(raw.get("end_date"))
    if not start or not end:
      continue
    if end < start:
      continue
    if (end - start).days > MAX_CONTEXT_DAYS:
      continue
    key = (start.isoformat(), end.isoformat())
    if key in seen:
      continue
    seen.add(key)
    out.append((start, end))
    if len(out) >= MAX_CONTEXT_SLICES:
      break
  return out


def _parse_bool(value: Any) -> bool:
  if isinstance(value, bool):
    return value
  if isinstance(value, (int, float)):
    return value != 0
  if isinstance(value, str):
    return value.strip().lower() in ("true", "yes", "1")
  return False


def _extract_context_request(
    data: Dict[str, Any]) -> Tuple[bool, List[Tuple[date, date]]]:
  slices = _normalize_context_slices(data.get("context_slices"))
  dates = _normalize_context_dates(data.get("context_dates"))
  needs = _parse_bool(data.get("needs_context")) or bool(slices or dates)
  if not needs:
    return False, []

  scopes: List[Tuple[date, date]] = list(slices)
  for dt in dates:
    scopes.append((dt, dt))

  if scopes:
    return True, scopes

  has_before = "days_before" in data
  has_after = "days_after" in data
  days_before = _sanitize_context_days(data.get("days_before"))
  days_after = _sanitize_context_days(data.get("days_after"))

  if not has_before and not has_after:
    days_before = DEFAULT_CONTEXT_DAYS
    days_after = DEFAULT_CONTEXT_DAYS
  else:
    if not has_before:
      days_before = days_after
    if not has_after:
      days_after = days_before
    if days_before == 0 and days_after == 0:
      days_before = DEFAULT_CONTEXT_DAYS
      days_after = DEFAULT_CONTEXT_DAYS

  today = datetime.now(SEOUL).date()
  start_date = today - timedelta(days=days_before)
  end_date = today + timedelta(days=days_after)
  return True, [(start_date, end_date)]


def _build_events_context(scopes: List[Tuple[date, date]],
                          session_id: Optional[str] = None) -> Dict[str, Any]:
  today = datetime.now(SEOUL).date()
  if not scopes:
    start_date = today - timedelta(days=DEFAULT_CONTEXT_DAYS)
    end_date = today + timedelta(days=DEFAULT_CONTEXT_DAYS)
    scopes = [(start_date, end_date)]

  snapshot: List[Dict[str, Any]] = []
  seen_ids: set[str] = set()
  if session_id:
    for scope in scopes:
      google_items = fetch_google_events_between(scope[0], scope[1], session_id)
      for item in google_items:
        raw_id = item.get("id")
        if not raw_id:
          continue
        id_key = str(raw_id)
        if id_key in seen_ids:
          continue
        seen_ids.add(id_key)
        snapshot.append({
            "id": raw_id,
            "title": item.get("title"),
            "start": item.get("start"),
            "end": item.get("end"),
            "location": item.get("location"),
            "recur": None,
            "all_day": item.get("all_day"),
        })
  else:
    for scope in scopes:
      for ev in events:
        if not _event_within_scope(ev, scope):
          continue
        id_key = str(ev.id)
        if id_key in seen_ids:
          continue
        seen_ids.add(id_key)
        snapshot.append({
            "id": ev.id,
            "title": ev.title,
            "start": ev.start,
            "end": ev.end,
            "location": ev.location,
            "recur": ev.recur,
            "all_day": ev.all_day,
        })

      rec_occurrences = _collect_local_recurring_occurrences(scope=scope)
      for occ in rec_occurrences:
        id_key = str(occ.id)
        if id_key in seen_ids:
          continue
        seen_ids.add(id_key)
        snapshot.append({
            "id": occ.id,
            "title": occ.title,
            "start": occ.start,
            "end": occ.end,
            "location": occ.location,
            "recur": occ.recur,
            "all_day": occ.all_day,
        })

  snapshot.sort(key=lambda x: x.get("start") or "")
  if len(snapshot) > MAX_CONTEXT_EVENTS:
    snapshot = snapshot[:MAX_CONTEXT_EVENTS]

  scope_payload = [
      {
          "start_date": scope[0].isoformat(),
          "end_date": scope[1].isoformat(),
      } for scope in scopes
  ]
  date_payload = [
      scope[0].isoformat() for scope in scopes if scope[0] == scope[1]
  ]

  payload: Dict[str, Any] = {
      "today": today.isoformat(),
      "timezone": "Asia/Seoul",
      "events": snapshot,
      "scopes": scope_payload,
      "dates": date_payload,
  }
  if len(scope_payload) == 1:
    payload["scope"] = scope_payload[0]
  return payload


def _normalize_int_list(value: Any,
                        min_val: int,
                        max_val: int,
                        allow_neg1: bool = False) -> List[int]:
  if not isinstance(value, list):
    return []
  out: List[int] = []
  seen: set[int] = set()
  for raw in value:
    try:
      iv = int(raw)
    except Exception:
      continue
    if allow_neg1 and iv == -1:
      if iv not in seen:
        out.append(iv)
        seen.add(iv)
      continue
    if min_val <= iv <= max_val and iv not in seen:
      out.append(iv)
      seen.add(iv)
  return out


def _normalize_recurrence_dict(rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
  freq = (rec.get("freq") or "").strip().upper()
  if freq not in {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}:
    return None

  interval_raw = rec.get("interval")
  try:
    interval = int(interval_raw) if interval_raw is not None else 1
  except Exception:
    interval = 1
  if interval < 1:
    interval = 1

  byweekday = _normalize_int_list(rec.get("byweekday"), 0, 6)
  bymonthday = _normalize_int_list(rec.get("bymonthday"),
                                   1,
                                   31,
                                   allow_neg1=True)
  bymonth = _normalize_int_list(rec.get("bymonth"), 1, 12)

  bysetpos_raw = rec.get("bysetpos")
  bysetpos: Optional[int] = None
  if bysetpos_raw is not None:
    try:
      iv = int(bysetpos_raw)
      if iv == -1 or 1 <= iv <= 5:
        bysetpos = iv
    except Exception:
      bysetpos = None

  end_value = rec.get("end")
  end: Optional[Dict[str, Any]] = None
  if isinstance(end_value, dict):
    until_raw = end_value.get("until")
    count_raw = end_value.get("count")
    until = (until_raw.strip() if isinstance(until_raw, str) else None)
    if until and not ISO_DATE_RE.match(until):
      until = None
    count: Optional[int] = None
    if count_raw is not None:
      try:
        count = int(count_raw)
      except Exception:
        count = None
      if count is not None and count <= 0:
        count = None
    if until and count:
      count = None
    if until or count:
      end = {"until": until, "count": count}

  return {
      "freq": freq,
      "interval": interval,
      "byweekday": byweekday or None,
      "bymonthday": bymonthday or None,
      "bysetpos": bysetpos,
      "bymonth": bymonth or None,
      "end": end,
  }


def _build_legacy_weekly_recurrence(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
  weekdays = _normalize_int_list(item.get("weekdays"), 0, 6)
  if not weekdays:
    return None
  end_date_str = item.get("end_date")
  until = None
  if isinstance(end_date_str, str) and ISO_DATE_RE.match(end_date_str.strip()):
    until = end_date_str.strip()
  end = {"until": until, "count": None} if until else None
  return {
      "freq": "WEEKLY",
      "interval": 1,
      "byweekday": weekdays or None,
      "bymonthday": None,
      "bysetpos": None,
      "bymonth": None,
      "end": end,
  }


def _resolve_recurrence(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
  rec_raw = item.get("recurrence")
  if isinstance(rec_raw, dict):
    normalized = _normalize_recurrence_dict(rec_raw)
    if normalized:
      item["recurrence"] = normalized
      return normalized

  legacy = _build_legacy_weekly_recurrence(item)
  if legacy:
    item["recurrence"] = legacy
    return legacy

  return None


_load_events_from_disk()


def _month_last_day(year: int, month: int) -> int:
  return calendar.monthrange(year, month)[1]


def _nth_weekday_in_month(year: int,
                          month: int,
                          weekday: int,
                          pos: int) -> Optional[date]:
  if pos == 0:
    return None
  last_day = _month_last_day(year, month)
  if pos > 0:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    day = 1 + offset + (pos - 1) * 7
    if 1 <= day <= last_day:
      return date(year, month, day)
    return None

  last = date(year, month, last_day)
  offset = (last.weekday() - weekday) % 7
  day = last_day - offset + (pos + 1) * 7
  if 1 <= day <= last_day:
    return date(year, month, day)
  return None


def _monthly_candidates(year: int,
                        month: int,
                        recurrence: Dict[str, Any],
                        default_day: int) -> List[date]:
  bymonthday = recurrence.get("bymonthday") or []
  byweekday = recurrence.get("byweekday") or []
  bysetpos = recurrence.get("bysetpos")
  last_day = _month_last_day(year, month)
  results: List[date] = []

  for d in bymonthday:
    if d == -1:
      day = last_day
    elif 1 <= d <= last_day:
      day = d
    else:
      continue
    results.append(date(year, month, day))

  if byweekday:
    if bysetpos is not None:
      for w in byweekday:
        dt = _nth_weekday_in_month(year, month, w, int(bysetpos))
        if dt:
          results.append(dt)
    else:
      for w in byweekday:
        dt = _nth_weekday_in_month(year, month, w, 1)
        while dt and dt.month == month:
          results.append(dt)
          dt = dt + timedelta(days=7)

  if not results:
    day = min(max(default_day, 1), last_day)
    results.append(date(year, month, day))

  dedup = sorted({d: None for d in results}.keys())
  return dedup


def _add_months(year: int, month: int, delta: int) -> Tuple[int, int]:
  total = (year * 12 + (month - 1)) + delta
  new_year = total // 12
  new_month = total % 12 + 1
  return new_year, new_month


def _collect_recurrence_dates(recurrence: Dict[str, Any],
                              start_date: date,
                              scope: Optional[Tuple[date, date]] = None) -> List[date]:
  freq = recurrence.get("freq")
  interval = int(recurrence.get("interval") or 1)
  interval = max(interval, 1)
  byweekday = recurrence.get("byweekday") or []
  bymonthday = recurrence.get("bymonthday") or []
  bysetpos = recurrence.get("bysetpos")
  bymonth = recurrence.get("bymonth") or []
  end = recurrence.get("end") or {}

  until_date: Optional[date] = None
  count: Optional[int] = None
  until_raw = end.get("until")
  if isinstance(until_raw, str) and ISO_DATE_RE.match(until_raw):
    try:
      until_date = datetime.strptime(until_raw, "%Y-%m-%d").date()
    except Exception:
      until_date = None
  count_raw = end.get("count")
  if count_raw is not None:
    try:
      count = int(count_raw)
    except Exception:
      count = None
    if count is not None and count <= 0:
      count = None
  if until_date and count:
    count = None

  scope_start = start_date
  limit_date = start_date + timedelta(days=MAX_RECURRENCE_EXPANSION_DAYS)
  scope_filter: Optional[Tuple[date, date]] = None

  if scope:
    if count is None:
      scope_start = max(scope_start, scope[0])
    else:
      scope_filter = scope
    limit_date = scope[1]

  if count is not None:
    count = min(count, MAX_RECURRENCE_OCCURRENCES)
    if not scope and not until_date:
      limit_days = MAX_RECURRENCE_EXPANSION_DAYS * count
      limit_date = max(limit_date, start_date + timedelta(days=limit_days))

  if until_date and until_date < limit_date:
    limit_date = until_date

  results: List[date] = []

  def push_date(d: date) -> bool:
    if d < scope_start:
      return False
    if d > limit_date:
      return False
    results.append(d)
    if len(results) >= MAX_RECURRENCE_OCCURRENCES:
      return True
    if count is not None and len(results) >= count:
      return True
    return False

  if freq == "DAILY":
    if scope_start <= start_date:
      cur = start_date
    else:
      delta_days = (scope_start - start_date).days
      offset = delta_days % interval
      cur = scope_start if offset == 0 else scope_start + timedelta(
          days=interval - offset)
    while cur <= limit_date:
      if push_date(cur):
        break
      cur += timedelta(days=interval)

  elif freq == "WEEKLY":
    weekdays = sorted({int(w) for w in byweekday
                       if isinstance(w, int) and 0 <= w <= 6})
    if not weekdays:
      weekdays = [start_date.weekday()]

    base = start_date - timedelta(days=start_date.weekday())
    week_index = 0
    while True:
      week_start = base + timedelta(days=week_index * interval * 7)
      if week_start > limit_date:
        break
      for w in weekdays:
        occ = week_start + timedelta(days=w)
        if occ < scope_start:
          continue
        if occ > limit_date:
          continue
        if push_date(occ):
          return results
      week_index += 1

  elif freq == "MONTHLY":
    month_index = 0
    while True:
      year, month = _add_months(start_date.year, start_date.month,
                                month_index * interval)
      first_day = date(year, month, 1)
      if first_day > limit_date:
        break
      candidates = _monthly_candidates(year, month, {
          "bymonthday": bymonthday,
          "byweekday": byweekday,
          "bysetpos": bysetpos
      }, start_date.day)
      for occ in candidates:
        if occ < scope_start:
          continue
        if occ > limit_date:
          continue
        if push_date(occ):
          return results
      month_index += 1

  elif freq == "YEARLY":
    months = [int(m) for m in bymonth if isinstance(m, int) and 1 <= m <= 12]
    if not months:
      months = [start_date.month]

    year_index = 0
    while True:
      year = start_date.year + year_index * interval
      first_day = date(year, 1, 1)
      if first_day > limit_date:
        break
      for month in months:
        candidates = _monthly_candidates(year, month, {
            "bymonthday": bymonthday,
            "byweekday": byweekday,
            "bysetpos": bysetpos
        }, start_date.day)
        for occ in candidates:
          if occ < scope_start:
            continue
          if occ > limit_date:
            continue
          if push_date(occ):
            return results
      year_index += 1

  results.sort()
  if scope_filter:
    return [d for d in results if scope_filter[0] <= d <= scope_filter[1]]
  return results


def _get_detail_value(detail: Any, key: str) -> Optional[int]:
  if detail is None:
    return None
  if isinstance(detail, dict):
    value = detail.get(key)
  else:
    value = getattr(detail, key, None)
  try:
    return int(value)
  except (TypeError, ValueError):
    return None


def _estimate_llm_cost(model_name: str, prompt_tokens: Optional[int],
                       cached_prompt_tokens: Optional[int],
                       completion_tokens: Optional[int]) -> Optional[Tuple[float,
                                                                           float]]:
  pricing = MODEL_PRICING.get(model_name)
  if not pricing:
    return None

  prompt = max(int(prompt_tokens or 0), 0)
  cached = max(int(cached_prompt_tokens or 0), 0)
  cached = min(cached, prompt)
  uncached = max(prompt - cached, 0)
  completion = max(int(completion_tokens or 0), 0)

  usd = ((uncached / 1_000_000) * pricing["input_per_m"] +
         (cached / 1_000_000) * pricing["cached_input_per_m"] +
         (completion / 1_000_000) * pricing["output_per_m"])
  krw = usd * USD_TO_KRW
  return usd, krw


async def _invoke_event_parser(kind: str,
                               text: str,
                               images: List[str],
                               reasoning_effort: Optional[str] = None,
                               model_name: Optional[str] = None,
                               context_cache_key: Optional[str] = None,
                               context_session_id: Optional[str] = None
                               ) -> Dict[str, Any]:
  payload = _build_events_user_payload(text, bool(images))
  cached_context = _get_context_cache(context_cache_key)
  if cached_context and _should_use_cached_context(text):
    payload_with_context = _build_events_user_payload(text, bool(images),
                                                      cached_context)
    if images:
      data = await _chat_multimodal_json(
          kind,
          build_events_multimodal_prompt_with_context(),
          payload_with_context,
          images,
          reasoning_effort=reasoning_effort,
          model_name=model_name)
    else:
      data = await _chat_json(kind,
                              build_events_system_prompt_with_context(),
                              payload_with_context,
                              reasoning_effort=reasoning_effort,
                              model_name=model_name)
    if isinstance(data, dict):
      data["context_used"] = True
    return data
  if images:
    data = await _chat_multimodal_json(kind,
                                       build_events_multimodal_prompt(),
                                       payload,
                                       images,
                                       reasoning_effort=reasoning_effort,
                                       model_name=model_name)
  else:
    data = await _chat_json(kind,
                            build_events_system_prompt(),
                            payload,
                            reasoning_effort=reasoning_effort,
                            model_name=model_name)

  needs_context, scopes = _extract_context_request(data)
  if not needs_context:
    if isinstance(data, dict):
      data["context_used"] = False
    return data

  context = _build_events_context(scopes, session_id=context_session_id)
  _set_context_cache(context_cache_key, context)
  payload_with_context = _build_events_user_payload(text, bool(images), context)

  if images:
    data = await _chat_multimodal_json(
        kind,
        build_events_multimodal_prompt_with_context(),
        payload_with_context,
        images,
        reasoning_effort=reasoning_effort,
        model_name=model_name)
  else:
    data = await _chat_json(kind,
                            build_events_system_prompt_with_context(),
                            payload_with_context,
                            reasoning_effort=reasoning_effort,
                            model_name=model_name)

  if isinstance(data, dict):
    data["context_used"] = True
  return data


def build_delete_system_prompt() -> str:
  return ("역할: '기존 일정 목록'과 '삭제 요청 문장'을 보고 삭제할 일정 id 목록만 고른다.\n"
          "항상 아래 형식의 JSON 한 개만 출력해라. 설명·코드블록·마크다운은 금지.\n\n"
          "{\n"
          '  \"ids\": [string | number]\n'
          "}\n\n"
          "규칙:\n"
          "- 문장과 명확히 매칭되는 일정의 id만 넣는다.\n"
          "- '전부', '모든 일정'이면 목록의 모든 id.\n"
          "- 애매하면 빈 배열[].\n")


# -------------------------
# LLM 호출 & 디버그
# -------------------------
def _debug_print(
    kind: str,
    input_text: str,
    system_prompt: str,
    raw_content: str,
    latency_ms: Optional[float] = None,
    usage: Optional[Dict[str, Any]] = None,
    model_name: str = "",
) -> None:
  if not LLM_DEBUG or kind == "preview":
    return

  head = system_prompt[:220].replace("\n", "\\n")
  _log_debug(f"[LLM DEBUG] kind: {kind}")
  _log_debug(f"[LLM DEBUG] input_text: {input_text}")
  _log_debug(f"[LLM DEBUG] system_prompt(head): {head}")
  _log_debug(f"[LLM DEBUG] raw_content: {raw_content}")
  if model_name:
    _log_debug(f"[LLM DEBUG] model: {model_name}")

  if latency_ms is not None:
    _log_debug(f"[LLM DEBUG] latency_ms: {latency_ms:.1f} ms")

  if usage is not None:
    p = usage.get("prompt")
    c = usage.get("completion")
    t = usage.get("total")
    cached_prompt = usage.get("cached_prompt")
    _log_debug(
        f"[LLM DEBUG] usage: prompt={p}, completion={c}, total={t}, cached_prompt={cached_prompt}"
    )
    cost = _estimate_llm_cost(model_name, p, cached_prompt, c)
    if cost:
      usd, krw = cost
      _log_debug(
          f"[LLM DEBUG] cost: ${usd:.6f} ≈ ₩{krw:,.0f} (model={model_name})")


def _safe_json_loads(raw: str) -> Dict[str, Any]:
  if not raw or not isinstance(raw, str):
    return {}
  raw = raw.strip()

  try:
    obj = json.loads(raw)
    return obj if isinstance(obj, dict) else {}
  except Exception:
    pass

  start = raw.find("{")
  end = raw.rfind("}")
  if start != -1 and end != -1 and end > start:
    try:
      obj = json.loads(raw[start:end + 1])
      return obj if isinstance(obj, dict) else {}
    except Exception:
      return {}

  return {}


def _current_reference_line() -> str:
  now = datetime.now(SEOUL)
  return f"기준 시각: {now.strftime('%Y-%m-%d')} (Asia/Seoul)\n"


def _resolve_request_id(raw: Optional[str]) -> str:
  if isinstance(raw, str) and raw.strip():
    return raw.strip()
  return secrets.token_hex(8)


async def _register_inflight(session_id: str, request_id: str,
                             task: asyncio.Task) -> None:
  async with inflight_lock:
    session_map = inflight_tasks.setdefault(session_id, {})
    existing = session_map.pop(request_id, None)
    if existing:
      existing.cancel()
    session_map[request_id] = task


async def _clear_inflight(session_id: str, request_id: str) -> None:
  async with inflight_lock:
    session_map = inflight_tasks.get(session_id)
    if not session_map:
      return
    session_map.pop(request_id, None)
    if not session_map:
      inflight_tasks.pop(session_id, None)


async def _cancel_inflight(session_id: str,
                           request_id: Optional[str] = None) -> int:
  async with inflight_lock:
    session_map = inflight_tasks.get(session_id)
    if not session_map:
      return 0
    if request_id:
      task = session_map.pop(request_id, None)
      if task:
        task.cancel()
        return 1
      return 0
    count = 0
    for task in list(session_map.values()):
      task.cancel()
      count += 1
    inflight_tasks.pop(session_id, None)
    return count


async def _run_with_interrupt(session_id: str, request_id: str, coro):
  task = asyncio.create_task(coro)
  await _register_inflight(session_id, request_id, task)
  try:
    return await task
  except asyncio.CancelledError:
    raise HTTPException(status_code=499, detail="요청이 중단되었습니다.")
  finally:
    await _clear_inflight(session_id, request_id)


async def _chat_json(kind: str,
                     system_prompt: str,
                     user_text: str,
                     reasoning_effort: Optional[str] = None,
                     model_name: Optional[str] = None) -> Dict[str, Any]:
  """
    gpt-5-nano를 Chat Completions로 호출하는 버전.
    - max_tokens 대신 max_completion_tokens 사용
    - temperature / reasoning 등 gpt-5-nano에서 에러나는 옵션은 보내지 않는다.
    """
  c = get_async_client()

  input_text = _current_reference_line() + user_text

  started = time.perf_counter()
  effort_value = _pick_reasoning_effort(reasoning_effort,
                                        DEFAULT_TEXT_REASONING_EFFORT)
  model = _sanitize_model(model_name) or DEFAULT_TEXT_MODEL
  try:
    completion = await c.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": input_text
            },
        ],
        max_completion_tokens=10000,
        reasoning_effort=effort_value,
        verbosity="low",
        response_format={"type": "json_object"},
    )

    latency_ms = (time.perf_counter() - started) * 1000.0

    choice = completion.choices[0]
    content = choice.message.content
    raw_content = content if isinstance(content, str) else ""

    usage_dict: Optional[Dict[str, Any]] = None
    usage_obj = getattr(completion, "usage", None)
    if usage_obj is not None:
      prompt_details = getattr(usage_obj, "prompt_tokens_details", None)
      cached_prompt = (_get_detail_value(prompt_details, "cached_tokens")
                       or _get_detail_value(prompt_details,
                                            "cached_prompt_tokens"))
      usage_dict = {
          "prompt": getattr(usage_obj, "prompt_tokens", None),
          "completion": getattr(usage_obj, "completion_tokens", None),
          "total": getattr(usage_obj, "total_tokens", None),
          "cached_prompt": cached_prompt,
      }

    _debug_print(kind, user_text, system_prompt, raw_content, latency_ms,
                 usage_dict, model)
    return _safe_json_loads(raw_content)

  except Exception as e:
    _log_debug(f"[LLM DEBUG] exception: {repr(e)}")
    raise


async def _chat_multimodal_json(kind: str,
                                system_prompt: str,
                                user_text: str,
                                images: List[str],
                                reasoning_effort: Optional[str] = None,
                                model_name: Optional[str] = None) -> Dict[str,
                                                                         Any]:
  c = get_async_client()

  user_parts: List[Dict[str, Any]] = [{
      "type": "text",
      "text": _current_reference_line() + user_text
  }]

  for img in images:
    user_parts.append({
        "type": "image_url",
        "image_url": {
            "url": img
        }
    })

  started = time.perf_counter()
  effort_value = _pick_reasoning_effort(reasoning_effort,
                                        DEFAULT_MULTIMODAL_REASONING_EFFORT)
  model = _sanitize_model(model_name) or DEFAULT_MULTIMODAL_MODEL
  try:
    completion = await c.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": [{
                    "type": "text",
                    "text": system_prompt
                }]
            },
            {
                "role": "user",
                "content": user_parts
            },
        ],
        max_completion_tokens=10000,
        reasoning_effort=effort_value,
        verbosity="low",
        response_format={"type": "json_object"},
    )

    latency_ms = (time.perf_counter() - started) * 1000.0

    choice = completion.choices[0]
    content = choice.message.content
    raw_content = content if isinstance(content, str) else ""

    usage_dict: Optional[Dict[str, Any]] = None
    usage_obj = getattr(completion, "usage", None)
    if usage_obj is not None:
      prompt_details = getattr(usage_obj, "prompt_tokens_details", None)
      cached_prompt = (_get_detail_value(prompt_details, "cached_tokens")
                       or _get_detail_value(prompt_details,
                                            "cached_prompt_tokens"))
      usage_dict = {
          "prompt": getattr(usage_obj, "prompt_tokens", None),
          "completion": getattr(usage_obj, "completion_tokens", None),
          "total": getattr(usage_obj, "total_tokens", None),
          "cached_prompt": cached_prompt,
      }

    _debug_print(kind, user_text, system_prompt, raw_content, latency_ms,
                 usage_dict, model)
    return _safe_json_loads(raw_content)

  except Exception as e:
    _log_debug(f"[LLM DEBUG] exception: {repr(e)}")
    raise


# -------------------------
# recurring 전개 & RRULE
# -------------------------
def _expand_recurring_item(item: Dict[str, Any],
                           scope: Optional[Tuple[date, date]] = None) -> List[Dict[str, Any]]:
  """
    recurring item -> 여러 개의 단일 일정 dict로 전개
    """
  title = (item.get("title") or "").strip()
  start_date_str = item.get("start_date")
  if not title or not isinstance(start_date_str, str):
    return []

  recurrence = _resolve_recurrence(item)
  if not recurrence:
    return []

  try:
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
  except Exception:
    return []

  time_str = item.get("time")
  duration_minutes = item.get("duration_minutes")
  location = item.get("location")
  timezone_str = item.get("timezone") or "Asia/Seoul"
  tzinfo = ZoneInfo(timezone_str)

  hh, mm = 0, 0
  time_valid = False
  if isinstance(time_str, str) and re.match(r"^\d{2}:\d{2}$",
                                            time_str.strip()):
    hh, mm = [int(x) for x in time_str.strip().split(":")]
    time_valid = 0 <= hh <= 23 and 0 <= mm <= 59

  dur: Optional[int] = None
  if duration_minutes is not None:
    try:
      dur = int(duration_minutes)
      if dur <= 0:
        dur = None
    except Exception:
      dur = None

  location_str = (location or "").strip() or None

  results: List[Dict[str, Any]] = []

  for cur in _collect_recurrence_dates(recurrence, start_date, scope=scope):
    if time_valid:
      start_dt = datetime(cur.year, cur.month, cur.day, hh, mm, tzinfo=tzinfo)
      start_str = start_dt.strftime("%Y-%m-%dT%H:%M")
      end_str: Optional[str] = None
      if dur is not None:
        end_dt = start_dt + timedelta(minutes=dur)
        end_str = end_dt.strftime("%Y-%m-%dT%H:%M")
    else:
      start_dt = datetime(cur.year, cur.month, cur.day, 0, 0, tzinfo=tzinfo)
      end_dt = datetime(cur.year, cur.month, cur.day, 23, 59, tzinfo=tzinfo)
      start_str = start_dt.strftime("%Y-%m-%dT%H:%M")
      end_str = end_dt.strftime("%Y-%m-%dT%H:%M")

    results.append({
        "title": title,
        "start": start_str,
        "end": end_str,
        "location": location_str,
        "recur": "recurring",
        "all_day": not time_valid
    })

  if scope:
    filtered: List[Dict[str, Any]] = []
    for ev in results:
      start_iso = ev.get("start")
      if not isinstance(start_iso, str):
        continue
      start_date_value = datetime.strptime(start_iso[:10], "%Y-%m-%d").date()
      if scope[0] <= start_date_value <= scope[1]:
        filtered.append(ev)
    return filtered

  return results


def _format_rrule_until(until_date: date,
                        time_str: Optional[str],
                        tz_name: str) -> str:
  if isinstance(time_str, str) and re.match(r"^\d{2}:\d{2}$",
                                            time_str.strip()):
    hh, mm = [int(x) for x in time_str.strip().split(":")]
    local_dt = datetime(until_date.year,
                        until_date.month,
                        until_date.day,
                        hh,
                        mm,
                        tzinfo=ZoneInfo(tz_name))
    utc_dt = local_dt.astimezone(timezone.utc)
    return utc_dt.strftime("%Y%m%dT%H%M%SZ")
  return until_date.strftime("%Y%m%d")


def _build_rrule_core(recurrence: Dict[str, Any],
                      start_date_str: str,
                      time_str: Optional[str],
                      tz_name: str) -> Optional[str]:
  freq = recurrence.get("freq")
  if freq not in {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}:
    return None

  interval = int(recurrence.get("interval") or 1)
  interval = max(interval, 1)
  byweekday = recurrence.get("byweekday") or []
  bymonthday = recurrence.get("bymonthday") or []
  bysetpos = recurrence.get("bysetpos")
  bymonth = recurrence.get("bymonth") or []
  end = recurrence.get("end") or {}

  try:
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
  except Exception:
    return None

  if freq == "WEEKLY" and not byweekday:
    byweekday = [start_date.weekday()]

  if freq == "MONTHLY" and not byweekday and not bymonthday:
    bymonthday = [start_date.day]

  if freq == "YEARLY":
    if not bymonth:
      bymonth = [start_date.month]
    if not byweekday and not bymonthday:
      bymonthday = [start_date.day]

  parts = [f"FREQ={freq}"]
  if interval != 1:
    parts.append(f"INTERVAL={interval}")

  if bymonth:
    parts.append("BYMONTH=" + ",".join(str(m) for m in bymonth))

  if bymonthday:
    parts.append("BYMONTHDAY=" + ",".join(str(d) for d in bymonthday))

  if byweekday:
    weekday_map = {
        0: "MO",
        1: "TU",
        2: "WE",
        3: "TH",
        4: "FR",
        5: "SA",
        6: "SU"
    }
    byday_list = []
    for w in byweekday:
      try:
        iw = int(w)
      except Exception:
        continue
      if iw in weekday_map:
        byday_list.append(weekday_map[iw])
    if byday_list:
      parts.append("BYDAY=" + ",".join(byday_list))

  if bysetpos is not None:
    try:
      parts.append(f"BYSETPOS={int(bysetpos)}")
    except Exception:
      pass

  until_raw = end.get("until")
  count_raw = end.get("count")
  until_date: Optional[date] = None
  if isinstance(until_raw, str) and ISO_DATE_RE.match(until_raw):
    try:
      until_date = datetime.strptime(until_raw, "%Y-%m-%d").date()
    except Exception:
      until_date = None

  count: Optional[int] = None
  if count_raw is not None:
    try:
      count = int(count_raw)
    except Exception:
      count = None
    if count is not None and count <= 0:
      count = None

  if until_date:
    parts.append("UNTIL=" + _format_rrule_until(until_date, time_str, tz_name))
  elif count:
    parts.append(f"COUNT={count}")

  return ";".join(parts)


def recurring_to_rrule(item: Dict[str, Any]) -> Optional[str]:
  """
    recurring item -> RRULE 문자열 (recurrence 기반)
    """
  recurrence = _resolve_recurrence(item)
  if not recurrence:
    return None

  start_date_str = item.get("start_date")
  if not isinstance(start_date_str, str):
    return None

  return _build_rrule_core(recurrence, start_date_str, item.get("time"),
                           "Asia/Seoul")


# -------------------------
# Google Calendar 유틸
# -------------------------
def is_gcal_configured() -> bool:
  return bool(ENABLE_GCAL and GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET
              and GOOGLE_REDIRECT_URI)


def _normalize_session_id(raw: Optional[str]) -> Optional[str]:
  if not isinstance(raw, str):
    return None
  value = raw.strip()
  if not value:
    return None
  if len(value) > 512:
    return None
  return value


def _session_key(session_id: str) -> str:
  return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _session_token_path(session_id: str) -> pathlib.Path:
  return GOOGLE_TOKEN_DIR / f"token_{_session_key(session_id)}.json"


def _ensure_token_dir() -> None:
  try:
    GOOGLE_TOKEN_DIR.mkdir(parents=True, exist_ok=True)
  except Exception:
    pass


def _get_session_id(request: Request) -> Optional[str]:
  return _normalize_session_id(request.cookies.get(SESSION_COOKIE_NAME))


def _new_session_id() -> str:
  return secrets.token_urlsafe(32)


def _ensure_session_id(request: Request, response: Response) -> str:
  session_id = _get_session_id(request)
  if session_id:
    return session_id
  session_id = _new_session_id()
  _set_cookie(response,
              SESSION_COOKIE_NAME,
              session_id,
              max_age=SESSION_COOKIE_MAX_AGE_SECONDS)
  return session_id


def _new_oauth_state() -> str:
  return secrets.token_urlsafe(16)


def _store_oauth_state(state_value: str, session_id: str) -> None:
  if not state_value or not session_id:
    return
  oauth_state_store[state_value] = {
      "session_id": session_id,
      "created_at": time.time(),
  }


def _pop_oauth_state(state_value: Optional[str]) -> Optional[str]:
  if not state_value:
    return None
  entry = oauth_state_store.pop(state_value, None)
  if not entry:
    return None
  created_at = entry.get("created_at")
  if created_at and (time.time() - float(created_at)) > OAUTH_STATE_MAX_AGE_SECONDS:
    return None
  return entry.get("session_id")


def _set_cookie(response: Response,
                name: str,
                value: str,
                max_age: Optional[int] = None) -> None:
  response.set_cookie(name,
                      value,
                      httponly=True,
                      samesite="lax",
                      secure=COOKIE_SECURE,
                      max_age=max_age,
                      path="/")


def _delete_cookie(response: Response, name: str) -> None:
  response.delete_cookie(name, path="/")


def _frontend_url(path: str) -> str:
  if not FRONTEND_BASE_URL:
    return path
  if not path.startswith("/"):
    path = f"/{path}"
  return f"{FRONTEND_BASE_URL}{path}"


def load_gcal_token_for_session(session_id: Optional[str]) -> Optional[Dict[str, Any]]:
  if not session_id:
    return None
  path = _session_token_path(session_id)
  if not path.exists():
    return None
  try:
    with path.open("r", encoding="utf-8") as f:
      return json.load(f)
  except Exception:
    return None


def load_gcal_token_for_request(request: Request) -> Optional[Dict[str, Any]]:
  session_id = _get_session_id(request)
  return load_gcal_token_for_session(session_id)


def save_gcal_token_for_session(session_id: str, data: Dict[str, Any]) -> None:
  if not session_id:
    return
  _ensure_token_dir()
  path = _session_token_path(session_id)
  path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                  encoding="utf-8")


def clear_gcal_token_for_session(session_id: Optional[str]) -> None:
  if not session_id:
    return
  try:
    path = _session_token_path(session_id)
    if path.exists():
      path.unlink()
  except Exception:
    pass


def _get_google_cache(session_id: str) -> Dict[str, Dict[str, Any]]:
  key = _session_key(session_id)
  cache = google_events_cache.get(key)
  if cache is None:
    cache = {}
    google_events_cache[key] = cache
  return cache


def _clear_google_cache(session_id: Optional[str]) -> None:
  if not session_id:
    return
  google_events_cache.pop(_session_key(session_id), None)


def _context_cache_key_for_session(session_id: Optional[str]) -> Optional[str]:
  if not session_id:
    return None
  return _session_key(session_id)


def _context_cache_key_for_session_mode(session_id: Optional[str],
                                        use_google: bool) -> Optional[str]:
  base = _context_cache_key_for_session(session_id)
  if not base:
    return None
  return f"{base}:{'google' if use_google else 'local'}"


def _get_context_cache(cache_key: Optional[str]) -> Optional[Dict[str, Any]]:
  if not cache_key:
    return None
  entry = context_cache.get(cache_key)
  if not entry:
    return None
  return entry.get("context")


def _set_context_cache(cache_key: Optional[str], context: Dict[str, Any]) -> None:
  if not cache_key:
    return
  context_cache[cache_key] = {
      "context": context,
  }


def _clear_context_cache(cache_key: Optional[str]) -> None:
  if not cache_key:
    return
  context_cache.pop(cache_key, None)


def _should_use_cached_context(text: str) -> bool:
  if not text:
    return False
  lowered = text.lower()
  return "assistant:" in lowered or "user:" in lowered or "사용자:" in text


def get_google_session_id(request: Request) -> Optional[str]:
  if is_admin(request) or not ENABLE_GCAL:
    return None
  session_id = _get_session_id(request)
  if not session_id:
    return None
  if load_gcal_token_for_session(session_id) is None:
    return None
  return session_id


def get_gcal_service(session_id: str):
  if not is_gcal_configured():
    raise RuntimeError("Google Calendar is not configured.")

  token_data = load_gcal_token_for_session(session_id)
  if not token_data:
    raise RuntimeError(
        "Google OAuth token not found. Run /auth/google/login first.")

  creds = Credentials.from_authorized_user_info(token_data, GCAL_SCOPES)

  if creds.expired and creds.refresh_token:
    creds.refresh(GoogleRequest())
    new_data = json.loads(creds.to_json())
    save_gcal_token_for_session(session_id, new_data)

  service = build("calendar", "v3", credentials=creds)
  return service


def gcal_create_single_event(title: str,
                             start_iso: str,
                             end_iso: Optional[str],
                             location: Optional[str],
                             all_day: Optional[bool] = None,
                             session_id: Optional[str] = None,
                             description: Optional[str] = None,
                             attendees: Optional[List[str]] = None,
                             reminders: Optional[List[int]] = None,
                             visibility: Optional[str] = None,
                             transparency: Optional[str] = None,
                             meeting_url: Optional[str] = None,
                             timezone_value: Optional[str] = None,
                             color_id: Optional[str] = None) -> Optional[str]:
  if not is_gcal_configured() or not session_id:
    return None

  try:
    service = get_gcal_service(session_id)
  except Exception as e:
    _log_debug(f"[GCAL] get service error: {e}")
    return None

  try:
    use_all_day = bool(all_day)
    if all_day is None:
      use_all_day = is_all_day_span(start_iso, end_iso)

    event_body: Dict[str, Any] = {"summary": title}
    merged_description = _merge_description(description, meeting_url)
    if merged_description is not None:
      event_body["description"] = merged_description
    attendees_value = _build_gcal_attendees(attendees)
    if attendees_value is not None:
      event_body["attendees"] = attendees_value
    reminders_value = _build_gcal_reminders(reminders)
    if reminders_value is not None:
      event_body["reminders"] = reminders_value
    visibility_value = _normalize_visibility(visibility)
    if visibility_value is not None:
      event_body["visibility"] = visibility_value
    transparency_value = _normalize_transparency(transparency)
    if transparency_value is not None:
      event_body["transparency"] = transparency_value
    color_value = _normalize_color_id(color_id)
    if color_value is not None:
      event_body["colorId"] = color_value

    if use_all_day:
      start_date_obj, end_exclusive = _compute_all_day_bounds(start_iso, end_iso)
      event_body["start"] = {"date": start_date_obj.strftime("%Y-%m-%d")}
      event_body["end"] = {"date": end_exclusive.strftime("%Y-%m-%d")}
    else:
      start_dt = datetime.strptime(start_iso,
                                   "%Y-%m-%dT%H:%M").replace(tzinfo=SEOUL)
      if end_iso:
        end_dt = datetime.strptime(end_iso,
                                   "%Y-%m-%dT%H:%M").replace(tzinfo=SEOUL)
      else:
        end_dt = start_dt + timedelta(hours=1)

      tz_value = timezone_value or "Asia/Seoul"
      event_body["start"] = {"dateTime": start_dt.isoformat(), "timeZone": tz_value}
      event_body["end"] = {"dateTime": end_dt.isoformat(), "timeZone": tz_value}

    if location:
      event_body["location"] = location

    created = service.events().insert(calendarId=GOOGLE_CALENDAR_ID,
                                      body=event_body).execute()
    return created.get("id")
  except Exception as e:
    _log_debug(f"[GCAL] create single event error: {e}")
    return None


def _build_gcal_event_body(title: Optional[str],
                           start_iso: Optional[str],
                           end_iso: Optional[str],
                           location: Optional[str],
                           all_day: Optional[bool],
                           description: Optional[str] = None,
                           attendees: Optional[List[str]] = None,
                           reminders: Optional[List[int]] = None,
                           visibility: Optional[str] = None,
                           transparency: Optional[str] = None,
                           meeting_url: Optional[str] = None,
                           timezone_value: Optional[str] = None,
                           color_id: Optional[str] = None) -> Dict[str, Any]:
  if not isinstance(start_iso, str) or not ISO_DATETIME_RE.match(start_iso):
    raise ValueError("Invalid start time for Google Calendar update.")

  use_all_day = bool(all_day)
  if all_day is None:
    use_all_day = is_all_day_span(start_iso, end_iso)

  body: Dict[str, Any] = {}
  if title is not None:
    body["summary"] = title
  if location is not None:
    body["location"] = location
  merged_description = _merge_description(description, meeting_url)
  if merged_description is not None:
    body["description"] = merged_description
  attendees_value = _build_gcal_attendees(attendees)
  if attendees_value is not None:
    body["attendees"] = attendees_value
  reminders_value = _build_gcal_reminders(reminders)
  if reminders_value is not None:
    body["reminders"] = reminders_value
  visibility_value = _normalize_visibility(visibility)
  if visibility_value is not None:
    body["visibility"] = visibility_value
  transparency_value = _normalize_transparency(transparency)
  if transparency_value is not None:
    body["transparency"] = transparency_value
  if color_id is not None:
    color_value = _normalize_color_id(color_id)
    if color_value is not None:
      body["colorId"] = color_value
    elif isinstance(color_id, str) and color_id.strip().lower() in {"default", ""}:
      body["colorId"] = None

  if use_all_day:
    start_date, end_exclusive = _compute_all_day_bounds(start_iso, end_iso)
    body["start"] = {"date": start_date.strftime("%Y-%m-%d")}
    body["end"] = {"date": end_exclusive.strftime("%Y-%m-%d")}
  else:
    start_dt = datetime.strptime(start_iso,
                                 "%Y-%m-%dT%H:%M").replace(tzinfo=SEOUL)
    if end_iso:
      end_dt = datetime.strptime(end_iso,
                                 "%Y-%m-%dT%H:%M").replace(tzinfo=SEOUL)
    else:
      end_dt = start_dt + timedelta(hours=1)
    tz_value = timezone_value or "Asia/Seoul"
    body["start"] = {"dateTime": start_dt.isoformat(), "timeZone": tz_value}
    body["end"] = {"dateTime": end_dt.isoformat(), "timeZone": tz_value}
  return body


def gcal_update_event(event_id: str,
                      title: Optional[str],
                      start_iso: Optional[str],
                      end_iso: Optional[str],
                      location: Optional[str],
                      all_day: Optional[bool],
                      session_id: Optional[str] = None,
                      description: Optional[str] = None,
                      attendees: Optional[List[str]] = None,
                      reminders: Optional[List[int]] = None,
                      visibility: Optional[str] = None,
                      transparency: Optional[str] = None,
                      meeting_url: Optional[str] = None,
                      timezone_value: Optional[str] = None,
                      color_id: Optional[str] = None) -> None:
  if not event_id:
    raise ValueError("event_id is empty")
  if not is_gcal_configured():
    raise RuntimeError("Google Calendar is not configured.")
  if not session_id:
    raise RuntimeError("Google OAuth session is missing.")

  service = get_gcal_service(session_id)
  body = _build_gcal_event_body(title,
                                start_iso,
                                end_iso,
                                location,
                                all_day,
                                description=description,
                                attendees=attendees,
                                reminders=reminders,
                                visibility=visibility,
                                transparency=transparency,
                                meeting_url=meeting_url,
                                timezone_value=timezone_value,
                                color_id=color_id)
  service.events().patch(calendarId=GOOGLE_CALENDAR_ID,
                         eventId=event_id,
                         body=body).execute()


def gcal_create_recurring_event(item: Dict[str, Any],
                                session_id: Optional[str] = None) -> Optional[str]:
  if not is_gcal_configured() or not session_id:
    return None

  rrule_core = recurring_to_rrule(item)
  if not rrule_core:
    return None

  title = (item.get("title") or "").strip()
  if not title:
    return None

  start_date_str = item.get("start_date")
  time_str = item.get("time")
  duration_minutes = item.get("duration_minutes")
  location = item.get("location")
  description = item.get("description")
  attendees = item.get("attendees")
  reminders = item.get("reminders")
  visibility = item.get("visibility")
  transparency = item.get("transparency")
  meeting_url = item.get("meeting_url")
  color_id = item.get("color_id")
  timezone_value = item.get("timezone") or "Asia/Seoul"

  if not isinstance(start_date_str, str):
    return None

  try:
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
  except Exception:
    return None

  all_day = not (isinstance(time_str, str)
                 and re.match(r"^\d{2}:\d{2}$", time_str.strip()))

  try:
    service = get_gcal_service(session_id)
  except Exception as e:
    _log_debug(f"[GCAL] get service error: {e}")
    return None

  try:
    event_body: Dict[str, Any] = {
        "summary": title,
        "recurrence": [f"RRULE:{rrule_core}"]
    }
    merged_description = _merge_description(description, meeting_url)
    if merged_description is not None:
      event_body["description"] = merged_description
    attendees_value = _build_gcal_attendees(attendees)
    if attendees_value is not None:
      event_body["attendees"] = attendees_value
    reminders_value = _build_gcal_reminders(reminders)
    if reminders_value is not None:
      event_body["reminders"] = reminders_value
    visibility_value = _normalize_visibility(visibility)
    if visibility_value is not None:
      event_body["visibility"] = visibility_value
    transparency_value = _normalize_transparency(transparency)
    if transparency_value is not None:
      event_body["transparency"] = transparency_value
    color_value = _normalize_color_id(color_id)
    if color_value is not None:
      event_body["colorId"] = color_value

    if all_day:
      start_date_str2 = start_date.strftime("%Y-%m-%d")
      end_date_excl = (start_date + timedelta(days=1)).strftime("%Y-%m-%d")
      event_body["start"] = {"date": start_date_str2}
      event_body["end"] = {"date": end_date_excl}
    else:
      hh, mm = [int(x) for x in time_str.strip().split(":")]
      start_dt = datetime(start_date.year,
                          start_date.month,
                          start_date.day,
                          hh,
                          mm,
                          tzinfo=SEOUL)
      if duration_minutes and isinstance(
          duration_minutes, (int, float)) and duration_minutes > 0:
        end_dt = start_dt + timedelta(minutes=int(duration_minutes))
      else:
        end_dt = start_dt + timedelta(hours=1)

      event_body["start"] = {
          "dateTime": start_dt.isoformat(),
          "timeZone": timezone_value
      }
      event_body["end"] = {
          "dateTime": end_dt.isoformat(),
          "timeZone": timezone_value
      }

    if location:
      event_body["location"] = (location or "").strip() or None

    created = service.events().insert(calendarId=GOOGLE_CALENDAR_ID,
                                      body=event_body).execute()
    return created.get("id")
  except Exception as e:
    _log_debug(f"[GCAL] create recurring event error: {e}")
    return None


def gcal_delete_event(event_id: str, session_id: Optional[str] = None) -> None:
  if not event_id:
    raise ValueError("event_id is empty")
  if not is_gcal_configured():
    raise RuntimeError("Google Calendar is not configured.")
  if not session_id:
    raise RuntimeError("Google OAuth session is missing.")
  service = get_gcal_service(session_id)
  service.events().delete(calendarId=GOOGLE_CALENDAR_ID, eventId=event_id).execute()


def _convert_gcal_time(obj: Dict[str, Any],
                       is_end: bool,
                       start_iso: Optional[str]) -> Tuple[Optional[str], bool]:
  if not isinstance(obj, dict):
    return (None, False)

  dt_value = obj.get("dateTime")
  if isinstance(dt_value, str):
    try:
      dt = datetime.fromisoformat(dt_value.replace("Z", "+00:00"))
      dt = dt.astimezone(SEOUL)
      return (dt.strftime("%Y-%m-%dT%H:%M"), False)
    except Exception:
      return (None, False)

  date_value = obj.get("date")
  if isinstance(date_value, str):
    try:
      date_obj = datetime.strptime(date_value, "%Y-%m-%d").date()
    except Exception:
      return (None, True)

    if not is_end:
      return (date_obj.strftime("%Y-%m-%dT00:00"), True)

    inclusive = date_obj - timedelta(days=1)
    if start_iso:
      start_date, _ = _split_iso_date_time(start_iso)
      if start_date and inclusive < start_date:
        inclusive = start_date
    return (inclusive.strftime("%Y-%m-%dT23:59"), True)

  return (None, False)


class SyncTokenInvalid(Exception):

  def __init__(self, kind: str = "invalid") -> None:
    super().__init__(kind)
    self.kind = kind


def _google_cache_key(range_start: date, range_end: date) -> str:
  return f"{range_start.isoformat()}:{range_end.isoformat()}"


def _event_in_date_range(ev: Dict[str, Any],
                         range_start: date,
                         range_end: date) -> bool:
  start_date, _ = _split_iso_date_time(ev.get("start"))
  if not start_date:
    return False
  end_date, _ = _split_iso_date_time(ev.get("end"))
  if not end_date:
    end_date = start_date
  if end_date < range_start or start_date > range_end:
    return False
  return True


def _normalize_gcal_event(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
  start_raw = raw.get("start") or {}
  start_iso, all_day_flag = _convert_gcal_time(start_raw, False, None)
  if not start_iso:
    return None
  end_raw = raw.get("end") or {}
  end_iso, _ = _convert_gcal_time(end_raw, True, start_iso)
  attendees_raw = raw.get("attendees")
  attendees: Optional[List[str]] = None
  if isinstance(attendees_raw, list):
    cleaned_attendees: List[str] = []
    for item in attendees_raw:
      if not isinstance(item, dict):
        continue
      email = item.get("email")
      if isinstance(email, str) and email.strip():
        cleaned_attendees.append(email.strip())
    attendees = cleaned_attendees or None

  reminders: Optional[List[int]] = None
  reminders_raw = raw.get("reminders") or {}
  overrides = reminders_raw.get("overrides")
  if isinstance(overrides, list):
    cleaned_reminders: List[int] = []
    for item in overrides:
      if not isinstance(item, dict):
        continue
      minutes = item.get("minutes")
      try:
        minutes_value = int(minutes)
      except Exception:
        continue
      if minutes_value >= 0:
        cleaned_reminders.append(minutes_value)
    reminders = cleaned_reminders or None

  meeting_url = raw.get("hangoutLink")
  if not meeting_url:
    conference = raw.get("conferenceData") or {}
    entry_points = conference.get("entryPoints")
    if isinstance(entry_points, list):
      for entry in entry_points:
        if not isinstance(entry, dict):
          continue
        uri = entry.get("uri")
        if isinstance(uri, str) and uri.strip():
          meeting_url = uri.strip()
          break

  timezone_value = None
  if isinstance(start_raw, dict):
    tz_raw = start_raw.get("timeZone")
    if isinstance(tz_raw, str) and tz_raw.strip():
      timezone_value = tz_raw.strip()

  return {
      "id": raw.get("id"),
      "title": raw.get("summary") or "(제목 없음)",
      "start": start_iso,
      "end": end_iso,
      "location": raw.get("location"),
      "description": raw.get("description"),
      "attendees": attendees,
      "reminders": reminders,
      "visibility": raw.get("visibility"),
      "transparency": raw.get("transparency"),
      "meeting_url": meeting_url,
      "timezone": timezone_value,
      "color_id": raw.get("colorId"),
      "all_day": all_day_flag,
      "status": raw.get("status"),
      "html_link": raw.get("htmlLink"),
      "organizer": (raw.get("organizer") or {}).get("email"),
      "created": raw.get("created"),
      "updated": raw.get("updated"),
  }


def _sorted_google_cache_items(cache: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
  items = list(cache.values())
  items.sort(key=lambda ev: ev.get("start") or "")
  return items


def _sync_token_error_kind(exc: Exception) -> Optional[str]:
  if not isinstance(exc, HttpError):
    return None
  status = getattr(exc.resp, "status", None)
  content = getattr(exc, "content", None)
  if isinstance(content, (bytes, bytearray)):
    content = content.decode("utf-8", errors="ignore")
  content_str = str(content or "")
  if status == 410:
    return "invalid"
  if status == 400 and "syncToken" in content_str:
    if "timeMin" in content_str or "timeMax" in content_str or "orderBy" in content_str:
      return "unsupported"
    return "invalid"
  return None


def _fetch_google_events_raw(service,
                             range_start: date,
                             range_end: date,
                             sync_token: Optional[str] = None
                             ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
  time_min = datetime(range_start.year,
                      range_start.month,
                      range_start.day,
                      0,
                      0,
                      tzinfo=SEOUL)
  time_max = datetime(range_end.year,
                      range_end.month,
                      range_end.day,
                      0,
                      0,
                      tzinfo=SEOUL) + timedelta(days=1)

  events_data: List[Dict[str, Any]] = []
  page_token: Optional[str] = None
  next_sync_token: Optional[str] = None

  while True:
    params: Dict[str, Any] = {
        "calendarId": GOOGLE_CALENDAR_ID,
        "singleEvents": True,
        "pageToken": page_token,
        "timeMin": time_min.isoformat(),
        "timeMax": time_max.isoformat(),
    }
    if sync_token:
      params["syncToken"] = sync_token
      params["showDeleted"] = True
    else:
      params["orderBy"] = "startTime"

    request = service.events().list(**params)
    try:
      response = request.execute()
    except HttpError as exc:
      if sync_token:
        kind = _sync_token_error_kind(exc)
        if kind:
          raise SyncTokenInvalid(kind) from exc
      raise

    items = response.get("items", [])
    if isinstance(items, list):
      events_data.extend(items)

    page_token = response.get("nextPageToken")
    if not page_token:
      next_sync_token = response.get("nextSyncToken") or next_sync_token
      break

  return events_data, next_sync_token


def _apply_gcal_items_to_cache(cache: Dict[str, Dict[str, Any]],
                               raw_items: List[Dict[str, Any]],
                               range_start: date,
                               range_end: date) -> None:
  for raw in raw_items:
    if not isinstance(raw, dict):
      continue
    event_id = raw.get("id")
    if not event_id:
      continue
    if raw.get("status") == "cancelled":
      cache.pop(event_id, None)
      continue
    normalized = _normalize_gcal_event(raw)
    if not normalized:
      continue
    if _event_in_date_range(normalized, range_start, range_end):
      cache[event_id] = normalized
    else:
      cache.pop(event_id, None)


def fetch_google_events_between(range_start: date,
                                range_end: date,
                                session_id: str) -> List[Dict[str, Any]]:
  if not is_gcal_configured():
    raise HTTPException(status_code=400,
                        detail="Google Calendar 연동이 설정되지 않았습니다.")
  if not session_id:
    raise HTTPException(status_code=401, detail="Google 로그인이 필요합니다.")

  try:
    service = get_gcal_service(session_id)
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google Calendar 인증에 실패했습니다: {exc}") from exc

  session_cache = _get_google_cache(session_id)
  cache_key = _google_cache_key(range_start, range_end)
  cache_entry = session_cache.get(cache_key)
  if cache_entry and cache_entry.get("sync_token") and not cache_entry.get("sync_disabled"):
    try:
      raw_items, next_sync = _fetch_google_events_raw(service,
                                                      range_start,
                                                      range_end,
                                                      sync_token=cache_entry.get(
                                                          "sync_token"))
      cache_events = cache_entry.get("events") or {}
      if not isinstance(cache_events, dict):
        cache_events = {}
      _apply_gcal_items_to_cache(cache_events, raw_items, range_start, range_end)
      cache_entry["events"] = cache_events
      if next_sync:
        cache_entry["sync_token"] = next_sync
      cache_entry["updated_at"] = _now_iso_minute()
      return _sorted_google_cache_items(cache_events)
    except SyncTokenInvalid as exc:
      cache_entry["sync_token"] = None
      if getattr(exc, "kind", "") == "unsupported":
        cache_entry["sync_disabled"] = True

  raw_items, next_sync = _fetch_google_events_raw(service, range_start,
                                                  range_end)
  events_by_id: Dict[str, Dict[str, Any]] = {}
  _apply_gcal_items_to_cache(events_by_id, raw_items, range_start, range_end)
  session_cache[cache_key] = {
      "events": events_by_id,
      "sync_token": next_sync,
      "sync_disabled": False,
      "updated_at": _now_iso_minute(),
  }
  return _sorted_google_cache_items(events_by_id)


def fetch_recent_google_events(session_id: str,
                               days: int = GOOGLE_RECENT_DAYS) -> List[Dict[str, Any]]:
  if days <= 0:
    days = GOOGLE_RECENT_DAYS

  if not is_gcal_configured():
    raise HTTPException(status_code=400,
                        detail="Google Calendar 연동이 설정되지 않았습니다.")
  if not session_id:
    raise HTTPException(status_code=401, detail="Google 로그인이 필요합니다.")

  try:
    service = get_gcal_service(session_id)
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google Calendar 인증에 실패했습니다: {exc}") from exc

  now = datetime.now(SEOUL)
  time_min = now - timedelta(days=days)

  events_data: List[Dict[str, Any]] = []
  page_token: Optional[str] = None

  while True:
    request = service.events().list(calendarId=GOOGLE_CALENDAR_ID,
                                    updatedMin=time_min.astimezone(
                                        timezone.utc).isoformat().replace(
                                            "+00:00", "Z"),
                                    singleEvents=True,
                                    orderBy="updated",
                                    maxResults=100,
                                    pageToken=page_token)
    response = request.execute()
    items = response.get("items", [])

    for raw in items:
      if not isinstance(raw, dict):
        continue

      start_raw = raw.get("start") or {}
      start_iso, all_day_flag = _convert_gcal_time(start_raw, False, None)
      if not start_iso:
        continue
      end_raw = raw.get("end") or {}
      end_iso, _ = _convert_gcal_time(end_raw, True, start_iso)

      events_data.append({
          "id": raw.get("id"),
          "title": raw.get("summary") or "(제목 없음)",
          "start": start_iso,
          "end": end_iso,
          "location": raw.get("location"),
          "all_day": all_day_flag,
          "status": raw.get("status"),
          "html_link": raw.get("htmlLink"),
          "organizer": (raw.get("organizer") or {}).get("email"),
          "created": raw.get("created"),
          "updated": raw.get("updated"),
      })

    page_token = response.get("nextPageToken")
    if not page_token:
      break

  events_data.sort(key=lambda ev: ev.get("updated") or ev.get("created") or "",
                   reverse=True)
  return events_data


# -------------------------
# 자연어 → 일정 생성(기존)
# -------------------------
async def create_events_from_natural_text_core(
    text: str,
    images: Optional[List[str]] = None,
    reasoning_effort: Optional[str] = None,
    model_name: Optional[str] = None,
    context_cache_key: Optional[str] = None,
    context_session_id: Optional[str] = None,
    session_id: Optional[str] = None) -> List[Event]:
  images = images or []
  t = normalize_text(text)
  if not t and not images:
    raise HTTPException(status_code=400, detail="문장이나 이미지를 입력해주세요.")

  if async_client is None:
    raise HTTPException(
        status_code=500,
        detail="LLM client is not configured (OPENAI_API_KEY 미설정)")

  data = await _invoke_event_parser("preview",
                                    t,
                                    images,
                                    reasoning_effort,
                                    model_name=model_name,
                                    context_cache_key=context_cache_key,
                                    context_session_id=context_session_id)
  if _parse_bool(data.get("need_more_information")):
    content = (data.get("content") or "").strip()
    raise HTTPException(
        status_code=422,
        detail={
            "need_more_information": True,
            "content": content or "추가로 확인할 정보가 필요합니다.",
        })
  items = data.get("items")
  if not isinstance(items, list):
    raise HTTPException(status_code=502, detail="LLM 응답 형식 오류입니다.")

  if len(items) == 0:
    raise HTTPException(status_code=422,
                        detail="일정을 만들 수 없습니다. 날짜/시간/기간이 모호한 문장입니다.")

  flat_events: List[Dict[str, Any]] = []
  recurring_items: List[Dict[str, Any]] = []

  for item in items:
    if not isinstance(item, dict):
      continue

    typ = (item.get("type") or "").strip().lower()

    if typ == "single":
      title = (item.get("title") or "").strip()
      start = item.get("start")
      end = item.get("end")
      location = item.get("location")

      start_iso, end_iso, all_day = _normalize_single_event_times(
          start, end)

      if not title or not start_iso:
        continue

      loc_str = (location or "").strip() or None

      flat_events.append({
          "title": title,
          "start": start_iso,
          "end": end_iso,
          "location": loc_str,
          "recur": None,
          "source_type": "single",
          "all_day": all_day
      })

    elif typ == "recurring":
      expanded = _expand_recurring_item(item)
      if not expanded:
        continue

      if len(expanded) == 1:
        only = expanded[0]
        title = (only.get("title") or "").strip()
        start_single = only.get("start")
        if not title or not isinstance(start_single, str):
          continue
        flat_events.append({
            "title": title,
            "start": start_single,
            "end": only.get("end"),
            "location": only.get("location"),
            "recur": None,
            "source_type": "single",
            "all_day": bool(only.get("all_day"))
        })
        continue

      flat_events.extend({
          **ev, "source_type": "recurring"
      } for ev in expanded)
      recurring_items.append(item)

  if not flat_events:
    raise HTTPException(status_code=422,
                        detail="일정을 만들 수 없습니다. 날짜/시간/형식을 다시 한 번 명확히 적어주세요.")

  created: List[Event] = []

  for ev in flat_events:
    title = ev["title"]
    start = ev["start"]
    end = ev.get("end")
    location = ev.get("location")
    recur = ev.get("recur")
    all_day_flag = bool(ev.get("all_day"))
    google_event_id: Optional[str] = None

    if ev.get("source_type") == "single":
      google_event_id = gcal_create_single_event(title, start, end, location,
                                                 all_day_flag,
                                                 session_id=session_id,
                                                 description=ev.get("description"),
                                                 attendees=ev.get("attendees"),
                                                 reminders=ev.get("reminders"),
                                                 visibility=ev.get("visibility"),
                                                 transparency=ev.get("transparency"),
                                                 meeting_url=ev.get("meeting_url"),
                                                 timezone_value=ev.get("timezone"))

    created.append(
        store_event(title=title,
                    start=start,
                    end=end,
                    location=location,
                    recur=recur,
                    google_event_id=google_event_id,
                    all_day=all_day_flag,
                    description=ev.get("description"),
                    attendees=ev.get("attendees"),
                    reminders=ev.get("reminders"),
                    visibility=ev.get("visibility"),
                    transparency=ev.get("transparency"),
                    meeting_url=ev.get("meeting_url"),
                    timezone_value=ev.get("timezone")))

  for rec_item in recurring_items:
    gcal_create_recurring_event(rec_item, session_id=session_id)

  return created


# -------------------------
# NLP Preview (추가 미리보기)
# -------------------------
async def preview_events_from_natural_text_core(
    text: str,
    images: Optional[List[str]] = None,
    reasoning_effort: Optional[str] = None,
    model_name: Optional[str] = None,
    context_cache_key: Optional[str] = None,
    context_session_id: Optional[str] = None) -> Dict[str, Any]:
  images = images or []
  t = normalize_text(text)
  if not t and not images:
    raise HTTPException(status_code=400, detail="문장이나 이미지를 입력해주세요.")

  if async_client is None:
    raise HTTPException(
        status_code=500,
        detail="LLM client is not configured (OPENAI_API_KEY 미설정)")

  data = await _invoke_event_parser("parse",
                                    t,
                                    images,
                                    reasoning_effort,
                                    model_name=model_name,
                                    context_cache_key=context_cache_key,
                                    context_session_id=context_session_id)
  context_used = _parse_bool(data.get("context_used"))
  if _parse_bool(data.get("need_more_information")):
    content = (data.get("content") or "").strip()
    return {
        "need_more_information": True,
        "content": content or "추가로 확인할 정보가 필요합니다.",
        "items": [],
        "context_used": context_used,
    }
  items = data.get("items")
  if not isinstance(items, list) or len(items) == 0:
    raise HTTPException(status_code=422, detail="미리보기를 만들 수 없습니다.")

  out_items: List[Dict[str, Any]] = []

  for item in items:
    if not isinstance(item, dict):
      continue

    typ = (item.get("type") or "").strip().lower()

    if typ == "single":
      title = (item.get("title") or "").strip()
      start = item.get("start")
      end = item.get("end")
      location = item.get("location")

      start_iso, end_iso, all_day = _normalize_single_event_times(
          start, end)
      if not title or not start_iso:
        continue

      out_items.append({
          "type": "single",
          "title": title,
          "start": start_iso,
          "end": end_iso,
          "location": (location or "").strip() or None,
          "all_day": all_day
      })

    elif typ == "recurring":
      title = (item.get("title") or "").strip()
      if not title:
        continue

      recurrence = _resolve_recurrence(item)
      expanded = _expand_recurring_item(item)
      count = len(expanded)

      if count == 1:
        only = expanded[0]
        single_title = (only.get("title") or title).strip()
        start_single = only.get("start")
        if not single_title or not isinstance(start_single, str):
          continue
        out_items.append({
            "type": "single",
            "title": single_title,
            "start": start_single,
            "end": only.get("end"),
            "location": only.get("location"),
            "all_day": bool(only.get("all_day"))
        })
        continue

      samples = []
      for ev in expanded[:5]:
        samples.append({"start": ev.get("start"), "end": ev.get("end")})

      occurrences = []
      for idx, ev in enumerate(expanded):
        occurrences.append({
            "index": idx,
            "title": ev.get("title"),
            "start": ev.get("start"),
            "end": ev.get("end"),
            "location": ev.get("location"),
            "all_day": ev.get("all_day", False)
        })

      display_end_date = None
      legacy_end = item.get("end_date")
      if isinstance(legacy_end, str) and ISO_DATE_RE.match(legacy_end):
        display_end_date = legacy_end
      elif recurrence and recurrence.get("end"):
        rec_end = recurrence.get("end") or {}
        until_val = rec_end.get("until")
        if isinstance(until_val, str) and ISO_DATE_RE.match(until_val):
          display_end_date = until_val
        elif rec_end.get("count") and occurrences:
          last_start = occurrences[-1].get("start")
          if isinstance(last_start, str) and len(last_start) >= 10:
            display_end_date = last_start[:10]

      display_weekdays = item.get("weekdays")
      if not isinstance(display_weekdays, list) and recurrence:
        display_weekdays = recurrence.get("byweekday")
      requires_end = False
      if recurrence:
        rec_end_val = recurrence.get("end")
        requires_end = not (rec_end_val and (rec_end_val.get("until") or rec_end_val.get("count")))
      elif not display_end_date:
        requires_end = True

      finite_count = count if recurrence and recurrence.get("end") else None

      out_items.append({
          "type": "recurring",
          "title": title,
          "start_date": item.get("start_date"),
          "end_date": display_end_date,
          "weekdays": display_weekdays,
          "time": item.get("time"),
          "duration_minutes": item.get("duration_minutes"),
          "location": (item.get("location") or "").strip() or None,
          "recurrence": recurrence,
          "count": finite_count,
          "samples": [x.get("start") for x in samples if x.get("start")],
          "all_day":
          len(expanded) > 0 and bool(expanded[0].get("all_day")),
          "occurrences": occurrences,
          "requires_end_confirmation": requires_end,
      })

  if not out_items:
    raise HTTPException(status_code=422, detail="미리보기를 만들 수 없습니다.")

  return {"items": out_items, "context_used": context_used}


# -------------------------
# 선택 적용: Add (모달에서 체크한 것만)
# -------------------------
def apply_add_items_core(items: List[Dict[str, Any]],
                         session_id: Optional[str] = None) -> List[Event]:
  created: List[Event] = []
  touched_google = False

  for item in items:
    if not isinstance(item, dict):
      continue

    typ = (item.get("type") or "").strip().lower()

    if typ == "single":
      title = (item.get("title") or "").strip()
      start = (item.get("start") or "").strip()
      end = item.get("end")
      location = (item.get("location") or "").strip() or None
      all_day_flag = bool(item.get("all_day"))

      if not title or not ISO_DATETIME_RE.match(start):
        continue

      end_str = _normalize_end_datetime(end)
      if not all_day_flag:
        all_day_flag = is_all_day_span(start, end_str)

      google_event_id = gcal_create_single_event(title, start, end_str,
                                                 location,
                                                 all_day_flag,
                                                 session_id=session_id,
                                                 description=item.get("description"),
                                                 attendees=item.get("attendees"),
                                                 reminders=item.get("reminders"),
                                                 visibility=item.get("visibility"),
                                                 transparency=item.get("transparency"),
                                                 meeting_url=item.get("meeting_url"),
                                                 timezone_value=item.get("timezone"),
                                                 color_id=item.get("color_id"))
      if session_id and google_event_id:
        touched_google = True

      created.append(
          store_event(
              title=title,
              start=start,
              end=end_str,
              location=location,
              recur=None,
              google_event_id=google_event_id,
              all_day=all_day_flag,
              description=item.get("description"),
              attendees=item.get("attendees"),
              reminders=item.get("reminders"),
              visibility=item.get("visibility"),
              transparency=item.get("transparency"),
              meeting_url=item.get("meeting_url"),
              timezone_value=item.get("timezone"),
              color_id=_normalize_color_id(item.get("color_id")),
          ))

    elif typ == "recurring":
      recurrence_spec = _resolve_recurrence(item)
      if not recurrence_spec:
        continue
      override = item.get("recurring_end_override")
      if isinstance(override, dict):
        mode = override.get("mode")
        if mode == "none":
          recurrence_spec = {
              **recurrence_spec, "end": None,
          }
        elif mode == "until":
          until_val = override.get("value")
          if isinstance(until_val, str) and ISO_DATE_RE.match(until_val):
            recurrence_spec = {
                **recurrence_spec,
                "end": {
                    "until": until_val,
                    "count": None
                }
            }
        elif mode == "count":
          try:
            count_val = int(override.get("value"))
          except Exception:
            count_val = None
          if count_val and count_val > 0:
            recurrence_spec = {
                **recurrence_spec,
                "end": {
                    "until": None,
                    "count": count_val
                }
            }
      item["recurrence"] = recurrence_spec

      expanded_all = _expand_recurring_item(item)
      if not expanded_all:
        continue

      selected_idx_raw = item.get("selected_occurrence_indexes")
      selected_idx: Optional[List[int]] = None
      if isinstance(selected_idx_raw, list):
        dedup: List[int] = []
        seen: set[int] = set()
        for val in selected_idx_raw:
          try:
            i = int(val)
          except Exception:
            continue
          if i < 0 or i >= len(expanded_all) or i in seen:
            continue
          seen.add(i)
          dedup.append(i)
        if dedup:
          selected_idx = dedup

      expanded = ([
          ev for idx, ev in enumerate(expanded_all) if selected_idx is None
          or idx in selected_idx
      ])

      if not expanded:
        continue

      full_recurring = selected_idx is None or len(expanded) == len(expanded_all)
      google_recur_id: Optional[str] = None
      if full_recurring:
        google_recur_id = gcal_create_recurring_event(item, session_id=session_id)
        if session_id and google_recur_id:
          touched_google = True
        start_date_value = item.get("start_date")
        if not isinstance(start_date_value, str):
          continue
        stored = store_recurring_event(
            title=(item.get("title") or "").strip(),
            start_date=start_date_value,
            time=item.get("time"),
            duration_minutes=item.get("duration_minutes"),
            location=(item.get("location") or "").strip() or None,
            description=item.get("description"),
            attendees=item.get("attendees"),
            reminders=item.get("reminders"),
            visibility=item.get("visibility"),
            transparency=item.get("transparency"),
            meeting_url=item.get("meeting_url"),
            recurrence=recurrence_spec,
            timezone_value=item.get("timezone") or "Asia/Seoul",
            color_id=_normalize_color_id(item.get("color_id")),
            google_event_id=google_recur_id,
        )
        created.append(_recurring_definition_to_event(stored))
        continue

      for ev in expanded:
        all_day_flag = bool(ev.get("all_day"))
        google_single_id = gcal_create_single_event(ev["title"], ev["start"],
                                                    ev.get("end"),
                                                    ev.get("location"),
                                                    all_day_flag,
                                                    session_id=session_id,
                                                    description=item.get("description"),
                                                    attendees=item.get("attendees"),
                                                    reminders=item.get("reminders"),
                                                    visibility=item.get("visibility"),
                                                    transparency=item.get("transparency"),
                                                    meeting_url=item.get("meeting_url"),
                                                    timezone_value=item.get("timezone"),
                                                    color_id=item.get("color_id"))
        if session_id and google_single_id:
          touched_google = True
        created.append(
            store_event(
                title=ev["title"],
                start=ev["start"],
                end=ev.get("end"),
                location=ev.get("location"),
                recur=None,
                google_event_id=google_single_id,
                all_day=all_day_flag,
                description=item.get("description"),
                attendees=item.get("attendees"),
                reminders=item.get("reminders"),
                visibility=item.get("visibility"),
                transparency=item.get("transparency"),
                meeting_url=item.get("meeting_url"),
                timezone_value=item.get("timezone"),
                color_id=_normalize_color_id(item.get("color_id")),
            ))

  if not created:
    raise HTTPException(status_code=422, detail="No valid items to create")

  if touched_google and session_id:
    _clear_google_cache(session_id)

  return created


# -------------------------
# 삭제 NLP (기존)
# -------------------------
async def create_delete_ids_from_natural_text(
    text: str,
    scope: Optional[Tuple[date, date]] = None,
    reasoning_effort: Optional[str] = None,
    model_name: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[Union[int, str]]:
  if async_client is None:
    return []

  if session_id and scope:
    google_items = fetch_google_events_between(scope[0], scope[1], session_id)
    snapshot = [{
        "id": item.get("id"),
        "title": item.get("title"),
        "start": item.get("start"),
        "end": item.get("end"),
        "location": item.get("location"),
        "recur": None,
    } for item in google_items][:50]
  else:
    snapshot = [{
        "id": e.id,
        "title": e.title,
        "start": e.start,
        "end": e.end,
        "location": e.location,
        "recur": e.recur
    } for e in events if _event_within_scope(e, scope)][:50]

  if not session_id:
    if len(snapshot) < 50:
      rec_occurrences = _collect_local_recurring_occurrences(scope=scope)
      for occ in rec_occurrences:
        snapshot.append({
            "id": occ.id,
            "title": occ.title,
            "start": occ.start,
            "end": occ.end,
            "location": occ.location,
            "recur": occ.recur
        })
        if len(snapshot) >= 50:
          break

    if len(snapshot) < 50:
      for rec in recurring_events:
        rec_ev = _recurring_definition_to_event(rec)
        if not scope or _event_within_scope(rec_ev, scope):
          snapshot.append({
              "id": rec_ev.id,
              "title": rec_ev.title,
              "start": rec_ev.start,
              "end": rec_ev.end,
              "location": rec_ev.location,
              "recur": rec_ev.recur
          })
          if len(snapshot) >= 50:
            break

  user_payload = {
      "existing_events": snapshot,
      "delete_request": normalize_text(text),
      "timezone": "Asia/Seoul"
  }

  data = await _chat_json("delete",
                          build_delete_system_prompt(),
                          json.dumps(user_payload, ensure_ascii=False),
                          reasoning_effort=reasoning_effort,
                          model_name=model_name)

  ids = data.get("ids")
  if not isinstance(ids, list):
    return []

  if session_id:
    cleaned: List[str] = []
    seen = set()
    for x in ids:
      if x is None:
        continue
      value = str(x).strip()
      if not value:
        continue
      if value in seen:
        continue
      seen.add(value)
      cleaned.append(value)
    return cleaned

  cleaned: List[int] = []
  seen = set()
  for x in ids:
    try:
      i = int(x)
    except Exception:
      continue
    if i < 0:
      rec_target = _decode_occurrence_id(i)
      if rec_target:
        i = rec_target
    if i not in seen:
      seen.add(i)
      cleaned.append(i)

  return cleaned


def delete_events_by_ids(ids: List[int]) -> List[int]:
  global events
  if not ids:
    return []

  normalized_ids: List[int] = []
  for raw in ids:
    if raw < 0:
      rec_target = _decode_occurrence_id(raw)
      if rec_target:
        normalized_ids.append(rec_target)
    else:
      normalized_ids.append(raw)

  id_set = set(normalized_ids)
  deleted: List[int] = []

  remaining: List[Event] = []
  for ev in events:
    if ev.id in id_set:
      deleted.append(ev.id)
    else:
      remaining.append(ev)
  events = remaining

  for raw_id in list(id_set):
    if raw_id in deleted:
      continue
    if _delete_recurring_event(raw_id, persist=False):
      deleted.append(raw_id)

  if deleted:
    _save_events_to_disk()
  return sorted(deleted)


# -------------------------
# 삭제 미리보기(그룹화)
# -------------------------
async def delete_preview_groups(text: str,
                                scope: Optional[Tuple[date, date]] = None,
                                reasoning_effort: Optional[str] = None,
                                model_name: Optional[str] = None,
                                session_id: Optional[str] = None
                                ) -> Dict[str, Any]:
  text = normalize_text(text)
  if not text:
    return {"groups": []}

  ids = await create_delete_ids_from_natural_text(text,
                                                  scope=scope,
                                                  reasoning_effort=reasoning_effort,
                                                  model_name=model_name,
                                                  session_id=session_id)
  if not ids:
    return {"groups": []}

  if session_id and scope:
    id_set = {str(x) for x in ids}
    combined_events = fetch_google_events_between(scope[0], scope[1], session_id)
    targets = [
        e for e in combined_events
        if str(e.get("id")) in id_set
    ]

    groups_map: Dict[str, Dict[str, Any]] = {}
    for e in targets:
      event_id = str(e.get("id"))
      key = f"single::{event_id}"
      g = groups_map.get(key)
      if g is None:
        t = (e.get("start") or "")[11:16] if isinstance(
            e.get("start"), str) and len(e.get("start")) >= 16 else None
        g = {
            "group_key": key,
            "kind": "single",
            "title": e.get("title"),
            "time": t,
            "location": e.get("location"),
            "ids": [],
            "items": [],
        }
        groups_map[key] = g

      g["ids"].append(event_id)
      g["items"].append({
          "id": event_id,
          "title": e.get("title"),
          "start": e.get("start"),
          "end": e.get("end"),
          "location": e.get("location"),
          "recur": None,
          "all_day": e.get("all_day"),
      })
  else:
    id_set = set(ids)
    combined_events = list(events)
    combined_events.extend(_collect_local_recurring_occurrences(scope=scope))
    for rec in recurring_events:
      event_obj = _recurring_definition_to_event(rec)
      if _event_within_scope(event_obj, scope):
        combined_events.append(event_obj)
    targets = [
        e for e in combined_events if e.id in id_set and _event_within_scope(e, scope)
    ]

    def group_key(e: Event) -> str:
      t = (e.start or
           "")[11:16] if isinstance(e.start, str) and len(e.start) >= 16 else ""
      loc = e.location or ""
      if e.recur == "recurring":
        return f"recur::{e.title}::{t}::{loc}"
      return f"single::{e.id}"

    groups_map: Dict[str, Dict[str, Any]] = {}

    for e in targets:
      key = group_key(e)
      g = groups_map.get(key)
      if g is None:
        t = (e.start or "")[11:16] if isinstance(e.start, str) and len(
            e.start) >= 16 else None
        g = {
            "group_key": key,
            "kind": "recurring" if e.recur == "recurring" else "single",
            "title": e.title,
            "time": t,
            "location": e.location,
            "ids": [],
            "items": [],
        }
        groups_map[key] = g

      g["ids"].append(e.id)
      g["items"].append({
          "id": e.id,
          "title": e.title,
          "start": e.start,
          "end": e.end,
          "location": e.location,
          "recur": e.recur,
          "all_day": e.all_day,
      })

  groups = list(groups_map.values())
  groups.sort(
      key=lambda x: (0 if x["kind"] == "recurring" else 1, x["title"] or ""))

  for g in groups:
    g["items"].sort(key=lambda it: it.get("start") or "")
    g["count"] = len(g["ids"])
    g["samples"] = [it.get("start") for it in g["items"][:5]]

  return {"groups": groups}


# -------------------------
# Google OAuth 엔드포인트
# -------------------------
@app.get("/auth/google/login")
def google_login(request: Request):
  if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI):
    raise HTTPException(
        status_code=500,
        detail=
        "Google OAuth 환경변수(GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI)가 설정되지 않았습니다.",
    )

  session_id = _get_session_id(request) or _new_session_id()
  state_value = _new_oauth_state()
  existing_token = load_gcal_token_for_session(session_id) or {}
  has_refresh_token = bool(existing_token.get("refresh_token"))
  prompt = request.query_params.get("prompt")
  if not prompt:
    if request.query_params.get("force") == "1":
      prompt = "consent"
    elif not has_refresh_token:
      prompt = "consent"
  params = {
      "client_id": GOOGLE_CLIENT_ID,
      "redirect_uri": GOOGLE_REDIRECT_URI,
      "response_type": "code",
      "scope": "https://www.googleapis.com/auth/calendar.events",
      "access_type": "offline",
      "state": state_value,
  }
  if prompt:
    params["prompt"] = prompt
  url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
      params)
  resp = RedirectResponse(url)
  _set_cookie(resp,
              SESSION_COOKIE_NAME,
              session_id,
              max_age=SESSION_COOKIE_MAX_AGE_SECONDS)
  _set_cookie(resp,
              OAUTH_STATE_COOKIE_NAME,
              state_value,
              max_age=OAUTH_STATE_MAX_AGE_SECONDS)
  _store_oauth_state(state_value, session_id)
  return resp


@app.get("/auth/google/callback")
def google_callback(request: Request):
  code = request.query_params.get("code")
  error = request.query_params.get("error")
  state = request.query_params.get("state")
  expected_state = request.cookies.get(OAUTH_STATE_COOKIE_NAME)
  if error:
    return JSONResponse({"ok": False, "error": error})

  if not code:
    raise HTTPException(status_code=400, detail="code가 없습니다.")
  stored_session_id = _pop_oauth_state(state)
  if not state or (expected_state and state != expected_state and not stored_session_id):
    raise HTTPException(status_code=400, detail="state 검증에 실패했습니다.")

  if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI):
    raise HTTPException(
        status_code=500,
        detail=
        "Google OAuth 환경변수(GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI)가 설정되지 않았습니다.",
    )

  token_endpoint = "https://oauth2.googleapis.com/token"
  data = {
      "code": code,
      "client_id": GOOGLE_CLIENT_ID,
      "client_secret": GOOGLE_CLIENT_SECRET,
      "redirect_uri": GOOGLE_REDIRECT_URI,
      "grant_type": "authorization_code",
  }

  resp = requests.post(token_endpoint, data=data)
  if not resp.ok:
    raise HTTPException(status_code=500,
                        detail=f"토큰 교환 실패: {resp.status_code} {resp.text}")

  session_id = _get_session_id(request) or stored_session_id
  if not session_id:
    raise HTTPException(status_code=400, detail="세션이 없습니다.")

  token_json = resp.json()
  access_token = token_json.get("access_token")
  refresh_token = token_json.get("refresh_token")
  expires_in = token_json.get("expires_in")

  if not refresh_token:
    existing = load_gcal_token_for_session(session_id) or {}
    refresh_token = existing.get("refresh_token")

  if not access_token or not refresh_token:
    raise HTTPException(
        status_code=500,
        detail="access_token/refresh_token missing. Retry with /auth/google/login?force=1",
    )

  expiry_dt = datetime.now(
      timezone.utc) + timedelta(seconds=int(expires_in or 0))
  token_data = {
      "token": access_token,
      "refresh_token": refresh_token,
      "token_uri": "https://oauth2.googleapis.com/token",
      "client_id": GOOGLE_CLIENT_ID,
      "client_secret": GOOGLE_CLIENT_SECRET,
      "scopes": GCAL_SCOPES,
      "expiry": expiry_dt.isoformat().replace("+00:00", "Z"),
  }

  save_gcal_token_for_session(session_id, token_data)

  # ✅ 성공 시 달력으로 이동
  resp = RedirectResponse(_frontend_url("/calendar"))
  _set_cookie(resp,
              SESSION_COOKIE_NAME,
              session_id,
              max_age=SESSION_COOKIE_MAX_AGE_SECONDS)
  _delete_cookie(resp, OAUTH_STATE_COOKIE_NAME)
  return resp


@app.get("/auth/google/status")
def google_status(request: Request):
  token_data = load_gcal_token_for_request(request)
  return {
      "enabled": ENABLE_GCAL,
      "configured": is_gcal_configured(),
      "has_token": token_data is not None,
      "admin": is_admin(request),
  }


# -------------------------
# Admin / Logout
# -------------------------
@app.get("/admin")
def enter_admin():
  resp = RedirectResponse("/calendar")
  resp.set_cookie(ADMIN_COOKIE_NAME,
                  ADMIN_COOKIE_VALUE,
                  httponly=True,
                  samesite="lax")
  return resp


@app.get("/admin/exit")
def exit_admin():
  resp = RedirectResponse("/")
  resp.delete_cookie(ADMIN_COOKIE_NAME)
  return resp


@app.get("/logout")
def logout(request: Request):
  session_id = _get_session_id(request)
  clear_gcal_token_for_session(session_id)
  _clear_google_cache(session_id)
  resp = RedirectResponse("/")
  resp.delete_cookie(ADMIN_COOKIE_NAME, path="/")
  _delete_cookie(resp, SESSION_COOKIE_NAME)
  _delete_cookie(resp, OAUTH_STATE_COOKIE_NAME)
  return resp


# -------------------------
# API 엔드포인트
# -------------------------
@app.get("/api/events", response_model=List[Event])
def list_events(request: Request,
                start_date: Optional[str] = Query(None),
                end_date: Optional[str] = Query(None)):
  if is_google_mode_active(request):
    return []
  scope = _parse_scope_dates(start_date, end_date, require=False, max_days=3650)
  items = _list_local_events_for_api(scope=scope)
  items.sort(key=lambda ev: ev.start)
  return items


def _format_recent_local_event(ev: Event) -> Dict[str, Any]:
  return {
      "id": ev.id,
      "title": ev.title,
      "start": ev.start,
      "end": ev.end,
      "location": ev.location,
      "all_day": ev.all_day,
      "created_at": ev.created_at,
      "source": "local",
      "google_event_id": ev.google_event_id,
  }


def _format_recent_recurring_event(rec: Dict[str, Any]) -> Dict[str, Any]:
  start_time = rec.get("time") or "00:00"
  start_value = f"{rec['start_date']}T{start_time}"
  return {
      "id": rec["id"],
      "title": rec["title"],
      "start": start_value,
      "end": None,
      "location": rec.get("location"),
      "all_day": not bool(rec.get("time")),
      "created_at": rec.get("created_at"),
      "source": "local",
      "google_event_id": rec.get("google_event_id"),
      "recurrence": rec.get("recurrence"),
  }


def _format_recent_google_event(item: Dict[str, Any]) -> Dict[str, Any]:
  created_raw = item.get("created") or item.get("updated")
  return {
      "id": item.get("id"),
      "title": item.get("title"),
      "start": item.get("start"),
      "end": item.get("end"),
      "location": item.get("location"),
      "all_day": item.get("all_day"),
      "created_at": _normalize_google_timestamp(created_raw),
      "source": "google",
      "google_event_id": item.get("id"),
  }


@app.get("/api/recent-events")
def list_recent_events(request: Request):
  session_id = get_google_session_id(request)
  if session_id:
    try:
      data = fetch_recent_google_events(session_id)
      formatted = [_format_recent_google_event(item) for item in data]
      return formatted[:200]
    except HTTPException:
      raise
    except Exception as exc:
      raise HTTPException(status_code=502,
                          detail=f"Google recent events 실패: {exc}") from exc

  cutoff = datetime.now(SEOUL) - timedelta(days=UNDO_RETENTION_DAYS)
  recent = [
      _format_recent_local_event(e)
      for e in events
      if _parse_created_at(e.created_at) >= cutoff
  ]
  for rec in recurring_events:
    if _parse_created_at(rec.get("created_at")) >= cutoff:
      recent.append(_format_recent_recurring_event(rec))
  recent.sort(key=lambda ev: _parse_created_at(ev.get("created_at")), reverse=True)
  return recent[:200]


@app.get("/api/google/events")
def google_events(request: Request,
                  start_date: str = Query(..., alias="start_date"),
                  end_date: str = Query(..., alias="end_date")):
  scope = _parse_scope_dates(start_date,
                             end_date,
                             require=True,
                             max_days=3650,
                             label="조회")
  session_id = get_google_session_id(request)
  if not session_id:
    raise HTTPException(status_code=401, detail="Google 로그인이 필요합니다.")
  return fetch_google_events_between(scope[0], scope[1], session_id)


@app.delete("/api/google/events/{event_id}")
def google_delete_event(request: Request, event_id: str):
  if not event_id:
    raise HTTPException(status_code=400, detail="event_id가 없습니다.")
  session_id = get_google_session_id(request)
  if not session_id:
    raise HTTPException(status_code=401, detail="Google 로그인이 필요합니다.")
  try:
    gcal_delete_event(event_id, session_id=session_id)
    _clear_google_cache(session_id)
    return {"ok": True}
  except HTTPException:
    raise
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google event 삭제 실패: {exc}") from exc


@app.patch("/api/google/events/{event_id}")
def google_update_event_api(request: Request, event_id: str, payload: EventUpdate):
  if not event_id:
    raise HTTPException(status_code=400, detail="event_id가 없습니다.")
  if not is_gcal_configured():
    raise HTTPException(status_code=400,
                        detail="Google Calendar 연동이 설정되지 않았습니다.")
  session_id = get_google_session_id(request)
  if not session_id:
    raise HTTPException(status_code=401, detail="Google 로그인이 필요합니다.")

  start_iso = _coerce_patch_start(payload.start)
  if not start_iso:
    raise HTTPException(status_code=400, detail="시작 시각은 필수입니다.")

  end_iso = None
  if payload.end is not None:
    end_iso = _coerce_patch_end(payload.end)

  title_value: Optional[str] = None
  if payload.title is not None:
    title_value = payload.title.strip()
    if not title_value:
      raise HTTPException(status_code=400, detail="제목을 비울 수 없습니다.")

  location_value: Optional[str] = None
  if payload.location is not None:
    cleaned = _clean_optional_str(payload.location)
    location_value = "" if cleaned is None else cleaned

  description_value: Optional[str] = None
  if payload.description is not None:
    cleaned = _clean_optional_str(payload.description)
    description_value = "" if cleaned is None else cleaned

  attendees_value: Optional[List[str]] = None
  if payload.attendees is not None:
    cleaned_attendees: List[str] = []
    if isinstance(payload.attendees, list):
      for item in payload.attendees:
        if not isinstance(item, str):
          continue
        email = item.strip()
        if email:
          cleaned_attendees.append(email)
    attendees_value = cleaned_attendees

  reminders_value: Optional[List[int]] = None
  if payload.reminders is not None:
    cleaned_reminders: List[int] = []
    if isinstance(payload.reminders, list):
      for item in payload.reminders:
        try:
          minutes = int(item)
        except Exception:
          continue
        if minutes >= 0:
          cleaned_reminders.append(minutes)
    reminders_value = cleaned_reminders

  visibility_value: Optional[str] = payload.visibility if payload.visibility is not None else None
  transparency_value: Optional[str] = payload.transparency if payload.transparency is not None else None

  meeting_url_value: Optional[str] = None
  if payload.meeting_url is not None:
    cleaned = _clean_optional_str(payload.meeting_url)
    meeting_url_value = "" if cleaned is None else cleaned

  timezone_value: Optional[str] = payload.timezone if payload.timezone is not None else None
  color_value: Optional[str] = None
  if payload.color_id is not None:
    color_value = payload.color_id

  all_day_flag = payload.all_day
  if all_day_flag is None:
    all_day_flag = is_all_day_span(start_iso, end_iso)

  try:
    gcal_update_event(event_id,
                      title_value,
                      start_iso,
                      end_iso,
                      location_value,
                      bool(all_day_flag),
                      session_id=session_id,
                      description=description_value,
                      attendees=attendees_value,
                      reminders=reminders_value,
                      visibility=visibility_value,
                      transparency=transparency_value,
                      meeting_url=meeting_url_value,
                      timezone_value=timezone_value,
                      color_id=color_value)
    _clear_google_cache(session_id)
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google event 업데이트 실패: {exc}") from exc

  return {"ok": True}


@app.post("/api/events", response_model=Event)
def create_event(request: Request, event_in: EventCreate):
  google_event_id: Optional[str] = None
  detected_all_day = event_in.all_day
  session_id = get_google_session_id(request)
  if detected_all_day is None:
    detected_all_day = is_all_day_span(event_in.start, event_in.end)
  try:
    google_event_id = gcal_create_single_event(event_in.title, event_in.start,
                                               event_in.end, event_in.location,
                                               detected_all_day,
                                               session_id=session_id,
                                               description=event_in.description,
                                               attendees=event_in.attendees,
                                               reminders=event_in.reminders,
                                               visibility=event_in.visibility,
                                               transparency=event_in.transparency,
                                               meeting_url=event_in.meeting_url,
                                               timezone_value=event_in.timezone,
                                               color_id=event_in.color_id)
    if session_id and google_event_id:
      _clear_google_cache(session_id)
  except Exception:
    _log_debug("[GCAL] /api/events create: 실패 (무시)")

  return store_event(
      title=event_in.title,
      start=event_in.start,
      end=event_in.end,
      location=event_in.location,
      recur=event_in.recur,
      google_event_id=google_event_id,
      all_day=bool(detected_all_day),
      created_at=event_in.created_at or _now_iso_minute(),
      description=event_in.description,
      attendees=event_in.attendees,
      reminders=event_in.reminders,
      visibility=event_in.visibility,
      transparency=event_in.transparency,
      meeting_url=event_in.meeting_url,
      timezone_value=event_in.timezone,
      color_id=_normalize_color_id(event_in.color_id),
  )


@app.delete("/api/events/{event_id}")
def delete_event(event_id: int):
  deleted = delete_events_by_ids([event_id])
  if not deleted:
    raise HTTPException(status_code=404, detail="일정을 찾을 수 없습니다.")
  return {"ok": True, "deleted": deleted}


@app.patch("/api/events/{event_id}", response_model=Event)
def update_event(request: Request, event_id: int, payload: EventUpdate):
  recurrence_id = _decode_occurrence_id(event_id)
  if recurrence_id:
    raise HTTPException(status_code=400, detail="반복 일정은 개별 수정할 수 없습니다.")
  if _find_recurring_event(event_id):
    raise HTTPException(status_code=400, detail="반복 일정은 개별 수정할 수 없습니다.")

  target = next((e for e in events if e.id == event_id), None)
  if target is None:
    raise HTTPException(status_code=404, detail="일정을 찾을 수 없습니다.")

  new_title = target.title
  if payload.title is not None:
    new_title = payload.title.strip()
    if not new_title:
      raise HTTPException(status_code=400, detail="제목을 입력해 주세요.")

  new_start = target.start
  if payload.start is not None:
    new_start = _coerce_patch_start(payload.start)
    if not new_start:
      raise HTTPException(status_code=400, detail="시작 시각 형식이 잘못되었습니다.")

  new_end = target.end
  if payload.end is not None:
    new_end = _coerce_patch_end(payload.end)

  location_provided = payload.location is not None
  new_location = target.location
  if location_provided:
    new_location = _clean_optional_str(payload.location)

  description_provided = payload.description is not None
  new_description = target.description
  if description_provided:
    new_description = _clean_optional_str(payload.description)

  attendees_provided = payload.attendees is not None
  new_attendees = target.attendees
  if attendees_provided:
    cleaned_attendees: List[str] = []
    if isinstance(payload.attendees, list):
      for item in payload.attendees:
        if not isinstance(item, str):
          continue
        cleaned = item.strip()
        if cleaned:
          cleaned_attendees.append(cleaned)
    new_attendees = cleaned_attendees

  reminders_provided = payload.reminders is not None
  new_reminders = target.reminders
  if reminders_provided:
    cleaned_reminders: List[int] = []
    if isinstance(payload.reminders, list):
      for item in payload.reminders:
        try:
          val = int(item)
        except Exception:
          continue
        if val >= 0:
          cleaned_reminders.append(val)
    new_reminders = cleaned_reminders

  visibility_provided = payload.visibility is not None
  new_visibility = target.visibility
  if visibility_provided:
    new_visibility = payload.visibility

  transparency_provided = payload.transparency is not None
  new_transparency = target.transparency
  if transparency_provided:
    new_transparency = payload.transparency

  color_provided = payload.color_id is not None
  new_color_id = target.color_id
  if color_provided:
    new_color_id = _normalize_color_id(payload.color_id)

  meeting_url_provided = payload.meeting_url is not None
  new_meeting_url = target.meeting_url
  if meeting_url_provided:
    new_meeting_url = _clean_optional_str(payload.meeting_url)

  timezone_provided = payload.timezone is not None
  new_timezone = target.timezone
  if timezone_provided:
    new_timezone = payload.timezone or "Asia/Seoul"

  if not new_start:
    raise HTTPException(status_code=400, detail="시작 시각을 설정할 수 없습니다.")

  new_all_day = payload.all_day
  if new_all_day is None:
    new_all_day = is_all_day_span(new_start, new_end)

  session_id = get_google_session_id(request)
  if target.google_event_id and session_id:
    try:
      gcal_location = None
      if location_provided:
        gcal_location = "" if new_location is None else new_location
      gcal_description = None
      if description_provided:
        gcal_description = "" if new_description is None else new_description
      gcal_attendees = new_attendees if attendees_provided else None
      gcal_reminders = new_reminders if reminders_provided else None
      gcal_visibility = new_visibility if visibility_provided else None
      gcal_transparency = new_transparency if transparency_provided else None
      gcal_meeting_url = new_meeting_url if meeting_url_provided else None
      gcal_timezone = new_timezone if timezone_provided else None
      gcal_color_id = payload.color_id if color_provided else None
      gcal_update_event(target.google_event_id,
                        new_title,
                        new_start,
                        new_end,
                        gcal_location,
                        bool(new_all_day),
                        session_id=session_id,
                        description=gcal_description,
                        attendees=gcal_attendees,
                        reminders=gcal_reminders,
                        visibility=gcal_visibility,
                        transparency=gcal_transparency,
                        meeting_url=gcal_meeting_url,
                        timezone_value=gcal_timezone,
                        color_id=gcal_color_id)
    except Exception as exc:
      _log_debug(f"[GCAL] local event update failed: {exc}")

  target.title = new_title
  target.start = new_start
  target.end = new_end
  target.location = new_location
  target.description = new_description
  target.attendees = new_attendees
  target.reminders = new_reminders
  target.visibility = new_visibility
  target.transparency = new_transparency
  target.meeting_url = new_meeting_url
  target.timezone = new_timezone
  target.color_id = new_color_id
  target.all_day = bool(new_all_day)
  _save_events_to_disk()
  return target


@app.post("/api/nlp-events", response_model=List[Event])
async def create_events_from_natural_text(body: NaturalText,
                                          request: Request,
                                          response: Response):
  try:
    session_id = _ensure_session_id(request, response)
    request_id = _resolve_request_id(body.request_id)
    images = _validate_image_payload(body.images)
    effort = _resolve_request_reasoning_effort(request, body.reasoning_effort)
    model_name = _resolve_request_model(request, body.model)
    use_google_context = is_google_mode_active(request)
    cache_key = _context_cache_key_for_session_mode(session_id,
                                                    use_google_context)
    gcal_session_id = get_google_session_id(request)
    return await _run_with_interrupt(
        session_id,
        request_id,
        create_events_from_natural_text_core(body.text,
                                             images,
                                             effort,
                                             model_name=model_name,
                                             context_cache_key=cache_key,
                                             context_session_id=gcal_session_id if use_google_context else None,
                                             session_id=gcal_session_id),
    )
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502,
                        detail=f"Natural language error: {str(e)}")


@app.post("/api/nlp-event", response_model=Event)
async def create_event_from_natural_text_compat(body: NaturalText,
                                                request: Request,
                                                response: Response):
  try:
    session_id = _ensure_session_id(request, response)
    request_id = _resolve_request_id(body.request_id)
    images = _validate_image_payload(body.images)
    effort = _resolve_request_reasoning_effort(request, body.reasoning_effort)
    model_name = _resolve_request_model(request, body.model)
    use_google_context = is_google_mode_active(request)
    cache_key = _context_cache_key_for_session_mode(session_id,
                                                    use_google_context)
    gcal_session_id = get_google_session_id(request)
    created = await _run_with_interrupt(
        session_id,
        request_id,
        create_events_from_natural_text_core(body.text,
                                             images,
                                             effort,
                                             model_name=model_name,
                                             context_cache_key=cache_key,
                                             context_session_id=gcal_session_id if use_google_context else None,
                                             session_id=gcal_session_id),
    )
    return created[0]
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502,
                        detail=f"Natural language error: {str(e)}")


@app.post("/api/nlp-preview")
async def nlp_preview(body: NaturalText, request: Request, response: Response):
  try:
    session_id = _ensure_session_id(request, response)
    request_id = _resolve_request_id(body.request_id)
    images = _validate_image_payload(body.images)
    effort = _resolve_request_reasoning_effort(request, body.reasoning_effort)
    model_name = _resolve_request_model(request, body.model)
    use_google_context = is_google_mode_active(request)
    cache_key = _context_cache_key_for_session_mode(session_id,
                                                    use_google_context)
    gcal_session_id = get_google_session_id(request)
    data = await _run_with_interrupt(
        session_id,
        request_id,
        preview_events_from_natural_text_core(body.text,
                                              images,
                                              effort,
                                              model_name=model_name,
                                              context_cache_key=cache_key,
                                              context_session_id=gcal_session_id if use_google_context else None),
    )
    if isinstance(data, dict):
      data["request_id"] = request_id
    return data
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502, detail=f"Preview NLP error: {str(e)}")


@app.post("/api/nlp-apply-add", response_model=List[Event])
def nlp_apply_add(body: ApplyItems, request: Request):
  try:
    items = body.items or []
    if not items:
      raise HTTPException(status_code=400, detail="items is empty")
    session_id = get_google_session_id(request)
    return apply_add_items_core(items, session_id=session_id)
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502, detail=f"Apply Add error: {str(e)}")


@app.post("/api/nlp-delete-preview")
async def nlp_delete_preview(body: NaturalTextWithScope,
                             request: Request,
                             response: Response):
  try:
    session_id = _ensure_session_id(request, response)
    request_id = _resolve_request_id(body.request_id)
    scope = _parse_scope_dates(body.start_date, body.end_date, require=True)
    effort = _resolve_request_reasoning_effort(request, body.reasoning_effort)
    model_name = _resolve_request_model(request, body.model)
    use_google_context = is_google_mode_active(request)
    gcal_session_id = get_google_session_id(request)
    data = await _run_with_interrupt(
        session_id,
        request_id,
        delete_preview_groups(body.text,
                              scope=scope,
                              reasoning_effort=effort,
                              model_name=model_name,
                              session_id=gcal_session_id if use_google_context else None),
    )
    if isinstance(data, dict):
      data["request_id"] = request_id
    return data
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502,
                        detail=f"Delete Preview error: {str(e)}")


@app.post("/api/nlp-context/reset")
def nlp_context_reset(request: Request, response: Response):
  session_id = _ensure_session_id(request, response)
  base_key = _context_cache_key_for_session(session_id)
  if base_key:
    _clear_context_cache(f"{base_key}:local")
    _clear_context_cache(f"{base_key}:google")
  return {"ok": True}


@app.post("/api/nlp-interrupt")
async def nlp_interrupt(body: InterruptRequest,
                        request: Request,
                        response: Response):
  session_id = _ensure_session_id(request, response)
  cancelled = await _cancel_inflight(session_id, body.request_id)
  return {"ok": True, "cancelled": cancelled}


@app.post("/api/delete-by-ids", response_model=DeleteResult)
def delete_by_ids(body: IdsPayload):
  deleted = delete_events_by_ids(body.ids or [])
  return DeleteResult(ok=True, deleted_ids=deleted, count=len(deleted))


@app.post("/api/nlp-delete-events", response_model=DeleteResult)
async def delete_events_from_natural_text(body: NaturalTextWithScope,
                                          request: Request,
                                          response: Response):
  try:
    session_id = _ensure_session_id(request, response)
    request_id = _resolve_request_id(body.request_id)
    scope = _parse_scope_dates(body.start_date, body.end_date, require=True)
    effort = _resolve_request_reasoning_effort(request, body.reasoning_effort)
    model_name = _resolve_request_model(request, body.model)
    use_google_context = is_google_mode_active(request)
    gcal_session_id = get_google_session_id(request)
    ids = await _run_with_interrupt(
        session_id,
        request_id,
        create_delete_ids_from_natural_text(body.text,
                                            scope=scope,
                                            reasoning_effort=effort,
                                            model_name=model_name,
                                            session_id=gcal_session_id if use_google_context else None),
    )
    if use_google_context and gcal_session_id:
      deleted: List[Union[int, str]] = []
      for raw_id in ids:
        event_id = str(raw_id)
        if not event_id:
          continue
        try:
          gcal_delete_event(event_id, session_id=gcal_session_id)
          deleted.append(event_id)
        except HTTPException:
          raise
        except Exception as exc:
          raise HTTPException(status_code=502,
                              detail=f"Google event 삭제 실패: {exc}") from exc
      _clear_google_cache(gcal_session_id)
      return DeleteResult(ok=True, deleted_ids=deleted, count=len(deleted))

    deleted = delete_events_by_ids([int(x) for x in ids if isinstance(x, int)])
    return DeleteResult(ok=True, deleted_ids=deleted, count=len(deleted))
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502, detail=f"Delete NLP error: {str(e)}")


def build_header_actions(request: Request, has_token: bool) -> str:
  admin = is_admin(request)
  token = has_token

  parts: List[str] = []

  if admin:
    parts.append('<a class="header-btn" href="/admin/exit">Admin 해제</a>')
    return "\n".join(parts)

  if token:
    return "\n".join(parts)

  # 토큰 없음(이 경우는 보통 /calendar 접근이 막히지만, 혹시 ENABLE_GCAL=0 등)
  if ENABLE_GCAL:
    parts.append(
        '<a class="header-btn" href="/auth/google/login">Google 로그인</a>')
  parts.append('<a class="header-btn" href="/admin">Admin</a>')
  return "\n".join(parts)


@app.get("/", response_class=HTMLResponse)
def start_page(request: Request):
  # Redirect to calendar only when a login token is present.
  if load_gcal_token_for_request(request) is not None:
    return RedirectResponse("/calendar")

  # 그 외: 시작 페이지
  return START_HTML


@app.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request):
  # 접근 조건: admin or (gcal 비활성) or (token 있음)
  if not is_admin(request) and ENABLE_GCAL and load_gcal_token_for_request(
      request) is None:
    return RedirectResponse("/")

  token_present = load_gcal_token_for_request(request) is not None
  admin_mode = is_admin(request)
  actions_html = build_header_actions(request, token_present)
  context = {
      "admin": admin_mode,
      "google_linked": token_present,
      "mode": "admin"
      if admin_mode else ("google" if token_present and ENABLE_GCAL else "local"),
  }
  html = CALENDAR_HTML_TEMPLATE.replace("__HEADER_ACTIONS__", actions_html)
  context_json = json.dumps(context, ensure_ascii=False)
  api_base_json = json.dumps(API_BASE, ensure_ascii=False)
  context_script = (
      f"<script>window.__APP_CONTEXT__ = {context_json};"
      f"window.__API_BASE__ = {api_base_json};</script>")
  if "</head>" in html:
    html = html.replace("</head>", f"{context_script}\n</head>", 1)
  else:
    html = context_script + html
  def _has_script_src(text: str, src: str) -> bool:
    return f'src="{src}"' in text or f"src='{src}'" in text

  if "https://cdn.jsdelivr.net/npm/fullcalendar@6.1.15/index.global.min.css" not in html:
    css_tag = ('<link rel="stylesheet" '
               'href="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.15/index.global.min.css">')
    if "</head>" in html:
      html = html.replace("</head>", f"{css_tag}\n</head>", 1)
    else:
      html = css_tag + html

  if not _has_script_src(html, "https://cdn.jsdelivr.net/npm/fullcalendar@6.1.15/index.global.min.js"):
    fullcalendar_tag = (
        '<script src="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.15/'
        'index.global.min.js" defer></script>')
    if "</head>" in html:
      html = html.replace("</head>", f"{fullcalendar_tag}\n</head>", 1)
    else:
      html = fullcalendar_tag + html
  if not _has_script_src(html, "/calendar-app.js"):
    app_tag = '<script src="/calendar-app.js" defer></script>'
    if "</body>" in html:
      html = html.replace("</body>", f"{app_tag}\n</body>", 1)
    else:
      html = html + app_tag
  return HTMLResponse(html)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
  if not is_admin(request) and ENABLE_GCAL and load_gcal_token_for_request(
      request) is None:
    return RedirectResponse("/")
  return HTMLResponse(SETTINGS_HTML)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
  if load_gcal_token_for_request(request) is not None:
    return RedirectResponse("/calendar")
  return HTMLResponse(LOGIN_HTML)


if FRONTEND_STATIC_DIR and FRONTEND_STATIC_DIR.exists():
  app.mount("/",
            StaticFiles(directory=str(FRONTEND_STATIC_DIR), html=False),
            name="frontend-static")
