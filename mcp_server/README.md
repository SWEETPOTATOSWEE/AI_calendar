# MCP Adapter Server

This server exposes MCP tools and forwards calls to the existing FastAPI backend.
It is intentionally thin and only uses endpoints that already exist.

## Quick Start

```bash
# from repo root
python mcp_server/server.py
```

## Scripted Run

Use the helper script to run the MCP server and optionally enter the
`GCAL_SESSION_ID` interactively.

```bash
# from repo root
scripts/run-mcp.sh
```

The MCP Streamable HTTP endpoint will be available at:

```
http://localhost:8001/mcp
```

## Environment Variables

- `MCP_HOST` (default: `0.0.0.0`)
- `MCP_PORT` (default: `8001`)
- `BACKEND_BASE_URL` (default: `http://localhost:8000`)
- `BACKEND_API_BASE` (default: `/api`)
- `GCAL_SESSION_ID` (required unless passed per tool call)
- `MCP_BACKEND_TIMEOUT` (default: `15` seconds)

## Tool Reference

Detailed tool inputs and required fields are documented in `mcp_server/TOOLS.md`.

## Notes

- `calendar.create_event` calls `POST /api/nlp-apply-add` to create multiple
  and/or recurring events. Responses include `event_id` mapped from
  `google_event_id` when available.
- `calendar.update_event`, `calendar.move_event`, and `calendar.cancel_event`
  accept `items` for batch operations (the adapter iterates and calls the
  single-event Google endpoint per item).
- The following tools return `not_implemented` by design:
  - `calendar.freebusy`
  - `calendar.summarize`
  - `tasks.summarize`
  - `tasks.plan_timeblock`

## Auth

This adapter forwards the Google session cookie (`gcal_session`) to the backend.
Provide it via the `GCAL_SESSION_ID` environment variable or the tool parameter
`session_id`.
