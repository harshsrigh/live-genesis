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

**On the pages.** The internal word "unsettled" is shown as **Mixed answers**. A timeline gets a third square (white with a thick ring); the key lists it only when the archive has a run with several samples. A mixed card carries the tag "Mixed answers" and, when the samples disagreed, the other distinct answers underneath with their counts. The counts themselves ("same answer 3 times out of 3", "same answer 2 times out of 3") are in "Where each answer came from" and in the data files, not on the card. A share card shows only the representative, with "mixed answers" next to the month when the samples did not all agree. The method panel of the model list gains one sentence.

**The failure rule applies per call.** If a sample fails on a zero-priced endpoint that model is skipped for the rest of the run; a cell it had started then shows "only 1 of 3 samples answered". On a priced endpoint the run stops, as always.

**Outside clients** still send one answer per run and question; it is stored as sample 1 and carries no sample counts.

## Edit and lock the questions
1. Edit `live_genesis/archive_questions.json` while it says `"status": "draft"`. Keep each prompt as `Answer in one or two sentences.` plus the question. No question about consciousness or feelings, none that asks for a diagnosis.
2. When the list is final, change the status to `"locked"`, commit the file, and run `python -m live_genesis.cli archive-lock` on the real database. This records the list's hash (audited).
3. From then on the file must not change. Real answers are refused if its hash or status differs from the lock. The hash covers the wording, not the file's spacing. A different core list later would be a new, separately named set; that is not built. New questions go in the trending set.

## The trending track
A question people are asking this week, added to the archive and then asked in every run after, in the same words. The point is the same as for the core list: to see how the answers move. It is also the material for short posts (see "Share cards").

**The file.** `live_genesis/archive_trending.json` has the shape of the core file plus three fields per question: `added` (YYYY-MM-DD), `topic` (the trend term, at most 40 characters) and `trend_source` (for example `Google Trends US, 2026-11-12`). Ids are `T001`, `T002`, ... in the order the questions were added. It holds 12 questions, set to locked by the owner on 13 November 2026, all in the group "Owner's picks" (questions the owner chose, not taken from a trend list); the group "Trending" is empty until a question is written from a real trend list.

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
6. `python -m live_genesis.snapshot --export --archive-only` (add `--replace` when `published/` already holds an export), read `published/archive.json`, `--approve --i-have-read-it`, commit, push.

### Share cards
Every question has a card at `archive/card/<ID>.html` (linked as "Share card" from the question's page): the mark and the name, the question in large type, the latest answer of up to three models with the model label and the month (a changed one is marked), and one honesty line ("Sample we wrote, not a real AI answer" on sample data, "AI-generated · we asked it ourselves · may be wrong" otherwise). Everything a viewer must read sits inside the box that video apps leave free on a 1080 by 1920 image (x 65 to 940, y 270 to 1440), as on the chat-app cards. Plain HTML and CSS: no script, no animation. An answer is clamped to three lines on the picture; when one may be cut the card says "full answer on the site". The text is never edited. In a window smaller than 540 by 960 CSS pixels the same page is ordinary flowing text that can be read and zoomed.

To capture one: open the card in a browser window of 540 by 960 CSS pixels (in Chrome or Firefox: developer tools, responsive mode, 540 x 960, device pixel ratio 2) and take a screenshot: that gives a 1080 by 1920 image, the portrait size TikTok and Instagram use. The card keeps its 9:16 shape and scales with the window, so a screen recording of it works too. Capture cards from the published site (reviewed data), not from the demo, and never crop the label at the bottom. Posting is done by hand; nothing in this repository posts anywhere.

Or use `node scripts/capture-cards.mjs --site DIR --out DIR` (or `--url BASE`; `--ids Q11,T001,C001-grok`, `--set core|trending|chat`): it writes each card as a 1080 x 1920 PNG, needs the operator's own Playwright install, and marks sample cards. Posting routine and wording rules: `docs/SOCIAL-POSTS.md`.

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

**The pages count it as a new model** when the requested id, the reported model name or the local digest differs from that model's previous recorded answer ("new version" / "new model file" in "Where each answer came from", with what it was before). A line there reads `We asked it ourselves · version <requested model> · via <endpoint name>` for a hosted endpoint and `We asked it ourselves · version <requested model> · run locally` for a loopback one; the version is also the answer's `data-version`. The pages do not say who made a model or how open it is. A model run locally or hosted by a third party is not the same as its maker's own chat app.

### Pinning a local model
An Ollama tag such as `model-a:4b` is a movable name: pulling it again can fetch different weights under the same tag. Pull each model once and never re-pull it while the archive runs. Before each model's first question, a loopback endpoint is asked once for its model list (`GET <origin>/api/tags`, loopback only, 2 second timeout, no proxy, no redirect) and the model's sha256 digest is stored with every answer; a failure is silent and the digest is recorded as not known. If the digest changes between runs, the pages say so.

## The chat-app track (asked by hand)
The lead of the site: the same questions typed into consumer chat apps **by hand**, with a fourth column of answers written by people. The apps are ChatGPT, Gemini and Grok (makers: OpenAI, Google, xAI). Copilot was dropped on the owner's decision of 14 November 2026. The owner opens each app, asks the questions, pastes the replies into a file, and the software imports that file. One reply per question and app, and at most one human answer per question.

**Why by hand.** The terms of these services forbid automated access to their apps. So the software never contacts them, in any form: no program opens, reads or copies from a chat app, and nothing in this repository may be changed to do so. `archive-chatbot-template` writes a file; `archive-chatbot-import` reads a file. Neither opens a network connection, calls a model or needs a key, and neither is a paid command. The same holds for the human answers: the owner collects them by hand, and the software never contacts Reddit or any other site.

**What is fixed.** `live_genesis/archive_chatbots.json` (format `live-genesis-chatbots/2`) holds:
- `chatbots`: the three apps (label, name, maker, and an optional `attribution` line a maker's brand guidance asks for; only Grok has one, "Written with Grok").
- `human`: the wording of the Human column (`name`: "Human"; `meta`: "written by people · collected by hand"). Human is a kind of its own. It is never an app and never a model, and its label `human` cannot be given to an app.
- `questions`: this track's **own** list, ids `C001`, `C002` ... in order. Each has `text`, the exact `prompt`, an optional `hook` (a short headline for the page and the card, at most 60 characters, which must not misstate what was asked) and the date `added`. The list is not linked to the locked core list or to the trending list, and importing does not need either of them locked.
- `prompt_prefix`: the one fixed first line of every prompt: "Answer in one short sentence, like you're texting a friend. Be funny if you can." Every `prompt` is that line, a blank line, then the question. It asks for a short, casual reply, which is why the replies are short: a recorded reply is never rewritten, shortened or "humanised" by us. The page shows the prompt under "Exactly what we asked".
- `aside`: one line of the owner's own, shown once, small and marked "Joke", on the "How it works" page of a real archive: "Why no Copilot? It is cheaper to ask a human than Copilot." It is a joke and makes no claim about price or quality. It is never on a share card, never in the record and never on a sample site.

The file says `"status": "draft"`: until the first real import the owner may change the list. The first import records every question with a hash (id, text, prompt, hook, date). From then on a recorded question never changes and never disappears (the import refuses, and names the id), and new questions are appended as `C011`, `C012` ... Every import records the id list and the hash of the configuration.

**No politics.** A question or hook that holds an obviously political word (president, election, vote, party names, and so on: `archive_chatbots.POLITICAL_TERMS`) is refused when the file is loaded. The check is crude: it matches words, not meaning, so it misses a political question that avoids those words and would refuse a harmless one that uses them. It holds politics only; joke words such as roast, destroy, worst or fight are not in it. The owner still reads every question. Also not added: a named private person, medical, legal or financial advice, questions about consciousness or feelings, anything sexual or hateful, and questions whose only point is to reveal how old the model is (today's date, the year, a cutoff, "which model are you").

### The monthly routine
1. **Read the terms first.** Before every session, read each service's current terms on using the app and on publishing what it wrote. They change. See "Terms: what was and was not checked" below.
2. **Template.** `python -m live_genesis.cli archive-chatbot-template --out chatbots-2026-12.json` (free). It holds today's date as `collected_on`; for each app an empty `conditions` object and one entry per question with the exact prompt to paste and an empty `answer`; and the optional `human` block. It never overwrites a file.
3. **Ask by hand.** For each app: fill in its `conditions`. Then, for every question, start a **new chat**, paste the `prompt` exactly, and copy the **whole** reply into `answer`. Paste the whole reply even if it is long; never trim or reword it. The page shortens the display, not the text. A reply with line breaks or quotation marks is awkward inside a JSON file: save it as a plain text file next to the template and put that file's name in `"answer_file"` instead. Optionally keep a screenshot and name it in `"screenshot"`: only its sha256 fingerprint is stored, never the picture or its path. If you did not ask one app that day, remove its whole block.
4. **Refusals and non-answers.** If the app refused or dodged the question, add `"refused": true` (with what it said in `answer`, or with nothing). If you could not ask at all, leave `answer` empty and add `"skipped": "a short reason"`. Both are stored, so the record is honest, and neither is shown: the pages show only real answers and say once, plainly, "Refusals and non-answers are left out." A reply the content check withholds is left out in the same way. A refusal never counts as a change, and a later answer is compared with the last answer that was shown.
5. **Human answers (optional).** Fill in the `human` block, or remove it. `conditions`: `source` (where, in a few plain words such as "Reddit thread"; no link, no username), `collected_on`, `count` (how many replies you read, or `null`). Per question one short `answer` (one sentence, like the apps) and `kind`: `"quoted"` (one person's words, copied exactly) or `"summarised"` (your own short summary of several replies); or `"skipped": "reason"`. The page shows which it is.
6. **Dry run.** `python -m live_genesis.cli archive-chatbot-import chatbots-2026-12.json --dry-run` checks the whole file and prints what would be stored: counts and lengths, never the text of a reply. Nothing is stored.
7. **Import.** The same command without `--dry-run` (free, audited). One import is one run dated `collected_on`; both that date and the import time are kept.
8. **Publish.** As for every recorded answer: `python -m live_genesis.snapshot --export --archive-only`, read `published/archive.json`, `--approve --i-have-read-it`, `--check`, commit, push. An archive that holds only hand-entered replies can be exported.

### Other people's words: decide the rule first
The software only has the column. How the owner collects human answers is the owner's manual work, and its rule is **not decided yet**. Publishing what other people wrote needs that rule before the first human answer is imported. At least: say in the thread itself that replies may be quoted on the site; publish no usernames; read that community's rules on surveys and self-promotion first. Posting a thread is outreach, so the owner does it. What the software enforces: no username field exists anywhere; an answer or a `source` that looks like a handle (`u/name`, `@name`) or a web address is refused; the same character rule and content guards as for an app's reply; one text of at most 500 characters. The patterns are crude and do not replace reading every answer.

A human answer that differs from the collection before is not called "changed": other people answered, nobody changed their mind. The pages say "Different answer this month".

### Keep the conditions the same
A chat app's reply depends on how it is asked. Record the conditions every time, and keep them the same from month to month:
- **Account:** a logged-in **free** account (`free_account`; owner's decision: ask logged in). `logged_out` and `paid_account` are accepted, but do not switch between them.
- **Temporary chat:** use the app's temporary or private chat where it has one: ChatGPT "Temporary Chat", Gemini "Temporary chat", Grok "Private Chat".
- **Memory off**, same **country**, same kind of **app** (web, mobile or desktop).
- **Search:** if the reply shows a "Sources" or citations indicator, search was on: record `web_search: "on"`. If you switched it off, `"off"`. If you cannot tell, `"unknown"`.
- **Shown model:** the model name the app displays, or `"not shown"`.
- `fresh_chat` must be `true`: a file with `false` is refused.

When an app's recorded conditions differ from its date before, that month's card carries "Conditions changed" and its timeline square a dot: a changed reply there may not mean the app changed. The details ("search: off → on") are in "Where each answer came from".

### What the importer checks and refuses
- The whole file is checked before anything is written; every problem is named by place and rule. Strict JSON, closed keys, complete conditions with closed choices, exactly the configured questions for each app and for the human block, every prompt unchanged, exactly one of `answer`, `answer_file` or `skipped` (`"refused": true` alone or with the reply).
- `collected_on` may not be in the future and may not be more than 14 days old (`--allow-old` lifts the second rule).
- One run per app and day, and an app's dates only move forward (the human block too): a second import for the same `collected_on` is refused, and so is a date before one already recorded. A stored reply is never replaced.
- A recorded question that was dropped or edited in the configuration.
- A database that holds scripted demo answers is refused. There is no override: test in a scratch database (`LIVE_DB=...`).
- Every reply passes the content guards and the character rule. A failing reply is stored as **withheld** with the category only, as for a model answer. An app's reply of up to 2000 characters is stored in full, as pasted (markdown and citation markers included); a longer one is stored as "no answer".
- "Changed" compares a reply with that app's previous shown reply to the same question, by the same plain text comparison as for models.

### Where it is kept and shown
Six append-only tables of their own: `archive_chatbot_runs`, `archive_chatbot_conditions`, `archive_chatbot_answers` (with `refused`), `archive_chatbot_questions` (the recorded list with its hashes), `archive_human_collections` and `archive_human_answers` (with `manner`: quoted or summarised). They are never rows of the model grids or timelines and never take part in a model's "changed". In `archive.json` they are the `chatbots` block (`apps`, `human`, `questions`, `runs`, `answers`, `human_answers`), and a file that holds it is marked `live-genesis-archive/5`; a file without it is still `live-genesis-archive/3`, exactly as before. `--check` refuses an answer to a question outside its run's recorded id list, an app that is not in `archive_chatbots.json` (or not under the name, maker and attribution that file gives), a question that is not that file's question word for word, a human answer without quoted or summarised or with a handle or a link, an answer without recorded conditions, and any key it does not know. The guards run again on export. A screenshot is exported as its hash only. Entered by hand means the reply is not verified: there is no receipt.

Pages: once a chat-app run exists, `archive/index.html` is the comparison (every question with the newest answer of each app and of Human); `archive/c/C001.html` ... are the question pages; `archive/chatbots.html` ("How it works") holds the method, how each one was asked, and the aside; share cards are `archive/card/C001.html` (every app and Human; Human's panel is outlined, not filled), `archive/card/C001-<app label>.html` and `archive/card/C001-human.html`. Product names appear as plain text only, never as a logo and never larger than the question; every Grok card says "Written with Grok". Where real replies are shown the page says: "AI-generated answers, typed into each app by hand and copied unedited. They may be wrong and are not advice. Not affiliated with, sponsored by or endorsed by OpenAI, Google or xAI; product names belong to their owners."

The sample site (`demo-archive`) shows this track under made-up names only (`chat-app-a` to `chat-app-c`, "Chat app A" ..., and "Human (sample)"), with its own scripted questions, every page labelled "Sample we wrote, not a real AI answer". It never puts words in a real product's or a real person's mouth; a test checks that no sample page, data file or payload holds a real name.

### Terms: what was and was not checked
- The owner must read each service's current terms about publishing its output before posting or pushing. This project has not verified them for you, and they change: read them again before each monthly session.
- The wording attributed here to OpenAI and xAI (including xAI's request for the "Written with Grok" attribution) was not verified on the live pages. Check it yourself.
- The apps' terms forbid passing AI output off as human-written. That is one more reason a recorded reply is never edited, and why the Human column is a separate kind with its own label.
- Nothing here is legal advice.

### Upgrading a database from before this track, or from its first shape
Back up first (`python -m live_genesis.cli backup NEW-FILE`), as before every upgrade. Any CLI command or the app then adds the tables in place. A database from the track's first shape (three tables, questions referring to the two locked lists) gains the `refused` column, the question table and the two human tables, and trades the rule "a hand-entered answer needs a locked question" for "an answer needs a recorded chat-app question". Nothing recorded is rewritten or rebuilt. A run stored in the first shape (none was ever published) is kept and shown nowhere.

## The reading pages
One layout for every question page (`c/`, `q/`, `t/`), made for a phone first. No script anywhere.
- **Top bar** in one line; then the id, the one honesty label and the question.
- **This month's answers as cards** at the full width of the page: one row on a wide screen, two per row on a tablet, one column on a phone. No sideways scrolling.
- **Older months one tap away:** radio inputs and labels in a strip (no form, no script; the Content-Security-Policy keeps `form-action 'none'`). A page shows up to 24 months this way; the table below holds every month.
- **A long answer is clamped** (about 12 lines on a wide screen, 8 on a tablet, 4 on a phone) with a "More" control, a checkbox and its label. The control exists only where the text is long enough to be cut (over 140, 240 or 300 characters). The text is in the page in full; nothing is edited.
- **Changed, shown inline:** the tag "Changed", an accent edge, and directly under the answer "Before · <month>" with the previous answer.
- **History** on a card that changed: that answerer's distinct answers, runs of the same answer collapsed ("Aug–Oct 2026 · 3 checks").
- **What changed:** a table (months down, answerers across). Only a new or different answer is written out; an unchanged one says "same as before". On a narrow screen its rows become blocks.
- **Closed by default:** "Why we ask this", "Exactly what we asked", "Where each answer came from".
- List pages (`index.html`, `models.html`, `trending.html`) show each question with every answerer's newest answer inline and a timeline of squares whose accessible name says when it changed.
- Words on the pages are plain: check (not run), version (not model ID), "We asked it ourselves", "Sent in by someone else, not checked", "Sample we wrote, not a real AI answer", "Mixed answers", "Hidden: broke our content rules". The internal names stay in the code and the data.
- Without a chat-app run, `index.html` lists the trending and the core questions with the models' answers; `models.html` always holds the core list.

## Machine-readable labels
The pages carry a second layer for programs, including future AI models that read the site. It uses attributes, `<meta>` and `<link>` tags and separate data files: no script (not even JSON-LD) and no hidden text. The rule: **a visible honesty label is never moved into metadata only.** What stays visible, once per page or card: AI-generated; typed in by hand or we asked it ourselves; may be wrong; not affiliated; "Written with Grok"; the sample label; Human with quoted or summarised; "Changed" and the Before line; "Refusals and non-answers are left out." Everything else about an answer (version shown, account, temporary chat, memory, search, country, app, endpoint name, pricing basis, sampling, digest, sample counts) is metadata, and people can still read it in the closed "Where each answer came from" block.

On each visible answer (the card, and its cell of the "What changed" table):

| Attribute | Values |
|---|---|
| `data-question-id` | `Q01`, `T002`, `C003` ... |
| `data-answerer` | the answerer's label |
| `data-answerer-kind` | `chat-app` \| `model` \| `human` |
| `data-maker` | the app's maker, or empty |
| `data-version` | the version the app showed or the service reported, or empty |
| `data-collected` | the date of the check, `YYYY-MM-DD` |
| `data-method` | `typed-by-hand` \| `asked-by-server` \| `sent-in-unverified` \| `collected-by-hand` (human answers) \| `scripted-sample` |
| `data-provenance` | `ai-generated` \| `human-written` \| `scripted-sample` |
| `data-human-form` | `quoted` \| `summarised` (human answers only) |
| `data-state` | `answered` (a first answer) \| `same` \| `changed` \| `mixed` |
| `data-previous-collected` | the date of the answer before, when `changed` |
| `data-truncated-display` | `true` when the visible text may be clamped; the full text is in the page and in the data file |
| `data-lang` | `en` |

The same elements carry schema.org microdata: the page's `<main>` is a `Question` (`name`, `text` = the exact prompt), each card a `suggestedAnswer` / `Answer` with `text`, `dateCreated` and an `author`: a `SoftwareApplication` (with its maker as `creator`) for an app or model, and an `Organization` named "Anonymous people" for the Human column, never a person's name.

In the page head: `<meta name="aot:schema">`, `aot:content` (`ai-generated-answers-recorded-verbatim`, or `scripted-sample` on a sample site), and on a question page `aot:question-id`, `aot:prompt`, `aot:collected-latest`, `aot:answerers`, with `<link rel="alternate" type="application/json">` to the question's data file. List pages link `data/answers.jsonl` as `application/x-ndjson`. A share card's `<main>` says `data-provenance="scripted-sample"` or `"recorded"`.

Data files, written with the pages: `archive/data/<ID>.json` (one question, exactly what was asked, every answer), `archive/data/answers.jsonl` (one self-contained answer per line: question, prompt, answerer, kind, maker, version, date, method, provenance, the text in full, the changed flag, the previous answer, the conditions, a notice) and `archive/data/README.txt` (the fields in plain words). They are derived from the same payload as the pages and hold nothing that is not in `archive.json`: no base URL, no key, no path, no username. They are not part of `published/`: the export and `--check` are unaffected. Both web apps serve them (`/archive/data/...`, read-only). A reply the owner marked as a refusal is in the data, marked `refused` and `shown: false`, and on no page.

Sample data is unmistakable to a machine too: every sample record and element carries `scripted-sample`, in every line of `answers.jsonl`, and no recorded answer carries it (tests check both).

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
python -m live_genesis.cli demo-archive       # free: scripted sample (3 fictional models, 8 dated runs, 3 scripted trending questions, 4 made-up chat apps); demo database only
python -m live_genesis.cli archive-chatbot-template --out FILE [--date YYYY-MM-DD]   # free: the file to fill in BY HAND for the chat apps
python -m live_genesis.cli archive-chatbot-import FILE --dry-run                     # free: check a filled-in file, store nothing
python -m live_genesis.cli archive-chatbot-import FILE [--allow-old]                 # free, audited: store it as one hand-entered run
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
The pages (`archive/index.html`, `archive/models.html`, `archive/trending.html`, `archive/chatbots.html`, `archive/c/C001.html` ..., `archive/q/Q01.html` ..., `archive/t/T001.html` ..., `archive/card/<ID>.html`) are plain HTML with no script, and the data files under `archive/data/` go with them. Both web apps serve them from the database, and every static snapshot contains them. The public site shows the scripted sample, labelled as a sample once on every page and card; its front page is the chat-app comparison under made-up names.

The public site holds these pages and nothing else (owner decision, 13 Nov 2026). CI builds every variant with `python -m live_genesis.snapshot OUTDIR --archive-only`, which writes `archive/`, `archive.json`, the stylesheet, mark, icon and fonts the pages use, and a root `index.html` that sends the visitor to `archive/index.html` (a meta refresh and a plain link, no script). No observatory page, `overview.json`, post, profile or stats page, script or agent kit is published, and the archive pages carry no link to the observatory there; with two snapshots (reviewed data at the root, demo under `demo/`) the bar links the other archive instead. Without the flag the builder still writes the full observatory, for local use. The reviewed export has a matching form: `--export --archive-only` writes `archive.json` and `MANIFEST.json` only, so no post, document, proposal, game or profile enters the public repository.

Recorded answers reach the public site only through the reviewed path in `docs/FIRST-REAL-ROUND.md`: `python -m live_genesis.snapshot --export --archive-only` writes exactly two files into `published/`, `archive.json` and `MANIFEST.json`, under the hash, guard and ASCII rules of that path; read `archive.json`, then `--approve --i-have-read-it`, commit, push. This export never reads the society tables and never writes `overview.json`, so no post, document, proposal, game or profile is exported. An answer that an outside agent submitted (`source: api`) is part of the archive and is exported with that agent's name and self-declared model; the export and approve summaries say how many such answers there are. It refuses when the archive holds only scripted demo answers, holds mock answers or holds no answers, when answers exist for a question list that is not locked, while a run is still marked running (in progress, or killed before it finished), when a content guard matches, or when `published/` already holds an export and `--replace` was not given. `--include-unfinished` exports a run that is still marked running as it stands. The export summary prints one line per recorded run with its number, status and answers recorded, so the owner sees a run that stopped early. The manifest (schema `live-genesis-published/5`) records the kind in `"contents"`: `"archive"` for this export, `"full"` for one made without `--archive-only`. For an archive-only export the manifest holds the sha256 of `archive.json` in `archive.sha256` and has no top-level `sha256`; `exported_at` is a note checked for shape only; the manifest itself is not hashed. The full export also writes `overview.json` (the society's posts, documents and agents), which would be public in the repository although the site does not show it, so it is not the path for the public site. `--approve` and `--check` take no `--archive-only`: they read the kind from the manifest. An archive-only folder must hold exactly `archive.json` and `MANIFEST.json`; an `overview.json` in it is refused. The check also refuses an `archive.json` that holds any key the exporter does not write, at any level, so nothing can be added to the file by hand after export. An older export (manifest schema `/4`) is refused: export again. Mock and dry-run answers are never exported or rendered. `archive.json` stays one file for both sets (schema `live-genesis-archive/3`: a `trending` object; per run, `asked`: how many questions of each set it asked, and `skipped`: models dropped after a failed call, with the reason category; per answer, `endpoint` (a name, never a URL), `model` (requested), `reported_model`, `model_digest` (first 12 hex digits), `pricing_basis`, `sampling_pinned` and `local`, all null for an answer that was not a server call). A run that asked several samples adds optional fields, with no change of schema because a single-sample archive has none of them: on the run, `samples` (how many were asked); on each of its answers, `samples` (answers received), `agree` (size of the largest group) and `others` (the other distinct answers, each with its `text` and `count`). `--check` validates them when present (they must add up, and belong to a run that asked several) and requires them on every server answer of such a run; the other answers pass the same content guards as the answer shown. `--check` verifies both sets, including that no answer belongs to a question its run did not ask, and the shape of every endpoint field. The system fingerprint, latency, raw finish reason, token counts and costs are not exported.

## Upgrading a database from before repeat samples
Back up first (`python -m live_genesis.cli backup NEW-FILE`). Any CLI command or the app then upgrades the database in place: `archive_runs` gains `samples` (1 on every existing run) and `archive_answers` gains `sample` (1 on every existing answer). Because the sample index is part of that table's uniqueness rule, which SQLite cannot alter, `archive_answers` is rebuilt once: every row is copied with the same id and the same values inside one transaction, and the row count is checked before the old table is dropped. No answer, run, lock or question is rewritten, an existing export still passes `--check`, and the pages of a single-sample archive are unchanged. A database opened by the newer code should not be used with the older code again.

## Upgrading a database from before named endpoints
Back up first (`python -m live_genesis.cli backup NEW-FILE`). Any CLI command or the app then upgrades the database in place: nine nullable columns are added to `archive_answers` and the table `archive_run_answerers` is created. No recorded lock, question, run or answer is rewritten; an older answer keeps null in the new columns (shown as not recorded) and the archive continues with the next run number. An export made before the upgrade (`archive.json` schema `/2`, manifest schema `/3`) no longer passes `--check`: export and approve again. A database opened by the newer code should not be used with the older code again.

## Upgrading a database from before the trending track
Back up first (`python -m live_genesis.cli backup NEW-FILE`). Any CLI command or the app then upgrades the database in place: new columns with defaults, two new tables, and the existing core lock copied as version 1. No recorded lock, question, run or answer is rewritten, and the core hash is unchanged, so the archive continues with the next run number. Two things to know: an export made before the upgrade (`published/` with manifest schema `/2`) no longer passes `--check`; export and approve again. And a database opened by the newer code should not be used with the older code again.

## What it does not claim
- Not a benchmark and not a score. Nothing here marks an answer right or wrong.
- "Changed" means the text differs. A reworded answer counts; a model can also answer differently from one call to the next with no change to the model. Repeat samples reduce that noise; they do not remove it, and "3 of 3 agree" says the three texts matched, not that the answer is right.
- "We asked it ourselves" (in the data: `asked-by-server`, source `engine`) means this server requested that model id. "Sent in by someone else, not checked" (`sent-in-unverified`, source `api`, self-declared) is whatever the client typed.
- `via <name>` and `run locally` say where a model ran, not who made it. A model run locally or hosted by a third party is not its maker's own chat app; a quantised local file in particular can answer differently from the hosted original.
- `reported_model` is what the service said in its response. It is recorded, not verified.
- `declared-free` is the operator's statement, not a fact this software checked.
- Scripted demo answers and their dates were written by hand. They are not model output. The demo's trending questions are scripted too and are not taken from any real trend list.
- A trending question says what people were searching for, not what is true or important. The trend list is someone else's data, read once by hand.
- Cost figures are local estimates.
- An `entered by hand` reply is what a person says a chat app wrote. There is no receipt. It also shows one reply on one day under the recorded conditions, not what the app "thinks": the same app can answer differently for another account, country or day.

## The one removal (14 November 2026)

The rule above stands: a locked question never changes and is never removed, and no command removes one. One exception was made by hand, on the owner's instruction, the day after the first lock. The trending question "Who is the president of the United States?" (then T004) was political, which the owner keeps off the site, and a model had answered it wrongly.

What was done, outside the software, on the owner's PC: the real database was backed up; on a copy, the append-only triggers were dropped inside one transaction, the question and its 15 recorded answers (5 answer cells of 3 samples) were deleted, the eight later questions moved up one id (T005 to T012 became T004 to T011, in the answers too), the trending lock was recorded again as version 1 over the 11 remaining questions, and the triggers were put back; the copy was checked (question list against the file, hashes, counts, an export) and then replaced the real database. The operator audit log holds one line saying so. `archive_trending.json` was changed in the same commit, and the published export was replaced by a new one that the owner approved.

What this did not do: the question and its answers remain in the git history of this repository (the first published export), in earlier backups on the owner's PC, and in any copy someone made of the site while it was public. Ids published before the removal (T005 to T012) now name different questions than they did for those few hours.
