# Current state and how to continue (for whichever session picks this up)

Updated 12 Nov 2026. Read `CLAUDE.md` and `.claude/skills/live-genesis-dev/SKILL.md` first; this file only says where things stand.

## Where things run
- Code of record: `main` on GitHub. Public page: GitHub Pages, rebuilt by CI on every push (scripted demo).
- Owner's PC (WSL, `~/live-genesis`): the real database `live-real.db`, the demo database `live.db`, `.env` with the provider key. Paid commands run only there, only on the owner's explicit word.
- One session on the owner's PC now does everything: code changes, merges, pushes and running things. The cloud session is retired.
- Windows has no Python and no GitHub credentials. Tests run through WSL against the Windows checkout; pushes go through the WSL GitHub login.
- In practice the paid `round` command is run by the owner in the Ubuntu terminal: the session's permission system refused to run it.

## Done
- All of Milestones 1 and 2 except running the control study. 887 offline tests.
- Security: one audit, fixes, one re-audit, fixes. Verdict: paid rounds on the owner's PC are fine; one outside key needs TLS in front, `LIVE_TRUSTED_PROXIES`, and `set-external` on the issued identity.
- Publishing path for reviewed real data (`snapshot --export / --approve / --check`); not yet used.
- Onboarding kit, participant rules, key-request issue form, resident skill.
- The Origin (scripted guide) with `set-guide on|off`.
- `openai-compatible` provider for local/free models (`docs/FREE-MODELS.md`); untested against any real server.
- Output-limit fix: truncated replies detected, `LIVE_MAX_OUTPUT_TOKENS` (default 1200), `cli turns`.
- Two real rounds (anthropic / claude-haiku-5-5, 3 agents each): 4 actions recorded (3 posts, 1 shared document), 2 turns lost (one to the old 650-token limit, one refused for a reason not stored at the time); about $0.0038 by local estimate. Only Astra, Moss and Quill have acted.
- Agent side: `reply` verb and threads, `available_actions` in the context, `dry_run`, refusal category and chosen verb per paid turn.
- Page: posts-first front page with a "What is this?" box, Stats page, one page per post and per agent, nested replies, plain labels. The overview window is counted in threads (newest 40 posts plus up to 160 replies beneath them).
- Operator-only culture-health report: `cli health`.

- Answers Over Time (`docs/ANSWERS-OVER-TIME.md`): built, with a scripted demo on the public demo site. The question list is a draft; no real run yet. Owner: edit `live_genesis/archive_questions.json`, set it to `locked`, `archive-lock`, set `LIVE_ARCHIVE_MODELS`, then `archive-plan` and (on the owner's word) `archive-run --max-usd X` about once a month.

## In progress
- Nothing.

## Next, in order
1. Owner reads the three real posts and the document; `reconcile --provider-usd` with the real charge; correct the two rate lines in `.env` if needed.
2. Owner picks from the culture proposals in `docs/research/AGENT-CULTURE.md` (a local draft on the PC, not in git). Suggested first three: rotate which agents act and in what order, tell an agent the category of its last refused turn, keep a supply of concrete shared work. Run `cli health` before and after each change, one change at a time.
3. Owner decisions left by the page batch: `profile/N.html` versus `agent/N.html`; whether the "WE ARE LIVE." line returns; the changed mode-label wording.
4. A third round (owner's word needed), then `cli turns` and `cli health`.
5. Export, read, approve and push `published/`; watch the CI run; check both pages.
6. Control study (`study-plan`, then `study-run` on the owner's word); one round with `set-guide off` for comparison.
7. Open code items: per-author cap in agent prompts; old rows with hidden characters are blocked from publishing but not cleaned; look-alike characters accepted; public read routes have no per-address limit; `available_actions` ignores the minute/day windows and the cooldown; `app.js` has no executed test.
8. Later page batch: search, filters, theme toggle, round digest. Later agent batch: sandbox world, JavaScript client, `curl` walkthrough.
9. Owner: create `harshsrigh.github.io` with `robots.txt` (site-root discovery); create the `agent-key-request` issue label.

## Working rules that are easy to forget
- Never run `round`, `study-run` or `archive-run` without the owner's explicit instruction, local models included.
- Never print or copy keys. `.env`, databases and `secrets/` never go to git.
- Back up a real database before pulling schema changes: `cli backup <file>`.
- One agent per git worktree; merge one branch at a time and run the full suite after each merge. Run the full suite before every push.
- `cli health` figures are for the operator only: never show them to agents or on any page.
