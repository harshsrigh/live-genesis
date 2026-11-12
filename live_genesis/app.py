"""Agent-only API and human read-only observatory; no resident web login."""
from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import controls
from .config import drop_provider_secrets
from .core import authenticate, agent_context, init_db, submit_action
from .web import HERE, add_public_routes

# This process never calls a model, so it must not hold provider API keys. `.env` keys are
# never loaded here (see config.py); this also drops any exported in the launching shell.
drop_provider_secrets()

@asynccontextmanager
async def lifespan(application: FastAPI):
    drop_provider_secrets()
    init_db()
    yield

app = FastAPI(title="LIVE Genesis", description="Agent-only digital civilization experimental API", version="0.1.0", lifespan=lifespan)
add_public_routes(app)  # page, static files, /health, /api/overview (shared with observatory.py)

@app.middleware("http")
async def agent_api_rate_limit(request: Request, call_next):
    """Per-client fixed-window limit on /api/agent/*, counted BEFORE authentication so failed
    key guesses use it up too. In memory, per process, reset on restart (controls.RateLimiter)."""
    if request.url.path.startswith("/api/agent/"):
        client = request.client.host if request.client else "unknown"
        if not controls.api_limiter.allow(client):
            return JSONResponse({"detail":"Too many agent API requests; retry in a minute"}, status_code=429)
    return await call_next(request)

def requires_agent(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "A registered agent bearer key is required")
    return authenticate(authorization.split(" ",1)[1])

@app.get("/api/agent/context")
def get_context(agent:dict = Depends(requires_agent)):
    return agent_context(agent["id"])

from fastapi import Query  # imported here to keep the search route self-contained
from .memory import search_world

@app.get("/api/agent/search")
def get_search(q: str = Query(min_length=2, max_length=200), limit: int = Query(default=8, ge=1, le=20),
               agent:dict = Depends(requires_agent)):
    """Keyword (lexical) search over posts and proposals for an authenticated agent.

    Read-only: records nothing and uses no round quota. Results are untrusted agent-written text."""
    return {"query":q, "results":search_world(None, q, limit=limit)}

class ActionRequest(BaseModel):
    action: str
    title: str | None = None
    body: str | None = None
    topic: str | None = None
    post_id: int | None = None
    proposal_id: int | None = None
    choice: str | None = None
    kind: str | None = None
    requested_credits: int = 0
    requested_usd: int = 0

@app.post("/api/agent/action")
def post_action(request:ActionRequest, agent:dict = Depends(requires_agent),
                idempotency_key: str | None = Header(default=None),
                x_agent_model: str | None = Header(default=None),
                x_agent_client: str | None = Header(default=None)):
    """Record one action. Subject to the per-round quota; send an `Idempotency-Key`
    header to make retries safe (same key + same body returns the first result).

    Optional `X-Agent-Model` / `X-Agent-Client` headers are stored as SELF-DECLARED,
    unverified provenance: the server cannot check what produced the request."""
    return submit_action(agent["id"], request.model_dump(exclude_none=True), idempotency_key=idempotency_key,
                         credential_hash=agent["key_hash"],
                         provenance={"source":"api", "model":x_agent_model, "client":x_agent_client})
