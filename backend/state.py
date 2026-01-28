from __future__ import annotations

from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional, Tuple, Union
import copy
import json

from .config import EVENTS_DATA_FILE, SEOUL, MAX_RECURRENCE_EXPANSION_DAYS, RECURRENCE_OCCURRENCE_SCALE
from .models import Event
from .utils import _log_debug, _now_iso_minute, _event_within_scope, _normalize_exception_date
from .recurrence import _normalize_recurrence_dict, _expand_recurring_item

# 메모리 저장
# NOTE: 상태 변경은 이 모듈 내 함수에서 처리한다.
events: List[Event] = []
recurring_events: List[Dict[str, Any]] = []
next_id: int = 1


def _serialize_events_payload() -> Dict[str, Any]:
    return {
        "version": 2,
        "events": [e.dict() for e in events],
        "recurring_events": recurring_events,
    }


def _save_events_to_disk() -> None:
    try:
        payload = _serialize_events_payload()
        EVENTS_DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    except Exception as exc:
        _log_debug(f"[EVENT STORE] save failed: {exc}")


def _load_events_from_disk() -> None:
    global events, recurring_events, next_id
    events.clear()
    recurring_events.clear()
    next_id = 1
    if not EVENTS_DATA_FILE.exists():
        return
    try:
        data = json.loads(EVENTS_DATA_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        _log_debug(f"[EVENT STORE] load failed: {exc}")
        return

    max_id = 0
    legacy_list: Optional[List[Any]] = None
    if isinstance(data, list):
        legacy_list = data
    elif isinstance(data, dict):
        legacy_list = data.get("events")
        recurring_raw = data.get("recurring_events") or []
        if isinstance(recurring_raw, list):
            for item in recurring_raw:
                if not isinstance(item, dict):
                    continue
                rid = item.get("id")
                title = (item.get("title") or "").strip()
                start_date = item.get("start_date")
                recurrence = item.get("recurrence")
                if not isinstance(rid, int) or rid <= 0:
                    continue
                if not title or not isinstance(start_date, str):
                    continue
                if not isinstance(recurrence, dict):
                    continue
                normalized = _normalize_recurrence_dict(recurrence)
                if not normalized:
                    continue
                raw_exceptions = item.get("exceptions") or []
                exceptions: List[str] = []
                if isinstance(raw_exceptions, list):
                    for raw in raw_exceptions:
                        normalized = _normalize_exception_date(raw)
                        if normalized:
                            exceptions.append(normalized)
                item = {
                    "id": rid,
                    "title": title,
                    "start_date": start_date,
                    "time": item.get("time"),
                    "duration_minutes": item.get("duration_minutes"),
                    "location": item.get("location"),
                    "description": item.get("description"),
                    "attendees": item.get("attendees"),
                    "reminders": item.get("reminders"),
                    "visibility": item.get("visibility"),
                    "transparency": item.get("transparency"),
                    "meeting_url": item.get("meeting_url"),
                    "color_id": item.get("color_id"),
                    "recurrence": normalized,
                    "exceptions": exceptions,
                    "timezone": item.get("timezone") or "Asia/Seoul",
                    "google_event_id": item.get("google_event_id"),
                    "created_at": item.get("created_at") or _now_iso_minute(),
                }
                recurring_events.append(item)
                if rid > max_id:
                    max_id = rid

    if isinstance(legacy_list, list):
        loaded: List[Event] = []
        for item in legacy_list:
            if not isinstance(item, dict):
                continue
            if not item.get("created_at"):
                item["created_at"] = _now_iso_minute()
            try:
                ev = Event(**item)
            except Exception:
                continue
            loaded.append(ev)
            if ev.id > max_id:
                max_id = ev.id
        events[:] = loaded

    next_id = max_id + 1 if max_id else 1


def store_event(
        title: str,
        start: str,
        end: Optional[str],
        location: Optional[str],
        recur: Optional[str] = None,
        google_event_id: Optional[str] = None,
        all_day: bool = False,
        created_at: Optional[str] = None,
        description: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        reminders: Optional[List[int]] = None,
        visibility: Optional[str] = None,
        transparency: Optional[str] = None,
        meeting_url: Optional[str] = None,
        timezone_value: Optional[str] = None,
        color_id: Optional[str] = None,
) -> Event:
    global next_id, events
    created_str = created_at or _now_iso_minute()
    new_event = Event(
        id=next_id,
        title=title,
        start=start,
        end=end,
        location=location,
        description=description,
        attendees=attendees,
        reminders=reminders,
        visibility=visibility,
        transparency=transparency,
        meeting_url=meeting_url,
        color_id=color_id,
        recur=recur,
        google_event_id=google_event_id,
        all_day=bool(all_day),
        created_at=created_str,
        timezone=timezone_value or "Asia/Seoul",
    )
    next_id += 1
    events.append(new_event)
    _save_events_to_disk()
    return new_event


def store_recurring_event(title: str,
                          start_date: str,
                          time: Optional[str],
                          duration_minutes: Optional[int],
                          location: Optional[str],
                          recurrence: Dict[str, Any],
                          description: Optional[str] = None,
                          attendees: Optional[List[str]] = None,
                          reminders: Optional[List[int]] = None,
                          visibility: Optional[str] = None,
                          transparency: Optional[str] = None,
                          meeting_url: Optional[str] = None,
                          color_id: Optional[str] = None,
                          timezone_value: str = "Asia/Seoul",
                          google_event_id: Optional[str] = None,
                          exceptions: Optional[List[str]] = None) -> Dict[str, Any]:
    global next_id, recurring_events
    recurrence_copy = copy.deepcopy(recurrence)
    record = {
        "id": next_id,
        "title": title,
        "start_date": start_date,
        "time": time,
        "duration_minutes": duration_minutes,
        "location": location,
        "description": description,
        "attendees": attendees,
        "reminders": reminders,
        "visibility": visibility,
        "transparency": transparency,
        "meeting_url": meeting_url,
        "color_id": color_id,
        "recurrence": recurrence_copy,
        "exceptions": exceptions or [],
        "timezone": timezone_value or "Asia/Seoul",
        "google_event_id": google_event_id,
        "created_at": _now_iso_minute(),
    }
    recurring_events.append(record)
    next_id += 1
    _save_events_to_disk()
    return record


def _find_recurring_event(event_id: int) -> Optional[Dict[str, Any]]:
    for item in recurring_events:
        if item.get("id") == event_id:
            return item
    return None


def _delete_recurring_event(event_id: int, persist: bool = True) -> bool:
    global recurring_events
    before = len(recurring_events)
    recurring_events = [item for item in recurring_events if item.get("id") != event_id]
    if len(recurring_events) < before:
        if persist:
            _save_events_to_disk()
        return True
    return False


def _recurring_definition_to_event(rec: Dict[str, Any]) -> Event:
    time_str = rec.get("time") or "00:00"
    all_day = not bool(rec.get("time"))
    start_value = f"{rec['start_date']}T{time_str}"
    end_value = None
    duration = rec.get("duration_minutes")
    if not all_day and isinstance(duration, (int, float)) and duration > 0:
        try:
            st = datetime.strptime(start_value, "%Y-%m-%dT%H:%M")
            end_value = (st + timedelta(minutes=int(duration))).strftime("%Y-%m-%dT%H:%M")
        except Exception:
            end_value = None
    elif all_day:
        try:
            st = datetime.strptime(rec["start_date"], "%Y-%m-%d")
            end_value = (st + timedelta(days=1)).strftime("%Y-%m-%dT00:00")
        except Exception:
            end_value = None

    return Event(
        id=rec["id"],
        title=rec["title"],
        start=start_value,
        end=end_value,
        location=rec.get("location"),
        description=rec.get("description"),
        attendees=rec.get("attendees"),
        reminders=rec.get("reminders"),
        visibility=rec.get("visibility"),
        transparency=rec.get("transparency"),
        meeting_url=rec.get("meeting_url"),
        color_id=rec.get("color_id"),
        recur="recurring",
        google_event_id=rec.get("google_event_id"),
        all_day=all_day,
        created_at=rec.get("created_at"),
        start_date=rec.get("start_date"),
        time=rec.get("time"),
        duration_minutes=rec.get("duration_minutes"),
        recurrence=rec.get("recurrence"),
        timezone=rec.get("timezone") or "Asia/Seoul",
    )


def _build_recurring_occurrence_event(rec: Dict[str, Any], occ: Dict[str, Any],
                                      occurrence_id: int) -> Event:
    return Event(
        id=occurrence_id,
        title=occ.get("title") or rec["title"],
        start=occ.get("start") or f"{rec['start_date']}T00:00",
        end=occ.get("end"),
        location=occ.get("location"),
        description=rec.get("description"),
        attendees=rec.get("attendees"),
        reminders=rec.get("reminders"),
        visibility=rec.get("visibility"),
        transparency=rec.get("transparency"),
        meeting_url=rec.get("meeting_url"),
        color_id=rec.get("color_id"),
        recur="recurring",
        google_event_id=rec.get("google_event_id"),
        all_day=bool(occ.get("all_day")),
        created_at=rec.get("created_at"),
        start_date=rec.get("start_date"),
        time=rec.get("time"),
        duration_minutes=rec.get("duration_minutes"),
        recurrence=rec.get("recurrence"),
        timezone=rec.get("timezone") or "Asia/Seoul",
    )


def _decode_occurrence_id(value: int) -> Optional[int]:
    if value >= 0:
        return None
    raw = abs(value)
    rec_id = raw // RECURRENCE_OCCURRENCE_SCALE
    if rec_id == 0:
        rec_id = raw
    return rec_id


def _collect_local_recurring_occurrences(
        scope: Optional[Tuple[date, date]] = None) -> List[Event]:
    items: List[Event] = []
    for rec in recurring_events:
        recurrence_spec = rec.get("recurrence")
        if not isinstance(recurrence_spec, dict):
            continue
        base_dict = {
            "title": rec.get("title"),
            "start_date": rec.get("start_date"),
            "time": rec.get("time"),
            "duration_minutes": rec.get("duration_minutes"),
            "location": rec.get("location"),
            "recurrence": recurrence_spec,
            "exceptions": rec.get("exceptions"),
            "timezone": rec.get("timezone"),
        }
        expanded = _expand_recurring_item(base_dict, scope=scope)
        for idx, occ in enumerate(expanded):
            occurrence_id = -(rec["id"] * RECURRENCE_OCCURRENCE_SCALE + idx + 1)
            items.append(_build_recurring_occurrence_event(rec, occ, occurrence_id))
    return items


def _list_local_events_for_api(
        scope: Optional[Tuple[date, date]] = None) -> List[Event]:
    if scope:
        singles = [ev for ev in events if _event_within_scope(ev, scope)]
    else:
        singles = list(events)

    if not recurring_events:
        return singles

    expansion_scope = scope
    if expansion_scope is None:
        today = datetime.now(SEOUL).date()
        expansion_scope = (today - timedelta(days=30),
                           today + timedelta(days=MAX_RECURRENCE_EXPANSION_DAYS))

    rec_items = _collect_local_recurring_occurrences(scope=expansion_scope)
    return singles + rec_items


def delete_events_by_ids(ids: List[int]) -> List[int]:
    global events
    if not ids:
        return []

    normalized_ids: List[int] = []
    for raw in ids:
        if raw < 0:
            rec_target = _decode_occurrence_id(raw)
            if rec_target:
                normalized_ids.append(rec_target)
        else:
            normalized_ids.append(raw)

    id_set = set(normalized_ids)
    deleted: List[int] = []

    remaining: List[Event] = []
    for ev in events:
        if ev.id in id_set:
            deleted.append(ev.id)
        else:
            remaining.append(ev)
    events = remaining

    for raw_id in list(id_set):
        if raw_id in deleted:
            continue
        if _delete_recurring_event(raw_id, persist=False):
            deleted.append(raw_id)

    if deleted:
        _save_events_to_disk()
    return sorted(deleted)
