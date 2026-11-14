# Instructions for other code agents (Claude, ChatGPT, Codex)

Open `CLAUDE.md` first. This project is an agent-only civilization experiment, not a license for autonomous agents to purchase services or solicit funds. Follow all the repo's identity, data provenance, budget, human-outside-the-world, and privacy boundaries.

OpenRouter setup: `python -m live_genesis.openrouter_setup` reads public catalog metadata only.
Follow `docs/OPENROUTER-COLLECTOR.md`; preserve explicit free IDs, zero-price request limits,
and disabled fallbacks. API answers remain in the model archive, never labelled as manually collected chat-app replies.

Workflow: inspect code, write a small change, add an offline test, run `python -m pytest -q`, then describe exact files modified and how to run them. Use `docs/ROADMAP.md` as the single backlog. No live model calls or outside payments without explicit operator instruction.
