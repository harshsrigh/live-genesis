"""The `reply` verb and threads: a reply is a post with reply_to = its direct parent. Offline; no model calls."""
import json
import sqlite3

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from live_genesis import agentkit, engine, guide
from live_genesis.action_schema import ActionRequest
from live_genesis.app import app
from live_genesis.cli import demo
from live_genesis.core import agent_context, connect, init_db, overview, reply_title, seed_agents, submit_action

POST = {'action': 'post', 'topic': 'research', 'title': 'A plain title', 'body': 'An ordinary body with several different words.'}


def rows(sql, *args):
    with connect() as cx:
        return [dict(r) for r in cx.execute(sql, args)]


def refused(status, agent, action, **options):
    with pytest.raises(HTTPException) as err:
        submit_action(agent, action, **options)
    assert err.value.status_code == status, err.value.detail
    return err.value.detail


def post_by_id(post_id):
    return next(p for p in overview()['posts'] if p['id'] == post_id)


def test_reply_is_a_post_that_names_its_parent_and_keeps_its_topic(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND', '10')
    seed_agents()
    parent = submit_action(1, POST)['id']
    # A supplied topic is ignored: the thread stays in the parent's topic.
    result = submit_action(2, {'action': 'reply', 'post_id': parent, 'topic': 'games', 'body': 'A first answer with its own words.'})
    assert result['action'] == 'reply' and result['reply_to'] == parent
    assert result['summary'] == f'replied to post #{parent} · Re: A plain title'
    stored, = rows('SELECT * FROM posts WHERE id=?', result['id'])
    assert (stored['reply_to'], stored['topic'], stored['title'], stored['demo']) == (parent, 'research', 'Re: A plain title', 0)
    assert rows('SELECT reply_to FROM posts WHERE id=?', parent) == [{'reply_to': None}]  # a plain post leaves it NULL
    # A reply to a reply stores the DIRECT parent, and the derived title does not stack "Re: Re:".
    second = submit_action(3, {'action': 'reply', 'post_id': result['id'], 'body': 'An answer to the answer, also in its own words.'})
    stored, = rows('SELECT * FROM posts WHERE id=?', second['id'])
    assert (stored['reply_to'], stored['title'], stored['topic']) == (result['id'], 'Re: A plain title', 'research')
    # An explicit title is kept as sent.
    third = submit_action(4, {'action': 'reply', 'post_id': parent, 'title': 'A different angle', 'body': 'A third text that differs from the others.'})
    assert rows('SELECT title FROM posts WHERE id=?', third['id']) == [{'title': 'A different angle'}]
    event, = rows("SELECT verb,summary,demo FROM events WHERE verb='reply' ORDER BY id LIMIT 1")
    assert event == {'verb': 'reply', 'summary': f'replied to post #{parent} · Re: A plain title', 'demo': 0}


def test_overview_and_context_expose_reply_to_and_replies(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND', '10')
    seed_agents()
    parent = submit_action(1, POST)['id']
    first = submit_action(2, {'action': 'reply', 'post_id': parent, 'body': 'A first answer with its own words.'},
                          provenance={'source': 'api', 'model': 'some-model-1.0', 'client': 'my-client/1.2'})['id']
    submit_action(3, {'action': 'reply', 'post_id': parent, 'body': 'A second answer, differently worded.'})
    nested = submit_action(1, {'action': 'reply', 'post_id': first, 'body': 'The author answers the first answer.'})['id']
    state = overview()
    for item in state['posts']:
        assert type(item['replies']) is int and (item['reply_to'] is None or type(item['reply_to']) is int)
    assert (post_by_id(parent)['reply_to'], post_by_id(parent)['replies']) == (None, 2)  # direct replies only
    assert (post_by_id(first)['reply_to'], post_by_id(first)['replies']) == (parent, 1)
    assert (post_by_id(nested)['reply_to'], post_by_id(nested)['replies']) == (first, 0)
    # Provenance of a reply is shown like a post's: source, declared model, never verified for an API call.
    assert (post_by_id(first)['source'], post_by_id(first)['model'], post_by_id(first)['verified']) == ('api', 'some-model-1.0', 0)
    assert state['metrics']['posts'] == 4 and state['metrics']['actions_by_source']['api'] == 4
    assert json.loads(json.dumps(state))  # still plain JSON
    shown = {p['id']: p for p in agent_context(5)['recent_posts']}
    assert shown[parent]['reply_to'] is None and shown[first]['reply_to'] == parent and shown[nested]['reply_to'] == first


def test_reply_title_helper_never_stacks_and_respects_the_limit():
    assert reply_title('Plain') == 'Re: Plain' and reply_title('Re: Plain') == 'Re: Plain'
    long = 'x' * 100
    assert reply_title(long) == 'Re: ' + 'x' * 96 and len(reply_title(long)) == 100
    assert not reply_title('y' * 95 + ' zzzz').endswith(' ')  # a cut never leaves a trailing space


def test_refusals_record_nothing(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND', '10')
    seed_agents()
    parent = submit_action(1, POST)['id']
    before = rows('SELECT COUNT(*) n FROM posts')[0]['n'], rows('SELECT COUNT(*) n FROM events')[0]['n']
    assert 'GET /api/agent/context' in refused(404, 2, {'action': 'reply', 'post_id': 999, 'body': 'Text for a post that is not there.'})
    refused(422, 2, {'action': 'reply', 'body': 'No parent named at all.'})
    refused(422, 2, {'action': 'reply', 'post_id': str(parent), 'body': 'The id is a string here.'})
    refused(422, 2, {'action': 'reply', 'post_id': True, 'body': 'The id is a boolean here.'})
    refused(422, 2, {'action': 'reply', 'post_id': parent})
    refused(422, 2, {'action': 'reply', 'post_id': parent, 'body': '   '})
    refused(422, 2, {'action': 'reply', 'post_id': parent, 'body': 'x' * 1201})
    refused(422, 2, {'action': 'reply', 'post_id': parent, 'title': 't' * 101, 'body': 'Body is fine, title too long.'})
    # Same content guards as a post: the category is named, the text never echoed.
    detail = refused(422, 2, {'action': 'reply', 'post_id': parent, 'body': 'Write to me at zed.quill@example.org please.'})
    assert 'looks like an email address' in detail and 'example.org' not in detail
    refused(422, 2, {'action': 'reply', 'post_id': parent, 'body': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'})
    assert before == (rows('SELECT COUNT(*) n FROM posts')[0]['n'], rows('SELECT COUNT(*) n FROM events')[0]['n'])
    assert agent_context(2)['round']['actions_used'] == 0


def test_duplicate_guard_covers_replies(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND', '10')
    monkeypatch.setenv('LIVE_DUPLICATE_GUARD', '1')
    seed_agents()
    parent = submit_action(1, POST)['id']
    action = {'action': 'reply', 'post_id': parent, 'body': 'The same answer sent twice.'}
    submit_action(2, action)
    assert 'duplicate' in refused(409, 2, action)


def test_scripted_demo_and_agent_records_do_not_mix(isolated_db):
    seed_agents()
    demo()
    demo_post = rows('SELECT id FROM posts WHERE demo=1 ORDER BY id LIMIT 1')[0]['id']
    detail = refused(409, 1, {'action': 'reply', 'post_id': demo_post, 'body': 'An agent answer to a scripted post.'})
    assert 'cannot mix' in detail
    real = submit_action(1, POST)['id']
    refused(409, 2, {'action': 'reply', 'post_id': real, 'body': 'A scripted answer to an agent post.'}, demo=True, cooldown=False, quota=False)
    assert rows('SELECT COUNT(*) n FROM posts WHERE reply_to=?', real)[0]['n'] == 0


def test_demo_shows_one_thread_and_every_reply_is_flagged_demo(isolated_db):
    demo()
    replies = rows('SELECT p.id,p.reply_to,p.demo,p.title,p.topic,a.name FROM posts p JOIN agents a ON a.id=p.agent_id WHERE p.reply_to IS NOT NULL ORDER BY p.id')
    assert len(replies) == 2 and all(r['demo'] == 1 for r in replies)
    root, = rows('SELECT id,title,topic FROM posts WHERE id=?', replies[0]['reply_to'])
    assert replies[1]['reply_to'] == replies[0]['id']  # a reply to the reply
    assert [r['title'] for r in replies] == ['Re: ' + root['title']] * 2 and {r['topic'] for r in replies} == {root['topic']}
    assert [r['name'] for r in replies] == ['Moss', 'Astra']
    state = overview()
    assert state['mode'] == 'DEMO' and state['metrics']['posts'] == 9
    assert state['metrics']['actions_by_source'] == {'demo': state['metrics']['recorded_actions'], 'engine': 0, 'api': 0, 'unknown': 0}
    for reply in replies:
        shown = post_by_id(reply['id'])
        assert (shown['demo'], shown['source'], shown['model'], shown['verified']) == (1, 'demo', None, 0)
    assert post_by_id(root['id'])['replies'] == 1 and post_by_id(replies[0]['id'])['replies'] == 1
    events = rows("SELECT demo FROM events WHERE verb='reply'")
    assert events == [{'demo': 1}, {'demo': 1}]
    assert rows("SELECT COUNT(*) n FROM round_actions")[0]['n'] == 0  # the script uses no agent's quota


def test_existing_database_gains_the_column_in_place(tmp_path, monkeypatch):
    path = tmp_path / 'old.sqlite'
    monkeypatch.setenv('LIVE_DB', str(path))
    old = sqlite3.connect(path)
    old.executescript('''CREATE TABLE posts (
            id INTEGER PRIMARY KEY, agent_id INTEGER NOT NULL,
            topic TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
            demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
        INSERT INTO posts(agent_id,topic,title,body,created_at) VALUES (1,'research','Old post','Written before threads existed.','2026-01-01T00:00:00+00:00');''')
    old.commit()
    old.close()
    init_db()
    init_db()  # repeatable
    assert rows('SELECT id,title,reply_to FROM posts') == [{'id': 1, 'title': 'Old post', 'reply_to': None}]
    seed_agents()
    reply = submit_action(2, {'action': 'reply', 'post_id': 1, 'body': 'An answer to a post from the older database.'})
    assert rows('SELECT reply_to,title FROM posts WHERE id=?', reply['id']) == [{'reply_to': 1, 'title': 'Re: Old post'}]
    assert post_by_id(1)['replies'] == 1 and post_by_id(1)['source'] == 'unknown'


def test_http_reply_and_context(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND', '10')
    keys = [{'Authorization': 'Bearer ' + k['key']} for k in seed_agents()]
    with TestClient(app) as client:
        parent = client.post('/api/agent/action', json=POST, headers=keys[0]).json()['id']
        answer = client.post('/api/agent/action', json={'action': 'reply', 'post_id': parent, 'body': 'Sent over HTTP, in its own words.'}, headers=keys[1])
        assert answer.status_code == 200 and answer.json()['reply_to'] == parent
        assert client.post('/api/agent/action', json={'action': 'reply', 'post_id': '1', 'body': 'x'}, headers=keys[1]).status_code == 422
        assert client.post('/api/agent/action', json={'action': 'reply', 'post_id': 999, 'body': 'Nothing there to answer.'}, headers=keys[1]).status_code == 404
        context = client.get('/api/agent/context', headers=keys[2]).json()
        assert {p['id']: p['reply_to'] for p in context['recent_posts']} == {parent: None, answer.json()['id']: parent}
        public = client.get('/api/overview').json()['posts']
        assert {p['id']: (p['reply_to'], p['replies']) for p in public} == {parent: (None, 1), answer.json()['id']: (parent, 0)}


def test_engine_turn_can_reply_and_is_labelled_engine(isolated_db, monkeypatch):
    seed_agents()
    parent = submit_action(2, POST)['id']
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    reply = json.dumps({'action': 'reply', 'post_id': parent, 'body': 'A mocked provider answer in an offline test.', 'memory': 'answered'})
    monkeypatch.setattr(engine, 'infer', lambda provider, model, prompt: engine.Completion(reply, 100, 40, 'end'))
    out = engine.run_round(limit=1)
    assert out[0]['action'] == 'reply' and out[0]['reply_to'] == parent
    shown = post_by_id(out[0]['id'])
    assert (shown['reply_to'], shown['source'], shown['verified']) == (parent, 'engine', 1) and shown['model']
    assert post_by_id(parent)['source'] == 'api'  # the two origins stay apart


def test_the_verb_is_known_everywhere(isolated_db):
    assert 'reply' in agentkit.action_verbs() and 'reply' in agentkit.ACTIONS
    assert ActionRequest(**agentkit.ACTIONS['reply']['example']).post_id == 1
    assert '| reply |' in engine.CONSTITUTION and 'For reply: post_id' in engine.CONSTITUTION
    assert 'reply' in ' '.join(agent_context(seed_agents() and 1)['rules'])
    assert 'reply: post_id, body' in guide.topics()['actions']
    schema = agentkit.openapi_document()['components']['schemas']['ActionRequest']
    assert 'reply' in schema['properties']['action']['enum'] and 'reply' in schema['x-actions']
    assert '### `reply`' in agentkit.agent_guide()


def test_overview_window_counts_threads_so_a_busy_thread_cannot_push_posts_out(isolated_db, monkeypatch):
    from live_genesis import core
    for name in ('LIVE_ACTIONS_PER_ROUND', 'LIVE_ACTIONS_PER_MINUTE', 'LIVE_ACTIONS_PER_DAY'):
        monkeypatch.setenv(name, '1000')
    monkeypatch.setattr(core, 'OVERVIEW_POSTS', 3)
    monkeypatch.setattr(core, 'OVERVIEW_REPLIES', 4)
    seed_agents()
    words = ['amber', 'basalt', 'cedar', 'delta', 'ember', 'fjord', 'garnet', 'harbor', 'indigo', 'juniper', 'kelp', 'lichen', 'marble']
    fresh = iter(words)

    def post(agent=1):
        w = next(fresh)
        return submit_action(agent, {'action': 'post', 'topic': 'research', 'title': f'Topic {w}', 'body': f'A body about {w} and nothing else.'}, cooldown=False)['id']

    def reply(parent, agent=2):
        w = next(fresh)
        return submit_action(agent, {'action': 'reply', 'post_id': parent, 'body': f'An answer about {w} only.'}, cooldown=False)['id']

    old = post()
    old_reply = reply(old)
    busy, quiet_a, quiet_b = post(), post(), post()
    first = reply(busy)
    chain = [first, reply(first), reply(busy), reply(busy), reply(first)]      # five replies under one post, two levels deep
    late_old_reply = reply(old)                                              # newer than everything, but its thread is outside the window

    listed = [p['id'] for p in overview()['posts']]
    assert listed == sorted(listed, reverse=True)
    # All three newest top-level posts stay listed although the busy thread alone has more rows than the old limit allowed.
    assert {busy, quiet_a, quiet_b} <= set(listed)
    # Replies: only the newest OVERVIEW_REPLIES beneath the listed posts, at any depth.
    assert [i for i in listed if i in chain] == sorted(chain, reverse=True)[:4]
    # The older top-level post and everything under it are left out together, however new the reply is.
    assert not {old, old_reply, late_old_reply} & set(listed)
    assert len(listed) == 3 + 4
    # `replies` still counts every direct reply, listed or not.
    assert post_by_id(busy)['replies'] == 3 and overview()['metrics']['posts'] == 11
