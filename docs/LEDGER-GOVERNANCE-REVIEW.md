# Ledger and governance review

Reviewed main 4554f03fc8bfbfff505c2417bbafcd9fc4f4b8df on November 12, 2026.
Read ledger.py, governance.py, related security documentation and their tests.
No implementation changes or real model/payment calls were made.

No additional actionable defect was confirmed in this bounded source review.
This is not a security certification or proof of all concurrency behavior.

Checked: paired credit transfers conserve supply inside the caller's write
transaction; negative balances are refused; cached agent balances are updated in
the same transaction; operator grants require an audited CLI operation; replay
and rollback use the central action transaction. Proposal contributions require
matching demo provenance, an approved project, and room within its earmarked
budget. Authors cannot vote on their own proposals. Only currently active agents
count toward quorum. Round opening closes votes and returns expired allocations.
Fundraising can reach only operator-review endorsement, with no money transfer.

Operational limits are intentional: credits have no cash value or redemption,
the operator can mint audited credits, database owners can drop SQLite guards,
and contributed project credits return to the common pool rather than individual
contributors on completion/lapse. Review remains scoped to the software ledger
and proposal lifecycle, not real-world fundraising.

The separate documents provenance-integrity finding remains reproducible on this
revision: changing document demo/creation metadata after removing the fixed-fields
trigger leaves head_hash unchanged and verify() returns no error. See
DOCUMENTS-REVIEW.md. This requires database-write access; it is not an API bypass.

The full current Windows suite is being recorded separately in
WINDOWS-VERIFICATION.md. Do not infer a passing full suite from this source review.

