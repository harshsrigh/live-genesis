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


# ---- The request path, not only the setup helper ------------------------------------------------
import json
import sys

from live_genesis import archive, archive_pages, core

from test_archive import ready, rows, run_cli  # noqa: F401,E402
from test_archive_endpoints import Status, every_cell, fake_sdk, reply  # noqa: E402

KEY = 'SENTINEL-OPENROUTER-KEY-51ce'
FREE_A, FREE_B = 'example/model-a:free', 'example/model-b:free'
CATALOG = {'data': [row(FREE_A), row(FREE_B), row('example/paid'), row('openrouter/free'), row('openrouter/auto')]}


def configure(monkeypatch, models=(FREE_A,)):
    for name in ('LIVE_INPUT_USD_PER_M', 'LIVE_OUTPUT_USD_PER_M'):
        monkeypatch.delenv(name, raising=False)
    for key, value in setup.settings(list(models), setup.free_models(CATALOG)).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv('OPENROUTER_API_KEY', KEY)
    monkeypatch.setattr(providers.config, 'load_provider_credentials', lambda: [])   # never read a developer's .env


def with_cost(response, cost):
    response.usage.cost = cost
    return response


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
def test_a_priced_or_undeclared_openrouter_endpoint_is_refused(isolated_db, monkeypatch, url):
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


def test_the_single_compatible_provider_cannot_point_at_openrouter(isolated_db, monkeypatch):
    # `round` and `study-run` use this provider at positive rates: OpenRouter is not reachable that way at all.
    monkeypatch.setenv('LIVE_COMPAT_BASE_URL', 'https://openrouter.ai/api/v1')
    with pytest.raises(providers.ProviderConfigError, match='free'):
        providers.compat_settings()
    sent = fake_sdk(monkeypatch)
    with pytest.raises(engine.ProviderNotReady):
        engine.infer(providers.COMPATIBLE, FREE_A, 'hello')
    assert sent == []


@pytest.mark.parametrize('model', ['openrouter/free', 'openrouter/auto', 'example/paid', 'example/model-a'])
def test_plan_and_run_refuse_a_paid_or_router_model_before_anything_is_sent(ready, monkeypatch, capsys, model):
    configure(monkeypatch)
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', f'm=openrouter-free:{model}')
    archive.lock()
    sent = fake_sdk(monkeypatch)
    code, out = run_cli(monkeypatch, capsys, 'archive-plan')
    assert code != 0 and 'vendor/model:free' in out
    monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS', 'YES')
    with pytest.raises(archive.ArchiveRefused, match='vendor/model:free'):
        archive.run_archive(max_usd=0.01)
    assert sent == [] and rows('SELECT * FROM archive_runs') == [] and rows('SELECT * FROM budget_reservations') == []


@pytest.mark.parametrize('cost,charged', [(None, False), (0, False), (0.0, False), (0.000001, True), (3, True),
                                          ('0', True), (float('nan'), True), (True, True)])
def test_a_reported_cost_above_zero_is_an_unexpected_charge(cost, charged):
    def create(**kwargs):
        return with_cost(reply(), cost)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    call = lambda: engine._chat(client, FREE_A, '', 'test', 100, json_mode=False, reasoning_effort=None,
                                sampling=None, free_openrouter=True)
    if charged:
        with pytest.raises(engine.UnexpectedCharge):
            call()
    else:
        assert call().text == 'Seven.'


def test_check_says_when_a_configured_model_is_no_longer_free(isolated_db, tmp_path, monkeypatch, capsys):
    configure(monkeypatch, (FREE_A, FREE_B))
    assert setup.configured_models() == [FREE_A, FREE_B]
    saved = tmp_path / 'catalog.json'
    saved.write_text(json.dumps(CATALOG), encoding='utf-8')
    monkeypatch.setattr(setup, 'catalog', lambda: (_ for _ in ()).throw(AssertionError('no network in tests')))
    monkeypatch.setattr(sys, 'argv', ['openrouter_setup', '--check', '--catalog', str(saved)])
    setup.main()
    assert 'All configured OpenRouter models are listed free' in capsys.readouterr().out
    # The price of one model changes: it is no longer eligible, and the check fails without sending anything.
    saved.write_text(json.dumps({'data': [row(FREE_A), row(FREE_B, completion='0.0000002')]}), encoding='utf-8')
    with pytest.raises(SystemExit) as stop:
        setup.main()
    printed = capsys.readouterr()
    assert stop.value.code == 1 and f'{FREE_B}: NOT listed free now' in printed.out and f'{FREE_A}: still listed free' in printed.out
    assert KEY not in printed.out + printed.err


def test_settings_file_for_bash_and_powershell(tmp_path):
    values = setup.settings([FREE_A], setup.free_models(CATALOG))
    setup.write_settings(tmp_path / 'a.ps1', values)
    setup.write_settings(tmp_path / 'a.sh', values, 'bash')
    ps, sh = (tmp_path / 'a.ps1').read_text(encoding='utf-8'), (tmp_path / 'a.sh').read_text(encoding='utf-8')
    assert "$env:LIVE_ALLOW_PAID_CALLS = 'NO'" in ps and "export LIVE_ALLOW_PAID_CALLS='NO'" in sh
    assert 'OPENROUTER_API_KEY=' not in sh.replace("KEY_VAR='OPENROUTER_API_KEY'", '') and 'sk-' not in ps + sh


def test_the_whole_free_only_workflow_with_mocked_answers_and_a_disposable_database(ready, monkeypatch, capsys):
    """discover -> select -> configure -> preview -> approved collection -> inspect -> second collection -> compare,
    then the three ways it can go wrong: a rate limit, an answer without usage, and a reported charge."""
    configure(monkeypatch, (FREE_A, FREE_B))
    archive.lock()
    slept = []
    monkeypatch.setattr(archive.time, 'sleep', slept.append)      # the generated settings pause 4 s between calls
    month = {'n': 1}

    def script(base_url, request, n):
        text = 'Seven.' if request['model'] == FREE_A or month['n'] == 1 else 'Eight, on reflection.'
        return with_cost(reply(text=text, model=request['model']), 0)
    seen = fake_sdk(monkeypatch, script)

    code, plan = run_cli(monkeypatch, capsys, 'archive-plan')
    assert code == 0 and 'declared-free' in plan and 'openrouter-free:' + FREE_A in plan and seen == []
    calls = len(archive.plan()['questions']) * 2
    assert f'Calls per run: {calls} ' in plan

    # The settings file leaves the opt-in off: nothing is sent until the owner turns it on for a concrete run.
    code, refused = run_cli(monkeypatch, capsys, 'archive-run', '--max-usd', '0.01')
    assert code != 0 and 'LIVE_ALLOW_PAID_CALLS=YES' in refused and seen == []
    monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS', 'YES')

    code, first = run_cli(monkeypatch, capsys, 'archive-run', '--max-usd', '0.01')
    assert code == 0 and '"status": "complete"' in first and len(seen) == calls
    assert slept and set(slept) == {4.0}
    for call in seen:
        assert call['base_url'] == setup.BASE and call['api_key'] == KEY
        assert (call['follow_redirects'], call['max_retries']) == (False, 0)
        request = call['request']
        assert request['model'] in (FREE_A, FREE_B) and 'models' not in request and 'plugins' not in request
        assert request['extra_body'] == {'provider': {'allow_fallbacks': False,
                                                      'max_price': {'prompt': 0, 'completion': 0, 'request': 0, 'image': 0}}}
        assert 'temperature' not in request and 'seed' not in request     # SEND_SAMPLING=NO in the generated settings
    stored = rows("SELECT endpoint,model,reported_model,pricing_basis,estimated_usd AS usd,source FROM archive_answers WHERE question_id='Q01' ORDER BY answerer")
    assert stored == [{'endpoint': 'openrouter-free', 'model': m, 'reported_model': m, 'pricing_basis': 'declared-free',
                       'usd': 0.0, 'source': 'engine'} for m in (FREE_A, FREE_B)]
    assert {h['reserved_usd'] for h in rows('SELECT reserved_usd FROM budget_reservations')} == {0.0}

    # Second collection: one model words its answer differently, the other does not.
    month['n'] = 2
    code, second = run_cli(monkeypatch, capsys, 'archive-run', '--max-usd', '0.01')
    assert code == 0 and '"status": "complete"' in second
    payload = archive.public_payload()
    assert archive.payload_problem(payload) is None and 'chatbots' not in payload      # model track only: never a chat-app reply
    latest = {(a['answerer'], a['question']): a for a in payload['answers'] if a['run'] == payload['runs'][-1]['number']}
    assert latest[('or-free-1', 'Q01')]['changed'] is False and latest[('or-free-2', 'Q01')]['changed'] is True
    page = archive_pages.render_question(payload, 'Q01')
    assert 'Eight, on reflection.' in page and 'Seven.' in page and 'via openrouter-free' in page
    for name in ('ChatGPT', 'Gemini', 'Grok'):
        assert name not in page
    code, status = run_cli(monkeypatch, capsys, 'archive-status')
    assert [r['status'] for r in json.loads(status)['runs']] == ['complete', 'complete']

    # A rate limit is a gap for that model, not a changed answer; the other model is still asked; nothing is retried.
    before = len(seen)
    seen_script = lambda base_url, request, n: Status(429) if request['model'] == FREE_A else with_cost(reply(text='Eight, on reflection.', model=request['model']), 0)
    seen = fake_sdk(monkeypatch, seen_script)
    code, third = run_cli(monkeypatch, capsys, 'archive-run', '--max-usd', '0.01')
    assert code == 0 and 'COMPLETE WITH GAPS' in third and 'or-free-1 (rate_limited)' in third
    assert sum(1 for c in seen if c['request']['model'] == FREE_A) == 1
    payload = archive.public_payload()
    run3 = payload['runs'][-1]
    assert run3['skipped'] == {'or-free-1': 'rate_limited'}
    assert not any(a['answerer'] == 'or-free-1' and a['run'] == run3['number'] and a.get('changed') for a in payload['answers'])

    # An answer without token counts is a gap too.
    seen = fake_sdk(monkeypatch, lambda base_url, request, n: reply(usage=None) if request['model'] == FREE_B
                    else with_cost(reply(model=request['model']), 0))
    code, fourth = run_cli(monkeypatch, capsys, 'archive-run', '--max-usd', '0.01')
    assert code == 0 and 'or-free-2 (no_usage)' in fourth
    assert core.budget_status()['unconfirmed_reservations'] == []

    # A reported charge on a free-only call: the answer is NOT recorded, the run STOPS, and nothing more is sent
    # (not to the other model either) until the owner has looked at the billing page and reconciled.
    answers_before = rows('SELECT COUNT(*) AS n FROM archive_answers')[0]['n']
    seen = fake_sdk(monkeypatch, lambda base_url, request, n: with_cost(reply(model=request['model']), 0.0004))
    code, fifth = run_cli(monkeypatch, capsys, 'archive-run', '--max-usd', '0.01')
    assert 'reported a charge' in fifth and 'cli reconcile' in fifth and len(seen) == 1
    assert rows('SELECT COUNT(*) AS n FROM archive_answers')[0]['n'] == answers_before
    assert len(core.budget_status()['unconfirmed_reservations']) == 1
    assert any('reported a charge on a free-only call' in r['operation'] for r in rows('SELECT operation FROM operator_audit'))
    seen = fake_sdk(monkeypatch, script)
    code, blocked = run_cli(monkeypatch, capsys, 'archive-run', '--max-usd', '0.01')
    assert code != 0 and 'reconcile' in blocked and seen == []

    # The key is in no table and no printed line.
    for where, text in {'database': every_cell(), 'plan': plan, 'first': first, 'second': second, 'third': third,
                        'fourth': fourth, 'fifth': fifth, 'blocked': blocked, 'status': status}.items():
        assert KEY not in text, where
