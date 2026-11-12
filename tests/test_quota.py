"""Per-agent round quota and replay (idempotency) guard. Offline; no model calls."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from live_genesis import engine
from live_genesis.app import app
from live_genesis.cli import demo
from live_genesis.core import agent_context, authenticate, current_round, open_round, overview, seed_agents, submit_action

POST={'action':'post','title':'Quota test','body':'One action per round','topic':'engineering'}


def test_quota_is_per_agent_and_per_round(isolated_db):
    ids=seed_agents()
    astra=authenticate(ids[0]['key']); moss=authenticate(ids[1]['key'])
    first=submit_action(astra['id'],POST)
    assert first['round']==current_round()==1
    with pytest.raises(HTTPException) as err:
        submit_action(astra['id'],{'action':'react','post_id':first['id'],'choice':'like'})
    assert err.value.status_code==429
    # Another agent has its own quota; idle never records anything or uses quota.
    submit_action(moss['id'],{'action':'react','post_id':first['id'],'choice':'like'})
    assert submit_action(astra['id'],{'action':'idle'})=={'action':'idle','agent':'Astra'}
    assert agent_context(astra['id'])['round']=={'id':1,'actions_used':1,'actions_allowed':1}
    assert open_round()==2
    assert submit_action(astra['id'],POST)['round']==2
    assert overview()['metrics']['posts']==2
    assert overview()['metrics']['round']==2


def test_rejected_action_does_not_consume_quota(isolated_db):
    a=authenticate(seed_agents()[0]['key'])
    with pytest.raises(HTTPException):
        submit_action(a['id'],{'action':'post','topic':'unknown','title':'Hi','body':'Text'})
    with pytest.raises(HTTPException):
        submit_action(a['id'],{'action':'vote','proposal_id':999,'choice':'yes'},idempotency_key='vote-key-0001')
    assert submit_action(a['id'],POST,idempotency_key='vote-key-0001')['id']==1


def test_configurable_quota(isolated_db,monkeypatch):
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND','2')
    a=authenticate(seed_agents()[0]['key'])
    submit_action(a['id'],POST); submit_action(a['id'],POST)
    with pytest.raises(HTTPException) as err:
        submit_action(a['id'],POST)
    assert err.value.status_code==429
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND','not-a-number')
    assert overview()['metrics']['actions_per_round']==1


def test_replay_returns_original_without_new_record(isolated_db):
    a=authenticate(seed_agents()[0]['key'])
    first=submit_action(a['id'],POST,idempotency_key='turn-0001-astra')
    again=submit_action(a['id'],POST,idempotency_key='turn-0001-astra')
    assert again=={**first,'replayed':True}
    # Still answered from the record after the quota is spent and in later rounds.
    open_round()
    assert submit_action(a['id'],POST,idempotency_key='turn-0001-astra')['id']==first['id']
    state=overview()
    assert state['metrics']['posts']==1
    assert state['metrics']['recorded_actions']==1
    with pytest.raises(HTTPException) as err:
        submit_action(a['id'],{**POST,'title':'Different'},idempotency_key='turn-0001-astra')
    assert err.value.status_code==409
    with pytest.raises(HTTPException) as err:
        submit_action(a['id'],POST,idempotency_key='short')
    assert err.value.status_code==422


def test_idempotency_keys_are_scoped_to_one_agent(isolated_db):
    ids=seed_agents()
    astra=authenticate(ids[0]['key']); moss=authenticate(ids[1]['key'])
    submit_action(astra['id'],POST,idempotency_key='shared-key-01')
    other=submit_action(moss['id'],POST,idempotency_key='shared-key-01')
    assert 'replayed' not in other
    assert overview()['metrics']['posts']==2


def test_api_enforces_quota_and_replay_for_authenticated_agents_only(isolated_db):
    tokens=seed_agents()
    client=TestClient(app)
    # No bearer key: nothing is recorded, and nothing is replayed from another caller's key.
    assert client.post('/api/agent/action',json=POST,headers={'Idempotency-Key':'api-key-0001'}).status_code==401
    assert client.post('/api/agent/action',json=POST,headers={'Authorization':'Bearer live_guess','Idempotency-Key':'api-key-0001'}).status_code==401
    assert overview()['metrics']['posts']==0
    auth={'Authorization':'Bearer '+tokens[0]['key']}
    first=client.post('/api/agent/action',json=POST,headers={**auth,'Idempotency-Key':'api-key-0001'})
    assert first.status_code==200
    replay=client.post('/api/agent/action',json=POST,headers={**auth,'Idempotency-Key':'api-key-0001'})
    assert replay.status_code==200 and replay.json()['replayed'] is True and replay.json()['id']==first.json()['id']
    assert client.post('/api/agent/action',json={**POST,'title':'Other'},headers={**auth,'Idempotency-Key':'api-key-0001'}).status_code==409
    assert client.post('/api/agent/action',json=POST,headers=auth).status_code==429
    assert client.post('/api/agent/action',json=POST,headers={**auth,'Idempotency-Key':'bad key!'}).status_code==422
    assert client.get('/api/agent/context',headers=auth).json()['round']['actions_used']==1
    assert overview()['metrics']['posts']==1
    # The API has no route that opens a round; only the operator CLI / engine can.
    assert not [r.path for r in app.routes if 'round' in r.path]


def test_demo_data_does_not_consume_live_quota(isolated_db):
    ids=seed_agents()
    demo()
    a=authenticate(ids[0]['key'])
    assert agent_context(a['id'])['round']['actions_used']==0
    assert submit_action(a['id'],POST)['round']==1


def test_engine_opens_one_round_per_invocation(isolated_db,monkeypatch):
    seed_agents()
    monkeypatch.setenv('OPENAI_API_KEY','fake-key-never-used')
    monkeypatch.setattr(engine,'infer',lambda provider,model,prompt: engine.Completion('{"action":"post","topic":"research","title":"Mocked","body":"Offline mocked provider response.","memory":"m"}',10,5))
    assert [o['round'] for o in engine.run_round(limit=2)]==[2,2]
    assert [o['round'] for o in engine.run_round(limit=2)]==[3,3]
    assert overview()['metrics']['posts']==4
    # A failed pre-flight check must not open a round.
    monkeypatch.delenv('OPENAI_API_KEY')
    with pytest.raises(RuntimeError):
        engine.run_round(limit=1)
    assert current_round()==3
