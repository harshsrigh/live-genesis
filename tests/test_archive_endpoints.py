"""Answers Over Time on several services in one run: named endpoints, the archive model-id rule,
per-endpoint pricing, pinned sampling, what a call records, and failure isolation.

Offline. The SDK client class (`openai.OpenAI`) is replaced, so the real `engine.infer` runs and a
run is stored as a real one, while no test opens a connection to anything but, at most, a loopback
port that is closed. No test holds a real key: the key values below are sentinels that are searched
for in every table and every printed line."""
import json
import sqlite3
import sys
from types import SimpleNamespace

import openai
import pytest

from live_genesis import archive, archive_pages, cli, config, controls, core, engine, providers, snapshot

from test_archive import REPO, question_file, ready, rows, run_cli, trending_file  # noqa: F401

KEY_CF = 'SENTINEL-KEY-VALUE-CF-7f3a'
KEY_GOOG = 'SENTINEL-KEY-VALUE-GOOG-91bc'
URLS = {'ollama': 'http://localhost:11999/private-local-path/v1',
        'cf': 'https://cf.example.invalid/private-cf-path/v1',
        'goog': 'https://goog.example.invalid/private-goog-path/v1'}
SECRETS = (KEY_CF, KEY_GOOG, 'private-local-path', 'private-cf-path', 'private-goog-path', 'example.invalid', 'localhost:11999')
THREE = 'gemma=ollama:gemma3:4b,llama=cf:@cf/meta/llama-3.3-70b-instruct-fp8-fast,gem=goog:google/gemma-4-31b-it:free'
OLD_SCHEMA = REPO / 'tests' / 'fixtures' / 'archive_schema_v2.sql'


def var(name, suffix):
    return config.endpoint_variable(name, suffix)


def three_endpoints(monkeypatch, models=THREE):
    """A local Ollama plus two hosted free tiers, each with its own base URL and key variable."""
    monkeypatch.setenv('LIVE_ARCHIVE_ENDPOINTS', 'ollama,cf,goog')
    for name, url in URLS.items():
        monkeypatch.setenv(var(name, 'BASE_URL'), url)
    for name, key_var, key in (('cf', 'CF_TEST_API_KEY', KEY_CF), ('goog', 'GOOG_TEST_API_KEY', KEY_GOOG)):
        monkeypatch.setenv(var(name, 'KEY_VAR'), key_var)
        monkeypatch.setenv(var(name, 'FREE_TIER'), 'YES')
        monkeypatch.setenv(key_var, key)
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', models)


class Status(Exception):
    """Shaped like an SDK status error: a status code, and text that must never be stored or printed."""
    def __init__(self, status_code):
        super().__init__(f'response body with {KEY_CF} and https://cf.example.invalid/private-cf-path/v1')
        self.status_code = status_code


class ReadTimeout(Exception):
    pass


def reply(text='Seven.', usage=(40, 12), model=None, fingerprint=None, finish='stop'):
    used = None if usage is None else SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1])
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=finish)], usage=used,
                           model=model, system_fingerprint=fingerprint)


def fake_sdk(monkeypatch, script=None):
    """Replace openai.OpenAI. `script(base_url, request, n)` returns a response or an exception to raise; `n` counts the
    calls to that base URL. Returns the list of calls: base_url, api_key, redirects, retries, request."""
    seen = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
            self.responses = SimpleNamespace(create=self.create_response)

        def create(self, **request):
            http = self.kwargs.get('http_client')
            call = {'base_url': self.kwargs.get('base_url'), 'api_key': self.kwargs.get('api_key'), 'request': request,
                    'max_retries': self.kwargs.get('max_retries'), 'timeout': self.kwargs.get('timeout'),
                    'follow_redirects': None if http is None else http.follow_redirects}
            seen.append(call)
            n = sum(1 for c in seen if c['base_url'] == call['base_url'])
            value = script(call['base_url'], request, n) if script else reply(model=request['model'])
            if isinstance(value, BaseException):
                raise value
            return value

        def create_response(self, **request):
            seen.append({'base_url': None, 'api_key': None, 'request': request})
            return SimpleNamespace(output_text='Seven.', usage=SimpleNamespace(input_tokens=40, output_tokens=12),
                                   status='completed', model=request['model'] + '-2026-01-01')
    monkeypatch.setattr(openai, 'OpenAI', FakeOpenAI)
    return seen


def every_cell():
    """Every value of every table and the schema itself, as one string."""
    with sqlite3.connect(str(core.db_path())) as cx:
        tables = [r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        parts = [repr(row) for table in tables for row in cx.execute(f'SELECT * FROM "{table}"')]
        parts += [r[0] or '' for r in cx.execute('SELECT sql FROM sqlite_master')]
    return '\n'.join(parts)


@pytest.fixture
def three(ready, monkeypatch):
    three_endpoints(monkeypatch)
    for name in ('LIVE_INPUT_USD_PER_M', 'LIVE_OUTPUT_USD_PER_M'):
        monkeypatch.delenv(name)                 # the global pair is not needed when every model is on a named endpoint
    monkeypatch.setenv('LIVE_PROVIDER', 'anthropic')   # only the default for `round`: an all-free list must not care
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    archive.lock()
    return ready


# ---- B. model ids ------------------------------------------------------------------------------------

def test_archive_model_ids_have_their_own_rule_and_agents_keep_theirs():
    accepted = ['gemma3:4b', 'llama3.2:3b', '@cf/meta/llama-3.3-70b-instruct-fp8-fast', 'google/gemma-4-31b-it:free',
                'claude-haiku-5-5', 'claude-3-5-haiku-20241022', 'accounts/acme/models/llama-v3p1-8b-instruct', 'x' * 120]
    for model in accepted:
        assert archive.model_id_ok(model), model
    for model in ('', 'x' * 121, 'has space', 'a..b', '../etc', 'tab\there', 'new\nline', 'zero​width', 'café', 'a,b', 'a=b', 'a?b',
                  'evil.example.org', 'host.tld/path', 'see-https://x.io', '<script>', 'a"b', "a'b", None, 7):
        assert not archive.model_id_ok(model), model
    # What an agent may declare about itself is NOT loosened by any of this.
    for model in ('gemma3:4b', '@cf/meta/llama-3.3-70b-instruct-fp8-fast', 'google/gemma-4-31b-it:free', 'claude-3-5-haiku-20241022'):
        assert not core.declared_ok(model), model
    assert core.DECLARED.pattern == r"(?=.{1,48}\Z)(?:[A-Za-z0-9_-]+/)?[A-Za-z0-9._-]+"


def test_entries_name_an_endpoint_and_the_old_form_is_unchanged(ready, monkeypatch):
    three_endpoints(monkeypatch)
    assert archive.answerers() == [
        {'label': 'gemma', 'provider': 'openai-compatible', 'model': 'gemma3:4b', 'endpoint': 'ollama'},
        {'label': 'llama', 'provider': 'openai-compatible', 'model': '@cf/meta/llama-3.3-70b-instruct-fp8-fast', 'endpoint': 'cf'},
        {'label': 'gem', 'provider': 'openai-compatible', 'model': 'google/gemma-4-31b-it:free', 'endpoint': 'goog'}]
    # Back-compat: the three provider names mean what they always meant, and carry no endpoint key.
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', 'a=anthropic:claude-haiku-5-5,b=openai:gpt-5.6-luna,c=openai-compatible:vendor/some-model-1.0')
    assert archive.answerers() == [{'label': 'a', 'provider': 'anthropic', 'model': 'claude-haiku-5-5'},
                                   {'label': 'b', 'provider': 'openai', 'model': 'gpt-5.6-luna'},
                                   {'label': 'c', 'provider': 'openai-compatible', 'model': 'vendor/some-model-1.0'}]
    for bad in ('a=nowhere:gemma3:4b', 'a=ollama:has space', 'a=ollama:', 'a=ollama:evil.example.org', 'a=OLLAMA :x y'):
        monkeypatch.setenv('LIVE_ARCHIVE_MODELS', bad)
        with pytest.raises(archive.ArchiveRefused):
            archive.answerers()
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', 'a=nowhere:gemma3:4b')
    with pytest.raises(archive.ArchiveRefused, match='endpoint name listed in LIVE_ARCHIVE_ENDPOINTS'):
        archive.answerers()


def test_old_single_endpoint_form_still_runs_with_the_same_client(ready, monkeypatch):
    monkeypatch.setenv('LIVE_PROVIDER', 'openai-compatible')
    monkeypatch.setenv('LIVE_COMPAT_BASE_URL', 'http://localhost:18999/private-legacy/v1')
    monkeypatch.setenv('LIVE_COMPAT_JSON_MODE', 'off')
    monkeypatch.setenv('LIVE_INPUT_USD_PER_M', '0')
    monkeypatch.setenv('LIVE_OUTPUT_USD_PER_M', '0')
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', 'local=openai-compatible:qwen3:8b')
    seen = fake_sdk(monkeypatch)
    archive.lock()
    planned = archive.plan()
    assert planned['worst_case_usd'] == 0 and planned['endpoints'][0]['basis'] == 'local' and planned['input_rate'] == 0
    done = archive.run_archive(max_usd=0.01)
    assert done['status'] == 'complete' and done['answers']['answered'] == 20 and not done['mock']
    # Same base URL, key and retries as before named endpoints; since the redirect fix it follows no redirect either.
    assert {(c['base_url'], c['api_key'], c['max_retries'], c['follow_redirects']) for c in seen} == {
        ('http://localhost:18999/private-legacy/v1', providers.LOCAL_PLACEHOLDER_KEY, 0, False)}
    assert seen[0]['request']['temperature'] == 0 and seen[0]['request']['seed'] == archive.ARCHIVE_SEED
    stored = rows('SELECT endpoint,provider,model,pricing_basis,local,sampling FROM archive_answers WHERE id=1')[0]
    assert stored == {'endpoint': 'openai-compatible', 'provider': 'openai-compatible', 'model': 'qwen3:8b', 'pricing_basis': 'local',
                      'local': 1, 'sampling': f'temperature=0;seed={archive.ARCHIVE_SEED}'}
    assert 'private-legacy' not in every_cell() and 'localhost' not in every_cell()


# ---- A. named endpoints: settings --------------------------------------------------------------------

def test_endpoint_settings_are_validated(isolated_db, monkeypatch):
    three_endpoints(monkeypatch)
    found = providers.endpoints()
    assert list(found) == ['ollama', 'cf', 'goog']
    assert (found['ollama'].basis, found['cf'].basis, found['goog'].basis) == ('local', 'declared-free', 'declared-free')
    assert found['ollama'].loopback and found['ollama'].key_var is None and not found['cf'].loopback
    assert found['cf'].key_var == 'CF_TEST_API_KEY' and found['cf'].host == 'cf.example.invalid' and found['cf'].port == 443
    assert KEY_CF not in repr(found) and found['cf'].send_sampling and found['cf'].pause_seconds == 0

    def case(match, restore, **settings):
        """Apply `settings`, expect a refusal that repeats no URL and no key, then put `restore` back."""
        for name, value in settings.items():
            monkeypatch.setenv(name, value)
        with pytest.raises(providers.ProviderConfigError, match=match) as err:
            providers.endpoints()
        assert not any(secret in str(err.value) for secret in SECRETS)
        for name, value in restore.items():
            monkeypatch.delenv(name, raising=False) if value is None else monkeypatch.setenv(name, value)

    case('must use https', {var('cf', 'BASE_URL'): URLS['cf']}, **{var('cf', 'BASE_URL'): 'http://cf.example.invalid/v1'})
    case('user name or password', {var('cf', 'BASE_URL'): URLS['cf']}, **{var('cf', 'BASE_URL'): 'https://u:p@cf.example.invalid/v1'})
    case('query string', {var('cf', 'BASE_URL'): URLS['cf']}, **{var('cf', 'BASE_URL'): 'https://cf.example.invalid/v1?key=x'})
    case('is required for endpoint cf', {var('cf', 'BASE_URL'): URLS['cf']}, **{var('cf', 'BASE_URL'): ''})
    for name in ('LIVE_SECRET_KEY', 'PATH', 'cf_api_key', 'CF KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'OPENAI_COMPATIBLE_API_KEY', KEY_CF):
        case('KEY_VAR', {var('cf', 'KEY_VAR'): 'CF_TEST_API_KEY'}, **{var('cf', 'KEY_VAR'): name})
    case('KEY_VAR is required', {var('cf', 'KEY_VAR'): 'CF_TEST_API_KEY'}, **{var('cf', 'KEY_VAR'): ''})
    # Two services never share one key variable; two names for the same host and port may.
    case('different hosts and name the same key', {var('goog', 'KEY_VAR'): 'GOOG_TEST_API_KEY'}, **{var('goog', 'KEY_VAR'): 'CF_TEST_API_KEY'})
    monkeypatch.setenv(var('goog', 'KEY_VAR'), 'CF_TEST_API_KEY')
    monkeypatch.setenv(var('goog', 'BASE_URL'), 'https://cf.example.invalid/another-account/v1')
    assert providers.endpoints()['goog'].key_var == 'CF_TEST_API_KEY'
    monkeypatch.setenv(var('goog', 'KEY_VAR'), 'GOOG_TEST_API_KEY')
    monkeypatch.setenv(var('goog', 'BASE_URL'), URLS['goog'])
    # A hosted endpoint is priced unless the operator states that it charges nothing.
    case('must be positive for a hosted endpoint.*FREE_TIER=YES', {var('cf', 'FREE_TIER'): 'YES'}, **{var('cf', 'FREE_TIER'): ''})
    case('must be YES or NO', {var('cf', 'FREE_TIER'): 'YES'}, **{var('cf', 'FREE_TIER'): 'yes'})
    monkeypatch.setenv(var('cf', 'FREE_TIER'), 'NO')
    monkeypatch.setenv(var('cf', 'INPUT_USD_PER_M'), '0.05')
    case('must be positive for a hosted endpoint', {}, **{var('cf', 'OUTPUT_USD_PER_M'): '0'})
    monkeypatch.setenv(var('cf', 'OUTPUT_USD_PER_M'), '0.10')
    assert (providers.endpoints()['cf'].basis, providers.endpoints()['cf'].input_rate) == ('rates', 0.05)
    case('finite non-negative', {var('cf', 'INPUT_USD_PER_M'): '0.05'}, **{var('cf', 'INPUT_USD_PER_M'): 'nan'})
    monkeypatch.setenv(var('ollama', 'OUTPUT_USD_PER_M'), '0.01')          # a local model may be given a price too
    assert providers.endpoints()['ollama'].basis == 'rates'
    monkeypatch.delenv(var('ollama', 'OUTPUT_USD_PER_M'))
    case('REASONING_EFFORT must be one of', {var('cf', 'REASONING_EFFORT'): None}, **{var('cf', 'REASONING_EFFORT'): 'maximum'})
    monkeypatch.setenv(var('cf', 'REASONING_EFFORT'), 'none')
    assert providers.endpoints()['cf'].reasoning_effort == 'none'
    case('MAX_OUTPUT_TOKENS', {var('cf', 'MAX_OUTPUT_TOKENS'): None}, **{var('cf', 'MAX_OUTPUT_TOKENS'): 'many'})
    case('SEND_SAMPLING must be YES or NO', {var('cf', 'SEND_SAMPLING'): None}, **{var('cf', 'SEND_SAMPLING'): 'off'})
    case('PAUSE_SECONDS', {var('cf', 'PAUSE_SECONDS'): None}, **{var('cf', 'PAUSE_SECONDS'): '-1'})
    for names in ('Ollama', 'a b', 'openai', 'anthropic', 'openai-compatible', 'x,x', 'under_score', ','.join(f'e{n}' for n in range(9))):
        case('LIVE_ARCHIVE_ENDPOINTS', {'LIVE_ARCHIVE_ENDPOINTS': 'ollama,cf,goog'}, LIVE_ARCHIVE_ENDPOINTS=names)
    monkeypatch.setenv('LIVE_ARCHIVE_ENDPOINTS', 'my-host')
    monkeypatch.setenv('LIVE_ARCHIVE_ENDPOINT_MY_HOST_BASE_URL', 'http://127.0.0.1:8080/v1')   # '-' is written '_' in the variable name
    assert providers.endpoints()['my-host'].port == 8080


def test_named_key_variables_are_provider_secrets_dropped_by_web_processes(isolated_db, monkeypatch):
    three_endpoints(monkeypatch)
    assert config.endpoint_key_variables() == ('CF_TEST_API_KEY', 'GOOG_TEST_API_KEY')
    assert set(config.provider_secret_keys()) == set(config.PROVIDER_SECRET_KEYS) | {'CF_TEST_API_KEY', 'GOOG_TEST_API_KEY'}
    # A key variable is never a LIVE_* setting (those are loaded by every process), and a mistyped setting cannot
    # make a web process drop PATH: only well-formed key-variable names are ever removed.
    monkeypatch.setenv(var('ollama', 'KEY_VAR'), 'PATH')
    monkeypatch.setenv(var('cf', 'KEY_VAR'), 'LIVE_SECRET_KEY')
    assert config.endpoint_key_variables() == ('GOOG_TEST_API_KEY',)
    monkeypatch.setenv(var('cf', 'KEY_VAR'), 'CF_TEST_API_KEY')
    import os
    path = os.environ['PATH']
    assert set(config.drop_provider_secrets()) == {'CF_TEST_API_KEY', 'GOOG_TEST_API_KEY'}
    assert 'CF_TEST_API_KEY' not in os.environ and 'GOOG_TEST_API_KEY' not in os.environ and os.environ['PATH'] == path
    # Both web apps call it at import and at start-up, so a key exported in their shell is gone before a request.
    for module in ('app.py', 'observatory.py'):
        assert (REPO / 'live_genesis' / module).read_text(encoding='utf-8').count('drop_provider_secrets()') >= 2


def test_named_key_variables_are_read_from_dotenv_settings_too(isolated_db, tmp_path, monkeypatch):
    env = tmp_path / '.env'
    env.write_text('LIVE_ARCHIVE_ENDPOINTS=hosted\nLIVE_ARCHIVE_ENDPOINT_HOSTED_KEY_VAR=HOSTED_API_TOKEN\nHOSTED_API_TOKEN=never-loaded\n',
                   encoding='utf-8')
    monkeypatch.setattr(config, 'ENV_FILE', env)
    assert config.endpoint_key_variables() == ('HOSTED_API_TOKEN',)
    monkeypatch.setenv('HOSTED_API_TOKEN', 'exported-in-the-shell')
    assert config.drop_provider_secrets() == ['HOSTED_API_TOKEN']


# ---- C. pricing per endpoint and the plan ---------------------------------------------------------------

def test_plan_prices_each_endpoint_and_labels_the_basis(three, monkeypatch, capsys):
    planned = archive.plan()
    assert [a['label'] for a in planned['models']] == ['gemma', 'llama', 'gem'] and planned['calls'] == 60
    assert planned['worst_case_usd'] == 0 and planned['input_rate'] is None
    assert [(g['endpoint'], g['calls'], g['basis'], g['worst_case_usd']) for g in planned['endpoints']] == [
        ('ollama', 20, 'local', 0.0), ('cf', 20, 'declared-free', 0.0), ('goog', 20, 'declared-free', 0.0)]
    text = archive.render_plan(planned)
    assert '  ollama: 20 calls, $0.000000, local (runs on this machine; recorded as $0 estimated)' in text
    assert '  cf: 20 calls, $0.000000, declared-free (UNVERIFIED: see below)' in text
    assert "operator's own statement" in text and 'NOT verified' in text and 'cannot see a charge' in text
    assert 'llama = cf:@cf/meta/llama-3.3-70b-instruct-fp8-fast' in text and archive.FAILURE_RULE in text
    assert 'Rates used for every model' not in text and 'a free one included' in text and 'archive-run --max-usd 0.01' in text
    assert not any(secret in text for secret in SECRETS)
    # A priced endpoint and a native provider next to the free ones: each at its own rates; zero-priced models go first.
    monkeypatch.setenv(var('cf', 'FREE_TIER'), 'NO')
    monkeypatch.setenv(var('cf', 'INPUT_USD_PER_M'), '1.00')
    monkeypatch.setenv(var('cf', 'OUTPUT_USD_PER_M'), '2.00')
    monkeypatch.setenv(var('cf', 'MAX_OUTPUT_TOKENS'), '500')
    monkeypatch.setenv('LIVE_INPUT_USD_PER_M', '0.10')
    monkeypatch.setenv('LIVE_OUTPUT_USD_PER_M', '0.50')
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', 'claude=anthropic:claude-haiku-5-5,' + archive.os.environ['LIVE_ARCHIVE_MODELS'])
    planned = archive.plan()
    assert [a['label'] for a in planned['models']] == ['gemma', 'gem', 'claude', 'llama']
    prompt = planned['questions'][0]['prompt']
    tokens_in = len(archive.SYSTEM_LINE.encode()) + len(prompt.encode()) + 200
    assert planned['holds_by_model']['llama']['Q01'] == pytest.approx(tokens_in * 1.00 / 1e6 + 500 * 2.00 / 1e6)
    assert planned['holds_by_model']['claude']['Q01'] == pytest.approx(tokens_in * 0.10 / 1e6 + 120 * 0.50 / 1e6) == planned['holds']['Q01']
    assert planned['holds_by_model']['gemma']['Q01'] == 0
    assert planned['worst_case_usd'] == pytest.approx(sum(sum(v.values()) for v in planned['holds_by_model'].values()))
    text = archive.render_plan(planned)
    assert 'cf: 20 calls' in text and 'rates $1.00 in / $2.00 out per million tokens' in text
    assert 'anthropic: 20 calls' in text and 'rates $0.10 in / $0.50 out' in text and 'Rates used for every model: $0.10 in / $0.50 out' in text
    # The global pair still may not be zero for a native provider.
    monkeypatch.setenv('LIVE_INPUT_USD_PER_M', '0')
    with pytest.raises(controls.ConfigError):
        archive.plan()


def test_a_free_run_still_needs_the_opt_in_the_cap_and_the_keys(three, monkeypatch):
    seen = fake_sdk(monkeypatch)
    monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS', 'NO')
    with pytest.raises(archive.ArchiveRefused, match='LIVE_ALLOW_PAID_CALLS=YES'):
        archive.run_archive(max_usd=1)
    monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS', 'YES')
    for cap in (0, None, float('nan')):
        with pytest.raises(archive.ArchiveRefused, match='--max-usd'):
            archive.run_archive(max_usd=cap)
    monkeypatch.delenv('GOOG_TEST_API_KEY')
    with pytest.raises(archive.ArchiveRefused, match='GOOG_TEST_API_KEY missing.*endpoint goog') as err:
        archive.run_archive(max_usd=1)
    assert not any(secret in str(err.value) for secret in SECRETS)
    assert seen == [] and rows('SELECT * FROM archive_runs') == [] and rows('SELECT * FROM budget_reservations') == []


# ---- A + D + E. the demonstration: three endpoints, one is rate-limited on its third call -------------------

def test_three_endpoints_one_rate_limited_the_others_complete_and_nothing_leaks(three, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(providers.config, 'load_provider_credentials', lambda: [])     # never read a developer's .env
    seen = fake_sdk(monkeypatch, lambda base_url, request, n: Status(429) if 'cf.example' in base_url and n == 3
                    else reply(model=request['model'], fingerprint='fp_abc123'))
    code, plan_out = run_cli(monkeypatch, capsys, 'archive-plan')
    assert code == 0 and 'ollama: 20 calls' in plan_out and 'declared-free' in plan_out
    code, check_out = run_cli(monkeypatch, capsys, 'archive-check-endpoints')
    assert code == 0 and seen == []
    code, run_out = run_cli(monkeypatch, capsys, 'archive-run', '--max-usd', '0.01')
    assert code == 0 and 'RUN COMPLETE WITH GAPS: skipped, no further question asked of: llama (rate_limited)' in run_out
    done = json.loads(run_out[run_out.index('{\n'):run_out.rindex('\n}') + 2])
    assert done['status'] == 'complete_with_gaps' and done['kind'] == 'real' and not done['mock']
    assert done['answers'] == {'answered': 42, 'no_answer': 0, 'withheld': 0} and done['estimated_usd'] == 0
    assert [(a['answerer'], a['endpoint'], a['pricing_basis'], a['outcome'], a['reason'], a['recorded']) for a in done['answerers']] == [
        ('gemma', 'ollama', 'local', 'complete', '', 20), ('llama', 'cf', 'declared-free', 'skipped', 'rate_limited', 2),
        ('gem', 'goog', 'declared-free', 'complete', '', 20)]
    # 43 requests: the rate-limited model was not asked again after its third call, and nothing was retried.
    assert [sum(1 for c in seen if name in c['base_url']) for name in ('localhost', 'cf.example', 'goog.example')] == [20, 3, 20]
    # A key is sent to its own endpoint only; no redirect is followed; no retry.
    assert {(c['base_url'], c['api_key']) for c in seen} == {(URLS['ollama'], providers.LOCAL_PLACEHOLDER_KEY), (URLS['cf'], KEY_CF),
                                                              (URLS['goog'], KEY_GOOG)}
    assert {(c['follow_redirects'], c['max_retries']) for c in seen} == {(False, 0)}
    assert all(c['request']['temperature'] == 0 and c['request']['seed'] == archive.ARCHIVE_SEED for c in seen)
    assert all('response_format' not in c['request'] and c['request']['max_tokens'] == 120 for c in seen)
    assert all(c['request']['messages'] == [{'role': 'system', 'content': archive.SYSTEM_LINE},
                                            {'role': 'user', 'content': c['request']['messages'][1]['content']}] for c in seen)
    # Money: every hold is $0; the failed call's hold is released, nothing is unconfirmed, the next run is not blocked.
    holds = rows('SELECT status,reserved_usd,provider,agent_id FROM budget_reservations ORDER BY id')
    assert len(holds) == 43 and [h['status'] for h in holds].count('released') == 1 and holds[22]['status'] == 'released'
    assert {h['status'] for h in holds} == {'settled', 'released'} and {h['reserved_usd'] for h in holds} == {0.0}
    assert core.budget_status()['unconfirmed_reservations'] == [] and core.budget_status()['committed_usd'] == 0
    assert any('llama skipped (rate_limited); $0 hold 23 released (pricing basis declared-free)' in r['operation']
               for r in rows('SELECT operation FROM operator_audit'))
    # What each answer records: the endpoint NAME, the reported model, the fingerprint, the sampling sent, the basis.
    stored = rows("SELECT endpoint,provider,model,reported_model,system_fingerprint,finish_reason,sampling,pricing_basis,local,model_digest,"
                  "latency_ms FROM archive_answers WHERE answerer='gem' AND question_id='Q01'")[0]
    assert stored.pop('latency_ms') >= 0
    assert stored == {'endpoint': 'goog', 'provider': 'openai-compatible', 'model': 'google/gemma-4-31b-it:free',
                      'reported_model': 'google/gemma-4-31b-it:free', 'system_fingerprint': 'fp_abc123', 'finish_reason': 'stop',
                      'sampling': f'temperature=0;seed={archive.ARCHIVE_SEED}', 'pricing_basis': 'declared-free', 'local': 0,
                      'model_digest': None}
    code, status_out = run_cli(monkeypatch, capsys, 'archive-status')
    status = json.loads(status_out)
    assert status['runs'][0]['status'] == 'complete_with_gaps' and status['endpoints_configured'] == 'ollama,cf,goog'
    assert status['last_run_answerers']['status'] == 'complete_with_gaps'
    assert [(a['answerer'], a['outcome'], a['reason']) for a in status['last_run_answerers']['answerers']] == [
        ('gemma', 'complete', ''), ('llama', 'skipped', 'rate_limited'), ('gem', 'complete', '')]
    # A second run is not blocked and asks the skipped model again from the start.
    code, again = run_cli(monkeypatch, capsys, 'archive-run', '--max-usd', '0.01')
    assert code == 0 and '"status": "complete"' in again and len(seen) == 43 + 60
    # The public payload, the pages and the reviewed export.
    payload = archive.public_payload()
    assert archive.payload_problem(payload) is None and payload['runs'][0]['skipped'] == {'llama': 'rate_limited'} and payload['runs'][1]['skipped'] == {}
    first = next(a for a in payload['answers'] if a['answerer'] == 'gemma')
    assert {key: first[key] for key in archive.PUBLIC_CALL_FIELDS} == {
        'endpoint': 'ollama', 'reported_model': 'gemma3:4b', 'model_digest': None, 'pricing_basis': 'local', 'sampling_pinned': True, 'local': True}
    page = archive_pages.render_question(payload, 'Q05')
    assert 'gemma3:4b · run locally · server call' in page and '@cf/meta/llama-3.3-70b-instruct-fp8-fast · via cf · server call' in page
    assert 'this model was skipped after a failed call (the service limited the number of requests)' in page
    assert "not the same as its maker's own chat app" in page and 'open model' not in page and ' says' not in page.replace(' says where', '')
    out = tmp_path / 'published'
    snapshot.export_published(out)
    snapshot.approve_published(out, confirmed=True)
    assert snapshot.check_published(out)['reviewed'] is True
    exported = (out / 'archive.json').read_text(encoding='utf-8')
    assert '"endpoint": "cf"' in exported and '"pricing_basis": "declared-free"' in exported and 'system_fingerprint' not in exported
    assert 'latency' not in exported and 'fp_abc123' not in exported
    # No key, no URL, no host and no provider error text anywhere: not in a table, not in a printed line, not in a file.
    site = '\n'.join(archive_pages.render_site(payload).values())
    everything = {'database': every_cell(), 'plan': plan_out, 'run': run_out, 'status': status_out, 'again': again, 'export': exported,
                  'pages': site, 'manifest': (out / 'MANIFEST.json').read_text(encoding='utf-8')}
    for where, text in everything.items():
        for secret in SECRETS + ('response body',):
            assert secret not in text, (where, secret)
    assert not any(secret in check_out for secret in (KEY_CF, KEY_GOOG, 'private-local-path', 'private-cf-path', 'private-goog-path'))
    # check-endpoints is the one place a HOST is printed (never the path, never the key).
    assert 'Endpoint cf: host cf.example.invalid  port 443' in check_out and 'Key variable CF_TEST_API_KEY set: yes' in check_out


def test_the_real_sdk_client_follows_no_redirect_and_a_key_reaches_its_own_host_only(isolated_db, monkeypatch):
    """The real openai SDK class on a mock transport (no socket): a redirect to another host is not followed, so
    neither the prompt nor the key travels there, and each endpoint's key is sent to that endpoint alone."""
    import httpx
    three_endpoints(monkeypatch)
    requests = []

    def handler(request):
        requests.append((request.url.host, request.url.path, request.headers.get('authorization')))
        if request.url.host == 'cf.example.invalid':
            return httpx.Response(307, headers={'location': 'https://elsewhere.example.invalid/v1/chat/completions'})
        return httpx.Response(200, json={'id': 'x', 'object': 'chat.completion', 'created': 1, 'model': 'reported-name-1',
                                         'system_fingerprint': 'fp_1', 'usage': {'prompt_tokens': 9, 'completion_tokens': 3, 'total_tokens': 12},
                                         'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'Seven.'}}]})
    real_client, built = httpx.Client, []

    class Client(real_client):               # the SDK checks isinstance(http_client, httpx.Client)
        def __init__(self, **kwargs):
            built.append(kwargs)
            super().__init__(transport=httpx.MockTransport(handler), trust_env=False, **kwargs)
    monkeypatch.setattr(httpx, 'Client', Client)
    found = providers.endpoints()
    with pytest.raises(Exception) as err:
        engine.infer('openai-compatible', 'model-x', 'the prompt', system='s', max_output_tokens=50, endpoint=found['cf'], sampling=dict(archive.SAMPLING))
    assert getattr(err.value, 'status_code', None) == 307 and archive.failure_category(err.value) == 'other'
    assert requests == [('cf.example.invalid', '/private-cf-path/v1/chat/completions', f'Bearer {KEY_CF}')]     # one request, never retried or redirected
    done = engine.infer('openai-compatible', 'model-y', 'the prompt', system='s', max_output_tokens=50, endpoint=found['goog'], sampling=dict(archive.SAMPLING))
    assert (done.text, done.input_tokens, done.output_tokens, done.stop) == ('Seven.', 9, 3, 'end')
    assert (done.reported_model, done.system_fingerprint, done.finish_reason) == ('reported-name-1', 'fp_1', 'stop')
    assert done.sampling == {'temperature': 0, 'seed': archive.ARCHIVE_SEED}
    assert requests[1] == ('goog.example.invalid', '/private-goog-path/v1/chat/completions', f'Bearer {KEY_GOOG}')
    assert all(kwargs['follow_redirects'] is False for kwargs in built) and len(built) == 2
    assert not any(host == 'elsewhere.example.invalid' for host, _, _ in requests)
    # A named endpoint is never mixed with a native provider's request shape.
    with pytest.raises(engine.ProviderNotReady):
        engine.infer('openai', 'model-x', 'the prompt', endpoint=found['cf'])


# ---- D. what is recorded, and what counts as a new model ------------------------------------------------

def test_reported_model_and_digest_are_recorded_and_fire_model_changed(three, monkeypatch):
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', 'gemma=ollama:gemma3:4b,gem=goog:gemini-flash-latest')
    digests = iter(['a' * 64, 'a' * 64, 'b' * 64])
    asked = []

    def tags(origin, timeout):
        asked.append(origin)
        return json.dumps({'models': [{'name': 'other:1b', 'digest': 'c' * 64},
                                      {'name': 'gemma3:4b', 'model': 'gemma3:4b', 'digest': 'sha256:' + next(digests)}]}).encode()
    monkeypatch.setattr(archive, '_fetch_tags', tags)
    monkeypatch.setattr(providers, '_loopback_port_open', lambda host, port, timeout=2.0: True)
    reported = iter(['gemini-2.5-flash-001', 'gemini-2.5-flash-002', 'gemini-2.5-flash-002'])
    for _ in range(3):
        name = next(reported)
        fake_sdk(monkeypatch, lambda base_url, request, n, name=name: reply(model=name if 'goog' in base_url else request['model']))
        assert archive.run_archive(max_usd=0.01)['status'] == 'complete'
    # One lookup per run, for the loopback endpoint only, at its origin (the hosted endpoint is never asked).
    assert asked == ['http://localhost:11999'] * 3
    got = rows("SELECT r.number,a.answerer,a.model,a.reported_model,a.model_digest FROM archive_answers a JOIN archive_runs r "
               "ON r.id=a.run_id WHERE a.question_id='Q01' ORDER BY r.number,a.answerer")
    assert [(g['number'], g['answerer'], g['reported_model'], (g['model_digest'] or '')[:3]) for g in got] == [
        (1, 'gem', 'gemini-2.5-flash-001', ''), (1, 'gemma', 'gemma3:4b', 'aaa'), (2, 'gem', 'gemini-2.5-flash-002', ''),
        (2, 'gemma', 'gemma3:4b', 'aaa'), (3, 'gem', 'gemini-2.5-flash-002', ''), (3, 'gemma', 'gemma3:4b', 'bbb')]
    assert rows('SELECT model_digest FROM archive_run_answerers WHERE run_id=3 AND answerer=?', 'gemma')[0]['model_digest'] == 'b' * 64
    payload = archive.public_payload()
    assert archive.payload_problem(payload) is None
    assert [a['model_digest'] for a in payload['answers'] if a['answerer'] == 'gemma' and a['question'] == 'Q01'] == ['a' * 12, 'a' * 12, 'b' * 12]
    page = archive_pages.render_question(payload, 'Q01')
    # The REQUESTED ids never changed; the change is seen in what the service reported and in the local file's digest.
    assert "gem&#x27;s model ID reported by the service changed from gemini-2.5-flash-001 to gemini-2.5-flash-002; the answer stayed the same." in page
    assert f"gemma&#x27;s local model file (digest) changed from {'a' * 12} to {'b' * 12}; the answer stayed the same." in page
    assert page.count('gemini-flash-latest · via goog · server call · new model ID') == 1
    assert page.count('gemma3:4b · run locally · server call · new model file') == 1
    assert 'gem gave the same answer in every run recorded.' in page


def test_what_a_provider_says_about_itself_is_untrusted(three, monkeypatch):
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', 'gem=goog:gemma-x')
    hostile = '<script>alert(1)</script> see https://evil.example.org'
    fake_sdk(monkeypatch, lambda base_url, request, n: reply(model=hostile if n == 1 else 'evil.example.org' if n == 2 else 'ok-model',
                                                              fingerprint=hostile, finish=hostile if n == 1 else 'content_filter'))
    archive.run_archive(max_usd=0.01)
    got = rows('SELECT reported_model,system_fingerprint,finish_reason,state FROM archive_answers ORDER BY id LIMIT 3')
    assert got[0] == {'reported_model': None, 'system_fingerprint': None, 'finish_reason': None, 'state': 'answered'}
    assert got[1]['reported_model'] is None and got[1]['finish_reason'] == 'content_filter'      # shaped like a domain: not kept
    assert got[2]['reported_model'] == 'ok-model'
    assert 'evil.example' not in every_cell() and '<script>' not in every_cell()
    payload = archive.public_payload()
    for field, bad, match in (('endpoint', 'https://cf.example.invalid/v1', 'endpoint is not a plain name'),
                              ('endpoint', 'Has Space', 'endpoint is not a plain name'),
                              ('reported_model', hostile, 'reported model name'), ('reported_model', 'evil.example.org', 'reported model name'),
                              ('model_digest', 'a' * 64, 'model digest'), ('model_digest', 'xyz', 'model digest'),
                              ('pricing_basis', 'free', 'pricing basis'), ('sampling_pinned', 'yes', 'sampling or local'), ('local', 1, 'sampling or local')):
        broken = json.loads(json.dumps(payload))
        broken['answers'][0][field] = bad
        assert match in archive.payload_problem(broken), field
    broken = json.loads(json.dumps(payload))
    broken['runs'][0]['skipped'] = {'gem': 'because https://x.example.org said so'}
    assert 'skipped models' in archive.payload_problem(broken)
    broken = json.loads(json.dumps(payload))
    del broken['answers'][0]['endpoint']
    assert archive.payload_problem(broken) is None and archive.export_problem(broken) == 'an answer lacks its endpoint fields'   # pages yes, publishing no


def test_sampling_is_switchable_and_native_apis_get_only_what_they_take(three, monkeypatch):
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', 'gemma=ollama:gemma3:4b,gem=goog:gemma-x,gpt=openai:gpt-test-1')
    monkeypatch.setenv('LIVE_INPUT_USD_PER_M', '0.20')
    monkeypatch.setenv('LIVE_OUTPUT_USD_PER_M', '1.20')
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    monkeypatch.setenv(var('goog', 'SEND_SAMPLING'), 'NO')
    monkeypatch.setenv(var('goog', 'REASONING_EFFORT'), 'none')
    seen = fake_sdk(monkeypatch)
    assert archive.run_archive(max_usd=0.05, question_set='core')['status'] == 'complete'
    by_host = {name: next(c['request'] for c in seen if c['base_url'] and name in c['base_url']) for name in ('localhost', 'goog')}
    assert by_host['localhost']['seed'] == archive.ARCHIVE_SEED and 'reasoning_effort' not in by_host['localhost']
    assert 'temperature' not in by_host['goog'] and 'seed' not in by_host['goog'] and by_host['goog']['reasoning_effort'] == 'none'
    native = [c['request'] for c in seen if c['base_url'] is None]
    # Native OpenAI: nothing is sent by default (some of its models refuse `temperature`), and that is what is recorded.
    assert len(native) == 20 and all('temperature' not in r and 'seed' not in r for r in native)
    sampling = {r['answerer']: r['sampling'] for r in rows('SELECT DISTINCT answerer,sampling FROM archive_answers')}
    assert sampling == {'gemma': f'temperature=0;seed={archive.ARCHIVE_SEED}', 'gem': 'none', 'gpt': 'none'}
    assert rows("SELECT DISTINCT reported_model,endpoint,pricing_basis FROM archive_answers WHERE answerer='gpt'") == [
        {'reported_model': 'gpt-test-1-2026-01-01', 'endpoint': 'openai', 'pricing_basis': 'rates'}]
    pinned = {a['answerer']: a['sampling_pinned'] for a in archive.public_payload()['answers']}
    assert pinned == {'gemma': True, 'gem': False, 'gpt': False}
    # Switched on for the native provider: the temperature only (the Responses API has no seed), recorded as sent.
    monkeypatch.setenv('LIVE_ARCHIVE_ENDPOINT_OPENAI_SEND_SAMPLING', 'YES')
    seen.clear()
    archive.run_archive(max_usd=0.05, only='gpt')
    assert all(c['request']['temperature'] == 0 and 'seed' not in c['request'] for c in seen) and len(seen) == 20
    assert rows('SELECT sampling FROM archive_answers ORDER BY id DESC LIMIT 1')[0]['sampling'] == 'temperature=0'
    # A resident turn and a study call still send no sampling at all.
    seen.clear()
    engine.infer('openai', 'gpt-test-1', 'a prompt')
    assert 'temperature' not in seen[0]['request'] and 'seed' not in seen[0]['request']


# ---- E. failure isolation and the hold rule --------------------------------------------------------------

@pytest.mark.parametrize('failure,category', [(Status(429), 'rate_limited'), (Status(404), 'not_found'), (Status(401), 'auth'),
                                              (Status(403), 'auth'), (Status(503), 'server_error'), (Status(400), 'other'),
                                              (ReadTimeout('timed out'), 'timeout'), (TimeoutError(), 'timeout'),
                                              (RuntimeError('boom'), 'other'), (reply(usage=None), 'no_usage'),
                                              (reply(usage=(-1, 5)), 'no_usage')])
def test_failure_categories_on_a_zero_priced_endpoint(three, monkeypatch, failure, category):
    fake_sdk(monkeypatch, lambda base_url, request, n: failure if 'goog' in base_url else reply())
    done = archive.run_archive(max_usd=0.01)
    assert done['status'] == 'complete_with_gaps' and done['answers']['answered'] == 40
    assert [(a['answerer'], a['outcome'], a['reason'], a['recorded']) for a in done['answerers']] == [
        ('gemma', 'complete', '', 20), ('llama', 'complete', '', 20), ('gem', 'skipped', category, 0)]
    assert archive.failure_category(failure) == category or category == 'no_usage'
    assert core.budget_status()['unconfirmed_reservations'] == []
    assert rows('SELECT status FROM budget_reservations ORDER BY id DESC LIMIT 1')[0]['status'] == 'released'
    # Only the category is kept: never the exception's text.
    assert 'boom' not in every_cell() and 'response body' not in every_cell() and 'timed out' not in every_cell()
    with pytest.raises(sqlite3.IntegrityError, match='append-only'):
        with core.connect() as cx:
            cx.execute("UPDATE archive_run_answerers SET outcome='complete'")
    with pytest.raises(sqlite3.IntegrityError, match='append-only'):
        with core.connect() as cx:
            cx.execute('DELETE FROM archive_run_answerers')


def test_a_missing_key_at_call_time_skips_only_that_endpoint(three, monkeypatch):
    seen = fake_sdk(monkeypatch)
    real = providers.endpoint_api_key
    calls = []

    def key(endpoint):
        calls.append(endpoint.name)
        if endpoint.name == 'cf' and calls.count('cf') > 1:      # present at the gate, gone at the first call
            raise providers.ProviderConfigError('CF_TEST_API_KEY missing')
        return real(endpoint)
    monkeypatch.setattr(providers, 'endpoint_api_key', key)
    done = archive.run_archive(max_usd=0.01)
    assert [(a['answerer'], a['outcome'], a['reason']) for a in done['answerers']] == [
        ('gemma', 'complete', ''), ('llama', 'skipped', 'not_ready'), ('gem', 'complete', '')]
    assert not any('cf.example' in c['base_url'] for c in seen)       # nothing was sent for it


def test_a_priced_endpoint_keeps_the_unconfirmed_hold_and_stops_but_free_models_were_asked_first(three, monkeypatch):
    # The priced endpoint is listed FIRST; it is still asked last, so its failure costs no free column.
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', 'llama=cf:llama-x,gemma=ollama:gemma3:4b,gem=goog:gemma-x,late=cf:other-x')
    monkeypatch.setenv(var('cf', 'FREE_TIER'), 'NO')
    monkeypatch.setenv(var('cf', 'INPUT_USD_PER_M'), '0.50')
    monkeypatch.setenv(var('cf', 'OUTPUT_USD_PER_M'), '1.00')
    seen = fake_sdk(monkeypatch, lambda base_url, request, n: Status(429) if 'cf.example' in base_url and n == 3 else reply())
    done = archive.run_archive(max_usd=0.05)
    assert done['status'] == 'incomplete' and done['answers']['answered'] == 42
    assert 'inference failed' in done['stop_reason'] and 'kept as unconfirmed (reservation 43)' in done['stop_reason']
    assert 'reconcile --receipt 43' in done['stop_reason'] and KEY_CF not in json.dumps(done)
    assert [(a['answerer'], a['pricing_basis'], a['outcome'], a['reason'], a['recorded'], a['reservation_id']) for a in done['answerers']] == [
        ('gemma', 'local', 'complete', '', 20, None), ('gem', 'declared-free', 'complete', '', 20, None),
        ('llama', 'rates', 'stopped', 'rate_limited', 2, 43), ('late', 'rates', 'not_asked', '', 0, None)]
    hold = rows('SELECT status,reserved_usd,input_rate,output_rate FROM budget_reservations WHERE id=43')[0]
    assert hold['status'] == 'unconfirmed' and hold['reserved_usd'] > 0 and (hold['input_rate'], hold['output_rate']) == (0.5, 1.0)
    assert core.budget_status()['unconfirmed_reservations'] == [43] and core.budget_status()['committed_usd'] > 0
    assert len(seen) == 43
    # Exactly as before: every paid command is blocked until the operator reconciles, a free run included.
    with pytest.raises(archive.ArchiveRefused, match='Unconfirmed provider charge'):
        archive.run_archive(max_usd=0.05, only='gemma')
    assert len(seen) == 43
    controls.reconcile_reservation(43, 0.0)
    assert archive.run_archive(max_usd=0.05, only='gemma')['status'] == 'complete'


def test_endpoint_pause_and_output_limit(three, monkeypatch):
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', 'gemma=ollama:gemma3:4b,gem=goog:gemma-x')
    monkeypatch.setenv(var('goog', 'PAUSE_SECONDS'), '4')
    monkeypatch.setenv(var('goog', 'MAX_OUTPUT_TOKENS'), '5000')        # clamped to the archive's 40-1000
    slept = []
    monkeypatch.setattr(archive.time, 'sleep', slept.append)
    seen = fake_sdk(monkeypatch)
    archive.run_archive(max_usd=0.01, pause_seconds=1.5)
    assert slept == [1.5] * 20 + [4.0] * 20                                 # the longer of --pause and the endpoint's own
    assert {c['request']['max_tokens'] for c in seen if 'goog' in c['base_url']} == {1000}
    assert {c['request']['max_tokens'] for c in seen if 'localhost' in c['base_url']} == {120}


# ---- the local model digest (best effort; loopback only) ----------------------------------------------------

def test_digest_lookup_is_loopback_only_silent_and_never_raises(isolated_db, monkeypatch):
    three_endpoints(monkeypatch)
    found = providers.endpoints()
    called = []

    def tags(body):
        def fetch(origin, timeout):
            called.append((origin, timeout))
            if isinstance(body, BaseException):
                raise body
            return body
        return fetch
    listing = json.dumps({'models': [{'name': 'gemma3:4b', 'digest': 'd' * 64}, {'name': 'llava:latest', 'digest': 'e' * 64}]}).encode()
    monkeypatch.setattr(archive, '_fetch_tags', tags(listing))
    assert archive.lookup_model_digest(found['cf'], 'gemma3:4b') is None and called == []          # hosted: no request at all
    assert archive.lookup_model_digest(None, 'gemma3:4b') is None and called == []
    monkeypatch.setattr(providers, '_loopback_port_open', lambda host, port, timeout=2.0: False)
    assert archive.lookup_model_digest(found['ollama'], 'gemma3:4b') is None and called == []      # nothing listens: no request
    monkeypatch.setattr(providers, '_loopback_port_open', lambda host, port, timeout=2.0: True)
    assert archive.lookup_model_digest(found['ollama'], 'gemma3:4b') == 'd' * 64
    assert called == [('http://localhost:11999', archive.DIGEST_TIMEOUT_SECONDS)] and archive.DIGEST_TIMEOUT_SECONDS <= 2
    assert archive.lookup_model_digest(found['ollama'], 'llava') == 'e' * 64                        # an untagged name is :latest
    assert archive.lookup_model_digest(found['ollama'], 'not-pulled') is None
    for body in (b'<html>not ollama</html>', b'{"models": "no"}', b'{}', json.dumps({'models': [{'name': 'gemma3:4b', 'digest': 'zz'}]}).encode(),
                 b'x' * 1_000_001, OSError('refused'), ValueError('bad'), TimeoutError()):
        monkeypatch.setattr(archive, '_fetch_tags', tags(body))
        assert archive.lookup_model_digest(found['ollama'], 'gemma3:4b') is None
    source = (REPO / 'live_genesis' / 'archive.py').read_text(encoding='utf-8')
    assert source.count('.open(') == 1 and 'ProxyHandler({})' in source and '_NoRedirect' in source     # one request site, no proxy, no redirect


# ---- F. cli archive-check-endpoints ------------------------------------------------------------------------

def test_check_endpoints_prints_hosts_and_yes_no_and_contacts_no_hosted_service(isolated_db, monkeypatch, capsys):
    three_endpoints(monkeypatch)
    monkeypatch.setattr(providers.config, 'load_provider_credentials', lambda: [])
    monkeypatch.setattr(openai, 'OpenAI', lambda **kwargs: (_ for _ in ()).throw(AssertionError('no client may be built')))
    probed = []
    monkeypatch.setattr(providers, '_loopback_port_open', lambda host, port, timeout=2.0: probed.append((host, port)) or False)
    monkeypatch.delenv('GOOG_TEST_API_KEY')
    monkeypatch.setattr(sys, 'argv', ['live_genesis.cli', 'archive-check-endpoints'])
    with pytest.raises(SystemExit) as stop:
        cli.main()
    out = capsys.readouterr().out
    assert stop.value.code == 1 and probed == [('localhost', 11999)]                      # only the loopback endpoint is probed
    assert 'no request is sent to any hosted service' in out
    assert 'Endpoint ollama: host localhost  port 11999  scheme http  (loopback: stays on this machine)' in out
    assert 'Key variable: none (loopback server; a placeholder is sent)' in out and 'TCP connection to localhost:11999: REFUSED or no answer' in out
    assert 'Endpoint cf: host cf.example.invalid  port 443  scheme https  (NOT loopback: prompts leave this machine)' in out
    assert 'Key variable CF_TEST_API_KEY set: yes' in out and 'Key variable GOOG_TEST_API_KEY set: no' in out
    assert 'Pricing basis: local' in out and 'Pricing basis: declared-free' in out and 'NOT verified' in out
    assert out.count('TCP connection: not tried (only loopback hosts are probed)') == 2
    assert 'PROBLEM: GOOG_TEST_API_KEY missing' in out and 'Not ready' in out
    assert not any(secret in out for secret in (KEY_CF, KEY_GOOG, 'private-local-path', 'private-cf-path', 'private-goog-path'))
    monkeypatch.setenv('GOOG_TEST_API_KEY', KEY_GOOG)
    cli.main()
    out = capsys.readouterr().out
    assert 'Settings look usable' in out and KEY_GOOG not in out
    monkeypatch.setenv(var('cf', 'BASE_URL'), 'http://cf.example.invalid/private-cf-path/v1')
    with pytest.raises(SystemExit):
        cli.main()
    out = capsys.readouterr().out
    assert 'PROBLEM: LIVE_ARCHIVE_ENDPOINT_CF_BASE_URL must use https' in out and 'private-cf-path' not in out
    monkeypatch.delenv('LIVE_ARCHIVE_ENDPOINTS')
    cli.main()
    assert 'No named endpoint is configured' in capsys.readouterr().out
    assert rows('SELECT * FROM budget_reservations') == []


# ---- migration of a database from before named endpoints -------------------------------------------------

def test_a_database_from_before_named_endpoints_migrates_in_place(ready, tmp_path, monkeypatch):
    trending_file(tmp_path, monkeypatch, 2)
    core.seed_agents()
    data, trending = archive.load_questions(), archive.load_questions(track='trending')
    with core.connect() as cx:
        for table in ('archive_run_answerers', 'archive_run_sets', 'archive_answers', 'archive_runs', 'archive_questions',
                      'archive_set_versions', 'archive_sets'):
            cx.execute(f'DROP TABLE {table}')
        cx.executescript(OLD_SCHEMA.read_text(encoding='utf-8'))
        assert 'endpoint' not in {r[1] for r in cx.execute('PRAGMA table_info(archive_answers)')}
    archive.lock()
    archive.lock('trending')                       # the old schema holds both sets and their versions
    with core.connect() as cx:
        cx.execute("INSERT INTO budget_reservations(agent_id,model,reserved_usd,status,created_at,provider,input_rate,output_rate) "
                   "VALUES (NULL,'gpt-old-1',0.001,'settled','2026-10-02T09:00:00+00:00','openai',0.2,1.2)")
        cx.execute("INSERT INTO archive_runs(number,kind,question_set,set_sha256,started_at,finished_at,status) "
                   "VALUES (1,'real',?,?,'2026-10-02T09:00:00+00:00','2026-10-02T09:05:00+00:00','complete')", (data['question_set'], data['sha256']))
        for track, part in (('core', data), ('trending', trending)):
            version, digest = cx.execute('SELECT version,sha256 FROM archive_set_versions WHERE question_set=?', (part['question_set'],)).fetchone()
            cx.execute('INSERT INTO archive_run_sets(run_id,track,question_set,version,sha256,questions) VALUES (1,?,?,?,?,?)',
                       (track, part['question_set'], version, digest, len(part['questions'])))
        cx.execute("INSERT INTO archive_answers(run_id,question_set,question_id,answerer,provider,model,source,reservation_id,state,answer_text,"
                   "normalised,answer_sha256,changed,created_at) VALUES (1,?,'Q01','model-a','openai','gpt-old-1','engine',1,'answered',"
                   "'Canberra.','canberra',?,NULL,'2026-10-02T09:00:00+00:00')", (data['question_set'], archive.answer_hash('Canberra.')))
        before_rows = [tuple(r) for r in cx.execute('SELECT * FROM archive_answers')]
        before_runs = [tuple(r) for r in cx.execute('SELECT * FROM archive_runs')]
    # Read-only before any migration (the observatory right after the upgrade): the old record is shown, the new fields are null.
    before = archive.public_payload(readonly=True)
    assert archive.payload_problem(before) is None and before['runs'][0]['skipped'] == {}
    assert {key: before['answers'][0][key] for key in archive.PUBLIC_CALL_FIELDS} == dict.fromkeys(archive.PUBLIC_CALL_FIELDS)
    core.init_db()                                  # what every CLI command and the app do on start
    core.init_db()                                  # idempotent
    with core.connect() as cx:
        columns = [r[1] for r in cx.execute('PRAGMA table_info(archive_answers)')]
        assert columns[-len(archive.CALL_COLUMNS):] == list(archive.CALL_COLUMNS)
        assert [tuple(r)[:len(before_rows[0])] for r in cx.execute('SELECT * FROM archive_answers')] == before_rows     # nothing rewritten
        assert [tuple(r)[len(before_rows[0]):] for r in cx.execute('SELECT * FROM archive_answers')] == [(None,) * len(archive.CALL_COLUMNS)]
        assert [tuple(r) for r in cx.execute('SELECT * FROM archive_runs')] == before_runs
        assert cx.execute('SELECT COUNT(*) FROM archive_run_answerers').fetchone()[0] == 0
        assert {'archive_run_answerers_no_update', 'archive_run_answerers_no_delete'} <= {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert archive.public_payload() == before
    # The archive carries on: run 2 on the migrated database, with a named endpoint, next to the old row.
    three_endpoints(monkeypatch, 'model-a=ollama:gemma3:4b')
    fake_sdk(monkeypatch, lambda base_url, request, n: reply('Canberra, the capital.', model='gemma3:4b'))
    done = archive.run_archive(max_usd=0.01)
    assert done['run'] == 2 and done['status'] == 'complete' and done['answers']['answered'] == 22
    payload = archive.public_payload()
    assert archive.payload_problem(payload) is None and archive.export_problem(payload) is None
    new = next(a for a in payload['answers'] if a['run'] == 2 and a['question'] == 'Q01')
    assert new['changed'] is True and new['endpoint'] == 'ollama' and new['local'] is True
    page = archive_pages.render_question(payload, 'Q01')
    assert 'gpt-old-1 · server call' in page and 'gemma3:4b · run locally · server call · new model ID' in page
    assert status_of(page) == 'ok'


def status_of(page):
    return 'ok' if '<script' not in page and 'style=' not in page else 'bad'


def test_fresh_and_migrated_databases_have_the_same_archive_columns(isolated_db):
    with core.connect() as cx:
        fresh = {t: [tuple(r)[1:3] for r in cx.execute(f'PRAGMA table_info({t})')] for t in ('archive_answers', 'archive_run_answerers')}
        for table in ('archive_run_answerers', 'archive_run_sets', 'archive_answers', 'archive_runs', 'archive_questions',
                      'archive_set_versions', 'archive_sets'):
            cx.execute(f'DROP TABLE {table}')
        cx.executescript(OLD_SCHEMA.read_text(encoding='utf-8'))
    core.init_db()
    with core.connect() as cx:
        migrated = {t: [tuple(r)[1:3] for r in cx.execute(f'PRAGMA table_info({t})')] for t in fresh}
    assert migrated == fresh


# ---- docs and settings file say what the code does ---------------------------------------------------------

def test_docs_and_env_example_describe_the_endpoint_settings():
    doc = (REPO / 'docs' / 'ANSWERS-OVER-TIME.md').read_text(encoding='utf-8')
    env = (REPO / '.env.example').read_text(encoding='utf-8')
    for text in (doc, env):
        for setting in ('LIVE_ARCHIVE_ENDPOINTS', '_BASE_URL', '_KEY_VAR', '_INPUT_USD_PER_M', '_OUTPUT_USD_PER_M', '_FREE_TIER', '_REASONING_EFFORT',
                        '_MAX_OUTPUT_TOKENS', '_SEND_SAMPLING', '_PAUSE_SECONDS'):
            assert setting in text, setting
    assert archive.FAILURE_RULE in doc and 'archive-check-endpoints' in doc and 'declared-free' in doc and 'live-genesis-archive/3' in doc
    assert 'terms' in doc and 'benchmarking' in doc and 'never re-pull' in doc and 'digest' in doc and 'never re-pull' in env
    # No key value ships in the example file: every key line is empty.
    for line in env.splitlines():
        name, eq, value = line.lstrip('# ').partition('=')
        if eq and (name.endswith('_API_KEY') or name.endswith('_TOKEN')) and not name.startswith('LIVE_'):
            assert value.strip() == '', line
    free = (REPO / 'docs' / 'FREE-MODELS.md').read_text(encoding='utf-8')
    assert 'LIVE_ARCHIVE_ENDPOINTS' in free and 'A hosted endpoint, free tier or not, still needs positive rates' not in free
