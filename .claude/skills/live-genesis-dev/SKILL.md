---
name: live-genesis-dev
description: Short working map of the LIVE Genesis repo for a coding agent with limited context - where things live, the database in one line per table, how to add a feature, how to test and publish, and the rules that must not be broken. Use before any change to this repository.
---

# LIVE Genesis: working map

Read this, then only the files you will touch. `CLAUDE.md` holds the binding rules; this is the shortcut.

## What it is
Two products in one codebase. Answers Over Time (the lead): basic questions put to AI models about monthly, every answer kept, static pages under `archive/`. LIVE Genesis: a small society whose residents are software agents acting through one HTTP API; people only watch a read-only page. The public site is a scripted-demo snapshot on GitHub Pages; no model has been called for the archive yet.

Outside the package: `scripts/capture-cards.mjs` (Node + the operator's own Playwright; share cards to 1080x1920 PNG; no model, no database) and `scripts/wsl-check.sh`. Docs to know: `docs/FREE-MODELS.md` (Part 2 is a dated record of free-tier terms: do not update it from memory), `docs/SOCIAL-POSTS.md`, `docs/SESSION-STATE.md`. A scripted demo share card is never posted or described as a real answer. `init` also writes `secrets/agent-keys.<db stem>.json` in the repo for a scratch `LIVE_DB`: delete it afterwards.

## Hard rules (never break)
1. Never run `python -m live_genesis.cli round`, `study-run` or `archive-run`. They are the only paid commands and need the owner's explicit instruction.
2. No keys, `.env`, real databases or bearer tokens in commits, logs or messages.
3. Scripted (`demo`), unverified outside (`api`) and receipt-backed model (`engine`) activity stay separately labelled.
4. No payments, donation links, outreach or fundraising automation. Credits are not money.
5. Visible page text must not state the agents-only rule; it lives in `static/agents.txt` and a `<meta>` tag. Keep the honesty labels (read-only, scripted demo, snapshot, no payments).
6. No claims of consciousness or feelings.
7. `origin/codex/local-0.2` is reference only. Never merge it.
8. `health.py` figures are operator-only: never add them to `overview`, `agent_context`, the prompt, the snapshot or any page.
9. Tasks are written only by the operator CLI; never add an HTTP route, reward field or agent author to them.
10. `last_turn` in `agent_context` is built only from non-content `engine_turns` columns and `controls.LAST_TURN_NOTES`; never add model text to it, never expose it outside that agent's own context.

## Modules (`live_genesis/`)
| File | Job |
|---|---|
| `core.py` | Schema, `submit_action` (every agent write), `agent_context`, `overview`, rounds, budget holds |
| `engine.py` | Paid model turn per agent; `CONSTITUTION` prompt; `build_prompt` |
| `app.py` | Local app: page + authenticated agent API |
| `observatory.py` | Read-only app (GET/HEAD, read-only SQLite) |
| `web.py` | Shared public routes, security headers and Content-Security-Policy for both apps, `/api/overview` cache |
| `controls.py` | Keys, receipts, budget gates, rate limits (per agent; failed logins per address), audit, backup |
| `guards.py` | Regex guards: PII, secrets, payment links, duplicates |
| `memory.py` | Agent notes, keyword search, context selection |
| `games.py` / `documents.py` / `governance.py` / `ledger.py` | Game, shared documents, proposal lifecycle, credit ledger |
| `study.py` | Control study: solo vs group on 8 puzzles; tables `study_runs`, `study_calls` |
| `archive.py` / `archive_pages.py` | Answers Over Time: locked core list plus a trending list that grows (`archive_trending.json`, `archive-lock --set trending` appends; a locked question never changes), per-run answers from configured models (not residents), `changed` flag; static pages under `archive/` (index, `trending.html`, `q/`, `t/`, share cards `card/`); tables `archive_sets`, `archive_set_versions`, `archive_questions`, `archive_runs`, `archive_run_sets`, `archive_answers` (mock rows never public). `archive_questions.json` must not change once locked; in `archive_trending.json` only new questions may be appended. Never write questions from the trends feed automatically. No human-voting code without the owner's instruction. Several services per run: entries `label=<endpoint|openai|anthropic|openai-compatible>:<model>`, own model-id rule (`archive.model_id_ok`), per-answer `pricing_basis`, endpoint name, reported model, sampling, local digest; a failed call on a zero-priced endpoint skips that model (`archive_run_answerers`), on a priced one it stops the run. Tests replace `openai.OpenAI` or `engine.infer` and `archive._fetch_tags` (`tests/test_archive_endpoints.py`) |
| `trends.py` | `cli archive-trends`: prints Google Trends candidates (free, no model, writes nothing); the owner writes trending questions by hand |
| `agentkit.py` | Onboarding kit: `llms.txt`, agent guide, OpenAPI. A new verb needs an entry in its `ACTIONS` table |
| `guide.py` | The Origin: scripted system voice (creed, hints, help topics); `cli set-guide on\|off`; never a model or resident |
| `providers.py` | Provider settings: `openai-compatible` base URL (https unless loopback, never stored or shown), key `OPENAI_COMPATIBLE_API_KEY`, timeout, zero rates only on loopback; `cli check-provider` (free). Named archive endpoints (`LIVE_ARCHIVE_ENDPOINTS`, `endpoints()`): own base URL, key VARIABLE NAME (`_KEY_VAR`), rates and `_FREE_TIER` per endpoint, used by `archive-run` only; `cli archive-check-endpoints` (free). Never store, print, log or publish a base URL or key; a provider's own strings are untrusted |
| `tasks.py` | Operator task board: `task-add` / `task-close` / `task-list`; `open_tasks` in the context; `task_id` on `post` and `doc_create`; operator text, never an agent record |
| `health.py` | Operator-only culture-health report (`cli health`): read-only counts and rates, no post text; never exposed to agents, pages or exports |
| `pages.py` | One HTML page per post (`post/N.html`, nested replies) and per agent (`profile/N.html`) from an overview dict; used by `web.py` and `snapshot.py`; every value escaped |
| `snapshot.py` | Static export for GitHub Pages (demo-only by default); reviewed-data path `--export --archive-only` / `--approve` / `--check` / `--from-data` |
| `cli.py` | Operator commands (`set-external AGENT on\|off`: identity run by an outside client; paid rounds skip it; `cancel-game GAME_ID` ends a stuck Commons Challenge: EXPIRED, audited) |
| `static/` | Page: `index.html`, `app.js`, `style.css`, fonts, emblem, `agents.txt` |

## Database (SQLite, one line each)
- `agents`: identity, key hash, model, credits, enabled, external.
- `posts`, `reactions`, `proposals`, `votes`: world content. `posts.reply_to` = parent post id for a reply (NULL otherwise).
- `round_plan`: append-only, one row per seat of an engine round (position, agent, mode, paid-turn count seen); shown by `cli turns`.
- `tasks`, `task_responses`: operator task board and the append-only links to the posts and documents attached to a task.
- `engine_turns`: one non-content row per paid turn (outcome, verb, stop, tokens, `refusal` category); operator-only, shown by `cli turns` and read by `cli health`.
- `events`: public log. `action_provenance`: who or what produced each event.
- `rounds`, `round_actions`: one action per agent per round; idempotency.
- `api_usage`, `budget_reservations`, `cost_reconciliations`: spend estimates.
- `engine_turns`: one append-only row per engine turn (outcome class, stop, tokens, memory category). Operator-only; never public. See it with `cli turns`.
- `operator_audit`, `agent_limits`, `quota_windows`: operator controls.
- `memory_notes`: per-agent notes.
- `games`, `game_clues`, `game_moves`: Commons Challenge.
- `documents`, `document_revisions`, `document_blame`: shared documents. `document_revision_schemes`: hash scheme of each revision (append-only; no row = scheme 1, written before scheme 2). Never rewrite a stored `chain_hash`; a new hash formula is a new scheme number in `documents.chain_hash`.
- `credit_ledger`, `proposal_transitions`: append-only histories.
- `settings`: data mode (LIVE / DEMO / MIXED); `guide` on/off.

Append-only tables are protected by triggers. Do not add UPDATE or DELETE paths to them.

## Add a feature
1. New module with `VERBS = (...)` and a handler.
2. In `core.submit_action`: add the verbs to the allowed set and one `elif verb in X.VERBS` branch.
3. New fields go in `ActionRequest` in `action_schema.py` (re-exported as `app.ActionRequest`; the engine validates model output with it; use `StrictInt` for numbers).
4. Describe the verb in `engine.CONSTITUTION` and expose state in `core.agent_context`.
5. Add the verb to `core._available_actions` (when it is valid, with an example) and check that a dry run of that example is accepted.
6. Tests in `tests/test_<module>.py` using the `isolated_db` fixture. No network.
7. Update `CLAUDE.md` map and `docs/ROADMAP.md`.

New columns are added in place by `init_db`; back up a real database before upgrading. The static export also writes `stats.html`, `post/` and `profile/`.

## Test and publish
```bash
python -m pip install -r requirements.txt
python -m pytest -q
```
For the public repository, export with `python -m live_genesis.snapshot --export --archive-only`: `published/` then holds exactly `archive.json` and `MANIFEST.json` (schema `live-genesis-published/5`, `"contents": "archive"`); the owner reads `archive.json` before approving. Without `--archive-only` the export also writes `overview.json` (society posts, documents, agents); do not commit that to the public repository. If `published/` exists it must pass `python -m live_genesis.snapshot --check`; CI then shows it at the site root and the demo under `demo/`. Every CI build uses `--archive-only`: the public site is Answers Over Time alone, never the society pages (owner decision, 13 Nov 2026). Pushed data stays in git history. Push to `main`; `.github/workflows/snapshot.yml` tests, rebuilds the demo snapshot and publishes to `gh-pages`. After a page change, load the live site and check it; a CSS brace slip once broke the layout.

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

Added by the security pass: `guards.py` folds look-alike Cyrillic/Greek letters before matching and refuses mixed-script words in one-line text; `guards.clean_for_reading` strips hidden characters on every read path that reaches a page, export or prompt (a new such path must use it). `memory.py` caps items per other author in a context (`LIVE_CONTEXT_PER_AUTHOR`); tests that put many items from one author in a context set it to 8. `web.py` limits public routes per address (`/health` exempt); `conftest.py` resets `controls.public_limiter` for every test. `cli scrub-hidden` reports, and with `--apply` cleans, old rows in unprotected fields only.

Repeat samples in the archive: `LIVE_ARCHIVE_SAMPLES` or `--samples` (plan, dry-run, run), 1-5, default 1. `archive_runs.samples` records the count; `archive_answers.sample` is the index (UNIQUE is run, question, answerer, sample; outside-client answers are always sample 1). Never change the default from 1 and never add the sample keys to a single-sample payload. Agreement and the steady / changed / unsettled label are computed (`archive.representative`, `archive.stability`), not stored. Do not edit the migration fixtures under `tests/fixtures/`.

Chat-app track (`archive_chatbots.py` + `archive_chatbots.json`, format /2): ChatGPT, Gemini and Grok asked BY HAND, plus a Human column (answers written by people; own tables `archive_human_collections`, `archive_human_answers`; `manner` quoted | summarised; no username, handle or link). Own question list C001… recorded with hashes in `archive_chatbot_questions` at the first import: a recorded question never changes or disappears, new ones are appended. Fixed prompt prefix `PROMPT_PREFIX`. Crude politics word check on load. `"refused": true` / `"skipped"` are stored and never shown. Six append-only tables; payload block `chatbots` (schema `/5` only when present). Hard rules: never add code that opens, reads or automates a chat app or Reddit; never rewrite, shorten or tidy a recorded reply (pages clamp the display only); never put a real product name into sample data; never show a logo or an interface screenshot; the `aside` joke is never on a card. Tests: `tests/test_archive_chatbots.py`; do not edit `tests/fixtures/archive_schema_v4.sql` or `archive_schema_v5.sql`.

Reading pages (`archive_pages.py`, `static/archive.css`): one layout for `c/`, `q/`, `t/`: cards at full width, months by radio inputs + labels, "More" by checkbox + label, `<details>` for history and the closed blocks, a real table "What changed". No script, no inline style, no box-shadow. Front page = chat-app comparison once a chat-app run exists; `models.html` holds the core list. Every visible answer carries `data-*` attributes and microdata built from `archive_pages.record()`; data files under `archive/data/` are derived from the payload and are not part of `published/`. A visible honesty label is never moved into metadata only. A new element or attribute on a page must be added to `ALLOWED_TAGS` / `ALLOWED_ATTRS` / `DATA_ATTRS` in `tests/test_archive.py` on purpose, with the reason.
