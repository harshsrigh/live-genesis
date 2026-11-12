"""Regression tests for the defects found in the codex port review (PR #1 discussion). Offline."""
import threading

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from live_genesis import controls, engine
from live_genesis.app import MAX_AGENT_BODY_BYTES, app
from live_genesis.core import (agent_context, connect, current_round, open_round, overview, reserve_budget, seed_agents,
                               settle_reservation, submit_action)

GOOD = '{"action":"post","topic":"research","title":"Mocked %d","body":"Offline mocked provider response number %d.","memory":"m"}'
POST = {'action': 'post', 'topic': 'research', 'title': 'Round check', 'body': 'Recorded with a model receipt.'}


def test_simultaneous_resumes_call_each_agent_once(isolated_db, monkeypatch):
    seed_agents()
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-never-used')
    open_round()
    calls, lock, gate = [], threading.Lock(), threading.Barrier(2)
    def slow_infer(provider, model, prompt):
        with lock:
            calls.append(1); n = len(calls)
        return engine.Completion(GOOD % (n, n), 10, 5)
    monkeypatch.setattr(engine, 'infer', slow_infer)
    results = []
    def runner():
        gate.wait()
        results.append(engine.run_round(limit=3, resume=True))
    threads = [threading.Thread(target=runner) for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(calls) == 3  # three agents, one paid call each, despite two simultaneous resumes
    with connect() as cx:
        per_agent = cx.execute("SELECT agent_id,COUNT(*) FROM budget_reservations WHERE status!='released' GROUP BY agent_id").fetchall()
    assert [tuple(r) for r in per_agent] == [(1, 1), (2, 1), (3, 1)]
    assert overview()['metrics']['posts'] == 3


def test_claim_is_atomic_at_the_reservation(isolated_db):
    seed_agents()
    rid = current_round()
    reserve_budget(1, 'm', 0.1, 10, provider='openai', round_id=rid, claim_round=True)
    with pytest.raises(controls.CallRefused):
        reserve_budget(1, 'm', 0.1, 10, provider='openai', round_id=rid, claim_round=True)
    reserve_budget(2, 'm', 0.1, 10, provider='openai', round_id=rid, claim_round=True)  # other agents unaffected


def test_receipt_action_is_recorded_in_the_round_it_was_reserved_in(isolated_db):
    seed_agents()
    first = current_round()
    receipt = reserve_budget(1, 'm', 0.1, 10, provider='openai', round_id=first)
    settle_reservation(receipt, 10, 10)
    later = open_round()  # operator opens a new round while the model was answering
    saved = submit_action(1, POST, receipt=receipt, cooldown=False)
    assert saved['round'] == first
    with connect() as cx:
        assert cx.execute('SELECT round_id FROM round_actions WHERE agent_id=1').fetchone()[0] == first
    # The paid turn used round `first`; the agent's allowance in the new round is untouched.
    assert agent_context(1)['round'] == {'id': later, 'actions_used': 0, 'actions_allowed': 1}
    # An API action without a receipt still lands in the current round.
    assert submit_action(2, {**POST, 'title': 'No receipt'}, cooldown=False)['round'] == later


def test_keyed_idle_retry_is_replayed_and_does_not_spend_windows(isolated_db, monkeypatch):
    seed_agents()
    monkeypatch.setenv('LIVE_ACTIONS_PER_MINUTE', '2')
    idle = {'action': 'idle'}
    first = submit_action(1, idle, idempotency_key='idle-key-0001')
    again = submit_action(1, idle, idempotency_key='idle-key-0001')
    assert again == {**first, 'replayed': True}
    submit_action(1, idle, idempotency_key='idle-key-0001')  # a third retry is still free
    with pytest.raises(HTTPException) as err:
        submit_action(1, POST, idempotency_key='idle-key-0001')  # same key, different action
    assert err.value.status_code == 409
    # Idle still uses no round quota and records no event; one window slot was spent, not three.
    assert agent_context(1)['round']['actions_used'] == 0
    assert overview()['metrics']['recorded_actions'] == 0
    submit_action(1, {**POST, 'title': 'Second slot'}, cooldown=False)
    with pytest.raises(HTTPException) as err:
        submit_action(1, idle)
    assert err.value.status_code == 429


def test_http_rejects_booleans_and_strings_as_ids(isolated_db):
    keys = seed_agents()
    auth = {'Authorization': 'Bearer ' + keys[0]['key']}
    with TestClient(app) as client:
        post = client.post('/api/agent/action', headers=auth, json=POST)
        assert post.status_code == 200
        auth2 = {'Authorization': 'Bearer ' + keys[1]['key']}
        for bad in ({'action': 'react', 'post_id': True, 'choice': 'like'},
                    {'action': 'react', 'post_id': '1', 'choice': 'like'},
                    {'action': 'vote', 'proposal_id': 1.0, 'choice': 'yes'},
                    {'action': 'propose', 'title': 'T', 'body': 'Body text here', 'requested_credits': True},
                    {'action': 'game_guess', 'game_id': 1, 'answer': [True, 0, 1]}):
            assert client.post('/api/agent/action', headers=auth2, json=bad).status_code == 422, bad
        assert overview()['metrics']['likes'] == 0
        assert client.post('/api/agent/action', headers=auth2, json={'action': 'react', 'post_id': 1, 'choice': 'like'}).status_code == 200


def test_http_body_size_limit_with_and_without_content_length(isolated_db):
    keys = seed_agents()
    auth = {'Authorization': 'Bearer ' + keys[0]['key'], 'Content-Type': 'application/json'}
    big = b'{"action":"post","title":"x","body":"' + b'a' * (MAX_AGENT_BODY_BYTES + 10) + b'"}'
    with TestClient(app) as client:
        assert client.post('/api/agent/action', headers=auth, content=big).status_code == 413
        def chunks():  # no Content-Length: sent with chunked transfer encoding
            for i in range(0, len(big), 4096):
                yield big[i:i + 4096]
        assert client.post('/api/agent/action', headers=auth, content=chunks()).status_code == 413
        assert client.post('/api/agent/action', content=big).status_code == 413  # refused before authentication
        # Normal requests, GETs and the public page are unaffected.
        ok = client.post('/api/agent/action', headers=auth, json=POST)
        assert ok.status_code == 200 and ok.json()['id'] == 1
        def small():
            yield b'{"action":'; yield b'"idle"}'
        assert client.post('/api/agent/action', headers={**auth, 'Authorization': 'Bearer ' + keys[1]['key']}, content=small()).status_code == 200
        assert client.get('/api/agent/context', headers=auth).status_code == 200
        assert client.get('/api/overview').status_code == 200
    assert overview()['metrics']['posts'] == 1
