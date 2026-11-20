# Your monthly round, start to finish

This is the guide for the person who does the round: you. It takes about an hour, most of it copy and paste. You cannot break anything by following it. Nothing is stored until step 9, and nothing is public until step 16.

Nothing here calls a model, costs money or needs a key. No program ever opens a chat app or Reddit for you: you do the asking by hand, every month. (The public pages do not say how the replies were collected. This guide is for you, so it does.)

**Where to type.** Every command below goes into the WSL window, in the project folder, because that is where Python lives on your PC.

1. Open **Windows PowerShell**.
2. Start WSL:
   ```
   wsl
   ```
3. Go to the project and switch Python on:
   ```bash
   cd ~/live-genesis
   . .venv/bin/activate
   ```
   You should see `(.venv)` at the start of the line.

*If you ever run Python straight from PowerShell instead of WSL:* the commands are the same, with two differences. Switch Python on with `.venv\Scripts\activate`, and where this guide writes `export LIVE_DB=...`, PowerShell wants `$env:LIVE_DB = "..."`. These two PowerShell forms were not run when this guide was checked; everything else was, on Linux, which is what WSL is.

**Which database.** Your real database is the file `LIVE_DB` points at (in your `.env`, or set by hand). With neither, it is `live.db` in the project folder. To be sure, ask:

```bash
python -m live_genesis.cli archive-status
```

You should see a block of text that starts with `{`. Near the end, under `"chatbots"`, the part called `"runs"` lists your earlier rounds with their dates. If they are there (or it is empty and this is your first round), you are in the right place.

---

## Part 1: before you ask

### Step 1. Make a safety copy

```bash
python -m live_genesis.cli backup ../backup-before-2026-12.db
```

You should see `Backup saved:` and the file's name. Use a new name every month; the command will not overwrite an old copy. The copy sits one folder above the project, so it can never end up on GitHub.

### Step 2. Read the apps' terms

Open the terms pages of ChatGPT, Gemini and Grok and read the parts about using the app and about publishing what it wrote. They change. If one of them now says you may not publish its replies, leave that app out this month (step 6 says how) and tell whoever helps you with the code.

### Step 3. Make this month's sheet

```bash
python -m live_genesis.cli archive-chatbot-template --out chatbots-2026-12.json
```

You should see `Template written to chatbots-2026-12.json. This is your sheet for the month.`

The sheet carries today's date. If you will ask on another day, give that day:

```bash
python -m live_genesis.cli archive-chatbot-template --date 2026-12-03 --out chatbots-2026-12.json
```

The command never overwrites a sheet. If it says the file already exists, that sheet may hold replies you already pasted: pick another name.

Open the sheet in a text editor (Notepad is fine; VS Code shows mistakes in red, which helps). The notes at the top say the same as this guide, in short.

---

## Part 2: ask

### Step 4. Set up each app the same way, every month

| App | Use | Also |
|---|---|---|
| ChatGPT | a logged-in free account, **Temporary Chat** | memory off |
| Gemini | a logged-in free account, **Temporary chat** | |
| Grok | a logged-in free account, **Private Chat** | memory off |

Same country, same kind of app (web, phone or desktop) as last month. The point is that a new answer should come from the app, not from you asking differently.

Then fill in that app's `conditions` in the sheet, before you ask it anything:

| Line | Write |
|---|---|
| `account` | `"free_account"` (or `"logged_out"`, `"paid_account"`) |
| `fresh_chat` | `true` |
| `temporary_chat` | `true` or `false` |
| `memory_off` | `true`, `false` or `"not_applicable"` |
| `web_search` | `"on"`, `"off"` or `"unknown"` |
| `country` | two capital letters, like `"US"` |
| `app` | `"web"`, `"mobile"` or `"desktop"` |
| `shown_model` | the model name the app shows, or `"not shown"` |
| `note` | anything odd, short; or leave it `""` |

`true` and `false` go without quotes. Everything else goes in quotes.

### Step 5. Ask, one question at a time

For each question in the sheet:

1. Start a **new chat**. Every question gets its own.
2. Copy the `prompt` from the sheet and paste it **exactly**. Both lines of it.
3. Wait for the reply.
4. Copy the **whole** reply and paste it between the quotes of `answer`.

Do not fix, trim or tidy a reply, even a long or a silly one. The site folds long replies by itself; the words stay as the app wrote them.

If the reply showed a "Sources" or citations marker, search was on: set `web_search` to `"on"` for that app.

Optional: keep a screenshot of each reply for yourself.

### Step 6. When it does not go to plan

| What happened | What to do in the sheet |
|---|---|
| The app refused, or dodged the question | Add `"refused": true` to that answer. You may paste what it said into `answer`, or leave it empty. Its words stay in your own database and are never published. |
| You could not ask at all (an error, the app was down) | Leave `answer` empty and add `"skipped": "a short reason"`. |
| The reply went off-topic but is an answer | Paste it as it is. An odd answer is still an answer. |
| The reply has line breaks or quotation marks | Save it as a plain text file next to the sheet, for example `gemini-C003.txt`, leave `answer` empty and add `"answer_file": "gemini-C003.txt"`. |
| You did not ask one app this month | Delete that app's whole block from the sheet. |

Never delete a single question, and never change a `prompt`.

A refused answer looks like this:

```json
{"question": "C002", "prompt": "(leave as it is)", "answer": "", "refused": true}
```

### Step 7. The Human column (optional)

The working rule, until you change it:

- **You post the question yourself**, and you say in the post that replies may be quoted or summed up on the site.
- **No usernames, handles or links are ever copied.** Not into the sheet, not anywhere.
- **Prefer a summary** of the thread over quoting one person. Quote only short, harmless lines.
- **Follow each community's rules** on surveys and self-promotion. If they say no, ask somewhere else.
- **Nothing is collected by software.** You read the replies and write the line yourself.

In the sheet's `human` block: `source` is where, in plain words (`"Reddit thread"`; no link, no name), `count` is how many replies you read (or `null`). For each question write one short `answer`, and `kind`: `"summarised"` for your own summary of several replies, `"quoted"` for one person's exact words. The site shows these as "summed up" and "quoted".

No answers from people this month? Delete the whole `human` block.

---

## Part 3: store it

### Step 8. Check the sheet (stores nothing)

```bash
python -m live_genesis.cli archive-chatbot-import chatbots-2026-12.json --dry-run
```

A long list scrolls past. Look at the last lines. You want:

```
DRY RUN: the file passed every check. Nothing was stored.
  chatgpt: 10 of 10 answers will show, 0 refused, 0 skipped
  gemini: 10 of 10 answers will show, 0 refused, 0 skipped
  grok: 8 of 10 answers will show, 1 refused, 1 skipped
  human: 10 of 10 answers will show
```

Do the numbers match what happened? Then go on.

If instead it says the file `is not ready yet`, that is this check doing its job. It lists what to fix: the app, the question, and what to do. Fix the sheet, save it, run the same command again. As often as you like.

### Step 9. Store it

The same command without `--dry-run`:

```bash
python -m live_genesis.cli archive-chatbot-import chatbots-2026-12.json
```

You should see `Done. Stored as hand-entered run` and a number. The replies are now in your database and nowhere else. This step cannot be undone or repeated: a stored reply is never replaced.

The very first time anything is stored, it also tells you the question list is now fixed. From then on you can add questions, but not change one. (On your real database that already happened on 19 November 2026, when the four local models were asked: the ten questions are fixed, and this message will not appear again.)

### Step 10. Look at it on your own PC

```bash
python -m uvicorn live_genesis.app:app --host 127.0.0.1 --port 8000
```

You should see `Uvicorn running on http://127.0.0.1:8000`. Open that address in your browser and read the front page, a question page and "How it works". Only you can see this. If it says the address is already in use, the pages are already running in another window: use that one, or put `8001` in place of `8000`.

Press **Ctrl+C** in the WSL window to stop it.

---

## Part 4: publish

Only you do this part. Once it is pushed it stays in the project's history for good, even if you take it down later.

### Step 11. Export

```bash
python -m live_genesis.snapshot --export published
```

You should see `Export written to published (NOT yet approved)` and a count of answers. If a `published` folder is already there from last month, add `--replace`:

```bash
python -m live_genesis.snapshot --export published --replace
```

### Step 12. Read every answer

Open `published/archive.json` and read every reply in it. This is the one step no program can do for you: the automatic checks catch patterns (an email address, a link), not meaning.

If models were asked the same questions by software (the "AI models" group), their replies are in this file too, under each model's own name. Read those as well; approving covers the whole file.

You are looking for anything you would not want under an app's name or yours: something hateful, something about a real private person, something that reads as advice. Refused replies are not in this file; only the fact that there was one.

Found something? Do not approve. See "If something goes wrong".

### Step 13. Approve

```bash
python -m live_genesis.snapshot --approve published --i-have-read-it
```

You should see `Approved at` and the time.

### Step 14. Check

```bash
python -m live_genesis.snapshot --check published
```

You should see `OK: export is intact and approved.`

### Step 15. See exactly what the world will see

```bash
python -m live_genesis.snapshot ../site-preview --from-data published
```

You should see `Static snapshot written to ../site-preview (rendered from approved data ...)`. That folder is the public site, built from the file you just read.

### Step 16. Push

First get the latest code, because others push to the project too:

```bash
git pull --ff-only
```

You should see `Already up to date.` or a list of updated files. If it changed anything, run step 14 again before going on.

```bash
git add published
```

```bash
git commit -m "Chat-app round, December 2026"
```

```bash
git push
```

Only the `published` folder goes up. Never add a database, your sheet or `.env`, and never type `git add .`: your sheet and any reply text files sit in this folder too. If `git push` is refused because someone pushed in the meantime, run `git pull --no-rebase`, then `git push` again.

### Step 17. Check the live pages

Wait a few minutes, then open <https://harshsrigh.github.io/live-genesis/>. Check the front page, one question page and "How it works". The month should be the new one, and the sample label should be gone from the main pages (the samples live on under "Sample site").

### Step 18. Make the share cards

```bash
node scripts/capture-cards.mjs --site ../site-preview --out ../cards --set chat
```

This step needs Node and Playwright, which are not installed in WSL on this PC yet; without them it says `node: command not found` and nothing is harmed.

You should see one line per card and, at the end, `card(s) written to ../cards at 1080 x 1920.` Posting them is by hand too: `docs/SOCIAL-POSTS.md`.

---

## If something goes wrong

**"is not ready yet, and nothing was stored"** (step 8). Read the lines under it. Each one names the app, the question and the fix. Nothing was stored, so nothing is lost.

**"could not be read ... not valid JSON".** The sheet has a typing slip. The usual cause is a reply with line breaks or quotation marks pasted straight in: move that reply to a text file and use `answer_file` (step 6). A missing comma or quote mark does it too. VS Code underlines the spot in red.

**"this block is still blank".** You have not filled in that app yet, or you meant to leave it out: delete its whole block.

**"more than 14 days ago".** The date at the top of the sheet (`collected_on`) is old. If that really is the day you asked, run the same command with `--allow-old` at the end:

```bash
python -m live_genesis.cli archive-chatbot-import chatbots-2026-12.json --allow-old
```

**"already has a hand-entered run dated ...".** That day is already stored for that app. If you imported this sheet before, you are done. If this is a new month, correct `collected_on` at the top of the sheet.

**"This database holds scripted demo answers".** You are pointed at the sample database, not your real one. Point `LIVE_DB` at the real file and run the command again:

```bash
export LIVE_DB=~/live-genesis/live.db
```

**You stored a round and then spotted a mistake in it.** A stored reply cannot be edited or removed; that is what makes the record honest. Do not publish yet. Put your safety copy from step 1 back in place of the database (copy the backup file over it), fix the sheet, and import again. If you already published, ask for help before doing anything.

**You found a reply you do not want to publish** (step 12). Do not approve. Delete the `published` folder if it is new this month, and ask for help: the choices are to leave that app out this month or to mark the reply as refused in a corrected round, as above.

**Anything else.** Stop, change nothing, and copy the message you got to whoever helps you with the code. Nothing in this guide gets worse by waiting.

---

*Checked on 19 November 2026 against a scratch database with placeholder replies: `backup`, `archive-status`, `archive-chatbot-template` (`--out`, `--date`), `archive-chatbot-import` (`--dry-run`, `--allow-old`, plain), the local pages, `snapshot` (`--export`, `--replace`, `--approve --i-have-read-it`, `--check`, `--from-data`) and `capture-cards.mjs` (`--site`, `--out`, `--set chat`). Not run: the `git` commands of step 16 and the two PowerShell forms named at the top.*

*Checked again on 19 November 2026 in WSL on the owner's PC, in a throwaway copy of the project with a scratch database and placeholder replies: steps 1, 3, 8 to 15 printed what this guide says. Step 16: `git add published` staged exactly the two files, `git commit` worked, `git push` was run as a dry run only. Not run: step 17, step 18 (no Node here), a real push, and the two PowerShell forms.*
