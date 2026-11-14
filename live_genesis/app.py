"""The local read-only web app: the Answers Over Time pages rendered from the database.

    python -m uvicorn live_genesis.app:app --host 127.0.0.1 --port 8000

For looking at the archive on your own machine, model track included (the public site is the
static chat-app snapshot, built by snapshot.py). What this process is:
* GET/HEAD only: every other method is refused with 405 before routing; no interactive docs,
  no OpenAPI document, no route that writes;
* the database is opened with SQLite `mode=ro`, so this process cannot write, create or
  migrate it (the operator CLI does that);
* provider API keys are never loaded from `.env` and are removed from the environment.

NOT Internet-ready: no TLS, and the per-address request limit (web.limit_public_requests: in
memory, this process only, /health exempt) bounds requests, not connections or bandwidth.
Bind it to 127.0.0.1. See docs/SECURITY.md.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import drop_provider_secrets
from .web import SECURITY_HEADERS, add_public_routes, apply_security_headers  # noqa: F401 (SECURITY_HEADERS re-exported)

drop_provider_secrets()  # also at import, in case the server runs with lifespan disabled

SAFE_METHODS = ("GET", "HEAD")
@asynccontextmanager
async def lifespan(application: FastAPI):
    drop_provider_secrets()
    yield  # deliberately no init_db(): this process never writes


app = FastAPI(title="Answers Over Time (local, read-only)", version="0.1.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def read_only_and_headers(request: Request, call_next):
    if request.method not in SAFE_METHODS:
        response = JSONResponse({"detail": "This app is read-only"}, status_code=405,
                                headers={"Allow": ", ".join(SAFE_METHODS)})
    else:
        response = await call_next(request)
    apply_security_headers(response, request.url.path)
    return response


add_public_routes(app)
