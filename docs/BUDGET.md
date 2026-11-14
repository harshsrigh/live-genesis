# Funding and spend plan (planning only)

Total *proposed* initial budget: **$4,000 USD**. **No money has been spent, transferred or raised by creating this repo.** The owner retains all funds, accounts and legal responsibility. First checkpoint: spend at most **$1,000** before validating product interest.

The table was drawn up when the project was an agent society (removed on 14 November 2026). It has not been redrawn for Answers Over Time; treat the categories as placeholders and the two totals as the limits the software enforces.

| Category | Ceiling (USD) | Phase |
|---|---:|---|
| Development/testing | $1,400 | 0-90 days |
| AI model reserve | $850 | 0-90 days |
| Hosting/database | $550 | 0-90 days |
| Security/monitoring | $350 | 0-90 days |
| Domain/tools | $250 | 0-90 days |
| Contingency | $600 | no spending without approval |
| **Total** | **$4,000** | |

## What the software gate enforces
The ceiling used before every paid call is min(`LIVE_SPEND_LIMIT_USD`, `LIVE_PROJECT_BUDGET_USD` − `LIVE_OTHER_COMMITTED_USD`, $1,000 − `LIVE_OTHER_COMMITTED_USD`). It counts only what this software knows: estimated API usage, open holds, and the hand-typed `LIVE_OTHER_COMMITTED_USD`. It verifies nothing and does not know about hosting or other bills unless declared. The $1,000 gate has no "validated" switch; raising it is a code change. `LIVE_PROJECT_BUDGET_USD` must stay below 4000.

The only command that can spend is `cli archive-run`. It needs the owner's explicit instruction, `LIVE_ALLOW_PAID_CALLS=YES` and its own `--max-usd`, which the worst-case estimate must fit under before the first call. An unconfirmed charge blocks all paid calls until the owner reconciles it from the provider bill; reconciliation figures are typed in, never fetched. Using both the cumulative `--provider-usd` form and the per-reservation form for the same charge can count it twice, which errs high, never low.

The chat-app track costs nothing in model usage: the owner asks the apps by hand.

## What it cannot do
The app cannot guarantee that a provider's billing stops at any figure: get a provider-level hard spending limit where one exists, alerts otherwise. Check vendor model access and live prices; the default rates are illustrative, not a contract.

Nothing in this repository requests, collects or moves money, and nothing links to a donation or payment page. Any sponsorship or paid service is an outside-world act decided by the owner.

## Names/domains
The name is open (see `docs/ROADMAP.md`). Do a domain and trademark check before spending money on branding.
