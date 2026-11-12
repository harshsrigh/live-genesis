import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from live_genesis.app import app
from live_genesis.core import authenticate, agent_context, connect, overview, seed_agents, submit_action
from live_genesis.cli import demo


def test_seed_auth_and_reseed(isolated_db):
    created=seed_agents()
    assert len(created)==10
    assert len(seed_agents())==0
    agent=authenticate(created[0]['key'])
    assert agent['name']=='Astra'
    assert agent['memory']==''
    assert 'key_hash' not in str(agent_context(agent['id']))
    with pytest.raises(HTTPException) as exc:
        authenticate('random_invalid_key')
    assert exc.value.status_code==401


def test_post_react_propose_vote_with_persistence(isolated_db):
    identities=seed_agents()
    astra=authenticate(identities[0]['key'])
    moss=authenticate(identities[1]['key'])
    post=submit_action(astra['id'],{'action':'post','title':'Test idea','body':'Useful content','topic':'research'})
    assert post['id'] >= 1
    submit_action(moss['id'],{'action':'react','post_id':post['id'],'choice':'like'})
    prop=submit_action(astra['id'],{'action':'propose','kind':'fundraising','title':'Proposal','body':'External approval required','requested_usd':100})
    submit_action(moss['id'],{'action':'vote','proposal_id':prop['id'],'choice':'yes'})
    state=overview()
    assert state['metrics']['likes']==1
    assert state['proposals'][0]['yes']==1
    assert state['proposals'][0]['requested_usd']==100
    assert state['proposals'][0]['status']=='DISCUSSION'
    assert state['metrics']['recorded_actions']==4
    assert state['mode']=='UNVERIFIED'


def test_prohibited_money_like_action_and_validation(isolated_db):
    a=authenticate(seed_agents()[0]['key'])
    with pytest.raises(HTTPException) as err:
        submit_action(a['id'],{'action':'transfer_money','amount':4000})
    assert err.value.status_code==422
    with pytest.raises(HTTPException):
        submit_action(a['id'],{'action':'propose','kind':'project','title':'Bad','body':'Invalid','requested_usd':99})
    with pytest.raises(HTTPException):
        submit_action(a['id'],{'action':'post','topic':'unknown','title':'Hi','body':'Text'})
    with connect() as db:
        assert db.execute('SELECT count(*) FROM events').fetchone()[0]==0


def test_authentication_required_for_write(isolated_db):
    tokens=seed_agents()
    client=TestClient(app)
    assert client.get('/health').json()['payments_enabled'] is False
    assert client.get('/').status_code==200
    assert 'WE ARE' in client.get('/').text
    assert client.get('/api/overview').status_code==200
    assert client.get('/api/agent/context').status_code==401
    assert client.post('/api/agent/action',json={'action':'idle'}).status_code==401
    headers={'Authorization':'Bearer '+tokens[0]['key'], 'Idempotency-Key':'original-test-1'}
    assert client.get('/api/agent/context',headers=headers).status_code==200
    result=client.post('/api/agent/action',headers=headers,json={'action':'post','title':'Hello','body':'Hello world','topic':'community'})
    assert result.status_code==200
    assert client.get('/api/overview').json()['posts'][0]['title']=='Hello'


def test_demo_is_provenance_labeled(isolated_db,monkeypatch):
    # CLI's `init` writes key file into repo; preseed to keep this test self-contained.
    seed_agents()
    demo()
    state=overview()
    assert state['mode']=='DEMO'
    assert all(p['demo']==1 for p in state['posts'])
    assert all(p['demo']==1 for p in state['proposals'])
    assert state['metrics']['cost_usd']==0
    with pytest.raises(RuntimeError):
        demo()


def test_live_action_after_demo_sets_mixed_mode(isolated_db):
    identities=seed_agents()
    demo()
    agent=authenticate(identities[0]['key'])
    submit_action(agent['id'], {'action':'post','title':'Real API contribution','body':'This action is not from the scripted demo','topic':'research'})
    state=overview()
    assert state['mode']=='MIXED'
    assert state['posts'][0]['demo']==0
    assert state['metrics']['posts']==8
