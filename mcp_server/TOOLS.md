# MCP Tools Reference

This document describes the MCP tools exposed by `mcp_server/server.py`.
Parameter requirements are derived from the tool function signatures.

## Common Notes

- **Auth**: All tools require a Google session. Provide it via the tool parameter
  `session_id` or set `GCAL_SESSION_ID` when running the MCP server.
- **Date/time formats**: The backend expects ISO-like strings for dates/times,
  e.g. `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM` depending on the endpoint.
- **Status codes**: Tools return `{ ok: true, data: ... }` on success or
  `{ ok: false, code: ..., message: ... }` on failure.

## calendar.create_event

Creates one or more calendar events.

Required:
- `items` (array)

Optional:
- `end` (string)
- `location` (string)
- `description` (string)
- `attendees` (string[])
- `reminders` (int[])
- `visibility` (string)
- `transparency` (string)
- `meeting_url` (string)
- `timezone` (string)
- `color_id` (string)
- `all_day` (bool)
- `session_id` (string)

Optional (multi/recurring mode):
- `session_id` (string)

Item schema (best-effort, based on backend `nlp-apply-add`):
- `type`: `"single"` or `"recurring"` (string, required)

For `type: "single"`:
- `title` (string, required)
- `start` (string, required, `YYYY-MM-DDTHH:MM`)
- `end` (string, optional)
- `location`, `description`, `attendees`, `reminders`, `visibility`,
  `transparency`, `meeting_url`, `timezone`, `color_id`, `all_day` (optional)

For `type: "recurring"`:
- `title` (string, required)
- `start_date` (string, required, `YYYY-MM-DD`)
- `time` (string, optional, `HH:MM`)
- `duration_minutes` (int, optional)
- `recurrence` (object, required)
  - `freq` (string, required, e.g. `DAILY`, `WEEKLY`, `MONTHLY`, `YEARLY`)
  - `interval` (int, optional)
  - `byweekday` (int[], optional, 0=Mon ... 6=Sun)
  - `bymonthday` (int[], optional)
  - `bysetpos` (int, optional)
  - `bymonth` (int[], optional)
  - `end` (object, optional)
    - `until` (string, optional, `YYYY-MM-DD`)
    - `count` (int, optional)

Example (single event):
```json
{
  "name": "calendar.create_event",
  "arguments": {
    "items": [
      {
        "type": "single",
        "title": "팀 미팅",
        "start": "2026-02-03T10:00",
        "end": "2026-02-03T11:00",
        "location": "회의실 A"
      }
    ]
  }
}
```

Example (multiple + recurring):
```json
{
  "name": "calendar.create_event",
  "arguments": {
    "items": [
      {
        "type": "single",
        "title": "요가 수업",
        "start": "2026-02-05T19:00",
        "end": "2026-02-05T20:00",
        "location": "스튜디오 B"
      },
      {
        "type": "recurring",
        "title": "주간 회고",
        "start_date": "2026-02-06",
        "time": "17:00",
        "duration_minutes": 60,
        "recurrence": {
          "freq": "WEEKLY",
          "interval": 1,
          "byweekday": [4],
          "end": {
            "count": 8
          }
        },
        "location": "회의실 C"
      }
    ]
  }
}
```

Returns:
- `data` is a list of created events. When a `google_event_id` is present,
  `event_id` is also added for convenience.

## calendar.update_event

Updates an existing Google event.

Required:
- `items` (array)

Optional:
- `start` (string, optional; required if `end` or `all_day` is provided)
- `end` (string)
- `title` (string)
- `location` (string)
- `description` (string)
- `attendees` (string[])
- `reminders` (int[])
- `visibility` (string)
- `transparency` (string)
- `meeting_url` (string)
- `timezone` (string)
- `color_id` (string)
- `all_day` (bool)
- `calendar_id` (string)
- `session_id` (string)

Item schema (multi):
- `event_id` (string, required)
- `start` (string, optional; required if `end` or `all_day` is provided)
- `end`, `title`, `location`, `description`, `attendees`, `reminders`,
  `visibility`, `transparency`, `meeting_url`, `timezone`, `color_id`,
  `all_day`, `calendar_id` (optional)

## calendar.move_event

Moves an event to a new time range.

Required:
- `items` (array)

Optional:
- `end` (string)
- `all_day` (bool)
- `calendar_id` (string)
- `session_id` (string)

Item schema (multi):
- `event_id` (string, required)
- `start` (string, required)
- `end`, `all_day`, `calendar_id` (optional)

## calendar.cancel_event

Deletes an event.

Required:
- `items` (array)

Optional:
- `calendar_id` (string)
- `session_id` (string)

Item schema (multi):
- `event_id` (string, required)
- `calendar_id` (string, optional)

Example (update multiple):
```json
{
  "name": "calendar.update_event",
  "arguments": {
    "items": [
      {
        "event_id": "abc123",
        "start": "2026-02-10T10:00",
        "end": "2026-02-10T11:00",
        "title": "수정된 미팅 제목"
      },
      {
        "event_id": "def456",
        "start": "2026-02-11T14:00",
        "end": "2026-02-11T14:30",
        "location": "회의실 B"
      }
    ]
  }
}
```

Example (move multiple):
```json
{
  "name": "calendar.move_event",
  "arguments": {
    "items": [
      {
        "event_id": "abc123",
        "start": "2026-02-12T09:00",
        "end": "2026-02-12T10:00"
      },
      {
        "event_id": "def456",
        "start": "2026-02-13T16:00"
      }
    ]
  }
}
```

Example (cancel multiple):
```json
{
  "name": "calendar.cancel_event",
  "arguments": {
    "items": [
      { "event_id": "abc123" },
      { "event_id": "def456", "calendar_id": "primary" }
    ]
  }
}
```

## calendar.find_event

Fetches events between dates. Filtering is handled by the backend.

Input (fixed JSON shape):
```json
{
  "ranges": [
    { "start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD" }
  ],
  "query": "string[] | null",
  "calendar_id": "string | null",
  "all_day": "boolean | null",
  "limit": "number | null",
  "session_id": "string | null"
}
```

Range rules:
- `start_date` and `end_date` are required per range.
- `end_date` must be >= `start_date`.
- Duplicate ranges are ignored.

Example:
```json
{
  "ranges": [
    { "start_date": "2026-02-01", "end_date": "2026-02-10" },
    { "start_date": "2026-03-01", "end_date": "2026-03-15" }
  ],
  "query": ["미팅", "회의"],
  "calendar_id": "primary",
  "all_day": false,
  "limit": 50,
  "session_id": null
}
```

Query notes:
- `query`는 문자열 배열만 허용합니다.
- 각 항목을 OR 검색으로 처리합니다.
- `limit`은 필터링 후 적용됩니다.

## calendar.freebusy

**Not implemented** (returns `not_implemented`).

Parameters accepted but ignored:
- `start_date` (string)
- `end_date` (string)
- `calendar_ids` (string[])
- `session_id` (string)

## calendar.summarize

**Not implemented** (returns `not_implemented`).

Parameters accepted but ignored:
- `start_date` (string)
- `end_date` (string)
- `session_id` (string)

## tasks.create_task

Creates a Google task.

Required:
- `title` (string)

Optional:
- `notes` (string)
- `due` (string)
- `session_id` (string)

## tasks.update_task

Updates a Google task.

Required:
- `task_id` (string)

Optional:
- `title` (string)
- `notes` (string)
- `due` (string)
- `status` (string)
- `session_id` (string)

## tasks.complete_task

Marks a task as completed.

Required:
- `task_id` (string)

Optional:
- `session_id` (string)

## tasks.find_task

Fetches tasks and filters locally.

Optional:
- `query` (string)
- `status` (string)
- `limit` (int)
- `session_id` (string)

## tasks.summarize

**Not implemented** (returns `not_implemented`).

Parameters accepted but ignored:
- `status` (string)
- `session_id` (string)

## tasks.plan_timeblock

**Not implemented** (returns `not_implemented`).

Parameters accepted but ignored:
- `task_id` (string)
- `duration_minutes` (int)
- `start_date` (string)
- `end_date` (string)
- `session_id` (string)
