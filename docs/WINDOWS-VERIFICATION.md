# Windows verification

Verified on GLERA against main ac8526a3cbf8b7173774f5cf2f17449c3c59cdee.
The checkout was exported through the GitHub connector; all 41 source blob hashes
matched the remote tree. Direct Git network access was unavailable.

129 tests that do not directly use TestClient passed on Windows. The full suite
stalled while Python initialized an asyncio socket pair in the restricted session.
The recorded stack is socket.accept -> _fallback_socketpair -> ProactorEventLoop
-> AnyIO TestClient. This is not a full-suite pass or evidence of an app defect.
An unrestricted test attempt was rejected by session permissions.

A combined-app startup attempt produced no startup confirmation within eight
seconds. Both HTTP apps and Start.cmd still require verification outside this
restricted environment. Existing local servers are not evidence for this revision.
Tests reused the existing local virtual environment, not a clean dependency install.

Local fonts are the upstream Google Fonts DM Sans and Space Grotesk variable TTFs,
with their SIL licenses. This removes Google Fonts network dependencies but does
not itself establish a complete Content-Security-Policy.

On the user's Windows computer, from the repository folder:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest httpx
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m live_genesis.cli --help
```

Initialize a demo database using the README, then run the two apps in separate
terminals on ports 8000 and 8001 as documented in README and observatory.py.
Start.cmd launches the combined app on port 8000; stop that server before testing
Start.cmd. Do not expose the combined agent API directly to the public Internet.
