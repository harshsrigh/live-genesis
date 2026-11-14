import pytest

import os

from live_genesis import archive, controls
from live_genesis.core import init_db


@pytest.fixture(autouse=True)
def fresh_public_limit(monkeypatch):
    """The per-address request limit lives in this process's memory and every TestClient
    request comes from one address: start each test empty and on the shipped default."""
    monkeypatch.delenv('LIVE_PUBLIC_REQUESTS_PER_MINUTE',raising=False)
    controls.public_limiter.reset()


@pytest.fixture
def isolated_db(tmp_path,monkeypatch):
    path=tmp_path/'genesis.sqlite'
    monkeypatch.setenv('LIVE_DB',str(path))
    monkeypatch.setenv('LIVE_SPEND_LIMIT_USD','10')
    monkeypatch.setenv('LIVE_INPUT_USD_PER_M','0.20')
    monkeypatch.setenv('LIVE_OUTPUT_USD_PER_M','1.20')
    monkeypatch.setenv('LIVE_PROVIDER','openai')
    # The opt-in for model calls is on so archive tests reach their mocked `infer`; no test has a real key.
    monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS','YES')
    for name in ('LIVE_PROJECT_BUDGET_USD','LIVE_OTHER_COMMITTED_USD','LIVE_TRUSTED_PROXIES'):
        monkeypatch.delenv(name,raising=False)  # shipped defaults, whatever the developer's shell or .env says
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.delenv('ANTHROPIC_API_KEY',raising=False)
    for name in ('OPENAI_COMPATIBLE_API_KEY','LIVE_COMPAT_BASE_URL','LIVE_COMPAT_JSON_MODE','LIVE_COMPAT_REASONING_EFFORT',
                 'LIVE_INFER_TIMEOUT_SECONDS'):
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
