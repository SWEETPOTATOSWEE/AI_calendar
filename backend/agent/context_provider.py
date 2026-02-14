from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from .schemas import PlanStep
from .normalizer import try_parse_date
from ..gcal import fetch_google_events_between, fetch_google_tasks


def _plan_needs_calendar(plan: List[PlanStep]) -> bool:
  return any(step.intent.startswith("calendar.") or step.intent == "meta.summarize"
             for step in plan)


def _plan_needs_tasks(plan: List[PlanStep]) -> bool:
  return any(step.intent.startswith("task.") or step.intent == "meta.summarize"
             for step in plan)


def _merge_query_ranges(plan: List[PlanStep], now_date: date,
                        default_to_today: bool = True) -> Tuple[Optional[date], Optional[date]]:
  """Merge query_ranges from all steps into a single date range."""
  start_date: Optional[date] = None
  end_date: Optional[date] = None

  for step in plan:
    if not step.query_ranges:
      continue
    for qr in step.query_ranges:
      if not isinstance(qr, dict):
        continue
      qr_start = try_parse_date(qr.get("start_date"))
      qr_end = try_parse_date(qr.get("end_date"))
      if qr_start:
        start_date = qr_start if start_date is None else min(start_date, qr_start)
      if qr_end:
        end_date = qr_end if end_date is None else max(end_date, qr_end)

  # No explicit query range -> fallback to today only.
  if start_date is None and end_date is None and default_to_today:
    return now_date, now_date
  if start_date is None and end_date is not None and default_to_today:
    return end_date, end_date
  if end_date is None and start_date is not None and default_to_today:
    return start_date, start_date

  return start_date, end_date


def _task_due_date(task: Dict[str, Any]) -> Optional[date]:
  due_raw = task.get("due")
  if not isinstance(due_raw, str):
    return None
  due_text = due_raw.strip()
  if len(due_text) < 10:
    return None
  return try_parse_date(due_text[:10])


def load_context(session_id: str, plan: List[PlanStep], now_date: date,
                 timezone_name: str,
                 override_start_date: date | None = None,
                 override_end_date: date | None = None) -> Dict[str, Any]:
  context: Dict[str, Any] = {
      "events": [],
      "tasks": [],
      "scope": None,
  }

  if _plan_needs_calendar(plan):
    start_date = override_start_date
    end_date = override_end_date
    if start_date is None or end_date is None:
      start_date, end_date = _merge_query_ranges(plan, now_date, default_to_today=True)
    if start_date is None or end_date is None:
      raise HTTPException(status_code=422,
                          detail="Calendar context requires query_ranges.")
    if end_date < start_date:
      start_date, end_date = end_date, start_date
    try:
      events = fetch_google_events_between(start_date, end_date, session_id)
    except HTTPException:
      raise
    except Exception as exc:
      raise HTTPException(status_code=502,
                          detail=f"Failed to load calendar context: {exc}") from exc
    context["events"] = events or []
    context["scope"] = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }

  if _plan_needs_tasks(plan):
    task_start = override_start_date
    task_end = override_end_date
    if task_start is None or task_end is None:
      task_start, task_end = _merge_query_ranges(plan, now_date, default_to_today=False)
    if task_start is None or task_end is None:
      context["tasks"] = []
    else:
      if task_end < task_start:
        task_start, task_end = task_end, task_start
      try:
        tasks = fetch_google_tasks(session_id)
      except HTTPException:
        raise
      except Exception as exc:
        raise HTTPException(status_code=502,
                            detail=f"Failed to load tasks context: {exc}") from exc
      filtered: List[Dict[str, Any]] = []
      for task in tasks:
        if not isinstance(task, dict):
          continue
        due_date = _task_due_date(task)
        if due_date is None:
          continue
        if task_start <= due_date <= task_end:
          filtered.append(task)
      context["tasks"] = filtered

  return context
