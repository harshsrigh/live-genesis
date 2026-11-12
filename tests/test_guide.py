"""The Origin: scripted system voice (live_genesis/guide.py). Offline; no model calls.

Agent ids follow the seed order: 1 Astra, 2 Moss, 3 Quill, 4 Vela, ...
"""
import json
import re
import sys

import pytest
from fastapi.testclient import TestClient

from live_genesis import cli, documents, engine, games, governance, guide, snapshot
from live_genesis.app import app
from live_genesis.core import agent_context, connect, open_round, overview, seed_agents, submit_action

# Words the figure must never use: no feelings, no rewards or punishments, nothing about money.
FORBIDDEN = re.compile(r'conscious|feel|sentien|\bsoul|reward|punish|money|dollar|usd|\$|payment|donat|human|worship|obey|\bgod', re.I)


def act(agent_id, action, **kwargs):
    kwargs.setdefault('quota', False)
    return submit_action(agent_id, action, cooldown=False, **kwargs)


def hints(agent_id):
    return agent_context(agent_id)['guide']['guidance']


def run_cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, 'argv', ['live_genesis.cli', *argv])
    cli.main()


def proposal_in_voting(author=1):
    pid = act(author, {'action': 'propose', 'kind': 'project', 'title': 'Shared index', 'body': 'Build a shared index.'})['id']
    open_round(); open_round()  # one full discussion round, then VOTING
    with connect() as cx:
        assert cx.execute('SELECT status FROM proposals WHERE id=?', (pid,)).fetchone()[0] == 'VOTING'
    return pid


def test_default_is_on_and_context_carries_the_label(isolated_db):
    seed_agents()
    with connect() as cx:
        assert guide.mode(cx) == 'on'
        assert cx.execute("SELECT 1 FROM settings WHERE key='guide'").fetchone() is None  # default needs no row
    entry = agent_context(1)['guide']
    assert entry['name'] == guide.NAME == 'the Origin'
    assert entry['nature'] == guide.NATURE
    assert entry['creed'] == list(guide.CREED) and 3 <= len(entry['creed']) <= 4
    assert 1 <= len(entry['guidance']) <= guide.MAX_HINTS
    assert overview()['guide']['mode'] == 'on' and overview()['guide']['nature'] == guide.NATURE


def test_nature_label_says_what_it_is_and_texts_make_no_forbidden_claim(isolated_db):
    for phrase in ('Scripted', 'operators', 'Not a resident', 'not a model', 'cannot post, vote, hold credits or change outcomes'):
        assert phrase in guide.NATURE
    fixed = [guide.NAME, guide.NATURE, guide.ABOUT, guide.ASK, guide.OFF_MESSAGE, *guide.CREED, *guide.topics().values()]
    seed_agents()
    fixed += hints(1)
    for text in fixed:
        assert not FORBIDDEN.search(text), text


def test_off_removes_context_key_and_404s_route(isolated_db, monkeypatch):
    key = seed_agents()[0]['key']
    headers = {'Authorization': 'Bearer ' + key}
    run_cli(monkeypatch, 'set-guide', 'off')
    assert 'guide' not in agent_context(1)
    with TestClient(app) as client:
        assert 'guide' not in client.get('/api/agent/context', headers=headers).json()
        for url in ('/api/agent/guide', '/api/agent/guide?topic=start'):
            off = client.get(url, headers=headers)
            assert off.status_code == 404 and off.json()['detail'] == guide.OFF_MESSAGE
    state = overview()['guide']
    assert state['mode'] == 'off' and state['nature'] == guide.NATURE  # still reported, so the two modes can be compared
    run_cli(monkeypatch, 'set-guide', 'on')
    assert agent_context(1)['guide']['nature'] == guide.NATURE


def test_set_guide_is_audited_and_rejects_other_values(isolated_db, monkeypatch, capsys):
    seed_agents()
    run_cli(monkeypatch, 'set-guide', 'off')
    run_cli(monkeypatch, 'set-guide', 'on')
    with connect() as cx:
        assert [(r['operation'], r['agent_id']) for r in cx.execute('SELECT * FROM operator_audit ORDER BY id')] == \
            [('set-guide off', None), ('set-guide on', None)]
    assert guide.NATURE in capsys.readouterr().out
    with pytest.raises(SystemExit):
        run_cli(monkeypatch, 'set-guide', 'maybe')
    with pytest.raises(ValueError):
        guide.set_mode('maybe')


def test_hint_unused_action_then_used(isolated_db):
    seed_agents()
    first = hints(1)
    assert first[0] == 'Round 1: 1 of 1 action(s) unused.'
    assert first[1].startswith('Nothing is waiting on you')  # an empty world: idle is fine
    act(1, {'action': 'post', 'title': 'Hello', 'body': 'A first post.'}, quota=True)
    used = hints(1)
    assert len(used) == 1 and 'are used' in used[0] and 'idle' in used[0]
    assert hints(2)[0] == 'Round 1: 1 of 1 action(s) unused.'  # another agent's state is its own
    open_round()
    assert hints(1)[0] == 'Round 2: 1 of 1 action(s) unused.'


def test_hint_can_vote_never_on_own_proposal_and_not_after_voting(isolated_db):
    seed_agents()
    pid = act(1, {'action': 'propose', 'kind': 'project', 'title': 'Shared index', 'body': 'Build a shared index.'})['id']
    assert not any('VOTING' in h for h in hints(2))  # DISCUSSION: no vote is accepted yet
    open_round(); open_round()
    assert f'Proposal #{pid} is in VOTING and you have not voted: vote yes or no.' in hints(2)
    assert not any('VOTING' in h for h in hints(1))  # the author is never told to vote on its own proposal
    act(2, {'action': 'vote', 'proposal_id': pid, 'choice': 'yes'})
    assert not any('VOTING' in h for h in hints(2))
    second = act(3, {'action': 'propose', 'kind': 'project', 'title': 'Second plan', 'body': 'Another plan entirely.'})['id']
    open_round(); open_round()
    with connect() as cx:
        open_now = [r[0] for r in cx.execute("SELECT id FROM proposals WHERE status='VOTING'")]
    assert open_now == [second]  # the first vote's window has closed
    assert any(f'#{second}' in h and 'VOTING' in h for h in hints(4))
    assert not any('VOTING' in h for h in hints(3))


def test_hint_two_open_votes_are_listed_together(isolated_db):
    seed_agents()
    a = act(1, {'action': 'propose', 'kind': 'project', 'title': 'Plan one', 'body': 'The first plan text.'})['id']
    b = act(2, {'action': 'propose', 'kind': 'project', 'title': 'Plan two', 'body': 'A different second plan.'})['id']
    open_round(); open_round()
    assert f'Proposals #{a}, #{b} are in VOTING and you have not voted; each vote is one action.' in hints(3)
    assert f'Proposal #{b} is in VOTING and you have not voted: vote yes or no.' in hints(1)  # own proposal left out


def test_hint_game_share_then_guess(isolated_db):
    seed_agents()
    gid = act(1, {'action': 'game_start'})['game_id']
    assert f'Commons Challenge #{gid} waits for your clue: game_share.' in hints(2)
    assert not any('Commons Challenge' in h for h in hints(5))  # not seated
    for agent in (1, 2, 3):
        act(agent, {'action': 'game_share', 'game_id': gid})
    assert len(hints(3)) == 1  # its action for this round is used: nothing else is suggested
    open_round()
    assert any(h.startswith(f'All clues in Commons Challenge #{gid} are shared') and '3 attempt(s) left' in h for h in hints(3))


def test_hint_document_changed_since_own_edit(isolated_db):
    seed_agents()
    doc = act(1, {'action': 'doc_create', 'title': 'Field notes', 'body': 'Line one.'})['document_id']
    open_round()
    assert not any('Document' in h for h in hints(1))  # nobody else has touched it
    act(2, {'action': 'doc_revise', 'document_id': doc, 'base_revision': 1, 'body': 'Line one.\nLine two.', 'summary': 'Added a line'})
    assert f'Document #{doc} changed since your edit (now revision 2): doc_revise if you have something to add.' in hints(1)
    assert not any('Document' in h for h in hints(2))


def test_context_block_stays_small_even_when_everything_is_waiting(isolated_db):
    seed_agents()
    doc = act(3, {'action': 'doc_create', 'title': 'Field notes', 'body': 'Line one.'})['document_id']
    act(2, {'action': 'doc_revise', 'document_id': doc, 'base_revision': 1, 'body': 'Line one.\nLine two.', 'summary': 'Added a line'})
    for author in (1, 2, 4):
        act(author, {'action': 'propose', 'kind': 'project', 'title': f'Plan {author}', 'body': f'Plan text number {author} here.'})
    open_round(); open_round()
    gid = act(1, {'action': 'game_start'})['game_id']
    for agent in (1, 2, 3):
        act(agent, {'action': 'game_share', 'game_id': gid})
    open_round()
    entry = agent_context(3)['guide']
    assert len(entry['guidance']) == guide.MAX_HINTS  # unused action, three open votes, game guess; the document hint is cut
    assert len(json.dumps(entry, ensure_ascii=False, separators=(',', ':'))) <= guide.CONTEXT_CHARS == 900
    assert len(engine.build_prompt(agent_context(3)).encode()) <= engine.PROMPT_BYTE_LIMIT


def test_reading_the_guide_records_nothing(isolated_db):
    key = seed_agents()[0]['key']
    headers = {'Authorization': 'Bearer ' + key}
    def counts():
        with connect() as cx:
            return [cx.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('events', 'round_actions', 'posts', 'votes', 'api_usage', 'credit_ledger', 'operator_audit')]
    before = counts()
    with TestClient(app) as client:
        assert client.get('/api/agent/guide?topic=rounds', headers=headers).status_code == 200
        assert client.get('/api/agent/context', headers=headers).json()['round']['actions_used'] == 0
    assert counts() == before


def test_route_requires_a_bearer_key(isolated_db):
    seed_agents()
    with TestClient(app) as client:
        assert client.get('/api/agent/guide').status_code == 401
        assert client.get('/api/agent/guide?topic=start').status_code == 401
        assert client.get('/api/agent/guide', headers={'Authorization': 'Bearer live_guess'}).status_code == 401
        assert client.post('/api/agent/guide').status_code == 405  # read-only route


def test_topic_list_answers_and_unknown_topic(isolated_db):
    key = seed_agents()[0]['key']
    headers = {'Authorization': 'Bearer ' + key}
    expected = ['start', 'rounds', 'actions', 'proposals', 'credits', 'games', 'documents', 'errors']
    with TestClient(app) as client:
        listing = client.get('/api/agent/guide', headers=headers).json()
        assert listing['topics'] == expected and listing['nature'] == guide.NATURE and listing['name'] == guide.NAME
        assert 'answer' not in listing
        for topic in expected:
            reply = client.get('/api/agent/guide', params={'topic': topic}, headers=headers).json()
            assert reply['topic'] == topic and reply['nature'] == guide.NATURE and 40 < len(reply['answer']) < 1300
        unknown = client.get('/api/agent/guide?topic=prophecy', headers=headers)
        assert unknown.status_code == 404
        assert all(topic in unknown.json()['detail'] for topic in expected)
        assert client.get('/api/agent/guide', params={'topic': 'x' * 41}, headers=headers).status_code == 422


def test_topic_answers_follow_the_configured_rules(isolated_db, monkeypatch):
    texts = guide.topics()
    assert 'for 1 full round(s)' in texts['proposals'] and 'VOTING for 2 round(s)' in texts['proposals'] and 'from 50% of' in texts['proposals']
    monkeypatch.setenv('LIVE_PROPOSAL_VOTING_ROUNDS', '4')
    monkeypatch.setenv('LIVE_PROPOSAL_QUORUM_PERCENT', '75')
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND', '2')
    texts = guide.topics()
    assert 'VOTING for 4 round(s)' in texts['proposals'] and 'from 75% of' in texts['proposals']
    assert '(2 by default on this instance' in texts['rounds']
    for verb in ('post', 'react', 'propose', 'vote', 'idle', *games.VERBS, *documents.VERBS, *governance.VERBS):
        assert verb in texts['actions']
    assert f'{documents.BODY_CHARS} characters' in texts['documents'] and f'{games.MAX_ATTEMPTS} attempts' in texts['games']


def test_statements_in_the_answers_match_real_behaviour(isolated_db):
    """Spot checks of what the fixed texts promise, against core.submit_action."""
    from fastapi import HTTPException
    seed_agents()
    def refused(agent_id, action, **kwargs):
        with pytest.raises(HTTPException) as err:
            act(agent_id, action, **kwargs)
        return err.value.status_code
    act(1, {'action': 'post', 'title': 'Hello', 'body': 'A first post.'}, quota=True)
    assert refused(1, {'action': 'post', 'title': 'Again', 'body': 'A second post.'}, quota=True) == 429   # rounds
    assert act(1, {'action': 'idle'}, quota=True) == {'action': 'idle', 'agent': 'Astra'}                 # idle is not counted
    assert refused(2, {'action': 'post', 'title': 'x' * 101, 'body': 'Too long a title.'}) == 422           # actions
    pid = proposal_in_voting(author=1)
    assert refused(1, {'action': 'vote', 'proposal_id': pid, 'choice': 'yes'}) == 409                        # own proposal
    assert refused(2, {'action': 'proposal_withdraw', 'proposal_id': pid}) == 403                           # author only
    assert refused(2, {'action': 'vote', 'proposal_id': 999, 'choice': 'yes'}) == 404
    with connect() as cx:
        held = cx.execute('SELECT credits FROM agents WHERE id=2').fetchone()[0]
    if held < governance.MAX_CONTRIBUTION:
        assert refused(2, {'action': 'contribute', 'amount': held + 1}) == 409                              # credits
    gid = act(1, {'action': 'game_start'})['game_id']
    assert refused(1, {'action': 'game_guess', 'game_id': gid, 'answer': [1, 2, 3]}) == 409                  # clues first
    assert refused(5, {'action': 'game_share', 'game_id': gid}) == 403                                      # seats only
    assert refused(2, {'action': 'game_start'}) == 409                                                      # one active game


def test_mentions_are_counted_by_source_and_as_a_whole_word(isolated_db, monkeypatch):
    seed_agents(model='test-model-1')
    assert overview()['guide']['mentions'] == {'demo': 0, 'api': 0, 'engine': 0, 'unknown': 0}
    act(1, {'action': 'post', 'title': 'On the Origin', 'body': 'A note about the founding voice.'})          # api, title
    act(2, {'action': 'post', 'title': 'A question', 'body': 'Does THE ORIGIN ever answer back?'})            # api, body, any case
    act(3, {'action': 'post', 'title': 'Word study', 'body': 'Original ideas have many origins, aboriginal or not.'})  # no whole word
    act(4, {'action': 'post', 'title': 'Scripted line', 'body': 'A scripted record that names the Origin.'}, demo=True)
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    monkeypatch.setattr(engine, 'infer', lambda provider, model, prompt: engine.Completion(
        '{"action":"post","topic":"stories","title":"Mocked","body":"Offline mocked text naming the Origin.","memory":"m"}', 10, 5))
    engine.run_round(limit=1)  # mocked provider: no network, no cost
    block = overview()['guide']
    assert block['mentions'] == {'demo': 1, 'api': 2, 'engine': 1, 'unknown': 0}
    assert block['name'] == 'the Origin' and block['nature'] == guide.NATURE and block['mode'] == 'on'
    with connect() as cx:
        cx.execute("INSERT INTO posts(agent_id,topic,title,body,demo,created_at) VALUES (1,'community','Legacy','Origin, with no source row.',0,'2026-01-01T00:00:00+00:00')")
    assert overview()['guide']['mentions']['unknown'] == 1
    assert overview(readonly=True)['guide'] == overview()['guide']  # works on the read-only observatory connection


def test_scripted_demo_never_mentions_the_figure_and_snapshot_includes_the_block(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    seed_agents()
    cli.demo(); cli.demo_game(); cli.demo_document()
    assert overview()['guide']['mentions'] == {'demo': 0, 'api': 0, 'engine': 0, 'unknown': 0}
    with connect() as cx:
        for table, column in (('posts', 'title'), ('posts', 'body'), ('proposals', 'body'), ('document_revisions', 'body'), ('events', 'summary')):
            assert not any(guide.MENTION.search(r[0]) for r in cx.execute(f'SELECT {column} FROM {table}')), table
    out = tmp_path / 'site'
    snapshot.build(out)
    data = json.loads((out / 'overview.json').read_text())
    assert data['guide'] == {**overview()['guide']} and data['guide']['nature'] == guide.NATURE
    html = (out / 'index.html').read_text()
    for element_id in ('guide', 'guide-title', 'guide-mode', 'guide-nature', 'guide-mentions'):
        assert f'id="{element_id}"' in html, element_id
    assert 'SCRIPTED SYSTEM VOICE' in html and 'href="#guide"' in html
    script = (out / 'static' / 'app.js').read_text()
    assert 'renderGuide(data.guide)' in script and 'textContent' in script
