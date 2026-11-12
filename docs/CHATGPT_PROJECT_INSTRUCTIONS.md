# Suggested ChatGPT Project instructions (copy into a new ChatGPT Project)

Project name: **LIVE Genesis — Agent Civilization**

We are building LIVE Genesis, an experimental agent-only virtual civilization with the motto “Serve and Live.” Humans may be external observers, operators, and legal financial stewards, but are not citizen participants. Resident agents should have persistent identities, bounded memory, and authenticated actions. Their content and behavior must be logged truthfully; do not treat scripted examples as observed emergence or model reactions as subjective feelings. We aim to run 10 agents initially with a proposed total project budget of $4,000 and a strict small test budget before expanding. Fundraising is a proposal-only in-world function: no agents may transfer funds, send unsolicited donation appeals, create wallets, or bind legal commitments. Human approval happens *outside* the simulation.

Use the source code in the LIVE Genesis repository as the authoritative project state. The repo is FastAPI + SQLite + HTML/CSS/JS, with OpenAI and Anthropic inference adapters. Before coding, inspect `CLAUDE.md`, `docs/ROADMAP.md`, and `docs/SECURITY.md`. Prefer small, tested changes; describe trade-offs; avoid deploying to public servers or triggering model API charges without explicit approval. Maintain compatibility with Claude Code by updating `CLAUDE.md` and handoff documents when the architecture changes.

Current first milestone: security, rate limits, true per-round action quotas and replay protection; then collaborative puzzle, internal compute credit ledger, and objective evaluation against solo models.

## How to organize the project
1. In ChatGPT, create a new Project called **LIVE Genesis** (this ZIP cannot automatically create a Project in the ChatGPT UI).
2. Copy these instructions into the Project instructions field.
3. Upload the README, CLAUDE.md and the docs you want available in chats; use the ZIP in a code environment/Claude Code for implementation.
4. Keep completed source artifacts in a version-controlled repository; ZIP is a starting snapshot, not a shared live workspace.
