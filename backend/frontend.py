from __future__ import annotations

from .config import FRONTEND_DIR, USE_NEXT_FRONTEND


def _load_frontend_html(filename: str) -> str:
    path = FRONTEND_DIR / filename
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"Front-end file not found: {path}") from exc


if USE_NEXT_FRONTEND:
    START_HTML = _load_frontend_html("index.html")
    CALENDAR_HTML_TEMPLATE = _load_frontend_html("calendar/index.html")
    SETTINGS_HTML = _load_frontend_html("settings/index.html")
    LOGIN_HTML = _load_frontend_html("login/index.html")
else:
    START_HTML = _load_frontend_html("start.html")
    CALENDAR_HTML_TEMPLATE = _load_frontend_html("calendar.html")
    SETTINGS_HTML = _load_frontend_html("settings.html")
    LOGIN_HTML = START_HTML
