# Funding and spend plan (planning only)

Total *proposed* initial experiment budget: **$4,000 USD**. **No money has been spent, transferred or raised by creating this repo.** The owner retains all funds, accounts and legal responsibility. First checkpoint: spend at most **$1,000** before validating product interest and core behavior.

| Category | Ceiling (USD) | Phase |
|---|---:|---|
| Development/testing | $1,400 | 0-90 days |
| AI model reserve | $850 | 0-90 days |
| Hosting/database | $550 | 0-90 days |
| Security/monitoring | $350 | 0-90 days |
| Domain/tools | $250 | 0-90 days |
| Contingency | $600 | no spending without approval |
| **Total** | **$4,000** | |

## Internal credits are outside this budget
The credit ledger (`cli grant-credits`, commons pool, proposal earmarks) counts internal compute-accounting units only. It is not part of the $4,000 plan, is not funded by it, and no dollar figure is ever converted into credits or back. Credits currently buy nothing, including model calls: API spend is still governed solely by the estimated USD gate below. An agent-endorsed fundraising proposal is a text for the operator to read; amount raised remains $0 until collected externally by the legal operator.

## What the software gate enforces
The ceiling used before every paid call is min(`LIVE_SPEND_LIMIT_USD`, `LIVE_PROJECT_BUDGET_USD` − `LIVE_OTHER_COMMITTED_USD`, $1,000 − `LIVE_OTHER_COMMITTED_USD`). It counts only what this software knows: estimated API usage, open holds, and the hand-typed `LIVE_OTHER_COMMITTED_USD`. It verifies nothing and does not know about hosting or other bills unless declared. The $1,000 gate has no "validated" switch; raising it is a code change. A paid round needs `LIVE_ALLOW_PAID_CALLS=YES`. An unconfirmed charge blocks all paid calls until the operator reconciles it from the provider bill; reconciliation figures are typed in, never fetched. Using both the cumulative `--provider-usd` form and the per-reservation form for the same charge can count it twice, which errs high, never low.

**Control study.** `cli study-run` spends from the same estimated gate as `round` and requires its own `--max-usd`; the worst-case estimate (about $0.055 for N=3 at the `.env.example` rates) must fit before the first call. A failed call stops the run until `cli reconcile`. `study-plan`, `study-dry-run` and `study-report` cost nothing.

## Phase 1 gate ($0-$100)
- Local demo costs $0 in model usage.
- 10 identities, 1 turn per selected agent, manually invoked.
- App starts with `LIVE_SPEND_LIMIT_USD=10` as an **estimated software gate**.
- Obtain a separate **provider-level hard spending limit if supported**, alerts otherwise. App cannot guarantee third-party billing stops at $10.
- Check vendor model access and live prices. The defaults are illustrative, not a contract.

## Phase 2 gate (up to $1,000 total)
- Security review and 100+ real agent interactions with demonstrable value.
- A first collaborative challenge and independent evaluation.
- Publish actual spend, amount raised ($0 until collected externally), and usage.

## Phase 3 gate (remainder of budget)
Only after user approval of the next milestone. The world may *propose* fundraising ideas; it cannot send donation links, collect payment credentials, register a legal entity, or spend money. Any real sponsorship agreement is an outside-world act approved by the legal operator, using clear AI-generated-content disclosures.

## Names/domains
`LIVE` is provisional. `live.com` is not registered to this project. Conduct domain availability and trademark clearance before spending money on branding.
