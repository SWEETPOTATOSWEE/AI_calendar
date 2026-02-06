from __future__ import annotations

from .config import FRONTEND_DIR, USE_NEXT_FRONTEND


def _load_frontend_html(filename: str) -> str:
    path = FRONTEND_DIR / filename
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if "calendar" in filename:
            return """<!doctype html><html><head><meta charset="utf-8"><title>Calendar</title></head><body>__HEADER_ACTIONS__<p>Frontend assets are not available. Run frontend dev server on port 3000.</p></body></html>"""
        if "settings" in filename:
            return """<!doctype html><html><head><meta charset="utf-8"><title>Settings</title></head><body><p>Frontend assets are not available. Run frontend dev server on port 3000.</p></body></html>"""
        return """<!doctype html><html><head><meta charset="utf-8"><title>Login</title></head><body><p>Frontend assets are not available. Run frontend dev server on port 3000.</p></body></html>"""


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
