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
- Named OpenRouter endpoints declared free reject anything except an explicit `:free` model ID.
- Requests include zero maximum prompt, completion, request and image prices, with provider
  fallback disabled. No web-search plugin is requested. The existing SDK retry count is zero.
- A free call whose usage reports a nonzero or unreadable `cost` raises `UsageMissing`, which the archive
  treats as a failed call (`no_usage`) on a zero-priced endpoint: nothing is stored as an answer, the
  $0 hold is released, that model is skipped for the rest of the run, and the run ends
  `complete_with_gaps`. The reported charge itself is not recorded anywhere, so check the provider's
  dashboard after such a run and stop if anything was billed. A missing `cost` field is NOT proof of
  zero billing; only the dashboard confirms that. A call interrupted mid-request leaves a $0 hold
  `held` (it blocks nothing).
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
