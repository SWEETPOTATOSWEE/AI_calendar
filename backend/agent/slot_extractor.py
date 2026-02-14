from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .llm_provider import run_structured_completion, get_agent_llm_settings
from .normalizer import _DATE_ONLY_RE_NORM, coerce_iso_minute, coerce_rfc3339, try_parse_date
from .resolve_event_target import resolve_event_target_with_debug
from .schemas import (
    PlanStep, StepArgs, ValidationIssue,
    CreateEventOutput, UpdateEventOutput, CancelEventOutput,
    CreateTaskOutput, UpdateTaskOutput,
    CancelTaskOutput,
)
from ..recurrence import _normalize_recurrence_dict, _normalize_rrule_core

_EVENT_UPDATE_PATCH_KEYS = (
    "type",
    "title",
    "start",
    "end",
    "start_date",
    "time",
    "duration_minutes",
    "location",
    "description",
    "reminders",
    "all_day",
    "recurrence",
    "rrule",
)
_EVENT_CREATE_ALLOWED_KEYS = (
    "items",
    "title",
    "start",
    "end",
    "start_date",
    "time",
    "duration_minutes",
    "recurrence",
    "rrule",
    "location",
    "description",
    "reminders",
    "all_day",
)
_EVENT_UPDATE_ALLOWED_KEYS = (
    "items",
    "event_id",
    "event_ids",
    "title",
    "start",
    "end",
    "start_date",
    "time",
    "duration_minutes",
    "location",
    "description",
    "reminders",
    "all_day",
    "recurrence",
    "rrule",
)
_EVENT_CANCEL_ALLOWED_KEYS = (
    "event_id",
    "event_ids",
)
_TASK_CREATE_ALLOWED_KEYS = (
    "items",
    "title",
    "notes",
    "due",
    "start_date",
    "time",
    "recurrence",
    "rrule",
)
_TASK_CREATE_ITEM_ALLOWED_KEYS = (
    "type",
    "title",
    "notes",
    "due",
    "start_date",
    "time",
    "recurrence",
    "rrule",
)
_TASK_UPDATE_ALLOWED_KEYS = (
    "items",
    "task_id",
    "task_ids",
    "title",
    "notes",
    "due",
    "status",
)
_TASK_UPDATE_ITEM_ALLOWED_KEYS = (
    "task_id",
    "title",
    "notes",
    "due",
    "status",
)
_TASK_TARGET_ALLOWED_KEYS = (
    "items",
    "task_id",
    "task_ids",
    "title",
)
_TASK_TARGET_ITEM_ALLOWED_KEYS = (
    "task_id",
    "title",
)
_TASK_UPDATE_PATCH_KEYS = ("title", "notes", "due", "status")
_ITEM_SINGLE_ALLOWED_KEYS = (
    "type",
    "title",
    "start",
    "end",
    "location",
    "description",
    "reminders",
    "all_day",
)
_ITEM_RECURRING_ALLOWED_KEYS = (
    "type",
    "title",
    "start_date",
    "time",
    "duration_minutes",
    "location",
    "description",
    "reminders",
    "all_day",
    "recurrence",
    "rrule",
)
_ITEM_UPDATE_ALLOWED_KEYS = (
    "event_id",
    "type",
    "title",
    "start",
    "end",
    "start_date",
    "time",
    "duration_minutes",
    "location",
    "description",
    "reminders",
    "all_day",
    "recurrence",
    "rrule",
)
_HHMM_RE = re.compile(r"^\d{2}:\d{2}$")
_RRULE_CANDIDATE_RE = re.compile(
    r"(?i)(?:RRULE:\s*|rrule\s+)(FREQ=[A-Z]+[A-Z0-9=,;:+-]*)")
_SELECTION_SPLIT_RE = re.compile(r"[,;\n]+")
_NUMERIC_RANGE_RE = re.compile(r"^(\d+)\s*(?:~|-|–|—|to)\s*(\d+)$", re.IGNORECASE)
SLOT_EXTRACTOR_MODEL = os.getenv("AGENT_SLOT_EXTRACTOR_MODEL", "gpt-5-mini").strip()
_SETTINGS = get_agent_llm_settings("SLOT_EXTRACTOR")
print(f"[SLOT_EXTRACTOR] Loaded model: {SLOT_EXTRACTOR_MODEL}, provider: {os.getenv('AGENT_LLM_PROVIDER', 'auto')}", flush=True)
SLOT_EXTRACTOR_CONTEXT_EVENT_LIMIT = 40
SLOT_EXTRACTOR_CONTEXT_TASK_LIMIT = 40
EVENT_TARGET_CANDIDATE_LIMIT = 40
SLOT_EXTRACTOR_CLARIFY_CONFIDENCE_THRESHOLD = 0.7


# ---------------------------------------------------------------------------
#  Per-intent system / developer prompts
# ---------------------------------------------------------------------------

_CREATE_EVENT_SYSTEM = """Event creation slot extractor. Return JSON only. No markdown.
Current: {now_iso}. Timezone: {timezone}.
Rules:
- If extract_hint is provided, extract ONLY the events described in extract_hint. Ignore everything else.
- type: "single" or "recurring".
- NEVER invent or guess times, durations, or dates not explicitly stated by user.
- If any value is uncertain, leave that field omitted (empty). Do not infer.
- Unknown/ambiguous values must be omitted rather than approximated.
- If user specifies a date but NOT a time, use date-only format "YYYY-MM-DD" (e.g. "2026-02-12") for start/end.
- CRITICAL: Do NOT add "T00:00" or any placeholder time if the user didn't mention a specific time.
- all_day=true only when user explicitly says all-day/종일/하루종일.
- recurring: prefer rrule string first; use recurrence object only if needed.
- Omit any field the user did not mention.
- If events context is provided, use it to resolve relative references (e.g. "after my meeting", "between classes") to concrete times.
"""

_CREATE_EVENT_DEV = """Input: user_text, now_iso, timezone, language, events (optional — existing calendar events for reference).
Output: items[] — each with title, type, and available fields.
single fields: title, start (YYYY-MM-DD or YYYY-MM-DDTHH:MM), end (YYYY-MM-DD or YYYY-MM-DDTHH:MM), location, description, reminders (list of minutes, e.g. [10,30]), all_day.
recurring fields: title, start_date (YYYY-MM-DD), time (HH:MM), duration_minutes, recurrence or rrule, location, description, reminders, all_day.
recurrence object: {{freq: "daily"|"weekly"|"monthly"|"yearly", interval?: number, byweekday?: [0-6], bymonthday?: [number], bysetpos?: number, bymonth?: [number], end?: {{until?: "YYYY-MM-DD", count?: number}}}}.
Omit unmentioned fields. Prefer omission over guessing.
If a field cannot be determined with explicit evidence, omit it (leave blank).
Never fill unknown values with defaults, estimates, placeholders, or "best guess".
If all_day is not explicitly stated but can be determined with high confidence from the request, set all_day=true.
When events context is provided, use it to resolve relative time references to concrete start/end times.

Examples:

User: "내일 오후 3시에 팀미팅 30분"
{{"items":[{{"type":"single","title":"팀미팅","start":"2026-02-12T15:00","end":"2026-02-12T15:30"}}]}}

User: "매주 월수금 아침 9시에 스탠드업 미팅 30분, 3월까지"
{{"items":[{{"type":"recurring","title":"스탠드업 미팅","start_date":"2026-02-11","time":"09:00","duration_minutes":30,"rrule":"FREQ=WEEKLY;BYDAY=MO,WE,FR;UNTIL=20260331"}}]}}

User: "2월 14일 종일 발렌타인데이"
{{"items":[{{"type":"single","title":"발렌타인데이","start":"2026-02-14","all_day":true}}]}}

User: "2월 18일 휴가 일정 등록해줘"
{{"items":[{{"type":"single","title":"휴가","start":"2026-02-18","all_day":true}}]}}

User: "3월 5일 프로젝트 킥오프 일정 추가해줘"
{{"items":[{{"type":"single","title":"프로젝트 킥오프","start":"2026-03-05"}}]}}

User: "2월 20일부터 2월 27일까지 휴가 일정 등록해줘"
{{"items":[{{"type":"single","title":"휴가","start":"2026-02-20","end":"2026-02-27","all_day":true}}]}}

User: "내일 오전 10시 치과, 오후 2시 미용실"
{{"items":[{{"type":"single","title":"치과","start":"2026-02-12T10:00"}},{{"type":"single","title":"미용실","start":"2026-02-12T14:00"}}]}}

User: "내일 마지막 회의 끝나고 저녁 약속 잡아줘" (events: [{"title":"팀미팅","start":"2026-02-12T14:00","end":"2026-02-12T15:00"},{"title":"프로젝트 회의","start":"2026-02-12T16:00","end":"2026-02-12T17:00"}])
{{"items":[{{"type":"single","title":"저녁 약속","start":"2026-02-12T17:00"}}]}}
"""

_UPDATE_EVENT_SYSTEM = """Event update slot extractor. Return JSON only. No markdown.
Current: {now_iso}. Timezone: {timezone}.
Rules:
- If extract_hint is provided, extract ONLY the update described in extract_hint. Ignore everything else.
- Prefer items[] output. Use one item for a single update and multiple items for multi updates.
- For each item, set event_id from context when possible.
- Use the event_id exactly as provided in context events (it may be a simple numeric id like "1", "2").
- Use item.type only when user clearly requests conversion or recurring-form update.
- Fill only fields the user wants to change.
- NEVER invent times not stated by user.
"""

_UPDATE_EVENT_DEV = """Input: user_text, now_iso, timezone, language, events.
Output:
- Preferred: items[] where each item has event_id + changed fields.
- Legacy (single only): event_id + changed fields.

single patch fields: title, start (YYYY-MM-DDTHH:MM), end (YYYY-MM-DDTHH:MM), location, description, reminders, all_day.
recurring patch fields: title, start_date (YYYY-MM-DD), time (HH:MM), duration_minutes, location, description, reminders, all_day, recurrence or rrule.
conversion:
- recurring -> single: set type="single" and provide single fields.
- single -> recurring: set type="recurring" and provide recurrence or rrule (plus start_date/time if user specified).
If all_day is not explicitly stated but can be determined with high confidence from the request, set all_day=true.
When updating an all-day event, write end as a date value (not dateTime). Use YYYY-MM-DD for all-day boundaries.
Use event_id from context exactly as given.

Examples:

User: "내일 팀미팅 3시에서 4시로 변경" (context has event_id "1")
{{"event_id":"1","start":"2026-02-12T16:00"}}

User: "매주 스탠드업을 월수금 오전 10시로 변경" (context has event_id "2")
{{"items":[{{"event_id":"2","type":"recurring","time":"10:00","rrule":"FREQ=WEEKLY;BYDAY=MO,WE,FR"}}]}}

User: "이 반복 회의를 이번 주 금요일 3시 단일 일정으로 바꿔" (context has event_id "3")
{{"items":[{{"event_id":"3","type":"single","start":"2026-02-13T15:00"}}]}}

User: "내일 치과 일정을 매주 화요일 오전 10시 반복으로 바꿔" (context has event_id "4")
{{"items":[{{"event_id":"4","type":"recurring","start_date":"2026-02-12","time":"10:00","rrule":"FREQ=WEEKLY;BYDAY=TU"}}]}}

User: "내일 팀미팅을 종일 일정으로 바꿔줘" (context has event_id "1")
{{"items":[{{"event_id":"1","all_day":true}}]}}

User: "내일 10시 치과는 11시로, 2시 미용실은 4시로 바꿔" (context has 2 events)
{{"items":[{{"event_id":"1","start":"2026-02-12T11:00"}},{{"event_id":"2","start":"2026-02-12T16:00"}}]}}
"""

_CANCEL_EVENT_SYSTEM = """Event cancellation slot extractor. Return JSON only. No markdown.
Current: {now_iso}. Timezone: {timezone}.
Rules:
- If extract_hint is provided, extract ONLY the cancellation described in extract_hint. Ignore everything else.
- Specific events: set event_id or event_ids from context.
- Use event_id values exactly as provided in context events (numeric ids are allowed).
"""

_CANCEL_EVENT_DEV = """Input: user_text, now_iso, timezone, language, events.
Output: event_id (single), event_ids (multiple).
Match events from context.
For multi-target cancellation, event_ids may include numeric ranges such as "1~10".

Examples:

User: "내일 스탠드업 취소해" (context has event_id "1")
{{"event_id":"1"}}

User: "내일 일정 전부 삭제해줘"
{{"event_ids":["1~2"]}}

User: "이 세 개 일정 다 삭제해" (context has 3 events)
{{"event_ids":["1","2","3"]}}
"""

_CREATE_TASK_SYSTEM = """Task creation slot extractor. Return JSON only. No markdown.
Current: {now_iso}. Timezone: {timezone}.
Rules:
- If extract_hint is provided, extract ONLY the tasks described in extract_hint. Ignore everything else.
- Prefer items[] output. One user request can map to multiple tasks.
- Each item.type is "single" or "recurring".
- For recurring tasks, output rrule (do not output natural language recurrence text).
- title is required per item.
- Set notes, due/start_date/time only when explicitly mentioned.
- NEVER invent or guess due dates, recurrence rules, notes, or any field not stated by user.
"""

_CREATE_TASK_DEV = """Input: user_text, now_iso, timezone, language.
Output:
- Preferred: items[].
single item fields: type="single", title, notes?, due? (RFC3339).
recurring item fields: type="recurring", title, notes?, start_date (YYYY-MM-DD), time? (HH:MM), rrule.
- Legacy fallback (single only): title, notes?, due.

Examples:

User: "내일까지 보고서 제출"
{{"items":[{{"type":"single","title":"보고서 제출","due":"2026-02-12T23:59:00+09:00"}}]}}

User: "우유 사기"
{{"items":[{{"type":"single","title":"우유 사기"}}]}}

User: "금요일까지 프레젠테이션 준비, 참고자료 첨부할 것"
{{"items":[{{"type":"single","title":"프레젠테이션 준비","notes":"참고자료 첨부할 것","due":"2026-02-13T23:59:00+09:00"}}]}}

User: "매주 월수금 아침 9시에 약 먹기 할일 추가해"
{{"items":[{{"type":"recurring","title":"약 먹기","start_date":"2026-02-11","time":"09:00","rrule":"FREQ=WEEKLY;BYDAY=MO,WE,FR"}}]}}
"""

_UPDATE_TASK_SYSTEM = """Task update slot extractor. Return JSON only. No markdown.
Current: {now_iso}. Timezone: {timezone}.
Rules:
- If extract_hint is provided, extract ONLY the updates described in extract_hint. Ignore everything else.
- Prefer items[] output. Use one item for a single update and multiple items for bulk updates.
- Match task from context using task_id when possible.
- Use task_id exactly as provided in context tasks (it may be a simple numeric id like "1", "2").
- Fill only changed fields. status: "needsAction" or "completed".
"""

_UPDATE_TASK_DEV = """Input: user_text, now_iso, timezone, language, tasks.
Output:
- Preferred: items[] where each item has task_id + changed fields (title, notes, due, status).
- Legacy (single only): task_id + changed fields.

Examples:

User: "보고서 제출 마감을 금요일로 연장해줘" (context has task_id "1")
{{"items":[{{"task_id":"1","due":"2026-02-13T23:59:00+09:00"}}]}}

User: "우유 사기 완료" (context has task_id "2")
{{"items":[{{"task_id":"2","status":"completed"}}]}}

User: "우유 사기는 완료로, 빨래하기는 내일로 미뤄줘" (context has 2 tasks)
{{"items":[{{"task_id":"1","status":"completed"}},{{"task_id":"2","due":"2026-02-12T23:59:00+09:00"}}]}}
"""

_CANCEL_TASK_SYSTEM = """Task cancellation slot extractor. Return JSON only. No markdown.
Current: {now_iso}. Timezone: {timezone}.
Rules:
- If extract_hint is provided, extract ONLY the cancellation requests described in extract_hint. Ignore everything else.
- Prefer items[] output for one or more targets.
- Match task from context using task_id.
- Use task_id exactly as provided in context tasks (numeric ids are allowed).
"""

_CANCEL_TASK_DEV = """Input: user_text, now_iso, timezone, language, tasks.
Output:
- Preferred: items[] with task_id.
- Legacy (single only): task_id.
For multi-target cancellation, task_ids may include numeric ranges such as "1~10".

Examples:

User: "우유 사기 삭제해" (context has task_id "1")
{{"items":[{{"task_id":"1"}}]}}
"""

_SLOT_CONFIDENCE_GUIDANCE = """

Confidence rubric (required):
- clear >= 0.85
- likely = 0.65~0.84
- partial = 0.40~0.64
- unclear < 0.40

Return a top-level "confidence" number in [0,1] that reflects extraction reliability.
"""


def _with_confidence_guidance(prompt: str) -> str:
  return f"{prompt.strip()}{_SLOT_CONFIDENCE_GUIDANCE}"

# ---------------------------------------------------------------------------
#  Utility functions
# ---------------------------------------------------------------------------

def _clean_str(value: Any) -> Optional[str]:
  if not isinstance(value, str):
    return None
  text = value.strip()
  return text or None


def _clean_int(value: Any) -> Optional[int]:
  if isinstance(value, bool):
    return None
  try:
    return int(value)
  except Exception:
    return None


def _clean_selection_token(value: Any) -> Optional[str]:
  if isinstance(value, bool):
    return None
  if isinstance(value, int):
    return str(value)
  if isinstance(value, float):
    if value.is_integer():
      return str(int(value))
    return None
  return _clean_str(value)


def _split_selection_tokens(value: Any) -> List[str]:
  token = _clean_selection_token(value)
  if not token:
    return []
  return [part.strip() for part in _SELECTION_SPLIT_RE.split(token) if part.strip()]


def _normalize_reminders(value: Any) -> Optional[List[int]]:
  if value is None:
    return None
  if not isinstance(value, list):
    return None
  out: List[int] = []
  seen: set[int] = set()
  for raw in value:
    minutes = _clean_int(raw)
    if minutes is None or minutes < 0 or minutes in seen:
      continue
    out.append(minutes)
    seen.add(minutes)
  return out


def _normalize_string_list(value: Any) -> Optional[List[str]]:
  if value is None:
    return None
  if not isinstance(value, list):
    return None
  out: List[str] = []
  seen: set[str] = set()
  for raw in value:
    text = _clean_str(raw)
    if not text:
      continue
    lowered = text.lower()
    if lowered in seen:
      continue
    seen.add(lowered)
    out.append(text)
  return out


def _normalize_time_hhmm(value: Any) -> Optional[str]:
  text = _clean_str(value)
  if not text:
    return None
  text = re.sub(r"\s*:\s*", ":", text)
  parts = text.split(":")
  if len(parts) >= 2:
    text = f"{parts[0]}:{parts[1]}"
  if not _HHMM_RE.match(text):
    return None
  try:
    hour, minute = [int(part) for part in text.split(":")]
  except Exception:
    return None
  if not (0 <= hour <= 23 and 0 <= minute <= 59):
    return None
  return f"{hour:02d}:{minute:02d}"


def _parse_iso_minute_safe(value: str) -> Optional[datetime]:
  try:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M")
  except Exception:
    return None


# ---------------------------------------------------------------------------
#  Item normalization
# ---------------------------------------------------------------------------

def _normalize_single_create_item(raw: Dict[str, Any],
                                  step_id: str,
                                  timezone_name: str,
                                  index: int) -> Tuple[Optional[Dict[str, Any]], List[ValidationIssue]]:
  item_raw = _filter_allowed_args(raw, _ITEM_SINGLE_ALLOWED_KEYS)
  issues: List[ValidationIssue] = []

  title = _clean_str(item_raw.get("title"))
  raw_start = item_raw.get("start")
  is_all_day = item_raw.get("all_day") is True

  # For all-day events, keep date-only string as-is (YYYY-MM-DD).
  # coerce_iso_minute rejects date-only, so handle separately.
  if is_all_day and isinstance(raw_start, str) and _DATE_ONLY_RE_NORM.match(raw_start.strip()):
    start = raw_start.strip()
  else:
    start = coerce_iso_minute(raw_start, timezone_name)

  raw_end = item_raw.get("end")
  if is_all_day and isinstance(raw_end, str) and _DATE_ONLY_RE_NORM.match(raw_end.strip()):
    end = raw_end.strip()
  else:
    end = coerce_iso_minute(raw_end, timezone_name)

  if not title:
    issues.append(
        _issue(step_id,
               "missing_slot",
               f"items[{index}].title is required for single events.",
               "items"))

  if not start:
    issues.append(
        _issue(step_id,
               "missing_slot",
               f"items[{index}].start is required for single events.",
               "items"))

  if start and end:
    start_dt = _parse_iso_minute_safe(start)
    end_dt = _parse_iso_minute_safe(end)
    if start_dt and end_dt and end_dt <= start_dt:
      issues.append(
          _issue(step_id,
                 "invalid_value",
                 f"items[{index}].end must be after items[{index}].start.",
                 "items"))

  reminders = _normalize_reminders(item_raw.get("reminders"))
  if item_raw.get("reminders") is not None and reminders is None:
    issues.append(
        _issue(step_id,
               "invalid_value",
               f"items[{index}].reminders must be an integer array.",
               "items"))
  item: Dict[str, Any] = {
      "type": "single",
      "title": title,
      "start": start,
      "end": end,
      "location": _clean_str(item_raw.get("location")),
      "description": _clean_str(item_raw.get("description")),
      "reminders": reminders,
  }
  if isinstance(item_raw.get("all_day"), bool):
    item["all_day"] = bool(item_raw.get("all_day"))
  return item, issues


def _normalize_recurring_create_item(raw: Dict[str, Any],
                                     step_id: str,
                                     index: int) -> Tuple[Optional[Dict[str, Any]], List[ValidationIssue]]:
  item_raw = _filter_allowed_args(raw, _ITEM_RECURRING_ALLOWED_KEYS)
  issues: List[ValidationIssue] = []

  title = _clean_str(item_raw.get("title"))
  start_date_raw = _clean_str(item_raw.get("start_date"))
  start_date = start_date_raw if start_date_raw and try_parse_date(start_date_raw) else None
  time_value = _normalize_time_hhmm(item_raw.get("time"))
  duration_value = _clean_int(item_raw.get("duration_minutes"))
  recurrence_raw = item_raw.get("recurrence")
  recurrence = _normalize_recurrence_dict(
      recurrence_raw) if isinstance(recurrence_raw, dict) else None
  rrule = _normalize_rrule_core(item_raw.get("rrule"))

  if not title:
    issues.append(
        _issue(step_id,
               "missing_slot",
               f"items[{index}].title is required for recurring events.",
               "items"))
  if not start_date:
    issues.append(
        _issue(step_id,
               "missing_slot",
               f"items[{index}].start_date is required for recurring events.",
               "items"))
  if item_raw.get("time") is not None and not time_value:
    issues.append(
        _issue(step_id,
               "invalid_value",
               f"items[{index}].time must be HH:MM.",
               "items"))
  if item_raw.get("duration_minutes") is not None:
    if duration_value is None or duration_value <= 0:
      issues.append(
          _issue(step_id,
                 "invalid_value",
                 f"items[{index}].duration_minutes must be a positive integer.",
                 "items"))
  if recurrence_raw is not None and recurrence is None:
    issues.append(
        _issue(step_id,
               "invalid_value",
               f"items[{index}].recurrence payload is invalid.",
               "items"))
  if item_raw.get("rrule") is not None and rrule is None:
    issues.append(
        _issue(step_id,
               "invalid_value",
               f"items[{index}].rrule is invalid.",
               "items"))
  if recurrence is None and rrule is None:
    issues.append(
        _issue(step_id,
               "missing_slot",
               f"items[{index}] requires recurrence or rrule for recurring events.",
               "items"))

  reminders = _normalize_reminders(item_raw.get("reminders"))
  if item_raw.get("reminders") is not None and reminders is None:
    issues.append(
        _issue(step_id,
               "invalid_value",
               f"items[{index}].reminders must be an integer array.",
               "items"))
  item: Dict[str, Any] = {
      "type": "recurring",
      "title": title,
      "start_date": start_date,
      "time": time_value,
      "duration_minutes": duration_value if duration_value and duration_value > 0 else None,
      "location": _clean_str(item_raw.get("location")),
      "description": _clean_str(item_raw.get("description")),
      "reminders": reminders,
      "recurrence": recurrence,
      "rrule": rrule,
  }
  if isinstance(item_raw.get("all_day"), bool):
    item["all_day"] = bool(item_raw.get("all_day"))
  return item, issues


def _normalize_create_items(items_raw: List[Any], step_id: str,
                            timezone_name: str) -> Tuple[List[Dict[str, Any]], List[ValidationIssue]]:
  normalized_items: List[Dict[str, Any]] = []
  issues: List[ValidationIssue] = []
  for index, raw in enumerate(items_raw):
    if not isinstance(raw, dict):
      issues.append(
          _issue(step_id,
                 "invalid_value",
                 f"items[{index}] must be an object.",
                 "items"))
      continue
    item_type = _clean_str(raw.get("type"))
    if item_type:
      item_type = item_type.lower()
    if item_type not in ("single", "recurring"):
      if any(raw.get(key) is not None for key in ("recurrence", "rrule", "start_date",
                                                  "duration_minutes")):
        item_type = "recurring"
      else:
        item_type = "single"

    if item_type == "recurring":
      item, item_issues = _normalize_recurring_create_item(raw, step_id, index)
    else:
      item, item_issues = _normalize_single_create_item(raw, step_id, timezone_name, index)

    issues.extend(item_issues)
    if item is not None:
      normalized_items.append(item)
  return normalized_items, issues


def _fallback_create_item_from_legacy_fields(args: Dict[str, Any], step_id: str,
                                             timezone_name: str
                                             ) -> Tuple[Optional[Dict[str, Any]], List[ValidationIssue]]:
  recurring_signal = any(args.get(key) is not None for key in (
      "recurrence",
      "rrule",
      "start_date",
      "time",
      "duration_minutes",
  ))
  if recurring_signal:
    raw = {
        "type": "recurring",
        "title": args.get("title"),
        "start_date": args.get("start_date"),
        "time": args.get("time"),
        "duration_minutes": args.get("duration_minutes"),
        "location": args.get("location"),
        "description": args.get("description"),
        "reminders": args.get("reminders"),
        "all_day": args.get("all_day"),
        "recurrence": args.get("recurrence"),
        "rrule": args.get("rrule"),
    }
    return _normalize_recurring_create_item(raw, step_id, 0)

  raw = {
      "type": "single",
      "title": args.get("title"),
      "start": args.get("start"),
      "end": args.get("end"),
      "location": args.get("location"),
      "description": args.get("description"),
      "reminders": args.get("reminders"),
      "all_day": args.get("all_day"),
  }
  return _normalize_single_create_item(raw, step_id, timezone_name, 0)


def _normalize_create_args(args: Dict[str, Any], step_id: str,
                           timezone_name: str) -> Tuple[Dict[str, Any], List[ValidationIssue]]:
  normalized: Dict[str, Any] = {}
  issues: List[ValidationIssue] = []

  items_raw = args.get("items")
  if items_raw is not None and not isinstance(items_raw, list):
    issues.append(
        _issue(step_id,
               "invalid_value",
               "items must be an array when provided.",
               "items"))
    items_raw = None

  if isinstance(items_raw, list) and items_raw:
    normalized_items, item_issues = _normalize_create_items(items_raw, step_id, timezone_name)
    normalized["items"] = normalized_items
    issues.extend(item_issues)
    if not normalized_items:
      issues.append(
          _issue(step_id,
                 "missing_slot",
                 "items must include at least one valid create item.",
                 "items"))
    return normalized, issues

  fallback_item, fallback_issues = _fallback_create_item_from_legacy_fields(
      args, step_id, timezone_name)
  issues.extend(fallback_issues)
  if fallback_item is not None:
    normalized["items"] = [fallback_item]
    return normalized, issues

  issues.append(
      _issue(step_id,
             "missing_slot",
             "items is required for calendar.create_event.",
             "items"))
  normalized["items"] = []
  return normalized, issues


# ---------------------------------------------------------------------------
#  update_event item normalization
# ---------------------------------------------------------------------------

def _infer_update_item_type(raw: Dict[str, Any]) -> Optional[str]:
  raw_type = _clean_str(raw.get("type"))
  if raw_type:
    lowered = raw_type.lower()
    if lowered in ("single", "recurring"):
      return lowered
    return None
  if any(raw.get(key) is not None for key in ("recurrence", "rrule",
                                               "start_date", "time",
                                               "duration_minutes")):
    return "recurring"
  return None


def _normalize_update_item(raw: Dict[str, Any],
                           step_id: str,
                           timezone_name: str,
                           index: int) -> Tuple[Optional[Dict[str, Any]], List[ValidationIssue]]:
  item_raw = _filter_allowed_args(raw, _ITEM_UPDATE_ALLOWED_KEYS)
  issues: List[ValidationIssue] = []

  raw_type = _clean_str(item_raw.get("type"))
  item_type = _infer_update_item_type(item_raw)
  if raw_type and item_type is None:
    issues.append(
        _issue(step_id,
               "invalid_value",
               f"items[{index}].type must be single or recurring.",
               "items"))

  event_id = _clean_str(item_raw.get("event_id"))

  # Accept date-only bounds for all-day updates and normalize to internal
  # minute-based representation: start=00:00, end=00:00 (next day).
  inferred_all_day = False
  start_raw = _clean_str(item_raw.get("start"))
  end_raw = _clean_str(item_raw.get("end"))

  start = coerce_iso_minute(item_raw.get("start"), timezone_name)
  if start is None and start_raw and try_parse_date(start_raw):
    start = f"{start_raw}T00:00"
    inferred_all_day = True

  end = coerce_iso_minute(item_raw.get("end"), timezone_name)
  if end is None and end_raw and try_parse_date(end_raw):
    try:
      end_date_obj = datetime.strptime(end_raw, "%Y-%m-%d").date()
      next_day = end_date_obj + timedelta(days=1)
      end = f"{next_day.strftime('%Y-%m-%d')}T00:00"
    except Exception:
      end = f"{end_raw}T00:00"
    inferred_all_day = True
  start_date_raw = _clean_str(item_raw.get("start_date"))
  start_date = start_date_raw if start_date_raw and try_parse_date(start_date_raw) else None
  time_value = _normalize_time_hhmm(item_raw.get("time"))
  duration_value = _clean_int(item_raw.get("duration_minutes"))
  recurrence_raw = item_raw.get("recurrence")
  recurrence = _normalize_recurrence_dict(
      recurrence_raw) if isinstance(recurrence_raw, dict) else None
  rrule = _normalize_rrule_core(item_raw.get("rrule"))

  if item_raw.get("start") is not None and start is None:
    issues.append(
        _issue(step_id,
               "invalid_value",
               f"items[{index}].start must be YYYY-MM-DDTHH:MM or YYYY-MM-DD.",
               "items"))
  if item_raw.get("end") is not None and end is None:
    issues.append(
        _issue(step_id,
               "invalid_value",
               f"items[{index}].end must be YYYY-MM-DDTHH:MM or YYYY-MM-DD.",
               "items"))
  if start and end:
    start_dt = _parse_iso_minute_safe(start)
    end_dt = _parse_iso_minute_safe(end)
    if start_dt and end_dt and end_dt <= start_dt:
      issues.append(
          _issue(step_id,
                 "invalid_value",
                 f"items[{index}].end must be after items[{index}].start.",
                 "items"))
  if item_raw.get("start_date") is not None and not start_date:
    issues.append(
        _issue(step_id,
               "invalid_value",
               f"items[{index}].start_date must be a valid YYYY-MM-DD date.",
               "items"))
  if item_raw.get("time") is not None and not time_value:
    issues.append(
        _issue(step_id,
               "invalid_value",
               f"items[{index}].time must be HH:MM.",
               "items"))
  if item_raw.get("duration_minutes") is not None:
    if duration_value is None or duration_value <= 0:
      issues.append(
          _issue(step_id,
                 "invalid_value",
                 f"items[{index}].duration_minutes must be a positive integer.",
                 "items"))
  if recurrence_raw is not None and recurrence is None:
    issues.append(
        _issue(step_id,
               "invalid_value",
               f"items[{index}].recurrence payload is invalid.",
               "items"))
  if item_raw.get("rrule") is not None and rrule is None:
    issues.append(
        _issue(step_id,
               "invalid_value",
               f"items[{index}].rrule is invalid.",
               "items"))

  reminders = _normalize_reminders(item_raw.get("reminders"))
  if item_raw.get("reminders") is not None and reminders is None:
    issues.append(
        _issue(step_id,
               "invalid_value",
               f"items[{index}].reminders must be an integer array.",
               "items"))

  explicit_all_day = bool(item_raw.get("all_day")) if isinstance(item_raw.get("all_day"), bool) else None
  if explicit_all_day is None and inferred_all_day:
    explicit_all_day = True

  item: Dict[str, Any] = {
      "event_id": event_id,
      "type": item_type,
      "title": _clean_str(item_raw.get("title")),
      "start": start,
      "end": end,
      "start_date": start_date,
      "time": time_value,
      "duration_minutes": duration_value if duration_value and duration_value > 0 else None,
      "location": _clean_str(item_raw.get("location")),
      "description": _clean_str(item_raw.get("description")),
      "reminders": reminders,
      "all_day": explicit_all_day,
      "recurrence": recurrence,
      "rrule": rrule,
  }

  raw_patch_provided = any(item_raw.get(key) is not None for key in _EVENT_UPDATE_PATCH_KEYS)
  if not _has_patch_fields(item, _EVENT_UPDATE_PATCH_KEYS) and not raw_patch_provided:
    issues.append(
        _issue(step_id,
               "missing_slot",
               f"No update fields were provided at items[{index}].",
               "patch_fields"))

  return item, issues


def _normalize_update_items(items_raw: List[Any], step_id: str,
                            timezone_name: str) -> Tuple[List[Dict[str, Any]], List[ValidationIssue]]:
  normalized_items: List[Dict[str, Any]] = []
  issues: List[ValidationIssue] = []
  for index, raw in enumerate(items_raw):
    if not isinstance(raw, dict):
      issues.append(
          _issue(step_id,
                 "invalid_value",
                 f"items[{index}] must be an object.",
                 "items"))
      continue
    item, item_issues = _normalize_update_item(raw, step_id, timezone_name, index)
    issues.extend(item_issues)
    if item is not None:
      normalized_items.append(item)
  return normalized_items, issues


def _fallback_update_items_from_legacy_fields(
    args: Dict[str, Any],
    step_id: str,
    timezone_name: str,
) -> Tuple[List[Dict[str, Any]], List[ValidationIssue]]:
  issues: List[ValidationIssue] = []
  items: List[Dict[str, Any]] = []

  event_id = _clean_str(args.get("event_id"))
  event_ids = _normalize_event_ids(args.get("event_ids"))
  if event_id:
    if event_ids is None:
      event_ids = [event_id]
    elif event_id not in event_ids:
      event_ids.insert(0, event_id)

  base_raw = {
      "title": args.get("title"),
      "start": args.get("start"),
      "end": args.get("end"),
      "start_date": args.get("start_date"),
      "time": args.get("time"),
      "duration_minutes": args.get("duration_minutes"),
      "location": args.get("location"),
      "description": args.get("description"),
      "reminders": args.get("reminders"),
      "all_day": args.get("all_day"),
      "recurrence": args.get("recurrence"),
      "rrule": args.get("rrule"),
  }

  if event_ids:
    for index, eid in enumerate(event_ids):
      raw_item = dict(base_raw)
      raw_item["event_id"] = eid
      item, item_issues = _normalize_update_item(raw_item, step_id, timezone_name, index)
      issues.extend(item_issues)
      if item is not None:
        items.append(item)
    return items, issues

  raw_item = dict(base_raw)
  raw_item["event_id"] = event_id
  item, item_issues = _normalize_update_item(raw_item, step_id, timezone_name, 0)
  issues.extend(item_issues)
  if item is not None:
    items.append(item)
  return items, issues


def _normalize_update_args(args: Dict[str, Any], step_id: str,
                           timezone_name: str) -> Tuple[Dict[str, Any], List[ValidationIssue]]:
  normalized: Dict[str, Any] = {}
  issues: List[ValidationIssue] = []

  items_raw = args.get("items")
  if items_raw is not None and not isinstance(items_raw, list):
    issues.append(
        _issue(step_id,
               "invalid_value",
               "items must be an array when provided.",
               "items"))
    items_raw = None

  normalized_items: List[Dict[str, Any]] = []
  if isinstance(items_raw, list) and items_raw:
    normalized_items, item_issues = _normalize_update_items(items_raw, step_id, timezone_name)
    issues.extend(item_issues)
    root_event_id = _clean_str(args.get("event_id"))
    root_event_ids = _normalize_event_ids(args.get("event_ids"))
    if root_event_id:
      if root_event_ids is None:
        root_event_ids = [root_event_id]
      elif root_event_id not in root_event_ids:
        root_event_ids.insert(0, root_event_id)
    if root_event_ids:
      missing_indices = [
          index for index, item in enumerate(normalized_items)
          if not _clean_str(item.get("event_id"))
      ]
      if len(missing_indices) == 1 and len(root_event_ids) == 1:
        normalized_items[missing_indices[0]]["event_id"] = root_event_ids[0]
      elif len(missing_indices) == len(root_event_ids):
        for missing_index, mapped_id in zip(missing_indices, root_event_ids):
          normalized_items[missing_index]["event_id"] = mapped_id
  else:
    normalized_items, item_issues = _fallback_update_items_from_legacy_fields(
        args, step_id, timezone_name)
    issues.extend(item_issues)

  if not normalized_items:
    issues.append(
        _issue(step_id,
               "missing_slot",
               "items is required for calendar.update_event.",
               "items"))
    normalized["items"] = []
    return normalized, issues

  normalized["items"] = normalized_items
  resolved_ids = [_clean_str(item.get("event_id")) for item in normalized_items]
  if len(normalized_items) == 1:
    if resolved_ids and resolved_ids[0]:
      normalized["event_id"] = resolved_ids[0]
  else:
    if resolved_ids and all(resolved_ids):
      normalized["event_ids"] = [eid for eid in resolved_ids if eid]

  return normalized, issues


# ---------------------------------------------------------------------------
#  task item normalization
# ---------------------------------------------------------------------------

def _normalize_task_ids(raw: Any) -> Optional[List[str]]:
  if raw is None:
    return None
  out: List[str] = []
  seen: set[str] = set()
  values = raw if isinstance(raw, list) else [raw]
  for item in values:
    for text in _split_selection_tokens(item):
      if text in seen:
        continue
      seen.add(text)
      out.append(text)
  return out if out else None


def _infer_task_create_item_type(raw: Dict[str, Any]) -> str:
  raw_type = _clean_str(raw.get("type"))
  if raw_type:
    lowered = raw_type.lower()
    if lowered in ("single", "recurring"):
      return lowered
  if any(raw.get(key) is not None for key in ("recurrence", "rrule", "start_date", "time")):
    return "recurring"
  return "single"


def _normalize_task_create_item(raw: Dict[str, Any],
                                step_id: str,
                                timezone_name: str,
                                index: int) -> Tuple[Optional[Dict[str, Any]], List[ValidationIssue]]:
  item_raw = _filter_allowed_args(raw, _TASK_CREATE_ITEM_ALLOWED_KEYS)
  issues: List[ValidationIssue] = []
  raw_type = _clean_str(item_raw.get("type"))
  item_type = _infer_task_create_item_type(item_raw)
  if raw_type and raw_type.lower() not in ("single", "recurring"):
    issues.append(
        _issue(step_id,
               "invalid_value",
               f"items[{index}].type must be single or recurring.",
               "items"))

  title = _clean_str(item_raw.get("title"))
  if not title:
    issues.append(
        _issue(step_id,
               "missing_slot",
               f"items[{index}].title is required.",
               "items"))

  notes = _clean_str(item_raw.get("notes"))
  due = coerce_rfc3339(item_raw.get("due"), timezone_name)
  if item_raw.get("due") is not None and due is None:
    issues.append(
        _issue(step_id,
               "invalid_value",
               f"items[{index}].due must be RFC3339.",
               "items"))

  if item_type == "single":
    return {
        "type": "single",
        "title": title,
        "notes": notes,
        "due": due,
    }, issues

  start_date_raw = _clean_str(item_raw.get("start_date"))
  start_date = start_date_raw if start_date_raw and try_parse_date(start_date_raw) else None
  if start_date is None and isinstance(due, str) and len(due) >= 10:
    derived = due[:10]
    if try_parse_date(derived):
      start_date = derived
  if not start_date:
    issues.append(
        _issue(step_id,
               "missing_slot",
               f"items[{index}].start_date is required for recurring tasks.",
               "items"))

  time_value = _normalize_time_hhmm(item_raw.get("time"))
  if item_raw.get("time") is not None and not time_value:
    issues.append(
        _issue(step_id,
               "invalid_value",
               f"items[{index}].time must be HH:MM.",
               "items"))

  recurrence_raw = item_raw.get("recurrence")
  recurrence = _normalize_recurrence_dict(
      recurrence_raw) if isinstance(recurrence_raw, dict) else None
  rrule = _normalize_rrule_core(item_raw.get("rrule"))
  if recurrence_raw is not None and recurrence is None:
    issues.append(
        _issue(step_id,
               "invalid_value",
               f"items[{index}].recurrence payload is invalid.",
               "items"))
  if item_raw.get("rrule") is not None and rrule is None:
    issues.append(
        _issue(step_id,
               "invalid_value",
               f"items[{index}].rrule is invalid.",
               "items"))
  if recurrence is None and rrule is None:
    issues.append(
        _issue(step_id,
               "missing_slot",
               f"items[{index}].rrule is required for recurring tasks.",
               "items"))

  item: Dict[str, Any] = {
      "type": "recurring",
      "title": title,
      "notes": notes,
      "start_date": start_date,
      "time": time_value,
      "recurrence": recurrence,
      "rrule": rrule,
  }
  if due is not None:
    item["due"] = due
  return item, issues


def _normalize_task_create_args(
    args: Dict[str, Any],
    step_id: str,
    timezone_name: str,
) -> Tuple[Dict[str, Any], List[ValidationIssue]]:
  normalized: Dict[str, Any] = {}
  issues: List[ValidationIssue] = []

  items_raw = args.get("items")
  if items_raw is not None and not isinstance(items_raw, list):
    issues.append(
        _issue(step_id,
               "invalid_value",
               "items must be an array when provided.",
               "items"))
    items_raw = None

  normalized_items: List[Dict[str, Any]] = []
  if isinstance(items_raw, list) and items_raw:
    for index, raw in enumerate(items_raw):
      if not isinstance(raw, dict):
        issues.append(
            _issue(step_id,
                   "invalid_value",
                   f"items[{index}] must be an object.",
                   "items"))
        continue
      item, item_issues = _normalize_task_create_item(raw, step_id, timezone_name, index)
      issues.extend(item_issues)
      if item is not None:
        normalized_items.append(item)
  else:
    recurring_signal = any(args.get(key) is not None for key in (
        "recurrence",
        "rrule",
        "start_date",
        "time",
    ))
    fallback_raw = {
        "type": "recurring" if recurring_signal else "single",
        "title": args.get("title"),
        "notes": args.get("notes"),
        "due": args.get("due"),
        "start_date": args.get("start_date"),
        "time": args.get("time"),
        "recurrence": args.get("recurrence"),
        "rrule": args.get("rrule"),
    }
    item, item_issues = _normalize_task_create_item(
        fallback_raw, step_id, timezone_name, 0)
    issues.extend(item_issues)
    if item is not None:
      normalized_items.append(item)

  if not normalized_items:
    issues.append(
        _issue(step_id,
               "missing_slot",
               "items is required for task.create_task.",
               "items"))
    normalized["items"] = []
    return normalized, issues

  normalized["items"] = normalized_items
  if len(normalized_items) == 1:
    only = normalized_items[0]
    normalized["title"] = only.get("title")
    normalized["notes"] = only.get("notes")
    if only.get("type") == "single":
      normalized["due"] = only.get("due")
    else:
      normalized["start_date"] = only.get("start_date")
      normalized["time"] = only.get("time")
      normalized["rrule"] = only.get("rrule")
      normalized["recurrence"] = only.get("recurrence")
  return normalized, issues


def _normalize_task_update_item(
    raw: Dict[str, Any],
    step_id: str,
    timezone_name: str,
    index: int,
) -> Tuple[Optional[Dict[str, Any]], List[ValidationIssue]]:
  item_raw = _filter_allowed_args(raw, _TASK_UPDATE_ITEM_ALLOWED_KEYS)
  issues: List[ValidationIssue] = []

  due = coerce_rfc3339(item_raw.get("due"), timezone_name)
  if item_raw.get("due") is not None and due is None:
    issues.append(
        _issue(step_id,
               "invalid_value",
               f"items[{index}].due must be RFC3339.",
               "items"))

  status_val = _clean_str(item_raw.get("status"))
  if status_val and status_val not in ("needsAction", "completed"):
    issues.append(
        _issue(step_id,
               "invalid_value",
               f"items[{index}].status must be needsAction or completed.",
               "items"))

  item = {
      "task_id": _clean_str(item_raw.get("task_id")),
      "title": _clean_str(item_raw.get("title")),
      "notes": _clean_str(item_raw.get("notes")),
      "due": due,
      "status": status_val,
  }

  raw_patch_provided = any(item_raw.get(key) is not None for key in _TASK_UPDATE_PATCH_KEYS)
  if not _has_patch_fields(item, _TASK_UPDATE_PATCH_KEYS) and not raw_patch_provided:
    issues.append(
        _issue(step_id,
               "missing_slot",
               f"No task fields were provided for update at items[{index}].",
               "patch_fields"))
  return item, issues


def _fallback_task_update_items_from_legacy_fields(
    args: Dict[str, Any],
    step_id: str,
    timezone_name: str,
) -> Tuple[List[Dict[str, Any]], List[ValidationIssue]]:
  issues: List[ValidationIssue] = []
  items: List[Dict[str, Any]] = []

  task_id = _clean_str(args.get("task_id"))
  task_ids = _normalize_task_ids(args.get("task_ids"))
  if task_id:
    if task_ids is None:
      task_ids = [task_id]
    elif task_id not in task_ids:
      task_ids.insert(0, task_id)

  base_raw = {
      "title": args.get("title"),
      "notes": args.get("notes"),
      "due": args.get("due"),
      "status": args.get("status"),
  }
  if task_ids:
    for index, tid in enumerate(task_ids):
      raw_item = dict(base_raw)
      raw_item["task_id"] = tid
      item, item_issues = _normalize_task_update_item(raw_item, step_id, timezone_name, index)
      issues.extend(item_issues)
      if item is not None:
        items.append(item)
    return items, issues

  raw_item = dict(base_raw)
  raw_item["task_id"] = task_id
  item, item_issues = _normalize_task_update_item(raw_item, step_id, timezone_name, 0)
  issues.extend(item_issues)
  if item is not None:
    items.append(item)
  return items, issues


def _finalize_task_args_from_items(args: Dict[str, Any],
                                   items: List[Dict[str, Any]]) -> Dict[str, Any]:
  args["items"] = items
  resolved_ids = [_clean_str(item.get("task_id")) for item in items]
  if len(items) == 1:
    only_id = resolved_ids[0] if resolved_ids else None
    if only_id:
      args["task_id"] = only_id
    else:
      args.pop("task_id", None)
    args.pop("task_ids", None)
    return args

  args.pop("task_id", None)
  if resolved_ids and all(resolved_ids):
    args["task_ids"] = [task_id for task_id in resolved_ids if task_id]
  else:
    args.pop("task_ids", None)
  return args


def _normalize_task_update_args(
    args: Dict[str, Any],
    step_id: str,
    timezone_name: str,
) -> Tuple[Dict[str, Any], List[ValidationIssue]]:
  normalized: Dict[str, Any] = {}
  issues: List[ValidationIssue] = []

  items_raw = args.get("items")
  if items_raw is not None and not isinstance(items_raw, list):
    issues.append(
        _issue(step_id,
               "invalid_value",
               "items must be an array when provided.",
               "items"))
    items_raw = None

  normalized_items: List[Dict[str, Any]] = []
  if isinstance(items_raw, list) and items_raw:
    for index, raw in enumerate(items_raw):
      if not isinstance(raw, dict):
        issues.append(
            _issue(step_id,
                   "invalid_value",
                   f"items[{index}] must be an object.",
                   "items"))
        continue
      item, item_issues = _normalize_task_update_item(raw, step_id, timezone_name, index)
      issues.extend(item_issues)
      if item is not None:
        normalized_items.append(item)

    root_task_id = _clean_str(args.get("task_id"))
    root_task_ids = _normalize_task_ids(args.get("task_ids"))
    if root_task_id:
      if root_task_ids is None:
        root_task_ids = [root_task_id]
      elif root_task_id not in root_task_ids:
        root_task_ids.insert(0, root_task_id)
    if root_task_ids:
      missing_indices = [
          index for index, item in enumerate(normalized_items)
          if not _clean_str(item.get("task_id"))
      ]
      if len(missing_indices) == 1 and len(root_task_ids) == 1:
        normalized_items[missing_indices[0]]["task_id"] = root_task_ids[0]
      elif len(missing_indices) == len(root_task_ids):
        for missing_index, mapped_id in zip(missing_indices, root_task_ids):
          normalized_items[missing_index]["task_id"] = mapped_id
  else:
    normalized_items, item_issues = _fallback_task_update_items_from_legacy_fields(
        args, step_id, timezone_name)
    issues.extend(item_issues)

  if not normalized_items:
    issues.append(
        _issue(step_id,
               "missing_slot",
               "items is required for task.update_task.",
               "items"))
    normalized["items"] = []
    return normalized, issues

  normalized = _finalize_task_args_from_items(normalized, normalized_items)
  return normalized, issues


def _normalize_task_target_item(
    raw: Dict[str, Any],
    step_id: str,
    index: int,
) -> Tuple[Optional[Dict[str, Any]], List[ValidationIssue]]:
  item_raw = _filter_allowed_args(raw, _TASK_TARGET_ITEM_ALLOWED_KEYS)
  task_id = _clean_str(item_raw.get("task_id"))
  title = _clean_str(item_raw.get("title"))
  issues: List[ValidationIssue] = []
  if not task_id and not title:
    issues.append(
        _issue(step_id,
               "missing_slot",
               f"items[{index}] requires task_id or title.",
               "items"))
  return {"task_id": task_id, "title": title}, issues


def _normalize_task_target_args(
    args: Dict[str, Any],
    step_id: str,
    intent: str,
) -> Tuple[Dict[str, Any], List[ValidationIssue]]:
  normalized: Dict[str, Any] = {}
  issues: List[ValidationIssue] = []

  items_raw = args.get("items")
  if items_raw is not None and not isinstance(items_raw, list):
    issues.append(
        _issue(step_id,
               "invalid_value",
               "items must be an array when provided.",
               "items"))
    items_raw = None

  normalized_items: List[Dict[str, Any]] = []
  if isinstance(items_raw, list) and items_raw:
    for index, raw in enumerate(items_raw):
      if not isinstance(raw, dict):
        issues.append(
            _issue(step_id,
                   "invalid_value",
                   f"items[{index}] must be an object.",
                   "items"))
        continue
      item, item_issues = _normalize_task_target_item(raw, step_id, index)
      issues.extend(item_issues)
      if item is not None:
        normalized_items.append(item)
  else:
    task_id = _clean_str(args.get("task_id"))
    task_ids = _normalize_task_ids(args.get("task_ids"))
    title = _clean_str(args.get("title"))
    if task_id:
      if task_ids is None:
        task_ids = [task_id]
      elif task_id not in task_ids:
        task_ids.insert(0, task_id)
    if task_ids:
      for task_id_item in task_ids:
        normalized_items.append({"task_id": task_id_item, "title": None})
    elif title:
      normalized_items.append({"task_id": None, "title": title})

  if not normalized_items:
    issues.append(
        _issue(step_id,
               "missing_slot",
               f"items is required for {intent}.",
               "items"))
    normalized["items"] = []
    return normalized, issues

  normalized = _finalize_task_args_from_items(normalized, normalized_items)
  if len(normalized_items) == 1:
    normalized["title"] = normalized_items[0].get("title")
  return normalized, issues


# ---------------------------------------------------------------------------
#  RRULE heuristics
# ---------------------------------------------------------------------------

def _extract_rrule_cores_from_text(text: str) -> List[str]:
  if not isinstance(text, str):
    return []
  seen: set[str] = set()
  cores: List[str] = []
  for match in _RRULE_CANDIDATE_RE.finditer(text):
    raw_core = match.group(1) or ""
    normalized = _normalize_rrule_core(raw_core)
    if not normalized or normalized in seen:
      continue
    seen.add(normalized)
    cores.append(normalized)
  return cores


def _build_recurring_item_from_legacy_args(args: Dict[str, Any],
                                           rrule_core: str,
                                           timezone_name: str) -> Optional[Dict[str, Any]]:
  title = _clean_str(args.get("title"))
  if not title:
    return None

  start = coerce_iso_minute(args.get("start"), timezone_name)
  end = coerce_iso_minute(args.get("end"), timezone_name)
  start_dt: Optional[datetime] = _parse_iso_minute_safe(start) if start else None
  end_dt: Optional[datetime] = _parse_iso_minute_safe(end) if end else None

  start_date = _clean_str(args.get("start_date"))
  if not start_date and start_dt is not None:
    start_date = start_dt.strftime("%Y-%m-%d")
  if not start_date or try_parse_date(start_date) is None:
    return None

  time_value = _normalize_time_hhmm(args.get("time"))
  if not time_value and start_dt is not None:
    time_value = start_dt.strftime("%H:%M")

  duration_value = _clean_int(args.get("duration_minutes"))
  if (duration_value is None or duration_value <= 0) and start_dt and end_dt and end_dt > start_dt:
    duration_value = int((end_dt - start_dt).total_seconds() // 60)
  if duration_value is not None and duration_value <= 0:
    duration_value = None

  return {
      "type": "recurring",
      "title": title,
      "start_date": start_date,
      "time": time_value,
      "duration_minutes": duration_value,
      "location": _clean_str(args.get("location")),
      "description": _clean_str(args.get("description")),
      "reminders": _normalize_reminders(args.get("reminders")),
      "all_day": bool(args.get("all_day")) if isinstance(args.get("all_day"), bool) else None,
      "rrule": rrule_core,
  }


def _apply_rrule_create_heuristics(plan: List[PlanStep],
                                   input_as_text: str,
                                   timezone_name: str) -> List[PlanStep]:
  rrule_cores = _extract_rrule_cores_from_text(input_as_text)
  if not rrule_cores:
    return plan

  create_indices: List[int] = []
  for idx, step in enumerate(plan):
    if step.intent != "calendar.create_event":
      continue
    create_indices.append(idx)
  if not create_indices:
    return plan

  assignment: Dict[int, str] = {}
  if len(rrule_cores) == 1:
    assignment[create_indices[0]] = rrule_cores[0]
  else:
    for idx, plan_index in enumerate(create_indices):
      if idx >= len(rrule_cores):
        break
      assignment[plan_index] = rrule_cores[idx]

  normalized: List[PlanStep] = []
  for idx, step in enumerate(plan):
    assigned_rrule = assignment.get(idx)
    if step.intent != "calendar.create_event" or not assigned_rrule:
      normalized.append(step)
      continue
    args = step.args_dict()

    existing_items = args.get("items")
    has_recurring_item = False
    if isinstance(existing_items, list):
      for item in existing_items:
        if not isinstance(item, dict):
          continue
        item_type = _clean_str(item.get("type"))
        if item_type and item_type.lower() == "recurring":
          has_recurring_item = True
          break
        if item.get("recurrence") is not None or item.get("rrule") is not None:
          has_recurring_item = True
          break
    if has_recurring_item:
      normalized.append(step)
      continue

    recurring_item = _build_recurring_item_from_legacy_args(
        args, assigned_rrule, timezone_name)
    if recurring_item is None:
      normalized.append(step)
      continue

    patched_args = dict(args)
    patched_args["items"] = [recurring_item]
    normalized.append(_build_step(step, patched_args))
  return normalized


def apply_rrule_heuristics(plan: List[PlanStep],
                           input_as_text: str,
                           timezone_name: str) -> List[PlanStep]:
  """Public wrapper for RRULE heuristics."""
  return _apply_rrule_create_heuristics(plan, input_as_text, timezone_name)


# ---------------------------------------------------------------------------
#  Event / Task resolution helpers
# ---------------------------------------------------------------------------

def _event_key(event: Dict[str, Any]) -> Optional[str]:
  raw_id = event.get("id")
  if not raw_id:
    return None
  calendar_id = event.get("calendar_id")
  if calendar_id:
    return f"{calendar_id}::{raw_id}"
  return str(raw_id)


def _build_event_id_alias(events: List[Dict[str, Any]],
                          limit: int = EVENT_TARGET_CANDIDATE_LIMIT
                          ) -> Tuple[Dict[str, str], int]:
  alias: Dict[str, str] = {}
  natural_index = 0
  for event in events[:limit]:
    if not isinstance(event, dict):
      continue
    real_event_id = _event_key(event)
    if not real_event_id:
      continue
    natural_index += 1
    natural_id = str(natural_index)
    alias[real_event_id] = real_event_id
    alias[natural_id] = real_event_id
    if "::" in real_event_id:
      tail = real_event_id.split("::", 1)[1]
      if tail and tail not in alias:
        alias[tail] = real_event_id
  return alias, natural_index


def _build_task_id_alias(tasks: List[Dict[str, Any]],
                         limit: int = SLOT_EXTRACTOR_CONTEXT_TASK_LIMIT
                         ) -> Tuple[Dict[str, str], int]:
  alias: Dict[str, str] = {}
  natural_index = 0
  for task in tasks[:limit]:
    if not isinstance(task, dict):
      continue
    real_task_id = _clean_str(task.get("id"))
    if not real_task_id:
      continue
    natural_index += 1
    natural_id = str(natural_index)
    alias[real_task_id] = real_task_id
    alias[natural_id] = real_task_id
  return alias, natural_index


def _expand_numeric_token(token: str, max_index: int) -> List[str]:
  if max_index <= 0:
    return []
  simple = _clean_int(token)
  if simple is not None:
    if 1 <= simple <= max_index:
      return [str(simple)]
    return []

  match = _NUMERIC_RANGE_RE.match(token.strip())
  if not match:
    return []
  start = _clean_int(match.group(1))
  end = _clean_int(match.group(2))
  if start is None or end is None:
    return []
  if start > end:
    start, end = end, start
  start = max(1, start)
  end = min(max_index, end)
  if start > end:
    return []
  return [str(index) for index in range(start, end + 1)]


def _resolve_event_selection_values(
    single_value: Any,
    multi_value: Any,
    alias: Dict[str, str],
    max_index: int,
) -> Tuple[List[str], List[str]]:
  tokens: List[str] = []
  tokens.extend(_split_selection_tokens(single_value))
  if isinstance(multi_value, list):
    for item in multi_value:
      tokens.extend(_split_selection_tokens(item))
  else:
    tokens.extend(_split_selection_tokens(multi_value))

  resolved: List[str] = []
  unresolved: List[str] = []
  seen: set[str] = set()

  for token in tokens:
    expanded_numeric = _expand_numeric_token(token, max_index)
    candidate_tokens = expanded_numeric if expanded_numeric else [token]
    mapped_any = False
    for candidate in candidate_tokens:
      mapped = alias.get(candidate)
      if not mapped:
        continue
      mapped_any = True
      if mapped in seen:
        continue
      seen.add(mapped)
      resolved.append(mapped)
    if not mapped_any:
      unresolved.append(token)

  return resolved, unresolved


def _resolve_task_selection_values(
    single_value: Any,
    multi_value: Any,
    alias: Dict[str, str],
    max_index: int,
) -> Tuple[List[str], List[str]]:
  return _resolve_event_selection_values(
      single_value,
      multi_value,
      alias=alias,
      max_index=max_index,
  )


def _event_candidates_by_title(title: str,
                               events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
  needle = title.strip().lower()
  if not needle:
    return []

  matched: List[Dict[str, Any]] = []
  for event in events:
    value = str(event.get("title") or "").strip().lower()
    if value and value == needle:
      matched.append(event)
  matched.sort(key=lambda x: x.get("start") or "")
  return matched


def _task_candidates_by_title(title: str,
                              tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
  needle = title.strip().lower()
  if not needle:
    return []

  matched: List[Dict[str, Any]] = []
  for task in tasks:
    value = str(task.get("title") or "").strip().lower()
    if value and value == needle:
      matched.append(task)
  return matched


def _task_candidate_preview(tasks: List[Dict[str, Any]],
                            limit: int = 5) -> List[Dict[str, Any]]:
  sample: List[Dict[str, Any]] = []
  for task in tasks[:limit]:
    if not isinstance(task, dict):
      continue
    sample.append({
        "task_id": task.get("id"),
        "title": task.get("title"),
        "status": task.get("status"),
        "due": task.get("due"),
    })
  return sample


def _issue(step_id: str,
           code: str,
           detail: str,
           slot: Optional[str] = None,
           candidates: Optional[List[Dict[str, Any]]] = None) -> ValidationIssue:
  return ValidationIssue(step_id=step_id,
                         code=code,
                         slot=slot,
                         detail=detail,
                         candidates=candidates or [])


def _resolve_event_target(step: PlanStep, events: List[Dict[str, Any]]
                          ) -> Tuple[Optional[str], Optional[ValidationIssue]]:
  args = step.args_dict()
  event_id = _clean_str(args.get("event_id"))
  if event_id:
    return event_id, None

  if step.intent == "calendar.cancel_event":
    title = _clean_str(args.get("title"))
    if title:
      candidates = _event_candidates_by_title(title, events)
      if len(candidates) == 1:
        resolved = _event_key(candidates[0])
        return resolved, None
      if len(candidates) > 1:
        sample = [{
            "event_id": _event_key(item),
            "title": item.get("title"),
            "start": item.get("start"),
        } for item in candidates[:5]]
        return None, _issue(step.step_id,
                            "ambiguous_reference",
                            "Multiple event candidates were found.",
                            slot="event_id",
                            candidates=sample)

  return None, _issue(step.step_id, "missing_slot", "Target event identifier is required.",
                      "event_id")


def _resolve_task_target(step: PlanStep, tasks: List[Dict[str, Any]]
                         ) -> Tuple[Optional[str], Optional[ValidationIssue]]:
  args = step.args_dict()
  task_id = _clean_str(args.get("task_id"))
  if task_id:
    return task_id, None

  if step.intent == "task.cancel_task":
    title = _clean_str(args.get("title"))
    if title:
      candidates = _task_candidates_by_title(title, tasks)
      if len(candidates) == 1:
        resolved = _clean_str(candidates[0].get("id"))
        return resolved, None
      if len(candidates) > 1:
        sample = [{
            "task_id": item.get("id"),
            "title": item.get("title"),
            "status": item.get("status"),
        } for item in candidates[:5]]
        return None, _issue(step.step_id,
                            "ambiguous_reference",
                            "Multiple task candidates were found.",
                            slot="task_id",
                            candidates=sample)

  return None, _issue(step.step_id, "missing_slot", "Target task identifier is required.",
                      "task_id")


# ---------------------------------------------------------------------------
#  cancel_event helpers
# ---------------------------------------------------------------------------


def _normalize_event_ids(raw: Any) -> Optional[List[str]]:
  if raw is None:
    return None
  seen: set[str] = set()
  out: List[str] = []
  values = raw if isinstance(raw, list) else [raw]
  for item in values:
    for text in _split_selection_tokens(item):
      if text in seen:
        continue
      seen.add(text)
      out.append(text)
  return out if out else None


def _normalize_cancel_args(
    args: Dict[str, Any],
    step_id: str,
    intent_by_step_id: Dict[str, str],
    step: "PlanStep",
) -> Tuple[Dict[str, Any], List[ValidationIssue]]:
  issues: List[ValidationIssue] = []

  event_id = _clean_str(args.get("event_id"))
  event_ids = _normalize_event_ids(args.get("event_ids"))
  if args.get("cancel_ranges") is not None:
    issues.append(
        _issue(step_id,
               "invalid_value",
               "cancel_ranges is no longer supported. Use event_id/event_ids only.",
               "cancel_ranges"))
  if args.get("start_date") is not None or args.get("end_date") is not None:
    issues.append(
        _issue(step_id,
               "invalid_value",
               "start_date/end_date range cancel is no longer supported.",
               "start_date"))

  if event_id:
    if event_ids is None:
      event_ids = [event_id]
    elif event_id not in event_ids:
      event_ids.insert(0, event_id)

  clean: Dict[str, Any] = {}
  if event_ids:
    if len(event_ids) == 1:
      clean["event_id"] = event_ids[0]
    else:
      clean["event_ids"] = event_ids

  dep_create = _depends_on_intent(step, intent_by_step_id, "calendar.create_event")
  if not clean and not dep_create:
    pass

  return clean, issues


def _cancel_needs_context(args: Dict[str, Any]) -> bool:
  return True


def _normalize_cancel_args_with_context(
    args: Dict[str, Any],
    step: "PlanStep",
    events: List[Dict[str, Any]],
    candidate_id_alias: Optional[Dict[str, str]] = None,
    candidate_max_index: int = 0,
) -> Tuple[Dict[str, Any], List[ValidationIssue]]:
  issues: List[ValidationIssue] = []
  alias = candidate_id_alias or {}

  resolved_ids, unresolved = _resolve_event_selection_values(
      args.get("event_id"),
      args.get("event_ids"),
      alias=alias,
      max_index=candidate_max_index,
  )
  if unresolved:
    unresolved_text = ", ".join(unresolved[:5])
    issues.append(
        _issue(step.step_id,
               "invalid_value",
               f"Unknown event selection: {unresolved_text}",
               "event_id",
               _event_candidate_preview(events)))

  if len(resolved_ids) == 1:
    args["event_id"] = resolved_ids[0]
    args.pop("event_ids", None)
  elif len(resolved_ids) > 1:
    args["event_ids"] = resolved_ids
    args.pop("event_id", None)
  else:
    args.pop("event_id", None)
    args.pop("event_ids", None)

  has_ids = bool(resolved_ids)
  if not has_ids:
    resolved_id, event_issue = _resolve_event_target(step, events)
    if resolved_id:
      args["event_id"] = resolved_id
    elif event_issue:
      issues.append(event_issue)

  return args, issues


def _resolve_single_update_item_target(
    step: "PlanStep",
    item: Dict[str, Any],
    events: List[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[ValidationIssue]]:
  event_id = _clean_str(item.get("event_id"))
  if event_id:
    return event_id, None

  title = _clean_str(item.get("title"))
  if title:
    candidates = _event_candidates_by_title(title, events)
    if len(candidates) == 1:
      resolved = _event_key(candidates[0])
      return resolved, None
    if len(candidates) > 1:
      sample = [{
          "event_id": _event_key(candidate),
          "title": candidate.get("title"),
          "start": candidate.get("start"),
      } for candidate in candidates[:5]]
      return None, _issue(step.step_id,
                          "ambiguous_reference",
                          "Multiple event candidates were found for the update.",
                          slot="event_id",
                          candidates=sample)

  return _resolve_event_target(step, events)


def _finalize_update_args_from_items(args: Dict[str, Any],
                                     items: List[Dict[str, Any]]) -> Dict[str, Any]:
  args["items"] = items
  resolved_ids = [_clean_str(item.get("event_id")) for item in items]
  if len(items) == 1:
    only_id = resolved_ids[0] if resolved_ids else None
    if only_id:
      args["event_id"] = only_id
    else:
      args.pop("event_id", None)
    args.pop("event_ids", None)
    return args

  args.pop("event_id", None)
  if resolved_ids and all(resolved_ids):
    args["event_ids"] = [event_id for event_id in resolved_ids if event_id]
  else:
    args.pop("event_ids", None)
  return args


def _normalize_update_args_with_context(
    args: Dict[str, Any],
    step: "PlanStep",
    events: List[Dict[str, Any]],
    candidate_id_alias: Optional[Dict[str, str]] = None,
    candidate_max_index: int = 0,
) -> Tuple[Dict[str, Any], List[ValidationIssue]]:
  issues: List[ValidationIssue] = []
  alias = candidate_id_alias or {}
  items_raw = args.get("items")
  if not isinstance(items_raw, list):
    issues.append(
        _issue(step.step_id,
               "missing_slot",
               "items is required for calendar.update_event.",
               "items"))
    args["items"] = []
    return args, issues

  items: List[Dict[str, Any]] = []
  for raw in items_raw:
    if isinstance(raw, dict):
      items.append(dict(raw))

  if not items:
    issues.append(
        _issue(step.step_id,
               "missing_slot",
               "items must include at least one update item.",
               "items"))
    args["items"] = []
    return args, issues

  for item in items:
    event_id = _clean_str(item.get("event_id"))
    if not event_id:
      continue
    resolved_ids, unresolved = _resolve_event_selection_values(
        event_id,
        None,
        alias=alias,
        max_index=candidate_max_index,
    )
    if unresolved:
      item.pop("event_id", None)
      continue
    if len(resolved_ids) > 1:
      item.pop("event_id", None)
      continue
    if len(resolved_ids) == 1:
      item["event_id"] = resolved_ids[0]
    else:
      item.pop("event_id", None)

  missing_indices = [
      index for index, item in enumerate(items)
      if not _clean_str(item.get("event_id"))
  ]
  if missing_indices:
    if len(items) == 1:
      resolved_id, event_issue = _resolve_single_update_item_target(step, items[0], events)
      if resolved_id:
        items[0]["event_id"] = resolved_id
      elif event_issue:
        issues.append(event_issue)
    else:
      for index in missing_indices:
        issues.append(
            _issue(step.step_id,
                   "missing_slot",
                   f"items[{index}].event_id is required for multi update.",
                   "items"))

  for index, item in enumerate(items):
    if not _has_patch_fields(item, _EVENT_UPDATE_PATCH_KEYS):
      issues.append(
          _issue(step.step_id,
                 "missing_slot",
                 f"No update fields were provided at items[{index}].",
                 "patch_fields"))

  args = _finalize_update_args_from_items(args, items)
  return args, issues


def _resolve_task_item_target(step: "PlanStep",
                              item: Dict[str, Any],
                              tasks: List[Dict[str, Any]],
                              *,
                              allow_title: bool) -> Tuple[Optional[str], Optional[ValidationIssue]]:
  task_id = _clean_str(item.get("task_id"))
  if task_id:
    return task_id, None

  if allow_title:
    title = _clean_str(item.get("title"))
    if title:
      candidates = _task_candidates_by_title(title, tasks)
      if len(candidates) == 1:
        return _clean_str(candidates[0].get("id")), None
      if len(candidates) > 1:
        return None, _issue(step.step_id,
                            "ambiguous_reference",
                            "Multiple task candidates were found.",
                            slot="task_id",
                            candidates=_task_candidate_preview(candidates))

  return None, _issue(step.step_id,
                      "missing_slot",
                      "Target task identifier is required.",
                      "task_id",
                      _task_candidate_preview(tasks))


def _normalize_task_update_args_with_context(
    args: Dict[str, Any],
    step: "PlanStep",
    tasks: List[Dict[str, Any]],
    candidate_id_alias: Optional[Dict[str, str]] = None,
    candidate_max_index: int = 0,
) -> Tuple[Dict[str, Any], List[ValidationIssue]]:
  issues: List[ValidationIssue] = []
  alias = candidate_id_alias or {}
  items_raw = args.get("items")
  if not isinstance(items_raw, list):
    issues.append(
        _issue(step.step_id,
               "missing_slot",
               "items is required for task.update_task.",
               "items"))
    args["items"] = []
    return args, issues

  items: List[Dict[str, Any]] = []
  for raw in items_raw:
    if isinstance(raw, dict):
      items.append(dict(raw))

  if not items:
    issues.append(
        _issue(step.step_id,
               "missing_slot",
               "items must include at least one update item.",
               "items"))
    args["items"] = []
    return args, issues

  root_task_ids, _ = _resolve_task_selection_values(
      args.get("task_id"),
      args.get("task_ids"),
      alias=alias,
      max_index=candidate_max_index,
  )

  for item in items:
    task_id = _clean_str(item.get("task_id"))
    if not task_id:
      continue
    resolved_ids, unresolved = _resolve_task_selection_values(
        task_id,
        None,
        alias=alias,
        max_index=candidate_max_index,
    )
    if unresolved:
      item.pop("task_id", None)
      continue
    if len(resolved_ids) > 1:
      item.pop("task_id", None)
      continue
    if len(resolved_ids) == 1:
      item["task_id"] = resolved_ids[0]
    else:
      item.pop("task_id", None)
  if root_task_ids:
    missing_indices = [
        index for index, item in enumerate(items)
        if not _clean_str(item.get("task_id"))
    ]
    if len(missing_indices) == 1 and len(root_task_ids) == 1:
      items[missing_indices[0]]["task_id"] = root_task_ids[0]
    elif len(missing_indices) == len(root_task_ids):
      for missing_index, mapped_id in zip(missing_indices, root_task_ids):
        items[missing_index]["task_id"] = mapped_id

  for index, item in enumerate(items):
    if _clean_str(item.get("task_id")):
      continue
    if len(items) == 1:
      resolved_id, task_issue = _resolve_task_item_target(
          step, item, tasks, allow_title=False)
      if resolved_id:
        item["task_id"] = resolved_id
      elif task_issue:
        issues.append(task_issue)
    else:
      issues.append(
          _issue(step.step_id,
                 "missing_slot",
                 f"items[{index}].task_id is required for multi update.",
                 "items",
                 _task_candidate_preview(tasks)))

  for index, item in enumerate(items):
    if not _has_patch_fields(item, _TASK_UPDATE_PATCH_KEYS):
      issues.append(
          _issue(step.step_id,
                 "missing_slot",
                 f"No task fields were provided for update at items[{index}].",
                 "patch_fields"))

  args = _finalize_task_args_from_items(args, items)
  return args, issues


def _normalize_task_target_args_with_context(
    args: Dict[str, Any],
    step: "PlanStep",
    tasks: List[Dict[str, Any]],
    candidate_id_alias: Optional[Dict[str, str]] = None,
    candidate_max_index: int = 0,
    *,
    allow_title: bool,
) -> Tuple[Dict[str, Any], List[ValidationIssue]]:
  issues: List[ValidationIssue] = []
  alias = candidate_id_alias or {}
  items_raw = args.get("items")
  if not isinstance(items_raw, list):
    issues.append(
        _issue(step.step_id,
               "missing_slot",
               f"items is required for {step.intent}.",
               "items"))
    args["items"] = []
    return args, issues

  items: List[Dict[str, Any]] = []
  for raw in items_raw:
    if isinstance(raw, dict):
      items.append(dict(raw))

  if not items:
    issues.append(
        _issue(step.step_id,
               "missing_slot",
               f"items must include at least one target for {step.intent}.",
               "items"))
    args["items"] = []
    return args, issues

  resolved_root_ids, unresolved_root = _resolve_task_selection_values(
      args.get("task_id"),
      args.get("task_ids"),
      alias=alias,
      max_index=candidate_max_index,
  )
  if unresolved_root:
    unresolved_text = ", ".join(unresolved_root[:5])
    issues.append(
        _issue(step.step_id,
               "invalid_value",
               f"Unknown task selection: {unresolved_text}",
               "task_id",
               _task_candidate_preview(tasks)))

  if resolved_root_ids:
    missing_indices = [
        index for index, item in enumerate(items)
        if not _clean_str(item.get("task_id"))
    ]
    if len(missing_indices) == 1 and len(resolved_root_ids) == 1:
      items[missing_indices[0]]["task_id"] = resolved_root_ids[0]
    elif len(missing_indices) == len(resolved_root_ids):
      for missing_index, mapped_id in zip(missing_indices, resolved_root_ids):
        items[missing_index]["task_id"] = mapped_id

  for index, item in enumerate(items):
    current_task_id = _clean_str(item.get("task_id"))
    if current_task_id:
      resolved_item_ids, unresolved_item = _resolve_task_selection_values(
          current_task_id,
          None,
          alias=alias,
          max_index=candidate_max_index,
      )
      if unresolved_item or len(resolved_item_ids) != 1:
        item.pop("task_id", None)
      else:
        item["task_id"] = resolved_item_ids[0]
    if _clean_str(item.get("task_id")):
      continue
    resolved_id, task_issue = _resolve_task_item_target(
        step, item, tasks, allow_title=allow_title)
    if resolved_id:
      item["task_id"] = resolved_id
      continue
    if task_issue is not None:
      if len(items) > 1 and task_issue.code == "missing_slot":
        issues.append(
            _issue(step.step_id,
                   "missing_slot",
                   f"items[{index}].task_id is required for multi target operations.",
                   "items",
                   _task_candidate_preview(tasks)))
      else:
        issues.append(task_issue)

  args = _finalize_task_args_from_items(args, items)
  return args, issues


def _normalize_start_end(args: Dict[str, Any],
                         timezone_name: str) -> Tuple[Optional[str], Optional[str]]:
  start = coerce_iso_minute(args.get("start"), timezone_name)
  end = coerce_iso_minute(args.get("end"), timezone_name)
  return start, end


# ---------------------------------------------------------------------------
#  Context payload helpers
# ---------------------------------------------------------------------------

def _compact_context_payload(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
  if not isinstance(context, dict):
    return {"scope": None, "events": [], "tasks": []}
  events = context.get("events") if isinstance(context.get("events"), list) else []
  tasks = context.get("tasks") if isinstance(context.get("tasks"), list) else []
  compact_events: List[Dict[str, Any]] = []
  natural_index = 0
  for event in events[:SLOT_EXTRACTOR_CONTEXT_EVENT_LIMIT]:
    if not isinstance(event, dict):
      continue
    real_event_id = _event_key(event)
    if not real_event_id:
      continue
    natural_index += 1
    compact_events.append({
        "event_id": str(natural_index),
        "title": event.get("title"),
        "start": event.get("start"),
        "end": event.get("end"),
        "calendar_id": event.get("calendar_id"),
    })
  compact_tasks: List[Dict[str, Any]] = []
  task_natural_index = 0
  for task in tasks[:SLOT_EXTRACTOR_CONTEXT_TASK_LIMIT]:
    if not isinstance(task, dict):
      continue
    real_task_id = _clean_str(task.get("id"))
    if not real_task_id:
      continue
    task_natural_index += 1
    compact_tasks.append({
        "task_id": str(task_natural_index),
        "title": task.get("title"),
        "status": task.get("status"),
        "due": task.get("due"),
    })
  return {
      "scope": context.get("scope") if isinstance(context.get("scope"), dict) else None,
      "events": compact_events,
      "tasks": compact_tasks,
  }


# ---------------------------------------------------------------------------
#  Per-intent slot extraction (each calls its own LLM)
# ---------------------------------------------------------------------------

def _build_extraction_debug(raw_output: str, llm_meta: Dict[str, Any],
                            intent: str,
                            payload: Optional[Dict[str, Any]] = None,
                            system_prompt: Optional[str] = None,
                            developer_prompt: Optional[str] = None,
                            extracted_confidence: Optional[float] = None) -> Dict[str, Any]:
  debug_payload = {
      "intent": intent,
      "model": str(llm_meta.get("model") or SLOT_EXTRACTOR_MODEL),
      "resolved_model": llm_meta.get("resolved_model"),
      "reasoning_effort": llm_meta.get("reasoning_effort"),
      "thinking_level": llm_meta.get("thinking_level"),
      "raw_output": raw_output,
      "llm_available": llm_meta.get("llm_available", True),
      "llm_error": llm_meta.get("llm_error"),
  }
  if isinstance(payload, dict):
    debug_payload["payload"] = payload
  if isinstance(system_prompt, str) and system_prompt:
    debug_payload["system_prompt"] = system_prompt
  if isinstance(developer_prompt, str) and developer_prompt:
    debug_payload["developer_prompt"] = developer_prompt
  if isinstance(extracted_confidence, (int, float)):
    debug_payload["extracted_confidence"] = float(extracted_confidence)
  return debug_payload


async def _extract_create_event(
    step: PlanStep,
    input_as_text: str,
    now_iso: str,
    timezone_name: str,
    language_code: str,
    context: Optional[Dict[str, Any]] = None,
    extract_hint: Optional[str] = None,
) -> Tuple[PlanStep, Dict[str, Any]]:
  payload: Dict[str, Any] = {
      "user_text": input_as_text,
      "now_iso": now_iso,
      "timezone": timezone_name,
      "language": language_code,
  }
  if extract_hint:
    payload["extract_hint"] = extract_hint
  if context and step.query_ranges:
    compact = _compact_context_payload(context)
    events = compact.get("events", [])
    if events:
      payload["events"] = events
  system_prompt = _CREATE_EVENT_SYSTEM.format(now_iso=now_iso, timezone=timezone_name)
  print(f"[SLOT_EXTRACTOR] Calling run_structured_completion for create_event, model={SLOT_EXTRACTOR_MODEL}", flush=True)
  parsed, raw_output, llm_meta = await run_structured_completion(
      model=SLOT_EXTRACTOR_MODEL,
      system_prompt=system_prompt,
      developer_prompt=_with_confidence_guidance(_CREATE_EVENT_DEV),
      user_payload=payload,
      response_model=CreateEventOutput,
      reasoning_effort=_SETTINGS["reasoning_effort"],
      gemini_thinking_level=_SETTINGS["gemini_thinking_level"],
      verbosity="low",
      max_completion_tokens=10000,
  )
  print(f"[SLOT_EXTRACTOR] Completed, parsed={parsed is not None}, llm_available={llm_meta.get('llm_available')}, llm_error={llm_meta.get('llm_error')}", flush=True)
  debug = _build_extraction_debug(
      raw_output,
      llm_meta,
      "calendar.create_event",
      payload=payload,
      system_prompt=system_prompt,
      developer_prompt=_with_confidence_guidance(_CREATE_EVENT_DEV),
      extracted_confidence=(parsed.confidence if parsed is not None else None),
  )
  if parsed is None:
    return step, debug
  items = [item.model_dump(exclude_none=True) for item in parsed.items]
  args = {"items": items} if items else {}
  return _build_step(step, args), debug


async def _extract_update_event(
    step: PlanStep,
    input_as_text: str,
    now_iso: str,
    timezone_name: str,
    language_code: str,
    context: Optional[Dict[str, Any]],
    extract_hint: Optional[str] = None,
) -> Tuple[PlanStep, Dict[str, Any]]:
  compact = _compact_context_payload(context)
  payload: Dict[str, Any] = {
      "user_text": input_as_text,
      "now_iso": now_iso,
      "timezone": timezone_name,
      "language": language_code,
      "events": compact.get("events", []),
  }
  if extract_hint:
    payload["extract_hint"] = extract_hint
  system_prompt = _UPDATE_EVENT_SYSTEM.format(now_iso=now_iso, timezone=timezone_name)
  parsed, raw_output, llm_meta = await run_structured_completion(
      model=SLOT_EXTRACTOR_MODEL,
      system_prompt=system_prompt,
      developer_prompt=_with_confidence_guidance(_UPDATE_EVENT_DEV),
      user_payload=payload,
      response_model=UpdateEventOutput,
      reasoning_effort=_SETTINGS["reasoning_effort"],
      gemini_thinking_level=_SETTINGS["gemini_thinking_level"],
      verbosity="low",
      max_completion_tokens=10000,
  )
  debug = _build_extraction_debug(
      raw_output,
      llm_meta,
      "calendar.update_event",
      payload=payload,
      system_prompt=system_prompt,
      developer_prompt=_with_confidence_guidance(_UPDATE_EVENT_DEV),
      extracted_confidence=(parsed.confidence if parsed is not None else None),
  )
  if parsed is None:
    return step, debug
  args = parsed.model_dump(exclude_none=True)
  args.pop("confidence", None)
  return _build_step(step, args), debug


async def _extract_cancel_event(
    step: PlanStep,
    input_as_text: str,
    now_iso: str,
    timezone_name: str,
    language_code: str,
    context: Optional[Dict[str, Any]],
    extract_hint: Optional[str] = None,
) -> Tuple[PlanStep, Dict[str, Any]]:
  compact = _compact_context_payload(context)
  payload: Dict[str, Any] = {
      "user_text": input_as_text,
      "now_iso": now_iso,
      "timezone": timezone_name,
      "language": language_code,
      "events": compact.get("events", []),
  }
  if extract_hint:
    payload["extract_hint"] = extract_hint
  system_prompt = _CANCEL_EVENT_SYSTEM.format(now_iso=now_iso, timezone=timezone_name)
  parsed, raw_output, llm_meta = await run_structured_completion(
      model=SLOT_EXTRACTOR_MODEL,
      system_prompt=system_prompt,
      developer_prompt=_with_confidence_guidance(_CANCEL_EVENT_DEV),
      user_payload=payload,
      response_model=CancelEventOutput,
      reasoning_effort=_SETTINGS["reasoning_effort"],
      gemini_thinking_level=_SETTINGS["gemini_thinking_level"],
      verbosity="low",
      max_completion_tokens=10000,
  )
  debug = _build_extraction_debug(
      raw_output,
      llm_meta,
      "calendar.cancel_event",
      payload=payload,
      system_prompt=system_prompt,
      developer_prompt=_with_confidence_guidance(_CANCEL_EVENT_DEV),
      extracted_confidence=(parsed.confidence if parsed is not None else None),
  )
  if parsed is None:
    return step, debug
  raw_args = parsed.model_dump(exclude_none=True)
  raw_args.pop("confidence", None)
  return _build_step(step, raw_args), debug


async def _extract_create_task(
    step: PlanStep,
    input_as_text: str,
    now_iso: str,
    timezone_name: str,
    language_code: str,
    extract_hint: Optional[str] = None,
) -> Tuple[PlanStep, Dict[str, Any]]:
  payload: Dict[str, Any] = {
      "user_text": input_as_text,
      "now_iso": now_iso,
      "timezone": timezone_name,
      "language": language_code,
  }
  if extract_hint:
    payload["extract_hint"] = extract_hint
  system_prompt = _CREATE_TASK_SYSTEM.format(now_iso=now_iso, timezone=timezone_name)
  parsed, raw_output, llm_meta = await run_structured_completion(
      model=SLOT_EXTRACTOR_MODEL,
      system_prompt=system_prompt,
      developer_prompt=_with_confidence_guidance(_CREATE_TASK_DEV),
      user_payload=payload,
      response_model=CreateTaskOutput,
      reasoning_effort=_SETTINGS["reasoning_effort"],
      gemini_thinking_level=_SETTINGS["gemini_thinking_level"],
      verbosity="low",
      max_completion_tokens=10000,
  )
  debug = _build_extraction_debug(
      raw_output,
      llm_meta,
      "task.create_task",
      payload=payload,
      system_prompt=system_prompt,
      developer_prompt=_with_confidence_guidance(_CREATE_TASK_DEV),
      extracted_confidence=(parsed.confidence if parsed is not None else None),
  )
  if parsed is None:
    return step, debug
  args = parsed.model_dump(exclude_none=True)
  args.pop("confidence", None)
  return _build_step(step, args), debug


async def _extract_update_task(
    step: PlanStep,
    input_as_text: str,
    now_iso: str,
    timezone_name: str,
    language_code: str,
    context: Optional[Dict[str, Any]],
    extract_hint: Optional[str] = None,
) -> Tuple[PlanStep, Dict[str, Any]]:
  compact = _compact_context_payload(context)
  payload: Dict[str, Any] = {
      "user_text": input_as_text,
      "now_iso": now_iso,
      "timezone": timezone_name,
      "language": language_code,
      "tasks": compact.get("tasks", []),
  }
  if extract_hint:
    payload["extract_hint"] = extract_hint
  system_prompt = _UPDATE_TASK_SYSTEM.format(now_iso=now_iso, timezone=timezone_name)
  parsed, raw_output, llm_meta = await run_structured_completion(
      model=SLOT_EXTRACTOR_MODEL,
      system_prompt=system_prompt,
      developer_prompt=_with_confidence_guidance(_UPDATE_TASK_DEV),
      user_payload=payload,
      response_model=UpdateTaskOutput,
      reasoning_effort=_SETTINGS["reasoning_effort"],
      gemini_thinking_level=_SETTINGS["gemini_thinking_level"],
      verbosity="low",
      max_completion_tokens=10000,
  )
  debug = _build_extraction_debug(
      raw_output,
      llm_meta,
      "task.update_task",
      payload=payload,
      system_prompt=system_prompt,
      developer_prompt=_with_confidence_guidance(_UPDATE_TASK_DEV),
      extracted_confidence=(parsed.confidence if parsed is not None else None),
  )
  if parsed is None:
    return step, debug
  args = parsed.model_dump(exclude_none=True)
  args.pop("confidence", None)
  return _build_step(step, args), debug


async def _extract_cancel_task(
    step: PlanStep,
    input_as_text: str,
    now_iso: str,
    timezone_name: str,
    language_code: str,
    context: Optional[Dict[str, Any]],
    extract_hint: Optional[str] = None,
) -> Tuple[PlanStep, Dict[str, Any]]:
  compact = _compact_context_payload(context)
  payload: Dict[str, Any] = {
      "user_text": input_as_text,
      "now_iso": now_iso,
      "timezone": timezone_name,
      "language": language_code,
      "tasks": compact.get("tasks", []),
  }
  if extract_hint:
    payload["extract_hint"] = extract_hint
  system_prompt = _CANCEL_TASK_SYSTEM.format(now_iso=now_iso, timezone=timezone_name)
  parsed, raw_output, llm_meta = await run_structured_completion(
      model=SLOT_EXTRACTOR_MODEL,
      system_prompt=system_prompt,
      developer_prompt=_with_confidence_guidance(_CANCEL_TASK_DEV),
      user_payload=payload,
      response_model=CancelTaskOutput,
      reasoning_effort=_SETTINGS["reasoning_effort"],
      gemini_thinking_level=_SETTINGS["gemini_thinking_level"],
      verbosity="low",
      max_completion_tokens=10000,
  )
  debug = _build_extraction_debug(
      raw_output,
      llm_meta,
      "task.cancel_task",
      payload=payload,
      system_prompt=system_prompt,
      developer_prompt=_with_confidence_guidance(_CANCEL_TASK_DEV),
      extracted_confidence=(parsed.confidence if parsed is not None else None),
  )
  if parsed is None:
    return step, debug
  args = parsed.model_dump(exclude_none=True)
  args.pop("confidence", None)
  return _build_step(step, args), debug


async def extract_step_args(
    step: PlanStep,
    input_as_text: str,
    now_iso: str,
    timezone_name: str,
    language_code: str,
    context: Optional[Dict[str, Any]] = None,
) -> Tuple[PlanStep, Dict[str, Any]]:
  """Extract args for a single plan step using intent-specific LLM."""
  intent = step.intent
  hint = step.extract_hint

  if intent == "calendar.create_event":
    return await _extract_create_event(step, input_as_text, now_iso, timezone_name, language_code, context, hint)
  elif intent == "calendar.update_event":
    return await _extract_update_event(step, input_as_text, now_iso, timezone_name, language_code, context, hint)
  elif intent == "calendar.cancel_event":
    return await _extract_cancel_event(step, input_as_text, now_iso, timezone_name, language_code, context, hint)
  elif intent == "task.create_task":
    return await _extract_create_task(step, input_as_text, now_iso, timezone_name, language_code, hint)
  elif intent == "task.update_task":
    return await _extract_update_task(step, input_as_text, now_iso, timezone_name, language_code, context, hint)
  elif intent == "task.cancel_task":
    return await _extract_cancel_task(step, input_as_text, now_iso, timezone_name, language_code, context, hint)
  else:
    # meta.clarify, meta.summarize — no extraction needed
    return step, {"skipped": True, "intent": intent}


# ---------------------------------------------------------------------------
#  Validation / normalization helpers
# ---------------------------------------------------------------------------

def _has_patch_fields(args: Dict[str, Any], keys: Tuple[str, ...]) -> bool:
  return any(args.get(key) is not None and args.get(key) != "" for key in keys)


def _filter_allowed_args(args: Dict[str, Any],
                         allowed_keys: Tuple[str, ...]) -> Dict[str, Any]:
  filtered: Dict[str, Any] = {}
  for key in allowed_keys:
    if key in args:
      filtered[key] = args.get(key)
  return filtered


def _build_step(step: PlanStep, args: Dict[str, Any]) -> PlanStep:
  return PlanStep(step_id=step.step_id,
                  intent=step.intent,
                  extract_hint=step.extract_hint,
                  args=args,
                  query_ranges=step.query_ranges,
                  depends_on=step.depends_on,
                  on_fail=step.on_fail)


def _depends_on_intent(step: PlanStep, intent_by_step_id: Dict[str, str],
                       target_intent: str) -> bool:
  for dep in step.depends_on:
    if intent_by_step_id.get(dep) == target_intent:
      return True
  return False


# ---------------------------------------------------------------------------
#  Pre-context validation
# ---------------------------------------------------------------------------

def validate_and_enrich_plan_pre_context(plan: List[PlanStep], timezone_name: str
                                         ) -> Tuple[List[PlanStep], List[ValidationIssue], bool]:
  issues: List[ValidationIssue] = []
  normalized: List[PlanStep] = []
  needs_context_lookup = False
  intent_by_step_id = {step.step_id: step.intent for step in plan}

  for step in plan:
    args = step.args_dict()

    if step.intent == "calendar.create_event":
      args = _filter_allowed_args(args, _EVENT_CREATE_ALLOWED_KEYS)
      args, create_issues = _normalize_create_args(args, step.step_id, timezone_name)
      issues.extend(create_issues)

    elif step.intent == "calendar.update_event":
      args = _filter_allowed_args(args, _EVENT_UPDATE_ALLOWED_KEYS)
      args, update_issues = _normalize_update_args(args, step.step_id, timezone_name)
      issues.extend(update_issues)
      needs_context_lookup = True

    elif step.intent == "calendar.cancel_event":
      args = _filter_allowed_args(args, _EVENT_CANCEL_ALLOWED_KEYS)
      args, cancel_issues = _normalize_cancel_args(args, step.step_id, intent_by_step_id, step)
      issues.extend(cancel_issues)
      if _cancel_needs_context(args):
        needs_context_lookup = True

    elif step.intent == "task.create_task":
      args = _filter_allowed_args(args, _TASK_CREATE_ALLOWED_KEYS)
      args, task_create_issues = _normalize_task_create_args(
          args, step.step_id, timezone_name)
      issues.extend(task_create_issues)

    elif step.intent == "task.update_task":
      args = _filter_allowed_args(args, _TASK_UPDATE_ALLOWED_KEYS)
      args, task_update_issues = _normalize_task_update_args(
          args, step.step_id, timezone_name)
      issues.extend(task_update_issues)
      # Always run context-stage validation for task IDs so numeric aliases
      # (e.g. "1", "2", "1~3") are resolved to real task IDs.
      needs_context_lookup = True

    elif step.intent == "task.cancel_task":
      args = _filter_allowed_args(args, _TASK_TARGET_ALLOWED_KEYS)
      args, cancel_task_issues = _normalize_task_target_args(
          args, step.step_id, step.intent)
      issues.extend(cancel_task_issues)
      # Always run context-stage validation for task IDs so numeric aliases
      # (e.g. "1", "2", "1~3") are resolved to real task IDs.
      needs_context_lookup = True

    elif step.intent == "meta.summarize":
      needs_context_lookup = True

    elif step.intent == "meta.clarify":
      pass

    normalized.append(_build_step(step, args))

  return normalized, issues, needs_context_lookup


# ---------------------------------------------------------------------------
#  Post-context validation
# ---------------------------------------------------------------------------

def validate_and_enrich_plan_with_context(plan: List[PlanStep], context: Dict[str, Any],
                                          timezone_name: str
                                          ) -> Tuple[List[PlanStep], List[ValidationIssue]]:
  events = context.get("events") if isinstance(context.get("events"), list) else []
  tasks = context.get("tasks") if isinstance(context.get("tasks"), list) else []
  candidate_id_alias, candidate_max_index = _build_event_id_alias(events)
  task_candidate_id_alias, task_candidate_max_index = _build_task_id_alias(tasks)

  issues: List[ValidationIssue] = []
  normalized: List[PlanStep] = []

  for step in plan:
    args = step.args_dict()

    if step.intent == "calendar.update_event":
      args = _filter_allowed_args(args, _EVENT_UPDATE_ALLOWED_KEYS)
      args, update_issues = _normalize_update_args(args, step.step_id, timezone_name)
      issues.extend(update_issues)
      args, context_issues = _normalize_update_args_with_context(
          args,
          step,
          events,
          candidate_id_alias=candidate_id_alias,
          candidate_max_index=candidate_max_index,
      )
      issues.extend(context_issues)

    elif step.intent == "calendar.cancel_event":
      args = _filter_allowed_args(args, _EVENT_CANCEL_ALLOWED_KEYS)
      args, cancel_issues = _normalize_cancel_args_with_context(
          args,
          step,
          events,
          candidate_id_alias=candidate_id_alias,
          candidate_max_index=candidate_max_index,
      )
      issues.extend(cancel_issues)

    elif step.intent == "task.update_task":
      args = _filter_allowed_args(args, _TASK_UPDATE_ALLOWED_KEYS)
      args, task_update_issues = _normalize_task_update_args(
          args, step.step_id, timezone_name)
      issues.extend(task_update_issues)
      args, context_issues = _normalize_task_update_args_with_context(
          args,
          step,
          tasks,
          candidate_id_alias=task_candidate_id_alias,
          candidate_max_index=task_candidate_max_index,
      )
      issues.extend(context_issues)

    elif step.intent == "task.cancel_task":
      args = _filter_allowed_args(args, _TASK_TARGET_ALLOWED_KEYS)
      args, cancel_task_issues = _normalize_task_target_args(args, step.step_id, step.intent)
      issues.extend(cancel_task_issues)
      args, context_issues = _normalize_task_target_args_with_context(
          args,
          step,
          tasks,
          candidate_id_alias=task_candidate_id_alias,
          candidate_max_index=task_candidate_max_index,
          allow_title=True,
      )
      issues.extend(context_issues)

    elif step.intent == "meta.summarize":
      pass

    normalized.append(_build_step(step, args))

  return normalized, issues


def _scope_range(context: Dict[str, Any]) -> Tuple[Optional[date], Optional[date]]:
  scope = context.get("scope")
  if not isinstance(scope, dict):
    return None, None
  start_date = try_parse_date(scope.get("start_date"))
  end_date = try_parse_date(scope.get("end_date"))
  return start_date, end_date


def _is_strictly_broader(previous_start: Optional[date], previous_end: Optional[date],
                         new_start: Optional[date], new_end: Optional[date]) -> bool:
  if previous_start is None or previous_end is None:
    return bool(new_start and new_end and new_end >= new_start)
  if new_start is None or new_end is None:
    return False
  if new_end < new_start:
    return False
  if new_start > previous_start or new_end < previous_end:
    return False
  return new_start < previous_start or new_end > previous_end


def _event_candidate_payload(events: List[Dict[str, Any]],
                             limit: int = EVENT_TARGET_CANDIDATE_LIMIT) -> List[Dict[str, Any]]:
  payload: List[Dict[str, Any]] = []
  for event in events[:limit]:
    if not isinstance(event, dict):
      continue
    event_id = _event_key(event)
    if not event_id:
      continue
    payload.append({
        "event_id": event_id,
        "title": event.get("title"),
        "start": event.get("start"),
        "end": event.get("end"),
        "calendar_id": event.get("calendar_id"),
    })
  return payload


def _event_candidate_preview(events: List[Dict[str, Any]],
                             limit: int = 5) -> List[Dict[str, Any]]:
  return _event_candidate_payload(events, limit=limit)


async def validate_and_enrich_plan_with_context_decision(
    plan: List[PlanStep],
    context: Dict[str, Any],
    timezone_name: str,
    input_as_text: str,
    now_iso: str,
    language_code: str = "en",
    decision_attempt: int = 0,
    max_resolver_calls: int = 1,
    debug_capture: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[PlanStep], List[ValidationIssue], Dict[str, Any]]:
  _ = language_code
  events = context.get("events") if isinstance(context.get("events"), list) else []
  tasks = context.get("tasks") if isinstance(context.get("tasks"), list) else []
  resolver_call_limit = max(0, int(max_resolver_calls))
  resolver_calls = 0

  issues: List[ValidationIssue] = []
  normalized: List[PlanStep] = []
  decision: Dict[str, Any] = {
      "action": "continue",
      "reason": None,
      "reason_codes": [],
      "start_date": None,
      "end_date": None,
      "step_id": None,
      "attempt": decision_attempt,
  }

  previous_start_date, previous_end_date = _scope_range(context)
  previous_start_iso = previous_start_date.isoformat() if previous_start_date else ""
  previous_end_iso = previous_end_date.isoformat() if previous_end_date else ""
  candidate_payload = _event_candidate_payload(events)
  candidate_id_alias, candidate_max_index = _build_event_id_alias(events)
  task_candidate_id_alias, task_candidate_max_index = _build_task_id_alias(tasks)

  for idx, step in enumerate(plan):
    args = step.args_dict()

    if step.intent == "calendar.update_event":
      args = _filter_allowed_args(args, _EVENT_UPDATE_ALLOWED_KEYS)
      args, update_issues = _normalize_update_args(args, step.step_id, timezone_name)
      issues.extend(update_issues)

      items_raw = args.get("items")
      items = [dict(item) for item in items_raw if isinstance(item, dict)] if isinstance(
          items_raw, list) else []
      if not items:
        issues.append(
            _issue(step.step_id,
                   "missing_slot",
                   "items must include at least one update item.",
                   "items"))
        args["items"] = []
      else:
        for item in items:
          event_id = _clean_str(item.get("event_id"))
          if not event_id:
            continue
          resolved_ids, unresolved = _resolve_event_selection_values(
              event_id,
              None,
              alias=candidate_id_alias,
              max_index=candidate_max_index,
          )
          if unresolved:
            item.pop("event_id", None)
            continue
          if len(resolved_ids) > 1:
            item.pop("event_id", None)
            continue
          if len(resolved_ids) == 1:
            item["event_id"] = resolved_ids[0]
          else:
            item.pop("event_id", None)

        missing_indices = [
            item_index for item_index, item in enumerate(items)
            if not _clean_str(item.get("event_id"))
        ]
        if missing_indices:
          if len(items) == 1:
            resolved_id, event_issue = _resolve_single_update_item_target(step, items[0], events)
            selected_event_id = resolved_id
            if selected_event_id is None and event_issue and event_issue.code == "ambiguous_reference":
              issues.append(event_issue)
            elif selected_event_id is None:
              if resolver_calls >= resolver_call_limit:
                issue_code = "ambiguous_reference" if len(events) > 1 else "missing_slot"
                issues.append(
                    _issue(step.step_id,
                           issue_code,
                           "Resolver call limit reached before identifying the target event.",
                           "event_id",
                           _event_candidate_preview(events)))
              else:
                resolver_calls += 1
                resolver_output, resolver_debug = await resolve_event_target_with_debug(
                    user_text=input_as_text,
                    intent=step.intent,
                    args=dict(items[0]),
                    previous_start_date=previous_start_iso,
                    previous_end_date=previous_end_iso,
                    candidates=candidate_payload,
                    now_iso=now_iso,
                    timezone=timezone_name,
                )
                if debug_capture is not None:
                  debug_capture.append({
                      "step_id": step.step_id,
                      "intent": step.intent,
                      "resolver_output": resolver_output.model_dump(exclude_none=True),
                      "resolver_debug": resolver_debug,
                  })
                if resolver_output.action == "select_event":
                  selected_id = _clean_str(resolver_output.selected_event_id)
                  mapped_id = candidate_id_alias.get(selected_id or "") if selected_id else None
                  if mapped_id:
                    selected_event_id = mapped_id
                  else:
                    issues.append(
                        _issue(step.step_id,
                               "not_found",
                               "Resolver selected an event outside current candidates.",
                               "event_id",
                               _event_candidate_preview(events)))
                elif resolver_output.action == "expand_context":
                  new_start = try_parse_date(resolver_output.start_date)
                  new_end = try_parse_date(resolver_output.end_date)
                  reason = _clean_str(resolver_output.reason) or "Need a broader context range."
                  if _is_strictly_broader(previous_start_date, previous_end_date, new_start, new_end):
                    decision = {
                        "action": "expand_context",
                        "reason": reason,
                        "reason_codes": resolver_output.reason_codes or [],
                        "start_date": new_start.isoformat() if new_start else None,
                        "end_date": new_end.isoformat() if new_end else None,
                        "step_id": step.step_id,
                        "attempt": decision_attempt,
                        "confidence": resolver_output.confidence,
                    }
                    args = _finalize_update_args_from_items(args, items)
                    normalized.append(_build_step(step, args))
                    for rest in plan[idx + 1:]:
                      normalized.append(rest)
                    return normalized, issues, decision
                  issues.append(
                      _issue(step.step_id,
                             "invalid_value",
                             "Expanded range must be strictly broader than previous context range.",
                             "context_range"))
                else:
                  issues.append(
                      _issue(step.step_id,
                             "ambiguous_reference" if len(events) > 1 else "missing_slot",
                             _clean_str(resolver_output.reason)
                             or "Need clarification to identify the target event.",
                             "event_id",
                             _event_candidate_preview(events)))

            if selected_event_id:
              items[0]["event_id"] = selected_event_id
          else:
            for item_index in missing_indices:
              issues.append(
                  _issue(step.step_id,
                         "missing_slot",
                         f"items[{item_index}].event_id is required for multi update.",
                         "items",
                         _event_candidate_preview(events)))

        args = _finalize_update_args_from_items(args, items)

    elif step.intent == "calendar.cancel_event":
      args = _filter_allowed_args(args, _EVENT_CANCEL_ALLOWED_KEYS)
      args, cancel_issues = _normalize_cancel_args_with_context(
          args,
          step,
          events,
          candidate_id_alias=candidate_id_alias,
          candidate_max_index=candidate_max_index,
      )
      issues.extend(cancel_issues)

    elif step.intent == "task.update_task":
      args = _filter_allowed_args(args, _TASK_UPDATE_ALLOWED_KEYS)
      args, task_update_issues = _normalize_task_update_args(
          args, step.step_id, timezone_name)
      issues.extend(task_update_issues)
      args, context_issues = _normalize_task_update_args_with_context(
          args,
          step,
          tasks,
          candidate_id_alias=task_candidate_id_alias,
          candidate_max_index=task_candidate_max_index,
      )
      issues.extend(context_issues)

    elif step.intent == "task.cancel_task":
      args = _filter_allowed_args(args, _TASK_TARGET_ALLOWED_KEYS)
      args, cancel_task_issues = _normalize_task_target_args(args, step.step_id, step.intent)
      issues.extend(cancel_task_issues)
      args, context_issues = _normalize_task_target_args_with_context(
          args,
          step,
          tasks,
          candidate_id_alias=task_candidate_id_alias,
          candidate_max_index=task_candidate_max_index,
          allow_title=True,
      )
      issues.extend(context_issues)

    elif step.intent == "meta.summarize":
      pass

    normalized.append(_build_step(step, args))

  return normalized, issues, decision


def validate_and_enrich_plan(plan: List[PlanStep], context: Dict[str, Any],
                             timezone_name: str
                             ) -> Tuple[List[PlanStep], List[ValidationIssue]]:
  pre_plan, pre_issues, _needs_context = validate_and_enrich_plan_pre_context(
      plan, timezone_name)
  if pre_issues:
    return pre_plan, pre_issues
  return validate_and_enrich_plan_with_context(pre_plan, context, timezone_name)
