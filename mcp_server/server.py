from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:8000").rstrip("/")
BACKEND_API_BASE = os.getenv("BACKEND_API_BASE", "/api").rstrip("/")
DEFAULT_SESSION_ID = os.getenv("GCAL_SESSION_ID", "").strip()
REQUEST_TIMEOUT = float(os.getenv("MCP_BACKEND_TIMEOUT", "15"))
DEBUG_MODE = os.getenv("MCP_DEBUG", "1").strip() in ("1", "true", "True", "yes")
LOG_REQUESTS = os.getenv("MCP_LOG_REQUESTS", "0").strip() in ("1", "true", "True", "yes")

mcp = FastMCP("ai-calendar-agent")


def _log_tool_call(tool_name: str, input_data: Dict[str, Any], output_data: Dict[str, Any]) -> None:
  """도구 호출 입출력을 터미널에 출력"""
  if not DEBUG_MODE:
    return
  print(f"\n{'='*80}")
  print(f"🔧 Tool: {tool_name}")
  print(f"{'='*80}")
  print(f"📥 입력:")
  print(json.dumps(input_data, indent=2, ensure_ascii=False))
  print(f"\n📤 출력:")
  print(json.dumps(output_data, indent=2, ensure_ascii=False))
  print(f"{'='*80}\n")


def _filter_event_fields(event: Dict[str, Any]) -> Dict[str, Any]:
  """이벤트에서 불필요한 필드 제거"""
  exclude_fields = {
      "attendees", "visibility", "transparency", "meeting_url", 
      "color_id", "status", "html_link", "organizer", "created", "updated",
      "google_event_id"
  }
  return {k: v for k, v in event.items() if k not in exclude_fields}


class FindEventRange(BaseModel):
  start_date: str
  end_date: str


class RequestLoggerMiddleware:
  def __init__(self, app: Any):
    self.app = app

  async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
    if scope.get("type") != "http":
      await self.app(scope, receive, send)
      return

    method = scope.get("method", "")
    path = scope.get("path", "")
    headers = self._decode_headers(scope.get("headers") or [])

    self._log_request(method, path, headers)
    await self.app(scope, receive, send)

  def _decode_headers(self, raw_headers: List[Tuple[bytes, bytes]]) -> Dict[str, str]:
    decoded: Dict[str, str] = {}
    for key, value in raw_headers:
      try:
        header_key = key.decode("latin-1")
      except Exception:
        header_key = str(key)
      try:
        header_value = value.decode("latin-1")
      except Exception:
        header_value = str(value)
      decoded[header_key.lower()] = header_value
    return decoded

  def _log_request(
      self,
      method: str,
      path: str,
      headers: Dict[str, str],
  ) -> None:
    if not LOG_REQUESTS:
      return
    safe_headers = dict(headers)
    if "authorization" in safe_headers:
      safe_headers["authorization"] = "(redacted)"
    if "cookie" in safe_headers:
      safe_headers["cookie"] = "(redacted)"

    print("\n" + "=" * 80)
    print("📡 MCP HTTP Request")
    print("=" * 80)
    print(f"method: {method}")
    print(f"path: {path}")
    print("headers:")
    print(json.dumps(safe_headers, indent=2, ensure_ascii=False))
    print("=" * 80 + "\n")


def _range_entry_to_dict(entry: Any) -> Dict[str, Any]:
  if isinstance(entry, FindEventRange):
    if hasattr(entry, "model_dump"):
      return entry.model_dump()
    return entry.dict()
  if isinstance(entry, dict):
    return entry
  return {"value": str(entry)}


def _api_path(path: str) -> str:
  return f"{BACKEND_API_BASE}/{path.lstrip('/')}"


def _require_session_id(session_id: Optional[str]) -> str:
  sid = (session_id or DEFAULT_SESSION_ID).strip()
  if not sid:
    raise ValueError(
        "gcal_session is required. Pass session_id or set GCAL_SESSION_ID."
    )
  return sid


def _request(method: str,
             path: str,
             session_id: Optional[str],
             params: Optional[Dict[str, Any]] = None,
             payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
  sid = _require_session_id(session_id)
  url = f"{BACKEND_BASE_URL}{path}"
  headers = {"Cookie": f"gcal_session={sid}"}
  try:
    resp = requests.request(method,
                            url,
                            params=params,
                            json=payload,
                            headers=headers,
                            timeout=REQUEST_TIMEOUT)
  except Exception as exc:
    return {
        "ok": False,
        "code": "request_failed",
        "message": f"Backend request failed: {exc}",
    }

  try:
    data = resp.json()
  except Exception:
    data = {"raw": resp.text}

  if resp.status_code >= 400:
    return {
        "ok": False,
        "code": "backend_error",
        "status": resp.status_code,
        "error": data,
    }

  return {"ok": True, "data": data}


def _not_implemented(tool_name: str) -> Dict[str, Any]:
  return {
      "ok": False,
      "code": "not_implemented",
      "message": f"{tool_name} is not implemented yet.",
  }


def _filter_by_query(items: List[Dict[str, Any]], query: Optional[str]) -> List[Dict[str, Any]]:
  if not query:
    return items
  lowered = query.lower()
  filtered: List[Dict[str, Any]] = []
  for item in items:
    title = str(item.get("title") or item.get("summary") or "")
    location = str(item.get("location") or "")
    description = str(item.get("description") or "")
    haystack = f"{title} {location} {description}".lower()
    if lowered in haystack:
      filtered.append(item)
  return filtered


def _normalize_or_terms(query: Any) -> List[str]:
  if query is None:
    return []
  terms: List[str] = []
  if isinstance(query, list):
    terms = [str(term) for term in query]
  cleaned = [term.strip() for term in terms if isinstance(term, str) and term.strip()]
  return cleaned


def _filter_by_or_terms(items: List[Dict[str, Any]], terms: List[str]) -> List[Dict[str, Any]]:
  if not terms:
    return items
  lowered_terms = [term.lower() for term in terms]
  filtered: List[Dict[str, Any]] = []
  for item in items:
    title = str(item.get("title") or item.get("summary") or "")
    location = str(item.get("location") or "")
    description = str(item.get("description") or "")
    haystack = f"{title} {location} {description}".lower()
    for term in lowered_terms:
      if term in haystack:
        filtered.append(item)
        break
  return filtered


@mcp.tool(name="calendar.create_event")
def calendar_create_event(
    output_parsed: Dict[str, Any],
) -> Dict[str, Any]:
  items = output_parsed.get("items")
  session_id = output_parsed.get("session_id")

  if not isinstance(items, list) or not items:
    result = {
        "ok": False,
        "code": "invalid_request",
        "message": "items is required and must be a non-empty array.",
    }
    _log_tool_call("calendar.create_event", output_parsed, result)
    return result

  result = _request("POST",
                    _api_path("/nlp-apply-add"),
                    session_id,
                    payload={"items": items})
  if not result.get("ok"):
    _log_tool_call("calendar.create_event", output_parsed, result)
    return result
  data = result.get("data")
  if isinstance(data, list):
    for item in data:
      if isinstance(item, dict):
        google_event_id = item.get("google_event_id")
        if google_event_id:
          item.setdefault("event_id", google_event_id)
  _log_tool_call("calendar.create_event", output_parsed, result)
  return result


@mcp.tool(name="calendar.update_event")
def calendar_update_event(
    output_parsed: Dict[str, Any],
) -> Dict[str, Any]:
  items = output_parsed.get("items")
  session_id = output_parsed.get("session_id")

  if not isinstance(items, list) or not items:
    result = {
        "ok": False,
        "code": "invalid_request",
        "message": "items is required and must be a non-empty array.",
    }
    _log_tool_call("calendar.update_event", output_parsed, result)
    return result

  results: List[Dict[str, Any]] = []
  for item in items:
    if not isinstance(item, dict):
      continue
    item_event_id = item.get("event_id")
    item_start = item.get("start")
    if not item_event_id:
      results.append({
          "ok": False,
          "code": "invalid_request",
          "message": "event_id is required for each item.",
          "item": item,
      })
      continue
    if item_start is None and (item.get("end") is not None or item.get("all_day") is not None):
      results.append({
          "ok": False,
          "code": "invalid_request",
          "message": "start is required when end or all_day is provided.",
          "item": item,
      })
      continue
    item_payload = {
        "title": item.get("title"),
        "start": item_start,
        "end": item.get("end"),
        "location": item.get("location"),
        "description": item.get("description"),
        "attendees": item.get("attendees"),
        "reminders": item.get("reminders"),
        "visibility": item.get("visibility"),
        "transparency": item.get("transparency"),
        "meeting_url": item.get("meeting_url"),
        "timezone": item.get("timezone"),
        "color_id": item.get("color_id"),
        "all_day": item.get("all_day"),
    }
    item_payload = {k: v for k, v in item_payload.items() if v is not None}
    item_calendar_id = item.get("calendar_id")
    params = {"calendar_id": item_calendar_id} if item_calendar_id else None
    safe_event_id = quote(str(item_event_id), safe="")
    results.append(
        _request("PATCH",
                 _api_path(f"/google/events/{safe_event_id}"),
                 session_id,
                 params=params,
                 payload=item_payload))
  result = {"ok": True, "data": results}
  _log_tool_call("calendar.update_event", output_parsed, result)
  return result


@mcp.tool(name="calendar.move_event")
def calendar_move_event(
    output_parsed: Dict[str, Any],
) -> Dict[str, Any]:
  items = output_parsed.get("items")
  session_id = output_parsed.get("session_id")

  if not isinstance(items, list) or not items:
    result = {
        "ok": False,
        "code": "invalid_request",
        "message": "items is required and must be a non-empty array.",
    }
    _log_tool_call("calendar.move_event", output_parsed, result)
    return result

  results: List[Dict[str, Any]] = []
  for item in items:
    if not isinstance(item, dict):
      continue
    item_event_id = item.get("event_id")
    item_start = item.get("start")
    if not item_event_id or not item_start:
      results.append({
          "ok": False,
          "code": "invalid_request",
          "message": "event_id and start are required for each item.",
          "item": item,
      })
      continue
    item_payload = {
        "start": item_start,
        "end": item.get("end"),
        "all_day": item.get("all_day"),
    }
    item_payload = {k: v for k, v in item_payload.items() if v is not None}
    item_calendar_id = item.get("calendar_id")
    params = {"calendar_id": item_calendar_id} if item_calendar_id else None
    safe_event_id = quote(str(item_event_id), safe="")
    results.append(
        _request("PATCH",
                 _api_path(f"/google/events/{safe_event_id}"),
                 session_id,
                 params=params,
                 payload=item_payload))
  result = {"ok": True, "data": results}
  _log_tool_call("calendar.move_event", output_parsed, result)
  return result


@mcp.tool(name="calendar.cancel_event")
def calendar_cancel_event(
    output_parsed: Dict[str, Any],
) -> Dict[str, Any]:
  items = output_parsed.get("items")
  session_id = output_parsed.get("session_id")

  if not isinstance(items, list) or not items:
    result = {
        "ok": False,
        "code": "invalid_request",
        "message": "items is required and must be a non-empty array.",
    }
    _log_tool_call("calendar.cancel_event", output_parsed, result)
    return result

  results: List[Dict[str, Any]] = []
  for item in items:
    if not isinstance(item, dict):
      continue
    item_event_id = item.get("event_id")
    if not item_event_id:
      results.append({
          "ok": False,
          "code": "invalid_request",
          "message": "event_id is required for each item.",
          "item": item,
      })
      continue
    item_calendar_id = item.get("calendar_id")
    params = {"calendar_id": item_calendar_id} if item_calendar_id else None
    safe_event_id = quote(str(item_event_id), safe="")
    results.append(
        _request("DELETE",
                 _api_path(f"/google/events/{safe_event_id}"),
                 session_id,
                 params=params))
  result = {"ok": True, "data": results}
  _log_tool_call("calendar.cancel_event", output_parsed, result)
  return result


@mcp.tool(name="calendar.find_event")
def calendar_find_event(
    output_parsed: Dict[str, Any],
) -> Dict[str, Any]:
  ranges = output_parsed.get("ranges")
  query = output_parsed.get("query")
  calendar_id = output_parsed.get("calendar_id")
  all_day = output_parsed.get("all_day")
  limit = output_parsed.get("limit")
  session_id = output_parsed.get("session_id")

  if query is not None and not isinstance(query, list):
    result = {
        "ok": False,
        "code": "invalid_request",
        "message": "query must be an array of strings.",
    }
    _log_tool_call("calendar.find_event", output_parsed, result)
    return result

  if not isinstance(ranges, list) or not ranges:
    result = {
        "ok": False,
        "code": "invalid_request",
        "message": "ranges is required and must be a non-empty array.",
    }
    _log_tool_call("calendar.find_event", output_parsed, result)
    return result

  unique_ranges: List[Dict[str, str]] = []
  seen_range_keys: set[str] = set()
  for entry in ranges:
    if isinstance(entry, FindEventRange):
      range_start = entry.start_date
      range_end = entry.end_date
    elif isinstance(entry, dict):
      range_start = entry.get("start_date")
      range_end = entry.get("end_date")
    else:
      continue
    if not range_start or not range_end:
      result = {
          "ok": False,
          "code": "invalid_request",
          "message": "Each range requires start_date and end_date.",
          "range": _range_entry_to_dict(entry),
      }
      _log_tool_call("calendar.find_event", output_parsed, result)
      return result
    if range_end < range_start:
      result = {
          "ok": False,
          "code": "invalid_request",
          "message": "end_date must be >= start_date.",
          "range": _range_entry_to_dict(entry),
      }
      _log_tool_call("calendar.find_event", output_parsed, result)
      return result
    key = f"{range_start}:{range_end}"
    if key in seen_range_keys:
      continue
    seen_range_keys.add(key)
    unique_ranges.append({"start_date": range_start, "end_date": range_end})

  if not unique_ranges:
    result = {
        "ok": False,
        "code": "invalid_request",
        "message": "ranges must contain at least one valid entry.",
    }
    _log_tool_call("calendar.find_event", output_parsed, result)
    return result

  unique_ranges.sort(key=lambda r: (r["start_date"], r["end_date"]))

  or_terms = _normalize_or_terms(query)

  backend_limit = limit
  if or_terms:
    backend_limit = None

  aggregated: List[Dict[str, Any]] = []
  seen: set[str] = set()
  for entry in unique_ranges:
    params = {
        "start_date": entry["start_date"],
        "end_date": entry["end_date"],
    }
    if calendar_id:
      params["calendar_id"] = calendar_id
    if all_day is not None:
      params["all_day"] = all_day
    if isinstance(backend_limit, int) and backend_limit > 0:
      params["limit"] = backend_limit

    result = _request("GET",
                      _api_path("/google/events"),
                      session_id,
                      params=params)
    if not result.get("ok"):
      _log_tool_call("calendar.find_event", output_parsed, result)
      return result

    items = result.get("data") or []
    if not isinstance(items, list):
      result = {
          "ok": False,
          "code": "unexpected_response",
          "message": "Events response is not a list.",
          "error": items,
      }
      _log_tool_call("calendar.find_event", output_parsed, result)
      return result
    for item in items:
      if not isinstance(item, dict):
        continue
      key = f"{item.get('calendar_id')}::{item.get('id')}"
      if key in seen:
        continue
      seen.add(key)
      aggregated.append(_filter_event_fields(item))

  if or_terms:
    aggregated = _filter_by_or_terms(aggregated, or_terms)
  aggregated.sort(key=lambda ev: ev.get("start") or "")
  if isinstance(limit, int) and limit > 0:
    aggregated = aggregated[:limit]
  result = {"ok": True, "data": aggregated}
  _log_tool_call("calendar.find_event", output_parsed, result)
  return result


@mcp.tool(name="calendar.freebusy")
def calendar_freebusy(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    calendar_ids: Optional[List[str]] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
  _ = (start_date, end_date, calendar_ids, session_id)
  return _not_implemented("calendar.freebusy")


@mcp.tool(name="calendar.summarize")
def calendar_summarize(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
  _ = (start_date, end_date, session_id)
  return _not_implemented("calendar.summarize")


@mcp.tool(name="tasks.create_task")
def tasks_create_task(
    output_parsed: Dict[str, Any],
) -> Dict[str, Any]:
  title = output_parsed.get("title")
  notes = output_parsed.get("notes")
  due = output_parsed.get("due")
  session_id = output_parsed.get("session_id")
  
  payload = {"title": title}
  if notes is not None:
    payload["notes"] = notes
  if due is not None:
    payload["due"] = due
  result = _request("POST", _api_path("/google/tasks"), session_id, payload=payload)
  _log_tool_call("tasks.create_task", output_parsed, result)
  return result


@mcp.tool(name="tasks.update_task")
def tasks_update_task(
    output_parsed: Dict[str, Any],
) -> Dict[str, Any]:
  task_id = output_parsed.get("task_id")
  title = output_parsed.get("title")
  notes = output_parsed.get("notes")
  due = output_parsed.get("due")
  status = output_parsed.get("status")
  session_id = output_parsed.get("session_id")
  
  payload = {
      "title": title,
      "notes": notes,
      "due": due,
      "status": status,
  }
  payload = {k: v for k, v in payload.items() if v is not None}
  safe_task_id = quote(task_id, safe="")
  result = _request("PATCH",
                  _api_path(f"/google/tasks/{safe_task_id}"),
                  session_id,
                  payload=payload)
  _log_tool_call("tasks.update_task", output_parsed, result)
  return result


@mcp.tool(name="tasks.complete_task")
def tasks_complete_task(
    output_parsed: Dict[str, Any],
) -> Dict[str, Any]:
  task_id = output_parsed.get("task_id")
  session_id = output_parsed.get("session_id")
  
  result = tasks_update_task(output_parsed={"task_id": task_id, "status": "completed", "session_id": session_id})
  _log_tool_call("tasks.complete_task", output_parsed, result)
  return result


@mcp.tool(name="tasks.find_task")
def tasks_find_task(
    output_parsed: Dict[str, Any],
) -> Dict[str, Any]:
  query = output_parsed.get("query")
  status = output_parsed.get("status")
  limit = output_parsed.get("limit")
  session_id = output_parsed.get("session_id")
  
  result = _request("GET", _api_path("/google/tasks"), session_id)
  if not result.get("ok"):
    _log_tool_call("tasks.find_task", output_parsed, result)
    return result

  items = result.get("data") or []
  if not isinstance(items, list):
    result = {
        "ok": False,
        "code": "unexpected_response",
        "message": "Tasks response is not a list.",
        "error": items,
    }
    _log_tool_call("tasks.find_task", output_parsed, result)
    return result

  filtered = items
  if status:
    filtered = [item for item in filtered if item.get("status") == status]
  filtered = _filter_by_query(filtered, query)
  if isinstance(limit, int) and limit > 0:
    filtered = filtered[:limit]

  result = {"ok": True, "data": filtered}
  _log_tool_call("tasks.find_task", output_parsed, result)
  return result


@mcp.tool(name="tasks.summarize")
def tasks_summarize(
    status: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
  _ = (status, session_id)
  return _not_implemented("tasks.summarize")


@mcp.tool(name="tasks.plan_timeblock")
def tasks_plan_timeblock(
    task_id: Optional[str] = None,
    duration_minutes: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
  _ = (task_id, duration_minutes, start_date, end_date, session_id)
  return _not_implemented("tasks.plan_timeblock")


if __name__ == "__main__":
  import uvicorn

  host = os.getenv("MCP_HOST", "0.0.0.0")
  port = int(os.getenv("MCP_PORT", "8001"))
  app = mcp.streamable_http_app()
  if LOG_REQUESTS:
    app = RequestLoggerMiddleware(app)
  uvicorn.run(app, host=host, port=port)
