## Quick start (build + run)

Run the combined script to build the frontend and start the backend in one step:

```bash
/workspaces/AI_calendar/scripts/build-and-run.sh
```

## Backend restart

Restart only the backend server:

```bash
/workspaces/AI_calendar/scripts/restart-backend.sh
```

Override host/port if needed:

```bash
HOST=0.0.0.0 PORT=9000 /workspaces/AI_calendar/scripts/restart-backend.sh
```

## Restart backend on port 8000

If port 8000 is already in use, stop it and start the backend again:

```bash
/workspaces/AI_calendar/scripts/restart-backend-8000.sh
```

## Dev mode (frontend + backend)

Start backend on 8000 and frontend dev server on 3000:

```bash
/workspaces/AI_calendar/scripts/dev-run.sh
```

Override host/port if needed:

```bash
BACKEND_PORT=8000 FRONTEND_PORT=3000 /workspaces/AI_calendar/scripts/dev-run.sh
```

Point the dev frontend to a backend base URL (recommended when using Google login):

```bash
BACKEND_PUBLIC_BASE=https://your-backend-domain:8000 /workspaces/AI_calendar/scripts/dev-run.sh
```

## Dev mode (restart only one)

Restart only the backend (dev):

```bash
/workspaces/AI_calendar/scripts/restart-backend.sh
```

Restart only the frontend (dev):

```bash
/workspaces/AI_calendar/scripts/restart-frontend.sh
```

## Stop processes on ports 3000 and 8000

Kill anything listening on 3000/8000 (frontend + backend):

```bash
/workspaces/AI_calendar/scripts/stop-ports.sh
```

## Google Webhook + SSE (auto sync)

Required env vars:

- `GOOGLE_WEBHOOK_URL`: Public HTTPS URL that Google can reach.
  - Must point to `/auth/google/webhook` on this server.
  - Example: `https://your-domain.com/auth/google/webhook`
- `GOOGLE_WEBHOOK_TOKEN` (recommended): Shared secret used to validate webhook requests.
  - Sent by Google in `X-Goog-Channel-Token`.
  - If set on the server, any request without the same token is rejected.

How watch registration works:

- When a user completes Google login, the server registers watch channels for all
  calendars in that account.
- Each watch expires (Google policy). This server re-registers when expiration is
  within ~1 hour.
- Re-registration happens when the app fetches Google events.

SSE stream:

- The frontend opens `/api/google/stream` while in Google mode.
- When a webhook arrives or a Google event is changed via API, the server emits
  `google_sync` and the client refreshes automatically.

Practical checklist:

1. Make sure your backend is reachable via HTTPS at the `GOOGLE_WEBHOOK_URL`.
2. Set `GOOGLE_WEBHOOK_URL` and optionally `GOOGLE_WEBHOOK_TOKEN` in your env.
3. Restart the backend.
4. Log in to Google once to register watches.
5. Keep using the calendar UI (it fetches events and renews watches near expiry).
