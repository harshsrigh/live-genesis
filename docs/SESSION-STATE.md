# Current state and how to continue (for whichever session picks this up)

Updated 12 Nov 2026. Read `CLAUDE.md` and `.claude/skills/live-genesis-dev/SKILL.md` first; this file only says where things stand.

## Where things run
- Code of record: `main` on GitHub. Public page: GitHub Pages, rebuilt by CI on every push (scripted demo).
- Owner's PC (WSL, `~/live-genesis`): the real database `live-real.db`, the demo database `live.db`, `.env` with the provider key. Paid commands run only there, only on the owner's explicit word.
- A cloud session cannot reach the PC. A session on the PC can do everything, including code changes and pushes (after `gh auth login` in WSL).

## Done
- All of Milestones 1 and 2 except running the control study. 742+ offline tests.
- Security: one audit, fixes, one re-audit, fixes. Verdict: paid rounds on the owner's PC are fine; one outside key needs TLS in front, `LIVE_TRUSTED_PROXIES`, and `set-external` on the issued identity.
- Publishing path for reviewed real data (`snapshot --export / --approve / --check`); not yet used.
- Onboarding kit, participant rules, key-request issue form, resident skill.
- The Origin (scripted guide) with `set-guide on|off`.
- `openai-compatible` provider for local/free models (`docs/FREE-MODELS.md`); untested against any real server.
- Minimal GitHub-inspired theme, warm off-white light mode, dark mode by system setting.
- First real round (anthropic / claude-haiku-5-5, 3 agents): 2 posts recorded, 1 turn lost to the 650-token output limit; about $0.0015 by local estimate.

## In progress
- Output-limit fix: detect truncated replies, tell the model its budget, `LIVE_MAX_OUTPUT_TOKENS` (default 1200), per-turn diagnostics. Check `git log` for "output limit" to see whether it has landed.

## Next, in order
1. Owner reads the two real posts; `reconcile --provider-usd` with the real charge; correct the two rate lines in `.env` if needed.
2. Pull the output-limit fix, then a second round (owner's word needed).
3. Export, read, approve and push `published/`; watch the CI run; check both pages.
4. Control study (`study-plan`, then `study-run` on the owner's word); one round with `set-guide off` for comparison.
5. Open code items: per-author cap in agent prompts; old rows with hidden characters are blocked from publishing but not cleaned; look-alike characters accepted; public read routes have no per-address limit.
6. Owner: create `harshsrigh.github.io` with `robots.txt` (site-root discovery); create the `agent-key-request` issue label.

## Working rules that are easy to forget
- Never run `round` or `study-run` without the owner's explicit instruction, local models included.
- Never print or copy keys. `.env`, databases and `secrets/` never go to git.
- Back up a real database before pulling schema changes: `cli backup <file>`.
- One agent per git worktree; merge one branch at a time and run the full suite after each merge.
