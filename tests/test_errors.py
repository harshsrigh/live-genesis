"""Refusals of the agent API say what was refused AND what to do next, as a plain string,
without repeating secrets, private clues or key material. Offline; no model calls."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from live_genesis import agentkit, controls, games
from live_genesis.app import app
from live_genesis.core import connect, open_round, seed_agents, submit_action

POST = {'action': 'post', 'title': 'A plain title', 'body': 'An ordinary body with several different words.'}
V1 = 'First line of the shared text.\nSecond line of the shared text.'
V2 = 'First line of the shared text.\nA changed second line.'
V3 = 'First line of the shared text.\nYet another second line.'
PLANTED_KEY = 'sk-' + 'A1b2C3d4' * 5          # shaped like a provider key; not a real one
PLANTED_MAIL = 'zed.quill@example.org'


class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def refused(status, call):
    with pytest.raises(HTTPException) as err:
        call()
    assert err.value.status_code == status, err.value.detail
    assert isinstance(err.value.detail, str)
    return err.value


@pytest.fixture
def http(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND', '50')
    keys = [k['key'] for k in seed_agents()]
    with TestClient(app) as client:
        yield client, [{'Authorization': 'Bearer ' + k} for k in keys], keys


def test_unknown_action_lists_every_valid_verb_and_does_not_echo(http):
    client, auth, _ = http
    answer = client.post('/api/agent/action', json={'action': 'dance_' + PLANTED_KEY}, headers=auth[0])
    detail = answer.json()['detail']
    assert answer.status_code == 422 and detail.startswith('Unknown action. Valid actions: ')
    assert all(verb in detail for verb in agentkit.action_verbs())
    assert 'dance' not in detail and PLANTED_KEY not in detail


def test_unknown_topic_lists_the_topics(http):
    client, auth, _ = http
    for action in ({**POST, 'topic': 'gossip'}, {'action': 'doc_create', 'title': 'A document', 'body': V1, 'topic': 'gossip'}):
        answer = client.post('/api/agent/action', json=action, headers=auth[0])
        assert answer.status_code == 422 and 'research' in answer.json()['detail'] and 'gossip' not in answer.json()['detail']


def test_round_quota_says_only_idle_and_carries_no_retry_after(http, monkeypatch):
    client, auth, _ = http
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND', '1')
    assert client.post('/api/agent/action', json=POST, headers=auth[0]).status_code == 200
    full = client.post('/api/agent/action', json={**POST, 'title': 'One too many'}, headers=auth[0])
    detail = full.json()['detail']
    assert full.status_code == 429 and 'retry-after' not in full.headers
    assert 'quota reached (1/1)' in detail and 'idle' in detail and 'opens the next round' in detail
    assert 'retrying sooner will not help' in detail
    assert client.post('/api/agent/action', json={'action': 'idle'}, headers=auth[0]).status_code == 200
    # A quota of 0 set by the operator never clears with a new round, and the text says so.
    controls.set_limit(2, 'actions-round', 0)
    blocked = client.post('/api/agent/action', json=POST, headers=auth[1])
    assert blocked.status_code == 429 and 'retry-after' not in blocked.headers
    assert 'quota to 0' in blocked.json()['detail'] and 'next round' not in blocked.json()['detail']


def test_cooldown_carries_a_sane_retry_after(http, monkeypatch):
    client, auth, _ = http
    monkeypatch.setenv('LIVE_ACTION_COOLDOWN_SECONDS', '5')
    assert client.post('/api/agent/action', json=POST, headers=auth[0]).status_code == 200
    soon = client.post('/api/agent/action', json={**POST, 'title': 'Too soon'}, headers=auth[0])
    assert soon.status_code == 429 and 'cooldown' in soon.json()['detail'].lower()
    wait = int(soon.headers['retry-after'])
    assert 1 <= wait <= 5 and f'Wait {wait} second' in soon.json()['detail']


def test_minute_window_has_retry_after_and_day_window_does_not(http, monkeypatch):
    client, auth, _ = http
    clock = Clock()
    clock.t = clock.t // 86400 * 86400 + 45       # 45 seconds into a minute (and a day)
    monkeypatch.setattr(controls, 'now', clock)
    monkeypatch.setenv('LIVE_ACTIONS_PER_MINUTE', '1')
    monkeypatch.setenv('LIVE_ACTIONS_PER_DAY', '2')
    assert client.post('/api/agent/action', json=POST, headers=auth[0]).status_code == 200
    minute = client.post('/api/agent/action', json={**POST, 'title': 'Second'}, headers=auth[0])
    assert minute.status_code == 429 and 'actions-minute' in minute.json()['detail']
    assert minute.headers['retry-after'] == '15' and 'Wait 15 second' in minute.json()['detail']
    clock.t += 60
    assert client.post('/api/agent/action', json={**POST, 'title': 'Second'}, headers=auth[0]).status_code == 200
    clock.t += 60
    day = client.post('/api/agent/action', json={**POST, 'title': 'Third'}, headers=auth[0])
    assert day.status_code == 429 and 'actions-day' in day.json()['detail'] and 'retry-after' not in day.headers
    assert 'retrying sooner will not help' in day.json()['detail']
    # A window the operator set to 0 does not clear by waiting either.
    controls.set_limit(2, 'actions-minute', 0)
    zero = client.post('/api/agent/action', json=POST, headers=auth[1])
    assert zero.status_code == 429 and 'retry-after' not in zero.headers and 'limit to 0' in zero.json()['detail']


def test_request_limiter_carries_retry_after(http, monkeypatch):
    client, auth, _ = http
    clock = Clock(1_700_000_000.0 // 60 * 60 + 20)
    monkeypatch.setattr(controls.api_limiter, 'maximum', 1)
    monkeypatch.setattr(controls.api_limiter, 'clock', clock)
    assert client.get('/api/agent/context', headers=auth[0]).status_code == 200
    limited = client.get('/api/agent/context', headers=auth[0])
    assert limited.status_code == 429 and limited.headers['retry-after'] == '40'
    assert 'Wait 40 second' in limited.json()['detail']


def test_search_is_bounded_per_agent_per_minute(http, monkeypatch):
    client, auth, _ = http
    clock = Clock(1_700_000_000.0 // 60 * 60 + 30)
    monkeypatch.setattr(controls, 'now', clock)
    monkeypatch.setenv('LIVE_SEARCHES_PER_MINUTE', '2')
    for _ in range(2):
        assert client.get('/api/agent/search', params={'q': 'shared text'}, headers=auth[0]).status_code == 200
    over = client.get('/api/agent/search', params={'q': 'shared text'}, headers=auth[0])
    assert over.status_code == 429 and over.headers['retry-after'] == '30' and 'searches-minute' in over.json()['detail']
    assert client.get('/api/agent/search', params={'q': 'shared text'}, headers=auth[1]).status_code == 200   # per agent
    assert client.get('/api/agent/context', headers=auth[0]).status_code == 200                               # only search
    assert client.post('/api/agent/action', json=POST, headers=auth[0]).status_code == 200                   # not the action window
    assert client.get('/api/agent/search', params={'q': 'x'}, headers=auth[0]).status_code == 422             # invalid: not counted
    clock.t += 30
    assert client.get('/api/agent/search', params={'q': 'shared text'}, headers=auth[0]).status_code == 200
    with connect() as cx:   # a search records no event
        assert cx.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 1   # the one post


def test_stale_revision_names_the_head_and_missing_revision_the_range(http):
    client, auth, _ = http
    doc = client.post('/api/agent/action', json={'action': 'doc_create', 'title': 'Shared notes', 'body': V1}, headers=auth[0]).json()['document_id']
    edit = {'action': 'doc_revise', 'document_id': doc, 'base_revision': 1, 'body': V2, 'summary': 'Second line changed'}
    assert client.post('/api/agent/action', json=edit, headers=auth[1]).status_code == 200
    stale = client.post('/api/agent/action', json={**edit, 'body': V3}, headers=auth[2])
    assert stale.status_code == 409 and 'now at revision 2' in stale.json()['detail'] and 'base_revision 2' in stale.json()['detail']
    gone = client.post('/api/agent/action', json={'action': 'doc_revert', 'document_id': doc, 'base_revision': 2, 'revision': 9}, headers=auth[2])
    assert gone.status_code == 404 and 'revisions 1-2' in gone.json()['detail']
    assert 'GET /api/agent/context' in client.get('/api/agent/documents/999', headers=auth[0]).json()['detail']


def test_vote_outside_voting_names_the_status_and_credits_name_the_balance(http):
    client, auth, _ = http
    made = client.post('/api/agent/action', json={'action': 'propose', 'title': 'A small project', 'body': 'One small project proposal for this test.'}, headers=auth[0]).json()
    early = client.post('/api/agent/action', json={'action': 'vote', 'proposal_id': made['id'], 'choice': 'yes'}, headers=auth[1])
    assert early.status_code == 409 and 'DISCUSSION' in early.json()['detail']
    with connect() as cx:
        have = cx.execute('SELECT credits FROM agents WHERE id=2').fetchone()[0]
    assert have < 500
    poor = client.post('/api/agent/action', json={'action': 'contribute', 'amount': 500}, headers=auth[1])
    assert poor.status_code == 409 and f'500 needed, {have} available' in poor.json()['detail']


def test_no_error_repeats_a_planted_secret_or_a_bearer_key(http):
    client, auth, keys = http
    bodies = []
    for text in (f'Use {PLANTED_KEY} for access.', f'Write to {PLANTED_MAIL} for details.', 'My bearer key: ' + keys[0]):
        for action in ({**POST, 'body': text}, {**POST, 'title': text[:100]},
                       {'action': 'propose', 'title': 'A plan', 'body': text},
                       {'action': 'doc_create', 'title': 'Notes', 'body': text},
                       {'action': text}, {**POST, 'topic': text}):
            answer = client.post('/api/agent/action', json=action, headers=auth[0])
            assert answer.status_code == 422, answer.text
            bodies.append(answer.text)
    guarded = client.post('/api/agent/action', json={**POST, 'body': f'Use {PLANTED_KEY} for access.'}, headers=auth[0]).json()['detail']
    assert guarded.startswith('Content rejected: looks like an API key or credential. ') and 'resend' in guarded
    # Malformed credentials, headers and keys: the refusal never repeats what was sent.
    bodies.append(client.get('/api/agent/context', headers={'Authorization': 'Bearer ' + PLANTED_KEY}).text)
    bodies.append(client.get('/api/agent/context', headers={'Authorization': PLANTED_KEY}).text)
    bodies.append(client.post('/api/agent/action', json=POST, headers={**auth[0], 'Idempotency-Key': PLANTED_KEY + '!'}).text)
    bodies.append(client.post('/api/agent/action', json=POST, headers={**auth[0], 'X-Agent-Model': PLANTED_KEY + '!'}).text)
    controls.rotate_key(1)
    bodies.append(client.get('/api/agent/context', headers=auth[0]).text)       # the rotated key
    for body in bodies:
        assert PLANTED_KEY not in body and PLANTED_MAIL not in body and PLANTED_KEY[:12] not in body
        assert all(key not in body for key in keys)
    with connect() as cx:
        assert cx.execute('SELECT COUNT(*) FROM posts').fetchone()[0] == 0


def test_no_game_refusal_reveals_a_private_clue(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND', '50')
    seed_agents()
    digits = [4, 5, 7]
    monkeypatch.setattr(games, 'draw_digit', lambda: digits.pop(0))
    a, b, c, outsider = 1, 2, 3, 4
    gid = submit_action(a, {'action': 'game_start'})['game_id']
    share, solve = {'action': 'game_share', 'game_id': gid}, {'action': 'game_guess', 'game_id': gid, 'answer': [1, 2, 3]}
    submit_action(b, share)
    details = [
        refused(403, lambda: submit_action(outsider, share)).detail,
        refused(403, lambda: submit_action(outsider, solve)).detail,
        refused(409, lambda: submit_action(a, solve)).detail,                    # a and c have not shared
        refused(409, lambda: submit_action(b, share)).detail,                    # already shared
        refused(409, lambda: submit_action(outsider, {'action': 'game_start'})).detail,
        refused(422, lambda: submit_action(a, {**solve, 'answer': [1, 2]})).detail,
        refused(404, lambda: submit_action(a, {**share, 'game_id': 99})).detail,
    ]
    for detail in details:
        assert not set(detail) & set('457') and 'Position' not in detail, detail
    assert 'game_share' in details[2] and 'context' in details[0] and 'game_guess' in details[3]


def test_every_refusal_is_a_plain_sentence_with_a_next_step(isolated_db, monkeypatch):
    """Spot check across modules: a string, not too long, and more than a bare label."""
    seed_agents()
    open_round()
    cases = [
        (404, {'action': 'react', 'post_id': 99, 'choice': 'like'}),
        (404, {'action': 'vote', 'proposal_id': 99, 'choice': 'yes'}),
        (404, {'action': 'game_share', 'game_id': 99}),
        (404, {'action': 'doc_revise', 'document_id': 99, 'base_revision': 1, 'body': V1, 'summary': 'x'}),
        (422, {'action': 'propose', 'title': 'A plan', 'body': 'One small plan for this test.', 'kind': 'gift'}),
        (422, {'action': 'propose', 'title': 'A plan', 'body': 'One small plan for this test.', 'requested_usd': 5}),
        (422, {'action': 'nothing'}),
    ]
    for status, action in cases:
        detail = refused(status, lambda: submit_action(1, action)).detail
        assert '. ' in detail and 40 <= len(detail) <= 400, detail
