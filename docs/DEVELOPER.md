# Developer guide

How to run, test and operate the code. Where things stand: [SESSION-STATE.md](SESSION-STATE.md). The owner's decisions for the product: [PLAN-CHATBOT-COMPARISON.md](PLAN-CHATBOT-COMPARISON.md). Rules and settings in full: [ANSWERS-OVER-TIME.md](ANSWERS-OVER-TIME.md). The owner's monthly round, step by step: [MONTHLY-ROUND.md](MONTHLY-ROUND.md).

<p><img src="brand/lockup.svg" alt="Answers Over Time" width="381" /></p>

## What this is
**Answers Over Time** puts the same questions to consumer chat apps (ChatGPT, Gemini, Grok) and to people, about once a month, and keeps every reply side by side. The public site is static: <https://harshsrigh.github.io/live-genesis/>.

- **Chat-app track** (published): the owner asks the apps by hand and imports the replies from a file. Nothing in this repository opens a chat app. A second group of answerers, models asked the same questions by software (`archive-chatbot-ask`, PAID class), sits beside the apps under its own names.
- **Model track** (not published): `archive-run` asks API or local models a locked question list behind a budget gate. Its pages show only in the local web app.

Until the first hand-collected round is exported and approved, the public site shows scripted samples, labelled as samples on every page. Answers are AI output and may be wrong. Nothing here is a benchmark or a score.

The package is named `live_genesis` because the project began as an agent society, removed on 14 November 2026 (see [ROADMAP.md](ROADMAP.md), "History").

## Quick start (free; no model is called)
```bash
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m pytest -q

export LIVE_DB=/tmp/aot-demo.db                        # a scratch database outside the repository
python -m live_genesis.cli demo-archive                # scripted sample: made-up apps and models, written by hand
python -m live_genesis.snapshot /tmp/aot-site --archive-only    # the public site as a folder; open /tmp/aot-site/index.html
python -m uvicorn live_genesis.app:app --host 127.0.0.1 --port 8000   # local pages, model track included
```
`scripts/wsl-check.sh` does the setup and the free checks in one go on WSL or Linux. Docker: `docker compose run --rm test`, `docker compose up web` (see `compose.yaml`).

## The modules
| File | Job |
|---|---|
| `core.py` | Database connection (`connect(readonly=...)`), `init_db`, usage rows, budget reservations, cumulative reconciliation |
| `controls.py` | Budget gates, per-reservation reconcile, operator audit log, request limiter, backup |
| `config.py` | `.env` split: settings for all processes, provider keys for the two CLI commands that need them |
| `providers.py` | Provider and endpoint settings; `check-provider`, `archive-check-endpoints` |
| `engine.py` | The inference adapters (`infer`): OpenAI, Anthropic, OpenAI-compatible |
| `guards.py` | Content guards and the hidden-character rule |
| `archive.py` | Model track: question files, locks, plan, dry run, paid run, answers, payload, demo |
| `archive_chatbots.py` | Chat-app track: configuration (apps in answerer groups), template, import, the published block |
| `archive_pages.py` | Pages, share cards and data files from a payload; no script |
| `profiles.py` | The "just for fun" answering style per AI answerer: a pure count over its shown answers; display only |
| `trends.py` | `archive-trends`: candidates for trending questions; writes nothing |
| `snapshot.py` | The site build and the reviewed export |
| `web.py`, `app.py` | The read-only local web app |
| `cli.py` | Operator commands |

## The database
SQLite; `LIVE_DB` names the file (default `./live.db`; a relative path is taken from the repository root). `init_db` runs on every CLI command and only adds what is missing.

A fresh database holds: `api_usage`, `budget_reservations`, `cost_reconciliations` (spend estimates), `operator_audit` (append-only), the model track's `archive_sets`, `archive_set_versions`, `archive_questions`, `archive_runs`, `archive_run_sets`, `archive_run_answerers`, `archive_answers`, and the chat-app track's `archive_chatbot_questions`, `archive_chatbot_runs`, `archive_chatbot_conditions`, `archive_chatbot_answers`, `archive_human_collections`, `archive_human_answers`.

A database made before 14 November 2026 also holds the removed society's tables (`agents`, `posts`, `events` and others). The code neither reads nor changes them. `agent_id` on the accounting and answer tables is NULL on everything written now. Back up before upgrading: `python -m live_genesis.cli backup NEW-FILE`.

Append-only tables are protected by triggers. Do not add UPDATE or DELETE paths.

## Commands
```bash
python -m live_genesis.cli init                      # create the database / add missing tables
python -m live_genesis.cli archive-status            # lock state, question files, runs, the chat-app track
python -m live_genesis.cli archive-plan              # questions, models, calls, worst-case estimated cost
python -m live_genesis.cli archive-dry-run           # built-in FAKE answers, stored as mock, never shown
python -m live_genesis.cli archive-check-endpoints   # named endpoints: host, key set yes/no, rates
python -m live_genesis.cli check-provider            # LIVE_PROVIDER, the single compatible endpoint, key set yes/no
python -m live_genesis.cli archive-lock [--set trending]   # audited; a locked question never changes
python -m live_genesis.cli archive-trends            # prints trending search terms; writes nothing
python -m live_genesis.cli demo-archive              # scripted sample, empty database only
python -m live_genesis.cli archive-chatbot-template --out FILE [--date YYYY-MM-DD]
python -m live_genesis.cli archive-chatbot-import FILE --dry-run
python -m live_genesis.cli archive-chatbot-import FILE [--allow-old]    # audited: one hand-entered collection
python -m live_genesis.cli archive-chatbot-ask --dry-run               # the plan for the model answerers; calls nothing
python -m live_genesis.cli reconcile [--provider-usd X | --receipt ID --actual-usd X]
python -m live_genesis.cli backup NEW-FILE
# PAID: only on the owner's explicit word
python -m live_genesis.cli archive-run --max-usd 0.02 [--models a,b] [--set core|trending] [--samples N]
python -m live_genesis.cli archive-chatbot-ask --max-usd 0.02 [--models a,b] [--questions C011,C012] [--pause N]   # the chat-app questions, once, to each model answerer
```
Every command except `archive-run` and `archive-chatbot-ask` (without `--dry-run`) is free and calls no model. Both refuse without `LIVE_ALLOW_PAID_CALLS=YES`, a usable key, valid budget settings and a `--max-usd` the worst case fits under.

## The local web app
```bash
python -m uvicorn live_genesis.app:app --host 127.0.0.1 --port 8000
```
Read-only: GET and HEAD only, the database opened with SQLite `mode=ro`, no provider key in the process, no API, no docs page. `/` leads to `/archive/index.html`. It shows every page, the model track included (`/archive/models.html`, `/archive/trending.html`, `/archive/q/Q01.html`, `/archive/t/T001.html`), which the public site does not. It never creates the database: run `cli init` once first, or it answers 503. `/health` is always answered. Not for the Internet: no TLS; see [SECURITY.md](SECURITY.md).

## Publishing
The public site is built by `.github/workflows/snapshot.yml` on every push to `main`: tests, then the scripted demo archive, then the site, pushed to `gh-pages`.

- No `published/` folder: the scripted demo is the whole site.
- `published/` present: it must pass `python -m live_genesis.snapshot --check`; then the reviewed data is the whole site; the scripted sample is not built beside it (owner's decision, 20 Nov 2026). If the check fails, nothing is published.

The owner's path for a hand-collected round (only the owner runs the export and the approval):
```bash
python -m live_genesis.cli archive-chatbot-import FILE --dry-run     # then without --dry-run
python -m live_genesis.snapshot --export            # writes published/archive.json and MANIFEST.json (chat-app track only)
# read published/archive.json in full
python -m live_genesis.snapshot --approve --i-have-read-it
python -m live_genesis.snapshot --check
git add published && git commit && git push
```
`--archive-only` is accepted on every snapshot command and changes nothing. Model answers are never exported.

Share cards: `node scripts/capture-cards.mjs --site SITE-FOLDER --out OUT` writes each `archive/card/<ID>.html` as a 1080 x 1920 PNG (needs your own Playwright). Posting is by hand: [SOCIAL-POSTS.md](SOCIAL-POSTS.md).

## Settings
`.env.example` lists every setting with a comment. The ones that matter most: `LIVE_DB`, `LIVE_ALLOW_PAID_CALLS`, `LIVE_SPEND_LIMIT_USD`, the two global rates, `LIVE_ARCHIVE_MODELS`, `LIVE_ARCHIVE_ENDPOINTS` and its per-endpoint settings, `LIVE_ARCHIVE_SAMPLES`. Local and free models: [FREE-MODELS.md](FREE-MODELS.md).

## Tests
`python -m pytest -q`. Offline: every model call is a replaced `engine.infer` or a replaced SDK client; `archive._fetch_tags` (the one HTTP request the archive can make, to a loopback Ollama) is replaced in every test.

| File | Covers |
|---|---|
| `test_archive.py`, `test_archive_trending.py`, `test_archive_endpoints.py`, `test_archive_samples.py` | The model track: locks, plan, run, failure rules, pages, payload and publication rules, older databases |
| `test_archive_chatbots.py` | The chat-app track end to end |
| `test_snapshot.py`, `test_snapshot_archive_only.py`, `test_published.py` | The site build, the reviewed export and its checks, the workflow |
| `test_profiles.py` | The answering-style count, its wording and its place on "How it works" |
| `test_trends.py` | `archive-trends` |
| `test_budget.py`, `test_controls.py` | Reservations, gates, reconcile, audit log, limiter, an old database, backup, the CLI command list |
| `test_providers.py`, `test_output_limit.py`, `test_open_items.py` | Provider settings, adapters, no redirect followed, the request limit |
| `test_guards.py`, `test_reaudit.py` | Content guards and the character rule |
| `test_app.py` | The web app: routes, read-only, no keys |
| `test_local_fonts.py` | Bundled fonts |

Old-schema fixtures for upgrade tests are under `tests/fixtures/`.

## Adding something
1. Read `CLAUDE.md` (the rules) and the module you will touch.
2. Keep the change small; add an offline test; run the full suite.
3. A new page or field: render it in `archive_pages.py` through `esc`, add it to the closed key sets if it is published, and keep the page free of script and inline style.
4. A schema change: additive, in the module's `migrate`, tested against a fixture of the schema before it. Never touch a table this code does not own.
5. Update `CLAUDE.md`, this guide and `ROADMAP.md` when the map changes.
