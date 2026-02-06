# AI Calendar Frontend (Next.js)

This is the new Next.js + Tailwind frontend kept alongside the legacy static HTML in `../frontend/`.

## Getting Started

```bash
npm run dev
```

Then open [http://localhost:3000](http://localhost:3000).

## Configuration

The calendar UI expects the same API as the legacy frontend. Configure via env vars:

- `NEXT_PUBLIC_API_BASE` (default: `/api`)
- `NEXT_PUBLIC_APP_ADMIN` (`1` to enable admin header actions)
- `NEXT_PUBLIC_APP_GOOGLE_LINKED` (`1` when a Google token is present)
- `NEXT_PUBLIC_APP_MODE` (`local`, `google`, or `admin`)

These values are injected into `window.__APP_CONTEXT__` and `window.__API_BASE__` before the calendar script runs.

## Legacy Migration Notes

- Calendar markup is loaded from `templates/calendar-body.html` (extracted from the old `frontend/calendar.html`).
- Interactive behavior is preserved via `public/calendar-app.js` (the legacy script with a configurable API base).
- Styling is intentionally minimal; redesign can be done by replacing styles and gradually refactoring the markup into React components.

## Learn More

- [Next.js Documentation](https://nextjs.org/docs)
- [Learn Next.js](https://nextjs.org/learn)
