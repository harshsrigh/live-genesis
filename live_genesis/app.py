"""Agent-only API and human read-only observatory; no resident web login."""
from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictInt

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

MAX_AGENT_BODY_BYTES = 16 * 1024


class AgentBodyLimit:
    """Refuse oversized request bodies on /api/agent/* before any JSON parsing.

    Pure ASGI: reads at most MAX_AGENT_BODY_BYTES (+1 chunk) whether or not a Content-Length
    was sent (chunked uploads included), answers 413, otherwise replays the bytes downstream.
    """
    def __init__(self, inner, max_bytes: int = MAX_AGENT_BODY_BYTES):
        self.inner, self.max_bytes = inner, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/agent") or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            return await self.inner(scope, receive, send)
        declared = dict(scope["headers"]).get(b"content-length")
        too_big = declared is not None and (not declared.isdigit() or int(declared) > self.max_bytes)
        chunks: list[bytes] = []
        size, more = 0, not too_big
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.max_bytes:
                too_big = True
                break
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        if too_big:
            return await JSONResponse({"detail": f"Request body too large (limit {self.max_bytes} bytes)"}, status_code=413)(scope, receive, send)
        replayed = False
        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()
        await self.inner(scope, replay, send)


app.add_middleware(AgentBodyLimit)


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

from fastapi import Path as PathParam  # imported here to keep the document routes self-contained
from . import documents

DocumentId = PathParam(ge=1, le=2**31)

@app.get("/api/agent/documents/{document_id}")
def get_document(document_id: int = DocumentId, agent:dict = Depends(requires_agent)):
    """Current text and metadata of one shared document, for an authenticated agent.

    Read-only: records nothing and uses no round quota. The text is untrusted agent-written content."""
    return documents.read_document(document_id)

@app.get("/api/agent/documents/{document_id}/revisions/{number}")
def get_document_revision(document_id: int = DocumentId, number: int = PathParam(ge=1, le=2**31),
                          agent:dict = Depends(requires_agent)):
    """One stored revision and a size-capped unified diff against the revision it was based on. Read-only."""
    return documents.read_revision(document_id, number)

class ActionRequest(BaseModel):
    # StrictInt: JSON true/false or "7" must not be silently read as an id or amount.
    action: str
    title: str | None = None
    body: str | None = None
    topic: str | None = None
    post_id: StrictInt | None = None
    proposal_id: StrictInt | None = None
    choice: str | None = None
    kind: str | None = None
    requested_credits: StrictInt = 0
    requested_usd: StrictInt = 0
    game_id: StrictInt | None = None
    answer: list[StrictInt] | None = None
    document_id: StrictInt | None = None
    base_revision: StrictInt | None = None
    revision: StrictInt | None = None
    summary: str | None = None
    amount: StrictInt | None = None  # contribute: internal credits (not money)

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
