# Codex independent review: Answers Over Time

Reviewed commit: [4554f03fc8bfbfff505c2417bbafcd9fc4f4b8df](https://github.com/harshsrigh/live-genesis/commit/4554f03fc8bfbfff505c2417bbafcd9fc4f4b8df), on `main` at the start of this review. Date: 2026-11-12. Delivery branch: `codex/archive-review`.

Read `CLAUDE.md`, `docs/PIVOT-ANSWERS-OVER-TIME.md`, and `docs/ANSWERS-OVER-TIME.md` first, then `AGENTS.md`. Reviewed the requested archive implementation, question JSON, archive tests, and archive integration in CLI, API, web routes, and snapshots. Also followed the directly relevant provider adapter, budget reservation/settlement, and credential checks.

This is a review only. No application code, tests, question list, or configuration was changed. No paid command or provider request was started. The existing pytest suite invokes its own mocked runner functions. The new triggering cases below were traced through source; they were **not executed as new reproductions**. Severity P2 means a correctness/integrity issue to fix; P3 means a lower-impact presentation issue.

## Findings

### 1. P2 — A published question can change while retaining the original locked-set hash

**Locations:** [archive.py:872–919](https://github.com/harshsrigh/live-genesis/blob/4554f03fc8bfbfff505c2417bbafcd9fc4f4b8df/live_genesis/archive.py#L872-L919), especially 894–896; [snapshot.py:449–469](https://github.com/harshsrigh/live-genesis/blob/4554f03fc8bfbfff505c2417bbafcd9fc4f4b8df/live_genesis/snapshot.py#L449-L469). Canonical hash definition: [archive.py:194–196](https://github.com/harshsrigh/live-genesis/blob/4554f03fc8bfbfff505c2417bbafcd9fc4f4b8df/live_genesis/archive.py#L194-L196).

**What goes wrong:** The recording paths compare the current question file against the database lock. The publication path only checks the whole exported file against the manifest, checks field types/counts, and scans content. It never verifies that the exported questions actually hash to `set_hash`. A rehashed export can therefore display a different question and prompt beside the original answers, with `locked: true` and the original lock hash. This is a publication-integrity gap requiring write access to the export/manifest; it is not an unauthenticated API bypass or a way to defeat `require_lock()` when recording.

**Concrete trigger:** Starting with a valid approved export, change Q01 in `archive.json` to:

```json
{
  "text": "How many hours are in a day?",
  "prompt": "Answer in one or two sentences.\n\nHow many hours are in a day?"
}
```

Keep its id, group, rationale, all answers, `locked`, and `set_hash` unchanged. Recompute only the archive file SHA-256 in `MANIFEST.json`, as the existing `retarget_archive()` test helper does; preserve `reviewed: true`. The shape, counts, and content checks all still pass. `check_published()` and `build_from_data()` have no condition that rejects the mismatch with the locked question set.

**Suggested fix:** Export enough canonical lock data to reproduce exactly the hash used at lock time, including question positions and original group order, and verify it before approval, checking, or rendering reviewed data. If protection against replacing both the list and its claimed lock hash is required, compare against a separately trusted committed lock record as well. An unsigned, editable manifest alone cannot provide that protection.

**Missing test:** Extend the tamper cases at `tests/test_archive.py:711–783` with a safe question/prompt edit and recomputed outer hash while the inner `set_hash` remains unchanged. Require check, approve, and build to refuse it. Also test changed group explanations, ordering, and question removal.

### 2. P2 — Overlapping runs can permanently miss a change

**Locations:** [archive.py:410–418](https://github.com/harshsrigh/live-genesis/blob/4554f03fc8bfbfff505c2417bbafcd9fc4f4b8df/live_genesis/archive.py#L410-L418), [442–452](https://github.com/harshsrigh/live-genesis/blob/4554f03fc8bfbfff505c2417bbafcd9fc4f4b8df/live_genesis/archive.py#L442-L452), and [525–579](https://github.com/harshsrigh/live-genesis/blob/4554f03fc8bfbfff505c2417bbafcd9fc4f4b8df/live_genesis/archive.py#L525-L579).

**What goes wrong:** A new real run is allowed while an earlier run is still running. The write transaction protects each insertion, but it does not serialize the runs across provider calls. `changed` is computed once against earlier-numbered answers that already exist. A slow earlier answer arriving after a newer answer cannot repair the newer row because answers are append-only. Held reservations count toward the budget but do not prohibit another run; only unconfirmed reservations block calls.

**Concrete trigger:** In an offline fixture with adequate budget and the same answerer `model-a` and question Q01:

1. Open run 1; pause before its Q01 answer is stored.
2. Open run 2 and store Q01 = `"Eight."` first.
3. Store run 1 Q01 = `"Seven."`.

Run 2 receives `changed = NULL` because run 1 has no answer yet. Run 1 also receives NULL because it is first. After both complete, the final chronological record contains different answers but no changed flag. The normal operator runner can create this insertion order when two invocations overlap; no database tampering is necessary.

**Suggested fix:** Atomically refuse a new real archive run while another is running, with a documented recovery procedure for abandoned runs; or derive comparisons from the final ordered answer history rather than storing an immutable result too early. If concurrent runs remain supported, define when comparisons become final and prevent late earlier-run writes from silently invalidating them.

**Missing test:** `tests/test_archive.py:84–104` checks only sequential runs. Add a deterministic barrier-based overlap test that stores the newer answer first, without making a provider request, and asserts either that the second run is refused or that the final comparison reports the change.

### 3. P2 — Normalisation erases meaningful numeric punctuation

**Location:** [archive.py:156–165](https://github.com/harshsrigh/live-genesis/blob/4554f03fc8bfbfff505c2417bbafcd9fc4f4b8df/live_genesis/archive.py#L156-L165).

**What goes wrong:** Stripping every Unicode punctuation character at both ends treats leading decimal points/minus signs and terminal percent signs as cosmetic punctuation. This follows the current documented rule, but that rule conflates materially different numeric answers. The resulting hash suppresses a real text change.

**Concrete inputs:**

| Earlier reply | Later reply | Both normalise to |
| --- | --- | --- |
| `-5 C.` | `5 C.` | `5 c` |
| `.5 litres.` | `5 litres.` | `5 litres` |
| `5%` | `5` | `5` |

Both replies pass `classify()`; consecutive records for the same question/answerer receive `changed = false`. This matters for the existing temperature and water questions even though the archive does not judge correctness.

**Suggested fix:** Limit cosmetic trimming to explicitly defined quotation/sentence punctuation while preserving signs, decimal points attached to digits, and units such as percent. Version the normalisation rule and describe how existing hashes/comparisons are handled; do not silently reinterpret an append-only history.

**Missing test:** `tests/test_archive.py:72–81` preserves an inner decimal point but does not exercise numeric punctuation at the edges. Add these pairs and a signed Unicode-minus case alongside the existing quote/spacing cases.

### 4. P2 — Non-final provider output can become an ordinary answer and comparison baseline

**Locations:** [archive.py:172–189](https://github.com/harshsrigh/live-genesis/blob/4554f03fc8bfbfff505c2417bbafcd9fc4f4b8df/live_genesis/archive.py#L172-L189), [553–579](https://github.com/harshsrigh/live-genesis/blob/4554f03fc8bfbfff505c2417bbafcd9fc4f4b8df/live_genesis/archive.py#L553-L579). Adapter path: [engine.py:202–207](https://github.com/harshsrigh/live-genesis/blob/4554f03fc8bfbfff505c2417bbafcd9fc4f4b8df/live_genesis/engine.py#L202-L207).

**What goes wrong:** Only `stop == "length"` is excluded. Nonempty text with `stop == "other"` or `"unknown"` becomes `answered`, provided it passes the text guards. The OpenAI adapter maps an incomplete response with a non-token-limit reason to `other`; compatible servers can omit their finish reason and produce `unknown`. Neither the answer row nor the public payload retains this completion uncertainty. Partial text can therefore be exported as an ordinary answer and used as the previous answer for the next comparison.

**Concrete trigger:** A provider completion reaches the archive as `Completion("The answer is", 40, 12, "other")`, for example after an incomplete response with reason `content_filter`. `classify("The answer is", "other")` returns `("answered", "", "The answer is")`. The same is true for `"unknown"`. A subsequent complete reply is compared against this fragment. These are valid usage counts, so settlement does not reject the completion.

**Suggested fix:** Require an explicit completed stop state for an ordinary answer, or represent non-final/unknown completion separately and exclude it from changed comparisons. Retain the normalized stop reason so publication can explain why text was not accepted. A completed refusal can be treated according to an explicit policy; an incomplete fragment should not silently look completed.

**Missing test:** `tests/test_archive.py:107–115` covers `length`, empty, oversized, and guarded text, but not `other` or `unknown`. Test classification and export for each stop state, plus a sequence showing that a non-final fragment is skipped as a comparison baseline.

### 5. P3 — Missing/withheld answers are described as stable answers

**Locations:** [archive_pages.py:146–151](https://github.com/harshsrigh/live-genesis/blob/4554f03fc8bfbfff505c2417bbafcd9fc4f4b8df/live_genesis/archive_pages.py#L146-L151) and [234–238](https://github.com/harshsrigh/live-genesis/blob/4554f03fc8bfbfff505c2417bbafcd9fc4f4b8df/live_genesis/archive_pages.py#L234-L238).

**What goes wrong:** The index's no-answer check counts any answer row, including `withheld` and `no_answer`. The detail summary discards missing rows, then calls any nonempty series with no true changed flag stable across every run. It does not require two comparable answers. Individual cells show the gaps correctly, but the summaries overstate the evidence.

**Concrete triggers:**

- Q01 has two runs: one row with `state="withheld", text=null, changed=null`, the other `state="no_answer", text=null, changed=null`. The index says `No change in any model across 2 runs` despite having no answers to compare.
- `model-a` has `"Seven."` in run 1 and no answer (absent, withheld, or cut off) in run 2. The detail summary says `model-a gave the same answer in every run recorded.`
- Even a single first answer (`changed=null`) produces that stability statement.

**Suggested fix:** Distinguish no usable answers, one answer with no comparison, and multiple comparable answers. Say that recorded answers match while explicitly counting skipped runs; claim an answer in every run only when every run actually contains one.

**Missing test:** The page tests at `tests/test_archive.py:532–565` cover a rich demo, not all-missing, all-withheld, single-answer, or unchanged-with-gaps series. Add these small payloads and assert that the summary never claims unsupported stability.

## Results for the other requested boundaries

- **Paid-call gates:** No supported CLI/API path was found that records a server-generated archive answer while skipping the configured provider readiness check, exact paid opt-in, positive finite run cap, reservation, and settlement invocation. Checks are at `archive.py:485–540`; settlement precedes storage at 567–579. This is not a proof against arbitrary in-process code or a compromised database. On reported usage above a hold, settlement marks the receipt unconfirmed and the runner stores the returned answer before stopping; the existing test at 358–362 explicitly expects that behavior. A `server call` label is therefore not a claim that billing is fully reconciled.
- **External answers:** The authenticated POST deliberately accepts outside text without this server paying for it. It records `source="api"` and shows `self-declared`, not `server call`. That documented distinction is not a paid-call bypass. It cannot establish that an outside client actually called a model.
- **Mock/demo boundaries:** No normal-path leak of a stored mock/dry-run answer was found. Public SQL excludes mock runs and rows; pages filter mock rows again. Scripted cells and pages are labeled, and the reviewed publication path rejects mock/demo rows. This trusts configured providers and executable code: replacing SDK behavior or relabeling an editable export is not cryptographically detectable by `infer is REAL_INFER` or a manifest hash.
- **Lock while recording:** No supported-path bypass of the question file comparison in `require_lock()` was found. Relevant fields and ordering enter the canonical hash; status is checked separately. The publication-side gap is finding 1. System text is a code constant, not part of the locked question-set hash; changes to executable code were not treated as an untrusted question-file input.
- **HTML escaping:** No reachable unescaped value from archive questions/answers was found in the rendered pages. Text/attribute content uses `html.escape(..., quote=True)`; question ids used in links are constrained. Asset version values and relative URL prefixes are generated internally, rather than read from answer payloads. Hostile caller-supplied replacements for those internal function arguments were not treated as a public input surface.
- **One answer per run/question/agent:** No supported POST bypass was found. The route rejects extra body fields and derives identity from authentication; `submit_api_answer()` rechecks credentials in the write transaction, selects the latest real run, checks the question and duplicate, then inserts under `BEGIN IMMEDIATE`. The unique constraint and no-update/no-delete triggers provide an additional guard. A changed `X-Agent-Model` does not change the answerer identity. This conclusion is source-based for concurrency; no new parallel HTTP reproduction was executed.

Additional useful tests, beyond findings 1–5: two simultaneous POSTs for the same agent/run/question (exactly one row); duplicate submission after rotating the agent key or changing the model header; disabled/expired/revoked credentials on this specific route; mutation of each locked question field and ordering before an API submission; a withheld answer between two normal replies; and hostile group names/answerer names in the HTML fixtures. Existing tests cover many neighboring cases but do not explicitly exercise all of these boundaries.

## Validation and limitations

Ran the requested `python -m pytest -q` through the existing Windows virtual environment. No dependency installation or production data access was needed.

- The initial whole-suite attempt reached **230 passed and 706 setup errors** because pytest could not create its default numbered temporary directory. Those setup errors are not evidence of product failures.
- After creating a temporary-test parent inside the permitted `docs/reviews/` directory, a whole-suite retry stalled in the first API test. A focused archive retry reached 36 completed cases and then stalled at `test_both_web_apps_serve_the_same_pages`. A pytest faulthandler dump showed Python's Windows asyncio setup blocked in `socket._fallback_socketpair()` / `accept()` while opening Starlette's TestClient. Both stalled processes were stopped.
- A final bounded archive selection completed: **44 passed, 1 failed, 3 deselected, 1 warning in 29.51 seconds**. The failure was `test_published_archive_tamper_guard_and_mock_are_refused` at line 740: **WinError 1314**, insufficient privilege to create a symlink. That test stops there, so its later tamper checks were inspected but not executed in that run.
- The three deselected tests were `test_both_web_apps_serve_the_same_pages`, `test_archive_pages_answer_503_without_a_database`, and `test_outside_client_answers_are_self_declared_guarded_and_limited`. The warning was Starlette's httpx deprecation warning.

For the final selection, the executable was `outputs/live-genesis/.venv/Scripts/python.exe` relative to the task workspace. `PYTHONDONTWRITEBYTECODE=1` was set. The command remained `python -m pytest -q`; these options were supplied through `PYTEST_ADDOPTS`:

```text
tests/test_archive.py --basetemp=docs/reviews/.pytest-archive-offline -p no:cacheprovider --tb=short -k "not test_both_web_apps_serve_the_same_pages and not test_archive_pages_answer_503_without_a_database and not test_outside_client_answers_are_self_declared_guarded_and_limited"
```

Temporary pytest files remain local and uncommitted: automatic approval review rejected their cleanup because this session cannot approve that filesystem action. The delivery branch contains only this review file, with no database, keys, environment files, or secrets.

**Not checked:** live provider behavior or billing; actual paid runs; the deployed website or hosting configuration; production databases; a complete passing Windows/Linux suite; browser execution beyond the existing HTML parser tests; a new runtime reproduction for each finding; stress/fuzz testing or parallel HTTP tests; cryptographic provenance against a malicious operator/provider; and changes after the pinned commit. No PR was opened and no merge was performed.
