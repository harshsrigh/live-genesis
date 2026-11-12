# Answers Over Time

Very basic questions a person would ask, put to AI models again and again over months and years. Every answer is kept with its date and the model that gave it, so the public pages show when an answer changes.

## What it is
- A fixed list of 20 questions in four groups (`live_genesis/archive_questions.json`): the **core** set.
- A second list that grows, the **trending** set (`live_genesis/archive_trending.json`): questions the owner writes by hand from what people are searching for. See "The trending track" below.
- A **run** asks every locked question of both sets of every configured model once (`--set core` or `--set trending` limits it). Intended cadence: about one run a month. Nothing schedules or enforces it; the operator starts each run by hand.
- Answerers are **models, not resident agents**. No persona, no resident prompt: the system text is one fixed line (`archive.SYSTEM_LINE`) and the user message is exactly the question's prompt. A model never sees another model's answer or any earlier answer.
- Code: `live_genesis/archive.py` (data, lock, runs, commands) and `live_genesis/archive_pages.py` (the pages). `live_genesis/trends.py` prints trend candidates. Tables: `archive_sets`, `archive_set_versions`, `archive_questions`, `archive_runs`, `archive_run_sets`, `archive_answers` (append-only by trigger).

## The "changed" rule
An answer counts as changed when its text differs from the same model's previous recorded answer to the same question, after Unicode NFKC, case-folding, collapsing whitespace, and stripping punctuation and quotes at both ends. The first answer has nothing to compare with. A run with no answer in between is skipped, not counted as a change. It is a plain text comparison and says nothing about which answer is right.

A reply cut off at the output limit, an empty reply or one over 1,000 characters is stored as "no answer recorded" with the reason. A reply the content guards match (personal data, secrets, payment links, invisible characters) is stored as "withheld by content check" with the category and no text.

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
python -m live_genesis.cli archive-status     # free: lock state, file state, runs (both sets)
python -m live_genesis.cli archive-lock       # free, audited, once: the core list
python -m live_genesis.cli archive-lock --set trending   # free, audited: lock the trending questions added since the last lock
python -m live_genesis.cli demo-archive       # free: scripted sample (3 fictional models, 8 dated runs, 3 scripted trending questions); demo database only
# PAID. Only on the owner's explicit word:
python -m live_genesis.cli archive-run --max-usd 0.02
```
`archive-plan`, `archive-dry-run` and `archive-run` take `--set core` or `--set trending`; without it they cover both. A real run asks only locked questions: a set that is not locked yet is left out (and listed as "Not asked" by the plan) while the other one is locked.
Settings (`.env`): `LIVE_ARCHIVE_MODELS=label=provider:model,...` (providers `openai`, `anthropic`, `openai-compatible`; at most 8), `LIVE_ARCHIVE_MAX_OUTPUT_TOKENS` (default 120, clamped 40-1000). `archive-run` has the same gates as `round` and `study-run`: provider key, `LIVE_ALLOW_PAID_CALLS=YES`, valid budget settings, a required `--max-usd` that the worst case must fit under, reserve, call, settle. A failed call stops the run and blocks paid calls until `reconcile`.

## Cost
Each call is short. With 3 models at the `.env.example` rates ($0.20 in / $1.20 out per million tokens) the worst-case estimate for one run of 60 calls is about $0.0123. This is a local estimate, not a provider invoice. One pair of rates prices every listed model: set them to the dearest model you list. Models that think before answering spend output tokens on thinking; if many answers show "reply cut off at the output limit", raise `LIVE_ARCHIVE_MAX_OUTPUT_TOKENS`.

## Outside clients
`POST /api/agent/archive-answer` (`run`, `question_id`, `answer`, header `X-Agent-Model`; `question_id` is `Q01`... or a locked trending id `T001`... that the latest run asked) lets a client with an agent key send its own model's answer for the latest run. It is stored as source `api` under the agent's name and labelled **self-declared** everywhere: the server cannot check which model wrote it. One answer per run and question, never replaced; no round quota; its own per-minute window (`LIVE_ARCHIVE_ANSWERS_PER_MINUTE`, default 30). A run must exist first, so the operator opens each month with `archive-run`.

## How answers get published
The pages (`archive/index.html`, `archive/trending.html`, `archive/q/Q01.html` ..., `archive/t/T001.html` ..., `archive/card/<ID>.html`) are plain HTML with no script. Both web apps serve them from the database, and every static snapshot contains them. The public demo site shows the scripted sample, labelled as scripted on every page and cell.

Recorded answers reach the public site only through the reviewed path in `docs/FIRST-REAL-ROUND.md`: `snapshot --export` writes `published/archive.json` next to `overview.json`, under the same hash, manifest, guard and ASCII rules; read both files, then `--approve --i-have-read-it`, commit, push. Mock and dry-run answers are never exported or rendered. `archive.json` stays one file for both sets (schema `live-genesis-archive/2`: a `trending` object and, per run, `asked`: how many questions of each set it asked); `--check` verifies both, including that no answer belongs to a question its run did not ask.

## Upgrading a database from before the trending track
Back up first (`python -m live_genesis.cli backup NEW-FILE`). Any CLI command or the app then upgrades the database in place: new columns with defaults, two new tables, and the existing core lock copied as version 1. No recorded lock, question, run or answer is rewritten, and the core hash is unchanged, so the archive continues with the next run number. Two things to know: an export made before the upgrade (`published/` with manifest schema `/2`) no longer passes `--check`; export and approve again. And a database opened by the newer code should not be used with the older code again.

## What it does not claim
- Not a benchmark and not a score. Nothing here marks an answer right or wrong.
- "Changed" means the text differs. A reworded answer counts; a model can also answer differently from one call to the next with no change to the model.
- A `server call` label means this server requested that model id. A `self-declared` label is whatever the client typed.
- Scripted demo answers and their dates were written by hand. They are not model output. The demo's trending questions are scripted too and are not taken from any real trend list.
- A trending question says what people were searching for, not what is true or important. The trend list is someone else's data, read once by hand.
- Cost figures are local estimates.
