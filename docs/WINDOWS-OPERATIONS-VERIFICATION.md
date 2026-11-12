# Windows operations verification

Verified November 11, 2026 (America/Phoenix), on Windows GLERA, main
`d5b7568ebddac29bbcb4fc10d9f719ac33c228c4`.

These checks ran in an isolated copy under `work/windows-ops-main`, using a fresh
demo database. No existing user database was touched, no paid round was run, no
provider keys were loaded, and no credentials were printed. Application source
was not edited. The source copy came from the pinned GitHub export.

## Results

| Check | Observed result |
| --- | --- |
| `cli init` | Exit 0; ten agents initialized; keys saved only in private scratch folder |
| `cli rotate-key 1` | Exit 0; agent's stored credential changed; no key printed |
| `cli demo`, `demo-game`, `demo-document` | Exit 0; scripted records only |
| `cli verify-documents` | Exit 0; document history verified |
| `cli backup <new-file>` | Exit 0; backup opened with ten agents; SQLite integrity check `ok` |
| `python -m live_genesis.snapshot <new-directory>` | Exit 0; demo-only static snapshot written |
| Combined app on port 8023 | `/health`, `/`, `/api/overview`: HTTP 200; overview mode DEMO; unauthenticated POST to agent action: 401 |
| Read-only app on port 8024 | Same GET checks: HTTP 200; agent-action POST: 405 |
| Unmodified `Start.cmd`, port 8000 | Printed launcher instructions; started combined app; same GET checks 200 and unauthenticated POST 401 |
| Stop checks | All three test ports subsequently refused connections (Windows 10061); test process IDs no longer present |

## Environment and limits

The first server attempts, before this child session's network grant, did not
start within the bounded timeout. After `request_permissions` granted network
access for the turn, all three server checks above passed. This was still the
managed Windows session, **not an unrestricted/elevated session**. The successful
HTTP checks remove the earlier socket blocker for these operations; they do not
establish unrestricted-host deployment readiness.

Commands used the existing `outputs/live-genesis/.venv/Scripts/python.exe` and
its installed dependencies. The launcher test created a small `.venv` with
`venv --without-pip` and set `PYTHONPATH` to the existing environment's
site-packages. Therefore this verifies `Start.cmd` launch behavior but is **not a
clean dependency-install verification**. Full pytest results are reported by the
separate fix-verification task; they are not inferred from these smoke checks.

The standalone observatory returned read-only responses. `Start.cmd` starts the
combined local app, not the standalone observatory; its public UI is read-only,
but its authenticated agent API remains present. Do not expose that combined
development app as the public website.

All servers were stopped after the checks. The scripted snapshot and backup are
scratch artifacts, not a public deployment. The backup and private key file must
not be committed or included in public archives.

## Reproduction

From a current checkout with a configured local virtual environment, use a fresh
absolute `LIVE_DB` and keep `LIVE_ALLOW_PAID_CALLS=NO`:

```powershell
.\.venv\Scripts\python.exe -m live_genesis.cli init
.\.venv\Scripts\python.exe -m live_genesis.cli rotate-key 1
.\.venv\Scripts\python.exe -m live_genesis.cli demo
.\.venv\Scripts\python.exe -m live_genesis.cli demo-game
.\.venv\Scripts\python.exe -m live_genesis.cli demo-document
.\.venv\Scripts\python.exe -m live_genesis.cli verify-documents
.\.venv\Scripts\python.exe -m live_genesis.cli backup backup.sqlite
.\.venv\Scripts\python.exe -m live_genesis.snapshot site
```

The output directories/files must be new. Run the two services separately:

```powershell
.\.venv\Scripts\python.exe -m uvicorn live_genesis.app:app --host 127.0.0.1 --port 8023
.\.venv\Scripts\python.exe -m uvicorn live_genesis.observatory:app --host 127.0.0.1 --port 8024
```

Stop the combined app before testing `Start.cmd`, which uses port 8000.
Check `/health`, `/`, and `/api/overview` in the browser or an HTTP client.

Evidence: scratch scripts `work/windows_ops_verify.py` and
`work/windows_ops_servers_retry.py`; records `work/windows-ops-results.json`
and `work/windows-ops-retry.json`. The first JSON records pre-grant failures;
the retry JSON records passing HTTP checks. Immediate process polling in those
records can say `stopped: false`; subsequent explicit PID and closed-port checks
confirmed termination of every test server.

