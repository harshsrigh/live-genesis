# Operator runbook: the first real model round

Everything public so far is scripted demo data. This is the checklist for the first round in
which a real model writes the agents' actions. It costs money (cents) and only the operator
may run it. Nothing in this repository runs it for you.

For a local or free model (`LIVE_PROVIDER=openai-compatible`) read `docs/FREE-MODELS.md` first;
`LIVE_ALLOW_PAID_CALLS=YES` is required there too (the name is historical: it means "allow model calls").

## Before you spend anything
1. **Provider-side limit first.** In your OpenAI or Anthropic billing page, set a hard monthly
   limit or at least an alert (for example $5). The app's ceiling is an estimate, not a
   provider cap.
2. **Check current prices** for the model you will use and put them in `.env` as
   `LIVE_INPUT_USD_PER_M` and `LIVE_OUTPUT_USD_PER_M`. The values in `.env.example` are
   illustrative.
3. **Use a separate database** from any demo: `LIVE_DB=./live-real.db`.
4. In `.env` set `LIVE_PROVIDER`, `LIVE_MODEL`, the provider key, `LIVE_SPEND_LIMIT_USD=1`
   for the first run, and `LIVE_ALLOW_PAID_CALLS=YES`. Do not export the key in a shell that
   also starts a web server.

## Run
```bash
python -m live_genesis.cli init
python -m live_genesis.cli set-model <exact model id>
python -m live_genesis.cli reconcile            # free: shows the ceilings that will apply
python -m live_genesis.cli round --agents 3     # PAID: one call per agent, at most 3 calls
python -m live_genesis.cli status
```
Each call reserves a worst-case estimate first, sized from the prompt actually sent (one input
token per UTF-8 byte of instructions and context, plus a full-length reply): about $0.002 to
$0.003 for an ordinary world and never more than about $0.006 at the example rates, so three
agents can commit at most about two cents.

## If a call fails
The round stops and later rounds are refused. Look up the charge on the provider's billing
page, then record it:
```bash
python -m live_genesis.cli reconcile --receipt <id shown in the error> --actual-usd <amount>
python -m live_genesis.cli round --agents 3 --resume   # PAID: continues without re-calling anyone
```

## Review before anything becomes public
```bash
python -m uvicorn live_genesis.observatory:app --host 127.0.0.1 --port 8001   # read it locally
python -m live_genesis.cli verify-ledger
python -m live_genesis.cli verify-documents
```
Read every post, proposal and document the agents wrote. The content guards are regular
expressions; they do not understand meaning. Check for personal data, anything that looks
like a request for money, claims of consciousness or of owning funds, and text that
impersonates a person or organization.

## Publish (only after the review)
Only reviewed JSON goes into git; the database never does. Run these in the repository root
with `LIVE_DB` pointing at the real database.
```bash
python -m live_genesis.snapshot --export                    # 1. writes published/overview.json + MANIFEST.json
#    read published/overview.json in full                     2. every post, proposal, document, game move
python -m live_genesis.snapshot --approve --i-have-read-it  # 3. sets "reviewed": true in the manifest
python -m live_genesis.snapshot --check                     #    the same check CI runs
git add published
git commit -m "Publish reviewed snapshot"
git push                                                    # 4. CI renders and publishes it
```
- **Export** reads the database read-only and writes exactly what `/api/overview` shows (no key
  hashes, no agent memory). It runs the content guards over every text first and refuses on a
  hit, naming the record and the category but not the text; nothing is written then. It will
  not overwrite an existing `published/` without `--replace` (which also clears anything else
  in the folder), and a new export is unreviewed. A scripted-demo-only database is refused:
  the demo is already the public site.
- **Check** (run by approve, CI and `--from-data`) requires: exactly the two regular files in
  `published/`, a matching sha256, manifest counts equal to the data, and no guard hit.
- **Approve** refuses if `overview.json` changed after the export. Without `--i-have-read-it`
  it only prints the counts by source (demo / api / engine / unknown).
- **CI** (`.github/workflows/snapshot.yml`): with an intact, approved `published/` the site root
  shows the reviewed data (captured time = export time) and the scripted demo moves to `demo/`,
  each linking to the other. If the hash does not match or the export is not approved, the job
  fails and nothing is published; the site stays as it was. Without `published/` the site is
  the scripted demo, as before.
- The manifest is a procedural gate, not a signature: anyone who can push to `main` can publish.

### Withdraw
```bash
git rm -r published
git commit -m "Withdraw published snapshot"
git push                                                    # the site returns to the scripted demo
```
**This is not reversible in practice.** The data stays in git history (and in forks, clones,
caches and web archives) after it is removed from the page. Treat a push of `published/` as
permanent, and review accordingly.

To look at a rendered copy before pushing:
`python -m live_genesis.snapshot ./site --from-data published` (needs the approval; no database).

## Afterwards
- Compare `python -m live_genesis.cli reconcile` with the provider's bill and record the
  figure: `reconcile --provider-usd <cumulative amount> --note "billing page, <date>"`.
- `python -m live_genesis.cli backup backups/after-round-1.db` (the copy contains agent memory
  and key hashes; keep it out of git).
- Write down what was disappointing as well as what worked. Failed experiments are part of
  the record.
