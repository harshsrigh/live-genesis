"""Standalone read-only observatory: the only part of LIVE Genesis meant for human eyes.

    python -m uvicorn live_genesis.observatory:app --host 127.0.0.1 --port 8001

Compared with the combined `live_genesis.app`:
* no `/api/agent/*` routes, no interactive docs, no OpenAPI document;
* every method other than GET/HEAD is refused with 405 before routing;
* the database is opened with SQLite `mode=ro`, so this process cannot write, create
  or migrate it (the operator CLI or the agent API process does that);
* provider API keys are never loaded from `.env` and are removed from the environment.

Still NOT Internet-ready on its own: no TLS, no per-IP throttling, no CSP. See
docs/SECURITY.md.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import drop_provider_secrets
from .web import CONTENT_SECURITY_POLICY, add_public_routes

drop_provider_secrets()  # also at import, in case the server runs with lifespan disabled

SAFE_METHODS = ("GET", "HEAD")
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    # frame-ancestors only works as a header, so it is added here and not in snapshots.
    "Content-Security-Policy": CONTENT_SECURITY_POLICY + "; frame-ancestors 'none'",
}


@asynccontextmanager
async def lifespan(application: FastAPI):
    drop_provider_secrets()
    yield  # deliberately no init_db(): this process never writes


app = FastAPI(title="LIVE Genesis Observatory", version="0.1.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def read_only_and_headers(request: Request, call_next):
    if request.method not in SAFE_METHODS:
        response = JSONResponse({"detail": "This observatory is read-only"}, status_code=405,
                                headers={"Allow": ", ".join(SAFE_METHODS)})
    else:
        response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


add_public_routes(app, readonly=True)
