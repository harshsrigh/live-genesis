"""Per-round, per-agent action quotas. Offline; isolated disposable database."""
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from live_genesis import controls
from live_genesis.app import app
from live_genesis.core import agent_context, connect, init_db, overview, seed_agents, submit_action

IDLE = {'action': 'idle'}


def status_of(call):
    with pytest.raises(HTTPException) as err:
        call()
    return err.value.status_code, err.value.detail


def round_count(rid, agent_id):
    with connect() as cx:
        row = cx.execute('SELECT count FROM round_actions WHERE round_id=? AND agent_id=?', (rid, agent_id)).fetchone()
    return row[0] if row else 0


def test_no_round_means_only_window_quotas(isolated_db):
    seed_agents()
    submit_action(1, IDLE)
    submit_action(1, IDLE)
    assert agent_context(1)['quota'] == {'round_active': False, 'actions_per_round': 1, 'actions_used_this_round': 0}


def test_round_quota_is_per_agent_persistent_and_resets(isolated_db):
    seed_agents()
    first = controls.begin_round()
    submit_action(1, IDLE)
    init_db()  # restart must not forget the counter
    assert status_of(lambda: submit_action(1, IDLE)) == (429, 'actions-round quota exhausted')
    assert agent_context(1)['quota']['actions_used_this_round'] == 1
    submit_action(2, IDLE)  # another agent has its own allowance
    assert round_count(first, 1) == 1 and round_count(first, 2) == 1
    second = controls.begin_round()
    submit_action(1, IDLE)  # a new round restores the allowance
    assert round_count(second, 1) == 1 and round_count(first, 1) == 1


def test_agent_specific_override_and_audit(isolated_db):
    seed_agents()
    controls.set_limit(1, 'actions-round', 3)
    controls.begin_round()
    for _ in range(3):
        submit_action(1, IDLE)
    assert status_of(lambda: submit_action(1, IDLE))[0] == 429
    submit_action(2, IDLE)
    assert status_of(lambda: submit_action(2, IDLE))[0] == 429  # default still 1 for others
    controls.set_limit(3, 'actions-round', 0)  # zero blocks without revoking the key
    assert status_of(lambda: submit_action(3, IDLE))[0] == 429
    controls.set_limit(3, 'actions-round', None)
    submit_action(3, IDLE)
    with connect() as cx:
        audit = [r[0] for r in cx.execute("SELECT operation FROM operator_audit WHERE operation LIKE 'set-quota%' ORDER BY id")]
    assert audit == ['set-quota actions-round: 3', 'set-quota actions-round: 0', 'set-quota actions-round: default']


@pytest.mark.parametrize('agent_id,kind,maximum', [
    (999, 'actions-round', 1), (1, 'made-up', 1), (1, 'actions-round', -1),
    (1, 'actions-round', 10001), (1, 'actions-round', True), (1, 'actions-round', 1.5),
])
def test_invalid_overrides_are_refused(isolated_db, agent_id, kind, maximum):
    seed_agents()
    with pytest.raises(ValueError):
        controls.set_limit(agent_id, kind, maximum)
    with connect() as cx:
        assert cx.execute('SELECT COUNT(*) FROM agent_limits').fetchone()[0] == 0


def test_window_and_inference_overrides_are_agent_specific(isolated_db):
    seed_agents()
    controls.set_limit(1, 'actions-minute', 1)
    submit_action(1, IDLE)
    assert status_of(lambda: submit_action(1, IDLE)) == (429, 'actions-minute quota exhausted')
    submit_action(2, IDLE)
    submit_action(2, IDLE)
    controls.set_limit(1, 'inference-hour', 0)
    assert status_of(lambda: controls.reserve(1, 'test', 'test', 100, controls.begin_round()))[0] == 429
    assert controls.reserve(2, 'test', 'test', 100, controls.begin_round())
    with connect() as cx:  # the refused reservation left no hold and no round slot
        assert cx.execute('SELECT COUNT(*) FROM reservations WHERE agent_id=1').fetchone()[0] == 0
        assert cx.execute('SELECT COUNT(*) FROM round_slots WHERE agent_id=1').fetchone()[0] == 0


def test_rejected_and_replayed_actions_do_not_consume_round_quota(isolated_db):
    seed_agents()
    rid = controls.begin_round()
    assert status_of(lambda: submit_action(1, {'action': 'post', 'title': ''}))[0] == 422
    assert status_of(lambda: submit_action(1, {'action': 'transfer_money', 'amount': 5}))[0] == 422
    assert round_count(rid, 1) == 0
    first = submit_action(1, IDLE, request_id='retry-0001')
    assert submit_action(1, IDLE, request_id='retry-0001') == first  # identical retry, no 429
    assert round_count(rid, 1) == 1
    assert status_of(lambda: submit_action(1, IDLE, request_id='retry-0002'))[0] == 429
    assert len(overview()['events']) == 1


def test_runner_action_counts_against_the_receipt_round(isolated_db):
    seed_agents()
    reserved_in = controls.begin_round()
    receipt = controls.reserve(1, 'test', 'test', 100, reserved_in)
    controls.settle(receipt, 5, 5, 'offline-response')
    newer = controls.begin_round()
    assert submit_action(1, IDLE, receipt=receipt)['source'] == 'model_runner'
    assert round_count(reserved_in, 1) == 1 and round_count(newer, 1) == 0
    submit_action(1, IDLE)  # the newer round is still unused for this agent
    assert round_count(newer, 1) == 1


def test_round_quota_refusal_keeps_receipt_unused(isolated_db):
    seed_agents()
    rid = controls.begin_round()
    receipt = controls.reserve(1, 'test', 'test', 100, rid)
    controls.settle(receipt, 5, 5, 'offline-response')
    submit_action(1, IDLE)  # the agent spends its round allowance through the API first
    assert status_of(lambda: submit_action(1, IDLE, receipt=receipt))[0] == 429
    with connect() as cx:  # rollback: the receipt was not consumed by the refused action
        assert cx.execute('SELECT used FROM reservations WHERE id=?', (receipt,)).fetchone()[0] == 0


def test_expired_round_and_simulation_do_not_count(isolated_db):
    seed_agents()
    rid = controls.begin_round()
    submit_action(1, IDLE, demo=True)
    submit_action(1, IDLE, demo=True)
    assert round_count(rid, 1) == 0
    with connect() as cx:
        cx.execute('UPDATE rounds SET created=? WHERE id=?', (time.time() - controls.ROUND_SECONDS - 5, rid))
    submit_action(1, IDLE)
    submit_action(1, IDLE)
    assert round_count(rid, 1) == 0


def test_migration_from_0_2_adds_round_tracking(isolated_db):
    seed_agents()
    with connect() as cx:
        cx.execute('DROP TABLE round_actions')
        cx.execute('DROP TABLE agent_limits')
        cx.execute('ALTER TABLE reservations DROP COLUMN round_id')
        cx.execute('PRAGMA user_version=2')
    init_db()
    init_db()
    with connect() as cx:
        assert cx.execute('PRAGMA user_version').fetchone()[0] == 3
    controls.begin_round()
    submit_action(1, IDLE)
    assert status_of(lambda: submit_action(1, IDLE))[0] == 429


def test_http_quota_follows_the_authenticated_agent_only(isolated_db):
    keys = [item['key'] for item in seed_agents()]
    rid = controls.begin_round()

    def headers(key, n):
        return {'Authorization': 'Bearer ' + key, 'Idempotency-Key': f'round-quota-{n}'}

    with TestClient(app) as c:
        # No key, a made-up key: refused, and nobody's allowance is spent.
        assert c.post('/api/agent/action', json=IDLE).status_code == 401
        assert c.post('/api/agent/action', json=IDLE, headers=headers('live_' + 'x' * 43, 0)).status_code == 401
        with connect() as cx:
            assert cx.execute('SELECT COUNT(*) FROM round_actions').fetchone()[0] == 0
        # A caller cannot name another agent or pick a round in the request.
        for extra in ({'agent_id': 2}, {'agent': 'Moss'}, {'round_id': 'other'}, {'receipt': 1}):
            assert c.post('/api/agent/action', json={**IDLE, **extra}, headers=headers(keys[0], 1)).status_code == 422
        assert round_count(rid, 1) == 0
        assert c.post('/api/agent/action', json=IDLE, headers=headers(keys[0], 2)).status_code == 200
        assert c.post('/api/agent/action', json=IDLE, headers=headers(keys[0], 3)).status_code == 429
        assert round_count(rid, 1) == 1 and round_count(rid, 2) == 0
        # Agent 2's own key still has agent 2's allowance.
        assert c.post('/api/agent/action', json=IDLE, headers=headers(keys[1], 4)).status_code == 200
        quota = c.get('/api/agent/context', headers=headers(keys[0], 5)).json()['quota']
        assert quota == {'round_active': True, 'actions_per_round': 1, 'actions_used_this_round': 1}
        public = c.get('/api/overview').text
        assert rid not in public
