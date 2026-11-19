# OpenRouter: free-only collection on Windows

This is API model collection, not ChatGPT/Gemini/Grok website collection. It uses the existing
archive's locked model-track questions, reservations, append-only records and comparisons.
It does not import API replies into the manually collected chat-app track. The public chat-app
site currently excludes model-track data; publishing this track requires a separate deliberate change.

## Set up without calling a model

From the repository, in PowerShell:

```powershell
python -m live_genesis.openrouter_setup
```

This fetches public model metadata only, without an API key. Choose one or two exact IDs from
the printed list. They must end in `:free`, with all advertised prices zero. Avoid router aliases.

```powershell
python -m live_genesis.openrouter_setup --model 'vendor/exact-model:free' --out "$HOME/openrouter-free.ps1"
. "$HOME/openrouter-free.ps1"
```

Replace the example with an ID actually listed. The file contains settings only, no key, and
is never overwritten. Model calls remain disabled. Regenerate under a new filename to recheck
availability before later collections. Do not change model IDs silently between collections.

Create a free OpenRouter account and API key yourself at https://openrouter.ai/settings/keys.
Do not buy credits, enable auto top-up, or paste a key into chat. In PowerShell 7, enter it privately:

```powershell
$env:OPENROUTER_API_KEY = Read-Host 'OpenRouter key (hidden)' -MaskInput
$env:LIVE_DB = Join-Path $HOME 'live-genesis-openrouter.sqlite'
python -m live_genesis.cli archive-check-endpoints
```

The endpoint check makes no hosted request. Set LIVE_DB before initializing a new database;
never point this setup at a demo or existing real database by accident. Follow the
existing archive-lock/plan workflow in ANSWERS-OVER-TIME.md before collecting.

## Free-only protections and limits

- The generated endpoint has zero estimated input/output rates and `declared-free` provenance.
- Any endpoint whose host is OpenRouter (capitals, a trailing dot and subdomains included) must be declared
  free at zero rates and accepts only an explicit `vendor/model:free` id. A paid id or a router alias
  (`openrouter/auto`, `openrouter/free`) is refused when settings are read, again by `archive-plan` and
  `archive-run` before a run exists, and again just before the request is built. `LIVE_COMPAT_BASE_URL`
  may not point at OpenRouter.
- Requests include zero maximum prompt, completion, request and image prices, with provider
  fallback disabled. No web-search plugin is requested. The existing SDK retry count is zero.
- **A reported charge stops the collection.** A free call whose usage reports a nonzero or unreadable
  `cost` raises `engine.UnexpectedCharge`: nothing is stored as an answer, no further call is made to
  that model or any other, and the hold stays `unconfirmed`, so every later run is refused until you
  have looked at OpenRouter's billing page and run `python -m live_genesis.cli reconcile` as the
  message says. It is written to the operator audit log. A missing `cost` field is NOT proof of zero
  billing; only the dashboard confirms that. A call interrupted mid-request leaves a $0 hold `held`
  (it blocks nothing).
- Before each collection, `python -m live_genesis.openrouter_setup --check` (with your settings loaded)
  reads the public list again and says whether each configured model is still listed free. No key, no
  model call; exit code 1 if one is not. If it fails, stop and choose another free model in a new
  settings file; never switch to a paid one.
- No price metadata is a billing guarantee. Keep the account unfunded and use only the free IDs.
- The existing opt-in and explicit `--max-usd` gate still apply, including to free calls. This setup
  does not execute archive-run or enable opt-in. A later run needs the owner's instruction.
- At the checked limit of 50 free requests/day, the full core+trending lists can exceed one day's
  allowance across multiple models. Review archive-plan, select one question set and one model
  if needed, and leave room for failures. Never switch to a paid model when limited.
- Free models can disappear. A missing answer is a gap, not a changed answer. A provider may
  change the implementation behind a model ID; preserve the returned model and recorded settings.

## Open owner decisions (before any real run)

- Question set: the model track's core/trending lists differ from the ten manual chat-app questions.
  Do not change locked prompts or relabel API replies as manual.
- Samples: setup proposes 1; an older setup recorded a preference for 3. Confirm the count and
  show questions × models × samples before running.
- Publication: the public export excludes the model track. Results stay local until the owner
  authorizes a separate publishing change.

Sources checked 14 November 2026:
https://openrouter.ai/collections/free-models
https://openrouter.ai/docs/quickstart
https://openrouter.ai/docs/guides/routing/provider-selection

Offline verification: `python -m pytest -q tests/test_openrouter_setup.py tests/test_archive_endpoints.py`.

## Guided launcher (Windows)

Double-click `Collect-Free.cmd`, or from the repository in PowerShell:

```powershell
.venv\Scripts\python.exe -m live_genesis.operator_launcher --preview-only   # plan only: no key, no call, nothing recorded
.venv\Scripts\python.exe -m live_genesis.operator_launcher                  # the guided collection
```

Requirements: Windows, the repository's `.venv` (README), internet for the public model catalog only. It reuses
`openrouter_setup`, `archive.plan` and `archive.run_archive`; there is no new database, collector or route.

Steps: choose the database file (default `%USERPROFILE%\live-genesis-openrouter.sqlite`, always outside git; a path
inside the repository is refused) -> pick one to three listed `:free` models by number -> choose samples (typed on
purpose: the proposed 1 versus an older recorded preference of 3) and the question set (default `core`, the locked
model-track list, not the ten manual chat-app prompts) -> key via a hidden prompt (held in the process only; if
`OPENROUTER_API_KEY` is already set it is used and not shown) -> lock the list if it is not locked yet (typed `yes`)
-> the plan with `questions x models x samples = calls`, zero estimated cost, a warning above 50 calls -> you type
the exact phrase it prints (`RUN <calls> CALLS`); anything else calls nothing -> results.

Failure recovery: a rate-limited or failing model is skipped for the rest of the run and shows as a gap; run the
launcher again later to ask again (a missing answer is a gap, never a change). A reported charge is different: the collection stops
at once and later runs are refused until you have checked the OpenRouter dashboard and reconciled. Close the window to stop; recorded answers stay. Nothing is retried. If the key
was typed wrongly, run again; a rejected call is a gap. Local data: the database file named at the start; back it up
with `python -m live_genesis.cli backup NEW-FILE` (set `LIVE_DB` first). Read the saved answers locally with the
`uvicorn` line the launcher prints; the public site never carries them.
