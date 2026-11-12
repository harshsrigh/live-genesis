import pytest

from live_genesis import controls
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
    for name in ('LIVE_PROJECT_BUDGET_USD','LIVE_OTHER_COMMITTED_USD','LIVE_ACTIONS_PER_ROUND','LIVE_API_REQUESTS_PER_MINUTE'):
        monkeypatch.delenv(name,raising=False)  # shipped defaults, whatever the developer's shell or .env says
    # Wall-clock window quotas are set far above what any older test does in one minute (some
    # record dozens of actions for one agent); tests/test_controls.py lowers them with a fake clock.
    for name in ('LIVE_ACTIONS_PER_MINUTE','LIVE_ACTIONS_PER_DAY','LIVE_CALLS_PER_HOUR'):
        monkeypatch.setenv(name,'10000')
    for name in ('LIVE_PROPOSAL_DISCUSSION_ROUNDS','LIVE_PROPOSAL_VOTING_ROUNDS','LIVE_PROPOSAL_QUORUM_PERCENT'):
        monkeypatch.delenv(name,raising=False)  # proposal lifecycle defaults; tests/test_governance.py overrides them
    controls.api_limiter.reset()  # in-memory HTTP limiter is per process: start each test empty
    # CLI key commands (and `demo` on an empty database) write secrets/agent-keys.json under ROOT.
    monkeypatch.setattr('live_genesis.cli.ROOT',tmp_path)
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.delenv('ANTHROPIC_API_KEY',raising=False)
    init_db()
    return path
