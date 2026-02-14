from __future__ import annotations

import asyncio
import copy
import json
import time
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from ..config import LLM_DEBUG
from .context_provider import load_context
from .intent_router import build_plan_from_text_with_debug
from .normalizer import (
    detect_user_language,
    now_iso_in_timezone,
    normalize_input_as_text,
    resolve_timezone,
    try_parse_date,
)
from .question_agent import (build_clarification_question,
                             build_early_clarification_question)
from .response_agent import build_response_text
from .schemas import AgentStepResult, PlanStep, ValidationIssue
from .slot_extractor import (extract_step_args,
                             apply_rrule_heuristics,
                             validate_and_enrich_plan_pre_context,
                             validate_and_enrich_plan_with_context_decision)
from .state import (clear_pending_clarification, get_pending_clarification,
                    get_preferences, set_pending_clarification)
from ..recurrence import _expand_recurring_item
from ..gcal import (
    fetch_google_events_between,
    fetch_google_tasks,
    sync_google_event_after_write,
    sync_google_event_after_delete,
    emit_google_sync,
    gcal_create_single_event,
    gcal_create_recurring_event,
    gcal_delete_event,
    gcal_update_event,
    get_google_tasks_service,
    gcal_batch_insert_events,
    gcal_batch_update_events,
    gcal_batch_delete_events,
    gcal_batch_insert_tasks,
    gcal_batch_patch_tasks,
    gcal_batch_delete_tasks,
    upsert_google_task_cache,
    remove_google_task_cache,
    emit_google_task_delta,
    get_google_revision,
    _build_single_event_body,
    _build_recurring_event_body,
    _build_gcal_event_body,
    _split_gcal_event_key,
    _prepare_update_event,
)

def _build_clarify_response(input_as_text: str, now_iso: str, timezone_name: str,
                            language_code: str,
                            reason: str,
                            question: str) -> Dict[str, Any]:
  return {
      "version": "full_agent.v1",
      "status": "needs_clarification",
      "input_as_text": input_as_text,
      "now_iso": now_iso,
      "timezone": timezone_name,
      "language": language_code,
      "plan": [{
          "step_id": "s1",
          "intent": "meta.clarify",
          "args": {
              "reason": reason
          },
          "depends_on": [],
          "on_fail": "stop",
      }],
      "issues": [{
          "step_id": "s1",
          "code": "missing_slot",
          "slot": "input_as_text",
          "detail": reason,
          "candidates": [],
      }],
      "question": question,
      "results": [],
  }


def _context_preview(context: Dict[str, Any]) -> Dict[str, Any]:
  events = context.get("events") if isinstance(context.get("events"), list) else []
  tasks = context.get("tasks") if isinstance(context.get("tasks"), list) else []
  return {
      "events_count": len(events),
      "tasks_count": len(tasks),
      "scope": context.get("scope"),
  }


def _context_debug_output(context: Dict[str, Any]) -> Dict[str, Any]:
  events = context.get("events") if isinstance(context.get("events"), list) else []
  tasks = context.get("tasks") if isinstance(context.get("tasks"), list) else []
  event_samples: List[Dict[str, Any]] = []
  for event in events[:5]:
    if not isinstance(event, dict):
      continue
    event_samples.append({
        "id": event.get("id"),
        "title": event.get("title"),
        "start": event.get("start"),
        "end": event.get("end"),
        "calendar_id": event.get("calendar_id"),
    })
  task_samples: List[Dict[str, Any]] = []
  for task in tasks[:5]:
    if not isinstance(task, dict):
      continue
    task_samples.append({
        "id": task.get("id"),
        "title": task.get("title"),
        "status": task.get("status"),
        "due": task.get("due"),
    })
  return {
      "preview": _context_preview(context),
      "events_sample": event_samples,
      "tasks_sample": task_samples,
  }


def _dump_plan(plan: List[PlanStep]) -> List[Dict[str, Any]]:
  return [step.model_dump(exclude_none=True) for step in plan]


def _dump_issues(issues: List[ValidationIssue]) -> List[Dict[str, Any]]:
  return [issue.model_dump(exclude_none=True) for issue in issues]


def _missing_slots_summary(issues: List[ValidationIssue]) -> List[Dict[str, Any]]:
  """Extract a concise summary of missing_slot issues for debug visibility."""
  summary: List[Dict[str, Any]] = []
  for issue in issues:
    entry: Dict[str, Any] = {
        "step_id": issue.step_id,
        "code": issue.code,
        "slot": issue.slot,
        "detail": issue.detail,
    }
    if issue.candidates:
      entry["candidates_count"] = len(issue.candidates)
    summary.append(entry)
  return summary


def _issue_step_ids(issues: List[ValidationIssue]) -> List[str]:
  return sorted({
      issue.step_id for issue in issues
      if isinstance(issue.step_id, str) and issue.step_id
  })


def _print_missing_slots_debug(issues: List[ValidationIssue], source: str) -> None:
  """Print missing slot details to terminal when LLM_DEBUG is enabled."""
  if not LLM_DEBUG:
    return
  if not issues:
    return
  print(f"[AGENT MISSING SLOTS] source={source} count={len(issues)}", flush=True)
  for issue in issues:
    parts = [f"  step_id={issue.step_id}", f"code={issue.code}"]
    if issue.slot:
      parts.append(f"slot={issue.slot}")
    parts.append(f"detail={issue.detail}")
    if issue.candidates:
      parts.append(f"candidates={len(issue.candidates)}")
    print(" ".join(parts), flush=True)
  print("[AGENT MISSING SLOTS END]", flush=True)


def _dump_results(results: List[AgentStepResult]) -> List[Dict[str, Any]]:
  return [item.model_dump(exclude_none=True) for item in results]


_SUMMARY_INTENTS = {"meta.summarize"}
_MUTATION_INTENTS = {
    "calendar.create_event",
    "calendar.update_event",
    "calendar.cancel_event",
    "task.create_task",
    "task.update_task",
    "task.cancel_task",
}

_TASK_BATCH_CHUNK_SIZE = 50
_SLOT_EXTRACTOR_CLARIFY_CONFIDENCE_THRESHOLD = 0.7


def _is_summary_intent(intent: str) -> bool:
  return intent in _SUMMARY_INTENTS


def _is_mutation_intent(intent: str) -> bool:
  return intent in _MUTATION_INTENTS


def _is_summary_only_plan(plan: List[PlanStep]) -> bool:
  if not plan:
    return False
  return all(_is_summary_intent(step.intent) for step in plan)


def _summary_requests_from_plan(plan: List[PlanStep]) -> List[Dict[str, Any]]:
  requests: List[Dict[str, Any]] = []
  for step in plan:
    if not _is_summary_intent(step.intent):
      continue
    requests.append({
        "step_id": step.step_id,
        "intent": step.intent,
        "hint": step.extract_hint,
        "query_ranges": step.query_ranges or [],
    })
  return requests


def _response_changes_from_results(results: List[AgentStepResult]) -> List[Dict[str, Any]]:
  changes: List[Dict[str, Any]] = []
  for item in results:
    if not _is_mutation_intent(item.intent):
      continue
    entry: Dict[str, Any] = {
        "step_id": item.step_id,
        "intent": item.intent,
        "ok": item.ok,
    }
    if item.ok:
      entry["data"] = _strip_ids_from_payload(item.data)
    elif item.error:
      entry["error"] = item.error
    changes.append(entry)
  return changes


def _attach_trace(payload: Dict[str, Any], trace: Dict[str, Any],
                  branch: str) -> Dict[str, Any]:
  if not LLM_DEBUG:
    return payload
  trace["branch"] = branch
  node_outputs = trace.get("node_outputs")
  if not isinstance(node_outputs, dict):
    node_outputs = {}
    trace["node_outputs"] = node_outputs
  node_outputs["response"] = {
      "status": payload.get("status"),
      "confidence": payload.get("confidence"),
      "issues_count": len(payload.get("issues") or []),
      "results_count": len(payload.get("results") or []),
      "question": payload.get("question"),
      "response_text": payload.get("response_text"),
  }
  payload["trace"] = trace
  return payload


def _push_node_timeline(trace: Dict[str, Any], node: str, status: str,
                        detail: Optional[Dict[str, Any]] = None,
                        on_change: Optional[Callable[[], None]] = None) -> None:
  timeline = trace.get("node_timeline")
  if not isinstance(timeline, list):
    timeline = []
    trace["node_timeline"] = timeline
  entry: Dict[str, Any] = {
      "node": node,
      "status": status,
      "at": datetime.now(timezone.utc).isoformat(),
  }
  if detail:
    entry["detail"] = detail
  timeline.append(entry)
  if callable(on_change):
    on_change()


def _append_llm_output(trace: Dict[str, Any],
                       node: str,
                       raw_output: str,
                       model: str = "gpt-5-nano",
                       reasoning_effort: Optional[str] = None,
                       thinking_level: Optional[str] = None,
                       on_change: Optional[Callable[[], None]] = None) -> None:
  outputs = trace.get("llm_outputs")
  if not isinstance(outputs, list):
    outputs = []
    trace["llm_outputs"] = outputs
  entry = {
      "node": node,
      "model": model,
      "output": raw_output or "",
      "at": datetime.now(timezone.utc).isoformat(),
  }
  if reasoning_effort:
    entry["reasoning_effort"] = reasoning_effort
  if thinking_level:
    entry["thinking_level"] = thinking_level
  outputs.append(entry)
  if callable(on_change):
    on_change()


def _execution_order(plan: List[PlanStep]) -> List[PlanStep]:
  pending: Dict[str, PlanStep] = {step.step_id: step for step in plan}
  completed = set()
  order: List[PlanStep] = []

  while pending:
    progressed = False
    for step_id in list(pending.keys()):
      step = pending[step_id]
      if all(dep in completed for dep in step.depends_on):
        order.append(step)
        completed.add(step_id)
        pending.pop(step_id, None)
        progressed = True
    if not progressed:
      for step_id in sorted(pending.keys()):
        order.append(pending[step_id])
      break
  return order


def _topological_levels(plan: List[PlanStep]) -> List[List[PlanStep]]:
  """Group plan steps into levels: steps in the same level are independent."""
  pending: Dict[str, PlanStep] = {step.step_id: step for step in plan}
  completed: set[str] = set()
  levels: List[List[PlanStep]] = []

  while pending:
    level: List[PlanStep] = []
    for step_id in list(pending.keys()):
      step = pending[step_id]
      if all(dep in completed for dep in step.depends_on):
        level.append(step)
    if not level:
      # Cycle fallback: put remaining in one level
      level = list(pending.values())
      levels.append(level)
      break
    for step in level:
      completed.add(step.step_id)
      pending.pop(step.step_id, None)
    levels.append(level)

  return levels


def _is_strictly_broader_scope(previous_scope: Dict[str, Any],
                               new_start: Optional[date],
                               new_end: Optional[date]) -> bool:
  if new_start is None or new_end is None:
    return False
  if new_end < new_start:
    return False
  previous_start = try_parse_date(previous_scope.get("start_date"))
  previous_end = try_parse_date(previous_scope.get("end_date"))
  if previous_start is None or previous_end is None:
    return True
  if new_start > previous_start or new_end < previous_end:
    return False
  return new_start < previous_start or new_end > previous_end


def _event_start_date(event: Dict[str, Any]) -> date | None:
  start_raw = event.get("start")
  if not isinstance(start_raw, str) or len(start_raw) < 10:
    return None
  try:
    return datetime.strptime(start_raw[:10], "%Y-%m-%d").date()
  except Exception:
    return None


def _event_lookup_keys(value: Optional[str]) -> List[str]:
  if not isinstance(value, str):
    return []
  text = value.strip()
  if not text:
    return []
  keys = [text]
  if "::" in text:
    tail = text.split("::", 1)[1]
    if tail:
      keys.append(tail)
  return keys


def _event_context_index(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
  index: Dict[str, Dict[str, Any]] = {}
  for event in events:
    if not isinstance(event, dict):
      continue
    raw_id = event.get("id")
    calendar_id = event.get("calendar_id")
    if isinstance(raw_id, str) and raw_id.strip():
      clean_raw = raw_id.strip()
      index[clean_raw] = event
      if isinstance(calendar_id, str) and calendar_id.strip():
        index[f"{calendar_id.strip()}::{clean_raw}"] = event
  return index


def _task_context_index(tasks: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
  index: Dict[str, Dict[str, Any]] = {}
  for task in tasks:
    if not isinstance(task, dict):
      continue
    raw_id = task.get("id")
    if isinstance(raw_id, str) and raw_id.strip():
      index[raw_id.strip()] = task
  return index


def _event_view(event: Optional[Dict[str, Any]]) -> Dict[str, Any]:
  if not isinstance(event, dict):
    return {}
  return {
      "title": event.get("title"),
      "start": event.get("start"),
      "end": event.get("end"),
      "location": event.get("location"),
      "description": event.get("description"),
      "all_day": event.get("all_day"),
      "recur": event.get("recur"),
  }


def _task_view(task: Optional[Dict[str, Any]]) -> Dict[str, Any]:
  if not isinstance(task, dict):
    return {}
  return {
      "title": task.get("title"),
      "notes": task.get("notes"),
      "due": task.get("due"),
      "status": task.get("status"),
  }


def _event_after_view(item: Dict[str, Any], before: Dict[str, Any],
                      target_type: Optional[str]) -> Dict[str, Any]:
  after = dict(before)
  for key in ("title", "start", "end", "location", "description", "all_day"):
    if key in item and item.get(key) is not None:
      after[key] = item.get(key)
  if item.get("start_date") is not None:
    after["start_date"] = item.get("start_date")
  if item.get("time") is not None:
    after["time"] = item.get("time")
  if item.get("duration_minutes") is not None:
    after["duration_minutes"] = item.get("duration_minutes")
  if item.get("rrule") is not None:
    after["rrule"] = item.get("rrule")
  if item.get("recurrence") is not None:
    after["recurrence"] = item.get("recurrence")
  if target_type == "recurring":
    after["recur"] = "recurring"
  elif target_type == "single":
    after["recur"] = None
  return after


def _task_after_view(item: Dict[str, Any], before: Dict[str, Any]) -> Dict[str, Any]:
  after = dict(before)
  for key in ("title", "notes", "due", "status"):
    if key in item and item.get(key) is not None:
      after[key] = item.get(key)
  return after


def _strip_ids_from_payload(value: Any) -> Any:
  id_keys = {
      "id",
      "event_id",
      "event_ids",
      "task_id",
      "task_ids",
      "deleted_ids",
      "calendar_id",
      "google_event_id",
      "op_id",
  }
  if isinstance(value, dict):
    out: Dict[str, Any] = {}
    for key, item in value.items():
      if key in id_keys:
        continue
      out[key] = _strip_ids_from_payload(item)
    return out
  if isinstance(value, list):
    return [_strip_ids_from_payload(item) for item in value]
  return value

def _infer_create_item_type(item: Dict[str, Any]) -> str:
  raw_type = str(item.get("type") or "").strip().lower()
  if raw_type in ("single", "recurring"):
    return raw_type
  if any(item.get(key) is not None for key in ("recurrence", "rrule", "start_date",
                                               "duration_minutes")):
    return "recurring"
  return "single"


def _calendar_create_items_from_args(args: Dict[str, Any]) -> List[Dict[str, Any]]:
  items_raw = args.get("items")
  if isinstance(items_raw, list):
    normalized = [item for item in items_raw if isinstance(item, dict)]
    if normalized:
      return normalized

  recurring_signal = any(args.get(key) is not None for key in (
      "recurrence",
      "rrule",
      "start_date",
      "time",
      "duration_minutes",
  ))
  if recurring_signal:
    return [{
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
    }]

  return [{
      "type": "single",
      "title": args.get("title"),
      "start": args.get("start"),
      "end": args.get("end"),
      "location": args.get("location"),
      "description": args.get("description"),
      "reminders": args.get("reminders"),
      "all_day": args.get("all_day"),
  }]


def _infer_update_item_target_type(item: Dict[str, Any]) -> Optional[str]:
  raw_type = str(item.get("type") or "").strip().lower()
  if raw_type in ("single", "recurring"):
    return raw_type
  if any(item.get(key) is not None for key in ("recurrence", "rrule", "start_date",
                                               "time", "duration_minutes")):
    return "recurring"
  return None


def _calendar_update_items_from_args(args: Dict[str, Any]) -> List[Dict[str, Any]]:
  items_raw = args.get("items")
  if isinstance(items_raw, list):
    normalized = [item for item in items_raw if isinstance(item, dict)]
    if normalized:
      return normalized

  explicit_ids: List[str] = []
  event_id = args.get("event_id")
  if isinstance(event_id, str) and event_id.strip():
    explicit_ids.append(event_id.strip())
  event_ids_raw = args.get("event_ids")
  if isinstance(event_ids_raw, list):
    for raw_id in event_ids_raw:
      if not isinstance(raw_id, str):
        continue
      clean_id = raw_id.strip()
      if not clean_id or clean_id in explicit_ids:
        continue
      explicit_ids.append(clean_id)

  shared_patch = {
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

  if explicit_ids:
    out: List[Dict[str, Any]] = []
    for raw_id in explicit_ids:
      item = dict(shared_patch)
      item["event_id"] = raw_id
      out.append(item)
    return out

  return [shared_patch]


def _chunk_items(items: List[Any], size: int) -> List[List[Any]]:
  if size <= 0:
    return [items]
  return [items[index:index + size] for index in range(0, len(items), size)]


def _infer_task_create_item_type(item: Dict[str, Any]) -> str:
  raw_type = str(item.get("type") or "").strip().lower()
  if raw_type in ("single", "recurring"):
    return raw_type
  if any(item.get(key) is not None for key in ("recurrence", "rrule", "start_date", "time")):
    return "recurring"
  return "single"


def _task_create_items_from_args(args: Dict[str, Any]) -> List[Dict[str, Any]]:
  items_raw = args.get("items")
  if isinstance(items_raw, list):
    normalized = [item for item in items_raw if isinstance(item, dict)]
    if normalized:
      return normalized

  recurring_signal = any(args.get(key) is not None for key in (
      "recurrence",
      "rrule",
      "start_date",
      "time",
  ))
  if recurring_signal:
    return [{
        "type": "recurring",
        "title": args.get("title"),
        "notes": args.get("notes"),
        "due": args.get("due"),
        "start_date": args.get("start_date"),
        "time": args.get("time"),
        "recurrence": args.get("recurrence"),
        "rrule": args.get("rrule"),
    }]

  return [{
      "type": "single",
      "title": args.get("title"),
      "notes": args.get("notes"),
      "due": args.get("due"),
  }]


def _task_update_items_from_args(args: Dict[str, Any]) -> List[Dict[str, Any]]:
  items_raw = args.get("items")
  if isinstance(items_raw, list):
    normalized = [item for item in items_raw if isinstance(item, dict)]
    if normalized:
      return normalized

  explicit_ids: List[str] = []
  task_id = args.get("task_id")
  if isinstance(task_id, str) and task_id.strip():
    explicit_ids.append(task_id.strip())
  task_ids_raw = args.get("task_ids")
  if isinstance(task_ids_raw, list):
    for raw_id in task_ids_raw:
      if not isinstance(raw_id, str):
        continue
      clean_id = raw_id.strip()
      if not clean_id or clean_id in explicit_ids:
        continue
      explicit_ids.append(clean_id)

  shared_patch = {
      "title": args.get("title"),
      "notes": args.get("notes"),
      "due": args.get("due"),
      "status": args.get("status"),
  }
  if explicit_ids:
    out: List[Dict[str, Any]] = []
    for raw_id in explicit_ids:
      item = dict(shared_patch)
      item["task_id"] = raw_id
      out.append(item)
    return out
  return [shared_patch]


def _task_target_items_from_args(args: Dict[str, Any]) -> List[Dict[str, Any]]:
  items_raw = args.get("items")
  if isinstance(items_raw, list):
    normalized = [item for item in items_raw if isinstance(item, dict)]
    if normalized:
      return normalized

  explicit_ids: List[str] = []
  task_id = args.get("task_id")
  if isinstance(task_id, str) and task_id.strip():
    explicit_ids.append(task_id.strip())
  task_ids_raw = args.get("task_ids")
  if isinstance(task_ids_raw, list):
    for raw_id in task_ids_raw:
      if not isinstance(raw_id, str):
        continue
      clean_id = raw_id.strip()
      if not clean_id or clean_id in explicit_ids:
        continue
      explicit_ids.append(clean_id)
  if explicit_ids:
    return [{"task_id": task_id_value} for task_id_value in explicit_ids]

  title = args.get("title")
  if isinstance(title, str) and title.strip():
    return [{"title": title.strip()}]
  return []


def _task_due_iso_from_local(date_text: str, hour: int, minute: int,
                             timezone_name: str) -> Optional[str]:
  if not isinstance(date_text, str) or len(date_text) < 10:
    return None
  try:
    base_day = datetime.strptime(date_text[:10], "%Y-%m-%d")
  except Exception:
    return None
  try:
    tzinfo = ZoneInfo(timezone_name)
  except Exception:
    tzinfo = ZoneInfo("Asia/Seoul")
  dt = datetime(base_day.year, base_day.month, base_day.day, hour, minute, tzinfo=tzinfo)
  return dt.isoformat()


def _task_due_iso_from_occurrence_start(start_iso: str, timezone_name: str) -> Optional[str]:
  if not isinstance(start_iso, str) or len(start_iso) < 16:
    return None
  try:
    naive = datetime.strptime(start_iso[:16], "%Y-%m-%dT%H:%M")
  except Exception:
    return None
  try:
    tzinfo = ZoneInfo(timezone_name)
  except Exception:
    tzinfo = ZoneInfo("Asia/Seoul")
  return naive.replace(tzinfo=tzinfo).isoformat()


def _expand_task_recurring_item(item: Dict[str, Any],
                                timezone_name: str) -> List[Dict[str, Any]]:
  title = str(item.get("title") or "").strip()
  if not title:
    return []

  item_timezone = str(item.get("timezone") or timezone_name or "Asia/Seoul")
  recurring_item = {
      "title": title,
      "start_date": item.get("start_date"),
      "time": item.get("time"),
      "recurrence": item.get("recurrence"),
      "rrule": item.get("rrule"),
      "timezone": item_timezone,
  }
  expanded = _expand_recurring_item(recurring_item)
  if not expanded:
    return []

  notes = item.get("notes")
  out: List[Dict[str, Any]] = []
  for occ in expanded:
    if not isinstance(occ, dict):
      continue
    start_iso = occ.get("start")
    due_iso: Optional[str] = None
    if occ.get("all_day") is True and isinstance(start_iso, str) and len(start_iso) >= 10:
      due_iso = _task_due_iso_from_local(start_iso[:10], 23, 59, item_timezone)
    elif isinstance(start_iso, str):
      due_iso = _task_due_iso_from_occurrence_start(start_iso, item_timezone)
    if due_iso is None and isinstance(start_iso, str) and len(start_iso) >= 10:
      due_iso = _task_due_iso_from_local(start_iso[:10], 23, 59, item_timezone)

    body: Dict[str, Any] = {"title": title}
    if notes is not None:
      body["notes"] = notes
    if due_iso is not None:
      body["due"] = due_iso
    out.append(body)
  return out


def _build_task_patch_body(item: Dict[str, Any]) -> Dict[str, Any]:
  body: Dict[str, Any] = {}
  if item.get("title") is not None:
    body["title"] = item.get("title")
  if item.get("notes") is not None:
    body["notes"] = item.get("notes")
  if item.get("due") is not None:
    body["due"] = item.get("due")
  status_value = item.get("status")
  if status_value is not None:
    body["status"] = status_value
    if status_value == "completed":
      body["completed"] = datetime.now(timezone.utc).isoformat()
    elif status_value == "needsAction":
      body["completed"] = None
  return body


def _execute_step(step: PlanStep, session_id: str, timezone_name: str,
                  now_iso: str, context: Dict[str, Any],
                  suppress_sse: bool = False) -> Dict[str, Any]:
  args = step.args_dict()

  if step.intent == "meta.clarify":
    return {
        "reason": args.get("reason"),
    }

  if step.intent == "calendar.create_event":
    create_items = _calendar_create_items_from_args(args)
    if not create_items:
      raise HTTPException(status_code=422,
                          detail="calendar.create_event requires at least one create item.")

    # Prepare item metadata and event bodies
    item_metas: List[Dict[str, Any]] = []
    bodies: List[Dict[str, Any]] = []
    for index, raw_item in enumerate(create_items):
      item = dict(raw_item)
      item_type = _infer_create_item_type(item)
      item_timezone = item.get("timezone") or args.get("timezone") or timezone_name
      print(f"[EXEC] item[{index}] type={item_type}, keys={list(item.keys())}, "
            f"recurrence={item.get('recurrence')}, rrule={item.get('rrule')}, "
            f"time={item.get('time')}, start_date={item.get('start_date')}")

      body: Optional[Dict[str, Any]] = None
      if item_type == "single":
        all_day_value = item.get("all_day")
        if all_day_value is not None:
          all_day_value = bool(all_day_value)
        body = _build_single_event_body(
            title=item.get("title"),
            start_iso=item.get("start"),
            end_iso=item.get("end"),
            location=item.get("location"),
            all_day=all_day_value,
            description=item.get("description"),
            attendees=item.get("attendees"),
            reminders=item.get("reminders"),
            visibility=item.get("visibility"),
            transparency=item.get("transparency"),
            meeting_url=item.get("meeting_url"),
            timezone_value=item_timezone,
            color_id=item.get("color_id"),
        )
      elif item_type == "recurring":
        if item.get("timezone") is None:
          item["timezone"] = item_timezone
        body = _build_recurring_event_body(item)
      else:
        raise HTTPException(status_code=422,
                            detail=f"Unsupported create item type: {item_type}")

      if body is None:
        raise HTTPException(status_code=502,
                            detail=f"Failed to build event body at items[{index}].")
      bodies.append(body)
      item_metas.append({
          "type": item_type,
          "title": item.get("title"),
          "start": item.get("start"),
          "end": item.get("end"),
          "start_date": item.get("start_date"),
          "time": item.get("time"),
          "duration_minutes": item.get("duration_minutes"),
          "recurrence": item.get("recurrence"),
          "rrule": item.get("rrule"),
          "all_day": item.get("all_day"),
      })

    # Single item: direct call (no batch overhead)
    if len(bodies) == 1:
      item = dict(create_items[0])
      item_type = item_metas[0]["type"]
      item_timezone = item.get("timezone") or args.get("timezone") or timezone_name
      event_id: Optional[str] = None
      if item_type == "single":
        all_day_value = item.get("all_day")
        if all_day_value is not None:
          all_day_value = bool(all_day_value)
        event_id = gcal_create_single_event(
            title=item.get("title"),
            start_iso=item.get("start"),
            end_iso=item.get("end"),
            location=item.get("location"),
            all_day=all_day_value,
            session_id=session_id,
            description=item.get("description"),
            attendees=item.get("attendees"),
            reminders=item.get("reminders"),
            visibility=item.get("visibility"),
            transparency=item.get("transparency"),
            meeting_url=item.get("meeting_url"),
            timezone_value=item_timezone,
            color_id=item.get("color_id"),
        )
      elif item_type == "recurring":
        if item.get("timezone") is None:
          item["timezone"] = item_timezone
        event_id = gcal_create_recurring_event(item, session_id=session_id)
      if not event_id:
        raise HTTPException(status_code=502,
                            detail=f"Failed to create {item_type} event.")
      sync_google_event_after_write(session_id, event_id=event_id, emit_sse=not suppress_sse)
      return {
          "event_id": event_id,
          "event_ids": [event_id],
          "count": 1,
          "items": [{**item_metas[0], "event_id": event_id}],
      }

    # Multiple items: batch insert
    print(f"[EXEC] batch insert: {len(bodies)} events in 1 request")
    event_ids = gcal_batch_insert_events(bodies, session_id=session_id)
    print(f"[EXEC] batch insert done: {sum(1 for eid in event_ids if eid)}/{len(bodies)} succeeded")
    created_results: List[Dict[str, Any]] = []
    for index, eid in enumerate(event_ids):
      if not eid:
        raise HTTPException(status_code=502,
                            detail=f"Failed to create event at items[{index}].")
      sync_google_event_after_write(session_id, event_id=eid, emit_sse=not suppress_sse)
      created_results.append({**item_metas[index], "event_id": eid})

    primary_event_id = created_results[0]["event_id"]
    return {
        "event_id": primary_event_id,
        "event_ids": [item.get("event_id") for item in created_results],
        "count": len(created_results),
        "items": created_results,
    }

  if step.intent == "calendar.update_event":
    update_items = _calendar_update_items_from_args(args)
    if not update_items:
      raise HTTPException(status_code=422,
                          detail="calendar.update_event requires at least one update item.")

    events = context.get("events") if isinstance(context.get("events"), list) else []
    event_index = _event_context_index(events)

    # Single item: direct call (no batch overhead)
    if len(update_items) == 1:
      item = dict(update_items[0])
      event_id = item.get("event_id")
      if not isinstance(event_id, str) or not event_id.strip():
        raise HTTPException(status_code=422, detail="items[0].event_id is required.")
      all_day_value = item.get("all_day")
      if all_day_value is not None:
        all_day_value = bool(all_day_value)
      target_type = _infer_update_item_target_type(item)
      before_event: Dict[str, Any] = {}
      for key in _event_lookup_keys(event_id):
        found = event_index.get(key)
        if found:
          before_event = _event_view(found)
          break
      after_event = _event_after_view(item, before_event, target_type)

      gcal_update_event(event_id=event_id,
                        title=item.get("title"),
                        start_iso=item.get("start"),
                        end_iso=item.get("end"),
                        location=item.get("location"),
                        all_day=all_day_value,
                        session_id=session_id,
                        description=item.get("description"),
                        reminders=item.get("reminders"),
                        timezone_value=item.get("timezone") or args.get("timezone") or timezone_name,
                        start_date=item.get("start_date"),
                        time_value=item.get("time"),
                        duration_minutes=item.get("duration_minutes"),
                        recurrence=item.get("recurrence"),
                        rrule=item.get("rrule"),
                        target_type=target_type)
      sync_google_event_after_write(session_id, event_id=event_id, emit_sse=not suppress_sse)
      return {
          "event_id": event_id,
          "event_ids": [event_id],
          "updated": True,
          "updated_count": 1,
          "items": [{
              "before": before_event,
              "after": after_event,
          }],
      }

    # Multiple items: batch update
    batch_entries: List[Dict[str, Any]] = []
    item_metas: List[Dict[str, Any]] = []
    for index, raw_item in enumerate(update_items):
      item = dict(raw_item)
      event_id = item.get("event_id")
      if not isinstance(event_id, str) or not event_id.strip():
        raise HTTPException(status_code=422, detail=f"items[{index}].event_id is required.")
      all_day_value = item.get("all_day")
      if all_day_value is not None:
        all_day_value = bool(all_day_value)
      target_type = _infer_update_item_target_type(item)
      before_event: Dict[str, Any] = {}
      for key in _event_lookup_keys(event_id):
        found = event_index.get(key)
        if found:
          before_event = _event_view(found)
          break
      after_event = _event_after_view(item, before_event, target_type)

      raw_event_id, resolved_cal, body = _prepare_update_event(
          event_id=event_id,
          title=item.get("title"),
          start_iso=item.get("start"),
          end_iso=item.get("end"),
          location=item.get("location"),
          all_day=all_day_value,
          description=item.get("description"),
          reminders=item.get("reminders"),
          timezone_value=item.get("timezone") or args.get("timezone") or timezone_name,
          start_date=item.get("start_date"),
          time_value=item.get("time"),
          duration_minutes=item.get("duration_minutes"),
          recurrence=item.get("recurrence"),
          rrule=item.get("rrule"),
          target_type=target_type,
          session_id=session_id,
      )
      batch_entries.append({"event_id": raw_event_id, "calendar_id": resolved_cal, "body": body})
      item_metas.append({
          "event_id": event_id,
          "type": target_type,
          "title": item.get("title"),
          "start": item.get("start"),
          "end": item.get("end"),
          "start_date": item.get("start_date"),
          "time": item.get("time"),
          "duration_minutes": item.get("duration_minutes"),
          "recurrence": item.get("recurrence"),
          "rrule": item.get("rrule"),
          "all_day": item.get("all_day"),
          "before": before_event,
          "after": after_event,
      })

    print(f"[EXEC] batch update: {len(batch_entries)} events in 1 request")
    results_ok = gcal_batch_update_events(batch_entries, session_id=session_id)
    print(f"[EXEC] batch update done: {sum(results_ok)}/{len(batch_entries)} succeeded")
    updated_results: List[Dict[str, Any]] = []
    for index, ok in enumerate(results_ok):
      if not ok:
        raise HTTPException(status_code=502,
                            detail=f"Failed to update event at items[{index}].")
      eid = item_metas[index]["event_id"]
      sync_google_event_after_write(session_id, event_id=eid, emit_sse=not suppress_sse)
      updated_results.append({
          "before": item_metas[index].get("before"),
          "after": item_metas[index].get("after"),
      })

    primary_event_id = item_metas[0]["event_id"] if item_metas else None
    return {
        "event_id": primary_event_id,
        "event_ids": [item.get("event_id") for item in item_metas if item.get("event_id")],
        "updated": True,
        "updated_count": len(updated_results),
        "items": updated_results,
    }

  if step.intent == "calendar.cancel_event":
    event_id = args.get("event_id")
    event_ids_raw = args.get("event_ids")
    if args.get("cancel_ranges") is not None or args.get("start_date") is not None or args.get("end_date") is not None:
      raise HTTPException(
          status_code=422,
          detail="Range cancel is no longer supported. Use event_id/event_ids.",
      )

    # Normalise inputs
    explicit_ids: list[str] = []
    if isinstance(event_id, str) and event_id.strip():
      explicit_ids.append(event_id.strip())
    if isinstance(event_ids_raw, list):
      for eid in event_ids_raw:
        if isinstance(eid, str) and eid.strip() and eid.strip() not in explicit_ids:
          explicit_ids.append(eid.strip())

    if not explicit_ids:
      raise HTTPException(status_code=422,
                          detail="event_id or event_ids is required for cancel.")

    events = context.get("events") if isinstance(context.get("events"), list) else []
    event_index = _event_context_index(events)
    deleted_item_by_id: Dict[str, Dict[str, Any]] = {}
    for eid in explicit_ids:
      snapshot: Dict[str, Any] = {}
      for key in _event_lookup_keys(eid):
        found = event_index.get(key)
        if found:
          snapshot = _event_view(found)
          break
      if snapshot:
        deleted_item_by_id[eid] = snapshot

    deleted_ids: list[str] = []
    errors: list[str] = []
    all_delete_ids: list[str] = list(explicit_ids)

    # --- Single item: direct call (no batch overhead) ---
    if len(all_delete_ids) == 1:
      eid = all_delete_ids[0]
      try:
        gcal_delete_event(event_id=eid, session_id=session_id)
        sync_google_event_after_delete(session_id, event_id=eid, emit_sse=not suppress_sse)
        deleted_ids.append(eid)
      except Exception as exc:
        errors.append(f"{eid}: {exc}")
    elif len(all_delete_ids) > 1:
      # --- Multiple items: batch delete ---
      print(f"[EXEC] batch delete: {len(all_delete_ids)} events in 1 request")
      results_ok = gcal_batch_delete_events(all_delete_ids, session_id=session_id)
      print(f"[EXEC] batch delete done: {sum(results_ok)}/{len(all_delete_ids)} succeeded")
      for idx, ok in enumerate(results_ok):
        eid = all_delete_ids[idx]
        if ok:
          sync_google_event_after_delete(session_id, event_id=eid, emit_sse=not suppress_sse)
          deleted_ids.append(eid)
        else:
          errors.append(f"{eid}: batch delete failed")

    mode = "single" if len(explicit_ids) == 1 else "multi"

    return {
        "deleted": True,
        "mode": mode,
        "deleted_count": len(deleted_ids),
        "deleted_ids": deleted_ids,
        "deleted_items": [deleted_item_by_id[eid] for eid in deleted_ids if eid in deleted_item_by_id],
        "errors": errors if errors else None,
    }

  if step.intent == "meta.summarize":
    events = context.get("events") if isinstance(context.get("events"), list) else []
    tasks = context.get("tasks") if isinstance(context.get("tasks"), list) else []
    completed_count = sum(
        1 for task in tasks if str(task.get("status") or "") == "completed")

    return {
        "scope": context.get("scope"),
        "calendar": {
            "count": len(events),
            "items": [{
                "event_id": f"{item.get('calendar_id')}::{item.get('id')}"
                if item.get("calendar_id") else item.get("id"),
                "title": item.get("title"),
                "start": item.get("start"),
                "end": item.get("end"),
                "location": item.get("location"),
            } for item in events[:20]],
        },
        "tasks": {
            "count": len(tasks),
            "completed_count": completed_count,
            "open_count": max(0, len(tasks) - completed_count),
            "items": [{
                "task_id": task.get("id"),
                "title": task.get("title"),
                "status": task.get("status"),
                "due": task.get("due"),
            } for task in tasks[:30]],
        },
    }

  if step.intent == "task.create_task":
    create_items = _task_create_items_from_args(args)
    if not create_items:
      raise HTTPException(status_code=422,
                          detail="task.create_task requires at least one create item.")

    bodies: List[Dict[str, Any]] = []
    item_metas: List[Dict[str, Any]] = []
    expanded_generated = 0
    for index, raw_item in enumerate(create_items):
      item = dict(raw_item)
      item_type = _infer_task_create_item_type(item)
      if item_type == "single":
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
          raise HTTPException(status_code=422,
                              detail=f"items[{index}].title is required.")
        body: Dict[str, Any] = {"title": title.strip()}
        if item.get("notes") is not None:
          body["notes"] = item.get("notes")
        if item.get("due") is not None:
          body["due"] = item.get("due")
        bodies.append(body)
        item_metas.append({
            "type": "single",
            "source_item_index": index,
            "title": title.strip(),
            "due": item.get("due"),
        })
        continue

      if item_type != "recurring":
        raise HTTPException(status_code=422,
                            detail=f"Unsupported task create item type: {item_type}")
      expanded = _expand_task_recurring_item(item, timezone_name)
      if not expanded:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to expand recurring task at items[{index}].")
      expanded_generated += len(expanded)
      for occ_index, body in enumerate(expanded):
        bodies.append(body)
        item_metas.append({
            "type": "recurring",
            "source_item_index": index,
            "occurrence_index": occ_index,
            "title": body.get("title"),
            "due": body.get("due"),
            "start_date": item.get("start_date"),
            "time": item.get("time"),
            "recurrence": item.get("recurrence"),
            "rrule": item.get("rrule"),
        })

    if not bodies:
      raise HTTPException(status_code=422,
                          detail="No executable task create items were produced.")

    service = get_google_tasks_service(session_id)
    if len(bodies) == 1:
      result = service.tasks().insert(tasklist='@default', body=bodies[0]).execute()
      task_id = result.get("id")
      mutation_meta: Dict[str, Any] = {
          "new_revision": get_google_revision(session_id),
          "op_id": None,
      }
      if isinstance(result, dict):
        upsert_google_task_cache(session_id, result)
        mutation_meta = emit_google_task_delta(session_id, "upsert", task=result)
      return {
          "task_id": task_id,
          "task_ids": [task_id] if task_id else [],
          "count": 1,
          "expanded_count": expanded_generated,
          "items": [{
              **item_metas[0],
              "task_id": task_id,
              "status": result.get("status"),
              "due": result.get("due"),
          }],
          **mutation_meta,
      }

    created_results: List[Dict[str, Any]] = []
    offset = 0
    for chunk in _chunk_items(bodies, _TASK_BATCH_CHUNK_SIZE):
      print(f"[EXEC] task batch insert: {len(chunk)} tasks in 1 request")
      chunk_results = gcal_batch_insert_tasks(chunk, session_id=session_id, tasklist='@default')
      success_count = sum(1 for item in chunk_results if isinstance(item, dict))
      print(f"[EXEC] task batch insert done: {success_count}/{len(chunk)} succeeded")
      for local_idx, result in enumerate(chunk_results):
        global_idx = offset + local_idx
        if not isinstance(result, dict):
          raise HTTPException(status_code=502,
                              detail=f"Failed to create task at expanded_items[{global_idx}].")
        task_id = result.get("id")
        created_results.append({
            **item_metas[global_idx],
            "task_id": task_id,
            "status": result.get("status"),
            "due": result.get("due"),
        })
      offset += len(chunk)

    primary_task_id = created_results[0].get("task_id") if created_results else None
    return {
        "task_id": primary_task_id,
        "task_ids": [item.get("task_id") for item in created_results if item.get("task_id")],
        "count": len(created_results),
        "expanded_count": expanded_generated,
        "items": created_results,
        "new_revision": get_google_revision(session_id),
    }

  if step.intent == "task.update_task":
    update_items = _task_update_items_from_args(args)
    if not update_items:
      raise HTTPException(status_code=422,
                          detail="task.update_task requires at least one update item.")

    tasks = context.get("tasks") if isinstance(context.get("tasks"), list) else []
    task_index = _task_context_index(tasks)
    patch_entries: List[Dict[str, Any]] = []
    item_metas: List[Dict[str, Any]] = []
    for index, raw_item in enumerate(update_items):
      item = dict(raw_item)
      task_id = item.get("task_id")
      if not isinstance(task_id, str) or not task_id.strip():
        raise HTTPException(status_code=422, detail=f"items[{index}].task_id is required.")
      patch_body = _build_task_patch_body(item)
      if not patch_body:
        raise HTTPException(status_code=422,
                            detail=f"No patch fields were provided at items[{index}].")
      patch_entries.append({
          "task_id": task_id.strip(),
          "body": patch_body,
      })
      before_task = _task_view(task_index.get(task_id.strip()))
      after_task = _task_after_view(item, before_task)
      item_metas.append({
          "task_id": task_id.strip(),
          "title": item.get("title"),
          "due": item.get("due"),
          "status": item.get("status"),
          "notes": item.get("notes"),
          "before": before_task,
          "after": after_task,
      })

    service = get_google_tasks_service(session_id)
    if len(patch_entries) == 1:
      entry = patch_entries[0]
      result = service.tasks().patch(tasklist='@default',
                                     task=entry["task_id"],
                                     body=entry["body"]).execute()
      mutation_meta: Dict[str, Any] = {
          "new_revision": get_google_revision(session_id),
          "op_id": None,
      }
      if isinstance(result, dict):
        upsert_google_task_cache(session_id, result)
        mutation_meta = emit_google_task_delta(session_id, "upsert", task=result)
      return {
          "task_id": result.get("id"),
          "task_ids": [result.get("id")] if result.get("id") else [],
          "updated": True,
          "updated_count": 1,
          "items": [{
              "before": item_metas[0].get("before"),
              "after": item_metas[0].get("after"),
          }],
          **mutation_meta,
      }

    updated_results: List[Dict[str, Any]] = []
    offset = 0
    for chunk in _chunk_items(patch_entries, _TASK_BATCH_CHUNK_SIZE):
      print(f"[EXEC] task batch patch: {len(chunk)} tasks in 1 request")
      chunk_results = gcal_batch_patch_tasks(chunk, session_id=session_id, tasklist='@default')
      success_count = sum(1 for item in chunk_results if isinstance(item, dict))
      print(f"[EXEC] task batch patch done: {success_count}/{len(chunk)} succeeded")
      for local_idx, result in enumerate(chunk_results):
        global_idx = offset + local_idx
        if not isinstance(result, dict):
          raise HTTPException(status_code=502,
                              detail=f"Failed to update task at items[{global_idx}].")
        meta = item_metas[global_idx]
        updated_results.append({
            "before": meta.get("before"),
            "after": meta.get("after"),
        })
      offset += len(chunk)

    primary_task_id = patch_entries[0].get("task_id") if patch_entries else None
    return {
        "task_id": primary_task_id,
        "task_ids": [entry.get("task_id") for entry in patch_entries if entry.get("task_id")],
        "updated": True,
        "updated_count": len(updated_results),
        "items": updated_results,
        "new_revision": get_google_revision(session_id),
    }

  if step.intent == "task.cancel_task":
    target_items = _task_target_items_from_args(args)
    if not target_items:
      raise HTTPException(status_code=422,
                          detail="task.cancel_task requires at least one target item.")

    task_ids: List[str] = []
    for index, raw_item in enumerate(target_items):
      item = dict(raw_item)
      task_id = item.get("task_id")
      if not isinstance(task_id, str) or not task_id.strip():
        raise HTTPException(status_code=422, detail=f"items[{index}].task_id is required.")
      clean_id = task_id.strip()
      if clean_id not in task_ids:
        task_ids.append(clean_id)

    if not task_ids:
      raise HTTPException(status_code=422,
                          detail="No valid task_id values were provided for cancel.")

    tasks = context.get("tasks") if isinstance(context.get("tasks"), list) else []
    task_index = _task_context_index(tasks)
    deleted_item_by_id: Dict[str, Dict[str, Any]] = {}
    for tid in task_ids:
      snapshot = _task_view(task_index.get(tid))
      if snapshot:
        deleted_item_by_id[tid] = snapshot

    service = get_google_tasks_service(session_id)
    deleted_ids: List[str] = []
    errors: List[str] = []
    if len(task_ids) == 1:
      task_id = task_ids[0]
      try:
        service.tasks().delete(tasklist='@default', task=task_id).execute()
        deleted_ids.append(task_id)
        remove_google_task_cache(session_id, task_id)
        emit_google_task_delta(session_id, "delete", task_id=task_id)
      except Exception as exc:
        errors.append(f"{task_id}: {exc}")
    else:
      offset = 0
      for chunk in _chunk_items(task_ids, _TASK_BATCH_CHUNK_SIZE):
        print(f"[EXEC] task batch delete: {len(chunk)} tasks in 1 request")
        chunk_results = gcal_batch_delete_tasks(chunk, session_id=session_id, tasklist='@default')
        print(f"[EXEC] task batch delete done: {sum(chunk_results)}/{len(chunk)} succeeded")
        for local_idx, ok in enumerate(chunk_results):
          global_idx = offset + local_idx
          task_id = task_ids[global_idx]
          if ok:
            deleted_ids.append(task_id)
          else:
            errors.append(f"{task_id}: batch delete failed")
        offset += len(chunk)

    return {
        "deleted": True,
        "deleted_count": len(deleted_ids),
        "deleted_ids": deleted_ids,
        "deleted_items": [deleted_item_by_id[tid] for tid in deleted_ids if tid in deleted_item_by_id],
        "errors": errors if errors else None,
        "new_revision": get_google_revision(session_id),
    }

  raise HTTPException(status_code=422, detail=f"Unsupported intent: {step.intent}")


async def run_full_agent(session_id: str,
                         input_as_text: str,
                         requested_timezone: str | None = None,
                         dry_run: bool = False,
                         on_debug_update: Optional[Callable[[Dict[str, Any]], None]] = None,
                         on_agent_stream_event: Optional[Callable[[Dict[str, Any]], None]] = None
                         ) -> Dict[str, Any]:
  normalized_text = normalize_input_as_text(input_as_text)
  language_code = detect_user_language(normalized_text)
  base_preferences = get_preferences(session_id)
  timezone_name = resolve_timezone(requested_timezone, base_preferences)
  now_iso = now_iso_in_timezone(timezone_name)
  debug_enabled = bool(LLM_DEBUG)

  trace: Dict[str, Any] = {
      "debug": {
          "enabled": bool(LLM_DEBUG),
          "current_node": "input_gate",
      },
      "node_timeline": [],
      "llm_outputs": [],
      "node_outputs": {
          "input_gate": {
              "raw_input_as_text": input_as_text,
              "normalized_input_as_text": normalized_text,
              "is_empty": not bool(normalized_text),
          },
          "normalizer": {
              "language": language_code,
              "timezone": timezone_name,
              "now_iso": now_iso,
          },
      }
  }
  def _emit_debug_snapshot() -> None:
    if not debug_enabled:
      return
    if not callable(on_debug_update):
      return
    try:
      on_debug_update({
          "debug": copy.deepcopy(trace.get("debug") or {}),
          "node_timeline": copy.deepcopy(trace.get("node_timeline") or []),
          "llm_outputs": copy.deepcopy(trace.get("llm_outputs") or []),
          "node_outputs": copy.deepcopy(trace.get("node_outputs") or {}),
          "branch": trace.get("branch"),
      })
    except Exception:
      return

  def _set_current_node(node: str) -> None:
    if not debug_enabled:
      return
    trace["debug"]["current_node"] = node
    _emit_debug_snapshot()

  def _push(node: str, status: str,
            detail: Optional[Dict[str, Any]] = None) -> None:
    if debug_enabled:
      _push_node_timeline(trace, node, status, detail, on_change=_emit_debug_snapshot)
    _emit_stream_event({
        "type": "agent_status",
        "node": node,
        "status": status,
        "detail": detail or {},
        "at": datetime.now(timezone.utc).isoformat(),
    })

  def _append_llm(node: str, output: str, model: str = "gpt-5-nano",
                  reasoning_effort: Optional[str] = None,
                  thinking_level: Optional[str] = None) -> None:
    if not debug_enabled:
      return
    _append_llm_output(trace, node, output, model=model,
                       reasoning_effort=reasoning_effort,
                       thinking_level=thinking_level,
                       on_change=_emit_debug_snapshot)

  def _emit_stream_event(payload: Dict[str, Any]) -> None:
    if not callable(on_agent_stream_event):
      return
    try:
      on_agent_stream_event(payload)
    except Exception:
      return

  def _emit_stream_delta(node: str, delta: str) -> None:
    if not isinstance(delta, str) or not delta:
      return
    _emit_stream_event({
        "type": "agent_delta",
        "node": node,
        "delta": delta,
        "at": datetime.now(timezone.utc).isoformat(),
    })

  def _append_response_agent_llm(response_agent_debug: Dict[str, Any],
                                 fallback_output: str = "") -> None:
    attempts = response_agent_debug.get("attempts")
    appended = False
    if isinstance(attempts, list):
      for attempt in attempts:
        if not isinstance(attempt, dict):
          continue
        _append_llm(
            "response_agent",
            str(attempt.get("raw_output") or ""),
            model=str(attempt.get("model") or response_agent_debug.get("model")
                      or "gpt-5-mini"),
            reasoning_effort=attempt.get("reasoning_effort"),
            thinking_level=attempt.get("thinking_level"),
        )
        appended = True
    if appended:
      return
    response_agent_output_for_log = str(response_agent_debug.get("raw_output") or "")
    if not response_agent_output_for_log:
      response_agent_output_for_log = str(
          response_agent_debug.get("fallback_response") or fallback_output or "")
    _append_llm(
        "response_agent",
        response_agent_output_for_log,
        model=str(response_agent_debug.get("model") or "gpt-5-mini"),
        reasoning_effort=response_agent_debug.get("reasoning_effort"),
        thinking_level=response_agent_debug.get("thinking_level"),
    )

  def _attach(payload: Dict[str, Any], branch: str) -> Dict[str, Any]:
    if not debug_enabled:
      return payload
    result = _attach_trace(payload, trace, branch)
    _emit_debug_snapshot()
    return result

  def _remember_pending(plan_to_resume: List[PlanStep], issues_to_resume: List[ValidationIssue],
                        source: str, confidence: float) -> None:
    set_pending_clarification(
        session_id,
        {
            "plan": _dump_plan(plan_to_resume),
            "issue_step_ids": _issue_step_ids(issues_to_resume),
            "source": source,
            "confidence": confidence,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        },
    )

  def _clear_pending() -> None:
    clear_pending_clarification(session_id)

  _emit_debug_snapshot()
  _push("input_gate", "running")
  _push("input_gate", "done")
  _set_current_node("normalizer")
  _push("normalizer", "running")
  _push("normalizer", "done")

  if not normalized_text:
    response = _build_clarify_response(
        input_as_text=normalized_text,
        now_iso=now_iso,
        timezone_name=timezone_name,
        language_code=language_code,
        reason="input_as_text is empty.",
        question="Please provide your request text.",
    )
    trace["node_outputs"]["early_clarify"] = {
        "reason": "input_as_text is empty.",
        "confidence": 0.0,
    }
    trace["node_outputs"]["question_agent"] = {
        "question": response.get("question"),
        "source": "empty_input_fallback",
    }
    _set_current_node("response")
    _push("early_clarify", "done", {"reason": "empty_input"})
    _push("question_agent", "done")
    _push("response", "done")
    _clear_pending()
    return _attach(response, "empty_input")

  pending_resume = get_pending_clarification(session_id)
  resumed_from_pending = False
  resume_issue_step_ids: List[str] = []
  plan: List[PlanStep] = []
  plan_confidence = 0.0

  pending_plan_raw = pending_resume.get("plan")
  pending_issue_step_ids_raw = pending_resume.get("issue_step_ids")
  if isinstance(pending_plan_raw, list) and isinstance(pending_issue_step_ids_raw, list):
    parsed_plan: List[PlanStep] = []
    for raw_step in pending_plan_raw:
      if not isinstance(raw_step, dict):
        continue
      try:
        parsed_plan.append(PlanStep.model_validate(raw_step))
      except Exception:
        continue
    parsed_issue_step_ids = sorted({
        str(step_id).strip()
        for step_id in pending_issue_step_ids_raw
        if str(step_id).strip()
    })
    if parsed_plan and parsed_issue_step_ids:
      resumed_from_pending = True
      plan = parsed_plan
      resume_issue_step_ids = parsed_issue_step_ids
      try:
        plan_confidence = float(pending_resume.get("confidence") or 0.0)
      except Exception:
        plan_confidence = 0.0
      trace["node_outputs"]["resume"] = {
          "enabled": True,
          "source": pending_resume.get("source"),
          "issue_step_ids": resume_issue_step_ids,
          "plan": _dump_plan(plan),
      }
      trace["node_outputs"]["intent_router"] = {
          "skipped": True,
          "reason": "resume_from_pending_clarification",
      }
      _set_current_node("intent_router")
      _push("intent_router", "done", {
          "skipped": True,
          "reason": "resume_from_pending_clarification",
      })
    else:
      _clear_pending()

  if not resumed_from_pending:
    _clear_pending()
    _set_current_node("intent_router")
    _push("intent_router", "running")
    plan_output, intent_router_debug = await build_plan_from_text_with_debug(
        input_as_text=normalized_text,
        now_iso=now_iso,
        timezone=timezone_name,
        preferences=base_preferences,
    )
    _push("intent_router", "done")
    router_debug_output = str(intent_router_debug.get("raw_output") or "")
    _append_llm(
        "intent_router",
        router_debug_output,
        model=str(intent_router_debug.get("model") or "gpt-5-mini"),
        reasoning_effort=intent_router_debug.get("reasoning_effort"),
        thinking_level=intent_router_debug.get("thinking_level"),
    )
    plan = plan_output.plan
    plan_confidence = plan_output.confidence

    primary_intent = plan[0].intent if plan else "meta.clarify"
    trace["node_outputs"]["intent_router"] = {
        "confidence": plan_confidence,
        "primary_intent": primary_intent,
        "plan": _dump_plan(plan),
        "debug": {
            "payload": intent_router_debug.get("payload"),
            "system_prompt": intent_router_debug.get("system_prompt"),
            "developer_prompt": intent_router_debug.get("developer_prompt"),
            "model": intent_router_debug.get("model"),
            "reasoning_effort": intent_router_debug.get("reasoning_effort"),
            "thinking_level": intent_router_debug.get("thinking_level"),
        },
    }
  else:
    primary_intent = plan[0].intent if plan else "meta.clarify"

  if (not resumed_from_pending) and primary_intent == "meta.clarify":
    first_args = plan[0].args_dict() if plan else {}
    router_reason = str(first_args.get("reason") or "").strip()
    reason = router_reason.strip()
    if not reason:
      reason = "Need a clearer request."

    _set_current_node("early_clarify")
    _push("early_clarify", "running")
    early_issues = [
        ValidationIssue(
            step_id=plan[0].step_id if plan else "s1",
            code="missing_slot",
            slot="input_as_text",
            detail=reason,
            candidates=[],
        )
    ]
    question_debug: Dict[str, Any] = {}
    _set_current_node("question_agent")
    _push("question_agent", "running")
    question = await build_early_clarification_question(
        router_intent=primary_intent,
        input_as_text=normalized_text,
        now_iso=now_iso,
        context={
            "timezone": timezone_name,
            "preferences": base_preferences,
            "router_confidence": plan_confidence,
            "reason": reason,
        },
        language_code=language_code,
        debug_capture=question_debug,
        on_stream_delta=lambda delta: _emit_stream_delta("question_agent", delta),
    )
    _push("question_agent", "done")
    _append_llm(
        "question_agent",
        str(question_debug.get("raw_output") or ""),
        model=str(question_debug.get("model") or "gpt-5-nano"),
        reasoning_effort=question_debug.get("reasoning_effort"),
        thinking_level=question_debug.get("thinking_level"),
    )
    _push("early_clarify", "done")
    trace["node_outputs"]["early_clarify"] = {
        "triggered": True,
        "primary_intent": primary_intent,
        "confidence": plan_confidence,
        "reason": reason,
    }
    trace["node_outputs"]["question_agent"] = {
        "question": question,
        "issues": _dump_issues(early_issues),
        "debug": question_debug,
    }
    response = {
        "version": "full_agent.v1",
        "status": "needs_clarification",
        "input_as_text": normalized_text,
        "now_iso": now_iso,
        "timezone": timezone_name,
        "language": language_code,
        "confidence": plan_confidence,
        "plan": _dump_plan(plan),
        "issues": _dump_issues(early_issues),
        "missing_slots": _missing_slots_summary(early_issues),
        "question": question,
        "results": [],
    }
    _set_current_node("response")
    _push("response", "done")
    _clear_pending()
    return _attach(response, "early_clarify")

  if _is_summary_only_plan(plan):
    now_date = datetime.fromisoformat(now_iso).date()
    context_provider_input = {
        "session_id_present": bool(session_id),
        "now_date": now_date.isoformat(),
        "timezone": timezone_name,
        "plan": _dump_plan(plan),
        "phase": "summary_direct",
    }
    _set_current_node("context_provider")
    _push("context_provider", "running", {"phase": "summary_direct"})
    context_started_at = time.perf_counter()
    context = load_context(session_id, plan, now_date, timezone_name)
    context_elapsed_ms = round((time.perf_counter() - context_started_at) * 1000, 1)
    _push("context_provider", "done", {
        "phase": "summary_direct",
        "elapsed_ms": context_elapsed_ms,
    })
    trace["node_outputs"]["context_provider"] = {
        "input": context_provider_input,
        "duration_ms": context_elapsed_ms,
        "output": _context_debug_output(context),
    }
    trace["node_outputs"]["slot_extractor"] = {
        "skipped": True,
        "reason": "summary_direct_path",
    }
    trace["node_outputs"]["slot_validator"] = {
        "skipped": True,
        "reason": "summary_direct_path",
    }
    trace["node_outputs"]["executor"] = {
        "skipped": True,
        "reason": "summary_direct_path",
    }
    summary_requests = _summary_requests_from_plan(plan)
    response_agent_debug: Dict[str, Any] = {}
    _set_current_node("response_agent")
    _push("response_agent", "running", {"mode": "summarize"})
    response_text = await build_response_text(
        user_text=normalized_text,
        intent=primary_intent,
        now_iso=now_iso,
        timezone_name=timezone_name,
        language_code=language_code,
        changes=[],
        summary_requests=summary_requests,
        context=context,
        debug_capture=response_agent_debug,
        on_stream_delta=lambda delta: _emit_stream_delta("response_agent", delta),
    )
    _push("response_agent", "done", {
        "mode": "summarize",
        "summary_request_count": len(summary_requests),
    })
    _append_response_agent_llm(
        response_agent_debug,
        fallback_output=str(response_text or ""),
    )
    trace["node_outputs"]["response_agent"] = {
        "summary_requests": summary_requests,
        "debug": response_agent_debug,
    }
    response = {
        "version": "full_agent.v1",
        "status": "completed",
        "input_as_text": normalized_text,
        "now_iso": now_iso,
        "timezone": timezone_name,
        "language": language_code,
        "confidence": plan_confidence,
        "plan": _dump_plan(plan),
        "issues": [],
        "results": [],
        "response_text": response_text,
    }
    _set_current_node("response")
    _push("response", "done")
    _clear_pending()
    return _attach(response, "summary_direct")

  now_date = datetime.fromisoformat(now_iso).date()
  # --- Context loading (query_ranges based) ---
  any_needs_context = any(step.query_ranges for step in plan) or any(
      step.intent in ("calendar.update_event", "calendar.cancel_event",
                       "meta.summarize",
                       "task.update_task", "task.cancel_task")
      for step in plan)
  # Also load context for create_event when query_ranges is set
  if not any_needs_context:
    any_needs_context = any(
        step.intent == "calendar.create_event" and step.query_ranges
        for step in plan)
  context: Dict[str, Any] = {"events": [], "tasks": [], "scope": None}
  if any_needs_context:
    context_provider_initial_input = {
        "session_id_present": bool(session_id),
        "now_date": now_date.isoformat(),
        "timezone": timezone_name,
        "plan": _dump_plan(plan),
        "phase": "pre_extraction",
    }
    _set_current_node("context_provider")
    _push("context_provider", "running", {"phase": "pre_extraction"})
    context_started_at = time.perf_counter()
    context = load_context(session_id, plan, now_date, timezone_name)
    context_elapsed_ms = round((time.perf_counter() - context_started_at) * 1000, 1)
    _push("context_provider", "done", {
        "phase": "pre_extraction",
        "elapsed_ms": context_elapsed_ms,
    })
    trace["node_outputs"]["context_provider_initial"] = {
        "input": context_provider_initial_input,
        "duration_ms": context_elapsed_ms,
        "output": _context_debug_output(context),
    }

  # --- Per-step slot extraction (parallel for independent steps) ---
  _set_current_node("slot_extractor")
  _push("slot_extractor", "running", {"stage": "per_intent"})

  # Group steps into topological levels for parallel execution
  levels = _topological_levels(plan)
  extracted_steps: List[PlanStep] = []
  all_extraction_debug: List[Dict[str, Any]] = []
  target_step_ids = set(resume_issue_step_ids) if resumed_from_pending else {
      step.step_id for step in plan
  }

  for level in levels:
    if len(level) == 1:
      step = level[0]
      if step.step_id not in target_step_ids:
        extracted_steps.append(step)
        all_extraction_debug.append({
            "skipped": True,
            "step_id": step.step_id,
            "intent": step.intent,
            "reason": "resume_cached_step",
        })
        continue
      _push("slot_extractor", "running", {
          "stage": "per_intent",
          "step_id": step.step_id,
          "intent": step.intent,
      })
      enriched_step, step_debug = await extract_step_args(
          step, normalized_text, now_iso, timezone_name, language_code,
          context=context if any_needs_context else None,
      )
      _push("slot_extractor", "done", {
          "stage": "per_intent",
          "step_id": step.step_id,
          "intent": step.intent,
      })
      extracted_steps.append(enriched_step)
      all_extraction_debug.append(step_debug)
    else:
      # Parallel extraction for independent target steps.
      target_steps: List[PlanStep] = []
      for step in level:
        if step.step_id not in target_step_ids:
          extracted_steps.append(step)
          all_extraction_debug.append({
              "skipped": True,
              "step_id": step.step_id,
              "intent": step.intent,
              "reason": "resume_cached_step",
          })
          continue
        target_steps.append(step)
        _push("slot_extractor", "running", {
            "stage": "per_intent",
            "step_id": step.step_id,
            "intent": step.intent,
        })
      if not target_steps:
        continue
      coros = [
          extract_step_args(
              step, normalized_text, now_iso, timezone_name, language_code,
              context=context if any_needs_context else None,
          )
          for step in target_steps
      ]
      results_parallel = await asyncio.gather(*coros)
      for index, (enriched_step, step_debug) in enumerate(results_parallel):
        source_step = target_steps[index]
        _push("slot_extractor", "done", {
            "stage": "per_intent",
            "step_id": source_step.step_id,
            "intent": source_step.intent,
        })
        extracted_steps.append(enriched_step)
        all_extraction_debug.append(step_debug)

  plan = apply_rrule_heuristics(extracted_steps, normalized_text, timezone_name)

  _push("slot_extractor", "done", {"stage": "per_intent"})
  extraction_records: List[Dict[str, Any]] = []
  for index, dbg in enumerate(all_extraction_debug):
    step = extracted_steps[index] if index < len(extracted_steps) else None
    raw_out = str(dbg.get("raw_output") or "")
    model_name = str(dbg.get("model") or "gpt-5-mini")
    intent_name = str(dbg.get("intent") or (step.intent if step else "") or "")
    node_name = f"slot_extractor:{intent_name}" if intent_name else "slot_extractor"
    step_id = step.step_id if step else str(dbg.get("step_id") or "")
    args_payload: Dict[str, Any] = {}
    if step and isinstance(step.args, dict):
      args_payload = copy.deepcopy(step.args)

    fallback_payload = json.dumps({
        "intent": intent_name,
        "step_id": step_id,
        "args": args_payload,
        "llm_available": dbg.get("llm_available", True),
        "llm_error": dbg.get("llm_error"),
        "raw_output_empty": not bool(raw_out),
    }, ensure_ascii=False, indent=2)
    _append_llm(node_name, raw_out or fallback_payload, model=model_name,
                reasoning_effort=dbg.get("reasoning_effort"),
                thinking_level=dbg.get("thinking_level"))

    extraction_records.append({
        "node": node_name,
        "intent": intent_name,
        "step_id": step_id,
        "extracted_confidence": dbg.get("extracted_confidence"),
        "model": model_name,
        "reasoning_effort": dbg.get("reasoning_effort"),
        "thinking_level": dbg.get("thinking_level"),
        "llm_available": dbg.get("llm_available", True),
        "llm_error": dbg.get("llm_error"),
        "raw_output": raw_out,
        "raw_output_empty": not bool(raw_out),
        "args": args_payload,
        "payload": dbg.get("payload"),
        "system_prompt": dbg.get("system_prompt"),
        "developer_prompt": dbg.get("developer_prompt"),
    })
  trace["node_outputs"]["slot_extractor"] = {
      "extraction_count": len(all_extraction_debug),
      "level_count": len(levels),
      "extractions": extraction_records,
  }

  _set_current_node("slot_validator")
  _push("slot_validator", "running", {"stage": "pre"})
  pre_validated_plan, pre_issues, needs_context_lookup = validate_and_enrich_plan_pre_context(
      plan, timezone_name)
  low_confidence_issues: List[ValidationIssue] = []
  for index, dbg in enumerate(all_extraction_debug):
    step = extracted_steps[index] if index < len(extracted_steps) else None
    if not step:
      continue
    conf_raw = dbg.get("extracted_confidence")
    if not isinstance(conf_raw, (int, float)):
      continue
    conf = float(conf_raw)
    if conf <= _SLOT_EXTRACTOR_CLARIFY_CONFIDENCE_THRESHOLD:
      low_confidence_issues.append(
          ValidationIssue(
              step_id=step.step_id,
              code="missing_slot",
              slot="extract_confidence",
              detail=(
                  f"Low slot extraction confidence ({conf:.2f}) <= "
                  f"{_SLOT_EXTRACTOR_CLARIFY_CONFIDENCE_THRESHOLD:.2f}."),
              candidates=[],
          ))
  if low_confidence_issues:
    pre_issues = low_confidence_issues + pre_issues
  pre_missing_step_ids = sorted({
      issue.step_id for issue in pre_issues
      if issue.code == "missing_slot" and isinstance(issue.step_id, str) and issue.step_id
  })
  _push("slot_validator", "done", {
      "stage": "pre",
      "missing_slots_count": len(pre_missing_step_ids),
      "missing_step_ids": pre_missing_step_ids,
  })
  trace["node_outputs"]["slot_validator_pre"] = {
      "validated_plan": _dump_plan(pre_validated_plan),
      "issues_count": len(pre_issues),
      "issues": _dump_issues(pre_issues),
      "missing_slots": _missing_slots_summary(pre_issues),
      "low_confidence_issues": _dump_issues(low_confidence_issues),
      "needs_context_lookup": needs_context_lookup,
  }

  if pre_issues:
    _print_missing_slots_debug(pre_issues, "slot_validator_pre")
    _append_llm(
        "missing_slots:slot_validator_pre",
        json.dumps(_missing_slots_summary(pre_issues), ensure_ascii=False, indent=2),
        model="(validation)",
    )
    question_debug: Dict[str, Any] = {}
    _set_current_node("question_agent")
    _push("question_agent", "running")
    question = await build_clarification_question(normalized_text, pre_validated_plan,
                                                  pre_issues, now_iso, timezone_name, language_code,
                                                  debug_capture=question_debug,
                                                  on_stream_delta=lambda delta: _emit_stream_delta("question_agent", delta))
    _push("question_agent", "done")
    _append_llm(
        "question_agent",
        str(question_debug.get("raw_output") or ""),
        model=str(question_debug.get("model") or "gpt-5-nano"),
        reasoning_effort=question_debug.get("reasoning_effort"),
        thinking_level=question_debug.get("thinking_level"),
    )
    trace["node_outputs"]["question_agent"] = {
        "question": question,
        "issues": _dump_issues(pre_issues),
        "debug": question_debug,
    }
    _remember_pending(pre_validated_plan, pre_issues, "slot_pre_validation_issues", plan_confidence)
    response = {
        "version": "full_agent.v1",
        "status": "needs_clarification",
        "input_as_text": normalized_text,
        "now_iso": now_iso,
        "timezone": timezone_name,
        "language": language_code,
        "plan": _dump_plan(pre_validated_plan),
        "issues": _dump_issues(pre_issues),
        "missing_slots": _missing_slots_summary(pre_issues),
        "question": question,
        "results": [],
    }
    _set_current_node("response")
    _push("response", "done")
    return _attach(response, "slot_pre_validation_issues")

  validated_plan = pre_validated_plan
  issues: List[ValidationIssue] = []
  context_decision: Dict[str, Any] = {
      "action": "continue",
      "reason": None,
      "reason_codes": [],
      "start_date": None,
      "end_date": None,
      "step_id": None,
      "attempt": 0,
  }
  context_provider_input = {
      "session_id_present": bool(session_id),
      "now_date": now_date.isoformat(),
      "timezone": timezone_name,
      "plan": _dump_plan(pre_validated_plan),
  }

  if needs_context_lookup:
    if not any_needs_context:
      # Context not yet loaded — load now based on validated plan
      _set_current_node("context_provider")
      _push("context_provider", "running", {"phase": "post_validation"})
      context_started_at = time.perf_counter()
      context = load_context(session_id, pre_validated_plan, now_date, timezone_name)
      context_elapsed_ms = round((time.perf_counter() - context_started_at) * 1000, 1)
      _push("context_provider", "done", {
          "phase": "post_validation",
          "elapsed_ms": context_elapsed_ms,
      })
      context_provider_input["duration_ms"] = context_elapsed_ms
    trace["node_outputs"]["context_provider"] = {
        "input": context_provider_input,
        "output": _context_debug_output(context),
    }

    max_expand_attempts = 2
    expand_attempt = 0
    resolver_call_budget = 1
    while True:
      slot_decision_debug: List[Dict[str, Any]] = []
      _set_current_node("slot_validator")
      _push("slot_validator", "running", {
          "stage": "context",
          "attempt": expand_attempt + 1
      })
      validated_plan, issues, context_decision = await validate_and_enrich_plan_with_context_decision(
          pre_validated_plan,
          context,
          timezone_name,
          input_as_text=normalized_text,
          now_iso=now_iso,
          language_code=language_code,
          decision_attempt=expand_attempt,
          max_resolver_calls=resolver_call_budget,
          debug_capture=slot_decision_debug,
      )
      context_missing_step_ids = sorted({
          issue.step_id for issue in issues
          if issue.code == "missing_slot" and isinstance(issue.step_id, str) and issue.step_id
      })
      _push("slot_validator", "done", {
          "stage": "context",
          "attempt": expand_attempt + 1,
          "missing_slots_count": len(context_missing_step_ids),
          "missing_step_ids": context_missing_step_ids,
      })
      resolver_call_budget = max(0, resolver_call_budget - len(slot_decision_debug))
      for item in slot_decision_debug:
        resolver_debug = item.get("resolver_debug") if isinstance(item, dict) else {}
        raw_output = ""
        model_name = "gpt-5-mini"
        if isinstance(resolver_debug, dict):
          raw_output = str(resolver_debug.get("raw_output") or "")
          model_name = str(resolver_debug.get("model") or model_name)
        _append_llm("event_target_resolver", raw_output, model=model_name,
                    reasoning_effort=resolver_debug.get("reasoning_effort"),
                    thinking_level=resolver_debug.get("thinking_level"))
      trace["node_outputs"]["slot_validator"] = {
          "validated_plan": _dump_plan(validated_plan),
          "issues_count": len(issues),
          "issues": _dump_issues(issues),
          "missing_slots": _missing_slots_summary(issues),
          "decision": context_decision,
          "attempt": expand_attempt + 1,
      }
      if context_decision.get("action") != "expand_context":
        break

      if expand_attempt >= max_expand_attempts:
        issues.append(
            ValidationIssue(
                step_id=str(context_decision.get("step_id") or "s1"),
                code="missing_slot",
                slot="context_range",
                detail="Reached maximum context expansion attempts.",
                candidates=[],
            ))
        context_decision = {
            "action": "ask_user",
            "reason": "Reached maximum context expansion attempts.",
            "attempt": expand_attempt,
        }
        break

      previous_scope = context.get("scope") if isinstance(context.get("scope"), dict) else {}
      new_start = try_parse_date(context_decision.get("start_date"))
      new_end = try_parse_date(context_decision.get("end_date"))
      if not _is_strictly_broader_scope(previous_scope, new_start, new_end):
        issues.append(
            ValidationIssue(
                step_id=str(context_decision.get("step_id") or "s1"),
                code="invalid_value",
                slot="context_range",
                detail="Expanded range must be strictly broader than previous range.",
                candidates=[],
            ))
        break

      expand_attempt += 1
      _set_current_node("context_provider")
      _push("context_provider", "running", {
          "phase": "expanded",
          "attempt": expand_attempt
      })
      context_started_at = time.perf_counter()
      context = load_context(session_id,
                             pre_validated_plan,
                             now_date,
                             timezone_name,
                             override_start_date=new_start,
                             override_end_date=new_end)
      context_elapsed_ms = round((time.perf_counter() - context_started_at) * 1000, 1)
      _push("context_provider", "done", {
          "phase": "expanded",
          "attempt": expand_attempt,
          "elapsed_ms": context_elapsed_ms,
      })
      trace["node_outputs"]["context_provider"] = {
          "input": {
              **context_provider_input,
              "phase": "expanded",
              "attempt": expand_attempt,
              "duration_ms": context_elapsed_ms,
              "override_start_date": new_start.isoformat() if new_start else None,
              "override_end_date": new_end.isoformat() if new_end else None,
              "previous_scope": previous_scope,
          },
          "output": _context_debug_output(context),
      }

    if context_decision.get("action") == "ask_user" and not issues:
      issues.append(
          ValidationIssue(
              step_id=str(context_decision.get("step_id") or "s1"),
              code="missing_slot",
              slot="event_id",
              detail=str(context_decision.get("reason") or
                         "Need clarification to identify the target event."),
              candidates=[],
          ))
  else:
    trace["node_outputs"]["context_provider"] = {
        "input": context_provider_input,
        "output": {
            "skipped": True,
            "reason": "lookup_not_required",
        },
    }
    trace["node_outputs"]["slot_validator"] = {
        "validated_plan": _dump_plan(validated_plan),
        "issues_count": 0,
        "issues": [],
        "stage": "context_skipped",
    }

  if issues:
    question_debug: Dict[str, Any] = {}
    _set_current_node("question_agent")
    _push("question_agent", "running")
    question = await build_clarification_question(normalized_text, validated_plan,
                                                  issues, now_iso, timezone_name, language_code,
                                                  debug_capture=question_debug,
                                                  on_stream_delta=lambda delta: _emit_stream_delta("question_agent", delta))
    _push("question_agent", "done")
    _append_llm(
        "question_agent",
        str(question_debug.get("raw_output") or ""),
        model=str(question_debug.get("model") or "gpt-5-nano"),
        reasoning_effort=question_debug.get("reasoning_effort"),
        thinking_level=question_debug.get("thinking_level"),
    )
    _print_missing_slots_debug(issues, "slot_validator_context")
    _append_llm(
        "missing_slots:slot_validator_context",
        json.dumps(_missing_slots_summary(issues), ensure_ascii=False, indent=2),
        model="(validation)",
    )
    trace["node_outputs"]["question_agent"] = {
        "question": question,
        "issues": _dump_issues(issues),
        "debug": question_debug,
    }
    _remember_pending(validated_plan, issues, "slot_context_validation_issues", plan_confidence)
    response = {
        "version": "full_agent.v1",
        "status": "needs_clarification",
        "input_as_text": normalized_text,
        "now_iso": now_iso,
        "timezone": timezone_name,
        "language": language_code,
        "plan": _dump_plan(validated_plan),
        "issues": _dump_issues(issues),
        "missing_slots": _missing_slots_summary(issues),
        "question": question,
        "results": [],
    }
    _set_current_node("response")
    _push("response", "done")
    return _attach(response, "slot_context_validation_issues")
  if dry_run:
    trace["node_outputs"]["executor"] = {
        "skipped": True,
        "reason": "dry_run=true",
    }
    response = {
        "version": "full_agent.v1",
        "status": "planned",
        "input_as_text": normalized_text,
        "now_iso": now_iso,
        "timezone": timezone_name,
        "language": language_code,
        "confidence": plan_confidence,
        "plan": _dump_plan(validated_plan),
        "issues": [],
        "results": [],
    }
    _set_current_node("response")
    _push("response", "done")
    _clear_pending()
    return _attach(response, "dry_run_planned")

  ordered_plan = _execution_order(validated_plan)
  results: List[AgentStepResult] = []
  result_by_step_id: Dict[str, AgentStepResult] = {}
  context_for_execution = dict(context)
  if not isinstance(context_for_execution.get("events"), list):
    context_for_execution["events"] = []
  if not isinstance(context_for_execution.get("tasks"), list):
    context_for_execution["tasks"] = []

  _set_current_node("executor")
  _push("executor", "running")
  had_write_mutation = False
  for step in ordered_plan:
    if _is_summary_intent(step.intent):
      step_result = AgentStepResult(step_id=step.step_id,
                                    intent=step.intent,
                                    ok=True,
                                    data={
                                        "skipped": True,
                                        "handled_by": "response_agent",
                                    })
      results.append(step_result)
      result_by_step_id[step.step_id] = step_result
      continue

    step_for_execution = step
    if step.intent == "calendar.update_event":
      args = step.args_dict()
      patched_args = dict(args)
      items_raw = args.get("items")
      patched_items = [dict(item) for item in items_raw if isinstance(item, dict)] if isinstance(
          items_raw, list) else None
      unresolved_indices: List[int] = []
      if isinstance(patched_items, list):
        for item_index, item in enumerate(patched_items):
          event_id = item.get("event_id")
          if isinstance(event_id, str) and event_id.strip():
            continue
          unresolved_indices.append(item_index)
      else:
        event_id = args.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
          unresolved_indices = [0]

      if unresolved_indices:
        dep_event_ids: List[str] = []
        for dep_id in step.depends_on:
          dep_result = result_by_step_id.get(dep_id)
          if not dep_result or not dep_result.ok:
            continue
          dep_data = dep_result.data if isinstance(dep_result.data, dict) else {}
          dep_event_ids_raw = dep_data.get("event_ids")
          if isinstance(dep_event_ids_raw, list):
            for raw_id in dep_event_ids_raw:
              if not isinstance(raw_id, str):
                continue
              clean_id = raw_id.strip()
              if not clean_id or clean_id in dep_event_ids:
                continue
              dep_event_ids.append(clean_id)
          dep_event_id = dep_data.get("event_id")
          if isinstance(dep_event_id, str):
            clean_id = dep_event_id.strip()
            if clean_id and clean_id not in dep_event_ids:
              dep_event_ids.insert(0, clean_id)

        patched = False
        if dep_event_ids:
          if isinstance(patched_items, list) and patched_items:
            if len(unresolved_indices) == 1:
              patched_items[unresolved_indices[0]]["event_id"] = dep_event_ids[0]
              patched = True
            elif len(unresolved_indices) == len(dep_event_ids):
              for unresolved_index, dep_event_id in zip(unresolved_indices, dep_event_ids):
                patched_items[unresolved_index]["event_id"] = dep_event_id
              patched = True
            if patched:
              patched_args["items"] = patched_items
              item_event_ids: List[str] = []
              for item in patched_items:
                value = item.get("event_id")
                if isinstance(value, str) and value.strip():
                  item_event_ids.append(value.strip())
              if len(patched_items) == 1:
                if item_event_ids:
                  patched_args["event_id"] = item_event_ids[0]
                else:
                  patched_args.pop("event_id", None)
                patched_args.pop("event_ids", None)
              else:
                patched_args.pop("event_id", None)
                if len(item_event_ids) == len(patched_items):
                  patched_args["event_ids"] = item_event_ids
                else:
                  patched_args.pop("event_ids", None)
          elif dep_event_ids:
            patched_args["event_id"] = dep_event_ids[0]
            patched = True

        if patched:
          step_for_execution = PlanStep(
              step_id=step.step_id,
              intent=step.intent,
              extract_hint=step.extract_hint,
              args=patched_args,
              query_ranges=step.query_ranges,
              depends_on=step.depends_on,
              on_fail=step.on_fail,
          )
    elif step.intent == "calendar.cancel_event":
      args = step.args_dict()
      event_id = args.get("event_id")
      # Skip dependency resolution for multi cancel.
      is_bulk_cancel = bool(args.get("event_ids"))
      if not is_bulk_cancel and (not isinstance(event_id, str) or not event_id.strip()):
        for dep_id in step.depends_on:
          dep_result = result_by_step_id.get(dep_id)
          if not dep_result or not dep_result.ok:
            continue
          dep_data = dep_result.data if isinstance(dep_result.data, dict) else {}
          dep_event_id = dep_data.get("event_id")
          if isinstance(dep_event_id, str) and dep_event_id.strip():
            patched_args = dict(args)
            patched_args["event_id"] = dep_event_id
            step_for_execution = PlanStep(
                step_id=step.step_id,
                intent=step.intent,
                extract_hint=step.extract_hint,
                args=patched_args,
                query_ranges=step.query_ranges,
                depends_on=step.depends_on,
                on_fail=step.on_fail,
            )
            break
    try:
      data = _execute_step(step_for_execution, session_id, timezone_name, now_iso, context_for_execution, suppress_sse=True)
      step_result = AgentStepResult(step_id=step.step_id,
                                    intent=step.intent,
                                    ok=True,
                                    data=data)
      results.append(step_result)
      result_by_step_id[step.step_id] = step_result
      if _is_mutation_intent(step.intent):
        had_write_mutation = True
      if step.intent in ("calendar.create_event", "calendar.update_event",
                         "calendar.cancel_event"):
        # Refresh event context after calendar mutation for downstream steps.
        try:
          scope = context_for_execution.get("scope")
          if isinstance(scope, dict):
            start_date = try_parse_date(scope.get("start_date"))
            end_date = try_parse_date(scope.get("end_date"))
            if start_date and end_date:
              context_for_execution["events"] = fetch_google_events_between(
                  start_date, end_date, session_id)
        except Exception:
          pass
      if step.intent in ("task.create_task", "task.update_task", "task.cancel_task"):
        try:
          context_for_execution["tasks"] = fetch_google_tasks(session_id)
        except Exception:
          pass
    except HTTPException as exc:
      step_result = AgentStepResult(step_id=step.step_id,
                                    intent=step.intent,
                                    ok=False,
                                    error=str(exc.detail))
      results.append(step_result)
      result_by_step_id[step.step_id] = step_result
      if step.on_fail == "stop":
        trace["node_outputs"]["executor"] = {
            "ordered_plan": _dump_plan(ordered_plan),
            "step_results": _dump_results(results),
            "stopped_at_step_id": step.step_id,
        }
        response = {
            "version": "full_agent.v1",
            "status": "failed",
            "input_as_text": normalized_text,
            "now_iso": now_iso,
            "timezone": timezone_name,
            "language": language_code,
            "confidence": plan_confidence,
            "plan": _dump_plan(ordered_plan),
            "issues": [],
            "results": _dump_results(results),
        }
        _push("executor", "failed", {"step_id": step.step_id})
        _set_current_node("response")
        _push("response", "done")
        if had_write_mutation:
          emit_google_sync(session_id, bump_revision=False)
        _clear_pending()
        return _attach(response, "execution_failed")
    except Exception as exc:
      step_result = AgentStepResult(step_id=step.step_id,
                                    intent=step.intent,
                                    ok=False,
                                    error=str(exc))
      results.append(step_result)
      result_by_step_id[step.step_id] = step_result
      if step.on_fail == "stop":
        trace["node_outputs"]["executor"] = {
            "ordered_plan": _dump_plan(ordered_plan),
            "step_results": _dump_results(results),
            "stopped_at_step_id": step.step_id,
        }
        response = {
            "version": "full_agent.v1",
            "status": "failed",
            "input_as_text": normalized_text,
            "now_iso": now_iso,
            "timezone": timezone_name,
            "language": language_code,
            "confidence": plan_confidence,
            "plan": _dump_plan(ordered_plan),
            "issues": [],
            "results": _dump_results(results),
        }
        _push("executor", "failed", {"step_id": step.step_id})
        _set_current_node("response")
        _push("response", "done")
        if had_write_mutation:
          emit_google_sync(session_id, bump_revision=False)
        _clear_pending()
        return _attach(response, "execution_failed")

  _push("executor", "done")
  trace["node_outputs"]["executor"] = {
      "ordered_plan": _dump_plan(ordered_plan),
      "step_results": _dump_results(results),
      "steps_executed": len(results),
  }
  summary_requests = _summary_requests_from_plan(ordered_plan)
  response_changes = _response_changes_from_results(results)
  response_text: Optional[str] = None
  if response_changes or summary_requests:
    response_agent_debug: Dict[str, Any] = {}
    _set_current_node("response_agent")
    _push("response_agent", "running", {
        "changes_count": len(response_changes),
        "summary_request_count": len(summary_requests),
    })
    response_text = await build_response_text(
        user_text=normalized_text,
        intent=primary_intent,
        now_iso=now_iso,
        timezone_name=timezone_name,
        language_code=language_code,
        changes=response_changes,
        summary_requests=summary_requests,
        context=context_for_execution,
        debug_capture=response_agent_debug,
        on_stream_delta=lambda delta: _emit_stream_delta("response_agent", delta),
    )
    _push("response_agent", "done", {
        "changes_count": len(response_changes),
        "summary_request_count": len(summary_requests),
    })
    _append_response_agent_llm(
        response_agent_debug,
        fallback_output=str(response_text or ""),
    )
    trace["node_outputs"]["response_agent"] = {
        "changes_count": len(response_changes),
        "summary_requests": summary_requests,
        "debug": response_agent_debug,
    }
  else:
    trace["node_outputs"]["response_agent"] = {
        "skipped": True,
        "reason": "no_changes_or_summary_requests",
    }
  response = {
      "version": "full_agent.v1",
      "status": "completed",
      "input_as_text": normalized_text,
      "now_iso": now_iso,
      "timezone": timezone_name,
      "language": language_code,
      "confidence": plan_confidence,
      "plan": _dump_plan(ordered_plan),
      "issues": [],
      "results": _dump_results(results),
      "response_text": response_text,
  }
  _set_current_node("response")
  _push("response", "done")
  if had_write_mutation:
    emit_google_sync(session_id, bump_revision=False)
  _clear_pending()
  return _attach(response, "completed")
