# Re-verification of the five LIVE Genesis port fixes

Reviewed November 11, 2026 (America/Phoenix), pinned main
`d5b7568ebddac29bbcb4fc10d9f719ac33c228c4`, against the five findings in the prior
Codex review. The isolated `work/reverify-main` export matches all **59 remote Git
blob hashes**. AGENTS.md and CLAUDE.md were read. No engine/application source edits,
real provider calls, or spending were performed.

## Result

**All five original defect behaviors are fixed in the independent reproductions.**
This closes the original findings at this revision; it is not a blanket security
certification or authorization to spend money.

| Finding | Independent reproduction on the fixed revision | Result |
|---|---|---|
| Concurrent resumes pay twice | Two threads synchronize immediately before budget reservation, then resume the same agent/round with a mocked provider. Exactly **one mock inference call and one settled/consumed receipt** are recorded. The competing claim is refused. | Fixed |
| Receipt charged to current round | Reserve and settle agent 2 in round 1, open round 2, submit the paid response. Saved action remains in **round 1**, while current round is 2. | Fixed |
| Keyed idle retry consumes quota repeatedly | Two identical idle submissions with the same key return the cached result with `replayed=true`; daily and minute counters are **one**, not two. | Fixed |
| Boolean/string IDs coerce to integers | The actual ActionRequest model rejects `post_id=true`, `post_id="1"`, and `post_id=1.0` with validation errors. | Fixed |
| Request body has no byte limit | Drive the real AgentBodyLimit ASGI middleware with both declared oversized content and a chunked body exceeding 16 KB. Both return **413 before calling the downstream app**. | Fixed |

Reproduction script: `work/reverify_repros.py`. It creates a disposable SQLite file,
uses a fake provider implementation, and imports the pinned source. The concurrency
barrier is placed immediately before the reservation call to force the original
race independently of thread scheduling. The middleware check drives ASGI directly;
it is not presented as an HTTP-network test.

## Existing test selections rerun

- **105 passed** from authentication, core, provenance and quota tests (17.88 seconds).
- **116 passed** from budget, engine, controls and game tests (20.33 seconds).
- These selections overlap; they are not 221 unique tests.
- Both selections exclude direct TestClient functions, matching the previous review.
- Both report one Starlette/httpx deprecation warning, with no test failures.

Logs: `work/reverify-auth.log` and `work/reverify-budget.log`; runner:
`work/reverify_tests.py`. Tests used the existing Windows virtual environment,
not a fresh dependency installation. A bounded full-suite attempt is recorded
separately in `work/reverify-full.log` and `work/reverify-results.json`.

## Disposition

No remaining reproduction of the five original defects was found. The accepted
compatibility differences (optional idempotency keys, nonexpiring operator rounds,
current-round resume, and in-memory request limiter) are not reopened as findings.
The documents and later ledger/governance review is a separate work item.

Keep the human observatory read-only and maintain truthful model/demo provenance,
no human citizens, no autonomous collection/outreach/transfers, the below-$4,000
project ceiling, and the $1,000 validation gate.

