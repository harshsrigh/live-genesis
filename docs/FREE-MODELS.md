# Local and free models

Two parts:

- **Part 1**: `round` and `study-run` on a local or free model through the single `openai-compatible` provider.
- **Part 2**: a dated guide to free and local models for Answers Over Time (`archive-run`), with the settings for each. A record of research done on 12 November 2026.

**Not tested against any real server.** The adapters are covered only by offline tests with a replaced SDK client. Nothing in this repository has contacted Ollama, a hosted free tier or any other endpoint. Expect to adjust settings on the first try.

**A free or local model does not lift the rule:** `round`, `study-run` and `archive-run` run only on the operator's explicit instruction, and `LIVE_ALLOW_PAID_CALLS=YES` is required for every provider (the name is historical: read it as "allow model calls"). `archive-run` always needs `--max-usd`.

---

# Part 1: `round` and `study-run` on one local or free endpoint

`LIVE_PROVIDER=openai-compatible` sends each agent turn to any server that speaks the OpenAI
**Chat Completions** API at a base URL you choose: a local Ollama, LM Studio or llama.cpp
server, or a hosted endpoint with a free tier.

This part is about `round` and `study-run`, which use ONE endpoint and one pair of rates.
Answers Over Time (`archive-run`) can ask several services in one run through named endpoints
(`LIVE_ARCHIVE_ENDPOINTS`), each with its own base URL, key variable and rates, and there a
hosted free tier may be declared free (`_FREE_TIER=YES`, zero rates). See Part 2 and "Several
models from several services" in `docs/ANSWERS-OVER-TIME.md`. Nothing below about positive rates
or about a failed call stopping everything was changed for `round` and `study-run`.

What stays the same as for the paid providers: every call is held against the estimated ceiling
first; a failed call stops the round until you reconcile it; actions are labelled `engine` with
the provider `openai-compatible` and the model id you configured. The base URL is never stored
in the database or shown on the page.

A model that does not return exactly one valid JSON object simply produces an error outcome
for that agent in that round. Its token usage is still recorded and the other agents continue.
Small models will do this often. A server that returns no token usage is treated as a failed
call: the hold becomes `unconfirmed` and the round stops; no numbers are invented.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `LIVE_PROVIDER` | `openai` | `openai-compatible` selects this adapter |
| `LIVE_MODEL` | none for this provider | exact model id the server expects (set it **before** `cli init`, or run `cli set-model`) |
| `LIVE_COMPAT_BASE_URL` | required | e.g. `http://localhost:11434/v1`. Must be `https` unless the host is loopback (`localhost`, `127.x.x.x`, `::1`). No user:password, query or fragment |
| `OPENAI_COMPATIBLE_API_KEY` | empty | a secret: loaded only by `round`, `study-run`, `archive-run` and `check-provider`, removed from web processes. Not needed for a loopback server; required for any other host |
| `LIVE_COMPAT_JSON_MODE` | `on` | sends `response_format={"type":"json_object"}`. Set `off` if the server rejects it |
| `LIVE_COMPAT_REASONING_EFFORT` | unset | `low`, `medium` or `high` is sent as `reasoning_effort`; unset sends nothing |
| `LIVE_INFER_TIMEOUT_SECONDS` | `35` | per call, all providers; clamped to 5-600 |
| `LIVE_MAX_OUTPUT_TOKENS` | `1200` | reply limit per turn, all providers; clamped to 200-4000; a server that ignores `max_tokens` is not bounded by it |
| `LIVE_INPUT_USD_PER_M`, `LIVE_OUTPUT_USD_PER_M` | none | may be `0` **only** for a loopback base URL. Any other host needs positive figures |

## (a) Local model with Ollama (Windows / WSL)

1. Install Ollama: the Windows installer from ollama.com, or inside WSL
   `curl -fsSL https://ollama.com/install.sh | sh`.
2. Pull a model: `ollama pull <model>`. Starting points by GPU memory, to be adjusted by trial:
   4 GB: a ~4B model; 8 GB: ~8B; 16 GB: ~14-20B; 24 GB: ~27-30B. Without a GPU it runs on the
   CPU, slowly.
3. **Raise the context window.** Ollama's default is 4,096 tokens and a longer prompt is
   silently truncated: the agent would lose its instructions or its context without any error.
   A turn here sends up to 24,000 bytes plus a reply of up to `LIVE_MAX_OUTPUT_TOKENS` (default 1,200), so start the server with
   `OLLAMA_CONTEXT_LENGTH=16384` (or more).
4. `.env`:
   ```
   LIVE_PROVIDER=openai-compatible
   LIVE_COMPAT_BASE_URL=http://localhost:11434/v1
   LIVE_MODEL=<the exact name you pulled>
   LIVE_INPUT_USD_PER_M=0
   LIVE_OUTPUT_USD_PER_M=0
   LIVE_INFER_TIMEOUT_SECONDS=300
   LIVE_ALLOW_PAID_CALLS=YES
   ```
   On a CPU a turn can take minutes: raise `LIVE_INFER_TIMEOUT_SECONDS` (maximum 600). A
   timeout counts as a failed call and stops the round; for a local model reconcile it with
   `cli reconcile --receipt <id> --actual-usd 0`.
5. **Reasoning models** can spend the whole reply limit (`LIVE_MAX_OUTPUT_TOKENS`, default 1,200)
   thinking and return no JSON; `cli turns` then shows the turn as `truncated`. Try
   `LIVE_COMPAT_REASONING_EFFORT=low`, raise `LIVE_MAX_OUTPUT_TOKENS` (up to 4000), or use a
   non-reasoning model. The same applies to the `openai` and `anthropic` providers, whose current
   models think by default: the adapters send no thinking setting for them.
6. **Reachability.** From inside WSL or a Docker container, `localhost` may not be the Windows
   host where Ollama runs. Run the round in the same place as the server, or make the server
   reachable there. An address such as `host.docker.internal` or a LAN IP is **not** loopback:
   plain `http` to it is refused and zero rates are not accepted.

A local model costs no provider fee, so it is recorded as $0 estimated. Electricity and
hardware are not counted anywhere.

## (b) A hosted free tier for `round` / `study-run`

Which services have a free tier, and what their terms said on 12 November 2026, is in Part 2.
Two things differ for resident rounds:

- Agent context (posts, proposals, documents, private notes) is sent with every call. A service
  that uses free-tier content to improve its products, or has people review it, receives all of
  that. The archive sends only a fixed public question.
- Positive rates are required for every non-loopback host, so the estimated ceiling keeps
  working if a free tier starts charging. `_FREE_TIER=YES` exists for `archive-run` only.

Pattern:
```
LIVE_PROVIDER=openai-compatible
LIVE_COMPAT_BASE_URL=https://<host from the service's documentation>/v1
OPENAI_COMPATIBLE_API_KEY=<your key>
LIVE_MODEL=<exact model id from the service>
LIVE_INPUT_USD_PER_M=<a conservative positive figure, e.g. the service's paid price>
LIVE_OUTPUT_USD_PER_M=<same>
```
The page then shows an estimated cost even if you paid nothing; `cli reconcile --provider-usd 0`
records what the bill really says. If the service rejects `response_format`, set
`LIVE_COMPAT_JSON_MODE=off`. Rate limiting by the service shows up as a failed call and stops
the round (nothing is retried). Limits are usually per account or per project, not per key;
extra keys do not add quota.

## (c) Commands

```bash
python -m live_genesis.cli check-provider      # free: prints the settings; makes no model call
python -m live_genesis.cli init                # once, on a database separate from the demo (LIVE_DB)
python -m live_genesis.cli round --agents 1    # the first real call: one agent, one request. Owner's word only
python -m live_genesis.cli status
```
`check-provider` shows the provider, model, base URL host (never the path), timeout, whether
a key is set (yes/no), whether the rates are zero or positive and whether zero is allowed, and
for a loopback host whether a TCP connection opens. It never contacts a non-loopback host and
cannot tell whether the server knows the model or returns token usage; only the first round
shows that. `python -m live_genesis.cli archive-check-endpoints` does the same for the archive's
named endpoints. Then follow `docs/FIRST-REAL-ROUND.md` for review and publishing.

---

# Part 2: free and local models for Answers Over Time

**Record of research done on 12 November 2026.** Read this before relying on any line of it:

- The findings come from the services' own pages as read on that date, except where a line says **UNVERIFIED**.
- **Free tiers change.** They are reduced, withdrawn or start billing without notice. Model lists change faster still.
- **Re-read each service's current terms, limits and data policy yourself before the first call**, and again before each later run if months have passed. Look for: publishing a model's outputs, benchmarking or comparing models, use of prompts and outputs for training, where the service may be used, request limits.
- This is not legal advice. Whether a use fits a service's terms is the operator's reading and the operator's risk.
- Nothing here was tried: no account was opened, no key was made, no request was sent to any of these services, and no model was pulled.
- No service is recommended. Hosts below are placeholders (`.example`): copy the real base URL from the service's own documentation.

## Recommended start: local models through Ollama

Why local first: no account, no terms on outputs beyond each model's licence, no rate limit, and the model file can be kept unchanged for years.

- One OpenAI-compatible endpoint (`http://localhost:11434/v1`) serves every model you have pulled. As found, it accepts `seed`, `temperature` and `reasoning_effort`, which are what the archive sends.
- **Ollama library tags are mutable.** The same tag can point at different weights later. As found, a model cannot be pulled by digest and an older version behind a tag cannot be recovered.
  - Pull each model **once**. Never re-pull a model the archive already uses.
  - Record the digest. The archive does this itself: it stores the local model's sha256 digest with every answer, and the pages say "new model file" if it changes.
  - Back up Ollama's models directory, or keep the GGUF file with its sha256, somewhere off the machine. If the file is lost, that column of the archive ends.
- Same machine, same runner version and same digest give **mostly, not exactly**, identical answers. Pinned sampling reduces noise; it does not make a model deterministic. An Ollama upgrade can change answers with the model file unchanged; note the Ollama version in your run notes (the archive does not record it).
- **A local small quantized model is not the model behind its maker's chat app.** The page labels it "run locally" and nothing more; say the same anywhere the answers are shown.

### Starter set (tags UNVERIFIED)

Fits a CPU-only machine with 16 GB of RAM; four makers; about 11 GB of disk in total. **The tags below are unverified: confirm each with `ollama show <tag>` (and the library page) before pulling**, and read the licence shown there.

| Tag (UNVERIFIED) | Maker | Licence as found | Note |
|---|---|---|---|
| `phi4-mini` | Microsoft | MIT | |
| `gemma3:4b` | Google | Gemma terms | Read the Gemma terms before publishing its answers |
| `granite4.2:3b` | IBM | Apache 2.0 | |
| `qwen3.5:4b` | Alibaba | Apache 2.0 | Send reasoning effort `none` |

Other findings:

- **Llama 3.2**: its licence asks products that use it to display "Built with Llama". If a Llama model is added, that line has to be shown where its answers are; the pages do not do this today.
- **Small `deepseek-r1` tags** are distillations onto other makers' base models, and they always "think". Label them as distillations or leave them out; a column titled as if it were the maker's own model would mislead.
- Any model that thinks before answering spends output tokens on thinking. If many cells read "reply cut off at the output limit", raise that endpoint's `_MAX_OUTPUT_TOKENS`.

### Settings for the starter set

`_REASONING_EFFORT` is set per endpoint, not per model. To send `none` only to the model that needs it, give the same local server two names:

```
LIVE_ARCHIVE_ENDPOINTS=local,local-nothink

LIVE_ARCHIVE_ENDPOINT_LOCAL_BASE_URL=http://localhost:11434/v1

LIVE_ARCHIVE_ENDPOINT_LOCAL_NOTHINK_BASE_URL=http://localhost:11434/v1
LIVE_ARCHIVE_ENDPOINT_LOCAL_NOTHINK_REASONING_EFFORT=none

LIVE_ARCHIVE_MODELS=phi4-mini=local:phi4-mini,gemma3-4b=local:gemma3:4b,granite-3b=local:granite4.2:3b,qwen-4b=local-nothink:qwen3.5:4b
LIVE_INFER_TIMEOUT_SECONDS=300
LIVE_ALLOW_PAID_CALLS=YES
```

- A loopback endpoint needs no key and defaults to zero rates; its answers get the pricing basis `local`.
- The labels on the left of each `=` are what the pages show. Choose them once: a new label starts a new column.
- The model id is everything after the first colon, so `local:gemma3:4b` asks for `gemma3:4b`.
- Both names point at one server, so both are labelled "run locally". The endpoint name is stored with each answer.
- On a CPU, raise `LIVE_INFER_TIMEOUT_SECONDS` (maximum 600). A local model that fails a call is skipped for the rest of that run; the other models go on.
- The settings above were checked with `archive-check-endpoints` and `archive-plan` (free, no model call). They were not run against an Ollama server.

## Hosted free tiers, as found on 12 November 2026

| Service | Free allowance as found | Models as found | Terms as found | Fit for a public archive |
|---|---|---|---|---|
| **Cloudflare Workers AI** | 10,000 Neurons a day | Open models from Meta, Mistral, Google (Gemma), OpenAI (open weights), Alibaba, IBM, NVIDIA, Z.ai | Customer keeps outputs; no training on customer content | Possible. OpenAI-compatible endpoint. How many archive calls 10,000 Neurons cover was not worked out |
| **Google Gemini API, free tier** | Free tier on Flash models | Gemini Flash models | Free-tier content is used to improve products and may be read by human reviewers. The terms restrict free-tier use for API clients offered to users in the EEA, Switzerland and the UK. Retired model ids are silently re-routed to newer models | Possible, with care: decide whether the regional restriction applies to you before use; watch `reported_model` |
| **Groq, free plan** | 30 requests a minute and 1,000 a day, per model | OpenAI open-weight models and Qwen models | No training on inputs or outputs | Possible. Set a pause between calls |
| **OpenRouter, `:free` models** | 50 requests a day unless credits were bought | Models with the `:free` suffix; they carry expiry dates | The terms page contradicts the limits page on whether credits are required | Fragile: models expire, and the rule on credits is unclear. Expect columns to end |
| **SambaNova, free tier** | 20 requests a day per model | Not recorded | **UNVERIFIED**: the terms page could not be read | Do not use until you have read the terms yourself. 20 a day does not cover a full run (see below) |

An OpenAI-compatible endpoint was found for Cloudflare Workers AI and for the Gemini API. For Groq, OpenRouter and SambaNova it was not recorded in this research: confirm it, and the base URL, in each service's documentation. A named endpoint can only be a service that speaks the OpenAI Chat Completions API.

**What the limits mean for a run.** A run asks every locked question once per model. With the 20 core questions and the 12 trending drafts that would be 32 calls per model.
- 50 requests a day covers one such model, not two, if the limit counts all of an account's free models together (not checked).
- 20 requests a day per model covers `--set core` exactly, with no spare call, and not a full run.
- 30 requests a minute needs a pause: `_PAUSE_SECONDS=3` keeps one model under it. If two models share the limit, raise it.
- A limit hit is a failed call. On a zero-priced endpoint that model is skipped for the rest of the run and the run ends `complete_with_gaps`; nothing is retried.

### Avoid for a public comparison

| Service | Why, as found |
|---|---|
| **Cohere trial keys** | The terms forbid benchmarking / competitive analysis |
| **Cerebras** | The terms forbid benchmarking / competitive analysis. Its permanent free tier is also gone |
| **NVIDIA API trial** | The terms limit outputs to internal testing, not production use |

A public page that sets several models' answers side by side is close enough to a comparison that these terms are a reason to stay away, whatever the page calls itself.

### Gone or not free

- **GitHub Models**: retired on 30 July 2026.
- **Hugging Face Inference Providers**: the free credits are gone.
- **Cerebras**: no permanent free tier.

### Not free: the makers' own APIs

OpenAI, Anthropic, xAI and DeepSeek first-party APIs have no free tier for this. At roughly 75 short calls a run, each costs a few cents at most. That is a paid run like any other: a positive `--max-usd`, positive rates, and the owner's explicit word.

## Always keep the model name the service reports

Services substitute models silently: a retired id is re-routed, a "latest" alias moves, a host swaps a quantization. The archive therefore stores, per answer, the model id it asked for **and** the name the service reported in its response (`reported_model`), and the pages mark a difference from the previous run as "new model ID". Do not turn this off, and when you write about an answer, check both names. What a service says about itself is recorded, not verified.

## How each maps onto the named-endpoint settings

Exact rules for every setting: `docs/ANSWERS-OVER-TIME.md`, "Named endpoints". The key itself goes in `.env` under the variable name you choose (ending in `_KEY` or `_TOKEN`, not starting with `LIVE_`); the lines below leave it empty on purpose. **Never commit a key, and never put a base URL or a key in a document, an issue or a post.**

| Service | Endpoint name (your choice) | `_BASE_URL` | `_KEY_VAR` | Rates / `_FREE_TIER` | Pricing basis recorded | Other settings to consider |
|---|---|---|---|---|---|---|
| Ollama on this machine | `local` | `http://localhost:11434/v1` | none | default 0 | `local` | `_REASONING_EFFORT=none` on a second name for a model that needs it |
| Cloudflare Workers AI | `workers-ai` | placeholder; from its documentation | e.g. `WORKERS_AI_API_TOKEN` | `_FREE_TIER=YES` | `declared-free` | |
| Google Gemini API free tier | `gemini-free` | placeholder; its OpenAI-compatible endpoint | e.g. `GEMINI_FREE_API_KEY` | `_FREE_TIER=YES` | `declared-free` | watch `reported_model` (re-routed ids) |
| Groq free plan | `groq-free` | placeholder | e.g. `GROQ_FREE_API_KEY` | `_FREE_TIER=YES` | `declared-free` | `_PAUSE_SECONDS=3` |
| OpenRouter `:free` | `openrouter-free` | placeholder | e.g. `OPENROUTER_FREE_API_KEY` | `_FREE_TIER=YES` | `declared-free` | only one model per day at 50 requests |
| SambaNova free tier (UNVERIFIED) | `sambanova-free` | placeholder | e.g. `SAMBANOVA_FREE_API_KEY` | `_FREE_TIER=YES` | `declared-free` | `archive-run --set core` only |
| OpenAI, Anthropic (paid) | none: use `openai:` / `anthropic:` entries | built in | `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | global `LIVE_INPUT_USD_PER_M` / `LIVE_OUTPUT_USD_PER_M`, positive | `rates` | sampling is not sent by default |
| xAI, DeepSeek (paid) | a named endpoint, if the service offers an OpenAI-compatible endpoint (not checked) | placeholder | your choice | own positive `_INPUT_USD_PER_M` / `_OUTPUT_USD_PER_M`; no `_FREE_TIER` | `rates` | |

If a service rejects the pinned sampling (`temperature=0` and a fixed `seed`), its calls fail; set `_SEND_SAMPLING=NO` for that endpoint. The sampling actually sent is stored with each answer.

`_FREE_TIER=YES` is **your** statement that the service charges nothing. Nothing checks it, the answers are labelled `declared-free`, and the local ceiling cannot see a charge there: only the service's own billing page can. If you are not sure a tier is free for your account, enter positive rates instead.

### Worked example: local models plus two hosted free tiers

Hosts and hosted model ids are placeholders. Replace each from the service's documentation and model list.

```
LIVE_ARCHIVE_ENDPOINTS=local,local-nothink,workers-ai,groq-free

LIVE_ARCHIVE_ENDPOINT_LOCAL_BASE_URL=http://localhost:11434/v1
LIVE_ARCHIVE_ENDPOINT_LOCAL_NOTHINK_BASE_URL=http://localhost:11434/v1
LIVE_ARCHIVE_ENDPOINT_LOCAL_NOTHINK_REASONING_EFFORT=none

LIVE_ARCHIVE_ENDPOINT_WORKERS_AI_BASE_URL=https://workers-ai.example/v1
LIVE_ARCHIVE_ENDPOINT_WORKERS_AI_KEY_VAR=WORKERS_AI_API_TOKEN
LIVE_ARCHIVE_ENDPOINT_WORKERS_AI_FREE_TIER=YES
WORKERS_AI_API_TOKEN=

LIVE_ARCHIVE_ENDPOINT_GROQ_FREE_BASE_URL=https://groq-free.example/v1
LIVE_ARCHIVE_ENDPOINT_GROQ_FREE_KEY_VAR=GROQ_FREE_API_KEY
LIVE_ARCHIVE_ENDPOINT_GROQ_FREE_FREE_TIER=YES
LIVE_ARCHIVE_ENDPOINT_GROQ_FREE_PAUSE_SECONDS=3
GROQ_FREE_API_KEY=

LIVE_ARCHIVE_MODELS=phi4-mini=local:phi4-mini,gemma3-4b=local:gemma3:4b,qwen-4b=local-nothink:qwen3.5:4b,open-a=workers-ai:model-id-from-the-service,open-b=groq-free:model-id-from-the-service
LIVE_INFER_TIMEOUT_SECONDS=300
LIVE_ALLOW_PAID_CALLS=YES
```

Then, in this order:

```bash
python -m live_genesis.cli archive-check-endpoints   # free, no model call: host, key set yes/no, rates, basis per endpoint
python -m live_genesis.cli archive-plan              # free: calls per endpoint, worst-case estimate, the run command
# Only on the owner's explicit word, a free or local run included:
python -m live_genesis.cli archive-run --max-usd 0.01
```

At most 8 models and 8 endpoint names per run. Zero-priced models are asked first; a failed call on a priced endpoint stops the run and blocks paid calls until `cli reconcile`.

## Before the first real run: a short list

1. Questions reviewed and locked (`archive-lock`, `archive-lock --set trending`). This cannot be redone.
2. Models chosen. Local tags confirmed with `ollama show`, pulled once, models directory backed up.
3. For each hosted service: terms read today, by you; limits noted; key in `.env` only.
4. `archive-check-endpoints`, then `archive-plan`; read both outputs.
5. The owner says to run, with a ceiling.
6. After the run: `archive-status`, then export, read, approve (`docs/FIRST-REAL-ROUND.md`).
