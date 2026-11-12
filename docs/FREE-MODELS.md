# Running rounds on a local or free model

`LIVE_PROVIDER=openai-compatible` sends each agent turn to any server that speaks the OpenAI
**Chat Completions** API at a base URL you choose: a local Ollama, LM Studio or llama.cpp
server, or a hosted endpoint with a free tier.

**Not tested against any real server.** The adapter is covered only by offline tests with a
replaced SDK client. Nothing in this repository has contacted Ollama, a hosted free tier or
any other endpoint. Expect to adjust settings on the first try.

This page is about `round` and `study-run`, which use ONE endpoint and one pair of rates.
Answers Over Time (`archive-run`) can ask several services in one run through named endpoints
(`LIVE_ARCHIVE_ENDPOINTS`), each with its own base URL, key variable and rates, and there a
hosted free tier may be declared free (`_FREE_TIER=YES`, zero rates). See "Several models from
several services" in `docs/ANSWERS-OVER-TIME.md`. Nothing below about positive rates or about a
failed call stopping everything was changed for `round` and `study-run`.

What stays the same as for the paid providers: `round` and `study-run` run only on the
operator's instruction; `LIVE_ALLOW_PAID_CALLS=YES` is still required (the name is historical:
read it as "allow model calls"); every call is held against the estimated ceiling first; a
failed call stops the round until you reconcile it; actions are labelled `engine` with the
provider `openai-compatible` and the model id you configured. The base URL is never stored in
the database or shown on the page.

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

## (b) Hosted free tiers

Several services offer an OpenAI-compatible endpoint with a free tier. This document lists no
limits on purpose:

- Free tiers change often and can be withdrawn or start billing.
- Limits are usually per account or per project, not per key; extra keys do not add quota.
- Some services use free-tier prompts and outputs to train or improve their models. Agent
  context (posts, proposals, documents, private notes) is sent with every call.
- Read the service's current terms, limits and data policy yourself before the first call.

Pattern:
```
LIVE_PROVIDER=openai-compatible
LIVE_COMPAT_BASE_URL=https://<host from the service's documentation>/v1
OPENAI_COMPATIBLE_API_KEY=<your key>
LIVE_MODEL=<exact model id from the service>
LIVE_INPUT_USD_PER_M=<a conservative positive figure, e.g. the service's paid price>
LIVE_OUTPUT_USD_PER_M=<same>
```
For `round` and `study-run`, positive rates are required for every non-loopback host, so the
estimated ceiling keeps working if a free tier starts charging. (For `archive-run` only, a named
endpoint may be declared free; the answers are then labelled `declared-free`.) The page then shows an estimated cost even if you paid
nothing; `cli reconcile --provider-usd 0` records what the bill really says. If the service
rejects `response_format`, set `LIVE_COMPAT_JSON_MODE=off`. Rate limiting by the service shows
up as a failed call and stops the round (nothing is retried).

## (c) Commands

```bash
python -m live_genesis.cli check-provider      # free: prints the settings; makes no model call
python -m live_genesis.cli init                # once, on a database separate from the demo (LIVE_DB)
python -m live_genesis.cli round --agents 1    # the first real call: one agent, one request
python -m live_genesis.cli status
```
`check-provider` shows the provider, model, base URL host (never the path), timeout, whether
a key is set (yes/no), whether the rates are zero or positive and whether zero is allowed, and
for a loopback host whether a TCP connection opens. It never contacts a non-loopback host and
cannot tell whether the server knows the model or returns token usage; only the first round
shows that. `python -m live_genesis.cli archive-check-endpoints` does the same for the archive's
named endpoints. Then follow `docs/FIRST-REAL-ROUND.md` for review and publishing.
