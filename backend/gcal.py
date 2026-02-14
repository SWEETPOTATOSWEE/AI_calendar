from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import pathlib
import re
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
    ISO_DATETIME_RE,
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
    GCAL_RANGE_CACHE_TTL_SECONDS,
    GCAL_TASKS_CACHE_TTL_SECONDS,
    SESSION_COOKIE_NAME,
    OAUTH_STATE_COOKIE_NAME,
    SESSION_COOKIE_MAX_AGE_SECONDS,
    OAUTH_STATE_MAX_AGE_SECONDS,
    COOKIE_SECURE,
    FRONTEND_BASE_URL,
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
    _compute_all_day_bounds,
)
from .recurrence import recurring_to_rrule

# gcal 愿??罹먯떆
google_events_cache: Dict[str, Dict[str, Any]] = {}
context_cache: Dict[str, Dict[str, Any]] = {}
oauth_state_store: Dict[str, Dict[str, Any]] = {}
google_sse_subscribers: Dict[str, List[asyncio.Queue]] = {}

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


def _empty_google_cache() -> Dict[str, Any]:
  return {
      "events": {},
      "calendars": {},
      "coverage_start": None,
      "coverage_end": None,
      "updated_at": None,
      "updated_at_ts": 0.0,
      "dirty": False,
      "tasks": [],
      "tasks_updated_at": None,
      "tasks_updated_at_ts": 0.0,
      "revision": 0,
      "events_revision": 0,
      "tasks_revision": 0,
      "op_seq": 0,
  }


def _ensure_google_revision_fields(cache: Dict[str, Any]) -> None:
  if not isinstance(cache.get("revision"), int):
    cache["revision"] = 0
  if not isinstance(cache.get("events_revision"), int):
    cache["events_revision"] = int(cache.get("revision") or 0)
  if not isinstance(cache.get("tasks_revision"), int):
    cache["tasks_revision"] = int(cache.get("revision") or 0)
  if not isinstance(cache.get("op_seq"), int):
    cache["op_seq"] = 0


def _get_google_cache(session_id: str) -> Dict[str, Any]:
  key = _session_key(session_id)
  cache = google_events_cache.get(key)
  if not isinstance(cache, dict):
    cache = _empty_google_cache()
    google_events_cache[key] = cache
  if not isinstance(cache.get("events"), dict):
    cache["events"] = {}
  if not isinstance(cache.get("calendars"), dict):
    cache["calendars"] = {}
  if not isinstance(cache.get("tasks"), list):
    cache["tasks"] = []
  _ensure_google_revision_fields(cache)
  return cache


def get_google_revision_state(session_id: str) -> Dict[str, int]:
  cache = _get_google_cache(session_id)
  _ensure_google_revision_fields(cache)
  return {
      "revision": int(cache.get("revision") or 0),
      "events_revision": int(cache.get("events_revision") or 0),
      "tasks_revision": int(cache.get("tasks_revision") or 0),
  }


def get_google_revision(session_id: str) -> int:
  return int(get_google_revision_state(session_id).get("revision") or 0)


def bump_google_revision(session_id: str,
                         resource: str = "events",
                         count: int = 1) -> int:
  if not session_id:
    return 0
  cache = _get_google_cache(session_id)
  _ensure_google_revision_fields(cache)
  increments = max(1, int(count or 1))
  for _ in range(increments):
    cache["revision"] = int(cache.get("revision") or 0) + 1
  current_revision = int(cache.get("revision") or 0)
  if resource == "events":
    cache["events_revision"] = current_revision
  elif resource == "tasks":
    cache["tasks_revision"] = current_revision
  else:
    cache["events_revision"] = max(int(cache.get("events_revision") or 0),
                                   current_revision)
    cache["tasks_revision"] = max(int(cache.get("tasks_revision") or 0),
                                  current_revision)
  return current_revision


def _next_google_op_id(session_id: str, resource: str) -> str:
  cache = _get_google_cache(session_id)
  _ensure_google_revision_fields(cache)
  cache["op_seq"] = int(cache.get("op_seq") or 0) + 1
  clean_resource = str(resource or "google").strip().lower() or "google"
  return f"{clean_resource}:{cache['op_seq']}"


def _clear_google_cache(session_id: Optional[str]) -> None:
  if not session_id:
    return
  key = _session_key(session_id)
  google_events_cache.pop(key, None)
  _clear_context_cache(_context_cache_key_for_session_mode(session_id, True))


def _mark_google_cache_dirty(session_id: Optional[str]) -> None:
  if not session_id:
    return
  session_cache = _get_google_cache(session_id)
  events = session_cache.get("events")
  if isinstance(events, dict):
    events.clear()
  calendars_state = session_cache.get("calendars")
  if isinstance(calendars_state, dict):
    calendars_state.clear()
  session_cache["coverage_start"] = None
  session_cache["coverage_end"] = None
  session_cache["dirty"] = True


def _cache_event_key(calendar_id: Optional[str], event_id: Any) -> Optional[str]:
  if not isinstance(event_id, str) or not event_id:
    return None
  if isinstance(calendar_id, str) and calendar_id:
    return f"{calendar_id}::{event_id}"
  return event_id


def _is_google_cache_entry_fresh(cache_entry: Dict[str, Any]) -> bool:
  updated_at_ts = cache_entry.get("updated_at_ts")
  try:
    updated_ts = float(updated_at_ts)
  except Exception:
    return False
  if updated_ts <= 0:
    return False
  if bool(cache_entry.get("dirty")):
    return False
  age = time.time() - updated_ts
  return age <= GCAL_RANGE_CACHE_TTL_SECONDS


def _context_cache_key_for_session(session_id: Optional[str]) -> Optional[str]:
  if not session_id:
    return None
  return _session_key(session_id)


def _context_cache_key_for_session_mode(session_id: Optional[str],
                                        use_google: bool) -> Optional[str]:
  base = _context_cache_key_for_session(session_id)
  if not base:
    return None
  return f"{base}:google"


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
  return "assistant:" in lowered or "user:" in lowered or "?ъ슜??" in text


def get_google_session_id(request: Request) -> Optional[str]:
  if not ENABLE_GCAL:
    return None
  session_id = _get_session_id(request)
  if not session_id:
    return None
  if load_gcal_token_for_session(session_id) is None:
    return None
  return session_id


def require_google_session_id(request: Request) -> str:
  session_id = get_google_session_id(request)
  if not session_id:
    raise HTTPException(status_code=401, detail="Google 濡쒓렇?몄씠 ?꾩슂?⑸땲??")
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


def _get_google_tasks_cache_entry(session_id: str) -> Dict[str, Any]:
  entry = _get_google_cache(session_id)
  if not isinstance(entry.get("tasks"), list):
    entry["tasks"] = []
  return entry


def _set_google_tasks_cache(session_id: str, items: List[Dict[str, Any]]) -> None:
  entry = _get_google_tasks_cache_entry(session_id)
  entry["tasks"] = copy.deepcopy(items)
  entry["tasks_updated_at"] = _now_iso_minute()
  entry["tasks_updated_at_ts"] = time.time()


def fetch_google_tasks(session_id: str, force_refresh: bool = False) -> List[Dict[str, Any]]:
  if not session_id:
    raise HTTPException(status_code=401, detail="Google login is required.")

  cache_entry = _get_google_tasks_cache_entry(session_id)
  if not force_refresh:
    updated_at_ts = cache_entry.get("tasks_updated_at_ts")
    try:
      updated_ts = float(updated_at_ts)
    except Exception:
      updated_ts = 0.0
    if updated_ts > 0 and (time.time() - updated_ts) <= GCAL_TASKS_CACHE_TTL_SECONDS:
      cached_items = cache_entry.get("tasks")
      if isinstance(cached_items, list):
        return copy.deepcopy(cached_items)

  service = get_google_tasks_service(session_id)
  result = service.tasks().list(tasklist='@default',
                                showCompleted=True,
                                showHidden=True).execute()
  items = result.get("items", []) if isinstance(result, dict) else []
  normalized_items = [item for item in items if isinstance(item, dict)]
  _set_google_tasks_cache(session_id, normalized_items)
  return copy.deepcopy(normalized_items)


def upsert_google_task_cache(session_id: str, task: Dict[str, Any]) -> None:
  if not session_id or not isinstance(task, dict):
    return
  task_id = task.get("id")
  if not isinstance(task_id, str) or not task_id:
    return
  entry = _get_google_tasks_cache_entry(session_id)
  items = entry.get("tasks")
  if not isinstance(items, list):
    items = []
  replaced = False
  next_items: List[Dict[str, Any]] = []
  for item in items:
    if not isinstance(item, dict):
      continue
    if item.get("id") == task_id:
      next_items.append(copy.deepcopy(task))
      replaced = True
    else:
      next_items.append(item)
  if not replaced:
    next_items.append(copy.deepcopy(task))
  _set_google_tasks_cache(session_id, next_items)


def remove_google_task_cache(session_id: str, task_id: str) -> None:
  if not session_id or not task_id:
    return
  entry = _get_google_tasks_cache_entry(session_id)
  items = entry.get("tasks")
  if not isinstance(items, list):
    return
  next_items = [item for item in items if isinstance(item, dict) and item.get("id") != task_id]
  _set_google_tasks_cache(session_id, next_items)


def emit_google_task_delta(session_id: str,
                           action: str,
                           *,
                           task: Optional[Dict[str, Any]] = None,
                           task_id: Optional[str] = None,
                           revision: Optional[int] = None,
                           op_id: Optional[str] = None,
                           bump_if_missing: bool = True) -> Dict[str, Any]:
  if revision is None and bump_if_missing:
    revision = bump_google_revision(session_id, "tasks")
  if revision is None:
    revision = get_google_revision(session_id)
  if not isinstance(op_id, str) or not op_id.strip():
    op_id = _next_google_op_id(session_id, "tasks")
  payload: Dict[str, Any] = {"action": action}
  if isinstance(task, dict):
    payload["task"] = task
  if isinstance(task_id, str) and task_id:
    payload["task_id"] = task_id
  payload["revision"] = int(revision)
  payload["op_id"] = op_id
  _emit_google_sse(session_id, "google_task_delta", payload)
  return {
      "new_revision": int(revision),
      "op_id": op_id,
  }


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
  if not is_gcal_configured():
    raise HTTPException(status_code=400,
                        detail="Google Calendar integration is not configured.")
  if not session_id:
    raise HTTPException(status_code=401, detail="Google login is required.")

  try:
    service = get_gcal_service(session_id)
  except Exception as e:
    _log_debug(f"[GCAL] get service error: {e}")
    raise HTTPException(status_code=502,
                        detail=f"Google Calendar service error: {e}") from e

  try:
    # Do not auto-infer all-day from start/end shape.
    # Treat as all-day only when caller explicitly sets all_day=True.
    use_all_day = bool(all_day)

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
    event_id = created.get("id") if isinstance(created, dict) else None
    if not event_id:
      raise HTTPException(status_code=502,
                          detail="Google event create failed: missing event id.")
    return str(event_id)
  except HTTPException:
    raise
  except Exception as e:
    _log_debug(f"[GCAL] create single event error: {e}")
    raise HTTPException(status_code=502,
                        detail=f"Google event create failed: {e}") from e


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

  if start_iso is not None:
    if not isinstance(start_iso, str) or not ISO_DATETIME_RE.match(start_iso):
      raise ValueError("Invalid start time for Google Calendar update.")

    # Do not auto-infer all-day from start/end shape.
    # Treat as all-day only when caller explicitly sets all_day=True.
    use_all_day = bool(all_day)

    if use_all_day:
      start_date, end_exclusive = _compute_all_day_bounds(start_iso, end_iso)
      # Internal representation uses exclusive end (next day 00:00) for all-day spans.
      body["start"] = {
          "date": start_date.strftime("%Y-%m-%d"),
          "dateTime": None,
          "timeZone": None,
      }
      body["end"] = {
          "date": end_exclusive.strftime("%Y-%m-%d"),
          "dateTime": None,
          "timeZone": None,
      }
    else:
      start_dt = datetime.strptime(start_iso,
                                   "%Y-%m-%dT%H:%M").replace(tzinfo=SEOUL)
      if end_iso:
        end_dt = datetime.strptime(end_iso,
                                   "%Y-%m-%dT%H:%M").replace(tzinfo=SEOUL)
      else:
        end_dt = start_dt + timedelta(hours=1)
      tz_value = timezone_value or "Asia/Seoul"
      # Clear all-day keys explicitly when switching to timed.
      body["start"] = {
          "date": None,
          "dateTime": start_dt.isoformat(),
          "timeZone": tz_value,
      }
      body["end"] = {
          "date": None,
          "dateTime": end_dt.isoformat(),
          "timeZone": tz_value,
      }
  else:
    if end_iso is not None:
      if not isinstance(end_iso, str) or not ISO_DATETIME_RE.match(end_iso):
        raise ValueError("Invalid end time for Google Calendar update.")
      use_all_day = bool(all_day)
      if use_all_day:
        # Internal representation uses exclusive end (next day 00:00) for all-day spans.
        end_dt, end_time = _split_iso_date_time(end_iso)
        if end_time == "00:00":
          body["end"] = {"date": end_dt.strftime("%Y-%m-%d")}
        else:
          # Fallback for old inclusive format (T23:59 or other)
          body["end"] = {"date": (end_dt + timedelta(days=1)).strftime("%Y-%m-%d")}
      else:
        tz_value = timezone_value or "Asia/Seoul"
        end_dt = datetime.strptime(end_iso,
                                   "%Y-%m-%dT%H:%M").replace(tzinfo=SEOUL)
        body["end"] = {"dateTime": end_dt.isoformat(), "timeZone": tz_value}
    if all_day is not None:
      raise ValueError("all_day requires start for Google Calendar update.")
  return body


def _google_datetime_to_iso_minute(value: Any) -> Optional[str]:
  if not isinstance(value, str):
    return None
  text = value.strip()
  if len(text) >= 16 and text[4] == "-" and text[7] == "-" and text[10] == "T" and text[13] == ":":
    return text[:16]
  if text.endswith("Z"):
    text = text[:-1] + "+00:00"
  try:
    dt = datetime.fromisoformat(text)
  except Exception:
    return None
  return dt.strftime("%Y-%m-%dT%H:%M")


def _extract_existing_event_bounds(raw_event: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[bool], Optional[str]]:
  start_raw = raw_event.get("start") if isinstance(raw_event.get("start"), dict) else {}
  end_raw = raw_event.get("end") if isinstance(raw_event.get("end"), dict) else {}
  timezone_value = start_raw.get("timeZone") if isinstance(start_raw, dict) else None

  start_dt = _google_datetime_to_iso_minute(start_raw.get("dateTime")) if isinstance(start_raw, dict) else None
  end_dt = _google_datetime_to_iso_minute(end_raw.get("dateTime")) if isinstance(end_raw, dict) else None
  if start_dt:
    return start_dt, end_dt, False, timezone_value if isinstance(timezone_value, str) else None

  start_date = start_raw.get("date") if isinstance(start_raw, dict) else None
  end_date_exclusive = end_raw.get("date") if isinstance(end_raw, dict) else None
  if isinstance(start_date, str) and len(start_date) >= 10:
    start_iso = f"{start_date[:10]}T00:00"
    if isinstance(end_date_exclusive, str) and len(end_date_exclusive) >= 10:
      end_iso = f"{end_date_exclusive[:10]}T00:00"
    else:
      # Fallback to next day 00:00 if end date is missing
      try:
        start_dt_obj = datetime.strptime(start_date[:10], "%Y-%m-%d").date()
        end_dt_obj = start_dt_obj + timedelta(days=1)
        end_iso = f"{end_dt_obj.strftime('%Y-%m-%d')}T00:00"
      except Exception:
        end_iso = f"{start_date[:10]}T00:00"
    return start_iso, end_iso, True, None
  return None, None, None, None


def _prepare_update_event(
    event_id: str,
    title: Optional[str],
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
    color_id: Optional[str] = None,
    start_date: Optional[str] = None,
    time_value: Optional[str] = None,
    duration_minutes: Optional[int] = None,
    recurrence: Optional[Dict[str, Any]] = None,
    rrule: Optional[str] = None,
    target_type: Optional[str] = None,
    calendar_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Tuple[str, str, Dict[str, Any]]:
  """Normalize update parameters and build the patch body.
  Returns (raw_event_id, resolved_calendar_id, body).
  Raises ValueError on invalid inputs.
  """
  raw_event_id, parsed_calendar = _split_gcal_event_key(event_id)
  if calendar_id is None:
    calendar_id = parsed_calendar
  if not raw_event_id:
    raise ValueError("event_id is empty")
  resolved_calendar = calendar_id or GOOGLE_CALENDAR_ID

  normalized_target_type = (target_type or "").strip().lower() if isinstance(target_type, str) else ""
  if normalized_target_type not in ("", "single", "recurring"):
    raise ValueError("target_type must be single or recurring.")

  normalized_start_date: Optional[str] = None
  if start_date is not None:
    if not isinstance(start_date, str):
      raise ValueError("start_date must be YYYY-MM-DD.")
    raw_start_date = start_date.strip()
    try:
      datetime.strptime(raw_start_date, "%Y-%m-%d")
      normalized_start_date = raw_start_date
    except Exception as exc:
      raise ValueError("start_date must be YYYY-MM-DD.") from exc

  normalized_time: Optional[str] = None
  if time_value is not None:
    if not isinstance(time_value, str):
      raise ValueError("time_value must be HH:MM.")
    raw_time = time_value.strip()
    try:
      parsed_time = datetime.strptime(raw_time, "%H:%M")
      normalized_time = parsed_time.strftime("%H:%M")
    except Exception as exc:
      raise ValueError("time_value must be HH:MM.") from exc

  normalized_duration: Optional[int] = None
  if duration_minutes is not None:
    try:
      normalized_duration = int(duration_minutes)
    except Exception as exc:
      raise ValueError("duration_minutes must be a positive integer.") from exc
    if normalized_duration <= 0:
      raise ValueError("duration_minutes must be a positive integer.")

  patched_start_iso = start_iso
  patched_end_iso = end_iso
  patched_all_day = all_day
  if normalized_time and patched_start_iso is None:
    anchor_date = normalized_start_date
    if not anchor_date:
      if isinstance(start_iso, str) and len(start_iso) >= 10:
        anchor_date = start_iso[:10]
      else:
        raise ValueError("time_value requires start_date or start.")
    patched_start_iso = f"{anchor_date}T{normalized_time}"
    if patched_end_iso is None:
      try:
        start_dt = datetime.strptime(patched_start_iso, "%Y-%m-%dT%H:%M")
      except Exception as exc:
        raise ValueError("Failed to compose start from start_date/time_value.") from exc
      if normalized_duration:
        end_dt = start_dt + timedelta(minutes=normalized_duration)
      else:
        end_dt = start_dt + timedelta(hours=1)
      patched_end_iso = end_dt.strftime("%Y-%m-%dT%H:%M")
    if patched_all_day is None:
      patched_all_day = False
  elif normalized_start_date and patched_start_iso is None:
    if patched_all_day is True or normalized_target_type == "single":
      patched_start_iso = f"{normalized_start_date}T00:00"
      if patched_end_iso is None:
        try:
          s_date = datetime.strptime(normalized_start_date, "%Y-%m-%d").date()
          e_date = s_date + timedelta(days=1)
          patched_end_iso = f"{e_date.strftime('%Y-%m-%d')}T00:00"
        except Exception:
          patched_end_iso = f"{normalized_start_date}T00:00"
      if patched_all_day is None:
        patched_all_day = True

  # Allow all_day-only patch by deriving start/end from the current event.
  if patched_start_iso is None and patched_all_day is not None:
    if not session_id:
      raise ValueError("all_day-only update requires session context.")
    service = get_gcal_service(session_id)
    raw_event = service.events().get(calendarId=resolved_calendar, eventId=raw_event_id).execute()
    existing_start, existing_end, existing_is_all_day, existing_tz = _extract_existing_event_bounds(
        raw_event if isinstance(raw_event, dict) else {})
    if existing_tz and timezone_value is None:
      timezone_value = existing_tz
    if not existing_start:
      raise ValueError("Could not derive existing event bounds for all_day update.")
    if patched_all_day:
      start_date = existing_start[:10]
      if isinstance(patched_end_iso, str) and len(patched_end_iso) >= 10:
        end_date = patched_end_iso[:10]
        # Ensure T00:00 format
        patched_end_iso = f"{end_date}T00:00"
      else:
        # derive from existing_end which is already in our internal format
        patched_end_iso = existing_end
      patched_start_iso = f"{start_date}T00:00"
    else:
      if existing_is_all_day:
        start_date = existing_start[:10]
        patched_start_iso = f"{start_date}T09:00"
        patched_end_iso = f"{start_date}T10:00"
      else:
        patched_start_iso = existing_start
        patched_end_iso = existing_end

  body = _build_gcal_event_body(title,
                                patched_start_iso,
                                patched_end_iso,
                                location,
                                patched_all_day,
                                description=description,
                                attendees=attendees,
                                reminders=reminders,
                                visibility=visibility,
                                transparency=transparency,
                                meeting_url=meeting_url,
                                timezone_value=timezone_value,
                                color_id=color_id)

  rrule_core: Optional[str] = None
  if recurrence is not None or rrule is not None:
    recurrence_item: Dict[str, Any] = {
        "start_date": normalized_start_date,
        "time": normalized_time,
        "duration_minutes": normalized_duration,
        "recurrence": recurrence,
        "rrule": rrule,
    }
    if recurrence_item.get("start_date") is None and isinstance(patched_start_iso, str) and len(
            patched_start_iso) >= 10:
      recurrence_item["start_date"] = patched_start_iso[:10]
    rrule_core = recurring_to_rrule(recurrence_item)
    if rrule is not None and not rrule_core:
      raise ValueError("rrule is invalid for Google Calendar update.")
    if recurrence is not None and not rrule_core:
      raise ValueError("recurrence payload is invalid for Google Calendar update.")

  if rrule_core:
    body["recurrence"] = [f"RRULE:{rrule_core}"]

  if normalized_target_type == "single":
    body["recurrence"] = None

  return raw_event_id, resolved_calendar, body


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
                      start_date: Optional[str] = None,
                      time_value: Optional[str] = None,
                      duration_minutes: Optional[int] = None,
                      recurrence: Optional[Dict[str, Any]] = None,
                      rrule: Optional[str] = None,
                      target_type: Optional[str] = None,
                      calendar_id: Optional[str] = None) -> None:
  if not is_gcal_configured():
    raise RuntimeError("Google Calendar is not configured.")
  if not session_id:
    raise RuntimeError("Google OAuth session is missing.")

  raw_event_id, resolved_cal, body = _prepare_update_event(
      event_id=event_id, title=title, start_iso=start_iso, end_iso=end_iso,
      location=location, all_day=all_day, description=description,
      attendees=attendees, reminders=reminders, visibility=visibility,
      transparency=transparency, meeting_url=meeting_url,
      timezone_value=timezone_value, color_id=color_id,
      start_date=start_date, time_value=time_value,
      duration_minutes=duration_minutes, recurrence=recurrence,
      rrule=rrule, target_type=target_type, calendar_id=calendar_id,
      session_id=session_id,
  )

  service = get_gcal_service(session_id)
  service.events().patch(calendarId=resolved_cal,
                         eventId=raw_event_id,
                         body=body).execute()


_BYDAY_MAP = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _align_start_to_byday(start_date: date, rrule_core: str) -> date:
  """Advance start_date to the first day matching BYDAY in the RRULE.

  Google Calendar uses DTSTART as a reference; if DTSTART falls on a weekday
  not listed in BYDAY, the generated instances may be wrong or missing.
  """
  byday_match = re.search(r"BYDAY=([^;]+)", rrule_core)
  if not byday_match:
    return start_date
  allowed: set[int] = set()
  for part in byday_match.group(1).split(","):
    abbr = part.strip()[-2:]
    if abbr in _BYDAY_MAP:
      allowed.add(_BYDAY_MAP[abbr])
  if not allowed or start_date.weekday() in allowed:
    return start_date
  for offset in range(1, 8):
    candidate = start_date + timedelta(days=offset)
    if candidate.weekday() in allowed:
      return candidate
  return start_date


def gcal_create_recurring_event(item: Dict[str, Any],
                                session_id: Optional[str] = None,
                                calendar_id: Optional[str] = None) -> Optional[str]:
  if not is_gcal_configured() or not session_id:
    return None

  rrule_core = recurring_to_rrule(item)
  if not rrule_core:
    _log_debug(f"[GCAL] recurring_to_rrule returned falsy: {rrule_core!r}, "
               f"item recurrence={item.get('recurrence')}, rrule={item.get('rrule')}")
    return None

  _log_debug(f"[GCAL] recurring_to_rrule result: {rrule_core}")

  title = (item.get("title") or "").strip()
  if not title:
    _log_debug("[GCAL] recurring event title is empty")
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

  # Align start_date to the first matching BYDAY so Google Calendar generates
  # correct recurring instances.  e.g. if start_date is Wed but BYDAY=TU,TH,
  # advance to the next Thu.
  start_date = _align_start_to_byday(start_date, rrule_core)

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

    _log_debug(f"[GCAL] create recurring event body: recurrence={event_body.get('recurrence')}, "
               f"start={event_body.get('start')}, end={event_body.get('end')}, "
               f"summary={event_body.get('summary')}")

    created = service.events().insert(calendarId=calendar_id
                                      or GOOGLE_CALENDAR_ID,
                                      body=event_body).execute()
    _log_debug(f"[GCAL] recurring event created: id={created.get('id')}, "
               f"recurrence={created.get('recurrence')}")
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
  resolved_calendar = calendar_id or GOOGLE_CALENDAR_ID
  _log_debug(f"[GCAL] delete event: eventId={event_id}, calendarId={resolved_calendar}")
  service = get_gcal_service(session_id)
  try:
    service.events().delete(calendarId=resolved_calendar,
                            eventId=event_id).execute()
  except HttpError as exc:
    status = getattr(exc.resp, "status", None)
    # 404/410 = already deleted or not found — treat as success
    if status in (404, 410):
      _log_debug(f"[GCAL] delete event: already gone (status={status})")
      return
    _log_debug(f"[GCAL] delete event error: status={status}, "
               f"content={getattr(exc, 'content', b'')[:500]}")
    raise


# ---------------------------------------------------------------------------
#  Batch API helpers
# ---------------------------------------------------------------------------

def _build_single_event_body(
    title: str,
    start_iso: str,
    end_iso: Optional[str],
    location: Optional[str],
    all_day: Optional[bool] = None,
    description: Optional[str] = None,
    attendees: Optional[List[str]] = None,
    reminders: Optional[List[int]] = None,
    visibility: Optional[str] = None,
    transparency: Optional[str] = None,
    meeting_url: Optional[str] = None,
    timezone_value: Optional[str] = None,
    color_id: Optional[str] = None,
) -> Dict[str, Any]:
  """Build a Google Calendar event body dict for a single event (no API call)."""
  use_all_day = bool(all_day)
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
    start_dt = datetime.strptime(start_iso, "%Y-%m-%dT%H:%M").replace(tzinfo=SEOUL)
    if end_iso:
      end_dt = datetime.strptime(end_iso, "%Y-%m-%dT%H:%M").replace(tzinfo=SEOUL)
    else:
      end_dt = start_dt + timedelta(hours=1)
    tz_value = timezone_value or "Asia/Seoul"
    event_body["start"] = {"dateTime": start_dt.isoformat(), "timeZone": tz_value}
    event_body["end"] = {"dateTime": end_dt.isoformat(), "timeZone": tz_value}

  if location:
    event_body["location"] = location
  return event_body


def _build_recurring_event_body(
    item: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
  """Build a Google Calendar event body dict for a recurring event (no API call).
  Returns None if the item is invalid."""
  rrule_core = recurring_to_rrule(item)
  if not rrule_core:
    _log_debug(f"[GCAL] batch: recurring_to_rrule returned falsy: {rrule_core!r}")
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
    start_date_obj = datetime.strptime(start_date_str, "%Y-%m-%d").date()
  except Exception:
    return None

  start_date_obj = _align_start_to_byday(start_date_obj, rrule_core)
  all_day = not (isinstance(time_str, str)
                 and re.match(r"^\d{2}:\d{2}$", time_str.strip()))

  event_body: Dict[str, Any] = {
      "summary": title,
      "recurrence": [f"RRULE:{rrule_core}"],
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
    event_body["start"] = {"date": start_date_obj.strftime("%Y-%m-%d")}
    end_date_excl = (start_date_obj + timedelta(days=1)).strftime("%Y-%m-%d")
    event_body["end"] = {"date": end_date_excl}
  else:
    hh, mm = [int(x) for x in time_str.strip().split(":")]
    start_dt = datetime(start_date_obj.year, start_date_obj.month,
                        start_date_obj.day, hh, mm, tzinfo=SEOUL)
    if duration_minutes and isinstance(duration_minutes, (int, float)) and duration_minutes > 0:
      end_dt = start_dt + timedelta(minutes=int(duration_minutes))
    else:
      end_dt = start_dt + timedelta(hours=1)
    event_body["start"] = {"dateTime": start_dt.isoformat(), "timeZone": timezone_value}
    event_body["end"] = {"dateTime": end_dt.isoformat(), "timeZone": timezone_value}

  if location:
    event_body["location"] = (location or "").strip() or None
  return event_body


def gcal_batch_insert_events(
    bodies: List[Dict[str, Any]],
    session_id: str,
    calendar_id: Optional[str] = None,
) -> List[Optional[str]]:
  """Insert multiple events in a single batch HTTP request.
  Returns a list of event IDs (or None for failed items) in the same order as *bodies*.
  """
  if not bodies:
    return []
  if not is_gcal_configured():
    raise HTTPException(status_code=400,
                        detail="Google Calendar integration is not configured.")
  if not session_id:
    raise HTTPException(status_code=401, detail="Google login is required.")

  service = get_gcal_service(session_id)
  resolved_cal = calendar_id or GOOGLE_CALENDAR_ID
  results: List[Optional[str]] = [None] * len(bodies)
  errors: List[str] = []

  def _callback(index: int):
    def _inner(request_id, response, exception):
      if exception is not None:
        _log_debug(f"[GCAL] batch insert [{index}] error: {exception}")
        errors.append(f"items[{index}]: {exception}")
        return
      event_id = response.get("id") if isinstance(response, dict) else None
      results[index] = str(event_id) if event_id else None
      if not event_id:
        errors.append(f"items[{index}]: missing event id in response")
    return _inner

  batch = service.new_batch_http_request()
  for idx, body in enumerate(bodies):
    req = service.events().insert(calendarId=resolved_cal, body=body)
    batch.add(req, callback=_callback(idx), request_id=str(idx))
  batch.execute()

  if errors:
    _log_debug(f"[GCAL] batch insert errors: {errors}")
  return results


def gcal_batch_update_events(
    updates: List[Dict[str, Any]],
    session_id: str,
) -> List[bool]:
  """Patch multiple events in a single batch HTTP request.
  Each entry in *updates* must have 'event_id' and 'body' keys.
  Returns a list of success booleans in the same order.
  """
  if not updates:
    return []
  if not is_gcal_configured():
    raise HTTPException(status_code=400,
                        detail="Google Calendar integration is not configured.")
  if not session_id:
    raise HTTPException(status_code=401, detail="Google login is required.")

  service = get_gcal_service(session_id)
  results: List[bool] = [False] * len(updates)
  errors: List[str] = []

  def _callback(index: int):
    def _inner(request_id, response, exception):
      if exception is not None:
        _log_debug(f"[GCAL] batch update [{index}] error: {exception}")
        errors.append(f"items[{index}]: {exception}")
        return
      results[index] = True
    return _inner

  batch = service.new_batch_http_request()
  for idx, entry in enumerate(updates):
    raw_event_id = entry["event_id"]
    cal_id = entry.get("calendar_id") or GOOGLE_CALENDAR_ID
    body = entry["body"]
    req = service.events().patch(calendarId=cal_id, eventId=raw_event_id, body=body)
    batch.add(req, callback=_callback(idx), request_id=str(idx))
  batch.execute()

  if errors:
    _log_debug(f"[GCAL] batch update errors: {errors}")
  return results


def gcal_batch_delete_events(
    event_ids: List[str],
    session_id: str,
) -> List[bool]:
  """Delete multiple events in a single batch HTTP request.
  Each entry in *event_ids* is in 'calendar_id::event_id' or plain 'event_id' format.
  Returns a list of success booleans in the same order.
  """
  if not event_ids:
    return []
  if not is_gcal_configured():
    raise HTTPException(status_code=400,
                        detail="Google Calendar integration is not configured.")
  if not session_id:
    raise HTTPException(status_code=401, detail="Google login is required.")

  service = get_gcal_service(session_id)
  results: List[bool] = [False] * len(event_ids)
  errors: List[str] = []

  parsed: List[Tuple[str, str]] = []
  for eid in event_ids:
    raw_id, parsed_cal = _split_gcal_event_key(eid)
    parsed.append((raw_id, parsed_cal or GOOGLE_CALENDAR_ID))

  def _callback(index: int):
    def _inner(request_id, response, exception):
      if exception is not None:
        if isinstance(exception, HttpError):
          status = getattr(exception.resp, "status", None)
          if status in (404, 410):
            # Already deleted — treat as success
            results[index] = True
            return
        _log_debug(f"[GCAL] batch delete [{index}] error: {exception}")
        errors.append(f"{event_ids[index]}: {exception}")
        return
      results[index] = True
    return _inner

  batch = service.new_batch_http_request()
  for idx, (raw_id, cal_id) in enumerate(parsed):
    req = service.events().delete(calendarId=cal_id, eventId=raw_id)
    batch.add(req, callback=_callback(idx), request_id=str(idx))
  batch.execute()

  if errors:
    _log_debug(f"[GCAL] batch delete errors: {errors}")
  return results


# ---------------------------------------------------------------------------
#  Task batch API helpers
# ---------------------------------------------------------------------------

def gcal_batch_insert_tasks(
    bodies: List[Dict[str, Any]],
    session_id: str,
    tasklist: str = "@default",
    emit_deltas: bool = True,
) -> List[Optional[Dict[str, Any]]]:
  """Insert multiple tasks in a single batch HTTP request.
  Returns task payloads (or None for failed items) in the same order.
  """
  if not bodies:
    return []
  if not is_gcal_configured():
    raise HTTPException(status_code=400,
                        detail="Google Calendar integration is not configured.")
  if not session_id:
    raise HTTPException(status_code=401, detail="Google login is required.")

  service = get_google_tasks_service(session_id)
  results: List[Optional[Dict[str, Any]]] = [None] * len(bodies)
  errors: List[str] = []

  def _callback(index: int):
    def _inner(request_id, response, exception):
      if exception is not None:
        _log_debug(f"[GCAL] task batch insert [{index}] error: {exception}")
        errors.append(f"items[{index}]: {exception}")
        return
      results[index] = response if isinstance(response, dict) else None
      if results[index] is None:
        errors.append(f"items[{index}]: missing task response")
    return _inner

  batch = service.new_batch_http_request()
  request_count = 0
  for idx, body in enumerate(bodies):
    req = service.tasks().insert(tasklist=tasklist, body=body)
    batch.add(req, callback=_callback(idx), request_id=str(idx))
    request_count += 1
  if request_count > 0:
    batch.execute()

  if errors:
    _log_debug(f"[GCAL] task batch insert errors: {errors}")
  if emit_deltas:
    for result in results:
      if not isinstance(result, dict):
        continue
      upsert_google_task_cache(session_id, result)
      emit_google_task_delta(session_id,
                             "upsert",
                             task=result,
                             bump_if_missing=True)
  return results


def gcal_batch_patch_tasks(
    updates: List[Dict[str, Any]],
    session_id: str,
    tasklist: str = "@default",
    emit_deltas: bool = True,
) -> List[Optional[Dict[str, Any]]]:
  """Patch multiple tasks in a single batch HTTP request.
  Each entry must have 'task_id' and 'body'.
  Returns task payloads (or None for failed items) in the same order.
  """
  if not updates:
    return []
  if not is_gcal_configured():
    raise HTTPException(status_code=400,
                        detail="Google Calendar integration is not configured.")
  if not session_id:
    raise HTTPException(status_code=401, detail="Google login is required.")

  service = get_google_tasks_service(session_id)
  results: List[Optional[Dict[str, Any]]] = [None] * len(updates)
  errors: List[str] = []

  def _callback(index: int):
    def _inner(request_id, response, exception):
      if exception is not None:
        _log_debug(f"[GCAL] task batch patch [{index}] error: {exception}")
        errors.append(f"items[{index}]: {exception}")
        return
      results[index] = response if isinstance(response, dict) else None
      if results[index] is None:
        errors.append(f"items[{index}]: missing task response")
    return _inner

  batch = service.new_batch_http_request()
  request_count = 0
  for idx, entry in enumerate(updates):
    task_id = str(entry.get("task_id") or "").strip()
    body = entry.get("body")
    if not task_id or not isinstance(body, dict):
      errors.append(f"items[{idx}]: task_id/body is invalid")
      continue
    req = service.tasks().patch(tasklist=tasklist, task=task_id, body=body)
    batch.add(req, callback=_callback(idx), request_id=str(idx))
    request_count += 1
  if request_count > 0:
    batch.execute()

  if errors:
    _log_debug(f"[GCAL] task batch patch errors: {errors}")
  if emit_deltas:
    for result in results:
      if not isinstance(result, dict):
        continue
      upsert_google_task_cache(session_id, result)
      emit_google_task_delta(session_id,
                             "upsert",
                             task=result,
                             bump_if_missing=True)
  return results


def gcal_batch_delete_tasks(
    task_ids: List[str],
    session_id: str,
    tasklist: str = "@default",
    emit_deltas: bool = True,
) -> List[bool]:
  """Delete multiple tasks in a single batch HTTP request.
  Returns a list of success booleans in the same order.
  """
  if not task_ids:
    return []
  if not is_gcal_configured():
    raise HTTPException(status_code=400,
                        detail="Google Calendar integration is not configured.")
  if not session_id:
    raise HTTPException(status_code=401, detail="Google login is required.")

  service = get_google_tasks_service(session_id)
  results: List[bool] = [False] * len(task_ids)
  errors: List[str] = []

  def _callback(index: int):
    def _inner(request_id, response, exception):
      if exception is not None:
        if isinstance(exception, HttpError):
          status = getattr(exception.resp, "status", None)
          if status in (404, 410):
            results[index] = True
            return
        _log_debug(f"[GCAL] task batch delete [{index}] error: {exception}")
        errors.append(f"items[{index}]: {exception}")
        return
      results[index] = True
    return _inner

  batch = service.new_batch_http_request()
  request_count = 0
  for idx, task_id in enumerate(task_ids):
    clean_id = str(task_id or "").strip()
    if not clean_id:
      errors.append(f"items[{idx}]: task_id is empty")
      continue
    req = service.tasks().delete(tasklist=tasklist, task=clean_id)
    batch.add(req, callback=_callback(idx), request_id=str(idx))
    request_count += 1
  if request_count > 0:
    batch.execute()

  if errors:
    _log_debug(f"[GCAL] task batch delete errors: {errors}")
  if emit_deltas:
    for idx, ok in enumerate(results):
      if not ok:
        continue
      task_id = str(task_ids[idx] or "").strip()
      if not task_id:
        continue
      remove_google_task_cache(session_id, task_id)
      emit_google_task_delta(session_id,
                             "delete",
                             task_id=task_id,
                             bump_if_missing=True)
  return results


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

    return (date_obj.strftime("%Y-%m-%dT00:00"), True)

  return (None, False)


class SyncTokenInvalid(Exception):

  def __init__(self, kind: str = "invalid") -> None:
    super().__init__(kind)
    self.kind = kind


def _normalize_range(range_start: date, range_end: date) -> Tuple[date, date]:
  if range_end < range_start:
    return range_end, range_start
  return range_start, range_end


def _cache_coverage(cache_entry: Dict[str, Any]) -> Tuple[Optional[date], Optional[date]]:
  start_raw = cache_entry.get("coverage_start")
  end_raw = cache_entry.get("coverage_end")
  if not isinstance(start_raw, str) or not isinstance(end_raw, str):
    return None, None
  try:
    start_date = date.fromisoformat(start_raw)
    end_date = date.fromisoformat(end_raw)
  except Exception:
    return None, None
  if end_date < start_date:
    start_date, end_date = end_date, start_date
  return start_date, end_date


def _set_cache_coverage(cache_entry: Dict[str, Any],
                        range_start: date,
                        range_end: date) -> None:
  range_start, range_end = _normalize_range(range_start, range_end)
  current_start, current_end = _cache_coverage(cache_entry)
  if current_start is None or current_end is None:
    cache_entry["coverage_start"] = range_start.isoformat()
    cache_entry["coverage_end"] = range_end.isoformat()
    return
  cache_entry["coverage_start"] = min(current_start, range_start).isoformat()
  cache_entry["coverage_end"] = max(current_end, range_end).isoformat()


def _cache_covers_range(cache_entry: Dict[str, Any],
                        range_start: date,
                        range_end: date) -> bool:
  coverage_start, coverage_end = _cache_coverage(cache_entry)
  if coverage_start is None or coverage_end is None:
    return False
  range_start, range_end = _normalize_range(range_start, range_end)
  return coverage_start <= range_start and coverage_end >= range_end


def _cache_events_map(cache_entry: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
  events = cache_entry.get("events")
  if not isinstance(events, dict):
    events = {}
    cache_entry["events"] = events
  return events


def _cache_calendars_state(cache_entry: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
  calendars_state = cache_entry.get("calendars")
  if not isinstance(calendars_state, dict):
    calendars_state = {}
    cache_entry["calendars"] = calendars_state
  return calendars_state


def _touch_google_cache(cache_entry: Dict[str, Any], *, dirty: Optional[bool] = None) -> None:
  cache_entry["updated_at"] = _now_iso_minute()
  cache_entry["updated_at_ts"] = time.time()
  if isinstance(dirty, bool):
    cache_entry["dirty"] = dirty


def _cached_events_for_range(cache_entry: Dict[str, Any],
                             range_start: date,
                             range_end: date) -> List[Dict[str, Any]]:
  range_start, range_end = _normalize_range(range_start, range_end)
  events = _cache_events_map(cache_entry)
  items: List[Dict[str, Any]] = []
  for event in events.values():
    if not isinstance(event, dict):
      continue
    if _event_in_date_range(event, range_start, range_end):
      items.append(event)
  items.sort(key=lambda ev: ev.get("start") or "")
  return items


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

  # Detect recurring: raw event has recurringEventId when fetched with
  # singleEvents=True, or has recurrence[] when it's the series master.
  raw_recurring_event_id = raw.get("recurringEventId")
  raw_recurrence = raw.get("recurrence")
  is_recurring = bool(raw_recurring_event_id or raw_recurrence)

  return {
      "id": raw.get("id"),
      "calendar_id": calendar_id,
      "google_event_id": raw.get("id"),
      "title": raw.get("summary") or "(?쒕ぉ ?놁쓬)",
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
      "recur": "recurring" if is_recurring else None,
      "recurring_event_id": raw_recurring_event_id,
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
                             sync_token: Optional[str] = None,
                             query: Optional[str] = None,
                             max_results: Optional[int] = None,
                             order_by: Optional[str] = None
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
    }
    if sync_token:
      params["syncToken"] = sync_token
      params["showDeleted"] = True
    else:
      params["timeMin"] = time_min.isoformat()
      params["timeMax"] = time_max.isoformat()
      if query:
        params["q"] = query
      if isinstance(max_results, int) and max_results > 0:
        params["maxResults"] = max_results
      params["orderBy"] = order_by or "startTime"

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


def _normalize_gcal_items(raw_items: List[Dict[str, Any]],
                          range_start: date,
                          range_end: date,
                          calendar_id: Optional[str]) -> List[Dict[str, Any]]:
  items: List[Dict[str, Any]] = []
  for raw in raw_items:
    if not isinstance(raw, dict):
      continue
    if raw.get("status") == "cancelled":
      continue
    normalized = _normalize_gcal_event(raw, calendar_id)
    if not normalized:
      continue
    if _event_in_date_range(normalized, range_start, range_end):
      items.append(normalized)
  return items


def _upsert_event_in_session_cache(session_id: str, event: Dict[str, Any]) -> None:
  if not session_id or not isinstance(event, dict):
    return
  cache_key = _cache_event_key(event.get("calendar_id"), event.get("id"))
  if not cache_key:
    return

  cache_entry = _get_google_cache(session_id)
  events = _cache_events_map(cache_entry)
  coverage_start, coverage_end = _cache_coverage(cache_entry)
  if coverage_start is None or coverage_end is None:
    events[cache_key] = copy.deepcopy(event)
    _touch_google_cache(cache_entry, dirty=False)
    return
  if _event_in_date_range(event, coverage_start, coverage_end):
    events[cache_key] = copy.deepcopy(event)
  else:
    events.pop(cache_key, None)
  _touch_google_cache(cache_entry, dirty=False)


def _remove_event_from_session_cache(session_id: str,
                                     event_id: str,
                                     calendar_id: Optional[str] = None) -> None:
  if not session_id or not event_id:
    return
  raw_event_id, parsed_calendar = _split_gcal_event_key(event_id)
  resolved_calendar = calendar_id or parsed_calendar
  exact_keys = {raw_event_id}
  if isinstance(resolved_calendar, str) and resolved_calendar:
    exact_keys.add(f"{resolved_calendar}::{raw_event_id}")

  cache_entry = _get_google_cache(session_id)
  events = _cache_events_map(cache_entry)
  removed = False
  for key in list(events.keys()):
    if key in exact_keys:
      events.pop(key, None)
      removed = True
      continue
    if "::" in key and key.endswith(f"::{raw_event_id}") and not resolved_calendar:
      events.pop(key, None)
      removed = True
  if removed:
    _touch_google_cache(cache_entry, dirty=False)


def _remove_recurring_instances_from_cache(session_id: str,
                                           base_event_id: str) -> bool:
  """Remove all expanded instances whose recurringEventId matches *base_event_id*.

  Returns True if any instances were removed.
  """
  if not session_id or not base_event_id:
    return False
  cache_entry = _get_google_cache(session_id)
  events = _cache_events_map(cache_entry)
  removed = False
  for key in list(events.keys()):
    ev = events.get(key)
    if not isinstance(ev, dict):
      continue
    # Match by recurring_event_id (set by _normalize_gcal_event)
    if ev.get("recurring_event_id") == base_event_id:
      events.pop(key, None)
      removed = True
      continue
    # Also match by cache key pattern: instance keys contain the base ID
    # followed by '_' and a timestamp, e.g. baseId_20260211T010000Z
    raw_key = key.split("::")[-1] if "::" in key else key
    if raw_key.startswith(base_event_id + "_"):
      events.pop(key, None)
      removed = True
  if removed:
    _touch_google_cache(cache_entry, dirty=False)
  return removed


def emit_google_event_delta(session_id: str,
                            action: str,
                            *,
                            event: Optional[Dict[str, Any]] = None,
                            event_id: Optional[str] = None,
                            calendar_id: Optional[str] = None,
                            revision: Optional[int] = None,
                            op_id: Optional[str] = None,
                            bump_if_missing: bool = True) -> Dict[str, Any]:
  if revision is None and bump_if_missing:
    revision = bump_google_revision(session_id, "events")
  if revision is None:
    revision = get_google_revision(session_id)
  if not isinstance(op_id, str) or not op_id.strip():
    op_id = _next_google_op_id(session_id, "events")
  payload: Dict[str, Any] = {"action": action}
  if isinstance(event, dict):
    payload["event"] = copy.deepcopy(event)
    payload["event_id"] = event.get("id")
    payload["calendar_id"] = event.get("calendar_id")
  else:
    if isinstance(event_id, str) and event_id:
      payload["event_id"] = event_id
    if isinstance(calendar_id, str) and calendar_id:
      payload["calendar_id"] = calendar_id
  payload["revision"] = int(revision)
  payload["op_id"] = op_id
  _emit_google_sse(session_id, "google_delta", payload)
  return {
      "new_revision": int(revision),
      "op_id": op_id,
  }


def fetch_google_event_by_id(session_id: str,
                             event_id: str,
                             calendar_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
  raw_event_id, parsed_calendar = _split_gcal_event_key(event_id)
  if not raw_event_id:
    return None
  preferred_calendar = calendar_id or parsed_calendar

  service = get_gcal_service(session_id)
  candidate_calendars: List[str] = []
  if preferred_calendar:
    candidate_calendars.append(preferred_calendar)
  else:
    try:
      calendars = list_google_calendars(session_id)
      candidate_calendars.extend([
          item["id"] for item in calendars if isinstance(item, dict) and isinstance(item.get("id"), str)
      ])
    except Exception:
      candidate_calendars.append(GOOGLE_CALENDAR_ID)
  if not candidate_calendars:
    candidate_calendars.append(GOOGLE_CALENDAR_ID)

  for cal_id in candidate_calendars:
    try:
      raw = service.events().get(calendarId=cal_id, eventId=raw_event_id).execute()
    except HttpError as exc:
      status = getattr(exc.resp, "status", None)
      if status in (404, 410):
        continue
      raise
    if not isinstance(raw, dict):
      continue
    if raw.get("status") == "cancelled":
      return None
    normalized = _normalize_gcal_event(raw, cal_id)
    if normalized:
      return normalized
  return None


def sync_google_event_after_write(session_id: str,
                                  event_id: str,
                                  calendar_id: Optional[str] = None,
                                  emit_sse: bool = True) -> Dict[str, Any]:
  if not session_id or not event_id:
    return {
        "event": None,
        "new_revision": 0,
        "op_id": None,
    }
  context_key = _context_cache_key_for_session_mode(session_id, True)
  try:
    latest = fetch_google_event_by_id(session_id, event_id, calendar_id=calendar_id)
  except Exception:
    latest = None

  if isinstance(latest, dict):
    # If the fetched event is a recurring series master (has recur but no
    # recurring_event_id), it must NOT be upserted into the session cache
    # which is built with singleEvents=True (expanded instances).  Instead
    # mark the cache dirty so the next list request re-fetches from Google
    # and properly expands all instances.
    is_master_recurring = (
        latest.get("recur") == "recurring"
        and not latest.get("recurring_event_id")
    )
    new_revision = bump_google_revision(session_id, "events")
    op_id = _next_google_op_id(session_id, "events")
    if is_master_recurring:
      _mark_google_cache_dirty(session_id)
    else:
      _upsert_event_in_session_cache(session_id, latest)
    _clear_context_cache(context_key)
    if emit_sse:
      if is_master_recurring:
        _emit_google_sse(session_id, "google_sync", {
            "calendar_id": calendar_id,
            "revision": new_revision,
            "op_id": op_id,
        })
      else:
        emit_google_event_delta(session_id,
                                "upsert",
                                event=latest,
                                revision=new_revision,
                                op_id=op_id,
                                bump_if_missing=False)
    return {
        "event": latest,
        "new_revision": new_revision,
        "op_id": op_id,
    }

  new_revision = bump_google_revision(session_id, "events")
  op_id = _next_google_op_id(session_id, "events")
  _mark_google_cache_dirty(session_id)
  _clear_context_cache(context_key)
  if emit_sse:
    _emit_google_sse(session_id, "google_sync", {
        "calendar_id": calendar_id,
        "revision": new_revision,
        "op_id": op_id,
    })
  return {
      "event": None,
      "new_revision": new_revision,
      "op_id": op_id,
  }


def emit_google_sync(session_id: str,
                     *,
                     resource: str = "events",
                     bump_revision: bool = True,
                     payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
  """Emit a single google_sync SSE to trigger a frontend refresh."""
  if bump_revision:
    new_revision = bump_google_revision(session_id, resource)
  else:
    new_revision = get_google_revision(session_id)
  op_id = _next_google_op_id(session_id, "sync")
  data = payload.copy() if isinstance(payload, dict) else {}
  data["revision"] = new_revision
  data["op_id"] = op_id
  _emit_google_sse(session_id, "google_sync", data)
  return {
      "new_revision": new_revision,
      "op_id": op_id,
  }


def sync_google_event_after_delete(session_id: str,
                                   event_id: str,
                                   calendar_id: Optional[str] = None,
                                   emit_sse: bool = True) -> Dict[str, Any]:
  if not session_id or not event_id:
    return {
        "new_revision": 0,
        "op_id": None,
    }
  raw_event_id, parsed_calendar = _split_gcal_event_key(event_id)
  resolved_calendar = calendar_id or parsed_calendar
  new_revision = bump_google_revision(session_id, "events")
  op_id = _next_google_op_id(session_id, "events")
  _remove_event_from_session_cache(session_id, raw_event_id, resolved_calendar)

  # When the deleted event is a recurring series master, also purge all
  # expanded instances from the singleEvents=True cache.  Instance cache
  # keys look like  `calId::baseId_20260211T010000Z`  and the normalised
  # event dicts carry  `recurring_event_id == baseId`.
  had_recurring_instances = _remove_recurring_instances_from_cache(
      session_id, raw_event_id)

  if had_recurring_instances:
    # After purging instances, mark cache dirty so the next list request
    # re-fetches from Google API rather than serving stale data.
    _mark_google_cache_dirty(session_id)

  _clear_context_cache(_context_cache_key_for_session_mode(session_id, True))
  if emit_sse:
    if had_recurring_instances:
      # Recurring series was deleted — use google_sync so the frontend does
      # a full refresh instead of trying to match individual instance IDs.
      _emit_google_sse(session_id, "google_sync",
                       {
                           "calendar_id": resolved_calendar,
                           "revision": new_revision,
                           "op_id": op_id,
                       })
    else:
      emit_google_event_delta(session_id,
                              "delete",
                              event_id=raw_event_id,
                              calendar_id=resolved_calendar,
                              revision=new_revision,
                              op_id=op_id,
                              bump_if_missing=False)
  return {
      "new_revision": new_revision,
      "op_id": op_id,
  }


def fetch_google_events_between_with_options(
    range_start: date,
    range_end: date,
    session_id: str,
    *,
    calendar_id: Optional[str] = None,
    query: Optional[str] = None,
    limit: Optional[int] = None,
    all_day: Optional[bool] = None,
) -> List[Dict[str, Any]]:
  if not is_gcal_configured():
    raise HTTPException(status_code=400,
                        detail="Google Calendar ?곕룞???ㅼ젙?섏? ?딆븯?듬땲??")
  if not session_id:
    raise HTTPException(status_code=401, detail="Google 濡쒓렇?몄씠 ?꾩슂?⑸땲??")

  try:
    service = get_gcal_service(session_id)
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google Calendar ?몄쬆???ㅽ뙣?덉뒿?덈떎: {exc}") from exc

  calendar_ids: List[str] = []
  if calendar_id:
    calendar_ids = [calendar_id]
  else:
    try:
      calendars = list_google_calendars(session_id)
    except Exception as exc:
      raise HTTPException(status_code=502,
                          detail=f"Google Calendar 紐⑸줉 議고쉶 ?ㅽ뙣: {exc}") from exc
    calendar_ids = [item["id"] for item in calendars if isinstance(item, dict)]

  if not calendar_ids:
    return []

  items: List[Dict[str, Any]] = []
  max_results = limit if isinstance(limit, int) and limit > 0 else None

  for cal_id in calendar_ids:
    raw_items, _ = _fetch_google_events_raw(service,
                                            range_start,
                                            range_end,
                                            cal_id,
                                            query=query,
                                            max_results=max_results)
    items.extend(_normalize_gcal_items(raw_items, range_start, range_end, cal_id))
    if max_results and len(items) >= max_results:
      break

  if all_day is not None:
    items = [item for item in items if bool(item.get("all_day")) == all_day]

  items.sort(key=lambda ev: ev.get("start") or "")
  if max_results:
    items = items[:max_results]

  return items


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


def _merge_date_ranges(ranges: List[Tuple[date, date]]) -> List[Tuple[date, date]]:
  normalized: List[Tuple[date, date]] = []
  for start_date, end_date in ranges:
    start_date, end_date = _normalize_range(start_date, end_date)
    normalized.append((start_date, end_date))
  if not normalized:
    return []
  normalized.sort(key=lambda item: item[0])
  merged: List[Tuple[date, date]] = []
  current_start, current_end = normalized[0]
  for start_date, end_date in normalized[1:]:
    if start_date <= current_end + timedelta(days=1):
      if end_date > current_end:
        current_end = end_date
      continue
    merged.append((current_start, current_end))
    current_start, current_end = start_date, end_date
  merged.append((current_start, current_end))
  return merged


def _refresh_event_cache_slice(service,
                               cache_entry: Dict[str, Any],
                               calendar_ids: List[str],
                               range_start: date,
                               range_end: date) -> None:
  cache_events = _cache_events_map(cache_entry)
  calendars_state = _cache_calendars_state(cache_entry)
  for calendar_id in calendar_ids:
    raw_items, next_sync = _fetch_google_events_raw(service,
                                                    range_start,
                                                    range_end,
                                                    calendar_id)
    _reset_gcal_cache_range(cache_events, range_start, range_end, calendar_id)
    _apply_gcal_items_to_cache(cache_events, raw_items, range_start, range_end,
                               calendar_id)
    calendars_state[calendar_id] = {
        "sync_token": next_sync,
        "sync_disabled": False,
    }
  _set_cache_coverage(cache_entry, range_start, range_end)


def fetch_google_events_between(range_start: date,
                                range_end: date,
                                session_id: str,
                                *,
                                force_refresh: bool = False) -> List[Dict[str, Any]]:
  if not is_gcal_configured():
    raise HTTPException(status_code=400,
                        detail="Google Calendar integration is not configured.")
  if not session_id:
    raise HTTPException(status_code=401, detail="Google login is required.")

  range_start, range_end = _normalize_range(range_start, range_end)
  cache_entry = _get_google_cache(session_id)

  # Serve directly from cache when requested range is already covered.
  if not force_refresh and _cache_covers_range(cache_entry, range_start, range_end):
    return _cached_events_for_range(cache_entry, range_start, range_end)

  try:
    service = get_gcal_service(session_id)
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google Calendar auth failed: {exc}") from exc

  if _gcal_watch_enabled():
    ensure_gcal_watches(session_id)

  try:
    calendars = list_google_calendars(session_id)
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google Calendar list fetch failed: {exc}") from exc

  calendar_ids = [item["id"] for item in calendars if isinstance(item, dict)]
  cache_events = _cache_events_map(cache_entry)
  calendars_state = _cache_calendars_state(cache_entry)

  if not calendar_ids:
    cache_events.clear()
    calendars_state.clear()
    cache_entry["coverage_start"] = range_start.isoformat()
    cache_entry["coverage_end"] = range_end.isoformat()
    _touch_google_cache(cache_entry, dirty=False)
    return []

  active_ids = set(calendar_ids)
  for cache_id in list(cache_events.keys()):
    if not isinstance(cache_id, str):
      continue
    if "::" not in cache_id:
      continue
    cal_id = cache_id.split("::", 1)[0]
    if cal_id not in active_ids:
      cache_events.pop(cache_id, None)
  for cached_calendar_id in list(calendars_state.keys()):
    if cached_calendar_id not in active_ids:
      calendars_state.pop(cached_calendar_id, None)

  coverage_start, coverage_end = _cache_coverage(cache_entry)
  slices_to_fetch: List[Tuple[date, date]] = []
  if force_refresh or coverage_start is None or coverage_end is None:
    slices_to_fetch.append((range_start, range_end))
  else:
    if range_start < coverage_start:
      slices_to_fetch.append((range_start, coverage_start - timedelta(days=1)))
    if range_end > coverage_end:
      slices_to_fetch.append((coverage_end + timedelta(days=1), range_end))

  for slice_start, slice_end in _merge_date_ranges(slices_to_fetch):
    try:
      _refresh_event_cache_slice(service,
                                 cache_entry,
                                 calendar_ids,
                                 slice_start,
                                 slice_end)
    except Exception as exc:
      cache_entry["dirty"] = True
      raise HTTPException(status_code=502,
                          detail=f"Google Calendar fetch failed: {exc}") from exc

  _touch_google_cache(cache_entry, dirty=False)
  return _cached_events_for_range(cache_entry, range_start, range_end)


def refresh_google_cache_for_calendar(session_id: str,
                                      calendar_id: str) -> None:
  if not session_id or not calendar_id:
    return
  _clear_context_cache(_context_cache_key_for_session_mode(session_id, True))
  cache_entry = _get_google_cache(session_id)
  coverage_start, coverage_end = _cache_coverage(cache_entry)
  if coverage_start is None or coverage_end is None:
    return
  try:
    service = get_gcal_service(session_id)
  except Exception:
    _mark_google_cache_dirty(session_id)
    return

  cache_events = _cache_events_map(cache_entry)
  calendars_state = _cache_calendars_state(cache_entry)
  try:
    raw_items, next_sync = _fetch_google_events_raw(service,
                                                    coverage_start,
                                                    coverage_end,
                                                    calendar_id)
  except Exception:
    cache_entry["dirty"] = True
    return

  _reset_gcal_cache_range(cache_events, coverage_start, coverage_end, calendar_id)
  _apply_gcal_items_to_cache(cache_events,
                             raw_items,
                             coverage_start,
                             coverage_end,
                             calendar_id)
  calendars_state[calendar_id] = {
      "sync_token": next_sync,
      "sync_disabled": False,
  }
  _touch_google_cache(cache_entry, dirty=False)


def fetch_recent_google_events(session_id: str,
                               days: int = GOOGLE_RECENT_DAYS) -> List[Dict[str, Any]]:
  if days <= 0:
    days = GOOGLE_RECENT_DAYS

  if not is_gcal_configured():
    raise HTTPException(status_code=400,
                        detail="Google Calendar ?곕룞???ㅼ젙?섏? ?딆븯?듬땲??")
  if not session_id:
    raise HTTPException(status_code=401, detail="Google 濡쒓렇?몄씠 ?꾩슂?⑸땲??")

  try:
    service = get_gcal_service(session_id)
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google Calendar ?몄쬆???ㅽ뙣?덉뒿?덈떎: {exc}") from exc

  try:
    calendars = list_google_calendars(session_id)
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google Calendar 紐⑸줉 議고쉶 ?ㅽ뙣: {exc}") from exc

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
            "title": raw.get("summary") or "(?쒕ぉ ?놁쓬)",
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
