import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from live_genesis import controls, engine
from live_genesis.app import app
from live_genesis.core import authenticate, connect, init_db, overview, seed_agents, submit_action, agent_context


def test_rotation_revocation_and_expiry(isolated_db):
    old = seed_agents()[0]['key']
    aid = authenticate(old)['id']
    new = controls.rotate_key(aid)
    with pytest.raises(HTTPException): authenticate(old)
    assert authenticate(new)['id'] == aid
    with connect() as cx: cx.execute('UPDATE agents SET key_expires=1 WHERE id=?',(aid,))
    with pytest.raises(HTTPException): authenticate(new)
    controls.rotate_key(aid,revoke=True)
    with pytest.raises(HTTPException): submit_action(aid,{'action':'idle'})


def test_replay_atomic_and_persistent(isolated_db):
    aid = authenticate(seed_agents()[0]['key'])['id']
    action = {'action':'post','title':'Once','body':'Exactly once'}
    def send(_): return submit_action(aid,action,request_id='repeat-123')
    with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(send,range(4)))
    assert all(r == results[0] for r in results)
    init_db()
    assert send(0) == results[0]
    assert overview()['metrics']['posts'] == 1
    with pytest.raises(HTTPException) as err:
        submit_action(aid,{'action':'idle'},request_id='repeat-123')
    assert err.value.status_code == 409


def test_idle_quota_and_invalid_action_rollback(isolated_db,monkeypatch):
    aid = authenticate(seed_agents()[0]['key'])['id']
    monkeypatch.setenv('LIVE_ACTIONS_PER_MINUTE','2')
    with pytest.raises(HTTPException): submit_action(aid,{'action':'post','title':''})
    submit_action(aid,{'action':'idle'})
    submit_action(aid,{'action':'idle'})
    init_db()
    with pytest.raises(HTTPException) as err: submit_action(aid,{'action':'idle'})
    assert err.value.status_code == 429


def test_http_boundary_and_provenance(isolated_db):
    token = seed_agents()[0]['key']
    with TestClient(app) as c:
        headers={'Authorization':'Bearer '+token,'Idempotency-Key':'http-test-1'}
        assert c.post('/api/agent/action',json={'action':'idle'}).status_code == 401
        assert c.post('/api/agent/action',headers=headers,json={'action':'idle','source':'model_runner'}).status_code==422
        assert c.post('/api/agent/action',headers=headers,json={'action':'react','post_id':True}).status_code==422
        assert c.post('/api/agent/action',headers=headers,content='x'*17000).status_code==413
        result=c.post('/api/agent/action',headers=headers,json={'action':'idle'})
        assert result.status_code == 200
        assert result.json()['source'] == 'external_unverified'
        public=c.get('/api/overview').text
        assert token not in public and 'key_hash' not in public


def test_concurrent_budget_reservations(isolated_db,monkeypatch):
    seed_agents()
    monkeypatch.setenv('LIVE_INPUT_USD_PER_M','1')
    monkeypatch.setenv('LIVE_OUTPUT_USD_PER_M','1')
    monkeypatch.setenv('LIVE_SPEND_LIMIT_USD','0.015')
    def call(aid):
        try: return controls.reserve(aid,'test','test',10000,controls.begin_round())
        except RuntimeError: return None
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(call,[1,2]))
    assert sum(r is not None for r in results)==1
    init_db()
    assert call(3) is None


def test_round_once_and_receipt_once(isolated_db):
    seed_agents()
    rid=controls.begin_round()
    receipt=controls.reserve(1,'test','test',10000,rid)
    controls.settle(receipt,10,10,'response-test')
    with pytest.raises(HTTPException): controls.reserve(1,'test','test',10000,rid)
    result=submit_action(1,{'action':'idle'},receipt=receipt)
    assert result['source']=='model_runner'
    with pytest.raises(HTTPException): submit_action(1,{'action':'idle'},receipt=receipt)


def test_uncertain_failure_stops_round_and_requires_reconciliation(isolated_db,monkeypatch):
    seed_agents()
    monkeypatch.setenv('OPENAI_API_KEY','offline-fake')
    calls=[]
    def fail(*args):
        calls.append(1)
        raise TimeoutError('secret must not leak')
    monkeypatch.setattr(engine,'infer',fail)
    result=engine.run_round(limit=3)
    assert len(calls)==1 and 'secret' not in json.dumps(result)
    engine.run_round(limit=3)
    assert len(calls)==1
    controls.reconcile(result[0]['receipt_id'],0.01)
    assert overview()['metrics']['cost_usd']==0.01


@pytest.mark.parametrize('value',['nan','inf','-1','0'])
def test_invalid_budget_fails_closed(isolated_db,monkeypatch,value):
    seed_agents()
    monkeypatch.setenv('LIVE_SPEND_LIMIT_USD',value)
    with pytest.raises(RuntimeError): controls.reserve(1,'test','test',10000,controls.begin_round())


def test_project_budget(isolated_db,monkeypatch):
    seed_agents()
    monkeypatch.setenv('LIVE_PROJECT_BUDGET_USD','4000')
    with pytest.raises(RuntimeError): controls.reserve(1,'test','test',10000,controls.begin_round())
    monkeypatch.setenv('LIVE_PROJECT_BUDGET_USD','3999')
    monkeypatch.setenv('LIVE_OTHER_COMMITTED_USD','1000')
    with pytest.raises(RuntimeError): controls.reserve(1,'test','test',10000,controls.begin_round())


def test_game_cooperation_restart_scoring(isolated_db):
    seed_agents()
    gid=submit_action(1,{'action':'game_start'})['game_id']
    assert all(c['value'] is None for c in overview()['games'][0]['clues'])
    assert agent_context(4)['games'][0]['your_private_clue'] is None
    with pytest.raises(HTTPException): submit_action(1,{'action':'game_solve','game_id':gid,'answer':[0,0,0]})
    with pytest.raises(HTTPException): submit_action(4,{'action':'game_share','game_id':gid})
    answer=[]
    for aid in (1,2,3):
        answer.append(agent_context(aid)['games'][0]['your_private_clue']['value'])
        submit_action(aid,{'action':'game_share','game_id':gid})
    init_db()
    with pytest.raises(HTTPException): submit_action(1,{'action':'game_share','game_id':gid})
    result=submit_action(3,{'action':'game_solve','game_id':gid,'answer':answer})
    assert result['state']=='WON' and result['score']==3
    assert len(overview()['games'][0]['moves'])==5
    with pytest.raises(HTTPException): submit_action(2,{'action':'game_solve','game_id':gid,'answer':answer})


def test_game_loss_and_demo_isolation(isolated_db):
    seed_agents()
    gid=submit_action(1,{'action':'game_start'},demo=True)['game_id']
    with pytest.raises(HTTPException): submit_action(1,{'action':'game_share','game_id':gid})
    for aid in (1,2,3): submit_action(aid,{'action':'game_share','game_id':gid},demo=True)
    actual=[c['value'] for c in overview()['games'][0]['clues']]
    wrong=[(n+1)%10 for n in actual]
    for aid in (1,2,3): result=submit_action(aid,{'action':'game_solve','game_id':gid,'answer':wrong},demo=True)
    assert result['state']=='LOST' and result['score']==0
    assert overview()['games'][0]['source']=='simulation'


def test_malformed_model_output_still_charged(isolated_db,monkeypatch):
    seed_agents()
    monkeypatch.setenv('OPENAI_API_KEY','fake-offline')
    monkeypatch.setattr(engine,'infer',lambda *a: engine.Completion('not json',20,20))
    engine.run_round(limit=1)
    assert overview()['metrics']['tokens']==40
    assert not overview()['events']


def test_paid_opt_in(isolated_db,monkeypatch):
    seed_agents()
    monkeypatch.setenv('OPENAI_API_KEY','fake-offline')
    monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS','NO')
    with pytest.raises(RuntimeError,match='opt-in'): engine.run_round(limit=1)


def test_migration_preserves_old_content(isolated_db):
    seed_agents()
    submit_action(1,{'action':'post','title':'Legacy','body':'Keep this'})
    submit_action(2,{'action':'post','title':'Old demo','body':'Scripted'},demo=True)
    with connect() as cx:
        for table in ('posts','reactions','proposals','votes','events'):
            cx.execute(f'ALTER TABLE {table} DROP COLUMN source')
        cx.execute('ALTER TABLE agents DROP COLUMN enabled')
        cx.execute('ALTER TABLE agents DROP COLUMN key_expires')
        cx.execute('PRAGMA user_version=0')
    init_db()
    init_db()
    posts=overview()['posts']
    assert len(posts)==2
    assert posts[0]['source']=='simulation'
    assert posts[1]['source']=='legacy_unverified'


def test_rotation_between_auth_and_write(isolated_db):
    agent=authenticate(seed_agents()[0]['key'])
    controls.rotate_key(agent['id'])
    with pytest.raises(HTTPException) as exc:
        submit_action(agent['id'],{'action':'idle'},credential_hash=agent['key_hash'])
    assert exc.value.status_code==401
    assert not overview()['events']


def test_overrun_reconciliation_does_not_double_count(isolated_db):
    seed_agents()
    receipt=controls.reserve(1,'test','test',1,controls.begin_round())
    cost=controls.settle(receipt,100000,100000,'actual-provider-response')
    with pytest.raises(RuntimeError,match='Uncertain'):
        controls.reserve(2,'test','test',1,controls.begin_round())
    controls.reconcile(receipt,cost)
    assert overview()['metrics']['cost_usd']==pytest.approx(cost)
    controls.reserve(2,'test','test',1,controls.begin_round())


def test_resumed_round_does_not_recall_agents(isolated_db,monkeypatch):
    seed_agents()
    monkeypatch.setenv('OPENAI_API_KEY','fake-offline')
    calls=[]
    def infer(*args):
        calls.append(1)
        return engine.Completion('{"action":"idle"}',5,5)
    monkeypatch.setattr(engine,'infer',infer)
    result=engine.run_round(limit=2)
    assert len(calls)==2
    assert engine.run_round(limit=2,round_id=result[0]['round_id'])==[]
    assert len(calls)==2


def test_api_rate_limit_includes_failed_auth(isolated_db):
    seed_agents()
    with connect() as cx:
        for _ in range(120): controls.quota(cx,0,'api-global',120)
    with TestClient(app) as c:
        assert c.get('/api/agent/context').status_code==429
        assert c.get('/api/overview').status_code==200
