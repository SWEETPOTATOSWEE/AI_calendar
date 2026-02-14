from __future__ import annotations

import asyncio
import copy
import json
import logging
import urllib
import uuid
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse, StreamingResponse

from .config import (
    SESSION_COOKIE_NAME,
    OAUTH_STATE_COOKIE_NAME,
    SESSION_COOKIE_MAX_AGE_SECONDS,
    OAUTH_STATE_MAX_AGE_SECONDS,
    GOOGLE_WEBHOOK_TOKEN,
    ENABLE_GCAL,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
    GCAL_SCOPES,
    API_BASE,
    LLM_DEBUG,
)
from .models import (
    Event,
    EventCreate,
    EventUpdate,
    RecurringEventUpdate,
    RecurringExceptionPayload,
    IdsPayload,
    DeleteResult,
    TaskCreate,
    TaskUpdate,
    AgentRunRequest,
)
from .frontend import (
    CALENDAR_HTML_TEMPLATE,
    SETTINGS_HTML,
    LOGIN_HTML,
)
from .utils import (
    _log_debug,
    _parse_scope_dates,
    _normalize_google_timestamp,
    _now_iso_minute,
    _coerce_patch_start,
    _coerce_patch_end,
    _clean_optional_str,
    is_all_day_span,
    _normalize_color_id,
)
from .state import (
    store_event,
)
from .recurrence import _normalize_recurrence_dict, _normalize_rrule_core
from .gcal import (
    is_gcal_configured,
    load_gcal_token_for_request,
    load_gcal_token_for_session,
    save_gcal_token_for_session,
    clear_gcal_token_for_session,
    get_google_session_id,
    require_google_session_id,
    get_google_tasks_service,
    get_google_userinfo,
    list_google_calendars,
    ensure_gcal_watches,
    fetch_google_events_between,
    fetch_google_events_between_with_options,
    fetch_google_tasks,
    get_google_revision_state,
    fetch_recent_google_events,
    refresh_google_cache_for_calendar,
    gcal_create_single_event,
    gcal_update_event,
    gcal_delete_event,
    gcal_create_recurring_event,
    upsert_google_task_cache,
    remove_google_task_cache,
    emit_google_task_delta,
    emit_google_sync,
    sync_google_event_after_write,
    sync_google_event_after_delete,
    _gcal_watch_enabled,
    _load_gcal_watch_state,
    _save_gcal_watch_state,
    _get_watch_session_entry,
    _remove_watch_entry,
    _clear_watches_for_session,
    _emit_google_sse,
    _register_google_sse,
    _unregister_google_sse,
    _format_sse_event,
    _resolve_google_redirect_uri,
    _get_session_id,
    _new_session_id,
    _new_oauth_state,
    _store_oauth_state,
    _pop_oauth_state,
    _set_cookie,
    _delete_cookie,
    _clear_google_cache,
    _frontend_url,
)
from .agent import run_full_agent

router = APIRouter()
logger = logging.getLogger(__name__)
_AGENT_DEBUG_STORE: Dict[str, Dict[str, Any]] = {}
_AGENT_DEBUG_LOCK = Lock()
_AGENT_MUTATION_INTENTS = {
    "calendar.create_event",
    "calendar.update_event",
    "calendar.cancel_event",
    "task.create_task",
    "task.update_task",
    "task.cancel_task",
}


def _prewarm_agent_context_cache(session_id: str) -> None:
  if not session_id:
    return
  today = datetime.now(timezone.utc).date()
  range_start = today - timedelta(days=7)
  range_end = today + timedelta(days=30)
  try:
    fetch_google_events_between(range_start, range_end, session_id, force_refresh=True)
  except Exception as exc:
    _log_debug(f"[GCAL] prewarm events failed: {exc}")
  try:
    fetch_google_tasks(session_id, force_refresh=True)
  except Exception as exc:
    _log_debug(f"[GCAL] prewarm tasks failed: {exc}")


def _wrap_read_with_revision(session_id: str,
                             items: List[Dict[str, Any]]) -> Dict[str, Any]:
  revisions = get_google_revision_state(session_id)
  return {
      "items": items,
      "revision": revisions.get("revision", 0),
      "events_revision": revisions.get("events_revision", 0),
      "tasks_revision": revisions.get("tasks_revision", 0),
  }


def _attach_agent_revision(result: Dict[str, Any], session_id: str) -> Dict[str, Any]:
  if not isinstance(result, dict):
    return result
  revisions = get_google_revision_state(session_id)
  output = dict(result)
  output["revision"] = revisions.get("revision", 0)
  mutation_applied = False
  step_results = output.get("results")
  if isinstance(step_results, list):
    for step in step_results:
      if not isinstance(step, dict):
        continue
      if not bool(step.get("ok")):
        continue
      intent = str(step.get("intent") or "").strip()
      if intent in _AGENT_MUTATION_INTENTS:
        mutation_applied = True
        break
  if mutation_applied:
    output["new_revision"] = revisions.get("revision", 0)
  return output


def _agent_debug_start(session_id: str, input_as_text: str) -> str:
  if not LLM_DEBUG:
    return ""
  run_id = uuid.uuid4().hex
  now = datetime.now(timezone.utc).isoformat()
  snapshot = {
      "enabled": bool(LLM_DEBUG),
      "run_id": run_id,
      "status": "running",
      "current_node": "input_gate",
      "branch": None,
      "node_timeline": [{
          "node": "input_gate",
          "status": "running",
          "at": now,
      }],
      "llm_outputs": [],
      "node_outputs": {},
      "error": None,
      "started_at": now,
      "updated_at": now,
      "input_preview": (input_as_text or "")[:300],
  }
  with _AGENT_DEBUG_LOCK:
    _AGENT_DEBUG_STORE[session_id] = snapshot
  return run_id


def _agent_debug_update(session_id: str, run_id: str,
                        update: Dict[str, Any]) -> None:
  if not LLM_DEBUG or not run_id:
    return
  with _AGENT_DEBUG_LOCK:
    state = _AGENT_DEBUG_STORE.get(session_id)
    if not isinstance(state, dict):
      return
    if state.get("run_id") != run_id:
      return
    debug_obj = update.get("debug")
    if isinstance(debug_obj, dict):
      if isinstance(debug_obj.get("enabled"), bool):
        state["enabled"] = bool(debug_obj.get("enabled"))
      current_node = debug_obj.get("current_node")
      if isinstance(current_node, str) and current_node.strip():
        state["current_node"] = current_node.strip()
    node_timeline = update.get("node_timeline")
    if isinstance(node_timeline, list):
      state["node_timeline"] = copy.deepcopy(node_timeline)
    llm_outputs = update.get("llm_outputs")
    if isinstance(llm_outputs, list):
      state["llm_outputs"] = copy.deepcopy(llm_outputs)
    node_outputs = update.get("node_outputs")
    if isinstance(node_outputs, dict):
      state["node_outputs"] = copy.deepcopy(node_outputs)
    branch = update.get("branch")
    if isinstance(branch, str) and branch.strip():
      state["branch"] = branch.strip()
    state["updated_at"] = datetime.now(timezone.utc).isoformat()


def _agent_debug_finish(session_id: str, run_id: str, status: str,
                        trace: Optional[Dict[str, Any]] = None,
                        error: Optional[str] = None) -> None:
  if not LLM_DEBUG or not run_id:
    return
  with _AGENT_DEBUG_LOCK:
    state = _AGENT_DEBUG_STORE.get(session_id)
    if not isinstance(state, dict):
      return
    if state.get("run_id") != run_id:
      return
    if isinstance(trace, dict):
      debug_obj = trace.get("debug")
      if isinstance(debug_obj, dict):
        current_node = debug_obj.get("current_node")
        if isinstance(current_node, str) and current_node.strip():
          state["current_node"] = current_node.strip()
      node_timeline = trace.get("node_timeline")
      if isinstance(node_timeline, list):
        state["node_timeline"] = copy.deepcopy(node_timeline)
      llm_outputs = trace.get("llm_outputs")
      if isinstance(llm_outputs, list):
        state["llm_outputs"] = copy.deepcopy(llm_outputs)
      node_outputs = trace.get("node_outputs")
      if isinstance(node_outputs, dict):
        state["node_outputs"] = copy.deepcopy(node_outputs)
      branch = trace.get("branch")
      if isinstance(branch, str) and branch.strip():
        state["branch"] = branch.strip()
    state["status"] = status
    state["error"] = error
    state["updated_at"] = datetime.now(timezone.utc).isoformat()


def _agent_debug_get(session_id: Optional[str]) -> Dict[str, Any]:
  if not LLM_DEBUG:
    return {"enabled": False}
  if not session_id:
    return {"enabled": bool(LLM_DEBUG)}
  with _AGENT_DEBUG_LOCK:
    state = _AGENT_DEBUG_STORE.get(session_id)
    if not isinstance(state, dict):
      return {"enabled": bool(LLM_DEBUG)}
    return copy.deepcopy(state)

# -------------------------
# Google OAuth endpoints
# -------------------------
@router.get("/auth/google/login")
@router.get("/auth/google/login/")
def google_login(request: Request):
  _log_debug("[GCAL] login start")
  redirect_uri = _resolve_google_redirect_uri(request)
  _log_debug(f"[DEBUG] Google OAuth redirect_uri: {redirect_uri}")
  if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and redirect_uri):
    raise HTTPException(
        status_code=500,
        detail=
        "Google OAuth environment variables (GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI) are not configured.",
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
      "redirect_uri": redirect_uri,
      "response_type": "code",
      "scope": " ".join(GCAL_SCOPES),
      "access_type": "offline",
      "state": state_value,
  }
  if prompt:
    params["prompt"] = prompt
  url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
      params)
  resp = RedirectResponse(url)
  _log_debug(f"[GCAL] login redirect url={url}")
  _set_cookie(resp,
              SESSION_COOKIE_NAME,
              session_id,
              max_age=SESSION_COOKIE_MAX_AGE_SECONDS)
  _set_cookie(resp,
              OAUTH_STATE_COOKIE_NAME,
              state_value,
              max_age=OAUTH_STATE_MAX_AGE_SECONDS)
  _store_oauth_state(state_value, session_id, redirect_uri)
  return resp


@router.get("/auth/google/callback")
@router.get("/auth/google/callback/")
def google_callback(request: Request):
  code = request.query_params.get("code")
  error = request.query_params.get("error")
  state = request.query_params.get("state")
  _log_debug(f"[GCAL] callback start error={error} state={state}")
  expected_state = request.cookies.get(OAUTH_STATE_COOKIE_NAME)
  if error:
    _log_debug(f"[GCAL] callback error={error}")
    return JSONResponse({"ok": False, "error": error})

  if not code:
    raise HTTPException(status_code=400, detail="Missing code.")
  oauth_entry = _pop_oauth_state(state)
  if not state or (expected_state and state != expected_state and not oauth_entry):
    raise HTTPException(status_code=400, detail="State verification failed.")

  redirect_uri = (
      oauth_entry.get("redirect_uri") if isinstance(oauth_entry, dict) else None)
  redirect_uri = redirect_uri or GOOGLE_REDIRECT_URI
  if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and redirect_uri):
    raise HTTPException(
        status_code=500,
        detail=
        "Google OAuth environment variables (GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI) are not configured.",
    )

  token_endpoint = "https://oauth2.googleapis.com/token"
  data = {
      "code": code,
      "client_id": GOOGLE_CLIENT_ID,
      "client_secret": GOOGLE_CLIENT_SECRET,
      "redirect_uri": redirect_uri,
      "grant_type": "authorization_code",
  }

  resp = requests.post(token_endpoint, data=data)
  if not resp.ok:
    _log_debug(f"[GCAL] token exchange failed: {resp.status_code} {resp.text}")
    raise HTTPException(status_code=500,
                        detail=f"Token exchange failed: {resp.status_code} {resp.text}")

  stored_session_id = (
      oauth_entry.get("session_id")
      if isinstance(oauth_entry, dict) else None)
  session_id = _get_session_id(request) or stored_session_id
  if not session_id:
    raise HTTPException(status_code=400, detail="Session is missing.")

  token_json = resp.json()
  access_token = token_json.get("access_token")
  refresh_token = token_json.get("refresh_token")
  expires_in = token_json.get("expires_in")

  if not refresh_token:
    existing = load_gcal_token_for_session(session_id) or {}
    refresh_token = existing.get("refresh_token")

  if not access_token or not refresh_token:
    _log_debug("[GCAL] token exchange missing access/refresh token")
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
  _log_debug("[GCAL] token exchange success")
  if _gcal_watch_enabled():
    ensure_gcal_watches(session_id)
  _prewarm_agent_context_cache(session_id)

  # On success, redirect to calendar page
  resp = RedirectResponse(_frontend_url("/calendar"))
  _set_cookie(resp,
              SESSION_COOKIE_NAME,
              session_id,
              max_age=SESSION_COOKIE_MAX_AGE_SECONDS)
  _delete_cookie(resp, OAUTH_STATE_COOKIE_NAME)
  return resp


@router.get("/auth/google/status")
@router.get("/auth/google/status/")
def google_status(request: Request):
  token_data = load_gcal_token_for_request(request)
  userinfo = get_google_userinfo(request) if token_data else None
  photo_url = None
  if isinstance(userinfo, dict):
    picture = userinfo.get("picture")
    if isinstance(picture, str) and picture.strip():
      photo_url = picture
  return {
      "enabled": ENABLE_GCAL,
      "configured": is_gcal_configured(),
      "has_token": token_data is not None,
      "photo_url": photo_url,
  }


@router.post("/auth/google/webhook")
def google_webhook(request: Request):
  if not _gcal_watch_enabled():
    return Response(status_code=404)

  channel_id = request.headers.get("X-Goog-Channel-ID") or request.headers.get(
      "X-Goog-Channel-Id")
  resource_id = request.headers.get("X-Goog-Resource-ID") or request.headers.get(
      "X-Goog-Resource-Id")
  resource_state = request.headers.get("X-Goog-Resource-State", "")
  channel_token = request.headers.get("X-Goog-Channel-Token")

  if GOOGLE_WEBHOOK_TOKEN and channel_token != GOOGLE_WEBHOOK_TOKEN:
    return Response(status_code=403)
  if not channel_id:
    return Response(status_code=400)

  state = _load_gcal_watch_state()
  channel_info = state.get("channels", {}).get(channel_id)
  if not isinstance(channel_info, dict):
    return Response(status_code=404)
  if resource_id and channel_info.get("resource_id") not in (None, resource_id):
    return Response(status_code=403)

  session_id = channel_info.get("session_id")
  calendar_id = channel_info.get("calendar_id")
  if not session_id or not calendar_id:
    return Response(status_code=404)

  if isinstance(resource_state, str) and resource_state.lower() == "not_exists":
    session_entry = _get_watch_session_entry(state, session_id)
    calendars_state = session_entry.get("calendars", {})
    entry = calendars_state.get(calendar_id)
    if isinstance(entry, dict):
      _remove_watch_entry(state, session_id, calendar_id, entry)
      _save_gcal_watch_state(state)
    return JSONResponse({"ok": True})

  if isinstance(resource_state, str) and resource_state.lower() == "sync":
    return JSONResponse({"ok": True})

  refresh_google_cache_for_calendar(session_id, calendar_id)
  emit_google_sync(session_id,
                   resource="events",
                   bump_revision=True,
                   payload={"calendar_id": calendar_id})
  return JSONResponse({"ok": True})


# Temporary webhook URL compatibility endpoint (auto-expire later)
@router.post("/webhook/google")
def google_webhook_legacy(request: Request):
  """Temporary compatibility endpoint for old watch URL registrations."""
  # Return 200 OK to avoid repeated webhook retries from Google
  return JSONResponse({"ok": True, "deprecated": True})


GOOGLE_DELTA_BATCH_WINDOW_SECONDS = 3.0


def _extract_google_delta_key(payload: Dict[str, Any]) -> Optional[str]:
  if not isinstance(payload, dict):
    return None
  event_id_raw = payload.get("event_id")
  calendar_id_raw = payload.get("calendar_id")
  event = payload.get("event")
  if isinstance(event, dict):
    if event_id_raw is None:
      event_id_raw = event.get("id") or event.get("google_event_id")
    if calendar_id_raw is None:
      calendar_id_raw = event.get("calendar_id")
  event_id = str(event_id_raw or "").strip()
  if not event_id:
    return None
  calendar_id = str(calendar_id_raw or "").strip()
  return f"{calendar_id}::{event_id}" if calendar_id else event_id


def _coalesce_google_delta_batch(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
  order: List[str] = []
  latest_by_key: Dict[str, Dict[str, Any]] = {}
  passthrough: List[Dict[str, Any]] = []
  for raw in items:
    if not isinstance(raw, dict):
      continue
    payload = {k: v for k, v in raw.items() if k != "type"}
    action = str(payload.get("action") or "").strip().lower()
    if action not in {"upsert", "delete"}:
      passthrough.append(payload)
      continue
    key = _extract_google_delta_key(payload)
    if not key:
      passthrough.append(payload)
      continue
    if key not in latest_by_key:
      order.append(key)
    latest_by_key[key] = payload
  coalesced = [latest_by_key[key] for key in order if key in latest_by_key]
  coalesced.extend(passthrough)
  return coalesced


@router.get("/api/google/stream")
async def google_stream(request: Request):
  session_id = get_google_session_id(request)
  if not session_id:
    raise HTTPException(status_code=401, detail="Google login is required.")

  key, queue = _register_google_sse(session_id)

  async def event_generator():
    pending_payload: Optional[Dict[str, Any]] = None
    try:
      yield _format_sse_event("ready", {})
      while True:
        if await request.is_disconnected():
          break
        try:
          if pending_payload is not None:
            payload = pending_payload
            pending_payload = None
          else:
            payload = await asyncio.wait_for(queue.get(), timeout=20)
        except asyncio.TimeoutError:
          yield _format_sse_event("ping", {})
          continue
        if not isinstance(payload, dict):
          yield _format_sse_event("message", {})
          continue
        event_type = str(payload.get("type") or "message")
        if event_type != "google_delta":
          yield _format_sse_event(event_type, payload)
          continue

        loop = asyncio.get_running_loop()
        deadline = loop.time() + GOOGLE_DELTA_BATCH_WINDOW_SECONDS
        batch_items: List[Dict[str, Any]] = [payload]
        while True:
          remaining = deadline - loop.time()
          if remaining <= 0:
            break
          try:
            next_payload = await asyncio.wait_for(queue.get(), timeout=remaining)
          except asyncio.TimeoutError:
            break
          if not isinstance(next_payload, dict):
            continue
          next_type = str(next_payload.get("type") or "message")
          if next_type != "google_delta":
            pending_payload = next_payload
            break
          batch_items.append(next_payload)

        coalesced = _coalesce_google_delta_batch(batch_items)
        if len(coalesced) == 1:
          yield _format_sse_event("google_delta", {
              "type": "google_delta",
              **coalesced[0],
          })
        elif coalesced:
          yield _format_sse_event("google_delta_batch", {
              "type": "google_delta_batch",
              "events": coalesced,
              "count": len(coalesced),
          })
    finally:
      _unregister_google_sse(key, queue)

  return StreamingResponse(
      event_generator(),
      media_type="text/event-stream",
      headers={
          "Cache-Control": "no-cache",
          "Connection": "keep-alive",
          "X-Accel-Buffering": "no",
      },
  )


# -------------------------
# Admin / Logout
# -------------------------
@router.get("/admin")
def enter_admin():
  raise HTTPException(status_code=410, detail="Admin mode is disabled.")


@router.get("/admin/exit")
def exit_admin():
  raise HTTPException(status_code=410, detail="Admin mode is disabled.")


@router.get("/logout")
def logout(request: Request):
  session_id = _get_session_id(request)
  if session_id:
    _clear_watches_for_session(session_id)
  clear_gcal_token_for_session(session_id)
  _clear_google_cache(session_id)
  resp = RedirectResponse(_frontend_url("/"))
  _delete_cookie(resp, SESSION_COOKIE_NAME)
  _delete_cookie(resp, OAUTH_STATE_COOKIE_NAME)
  return resp


# -------------------------
# API endpoints
# -------------------------
@router.get("/api/events")
def list_events(request: Request,
                start_date: Optional[str] = Query(None),
                end_date: Optional[str] = Query(None)):
  session_id = require_google_session_id(request)
  scope = _parse_scope_dates(start_date,
                             end_date,
                             require=True,
                             max_days=3650,
                             label="조회")
  items = fetch_google_events_between(scope[0], scope[1], session_id)
  return _wrap_read_with_revision(session_id, items)


def _format_recent_google_event(item: Dict[str, Any]) -> Dict[str, Any]:
  created_raw = item.get("created") or item.get("updated")
  return {
      "id": item.get("id"),
      "calendar_id": item.get("calendar_id"),
      "title": item.get("title"),
      "start": item.get("start"),
      "end": item.get("end"),
      "location": item.get("location"),
      "all_day": item.get("all_day"),
      "created_at": _normalize_google_timestamp(created_raw),
      "source": "google",
      "google_event_id": item.get("id"),
  }


@router.get("/api/recent-events")
def list_recent_events(request: Request):
  session_id = require_google_session_id(request)
  try:
    data = fetch_recent_google_events(session_id)
    formatted = [_format_recent_google_event(item) for item in data]
    return _wrap_read_with_revision(session_id, formatted[:200])
  except HTTPException:
    raise
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google recent events fetch failed: {exc}") from exc


@router.get("/api/google/events")
def google_events(request: Request,
                  start_date: str = Query(..., alias="start_date"),
                  end_date: str = Query(..., alias="end_date"),
                  query: Optional[str] = Query(None, alias="query"),
                  calendar_id: Optional[str] = Query(None, alias="calendar_id"),
                  all_day: Optional[bool] = Query(None, alias="all_day"),
                  limit: Optional[int] = Query(None, alias="limit")):
  scope = _parse_scope_dates(start_date,
                             end_date,
                             require=True,
                             max_days=3650,
                             label="조회")
  session_id = get_google_session_id(request)
  if not session_id:
    raise HTTPException(status_code=401, detail="Google login is required.")
  if query or calendar_id or all_day is not None or (isinstance(limit, int) and limit > 0):
    items = fetch_google_events_between_with_options(scope[0],
                                                     scope[1],
                                                     session_id,
                                                     calendar_id=calendar_id,
                                                     query=query,
                                                     limit=limit,
                                                     all_day=all_day)
    return _wrap_read_with_revision(session_id, items)
  items = fetch_google_events_between(scope[0], scope[1], session_id)
  return _wrap_read_with_revision(session_id, items)


@router.get("/api/google/tasks")
def google_tasks(request: Request):
  session_id = get_google_session_id(request)
  if not session_id:
    raise HTTPException(status_code=401, detail="Google login is required.")
  try:
    items = fetch_google_tasks(session_id)
    return _wrap_read_with_revision(session_id, items)
  except Exception as e:
    logger.exception("Google Tasks fetch error")
    raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/google/tasks")
def google_create_task(request: Request, task_data: TaskCreate):
  session_id = get_google_session_id(request)
  if not session_id:
    raise HTTPException(status_code=401, detail="Google login is required.")
  try:
    service = get_google_tasks_service(session_id)
    task = {
      "title": task_data.title,
    }
    if task_data.notes:
      task["notes"] = task_data.notes
    if task_data.due:
      task["due"] = task_data.due
    result = service.tasks().insert(tasklist='@default', body=task).execute()
    mutation_meta: Dict[str, Any] = {"new_revision": get_google_revision_state(session_id).get("revision", 0)}
    if isinstance(result, dict):
      upsert_google_task_cache(session_id, result)
      mutation_meta = emit_google_task_delta(session_id, "upsert", task=result)
      return {
          **result,
          **mutation_meta,
      }
    return {
        "task": result,
        **mutation_meta,
    }
  except Exception as e:
    logger.exception("Google Tasks create error")
    raise HTTPException(status_code=500, detail=str(e))


@router.patch("/api/google/tasks/{task_id}")
def google_update_task(request: Request, task_id: str, task_data: TaskUpdate):
  session_id = get_google_session_id(request)
  if not session_id:
    raise HTTPException(status_code=401, detail="Google login is required.")
  try:
    service = get_google_tasks_service(session_id)
    # First load the existing task
    task = service.tasks().get(tasklist='@default', task=task_id).execute()
    
    # Apply only provided fields
    if task_data.title is not None:
      task["title"] = task_data.title
    if task_data.notes is not None:
      task["notes"] = task_data.notes
    if task_data.due is not None:
      task["due"] = task_data.due
    if task_data.status is not None:
      task["status"] = task_data.status
      if task_data.status == "completed":
        from datetime import datetime, timezone
        task["completed"] = datetime.now(timezone.utc).isoformat()
      elif "completed" in task:
        del task["completed"]
    
    result = service.tasks().update(tasklist='@default', task=task_id, body=task).execute()
    mutation_meta: Dict[str, Any] = {"new_revision": get_google_revision_state(session_id).get("revision", 0)}
    if isinstance(result, dict):
      upsert_google_task_cache(session_id, result)
      mutation_meta = emit_google_task_delta(session_id, "upsert", task=result)
      return {
          **result,
          **mutation_meta,
      }
    return {
        "task": result,
        **mutation_meta,
    }
  except Exception as e:
    logger.exception("Google Tasks update error")
    raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/google/tasks/{task_id}")
def google_delete_task(request: Request, task_id: str):
  session_id = get_google_session_id(request)
  if not session_id:
    raise HTTPException(status_code=401, detail="Google login is required.")
  try:
    service = get_google_tasks_service(session_id)
    service.tasks().delete(tasklist='@default', task=task_id).execute()
    remove_google_task_cache(session_id, task_id)
    mutation_meta = emit_google_task_delta(session_id, "delete", task_id=task_id)
    return {"ok": True, **mutation_meta}
  except Exception as e:
    logger.exception("Google Tasks delete error")
    raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/google/events/{event_id}")
def google_delete_event(request: Request,
                        event_id: str,
                        calendar_id: Optional[str] = Query(None,
                                                           alias="calendar_id")):
  if not event_id:
    raise HTTPException(status_code=400, detail="event_id is missing.")
  session_id = get_google_session_id(request)
  if not session_id:
    raise HTTPException(status_code=401, detail="Google login is required.")
  try:
    gcal_delete_event(event_id,
                      session_id=session_id,
                      calendar_id=calendar_id)
    mutation_meta = sync_google_event_after_delete(session_id,
                                                   event_id=event_id,
                                                   calendar_id=calendar_id)
    return {"ok": True, **mutation_meta}
  except HTTPException:
    raise
  except Exception as exc:
    print(f"[DELETE EVENT] 502 error for event_id={event_id}, calendar_id={calendar_id}: {exc}")
    raise HTTPException(status_code=502,
                        detail=f"Google event delete failed: {exc}") from exc


@router.patch("/api/google/events/{event_id}")
def google_update_event_api(request: Request,
                            event_id: str,
                            payload: EventUpdate,
                            calendar_id: Optional[str] = Query(
                                None, alias="calendar_id")):
  if not event_id:
    raise HTTPException(status_code=400, detail="event_id is missing.")
  if not is_gcal_configured():
    raise HTTPException(status_code=400,
                        detail="Google Calendar integration is not configured.")
  session_id = get_google_session_id(request)
  if not session_id:
    raise HTTPException(status_code=401, detail="Google login is required.")

  start_iso = _coerce_patch_start(payload.start)
  if payload.start is None:
    start_iso = None
    if payload.end is not None:
      raise HTTPException(status_code=400,
                          detail="To change end time, start time is required.")
    if payload.all_day is not None:
      raise HTTPException(status_code=400,
                          detail="For all-day changes, start time is required.")
  elif not start_iso:
    raise HTTPException(status_code=400, detail="Invalid start time format.")

  end_iso = None
  if payload.end is not None:
    end_iso = _coerce_patch_end(payload.end)

  title_value: Optional[str] = None
  if payload.title is not None:
    title_value = payload.title.strip()
    if not title_value:
      raise HTTPException(status_code=400, detail="Title cannot be empty.")

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
                      color_id=color_value,
                      calendar_id=calendar_id)
    mutation_meta = sync_google_event_after_write(session_id,
                                                  event_id=event_id,
                                                  calendar_id=calendar_id)
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google event update failed: {exc}") from exc

  return {"ok": True, **mutation_meta}


@router.post("/api/events")
def create_event(request: Request, event_in: EventCreate):
  session_id = require_google_session_id(request)
  google_event_id: Optional[str] = None
  detected_all_day = event_in.all_day
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
  except HTTPException:
    raise
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google event create failed: {exc}") from exc

  if not google_event_id:
    raise HTTPException(status_code=502, detail="Google event create failed")

  mutation_meta = sync_google_event_after_write(session_id, event_id=google_event_id)

  stored = store_event(
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
  stored_payload = stored.model_dump() if hasattr(stored, "model_dump") else dict(stored)
  return {
      **stored_payload,
      **mutation_meta,
  }


@router.post("/api/google/recurring-events")
def create_google_recurring_event(request: Request, payload: RecurringEventUpdate):
  session_id = require_google_session_id(request)

  recurrence: Optional[Dict[str, Any]] = None
  if payload.recurrence is not None:
    recurrence_raw = payload.recurrence.model_dump() if hasattr(
        payload.recurrence, "model_dump") else dict(payload.recurrence)
    recurrence = _normalize_recurrence_dict(recurrence_raw)
    if not recurrence:
      raise HTTPException(status_code=400, detail="Invalid recurrence payload.")
  rrule = _normalize_rrule_core(payload.rrule)
  if payload.rrule is not None and not rrule:
    raise HTTPException(status_code=400, detail="Invalid rrule payload.")
  if not recurrence and not rrule:
    raise HTTPException(status_code=400,
                        detail="Either recurrence or rrule must be provided.")

  item: Dict[str, Any] = {
      "type": "recurring",
      "title": payload.title,
      "start_date": payload.start_date,
      "time": payload.time,
      "duration_minutes": payload.duration_minutes,
      "location": payload.location,
      "description": payload.description,
      "attendees": payload.attendees,
      "reminders": payload.reminders,
      "visibility": payload.visibility,
      "transparency": payload.transparency,
      "meeting_url": payload.meeting_url,
      "timezone": payload.timezone,
      "color_id": payload.color_id,
      "recurrence": recurrence,
      "rrule": rrule,
  }

  try:
    google_event_id = gcal_create_recurring_event(item, session_id=session_id)
  except HTTPException:
    raise
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google recurring event create failed: {exc}") from exc

  if not google_event_id:
    raise HTTPException(status_code=502, detail="Google recurring event create failed.")

  mutation_meta = sync_google_event_after_write(session_id, event_id=google_event_id)
  return {"ok": True, "google_event_id": google_event_id, **mutation_meta}


@router.delete("/api/events/{event_id}")
def delete_event(event_id: int):
  raise HTTPException(status_code=410, detail="Local mode has been removed.")


@router.patch("/api/events/{event_id}", response_model=Event)
def update_event(request: Request, event_id: int, payload: EventUpdate):
  raise HTTPException(status_code=410, detail="Local mode has been removed.")


@router.patch("/api/recurring-events/{event_id}", response_model=Event)
def update_recurring_event(request: Request, event_id: int, payload: RecurringEventUpdate):
  raise HTTPException(status_code=410, detail="Local mode has been removed.")


@router.post("/api/recurring-events/{event_id}/exceptions")
def add_recurring_exception(request: Request,
                            event_id: int,
                            payload: RecurringExceptionPayload):
  raise HTTPException(status_code=410, detail="Local mode has been removed.")


@router.post("/api/agent/run")
async def agent_run(body: AgentRunRequest, request: Request, response: Response):
  _ = response
  session_id = ""
  run_id = ""
  try:
    session_id = require_google_session_id(request)
    run_id = _agent_debug_start(session_id, body.input_as_text or "")
    result = await run_full_agent(
        session_id=session_id,
        input_as_text=body.input_as_text or "",
        requested_timezone=body.timezone,
        dry_run=bool(body.dry_run),
        on_debug_update=lambda snapshot: _agent_debug_update(session_id, run_id, snapshot),
    )
    if isinstance(result, dict):
      result = _attach_agent_revision(result, session_id)
    trace = result.get("trace") if isinstance(result, dict) else None
    response_status = str(result.get("status") or "completed")
    _agent_debug_finish(session_id,
                        run_id,
                        status=response_status,
                        trace=trace if isinstance(trace, dict) else None)
    return result
  except HTTPException as exc:
    import traceback
    if exc.status_code >= 500:
      print(f"[AGENT] HTTPException {exc.status_code}: {exc.detail}", flush=True)
      traceback.print_exc()
    if session_id and run_id:
      _agent_debug_finish(session_id, run_id, status="failed", error=str(exc.detail))
    raise
  except Exception as e:
    import traceback
    print(f"[AGENT] Unhandled exception: {e}", flush=True)
    traceback.print_exc()
    if session_id and run_id:
      _agent_debug_finish(session_id, run_id, status="failed", error=str(e))
    raise HTTPException(status_code=502, detail=f"Agent run error: {str(e)}")


@router.post("/api/agent/run/stream")
async def agent_run_stream(body: AgentRunRequest, request: Request, response: Response):
  _ = response
  session_id = require_google_session_id(request)

  async def event_generator():
    run_id = _agent_debug_start(session_id, body.input_as_text or "")
    stream_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
    runner_done = asyncio.Event()
    loop = asyncio.get_event_loop()

    def _enqueue(payload: Dict[str, Any]) -> None:
      try:
        # 스레드 안전하게 큐에 넣기 위해 loop.call_soon_threadsafe 사용
        loop.call_soon_threadsafe(stream_queue.put_nowait, payload)
      except Exception:
        return

    async def _runner():
      try:
        result = await run_full_agent(
            session_id=session_id,
            input_as_text=body.input_as_text or "",
            requested_timezone=body.timezone,
            dry_run=bool(body.dry_run),
            on_debug_update=lambda snapshot: _agent_debug_update(session_id, run_id, snapshot),
            on_agent_stream_event=_enqueue,
        )
        if isinstance(result, dict):
          result = _attach_agent_revision(result, session_id)
        trace = result.get("trace") if isinstance(result, dict) else None
        response_status = str(result.get("status") or "completed")
        _agent_debug_finish(session_id,
                            run_id,
                            status=response_status,
                            trace=trace if isinstance(trace, dict) else None)
        _enqueue({
            "type": "agent_result",
            "result": result,
            "status": response_status,
        })
      except HTTPException as exc:
        import traceback
        if exc.status_code >= 500:
          print(f"[AGENT STREAM] HTTPException {exc.status_code}: {exc.detail}", flush=True)
          traceback.print_exc()
        _agent_debug_finish(session_id, run_id, status="failed", error=str(exc.detail))
        _enqueue({
            "type": "agent_error",
            "status_code": exc.status_code,
            "message": str(exc.detail),
        })
      except asyncio.CancelledError:
        _agent_debug_finish(session_id,
                            run_id,
                            status="cancelled",
                            error="client_disconnected")
        _enqueue({
            "type": "agent_error",
            "status_code": 499,
            "message": "Client disconnected.",
        })
        raise
      except Exception as exc:
        import traceback
        print(f"[AGENT STREAM] Unhandled exception: {exc}", flush=True)
        traceback.print_exc()
        _agent_debug_finish(session_id, run_id, status="failed", error=str(exc))
        _enqueue({
            "type": "agent_error",
            "status_code": 502,
            "message": f"Agent run error: {str(exc)}",
        })
      finally:
        runner_done.set()

    run_task = asyncio.create_task(_runner())
    try:
      yield _format_sse_event("ready", {"type": "ready"})
      while True:
        if await request.is_disconnected():
          if not run_task.done():
            run_task.cancel()
          break
        try:
          payload = await asyncio.wait_for(stream_queue.get(), timeout=20)
        except asyncio.TimeoutError:
          if runner_done.is_set():
            break
          yield _format_sse_event("ping", {"type": "ping"})
          continue
        if not isinstance(payload, dict):
          continue
        event_type = str(payload.get("type") or "message")
        yield _format_sse_event(event_type, payload)
        if event_type in ("agent_result", "agent_error") and runner_done.is_set(
        ) and stream_queue.empty():
          break
      yield _format_sse_event("done", {"type": "done"})
    finally:
      if not run_task.done():
        run_task.cancel()
      try:
        await run_task
      except Exception:
        pass

  return StreamingResponse(
      event_generator(),
      media_type="text/event-stream",
      headers={
          "Cache-Control": "no-cache",
          "Connection": "keep-alive",
          "X-Accel-Buffering": "no",
      },
  )


@router.get("/api/agent/debug")
async def agent_debug_status(request: Request):
  session_id = get_google_session_id(request) or _get_session_id(request)
  return _agent_debug_get(session_id)


@router.post("/api/nlp-events")
@router.post("/api/nlp-event")
@router.post("/api/nlp-classify")
@router.post("/api/nlp-preview")
@router.post("/api/nlp-preview-stream")
@router.post("/api/nlp-apply-add")
@router.post("/api/nlp-delete-preview")
@router.post("/api/nlp-context/reset")
@router.post("/api/nlp-interrupt")
@router.post("/api/nlp-delete-events")
async def legacy_nlp_removed(request: Request):
  _ = request
  raise HTTPException(status_code=410,
                      detail="Legacy NLP agent was removed. Use /api/agent/run.")


@router.post("/api/delete-by-ids", response_model=DeleteResult)
def delete_by_ids(body: IdsPayload):
  _ = body
  raise HTTPException(status_code=410, detail="Local mode has been removed.")


def build_header_actions(has_token: bool) -> str:
  parts: List[str] = []
  if not has_token:
    parts = ['<a class="header-btn" href="/auth/google/login">Google Login</a>']
  return "\n".join(parts)


@router.get("/", response_class=HTMLResponse)
def start_page(request: Request):
  if load_gcal_token_for_request(request) is not None:
    return RedirectResponse(_frontend_url("/calendar"))
  return RedirectResponse(_frontend_url("/login"))


@router.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request):
  if load_gcal_token_for_request(request) is None:
    return RedirectResponse(_frontend_url("/login"))

  token_present = load_gcal_token_for_request(request) is not None
  actions_html = build_header_actions(token_present)
  context = {
      "google_linked": token_present,
      "mode": "google",
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


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
  if load_gcal_token_for_request(request) is None:
    return RedirectResponse(_frontend_url("/login"))
  return HTMLResponse(SETTINGS_HTML)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
  if load_gcal_token_for_request(request) is not None:
    return RedirectResponse(_frontend_url("/calendar"))
  return HTMLResponse(LOGIN_HTML)
