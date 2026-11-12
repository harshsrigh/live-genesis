# Handoff — LIVE Genesis 0.2

Open the delivered live-genesis folder in Claude Code or Codex. Read CLAUDE.md and AGENTS.md.
The original ZIP was imported and extended in place. Start with `python -m pytest -q` using
the project environment. `Start.cmd` serves the read-only local observatory; `demo-game`
is an explicitly scripted demonstration, never an autonomous AI claim.

Implemented: durable authentication controls/quotas/replay; atomic estimated-budget reservation
and reconciliation; honest source labels; cooperative Commons Challenge and persistent replay.
No billable provider calls, financial transfers, outside contacts, or public deployments were run.

Added after 0.2: per-round per-agent action quotas and operator `set-quota` overrides
(tests/test_round_quota.py). The HTTP tests in that file need FastAPI installed to run.

Next task: choose an unchecked item in docs/ROADMAP.md. The immediate useful improvements
are stronger privacy/moderation and a substantive cooperation benchmark. Preserve all product
boundaries. Live provider verification requires explicit operator instruction and configured prices.
Use offline mocks for normal tests. Never package secrets, databases or the virtual environment.
