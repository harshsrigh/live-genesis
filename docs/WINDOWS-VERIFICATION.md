# Windows verification — November 13, 2026

Reviewed fresh clone of main c441cdf0531d22539e44d0187ade835ccc8b32a6 on Windows, using a new Python 3.12 virtual environment under sibling work/review-nov13-runtime/.venv. The old outputs checkout/environment was not used. Installed requirements-lock.txt and requirements-anthropic.txt (exit 0). Read the five requested project documents and development instructions. No application edits, real database access, model requests, sign-ups or merges.

## Result: incomplete full verification

**1112 tests collected; the full suite is NOT passing or fully verified.**

- First full attempt: 267 passed, 845 fixture setup errors, exit 1, 286.07 seconds. This was a harness error: an unquoted backslash path in PYTEST_ADDOPTS was parsed without its backslashes. It is discarded as application validation, not presented as 845 application defects. Temporary-directory setup failed before the affected tests ran.
- Corrected full attempt used a forward-slash absolute basetemp outside the repo. It stalled in tests/test_agent_client.py::test_context_search_and_action_end_to_end while constructing the world fixture, before the first test completed. A 45-second faulthandler dump showed socket._fallback_socketpair / accept during Windows asyncio/Starlette TestClient setup. Stopped the stalled process; no complete test count exists for that attempt.
- python -m pytest --collect-only -q: exit 0, **1112 collected in 10.86 seconds**.
- Targeted rerun of all seven previous failures: **5 passed, 1 failed, 1 skipped in 13.38 seconds**, exit 1. This selection is not the full suite.

## P2 — Symlink fallback leaves the archive fixture missing

Location: tests/test_archive.py:768-777.

Precondition: Windows user cannot create symlinks. No attacker is needed.

Exact reproduction: run tests/test_archive.py::test_published_archive_tamper_guard_and_mock_are_refused on this Windows account. It unlinks archive.json, symlink_to fails, and the except block passes without restoring the original file.

Observed short traceback:

```text
tests/test_archive.py:777 -> retarget_archive(...)
tests/test_archive.py:693 -> (folder / 'archive.json').read_text(encoding='utf-8')
pathlib.py:1027 -> self.open(...)
FileNotFoundError: [Errno 2] No such file or directory: .../published/archive.json
```

Expected: skip only the unavailable symlink assertion and retain a valid export for the remaining tamper checks.
Direction: restore the known-good archive bytes when link creation fails, or isolate symlink capability checks before unlinking. The remaining tamper checks are currently not executed on this path. No test code was changed.

The five original default-encoding tests now passed:

- tests/test_expiry.py::test_agent_facing_texts_state_the_limits_from_code
- tests/test_guide.py::test_scripted_demo_never_mentions_the_figure_and_snapshot_includes_the_block
- tests/test_snapshot.py::test_export_is_self_contained_and_labelled
- tests/test_snapshot.py::test_page_refers_to_the_kit_in_head_only_and_visible_text_is_unchanged
- tests/test_snapshot.py::test_api_base_and_operator_contact_appear_only_when_given

tests/test_published.py::test_folder_holds_exactly_its_three_regular_files skipped at its final symlink-capability guard; its preceding non-link checks ran. Neither a skip nor the archive fallback failure is counted as passing.

## Free commands

Each command used python -m live_genesis.cli, LIVE_ALLOW_PAID_CALLS=NO, and LIVE_DB pointing to sibling review-nov13-runtime/windows-smoke.sqlite, outside the repo. Init output was discarded; the generated scratch key file was removed without printing or copying its contents. All other command stdout/stderr was discarded by the smoke harness; only exit codes were recorded.

| Command | Exit |
| --- | ---: |
| init | 0 |
| demo | 0 |
| demo-game | 0 |
| demo-document | 0 |
| demo-archive | 0 |
| verify-documents | 0 |
| verify-ledger | 0 |
| backup OUTSIDE_REPO/windows-backup.sqlite | 0 |
| archive-plan, without explicit rates | 1 |
| archive-plan, with rates 0.20 input / 1.20 output per million | 0 |
| archive-dry-run | 0 |
| archive-status | 0 |
| archive-check-endpoints | 0 |
| scrub-hidden (report only) | 0 |
| python -m live_genesis.snapshot OUTSIDE_REPO/snapshot | 0 |

The first archive-plan refusal was expected configuration validation: LIVE_INPUT_USD_PER_M must be a finite positive number. The rerun configured a synthetic model id and explicit rates only; no key or endpoint was used. archive-check-endpoints ran with no named endpoints configured, so this is a command smoke test, not validation of any service.

The backup command copied only the newly generated disposable fixture, as explicitly requested. No real database was opened or copied.

## App probes

Started app and observatory with Uvicorn bound to 127.0.0.1 on individually allocated free ports. Every attempted probe failed to connect (URLError), so none can be reported as 200:

/health, /, /api/overview, /archive/index.html, /archive/trending.html, /archive/q/Q01.html, /archive/t/T001.html, /archive/card/Q01.html.

The unauthenticated POST /api/agent/archive-answer also failed to connect: **authentication refusal was not verified**. Each subprocess was terminated and waited for. An additional direct combined-app startup on port 18769 also failed to become usable and was stopped. The observed TestClient socket-pair hang suggests a host/runtime limitation, but no proof attributes all server failures to that same cause.

## Commands and limits

Full attempts ran python -m pytest -q. The corrected options were supplied via PYTEST_ADDOPTS:
```text
--basetemp=C:/Users/harsh/Documents/Codex/2026-11-11/referenced-chatgpt-conversation-this-is-an/work/review-nov13-runtime/pytest-retry -p no:cacheprovider --tb=short -o faulthandler_timeout=45
```
PYTHONDONTWRITEBYTECODE=1 was set. The targeted run used a separate outside-repo basetemp and the seven exact node ids listed above. Full-suite completion and HTTP checks still need an environment where Windows asyncio sockets work. The initial incorrectly parsed test directory was a disposable harness artifact, not the real database; no database or secret is included in this PR.

Not checked: a complete passing Windows suite, successful live HTTP responses/authentication refusal, browser behavior, Start.cmd, WSL, real model endpoints, provider billing, production data or hosting. This is not a certification.

---

## Historical verification (superseded for current main)

# Windows verification — November 12, 2026

Machine: GLERA. Current reviewed main: 4554f03fc8bfbfff505c2417bbafcd9fc4f4b8df.
The current checkout was cloned from GitHub. No application source changes,
paid calls, financial actions or existing user database mutations were made.

## Full suite

With network permission granted to the executing session, the full default
`python -m pytest -q` ran, including HTTP tests: **929 passed, 7 failed**, one
Starlette/httpx deprecation warning, 378.63 seconds. All seven failures were then
rerun with short tracebacks and reproduced. This is not a passing full suite.

Five tests use default-encoding `Path.read_text()` on UTF-8 assets, causing
UnicodeDecodeError under Windows cp1252:

- tests/test_expiry.py::test_agent_facing_texts_state_the_limits_from_code
- tests/test_guide.py::test_scripted_demo_never_mentions_the_figure_and_snapshot_includes_the_block
- tests/test_snapshot.py::test_export_is_self_contained_and_labelled
- tests/test_snapshot.py::test_page_refers_to_the_kit_in_head_only_and_visible_text_is_unchanged
- tests/test_snapshot.py::test_api_base_and_operator_contact_appear_only_when_given

Two tests cannot create filesystem symlinks here (WinError 1314):

- tests/test_archive.py::test_published_archive_tamper_guard_and_mock_are_refused
- tests/test_published.py::test_folder_holds_exactly_its_three_regular_files

Recommended follow-up: explicitly decode UTF-8 assets in tests. Split symlink
checks from the remaining tamper checks so capability limitations do not hide
other assertions; run those symlink checks on a host with the needed capability.
No protections were disabled and no failures were silently skipped.

The older d5b7568 review revision also completed its full run: **330 passed,
1 failed** in 58.39 seconds, the same default-encoding snapshot test. All five
original defect reproductions passed independently. The earlier 105/116 test
selections overlap and must not be added together.

## Operation checks

At 4554f03, CLI initialization, key rotation (stored key changed, never printed),
demo, demo-game, demo-document, verify-documents, backup and snapshot export all
returned exit 0. Backup SQLite integrity check returned `ok`. Combined and
standalone read-only apps returned 200 for /health, / and /api/overview; unauthenticated
agent POST returned 401 and 405 respectively. Test servers were stopped.
The current Start.cmd attempt was skipped because port 8000 was already occupied;
the unrelated listener was left alone. The unmodified launcher was successfully
checked at d5b7568 earlier (see WINDOWS-OPERATIONS-VERIFICATION.md), not claimed
as a new successful launch at 4554f03.

## Scope and environment

The network grant removed the earlier HTTP/socket blockage. Execution remained
in the managed Windows session, not an unrestricted/elevated session. The two
remaining symlink failures demonstrate an outstanding privilege limitation.
Tests reused the existing virtual environment; no clean dependency installation
was claimed. The independent operation checks use disposable demo databases.

Current source review of ledger/governance found no additional confirmed defect;
their tests are included in the full suite, and none appears in the failure list.
The documents metadata hash-chain finding remains reproducible on current main.

Public archive verified HTTP 200 and in-browser at
https://harshsrigh.github.io/live-genesis/archive/index.html . It explicitly labels
answers and dates scripted and says no model was called. PR #3 was closed as
superseded by the main snapshot exporter and CI; no duplicate static source merged.


