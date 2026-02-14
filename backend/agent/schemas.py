from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

IntentName = Literal[
    "meta.clarify",
    "meta.summarize",
    "calendar.create_event",
    "calendar.update_event",
    "calendar.cancel_event",
    "task.create_task",
    "task.update_task",
    "task.cancel_task",
]


# ---------------------------------------------------------------------------
#  Intent Router schemas
# ---------------------------------------------------------------------------

class QueryRange(BaseModel):
  """Date range for context queries."""
  model_config = ConfigDict(extra="ignore")

  start_date: str
  end_date: str


class RouterPlanStep(BaseModel):
  """Plan step from the intent router. No args — only routing info."""
  model_config = ConfigDict(extra="ignore")

  step_id: str = Field(min_length=1, max_length=32)
  intent: IntentName
  hint: Optional[str] = None
  extract_hint: Optional[str] = None
  query_ranges: Optional[List[QueryRange]] = None
  depends_on: List[str] = Field(default_factory=list)
  on_fail: Literal["stop", "continue"] = "stop"


class RouterPlannerOutput(BaseModel):
  """Intent router LLM output."""
  model_config = ConfigDict(extra="ignore")

  plan: List[RouterPlanStep] = Field(default_factory=list, max_length=8)
  confidence: float = Field(default=0.0, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
#  Per-intent slot extractor output schemas
# ---------------------------------------------------------------------------

class RecurrenceEndInputSchema(BaseModel):
  model_config = ConfigDict(extra="ignore")
  until: Optional[str] = None
  count: Optional[int] = None


class RecurrenceInputSchema(BaseModel):
  model_config = ConfigDict(extra="ignore")
  freq: Optional[str] = None
  interval: Optional[int] = None
  byweekday: Optional[List[int]] = None
  bymonthday: Optional[List[int]] = None
  bysetpos: Optional[int] = None
  bymonth: Optional[List[int]] = None
  end: Optional[RecurrenceEndInputSchema] = None


class CreateEventItemSchema(BaseModel):
  model_config = ConfigDict(extra="ignore")

  type: Literal["single", "recurring"] = "single"
  title: str = ""
  start: Optional[str] = None
  end: Optional[str] = None
  start_date: Optional[str] = None
  time: Optional[str] = None
  duration_minutes: Optional[int] = None
  location: Optional[str] = None
  description: Optional[str] = None
  reminders: Optional[List[int]] = None
  all_day: Optional[bool] = None
  recurrence: Optional[RecurrenceInputSchema] = None
  rrule: Optional[str] = None


class CreateEventOutput(BaseModel):
  model_config = ConfigDict(extra="ignore")
  items: List[CreateEventItemSchema] = Field(default_factory=list, max_length=10)
  confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class UpdateEventItemSchema(BaseModel):
  model_config = ConfigDict(extra="ignore")

  event_id: Optional[str] = None
  type: Optional[Literal["single", "recurring"]] = None
  title: Optional[str] = None
  start: Optional[str] = None
  end: Optional[str] = None
  start_date: Optional[str] = None
  time: Optional[str] = None
  duration_minutes: Optional[int] = None
  location: Optional[str] = None
  description: Optional[str] = None
  reminders: Optional[List[int]] = None
  all_day: Optional[bool] = None
  recurrence: Optional[RecurrenceInputSchema] = None
  rrule: Optional[str] = None


class UpdateEventOutput(BaseModel):
  model_config = ConfigDict(extra="ignore")

  items: Optional[List[UpdateEventItemSchema]] = Field(default=None, max_length=10)
  event_id: Optional[str] = None
  event_ids: Optional[List[str]] = None
  title: Optional[str] = None
  start: Optional[str] = None
  end: Optional[str] = None
  start_date: Optional[str] = None
  time: Optional[str] = None
  duration_minutes: Optional[int] = None
  location: Optional[str] = None
  description: Optional[str] = None
  reminders: Optional[List[int]] = None
  all_day: Optional[bool] = None
  recurrence: Optional[RecurrenceInputSchema] = None
  rrule: Optional[str] = None
  confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class CancelEventOutput(BaseModel):
  model_config = ConfigDict(extra="ignore")

  event_id: Optional[str] = None
  event_ids: Optional[List[str]] = None
  confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class CreateTaskOutput(BaseModel):
  model_config = ConfigDict(extra="ignore")

  items: Optional[List["TaskCreateItemSchema"]] = Field(default=None, max_length=50)
  title: Optional[str] = None
  notes: Optional[str] = None
  due: Optional[str] = None
  start_date: Optional[str] = None
  time: Optional[str] = None
  recurrence: Optional[RecurrenceInputSchema] = None
  rrule: Optional[str] = None
  confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class TaskCreateItemSchema(BaseModel):
  model_config = ConfigDict(extra="ignore")

  type: Literal["single", "recurring"] = "single"
  title: Optional[str] = None
  notes: Optional[str] = None
  due: Optional[str] = None
  start_date: Optional[str] = None
  time: Optional[str] = None
  recurrence: Optional[RecurrenceInputSchema] = None
  rrule: Optional[str] = None


class TaskMutationItemSchema(BaseModel):
  model_config = ConfigDict(extra="ignore")

  task_id: Optional[str] = None
  title: Optional[str] = None
  notes: Optional[str] = None
  due: Optional[str] = None
  status: Optional[str] = None


class TaskTargetItemSchema(BaseModel):
  model_config = ConfigDict(extra="ignore")

  task_id: Optional[str] = None


class UpdateTaskOutput(BaseModel):
  model_config = ConfigDict(extra="ignore")

  items: Optional[List[TaskMutationItemSchema]] = Field(default=None, max_length=50)
  task_id: Optional[str] = None
  task_ids: Optional[List[str]] = None
  title: Optional[str] = None
  notes: Optional[str] = None
  due: Optional[str] = None
  status: Optional[str] = None
  confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class CancelTaskOutput(BaseModel):
  model_config = ConfigDict(extra="ignore")

  items: Optional[List[TaskTargetItemSchema]] = Field(default=None, max_length=50)
  task_id: Optional[str] = None
  task_ids: Optional[List[str]] = None
  confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
#  Internal plan representation
# ---------------------------------------------------------------------------

class StepArgs(BaseModel):
  model_config = ConfigDict(extra="forbid")

  # Clarification
  reason: Optional[str] = None
  hints: Optional[List[str]] = None

  # Calendar create
  items: Optional[List[Dict[str, Any]]] = None

  # Calendar update / cancel / shared
  event_id: Optional[str] = None
  event_ids: Optional[List[str]] = None
  cancel_ranges: Optional[List[Dict[str, str]]] = None
  title: Optional[str] = None
  start: Optional[str] = None
  end: Optional[str] = None
  location: Optional[str] = None
  description: Optional[str] = None
  reminders: Optional[List[int]] = None
  all_day: Optional[bool] = None

  # Calendar create legacy
  start_date: Optional[str] = None
  end_date: Optional[str] = None
  scope: Optional[str] = None
  time: Optional[str] = None
  duration_minutes: Optional[int] = None
  recurrence: Optional[Dict[str, Any]] = None
  rrule: Optional[str] = None

  # Task
  task_id: Optional[str] = None
  task_ids: Optional[List[str]] = None
  notes: Optional[str] = None
  due: Optional[str] = None
  status: Optional[str] = None


class PlanStep(BaseModel):
  model_config = ConfigDict(extra="forbid")

  step_id: str = Field(min_length=1, max_length=32)
  intent: IntentName
  extract_hint: Optional[str] = None
  args: StepArgs = Field(default_factory=StepArgs)
  query_ranges: Optional[List[Dict[str, str]]] = None
  depends_on: List[str] = Field(default_factory=list)
  on_fail: Literal["stop", "continue"] = "stop"

  def args_dict(self) -> Dict[str, Any]:
    return self.args.model_dump(exclude_none=True)


class PlannerOutput(BaseModel):
  model_config = ConfigDict(extra="forbid")

  plan: List[PlanStep] = Field(default_factory=list, max_length=8)
  confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ValidationIssue(BaseModel):
  model_config = ConfigDict(extra="forbid")

  step_id: str
  code: Literal["missing_slot", "ambiguous_reference", "not_found",
                "invalid_value"]
  slot: Optional[str] = None
  detail: str
  candidates: List[Dict[str, Any]] = Field(default_factory=list)


class AgentStepResult(BaseModel):
  model_config = ConfigDict(extra="forbid")

  step_id: str
  intent: IntentName
  ok: bool
  data: Dict[str, Any] = Field(default_factory=dict)
  error: Optional[str] = None
