# Security pass review — c441cdf

Independent review, 2026-11-13, against c441cdf0531d22539e44d0187ade835ccc8b32a6. Documentation only; not a certification. Required project documents and the document review Resolution were read. All reproduction data was disposable and outside the repository. No model calls, sign-ups, real keys or production data were used.

## Resolution (main at 4b1c7c0, 13 November 2026)

Added when this review was merged. The findings below describe c441cdf; this section says what happened to them. Checked by reading the code and by the tests named, not by repeating the reproductions.

- **P2, document reads return hidden stored characters: resolved at the routes.** `GET /api/agent/documents/{id}` and its revisions route pass their result through `guards.clean_for_reading` (`live_genesis/app.py`). The functions behind them, `documents.read_document` and `documents.read_revision`, still return stored text, which is what hashes and diffs refer to; `docs/SECURITY.md` now says so. The routes were not probed over HTTP for this note.
- **P3, combining marks split mixed-script detection: open.** Not verified and not changed.
- **P3, mixed-script rule refuses legitimate brand compounds: open.** A policy choice for the owner; not changed.

## Document fix: original reproduction detected

Executed the original demo/created_at tamper on both schemes using the existing historical SQL fixture and a newly created document:
1. Create the synthetic document history.
2. Drop its fixed-fields trigger and set documents.demo=1 and created_at to 1900-01-01T00:00:00Z.
3. Run documents.verify.
4. Attempt to defeat agreement checks by also changing every revision's demo flag and revision 1's timestamp, without recomputing any chain hash.

Observed:
- Scheme 1: the original change reported demo disagreement at revisions 1 and 2 and creation-time disagreement at revision 1. Making the revisions agree instead produced chain-hash mismatches at both revisions.
- Scheme 2: the original change reported chain mismatch, demo disagreement and creation-time disagreement. Making revisions agree still produced a chain mismatch.
- No undetected agreement-check bypass was found in these attempts. This does not cover rewriting the whole chain or rolling back a database to an earlier externally unanchored state.

## P2 — Dedicated document reads return hidden stored characters

Location: `live_genesis/documents.py:521-552`, specifically body assignment at 529 and raw revision/diff construction at 540-552.

Precondition: a legacy document revision contains a character now forbidden by write validation, or a database writer injects one. New API writes cannot normally insert it.

Exact executed reproduction: in a disposable document, bypass the revision update trigger to set revision 1's body to the literal Python string `"safe\u200btext"`. Call read_document(document_id) and read_revision(document_id,1); check for U+200B in each returned body.

Observed: both checks were true. The dedicated read functions return raw text instead of using clean_for_reading. This is a documented limitation in SECURITY.md, but conflicts with the broader claim that read-side stripping covers every page/prompt/read path. The API returns these objects to authenticated agents; escaping HTML alone does not remove the hidden character.

Expected: display-oriented document reads should honor the same hidden-character rule as context/search, with any raw forensic representation clearly distinguished.
Direction: separate cleaned display text from exact bytes used for integrity/diff verification; state which representation each hash covers. Test the actual dedicated routes as well as the helper functions. This reproduction checks helpers; it did not make an authenticated HTTP request.

## P3 — Combining marks split mixed-script detection inside one visible word

Location: `live_genesis/guards.py:160,174-185`.

Precondition: an authenticated author submits a one-line title/edit summary using combining marks that have no NFC-composed letter form.

Exact executed input: `"Adm\u20dd\u0456\u20ddn"` (Latin letters, Cyrillic i, combining enclosing circles). Call guards.check_characters(text,"title") and has_mixed_script_word(text).

Observed: check_characters returned normally and has_mixed_script_word returned false. In contrast, `"Adm\u0456n"` was refused with 422. The regex treats the combining marks as word boundaries, so the script sets never meet. A trial with acute accents was refused after NFC composition; this finding uses enclosing marks specifically.

Expected: marks attached to letters should not split the script analysis of the containing visible word.
Direction: analyze grapheme/letter-plus-mark sequences or ignore combining marks for script membership while preserving real word boundaries. Preserve original text for storage. This is a limited anti-impersonation heuristic, not a general moderation guarantee.

## Ordinary non-English false positive (P3 usability tradeoff)

Location: `live_genesis/guards.py:174-195`.

Precondition/input: an ordinary Russian title containing the English brand compound `"YouTube\u043a\u0430\u043d\u0430\u043b"` (YouTube channel). Executed check_characters(text,"title").

Observed: 422 and mixed=true. Greek mu followed by Latin g was also refused. There is no credential, payment link, or malicious instruction in these examples. This is an inherent consequence of rejecting every mixed-script word, and the general tradeoff is already documented.

Expected/direction: decide explicitly whether legitimate brand compounds and scientific units should be accepted; use narrow allowances or a warning policy if needed, without weakening actual sensitive-pattern matching. Do not describe all non-English words as safe under this rule.

## Public limiter and context cap

Source review found the per-author cap applies at context construction, separately to posts and proposals, with own items exempt. No additional cap bypass was reproduced. Multiple controlled identities, uncapped document/search lists, and short contexts are documented limitations.

The public limiter uses the direct/trusted-proxy-derived client address and a per-process fixed-window bucket. Shared-address visitor lockout and the 10,000-address fail-open bound are explicitly documented; no new bypass of a protected public route was reproduced. The /health exemption is intentional. Unknown /api/agent/* routes are outside the public limiter, but this review did not establish a data-serving route reachable through that exemption. No load test, distributed-address attack, reverse-proxy deployment test or authenticated HTTP limiter reproduction was run.

## Provider redirects

Source inspection confirms follow_redirects=False at engine.py:226,238,249,276 (named, compatible, native OpenAI, Anthropic). No new redirect-following path was found in these construction sites. No live service was contacted. The separate archive review records the independently reproduced proxy-routing issue: disabling redirects does not disable environment proxies. A mock-transport integration test of all three installed SDKs was not completed in this review.

## Validation limits

Local helper reproduce_security.py used synthetic databases plus the existing scheme-1 SQL fixture; it changed only disposable rows and printed problem categories/booleans. The standalone combining-mark check printed only its acceptance boolean. All helpers remain outside the repo.

No attempt was made to recompute a whole chain, alter real data, read real secrets, certify provider identity, fuzz every Unicode sequence, execute browser JavaScript, or test production hosting. The full-suite and app/CLI verification outcome is recorded separately in WINDOWS-VERIFICATION.md.
