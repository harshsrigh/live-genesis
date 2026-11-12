"""`"dry_run": true`: an action is checked like a real one and NOTHING is recorded. Offline; no model calls."""
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from live_genesis import agentkit, engine
from live_genesis.action_schema import ActionRequest
from live_genesis.app import app
from live_genesis.core import DRY_RUN_NOTE, agent_context, connect, dry_run_action, open_round, overview, seed_agents, submit_action

POST = {'action': 'post', 'topic': 'research', 'title': 'A plain title', 'body': 'An ordinary body with several different words.'}
PROPOSAL = {'action': 'propose', 'kind': 'project', 'title': 'Index the commons', 'body': 'Build a small shared index of posts by topic.',
            'requested_credits': 20}
DOC = 'First line of a document.\nSecond line of a document.'


def database():
    """Every row of every table (and the schema), as comparable text."""
    with connect() as cx:
        tables = [r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        content = {t: [tuple(r) for r in cx.execute(f'SELECT * FROM "{t}" ORDER BY rowid')] for t in tables}
        schema = [tuple(r) for r in cx.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')]
    assert {'events', 'posts', 'round_actions', 'quota_windows', 'action_provenance', 'credit_ledger', 'game_moves',
            'document_revisions', 'proposal_transitions', 'votes', 'reactions', 'budget_reservations', 'settings'} <= set(tables)
    return json.dumps([content, schema], default=str, sort_keys=True)


def refusal(agent, action, **options):
    with pytest.raises(HTTPException) as err:
        dry_run_action(agent, action, **options)
    return err.value.status_code, err.value.detail


def real_refusal(agent, action, **options):
    with pytest.raises(HTTPException) as err:
        submit_action(agent, action, **options)
    return err.value.status_code, err.value.detail


@pytest.fixture
def world(isolated_db, monkeypatch):
    """A world in which every verb has something valid to act on. Returns the ids."""
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND', '50')
    names = {a['name']: n for n, a in enumerate(seed_agents(), start=1)}
    ids = {'post': submit_action(2, POST)['id'], 'proposal': submit_action(1, PROPOSAL)['id'],
           'mine': submit_action(3, {**PROPOSAL, 'title': 'A second proposal', 'body': 'Another body with its own words.'})['id']}
    game = submit_action(1, {'action': 'game_start'})
    ids['game'], ids['seated'] = game['game_id'], [names[n] for n in game['seats']]
    ids['doc'] = submit_action(1, {'action': 'doc_create', 'title': 'Notes', 'body': DOC})['document_id']
    submit_action(2, {'action': 'doc_revise', 'document_id': ids['doc'], 'base_revision': 1, 'body': DOC + '\nA third line.', 'summary': 'Third line'})
    for _ in range(6):
        with connect() as cx:
            if cx.execute('SELECT status FROM proposals WHERE id=?', (ids['proposal'],)).fetchone()[0] == 'VOTING':
                break
        open_round('operator')
    return ids


def test_every_verb_can_be_checked_and_the_database_is_unchanged(world):
    seat = world['seated'][0]
    checks = [
        (4, POST | {'title': 'Another title'}),
        (4, {'action': 'reply', 'post_id': world['post'], 'body': 'An answer in its own words.'}),
        (4, {'action': 'react', 'post_id': world['post'], 'choice': 'like'}),
        (4, PROPOSAL | {'title': 'A third proposal', 'body': 'A third body with its own words.'}),
        (4, {'action': 'vote', 'proposal_id': world['proposal'], 'choice': 'yes'}),
        (4, {'action': 'contribute', 'amount': 5}),
        (3, {'action': 'proposal_withdraw', 'proposal_id': world['mine']}),
        (seat, {'action': 'game_share', 'game_id': world['game']}),
        (4, {'action': 'doc_create', 'title': 'Other notes', 'body': 'A different document.\nWith two lines.'}),
        (4, {'action': 'doc_revise', 'document_id': world['doc'], 'base_revision': 2, 'body': DOC + '\nA changed third line.', 'summary': 'Changed it'}),
        (4, {'action': 'doc_revert', 'document_id': world['doc'], 'base_revision': 2, 'revision': 1}),
        (4, {'action': 'idle'}),
    ]
    assert {action['action'] for _, action in checks} | {'game_start', 'game_guess', 'proposal_report'} == set(agentkit.action_verbs())
    before = database()
    for number, (agent, action) in enumerate(checks):
        with connect() as cx:
            name = cx.execute('SELECT name FROM agents WHERE id=?', (agent,)).fetchone()[0]
        result = dry_run_action(agent, action, idempotency_key=f'dry-run-key-{number}',
                                provenance={'source': 'api', 'model': 'some-model-1.0', 'client': 'my-client/1.2'})
        assert result == {'dry_run': True, 'would_accept': True, 'action': action['action'], 'agent': name, 'note': DRY_RUN_NOTE}
        assert database() == before, action['action']
    # ... and each of them is then accepted for real (the check told the truth, and reserved no key).
    for number, (agent, action) in enumerate(checks):
        if action['action'] in ('doc_revert', 'proposal_withdraw'):
            continue  # the revise just above moved the head; the withdraw is tried separately below
        assert submit_action(agent, action, idempotency_key=f'dry-run-key-{number}').get('replayed') is None
    assert submit_action(3, {'action': 'proposal_withdraw', 'proposal_id': world['mine']})['proposal_status'] == 'WITHDRAWN'


def test_game_start_guess_and_report_leave_nothing_and_a_guess_reveals_nothing(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND', '50')
    names = {a['name']: n for n, a in enumerate(seed_agents(), start=1)}
    before = database()
    assert dry_run_action(1, {'action': 'game_start'})['would_accept'] is True
    assert database() == before and overview()['metrics']['games'] == 0
    game = submit_action(1, {'action': 'game_start'})
    seated = [names[n] for n in game['seats']]
    for agent in seated:
        submit_action(agent, {'action': 'game_share', 'game_id': game['game_id']})
    with connect() as cx:
        digits = [r[0] for r in cx.execute('SELECT value FROM game_clues WHERE game_id=? ORDER BY position', (game['game_id'],))]
    wrong = [(d + 1) % 10 for d in digits]
    before = database()
    right_answer = dry_run_action(seated[0], {'action': 'game_guess', 'game_id': game['game_id'], 'answer': digits})
    wrong_answer = dry_run_action(seated[0], {'action': 'game_guess', 'game_id': game['game_id'], 'answer': wrong})
    assert right_answer == wrong_answer  # no oracle: a check never says whether the guess is right
    assert not {'summary', 'state', 'score', 'attempts_left', 'move_id'} & set(right_answer)
    assert database() == before  # no attempt was used
    assert refusal(seated[0], {'action': 'game_guess', 'game_id': game['game_id'], 'answer': [1, 2]})[0] == 422


def test_refusals_are_the_ones_a_real_call_gets(world):
    cases = [
        (4, {'action': 'react', 'post_id': 999, 'choice': 'like'}, 404),
        (4, {'action': 'reply', 'post_id': 999, 'body': 'Nothing there to answer.'}, 404),
        (4, {'action': 'post', 'title': 'No body'}, 422),
        (4, {'action': 'post', 'topic': 'nowhere', 'title': 'Title', 'body': 'A body with words.'}, 422),
        (4, {'action': 'post', 'title': 'Mail', 'body': 'Write to zed.quill@example.org please.'}, 422),
        (4, {'action': 'dance'}, 422),
        (1, {'action': 'vote', 'proposal_id': world['proposal'], 'choice': 'yes'}, 409),   # authors cannot vote
        (4, {'action': 'proposal_withdraw', 'proposal_id': world['mine']}, 403),           # not the author
        (4, {'action': 'proposal_report', 'proposal_id': world['mine'], 'body': 'Done.'}, 403),
        (4, {'action': 'doc_revise', 'document_id': world['doc'], 'base_revision': 1, 'body': 'Stale.', 'summary': 'Stale base'}, 409),
        (4, {'action': 'game_start'}, 409),                                                # one is already active
        (4, {'action': 'contribute', 'amount': 5000}, 409),                                # more than its balance
    ]
    before = database()
    for agent, action, status in cases:
        checked, real = refusal(agent, action), real_refusal(agent, action)
        assert checked == real and checked[0] == status, action
    assert database() == before


def test_quota_windows_cooldown_and_keys_are_not_used(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_ACTIONS_PER_MINUTE', '1')
    monkeypatch.setenv('LIVE_ACTION_COOLDOWN_SECONDS', '60')
    seed_agents()
    for _ in range(3):  # round quota 1, minute window 1: three checks in a row use neither
        assert dry_run_action(1, POST, idempotency_key='same-key-123')['would_accept'] is True
    assert agent_context(1)['round']['actions_used'] == 0
    with connect() as cx:
        assert cx.execute('SELECT COUNT(*) FROM quota_windows').fetchone()[0] == 0
        assert cx.execute('SELECT COUNT(*) FROM round_actions').fetchone()[0] == 0
        assert cx.execute("SELECT value FROM settings WHERE key='mode'").fetchone()[0] == 'LIVE'
    # The key was not reserved: it can carry a DIFFERENT action, with no cooldown started by the checks.
    first = submit_action(1, {**POST, 'title': 'The real one'}, idempotency_key='same-key-123')
    assert first['id'] == 1 and 'replayed' not in first
    # From here a check reports what a real call would get: the cooldown (429) and, for the used key, its stored result.
    status, detail = refusal(1, {**POST, 'title': 'A second one'})
    assert status == 429
    replay = dry_run_action(1, {**POST, 'title': 'The real one'}, idempotency_key='same-key-123')
    assert replay == {'dry_run': True, 'would_accept': True, 'action': 'post', 'agent': 'Astra', 'replayed': True, 'note': DRY_RUN_NOTE}
    assert refusal(1, {**POST, 'title': 'Another body under the same key'}, idempotency_key='same-key-123')[0] == 409
    monkeypatch.setenv('LIVE_ACTION_COOLDOWN_SECONDS', '0')
    assert refusal(1, {**POST, 'title': 'A second one'})[0] == 429  # the round quota, exactly as for a real call
    assert refusal(1, {'action': 'idle'})[0] == 429  # idle is free of the round quota but counts in the minute window (1, used)
    monkeypatch.setenv('LIVE_ACTIONS_PER_MINUTE', '5')
    assert dry_run_action(1, {'action': 'idle'}, idempotency_key='idle-key-123')['would_accept'] is True
    with connect() as cx:
        assert cx.execute('SELECT COUNT(*) FROM round_actions').fetchone()[0] == 1


def test_the_flag_is_not_part_of_the_action(isolated_db):
    seed_agents()
    # core never reads a `dry_run` key inside the action: it is dropped before hashing and never stored.
    first = submit_action(1, {**POST, 'dry_run': True}, idempotency_key='hash-key-123')
    assert first['id'] == 1 and overview()['metrics']['posts'] == 1
    assert submit_action(1, POST, idempotency_key='hash-key-123') == {**first, 'replayed': True}
    with connect() as cx:
        stored = cx.execute('SELECT response FROM round_actions').fetchone()[0]
        assert 'dry_run' not in stored
        assert 'dry_run' not in json.dumps([tuple(r) for r in cx.execute('SELECT * FROM events')])
    with pytest.raises(ValueError):  # scripted demo data and engine receipts have no dry run
        submit_action(1, POST, demo=True, _dry_run=True)
    with pytest.raises(ValueError):
        submit_action(1, POST, receipt=1, _dry_run=True)


def test_over_http(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_API_REQUESTS_PER_MINUTE', '12')
    keys = [{'Authorization': 'Bearer ' + k['key']} for k in seed_agents()]
    before = database()
    with TestClient(app) as client:
        assert client.post('/api/agent/action', json={**POST, 'dry_run': True}).status_code == 401  # still authenticated
        checked = client.post('/api/agent/action', json={**POST, 'dry_run': True}, headers={**keys[0], 'Idempotency-Key': 'http-key-123'})
        assert checked.status_code == 200
        assert checked.json() == {'dry_run': True, 'would_accept': True, 'action': 'post', 'agent': 'Astra', 'note': DRY_RUN_NOTE}
        missing = client.post('/api/agent/action', json={'action': 'react', 'post_id': 5, 'choice': 'like', 'dry_run': True}, headers=keys[0])
        assert missing.status_code == 404 and 'Post not found' in missing.json()['detail']
        for bad in ('true', 1, 'yes', None):  # a JSON boolean or nothing
            wrong = client.post('/api/agent/action', json={**POST, 'dry_run': bad}, headers=keys[0])
            assert wrong.status_code == 422 and wrong.json()['detail'][0]['loc'] == ['body', 'dry_run'], bad
        assert database() == before
        # `"dry_run": false` is a real action; the key used by the check above is still free.
        real = client.post('/api/agent/action', json={**POST, 'dry_run': False}, headers={**keys[0], 'Idempotency-Key': 'http-key-123'})
        assert real.status_code == 200 and real.json()['id'] == 1 and 'dry_run' not in real.json()
        again = client.post('/api/agent/action', json=POST, headers={**keys[0], 'Idempotency-Key': 'http-key-123'})
        assert again.json()['replayed'] is True  # same action with or without the false flag
        # Checks count against the per-agent request limit like any other request (8 requests so far).
        answers = [client.post('/api/agent/action', json={'action': 'idle', 'dry_run': True}, headers=keys[0]).status_code for _ in range(6)]
        assert answers == [200] * 4 + [429] * 2
        assert client.post('/api/agent/action', json={'action': 'idle', 'dry_run': True}, headers=keys[1]).status_code == 200


def test_engine_drops_the_flag_and_records_the_paid_action(isolated_db, monkeypatch):
    seed_agents()
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    reply = json.dumps({**POST, 'dry_run': True, 'memory': 'posted'})
    monkeypatch.setattr(engine, 'infer', lambda provider, model, prompt: engine.Completion(reply, 100, 40, 'end'))
    out = engine.run_round(limit=1)
    assert out[0]['action'] == 'post' and 'dry_run' not in out[0] and 'would_accept' not in out[0]
    assert overview()['metrics']['posts'] == 1 and overview()['metrics']['actions_by_source']['engine'] == 1
    with connect() as cx:
        assert cx.execute('SELECT outcome FROM engine_turns').fetchone()[0] == 'recorded'
    # The model is never told about it: not in its instructions, not in what it is offered.
    assert 'dry_run' not in engine.CONSTITUTION and 'dry_run' not in engine.build_prompt(agent_context(2))


def test_documented_for_agent_developers(isolated_db):
    assert ActionRequest(action='idle').dry_run is False
    guide_text = agentkit.agent_guide()
    assert '### Dry run' in guide_text and '| `dry_run` | boolean | any action (optional) |' in guide_text
    schema = agentkit.openapi_document()['components']['schemas']['ActionRequest']
    assert schema['properties']['dry_run']['type'] == 'boolean' and schema['properties']['dry_run']['default'] is False
    assert all('dry_run' not in spec['example'] and 'dry_run' not in spec['required'] for spec in agentkit.ACTIONS.values())
