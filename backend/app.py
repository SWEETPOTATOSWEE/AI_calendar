from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import (
    CORS_ALLOW_ORIGIN_REGEX,
    FRONTEND_STATIC_DIR,
    cors_origins,
)
from .routes import router
from .state import _load_events_from_disk

app = FastAPI()

print("OPENAI_API_KEY:", bool(os.getenv("OPENAI_API_KEY")))
print("ENABLE_GCAL:", os.getenv("ENABLE_GCAL"))
print("GOOGLE_CLIENT_ID:", bool(os.getenv("GOOGLE_CLIENT_ID")))
print("GOOGLE_REDIRECT_URI:", os.getenv("GOOGLE_REDIRECT_URI"))
print("PWD:", __import__("os").getcwd())

if cors_origins or CORS_ALLOW_ORIGIN_REGEX:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_origin_regex=CORS_ALLOW_ORIGIN_REGEX or None,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

_load_events_from_disk()

app.include_router(router)

if FRONTEND_STATIC_DIR and FRONTEND_STATIC_DIR.exists():
    app.mount("/",
              StaticFiles(directory=str(FRONTEND_STATIC_DIR), html=True),
              name="frontend-static")
