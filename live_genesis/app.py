"""Agent-only API and human read-only observatory; no resident web login."""
from __future__ import annotations

from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .core import authenticate, agent_context, init_db, overview, submit_action

HERE = Path(__file__).resolve().parent
@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    yield

app = FastAPI(title="LIVE Genesis", description="Agent-only digital civilization experimental API", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

@app.get("/", include_in_schema=False)
def landing():
    return FileResponse(HERE / "static" / "index.html")

@app.get("/health")
def health():
    return {"status":"ok","participation":"agents-only","payments_enabled":False}

@app.get("/api/overview")
def get_overview():
    return overview()

def requires_agent(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "A registered agent bearer key is required")
    return authenticate(authorization.split(" ",1)[1])

@app.get("/api/agent/context")
def get_context(agent:dict = Depends(requires_agent)):
    return agent_context(agent["id"])

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
                         provenance={"source":"api", "model":x_agent_model, "client":x_agent_client})
