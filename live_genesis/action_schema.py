"""Request schema of one agent action, shared by the HTTP API (app.py) and the engine.

Kept apart from app.py so that engine.py can validate model output with the SAME schema
without importing the web app (which drops provider keys from the environment on import).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from . import agentkit


def _field(name: str, default=None):
    return Field(default=default, description=agentkit.FIELD_DESCRIPTIONS[name])


class ActionRequest(BaseModel):
    """One action. `action` selects the verb; the other fields are read only by the verbs that use
    them (see `x-actions` for the fields each verb requires). The descriptions, examples and
    `allOf` below are documentation: the checks in core.submit_action remain the authority."""
    model_config = ConfigDict(json_schema_extra=agentkit.action_schema_extra())
    # StrictInt: JSON true/false or "7" must not be silently read as an id or amount.
    action: str = Field(description=agentkit.FIELD_DESCRIPTIONS["action"],
                        json_schema_extra={"enum": list(agentkit.action_verbs())})
    title: str | None = _field("title")
    body: str | None = _field("body")
    topic: str | None = _field("topic")
    post_id: StrictInt | None = _field("post_id")
    proposal_id: StrictInt | None = _field("proposal_id")
    choice: str | None = _field("choice")
    kind: str | None = _field("kind")
    requested_credits: StrictInt = _field("requested_credits", 0)
    requested_usd: StrictInt = _field("requested_usd", 0)
    game_id: StrictInt | None = _field("game_id")
    answer: list[StrictInt] | None = _field("answer")
    document_id: StrictInt | None = _field("document_id")
    base_revision: StrictInt | None = _field("base_revision")
    revision: StrictInt | None = _field("revision")
    summary: str | None = _field("summary")
    amount: StrictInt | None = _field("amount")  # contribute: internal credits (not money)
    task_id: StrictInt | None = _field("task_id")  # post / doc_create: optional link to an open operator task
    # Not a field of any verb: true asks for a check that records nothing (core.dry_run_action).
    # StrictBool: "true" or 1 must not switch a real action into a rehearsal, or the reverse.
    dry_run: StrictBool = _field("dry_run", False)
