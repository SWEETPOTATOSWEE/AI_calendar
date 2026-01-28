from __future__ import annotations

import asyncio
import hashlib
import json
import os
import pathlib
import secrets
import time
from datetime import datetime, timedelta, timezone, date
from typing import Any, Dict, List, Optional, Tuple

import requests
from fastapi import HTTPException, Request, Response

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .config import (
    ENABLE_GCAL,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
    GOOGLE_CALENDAR_ID,
    GCAL_SCOPES,
    GOOGLE_TOKEN_DIR,
    GOOGLE_WEBHOOK_URL,
    GOOGLE_WEBHOOK_TOKEN,
    GCAL_WATCH_STATE_PATH,
    GCAL_WATCH_LEEWAY_SECONDS,
    SESSION_COOKIE_NAME,
    OAUTH_STATE_COOKIE_NAME,
    SESSION_COOKIE_MAX_AGE_SECONDS,
    OAUTH_STATE_MAX_AGE_SECONDS,
    COOKIE_SECURE,
    FRONTEND_BASE_URL,
    ADMIN_COOKIE_NAME,
    ADMIN_COOKIE_VALUE,
    SEOUL,
    GOOGLE_RECENT_DAYS,
)
from .utils import (
    _log_debug,
    _now_iso_minute,
    _merge_description,
    _build_gcal_attendees,
    _build_gcal_reminders,
    _normalize_visibility,
    _normalize_transparency,
    _normalize_color_id,
    _normalize_google_timestamp,
    _split_iso_date_time,
)
from .recurrence import recurring_to_rrule

# gcal 관련 캐시
google_events_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
context_cache: Dict[str, Dict[str, Any]] = {}
oauth_state_store: Dict[str, Dict[str, Any]] = {}
google_sse_subscribers: Dict[str, List[asyncio.Queue]] = {}

# -------------------------
# Google Calendar 유틸
# -------------------------
def is_admin(request: Request) -> bool:
  return request.cookies.get(ADMIN_COOKIE_NAME) == ADMIN_COOKIE_VALUE


def is_google_mode_active(request: Request, has_token: Optional[bool] = None) -> bool:
  if is_admin(request) or not ENABLE_GCAL:
    return False

  # URL 쿼리 파라미터 또는 쿠키에서 명시적인 모드 확인을 우선함
  mode_param = request.query_params.get("mode")
  mode_cookie = request.cookies.get("calendar_mode")

  # 명시적으로 google 모드가 아니면 False (로컬 모드 유지)
  if mode_param == "local" or mode_cookie == "local":
    return False

  # 명시적으로 google 모드거나, 토큰이 있는 경우 google 모드로 간주 (하위 호환)
  if mode_param == "google" or mode_cookie == "google":
    token_present = load_gcal_token_for_request(
        request) is not None if has_token is None else has_token
    return bool(token_present)

  # 기본적으로 토큰이 있으면 google 모드 (하위 호환)
  token_present = load_gcal_token_for_request(
      request) is not None if has_token is None else has_token
  return bool(token_present)


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


def _register_google_sse(session_id: str) -> Tuple[str, asyncio.Queue]:
  key = _session_key(session_id)
  queue: asyncio.Queue = asyncio.Queue()
  google_sse_subscribers.setdefault(key, []).append(queue)
  return key, queue


def _unregister_google_sse(key: str, queue: asyncio.Queue) -> None:
  listeners = google_sse_subscribers.get(key)
  if not listeners:
    return
  try:
    listeners.remove(queue)
  except ValueError:
    return
  if not listeners:
    google_sse_subscribers.pop(key, None)


def _emit_google_sse(session_id: str,
                     event_type: str,
                     payload: Optional[Dict[str, Any]] = None) -> None:
  if not session_id:
    return
  key = _session_key(session_id)
  listeners = google_sse_subscribers.get(key, [])
  if not listeners:
    return
  data = payload.copy() if isinstance(payload, dict) else {}
  data["type"] = event_type
  for queue in list(listeners):
    try:
      queue.put_nowait(data)
    except Exception:
      continue


def _format_sse_event(event_type: str, payload: Dict[str, Any]) -> str:
  body = json.dumps(payload, ensure_ascii=False)
  return f"event: {event_type}\ndata: {body}\n\n"


def _split_gcal_event_key(event_id: str) -> Tuple[str, Optional[str]]:
  if not isinstance(event_id, str):
    return (event_id, None)
  if "::" not in event_id:
    return (event_id, None)
  calendar_id, raw_id = event_id.split("::", 1)
  if raw_id:
    return (raw_id, calendar_id or None)
  return (event_id, None)


def _session_token_path(session_id: str) -> pathlib.Path:
  return GOOGLE_TOKEN_DIR / f"token_{_session_key(session_id)}.json"


def _ensure_token_dir() -> None:
  try:
    GOOGLE_TOKEN_DIR.mkdir(parents=True, exist_ok=True)
  except Exception:
    pass


def _gcal_watch_enabled() -> bool:
  return bool(GOOGLE_WEBHOOK_URL)


def _empty_watch_state() -> Dict[str, Any]:
  return {"sessions": {}, "channels": {}}


def _load_gcal_watch_state() -> Dict[str, Any]:
  _ensure_token_dir()
  if not GCAL_WATCH_STATE_PATH.exists():
    return _empty_watch_state()
  try:
    with GCAL_WATCH_STATE_PATH.open("r", encoding="utf-8") as f:
      data = json.load(f)
      if isinstance(data, dict):
        data.setdefault("sessions", {})
        data.setdefault("channels", {})
        return data
  except Exception:
    pass
  return _empty_watch_state()


def _save_gcal_watch_state(state: Dict[str, Any]) -> None:
  _ensure_token_dir()
  try:
    GCAL_WATCH_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
  except Exception:
    pass


def _get_watch_session_entry(state: Dict[str, Any],
                             session_id: str) -> Dict[str, Any]:
  sessions = state.setdefault("sessions", {})
  session_key = _session_key(session_id)
  entry = sessions.get(session_key)
  if not isinstance(entry, dict):
    entry = {"session_id": session_id, "calendars": {}}
    sessions[session_key] = entry
  return entry


def _watch_expiring(expiration_ms: Optional[int]) -> bool:
  if not expiration_ms:
    return True
  now_ms = int(time.time() * 1000)
  return expiration_ms <= now_ms + GCAL_WATCH_LEEWAY_SECONDS * 1000


def _stop_gcal_watch_channel(session_id: str,
                             channel_id: str,
                             resource_id: Optional[str]) -> None:
  if not channel_id or not resource_id:
    return
  try:
    service = get_gcal_service(session_id)
    service.channels().stop(
        body={"id": channel_id, "resourceId": resource_id}).execute()
  except Exception:
    pass


def _register_gcal_watch(session_id: str,
                         calendar_id: str,
                         summary: Optional[str],
                         primary: bool) -> Optional[Dict[str, Any]]:
  if not _gcal_watch_enabled():
    return None
  if not calendar_id:
    return None
  try:
    service = get_gcal_service(session_id)
    channel_id = secrets.token_urlsafe(16)
    body: Dict[str, Any] = {
        "id": channel_id,
        "type": "web_hook",
        "address": GOOGLE_WEBHOOK_URL,
    }
    if GOOGLE_WEBHOOK_TOKEN:
      body["token"] = GOOGLE_WEBHOOK_TOKEN
    response = service.events().watch(calendarId=calendar_id, body=body).execute()
    resource_id = response.get("resourceId")
    expiration_raw = response.get("expiration")
    expiration_ms: Optional[int] = None
    if isinstance(expiration_raw, (int, float)):
      expiration_ms = int(expiration_raw)
    elif isinstance(expiration_raw, str) and expiration_raw.isdigit():
      expiration_ms = int(expiration_raw)
    return {
        "calendar_id": calendar_id,
        "summary": summary,
        "primary": bool(primary),
        "channel_id": channel_id,
        "resource_id": resource_id,
        "expiration": expiration_ms,
    }
  except Exception as exc:
    _log_debug(f"[GCAL] watch registration failed: {exc}")
    return None


def _remove_watch_entry(state: Dict[str, Any],
                        session_id: str,
                        calendar_id: str,
                        watch: Dict[str, Any]) -> None:
  channel_id = watch.get("channel_id")
  if channel_id:
    state.get("channels", {}).pop(channel_id, None)
  session_entry = _get_watch_session_entry(state, session_id)
  calendars = session_entry.get("calendars", {})
  if isinstance(calendars, dict):
    calendars.pop(calendar_id, None)


def _clear_watches_for_session(session_id: str) -> None:
  if not session_id:
    return
  state = _load_gcal_watch_state()
  session_entry = _get_watch_session_entry(state, session_id)
  calendars = session_entry.get("calendars", {})
  if isinstance(calendars, dict):
    for calendar_id, entry in list(calendars.items()):
      if not isinstance(entry, dict):
        calendars.pop(calendar_id, None)
        continue
      _stop_gcal_watch_channel(session_id,
                               entry.get("channel_id", ""),
                               entry.get("resource_id"))
      _remove_watch_entry(state, session_id, calendar_id, entry)
  _save_gcal_watch_state(state)


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


def _store_oauth_state(state_value: str,
                       session_id: str,
                       redirect_uri: Optional[str] = None) -> None:
  if not state_value or not session_id:
    return
  entry: Dict[str, Any] = {
      "session_id": session_id,
      "created_at": time.time(),
  }
  if redirect_uri:
    entry["redirect_uri"] = redirect_uri
  oauth_state_store[state_value] = entry


def _pop_oauth_state(state_value: Optional[str]) -> Optional[Dict[str, Any]]:
  if not state_value:
    return None
  entry = oauth_state_store.pop(state_value, None)
  if not entry:
    return None
  created_at = entry.get("created_at")
  if created_at and (time.time() - float(created_at)) > OAUTH_STATE_MAX_AGE_SECONDS:
    return None
  return entry


def _set_cookie(response: Response,
                name: str,
                value: str,
                max_age: Optional[int] = None) -> None:
  samesite_value = "none" if COOKIE_SECURE else "lax"
  response.set_cookie(name,
                      value,
                      httponly=True,
                      samesite=samesite_value,
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


def _request_base_url(request: Request) -> str:
  forwarded_proto = request.headers.get("x-forwarded-proto")
  forwarded_host = request.headers.get("x-forwarded-host")
  proto = forwarded_proto or request.url.scheme
  host = forwarded_host or request.headers.get("host") or request.url.netloc
  return f"{proto}://{host}"


def _resolve_google_redirect_uri(request: Request) -> Optional[str]:
  if FRONTEND_BASE_URL:
    return f"{FRONTEND_BASE_URL}/auth/google/callback"
  if GOOGLE_REDIRECT_URI:
    return GOOGLE_REDIRECT_URI
  request_base = _request_base_url(request)
  return f"{request_base}/auth/google/callback"


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


def get_google_tasks_service(session_id: str):
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

  service = build("tasks", "v1", credentials=creds)
  return service


def get_google_userinfo(request: Request) -> Optional[Dict[str, Any]]:
  session_id = _get_session_id(request)
  if not session_id:
    return None
  token_data = load_gcal_token_for_session(session_id)
  if not token_data:
    return None
  creds = Credentials.from_authorized_user_info(token_data, GCAL_SCOPES)
  if creds.expired and creds.refresh_token:
    creds.refresh(GoogleRequest())
    new_data = json.loads(creds.to_json())
    save_gcal_token_for_session(session_id, new_data)
  access_token = creds.token
  if not access_token:
    return None
  try:
    response = requests.get(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=5,
    )
  except Exception:
    return None
  if not response.ok:
    return None
  try:
    payload = response.json()
  except Exception:
    return None
  return payload if isinstance(payload, dict) else None


def list_google_calendars(session_id: str) -> List[Dict[str, Any]]:
  service = get_gcal_service(session_id)
  calendars: List[Dict[str, Any]] = []
  page_token: Optional[str] = None
  while True:
    response = service.calendarList().list(pageToken=page_token).execute()
    items = response.get("items", [])
    for raw in items:
      if not isinstance(raw, dict):
        continue
      if raw.get("deleted"):
        continue
      calendar_id = raw.get("id")
      if not isinstance(calendar_id, str) or not calendar_id.strip():
        continue
      access_role = raw.get("accessRole")
      if access_role not in ("owner", "writer", "reader"):
        continue
      calendars.append({
          "id": calendar_id,
          "summary": raw.get("summary"),
          "primary": bool(raw.get("primary")),
          "access_role": access_role,
      })
    page_token = response.get("nextPageToken")
    if not page_token:
      break
  return calendars


def ensure_gcal_watches(session_id: str) -> None:
  if not session_id or not _gcal_watch_enabled():
    return
  try:
    calendars = list_google_calendars(session_id)
  except Exception as exc:
    _log_debug(f"[GCAL] watch calendar list failed: {exc}")
    return
  state = _load_gcal_watch_state()
  session_entry = _get_watch_session_entry(state, session_id)
  calendars_state = session_entry.setdefault("calendars", {})
  if not isinstance(calendars_state, dict):
    calendars_state = {}
    session_entry["calendars"] = calendars_state

  active_ids = {item["id"] for item in calendars}
  for calendar_id in list(calendars_state.keys()):
    if calendar_id not in active_ids:
      existing = calendars_state.get(calendar_id)
      if isinstance(existing, dict):
        _stop_gcal_watch_channel(session_id,
                                 existing.get("channel_id", ""),
                                 existing.get("resource_id"))
        _remove_watch_entry(state, session_id, calendar_id, existing)

  for item in calendars:
    calendar_id = item["id"]
    if item.get("access_role") == "reader":
      existing = calendars_state.get(calendar_id)
      if isinstance(existing, dict):
        _stop_gcal_watch_channel(session_id,
                                 existing.get("channel_id", ""),
                                 existing.get("resource_id"))
        _remove_watch_entry(state, session_id, calendar_id, existing)
      continue
    existing = calendars_state.get(calendar_id)
    if isinstance(existing, dict) and not _watch_expiring(existing.get("expiration")):
      continue
    if isinstance(existing, dict):
      _stop_gcal_watch_channel(session_id,
                               existing.get("channel_id", ""),
                               existing.get("resource_id"))
      _remove_watch_entry(state, session_id, calendar_id, existing)
    new_watch = _register_gcal_watch(session_id,
                                     calendar_id,
                                     item.get("summary"),
                                     bool(item.get("primary")))
    if new_watch:
      calendars_state[calendar_id] = new_watch
      state.setdefault("channels", {})[new_watch["channel_id"]] = {
          "session_id": session_id,
          "calendar_id": calendar_id,
          "resource_id": new_watch.get("resource_id"),
          "expiration": new_watch.get("expiration"),
      }
  _save_gcal_watch_state(state)


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
                             color_id: Optional[str] = None,
                             calendar_id: Optional[str] = None) -> Optional[str]:
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

    created = service.events().insert(calendarId=calendar_id
                                      or GOOGLE_CALENDAR_ID,
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
                      color_id: Optional[str] = None,
                      calendar_id: Optional[str] = None) -> None:
  event_id, parsed_calendar = _split_gcal_event_key(event_id)
  if calendar_id is None:
    calendar_id = parsed_calendar
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
  service.events().patch(calendarId=calendar_id or GOOGLE_CALENDAR_ID,
                         eventId=event_id,
                         body=body).execute()


def gcal_create_recurring_event(item: Dict[str, Any],
                                session_id: Optional[str] = None,
                                calendar_id: Optional[str] = None) -> Optional[str]:
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

    created = service.events().insert(calendarId=calendar_id
                                      or GOOGLE_CALENDAR_ID,
                                      body=event_body).execute()
    return created.get("id")
  except Exception as e:
    _log_debug(f"[GCAL] create recurring event error: {e}")
    return None


def gcal_delete_event(event_id: str,
                      session_id: Optional[str] = None,
                      calendar_id: Optional[str] = None) -> None:
  event_id, parsed_calendar = _split_gcal_event_key(event_id)
  if calendar_id is None:
    calendar_id = parsed_calendar
  if not event_id:
    raise ValueError("event_id is empty")
  if not is_gcal_configured():
    raise RuntimeError("Google Calendar is not configured.")
  if not session_id:
    raise RuntimeError("Google OAuth session is missing.")
  service = get_gcal_service(session_id)
  service.events().delete(calendarId=calendar_id or GOOGLE_CALENDAR_ID,
                          eventId=event_id).execute()


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


def _ensure_google_cache_entry(session_cache: Dict[str, Dict[str, Any]],
                               cache_key: str) -> Dict[str, Any]:
  entry = session_cache.get(cache_key)
  if not isinstance(entry, dict):
    entry = {}
    session_cache[cache_key] = entry
  if not isinstance(entry.get("events"), dict):
    entry["events"] = {}
  if not isinstance(entry.get("calendars"), dict):
    entry["calendars"] = {}
  return entry


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


def _normalize_gcal_event(raw: Dict[str, Any],
                          calendar_id: Optional[str]) -> Optional[Dict[str, Any]]:
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
      "calendar_id": calendar_id,
      "google_event_id": raw.get("id"),
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
                             calendar_id: str,
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
        "calendarId": calendar_id,
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
                               range_end: date,
                               calendar_id: Optional[str]) -> None:
  for raw in raw_items:
    if not isinstance(raw, dict):
      continue
    event_id = raw.get("id")
    if not event_id:
      continue
    cache_key = f"{calendar_id}::{event_id}" if calendar_id else event_id
    if raw.get("status") == "cancelled":
      cache.pop(cache_key, None)
      continue
    normalized = _normalize_gcal_event(raw, calendar_id)
    if not normalized:
      continue
    if _event_in_date_range(normalized, range_start, range_end):
      cache[cache_key] = normalized
    else:
      cache.pop(cache_key, None)


def _reset_gcal_cache_range(cache: Dict[str, Dict[str, Any]],
                            range_start: date,
                            range_end: date,
                            calendar_id: Optional[str]) -> None:
  if not calendar_id:
    return
  prefix = f"{calendar_id}::"
  for key, event in list(cache.items()):
    if not isinstance(key, str) or not key.startswith(prefix):
      continue
    if not isinstance(event, dict):
      continue
    if _event_in_date_range(event, range_start, range_end):
      cache.pop(key, None)


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

  if _gcal_watch_enabled():
    ensure_gcal_watches(session_id)

  try:
    calendars = list_google_calendars(session_id)
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google Calendar 목록 조회 실패: {exc}") from exc

  calendar_ids = [item["id"] for item in calendars if isinstance(item, dict)]
  if not calendar_ids:
    return []

  session_cache = _get_google_cache(session_id)
  cache_key = _google_cache_key(range_start, range_end)
  cache_entry = _ensure_google_cache_entry(session_cache, cache_key)
  cache_events = cache_entry.get("events", {})
  calendars_state = cache_entry.get("calendars", {})
  if not isinstance(cache_events, dict):
    cache_events = {}
    cache_entry["events"] = cache_events
  if not isinstance(calendars_state, dict):
    calendars_state = {}
    cache_entry["calendars"] = calendars_state

  active_ids = set(calendar_ids)
  for cache_id in list(cache_events.keys()):
    if not isinstance(cache_id, str):
      continue
    if "::" not in cache_id:
      continue
    cal_id = cache_id.split("::", 1)[0]
    if cal_id not in active_ids:
      cache_events.pop(cache_id, None)

  for calendar_id in calendar_ids:
    cal_state = calendars_state.get(calendar_id)
    sync_token = None
    sync_disabled = False
    if isinstance(cal_state, dict):
      sync_token = cal_state.get("sync_token")
      sync_disabled = bool(cal_state.get("sync_disabled"))
    if sync_token and not sync_disabled:
      try:
        raw_items, next_sync = _fetch_google_events_raw(service,
                                                        range_start,
                                                        range_end,
                                                        calendar_id,
                                                        sync_token=sync_token)
        _apply_gcal_items_to_cache(cache_events, raw_items, range_start,
                                   range_end, calendar_id)
        calendars_state[calendar_id] = {
            "sync_token": next_sync or sync_token,
            "sync_disabled": False,
        }
        cache_entry["updated_at"] = _now_iso_minute()
        continue
      except SyncTokenInvalid as exc:
        if getattr(exc, "kind", "") == "unsupported":
          calendars_state[calendar_id] = {
              "sync_token": None,
              "sync_disabled": True,
          }
        else:
          calendars_state[calendar_id] = {
              "sync_token": None,
              "sync_disabled": False,
          }

    raw_items, next_sync = _fetch_google_events_raw(service, range_start,
                                                    range_end, calendar_id)
    _reset_gcal_cache_range(cache_events, range_start, range_end, calendar_id)
    _apply_gcal_items_to_cache(cache_events, raw_items, range_start, range_end,
                               calendar_id)
    calendars_state[calendar_id] = {
        "sync_token": next_sync,
        "sync_disabled": False,
    }
    cache_entry["updated_at"] = _now_iso_minute()

  return _sorted_google_cache_items(cache_events)


def refresh_google_cache_for_calendar(session_id: str,
                                      calendar_id: str) -> None:
  if not session_id or not calendar_id:
    return
  session_cache = _get_google_cache(session_id)
  if not session_cache:
    return
  try:
    service = get_gcal_service(session_id)
  except Exception:
    return

  for cache_key in list(session_cache.keys()):
    if not isinstance(cache_key, str) or ":" not in cache_key:
      continue
    try:
      start_str, end_str = cache_key.split(":", 1)
      range_start = date.fromisoformat(start_str)
      range_end = date.fromisoformat(end_str)
    except Exception:
      continue

    cache_entry = _ensure_google_cache_entry(session_cache, cache_key)
    cache_events = cache_entry.get("events", {})
    calendars_state = cache_entry.get("calendars", {})
    if not isinstance(cache_events, dict):
      cache_events = {}
      cache_entry["events"] = cache_events
    if not isinstance(calendars_state, dict):
      calendars_state = {}
      cache_entry["calendars"] = calendars_state

    cal_state = calendars_state.get(calendar_id)
    sync_token = None
    sync_disabled = False
    if isinstance(cal_state, dict):
      sync_token = cal_state.get("sync_token")
      sync_disabled = bool(cal_state.get("sync_disabled"))

    if sync_token and not sync_disabled:
      try:
        raw_items, next_sync = _fetch_google_events_raw(service,
                                                        range_start,
                                                        range_end,
                                                        calendar_id,
                                                        sync_token=sync_token)
        _apply_gcal_items_to_cache(cache_events, raw_items, range_start,
                                   range_end, calendar_id)
        calendars_state[calendar_id] = {
            "sync_token": next_sync or sync_token,
            "sync_disabled": False,
        }
        cache_entry["updated_at"] = _now_iso_minute()
        continue
      except SyncTokenInvalid as exc:
        calendars_state[calendar_id] = {
            "sync_token": None,
            "sync_disabled": getattr(exc, "kind", "") == "unsupported",
        }

    try:
      raw_items, next_sync = _fetch_google_events_raw(service, range_start,
                                                      range_end, calendar_id)
    except Exception:
      continue
    _reset_gcal_cache_range(cache_events, range_start, range_end, calendar_id)
    _apply_gcal_items_to_cache(cache_events, raw_items, range_start, range_end,
                               calendar_id)
    calendars_state[calendar_id] = {
        "sync_token": next_sync,
        "sync_disabled": False,
    }
    cache_entry["updated_at"] = _now_iso_minute()


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

  try:
    calendars = list_google_calendars(session_id)
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google Calendar 목록 조회 실패: {exc}") from exc

  now = datetime.now(SEOUL)
  time_min = now - timedelta(days=days)

  events_data: List[Dict[str, Any]] = []
  updated_min = time_min.astimezone(
      timezone.utc).isoformat().replace("+00:00", "Z")
  for cal in calendars:
    calendar_id = cal.get("id")
    if not isinstance(calendar_id, str) or not calendar_id:
      continue
    page_token: Optional[str] = None
    while True:
      request = service.events().list(calendarId=calendar_id,
                                      updatedMin=updated_min,
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
            "calendar_id": calendar_id,
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
