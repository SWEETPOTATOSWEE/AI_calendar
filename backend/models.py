from __future__ import annotations

from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Union


class Event(BaseModel):
    id: int
    title: str
    start: str  # "YYYY-MM-DDTHH:MM"
    end: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    attendees: Optional[List[str]] = None
    reminders: Optional[List[int]] = None
    visibility: Optional[str] = None
    transparency: Optional[str] = None
    meeting_url: Optional[str] = None
    color_id: Optional[str] = None
    recur: Optional[str] = None
    google_event_id: Optional[str] = None
    all_day: bool = False
    created_at: Optional[str] = None
    start_date: Optional[str] = None
    time: Optional[str] = None
    duration_minutes: Optional[int] = None
    recurrence: Optional[Dict[str, Any]] = None
    timezone: Optional[str] = "Asia/Seoul"


class EventCreate(BaseModel):
    title: str
    start: str
    end: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    attendees: Optional[List[str]] = None
    reminders: Optional[List[int]] = None
    visibility: Optional[str] = None
    transparency: Optional[str] = None
    meeting_url: Optional[str] = None
    timezone: Optional[str] = None
    color_id: Optional[str] = None
    recur: Optional[str] = None
    google_event_id: Optional[str] = None
    all_day: Optional[bool] = None
    created_at: Optional[str] = None


class EventUpdate(BaseModel):
    title: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    attendees: Optional[List[str]] = None
    reminders: Optional[List[int]] = None
    visibility: Optional[str] = None
    transparency: Optional[str] = None
    meeting_url: Optional[str] = None
    timezone: Optional[str] = None
    color_id: Optional[str] = None
    all_day: Optional[bool] = None


class RecurrenceEndPayload(BaseModel):
    until: Optional[str] = None
    count: Optional[int] = None


class RecurrencePayload(BaseModel):
    freq: str
    interval: Optional[int] = None
    byweekday: Optional[List[int]] = None
    bymonthday: Optional[List[int]] = None
    bysetpos: Optional[int] = None
    bymonth: Optional[List[int]] = None
    end: Optional[RecurrenceEndPayload] = None


class RecurringEventUpdate(BaseModel):
    title: str
    start_date: str
    time: Optional[str] = None
    duration_minutes: Optional[int] = None
    location: Optional[str] = None
    description: Optional[str] = None
    attendees: Optional[List[str]] = None
    reminders: Optional[List[int]] = None
    visibility: Optional[str] = None
    transparency: Optional[str] = None
    meeting_url: Optional[str] = None
    timezone: Optional[str] = None
    color_id: Optional[str] = None
    recurrence: Optional[RecurrencePayload] = None
    rrule: Optional[str] = None


class RecurringExceptionPayload(BaseModel):
    date: str


class DeleteResult(BaseModel):
    ok: bool
    deleted_ids: List[Union[int, str]]
    count: int


class IdsPayload(BaseModel):
    ids: List[int]


class Task(BaseModel):
    id: str
    title: str
    notes: Optional[str] = None
    due: Optional[str] = None  # ISO datetime
    status: str = "needsAction"  # "needsAction" or "completed"
    completed: Optional[str] = None


class TaskCreate(BaseModel):
    title: str
    notes: Optional[str] = None
    due: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    notes: Optional[str] = None
    due: Optional[str] = None
    status: Optional[str] = None


class AgentRunRequest(BaseModel):
    input_as_text: Optional[str] = None
    timezone: Optional[str] = None
    dry_run: Optional[bool] = False
