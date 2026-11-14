---
name: live-genesis-dev
description: Short working map of the Answers Over Time repository (package live_genesis) for a coding agent with limited context - where things live, the database in one line per table, how to change something, how to test and publish, and the rules that must not be broken. Use before any change to this repository.
---

# Answers Over Time: working map

Read this, then only the files you will touch. `CLAUDE.md` holds the binding rules; this is the shortcut.

## What it is
A public static site that puts the same questions to consumer chat apps (ChatGPT, Gemini, Grok) and to people every month and keeps every reply. The owner collects the replies **by hand** and imports a file. A second track, which asks API or local models a locked question list, stays in the code and is **not published**. The package is called `live_genesis` for historical reasons: the agent society it began as was removed on 14 November 2026 (`docs/ROADMAP.md`, "History"). Do not rebuild it.

## Hard rules (never break)
1. Never run `python -m live_genesis.cli archive-run`. It is the only command that calls a model and needs the owner's explicit instruction each time, local and free models included.
2. No keys, `.env`, real databases, `secrets/` or endpoint base URLs in commits, logs or messages.
3. Scripted (`demo`), server-asked (`engine`), older self-declared (`api`) and hand-entered (`manual`) answers stay separately labelled. `mock` answers are never shown or exported. A scripted sample is never presented as a real answer.
4. Only the owner runs `snapshot --export` and `--approve`. Never approve for them.
5. The public site and the export hold the chat-app track only. Nothing of the model track is published.
6. Pages carry no script and no inline style; every value goes through `archive_pages.esc`. Keep the honesty labels. The published pages do not say how replies were collected.
7. Append-only tables and locked questions never change. No migration may drop or alter a table this code does not own: an older database still holds the removed society's tables.
8. No payments, donation links, outreach or posting automation. No new dependency without the owner's approval.

## Modules (`live_genesis/`)
| File | Job |
|---|---|
| `core.py` | `connect` (read-only mode), `init_db`, usage rows, budget reservations, cumulative reconcile |
| `controls.py` | Budget gates, per-reservation reconcile, operator audit log, request limiter, backup, hold constants |
| `config.py` | `.env` split: `LIVE_*` settings everywhere; provider keys only for `archive-run`, `check-provider`, `archive-check-endpoints` |
| `providers.py` | `openai`, `anthropic`, the single `openai-compatible` endpoint, named endpoints (`LIVE_ARCHIVE_ENDPOINTS`): base URL rules (https unless loopback, never stored or shown), key variable names, rates |
| `engine.py` | `infer(...)`: the three adapters, no redirect, no retry; `Completion`; `REAL_INFER` |
| `guards.py` | Regex guards (PII, secrets, payment links), hidden-character rule, look-alike folding |
| `archive.py` | Model track: question files, locks, plan, dry run, PAID run, answers, samples, `public_payload`, payload and publication rules, scripted demo |
| `archive_chatbots.py` (+ `archive_chatbots.json`) | Chat-app track: apps, Human column, own questions, template, import, the published block |
| `archive_pages.py` | Pages, share cards, data files from a payload |
| `trends.py` | `archive-trends`: prints candidates, writes nothing |
| `snapshot.py` | Site build; reviewed export `--export` / `--approve` / `--check` / `--from-data` (`--archive-only` accepted, no effect) |
| `web.py`, `app.py` | The one web app: local, read-only, serves the archive pages |
| `cli.py` | `init`, `reconcile`, `backup`, and the commands the modules above register |
| `static/` | `archive.css`, mark, icon, three fonts |

Outside the package: `scripts/capture-cards.mjs` (share cards to PNG with the operator's own Playwright), `scripts/wsl-check.sh`. Docs to know: `docs/DEVELOPER.md`, `docs/ANSWERS-OVER-TIME.md`, `docs/PLAN-CHATBOT-COMPARISON.md`, `docs/SESSION-STATE.md`, `docs/FREE-MODELS.md` (Part 2 is a dated record of free-tier terms: do not update it from memory), `docs/SOCIAL-POSTS.md`.

## Database (SQLite, one line each)
- `api_usage`, `budget_reservations`, `cost_reconciliations`: spend estimates and holds.
- `operator_audit`: append-only log of operator changes.
- `archive_sets`, `archive_set_versions`, `archive_questions`: locked question lists (core; trending grows by version).
- `archive_runs`, `archive_run_sets`, `archive_run_answerers`, `archive_answers`: model runs and their append-only answers (`sample` index per answer).
- `archive_chatbot_questions`, `archive_chatbot_runs`, `archive_chatbot_conditions`, `archive_chatbot_answers`, `archive_human_collections`, `archive_human_answers`: the chat-app track.

Older databases also hold society tables (`agents`, `posts`, `events` ...): never touched. `agent_id` columns are NULL on everything written now.

## Change something
1. Find the module above; read it and its test file.
2. Schema changes are additive, inside that module's `migrate`, with a test against an older-schema fixture (`tests/fixtures/`).
3. Anything published: add it to the closed key sets, escape it on pages, keep labels.
4. Offline test with the `isolated_db` fixture. No network: replace `engine.infer` or the SDK client.
5. Update `CLAUDE.md`, `docs/DEVELOPER.md` and `docs/ROADMAP.md` when the map shifts.

## Test and look
```bash
python -m pip install -r requirements.txt
python -m pytest -q
LIVE_DB=/tmp/preview.sqlite python -m live_genesis.cli demo-archive
LIVE_DB=/tmp/preview.sqlite python -m live_genesis.snapshot /tmp/site --archive-only
LIVE_DB=/tmp/preview.sqlite python -m uvicorn live_genesis.app:app --port 8000
```
The site build refuses a database whose archive is not scripted-demo-only unless `--allow-non-demo` is given.

## Where work runs
- Code and tests: any Linux container or the owner's PC (Python 3.10+; CI uses 3.12). Windows has no Python: tests run through WSL.
- The real database and any model call: the owner's PC only.

## Working in parallel
One git worktree per agent and a narrow brief. Other sessions push to `main` too: fetch and re-merge before a push, run the full suite after each merge.

## Report format
What changed, test count, what was not verified, and anything that needs the owner's decision.
