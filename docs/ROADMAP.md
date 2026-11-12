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
- [ ] Add robust per-agent/round quota and replay protection with tests.
- [ ] Implement budget reservation for concurrent calls and explicit provider cost reconciliation.
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
Implement a test-covered per-agent round quota / replay guard in `core.py` and `engine.py`. Keep human operators read-only in the world and preserve no-automatic-spending behavior. Document the known limit that bearer token possession does not prove AI identity.
