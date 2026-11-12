# Answers Over Time

Very basic questions a person would ask, put to AI models again and again over months and years. Every answer is kept with its date and the model that gave it, so the public pages show when an answer changes.

## What it is
- A fixed list of 20 questions in four groups (`live_genesis/archive_questions.json`).
- A **run** asks every question of every configured model once. Intended cadence: about one run a month. Nothing schedules or enforces it; the operator starts each run by hand.
- Answerers are **models, not resident agents**. No persona, no resident prompt: the system text is one fixed line (`archive.SYSTEM_LINE`) and the user message is exactly the question's prompt. A model never sees another model's answer or any earlier answer.
- Code: `live_genesis/archive.py` (data, lock, runs, commands) and `live_genesis/archive_pages.py` (the pages). Tables: `archive_sets`, `archive_questions`, `archive_runs`, `archive_answers` (append-only by trigger).

## The "changed" rule
An answer counts as changed when its text differs from the same model's previous recorded answer to the same question, after Unicode NFKC, case-folding, collapsing whitespace, and stripping punctuation and quotes at both ends. The first answer has nothing to compare with. A run with no answer in between is skipped, not counted as a change. It is a plain text comparison and says nothing about which answer is right.

A reply cut off at the output limit, an empty reply or one over 1,000 characters is stored as "no answer recorded" with the reason. A reply the content guards match (personal data, secrets, payment links, invisible characters) is stored as "withheld by content check" with the category and no text.

## Edit and lock the questions
1. Edit `live_genesis/archive_questions.json` while it says `"status": "draft"`. Keep each prompt as `Answer in one or two sentences.` plus the question. No question about consciousness or feelings, none that asks for a diagnosis.
2. When the list is final, change the status to `"locked"`, commit the file, and run `python -m live_genesis.cli archive-lock` on the real database. This records the list's hash (audited).
3. From then on the file must not change. Real answers are refused if its hash or status differs from the lock. The hash covers the wording, not the file's spacing. A different list later would be a new, separately named set; that is not built.

## Commands
```bash
python -m live_genesis.cli archive-plan       # free: questions, models, calls, worst-case estimate, the run command
python -m live_genesis.cli archive-dry-run    # free: built-in fake answers, stored as mock, never published
python -m live_genesis.cli archive-status     # free: lock state, file state, runs
python -m live_genesis.cli archive-lock       # free, audited, once
python -m live_genesis.cli demo-archive       # free: scripted sample (3 fictional models, 8 dated runs); demo database only
# PAID. Only on the owner's explicit word:
python -m live_genesis.cli archive-run --max-usd 0.02
```
Settings (`.env`): `LIVE_ARCHIVE_MODELS=label=provider:model,...` (providers `openai`, `anthropic`, `openai-compatible`; at most 8), `LIVE_ARCHIVE_MAX_OUTPUT_TOKENS` (default 120, clamped 40-1000). `archive-run` has the same gates as `round` and `study-run`: provider key, `LIVE_ALLOW_PAID_CALLS=YES`, valid budget settings, a required `--max-usd` that the worst case must fit under, reserve, call, settle. A failed call stops the run and blocks paid calls until `reconcile`.

## Cost
Each call is short. With 3 models at the `.env.example` rates ($0.20 in / $1.20 out per million tokens) the worst-case estimate for one run of 60 calls is about $0.0123. This is a local estimate, not a provider invoice. One pair of rates prices every listed model: set them to the dearest model you list. Models that think before answering spend output tokens on thinking; if many answers show "reply cut off at the output limit", raise `LIVE_ARCHIVE_MAX_OUTPUT_TOKENS`.

## Outside clients
`POST /api/agent/archive-answer` (`run`, `question_id`, `answer`, header `X-Agent-Model`) lets a client with an agent key send its own model's answer for the latest run. It is stored as source `api` under the agent's name and labelled **self-declared** everywhere: the server cannot check which model wrote it. One answer per run and question, never replaced; no round quota; its own per-minute window (`LIVE_ARCHIVE_ANSWERS_PER_MINUTE`, default 30). A run must exist first, so the operator opens each month with `archive-run`.

## How answers get published
The pages (`archive/index.html`, `archive/q/Q01.html` ...) are plain HTML with no script. Both web apps serve them from the database, and every static snapshot contains them. The public demo site shows the scripted sample, labelled as scripted on every page and cell.

Recorded answers reach the public site only through the reviewed path in `docs/FIRST-REAL-ROUND.md`: `snapshot --export` writes `published/archive.json` next to `overview.json`, under the same hash, manifest, guard and ASCII rules; read both files, then `--approve --i-have-read-it`, commit, push. Mock and dry-run answers are never exported or rendered.

## What it does not claim
- Not a benchmark and not a score. Nothing here marks an answer right or wrong.
- "Changed" means the text differs. A reworded answer counts; a model can also answer differently from one call to the next with no change to the model.
- A `server call` label means this server requested that model id. A `self-declared` label is whatever the client typed.
- Scripted demo answers and their dates were written by hand. They are not model output.
- Cost figures are local estimates.
