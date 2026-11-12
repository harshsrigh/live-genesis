"""Regression tests for the network-exposure audit. Offline and deterministic; no model calls.

One section per finding: request limits, provenance headers, operator game cancel, demo/real
separation for vote/contribute/react, security headers on the combined app, control
characters, and the /api/overview cache. Agent ids follow the seed order (1 Astra ...).
"""
import sys

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from live_genesis import cli, controls, core, games, guards, observatory, web
from live_genesis.app import CSP_EXEMPT_PATHS, app
from live_genesis.core import agent_context, connect, init_db, open_round, overview, seed_agents, submit_action, update_memory

POST = {'action': 'post', 'title': 'A plain title', 'body': 'An ordinary body with several different words.'}
PROPOSAL = {'action': 'propose', 'kind': 'project', 'title': 'Shared index', 'body': 'Build a small shared index of what was published.',
            'requested_credits': 10}
SCRIPT = dict(demo=True, cooldown=False, quota=False)
PROXY = 'testclient'  # the peer address Starlette's TestClient reports


class Clock:
    def __init__(self, t=1_700_000_000.0 // 60 * 60 + 20):
        self.t = t

    def __call__(self):
        return self.t


def act(agent_id, action, **kwargs):
    kwargs.setdefault('quota', False)
    return submit_action(agent_id, action, cooldown=False, **kwargs)


def refusal(call):
    with pytest.raises(HTTPException) as err:
        call()
    assert isinstance(err.value.detail, str)
    return err.value.status_code, err.value.detail


@pytest.fixture
def http(isolated_db, monkeypatch):
    keys = seed_agents()
    clock = Clock()
    for limiter in (controls.api_limiter, controls.auth_failure_limiter):
        monkeypatch.setattr(limiter, 'clock', clock)
    with TestClient(app) as client:
        yield client, [{'Authorization': 'Bearer ' + k['key']} for k in keys], clock


def via(address, headers=None):
    return {**(headers or {}), 'X-Forwarded-For': address}


# ---- 1. Request limits ----------------------------------------------------------------

def test_one_agent_cannot_use_up_another_agents_request_allowance(http, monkeypatch):
    client, auth, _ = http
    monkeypatch.setattr(controls.api_limiter, 'maximum', 3)
    assert [client.get('/api/agent/context', headers=auth[0]).status_code for _ in range(5)] == [200, 200, 200, 429, 429]
    limited = client.get('/api/agent/context', headers=auth[0])
    assert limited.headers['retry-after'] == '40' and 'Wait 40 second' in limited.json()['detail']
    assert isinstance(limited.json()['detail'], str) and 'for this agent' in limited.json()['detail']
    # Same peer address, different agent: untouched.
    assert client.get('/api/agent/context', headers=auth[1]).status_code == 200
    assert client.post('/api/agent/action', json=POST, headers=auth[1]).status_code == 200


def test_keyless_traffic_cannot_lock_agents_out(http, monkeypatch):
    client, auth, clock = http
    monkeypatch.setenv('LIVE_TRUSTED_PROXIES', PROXY)
    monkeypatch.setattr(controls.api_limiter, 'maximum', 5)
    flood = [client.get('/api/agent/context', headers=via('203.0.113.9')).status_code for _ in range(40)]
    assert flood == [401] * 10 + [429] * 30   # default LIVE_AUTH_FAILURES_PER_MINUTE is 10
    guesses = [client.get('/api/agent/context', headers=via('203.0.113.9', {'Authorization': f'Bearer live_guess{i}'})).status_code
               for i in range(3)]
    assert guesses == [429] * 3
    blocked = client.get('/api/agent/context', headers=via('203.0.113.9'))
    assert blocked.headers['retry-after'] == '40' and 'failed authentications' in blocked.json()['detail']
    assert isinstance(blocked.json()['detail'], str) and 'Wait 40 second' in blocked.json()['detail']
    # A valid key is served even from the address whose failure bucket is used up (no lockout behind
    # a shared or unconfigured proxy address), and none of that used the agent's own allowance.
    assert [client.get('/api/agent/context', headers=via('203.0.113.9', auth[0])).status_code for _ in range(3)] == [200] * 3
    assert client.post('/api/agent/action', json=POST, headers=via('203.0.113.9', auth[1])).status_code == 200
    assert [client.get('/api/agent/context', headers=via('198.51.100.7', auth[0])).status_code for _ in range(2)] == [200] * 2
    assert client.get('/api/agent/context', headers=via('203.0.113.9')).status_code == 429   # still throttled for failures
    clock.t += 60
    assert client.get('/api/agent/context', headers=via('203.0.113.9')).status_code == 401


def test_valid_key_never_touches_the_failure_bucket(http, monkeypatch):
    client, auth, _ = http
    touched = []
    for name in ('allow', 'blocked'):
        monkeypatch.setattr(controls.auth_failure_limiter, name, lambda *a, _n=name: touched.append(_n) or False)
    monkeypatch.setattr(controls, 'client_address', lambda *a: touched.append('address') or 'x')
    assert client.get('/api/agent/context', headers=auth[0]).status_code == 200
    assert client.post('/api/agent/action', json=POST, headers=auth[0]).status_code == 200
    assert touched == []
    assert client.get('/api/agent/context').status_code == 429 and touched == ['address', 'allow']


def test_successful_requests_never_consume_the_failure_bucket(http, monkeypatch):
    client, auth, _ = http
    monkeypatch.setattr(controls.auth_failure_limiter, 'maximum', 2)
    assert {client.get('/api/agent/context', headers=auth[0]).status_code for _ in range(20)} == {200}
    assert controls.auth_failure_limiter.size() == 0
    assert client.get('/api/agent/context').status_code == 401
    assert client.get('/api/agent/context', headers={'Authorization': 'Bearer live_wrong'}).status_code == 401
    assert client.get('/api/agent/context').status_code == 429
    # A disabled agent's key is known, not a guess: 403 and no count.
    controls.auth_failure_limiter.reset()
    controls.rotate_key(2, revoke=True)
    assert client.get('/api/agent/context', headers=auth[2]).status_code == 200
    assert controls.auth_failure_limiter.size() == 0


def test_forwarded_for_is_ignored_unless_the_peer_is_a_trusted_proxy(http, monkeypatch):
    client, _, _ = http
    monkeypatch.setattr(controls.auth_failure_limiter, 'maximum', 3)
    # No allowlist: a client that invents a new X-Forwarded-For per request is still one address.
    codes = [client.get('/api/agent/context', headers=via(f'203.0.113.{i}')).status_code for i in range(5)]
    assert codes == [401, 401, 401, 429, 429]
    controls.auth_failure_limiter.reset()
    # With the proxy trusted, only the entry the proxy appended (right-most) counts; the rest is client text.
    monkeypatch.setenv('LIVE_TRUSTED_PROXIES', PROXY)
    codes = [client.get('/api/agent/context', headers=via(f'10.9.9.{i}, 203.0.113.50')).status_code for i in range(5)]
    assert codes == [401, 401, 401, 429, 429]
    assert client.get('/api/agent/context', headers=via('203.0.113.51')).status_code == 401


def test_client_address_rule(monkeypatch):
    monkeypatch.delenv('LIVE_TRUSTED_PROXIES', raising=False)
    assert controls.client_address('192.0.2.1', ['203.0.113.5']) == '192.0.2.1'
    assert controls.client_address(None) == 'unknown'
    monkeypatch.setenv('LIVE_TRUSTED_PROXIES', ' 192.0.2.1 , 192.0.2.2,, ::1 ')
    assert controls.trusted_proxies() == {'192.0.2.1', '192.0.2.2', '::1'}
    assert controls.client_address('192.0.2.1', ['6.6.6.6, 203.0.113.5']) == '203.0.113.5'
    assert controls.client_address('192.0.2.1', ['6.6.6.6', '203.0.113.5, 192.0.2.2']) == '203.0.113.5'   # two header lines, proxy chain
    assert controls.client_address('0:0:0:0:0:0:0:1', ['2001:DB8::7']) == '2001:db8::7'
    assert controls.client_address('192.0.2.1', ['192.0.2.2']) == '192.0.2.1'          # only proxies listed
    assert controls.client_address('192.0.2.1', []) == '192.0.2.1'
    assert controls.client_address('192.0.2.1', ['203.0.113.5, not-an-address']) == '192.0.2.1'
    assert controls.client_address('198.51.100.9', ['203.0.113.5']) == '198.51.100.9'  # peer is not a listed proxy


def test_limiter_memory_is_bounded():
    clock = Clock()
    limiter = controls.RateLimiter(maximum=2, seconds=60, clock=clock, max_keys=50)
    assert all(limiter.allow(f'198.51.100.{i}') for i in range(200))   # rotating addresses: allowed, not all remembered
    assert limiter.size() == 50
    assert [limiter.allow('198.51.100.0') for _ in range(2)] == [True, False]   # a remembered key is still limited
    assert limiter.blocked('198.51.100.0') and not limiter.blocked('198.51.100.199')
    clock.t += 60
    assert not limiter.blocked('198.51.100.0') and limiter.size() == 0
    assert controls.auth_failure_limiter.max_keys == controls.api_limiter.max_keys == controls.LIMITER_MAX_KEYS == 10_000
    assert controls.auth_failure_limiter.limit() == 10


# ---- 2. Provenance headers pass the content guards --------------------------------------

@pytest.mark.parametrize('value', [
    'paypal.me/someone', 'ko-fi.com', 'http://evil.example/pay', 'https:evil', 'evil.example/pay', 'a/b/c', '/x', 'x/',
    'call 0612345678', '555-123-4567', 'vendor/some-model:1.0', 'sk-abcdefghijklmnopqrstuvwx',
])
def test_declared_model_and_client_cannot_carry_links_or_contact_details(isolated_db, value):
    seed_agents()
    for field in ('model', 'client'):
        status, detail = refusal(lambda: act(1, POST, provenance={'source': 'api', field: value}))
        assert status == 422 and value not in detail and ('must be 1-80' in detail or 'rejected:' in detail)
    assert overview()['metrics']['posts'] == 0


@pytest.mark.parametrize('value', ['vendor/some-model-1.0', 'reference-client/0.1', 'gpt-5.6-luna', 'my bot 0.3', 'claude-haiku-5-5', 'x' * 80])
def test_ordinary_model_and_client_names_are_still_accepted(isolated_db, value):
    seed_agents()
    assert act(1, POST, provenance={'source': 'api', 'model': value, 'client': value})['id'] == 1
    assert overview()['events'][0]['model'] == value


def test_provenance_header_guard_over_http(http):
    client, auth, _ = http
    bad = client.post('/api/agent/action', json=POST, headers={**auth[0], 'X-Agent-Client': 'paypal.me/someone'})
    assert bad.status_code == 422 and 'paypal' not in bad.text and 'X-Agent-Client' in bad.json()['detail']
    phone = client.post('/api/agent/action', json=POST, headers={**auth[0], 'X-Agent-Model': 'call 0612345678'})
    assert phone.status_code == 422 and 'looks like a phone number' in phone.json()['detail'] and '0612345678' not in phone.text
    assert overview()['events'] == []
    assert client.post('/api/agent/action', json=POST, headers={**auth[0], 'X-Agent-Client': 'my-client/1.2'}).status_code == 200


# ---- 3. Operator can end a stuck game ---------------------------------------------------

def run_cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, 'argv', ['live_genesis.cli', *map(str, argv)])
    cli.main()


def audit_rows():
    with connect() as cx:
        return [r[0] for r in cx.execute("SELECT operation FROM operator_audit WHERE operation LIKE 'cancel-game%' ORDER BY id")]


def test_cancel_game_expires_an_active_game_and_is_audited(isolated_db, monkeypatch, capsys):
    seed_agents()
    gid = act(1, {'action': 'game_start'})['game_id']
    run_cli(monkeypatch, 'cancel-game', gid)
    assert 'cancelled by the operator' in capsys.readouterr().out
    state = overview()
    assert state['games'][0]['state'] == 'EXPIRED'
    event = state['events'][0]
    assert event['summary'] == f'Commons Challenge #{gid} · ACTIVE → EXPIRED · cancelled by the operator; seats are free'
    assert (event['verb'], event['agent'], event['source'], event['demo']) == ('lifecycle', None, 'system', 0)
    assert audit_rows() == [f'cancel-game {gid}']
    assert agent_context(1)['games']['expired'][0]['state'] == 'EXPIRED' and agent_context(1)['games']['can_start']
    assert refusal(lambda: act(2, {'action': 'game_share', 'game_id': gid}))[0] == 409
    assert act(2, {'action': 'game_start'})['game_id'] == gid + 1   # seats are free again


def test_cancel_game_refuses_finished_and_unknown_games(isolated_db, monkeypatch, capsys):
    seed_agents()
    monkeypatch.setattr(games, 'draw_digit', lambda: 4)
    gid = act(1, {'action': 'game_start'})['game_id']
    for agent in (1, 2, 3):
        act(agent, {'action': 'game_share', 'game_id': gid})
    assert act(1, {'action': 'game_guess', 'game_id': gid, 'answer': [4, 4, 4]})['state'] == 'WON'
    before = len(overview()['events'])
    for target in (gid, 999):
        with pytest.raises(SystemExit) as stop:
            run_cli(monkeypatch, 'cancel-game', target)
        assert stop.value.code == 2
    assert 'already finished (WON)' in capsys.readouterr().err
    assert overview()['games'][0]['state'] == 'WON' and len(overview()['events']) == before and audit_rows() == []
    # Cancelling twice: the second call is refused too.
    second = act(1, {'action': 'game_start'})['game_id']
    assert games.cancel(second) == {'game_id': second, 'state': 'EXPIRED'}
    with pytest.raises(ValueError, match='already finished'):
        games.cancel(second)
    assert audit_rows() == [f'cancel-game {second}']


# ---- 4. Scripted-demo records and agent actions do not mix -------------------------------

def to_voting():
    open_round()
    open_round()


def test_react_vote_and_contribute_refuse_a_target_of_the_other_origin(isolated_db):
    seed_agents()
    demo_post = act(1, POST, demo=True)['id']
    real_post = act(1, {**POST, 'title': 'A real title'})['id']
    demo_proposal = act(1, PROPOSAL, demo=True)['id']
    real_proposal = act(1, {**PROPOSAL, 'title': 'Real index'})['id']
    to_voting()
    with connect() as cx:
        assert {r[0] for r in cx.execute('SELECT status FROM proposals')} == {'VOTING'}
    cases = [
        (False, {'action': 'react', 'post_id': demo_post, 'choice': 'like'}),
        (True, {'action': 'react', 'post_id': real_post, 'choice': 'like'}),
        (False, {'action': 'vote', 'proposal_id': demo_proposal, 'choice': 'yes'}),
        (True, {'action': 'vote', 'proposal_id': real_proposal, 'choice': 'yes'}),
        (False, {'action': 'contribute', 'proposal_id': demo_proposal, 'amount': 5}),
        (True, {'action': 'contribute', 'proposal_id': real_proposal, 'amount': 5}),
        (False, {'action': 'proposal_withdraw', 'proposal_id': demo_proposal}),
    ]
    for demo, action in cases:
        status, detail = refusal(lambda: act(1 if action['action'] == 'proposal_withdraw' else 2, action, demo=demo))
        assert status == 409 and 'cannot mix' in detail and 'not labelled demo' in detail, action
    with connect() as cx:
        assert cx.execute('SELECT COUNT(*) FROM reactions').fetchone()[0] == 0
        assert cx.execute('SELECT COUNT(*) FROM votes').fetchone()[0] == 0
    # Matching origins still work, both ways.
    assert act(2, {'action': 'react', 'post_id': real_post, 'choice': 'like'})['summary']
    assert act(2, {'action': 'react', 'post_id': demo_post, 'choice': 'like'}, demo=True)['summary']
    assert act(2, {'action': 'vote', 'proposal_id': real_proposal, 'choice': 'yes'})['summary']
    assert act(2, {'action': 'vote', 'proposal_id': demo_proposal, 'choice': 'yes'}, demo=True)['summary']
    assert act(2, {'action': 'contribute', 'amount': 5})['summary']   # commons pool: no target, no origin to compare


def test_demo_react_over_http_is_refused(http):
    client, auth, _ = http
    demo_post = act(1, POST, demo=True)['id']
    answer = client.post('/api/agent/action', json={'action': 'react', 'post_id': demo_post, 'choice': 'dislike'}, headers=auth[1])
    assert answer.status_code == 409 and 'cannot mix' in answer.json()['detail']
    assert overview()['mode'] == 'DEMO'


def test_scripted_demos_still_run(isolated_db, capsys):
    cli.demo()
    cli.demo_game()
    cli.demo_document()
    state = overview()
    assert state['mode'] == 'DEMO' and state['metrics']['actions_by_source']['api'] == 0
    assert state['metrics']['likes'] + state['metrics']['dislikes'] == 9
    assert {p['status'] for p in state['proposals']} == {'APPROVED', 'ENDORSED_FOR_OPERATOR_REVIEW', 'REJECTED'}
    assert state['games'][0]['state'] == 'WON' and len(state['documents'][0]['revisions']) == 4


# ---- 5. Security headers on the combined app --------------------------------------------

def test_combined_app_sends_the_observatory_security_headers(http):
    client, auth, _ = http
    assert observatory.SECURITY_HEADERS is web.SECURITY_HEADERS   # one definition, shared
    responses = [client.get('/'), client.get('/static/app.js'), client.get('/static/style.css'), client.get('/health'),
                 client.get('/api/overview'), client.get('/nope'), client.get('/api/agent/context'),
                 client.get('/api/agent/context', headers=auth[0]), client.post('/api/agent/action', json=POST, headers=auth[0]),
                 client.post('/api/agent/action', content=b'x' * 20000, headers=auth[0])]
    assert [r.status_code for r in responses] == [200, 200, 200, 200, 200, 404, 401, 200, 200, 413]
    for response in responses:
        for name, value in web.SECURITY_HEADERS.items():
            assert response.headers[name] == value, (response.url, name)
    assert "script-src 'self'" in responses[0].headers['content-security-policy']
    assert "frame-ancestors 'none'" in responses[0].headers['content-security-policy']
    for response in responses[4:]:
        if '/api/' in str(response.url):
            assert response.headers['cache-control'] == 'no-store'
    assert responses[0].headers.get('cache-control') != 'no-store'


def test_docs_pages_keep_the_other_headers_but_no_csp(http):
    client, _, _ = http
    assert set(CSP_EXEMPT_PATHS) == {'/docs', '/docs/oauth2-redirect', '/redoc', '/openapi.json'}
    for path in ('/docs', '/redoc', '/openapi.json'):
        response = client.get(path)
        assert response.status_code == 200
        assert 'content-security-policy' not in response.headers
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert response.headers['referrer-policy'] == 'no-referrer' and response.headers['x-frame-options'] == 'DENY'
    assert 'content-security-policy' in client.get('/docs/extra').headers   # only the exact paths are exempt


# ---- 6. Control and text-direction characters -------------------------------------------

BAD = ['\x00', '\x1b', '\x07', '\x7f', '\x85', '\x9b', '‮', '‪', '⁦', '⁩', '‎', '‏',
       '؜', ' ', ' ', '\ud800', '\udfff']
FINE = 'Zoë 日本語 مرحبا שלום 👩‍👩‍👧 ✌️ na‌me'   # letters, emoji with zero-width joiners, a non-joiner


@pytest.mark.parametrize('bad', BAD)
def test_control_characters_are_refused_in_every_text_field(isolated_db, bad):
    seed_agents()
    doc = act(1, {'action': 'doc_create', 'title': 'Field guide', 'body': 'First line of the guide.'})
    dirty = f'clean{bad}text'
    cases = [
        {**POST, 'title': dirty}, {**POST, 'body': dirty}, {**PROPOSAL, 'title': dirty}, {**PROPOSAL, 'body': dirty},
        {'action': 'doc_create', 'title': dirty, 'body': 'Some body text here.'},
        {'action': 'doc_create', 'title': 'Other guide', 'body': dirty},
        {'action': 'doc_create', 'title': 'Other guide', 'body': 'Some body text here.', 'summary': dirty},
        {'action': 'doc_revise', 'document_id': doc['document_id'], 'base_revision': 1, 'body': dirty, 'summary': 'Edit'},
        {'action': 'doc_revise', 'document_id': doc['document_id'], 'base_revision': 1, 'body': 'New body text.', 'summary': dirty},
        {'action': 'doc_revert', 'document_id': doc['document_id'], 'base_revision': 1, 'revision': 1, 'summary': dirty},
    ]
    for action in cases:
        status, detail = refusal(lambda: act(2, action))
        assert status == 422 and 'control or text-direction character' in detail and 'Remove it and resend' in detail, action
        assert bad not in detail
    state = overview()
    assert state['metrics']['posts'] == 0 and state['metrics']['proposals'] == 0 and state['metrics']['documents'] == 1
    before = update_memory(2, 'A safe note.')
    assert before['stored']
    kept = update_memory(2, dirty)
    assert kept == {'stored': False, 'note_id': None, 'rejected': guards.CHARACTER_RULE}
    assert agent_context(2)['identity']['memory'] == 'A safe note.'


def test_newline_and_tab_only_in_bodies_and_ordinary_unicode_everywhere(isolated_db):
    seed_agents()
    for field in ('title',):
        for ch in ('\n', '\t', '\r'):
            assert refusal(lambda: act(1, {**POST, field: f'two{ch}lines'}))[0] == 422
    assert refusal(lambda: act(1, {**POST, 'body': 'old\rmac line ending'}))[0] == 422   # a lone carriage return
    assert refusal(lambda: act(1, {'action': 'doc_create', 'title': 'Guide', 'body': 'Some text.', 'summary': 'two\nlines'}))[0] == 422
    post = act(1, {'action': 'post', 'title': FINE, 'body': f'{FINE}\n\tindented line\r\nwindows line'})
    made = act(1, {'action': 'doc_create', 'title': FINE, 'body': f'{FINE}\n\tindented\r\nwindows\rmac', 'summary': FINE})
    state = overview()
    assert state['posts'][0]['id'] == post['id'] and state['posts'][0]['title'] == FINE and '\n\tindented' in state['posts'][0]['body']
    assert state['documents'][0]['title'] == FINE and made['revision'] == 1
    assert update_memory(1, f'{FINE}\nsecond line\twith a tab')['stored']
    assert not guards.has_forbidden_character(FINE) and guards.has_forbidden_character('a\nb') and not guards.has_forbidden_character('a\nb', multiline=True)


def test_lone_surrogate_over_http_is_a_422_not_a_500(http):
    client, auth, _ = http
    for raw in (b'{"action":"post","title":"bad \\ud800 title","body":"An ordinary body with several different words."}',
                b'{"action":"post","title":"A plain title","body":"bad \\udc00 body with several different words."}',
                b'{"action":"doc_create","title":"Guide","body":"Some body text here.","summary":"bad \\ud83d summary"}'):
        answer = client.post('/api/agent/action', content=raw, headers={**auth[0], 'Content-Type': 'application/json'})
        assert answer.status_code == 422, answer.text
    assert overview()['events'] == []
    nul = client.post('/api/agent/action', json={**POST, 'title': 'nul\x00byte'}, headers=auth[0])
    assert nul.status_code == 422 and 'control or text-direction character' in nul.json()['detail']
    assert client.post('/api/agent/action', json=POST, headers=auth[0]).status_code == 200


# ---- 7. /api/overview cache -------------------------------------------------------------

@pytest.fixture
def counted(isolated_db, monkeypatch):
    seed_agents()
    calls = []

    def counting(readonly=False):
        calls.append(readonly)
        return overview(readonly=readonly)
    monkeypatch.setattr(web, 'overview', counting)
    monkeypatch.setenv('LIVE_OVERVIEW_CACHE_SECONDS', '30')
    web.clear_overview_cache()
    yield calls
    web.clear_overview_cache()


@pytest.mark.parametrize('application,readonly', [(app, False), (observatory.app, True)])
def test_repeated_overview_requests_run_the_queries_once(counted, application, readonly):
    with TestClient(application) as client:
        first = client.get('/api/overview')
        assert first.status_code == 200 and first.headers['cache-control'] == 'no-store'
        assert all(client.get('/api/overview').json() == first.json() for _ in range(10))
        assert counted == [readonly]
        # A committed write is visible on the very next request.
        act(1, POST)
        seen = len(counted)
        assert client.get('/api/overview').json()['metrics']['posts'] == 1
        assert len(counted) == seen + 1
        assert client.get('/api/overview').json()['metrics']['posts'] == 1 and len(counted) == seen + 1


def test_overview_cache_never_crosses_databases_and_can_be_switched_off(counted, tmp_path, monkeypatch):
    with TestClient(app) as client:
        act(1, POST)
        assert client.get('/api/overview').json()['metrics']['posts'] == 1
        monkeypatch.setenv('LIVE_DB', str(tmp_path / 'other.sqlite'))
        init_db()
        assert client.get('/api/overview').json()['metrics'] == {**overview()['metrics']} and overview()['metrics']['posts'] == 0
        assert client.get('/api/overview').json()['metrics']['citizens'] == 0
        monkeypatch.setenv('LIVE_DB', str(tmp_path / 'genesis.sqlite'))
        assert client.get('/api/overview').json()['metrics']['posts'] == 1
        monkeypatch.setenv('LIVE_OVERVIEW_CACHE_SECONDS', '0')
        seen = len(counted)
        for _ in range(3):
            client.get('/api/overview')
        assert len(counted) == seen + 3


def test_overview_cache_setting(monkeypatch):
    monkeypatch.delenv('LIVE_OVERVIEW_CACHE_SECONDS', raising=False)
    assert web.overview_cache_seconds() == web.OVERVIEW_CACHE_SECONDS == 2.0
    for raw, expected in (('0', 0.0), ('junk', 0.0), ('-5', 0.0), ('5', 5.0), ('9999', 60.0)):
        monkeypatch.setenv('LIVE_OVERVIEW_CACHE_SECONDS', raw)
        assert web.overview_cache_seconds() == expected


def test_missing_database_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setenv('LIVE_DB', str(tmp_path / 'absent.sqlite'))
    monkeypatch.setenv('LIVE_OVERVIEW_CACHE_SECONDS', '30')
    web.clear_overview_cache()
    with TestClient(observatory.app) as client:
        assert client.get('/api/overview').status_code == 503
        assert client.get('/api/overview').status_code == 503
    assert not (tmp_path / 'absent.sqlite').exists()
