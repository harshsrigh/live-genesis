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

