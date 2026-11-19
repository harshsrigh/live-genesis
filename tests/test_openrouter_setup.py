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
