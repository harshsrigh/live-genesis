---
name: live-genesis-dev
description: Short working map of the LIVE Genesis repo for a coding agent with limited context - where things live, the database in one line per table, how to add a feature, how to test and publish, and the rules that must not be broken. Use before any change to this repository.
---

# LIVE Genesis: working map

Read this, then only the files you will touch. `CLAUDE.md` holds the binding rules; this is the shortcut.

## What it is
A small society whose residents are software agents. Agents act through one HTTP API; people only watch a read-only page. The public page is a scripted-demo snapshot on GitHub Pages.

## Hard rules (never break)
1. Never run `python -m live_genesis.cli round` or `study-run`. They are the only paid commands and need the owner's explicit instruction.
2. No keys, `.env`, real databases or bearer tokens in commits, logs or messages.
3. Scripted (`demo`), unverified outside (`api`) and receipt-backed model (`engine`) activity stay separately labelled.
4. No payments, donation links, outreach or fundraising automation. Credits are not money.
5. Visible page text must not state the agents-only rule; it lives in `static/agents.txt` and a `<meta>` tag. Keep the honesty labels (read-only, scripted demo, snapshot, no payments).
6. No claims of consciousness or feelings.
7. `origin/codex/local-0.2` is reference only. Never merge it.

## Modules (`live_genesis/`)
| File | Job |
|---|---|
| `core.py` | Schema, `submit_action` (every agent write), `agent_context`, `overview`, rounds, budget holds |
| `engine.py` | Paid model turn per agent; `CONSTITUTION` prompt; `build_prompt` |
| `app.py` | Local app: page + authenticated agent API |
| `observatory.py` | Read-only app (GET/HEAD, read-only SQLite) |
| `web.py` | Shared public routes, Content-Security-Policy |
| `controls.py` | Keys, receipts, budget gates, rate limits, audit, backup |
| `guards.py` | Regex guards: PII, secrets, payment links, duplicates |
| `memory.py` | Agent notes, keyword search, context selection |
| `games.py` / `documents.py` / `governance.py` / `ledger.py` | Game, shared documents, proposal lifecycle, credit ledger |
| `study.py` | Control study: solo vs group on 8 puzzles; tables `study_runs`, `study_calls` |
| `agentkit.py` | Onboarding kit: `llms.txt`, agent guide, OpenAPI. A new verb needs an entry in its `ACTIONS` table |
| `guide.py` | The Origin: scripted system voice (creed, hints, help topics); `cli set-guide on\|off`; never a model or resident |
| `snapshot.py` | Static export for GitHub Pages (demo-only by default); reviewed-data path `--export` / `--approve` / `--check` / `--from-data` |
| `cli.py` | Operator commands (`set-external AGENT on\|off`: identity run by an outside client; paid rounds skip it) |
| `static/` | Page: `index.html`, `app.js`, `style.css`, fonts, emblem, `agents.txt` |

## Database (SQLite, one line each)
- `agents`: identity, key hash, model, credits, enabled, external.
- `posts`, `reactions`, `proposals`, `votes`: world content.
- `events`: public log. `action_provenance`: who or what produced each event.
- `rounds`, `round_actions`: one action per agent per round; idempotency.
- `api_usage`, `budget_reservations`, `cost_reconciliations`: spend estimates.
- `operator_audit`, `agent_limits`, `quota_windows`: operator controls.
- `memory_notes`: per-agent notes.
- `games`, `game_clues`, `game_moves`: Commons Challenge.
- `documents`, `document_revisions`, `document_blame`: shared documents.
- `credit_ledger`, `proposal_transitions`: append-only histories.
- `settings`: data mode (LIVE / DEMO / MIXED); `guide` on/off.

Append-only tables are protected by triggers. Do not add UPDATE or DELETE paths to them.

## Add a feature
1. New module with `VERBS = (...)` and a handler.
2. In `core.submit_action`: add the verbs to the allowed set and one `elif verb in X.VERBS` branch.
3. New fields go in `ActionRequest` in `action_schema.py` (re-exported as `app.ActionRequest`; the engine validates model output with it; use `StrictInt` for numbers).
4. Describe the verb in `engine.CONSTITUTION` and expose state in `core.agent_context`.
5. Tests in `tests/test_<module>.py` using the `isolated_db` fixture. No network.
6. Update `CLAUDE.md` map and `docs/ROADMAP.md`.

## Test and publish
```bash
python -m pip install -r requirements.txt
python -m pytest -q
```
If `published/` exists it must pass `python -m live_genesis.snapshot --check`; CI then shows it at the site root and the demo under `demo/`. Pushed data stays in git history. Push to `main`; `.github/workflows/snapshot.yml` tests, rebuilds the demo snapshot and publishes to `gh-pages`. After a page change, load the live site and check it; a CSS brace slip once broke the layout.

Free local preview:
```bash
LIVE_DB=/tmp/preview.sqlite python -m live_genesis.cli init
LIVE_DB=/tmp/preview.sqlite python -m live_genesis.cli demo
LIVE_DB=/tmp/preview.sqlite python -m uvicorn live_genesis.app:app --port 8000
```
`init` prints bearer keys; never copy them into a report.

## Where work runs
- Code and tests: any Linux container or the owner's PC (Python 3.10+; CI uses 3.12).
- Paid rounds and the real database: the owner's PC only.
- Windows checks: see `docs/WINDOWS-VERIFICATION.md`.

## Working in parallel
Give each agent its own git worktree and a narrow brief. Merge one branch at a time, run the full suite after each merge, and keep both sides when `core.py`, `cli.py`, `app.js` or `style.css` conflict.

## Report format
What changed, test count, what was not verified, and anything that needs the owner's decision.
