from __future__ import annotations

import asyncio
import json
import logging
import urllib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union

import requests
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse, StreamingResponse

from .config import (
    ADMIN_COOKIE_NAME,
    ADMIN_COOKIE_VALUE,
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
    ISO_DATE_RE,
    SEOUL,
    UNDO_RETENTION_DAYS,
)
from .models import (
    Event,
    EventCreate,
    EventUpdate,
    RecurringEventUpdate,
    RecurringExceptionPayload,
    NaturalText,
    NlpClassifyRequest,
    NaturalTextWithScope,
    ApplyItems,
    IdsPayload,
    DeleteResult,
    InterruptRequest,
    TaskCreate,
    TaskUpdate,
)
from .frontend import (
    START_HTML,
    CALENDAR_HTML_TEMPLATE,
    SETTINGS_HTML,
    LOGIN_HTML,
)
from .utils import (
    _log_debug,
    _parse_scope_dates,
    _normalize_google_timestamp,
    _parse_created_at,
    _now_iso_minute,
    _coerce_patch_start,
    _coerce_patch_end,
    _clean_optional_str,
    _normalize_end_datetime,
    is_all_day_span,
    _normalize_exception_date,
    _normalize_color_id,
    _validate_image_payload,
)
from . import state
from .state import (
    store_event,
    store_recurring_event,
    _find_recurring_event,
    _delete_recurring_event,
    _recurring_definition_to_event,
    _collect_local_recurring_occurrences,
    _decode_occurrence_id,
    _list_local_events_for_api,
    delete_events_by_ids,
    _save_events_to_disk,
)
from .recurrence import _resolve_recurrence, _normalize_recurrence_dict
from .gcal import (
    is_admin,
    is_google_mode_active,
    is_gcal_configured,
    load_gcal_token_for_request,
    load_gcal_token_for_session,
    save_gcal_token_for_session,
    clear_gcal_token_for_session,
    get_google_session_id,
    get_google_tasks_service,
    get_google_userinfo,
    list_google_calendars,
    ensure_gcal_watches,
    fetch_google_events_between,
    fetch_recent_google_events,
    refresh_google_cache_for_calendar,
    gcal_create_single_event,
    gcal_update_event,
    gcal_delete_event,
    gcal_create_recurring_event,
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
    _split_gcal_event_key,
    _request_base_url,
    _resolve_google_redirect_uri,
    _get_session_id,
    _new_session_id,
    _ensure_session_id,
    _new_oauth_state,
    _store_oauth_state,
    _pop_oauth_state,
    _set_cookie,
    _delete_cookie,
    _clear_google_cache,
    _context_cache_key_for_session,
    _context_cache_key_for_session_mode,
    _clear_context_cache,
    _frontend_url,
)
from .nlp import (
    create_events_from_natural_text_core,
    preview_events_from_natural_text_core,
    _post_process_nlp_preview_result,
    apply_add_items_core,
    create_delete_ids_from_natural_text,
    delete_preview_groups,
)
from .llm import (
    _resolve_request_id,
    _resolve_request_reasoning_effort,
    _resolve_request_model,
    _run_with_interrupt,
    _cancel_inflight,
    _invoke_event_parser_stream,
    classify_nlp_request,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# -------------------------
# Google OAuth 엔드포인트
# -------------------------
@router.get("/auth/google/login")
@router.get("/auth/google/login/")
def google_login(request: Request):
  _log_debug("[GCAL] login start")
  redirect_uri = _resolve_google_redirect_uri(request)
  print(f"[DEBUG] Google OAuth redirect_uri: {redirect_uri}")
  if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and redirect_uri):
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
    raise HTTPException(status_code=400, detail="code가 없습니다.")
  oauth_entry = _pop_oauth_state(state)
  if not state or (expected_state and state != expected_state and not oauth_entry):
    raise HTTPException(status_code=400, detail="state 검증에 실패했습니다.")

  redirect_uri = (
      oauth_entry.get("redirect_uri") if isinstance(oauth_entry, dict) else None)
  redirect_uri = redirect_uri or GOOGLE_REDIRECT_URI
  if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and redirect_uri):
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
      "redirect_uri": redirect_uri,
      "grant_type": "authorization_code",
  }

  resp = requests.post(token_endpoint, data=data)
  if not resp.ok:
    _log_debug(f"[GCAL] token exchange failed: {resp.status_code} {resp.text}")
    raise HTTPException(status_code=500,
                        detail=f"토큰 교환 실패: {resp.status_code} {resp.text}")

  stored_session_id = (
      oauth_entry.get("session_id")
      if isinstance(oauth_entry, dict) else None)
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

  # ✅ 성공 시 달력으로 이동
  resp = RedirectResponse(_frontend_url("/calendar"))
  resp.delete_cookie(ADMIN_COOKIE_NAME, path="/")
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
      "admin": is_admin(request),
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
  _emit_google_sse(session_id, "google_sync", {"calendar_id": calendar_id})
  return JSONResponse({"ok": True})


# 이전 webhook URL 호환성을 위한 임시 엔드포인트 (자동 만료될 때까지)
@router.post("/webhook/google")
def google_webhook_legacy(request: Request):
  """이전 URL로 등록된 watch를 위한 임시 엔드포인트. 24시간 후 자동 만료됨."""
  # 단순히 200 OK를 반환하여 Google 서버 에러 방지
  return JSONResponse({"ok": True, "deprecated": True})


@router.get("/api/google/stream")
async def google_stream(request: Request):
  session_id = get_google_session_id(request)
  if not session_id:
    raise HTTPException(status_code=401, detail="Google 로그인이 필요합니다.")

  key, queue = _register_google_sse(session_id)

  async def event_generator():
    try:
      yield _format_sse_event("ready", {})
      while True:
        if await request.is_disconnected():
          break
        try:
          payload = await asyncio.wait_for(queue.get(), timeout=20)
          event_type = payload.get("type") if isinstance(payload, dict) else "message"
          yield _format_sse_event(event_type or "message", payload or {})
        except asyncio.TimeoutError:
          yield _format_sse_event("ping", {})
    finally:
      _unregister_google_sse(key, queue)

  return StreamingResponse(event_generator(), media_type="text/event-stream")


# -------------------------
# Admin / Logout
# -------------------------
@router.get("/admin")
def enter_admin():
  resp = RedirectResponse(_frontend_url("/calendar"))
  resp.set_cookie(ADMIN_COOKIE_NAME,
                  ADMIN_COOKIE_VALUE,
                  httponly=True,
                  samesite="lax")
  return resp


@router.get("/admin/exit")
def exit_admin():
  resp = RedirectResponse(_frontend_url("/"))
  resp.delete_cookie(ADMIN_COOKIE_NAME)
  return resp


@router.get("/logout")
def logout(request: Request):
  session_id = _get_session_id(request)
  if session_id:
    _clear_watches_for_session(session_id)
  clear_gcal_token_for_session(session_id)
  _clear_google_cache(session_id)
  resp = RedirectResponse(_frontend_url("/"))
  resp.delete_cookie(ADMIN_COOKIE_NAME, path="/")
  _delete_cookie(resp, SESSION_COOKIE_NAME)
  _delete_cookie(resp, OAUTH_STATE_COOKIE_NAME)
  return resp


# -------------------------
# API 엔드포인트
# -------------------------
@router.get("/api/events", response_model=List[Event])
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
      for e in state.events
      if _parse_created_at(e.created_at) >= cutoff
  ]
  for rec in state.recurring_events:
    if _parse_created_at(rec.get("created_at")) >= cutoff:
      recent.append(_format_recent_recurring_event(rec))
  recent.sort(key=lambda ev: _parse_created_at(ev.get("created_at")), reverse=True)
  return recent[:200]


@router.get("/api/google/events")
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


@router.get("/api/google/tasks")
def google_tasks(request: Request):
  session_id = get_google_session_id(request)
  if not session_id:
    raise HTTPException(status_code=401, detail="Google 로그인이 필요합니다.")
  try:
    service = get_google_tasks_service(session_id)
    results = service.tasks().list(tasklist='@default', showCompleted=True, showHidden=True).execute()
    return results.get('items', [])
  except Exception as e:
    logger.exception("Google Tasks fetch error")
    raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/google/tasks")
def google_create_task(request: Request, task_data: TaskCreate):
  session_id = get_google_session_id(request)
  if not session_id:
    raise HTTPException(status_code=401, detail="Google 로그인이 필요합니다.")
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
    return result
  except Exception as e:
    logger.exception("Google Tasks create error")
    raise HTTPException(status_code=500, detail=str(e))


@router.patch("/api/google/tasks/{task_id}")
def google_update_task(request: Request, task_id: str, task_data: TaskUpdate):
  session_id = get_google_session_id(request)
  if not session_id:
    raise HTTPException(status_code=401, detail="Google 로그인이 필요합니다.")
  try:
    service = get_google_tasks_service(session_id)
    # 먼저 기존 task를 가져옵니다
    task = service.tasks().get(tasklist='@default', task=task_id).execute()
    
    # 업데이트할 필드만 변경
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
    return result
  except Exception as e:
    logger.exception("Google Tasks update error")
    raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/google/tasks/{task_id}")
def google_delete_task(request: Request, task_id: str):
  session_id = get_google_session_id(request)
  if not session_id:
    raise HTTPException(status_code=401, detail="Google 로그인이 필요합니다.")
  try:
    service = get_google_tasks_service(session_id)
    service.tasks().delete(tasklist='@default', task=task_id).execute()
    return {"ok": True}
  except Exception as e:
    logger.exception("Google Tasks delete error")
    raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/google/events/{event_id}")
def google_delete_event(request: Request,
                        event_id: str,
                        calendar_id: Optional[str] = Query(None,
                                                           alias="calendar_id")):
  if not event_id:
    raise HTTPException(status_code=400, detail="event_id가 없습니다.")
  session_id = get_google_session_id(request)
  if not session_id:
    raise HTTPException(status_code=401, detail="Google 로그인이 필요합니다.")
  try:
    gcal_delete_event(event_id,
                      session_id=session_id,
                      calendar_id=calendar_id)
    _clear_google_cache(session_id)
    _, parsed_calendar = _split_gcal_event_key(event_id)
    _emit_google_sse(session_id, "google_sync",
                     {"calendar_id": calendar_id or parsed_calendar})
    return {"ok": True}
  except HTTPException:
    raise
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google event 삭제 실패: {exc}") from exc


@router.patch("/api/google/events/{event_id}")
def google_update_event_api(request: Request,
                            event_id: str,
                            payload: EventUpdate,
                            calendar_id: Optional[str] = Query(
                                None, alias="calendar_id")):
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
                      color_id=color_value,
                      calendar_id=calendar_id)
    _clear_google_cache(session_id)
    _, parsed_calendar = _split_gcal_event_key(event_id)
    _emit_google_sse(session_id, "google_sync",
                     {"calendar_id": calendar_id or parsed_calendar})
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google event 업데이트 실패: {exc}") from exc

  return {"ok": True}


@router.post("/api/events", response_model=Event)
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
      _emit_google_sse(session_id, "google_sync", {})
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


@router.delete("/api/events/{event_id}")
def delete_event(event_id: int):
  deleted = delete_events_by_ids([event_id])
  if not deleted:
    raise HTTPException(status_code=404, detail="일정을 찾을 수 없습니다.")
  return {"ok": True, "deleted": deleted}


@router.patch("/api/events/{event_id}", response_model=Event)
def update_event(request: Request, event_id: int, payload: EventUpdate):
  recurrence_id = _decode_occurrence_id(event_id)
  if recurrence_id:
    raise HTTPException(status_code=400, detail="반복 일정은 개별 수정할 수 없습니다.")
  if _find_recurring_event(event_id):
    raise HTTPException(status_code=400, detail="반복 일정은 개별 수정할 수 없습니다.")

  target = next((e for e in state.events if e.id == event_id), None)
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


@router.patch("/api/recurring-events/{event_id}", response_model=Event)
def update_recurring_event(request: Request, event_id: int, payload: RecurringEventUpdate):
  if is_google_mode_active(request):
    raise HTTPException(status_code=400, detail="Google 모드에서는 반복 일정을 수정할 수 없습니다.")

  recurrence_id = _decode_occurrence_id(event_id) or event_id
  target = _find_recurring_event(recurrence_id)
  if target is None:
    raise HTTPException(status_code=404, detail="반복 일정을 찾을 수 없습니다.")

  title_value = payload.title.strip()
  if not title_value:
    raise HTTPException(status_code=400, detail="제목을 입력해 주세요.")

  start_date_value = payload.start_date.strip()
  if not ISO_DATE_RE.match(start_date_value):
    raise HTTPException(status_code=400, detail="시작 날짜 형식이 잘못되었습니다.")

  recurrence_item = {"recurrence": payload.recurrence.model_dump()}
  recurrence_spec = _resolve_recurrence(recurrence_item)
  if not recurrence_spec:
    raise HTTPException(status_code=400, detail="반복 규칙을 확인해 주세요.")

  location_value = _clean_optional_str(payload.location)
  description_value = _clean_optional_str(payload.description)
  meeting_url_value = _clean_optional_str(payload.meeting_url)
  timezone_value = payload.timezone or "Asia/Seoul"
  color_value = _normalize_color_id(payload.color_id)

  attendees_value: Optional[List[str]] = None
  if payload.attendees is not None:
    cleaned: List[str] = []
    for item in payload.attendees:
      if isinstance(item, str):
        trimmed = item.strip()
        if trimmed:
          cleaned.append(trimmed)
    attendees_value = cleaned

  reminders_value: Optional[List[int]] = None
  if payload.reminders is not None:
    cleaned_reminders: List[int] = []
    for item in payload.reminders:
      try:
        minutes = int(item)
      except Exception:
        continue
      if minutes >= 0:
        cleaned_reminders.append(minutes)
    reminders_value = cleaned_reminders

  target["title"] = title_value
  target["start_date"] = start_date_value
  target["time"] = payload.time
  target["duration_minutes"] = payload.duration_minutes
  target["location"] = location_value
  target["description"] = description_value
  target["attendees"] = attendees_value
  target["reminders"] = reminders_value
  target["visibility"] = payload.visibility
  target["transparency"] = payload.transparency
  target["meeting_url"] = meeting_url_value
  target["timezone"] = timezone_value
  target["color_id"] = color_value
  target["recurrence"] = recurrence_spec

  _save_events_to_disk()
  return _recurring_definition_to_event(target)


@router.post("/api/recurring-events/{event_id}/exceptions")
def add_recurring_exception(request: Request,
                            event_id: int,
                            payload: RecurringExceptionPayload):
  if is_google_mode_active(request):
    raise HTTPException(status_code=400, detail="Google 모드에서는 반복 일정을 수정할 수 없습니다.")

  recurrence_id = _decode_occurrence_id(event_id) or event_id
  target = _find_recurring_event(recurrence_id)
  if target is None:
    raise HTTPException(status_code=404, detail="반복 일정을 찾을 수 없습니다.")

  exception_date = _normalize_exception_date(payload.date)
  if not exception_date:
    raise HTTPException(status_code=400, detail="제외 날짜 형식이 잘못되었습니다.")

  exceptions = target.get("exceptions")
  if not isinstance(exceptions, list):
    exceptions = []
  if exception_date not in exceptions:
    exceptions.append(exception_date)
  target["exceptions"] = exceptions
  _save_events_to_disk()
  return {"ok": True}


@router.post("/api/nlp-events", response_model=List[Event])
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
                                             session_id=gcal_session_id,
                                             is_google=use_google_context),
    )
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502,
                        detail=f"Natural language error: {str(e)}")


@router.post("/api/nlp-event", response_model=Event)
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
                                             session_id=gcal_session_id,
                                             is_google=use_google_context),
    )
    return created[0]
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502,
                        detail=f"Natural language error: {str(e)}")


@router.post("/api/nlp-classify")
async def nlp_classify(body: NlpClassifyRequest,
                       request: Request,
                       response: Response):
  try:
    session_id = _ensure_session_id(request, response)
    request_id = _resolve_request_id(body.request_id)
    result = await _run_with_interrupt(
        session_id,
        request_id,
        classify_nlp_request(body.text, bool(body.has_images)))
    return {"type": result}
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502,
                        detail=f"Classify NLP error: {str(e)}")


@router.post("/api/nlp-preview")
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
                                              context_session_id=gcal_session_id if use_google_context else None,
                                              context_confirmed=bool(body.context_confirmed),
                                              is_google=use_google_context),
    )
    if isinstance(data, dict):
      data["request_id"] = request_id
    return data
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502, detail=f"Preview NLP error: {str(e)}")


@router.post("/api/nlp-preview-stream")
async def nlp_preview_stream(body: NaturalText, request: Request, response: Response):
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

    print(f"[SSE STREAM] Starting stream for request_id={request_id}, google_mode={use_google_context}, session_id={session_id}")

    async def event_generator():
      chunk_count = 0
      try:
        async for chunk in _invoke_event_parser_stream(
            "parse",
            body.text,
            images,
            reasoning_effort=effort,
            model_name=model_name,
            context_cache_key=cache_key,
            context_session_id=gcal_session_id if use_google_context else None,
            context_confirmed=bool(body.context_confirmed),
            is_google=use_google_context
        ):
          if await request.is_disconnected():
            print(f"[SSE STREAM] Client disconnected at chunk {chunk_count}")
            break

          chunk_count += 1
          event_type = chunk.get("type", "message")
          
          if chunk.get("type") == "data":
            processed = _post_process_nlp_preview_result(chunk["data"])
            processed["request_id"] = request_id
            event_data = _format_sse_event("data", processed)
            print(f"[SSE STREAM] Chunk {chunk_count}: Sending final data event, size={len(event_data)} bytes")
            yield event_data
          else:
            event_data = _format_sse_event(event_type, chunk)
            print(f"[SSE STREAM] Chunk {chunk_count}: Sending {event_type} event, size={len(event_data)} bytes")
            yield event_data
            
        print(f"[SSE STREAM] Stream completed successfully, total chunks: {chunk_count}")
      except Exception as e:
        print(f"[SSE STREAM] Error in stream: {e}")
        yield _format_sse_event("error", {"detail": str(e)})

    return StreamingResponse(
      event_generator(),
      media_type="text/event-stream",
      headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
      }
    )
  except Exception as e:
    print(f"[SSE STREAM] Failed to start stream: {e}")
    raise HTTPException(status_code=502,
                        detail=f"Preview NLP stream error: {str(e)}")


@router.post("/api/nlp-apply-add", response_model=List[Event])
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


@router.post("/api/nlp-delete-preview")
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
                              session_id=gcal_session_id if use_google_context else None,
                              context_confirmed=bool(body.context_confirmed),
                              is_google=use_google_context),
    )
    if isinstance(data, dict):
      data["request_id"] = request_id
    return data
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502,
                        detail=f"Delete Preview error: {str(e)}")


@router.post("/api/nlp-context/reset")
def nlp_context_reset(request: Request, response: Response):
  session_id = _ensure_session_id(request, response)
  base_key = _context_cache_key_for_session(session_id)
  if base_key:
    _clear_context_cache(f"{base_key}:local")
    _clear_context_cache(f"{base_key}:google")
  return {"ok": True}


@router.post("/api/nlp-interrupt")
async def nlp_interrupt(body: InterruptRequest,
                        request: Request,
                        response: Response):
  session_id = _ensure_session_id(request, response)
  cancelled = await _cancel_inflight(session_id, body.request_id)
  return {"ok": True, "cancelled": cancelled}


@router.post("/api/delete-by-ids", response_model=DeleteResult)
def delete_by_ids(body: IdsPayload):
  deleted = delete_events_by_ids(body.ids or [])
  return DeleteResult(ok=True, deleted_ids=deleted, count=len(deleted))


@router.post("/api/nlp-delete-events", response_model=DeleteResult)
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
    ids_or_perm = await _run_with_interrupt(
        session_id,
        request_id,
        create_delete_ids_from_natural_text(body.text,
                                            scope=scope,
                                            reasoning_effort=effort,
                                            model_name=model_name,
                                            session_id=gcal_session_id if use_google_context else None,
                                            context_confirmed=bool(body.context_confirmed),
                                            is_google=use_google_context),
    )
    if isinstance(ids_or_perm, dict) and ids_or_perm.get("permission_required"):
      return ids_or_perm
    ids = ids_or_perm if isinstance(ids_or_perm, list) else []

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
      _emit_google_sse(gcal_session_id, "google_sync", {})
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


@router.get("/", response_class=HTMLResponse)
def start_page(request: Request):
  # Redirect to calendar only when a login token is present.
  if load_gcal_token_for_request(request) is not None:
    return RedirectResponse(_frontend_url("/calendar"))

  # 그 외: 시작 페이지
  return START_HTML


@router.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request):
  # 접근 조건: admin or (gcal 비활성) or (token 있음)
  if not is_admin(request) and ENABLE_GCAL and load_gcal_token_for_request(
      request) is None:
    return RedirectResponse(_frontend_url("/"))

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


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
  if not is_admin(request) and ENABLE_GCAL and load_gcal_token_for_request(
      request) is None:
    return RedirectResponse(_frontend_url("/"))
  return HTMLResponse(SETTINGS_HTML)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
  if load_gcal_token_for_request(request) is not None:
    return RedirectResponse(_frontend_url("/calendar"))
  return HTMLResponse(LOGIN_HTML)
