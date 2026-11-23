# Current state and how to continue (for whichever session picks this up)

Updated 23 Nov 2026 (UTC) by the session on the owner's PC (WSL), at commit `292f9f7`. Read `CLAUDE.md` first; `docs/COMPACT.md` is the one-page version of this file. This file only says where things stand. "Checked on 23 Nov" holds what that session ran and read itself that day; "Decided on 23 Nov" was written by the cloud session and is kept as it was; "Carried over" was not looked at again.

## In one paragraph
The repository is **Answers Over Time** and nothing else. The public site shows one group of answerers, "AI models": four local models asked by software. They answered the ten main questions on 19 November 2026 and three newer ones (C011 to C013, listed under a "Trending" tab) on 22 November; the owner read and approved both exports, and `published/` holds 52 answers. No ask is wanted now: all thirteen are asked together in December. The chat apps (ChatGPT, Gemini, Grok) and the Human column are configured but have no replies yet: the first hand-collected month is still the owner's to do. The model track (`archive-run`) stays in the code and is not published.

## Checked on 23 Nov 2026
On the owner's PC, in `~/live-genesis` on `main` at `292f9f7`, after a fresh backup of the real database. No model was called.

- **Tests:** `python -m pytest -q` gives 648 passed.
- **Real database, chat-app track** (`archive-status`, read-only): two runs of kind `asked`. Run 1, dated 2026-11-19, 40 answers (C001 to C010); run 2, dated 2026-11-22, 12 answers (C011 to C013). Four models each, no Human answers, no hand-entered run. Recorded questions: C001 to C013. The configuration reports no problem.
- **Nothing waiting** (`reconcile`, read-only): no open and no unconfirmed reservation. The local estimate stands at $0.003847, all of it from before the pivot; both asks were recorded at $0.
- **Real database, model track:** no run recorded.
- **`archive-chatbot-ask --list trending --dry-run`** (free, called nothing): C011 to C013 only, four models, **12 calls**, worst-case estimate $0.000000; it names the ten questions it leaves out and says nothing is recorded about them.
- **`archive-chatbot-ask --dry-run`** (free, called nothing): all thirteen, **52 calls**, worst-case estimate $0.000000. Both estimates come from token counts, not an invoice; both print `--max-usd 0.01` as the ceiling. Neither was run for real.
- **`published/`:** `snapshot --check published` says intact and approved; exported 2026-11-23T05:22:20Z; two runs, 52 answer rows, no Human rows.
- **Local build as the workflow makes it** (`LIVE_SITE_URL=... snapshot OUT --from-data published --archive-only`, `LIVE_CARD_IMAGES` unset), opened in a browser and read: the front page (ten main questions, "10 questions"), the Trending page (three questions, "New questions. Same game."), the C012 page ("Trending question · added Nov 2026", asked 22 Nov 2026, four first answers), the C001 page and "How it works" (each model counted from 13 answers, "2 times in all"). The top bar is "AI models · Trending · How it works". They match the live site. The one expected difference: without `LIVE_CARD_IMAGES` the share line is "Facebook · Open card" and there is no preview picture tag. Built once more with `LIVE_CARD_IMAGES=YES` on a scratch copy: "Facebook · Save card · Open picture", the sentence about saving, "Caption to copy", and the save name `answers-over-time-C001-2026-11.png`. No page carries a script.
- **Live site** (23 Nov at `292f9f7`, build green): all thirteen question pages, their cards and card pictures are served; "full answer on the site" is on the six four-model cards where a reply is really cut (C002, C003, C006, C007, C012, C013) and on no other four-model card; no footer line, no sample label.
- **Share cards as pictures:** 65 cards captured on this PC from the published answers; on every one the note matches what the browser shows.
- **The guide** (`docs/MONTHLY-ROUND.md`): its commands were run again on a scratch copy; its last paragraph says which.

## Not checked: the share line on a real phone
`docs/SHARE-CARD.md` still says "Not tested" for iPhone Safari and Android Chrome. That check needs the owner's own phone (tap "Save card" and "Open picture" on a question page, copy the caption, upload the saved picture by hand in Instagram and TikTok). It has not been done; the table was left as it is.

## Seen and not changed
- **"How it works" still talks about apps on a site that shows only models:** "each app gets the same questions", "Every app gets the exact same words", "When an app refuses", and a "Humans too" rule although there is no Human answer. The copy is in `archive_pages.py`; changing it is the owner's call. (Read again on 23 Nov.)
- **`snapshot --check` and `--export` print a wrong first NOTE** for model rows: it calls them "ENTERED BY HAND", and the next line corrects it. Wording only; the data is right. (Seen again on 23 Nov.)
- **`published/archive.json` says `"source": "asked"`** on each row, although the pages say nothing about how replies were collected. The owner approved the file as it is, twice.
- **`archive-chatbot-ask --dry-run` prints the configured names** ("Phi-4 mini (local)"); the pages show "Name (Maker)". That is by design (`archive_pages.shown_name`, display only). Do not rename the models in the configuration.
- **A filled-in sheet is not git-ignored:** `chatbots-2026-12.json` and reply text files show as untracked in the project folder. The guide says never to type `git add .`; an ignore rule would be safer.
- **With 13 answers each, Phi-4 mini's "just for fun" style moved** from "Class clown" to "Good-time friend". That is the count; nothing was set by hand.

## Decided on 23 Nov: trending questions and a Trending tab
The owner's words: "Wait for December. All thirteen are asked together then, and the three new ones simply start a month later, but ask the trending questions and add a top tab for trending questions."

Where that stands (cloud session, 23 Nov; checked against scratch databases and a build from `published/`, not against the real database):

- C011 "Does home-court advantage really exist?", C012 "Why do sports teams have mascots?" and C013 "What's the best comedy movie ever made?" were asked on 22 Nov with `--questions` and published by the owner (`published/` holds 13 questions on two dates). C001 to C010 are not asked again before December.
- The three are now marked `"list": "trending"` in `live_genesis/archive_chatbots.json`. The mark is a display attribute of that file only: no hash, table, payload or published file holds it, so the real database and `published/` are untouched and still pass every check.
- The public build reads the mark from the checked-in file: the top bar is "AI models · Trending · How it works", the front page lists the ten main questions, `archive/trending-now.html` lists the three. No new export is needed for that; it shows at the next push to `main`.
- To ask only the trending questions again (free preview first, then the run on the owner's word for that run):

```bash
python -m live_genesis.cli archive-chatbot-ask --list trending --dry-run
python -m live_genesis.cli archive-chatbot-ask --list trending --max-usd 0.01
```

- From December: one ask without `--list` or `--questions` covers all thirteen together (`docs/MONTHLY-ROUND.md`, "The models").
- A date that holds only some questions is handled by the pages: no gap, no "not asked" row, no false "Changed" or "From <month>" for the questions that were left out.

## Carried over, not checked on 23 Nov
- From this session's earlier work: Node, Playwright, its browser and an emoji font are installed under the owner's home folder in WSL (used again on 23 Nov). The launcher's two defects (a preview that created a database, a traceback when input ends) are fixed and tested offline. `--questions` and the card-fit estimate (line breaks after a hyphen and around a long dash, half a percent held back) are this session's, with offline tests; the estimate was compared with Chromium on 1,210 answers on 22 Nov.
- From `docs/COMPACT.md` (cloud session, 20 Nov): no real request to OpenRouter's model API has ever been made; the launcher was never run on Windows itself; a weekly cloud task (Mondays 18:35 UTC) reports draft question ideas and changes nothing; Codex's Windows review of the collector has not been seen.
- From earlier notes: a reviewed export of model-track answers was once published and withdrawn by the owner. The real database was made before the society was removed and still holds its tables; the code leaves them alone. Both model-track question lists are locked (20 core, 11 trending; last checked 21 Nov).
- Open code items from 14 and 15 Nov, not looked at again: the Content-Security-Policy still allows same-origin scripts and style attributes no page uses (`web.CONTENT_SECURITY_POLICY`); `archive.publish_problem` has no caller and is kept with its tests; `docs/SOCIAL-POSTS.md` and parts of `docs/PLAN-CHATBOT-COMPARISON.md` and `docs/FREE-MODELS.md` describe the state of their date.

## Where things run
- Code of record: `main` on GitHub. The cloud session and Codex also push to it: fetch and re-merge before every push.
- Public site: GitHub Pages, rebuilt by `.github/workflows/snapshot.yml` on every push to `main`, from `published/`. The workflow runs the tests first and makes a PNG of every share card; if either fails, nothing is published. That happened once, on 23 Nov: the push of the second export failed five tests that were pinned to figures in `published/`, and the site stayed as it was until the fix. After an export, run the full suite before pushing, and watch the build to the end. Address: `https://harshsrigh.github.io/live-genesis/`.
- Owner's PC (WSL, `~/live-genesis`): the real database, `.env`, the local models. Model-calling commands run only there, only on the owner's explicit word for that run.
- Windows has no Python. Tests run through WSL.
- Backups sit outside the repository, in the owner's home folder in WSL and in `~/live-genesis/backups/` (git-ignored). Latest: 23 Nov 2026 (UTC), before the checks above.

## Next (the owner's moves)
1. Try the share line on your own phone (`docs/SHARE-CARD.md`, the table at the end). In December: one ask of all thirteen questions together (`docs/MONTHLY-ROUND.md`, "The models"), on your word for that run.
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
