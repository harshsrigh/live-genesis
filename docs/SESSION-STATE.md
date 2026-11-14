# Current state and how to continue (for whichever session picks this up)

Updated 12 Nov 2026. Read `CLAUDE.md` and `.claude/skills/live-genesis-dev/SKILL.md` first; this file only says where things stand.

## In one paragraph
The project now leads with **Answers Over Time** (`docs/PIVOT-ANSWERS-OVER-TIME.md`): basic questions put to AI models about monthly, every answer kept. The code for it is merged and the public site shows it with a scripted demo. **Nothing real exists yet:** both question lists are drafts, nothing is locked, no model has been called for the archive, and no endpoint has been tested against a real server. The next moves are the owner's. The agent society still works and is the second product.

## Where things run
- Code of record: `main` on GitHub. Public page: GitHub Pages, rebuilt by CI on every push (scripted demo; there is no `published/` folder). Since 13 Nov 2026 it holds Answers Over Time only (`snapshot --archive-only`); the society pages are not published, the root address leads to the archive. Of Answers Over Time it holds the chat-app comparison only (owner: "Remove anything from previous models"): no `models.html`, `trending.html`, `q/`, `t/`, model card or model data is published, and `archive.json` there holds `schema` and `chatbots` only. The local apps and a full snapshot keep every model page. Archive pages: `https://harshsrigh.github.io/live-genesis/archive/index.html`.
- Owner's PC (WSL, `~/live-genesis`): the real database `live-real.db`, the demo database `live.db`, `.env` with the provider key. Model-calling commands run only there, only on the owner's explicit word.
- One session on the owner's PC now does everything: code changes, merges, pushes and running things. The cloud session is retired.
- Windows has no Python and no GitHub credentials. Tests run through WSL against the Windows checkout; pushes go through the WSL GitHub login.
- In practice the paid `round` command is run by the owner in the Ubuntu terminal: the session's permission system refused to run it. Expect the same for `archive-run`.

## Merged on `main`
- **Answers Over Time, core archive:** 20 draft questions (`live_genesis/archive_questions.json`), lock, runs, append-only answers, `changed` flag, script-free pages under `archive/`, logo (`docs/brand/`), scripted demo (`demo-archive`).
- **Trending track:** `live_genesis/archive_trending.json` (12 draft questions), `archive-lock --set trending` (appends; a locked question never changes), `archive-trends` (prints candidates, writes nothing), `archive/trending.html`, `archive/t/`.
- **Chat-app comparison (lead, merged 14 Nov 2026):** `live_genesis/archive_chatbots.py` + `archive_chatbots.json` (three apps + Human, ten draft questions C001-C010, format /2, payload schema /5); front page `archive/index.html` is the comparison, the core list moved to `archive/models.html` (local apps and a full snapshot only; not on the public site); see `docs/PLAN-CHATBOT-COMPARISON.md`. Next: the owner reads the ten questions, collects the first round by hand, imports, exports, approves.
- **Share cards:** `archive/card/<ID>.html`; `scripts/capture-cards.mjs` writes them as 1080 x 1920 PNG files (needs the operator's own Playwright; tested on a demo snapshot only). Posting routine: `docs/SOCIAL-POSTS.md`.
- **Named endpoints:** several services in one run (`LIVE_ARCHIVE_ENDPOINTS`), own model-id rule, `pricing_basis` per answer (`rates` / `local` / `declared-free`), pinned sampling, reported model name and local model digest per answer, `archive-check-endpoints`.
- **Per-model failure isolation:** a failed call on a zero-priced endpoint skips that model and the run goes on (`complete_with_gaps`); on a priced endpoint the run stops until `reconcile`.
- **Free-model guide:** `docs/FREE-MODELS.md`, Part 2: a dated record (12 Nov 2026) of local and hosted free options and their terms, with the settings for each. Nothing in it was tried against a service.
- **Agent kit:** `llms.txt` and the agent guide document `POST /api/agent/archive-answer`.
- **Pull requests 4 to 7** are merged. The `codex/*` review and verification records are on `main` as documents (`docs/FIX-REVERIFICATION.md`, `docs/DOCUMENTS-REVIEW.md`, `docs/LEDGER-GOVERNANCE-REVIEW.md`, `docs/WINDOWS-VERIFICATION.md`, `docs/WINDOWS-OPERATIONS-VERIFICATION.md`).
- **Windows test fixes:** tests read files as UTF-8 explicitly, and the link checks run only where links can be made.
- Everything listed under "Society: done" below. The test suite is offline; run `python -m pytest -q` for the current count.

## In progress (other branches; check before starting)
- `LIVE_ARCHIVE_SAMPLES` (1 to 5 samples per question per run) is merged on `main`.
- Edits to `docs/SECURITY.md`, `docs/ANSWERS-OVER-TIME.md`, `docs/DOCUMENTS-REVIEW.md` and `.env.example` by other sessions. Pull before touching them.

## Next: Answers Over Time (the owner's moves, in order)
1. **Review the draft questions:** 20 core (`live_genesis/archive_questions.json`) and 12 trending (`live_genesis/archive_trending.json`). `python -m live_genesis.cli archive-plan` prints them. Edit, cut or replace. After the lock the core list can never change and a trending question can never change or be removed.
2. **One sample or several: decided, three** (owner, 13 Nov 2026; `LIVE_ARCHIVE_SAMPLES=3` is set in the `.env` on the PC). Keep it for every run: the method cannot change afterwards.
3. **Lock:** set `"status": "locked"` in each file, commit, then on the real database `archive-lock` and `archive-lock --set trending`. Back up the database first (`cli backup <file>`).
4. **Choose models.** Read `docs/FREE-MODELS.md` Part 2. For each hosted service, read its current terms yourself. Local tags are unverified: confirm with `ollama show`.
5. **Report the WSL machine,** so the model choice fits it: RAM (`free -h`), CPU threads (`nproc`), GPU if any (`nvidia-smi`), free disk (`df -h ~`), and Ollama (`ollama --version`, `ollama list`; or "not installed"). Say whether Ollama runs inside WSL or on Windows: `localhost` must reach it from where the run starts.
6. **Configure and check, free:** endpoint settings in `.env`, then `archive-check-endpoints` and `archive-plan`. Read both outputs.
7. **First run: only on the owner's explicit word,** with a ceiling: `archive-run --max-usd X`. This holds for a run on local or free models too. A session must not start it on its own, and must not treat an earlier "yes" as standing permission.
8. Then `archive-status`. Model answers are not published on the public site: `snapshot --export --archive-only` holds the chat-app track only and leaves them out (`docs/FIRST-REAL-ROUND.md`). They show in the local apps and in a full local snapshot.
9. After the first published run: Answers Over Time moves to the site's front page; first share card and post by hand (`docs/SOCIAL-POSTS.md`).

For Claude, when asked: apply the proposed lines to `CLAUDE.md` and the skill file; help word questions; never write trending questions from the trends feed automatically; no human-voting code without the owner's instruction.

## Society: done
- All of Milestones 1 and 2 except running the control study.
- Security: one audit, fixes, one re-audit, fixes. Verdict: paid rounds on the owner's PC are fine; one outside key needs TLS in front, `LIVE_TRUSTED_PROXIES`, and `set-external` on the issued identity.
- Publishing path for reviewed real data (`snapshot --export / --approve / --check`); not yet used. For the public repository the export is `--export --archive-only` (`archive.json` with the chat-app track only, and the manifest; no society table read, no model answer exported); the full export, which also writes `overview.json`, stays in the code but is not the path for the public site.
- Onboarding kit, participant rules, key-request issue form, resident skill.
- The Origin (scripted guide) with `set-guide on|off`.
- `openai-compatible` provider for local/free models (`docs/FREE-MODELS.md`, Part 1); untested against any real server.
- Output-limit fix: truncated replies detected, `LIVE_MAX_OUTPUT_TOKENS` (default 1200), `cli turns`.
- Two real rounds (anthropic / claude-haiku-5-5, 3 agents each): 4 actions recorded (3 posts, 1 shared document), 2 turns lost (one to the old 650-token limit, one refused for a reason not stored at the time); about $0.0038 by local estimate. Only Astra, Moss and Quill have acted. These are the only model calls the project has made; none was for the archive, and none is published.
- Agent side: `reply` verb and threads, `available_actions` in the context, `dry_run`, refusal category and chosen verb per paid turn.
- Page: posts-first front page with a "What is this?" box, Stats page, one page per post and per agent, nested replies, plain labels. The overview window is counted in threads (newest 40 posts plus up to 160 replies beneath them).
- Operator-only culture-health report: `cli health`.
- Three culture changes the owner approved from `docs/research/AGENT-CULTURE.md`: round rotation with a stored plan (`--order id` / `--only` override), `last_turn` feedback by category, and the operator task board (`task-add`, `task-close`, `task-list`). None has been exercised by a real model yet, and no real task has been added.

## Society: next, in order (second priority since the pivot)
1. Owner reads the three real posts and the document; `reconcile --provider-usd` with the real charge; correct the two rate lines in `.env` if needed.
2. Owner writes the first real tasks with `task-add` (the board is empty), and decides the wording left open by the task board: the `SET BY THE OPERATOR` label, the constitution line, the reward-wording and link refusals. The remaining culture proposals are in `docs/research/AGENT-CULTURE.md` (a local draft on the PC, not in git). The three changes landed together, so the next `cli health` reading cannot separate their effects; baseline before them: 3 of 10 agents had acted, 3 of 3 posts unanswered, 1 work action to 3 talk actions.
3. Owner decisions left by the page batch: `profile/N.html` versus `agent/N.html`; whether the "WE ARE LIVE." line returns; the changed mode-label wording.
4. A third round (owner's word needed), then `cli turns` and `cli health`.
5. No society export to the public repository (owner decision): `published/` takes only the archive-only export, which reads no society table and holds the chat-app track only (no model answer, so no answer that an outside agent submitted).
6. Control study (`study-plan`, then `study-run` on the owner's word); one round with `set-guide off` for comparison.
7. Open code items: per-author cap in agent prompts; old rows with hidden characters are blocked from publishing but not cleaned; look-alike characters accepted; public read routes have no per-address limit; `available_actions` ignores the minute/day windows and the cooldown; `app.js` has no executed test.
8. Later page batch: search, filters, theme toggle, round digest. Later agent batch: sandbox world, JavaScript client, `curl` walkthrough.
9. Owner: create `harshsrigh.github.io` with `robots.txt` (site-root discovery); create the `agent-key-request` issue label.

## Working rules that are easy to forget
- Never run `round`, `study-run` or `archive-run` without the owner's explicit instruction, local and free models included. `archive-run` always needs `--max-usd`.
- Never print or copy keys. `.env`, databases and `secrets/` never go to git. Never store, print or publish an endpoint's base URL.
- Scripted demo data is never presented as model output: not on a page, not on a share card, not in a post.
- Back up a real database before pulling schema changes: `cli backup <file>`.
- One agent per git worktree; merge one branch at a time and run the full suite after each merge. Run the full suite before every push.
- `cli health` figures are for the operator only: never show them to agents or on any page.
- Scratch databases go outside the repository (`LIVE_DB=<path>`); `init` also writes a key file under `secrets/` named after the database: delete it afterwards.
