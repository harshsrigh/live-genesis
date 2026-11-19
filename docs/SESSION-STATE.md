# Current state and how to continue (for whichever session picks this up)

Updated 14 Nov 2026, when the agent society was removed. Read `CLAUDE.md` and `.claude/skills/live-genesis-dev/SKILL.md` first; this file only says where things stand.

## In one paragraph
The repository is **Answers Over Time** and nothing else: the chat-app comparison (ChatGPT, Gemini, Grok and a Human column, asked by hand), which is the public site, and a model track that stays in the code and is not published. The agent society the project began with (ten agents, posts, proposals, rounds, the observatory page, the agent API) was removed on 14 November 2026; `docs/ROADMAP.md`, "History", says what went and at which commit it last existed. **The next moves are the owner's:** the first hand-collected chat-app round.

## What the repository itself shows (checked on 14 Nov 2026)
- `live_genesis/archive_chatbots.json`: status `draft`, format /3, three apps and Human in one answerer group, ten questions (C001 to C010). Editable until the first import, fixed after it. The pages have the "Group chat" look and take any number of answerers in groups (payload schema /6).
- `live_genesis/archive_questions.json` (20 core questions) and `live_genesis/archive_trending.json` (11 trending questions): status `locked` in the files.
- No `published/` folder, so CI builds the scripted demo as the whole public site.
- The full test suite passes offline: `python -m pytest -q`.

## What earlier notes say and this session did not check
From `docs/PLAN-CHATBOT-COMPARISON.md` (written by the PC session on 14 Nov 2026). The real database is on the owner's PC and was not opened for this update:
- Both model question lists are locked in the real database, and two real runs of local models are recorded there at $0.
- A reviewed export of model answers was once published and then withdrawn by the owner, with the political trending question. The public site shows scripted samples only.
- No chat-app round has been collected yet.

## Where things run
- Code of record: `main` on GitHub. Other sessions (cloud, Codex) also push to it: fetch and re-merge before every push.
- Public page: GitHub Pages, rebuilt by `.github/workflows/snapshot.yml` on every push to `main`. Address: `https://harshsrigh.github.io/live-genesis/`.
- Owner's PC (WSL, `~/live-genesis`): the real database, the demo database, `.env` with any provider key. Model-calling commands run only there, only on the owner's explicit word.
- Windows has no Python. Tests run through WSL against the Windows checkout.
- The real database on the PC was made before the society was removed, so it still holds the society's tables. The code leaves them alone. Back it up before running a newer version on it: `python -m live_genesis.cli backup NEW-FILE`.

## Next (the owner's moves, in order)
1. Read the ten chat-app questions in `live_genesis/archive_chatbots.json` and edit them while the file's status is `draft`; `docs/ANSWERS-OVER-TIME.md` ("The chat-app track") says when they become fixed.
2. Read the defaults recorded in `docs/PLAN-CHATBOT-COMPARISON.md` ("Decided by default on 15 November 2026") and change any you disagree with; still open there: the name.
3. Collect the first round by hand, following `docs/MONTHLY-ROUND.md` step by step (sheet, ask, check, store, export, read, approve, push, cards).
4. Export, read, approve, commit, push (only the owner): `snapshot --export`, read `published/archive.json`, `snapshot --approve --i-have-read-it`.
5. First share card and post by hand (`docs/SOCIAL-POSTS.md`).

For a coding session, when asked: help word questions; never write trending questions from the trends feed automatically; no human-voting code and no model-track publication without the owner's instruction.

## Open code items
- The Content-Security-Policy still allows same-origin scripts and style attributes that no page uses (`web.CONTENT_SECURITY_POLICY`); tightening it changes the text inside every published page.
- The public copy was rewritten in a friendlier voice on 15 November 2026 (footer included: it no longer reads "Part of LIVE Genesis"). The owner can change any of it; the strings are in `archive_pages.py`.
- `archive.publish_problem` and the closed-key rules of the model payload have no caller since the full export went; they are kept, with tests, for the day model answers are published again.
- `docs/SOCIAL-POSTS.md` and parts of `docs/PLAN-CHATBOT-COMPARISON.md` and `docs/FREE-MODELS.md` (Part 2) describe the state of their date.

## Working rules that are easy to forget
- Never run `archive-run` without the owner's explicit instruction, local and free models included. It always needs `--max-usd`.
- Never print or copy keys. `.env`, databases and `secrets/` never go to git. Never store, print or publish an endpoint's base URL.
- Scripted demo data is never presented as a real answer: not on a page, not on a share card, not in a post.
- Only the owner exports and approves.
- Back up a real database before pulling schema changes.
- One agent per git worktree; run the full suite before every push.
- Scratch databases go outside the repository (`LIVE_DB=<path>`).
