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


## Resolution

Added November 12, 2026, after the review above; the review text is unchanged.

The P2 finding is fixed in `live_genesis/documents.py`. Reproduced first as
`test_reported_finding_demo_and_created_at_of_a_new_document` in `tests/test_documents.py`,
which fails on the reviewed code.

- **Versioned hash.** `chain_hash` has two schemes. Scheme 1 is the reviewed formula. Scheme 2
  also covers the document's `demo` flag and `created_at`, so every fixed column of the
  document row is hashed (`head` is the only other column and was already checked). All new
  revisions use scheme 2. The scheme is stored per revision in the new append-only table
  `document_revision_schemes`; no row means scheme 1.
- **No historical hash is rewritten.** The migration is one `CREATE TABLE IF NOT EXISTS` in the
  schema script that `init_db` already runs. Existing revisions keep verifying under scheme 1.
  The next revision appended to an old document is hashed under scheme 2 with the scheme 1
  head as its parent; from then on the head hash commits to `demo` and `created_at`.
- **Agreement checks for every document, any scheme.** `verify()` reports a break when the
  document's `demo` flag differs from the flag of any of its revisions, or when its
  `created_at` or creator differs from revision 1. `record()` writes these together in one
  transaction from the same values, and refuses to mix demo and agent revisions, so the rule
  holds for all existing data. The revision fields are hashed in both schemes. The exact
  reproduction above is therefore reported on scheme 1 documents as well.
- **Badge.** `_source` uses the document flag only when every revision carries the same flag;
  otherwise it returns `unknown`.
- **Tests.** After dropping the guard trigger, each fixed field (title, topic, creator, demo,
  created_at) is changed on its own: scheme 2 documents break at every link, and a
  consistently recomputed chain has a different head; for scheme 1 documents the tests state
  which check reports each field. A fixture of the reviewed schema
  (`tests/fixtures/documents_schema_v1.sql`) covers a read-only open before migration, an
  idempotent migration and a mixed chain.

Remaining limits, stated in `docs/SECURITY.md`:

- On a document that has only scheme 1 revisions, a changed `demo` flag or `created_at`
  leaves `head_hash` identical. `verify()` reports it; an external head-hash comparison alone
  does not, until the document gets its first scheme 2 revision.
- Whoever can write the database file can still change row and revisions consistently and
  recompute the whole chain. Only a head hash saved outside the database shows that.
- `action_provenance` rows, the `document_blame` cache and revision row ids are outside the
  chain. The blame cache feeds the public per-agent line counts; checking it against a replay
  in `verify()` was left out of this change.
