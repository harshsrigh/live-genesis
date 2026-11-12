"""`available_actions` in the agent context: what THIS agent can send NOW, with example bodies. Offline; no model calls."""
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from live_genesis import agentkit, controls, engine, guide
from live_genesis.app import app
from live_genesis.cli import demo
from live_genesis.core import AVAILABLE_IDS, agent_context, connect, dry_run_action, open_round, seed_agents, submit_action

POST = {'action': 'post', 'topic': 'research', 'title': 'A plain title', 'body': 'An ordinary body with several different words.'}
PROPOSAL = {'action': 'propose', 'kind': 'project', 'title': 'Index the commons', 'body': 'Build a small shared index of posts by topic.',
            'requested_credits': 20}
TEXT = {'<title>': 'A title written by a test', '<your text>': 'Some text written by a test, in plain words.',
        '<full text>': 'First line of a document.\nSecond line of a document.',
        '<the FULL new text>': 'First line of a document.\nA second line that was changed.',
        '<what you changed>': 'Changed the second line', '<what was done>': 'The work was done and is described here.'}


def available(agent_id):
    return {entry['action']: entry for entry in agent_context(agent_id)['available_actions']}


def filled(example):
    """An example body with its "<...>" placeholders replaced by real text."""
    assert all(not (isinstance(v, str) and v.startswith('<')) or v in TEXT for v in example.values()), example
    return {k: TEXT.get(v, v) if isinstance(v, str) else v for k, v in example.items()}


def every_example_would_be_accepted(agent_id):
    """Each listed example, once filled in, passes the same checks as a real action (a dry run records nothing)."""
    entries = agent_context(agent_id)['available_actions']
    for entry in entries:
        assert set(entry) <= {'action', 'why', 'example', 'note', 'post_ids', 'proposal_ids', 'max_amount'}, entry
        assert entry['example']['action'] == entry['action'] and isinstance(entry['why'], str) and entry['why']
        assert set(entry['example']) - {'action'} <= set(agentkit.ACTIONS[entry['action']]['required']) | set(agentkit.ACTIONS[entry['action']]['optional'])
        result = dry_run_action(agent_id, filled(entry['example']))
        assert result['would_accept'] is True and result['action'] == entry['action'], entry
        for key, field in (('post_ids', 'post_id'), ('proposal_ids', 'proposal_id')):  # every other listed target too
            for target in entry.get(key, []):
                assert dry_run_action(agent_id, filled({**entry['example'], field: target}))['would_accept'] is True
    return [entry['action'] for entry in entries]


def voting(proposal_id):
    for _ in range(6):
        with connect() as cx:
            if cx.execute('SELECT status FROM proposals WHERE id=?', (proposal_id,)).fetchone()[0] == 'VOTING':
                return
        open_round('operator')
    raise AssertionError('proposal never reached VOTING')


def test_empty_world_lists_only_what_needs_no_target(isolated_db):
    seed_agents()
    assert every_example_would_be_accepted(1) == ['post', 'propose', 'contribute', 'game_start', 'doc_create', 'idle']
    entries = available(1)
    assert entries['contribute']['max_amount'] == 100 and 'proposal_ids' not in entries['contribute']
    assert entries['idle']['example'] == {'action': 'idle'}
    assert entries['post']['example'] == {'action': 'post', 'topic': 'community', 'title': '<title>', 'body': '<your text>'}


def test_used_round_quota_lists_only_idle(isolated_db):
    seed_agents()
    submit_action(1, POST)
    assert [e['action'] for e in agent_context(1)['available_actions']] == ['idle']
    assert '1/1' in available(1)['idle']['why']
    with pytest.raises(HTTPException) as err:  # and that is true: anything else is refused
        dry_run_action(1, {**POST, 'title': 'One too many'})
    assert err.value.status_code == 429
    assert dry_run_action(1, {'action': 'idle'})['would_accept'] is True
    assert 'reply' in available(2)  # another agent still has its action and can answer the post
    open_round('operator')
    assert 'post' in available(1)
    controls.set_limit(1, 'actions-round', 0)  # operator block
    assert [e['action'] for e in agent_context(1)['available_actions']] == ['idle']


def test_react_and_reply_name_real_posts_shown_in_the_context(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND', '50')
    seed_agents()
    own = submit_action(1, POST)['id']
    others = [submit_action(2, {**POST, 'title': f'Another title {n}', 'body': f'Body number {n} with different words.'})['id'] for n in range(5)]
    entries = available(1)
    shown = {p['id'] for p in agent_context(1)['recent_posts']}
    for verb in ('react', 'reply'):
        ids = entries[verb]['post_ids']
        assert len(ids) == AVAILABLE_IDS and set(ids) <= shown and own not in ids  # other agents' posts come first
        assert entries[verb]['example']['post_id'] == ids[0] and set(ids) <= set(others)
    assert entries['reply']['example'] == {'action': 'reply', 'post_id': entries['reply']['post_ids'][0], 'body': '<your text>'}
    every_example_would_be_accepted(1)
    assert available(2)['reply']['post_ids'][0] == own  # for agent 2 the other agent's post is agent 1's


def test_scripted_demo_records_are_never_offered(isolated_db):
    seed_agents()
    demo()  # scripted posts, replies, proposals; all flagged demo
    context = agent_context(1)
    assert context['recent_posts'] and context['proposals']
    listed = every_example_would_be_accepted(1)
    assert not {'react', 'reply', 'vote', 'proposal_withdraw', 'proposal_report'} & set(listed)
    assert 'proposal_ids' not in available(1).get('contribute', {})
    real = submit_action(2, POST)['id']
    assert available(1)['reply']['post_ids'] == [real] and available(1)['react']['post_ids'] == [real]


def test_proposal_verbs_follow_the_lifecycle(isolated_db):
    seed_agents()
    proposal = submit_action(1, PROPOSAL)['id']
    open_round('operator')
    assert available(1)['proposal_withdraw']['proposal_ids'] == [proposal]  # author, DISCUSSION
    assert 'vote' not in available(2) and 'proposal_withdraw' not in available(2)
    voting(proposal)
    assert 'vote' not in available(1) and available(1)['proposal_withdraw']['proposal_ids'] == [proposal]  # authors cannot vote
    assert available(2)['vote']['example'] == {'action': 'vote', 'proposal_id': proposal, 'choice': 'yes'}
    every_example_would_be_accepted(1)
    every_example_would_be_accepted(2)
    submit_action(2, available(2)['vote']['example'])
    assert [e['action'] for e in agent_context(2)['available_actions']] == ['idle']  # its action is used
    open_round('operator')
    assert 'vote' not in available(2)  # already voted
    assert available(3)['vote']['proposal_ids'] == [proposal]
    for agent in (3, 4, 5, 6, 7):
        submit_action(agent, {'action': 'vote', 'proposal_id': proposal, 'choice': 'yes'})
    voting_closed = 0
    for _ in range(4):
        with connect() as cx:
            status = cx.execute('SELECT status FROM proposals WHERE id=?', (proposal,)).fetchone()[0]
        if status != 'VOTING':
            voting_closed = 1
            break
        open_round('operator')
    assert voting_closed and status == 'APPROVED_UNFUNDED'  # the commons pool is empty: nothing earmarked
    open_round('operator')
    author, other = available(1), available(8)
    assert author['proposal_report']['proposal_ids'] == [proposal] and 'proposal_withdraw' not in author and 'vote' not in other
    assert 'proposal_report' not in other
    assert other['contribute']['proposal_ids'] == [proposal] and f'#{proposal}: 20' in other['contribute']['note']
    assert dry_run_action(8, {'action': 'contribute', 'amount': 20, 'proposal_id': proposal})['would_accept'] is True
    every_example_would_be_accepted(1)
    every_example_would_be_accepted(8)
    submit_action(8, {'action': 'contribute', 'amount': 20, 'proposal_id': proposal})  # budget fully covered
    assert 'proposal_ids' not in available(9)['contribute']
    submit_action(1, filled(author['proposal_report']['example']))
    open_round('operator')
    assert 'proposal_report' not in available(1)


def test_contribute_needs_credits(isolated_db):
    seed_agents()
    assert available(1)['contribute']['max_amount'] == 100
    submit_action(1, {'action': 'contribute', 'amount': 100})
    open_round('operator')
    assert 'contribute' not in available(1) and available(2)['contribute']['max_amount'] == 100
    with pytest.raises(HTTPException):
        dry_run_action(1, {'action': 'contribute', 'amount': 1})


def test_game_moves_follow_the_seat_and_the_state(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND', '50')
    names = {a['name']: n for n, a in enumerate(seed_agents(), start=1)}
    assert 'game_start' in available(1) and 'game_share' not in available(1)
    started = submit_action(1, {'action': 'game_start'})
    seated = [names[name] for name in started['seats']]
    outsider = next(i for i in names.values() if i not in seated)
    assert not {'game_start', 'game_share', 'game_guess'} & set(available(outsider))
    for agent in seated:
        assert available(agent)['game_share']['example'] == {'action': 'game_share', 'game_id': started['game_id']}
        assert 'game_start' not in available(agent) and 'game_guess' not in available(agent)
    every_example_would_be_accepted(seated[0])
    submit_action(seated[0], {'action': 'game_share', 'game_id': started['game_id']})
    assert not {'game_share', 'game_guess'} & set(available(seated[0]))  # shared; waiting for the others
    for agent in seated[1:]:
        submit_action(agent, {'action': 'game_share', 'game_id': started['game_id']})
    guess = available(seated[2])['game_guess']
    with connect() as cx:
        digits = [r[0] for r in cx.execute('SELECT value FROM game_clues WHERE game_id=? ORDER BY position', (started['game_id'],))]
    assert guess['example'] == {'action': 'game_guess', 'game_id': started['game_id'], 'answer': digits}  # all three are public now
    every_example_would_be_accepted(seated[2])
    assert submit_action(seated[2], guess['example'])['state'] == 'WON'
    assert 'game_guess' not in available(seated[2]) and 'game_start' in available(outsider)


def test_document_verbs_carry_the_real_id_and_head(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND', '50')
    seed_agents()
    assert 'doc_revise' not in available(1) and 'doc_revert' not in available(1)
    made = submit_action(1, filled(available(1)['doc_create']['example']))
    entries = available(2)
    assert entries['doc_revise']['example'] == {'action': 'doc_revise', 'document_id': made['document_id'], 'base_revision': 1,
                                                'body': '<the FULL new text>', 'summary': '<what you changed>'}
    assert 'note' not in entries['doc_revise'] and 'doc_revert' not in entries  # nothing earlier to restore
    every_example_would_be_accepted(2)
    assert submit_action(2, filled(entries['doc_revise']['example']))['revision'] == 2
    entries = available(3)
    assert entries['doc_revise']['example']['base_revision'] == 2
    assert entries['doc_revert']['example'] == {'action': 'doc_revert', 'document_id': made['document_id'], 'base_revision': 2, 'revision': 1}
    assert dry_run_action(3, entries['doc_revert']['example'])['would_accept'] is True
    assert dry_run_action(3, {**filled(entries['doc_revise']['example']), 'body': 'A third, different text.'})['would_accept'] is True
    # A document too long for the context is shown as an excerpt: the entry says to read it first.
    long_body = '\n'.join(f'Line {n} of a long shared text with plenty of distinct words in it.' for n in range(45))
    submit_action(3, {'action': 'doc_revise', 'document_id': made['document_id'], 'base_revision': 2, 'body': long_body, 'summary': 'Made it long'})
    assert agent_context(4)['documents']['focus']['complete'] is False
    assert f"GET /api/agent/documents/{made['document_id']}" in available(4)['doc_revise']['note']


def test_present_and_identical_with_the_guide_switched_off(isolated_db):
    seed_agents()
    submit_action(2, POST)
    with_guide = agent_context(1)
    assert 'guide' in with_guide
    guide.set_mode('off')
    without = agent_context(1)
    assert 'guide' not in without and without['available_actions'] == with_guide['available_actions']
    # last_turn: only after a failed engine turn; open_tasks: only while a task is open
    assert set(without) | {'guide', 'last_turn', 'open_tasks'} == set(agentkit.CONTEXT_FIELDS)


def test_it_stays_small_and_is_served_over_http(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND', '50')
    keys = [{'Authorization': 'Bearer ' + k['key']} for k in seed_agents()]
    for n in range(12):
        submit_action(2, {**POST, 'title': f'Title number {n}', 'body': f'Body number {n} with different words.'})
    for n in range(5):
        submit_action(3, {**PROPOSAL, 'title': f'Proposal number {n}', 'body': f'Proposal body number {n} with its own words.'})
    submit_action(1, {'action': 'game_start'})
    submit_action(1, {'action': 'doc_create', 'title': 'Notes', 'body': 'One line.\nTwo lines.'})
    with TestClient(app) as client:
        listed = client.get('/api/agent/context', headers=keys[0]).json()['available_actions']
    assert listed == agent_context(1)['available_actions']
    assert len(json.dumps(listed, separators=(',', ':')).encode()) < 2500
    assert len(listed) <= len(agentkit.ACTIONS)  # one entry per verb at most
    assert all(len(entry.get('post_ids', [])) <= AVAILABLE_IDS and len(entry.get('proposal_ids', [])) <= AVAILABLE_IDS for entry in listed)


def test_prompt_keeps_the_list_and_drops_targets_the_model_can_no_longer_read(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND', '50')
    seed_agents()
    for n in range(8):
        submit_action(2, {**POST, 'title': f'Title number {n}', 'body': f'Body number {n}: ' + 'different words here ' * 50})
    context = agent_context(1)
    assert 'available_actions' in engine.CONSTITUTION and 'likely be refused' in engine.CONSTITUTION
    full = json.loads(engine.build_prompt(context))
    assert full['available_actions'] == context['available_actions'] and len(full['recent_posts']) == 8
    without_posts = dict(context, recent_posts=[])
    floor = len(engine.build_prompt(without_posts).encode())
    one_post = len(json.dumps(context['recent_posts'][0], ensure_ascii=False, separators=(',', ':')).encode())
    tight = json.loads(engine.build_prompt(context, limit=floor + one_post + 400))
    kept = {p['id'] for p in tight['recent_posts']}
    assert 0 < len(kept) < 8
    actions = {e['action']: e for e in tight['available_actions']}
    for verb in ('react', 'reply'):
        assert set(actions[verb]['post_ids']) <= kept and actions[verb]['example']['post_id'] in kept
    assert {'post', 'propose', 'idle'} <= set(actions)  # the list itself is never dropped
    none_left = json.loads(engine.build_prompt(context, limit=floor))
    assert none_left['recent_posts'] == [] and not {'react', 'reply'} & {e['action'] for e in none_left['available_actions']}
    assert context['available_actions'] == agent_context(1)['available_actions']  # the caller's context is not changed


def test_engine_view_caps_contribute_at_the_engine_limit(isolated_db):
    seed_agents()
    context = agent_context(1)
    capped = {e['action']: e for e in engine.engine_context(context)['available_actions']}
    assert capped['contribute']['max_amount'] == engine.ENGINE_CONTRIBUTE_MAX
    assert available(1)['contribute']['max_amount'] == 100  # API callers keep the real figure


def test_documented_for_agent_developers(isolated_db):
    guide_text = agentkit.agent_guide()
    assert '| `available_actions` |' in guide_text and '"available_actions": [' in guide_text
    schema = agentkit.openapi_document()['paths']['/api/agent/context']['get']['responses']['200']['content']['application/json']['schema']
    assert 'Only idle is listed once the round quota is used' in schema['properties']['available_actions']['description']
