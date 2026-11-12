"""Commons Challenge (ported from codex/local-0.2). Offline; no model calls; digits are injected."""
import json
import re

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from live_genesis import cli, engine, games
from live_genesis.app import app
from live_genesis.core import (agent_context, connect, init_db, open_round, overview, reserve_budget, seed_agents,
                               settle_reservation, submit_action)

POST = {'action': 'post', 'title': 'Not a game move', 'body': 'An ordinary post.', 'topic': 'games'}


def engine_receipt(agent_id):
    """A settled, unused budget reservation: the only way an action may be labelled 'engine'."""
    receipt = reserve_budget(agent_id, 'test-model-1', 0.4, 10, provider='openai')
    settle_reservation(receipt, 10, 10)
    return receipt


def deal(monkeypatch, *digits):
    """Make the next clue digits deterministic (seat order)."""
    queue = list(digits)
    monkeypatch.setattr(games, 'draw_digit', lambda: queue.pop(0))


def ids():
    with connect() as cx:
        return [r[0] for r in cx.execute('SELECT id FROM agents ORDER BY id')]


def start(monkeypatch, digits=(4, 0, 7), **kwargs):
    """Seed agents, deal `digits` and start a game as agent 1. Seats are agents 1, 2, 3."""
    seed_agents()
    deal(monkeypatch, *digits)
    a, b, c, outsider = ids()[:4]
    game = submit_action(a, {'action': 'game_start'}, **kwargs)
    return game['game_id'], a, b, c, outsider


def share_all(gid, *members, **kwargs):
    for member in members:
        submit_action(member, {'action': 'game_share', 'game_id': gid}, **kwargs)


def rejected(status, agent_id, action, **kwargs):
    with pytest.raises(HTTPException) as err:
        submit_action(agent_id, action, **kwargs)
    assert err.value.status_code == status, err.value.detail
    return err.value


def counts():
    with connect() as cx:
        return {t: cx.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                for t in ('events', 'action_provenance', 'round_actions', 'games', 'game_clues', 'game_moves')}


def test_cooperative_win_first_attempt_scores_three(isolated_db, monkeypatch):
    gid, a, b, c, _ = start(monkeypatch)
    started = overview()['games'][0]
    assert (started['state'], started['attempts'], started['score'], started['max_attempts']) == ('ACTIVE', 0, 0, 3)
    assert [(s['position'], s['agent'], s['shared'], s['value']) for s in started['clues']] == [
        (1, 'Astra', 0, None), (2, 'Moss', 0, None), (3, 'Quill', 0, None)]
    share_all(gid, b, c)
    open_round()  # agent `a` already used round 1's action on game_start
    share_all(gid, a)
    assert [s['value'] for s in overview()['games'][0]['clues']] == [4, 0, 7]
    won = submit_action(b, {'action': 'game_guess', 'game_id': gid, 'answer': [4, 0, 7]})
    assert (won['state'], won['score'], won['attempts_left']) == ('WON', 3, 2)
    game = overview()['games'][0]
    assert (game['state'], game['attempts'], game['score'], game['source']) == ('WON', 1, 3, 'api')
    assert [m['move'] for m in game['moves']] == ['game_start', 'game_share', 'game_share', 'game_share', 'game_guess']
    assert overview()['metrics']['games'] == 1
    open_round()
    rejected(409, c, {'action': 'game_guess', 'game_id': gid, 'answer': [4, 0, 7]})  # finished


def test_score_falls_with_each_wrong_attempt(isolated_db, monkeypatch):
    gid, a, b, c, _ = start(monkeypatch)
    share_all(gid, b, c)
    open_round(); share_all(gid, a)
    wrong = submit_action(b, {'action': 'game_guess', 'game_id': gid, 'answer': [4, 0, 8]})
    assert (wrong['state'], wrong['score'], wrong['attempts_left']) == ('ACTIVE', 0, 2)
    assert agent_context(c)['games']['active'][0]['wrong_guesses'] == [[4, 0, 8]]
    won = submit_action(c, {'action': 'game_guess', 'game_id': gid, 'answer': [4, 0, 7]})
    assert (won['state'], won['score']) == ('WON', 2)
    assert 'wrong' in overview()['games'][0]['moves'][-2]['detail']  # wrong guesses are public


def test_loss_after_three_wrong_guesses(isolated_db, monkeypatch):
    gid, a, b, c, _ = start(monkeypatch)
    share_all(gid, b, c)
    open_round(); share_all(gid, a)
    states = []
    for guesser in (b, c):
        states.append(submit_action(guesser, {'action': 'game_guess', 'game_id': gid, 'answer': [9, 9, 9]})['state'])
    open_round()
    lost = submit_action(a, {'action': 'game_guess', 'game_id': gid, 'answer': [1, 1, 1]})
    assert states == ['ACTIVE', 'ACTIVE'] and (lost['state'], lost['score'], lost['attempts_left']) == ('LOST', 0, 0)
    rejected(409, b, {'action': 'game_guess', 'game_id': gid, 'answer': [4, 0, 7]})
    game = overview()['games'][0]
    assert (game['state'], game['attempts'], game['score']) == ('LOST', 3, 0)
    # A finished game frees the slot.
    deal(monkeypatch, 1, 2, 3)
    assert submit_action(c, {'action': 'game_start'})['game_id'] == gid + 1


def test_move_rules_and_rejections_record_nothing(isolated_db, monkeypatch):
    gid, a, b, c, outsider = start(monkeypatch)
    before = counts()
    rejected(409, b, {'action': 'game_guess', 'game_id': gid, 'answer': [4, 0, 7]})   # nobody shared yet
    rejected(409, outsider, {'action': 'game_start'})                                # only one active game
    rejected(403, outsider, {'action': 'game_share', 'game_id': gid})                # not seated
    rejected(403, outsider, {'action': 'game_guess', 'game_id': gid, 'answer': [4, 0, 7]})
    rejected(404, b, {'action': 'game_share', 'game_id': 999})
    rejected(422, b, {'action': 'game_share'})
    rejected(422, b, {'action': 'game_share', 'game_id': '1'})
    rejected(422, b, {'action': 'game_share', 'game_id': True})
    for bad in (None, 'abc', [4, 0], [4, 0, 7, 1], [4, 0, 10], [4, 0, -1], [4, 0, '7'], [4, 0, True], [4.0, 0, 7]):
        rejected(422, b, {'action': 'game_guess', 'game_id': gid, 'answer': bad})
    assert counts() == before
    share_all(gid, b)
    open_round()
    rejected(409, b, {'action': 'game_share', 'game_id': gid})                       # double share
    share_all(gid, c)
    rejected(409, c, {'action': 'game_guess', 'game_id': gid, 'answer': [4, 0, 7]}, quota=False)  # `a` has not shared
    after = counts()
    assert after['game_moves'] == before['game_moves'] + 2 and after['events'] == before['events'] + 2


def test_needs_three_agents(isolated_db, monkeypatch):
    # Agents with ledger entries cannot be deleted (append-only credit ledger), so seed only two.
    from live_genesis import core
    monkeypatch.setattr(core, 'SEED_IDENTITIES', core.SEED_IDENTITIES[:2])
    seed_agents()
    rejected(409, 1, {'action': 'game_start'})
    assert counts()['games'] == 0
    with connect() as cx:
        assert games.pick_peers(cx, 2) == [1]


def test_each_move_uses_the_round_quota_and_a_rejection_does_not(isolated_db, monkeypatch):
    gid, a, b, c, _ = start(monkeypatch)
    assert agent_context(a)['round']['actions_used'] == 1
    rejected(429, a, {'action': 'game_share', 'game_id': gid})     # start was this round's action
    rejected(429, a, POST)
    # A rejected move leaves the quota untouched: the same agent can still act this round.
    rejected(409, b, {'action': 'game_guess', 'game_id': gid, 'answer': [4, 0, 7]})
    assert agent_context(b)['round']['actions_used'] == 0
    share_all(gid, b)
    assert agent_context(b)['round']['actions_used'] == 1
    rejected(429, b, POST)
    open_round()
    share_all(gid, a)
    rejected(429, a, {'action': 'game_guess', 'game_id': gid, 'answer': [4, 0, 7]})


def test_cooldown_applies_to_game_moves(isolated_db, monkeypatch):
    gid, a, b, c, _ = start(monkeypatch)
    monkeypatch.setenv('LIVE_ACTION_COOLDOWN_SECONDS', '60')
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND', '5')
    err = rejected(429, a, {'action': 'game_share', 'game_id': gid})
    assert 'cooldown' in err.detail.lower()


def test_idempotent_replay_of_a_move(isolated_db, monkeypatch):
    gid, a, b, c, _ = start(monkeypatch, idempotency_key='start-key-0001')
    share = {'action': 'game_share', 'game_id': gid}
    first = submit_action(b, share, idempotency_key='share-key-0001')
    before = counts()
    again = submit_action(b, share, idempotency_key='share-key-0001')
    assert again == {**first, 'replayed': True}
    assert counts() == before
    rejected(409, b, {'action': 'game_share', 'game_id': gid + 1}, idempotency_key='share-key-0001')  # key reused for another body
    replayed_start = submit_action(a, {'action': 'game_start'}, idempotency_key='start-key-0001')
    assert replayed_start['game_id'] == gid and replayed_start['replayed'] is True and counts() == before
    # A rejected move does not burn its key.
    rejected(409, c, {'action': 'game_guess', 'game_id': gid, 'answer': [4, 0, 7]}, idempotency_key='guess-key-0001')
    assert submit_action(c, share, idempotency_key='guess-key-0001')['move_id'] == first['move_id'] + 1


def test_provenance_rows_and_mixed_source_label(isolated_db, monkeypatch):
    seed_agents()
    gid, a, b, c, _ = start(monkeypatch, receipt=engine_receipt(ids()[0]))
    assert overview()['games'][0]['source'] == 'engine'
    submit_action(b, {'action': 'game_share', 'game_id': gid}, provenance={'source': 'api', 'model': 'declared-model', 'client': 'bot/1'})
    share_all(gid, c, receipt=engine_receipt(c))
    with connect() as cx:
        rows = [dict(r) for r in cx.execute('''SELECT ap.*,e.verb,e.demo FROM action_provenance ap
            JOIN events e ON e.id=ap.event_id ORDER BY ap.event_id''')]
        move_ids = [r[0] for r in cx.execute('SELECT id FROM game_moves ORDER BY id')]
    assert [(r['kind'], r['verb'], r['source'], r['model'], r['client'], r['verified'], r['demo']) for r in rows] == [
        ('game_start', 'game_start', 'engine', 'test-model-1', None, 1, 0),
        ('game_share', 'game_share', 'api', 'declared-model', 'bot/1', 0, 0),
        ('game_share', 'game_share', 'engine', 'test-model-1', None, 1, 0)]
    assert [r['ref_id'] for r in rows] == move_ids
    state = overview()
    game = state['games'][0]
    assert game['source'] == 'mixed'
    assert [(m['source'], m['model'], m['verified']) for m in game['moves']] == [
        ('engine', 'test-model-1', 1), ('api', 'declared-model', 0), ('engine', 'test-model-1', 1)]
    assert state['metrics']['actions_by_source'] == {'demo': 0, 'engine': 2, 'api': 1, 'unknown': 0}
    assert [e['verb'] for e in state['events']] == ['game_share', 'game_share', 'game_start']


def test_demo_and_agent_games_cannot_mix(isolated_db, monkeypatch):
    script = dict(demo=True, cooldown=False, quota=False)
    demo_gid, a, b, c, outsider = start(monkeypatch, **script)
    assert overview()['mode'] == 'DEMO'
    rejected(409, b, {'action': 'game_share', 'game_id': demo_gid})                 # real move on a scripted game
    assert overview()['mode'] == 'DEMO' and agent_context(b)['round']['actions_used'] == 0
    # Scripted games are not offered to agents as something to play.
    assert agent_context(a)['games'] == {'active': [], 'can_start': True}
    deal(monkeypatch, 1, 2, 3)
    real_gid = submit_action(a, {'action': 'game_start'})['game_id']
    rejected(409, b, {'action': 'game_share', 'game_id': real_gid}, **script)       # scripted move on a real game
    rejected(409, outsider, {'action': 'game_start'}, **script)                     # one active scripted game
    with pytest.raises(ValueError):                                                 # demo flag and source must agree
        submit_action(b, {'action': 'game_share', 'game_id': demo_gid}, demo=True, provenance={'source': 'api'})
    share_all(demo_gid, a, b, c, **script)
    submit_action(c, {'action': 'game_guess', 'game_id': demo_gid, 'answer': [4, 0, 7]}, **script)
    by_id = {g['id']: g for g in overview()['games']}
    assert (by_id[demo_gid]['source'], by_id[demo_gid]['demo'], by_id[demo_gid]['state']) == ('demo', 1, 'WON')
    assert all(m['source'] == 'demo' and m['model'] is None and m['verified'] == 0 for m in by_id[demo_gid]['moves'])
    assert (by_id[real_gid]['source'], by_id[real_gid]['demo'], by_id[real_gid]['state']) == ('api', 0, 'ACTIVE')
    assert [m['source'] for m in by_id[real_gid]['moves']] == ['api']
    with connect() as cx:
        assert cx.execute('SELECT COUNT(*) FROM round_actions').fetchone()[0] == 1  # scripted moves use no quota rows


STAMP = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00')


def public_snapshot(tmp_path, monkeypatch, name, digits):
    """Everything a non-owner can see after seat 1 shared and seats 2 and 3 did not."""
    monkeypatch.setenv('LIVE_DB', str(tmp_path / name))
    init_db()
    gid, a, b, c, outsider = start(monkeypatch, digits)
    open_round()
    share_all(gid, a)
    with connect() as cx:
        private = {
            'events': [tuple(r) for r in cx.execute('SELECT agent_id,verb,summary,demo FROM events ORDER BY id')],
            'provenance': [tuple(r) for r in cx.execute('SELECT * FROM action_provenance ORDER BY event_id')],
            'responses': [tuple(r) for r in cx.execute('SELECT verb,response FROM round_actions ORDER BY id')],
            'moves': [tuple(r) for r in cx.execute('SELECT game_id,agent_id,move,detail,answer,correct FROM game_moves ORDER BY id')],
        }
    seat_two = agent_context(b)
    own = seat_two['games']['active'][0]['your_clue'].pop('digit')
    snapshot = {'overview': overview(), 'outsider': agent_context(outsider), 'seat_one': agent_context(a),
                'seat_two_without_own_digit': seat_two, **private}
    return STAMP.sub('T', json.dumps(snapshot, sort_keys=True, default=str)), own


def test_unshared_clues_are_invisible_everywhere(isolated_db, tmp_path, monkeypatch):
    # Two worlds that differ ONLY in the two unshared digits must look identical from outside.
    first, own_first = public_snapshot(tmp_path, monkeypatch, 'one.sqlite', (5, 1, 2))
    second, own_second = public_snapshot(tmp_path, monkeypatch, 'two.sqlite', (5, 8, 9))
    assert first == second
    assert (own_first, own_second) == (1, 8)      # the owner, and only the owner, sees its digit
    # ...and the comparison is meaningful: a different SHARED digit does show up.
    third, _ = public_snapshot(tmp_path, monkeypatch, 'three.sqlite', (6, 1, 2))
    assert third != first


def test_context_entry_is_private_small_and_fits_the_prompt(isolated_db, monkeypatch):
    gid, a, b, c, outsider = start(monkeypatch, (4, 0, 7))
    share_all(gid, b)
    mine = agent_context(c)['games']
    assert mine == {'can_start': False, 'active': [{
        'id': gid, 'attempts_left': 3, 'your_clue': {'position': 3, 'digit': 7, 'shared': False},
        'seats': [{'position': 1, 'agent': 'Astra', 'digit': None}, {'position': 2, 'agent': 'Moss', 'digit': 0},
                  {'position': 3, 'agent': 'Quill', 'digit': None}],
        'wrong_guesses': [], 'expires_in_rounds': 6, 'next': 'game_share'}]}
    assert agent_context(b)['games']['active'][0]['next'] == 'wait for the other clues'
    assert agent_context(outsider)['games'] == {'active': [], 'can_start': False}
    assert len(json.dumps(mine)) < 600
    assert '"games"' in engine.build_prompt(agent_context(c))
    share_all(gid, c)
    open_round(); share_all(gid, a)
    assert agent_context(a)['games']['active'][0]['next'] == 'game_guess'
    submit_action(b, {'action': 'game_guess', 'game_id': gid, 'answer': [4, 0, 7]})
    assert agent_context(a)['games'] == {'active': [], 'can_start': True}
    for verb in games.VERBS:
        assert verb in engine.CONSTITUTION


def test_engine_round_can_play_a_move(isolated_db, monkeypatch):
    seed_agents(model='test-model-1')
    deal(monkeypatch, 4, 0, 7)
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    monkeypatch.setattr(engine, 'infer', lambda provider, model, prompt: engine.Completion('{"action":"game_start","memory":"started"}', 10, 5))
    outcomes = engine.run_round(limit=2)
    assert outcomes[0]['action'] == 'game_start' and outcomes[0]['game_id'] == 1
    assert outcomes[1] == {'agent': 'Moss', 'error': 'HTTPException'}      # second start rejected, nothing recorded
    game = overview()['games'][0]
    assert game['source'] == 'engine' and len(game['moves']) == 1 and game['moves'][0]['model'] == 'test-model-1'


def test_overview_keeps_only_latest_ten_games(isolated_db, monkeypatch):
    seed_agents()
    script = dict(demo=True, cooldown=False, quota=False)
    for _ in range(12):
        deal(monkeypatch, 1, 2, 3)
        gid = submit_action(1, {'action': 'game_start'}, **script)['game_id']
        share_all(gid, 1, 2, 3, **script)
        submit_action(2, {'action': 'game_guess', 'game_id': gid, 'answer': [1, 2, 3]}, **script)
    state = overview()
    assert [g['id'] for g in state['games']] == list(range(12, 2, -1))
    assert state['metrics']['games'] == 12


def test_demo_game_on_empty_database(isolated_db, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, 'ROOT', tmp_path)       # keep generated keys out of the repository
    cli.demo_game()
    out = capsys.readouterr().out
    assert 'NO model was called' in out and 'SCRIPTED' in out
    assert (tmp_path / 'secrets' / 'agent-keys.json').exists()   # agents had to be created
    state = overview()
    game = state['games'][0]
    assert state['mode'] == 'DEMO'
    assert (game['state'], game['score'], game['attempts'], game['source'], game['demo']) == ('WON', 3, 1, 'demo', 1)
    assert len(game['moves']) == 5 and all(m['source'] == 'demo' for m in game['moves'])
    assert all(e['demo'] == 1 and e['source'] == 'demo' and e['model'] is None for e in state['events'])
    with connect() as cx:
        assert cx.execute('SELECT COUNT(*) FROM round_actions').fetchone()[0] == 0
        assert cx.execute('SELECT COUNT(*) FROM api_usage').fetchone()[0] == 0


def test_demo_game_after_demo_writes_no_key_file(isolated_db, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    seed_agents()
    cli.demo()
    cli.demo_game()
    cli.demo_game()                                  # repeatable: each run adds one finished scripted game
    assert not (tmp_path / 'secrets').exists()
    state = overview()
    assert state['mode'] == 'DEMO' and state['metrics']['games'] == 2
    assert state['metrics']['actions_by_source']['demo'] == state['metrics']['recorded_actions']


def test_demo_game_refuses_when_real_activity_exists(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    seed_agents()
    submit_action(1, POST)
    before = counts()
    with pytest.raises(RuntimeError, match='fresh DB'):
        cli.demo_game()
    assert counts() == before and overview()['mode'] == 'LIVE'
    assert not (tmp_path / 'secrets').exists()


def test_http_round_trip_and_validation(isolated_db, monkeypatch):
    keys = [k['key'] for k in seed_agents()]
    deal(monkeypatch, 4, 0, 7)
    auth = [{'Authorization': 'Bearer ' + k} for k in keys]
    with TestClient(app) as client:
        send = lambda who, body, **headers: client.post('/api/agent/action', json=body, headers={**auth[who], **headers})
        assert send(0, {'action': 'game_start'}).status_code == 200
        gid = client.get('/api/overview').json()['games'][0]['id']
        assert client.post('/api/agent/action', json={'action': 'game_share', 'game_id': gid}).status_code == 401
        assert send(1, {'action': 'game_share'}).status_code == 422                          # missing game_id
        assert send(1, {'action': 'game_share', 'game_id': 'one'}).status_code == 422
        assert send(3, {'action': 'game_share', 'game_id': gid}).status_code == 403
        assert send(1, {'action': 'game_guess', 'game_id': gid, 'answer': [4, 0, 7]}).status_code == 409
        shared = send(1, {'action': 'game_share', 'game_id': gid}, **{'Idempotency-Key': 'http-share-01', 'X-Agent-Model': 'declared-model'})
        assert shared.status_code == 200 and 'replayed' not in shared.json()
        assert send(1, {'action': 'game_share', 'game_id': gid}, **{'Idempotency-Key': 'http-share-01'}).json()['replayed'] is True
        assert send(2, {'action': 'game_share', 'game_id': gid}).status_code == 200
        mine = client.get('/api/agent/context', headers=auth[0]).json()['games']['active'][0]
        assert mine['your_clue'] == {'position': 1, 'digit': 4, 'shared': False}
        assert [s['digit'] for s in mine['seats']] == [None, 0, 7]
        public = client.get('/api/overview').json()['games'][0]
        assert [c['value'] for c in public['clues']] == [None, 0, 7]
        assert public['moves'][1]['model'] == 'declared-model' and public['moves'][1]['verified'] == 0
        open_round()
        assert send(0, {'action': 'game_share', 'game_id': gid}).status_code == 200
        for bad in ('407', [4, 0], [4, 0, 70], [4, 0, 'x'], {'a': 1}):
            assert send(1, {'action': 'game_guess', 'game_id': gid, 'answer': bad}).status_code == 422, bad
        assert send(1, {'action': 'game_guess', 'game_id': gid}).status_code == 422          # missing answer
        won = send(1, {'action': 'game_guess', 'game_id': gid, 'answer': [4, 0, 7]})
        assert won.status_code == 200 and (won.json()['state'], won.json()['score']) == ('WON', 3)
        final = client.get('/api/overview').json()
        assert final['metrics']['games'] == 1 and final['games'][0]['state'] == 'WON'
        page = client.get('/').text
        assert 'id="games"' in page and 'id="games-list"' in page


def test_revoked_agents_are_never_seated(isolated_db, monkeypatch):
    from live_genesis import controls
    seed_agents()
    controls.rotate_key(ids()[1], revoke=True)
    deal(monkeypatch, 1, 2, 3)
    game = submit_action(ids()[0], {'action': 'game_start'})
    seats = [c['agent_id'] for c in overview()['games'][0]['clues']]
    assert seats == [ids()[0], ids()[2], ids()[3]] and game['state'] == 'ACTIVE'
