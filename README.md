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
