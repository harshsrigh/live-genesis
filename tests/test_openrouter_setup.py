from types import SimpleNamespace

import pytest

from live_genesis import engine, openrouter_setup as setup, providers


def row(model='example/model:free', **prices):
    return {'id': model, 'pricing': {'prompt': '0', 'completion': '0', **prices}}


@pytest.mark.parametrize('item', [row('openrouter/free'), row('example/model'), row(request='0.1'),
                                row(prompt='NaN'), row(completion='-1'), row(image='oops')])
def test_catalog_rejects_routers_paid_and_invalid_prices(item):
    assert setup.free_models({'data': [item]}) == []


def test_settings_are_compatible_disabled_and_no_overwrite(tmp_path, monkeypatch):
    available = setup.free_models({'data': [row()]})
    values = setup.settings(available, available)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    endpoint = providers.endpoints()['openrouter-free']
    assert endpoint.host == 'openrouter.ai' and endpoint.basis == providers.BASIS_FREE
    assert endpoint.input_rate == endpoint.output_rate == 0
    assert values['LIVE_ALLOW_PAID_CALLS'] == 'NO'
    out = tmp_path / 'settings.ps1'
    setup.write_settings(out, values)
    before = out.read_bytes()
    with pytest.raises(FileExistsError):
        setup.write_settings(out, values)
    assert out.read_bytes() == before


@pytest.mark.parametrize('model', ['openrouter/auto', 'openrouter/free', 'vendor/paid', 'vendor/model:free:online'])
def test_paid_or_router_model_rejected_before_request(model):
    with pytest.raises(engine.ProviderNotReady):
        engine._chat(None, model, '', 'test', 100, json_mode=False, reasoning_effort=None,
                     sampling=None, free_openrouter=True)


def test_free_request_has_zero_price_and_no_fallback():
    sent = []
    def create(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(usage=None)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(engine.UsageMissing):
        engine._chat(client, 'example/model:free', '', 'test', 100, json_mode=False,
                     reasoning_effort=None, sampling=None, free_openrouter=True)
    policy = sent[0]['extra_body']['provider']
    assert policy['allow_fallbacks'] is False
    assert all(value == 0 for value in policy['max_price'].values())
    assert 'models' not in sent[0] and 'plugins' not in sent[0]


def test_setup_refuses_duplicate_or_absent_models():
    for models in [[], ['x/y:free', 'x/y:free'], ['x/paid']]:
        with pytest.raises(ValueError):
            setup.settings(models, ['x/y:free'])


# ---- through the real engine.infer (SDK class replaced, no connection) ----------------------------

from test_archive_endpoints import KEY_CF, fake_sdk, reply  # noqa: E402

FREE_ID = 'example/model:free'


def openrouter_endpoint(monkeypatch):
    for key, value in setup.settings([FREE_ID], [FREE_ID]).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv('OPENROUTER_API_KEY', KEY_CF)
    return providers.endpoints()['openrouter-free']


def call(model, endpoint):
    return engine.infer(providers.COMPATIBLE, model, 'Question?', system='Be brief.', max_output_tokens=50,
                        endpoint=endpoint)


def test_infer_sends_the_free_only_policy_on_the_real_request_path(monkeypatch):
    endpoint = openrouter_endpoint(monkeypatch)
    seen = fake_sdk(monkeypatch)
    result = call(FREE_ID, endpoint)
    assert result.text == 'Seven.' and len(seen) == 1
    sent = seen[0]
    policy = sent['request']['extra_body']['provider']
    assert policy['allow_fallbacks'] is False
    assert policy['max_price'] == {'prompt': 0, 'completion': 0, 'request': 0, 'image': 0}
    assert sent['max_retries'] == 0 and sent['follow_redirects'] is False
    assert sent['request']['model'] == FREE_ID
    assert not {'models', 'plugins', 'tools', 'route', 'transforms'} & set(sent['request'])
    assert not {'models', 'plugins', 'route'} & set(sent['request']['extra_body'])


@pytest.mark.parametrize('model', ['openrouter/auto', 'openrouter/free', 'vendor/paid', 'vendor/model:free:online'])
def test_infer_rejects_a_non_free_or_router_id_before_any_sdk_call(monkeypatch, model):
    endpoint = openrouter_endpoint(monkeypatch)
    seen = fake_sdk(monkeypatch)
    with pytest.raises(engine.ProviderNotReady):
        call(model, endpoint)
    assert seen == []


@pytest.mark.parametrize('cost', [0.0001, '0.5', 'NaN', 'oops', float('inf')])
def test_a_reported_charge_on_a_free_call_is_not_recorded_as_free(monkeypatch, cost):
    endpoint = openrouter_endpoint(monkeypatch)
    response = reply(model=FREE_ID)
    response.usage.cost = cost
    fake_sdk(monkeypatch, lambda *a: response)
    with pytest.raises(engine.UsageMissing):
        call(FREE_ID, endpoint)


def test_a_charge_kept_as_an_sdk_extra_is_also_caught(monkeypatch):
    endpoint = openrouter_endpoint(monkeypatch)
    response = reply(model=FREE_ID)
    response.usage.model_extra = {'cost': 0.01}
    fake_sdk(monkeypatch, lambda *a: response)
    with pytest.raises(engine.UsageMissing):
        call(FREE_ID, endpoint)


@pytest.mark.parametrize('cost', [0, 0.0, '0', None])
def test_zero_or_absent_cost_is_accepted_but_absence_is_not_proof(monkeypatch, cost):
    endpoint = openrouter_endpoint(monkeypatch)
    response = reply(model=FREE_ID)
    response.usage.cost = cost
    fake_sdk(monkeypatch, lambda *a: response)
    assert call(FREE_ID, endpoint).text == 'Seven.'


def test_another_free_endpoint_is_not_given_the_openrouter_policy(monkeypatch):
    monkeypatch.setenv('LIVE_ARCHIVE_ENDPOINTS', 'goog')
    monkeypatch.setenv('LIVE_ARCHIVE_ENDPOINT_GOOG_BASE_URL', 'https://goog.example.invalid/v1')
    monkeypatch.setenv('LIVE_ARCHIVE_ENDPOINT_GOOG_KEY_VAR', 'GOOG_TEST_API_KEY')
    monkeypatch.setenv('LIVE_ARCHIVE_ENDPOINT_GOOG_FREE_TIER', 'YES')
    monkeypatch.setenv('GOOG_TEST_API_KEY', 'SENTINEL-GOOG')
    endpoint = providers.endpoints()['goog']
    seen = fake_sdk(monkeypatch)
    call('google/gemma:free', endpoint)
    assert 'extra_body' not in seen[0]['request']


@pytest.mark.parametrize('data', [None, [], {}, {'data': None}, {'data': 'x'}, {'data': {'a': 1}}])
def test_a_catalog_without_a_row_list_is_a_clean_error(data):
    with pytest.raises(ValueError):
        setup.free_models(data)


def test_malformed_rows_are_skipped_not_fatal():
    def with_arch(model, architecture):
        return {**row(model), 'architecture': architecture}
    rows = [None, 'x', 7, [], {}, {'id': None, 'pricing': {}}, {'id': 'a/b:free', 'pricing': None},
            {'id': 'a/b:free', 'pricing': {'prompt': None, 'completion': '0'}},
            with_arch('a/c:free', 'text'), with_arch('a/d:free', {'output_modalities': 'text'}),
            with_arch('a/e:free', {'output_modalities': ['image']}), row()]
    # A non-dict architecture is treated as unstated (text); a malformed or non-text modality list is skipped.
    assert setup.free_models({'data': rows}) == ['a/c:free', 'example/model:free']


# ---- Free-only rules that hold on every path, not only for the exact host with a free declaration ----
import json
import sys

FREE_A, FREE_B = 'example/model-a:free', 'example/model-b:free'
CATALOG = {'data': [row(FREE_A), row(FREE_B), row('example/paid'), row('openrouter/free')]}


def configure(monkeypatch, models=(FREE_A,)):
    for key, value in setup.settings(list(models), setup.free_models(CATALOG)).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv('OPENROUTER_API_KEY', 'SENTINEL-OPENROUTER-KEY-51ce')


@pytest.mark.parametrize('host,expected', [('openrouter.ai', True), ('OpenRouter.AI', True), ('openrouter.ai.', True),
                                           ('api.openrouter.ai', True), ('notopenrouter.ai', False),
                                           ('openrouter.ai.example.invalid', False), (None, False)])
def test_openrouter_host_rule_has_no_spelling_that_gets_around_it(host, expected):
    assert providers.is_openrouter_host(host) is expected


@pytest.mark.parametrize('model', ['openrouter/free', 'openrouter/auto', 'openrouter/auto:free', 'vendor/paid',
                                   'vendor/model:free:online', 'vendor/model:FREE', 'vendor/model:free ', 'model:free', None])
def test_only_an_explicit_vendor_model_free_id_is_accepted(model):
    assert not providers.openrouter_free_model_ok(model)
    assert providers.openrouter_free_model_ok('vendor/model-1.5:free')


@pytest.mark.parametrize('url', ['https://openrouter.ai/api/v1', 'https://OPENROUTER.ai./api/v1', 'https://eu.openrouter.ai/api/v1'])
def test_a_priced_or_undeclared_openrouter_endpoint_is_refused(monkeypatch, url):
    configure(monkeypatch)
    monkeypatch.setenv('LIVE_ARCHIVE_ENDPOINT_OPENROUTER_FREE_BASE_URL', url)
    assert providers.endpoints()['openrouter-free'].basis == providers.BASIS_FREE
    monkeypatch.setenv('LIVE_ARCHIVE_ENDPOINT_OPENROUTER_FREE_INPUT_USD_PER_M', '0.5')
    monkeypatch.setenv('LIVE_ARCHIVE_ENDPOINT_OPENROUTER_FREE_OUTPUT_USD_PER_M', '1.5')
    with pytest.raises(providers.ProviderConfigError, match='free models only') as err:
        providers.endpoints()
    assert 'openrouter.ai/api' not in str(err.value).lower()
    monkeypatch.setenv('LIVE_ARCHIVE_ENDPOINT_OPENROUTER_FREE_INPUT_USD_PER_M', '0')
    monkeypatch.setenv('LIVE_ARCHIVE_ENDPOINT_OPENROUTER_FREE_OUTPUT_USD_PER_M', '0')
    monkeypatch.setenv('LIVE_ARCHIVE_ENDPOINT_OPENROUTER_FREE_FREE_TIER', 'NO')
    with pytest.raises(providers.ProviderConfigError, match='free models only'):
        providers.endpoints()


def test_the_single_compatible_provider_cannot_point_at_openrouter(monkeypatch):
    monkeypatch.setenv('LIVE_COMPAT_BASE_URL', 'https://openrouter.ai/api/v1')
    with pytest.raises(providers.ProviderConfigError, match='free'):
        providers.compat_settings()


@pytest.mark.parametrize('model', ['openrouter/free', 'openrouter/auto', 'example/paid', 'example/model-a'])
def test_the_plan_refuses_a_paid_or_router_model_before_a_run_exists(monkeypatch, model):
    from live_genesis import archive
    configure(monkeypatch)
    answerers = [{'label': 'm', 'provider': providers.COMPATIBLE, 'model': model, 'endpoint': 'openrouter-free'}]
    with pytest.raises(archive.ArchiveRefused, match='vendor/model:free'):
        archive.resolve(answerers, 120)
    assert archive.resolve([{**answerers[0], 'model': FREE_A}], 120)[0][0]['basis'] == providers.BASIS_FREE


def test_a_charge_is_its_own_error_so_the_archive_can_stop_on_it():
    def create(**kwargs):
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2, cost=0.0004), choices=[])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(engine.UnexpectedCharge):
        engine._chat(client, FREE_A, '', 'test', 100, json_mode=False, reasoning_effort=None, sampling=None, free_openrouter=True)
    assert issubclass(engine.UnexpectedCharge, engine.UsageMissing)


def test_check_says_when_a_configured_model_is_no_longer_free(tmp_path, monkeypatch, capsys):
    configure(monkeypatch, (FREE_A, FREE_B))
    assert setup.configured_models() == [FREE_A, FREE_B]
    saved = tmp_path / 'catalog.json'
    saved.write_text(json.dumps(CATALOG), encoding='utf-8')
    monkeypatch.setattr(setup, 'catalog', lambda: (_ for _ in ()).throw(AssertionError('no network in tests')))
    monkeypatch.setattr(sys, 'argv', ['openrouter_setup', '--check', '--catalog', str(saved)])
    setup.main()
    assert 'All configured OpenRouter models are listed free' in capsys.readouterr().out
    saved.write_text(json.dumps({'data': [row(FREE_A), row(FREE_B, completion='0.0000002')]}), encoding='utf-8')
    with pytest.raises(SystemExit) as stop:
        setup.main()
    printed = capsys.readouterr()
    assert stop.value.code == 1 and f'{FREE_B}: NOT listed free now' in printed.out and f'{FREE_A}: still listed free' in printed.out
    assert 'SENTINEL' not in printed.out + printed.err
