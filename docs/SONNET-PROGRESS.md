# Sonnet progress checkpoint

Base main SHA: e88066a (open PRs not checked: no gh CLI)
Working branch / PR / latest commit: claude/sonnet-takeover-task-b (stacked on task-a 3581de1) / no PR / see git log
Task A/B/C and status: A done (pushed); B done pending review; C not started
Changes completed: B = tests/test_offline_collection_proof.py (3 collections, stub SDK through real infer + archive; disposable DB). A's collector doc corrected for the charge case.
Exact test commands and results: `python -m pytest -q` (WSL venv) 537 passed. No live calls.
Unverified or failing: real OpenRouter cost field shape. FINDINGS: a charge reported on a free call is skipped as `no_usage`, hold released, charge NOT recorded (check dashboard). Interrupted $0 call leaves hold `held` (blocks nothing).
Pending owner decisions: question set; samples 1 vs 3; publication stays local
Next action: Task C Windows launcher reusing cli (setup, model select, plan preview, results)
Local scratch paths (no secrets): none
