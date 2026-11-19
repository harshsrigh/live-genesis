# Instructions for other code agents (Claude, ChatGPT, Codex)

Open `CLAUDE.md` first. This repository is **Answers Over Time**: a static site comparing the replies of chat apps over time, plus a model track that is not published. It is not a license to call models, publish data or spend money: `archive-run` needs the owner's explicit word each time, and only the owner exports and approves published data.

OpenRouter setup: `python -m live_genesis.openrouter_setup` reads public catalog metadata only. Follow `docs/OPENROUTER-COLLECTOR.md`; preserve explicit `:free` IDs, zero-price request limits and disabled fallback. API answers stay in the model track, never labelled as manually collected chat-app replies.

Workflow: inspect the code, write a small change, add an offline test, run `python -m pytest -q`, then describe the exact files modified and how to run them. Use `docs/ROADMAP.md` as the single backlog. Other sessions push to `main` too: fetch and re-merge before a push.
