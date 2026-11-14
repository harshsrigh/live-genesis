"""openai-compatible provider, shared provider settings and `cli check-provider`. Offline: the SDK client
class is replaced, so no test opens an HTTP connection or holds a real key."""
import socket
import sys
from types import SimpleNamespace

import openai
import pytest

from live_genesis import cli, config, controls, engine, providers
from live_genesis.core import connect, reserve_budget

LOCAL = 'http://localhost:18999/private-path/v1'
HOSTED = 'https://models.example.invalid/v1'
POST = 'Mocked chat completion in an offline test.'
SYSTEM = 'the system line'
CALL = {'system': SYSTEM, 'max_output_tokens': 120}   # every caller names both: the adapters have no default


def compat_env(monkeypatch, base_url=LOCAL, rates=('0', '0')):
    monkeypatch.setenv('LIVE_PROVIDER', 'openai-compatible')
    monkeypatch.setenv('LIVE_COMPAT_BASE_URL', base_url)
    monkeypatch.setenv('LIVE_INPUT_USD_PER_M', rates[0])
    monkeypatch.setenv('LIVE_OUTPUT_USD_PER_M', rates[1])


def fake_sdk(monkeypatch, *, text=POST, usage=(210, 64), fail=None):
    """Replace openai.OpenAI. Returns the list of {'init': kwargs, 'create': kwargs} records."""
    seen = []
    class FakeOpenAI:
        def __init__(self, **kwargs):
            http = kwargs.pop('http_client')  # every adapter passes its own HTTP client, and it follows no redirect
            assert http.follow_redirects is False and http.timeout.read == kwargs['timeout']
            self.record = {'init': kwargs, 'create': None}
            seen.append(self.record)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
            self.responses = SimpleNamespace(create=self.create_response)
        def create(self, **kwargs):
            self.record['create'] = kwargs
            if fail:
                raise fail
            used = None if usage is None else SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=used)
        def create_response(self, **kwargs):
            self.record['create'] = kwargs
            return SimpleNamespace(output_text=text, usage=SimpleNamespace(input_tokens=usage[0], output_tokens=usage[1]))
    monkeypatch.setattr(openai, 'OpenAI', FakeOpenAI)
    return seen


def sdk_must_not_be_built(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('the SDK client must not be constructed')
    monkeypatch.setattr(openai, 'OpenAI', refuse)


def reservations():
    with connect() as cx:
        return [dict(r) for r in cx.execute('SELECT * FROM budget_reservations ORDER BY id')]


# ---- Adapter request shape ----------------------------------------------------------------

def test_adapter_request_shape_defaults(monkeypatch):
    compat_env(monkeypatch)
    monkeypatch.delenv('OPENAI_COMPATIBLE_API_KEY', raising=False)
    monkeypatch.delenv('LIVE_COMPAT_JSON_MODE', raising=False)
    monkeypatch.delenv('LIVE_COMPAT_REASONING_EFFORT', raising=False)
    monkeypatch.delenv('LIVE_INFER_TIMEOUT_SECONDS', raising=False)
    seen = fake_sdk(monkeypatch)
    done = engine.infer('openai-compatible', 'qwen3:8b', 'the prompt', **CALL)
    assert (done.text, done.input_tokens, done.output_tokens) == (POST, 210, 64)
    assert seen[0]['init'] == {'base_url': LOCAL, 'api_key': providers.LOCAL_PLACEHOLDER_KEY, 'timeout': 35.0, 'max_retries': 0}
    assert seen[0]['create'] == {
        'model': 'qwen3:8b', 'max_tokens': 120, 'response_format': {'type': 'json_object'},
        'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': 'the prompt'}]}


def test_adapter_json_mode_off_reasoning_effort_timeout_and_key(monkeypatch):
    compat_env(monkeypatch, base_url=HOSTED)
    monkeypatch.setenv('OPENAI_COMPATIBLE_API_KEY', 'fake-compatible-key')
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-openai-key-must-not-be-sent-here')
    monkeypatch.setenv('LIVE_COMPAT_JSON_MODE', 'off')
    monkeypatch.setenv('LIVE_COMPAT_REASONING_EFFORT', 'LOW')
    monkeypatch.setenv('LIVE_INFER_TIMEOUT_SECONDS', '240')
    seen = fake_sdk(monkeypatch)
    engine.infer('openai-compatible', 'vendor/model-1', 'p', system='another system line', max_output_tokens=300)
    assert seen[0]['init'] == {'base_url': HOSTED, 'api_key': 'fake-compatible-key', 'timeout': 240.0, 'max_retries': 0}
    sent = seen[0]['create']
    assert 'response_format' not in sent and sent['reasoning_effort'] == 'low' and sent['max_tokens'] == 300
    assert sent['messages'][0] == {'role': 'system', 'content': 'another system line'}


def test_timeout_applies_to_the_openai_adapter_and_is_clamped(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    seen = fake_sdk(monkeypatch)
    for raw, expected in (('', 35.0), ('1', 5.0), ('9999', 600.0), ('120', 120.0)):
        monkeypatch.setenv('LIVE_INFER_TIMEOUT_SECONDS', raw)
        engine.infer('openai', 'm', 'p', **CALL)
        assert seen[-1]['init'] == {'timeout': expected, 'max_retries': 0}
    for bad in ('soon', 'nan', 'inf'):
        monkeypatch.setenv('LIVE_INFER_TIMEOUT_SECONDS', bad)
        with pytest.raises(engine.ProviderNotReady, match='LIVE_INFER_TIMEOUT_SECONDS'):
            engine.infer('openai', 'm', 'p', **CALL)
    assert len(seen) == 4


def test_missing_or_invalid_usage_is_never_invented(monkeypatch):
    compat_env(monkeypatch)
    monkeypatch.delenv('LIVE_INFER_TIMEOUT_SECONDS', raising=False)
    fake_sdk(monkeypatch, usage=None)
    with pytest.raises(RuntimeError, match='usage missing') as err:
        engine.infer('openai-compatible', 'm', 'p', **CALL)
    assert not isinstance(err.value, engine.ProviderNotReady)  # the request was sent: callers keep the hold
    fake_sdk(monkeypatch, usage=(None, 5))
    with pytest.raises(RuntimeError, match='usage missing'):
        engine.infer('openai-compatible', 'm', 'p', **CALL)
    fake_sdk(monkeypatch, text=None)  # e.g. a reasoning model that spent the whole budget thinking
    assert engine.infer('openai-compatible', 'm', 'p', **CALL).text == ''


def test_setup_problems_are_not_ready_and_build_no_client(monkeypatch):
    sdk_must_not_be_built(monkeypatch)
    monkeypatch.delenv('LIVE_INFER_TIMEOUT_SECONDS', raising=False)
    compat_env(monkeypatch, base_url='')
    with pytest.raises(engine.ProviderNotReady, match='LIVE_COMPAT_BASE_URL is required'):
        engine.infer('openai-compatible', 'm', 'p', **CALL)
    compat_env(monkeypatch, base_url=HOSTED)
    monkeypatch.delenv('OPENAI_COMPATIBLE_API_KEY', raising=False)
    with pytest.raises(engine.ProviderNotReady, match='OPENAI_COMPATIBLE_API_KEY missing'):
        engine.infer('openai-compatible', 'm', 'p', **CALL)
    with pytest.raises(engine.ProviderNotReady, match='openai, anthropic or openai-compatible'):
        engine.infer('other', 'm', 'p', **CALL)


# ---- Base URL rules -----------------------------------------------------------------------

@pytest.mark.parametrize('host,loopback', [
    ('localhost', True), ('LOCALHOST', True), ('127.0.0.1', True), ('127.8.9.10', True), ('::1', True),
    ('128.0.0.1', False), ('0.0.0.0', False), ('192.168.1.5', False), ('::ffff:127.0.0.1', False), ('::2', False),
    ('localhost.example.com', False), ('my.localhost', False), ('127.1', False), ('host.docker.internal', False),
    ('', False), (None, False)])
def test_loopback_rule(host, loopback):
    assert providers.is_loopback_host(host) is loopback


def test_https_required_unless_loopback(monkeypatch):
    monkeypatch.delenv('LIVE_COMPAT_JSON_MODE', raising=False)
    monkeypatch.delenv('LIVE_COMPAT_REASONING_EFFORT', raising=False)
    for url in ('http://localhost:11434/v1', 'http://127.0.0.1:1234/v1', 'http://[::1]:8080/v1', 'https://localhost/v1', HOSTED):
        compat_env(monkeypatch, base_url=url)
        assert providers.compat_settings().base_url == url
    for url in ('http://models.example.invalid/v1', 'http://192.168.1.5:11434/v1', 'http://host.docker.internal:11434/v1',
                'http://localhost.example.com/v1', 'http://127.0.0.1.example.com/v1'):
        compat_env(monkeypatch, base_url=url)
        with pytest.raises(providers.ProviderConfigError, match='must use https') as err:
            providers.compat_settings()
        assert 'example' not in str(err.value) and '192.168' not in str(err.value)  # the URL is never echoed
    for url, why in (('ftp://localhost/v1', 'http'), ('localhost:11434/v1', 'http'), ('https:///v1', 'host'),
                     ('https://user:secret-in-url@models.example.invalid/v1', 'user name or password'),
                     ('https://models.example.invalid/v1?key=secret-in-url', 'query'), ('http://localhost:notaport/v1', 'valid URL')):
        compat_env(monkeypatch, base_url=url)
        with pytest.raises(providers.ProviderConfigError, match=why) as err:
            providers.compat_settings()
        assert 'secret-in-url' not in str(err.value)


def test_switch_and_effort_validation(monkeypatch):
    compat_env(monkeypatch)
    monkeypatch.delenv('LIVE_COMPAT_JSON_MODE', raising=False)
    monkeypatch.delenv('LIVE_COMPAT_REASONING_EFFORT', raising=False)
    assert providers.compat_settings().json_mode is True and providers.compat_settings().reasoning_effort is None
    monkeypatch.setenv('LIVE_COMPAT_JSON_MODE', 'maybe')
    with pytest.raises(providers.ProviderConfigError, match='LIVE_COMPAT_JSON_MODE'):
        providers.compat_settings()
    monkeypatch.setenv('LIVE_COMPAT_JSON_MODE', '0')
    monkeypatch.setenv('LIVE_COMPAT_REASONING_EFFORT', 'extreme')
    with pytest.raises(providers.ProviderConfigError, match='LIVE_COMPAT_REASONING_EFFORT'):
        providers.compat_settings()


# ---- Rates --------------------------------------------------------------------------------

def test_zero_rates_only_for_openai_compatible_on_loopback(isolated_db, monkeypatch):
    compat_env(monkeypatch)
    budget = controls.budget_config()
    assert budget['input_rate'] == 0 and budget['output_rate'] == 0
    # Hosted compatible endpoint: a "free tier" still needs positive rates.
    monkeypatch.setenv('LIVE_COMPAT_BASE_URL', HOSTED)
    with pytest.raises(controls.ConfigError, match='LIVE_INPUT_USD_PER_M'):
        controls.budget_config()
    monkeypatch.setenv('LIVE_INPUT_USD_PER_M', '0.05')
    with pytest.raises(controls.ConfigError, match='LIVE_OUTPUT_USD_PER_M'):
        controls.budget_config()
    monkeypatch.setenv('LIVE_OUTPUT_USD_PER_M', '0.05')
    assert controls.budget_config()['output_rate'] == 0.05
    # An unusable or plain-http remote base URL never unlocks zero rates.
    monkeypatch.setenv('LIVE_INPUT_USD_PER_M', '0')
    for url in ('', 'http://192.168.1.5:11434/v1', 'not a url'):
        monkeypatch.setenv('LIVE_COMPAT_BASE_URL', url)
        with pytest.raises(controls.ConfigError):
            controls.budget_config()
    # Paid providers stay fail-closed even with a loopback compatible URL lying in the settings.
    monkeypatch.setenv('LIVE_COMPAT_BASE_URL', LOCAL)
    for provider in ('openai', 'anthropic', 'other'):
        monkeypatch.setenv('LIVE_PROVIDER', provider)
        with pytest.raises(controls.ConfigError):
            controls.budget_config()
    # Negative and non-numeric rates are refused everywhere.
    monkeypatch.setenv('LIVE_PROVIDER', 'openai-compatible')
    for bad in ('-1', 'nan', 'free'):
        monkeypatch.setenv('LIVE_INPUT_USD_PER_M', bad)
        with pytest.raises(controls.ConfigError):
            controls.budget_config()


def test_zero_hold_needs_zero_stored_rates(isolated_db):
    with pytest.raises(ValueError, match='positive'):
        reserve_budget(None, 'm', 0, 10, provider='openai', input_rate=0.2, output_rate=1.2)
    with pytest.raises(ValueError, match='positive'):
        reserve_budget(None, 'm', 0, 10, provider='openai')  # rates from settings (0.20 / 1.20)
    with pytest.raises(ValueError, match='positive'):
        reserve_budget(None, 'm', -0.01, 10, provider='openai-compatible', input_rate=0, output_rate=0)
    assert reserve_budget(None, 'm', 0, 10, provider='openai-compatible', input_rate=0, output_rate=0) > 0


# ---- Secrets ------------------------------------------------------------------------------

def test_compatible_key_is_a_provider_secret_not_a_setting():
    assert 'OPENAI_COMPATIBLE_API_KEY' in config.PROVIDER_SECRET_KEYS
    assert not providers.KEY_NAMES['openai-compatible'].startswith(config.SETTINGS_PREFIX)


# ---- cli check-provider -------------------------------------------------------------------

def run_check(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['live_genesis.cli', 'check-provider'])
    monkeypatch.setattr(providers.config, 'load_provider_credentials', lambda: [])  # never read a developer's .env
    code = 0
    try:
        cli.main()
    except SystemExit as stop:
        code = stop.code
    return code, capsys.readouterr().out


def test_check_provider_local_reports_and_probes_only_loopback(isolated_db, monkeypatch, capsys):
    sdk_must_not_be_built(monkeypatch)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        compat_env(monkeypatch, base_url=f'http://127.0.0.1:{port}/private-path/v1')
        monkeypatch.setenv('LIVE_INFER_TIMEOUT_SECONDS', '300')
        code, out = run_check(monkeypatch, capsys)
    finally:
        listener.close()
    assert code == 0
    assert 'Provider: openai-compatible' in out and 'LIVE_ARCHIVE_MODELS' in out and 'Timeout per call: 300 s' in out
    assert f'Base URL host: 127.0.0.1  port {port}' in out and 'private-path' not in out
    assert 'Key set (OPENAI_COMPATIBLE_API_KEY): no' in out and 'input zero, output zero' in out
    assert 'Zero rates allowed here: yes' in out and f'TCP connection to 127.0.0.1:{port}: opens' in out
    assert 'no model call' in out
    code, out = run_check(monkeypatch, capsys)  # listener closed
    assert code == 0 and 'REFUSED or no answer' in out
    assert reservations() == []


def test_check_provider_hosted_prints_no_key_and_opens_no_connection(isolated_db, monkeypatch, capsys):
    sdk_must_not_be_built(monkeypatch)
    def no_sockets(*args, **kwargs):
        raise AssertionError('check-provider must not contact a non-loopback host')
    monkeypatch.setattr(providers.socket, 'getaddrinfo', no_sockets)
    monkeypatch.setattr(providers.socket, 'socket', no_sockets)
    compat_env(monkeypatch, base_url='https://models.example.invalid/private-path/v1', rates=('0', '0.10'))
    monkeypatch.setenv('OPENAI_COMPATIBLE_API_KEY', 'fake-compatible-key-VALUE')
    code, out = run_check(monkeypatch, capsys)
    assert code == 1  # zero input rate on a hosted endpoint
    assert 'fake-compatible-key-VALUE' not in out and 'private-path' not in out
    assert 'Key set (OPENAI_COMPATIBLE_API_KEY): yes' in out and 'Base URL host: models.example.invalid' in out
    assert 'Zero rates allowed here: no' in out and 'not tried' in out and 'PROBLEM: LIVE_INPUT_USD_PER_M' in out
    monkeypatch.setenv('LIVE_INPUT_USD_PER_M', '0.05')
    code, out = run_check(monkeypatch, capsys)
    assert code == 0 and 'PROBLEM' not in out and 'fake-compatible-key-VALUE' not in out
    monkeypatch.delenv('OPENAI_COMPATIBLE_API_KEY')
    code, out = run_check(monkeypatch, capsys)
    assert code == 1 and 'PROBLEM: OPENAI_COMPATIBLE_API_KEY missing' in out


def test_loopback_probe_skips_a_non_loopback_resolution(monkeypatch):
    """If `localhost` resolved to an outside address on some machine, nothing would be contacted."""
    monkeypatch.setattr(providers.socket, 'getaddrinfo',
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('203.0.113.9', 11434))])
    def no_socket(*args, **kwargs):
        raise AssertionError('must not connect to a non-loopback address')
    monkeypatch.setattr(providers.socket, 'socket', no_socket)
    assert providers._loopback_port_open('localhost', 11434) is False


def test_check_provider_paid_providers_and_unknown(isolated_db, monkeypatch, capsys):
    sdk_must_not_be_built(monkeypatch)
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-openai-key-VALUE')
    code, out = run_check(monkeypatch, capsys)
    assert code == 0 and 'Provider: openai' in out and 'Key set (OPENAI_API_KEY): yes' in out
    assert 'fake-openai-key-VALUE' not in out and 'input positive, output positive' in out and 'Base URL' not in out
    monkeypatch.setenv('LIVE_PROVIDER', 'other')
    code, out = run_check(monkeypatch, capsys)
    assert code == 1 and 'PROBLEM: LIVE_PROVIDER must be openai, anthropic or openai-compatible' in out
