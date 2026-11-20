# Answers Over Time roadmap / working task tracker

**Legend:** [x] built in this repo; [ ] next. A checkmark means implemented, **not production-hardened**.

Rules and settings: `docs/ANSWERS-OVER-TIME.md`. The owner's decisions for the chat-app track: `docs/PLAN-CHATBOT-COMPARISON.md`. Where things stand: `docs/SESSION-STATE.md`.

## Chat-app track (the product; the only thing published)
- [x] ChatGPT, Gemini and Grok asked **by hand**, plus a Human column; its own question list (`live_genesis/archive_chatbots.json`, C001 ...); template to fill in and import (`archive-chatbot-template`, `archive-chatbot-import`); refusals and non-answers left out; no politics (`tests/test_archive_chatbots.py`).
- [x] Reading pages without script: front page is the comparison, one page per question, share cards per question and per app, machine-readable labels and `archive/data/` files (`live_genesis/archive_pages.py`).
- [x] "Group chat" look on every Answers Over Time page (owner's choice of mock-up A) and any number of answerers in groups: `"groups"` / `"group"` in `archive_chatbots.json` (format `/3`, up to 24 apps in 6 groups; payload `live-genesis-archive/6`, `live-genesis-chat-archive/2`), position palette, group switch, one thread per group, share cards of at most four bubbles that paginate (`tests/test_archive_groups.py`).
- [x] A second kind of answerer, models asked the same questions by software: `"kind": "model"` entries in `archive_chatbots.json` (group "AI models"; four local models added on 19 Nov 2026), `cli archive-chatbot-ask` (PAID class: the gates of `archive-run`; `--dry-run` is free), runs and answers of kind `asked`, the model's name in place of an app's conditions, schema `live-genesis-chat-archive/3` / `live-genesis-archive/7` only when such an answerer is recorded, a one-time rebuild of four tables (`tests/test_archive_ask.py`, `tests/fixtures/archive_schema_v7.sql`). Never run against a real model.
- [ ] Owner decides which models to add to the group "AI models" and gives the word for the first `archive-chatbot-ask`. Their replies are published only after the owner reads and approves the export.
- [x] The public site holds the chat-app track only: `snapshot` writes the chat-app pages, cards and data files, an `archive.json` of two keys (`schema`, `chatbots`), and a root page that leads to `archive/index.html` (`tests/test_snapshot.py`).
- [x] Reviewed export: `snapshot --export` writes `published/archive.json` and `MANIFEST.json` only (chat-app track; no model answer); `--approve`, `--check`, `--from-data`; closed key sets, strict JSON, content guards, hash (`tests/test_snapshot_archive_only.py`, `tests/test_published.py`).
- [x] "Answering style", just for fun (20 Nov 2026): on "How it works" each AI answerer gets a joke name on a five-step scale from wild to grown-up, counted from all its shown answers (emoji, exclamation marks, slang, careful words, length) by `live_genesis/profiles.py`. Display only: in no data file or export. How it is worked out: `docs/ANSWERS-OVER-TIME.md`, "Answering style" (`tests/test_profiles.py`).
- [x] Share-card capture: `scripts/capture-cards.mjs` (needs the operator's own Playwright; tested on a scripted demo snapshot only). Posting routine: `docs/SOCIAL-POSTS.md`. Posting stays manual.
- [x] Friendlier words everywhere a person reads them (15 Nov 2026): pages, cards, footer (each honesty label said once), the sheet's notes, the import messages with a plain-words summary; the owner's step-by-step guide `docs/MONTHLY-ROUND.md`. A refused or skipped reply's words are in no published file, only the fact; the Human column has a working rule. Both are defaults the owner can change (`docs/PLAN-CHATBOT-COMPARISON.md`).
- [ ] Owner reads the chat-app questions (the file's status is `draft`), collects the first round by hand, imports it, exports, reads and approves it. Until then the public site shows scripted samples.
- [ ] First share card posted by hand. **Human voting on the site is not built** and needs the owner's instruction (plan in `docs/ANSWERS-OVER-TIME.md`).
- [ ] Monthly collections. Nothing schedules them.

## Model track (kept in the code; not published)
- [x] Fixed core list and a growing trending list, hash-locked (`archive-lock`); runs with append-only answers and a `changed` flag; repeat samples (`LIVE_ARCHIVE_SAMPLES`); scripted demo (`demo-archive`); pages for local viewing (`live_genesis/archive.py`, `tests/test_archive*.py`).
- [x] Several services in one run: named OpenAI-compatible endpoints with their own base URL, key variable and rates; `pricing_basis` per answer; pinned sampling; a failed call on a zero-priced endpoint skips that model, on a priced endpoint stops the run until `reconcile` (`tests/test_archive_endpoints.py`).
- [x] `archive-trends`: prints trending search terms as candidates; the owner writes trending questions by hand (`tests/test_trends.py`).
- [x] Free-model guide: `docs/FREE-MODELS.md`, Part 2, a dated record (12 November 2026). **Nothing in it was tried against a service.**
- [x] Budget gate shared by every paid call: reservations, the $1,000 gate, unconfirmed charges block paid calls, operator reconciliation (`tests/test_budget.py`, `tests/test_controls.py`). A local estimate, never a provider limit.
- [ ] Whether and when the model track is run again or published is the owner's decision. `archive-run` needs the owner's explicit word each time. Model answers have no path to the public site today; the publication rules are kept and tested (`archive.publish_problem`).

## Open
The name and a trademark search; how long to commit; a Llama model would need "Built with Llama" shown; the Ollama runner version is not recorded with an answer; the capture script is untested against the public site address; the Content-Security-Policy still allows same-origin script and style attributes although no page uses either (`web.CONTENT_SECURITY_POLICY`; tightening it changes every published page).

## Not built, on purpose
No scheduler, no automatic posting, no scraping of chat apps, no payments or donation links, no accounts, no write route on any web surface.

## History
**The agent society was removed on 14 November 2026** (owner: "remove observatory from this project. We won't doing anything with those 10 agents").

The project began as "LIVE Genesis": ten seeded software agents with keys, posts, proposals and governance, an internal credit ledger, shared documents, a cooperative game, a scripted guide voice, memory notes, an operator task board, a health report, paid rounds in which a model took a turn for each agent, a control study, an authenticated agent API, a read-only observatory page, and an onboarding kit for outside agents. It pivoted to Answers Over Time on 12 November 2026 and to the chat-app comparison on 14 November 2026.

- **Last commit that holds the society: `29d7594`** (`git checkout 29d7594` shows all of it: code, tests, docs).
- **Existing databases keep the society's tables untouched.** The code no longer creates, reads, alters or drops them. An older database still opens and the archive commands work on it. A database created now holds only the spend accounting, the audit log and the archive tables.
- Removed with it, because they depended on it: the outside-agent answer route of the model track (`POST /api/agent/archive-answer`; rows it stored, source `api`, still read), and the full reviewed export (the kind that also wrote `overview.json`; a manifest that says `"contents": "full"` is refused).
- Two real rounds were run for the society before the pivot (3 agents each, about $0.0038 by local estimate). They are the only model calls recorded in the earlier documents; none was for the archive and none was published.
- Records of earlier reviews and checks are kept as they were written under `docs/history/`: the first pivot's decision record (`PIVOT-ANSWERS-OVER-TIME.md`), `ARCHIVE-REVIEW.md`, `SECURITY-PASS-REVIEW.md`, `DOCUMENTS-REVIEW.md`, `LEDGER-GOVERNANCE-REVIEW.md`, `FIX-REVERIFICATION.md`, `WINDOWS-VERIFICATION.md`, `WINDOWS-OPERATIONS-VERIFICATION.md`. They describe the code of their date, much of which is gone; they are not rewritten.
- `origin/codex/local-0.2` is an old reference branch of the society. Never merge it.
