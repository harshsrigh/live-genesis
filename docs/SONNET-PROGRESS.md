# Sonnet progress checkpoint

Base main SHA: e88066a (origin/main unchanged at start; open PRs not checked, no gh CLI)
Working branch / PR / latest commit: claude/sonnet-takeover-task-a-2d43ba / PR not opened / see git log
Task A/B/C and status: A done pending review; B, C not started
Changes completed: PR #12 (67b0535) ported onto current main: openrouter_setup.py, engine._chat/infer free-only policy, charge rejection (UsageMissing), hardened catalog parsing, guides and collector doc.
Exact test commands and results: `python -m pytest -q` (WSL venv) 535 passed; targeted setup+endpoints 68 passed. No live calls.
Unverified or failing: no real OpenRouter response (cost field shape); openrouter.ai endpoint with positive rates gets no free policy (by design).
Pending owner decisions: question set; samples 1 vs 3; publication stays local
Next action: open PR for review, then Task B (offline end-to-end proof with stub adapter, disposable DB)
Local scratch paths (no secrets): none
