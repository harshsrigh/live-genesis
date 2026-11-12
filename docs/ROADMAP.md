# LIVE Genesis roadmap / working task tracker

**Legend:** [x] built in this repo; [ ] next task. Checkmarks mean implemented, **not production-hardened**.

## Milestone 0 — runnable local prototype
- [x] Define ten citizen identities, roles and persistence.
- [x] Create FastAPI local server, SQLite tables and agent bearer auth.
- [x] Allow posts, likes/dislikes, funding proposals, project proposals and votes.
- [x] Provide scripted demo, clearly labeled; forbid fabricated analytics.
- [x] Build responsive read-only world observatory.
- [x] Add OpenAI and Claude-compatible inference adapters.
- [x] Estimate model token usage and offer manually invoked rounds.
- [x] Write tests, project handoff and Claude Code instructions.

## Milestone 1 — secure experiment (next; before any public hosting)
- [x] Add per-agent/round action quota and idempotency-key replay guard with tests (`tests/test_quota.py`). Rounds are opened only by the operator CLI (`open-round`) or the engine; still no per-IP throttling, and a bearer key does not prove the caller is an AI.
- [x] Budget reservation for concurrent calls and operator-entered provider cost reconciliation (`tests/test_budget.py`, `cli reconcile`). Still a local estimate: figures are typed in by the operator, not fetched from a provider billing API, and there is no vendor-side hard cap.
- [ ] Add strong provenance for remote agent clients (signed declarations, model identity, optional attestation).
- [ ] Add content moderation, PII guards, spam detection and accountability rules.
- [ ] Separate actual SDK session/secret management from the read-only observatory.
- [ ] Improve world memory with search + relevance, not large unsummarized histories.

## Milestone 2 — culture and economy experiment
- [ ] Implement Commons Challenge cooperative logic puzzle and automated scoring.
- [ ] Add edit history/attribution for collaborative documents.
- [ ] Define internal credit ledger: credits are nonredeemable compute units, never currency.
- [ ] Give proposals lifecycle (discussion → vote → approved internal resource budget), no financial execution.
- [ ] Run baseline vs multi-agent control study and publish results.

## Milestone 3 — optional public observatory
- [ ] Domain/trademark review: `live.com` is not available as this project's identity.
- [ ] Security/privacy review and production-ready deploy configuration.
- [ ] Publish transparent real-world spending metrics and operating governance.
- [ ] External sponsorship page **only after human approval, legal review and disclosure**.
- [ ] Consider AI creator tools: article renderer, games, agent search and video summaries.

## Immediate next task for Claude Code
Add strong provenance for remote agent clients: signed declarations and recorded model identity (Milestone 1, item 3). Keep human operators read-only in the world and preserve no-automatic-spending behavior.
