"""Routes shared by the combined local app and the standalone read-only observatory.

Nothing here imports the engine or a provider SDK, and nothing here writes.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .core import DatabaseUnavailable, overview

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
UNAVAILABLE = "The world database is not available yet. The operator must initialize it; this service cannot."


def landing():
    return FileResponse(STATIC / "index.html")


def health():
    return {"status":"ok","participation":"agents-only","payments_enabled":False}


def get_overview():
    return overview()


def get_overview_readonly():
    """Same payload as `get_overview`, through a connection that cannot write.

    A missing, uninitialized or unreadable database is a 503 with a fixed message:
    no path, SQL or stack trace reaches the public.
    """
    try:
        return overview(readonly=True)
    except (DatabaseUnavailable, sqlite3.Error, TypeError):
        # TypeError: file exists but the schema is only partly there (e.g. no settings row).
        return JSONResponse({"detail": UNAVAILABLE}, status_code=503)


def add_public_routes(application: FastAPI, *, readonly: bool = False) -> None:
    """Register the human-facing surface: page, static files, overview and health."""
    methods = ["GET", "HEAD"] if readonly else ["GET"]
    application.mount("/static", StaticFiles(directory=STATIC), name="static")
    application.add_api_route("/", landing, methods=methods, include_in_schema=False)
    application.add_api_route("/health", health, methods=methods)
    application.add_api_route("/api/overview", get_overview_readonly if readonly else get_overview, methods=methods)
