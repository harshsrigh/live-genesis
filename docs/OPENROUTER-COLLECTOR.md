# OpenRouter: free-only collection (Windows first, WSL notes at the end)

**What this is.** A way to ask the archive's fixed questions of models through OpenRouter's API, free models only, and keep every answer over time.

**What this is not.** It is not the chat-app comparison. These are API replies from open models reached through OpenRouter. They are never replies collected from the ChatGPT, Gemini or Grok websites and must never be labelled as such.

| | Chat-app track (public site) | This collector (model track) |
|---|---|---|
| Who answers | Consumer chat apps, asked by hand | Models reached through OpenRouter's API |
| Questions | The ten chat-app questions (`C001`...) | The locked core list (20) and trending list (11) |
| Stored in | `archive_chatbot_*` tables | `archive_answers` |
| On the public site | Yes | **No.** The public site leaves the model track out |

So today the answers you collect here are visible on your own machine only. Asking the ten chat-app questions through OpenRouter, or publishing these answers, would each be a separate change that needs the owner's decision first (see "Not built" below).

State of verification: the workflow below is tested end to end with mocked answers and a throwaway database. **No real request to OpenRouter's model API has been made by this project.** Treat the first real collection as a test.

## The steps

All commands are run in PowerShell from the repository folder. Nothing before step 6 contacts a model.

### 1. Discover the free models (no key, no account)

```powershell
python -m live_genesis.openrouter_setup
```

This reads OpenRouter's public model list and prints the ids that end in `:free` and advertise a price of zero on every dimension. Router aliases such as `openrouter/free` and `openrouter/auto` are left out on purpose: they pick the model themselves.

### 2. Select exact ids and write the settings file

Pick one model to start (see the daily limit in step 5). Use an id from the list, not the example.

```powershell
python -m live_genesis.openrouter_setup --model 'vendor/exact-model:free' --out "$HOME\openrouter-free.ps1"
```

Repeat `--model` for up to three. The file holds settings only, no key. It is never overwritten: to change models, write a new file under a new name. It sets `LIVE_ALLOW_PAID_CALLS` to `NO`, so nothing can be sent yet.

Keep the same model ids from one collection to the next. A different model is a different column, not a continuation.

### 3. Give the key privately

Create a free OpenRouter account and an API key yourself at <https://openrouter.ai/settings/keys>. Do not buy credits and do not switch on auto top-up. When creating the key, set its **credit limit to 0** if the form offers it: that is a limit on OpenRouter's side, which this software cannot give you.

Never paste the key into a chat, a file in the repository or a command line. Enter it into the current window only:

```powershell
# PowerShell 7
$env:OPENROUTER_API_KEY = Read-Host 'OpenRouter key (hidden)' -MaskInput
```

```powershell
# Windows PowerShell 5.1
$secure = Read-Host 'OpenRouter key (hidden)' -AsSecureString
$env:OPENROUTER_API_KEY = [System.Net.NetworkCredential]::new('', $secure).Password
```

The key lives in that window until you close it.

### 4. Load the settings and point at a database outside the repository

```powershell
. "$HOME\openrouter-free.ps1"
$env:LIVE_DB = Join-Path $HOME 'live-genesis-openrouter.sqlite'
python -m live_genesis.cli archive-check-endpoints
python -m live_genesis.cli archive-lock
python -m live_genesis.cli archive-lock --set trending
```

Set `LIVE_DB` every time, before any other command: without it the commands use `live.db` in the repository folder. `archive-check-endpoints` sends nothing to OpenRouter; it shows the host, whether the key variable is set (yes or no, never the key) and the pricing basis. The two lock commands are needed once per database and create it if it does not exist. No `init` is needed and no agent key is printed.

If you already have a real database with model runs, do not mix: this collector gets its own file.

### 5. Preview the questions and the number of requests

```powershell
python -m live_genesis.openrouter_setup --check
python -m live_genesis.cli archive-plan
```

`--check` reads the public list again and says whether each configured model is still listed free. Run it before every collection: a free model can become paid or disappear. If it fails, stop; choose another free model in a new settings file and never switch to a paid one.

`archive-plan` prints every question, the models, the number of calls and the estimate (`$0.000000`). Nothing is sent.

**Daily limit.** OpenRouter's documentation (read 14 November 2026) gives accounts that have bought fewer than 10 credits 50 free-model requests a day and 20 a minute. Both lists together are 31 questions, so:

| Plan | Requests | Fits in one day |
|---|---|---|
| 1 model, both lists | 31 | Yes |
| 2 models, core list only (`--set core`) | 40 | Yes |
| 2 models, both lists | 62 | No: use `--models` to ask one model per day |

A failed request probably counts too, so leave room. The settings pause 4 seconds between calls.

### 6. Collect (only on the owner's explicit word for this run)

This is the only step that contacts a model. It is run by the owner, for a run the owner has looked at in step 5. A free run needs the same two switches as a paid one.

```powershell
$env:LIVE_ALLOW_PAID_CALLS = 'YES'
python -m live_genesis.cli archive-run --max-usd 0.01
$env:LIVE_ALLOW_PAID_CALLS = 'NO'
```

`LIVE_ALLOW_PAID_CALLS` means "allow model calls"; the name is historical. Useful variants: `--set core`, `--models or-free-1`.

### 7. Inspect what was saved

```powershell
python -m live_genesis.cli archive-status
python -m uvicorn live_genesis.app:app --host 127.0.0.1 --port 8000
```

`archive-status` lists the runs and each model's outcome in the last one. The second command serves the pages on your own machine only: open <http://127.0.0.1:8000/archive/models.html>. Each question page shows every model's answer per collection and marks where the wording differs from the previous one. Stop the server with Ctrl+C.

A model that was skipped shows "no answer recorded" for that collection. That is a gap, not a changed answer.

### 8. Next month

Open a new window, repeat steps 3 to 7 with the same settings file and the same `LIVE_DB`. Back up the database file now and then (`python -m live_genesis.cli backup "$HOME\live-genesis-openrouter-backup.sqlite"`).

## What keeps it free, and what does not

Checked on the request path, with tests (`tests/test_openrouter_setup.py`):

- **Free ids only.** An endpoint whose host is OpenRouter accepts only `vendor/model:free`. A paid id, a router alias or an id with a second suffix is refused when the settings are read, again by `archive-plan` and `archive-run`, and again immediately before the request is built. Capital letters, a trailing dot and subdomains do not get around the host rule.
- **No priced OpenRouter endpoint.** Giving an OpenRouter endpoint rates, or leaving out `_FREE_TIER=YES`, is refused. The single `openai-compatible` provider used by `round` and `study-run` refuses an OpenRouter address altogether.
- **Request limits.** Every request carries OpenRouter's provider routing options with a maximum price of zero for prompt, completion, request and image, and fallbacks off. No list of fallback models and no plugin is sent. OpenRouter documents that a request is not run when no provider meets the maximum price.
- **No redirect, no retry.** The client follows no redirect and retries nothing, so one planned call is at most one request, to the configured host.
- **A reported charge stops the run.** OpenRouter reports the amount charged in each response. If that is above zero, or not a number, the answer is not recorded, the run stops at once, no other model is asked, and further runs are refused until `cli reconcile` after you have looked at OpenRouter's billing page.
- **Gaps.** A rate limit, an error or an answer without token counts skips that model for the rest of the run. Nothing is retried and no other model is substituted.

What none of this can guarantee:

- These are requests the software makes, not a guarantee from OpenRouter. The real safeguards are on the account: no credits bought, no auto top-up, and a key credit limit of 0.
- If a response carries no cost figure at all, the software cannot tell and records the answer. Look at OpenRouter's activity page after the first collection.
- A provider can change the model behind an id without notice. The model name the service reports is stored with every answer.
- Free models may log or train on prompts, and OpenRouter may require a privacy setting to be switched on before free models answer. The questions here are public, so nothing private is sent. Read OpenRouter's current terms and privacy settings yourself before the first collection.

## Not built: decisions for the owner

1. **Asking the ten chat-app questions through OpenRouter.** Proposed form, if wanted: a third locked question set in the model archive (for example `chat`), holding the same ten texts and the same prefix, asked only when named with `--set chat`. The answers would stay in the model track, labelled "open model via OpenRouter", in their own columns. They would never enter the chat-app tables and never appear under a chat app's name.
2. **Showing these answers on the public site.** Today the public build leaves the model track out. Proposed form, if wanted: a separate page ("Open models, asked through an API") reached from the menu, with the reviewed export step as for any published data. The front page would stay the hand-collected comparison.

Neither is started. Each changes what the public sees or what is locked for good.

## WSL, Linux, macOS

The same steps with a POSIX settings file:

```bash
python -m live_genesis.openrouter_setup --model 'vendor/exact-model:free' --out "$HOME/openrouter-free.sh" --shell bash
```

```bash
read -rs -p 'OpenRouter key (hidden): ' OPENROUTER_API_KEY && export OPENROUTER_API_KEY
```

```bash
. "$HOME/openrouter-free.sh" && export LIVE_DB="$HOME/live-genesis-openrouter.sqlite"
```

## Sources read 14 November 2026

- <https://openrouter.ai/docs/guides/routing/provider-selection> (maximum price, disabling fallbacks)
- <https://openrouter.ai/docs/guides/routing/model-variants/free> (the `:free` variant; fallback lists are billed)
- <https://openrouter.ai/docs/api/reference/limits> (free-model request limits, key credit limit)
- <https://openrouter.ai/docs/cookbook/administration/usage-accounting> (cost reported in every response)

Offline tests: `python -m pytest -q tests/test_openrouter_setup.py tests/test_archive_endpoints.py`.
