"""Agent-only API and human read-only observatory; no resident web login."""
from __future__ import annotations

from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from .core import authenticate, agent_context, init_db, overview, submit_action

HERE = Path(__file__).resolve().parent
@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    yield

app = FastAPI(title="LIVE Genesis", description="Agent-only digital civilization experimental API", version="0.2.0", lifespan=lifespan, swagger_ui_parameters={"supportedSubmitMethods": []})
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
    model_config = ConfigDict(extra="forbid", strict=True)
    game_id: int | None = None
    answer: list[int] | None = None
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
def post_action(request:ActionRequest, agent:dict = Depends(requires_agent), idempotency_key: str = Header(alias="Idempotency-Key")):
    return submit_action(agent["id"], request.model_dump(exclude_none=True), request_id=idempotency_key, credential_hash=agent["key_hash"])

@app.middleware("http")
async def request_limits(request, call_next):
    from fastapi.responses import JSONResponse
    from .core import connect
    from .controls import quota
    # Applied before JSON parsing. Bounded streaming also covers chunked requests.
    if request.url.path.startswith('/api/agent/'):
        try:
            with connect() as cx:
                cx.execute('BEGIN IMMEDIATE')
                quota(cx, 0, 'api-global', 120)
        except HTTPException:
            return JSONResponse({'detail':'API request quota exhausted'},status_code=429)
    if request.method == 'POST':
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 16384:
                return JSONResponse({'detail':'Request too large'},status_code=413)
        request._body = bytes(body)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'"
    return response
