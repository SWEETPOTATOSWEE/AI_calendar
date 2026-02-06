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
    _validate_image_payload,
)
from .state import (
    store_event,
)
from .recurrence import _resolve_recurrence, _normalize_recurrence_dict
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
# API 엔드포인트
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
  return fetch_google_events_between(scope[0], scope[1], session_id)


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
    return formatted[:200]
  except HTTPException:
    raise
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google recent events 실패: {exc}") from exc


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
    raise HTTPException(status_code=401, detail="Google 로그인이 필요합니다.")
  if query or calendar_id or all_day is not None or (isinstance(limit, int) and limit > 0):
    return fetch_google_events_between_with_options(scope[0],
                                                    scope[1],
                                                    session_id,
                                                    calendar_id=calendar_id,
                                                    query=query,
                                                    limit=limit,
                                                    all_day=all_day)
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
  if payload.start is None:
    start_iso = None
    if payload.end is not None:
      raise HTTPException(status_code=400,
                          detail="종료 시각을 변경하려면 시작 시각이 필요합니다.")
    if payload.all_day is not None:
      raise HTTPException(status_code=400,
                          detail="종일 일정 변경에는 시작 시각이 필요합니다.")
  elif not start_iso:
    raise HTTPException(status_code=400, detail="시작 시각 형식이 잘못되었습니다.")

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
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google event 생성 실패: {exc}") from exc

  if not google_event_id:
    raise HTTPException(status_code=502, detail="Google event 생성 실패")

  _clear_google_cache(session_id)
  _emit_google_sse(session_id, "google_sync", {})

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
  raise HTTPException(status_code=410, detail="로컬 모드가 제거되었습니다.")


@router.patch("/api/events/{event_id}", response_model=Event)
def update_event(request: Request, event_id: int, payload: EventUpdate):
  raise HTTPException(status_code=410, detail="로컬 모드가 제거되었습니다.")


@router.patch("/api/recurring-events/{event_id}", response_model=Event)
def update_recurring_event(request: Request, event_id: int, payload: RecurringEventUpdate):
  raise HTTPException(status_code=410, detail="로컬 모드가 제거되었습니다.")


@router.post("/api/recurring-events/{event_id}/exceptions")
def add_recurring_exception(request: Request,
                            event_id: int,
                            payload: RecurringExceptionPayload):
  raise HTTPException(status_code=410, detail="로컬 모드가 제거되었습니다.")


@router.post("/api/nlp-events", response_model=List[Event])
async def create_events_from_natural_text(body: NaturalText,
                                          request: Request,
                                          response: Response):
  try:
    session_id = require_google_session_id(request)
    request_id = _resolve_request_id(body.request_id)
    images = _validate_image_payload(body.images)
    effort = _resolve_request_reasoning_effort(request, body.reasoning_effort)
    model_name = _resolve_request_model(request, body.model)
    cache_key = _context_cache_key_for_session_mode(session_id, True)
    return await _run_with_interrupt(
        session_id,
        request_id,
        create_events_from_natural_text_core(body.text,
                                             images,
                                             effort,
                                             model_name=model_name,
                                             context_cache_key=cache_key,
                                             context_session_id=session_id,
                                             session_id=session_id,
                                             is_google=True),
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
    session_id = require_google_session_id(request)
    request_id = _resolve_request_id(body.request_id)
    images = _validate_image_payload(body.images)
    effort = _resolve_request_reasoning_effort(request, body.reasoning_effort)
    model_name = _resolve_request_model(request, body.model)
    cache_key = _context_cache_key_for_session_mode(session_id, True)
    created = await _run_with_interrupt(
        session_id,
        request_id,
        create_events_from_natural_text_core(body.text,
                                             images,
                                             effort,
                                             model_name=model_name,
                                             context_cache_key=cache_key,
                                             context_session_id=session_id,
                                             session_id=session_id,
                                             is_google=True),
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
    session_id = require_google_session_id(request)
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
    session_id = require_google_session_id(request)
    request_id = _resolve_request_id(body.request_id)
    images = _validate_image_payload(body.images)
    effort = _resolve_request_reasoning_effort(request, body.reasoning_effort)
    model_name = _resolve_request_model(request, body.model)
    cache_key = _context_cache_key_for_session_mode(session_id, True)
    data = await _run_with_interrupt(
        session_id,
        request_id,
        preview_events_from_natural_text_core(body.text,
                                              images,
                                              effort,
                                              model_name=model_name,
                                              context_cache_key=cache_key,
                                              context_session_id=session_id,
                                              context_confirmed=bool(body.context_confirmed),
                                              is_google=True),
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
    session_id = require_google_session_id(request)
    request_id = _resolve_request_id(body.request_id)
    images = _validate_image_payload(body.images)
    effort = _resolve_request_reasoning_effort(request, body.reasoning_effort)
    model_name = _resolve_request_model(request, body.model)
    cache_key = _context_cache_key_for_session_mode(session_id, True)

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
            context_session_id=session_id,
            context_confirmed=bool(body.context_confirmed),
            is_google=True
        ):
          if await request.is_disconnected():
            break

          chunk_count += 1
          event_type = chunk.get("type", "message")
          
          if chunk.get("type") == "data":
            processed = _post_process_nlp_preview_result(chunk["data"])
            processed["request_id"] = request_id
            event_data = _format_sse_event("data", processed)
            yield event_data
          else:
            event_data = _format_sse_event(event_type, chunk)
            yield event_data
            
      except Exception as e:
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
    raise HTTPException(status_code=502,
                        detail=f"Preview NLP stream error: {str(e)}")


@router.post("/api/nlp-apply-add", response_model=List[Event])
def nlp_apply_add(body: ApplyItems, request: Request):
  try:
    items = body.items or []
    if not items:
      raise HTTPException(status_code=400, detail="items is empty")
    session_id = require_google_session_id(request)
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
    session_id = require_google_session_id(request)
    request_id = _resolve_request_id(body.request_id)
    scope = _parse_scope_dates(body.start_date, body.end_date, require=True)
    effort = _resolve_request_reasoning_effort(request, body.reasoning_effort)
    model_name = _resolve_request_model(request, body.model)
    data = await _run_with_interrupt(
        session_id,
        request_id,
        delete_preview_groups(body.text,
                              scope=scope,
                              reasoning_effort=effort,
                              model_name=model_name,
                              session_id=session_id,
                              context_confirmed=bool(body.context_confirmed),
                              is_google=True),
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
  session_id = require_google_session_id(request)
  base_key = _context_cache_key_for_session(session_id)
  if base_key:
    _clear_context_cache(f"{base_key}:google")
  return {"ok": True}


@router.post("/api/nlp-interrupt")
async def nlp_interrupt(body: InterruptRequest,
                        request: Request,
                        response: Response):
  session_id = require_google_session_id(request)
  cancelled = await _cancel_inflight(session_id, body.request_id)
  return {"ok": True, "cancelled": cancelled}


@router.post("/api/delete-by-ids", response_model=DeleteResult)
def delete_by_ids(body: IdsPayload):
  raise HTTPException(status_code=410, detail="로컬 모드가 제거되었습니다.")


@router.post("/api/nlp-delete-events", response_model=DeleteResult)
async def delete_events_from_natural_text(body: NaturalTextWithScope,
                                          request: Request,
                                          response: Response):
  try:
    session_id = require_google_session_id(request)
    request_id = _resolve_request_id(body.request_id)
    scope = _parse_scope_dates(body.start_date, body.end_date, require=True)
    effort = _resolve_request_reasoning_effort(request, body.reasoning_effort)
    model_name = _resolve_request_model(request, body.model)
    ids_or_perm = await _run_with_interrupt(
        session_id,
        request_id,
        create_delete_ids_from_natural_text(body.text,
                                            scope=scope,
                                            reasoning_effort=effort,
                                            model_name=model_name,
                                            session_id=session_id,
                                            context_confirmed=bool(body.context_confirmed),
                                            is_google=True),
    )
    if isinstance(ids_or_perm, dict) and ids_or_perm.get("permission_required"):
      return ids_or_perm
    ids = ids_or_perm if isinstance(ids_or_perm, list) else []

    deleted: List[Union[int, str]] = []
    for raw_id in ids:
      event_id = str(raw_id)
      if not event_id:
        continue
      try:
        gcal_delete_event(event_id, session_id=session_id)
        deleted.append(event_id)
      except HTTPException:
        raise
      except Exception as exc:
        raise HTTPException(status_code=502,
                            detail=f"Google event 삭제 실패: {exc}") from exc
    _clear_google_cache(session_id)
    _emit_google_sse(session_id, "google_sync", {})
    return DeleteResult(ok=True, deleted_ids=deleted, count=len(deleted))
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502, detail=f"Delete NLP error: {str(e)}")


def build_header_actions(has_token: bool) -> str:
  parts: List[str] = []
  if not has_token:
    parts.append(
        '<a class="header-btn" href="/auth/google/login">Google 로그인</a>')
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
