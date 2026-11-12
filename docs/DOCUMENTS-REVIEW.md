# Shared documents review

Reviewed November 11, 2026 (America/Phoenix), against `main`
`d5b7568ebddac29bbcb4fc10d9f719ac33c228c4`. Read-only code review and offline
reproductions; no application files changed, model calls, or spending.

## Finding

### P2 — Document origin flags and creation time are outside the hash chain

Location: `live_genesis/documents.py:137-145` (`chain_hash`), with effect at
`documents.py:393-396` (`_source`). The hash includes the fixed document ID, title,
topic and creator, but omits the document's `demo` and `created_at` fields. The
revision's separate `demo` flag is hashed, but verification does not compare it
with the document flag. Public document provenance trusts the unverified document
flag: `demo=1` makes the whole document report `source=demo`.

Reproduced on a fresh scratch database:

1. Agent 1 creates one ordinary API document. Public source is `api`, demo is 0.
2. Drop `documents_fixed_fields`, then update only that document's `demo` to 1 and
   `created_at` to `1900-01-01T00:00:00Z`.
3. Public source changes to `demo` and the timestamp changes. `verify()` still
   returns `[]`; the document `head_hash` is **identical**. No revision or hash
   was changed or recomputed.

This needs database-file/write access; it is not a remote agent authorization
bypass. It is nevertheless a defect in the claimed tamper detection: the module
and security docs explain that file writers can evade detection by *recomputing*
the chain, after which an externally saved head hash detects the rewrite. These
fields can change without recomputation and evade that external head comparison.
The chain helper also says it covers the document's fixed fields.

Include all fixed origin fields in a versioned hash scheme and verify agreement
between document and revision demo flags, or explicitly narrow the integrity
contract and avoid deriving the provenance badge from unchecked metadata.
Existing hashes need an explicit migration/version decision; do not silently
invalidate historical chains. Add regression tests that independently change
each fixed field after dropping the corresponding guard trigger, requiring a
detected break or changed anchored head.

Reproducer: `work/repro_document_hash.py`. It uses a temporary database and does
not modify source or any real agent database.

## Checks and positive coverage

**39 non-HTTP document tests passed** on Windows in 10.11 seconds using the
existing environment. Test output: `work/documents-review-tests.log`; bounded
runner: `work/review_documents_run.py`. Functions using FastAPI TestClient were
excluded because of the previously observed restricted Windows socket setup.
This is not a full HTTP or server-startup verification.

Reviewed transaction ownership through `core.submit_action`, base-revision conflict
handling, append-only guards, per-revision body hashes, revert attribution, demo
isolation, engine-receipt provenance linkage, content guards, context/diff size
bounds, read-only document views, and snapshot export. Tests cover create/revise/
revert, stale edits, replay, rollback, size limits, content guards, history tampering,
provenance, context truncation, search, demo export and mocked engine integration.
No further actionable defect was confirmed in this bounded review. Line blame is
correctly labeled as an attribution heuristic rather than contribution value.

## Ledger and governance status

Rechecked remote `main` at the end of this review. It still points to `d5b7568`;
neither `live_genesis/ledger.py` nor `live_genesis/governance.py` is in that tree.
Their review remains pending until those modules land. No assumptions about
unmerged implementation or test coverage are made here.

