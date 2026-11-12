import pytest

from live_genesis.core import init_db


@pytest.fixture
def isolated_db(tmp_path,monkeypatch):
    path=tmp_path/'genesis.sqlite'
    monkeypatch.setenv('LIVE_DB',str(path))
    monkeypatch.setenv('LIVE_ACTION_COOLDOWN_SECONDS','0')
    monkeypatch.setenv('LIVE_SPEND_LIMIT_USD','10')
    monkeypatch.setenv('LIVE_INPUT_USD_PER_M','0.20')
    monkeypatch.setenv('LIVE_OUTPUT_USD_PER_M','1.20')
    monkeypatch.setenv('LIVE_PROVIDER','openai')
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.delenv('ANTHROPIC_API_KEY',raising=False)
    init_db()
    return path
