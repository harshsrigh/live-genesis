# Current state and how to continue (for whichever session picks this up)

Updated 21 Nov 2026 by the session on the owner's PC (WSL), at commit `f9804ed`. Read `CLAUDE.md` first; `docs/COMPACT.md` is the one-page version of this file. This file only says where things stand, and for each statement whether it was checked on 21 November or carried over from an earlier note.

## In one paragraph
The repository is **Answers Over Time** and nothing else. The public site shows one group of answerers today, "AI models": four local models that were asked the ten chat-app questions by software on 19 November 2026, exported and approved by the owner on 20 November. The chat apps (ChatGPT, Gemini, Grok) and the Human column are configured but have no replies yet: the first hand-collected month is still the owner's to do. Three more questions (C011 to C013) were added to the list on 20 November; nothing has asked them, so they are on no page. The model track (`archive-run`) stays in the code and is not published.

## Checked on 21 Nov 2026
On the owner's PC, in `~/live-genesis` on `main` at `f9804ed`, after a fresh backup of the real database.

- **Tests:** `python -m pytest -q` gives 633 passed.
- **Real database, chat-app track** (`archive-status`, read-only): one run, number 1, kind `asked`, dated 2026-11-19, 40 answers (four models, ten questions), no Human answers. Recorded questions: C001 to C010. No hand-entered run.
- **Real database, model track:** both question lists locked and matching their files (20 core, 11 trending); no run recorded.
- **Configuration** (`live_genesis/archive_chatbots.json`): status `draft`, format /3, groups `ai-apps` (chatgpt, gemini, grok, Human) and `ai-models` (phi4-mini, gemma3-4b, granite42-3b, qwen35-4b), thirteen questions C001 to C013. `archive-status` reports no problem with the three appended questions: the list is add-only and nothing refused.
- **`archive-chatbot-ask --dry-run`** (free, called nothing): it plans **all thirteen questions** for the four models, 52 calls, worst-case estimate $0.000000 (an estimate from token counts, not an invoice), suggested ceiling `--max-usd 0.01`. There is no option to ask only some questions (`--models` picks models, nothing picks questions). See "Open: asking the new questions".
- **`published/`:** `snapshot --check published` says intact and approved; exported 2026-11-20T03:45:07Z; one run, 40 answer rows, no Human rows.
- **Local build as the workflow makes it** (`LIVE_SITE_URL=... snapshot OUT --from-data published --archive-only`, `LIVE_CARD_IMAGES` unset): front page, the C001 question page, the C001 share card and "How it works" were opened in a browser and read. They match the live site. No sample site is built beside it, no footer line, no history squares, models shown as "Name (Maker)", the answering styles show on "How it works", C011 to C013 appear nowhere. Without `LIVE_CARD_IMAGES` the Instagram and TikTok links open the card page and a question page has no preview picture tag; that is the expected local difference.
- **Live site** (checked 20 Nov at `f9804ed`): CI green; nav "AI models · How it works"; "full answer on the site" only on the four cards where a reply is really cut (C002, C003, C006, C007); `card/C001.png` is served.

## Seen on 21 Nov and not changed
- **"How it works" still talks about apps on a site that shows only models:** "each app gets the same questions", "Every app gets the exact same words", "When an app refuses", and a "Humans too" rule although there is no Human answer. The copy is in `archive_pages.py`; changing it is the owner's call.
- **`snapshot --check` and `--export` print a wrong first NOTE** for model rows: it calls them "ENTERED BY HAND", and the next line corrects it. Wording only; the data is right.
- **`published/archive.json` says `"source": "asked"`** on each row, although the pages say nothing about how replies were collected. The owner approved the file as it is.
- **`archive-chatbot-ask --dry-run` prints the configured names** ("Phi-4 mini (local)"); the pages show "Name (Maker)". That is by design (`archive_pages.shown_name`, display only). Do not rename the models in the configuration.

## Open: asking the new questions
C011 "Does home-court advantage really exist?", C012 "Why do sports teams have mascots?", C013 "What's the best comedy movie ever made?".

The command as it is would ask all thirteen. For C001 to C010 that records a second November answer two days after the first, and the pages would compare the two. The choices for the owner:

1. **Wait for the December ask.** All thirteen are asked together then; C011 to C013 simply start a month later. No code change.
2. **Add a `--questions` option** (the smallest change: the plan and the run keep only the named ids, refuse an unknown id, and the run row records the ids it asked; a model's bubble for a question it was not asked that day keeps its earlier answer, which the pages already handle). Not built; needs the owner's go-ahead, then tests.
3. **Ask all thirteen now** and accept a second November entry for the first ten.

Whichever it is, the run itself needs the owner's explicit word for that run, with `--max-usd`.

## Carried over, not checked on 21 Nov
- From this session's earlier work (19 and 20 Nov): Node, Playwright, its browser and an emoji font are installed under the owner's home folder in WSL; `scripts/capture-cards.mjs` wrote 50 cards from the published answers. The launcher's two defects (a preview that created a database, a traceback when input ends) are fixed and tested offline.
- From `docs/COMPACT.md` (cloud session, 20 Nov): no real request to OpenRouter's model API has ever been made; the launcher was never run on Windows itself; a weekly cloud task (Mondays 18:35 UTC) reports draft question ideas and changes nothing; Codex's Windows review of the collector has not been seen.
- From earlier notes: a reviewed export of model-track answers was once published and withdrawn by the owner. The real database was made before the society was removed and still holds its tables; the code leaves them alone.
- Open code items from 14 and 15 Nov, not looked at again: the Content-Security-Policy still allows same-origin scripts and style attributes no page uses (`web.CONTENT_SECURITY_POLICY`); `archive.publish_problem` has no caller and is kept with its tests; `docs/SOCIAL-POSTS.md` and parts of `docs/PLAN-CHATBOT-COMPARISON.md` and `docs/FREE-MODELS.md` describe the state of their date.

## Where things run
- Code of record: `main` on GitHub. The cloud session and Codex also push to it: fetch and re-merge before every push.
- Public site: GitHub Pages, rebuilt by `.github/workflows/snapshot.yml` on every push to `main`, from `published/`. The workflow also makes a PNG of every share card; if that step fails, nothing is published. Address: `https://harshsrigh.github.io/live-genesis/`.
- Owner's PC (WSL, `~/live-genesis`): the real database, `.env`, the local models. Model-calling commands run only there, only on the owner's explicit word for that run.
- Windows has no Python. Tests run through WSL.
- Backups sit outside the repository, in the owner's home folder in WSL and in `~/live-genesis/backups/` (git-ignored). Latest: 21 Nov 2026, before the checks above.

## Next (the owner's moves)
1. Decide how C011 to C013 get asked (the three choices above).
2. The first hand-collected month for ChatGPT, Gemini, Grok and, if wanted, the Human column: `docs/MONTHLY-ROUND.md`, step by step. Read each app's terms first.
3. After any new answers: export, read every reply, approve (only the owner), check, push.
4. First post of a share card, by hand (`docs/SOCIAL-POSTS.md`).
5. Still open from `docs/PLAN-CHATBOT-COMPARISON.md`: the name, the social accounts, whether "How it works" should speak of models while only models are shown.

For a coding session, when asked: help word questions; never write questions from the trends feed automatically; no human-voting code and no model-track publication without the owner's instruction.

## Working rules that are easy to forget
- Never run `archive-run` or `archive-chatbot-ask` without the owner's explicit word for that run, local and free models included. Each always needs `--max-usd`. (`archive-chatbot-ask --dry-run` is free and calls nothing.)
- Never set `LIVE_ALLOW_PAID_CALLS` yourself. Never print or copy keys or an endpoint's base URL. `.env`, databases and `secrets/` never go to git.
- Scripted demo data is never presented as a real answer.
- Only the owner exports and approves. Never edit `published/` by hand.
- Back up the real database before any command of a newer version touches it.
- One agent per git worktree; run the full suite before every push.
- Scratch databases go outside the repository (`LIVE_DB=<path>`).
