# Archive review — c441cdf

Independent review, 2026-11-13. Base: c441cdf0531d22539e44d0187ade835ccc8b32a6. Documentation only; no certification. Read the five requested project documents and repository development instructions. No model endpoint was contacted; no paid CLI command was run. Reproductions used disposable SQLite files outside the repository and replaced inference with local Python stubs. Provider readiness was stubbed in the interruption reproduction; it does not test credential validity.

## P2 — An interrupted priced call does not activate the reconciliation gate

Location: `live_genesis/archive.py:1338-1376,1403-1415`; subsequent-run check at 1277-1280.

Precondition: operator interrupts a priced call after a reservation exists; the provider may have received it. This requires no malicious agent.

Reproduction (executed offline):
1. Initialize a disposable database; lock a disposable copy of the core question list.
2. Configure a single native-provider model with positive input/output rates, a $1 cap and paid opt-in. Replace readiness checks and digest lookup with local stubs; replace both infer and REAL_INFER with a function raising KeyboardInterrupt. Block socket connections.
3. Call the runner function, catch KeyboardInterrupt, and inspect receipt status and unconfirmed_ids.
4. Replace inference with a completion stub returning Seven., 40 input tokens, 12 output tokens, end. Run the function again with the same cap.

Observed: receipt statuses were [held], unconfirmed count 0. The second run made 20 stub calls and completed. The first hold remains counted against the ceiling, so this is not erased spending; it is a bypass of the mandatory uncertainty/reconciliation stop.

Expected: an interrupted potentially sent priced call remains unconfirmed and blocks subsequent calls until reconciled.
Direction: track whether a request may have been sent; on BaseException cleanup mark its unsettled receipt unconfirmed before re-raising. Handle startup recovery for abandoned held receipts separately. Add interruption tests around inference and settlement.

## P2 — Ambient proxies can route a supposedly local endpoint off-machine

Location: `live_genesis/engine.py:226,238`; loopback classification `live_genesis/providers.py:92-103,130-147`.

Precondition: an HTTP_PROXY/ALL_PROXY setting applies to loopback (for example NO_PROXY is empty), while a zero-rate endpoint is configured as local.

Reproduction (executed without sending a request): set HTTP_PROXY to a synthetic proxy address and NO_PROXY to empty; construct the same httpx.Client(follow_redirects=False) used by the adapter; inspect its transport for http://127.0.0.1:11434/v1.

Observed: the selected transport pool was HTTPProxy, rather than a direct connection pool. The URL-based loopback classification still allows local zero pricing. Redirects being disabled does not disable proxy routing. A configured remote proxy could receive the HTTP prompt and authorization header, or serve the response, despite the local label. No real key or prompt was sent in this reproduction.

Expected: the local pricing/locality promise is bound to a direct loopback transport.
Direction: disable environment proxies for loopback clients (trust_env=False), or explicitly reject proxy-mediated local operation. Document separately that a deliberately configured local forwarding service cannot be attested by this software. Test transport routing without live services.

## Migration verification

Used git archive to extract tracked package source for 4554f03 and 7d3be7f outside the repository. For each version, created a new synthetic database, seeded identities without writing or printing their keys, populated demo-archive and archive-dry-run data, then upgraded that same file in place with current core.init_db twice. Compared canonical hashes of every original column of every original table, including row counts; only fingerprints were compared, no database was copied into the repository.

| Old commit | Original tables | Original rows | Archive answers before/after | Changed original tables | Seed/upgrade exits |
| --- | ---: | ---: | --- | --- | --- |
| 4554f03 | 32 | 579 | 538 / 538 | none | 0 / 0 |
| 7d3be7f | 35 | 679 | 622 / 622 | none | 0 / 0 |

This confirms preservation for these synthetic demo/mock histories, not every production state. Locked real histories, crash recovery midway through migration, and concurrent migration were not separately reproduced.

## Other checks and limits

- No new source-level bypass was identified for the explicit opt-in and finite positive max-usd checks. The interruption finding concerns cleanup after those checks.
- Declared-free remains an explicit, unverified operator assertion; the software cannot detect a provider beginning to bill. No claim of verified free service is justified.
- The four HTTP-client construction sites (named endpoint plus three providers) explicitly disable redirects. No live redirect was sent; transport behavior against all three actual SDKs was not independently reproduced.
- Core/trending lock and append-only paths, representative/sample handling, public payload validation, config key loading/drop paths, and Trends parser were inspected at source level. This review does not claim exhaustive coverage or that earlier review findings are resolved.
- Trends parses bounded UTF-8 input, rejects DOCTYPE/ENTITY markup before ElementTree, removes forbidden characters, guards candidate strings and emits ASCII JSON. No feed was fetched; no entity-expansion or encoding fuzz campaign was run.
- Single-sample compatibility has dedicated tests in tests/test_archive_samples.py. This review did not independently compare exported page bytes against the historical commit.
- No real endpoint, provider billing, TLS/proxy deployment, DNS override, real key, production database, or real model output was tested. SDK/provider-reported metadata and every alternate public field were not exhaustively fuzzed.
- Local execution helpers: review-nov13-runtime/reproduce.py, reproduce_security.py and verify_migrations.py (outside the repo, not committed). The Windows verification report records the full-suite outcome.
