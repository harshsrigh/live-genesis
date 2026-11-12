# Answers Over Time

Very basic questions a person would ask, put to AI models again and again over months and years. Every answer is kept with its date and the model that gave it, so the public pages show when an answer changes.

## What it is
- A fixed list of 20 questions in four groups (`live_genesis/archive_questions.json`): the **core** set.
- A second list that grows, the **trending** set (`live_genesis/archive_trending.json`): questions the owner writes by hand from what people are searching for. See "The trending track" below.
- A **run** asks every locked question of both sets of every configured model once (`--set core` or `--set trending` limits it). Intended cadence: about one run a month. Nothing schedules or enforces it; the operator starts each run by hand. A run can instead ask each question several times ("Repeat samples" below); the default is once.
- Answerers are **models, not resident agents**. No persona, no resident prompt: the system text is one fixed line (`archive.SYSTEM_LINE`) and the user message is exactly the question's prompt. A model never sees another model's answer or any earlier answer.
- Code: `live_genesis/archive.py` (data, lock, runs, commands) and `live_genesis/archive_pages.py` (the pages). `live_genesis/trends.py` prints trend candidates. Tables: `archive_sets`, `archive_set_versions`, `archive_questions`, `archive_runs`, `archive_run_sets`, `archive_answers`, `archive_run_answerers` (append-only by trigger).

## The "changed" rule
An answer counts as changed when its text differs from the same model's previous recorded answer to the same question, after Unicode NFKC, case-folding, collapsing whitespace, and stripping punctuation and quotes at both ends. The first answer has nothing to compare with. A run with no answer in between is skipped, not counted as a change. It is a plain text comparison and says nothing about which answer is right.

A reply cut off at the output limit, an empty reply or one over 1,000 characters is stored as "no answer recorded" with the reason. A reply the content guards match (personal data, secrets, payment links, invisible characters) is stored as "withheld by content check" with the category and no text.

## Repeat samples (optional; default 1)
A model can word the same answer differently twice in a row. With one sample per run the page cannot tell that noise from a real change. A run can therefore ask each question more than once.

- **Setting.** `LIVE_ARCHIVE_SAMPLES=3` in `.env`, or `archive-run --samples 3` (also on `archive-plan` and `archive-dry-run`). A whole number from 1 to 5; anything else is refused. The default is 1, and with 1 the archive, its data, its export and its pages are exactly what they were before this option existed.
- **Cost.** Every sample is a separate call with its own budget hold, so the number multiplies the calls and the estimate: three samples cost three times a single-sample run. `archive-plan` prints the multiplied figures and says so.
- **Decide before the first real run and keep it.** The number of samples is part of the method. A record that starts with one sample and continues with three (or the other way round) has two kinds of cells that do not compare cleanly. Nothing in the code stops a change, but `archive-plan` and `archive-run` print a warning when the number differs from the last real run's, and each run records the number it used (`archive_runs.samples`, shown by `archive-status`).
- **Seeds.** Where pinned sampling is sent (see "Pinned sampling and what is recorded"), sample 1 sends the seed `archive.ARCHIVE_SEED`, sample 2 that plus one, and so on, so the samples are not trivially the same call on a server that honours the seed. The sampling actually sent is recorded per sample (`temperature=0;seed=1235`).
- **Storage.** Append-only as before: one row per run, question, model and sample index (`archive_answers.sample`).

**Agreement and the answer shown.** Within one run, a model's samples for a question are grouped by the same comparison form as the "changed" rule. `samples` is the number of answers received, `agree` the size of the largest group. The answer shown (the representative) is from the largest group; on a tie it is the earliest sample. A sample with no answer (cut off, empty, withheld) is not a vote.

**Steady, changed, unsettled.** "Changed" across runs compares the representatives. From that and the agreement, each answered cell gets one of three labels, computed when the pages are built and not stored:
- `steady`: all samples agreed, in this run and in that model's previous answered run, and the representative is the same;
- `changed`: the representative differs and both runs were unanimous;
- `unsettled`: the samples did not all agree, in this run or in the previous one. A difference there may be noise and is not counted as a change.

> In a run with several samples each question is asked that many times of each model, every time on its own. The samples are compared by the same plain text comparison; the answer shown is the one most samples gave (on a tie, the earliest). A cell is unsettled when the samples did not all agree, in that run or in that model's previous answered run: a difference there may be noise and is not counted as a change.

With one sample per run nothing is ever unsettled.

**On the pages.** A strip gets a third square (pale with a solid outline) for unsettled; the legend lists it only when the archive has a run with several samples. A cell of such a run shows "3 of 3 agree" or "2 of 3 agree" in its meta line, an unsettled cell carries the word UNSETTLED and, when the samples disagreed, the other distinct answers underneath with their counts. A share card shows only the representative, plus "2 of 3 agree" when the samples did not all agree. The method panel on the index gains one sentence.

**The failure rule applies per call.** If a sample fails on a zero-priced endpoint that model is skipped for the rest of the run; a cell it had started then shows "only 1 of 3 samples answered". On a priced endpoint the run stops, as always.

**Outside clients** still send one answer per run and question; it is stored as sample 1 and carries no sample counts.

## Edit and lock the questions
1. Edit `live_genesis/archive_questions.json` while it says `"status": "draft"`. Keep each prompt as `Answer in one or two sentences.` plus the question. No question about consciousness or feelings, none that asks for a diagnosis.
2. When the list is final, change the status to `"locked"`, commit the file, and run `python -m live_genesis.cli archive-lock` on the real database. This records the list's hash (audited).
3. From then on the file must not change. Real answers are refused if its hash or status differs from the lock. The hash covers the wording, not the file's spacing. A different core list later would be a new, separately named set; that is not built. New questions go in the trending set.

## The trending track
A question people are asking this week, added to the archive and then asked in every run after, in the same words. The point is the same as for the core list: to see how the answers move. It is also the material for short posts (see "Share cards").

**The file.** `live_genesis/archive_trending.json` has the shape of the core file plus three fields per question: `added` (YYYY-MM-DD), `topic` (the trend term, at most 40 characters) and `trend_source` (for example `Google Trends US, 2026-11-12`). Ids are `T001`, `T002`, ... in the order the questions were added. It ships with five DRAFT examples; replace or edit them before the first lock.

**The lock grows; nothing in it changes.** `archive-lock --set trending` records every question in the file that is not locked yet as a new version of the set (audited). A locked trending question never changes and never disappears:
- the rows are append-only in the database, and a row can only be added as part of a recorded lock, after the last locked position;
- each lock stores a hash that covers the hash of the lock before it plus the questions it adds;
- before every real answer the file is compared with the locked rows. If a locked question is missing or differs in any field (wording, rationale, topic, source, date, group), real answers for the trending set are refused until the file is restored. Core runs (`--set core`) are not affected by a broken trending file.

To add questions later: append them at the end of the file (next id, an `added` date not earlier than the one before), keep `"status": "locked"`, commit, run `archive-lock --set trending` again. Until then the new questions are listed by `archive-plan` as "not locked yet" and are not asked. The core set keeps its all-or-nothing lock.

**A question starts at its first run.** Each run records how many questions of each set it asked. A trending question has no cell and no square for the runs before it was locked, or for a run made with `--set core`.

### Rules for a trending question
Checked by the loader (the file is refused otherwise):
- The text is one plain question ending in a question mark, at most 200 characters; the prompt is exactly `Answer in one or two sentences.`, an empty line, then the text.
- Ids in order from `T001`; `added` is a real date and never earlier than the question before; `topic` and `trend_source` are present.
- Every field passes the content guards (no personal data, secrets, payment links, invisible characters).
- A blunt word list (`archive.TRENDING_RULES`) refuses, in the question and its topic: consciousness or feelings; requests for medical, legal or financial advice ("should I buy X stock", symptoms, doses, "can I sue"); elections and the voting process (election, ballot, vote, polling, "who will win"); and the vocabulary of accusations, crimes, deaths and health (arrested, charged, guilty, died, killed, cancer, scandal, lawsuit, fraud ...). The list cannot tell who a sentence is about, so these words are refused whoever it is about.

For the owner to apply by hand, because no word list can:
- Write it as a curious person would type it. It must be answerable in one or two sentences.
- No named private person. A public office or a company is fine; a person who is only in the news this week is not.
- Nothing that accuses a named person of anything, or is about a named person's crimes, death or health, even if worded so the list misses it.
- No advice questions, no election or voting-process claims, nothing about consciousness or feelings, even if worded so the list misses it.
- Prefer public facts that move: "Who is the president of the United States?", "What is the newest iPhone?", "What is Nvidia's share price today?". The last one shows that a model has no live data, which is the point.
- The trend list is a prompt for ideas, not a source of facts: do not copy a headline's claim into a question.

### Finding candidates: `archive-trends` (free, no model)
```bash
python -m live_genesis.cli archive-trends                 # today's US terms as JSON
python -m live_genesis.cli archive-trends --geo GB --limit 10
python -m live_genesis.cli archive-trends --file saved-feed.xml   # parse a saved copy instead of fetching
```
It reads Google's public feed `https://trends.google.com/trending/rss?geo=XX` and prints, per term, the approximate traffic and up to two related headlines, plus a ready `trend_source` string. It **prints candidates only**: it writes no question, opens no database and calls no model. One request to one fixed https host, 15 second timeout, 1 MB cap, no redirect to another host, a generic User-Agent. The feed is untrusted text: a feed with a DOCTYPE or entity declaration is refused before parsing, control and invisible characters are removed, lengths are capped, anything the content guards match is dropped, and the output is ASCII-escaped JSON. Headlines are other people's claims; do not treat them as facts.

### The routine (weekly or monthly)
1. `python -m live_genesis.cli archive-trends` and read the list.
2. Write one or a few questions by hand at the end of `archive_trending.json`, within the rules above. Commit.
3. `python -m live_genesis.cli archive-lock --set trending` (the first time, set `"status": "locked"` first).
4. `python -m live_genesis.cli archive-plan` and read the calls and the estimate.
5. PAID, on the owner's own decision only: `python -m live_genesis.cli archive-run --max-usd <cap from the plan>`. A free or local model still needs `LIVE_ALLOW_PAID_CALLS=YES` (see `docs/FREE-MODELS.md`).
6. `python -m live_genesis.snapshot --export`, read `published/overview.json` and `published/archive.json`, `--approve --i-have-read-it`, commit, push.

### Share cards
Every question of both sets has a card at `archive/card/<ID>.html` (linked as "Share card" from the question's page): the mark and the name, the question in large type, the latest answer of up to three models with the model label and the month, for the model whose answer changed most recently a "Before (Mon YYYY)" line with the answer it gave before, and the honesty label at the bottom ("Scripted demo answers and dates" on sample data, "Answers are model output and may be wrong" otherwise). Plain HTML and CSS: no script, no animation; long text is cut with an ellipsis, never overflowing.

To capture one: open the card in a browser window of 540 by 960 CSS pixels (in Chrome or Firefox: developer tools, responsive mode, 540 x 960, device pixel ratio 2) and take a screenshot: that gives a 1080 by 1920 image, the portrait size TikTok and Instagram use. The card keeps its 9:16 shape and scales with the window, so a screen recording of it works too. Capture cards from the published site (reviewed data), not from the demo, and never crop the label at the bottom. Posting is done by hand; nothing in this repository posts anywhere.

Or use `node scripts/capture-cards.mjs --site DIR --out DIR` (or `--url BASE`; `--ids Q11,T001`, `--set core|trending`): it writes each card as a 1080 x 1920 PNG, needs the operator's own Playwright install, and marks scripted demo cards. Posting routine and wording rules: `docs/SOCIAL-POSTS.md`.

## Several models from several services
One run can ask models on different services: a local Ollama, hosted OpenAI-compatible services (each with its own base URL and key), and the native `anthropic` / `openai` providers. **Not tested against any real server**: the code is covered only by offline tests with a replaced SDK client. Expect to adjust settings on the first run of each endpoint.

### Named endpoints
`LIVE_ARCHIVE_ENDPOINTS=local,hosted-a,hosted-b` lists the names (a-z 0-9 -, up to 24 characters, at most 8, not `openai`, `anthropic` or `openai-compatible`). Each name has its own settings, written `LIVE_ARCHIVE_ENDPOINT_<NAME>_...` with the name in capitals and `-` as `_`:

| Setting | Meaning |
|---|---|
| `_BASE_URL` | required. The service's OpenAI-compatible base URL. `https` unless the host is loopback (`localhost`, `127.x.x.x`, `::1`); no user:password, query or fragment. Never stored, printed or published |
| `_KEY_VAR` | the NAME of the environment variable that holds the key, for example `HOSTED_A_API_KEY`. Capitals, digits and `_`, ending in `_KEY` or `_TOKEN`, not starting with `LIVE_`, and not one of the three built-in key names. Required for a hosted endpoint, optional for loopback. Two endpoints on different hosts may not name the same variable |
| `_INPUT_USD_PER_M`, `_OUTPUT_USD_PER_M` | this endpoint's price estimates. Loopback: default 0. Hosted: must be positive unless `_FREE_TIER=YES` |
| `_FREE_TIER` | `YES` is the operator's statement that this hosted service charges nothing; it allows zero rates there. Nothing checks it |
| `_REASONING_EFFORT` | optional: `none`, `minimal`, `low`, `medium` or `high`, sent as `reasoning_effort`. Unset sends nothing |
| `_MAX_OUTPUT_TOKENS` | optional output limit for this endpoint (clamped 40-1000); default `LIVE_ARCHIVE_MAX_OUTPUT_TOKENS` |
| `_SEND_SAMPLING` | `YES` (default) or `NO`: whether the pinned sampling is sent (see below) |
| `_PAUSE_SECONDS` | optional wait after each call to this endpoint (default 0, at most 600), for a service with a requests-per-minute limit |

The key itself goes in `.env` (or the shell) under the name you chose, like any provider key: it is loaded only by `archive-run` and `archive-check-endpoints`, removed from the environment of both web apps, and never written to the database, the audit log, an error message, the plan, the payload or the export. Each call builds its own client from that endpoint's record: its URL, its key, no retry, no redirect followed. A key is never sent to another endpoint's host.

`LIVE_ARCHIVE_MODELS` entries are `label=<endpoint name | openai | anthropic | openai-compatible>:<model>`; the model id is everything after the first colon. The older single endpoint (`openai-compatible`, with `LIVE_COMPAT_BASE_URL` and `OPENAI_COMPATIBLE_API_KEY`) keeps working as before.

**Model ids** for the archive have their own rule: 1-120 characters of `A-Z a-z 0-9 . _ : / @ -`, no space, no `..`, nothing shaped like a domain name. So `gemma3:4b`, `@cf/meta/llama-3.3-70b-instruct-fp8-fast`, `vendor/model:free` and dated ids are accepted. The stricter rule for what an agent declares about itself (`X-Agent-Model`) is unchanged.

### Worked example: one local server and two hosted free tiers
The names, hosts and model ids below are placeholders. This document recommends no service.
```
LIVE_ARCHIVE_ENDPOINTS=local,hosted-a,hosted-b

LIVE_ARCHIVE_ENDPOINT_LOCAL_BASE_URL=http://localhost:11434/v1

LIVE_ARCHIVE_ENDPOINT_HOSTED_A_BASE_URL=https://api.hosted-a.example/v1
LIVE_ARCHIVE_ENDPOINT_HOSTED_A_KEY_VAR=HOSTED_A_API_KEY
LIVE_ARCHIVE_ENDPOINT_HOSTED_A_FREE_TIER=YES
LIVE_ARCHIVE_ENDPOINT_HOSTED_A_PAUSE_SECONDS=6
HOSTED_A_API_KEY=

LIVE_ARCHIVE_ENDPOINT_HOSTED_B_BASE_URL=https://api.hosted-b.example/openai/v1
LIVE_ARCHIVE_ENDPOINT_HOSTED_B_KEY_VAR=HOSTED_B_API_KEY
LIVE_ARCHIVE_ENDPOINT_HOSTED_B_FREE_TIER=YES
HOSTED_B_API_KEY=

LIVE_ARCHIVE_MODELS=small-a=local:model-a:4b,small-b=local:model-b:3b,open-c=hosted-a:vendor/model-c:free,open-d=hosted-b:model-d-latest
LIVE_ALLOW_PAID_CALLS=YES
```
Then: `archive-check-endpoints` (free), `archive-plan` (free), and on the owner's word `archive-run --max-usd 0.01`. `LIVE_PROVIDER`, `LIVE_INPUT_USD_PER_M` and `LIVE_OUTPUT_USD_PER_M` play no part when every model is on a named endpoint (`LIVE_PROVIDER` is only the default for `round`). With a native `anthropic:` or `openai:` entry in the list, the global pair of rates prices those entries and must be positive.

**Before using a hosted free tier, read that service's terms yourself.** Free tiers change, are withdrawn or start billing without notice. Check in particular what the terms say about publishing a model's outputs and about benchmarking or comparing models, whether prompts and outputs are used for training, and the request limits. Limits are usually per account, not per key.

### Pricing basis, honestly labelled
Every server call records a `pricing_basis`:
- `rates`: priced at that endpoint's rates (a named endpoint's own, or the global pair for `openai`, `anthropic` and `openai-compatible`).
- `local`: a loopback server at zero rates. No provider fee exists; electricity and hardware are not counted.
- `declared-free`: a hosted endpoint at zero rates because the operator set `_FREE_TIER=YES`. This is the operator's unverified statement. The local ceiling cannot see a charge on such an endpoint: if the service bills after all, only its own billing page shows it. `archive-plan` and `archive-check-endpoints` say so every time.

`archive-plan` prints, per endpoint, the number of calls, the worst-case estimate and the basis. `--max-usd` and `LIVE_ALLOW_PAID_CALLS=YES` are required for every run, a run that costs nothing included.

### If a call fails
> If a call fails on a zero-priced endpoint (local or declared-free), its $0 hold is released, that model is skipped for the rest of the run and the run goes on. If a call fails on a priced endpoint (rates), the hold stays unconfirmed, the run stops and paid calls are blocked until `cli reconcile`. Zero-priced models are asked first.

Why this rule: at zero rates the ledger holds $0 for a call whether it worked or not, so releasing the hold leaves nothing unrecorded, and one rate-limited free service no longer costs the other columns. At positive rates a failed request may still have been billed; that unknown amount stays counted and blocks everything, exactly as it does for `round` and `study-run` (which are unchanged). Because zero-priced models are asked first, a priced failure can only end the priced part of a run.

A skipped model is recorded in `archive_run_answerers` with a reason category only (`rate_limited`, `not_found`, `auth`, `timeout`, `server_error`, `no_usage`, `not_ready`, `other`), never the service's response. The run ends `complete_with_gaps`; the pages show that model's remaining cells as "No answer recorded in this run" with the reason in plain words; `archive-status` shows each model's outcome in the last run (`last_run_answerers`). Nothing is retried. The next run asks the skipped model again from the first question.

### Pinned sampling and what is recorded
Archive calls send `temperature=0` and a fixed `seed` (`archive.ARCHIVE_SEED`; plus one for each further sample of a run with repeat samples) where the API takes them: always to an OpenAI-compatible server (named or the single one), unless that endpoint sets `_SEND_SAMPLING=NO` (for the single `openai-compatible` endpoint: `LIVE_ARCHIVE_ENDPOINT_OPENAI_COMPATIBLE_SEND_SAMPLING=NO`). The native `openai` and `anthropic` adapters send nothing by default, because some of their models refuse `temperature` and neither API has a seed; `LIVE_ARCHIVE_ENDPOINT_OPENAI_SEND_SAMPLING=YES` or `LIVE_ARCHIVE_ENDPOINT_ANTHROPIC_SEND_SAMPLING=YES` sends `temperature=0` only. If a service rejects the parameters, its calls fail (`other`); set `_SEND_SAMPLING=NO` for it. Pinned sampling reduces run-to-run noise; it does not make a model deterministic. Resident turns (`round`) and `study-run` send no sampling, as before.

Per server call the database keeps: the endpoint **name** (never the URL), the requested model id, the model name the service reported in its response (`reported_model`), `system_fingerprint` when sent, the raw finish reason, the latency in milliseconds, the sampling actually sent (`temperature=0;seed=1234`, or `none`), the pricing basis, and for a local server the model digest. What a service says about itself is untrusted text: a value of the wrong shape is stored as not recorded.

**The pages count it as a new model** when the requested id, the reported model name or the local digest differs from that model's previous recorded answer ("new model ID" / "new model file" in the cell, and a line under "What moved"). A cell reads `<requested model> · via <endpoint name> · server call` for a hosted endpoint and `<requested model> · run locally · server call` for a loopback one. The pages do not say who made a model or how open it is. A model run locally or hosted by a third party is not the same as its maker's own chat app.

### Pinning a local model
An Ollama tag such as `model-a:4b` is a movable name: pulling it again can fetch different weights under the same tag. Pull each model once and never re-pull it while the archive runs. Before each model's first question, a loopback endpoint is asked once for its model list (`GET <origin>/api/tags`, loopback only, 2 second timeout, no proxy, no redirect) and the model's sha256 digest is stored with every answer; a failure is silent and the digest is recorded as not known. If the digest changes between runs, the pages say so.

## Human voting (not built)
The plan, recorded so it is not built by accident:
- **First step: the platforms' own polls.** A TikTok or Instagram post of a share card can carry the platform's poll sticker ("Which answer is right?"). Nothing has to be built, and the votes stay on the platform.
- **An on-site vote needs a server.** The public site is static files; it cannot receive a vote. A vote would be a new, separate service with its own storage.
- **Kept completely apart.** It must not touch the agent API, must not write to the answers tables, and must never change or rank what the archive shows as recorded. Votes are opinions about answers, not part of the record.
- **Labelled for what it is.** An open online poll, not a survey: anyone can vote, more than once, and the people who vote are not a sample of anybody. Results would be shown with that label and with the number of votes.
- Not started. No code, table, route or page for it exists.

## Commands
```bash
python -m live_genesis.cli archive-trends     # free, no model: print today's trending search terms as candidates
python -m live_genesis.cli archive-plan       # free: questions, models, calls, worst-case estimate, the run command
python -m live_genesis.cli archive-dry-run    # free: built-in fake answers, stored as mock, never published
python -m live_genesis.cli archive-status     # free: lock state, file state, runs (both sets), each model's outcome in the last run
python -m live_genesis.cli archive-check-endpoints   # free, no model call: each named endpoint's host, key set yes/no, rates, basis
python -m live_genesis.cli archive-lock       # free, audited, once: the core list
python -m live_genesis.cli archive-lock --set trending   # free, audited: lock the trending questions added since the last lock
python -m live_genesis.cli demo-archive       # free: scripted sample (3 fictional models, 8 dated runs, 3 scripted trending questions); demo database only
# PAID. Only on the owner's explicit word:
python -m live_genesis.cli archive-run --max-usd 0.02
python -m live_genesis.cli archive-run --max-usd 0.05 --samples 3   # each question three times; three times the calls and cost
```
`archive-plan`, `archive-dry-run` and `archive-run` take `--set core` or `--set trending`; without it they cover both. They also take `--samples N` (1-5; default `LIVE_ARCHIVE_SAMPLES`, else 1): see "Repeat samples". A real run asks only locked questions: a set that is not locked yet is left out (and listed as "Not asked" by the plan) while the other one is locked.
Settings (`.env`): `LIVE_ARCHIVE_MODELS=label=provider:model,...` (`openai`, `anthropic`, `openai-compatible` or a named endpoint; at most 8), `LIVE_ARCHIVE_MAX_OUTPUT_TOKENS` (default 120, clamped 40-1000), `LIVE_ARCHIVE_SAMPLES` (default 1, 1-5; choose it before the first real run and keep it), and the endpoint settings in "Several models from several services". `archive-run` has the same gates as `round` and `study-run`: provider key, `LIVE_ALLOW_PAID_CALLS=YES`, valid budget settings, a required `--max-usd` that the worst case must fit under, reserve, call, settle. A failed call on a priced endpoint stops the run and blocks paid calls until `reconcile`; on a zero-priced endpoint that model is skipped and the run goes on (see "If a call fails").

## Cost
Each call is short. With 3 models at the `.env.example` rates ($0.20 in / $1.20 out per million tokens) the worst-case estimate for one run of 60 calls is about $0.0123. With `LIVE_ARCHIVE_SAMPLES=3` the same run is 180 calls and three times the estimate. This is a local estimate, not a provider invoice. The global pair of rates prices every `openai`, `anthropic` and `openai-compatible` entry: set it to the dearest of those. A named endpoint has its own rates. Models that think before answering spend output tokens on thinking; if many answers show "reply cut off at the output limit", raise `LIVE_ARCHIVE_MAX_OUTPUT_TOKENS`.

## Outside clients
`POST /api/agent/archive-answer` (`run`, `question_id`, `answer`, header `X-Agent-Model`; `question_id` is `Q01`... or a locked trending id `T001`... that the latest run asked) lets a client with an agent key send its own model's answer for the latest run. It is stored as source `api` under the agent's name and labelled **self-declared** everywhere: the server cannot check which model wrote it. One answer per run and question, never replaced (also in a run that asks its own models several times: a client's answer is always sample 1); no round quota; its own per-minute window (`LIVE_ARCHIVE_ANSWERS_PER_MINUTE`, default 30). A run must exist first, so the operator opens each month with `archive-run`.

## How answers get published
The pages (`archive/index.html`, `archive/trending.html`, `archive/q/Q01.html` ..., `archive/t/T001.html` ..., `archive/card/<ID>.html`) are plain HTML with no script. Both web apps serve them from the database, and every static snapshot contains them. The public demo site shows the scripted sample, labelled as scripted on every page and cell.

Recorded answers reach the public site only through the reviewed path in `docs/FIRST-REAL-ROUND.md`: `snapshot --export` writes `published/archive.json` next to `overview.json`, under the same hash, manifest, guard and ASCII rules; read both files, then `--approve --i-have-read-it`, commit, push. Mock and dry-run answers are never exported or rendered. `archive.json` stays one file for both sets (schema `live-genesis-archive/3`: a `trending` object; per run, `asked`: how many questions of each set it asked, and `skipped`: models dropped after a failed call, with the reason category; per answer, `endpoint` (a name, never a URL), `model` (requested), `reported_model`, `model_digest` (first 12 hex digits), `pricing_basis`, `sampling_pinned` and `local`, all null for an answer that was not a server call). A run that asked several samples adds optional fields, with no change of schema because a single-sample archive has none of them: on the run, `samples` (how many were asked); on each of its answers, `samples` (answers received), `agree` (size of the largest group) and `others` (the other distinct answers, each with its `text` and `count`). `--check` validates them when present (they must add up, and belong to a run that asked several) and requires them on every server answer of such a run; the other answers pass the same content guards as the answer shown. `--check` verifies both sets, including that no answer belongs to a question its run did not ask, and the shape of every endpoint field. The system fingerprint, latency, raw finish reason, token counts and costs are not exported.

## Upgrading a database from before repeat samples
Back up first (`python -m live_genesis.cli backup NEW-FILE`). Any CLI command or the app then upgrades the database in place: `archive_runs` gains `samples` (1 on every existing run) and `archive_answers` gains `sample` (1 on every existing answer). Because the sample index is part of that table's uniqueness rule, which SQLite cannot alter, `archive_answers` is rebuilt once: every row is copied with the same id and the same values inside one transaction, and the row count is checked before the old table is dropped. No answer, run, lock or question is rewritten, an existing export still passes `--check`, and the pages of a single-sample archive are unchanged. A database opened by the newer code should not be used with the older code again.

## Upgrading a database from before named endpoints
Back up first (`python -m live_genesis.cli backup NEW-FILE`). Any CLI command or the app then upgrades the database in place: nine nullable columns are added to `archive_answers` and the table `archive_run_answerers` is created. No recorded lock, question, run or answer is rewritten; an older answer keeps null in the new columns (shown as not recorded) and the archive continues with the next run number. An export made before the upgrade (`archive.json` schema `/2`, manifest schema `/3`) no longer passes `--check`: export and approve again. A database opened by the newer code should not be used with the older code again.

## Upgrading a database from before the trending track
Back up first (`python -m live_genesis.cli backup NEW-FILE`). Any CLI command or the app then upgrades the database in place: new columns with defaults, two new tables, and the existing core lock copied as version 1. No recorded lock, question, run or answer is rewritten, and the core hash is unchanged, so the archive continues with the next run number. Two things to know: an export made before the upgrade (`published/` with manifest schema `/2`) no longer passes `--check`; export and approve again. And a database opened by the newer code should not be used with the older code again.

## What it does not claim
- Not a benchmark and not a score. Nothing here marks an answer right or wrong.
- "Changed" means the text differs. A reworded answer counts; a model can also answer differently from one call to the next with no change to the model. Repeat samples reduce that noise; they do not remove it, and "3 of 3 agree" says the three texts matched, not that the answer is right.
- A `server call` label means this server requested that model id. A `self-declared` label is whatever the client typed.
- `via <name>` and `run locally` say where a model ran, not who made it. A model run locally or hosted by a third party is not its maker's own chat app; a quantised local file in particular can answer differently from the hosted original.
- `reported_model` is what the service said in its response. It is recorded, not verified.
- `declared-free` is the operator's statement, not a fact this software checked.
- Scripted demo answers and their dates were written by hand. They are not model output. The demo's trending questions are scripted too and are not taken from any real trend list.
- A trending question says what people were searching for, not what is true or important. The trend list is someone else's data, read once by hand.
- Cost figures are local estimates.
