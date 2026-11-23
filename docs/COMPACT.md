# Answers Over Time: compact handover

Written 20 November 2026 for any future session (cloud, WSL, Codex) that starts without the earlier conversation. One page. Rules are in `CLAUDE.md` and win over this file; day-to-day state is in `docs/SESSION-STATE.md`.

## What this is

A public static site that puts the same playful questions (thirteen: ten main questions and three under a "Trending" tab) to AI answerers every month and keeps every reply side by side, in a "Group chat" look. Live: <https://harshsrigh.github.io/live-genesis/archive/index.html> (GitHub Pages, rebuilt by `.github/workflows/snapshot.yml` on every push to `main`).

The owner is not a programmer. Write to them in plain words, give commands in copyable blocks, and put decisions to them as short choices with a recommendation.

## How it got here (latest wins)

1. LIVE Genesis, an agent-only society. **Removed from the code** on 14 Nov 2026. Do not bring it back.
2. Answers Over Time with API models (the "model track"). Kept in the code, **never published**.
3. Chat-app comparison: ChatGPT, Gemini, Grok asked **by hand** by the owner, plus a Human column. This is the product. No hand-collected month has been imported yet.
4. A second group of answerers, "AI models": models asked the same ten questions by software (`archive-chatbot-ask`). **This is what is live today:** four local models (Phi-4 mini, Gemma 3 4B, Granite 4.2 3B, Qwen 3.5 4B); ten questions asked 19 Nov 2026 and three more asked 22 Nov, each exported and approved by the owner (`published/`, 52 answers).

## State on 20 Nov 2026 (some lines brought up to date on 21 and 23 Nov)

- `main` passes 648 offline tests (`python -m pytest -q`, about a minute), counted on 23 Nov at `292f9f7`.
- Three questions (C011 to C013) were appended on 20 Nov, asked on 22 Nov with `--questions` and published. Since 23 Nov they are marked `"list": "trending"` in `archive_chatbots.json` (a display attribute only) and the site lists them under a "Trending" tab; `archive-chatbot-ask --list trending` asks only those, and from December one ask covers all thirteen (`docs/SESSION-STATE.md`, "Decided on 23 Nov").
- Free-only OpenRouter collector, guided Windows launcher (`Collect-Free.cmd`) and its guards are merged. **No real request to OpenRouter's model API has ever been made**; tested with mocked answers only; the launcher was never run on Windows itself.
- The owner's step-by-step guide for a month: `docs/MONTHLY-ROUND.md`.
- Since 23 Nov: `cli backup-encrypted --to DIR` writes a gpg-encrypted copy of the database and a counts-only receipt into a folder the owner names (a cloud-synced one); `cli backup-check FILE.gpg` proves it opens. The passphrase is the owner's alone and gpg asks for it; the software makes no network request. Guide: `docs/BACKUP.md`. Not yet run on the owner's PC.
- A weekly scheduled task ("Weekly trend question ideas", Mondays 18:35 UTC) runs `archive-trends` in a fresh cloud session and reports 5 to 8 draft question ideas. It changes nothing; the owner adds a question by hand.

## The owner's decisions that shape the pages

- Published pages, cards and data files do not say how replies were collected. The word "round" is on no page.
- Refused or skipped replies: only the fact is published, never the words.
- Once `published/` exists, the scripted sample is not built beside it (no `demo/`, no "Sample site" link).
- A model is shown as "Name (Maker)", never "(local)" (`archive_pages.shown_name`, display only; the approved data is untouched).
- Share cards carry no "read-only · no payments · id" line. The public site has no "The exact words we used" or "The details behind each answer" block (the local app keeps both).
- Only links and switches look like pills; labels and facts are plain text. The whole question card is one link.
- Defaults the owner can change: indigo accent; a question's short headline is fixed with the question; the Copilot joke stays on "How it works"; the Human column rule (owner posts, says replies may be quoted, no usernames or links, prefer a summary).

## Open

- Share links, settled on 20 Nov: the publishing workflow makes a picture of every share card; a question page carries preview tags. Since 23 Nov the share line is "Facebook · Save card · Open picture" (plain links to the card's picture, the first with a save name), and a question page has a "Caption to copy" block; the links named Instagram and TikTok are gone, and nothing posts anywhere. Never tried on a real phone yet (`docs/SHARE-CARD.md`). Facebook still opens Facebook's own share page. The front page has no history squares and no key; a changed answer says "Changed".
- "How it works" still speaks of "apps" and has a "Humans too" rule while only models are shown. Not changed; the owner's call.
- First hand-collected month for the chat apps: the owner reads the ten draft questions (fixed for good at the first import), then follows `docs/MONTHLY-ROUND.md`.
- Codex's Windows review of the collector and launcher: instructions given, report not seen yet.
- Per-group question lists are not built: every answerer group shares the ten questions. Build before a non-AI group (airline or phone-company chatbots) is added, and read those services' terms first.
- The name ("Answers Over Time" is generic), social handles, the repository's About text (the owner sets it by hand).

## Parked by the owner

- "Who said it?" blind reveal (read answers without names, pick one, reveal). Agreed shape: whole answers only, no excerpts; neutral bubbles in a shuffled order; works for apps and models; no stored choices; a route label allowed for the model group only. Come back when there is enough data.
- Human voting on the site; training internal models on the answers (the chat apps' terms forbid training on their output).

## How work is done here

- Several sessions push to `main`: the cloud session (leads, uses subagents in git worktrees), the WSL session on the owner's PC (owns the real database, exports and approvals on the owner's word), Codex on Windows (independent review through docs-only pull requests). They cannot message each other; anything for another session goes through the owner.
- Before every push: fetch, merge `origin/main`, run the full suite. Pushes are often refused because another session pushed first; merge and try again.
- Merging a subagent's branch: `git merge --no-ff --no-commit`, apply its proposed `CLAUDE.md` and skill lines yourself (subagents do not edit those files), run the suite, commit, push, watch CI, check the live site.
- Scratch databases: `LIVE_DB=<a file outside the repository>`. Never leave a `*.db` or `secrets/agent-keys*.json` in the repository.
- Never: run `archive-run` or `archive-chatbot-ask` without the owner's word for that run; set `LIVE_ALLOW_PAID_CALLS=YES`; approve an export; print or commit a key or a base URL; add code that opens a chat app or Reddit; rewrite or shorten a recorded reply; merge `codex/local-0.2`.

## Where to read more

`CLAUDE.md` (rules, map, commands) · `docs/SESSION-STATE.md` · `docs/PLAN-CHATBOT-COMPARISON.md` (decisions) · `docs/ANSWERS-OVER-TIME.md` (settings) · `docs/MONTHLY-ROUND.md` · `docs/OPENROUTER-COLLECTOR.md` · `docs/SECURITY.md` · `docs/ROADMAP.md`.
