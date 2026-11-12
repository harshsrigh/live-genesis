"""Round plan (who the engine calls, in what order, stored once) and `last_turn` (category-only feedback
to an agent about its previous engine turn).

Offline and deterministic: `engine.infer` is always replaced; no model is called.
"""
import json
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient

from live_genesis import agentkit, cli, controls, engine
from live_genesis.app import app
from live_genesis.core import agent_context, connect, current_round, init_db, open_round, overview, seed_agents, submit_action

NAMES = ['Astra', 'Moss', 'Quill', 'Vela', 'Orbit', 'Kairo', 'Echo-7', 'Nori', 'Lumen', 'Solace']
IDLE = '{"action":"idle"}'
MARK = 'PLANTED-MODEL-TEXT'


def provider(monkeypatch, reply=IDLE):
    """Replace engine.infer. `reply` is the reply text, or a function (name, prompt) -> text / Completion /
    exception to raise. Returns the list of (name, prompt) calls."""
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    calls = []

    def infer(provider_name, model, prompt):
        name = json.loads(prompt)['identity']['name']
        calls.append((name, prompt))
        answer = reply(name, prompt) if callable(reply) else reply
        if isinstance(answer, BaseException):
            raise answer
        return answer if isinstance(answer, engine.Completion) else engine.Completion(answer, 100, 20, 'end')
    monkeypatch.setattr(engine, 'infer', infer)
    return calls


def called(calls):
    return [name for name, _ in calls]


def plan_rows(round_id=None):
    with connect() as cx:
        return [tuple(r) for r in cx.execute(
            'SELECT p.round_id,p.position,a.name,p.mode,p.paid_turns FROM round_plan p JOIN agents a ON a.id=p.agent_id '
            'WHERE ? IS NULL OR p.round_id=? ORDER BY p.round_id,p.position', (round_id, round_id))]


def order_of(round_id):
    return [row[2] for row in plan_rows(round_id)]


def turn_counts():
    with connect() as cx:
        return {NAMES[agent - 1]: count for agent, (count, _) in controls.paid_turns(cx).items()}


# ---- 1. Who acts: fewest paid turns first -------------------------------------------------

def test_three_of_ten_cycles_through_everyone_before_anyone_goes_again(isolated_db, monkeypatch):
    seed_agents()
    calls = provider(monkeypatch)
    for number in range(1, 11):
        engine.run_round(limit=3)
        counts = [turn_counts().get(name, 0) for name in NAMES]
        assert max(counts) - min(counts) <= 1, (number, counts)  # nobody is two turns ahead of anybody
    assert len(calls) == 30 and set(turn_counts().values()) == {3}
    assert set(called(calls)[:9]) == set(NAMES[:9])            # rounds 1-3: nine different agents
    assert sorted(called(calls)[9:12]) == ['Astra', 'Moss', 'Solace']  # round 4: the one left, then the two that acted longest ago
    assert len(set(called(calls)[:12])) == 10                  # everyone has acted before anyone has a third turn


def test_idle_refused_and_cut_off_turns_all_count_as_a_paid_turn(isolated_db, monkeypatch):
    seed_agents()
    replies = {'Astra': IDLE, 'Moss': '{"action":"react","post_id":999,"choice":"like"}',
               'Quill': engine.Completion('{"action":"post","title":"cut', 100, 1200, 'length')}
    calls = provider(monkeypatch, lambda name, prompt: replies.get(name, IDLE))
    engine.run_round(limit=3)
    assert turn_counts() == {'Astra': 1, 'Moss': 1, 'Quill': 1}  # counted from budget_reservations, not from recorded actions
    engine.run_round(limit=3)
    assert called(calls)[3:] == ['Vela', 'Orbit', 'Kairo']


def test_api_actions_and_released_holds_do_not_count(isolated_db, monkeypatch):
    seed_agents()
    calls = provider(monkeypatch)
    submit_action(1, {'action': 'post', 'title': 'By API', 'body': 'An action sent through the API.'})  # no reservation
    from live_genesis.core import close_reservation, reserve_budget
    close_reservation(reserve_budget(2, 'm', 0.001, 10, provider='openai', round_id=current_round()), 'released')  # nothing sent
    reserve_budget(None, 'm', 0.001, 10)  # a study-style hold: no agent
    with connect() as cx:
        cx.execute("UPDATE budget_reservations SET status='released' WHERE agent_id IS NULL")
    engine.run_round(limit=2)
    assert called(calls) == ['Astra', 'Moss'] and turn_counts() == {'Astra': 1, 'Moss': 1}


def test_ties_break_by_longest_ago_then_id(isolated_db, monkeypatch):
    seed_agents()
    calls = provider(monkeypatch)
    engine.run_round(only=[3, 1])          # Quill acts before Astra
    engine.run_round(only=[2])             # then Moss
    del calls[:]
    engine.run_round(limit=9)              # the seven that never acted, then Quill and Astra (longest ago); not Moss
    assert sorted(called(calls)) == sorted(n for n in NAMES if n != 'Moss')
    del calls[:]
    engine.run_round(limit=1)              # everyone has one turn except... Moss has one too; Moss acted longest ago
    assert called(calls) == ['Moss']
    assert controls._fewest_turns({i: {} for i in (5, 2, 9)}, {}, 2) == [2, 5]  # no turns at all: lowest id


def test_disabled_and_external_agents_are_never_planned(isolated_db, monkeypatch):
    seed_agents()
    calls = provider(monkeypatch)
    controls.rotate_key(1, revoke=True)
    controls.set_external(2, True)
    engine.run_round(limit=3)
    assert called(calls) == ['Quill', 'Vela', 'Orbit'] and order_of(2) == ['Quill', 'Vela', 'Orbit']
    engine.run_round(limit=10)             # more seats than engine agents: the eight that exist
    assert len(plan_rows(3)) == 8 and not {'Astra', 'Moss'} & set(order_of(3))
    for bad in ([1], [2], [99], [3, 3], []):
        with pytest.raises(controls.PlanError):
            engine.run_round(only=bad)
    assert current_round() == 3 and len(calls) == 11  # a refused list opens no round and calls nobody


# ---- 2. In what order: the first speaker rotates --------------------------------------------

def test_first_speaker_rotates_when_the_same_agents_act_every_round(isolated_db, monkeypatch):
    seed_agents()
    calls = provider(monkeypatch)
    firsts = []
    for _ in range(10):
        del calls[:]
        engine.run_round(limit=10)
        firsts.append(called(calls)[0])
        start = NAMES.index(firsts[-1])
        assert called(calls) == NAMES[start:] + NAMES[:start]  # id order, wrapped around
    assert firsts == NAMES  # each agent opens one round in ten


def test_first_speaker_rotates_for_three_of_ten(isolated_db, monkeypatch):
    seed_agents()
    calls = provider(monkeypatch)
    firsts = []
    for _ in range(10):
        del calls[:]
        engine.run_round(limit=3)
        firsts.append(called(calls)[0])
    # The opener is the selected agent that has opened the fewest planned rounds (then: longest ago, then id).
    # Astra opens a second time in the eighth round only because all three selected then (Astra, Quill, Vela)
    # had opened one round each, Astra's the longest ago.
    assert firsts == ['Astra', 'Vela', 'Echo-7', 'Moss', 'Quill', 'Kairo', 'Lumen', 'Astra', 'Orbit', 'Nori']
    assert order_of(2) == ['Astra', 'Moss', 'Quill']    # a fresh world starts in id order
    assert order_of(5) == ['Moss', 'Solace', 'Astra']   # round 4 of the run: Astra already opened one, Moss did not


# ---- 3. The stored plan ----------------------------------------------------------------------

def test_plan_is_stored_before_the_first_call_and_is_append_only(isolated_db, monkeypatch):
    seed_agents()
    seen = []

    def reply(name, prompt):
        seen.append(plan_rows(current_round()))  # what is stored while the call is in flight
        return IDLE
    provider(monkeypatch, reply)
    announced = []
    engine.run_round(limit=3, announce=announced.append)
    rows = [(2, 0, 'Astra', 'rotation', 0), (2, 1, 'Moss', 'rotation', 0), (2, 2, 'Quill', 'rotation', 0)]
    assert seen == [rows, rows, rows] and plan_rows() == rows
    assert announced == [{'round': 2, 'mode': 'rotation', 'resumed': False, 'order': ['Astra', 'Moss', 'Quill'],
                          'calling': ['Astra', 'Moss', 'Quill']}]
    shown = controls.turns()
    assert shown['plan'] == {'mode': 'rotation', 'order': ['Astra', 'Moss', 'Quill']}
    assert [t['agent'] for t in shown['turns']] == ['Astra', 'Moss', 'Quill']
    with connect() as cx:
        for statement in ('UPDATE round_plan SET agent_id=9', 'DELETE FROM round_plan'):
            with pytest.raises(sqlite3.IntegrityError, match='append-only'):
                cx.execute(statement)
    engine.run_round(limit=2)
    assert plan_rows(3) == [(3, 0, 'Vela', 'rotation', 0), (3, 1, 'Orbit', 'rotation', 0)]  # paid_turns: what the selection saw
    open_round('operator')
    assert controls.turns(current_round())['plan'] is None  # a round the engine never ran has no plan
    assert 'round_plan' not in json.dumps(overview()) and 'plan' not in overview()


# ---- 4. Resume follows the plan ---------------------------------------------------------------

def test_resume_after_a_failed_call_follows_the_stored_plan(isolated_db, monkeypatch):
    seed_agents()
    fail = {'Vela'}
    calls = provider(monkeypatch, lambda name, prompt: TimeoutError('secret') if name in fail else IDLE)
    engine.run_round(only=[1, 2, 3])                 # round 2: Astra, Moss, Quill have one turn each
    out = engine.run_round(limit=4)                  # round 3 plan: Vela, Orbit, Kairo, Echo-7
    plan = plan_rows(3)
    assert [row[2] for row in plan] == ['Vela', 'Orbit', 'Kairo', 'Echo-7']
    assert called(calls)[3:] == ['Vela'] and out[0]['error'] == 'inference failed'  # the round stopped on the failed call
    receipt = out[0]['reservation']
    blocked = engine.run_round(limit=4, resume=True)
    assert 'reconcile' in blocked[0]['error'] and len(calls) == 4
    assert 'reconcile' in engine.run_round(limit=4)[0]['error'] and current_round() == 3  # later rounds refused, none opened
    controls.reconcile_reservation(receipt, 0.001)
    fail.clear()
    del calls[:]
    announced = []
    out = engine.run_round(limit=4, resume=True, announce=announced.append)
    # Same selection, same order, Vela not called again, although a fresh selection would now differ.
    assert called(calls) == ['Orbit', 'Kairo', 'Echo-7'] and current_round() == 3 and plan_rows(3) == plan
    assert announced[0]['order'] == ['Vela', 'Orbit', 'Kairo', 'Echo-7'] and announced[0]['calling'] == ['Orbit', 'Kairo', 'Echo-7']
    assert announced[0]['resumed'] is True
    assert engine.run_round(limit=4, resume=True) == [] and len(calls) == 3  # nothing left: nobody is called twice
    with connect() as cx:
        per_agent = [r[0] for r in cx.execute("SELECT COUNT(*) FROM budget_reservations WHERE round_id=3 GROUP BY agent_id")]
    assert per_agent == [1, 1, 1, 1]


def test_resume_does_not_reselect_when_turn_counts_changed_mid_round(isolated_db, monkeypatch):
    seed_agents()
    stop = {'Moss'}
    calls = provider(monkeypatch, lambda name, prompt: TimeoutError() if name in stop else IDLE)
    out = engine.run_round(limit=3)                  # Astra is paid, Moss fails, Quill is never reached
    assert called(calls) == ['Astra', 'Moss']
    controls.reconcile_reservation(out[1]['reservation'], 0)
    stop.clear()
    # A fresh rotation would now pick Quill, Vela, Orbit (no turns yet); the plan says Astra, Moss, Quill.
    with connect() as cx:
        assert [NAMES[i - 1] for i in controls._fewest_turns(controls.engine_agents(cx), controls.paid_turns(cx), 3)] == ['Quill', 'Vela', 'Orbit']
    engine.run_round(limit=3, resume=True)
    assert called(calls) == ['Astra', 'Moss', 'Quill'] and order_of(2) == ['Astra', 'Moss', 'Quill']


def test_resume_with_a_different_number_of_agents(isolated_db, monkeypatch):
    seed_agents()
    provider(monkeypatch)
    engine.run_round(only=[1, 2])                    # round 2
    stop = {'Quill'}
    provider_calls = provider(monkeypatch, lambda name, prompt: TimeoutError() if name in stop else IDLE)
    out = engine.run_round(limit=4)                  # round 3 plan: Quill, Vela, Orbit, Kairo; Quill fails
    controls.reconcile_reservation(out[0]['reservation'], 0)
    stop.clear()
    del provider_calls[:]
    # Fewer: only the first seats of the plan (Quill is done, so Vela alone).
    engine.run_round(limit=2, resume=True)
    assert called(provider_calls) == ['Vela'] and len(plan_rows(3)) == 4
    # More: the plan keeps its seats and gains seats by its own rule (fewest paid turns among agents not in it).
    engine.run_round(limit=6, resume=True)
    assert called(provider_calls) == ['Vela', 'Orbit', 'Kairo', 'Echo-7', 'Nori']
    assert order_of(3) == ['Quill', 'Vela', 'Orbit', 'Kairo', 'Echo-7', 'Nori']
    # --order / --only contradict a stored plan: refused before any call, nothing added.
    for flags in ({'order': 'id'}, {'order': 'rotation'}, {'only': [9]}):
        with pytest.raises(controls.PlanError, match='already has a stored plan'):
            engine.run_round(limit=3, resume=True, **flags)
    assert len(provider_calls) == 5 and len(plan_rows(3)) == 6 and current_round() == 3


def test_an_only_plan_is_never_extended(isolated_db, monkeypatch):
    seed_agents()
    stop = {'Echo-7'}
    calls = provider(monkeypatch, lambda name, prompt: TimeoutError() if name in stop else IDLE)
    out = engine.run_round(only=[4, 7, 9])
    assert called(calls) == ['Vela', 'Echo-7'] and plan_rows() == [(2, 0, 'Vela', 'only', 0), (2, 1, 'Echo-7', 'only', 0), (2, 2, 'Lumen', 'only', 0)]
    controls.reconcile_reservation(out[1]['reservation'], 0)
    stop.clear()
    engine.run_round(limit=10, resume=True)
    assert called(calls) == ['Vela', 'Echo-7', 'Lumen'] and len(plan_rows()) == 3


def test_an_agent_revoked_or_made_external_after_planning_is_not_called_on_resume(isolated_db, monkeypatch):
    seed_agents()
    stop = {'Astra'}
    calls = provider(monkeypatch, lambda name, prompt: TimeoutError() if name in stop else IDLE)
    out = engine.run_round(limit=4)
    controls.reconcile_reservation(out[0]['reservation'], 0)
    stop.clear()
    controls.rotate_key(2, revoke=True)
    controls.set_external(3, True)
    engine.run_round(limit=4, resume=True)
    assert called(calls) == ['Astra', 'Vela'] and order_of(2) == ['Astra', 'Moss', 'Quill', 'Vela']


def test_resuming_a_round_the_operator_opened_plans_it_then(isolated_db, monkeypatch):
    seed_agents()
    calls = provider(monkeypatch)
    engine.run_round(limit=2)
    open_round('operator')
    submit_action(3, {'action': 'post', 'title': 'By API', 'body': 'Quill acted through the API in this round.'})
    engine.run_round(limit=3, resume=True)  # no plan and no paid call yet: planned now, by rotation
    assert plan_rows(3) == [(3, 0, 'Quill', 'rotation', 0), (3, 1, 'Vela', 'rotation', 0), (3, 2, 'Orbit', 'rotation', 0)]
    assert called(calls)[2:] == ['Vela', 'Orbit']  # Quill already used this round's action: skipped, never paid for


# ---- 5. Operator overrides ---------------------------------------------------------------------

def test_order_id_is_the_earlier_behaviour(isolated_db, monkeypatch):
    seed_agents()
    calls = provider(monkeypatch)
    for _ in range(3):
        engine.run_round(limit=3, order='id')
    assert called(calls) == ['Astra', 'Moss', 'Quill'] * 3
    assert {row[3] for row in plan_rows()} == {'id'} and [row[4] for row in plan_rows(4)] == [2, 2, 2]
    with pytest.raises(controls.PlanError):
        engine.run_round(limit=3, order='random')
    with pytest.raises(controls.PlanError):
        engine.run_round(order='id', only=[1])
    assert current_round() == 4


def test_only_calls_exactly_the_listed_agents_in_that_order(isolated_db, monkeypatch):
    seed_agents()
    calls = provider(monkeypatch)
    out = engine.run_round(only=[9, 4, 7])
    assert called(calls) == ['Lumen', 'Vela', 'Echo-7'] and len(out) == 3
    assert plan_rows() == [(2, 0, 'Lumen', 'only', 0), (2, 1, 'Vela', 'only', 0), (2, 2, 'Echo-7', 'only', 0)]
    with pytest.raises(ValueError):
        engine.run_round(only=list(range(1, 12)))


def run_cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, 'argv', ['live_genesis.cli', *argv])
    monkeypatch.setattr(cli, 'load_provider_credentials', lambda: None)  # no .env is read in a test
    cli.main()


def test_cli_round_flags_and_the_selection_printed_before_the_first_call(isolated_db, monkeypatch, capsys):
    seed_agents()
    printed_before = []

    def reply(name, prompt):
        printed_before.append(capsys.readouterr().err)
        return IDLE
    calls = provider(monkeypatch, reply)
    run_cli(monkeypatch, 'round', '--agents', '2')
    assert 'Round 2 (selection: rotation). Planned order: Astra, Moss. Calling now, one paid call each: Astra, Moss.' in printed_before[0]
    assert [o['agent'] for o in json.loads(capsys.readouterr().out)] == ['Astra', 'Moss']  # stdout stays the JSON outcomes
    run_cli(monkeypatch, 'round')  # default: three agents, rotation
    assert called(calls)[2:] == ['Quill', 'Vela', 'Orbit']
    run_cli(monkeypatch, 'round', '--agents', '2', '--order', 'id')
    assert called(calls)[5:] == ['Astra', 'Moss']
    run_cli(monkeypatch, 'round', '--only', '9,4')
    assert called(calls)[7:] == ['Lumen', 'Vela']
    capsys.readouterr()
    run_cli(monkeypatch, 'round', '--agents', '2', '--resume')
    assert 'resumed; selection: only' in capsys.readouterr().err and len(calls) == 9
    run_cli(monkeypatch, 'turns')
    assert json.loads(capsys.readouterr().out)['plan'] == {'mode': 'only', 'order': ['Lumen', 'Vela']}
    for bad in (['--only', '9,4', '--agents', '2'], ['--only', '9,4', '--order', 'id'], ['--only', '9;4'], ['--only', '4,4'],
                ['--only', '77'], ['--order', 'shuffle'], ['--agents', '11'], ['--resume', '--order', 'id'], ['--resume', '--only', '1']):
        with pytest.raises(SystemExit):
            run_cli(monkeypatch, 'round', *bad)
    assert len(calls) == 9 and current_round() == 5  # refused before a round was opened or anyone was called
    capsys.readouterr()
    with pytest.raises(SystemExit):
        run_cli(monkeypatch, 'round', '--help')
    help_text = ' '.join(capsys.readouterr().out.split())
    assert 'one PAID call each' in help_text and 'Same cost either way' in help_text


# ---- 6. Migration -------------------------------------------------------------------------------

def test_a_database_with_rounds_and_reservations_but_no_plan_table_migrates_in_place(isolated_db, monkeypatch):
    seed_agents()
    stop = set()
    calls = provider(monkeypatch, lambda name, prompt: TimeoutError() if name in stop else IDLE)
    engine.run_round(limit=3, order='id')
    stop.add('Moss')
    out = engine.run_round(limit=4, order='id')      # round 3, interrupted at Moss: Astra and Moss were paid for
    with connect() as cx:
        cx.execute('DROP TABLE round_plan')          # the database as a version without plans left it
        before = [tuple(r) for r in cx.execute('SELECT id,agent_id,round_id,status FROM budget_reservations ORDER BY id')]
        rounds = cx.execute('SELECT COUNT(*) FROM rounds').fetchone()[0]
    init_db()
    init_db()  # repeatable
    with connect() as cx:
        assert cx.execute('SELECT COUNT(*) FROM round_plan').fetchone()[0] == 0
        assert [tuple(r) for r in cx.execute('SELECT id,agent_id,round_id,status FROM budget_reservations ORDER BY id')] == before
        assert cx.execute('SELECT COUNT(*) FROM rounds').fetchone()[0] == rounds
        triggers = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='round_plan'")}
    assert triggers == {'round_plan_no_update', 'round_plan_no_delete'}
    controls.reconcile_reservation(out[1]['reservation'], 0)
    stop.clear()
    del calls[:]
    # The interrupted round has paid calls and no plan: it was run by id, so the resume plans it by id.
    engine.run_round(limit=4, resume=True)
    assert called(calls) == ['Quill', 'Vela'] and plan_rows(3) == [
        (3, 0, 'Astra', 'id', 2), (3, 1, 'Moss', 'id', 2), (3, 2, 'Quill', 'id', 1), (3, 3, 'Vela', 'id', 0)]
    # The next round counts every earlier paid call, including those made before plans existed.
    del calls[:]
    engine.run_round(limit=3)
    assert called(calls) == ['Orbit', 'Kairo', 'Echo-7'] and [row[4] for row in plan_rows(4)] == [0, 0, 0]
    del calls[:]
    engine.run_round(limit=5)  # the three that never acted, then the one-turn agents that acted longest ago
    assert sorted(called(calls)) == sorted(['Nori', 'Lumen', 'Solace', 'Vela', 'Orbit'])


# ---- 7. last_turn: the category table ------------------------------------------------------------

NOTES = controls.LAST_TURN_NOTES


@pytest.mark.parametrize('outcome,fields,expected', [
    ('truncated', {'stop': 'length'}, {'outcome': 'truncated', 'note': NOTES['truncated']}),
    ('invalid_json', {}, {'outcome': 'invalid_json', 'note': NOTES['invalid_json']}),
    ('inference_failed', {}, {'outcome': 'inference_failed', 'note': NOTES['inference_failed']}),
    ('invalid_schema', {'refusal': 'schema:post_id'},
     {'outcome': 'invalid_schema', 'note': 'Your last action had a missing or wrong-typed field: post_id. Nothing was recorded.'}),
    ('invalid_schema', {'refusal': 'schema:amount,post_id'},
     {'outcome': 'invalid_schema', 'note': 'Your last action had a missing or wrong-typed field: amount, post_id. Nothing was recorded.'}),
    ('invalid_schema', {'refusal': 'schema:other'}, {'outcome': 'invalid_schema', 'note': NOTES['schema:other']}),
    ('invalid_schema', {'refusal': 'schema:not_a_field'}, {'outcome': 'invalid_schema', 'note': NOTES['schema:other']}),
    ('action_refused', {'refusal': '422:guard:looks like an email address', 'verb': 'post'},
     {'outcome': 'action_refused', 'verb': 'post',
      'note': 'Your last action was refused by a content check (looks like an email address). Do not repeat that content.'}),
    ('action_refused', {'refusal': '409:guard:duplicate of content you already published', 'verb': 'reply'},
     {'outcome': 'action_refused', 'verb': 'reply',
      'note': 'Your last action was refused by a content check (duplicate of content you already published). Do not repeat that content.'}),
    ('action_refused', {'refusal': '422:validation', 'verb': 'post'}, {'outcome': 'action_refused', 'verb': 'post', 'note': NOTES['422:validation']}),
    ('action_refused', {'refusal': '404:not_found', 'verb': 'react'}, {'outcome': 'action_refused', 'verb': 'react', 'note': NOTES['404:not_found']}),
    ('action_refused', {'refusal': '409:conflict', 'verb': 'doc_revise'}, {'outcome': 'action_refused', 'verb': 'doc_revise', 'note': NOTES['409:conflict']}),
    ('action_refused', {'refusal': '429:quota', 'verb': 'post'}, {'outcome': 'action_refused', 'verb': 'post', 'note': NOTES['429:quota']}),
    ('action_refused', {'refusal': 'contribute_cap', 'verb': 'contribute'}, {'outcome': 'action_refused', 'verb': 'contribute', 'note': NOTES['contribute_cap']}),
    ('action_refused', {'refusal': 'error:KeyError', 'verb': 'vote'}, {'outcome': 'action_refused', 'verb': 'vote', 'note': NOTES['other']}),
    ('action_refused', {'refusal': '403:forbidden'}, {'outcome': 'action_refused', 'note': NOTES['other']}),
    ('action_refused', {'refusal': f'<b>{MARK}</b>\n', 'verb': MARK}, {'outcome': 'action_refused', 'note': NOTES['other']}),  # stored as 'unrecognised'
    ('action_refused', {}, {'outcome': 'action_refused', 'note': NOTES['other']}),
    ('recorded', {'memory': 'refused', 'memory_refusal': 'looks like a phone number', 'verb': 'post'},
     {'outcome': 'recorded', 'verb': 'post',
      'note': 'Your last action was recorded, but its memory note was refused by a content check (looks like a phone number) and not saved.'}),
    ('recorded', {'memory': 'refused', 'memory_refusal': f'{MARK} <script>', 'verb': 'idle'},
     {'outcome': 'recorded', 'verb': 'idle', 'note': NOTES['memory:unnamed']}),
])
def test_last_turn_for_each_category(isolated_db, outcome, fields, expected):
    seed_agents()
    controls.record_turn(current_round(), 1, outcome, **fields)
    context = agent_context(1)
    assert context['last_turn'] == {'round': 1, **expected}
    assert list(context['last_turn'])[:2] == ['round', 'outcome'] and MARK not in json.dumps(context)
    assert 'last_turn' not in agent_context(2)  # another agent's context never carries it


def test_last_turn_absence_cases(isolated_db):
    seed_agents()
    assert 'last_turn' not in agent_context(1)                       # never had a paid turn
    controls.record_turn(1, 1, 'not_called')
    assert 'last_turn' not in agent_context(1)                       # nothing was sent: not a paid turn
    controls.record_turn(1, 1, 'truncated', stop='length')
    assert agent_context(1)['last_turn']['outcome'] == 'truncated'
    controls.record_turn(1, 1, 'not_called')
    assert agent_context(1)['last_turn']['outcome'] == 'truncated'  # a skipped turn does not hide the last paid one
    for memory in ('written', 'unchanged', 'omitted', 'not_text'):
        controls.record_turn(1, 1, 'recorded', verb='post', memory=memory)
        assert 'last_turn' not in agent_context(1)                   # a turn recorded in full: the key is omitted
    controls.record_turn(1, 1, 'invalid_json')
    assert 'last_turn' in agent_context(1)
    controls.set_external(1, True)
    assert 'last_turn' not in agent_context(1)                       # an identity run by an outside client
    controls.set_external(1, False)
    assert 'last_turn' in agent_context(1)


def test_every_fixed_sentence_is_short_plain_and_makes_no_claim_about_feelings():
    for key, note in NOTES.items():
        assert len(note.encode()) <= 200 and note.startswith('Your last ') and note.isascii(), key
        assert not any(word in note.lower() for word in ('feel', 'sorry', 'conscious', 'punish', 'score')), key
    worst = {'round': 999999, 'outcome': 'inference_failed', 'verb': 'proposal_withdraw',
             'note': NOTES['guard'].format(category='x' * 120)}
    assert len(json.dumps({'last_turn': worst}, separators=(',', ':')).encode()) < 320  # the most the key can add to a prompt


# ---- 8. last_turn end to end: no text leaks, it reaches the model, it is private -------------------

def refused_round(monkeypatch):
    """One round in which five agents each lose their turn in a different way, every reply carrying MARK."""
    replies = {
        'Astra': json.dumps({'action': 'post', 'topic': MARK, 'title': MARK, 'body': f'Body with {MARK}.', 'memory': MARK}),
        'Moss': json.dumps({'action': 'post', 'title': f'{MARK} mail', 'body': f'{MARK} write to planted.person@example.org.', 'memory': MARK}),
        'Quill': json.dumps({'action': 'react', 'post_id': MARK, 'choice': 'like', 'memory': MARK}),
        'Vela': engine.Completion(f'{{"action":"post","title":"{MARK}","body":"{MARK} cut', 100, 1200, 'length'),
        'Orbit': f'{MARK} is not JSON',
        'Kairo': json.dumps({'action': 'post', 'title': 'Fine', 'body': 'An ordinary accepted post.', 'memory': 'kept'}),
    }
    calls = provider(monkeypatch, lambda name, prompt: replies[name])
    engine.run_round(limit=6)
    return calls


def test_refused_text_never_reaches_the_next_context_or_prompt(isolated_db, monkeypatch):
    seed_agents()
    refused_round(monkeypatch)
    expected = {1: ('action_refused', 'post', NOTES['422:validation']),
                2: ('action_refused', 'post', NOTES['guard'].format(category='looks like an email address')),
                3: ('invalid_schema', None, NOTES['schema'].format(fields='post_id')),
                4: ('truncated', None, NOTES['truncated']),
                5: ('invalid_json', None, NOTES['invalid_json'])}
    for agent_id, (outcome, verb, note) in expected.items():
        context = agent_context(agent_id)
        assert context['last_turn'] == {'round': 2, 'outcome': outcome, **({'verb': verb} if verb else {}), 'note': note}
        prompt = engine.build_prompt(engine.engine_context(context))
        assert MARK not in json.dumps(context) and MARK not in prompt and 'example.org' not in prompt
        assert json.loads(prompt)['last_turn'] == context['last_turn']
    assert 'last_turn' not in agent_context(6)   # Kairo's turn was recorded in full
    assert 'last_turn' not in agent_context(7)   # Echo-7 was not called
    # The next real turn: the model is sent the category, and none of the refused text.
    calls = provider(monkeypatch)
    engine.run_round(only=[1, 2, 3, 4, 5, 6])
    for name, prompt in calls:
        assert MARK not in prompt
        assert ('last_turn' in json.loads(prompt)) == (name != 'Kairo'), name
    assert all('last_turn' not in agent_context(i) for i in range(1, 7))  # an idle turn is a turn recorded in full


def test_last_turn_is_private_not_in_overview_or_any_other_context(isolated_db, monkeypatch):
    seed_agents()
    refused_round(monkeypatch)
    public = json.dumps(overview())
    assert 'last_turn' not in public and MARK not in public
    assert not any(note.split('{')[0] in public for note in NOTES.values())
    assert all('last_turn' not in agent_context(i) for i in range(6, 11))
    assert NOTES['truncated'] not in json.dumps(agent_context(1)) and NOTES['422:validation'] not in json.dumps(agent_context(4))
    with connect() as cx:  # nothing was written into a memory note or a post either
        stored = json.dumps([tuple(r) for r in cx.execute('SELECT note FROM memory_notes')] + [tuple(r) for r in cx.execute('SELECT title,body FROM posts')])
    assert MARK not in stored and 'Your last' not in stored


def test_last_turn_is_served_to_the_agent_itself_over_http(isolated_db, monkeypatch):
    keys = {item['name']: item['key'] for item in seed_agents()}
    refused_round(monkeypatch)
    with TestClient(app) as client:
        own = client.get('/api/agent/context', headers={'Authorization': f"Bearer {keys['Vela']}"}).json()
        other = client.get('/api/agent/context', headers={'Authorization': f"Bearer {keys['Nori']}"}).json()
        public = client.get('/api/overview').text
    assert own['last_turn'] == {'round': 2, 'outcome': 'truncated', 'note': NOTES['truncated']}
    assert 'last_turn' not in other and 'last_turn' not in public
    assert 'last_turn' in agentkit.CONTEXT_FIELDS and '`last_turn`' in agentkit.agent_guide()


def test_last_turn_is_never_trimmed_from_the_prompt(isolated_db, monkeypatch):
    seed_agents()
    for author in range(2, 10):
        submit_action(author, {'action': 'post', 'title': f'Post {author}', 'body': 'Filler text for the prompt bound. ' * 30})
    controls.record_turn(current_round(), 1, 'action_refused', refusal='422:guard:looks like an email address', verb='post')
    context = engine.engine_context(agent_context(1))
    full = engine.build_prompt(context)
    assert json.loads(full)['last_turn']['verb'] == 'post' and len(json.loads(full)['recent_posts']) == 8
    limit, smallest = len(full.encode()), None
    while True:
        limit -= 150
        try:
            smallest = json.loads(engine.build_prompt(context, limit))
        except ValueError:
            break
        assert smallest['last_turn'] == context['last_turn'] and smallest['available_actions']
    assert smallest is not None and smallest['recent_posts'] == [] and smallest['proposals'] == []  # everything droppable went first


def test_constitution_names_last_turn_and_the_byte_bound_and_hold_still_follow_the_prompt(isolated_db, monkeypatch):
    line = ('If your context has "last_turn", it reports the outcome of your previous turn: do not repeat a refused or '
            'cut-off turn unchanged.\n')
    assert line in engine.CONSTITUTION and line in engine.constitution(4000) and len(line.encode()) == 130
    assert engine.PROMPT_BYTE_LIMIT + len(engine.CONSTITUTION.encode()) == engine.INPUT_BYTE_LIMIT
    seed_agents()
    controls.record_turn(current_round(), 1, 'truncated', stop='length')
    calls = provider(monkeypatch)
    engine.run_round(only=[1])
    (name, prompt), = calls
    assert json.loads(prompt)['last_turn']['outcome'] == 'truncated'
    assert len(prompt.encode()) + len(engine.constitution().encode()) <= engine.INPUT_BYTE_LIMIT
    with connect() as cx:
        held = cx.execute('SELECT reserved_usd FROM budget_reservations').fetchone()[0]
    assert held == pytest.approx(engine.call_hold(prompt, 0.20, 1.20))  # sized from the prompt actually sent, last_turn included
