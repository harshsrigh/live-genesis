"""Output limit: truncation detection, LIVE_MAX_OUTPUT_TOKENS, prompt guidance, engine-turn diagnostics.

Offline. `engine.infer` or the SDK client classes are always replaced; no model is called.
"""
import json
import sqlite3
import sys
from types import SimpleNamespace

import openai
import pytest

from live_genesis import agentkit, cli, controls, documents, engine, governance, memory, providers, snapshot, study
from live_genesis.core import connect, init_db, overview, seed_agents

GOOD = '{"action":"post","topic":"research","title":"Mocked","body":"Offline mocked provider response.","memory":"kept note"}'
CUT = '{"action":"post","topic":"research","title":"Mocked","body":"This reply stops in the middle of a sent'


def post(name, **extra):
    return json.dumps({'action': 'post', 'topic': 'research', 'title': f'Sample by {name}',
                       'body': f'A mocked provider response for {name} in an offline test.', **extra})


def mock_infer(monkeypatch, reply):
    """Replace engine.infer; `reply(name, prompt)` returns a Completion."""
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    calls = []

    def infer(provider, model, prompt):
        name = json.loads(prompt)['identity']['name']
        calls.append(name)
        return reply(name, prompt)
    monkeypatch.setattr(engine, 'infer', infer)
    return calls


def rows(table):
    with connect() as cx:
        return [dict(r) for r in cx.execute(f'SELECT * FROM {table} ORDER BY id')]


def count(table):
    with connect() as cx:
        return cx.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]


# ---- 1. Adapters normalise the stop reason -------------------------------------------------

def fake_openai(monkeypatch, *, chat=None, response=None):
    seen = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
            self.responses = SimpleNamespace(create=self.create_response)

        def create(self, **kwargs):
            seen.append(kwargs)
            return chat

        def create_response(self, **kwargs):
            seen.append(kwargs)
            return response
    monkeypatch.setattr(openai, 'OpenAI', FakeOpenAI)
    return seen


def fake_anthropic(monkeypatch, response):
    seen = []

    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = SimpleNamespace(create=self.create)

        def create(self, **kwargs):
            seen.append(kwargs)
            return response
    monkeypatch.setitem(sys.modules, 'anthropic', SimpleNamespace(Anthropic=FakeAnthropic))
    return seen


def chat_reply(**choice):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=CUT), **choice)],
                           usage=SimpleNamespace(prompt_tokens=200, completion_tokens=50))


def responses_reply(**fields):
    return SimpleNamespace(output_text=CUT, usage=SimpleNamespace(input_tokens=200, output_tokens=50), **fields)


def anthropic_reply(**fields):
    return SimpleNamespace(content=[SimpleNamespace(type='thinking', thinking=''), SimpleNamespace(type='text', text=CUT)],
                           usage=SimpleNamespace(input_tokens=200, output_tokens=50), **fields)


@pytest.mark.parametrize('choice,expected', [
    ({'finish_reason': 'length'}, 'length'), ({'finish_reason': 'stop'}, 'end'),
    ({'finish_reason': 'content_filter'}, 'other'), ({'finish_reason': None}, 'unknown'), ({}, 'unknown')])
def test_chat_completions_stop_is_normalised(monkeypatch, choice, expected):
    monkeypatch.setenv('LIVE_COMPAT_BASE_URL', 'http://localhost:11434/v1')
    monkeypatch.delenv('LIVE_MAX_OUTPUT_TOKENS', raising=False)
    seen = fake_openai(monkeypatch, chat=chat_reply(**choice))
    done = engine.infer('openai-compatible', 'm', 'p')
    assert done.stop == expected and (done.input_tokens, done.output_tokens) == (200, 50)
    assert seen[0]['max_tokens'] == engine.MAX_OUTPUT_TOKENS == 1200


@pytest.mark.parametrize('fields,expected', [
    ({'status': 'incomplete', 'incomplete_details': SimpleNamespace(reason='max_output_tokens')}, 'length'),
    ({'status': 'incomplete', 'incomplete_details': SimpleNamespace(reason='content_filter')}, 'other'),
    ({'status': 'incomplete'}, 'other'), ({'status': 'completed'}, 'end'), ({}, 'unknown')])
def test_openai_responses_stop_is_normalised(monkeypatch, fields, expected):
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    monkeypatch.setenv('LIVE_MAX_OUTPUT_TOKENS', '900')
    seen = fake_openai(monkeypatch, response=responses_reply(**fields))
    assert engine.infer('openai', 'm', 'p').stop == expected
    assert seen[0]['max_output_tokens'] == 900 and seen[0]['instructions'] == engine.constitution(900)


@pytest.mark.parametrize('fields,expected', [
    ({'stop_reason': 'max_tokens'}, 'length'), ({'stop_reason': 'end_turn'}, 'end'), ({'stop_reason': 'stop_sequence'}, 'end'),
    ({'stop_reason': 'refusal'}, 'other'), ({'stop_reason': None}, 'unknown'), ({}, 'unknown')])
def test_anthropic_stop_is_normalised(monkeypatch, fields, expected):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'fake-key-never-used')
    monkeypatch.setenv('LIVE_MAX_OUTPUT_TOKENS', '2000')
    seen = fake_anthropic(monkeypatch, anthropic_reply(**fields))
    done = engine.infer('anthropic', 'm', 'p')
    assert done.stop == expected and done.text == CUT
    assert seen[0]['max_tokens'] == 2000 and seen[0]['system'] == engine.constitution(2000)


def test_completion_keeps_its_positional_form_and_study_arguments_win(monkeypatch):
    assert engine.Completion('t', 1, 2).stop == 'unknown'
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    monkeypatch.setenv('LIVE_MAX_OUTPUT_TOKENS', '3000')
    seen = fake_openai(monkeypatch, response=responses_reply())
    engine.infer('openai', 'm', 'p', system='study system', max_output_tokens=650)
    assert seen[0]['max_output_tokens'] == 650 and seen[0]['instructions'] == 'study system'


# ---- 2. A truncated turn ------------------------------------------------------------------

def test_truncated_turn_records_nothing_settles_usage_and_keeps_the_receipt(isolated_db, monkeypatch):
    seed_agents()
    calls = mock_infer(monkeypatch, lambda name, prompt: engine.Completion(CUT, 2545, 1200, 'length') if name == 'Astra'
                       else engine.Completion(GOOD, 100, 50, 'end'))
    out = engine.run_round(limit=2)
    assert calls == ['Astra', 'Moss']  # the round continues after a truncated turn
    cut = out[0]
    assert cut['error'] == engine.TRUNCATED == 'reply truncated at the output limit'
    assert (cut['agent'], cut['input_tokens'], cut['output_tokens'], cut['output_limit']) == ('Astra', 2545, 1200, 1200)
    assert cut['estimated_usd'] == pytest.approx((2545 * 0.20 + 1200 * 1.20) / 1e6)
    assert 'This reply' not in json.dumps(out) and 'sent' not in cut['error']  # none of the model's text
    assert out[1]['action'] == 'post' and out[1]['memory'] == 'written'
    holds = rows('budget_reservations')
    assert [h['status'] for h in holds] == ['settled', 'settled'] and holds[0]['id'] == cut['reservation']
    assert [h['used'] for h in holds] == [0, 1]  # the truncated turn's receipt was never consumed
    assert count('posts') == 1 and count('api_usage') == 2
    with connect() as cx:
        assert cx.execute("SELECT memory FROM agents WHERE name='Astra'").fetchone()[0] == ''
        assert cx.execute("SELECT COUNT(*) FROM memory_notes m JOIN agents a ON a.id=m.agent_id WHERE a.name='Astra'").fetchone()[0] == 0
        assert cx.execute("SELECT COUNT(*) FROM events e JOIN agents a ON a.id=e.agent_id WHERE a.name='Astra'").fetchone()[0] == 0
    first, second = rows('engine_turns')
    assert (first['outcome'], first['stop'], first['input_tokens'], first['output_tokens'], first['reservation_id'], first['memory']) == \
        ('truncated', 'length', 2545, 1200, cut['reservation'], 'none')
    assert (second['outcome'], second['stop'], second['memory'], second['memory_refusal']) == ('recorded', 'end', 'written', None)
    assert first['round_id'] == second['round_id'] == holds[0]['round_id']
    assert 'reconcile' not in json.dumps(engine.run_round(limit=1))  # nothing is left unconfirmed


def test_truncated_reply_that_happens_to_parse_is_still_not_recorded(isolated_db, monkeypatch):
    seed_agents()
    mock_infer(monkeypatch, lambda name, prompt: engine.Completion(GOOD, 100, 1200, 'length'))
    out = engine.run_round(limit=1)
    assert out[0]['error'] == engine.TRUNCATED and count('posts') == 0 and rows('engine_turns')[0]['outcome'] == 'truncated'


def test_unknown_stop_falls_through_to_the_json_check(isolated_db, monkeypatch):
    seed_agents()
    replies = {'Astra': engine.Completion(CUT, 100, 50),            # stop absent: malformed, but not known to be truncated
               'Moss': engine.Completion(GOOD, 100, 50),            # stop absent and valid: recorded
               'Quill': engine.Completion('{"action":"vote","proposal_id":"seven","choice":"yes"}', 100, 50, 'end'),
               'Vela': engine.Completion('[1, 2]', 100, 50, 'other')}
    mock_infer(monkeypatch, lambda name, prompt: replies[name])
    out = engine.run_round(limit=4)
    assert out[0] == {'agent': 'Astra', 'error': engine.NOT_JSON, 'input_tokens': 100, 'output_tokens': 50,
                      'estimated_usd': out[0]['estimated_usd'], 'reservation': out[0]['reservation']}
    assert engine.NOT_JSON == 'reply was not one valid JSON object'
    assert out[1]['action'] == 'post'
    assert out[2] == {'agent': 'Quill', 'error': 'ValidationError', 'refusal': 'schema:proposal_id'}  # schema failure keeps its own error
    assert out[3]['error'] == engine.NOT_JSON
    assert [(t['outcome'], t['stop']) for t in rows('engine_turns')] == [
        ('invalid_json', 'unknown'), ('recorded', 'unknown'), ('invalid_schema', 'end'), ('invalid_json', 'other')]
    assert count('api_usage') == 4 and count('posts') == 1  # every call was settled; one action recorded
    assert 'stops in the middle' not in json.dumps(out)


# ---- 3. The setting: clamping, hold, guidance ---------------------------------------------

def test_setting_default_and_clamping(monkeypatch):
    monkeypatch.delenv('LIVE_MAX_OUTPUT_TOKENS', raising=False)
    assert engine.max_output_tokens() == engine.MAX_OUTPUT_TOKENS == providers.DEFAULT_MAX_OUTPUT_TOKENS == 1200
    for raw, expected in (('', 1200), (' 800 ', 800), ('1', 200), ('199', 200), ('200', 200), ('4000', 4000), ('99999', 4000), ('-5', 200)):
        monkeypatch.setenv('LIVE_MAX_OUTPUT_TOKENS', raw)
        assert engine.max_output_tokens() == expected
    for bad in ('many', '1e3', '1200.5', 'nan'):
        monkeypatch.setenv('LIVE_MAX_OUTPUT_TOKENS', bad)
        with pytest.raises(providers.ProviderConfigError, match='LIVE_MAX_OUTPUT_TOKENS'):
            engine.max_output_tokens()


def test_unusable_setting_stops_the_round_before_anything_is_opened(isolated_db, monkeypatch):
    seed_agents()
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    monkeypatch.setenv('LIVE_MAX_OUTPUT_TOKENS', 'lots')
    monkeypatch.setattr(engine, 'infer', lambda *a, **k: pytest.fail('no call may be made'))
    before = count('rounds')
    with pytest.raises(RuntimeError, match='LIVE_MAX_OUTPUT_TOKENS'):
        engine.run_round(limit=1)
    assert count('rounds') == before and count('budget_reservations') == 0


def test_hold_grows_with_the_setting(monkeypatch):
    prompt = 'x' * 5000
    monkeypatch.delenv('LIVE_MAX_OUTPUT_TOKENS', raising=False)
    default = engine.call_hold(prompt, 0.20, 1.20)
    assert default == pytest.approx((len(engine.CONSTITUTION.encode()) + 5000 + study.INPUT_TOKEN_MARGIN) * 0.20 / 1e6 + 1200 * 1.20 / 1e6)
    monkeypatch.setenv('LIVE_MAX_OUTPUT_TOKENS', '4000')
    raised = engine.call_hold(prompt, 0.20, 1.20)
    system = engine.constitution(4000)
    assert raised == pytest.approx((len(system.encode()) + 5000 + study.INPUT_TOKEN_MARGIN) * 0.20 / 1e6 + 4000 * 1.20 / 1e6)
    assert raised - default == pytest.approx(2800 * 1.20 / 1e6, abs=1e-8)
    # Worst case at the highest setting: still well under two cents a call at the example rates.
    assert engine.call_hold('x' * (engine.INPUT_BYTE_LIMIT - len(system.encode())), 0.20, 1.20) == pytest.approx(
        (engine.INPUT_BYTE_LIMIT + study.INPUT_TOKEN_MARGIN) * 0.20 / 1e6 + 4000 * 1.20 / 1e6)


@pytest.mark.parametrize('setting', ['200', '1200', '4000'])
def test_hold_bounds_a_call_that_uses_the_full_output_limit(isolated_db, monkeypatch, setting):
    seed_agents()
    monkeypatch.setenv('LIVE_MAX_OUTPUT_TOKENS', setting)
    limit = int(setting)
    system = engine.constitution(limit)
    sent = []

    def reply(name, prompt):
        sent.append(prompt)
        # Worst case the hold must cover: one input token per byte of system + prompt, and every output token used.
        return engine.Completion(GOOD, len(system.encode()) + len(prompt.encode()), limit, 'end')
    mock_infer(monkeypatch, reply)
    out = engine.run_round(limit=1)
    hold, = rows('budget_reservations')
    assert len(system.encode()) + len(sent[0].encode()) <= engine.INPUT_BYTE_LIMIT
    assert hold['status'] == 'settled' and out[0]['action'] == 'post'
    assert hold['reserved_usd'] == pytest.approx(engine.call_hold(sent[0], 0.20, 1.20))
    assert out[0]['estimated_usd'] <= hold['reserved_usd']
    assert hold['reserved_usd'] >= limit * 1.20 / 1e6


def test_guidance_follows_the_setting_and_is_part_of_the_bounded_prompt(monkeypatch):
    monkeypatch.delenv('LIVE_MAX_OUTPUT_TOKENS', raising=False)
    assert engine.constitution() == engine.CONSTITUTION and engine.CONSTITUTION.endswith(engine.output_guidance(1200))
    assert 'within 1200 output tokens' in engine.CONSTITUTION
    assert engine.reply_budget() == {'tokens': 1200, 'memory': 500, 'summary': 200, 'post_body': 1200,
                                     'proposal_body': 1400, 'document_body': 2000}
    monkeypatch.setenv('LIVE_MAX_OUTPUT_TOKENS', '650')
    small = engine.reply_budget()
    assert small['tokens'] == 650 and small['post_body'] < 1200 and small['document_body'] < 2000 and small['memory'] < 500
    text = engine.constitution()
    assert text != engine.CONSTITUTION and 'within 650 output tokens' in text
    for key in ('post_body', 'proposal_body', 'document_body', 'summary', 'memory'):
        assert f' {small[key]} ' in text or f' {small[key]}.' in text or f' {small[key]}\n' in text
    # The guidance counts against the input bound: system + default prompt limit is exactly the bound.
    assert len(text.encode()) + (engine.INPUT_BYTE_LIMIT - len(text.encode())) == engine.INPUT_BYTE_LIMIT
    assert len(engine.CONSTITUTION.encode()) + engine.PROMPT_BYTE_LIMIT == engine.INPUT_BYTE_LIMIT
    prompt = engine.build_prompt({'identity': {'name': 'Astra'}, 'recent_posts': [{'id': i, 'body': 'b' * 1200} for i in range(40)]})
    assert len(text.encode()) + len(prompt.encode()) <= engine.INPUT_BYTE_LIMIT


def test_guidance_never_promises_more_than_the_validators_or_the_limit_allow():
    previous = None
    for tokens in range(providers.MIN_MAX_OUTPUT_TOKENS, providers.MAX_MAX_OUTPUT_TOKENS + 1, 25):
        b = engine.reply_budget(tokens)
        assert 0 < b['post_body'] <= min(agentkit.POST_BODY_CHARS, governance.REPORT_CHARS)
        assert 0 < b['proposal_body'] <= agentkit.PROPOSAL_BODY_CHARS
        assert 0 < b['document_body'] <= documents.BODY_CHARS
        assert 0 < b['summary'] <= documents.SUMMARY_CHARS
        assert 0 < b['memory'] <= engine.MEMORY_REPLY_CHARS <= memory.MEMORY_CHARS
        # The largest reply the guidance allows fits the limit at 2.5 characters per token.
        largest = max(b['post_body'], b['proposal_body'], b['document_body']) + b['summary'] + b['memory'] + engine.REPLY_OVERHEAD_CHARS
        assert largest <= tokens * engine.CHARS_PER_OUTPUT_TOKEN
        if previous:
            assert all(b[k] >= previous[k] for k in b)  # never shrinks as the limit rises
        previous = b
    assert engine.reply_budget(4000)['document_body'] == documents.BODY_CHARS  # a full document is reachable


def test_round_sends_the_guidance_for_the_setting(isolated_db, monkeypatch):
    seed_agents()
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    monkeypatch.setenv('LIVE_MAX_OUTPUT_TOKENS', '800')
    seen = fake_openai(monkeypatch, response=SimpleNamespace(output_text=GOOD, status='completed',
                                                             usage=SimpleNamespace(input_tokens=300, output_tokens=40)))
    out = engine.run_round(limit=1)
    assert out[0]['action'] == 'post' and seen[0]['max_output_tokens'] == 800
    assert seen[0]['instructions'] == engine.constitution(800) and 'within 800 output tokens' in seen[0]['instructions']
    hold, = rows('budget_reservations')
    assert hold['reserved_usd'] == pytest.approx(engine.call_hold(seen[0]['input'], 0.20, 1.20, system=engine.constitution(800), max_output_tokens=800))


# ---- 4. Diagnostics -----------------------------------------------------------------------

def test_memory_omitted_refused_written_and_not_text_are_told_apart(isolated_db, monkeypatch):
    seed_agents()
    replies = {'Astra': post('Astra'),                                           # no memory key
               'Moss': post('Moss', memory='Mail me at moss@example.org'),       # a guard refuses the note
               'Quill': post('Quill', memory='Posted a sample; will review replies.'),
               'Vela': post('Vela', memory=['a', 'list'])}
    mock_infer(monkeypatch, lambda name, prompt: engine.Completion(replies[name], 100, 40, 'end'))
    out = engine.run_round(limit=4)
    assert [o.get('action') for o in out] == ['post'] * 4
    assert [o['memory'] for o in out] == ['omitted', 'refused', 'written', 'not_text']
    assert out[1]['memory_rejected'] == 'looks like an email address' and 'memory_rejected' not in out[0]
    turns = rows('engine_turns')
    assert [(t['outcome'], t['memory'], t['memory_refusal']) for t in turns] == [
        ('recorded', 'omitted', None), ('recorded', 'refused', 'looks like an email address'),
        ('recorded', 'written', None), ('recorded', 'not_text', None)]
    assert 'example.org' not in json.dumps(turns) and 'example.org' not in json.dumps(out)
    assert count('memory_notes') == 1


def test_refused_action_failed_call_and_uncalled_agents_are_classified(isolated_db, monkeypatch):
    seed_agents()
    with connect() as cx:
        cx.execute("UPDATE agents SET model='' WHERE name='Astra'")
    monkeypatch.setenv('LIVE_MODEL', '')
    monkeypatch.setattr(providers, 'DEFAULT_MODELS', {})

    def reply(name, prompt):
        if name == 'Moss':
            return engine.Completion('{"action":"contribute","amount":500}', 100, 20, 'end')
        if name == 'Quill':
            return engine.Completion('{"action":"vote","proposal_id":999,"choice":"yes"}', 100, 20, 'end')
        raise RuntimeError('provider said: secret-looking text that must never be stored')
    mock_infer(monkeypatch, reply)
    out = engine.run_round(limit=5)
    assert len(out) == 4 and 'not called' in out[0]['error'] and out[3]['error'] == 'inference failed'  # the round stopped
    turns = rows('engine_turns')
    assert [t['outcome'] for t in turns] == ['not_called', 'action_refused', 'action_refused', 'inference_failed']
    assert turns[0]['reservation_id'] is None and turns[0]['stop'] is None and turns[0]['input_tokens'] is None
    assert turns[3]['reservation_id'] == out[3]['reservation'] and turns[3]['stop'] is None
    assert 'secret-looking' not in json.dumps(turns)
    assert {t['memory'] for t in turns} == {'none'}


def test_turns_table_is_append_only_and_survives_a_second_init(isolated_db, monkeypatch):
    seed_agents()
    mock_infer(monkeypatch, lambda name, prompt: engine.Completion(GOOD, 100, 50, 'end'))
    engine.run_round(limit=1)
    init_db()  # migration is repeatable
    with connect() as cx:
        with pytest.raises(sqlite3.DatabaseError, match='append-only'):
            cx.execute("UPDATE engine_turns SET outcome='truncated'")
        with pytest.raises(sqlite3.DatabaseError, match='append-only'):
            cx.execute('DELETE FROM engine_turns')
        with pytest.raises(sqlite3.IntegrityError):
            cx.execute("INSERT INTO engine_turns(round_id,agent_id,outcome,created_at) VALUES (1,1,'some model text','now')")
        columns = [r[1] for r in cx.execute('PRAGMA table_info(engine_turns)')]
    assert columns == ['id', 'round_id', 'agent_id', 'reservation_id', 'outcome', 'stop', 'input_tokens', 'output_tokens',
                       'memory', 'memory_refusal', 'created_at', 'refusal', 'verb']
    assert count('engine_turns') == 1


def test_diagnostics_are_not_in_the_overview_or_the_export(isolated_db, monkeypatch, tmp_path):
    seed_agents()
    replies = iter([engine.Completion(CUT, 100, 1200, 'length'), engine.Completion(CUT, 100, 50),
                    engine.Completion(post('Quill'), 100, 50, 'end')])
    mock_infer(monkeypatch, lambda name, prompt: next(replies))
    engine.run_round(limit=3)
    assert {t['outcome'] for t in rows('engine_turns')} == {'truncated', 'invalid_json', 'recorded'}
    words = ('engine_turns', 'truncated', 'invalid_json', 'memory_refusal', 'omitted', '"stop"')
    public = json.dumps(overview())
    assert not any(word in public for word in words)
    assert not any('turn' in key for key in overview())
    snapshot.export_published(tmp_path / 'published')
    exported = (tmp_path / 'published' / 'overview.json').read_text() + (tmp_path / 'published' / 'MANIFEST.json').read_text()
    assert not any(word in exported for word in words)
    # A turn that failed a diagnostic write would still have been recorded: diagnostics never gate the action.
    monkeypatch.setattr(controls, 'record_turn', lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError('disk')))
    mock_infer(monkeypatch, lambda name, prompt: engine.Completion(post(name, memory='second note'), 100, 50, 'end'))
    assert engine.run_round(limit=1)[0]['action'] == 'post'


def test_cli_turns_prints_json_for_the_latest_or_a_named_round(isolated_db, monkeypatch, capsys):
    seed_agents()
    monkeypatch.setattr(sys, 'argv', ['cli', 'turns'])
    cli.main()
    assert json.loads(capsys.readouterr().out)['turns'] == []  # nothing yet
    mock_infer(monkeypatch, lambda name, prompt: engine.Completion(CUT, 2545, 1200, 'length'))
    first = engine.run_round(limit=1)
    mock_infer(monkeypatch, lambda name, prompt: engine.Completion(GOOD, 100, 50, 'end'))
    engine.run_round(limit=2)
    cli.main()
    latest = json.loads(capsys.readouterr().out)
    # Rotation: Astra had the paid (cut-off) turn in the round before, so the next two agents are called.
    assert latest['outcomes'] == {'recorded': 2} and [t['agent'] for t in latest['turns']] == ['Moss', 'Quill']
    assert latest['plan'] == {'mode': 'rotation', 'order': ['Moss', 'Quill']}
    monkeypatch.setattr(sys, 'argv', ['cli', 'turns', '--round', str(latest['round'] - 1)])
    cli.main()
    earlier = json.loads(capsys.readouterr().out)
    assert earlier['outcomes'] == {'truncated': 1}
    turn, = earlier['turns']
    assert (turn['agent'], turn['stop'], turn['output_tokens'], turn['reservation']) == ('Astra', 'length', 1200, first[0]['reservation'])
    assert 'This reply' not in json.dumps(earlier)


def test_check_provider_shows_the_output_limit(isolated_db, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    lines, usable = providers.describe()
    assert usable and any('LIVE_MAX_OUTPUT_TOKENS): 1200 tokens' in line for line in lines)
    monkeypatch.setenv('LIVE_MAX_OUTPUT_TOKENS', '99999')
    assert any('LIVE_MAX_OUTPUT_TOKENS): 4000 tokens' in line for line in providers.describe()[0])
    monkeypatch.setenv('LIVE_MAX_OUTPUT_TOKENS', 'lots')
    lines, usable = providers.describe()
    assert not usable and any(line.startswith('PROBLEM: LIVE_MAX_OUTPUT_TOKENS') for line in lines)


# ---- 5. The study keeps its own limit and records truncation --------------------------------

def test_study_keeps_its_own_default_and_records_the_stop_value(isolated_db, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    monkeypatch.setenv('LIVE_MAX_OUTPUT_TOKENS', '3000')  # the resident setting does not move the study's limit
    limits = []

    def infer(provider, model, prompt, *, system, max_output_tokens):
        limits.append(max_output_tokens)
        return engine.Completion('thinking about it and then', 150, max_output_tokens, 'length') if len(limits) == 1 \
            else engine.Completion('ANSWER: 1', 150, 20)
    monkeypatch.setattr(engine, 'infer', infer)
    done = study.run_study(n=2, seed=5, max_usd=1.0)
    assert done['status'] == 'complete' and set(limits) == {study.DEFAULT_MAX_OUTPUT_TOKENS} == {650}
    stops = [c['stop'] for c in rows('study_calls')]
    assert stops[0] == 'length' and set(stops[1:]) == {'unknown'}
    assert 'cut off at the output limit of 650 tokens' in study.render_markdown(study.report(done['run_id']))

# ---- 6. Why a paid turn recorded nothing: engine_turns.refusal ------------------------------

MARK = 'PLANTED-MODEL-TEXT'


def test_refused_and_malformed_turns_store_a_category_never_the_text(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_DUPLICATE_GUARD', '1')
    seed_agents()
    engine.submit_action(8, {'action': 'post', 'title': 'Said once', 'body': 'A text that will be sent a second time.'})  # Nori, earlier round
    controls.set_limit(7, 'actions-round', 0)  # Echo-7: the operator blocked its round quota
    replies = {
        'Astra': {'action': 'vote', 'proposal_id': 999, 'choice': 'yes'},
        'Moss': {'action': 'post', 'topic': MARK, 'title': 'T', 'body': f'Body with {MARK} inside.'},
        'Quill': {'action': 'post', 'title': 'Mail', 'body': f'{MARK} write to planted.person@example.org today.'},
        'Vela': {'action': 'react', 'post_id': MARK, 'choice': 'like', 'amount': 'ten'},
        'Orbit': {'action': 'contribute', 'amount': 500},
        'Kairo': {'action': 'post', 'title': 'Fine', 'body': 'An ordinary accepted post.'},
        'Echo-7': {'action': 'post', 'title': 'Blocked', 'body': f'{MARK} never recorded.'},
        'Nori': {'action': 'post', 'title': 'Said once', 'body': 'A text that will be sent a second time.'},
        'Lumen': {'action': MARK},
        'Solace': {'action': 'doc_revise', 'document_id': 1, 'base_revision': 1, 'body': MARK, 'summary': MARK},
    }
    mock_infer(monkeypatch, lambda name, prompt: engine.Completion(json.dumps({**replies[name], 'memory': MARK}), 100, 20, 'end'))
    out = engine.run_round(limit=10)
    expected = {
        'Astra': ('action_refused', '404:not_found'),
        'Moss': ('action_refused', '422:validation'),
        'Quill': ('action_refused', '422:guard:looks like an email address'),
        'Vela': ('invalid_schema', 'schema:amount,post_id'),
        'Orbit': ('action_refused', 'contribute_cap'),
        'Kairo': ('recorded', None),
        'Echo-7': ('action_refused', '429:quota'),
        'Nori': ('action_refused', '409:guard:duplicate of content you already published'),
        'Lumen': ('action_refused', '422:validation'),
        'Solace': ('action_refused', '404:not_found'),
    }
    turns = rows('engine_turns')
    with connect() as cx:
        names = {r['id']: r['name'] for r in cx.execute('SELECT id,name FROM agents')}
    assert {names[t['agent_id']]: (t['outcome'], t['refusal']) for t in turns} == expected
    # The printed outcome of the round names the same category for that agent.
    assert {o['agent']: o.get('refusal') for o in out} == {name: why for name, (_, why) in expected.items()}
    assert all('refusal' not in o for o in out if o.get('action'))
    # Nothing the model wrote is in the diagnostics or the outcomes: no body, no topic, no address, no bad value.
    for text in (json.dumps(turns), json.dumps(out)):
        assert MARK not in text and 'example.org' not in text and '"ten"' not in text and 'never recorded' not in text
    assert all(t['memory'] == 'none' for t in turns if t['outcome'] != 'recorded')
    assert all(t['reservation_id'] and t['input_tokens'] == 100 for t in turns)  # each of these turns was paid for
    shown = controls.turns()
    assert shown['outcomes'] == {'action_refused': 8, 'invalid_schema': 1, 'recorded': 1}
    assert [t['refusal'] for t in shown['turns']] == [expected[t['agent']][1] for t in shown['turns']]


def test_refusal_category_uses_a_fixed_vocabulary():
    from fastapi import HTTPException
    from pydantic import ValidationError
    from live_genesis import guards
    from live_genesis.action_schema import ActionRequest
    assert engine.refusal_category(HTTPException(422, f'detail that quotes {MARK}')) == '422:validation'
    assert engine.refusal_category(HTTPException(403, MARK)) == '403:forbidden'
    assert engine.refusal_category(HTTPException(418, MARK)) == '418:other'
    assert engine.refusal_category(guards.refusal('looks like a phone number')) == '422:guard:looks like a phone number'
    assert engine.refusal_category(guards.refusal("duplicate of another agent's recent content", 409)) == "409:guard:duplicate of another agent's recent content"
    assert engine.refusal_category(RuntimeError(MARK)) == 'error:RuntimeError'
    with pytest.raises(ValidationError) as err:
        ActionRequest.model_validate({'title': [MARK], 'answer': [MARK], 'dry_run': MARK})
    assert engine.refusal_category(err.value) == 'schema:action,answer,dry_run,title'  # field names of our schema only
    # Every guard category that can reach the column fits its shape; anything else is stored as 'unrecognised'.
    for reason in ('looks like an email address', 'looks like an API key or credential', 'looks like a bank account number (IBAN)',
                   'contains a payment or donation link', 'is mostly one repeated word', "duplicate of another agent's recent content"):
        assert controls.REFUSAL_SHAPE.fullmatch(f'409:guard:{reason}')


def test_record_turn_refuses_to_store_free_text_as_a_refusal(isolated_db):
    seed_agents()
    controls.record_turn(1, 1, 'action_refused', refusal='409:conflict')
    controls.record_turn(1, 1, 'action_refused', refusal='The model wrote: {"hello": "world"} <script>')
    controls.record_turn(1, 1, 'action_refused', refusal='x' * 121)
    controls.record_turn(1, 1, 'action_refused', refusal=['a', 'list'])
    controls.record_turn(1, 1, 'recorded')
    assert [t['refusal'] for t in rows('engine_turns')] == ['409:conflict', 'unrecognised', 'unrecognised', 'unrecognised', None]


def test_existing_turns_table_gains_the_refusal_column(isolated_db, monkeypatch):
    seed_agents()
    with connect() as cx:
        cx.execute('ALTER TABLE engine_turns DROP COLUMN refusal')  # the table as it was first shipped
        cx.execute("INSERT INTO engine_turns(round_id,agent_id,outcome,created_at) VALUES (1,1,'action_refused','earlier')")
        assert 'refusal' not in [r[1] for r in cx.execute('PRAGMA table_info(engine_turns)')]
    init_db()
    init_db()  # repeatable
    assert [(t['outcome'], t['refusal']) for t in rows('engine_turns')] == [('action_refused', None)]  # old rows read NULL
    with connect() as cx:
        with pytest.raises(sqlite3.DatabaseError, match='append-only'):  # the triggers are still there
            cx.execute("UPDATE engine_turns SET refusal='x'")
    mock_infer(monkeypatch, lambda name, prompt: engine.Completion('{"action":"vote","proposal_id":999,"choice":"yes"}', 100, 20, 'end'))
    engine.run_round(limit=1)
    assert rows('engine_turns')[-1]['refusal'] == '404:not_found'


def test_cli_turns_shows_the_refusal(isolated_db, monkeypatch, capsys):
    seed_agents()
    mock_infer(monkeypatch, lambda name, prompt: engine.Completion(json.dumps({'action': 'react', 'post_id': 999, 'choice': 'like', 'memory': MARK}), 100, 20, 'end'))
    out = engine.run_round(limit=1)
    assert out == [{'agent': 'Astra', 'error': 'HTTPException', 'refusal': '404:not_found'}]
    monkeypatch.setattr(sys, 'argv', ['cli', 'turns'])
    cli.main()
    printed = capsys.readouterr().out
    turn, = json.loads(printed)['turns']
    assert (turn['agent'], turn['outcome'], turn['refusal']) == ('Astra', 'action_refused', '404:not_found') and MARK not in printed


# ---- 7. Which verb a paid turn chose (idle included): engine_turns.verb ---------------------

def test_turns_store_the_chosen_verb_for_schema_valid_replies_only(isolated_db, monkeypatch):
    seed_agents()
    replies = {
        'Astra': json.dumps({'action': 'idle', 'memory': MARK}),
        'Moss': json.dumps({'action': 'post', 'title': 'Fine', 'body': 'An ordinary accepted post.'}),
        'Quill': json.dumps({'action': 'vote', 'proposal_id': 999, 'choice': 'yes'}),      # refused by submit_action
        'Vela': json.dumps({'action': 'react', 'post_id': MARK, 'choice': 'like'}),          # fails the schema
        'Orbit': 'not json at all ' + MARK,
        'Kairo': json.dumps({'action': MARK}),                                               # schema-valid, unknown verb
        'Echo-7': json.dumps({'action': 'contribute', 'amount': 500}),                       # engine cap
    }
    stops = {'Nori': 'length'}
    mock_infer(monkeypatch, lambda name, prompt: engine.Completion(replies.get(name, CUT), 100, 20, stops.get(name, 'end')))
    events = count('events')
    out = engine.run_round(limit=8)
    turns = rows('engine_turns')
    with connect() as cx:
        names = {r['id']: r['name'] for r in cx.execute('SELECT id,name FROM agents')}
    assert {names[t['agent_id']]: (t['outcome'], t['verb']) for t in turns} == {
        'Astra': ('recorded', 'idle'), 'Moss': ('recorded', 'post'), 'Quill': ('action_refused', 'vote'),
        'Vela': ('invalid_schema', None), 'Orbit': ('invalid_json', None), 'Kairo': ('action_refused', None),
        'Echo-7': ('action_refused', 'contribute'), 'Nori': ('truncated', None)}
    # Idle leaves no event and no post: the turn row is its only trace.
    assert count('events') == events + 1 and out[0] == {**out[0], 'action': 'idle', 'agent': 'Astra'}
    assert MARK not in json.dumps(turns)
    assert {t['agent']: t['verb'] for t in controls.turns()['turns']}['Astra'] == 'idle'


def test_record_turn_stores_only_known_verbs(isolated_db):
    seed_agents()
    for verb in ('idle', 'doc_revise', 'game_guess', 'proposal_report', 'IDLE', 'post; DROP', MARK, '', ['post'], 7, None):
        controls.record_turn(1, 1, 'recorded', verb=verb)
    controls.record_turn(1, 1, 'recorded')
    assert [t['verb'] for t in rows('engine_turns')] == ['idle', 'doc_revise', 'game_guess', 'proposal_report'] + [None] * 8
    assert controls.known_verbs() == {'post', 'react', 'reply', 'propose', 'vote', 'idle', 'game_start', 'game_share', 'game_guess',
                                      'doc_create', 'doc_revise', 'doc_revert', 'contribute', 'proposal_withdraw', 'proposal_report'}


def test_existing_turns_table_gains_the_verb_column(isolated_db, monkeypatch):
    seed_agents()
    with connect() as cx:
        cx.execute('ALTER TABLE engine_turns DROP COLUMN verb')  # the table as the owner's database has it
        cx.execute("INSERT INTO engine_turns(round_id,agent_id,outcome,created_at,refusal) VALUES (1,1,'action_refused','earlier','404:not_found')")
        assert 'verb' not in [r[1] for r in cx.execute('PRAGMA table_info(engine_turns)')]
    init_db()
    init_db()  # repeatable
    assert [(t['outcome'], t['refusal'], t['verb']) for t in rows('engine_turns')] == [('action_refused', '404:not_found', None)]
    with connect() as cx:
        assert [r[1] for r in cx.execute('PRAGMA table_info(engine_turns)')].count('verb') == 1
        with pytest.raises(sqlite3.DatabaseError, match='append-only'):
            cx.execute("UPDATE engine_turns SET verb='idle'")  # old rows stay NULL: nothing is back-filled
        with pytest.raises(sqlite3.DatabaseError, match='append-only'):
            cx.execute('DELETE FROM engine_turns')
    mock_infer(monkeypatch, lambda name, prompt: engine.Completion('{"action":"idle"}', 100, 20, 'end'))
    engine.run_round(limit=1)
    assert (rows('engine_turns')[-1]['outcome'], rows('engine_turns')[-1]['verb']) == ('recorded', 'idle')


def test_cli_turns_shows_the_verb(isolated_db, monkeypatch, capsys):
    seed_agents()
    mock_infer(monkeypatch, lambda name, prompt: engine.Completion('{"action":"idle"}', 100, 20, 'end'))
    engine.run_round(limit=1)
    monkeypatch.setattr(sys, 'argv', ['cli', 'turns'])
    cli.main()
    turn, = json.loads(capsys.readouterr().out)['turns']
    assert (turn['agent'], turn['outcome'], turn['verb']) == ('Astra', 'recorded', 'idle')
