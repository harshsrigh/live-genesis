"""Routes of the local read-only web app (app.py): the Answers Over Time pages, their static files, /health.

Nothing here imports the engine or a provider SDK, and nothing here writes.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import archive, archive_pages, controls
from .core import DatabaseUnavailable

# One policy for the web app and for static snapshots. Everything is same-origin: no inline or
# remote scripts, no remote styles, fonts or images, no forms, no framing. The archive pages hold
# no script at all and no style attribute; `script-src 'self'`, `style-src-attr 'unsafe-inline'`
# and `connect-src 'self'` are left from the removed observatory page and could be dropped (that
# changes the policy text inside every published page).
CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'none'", "script-src 'self'", "style-src 'self'", "style-src-attr 'unsafe-inline'",
    "img-src 'self'", "font-src 'self'", "connect-src 'self'", "base-uri 'none'", "form-action 'none'",
))

# Sent on every response (app.py uses apply_security_headers).
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    # frame-ancestors only works as a header, so it is added here and not in snapshots.
    "Content-Security-Policy": CONTENT_SECURITY_POLICY + "; frame-ancestors 'none'",
}


def apply_security_headers(response: Any, path: str) -> None:
    """Set the shared headers on one response."""
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value


HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
UNAVAILABLE = "The database is not available yet. The operator must initialize it; this service cannot."


def health():
    return {"status":"ok","payments_enabled":False}


# ---- Answers Over Time: the same pages a snapshot holds, rendered from the database ----------
# Read-only connection; plain HTML with relative links (archive_pages.py). Unlike the public site,
# these pages also show the model track. Mock answers are never in the payload. Any failure is a
# fixed 503 or 404: no path or SQL leaks.

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


def archive_chat_trending():
    """`trending-now.html`: the chat-app track's trending questions. A 404 while none of them has an answer to show."""
    return _archive_page(archive_pages.render_chat_trending)


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


# ---- Per-address request limit ---------------------------------------------------------------
# One in-memory fixed window per client address (controls.public_limiter: LIVE_PUBLIC_REQUESTS_PER_MINUTE,
# default 300, minimum 1; at most 10,000 addresses remembered per minute, further ones are let through).
# Counted: EVERY request except /health (a monitor must always get an answer), whatever the method or the
# answer (pages, /static/*, unknown paths). The client address follows LIVE_TRUSTED_PROXIES
# (controls.client_address). Honest limits: this process only, reset on restart, and it bounds requests,
# not connections or bandwidth.
PUBLIC_LIMIT_EXEMPT = ("/health",)
TOO_MANY_PUBLIC = "Too many requests from this address. Wait the number of seconds in the Retry-After header, then retry."


def public_limit_applies(path: str) -> bool:
    return path not in PUBLIC_LIMIT_EXEMPT


async def limit_public_requests(request: Request, call_next):
    """HTTP middleware: 429 with Retry-After and a fixed body once an address has used its minute's
    allowance. The 429 carries the security headers itself, wherever this middleware sits in the stack."""
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


def add_public_routes(application: FastAPI) -> None:
    """Register the whole surface: the archive pages, static files and health, behind the
    per-address request limit above. `/` leads to the archive."""
    application.middleware("http")(limit_public_requests)
    methods = ["GET", "HEAD"]
    application.mount("/static", StaticFiles(directory=STATIC), name="static")
    application.add_api_route("/health", health, methods=methods)
    for path in ("/", "/archive", "/archive/"):
        application.add_api_route(path, archive_redirect, methods=methods, include_in_schema=False)
    application.add_api_route("/archive/index.html", archive_index, methods=methods, include_in_schema=False)
    application.add_api_route("/archive/trending.html", archive_trending, methods=methods, include_in_schema=False)
    application.add_api_route("/archive/chatbots.html", archive_chatbots, methods=methods, include_in_schema=False)
    application.add_api_route(f"/archive/{archive_pages.CHAT_TRENDING_PAGE}", archive_chat_trending, methods=methods,
                              include_in_schema=False)
    application.add_api_route("/archive/models.html", archive_models, methods=methods, include_in_schema=False)
    application.add_api_route("/archive/c/{name}", archive_chat_question, methods=methods, include_in_schema=False)
    application.add_api_route("/archive/data/{name}", archive_data, methods=methods, include_in_schema=False)
    application.add_api_route("/archive/q/{name}", archive_question, methods=methods, include_in_schema=False)
    application.add_api_route("/archive/t/{name}", archive_trending_question, methods=methods, include_in_schema=False)
    application.add_api_route("/archive/card/{name}", archive_card, methods=methods, include_in_schema=False)
