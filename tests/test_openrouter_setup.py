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
