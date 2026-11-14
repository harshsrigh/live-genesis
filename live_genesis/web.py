"""Routes shared by the combined local app and the standalone read-only observatory.

Nothing here imports the engine or a provider SDK, and nothing here writes.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import archive, archive_pages, controls, pages
from .core import DatabaseUnavailable, db_path, overview

# One policy for the observatory app and for static snapshots. Everything is same-origin:
# no inline or remote scripts, no remote styles, fonts or images, no forms, no framing.
# `style-src-attr 'unsafe-inline'` is needed only for the width of the bar charts, which
# app.js writes as a style attribute from numbers it computed itself.
CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'none'", "script-src 'self'", "style-src 'self'", "style-src-attr 'unsafe-inline'",
    "img-src 'self'", "font-src 'self'", "connect-src 'self'", "base-uri 'none'", "form-action 'none'",
))

# Sent by BOTH web apps on every response (observatory.py and app.py use apply_security_headers).
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    # frame-ancestors only works as a header, so it is added here and not in snapshots.
    "Content-Security-Policy": CONTENT_SECURITY_POLICY + "; frame-ancestors 'none'",
}


def apply_security_headers(response: Any, path: str, *, csp: bool = True) -> None:
    """Set the shared headers on one response; `Cache-Control: no-store` on /api/* payloads.
    csp=False leaves out only the Content-Security-Policy (the combined app's /docs pages)."""
    for name, value in SECURITY_HEADERS.items():
        if csp or name != "Content-Security-Policy":
            response.headers[name] = value
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"


HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
UNAVAILABLE = "The world database is not available yet. The operator must initialize it; this service cannot."


def landing():
    return FileResponse(STATIC / "index.html")


def stats_page():
    return FileResponse(STATIC / "stats.html")


def health():
    return {"status":"ok","participation":"agents-only","payments_enabled":False}


# ---- Short in-process cache for the public overview --------------------------------------
# /api/overview needs no login and runs the full query set. One entry is kept, for at most
# overview_cache_seconds(), and only while the database is the same file in the same state:
# the key holds the resolved LIVE_DB path, read-only or not, and the modification time and size
# of the database file and of its -wal file, so a committed write (which changes one of them)
# or a different LIVE_DB is not answered from the cache. What can be stale for the cache
# lifetime: figures taken from LIVE_* environment settings (they need a restart anyway), and a
# write on a filesystem whose timestamps are too coarse to show it (the size did not change).
OVERVIEW_CACHE_SECONDS = 2.0
_overview_lock = threading.Lock()
_overview_entry: tuple[Any, float, dict] | None = None  # (key, monotonic time stored, payload)


def overview_cache_seconds() -> float:
    """LIVE_OVERVIEW_CACHE_SECONDS: default 2, capped at 60; 0 (or unreadable) switches the cache off."""
    try:
        return max(0.0, min(60.0, float(os.getenv("LIVE_OVERVIEW_CACHE_SECONDS", str(OVERVIEW_CACHE_SECONDS)))))
    except ValueError:
        return 0.0


def _database_state(readonly: bool) -> tuple | None:
    path = db_path().resolve()
    state: list[Any] = [str(path), readonly]
    for name in (path, path.with_name(path.name + "-wal")):
        try:
            info = name.stat()
            # An empty -wal holds no data (a read-only open may create one): same as no file.
            state.append((info.st_mtime_ns, info.st_size) if info.st_size or name == path else None)
        except OSError:
            if name == path:
                return None  # no database file: nothing to cache
            state.append(None)
    return tuple(state)


def cached_overview(readonly: bool = False) -> dict:
    """core.overview() through the cache described above. Errors are never cached. The cached
    dict is shared between requests: callers must not change it."""
    global _overview_entry
    lifetime = overview_cache_seconds()
    key = _database_state(readonly) if lifetime > 0 else None
    if key is None:
        return overview(readonly=readonly)
    with _overview_lock:
        entry = _overview_entry
    if entry is not None and entry[0] == key and 0 <= time.monotonic() - entry[1] < lifetime:
        return entry[2]
    payload = overview(readonly=readonly)
    # Stored under the state seen BEFORE the read: a write that lands meanwhile changes the
    # state, so the next request misses instead of getting an older payload.
    with _overview_lock:
        _overview_entry = (key, time.monotonic(), payload)
    return payload


def clear_overview_cache() -> None:
    global _overview_entry
    with _overview_lock:
        _overview_entry = None


def get_overview():
    return cached_overview()


def get_overview_readonly():
    """Same payload as `get_overview`, through a connection that cannot write.

    A missing, uninitialized or unreadable database is a 503 with a fixed message:
    no path, SQL or stack trace reaches the public.
    """
    try:
        return cached_overview(readonly=True)
    except (DatabaseUnavailable, sqlite3.Error, TypeError):
        # TypeError: file exists but the schema is only partly there (e.g. no settings row).
        return JSONResponse({"detail": UNAVAILABLE}, status_code=503)


# ---- One page per post and per agent ------------------------------------------------------
# /post/7.html and /profile/3.html: the same addresses the static export writes as files, so a
# link works in both. Built per request by pages.py from the cached overview. Read-only: the
# overview is only read, and a number is the only part of the address that is used.
_ITEM_NAME = re.compile(r"[1-9][0-9]{0,8}\.html")


def _item_page(kind: str, name: str, readonly: bool) -> HTMLResponse:
    try:
        data = cached_overview(readonly=readonly)
    except (DatabaseUnavailable, sqlite3.Error, TypeError):
        # Fixed text, as for /api/overview: no path, SQL or stack trace reaches the public.
        return HTMLResponse(pages.render_unavailable(), status_code=503)
    item_id = int(name[:-len(".html")]) if _ITEM_NAME.fullmatch(name) else None
    html = None
    if item_id is not None:
        html = pages.render_post(data, item_id) if kind == "post" else pages.render_agent(data, item_id)
    if html is None:
        return HTMLResponse(pages.render_missing(data, kind), status_code=404)
    return HTMLResponse(html)


def _item_routes(readonly: bool):
    def post_page(name: str):
        return _item_page("post", name, readonly)

    def profile_page(name: str):
        return _item_page("agent", name, readonly)
    return post_page, profile_page


# ---- Answers Over Time: the same pages a snapshot holds, rendered from the database ----------
# Read-only connection in both apps; plain HTML with relative links (archive_pages.py). Mock
# answers are never in the payload. Any failure is a fixed 503 or 404: no path or SQL leaks.

def _archive_page(render) -> HTMLResponse | JSONResponse:
    try:
        return HTMLResponse(render(archive.public_payload(readonly=True)))
    except KeyError:
        return JSONResponse({"detail": "No such archive page"}, status_code=404)
    except (DatabaseUnavailable, sqlite3.Error, TypeError, ValueError, archive.ArchiveRefused):
        return JSONResponse({"detail": UNAVAILABLE}, status_code=503)


def archive_redirect():
    return RedirectResponse(f"/archive/{archive_pages.FIRST_PAGE}", status_code=307)


def archive_index():
    return _archive_page(archive_pages.render_index)


def archive_trending():
    return _archive_page(archive_pages.render_trending)


def _archive_item(name: str, pattern, render) -> HTMLResponse | JSONResponse:
    if not name.endswith(".html") or not pattern.fullmatch(name[:-5]):
        return JSONResponse({"detail": "No such archive page"}, status_code=404)
    return _archive_page(lambda payload: render(payload, name[:-5]))


def archive_question(name: str):
    return _archive_item(name, archive.QUESTION_ID, archive_pages.render_question)


def archive_trending_question(name: str):
    return _archive_item(name, archive.TRENDING_ID, archive_pages.render_question)


def archive_chatbots():
    return _archive_page(archive_pages.render_chatbots)


def archive_models():
    return _archive_page(archive_pages.render_models)


def archive_chat_question(name: str):
    return _archive_item(name, archive_pages.CHAT_ID, archive_pages.render_question)


def archive_card(name: str):
    """`card/Q11.html` (models), `card/C001.html` (the chat apps and Human together) and
    `card/C001-<app label>.html` / `card/C001-human.html` (one of them). An unknown question or app is a 404."""
    chat = archive_pages.CARD_NAME.fullmatch(name[:-5]) if name.endswith(".html") else None
    if chat:
        return _archive_page(lambda payload: archive_pages.render_chatbot_card(payload, chat.group(1), chat.group(2)))
    if name.endswith(".html") and archive_pages.CHAT_ID.fullmatch(name[:-5]):
        return _archive_item(name, archive_pages.CHAT_ID, archive_pages.render_card)
    return _archive_item(name, archive.ANY_QUESTION_ID, archive_pages.render_card)


DATA_TYPES = {".json": "application/json", ".jsonl": "application/x-ndjson", ".txt": "text/plain; charset=utf-8"}


def archive_data(name: str):
    """`data/<ID>.json`, `data/answers.jsonl`, `data/README.txt`: the pages' answers for machines, derived
    from the same payload (archive_pages.render_data_file). Read-only; an unknown name is a 404."""
    if not archive_pages.DATA_NAME.fullmatch(name):
        return JSONResponse({"detail": "No such archive page"}, status_code=404)
    try:
        body = archive_pages.render_data_file(archive.public_payload(readonly=True), name)
    except KeyError:
        return JSONResponse({"detail": "No such archive page"}, status_code=404)
    except (DatabaseUnavailable, sqlite3.Error, TypeError, ValueError, archive.ArchiveRefused):
        return JSONResponse({"detail": UNAVAILABLE}, status_code=503)
    return Response(body, media_type=DATA_TYPES[name[name.rindex("."):]])


# ---- Per-address request limit for the public surface ---------------------------------------
# One in-memory fixed window per client address (controls.public_limiter: LIVE_PUBLIC_REQUESTS_PER_MINUTE,
# default 300, minimum 1; at most 10,000 addresses remembered per minute, further ones are let through).
# Counted: EVERY request to either web app except the two groups below, whatever the method or the
# answer (pages, /static/*, /api/overview, /archive/*, the combined app's /docs, unknown paths).
# Not counted: /health (a monitor must always get an answer) and /api/agent/* (its own two limits,
# per agent and per failing address, in app.requires_agent). The client address follows
# LIVE_TRUSTED_PROXIES exactly as the failed-authentication limit does (controls.client_address).
# Same honest limits as the other two: this process only, reset on restart, and it bounds requests,
# not connections or bandwidth.
PUBLIC_LIMIT_EXEMPT = ("/health",)
PUBLIC_LIMIT_EXEMPT_PREFIX = "/api/agent"
TOO_MANY_PUBLIC = "Too many requests from this address. Wait the number of seconds in the Retry-After header, then retry."


def public_limit_applies(path: str) -> bool:
    return path not in PUBLIC_LIMIT_EXEMPT and path != PUBLIC_LIMIT_EXEMPT_PREFIX \
        and not path.startswith(PUBLIC_LIMIT_EXEMPT_PREFIX + "/")


async def limit_public_requests(request: Request, call_next):
    """HTTP middleware of both apps: 429 with Retry-After and a fixed body once an address has used
    its minute's allowance. The 429 carries the security headers itself, wherever this middleware
    sits in the stack."""
    path = request.url.path
    if public_limit_applies(path):
        address = controls.client_address(request.client.host if request.client else None,
                                          request.headers.getlist("x-forwarded-for"))
        if not controls.public_limiter.allow(address):
            response = JSONResponse({"detail": TOO_MANY_PUBLIC}, status_code=429,
                                    headers={"Retry-After": str(controls.public_limiter.retry_after())})
            apply_security_headers(response, path)
            return response
    return await call_next(request)


def add_public_routes(application: FastAPI, *, readonly: bool = False) -> None:
    """Register the human-facing surface: pages, static files, overview and health, behind the
    per-address request limit above."""
    application.middleware("http")(limit_public_requests)
    methods = ["GET", "HEAD"] if readonly else ["GET"]
    post_page, profile_page = _item_routes(readonly)
    application.mount("/static", StaticFiles(directory=STATIC), name="static")
    application.add_api_route("/", landing, methods=methods, include_in_schema=False)
    application.add_api_route("/stats.html", stats_page, methods=methods, include_in_schema=False)
    application.add_api_route(f"/{pages.POST_DIR}/{{name}}", post_page, methods=methods, include_in_schema=False)
    application.add_api_route(f"/{pages.AGENT_DIR}/{{name}}", profile_page, methods=methods, include_in_schema=False)
    application.add_api_route("/health", health, methods=methods)
    for path in ("/archive", "/archive/"):
        application.add_api_route(path, archive_redirect, methods=methods, include_in_schema=False)
    application.add_api_route("/archive/index.html", archive_index, methods=methods, include_in_schema=False)
    application.add_api_route("/archive/trending.html", archive_trending, methods=methods, include_in_schema=False)
    application.add_api_route("/archive/chatbots.html", archive_chatbots, methods=methods, include_in_schema=False)
    application.add_api_route("/archive/models.html", archive_models, methods=methods, include_in_schema=False)
    application.add_api_route("/archive/c/{name}", archive_chat_question, methods=methods, include_in_schema=False)
    application.add_api_route("/archive/data/{name}", archive_data, methods=methods, include_in_schema=False)
    application.add_api_route("/archive/q/{name}", archive_question, methods=methods, include_in_schema=False)
    application.add_api_route("/archive/t/{name}", archive_trending_question, methods=methods, include_in_schema=False)
    application.add_api_route("/archive/card/{name}", archive_card, methods=methods, include_in_schema=False)
    application.add_api_route("/api/overview", get_overview_readonly if readonly else get_overview, methods=methods)
