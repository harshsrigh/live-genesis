# Sonnet progress checkpoint

Base main SHA: e88066a (open PRs #1 and #12 seen; #12 superseded by #13)
Working branch / PR / latest commit: stacked a -> b -> c. PR #13 https://github.com/harshsrigh/live-genesis/pull/13 (A, base main); PR #14 .../pull/14 (B, base A); PR #15 .../pull/15 (C, base B). Merge in order.
Task A/B/C and status: A, B, C done, PRs open, none merged
Changes completed: A guard + setup port; B offline end-to-end proof; C launcher (operator_launcher.py, Collect-Free.cmd). C's tests fixed a bug (choice "0" picked the last model).
Exact test commands and results: `python -m pytest -q` (WSL venv): A 535, B 537, C 555 passed. No live calls.
Unverified or failing: launcher and Collect-Free.cmd not run on Windows; real OpenRouter cost field shape. Charge on free call = gap, not recorded (check dashboard); interrupted $0 hold stays `held`.
Pending owner decisions: question set; samples 1 vs 3; publication stays local; close #12
Next action: owner runs `Collect-Free.cmd --preview-only`, reviews #13-#15 in order
Local scratch paths (no secrets): none
