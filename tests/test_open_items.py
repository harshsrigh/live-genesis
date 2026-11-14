"""Items left open by the two security audits, now closed. Offline and deterministic; no model, no network.

Hidden characters stripped on read, look-alike letters, the per-address request limit of the web app, and no
HTTP redirect followed by any provider client.
"""
import sys
import types
import unicodedata

import pytest
from fastapi.testclient import TestClient

from live_genesis import controls, engine, guards, web
from live_genesis.app import app


# ---- 2. Rows older than the character rule -------------------------------------------------

ZW, TAG, RLO = '​', '\U000e0041', '‮'


def test_clean_text_is_returned_unchanged_by_the_read_side():
    ordinary = 'Zoë 日本語 مرحبا \U0001F469‍\U0001F469‍\U0001F467 ✌️ 1️⃣ na‌me\nline two\ttab\r\nline three'
    assert guards.strip_forbidden_characters(ordinary, multiline=True) == ordinary
    node = {'a': [ordinary, 3, None, {'b': ordinary}], 'n': 1.5, 't': True}
    assert guards.clean_for_reading(node) == node
    for dirty in ('a\x00b', f'x{ZW}y', f'x{TAG}', 'a️', f'❤{ZW}️{ZW}️', 'cr\r alone', '', '\ud800'):
        cleaned = guards.strip_forbidden_characters(dirty, multiline=True)
        assert not guards.has_forbidden_character(cleaned, multiline=True), ascii(dirty)
    assert guards.strip_forbidden_characters('a\nb') == 'a b' and guards.strip_forbidden_characters('a\nb', multiline=True) == 'a\nb'


# ---- 3. Look-alike letters ----------------------------------------------------------------

CYR_A, CYR_O, CYR_E, CYR_P, CYR_C, CYR_K, GRK_O, GRK_A = 'а', 'о', 'е', 'р', 'с', 'к', 'ο', 'α'


def test_the_confusables_table_is_cyrillic_and_greek_to_ascii_letters():
    for code, latin in guards._CONFUSABLES.items():
        name = unicodedata.name(chr(code))
        assert name.startswith(('CYRILLIC', 'GREEK')) and len(latin) == 1 and latin.isascii() and latin.isalpha(), name
        assert unicodedata.normalize('NFKC', chr(code)) == chr(code)
    assert len(guards._CONFUSABLES) == 62


@pytest.mark.parametrize('text,reason', [
    (f'see p{CYR_A}ypal.me/garden', 'contains a payment or donation link'),
    (f'see {CYR_P}{CYR_A}у{CYR_P}{CYR_A}ӏ.{CYR_C}{CYR_O}м/garden', 'contains a payment or donation link'),   # no Latin letter at all
    (f'p{GRK_A}tre{GRK_O}n.com/someone', 'contains a payment or donation link'),
    (f'write to s{CYR_O}meone@ex{CYR_A}mple.org', 'looks like an email address'),
    (f's{CYR_K}-abcdefghijklmnopqrstuvwx', 'looks like an API key or credential'),
    (f'p{CYR_A}ssw{GRK_O}rd=hunter2', 'looks like a password or secret'),
    (f'bit{CYR_C}{CYR_O}in:bc1qexampleexampleexample', 'looks like a cryptocurrency wallet address'),
])
def test_guards_match_words_disguised_with_look_alike_letters(text, reason):
    assert guards.content_reason(text) == reason


# ---- The per-address request limit of the web app ----------------------------------------------

PUBLIC_PATHS = ('/', '/archive/index.html', '/archive/models.html', '/archive/data/answers.jsonl', '/static/archive.css', '/no-such-page')


def test_public_routes_are_limited_per_address_and_health_stays_reachable(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_PUBLIC_REQUESTS_PER_MINUTE', '5')
    clock = [1_000_000.0]
    monkeypatch.setattr(controls, 'now', lambda: clock[0])
    with TestClient(app) as client:
        assert [client.get('/archive/index.html').status_code for _ in range(5)] == [200] * 5
        for path in PUBLIC_PATHS:
            refused = client.get(path)
            assert refused.status_code == 429 and refused.json() == {'detail': web.TOO_MANY_PUBLIC}, path
            assert refused.headers['retry-after'] == '20' and refused.headers['x-content-type-options'] == 'nosniff'
            assert refused.headers['x-frame-options'] == 'DENY' and 'content-security-policy' in refused.headers
        for _ in range(20):                                   # health is never counted and never refused
            assert client.get('/health').json()['status'] == 'ok'
        assert client.post('/archive/index.html').status_code in (405, 429)
        clock[0] += 20                                        # the next minute: served again
        assert client.get('/archive/index.html').status_code == 200
    assert controls.public_limiter.limit() == 5


def test_public_limit_default_clamp_and_what_is_exempt(monkeypatch):
    assert controls.public_limiter.limit() == 300 and controls.public_limiter.max_keys == controls.LIMITER_MAX_KEYS == 10_000
    for raw, expected in (('0', 1), ('-3', 1), ('lots', 300), ('1000', 1000)):
        monkeypatch.setenv('LIVE_PUBLIC_REQUESTS_PER_MINUTE', raw)
        assert controls.public_limiter.limit() == expected
    assert not web.public_limit_applies('/health')
    for path in ('/', '/static/archive.css', '/healthz', '/health/', '/api/agent/context', '/docs', '/archive/q/Q01.html'):
        assert web.public_limit_applies(path), path


def test_public_limit_follows_trusted_proxies_and_keeps_bounded_memory(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_PUBLIC_REQUESTS_PER_MINUTE', '2')
    with TestClient(app) as client:
        # Not behind a listed proxy: X-Forwarded-For is client-supplied and ignored; everyone shares the peer address.
        codes = [client.get('/archive/index.html', headers={'X-Forwarded-For': f'203.0.113.{n}'}).status_code for n in range(4)]
        assert codes == [200, 200, 429, 429]
        # Behind a listed proxy: each forwarded client has its own allowance, and a spoofed left-hand entry does not help.
        controls.public_limiter.reset()
        monkeypatch.setenv('LIVE_TRUSTED_PROXIES', 'testclient')
        one, two = {'X-Forwarded-For': '198.51.100.1'}, {'X-Forwarded-For': '198.51.100.2'}
        assert [client.get('/archive/index.html', headers=one).status_code for _ in range(3)] == [200, 200, 429]
        assert [client.get('/archive/index.html', headers=two).status_code for _ in range(3)] == [200, 200, 429]
        assert client.get('/archive/index.html', headers={'X-Forwarded-For': '192.0.2.77, 198.51.100.1'}).status_code == 429
        assert client.get('/archive/index.html', headers={'X-Forwarded-For': '198.51.100.1, 192.0.2.77'}).status_code == 200
        # Bounded: at most max_keys addresses are remembered in a window; further ones are let through, not stored.
        controls.public_limiter.reset()
        monkeypatch.setattr(controls.public_limiter, 'max_keys', 3)
        for n in range(10):
            assert client.get('/health/x', headers={'X-Forwarded-For': f'198.51.100.{n}'}).status_code == 404
        assert controls.public_limiter.size() == 3


# ---- No provider client follows a redirect ---------------------------------------------------

def mock_transport(monkeypatch, handler):
    """Every httpx.Client built from now on answers from `handler` (no socket). Returns the constructor kwargs seen."""
    import httpx
    real, built = httpx.Client, []

    class Client(real):                      # the SDK checks isinstance(http_client, httpx.Client)
        def __init__(self, **kwargs):
            built.append(kwargs)
            super().__init__(transport=httpx.MockTransport(handler), **{**kwargs, 'trust_env': False})
    monkeypatch.setattr(httpx, 'Client', Client)
    return built


def redirecting(requests, sent_to):
    import httpx

    def handler(request):
        requests.append((request.url.host, request.url.path, request.headers.get('authorization')))
        if request.url.host == sent_to:
            return httpx.Response(307, headers={'location': 'https://elsewhere.example.invalid' + request.url.path})
        return httpx.Response(200, json={'never': 'reached'})
    return handler


def test_the_single_compatible_endpoint_follows_no_redirect(monkeypatch):
    monkeypatch.setenv('LIVE_COMPAT_BASE_URL', 'https://compat.example.invalid/private-path/v1')
    monkeypatch.setenv('OPENAI_COMPATIBLE_API_KEY', 'fake-compatible-key')
    for name in ('LIVE_COMPAT_JSON_MODE', 'LIVE_COMPAT_REASONING_EFFORT', 'LIVE_INFER_TIMEOUT_SECONDS', 'OPENAI_BASE_URL'):
        monkeypatch.delenv(name, raising=False)
    requests = []
    built = mock_transport(monkeypatch, redirecting(requests, 'compat.example.invalid'))
    with pytest.raises(Exception) as err:
        engine.infer('openai-compatible', 'model-x', 'the prompt', system='s', max_output_tokens=50)
    assert getattr(err.value, 'status_code', None) == 307
    # One request, to the configured host, with the key; nothing (no prompt, no key) went to the redirect target.
    assert requests == [('compat.example.invalid', '/private-path/v1/chat/completions', 'Bearer fake-compatible-key')]
    assert built == [{'follow_redirects': False, 'timeout': 35.0, 'trust_env': True}]   # a hosted URL may need the proxy


def test_a_loopback_base_url_never_goes_through_an_environment_proxy(monkeypatch):
    # HTTP_PROXY / ALL_PROXY with an empty NO_PROXY would otherwise route http://127.0.0.1 through the proxy.
    import httpx
    for url, trusted in (('http://127.0.0.1:11434/v1', False), ('http://localhost:8080/v1', False), ('http://[::1]:1/v1', False),
                         ('https://compat.example.invalid/v1', True), ('http://127.0.0.1.example.invalid/v1', True)):
        assert engine._trust_env(url) is trusted
    monkeypatch.setenv('HTTP_PROXY', 'http://proxy.example.invalid:3128')
    monkeypatch.setenv('ALL_PROXY', 'http://proxy.example.invalid:3128')
    monkeypatch.setenv('NO_PROXY', '')
    with httpx.Client(follow_redirects=False, trust_env=True) as proxied,             httpx.Client(follow_redirects=False, trust_env=engine._trust_env('http://127.0.0.1:11434/v1')) as direct:
        assert proxied._mounts and not direct._mounts   # the environment's proxies are mounted on the first only
    # The adapter builds its client that way for a loopback base URL.
    monkeypatch.setenv('LIVE_COMPAT_BASE_URL', 'http://127.0.0.1:11434/v1')
    monkeypatch.setenv('OPENAI_COMPATIBLE_API_KEY', 'fake-compatible-key')
    for name in ('LIVE_COMPAT_JSON_MODE', 'LIVE_COMPAT_REASONING_EFFORT', 'LIVE_INFER_TIMEOUT_SECONDS', 'OPENAI_BASE_URL'):
        monkeypatch.delenv(name, raising=False)
    built = mock_transport(monkeypatch, redirecting([], '127.0.0.1'))
    with pytest.raises(Exception):
        engine.infer('openai-compatible', 'model-x', 'the prompt', system='s', max_output_tokens=50)
    assert built == [{'follow_redirects': False, 'timeout': 35.0, 'trust_env': False}]


def test_the_native_openai_client_follows_no_redirect(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-openai-key')
    for name in ('OPENAI_BASE_URL', 'OPENAI_ORG_ID', 'OPENAI_PROJECT_ID', 'LIVE_INFER_TIMEOUT_SECONDS'):
        monkeypatch.delenv(name, raising=False)
    requests = []
    built = mock_transport(monkeypatch, redirecting(requests, 'api.openai.com'))
    with pytest.raises(Exception) as err:
        engine.infer('openai', 'model-x', 'the prompt', system='s', max_output_tokens=50)
    assert getattr(err.value, 'status_code', None) == 307
    assert requests == [('api.openai.com', '/v1/responses', 'Bearer fake-openai-key')]
    assert built == [{'follow_redirects': False, 'timeout': 35.0}]


def test_the_anthropic_client_is_built_without_redirects(monkeypatch):
    """The Anthropic SDK is optional and not installed for the tests: a stand-in module records how the client is built."""
    seen = []

    class Anthropic:
        def __init__(self, **kwargs):
            seen.append(kwargs)
            self.messages = types.SimpleNamespace(create=lambda **request: types.SimpleNamespace(
                content=[types.SimpleNamespace(type='text', text='{"action":"idle"}')], stop_reason='end_turn', model='reported',
                usage=types.SimpleNamespace(input_tokens=11, output_tokens=4)))
    monkeypatch.setitem(sys.modules, 'anthropic', types.SimpleNamespace(Anthropic=Anthropic))
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'fake-anthropic-key')
    monkeypatch.delenv('LIVE_INFER_TIMEOUT_SECONDS', raising=False)
    done = engine.infer('anthropic', 'model-x', 'the prompt', system='s', max_output_tokens=50)
    assert (done.text, done.input_tokens, done.output_tokens, done.stop) == ('{"action":"idle"}', 11, 4, 'end')
    http = seen[0].pop('http_client')
    assert http.follow_redirects is False and http.is_closed and seen == [{'timeout': 35.0, 'max_retries': 0}]
