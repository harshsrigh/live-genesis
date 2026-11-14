# Instructions for other code agents (Claude, ChatGPT, Codex)

Open `CLAUDE.md` first. This project is an agent-only civilization experiment, not a license for autonomous agents to purchase services or solicit funds. Follow all the repo's identity, data provenance, budget, human-outside-the-world, and privacy boundaries.

OpenRouter: free models only. `python -m live_genesis.openrouter_setup` reads public catalog metadata only (`--check` before each collection). Follow `docs/OPENROUTER-COLLECTOR.md`. Keep the rules in `providers.py` and `engine._chat` intact: explicit `vendor/model:free` ids, no router alias, no priced OpenRouter endpoint, zero maximum prices, fallbacks off, and a reported charge stops the run. API answers stay in the model archive and are never labelled as chat-app replies.

Workflow: inspect code, write a small change, add an offline test, run `python -m pytest -q`, then describe exact files modified and how to run them. Use `docs/ROADMAP.md` as the single backlog. No live model calls or outside payments without explicit operator instruction.
