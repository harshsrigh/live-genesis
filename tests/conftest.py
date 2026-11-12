import pytest

import os

from live_genesis import archive, controls, web
from live_genesis.core import init_db


@pytest.fixture
def isolated_db(tmp_path,monkeypatch):
    path=tmp_path/'genesis.sqlite'
    monkeypatch.setenv('LIVE_DB',str(path))
    monkeypatch.setenv('LIVE_ACTION_COOLDOWN_SECONDS','0')
    # Older tests re-post one fixture text; tests/test_guards.py turns the duplicate guard back on.
    monkeypatch.setenv('LIVE_DUPLICATE_GUARD','0')
    monkeypatch.setenv('LIVE_SPEND_LIMIT_USD','10')
    monkeypatch.setenv('LIVE_INPUT_USD_PER_M','0.20')
    monkeypatch.setenv('LIVE_OUTPUT_USD_PER_M','1.20')
    monkeypatch.setenv('LIVE_PROVIDER','openai')
    # Paid-call opt-in is on so engine tests reach their mocked `infer`; no test has a real key.
    monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS','YES')
    for name in ('LIVE_PROJECT_BUDGET_USD','LIVE_OTHER_COMMITTED_USD','LIVE_ACTIONS_PER_ROUND','LIVE_API_REQUESTS_PER_MINUTE','LIVE_SEARCHES_PER_MINUTE'):
        monkeypatch.delenv(name,raising=False)  # shipped defaults, whatever the developer's shell or .env says
    # Wall-clock window quotas are set far above what any older test does in one minute (some
    # record dozens of actions for one agent); tests/test_controls.py lowers them with a fake clock.
    for name in ('LIVE_ACTIONS_PER_MINUTE','LIVE_ACTIONS_PER_DAY','LIVE_CALLS_PER_HOUR'):
        monkeypatch.setenv(name,'10000')
    for name in ('LIVE_PROPOSAL_DISCUSSION_ROUNDS','LIVE_PROPOSAL_VOTING_ROUNDS','LIVE_PROPOSAL_QUORUM_PERCENT',
                 'LIVE_PROPOSAL_REPORT_ROUNDS','LIVE_GAME_IDLE_ROUNDS'):
        monkeypatch.delenv(name,raising=False)  # proposal lifecycle defaults; tests/test_governance.py overrides them
    for name in ('LIVE_AUTH_FAILURES_PER_MINUTE','LIVE_TRUSTED_PROXIES','LIVE_MAX_OUTPUT_TOKENS'):
        monkeypatch.delenv(name,raising=False)
    # The /api/overview cache is off so no test waits for it; tests/test_hardening.py turns it on.
    monkeypatch.setenv('LIVE_OVERVIEW_CACHE_SECONDS','0')
    web.clear_overview_cache()
    controls.api_limiter.reset()  # in-memory HTTP limiters are per process: start each test empty
    controls.auth_failure_limiter.reset()
    # CLI key commands (and `demo` on an empty database) write secrets/agent-keys.json under ROOT.
    monkeypatch.setattr('live_genesis.cli.ROOT',tmp_path)
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.delenv('ANTHROPIC_API_KEY',raising=False)
    for name in ('OPENAI_COMPATIBLE_API_KEY','LIVE_COMPAT_BASE_URL','LIVE_COMPAT_JSON_MODE','LIVE_COMPAT_REASONING_EFFORT',
                 'LIVE_INFER_TIMEOUT_SECONDS','LIVE_MODEL'):
        monkeypatch.delenv(name,raising=False)  # provider settings from the developer's shell or .env never leak in
    for name in [n for n in os.environ if n.startswith('LIVE_ARCHIVE_')]:
        monkeypatch.delenv(name)  # archive models and named endpoints: only what a test sets itself
    # The one HTTP request live_genesis/archive.py can make (a loopback Ollama's model list) never happens in a test
    # unless the test replaces this again with its own fake.
    def no_network(*args, **kwargs):
        raise AssertionError('archive._fetch_tags must be replaced in a test: no real request')
    monkeypatch.setattr(archive, '_fetch_tags', no_network)
    init_db()
    return path
