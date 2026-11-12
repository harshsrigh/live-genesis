"""Per-action provenance: demo / engine / api. Offline; no model calls."""
import pytest
from fastapi.testclient import TestClient

from live_genesis import engine
from live_genesis.app import app
from live_genesis.cli import demo
from live_genesis.core import authenticate, connect, init_db, open_round, overview, seed_agents, submit_action

POST={'action':'post','title':'Provenance test','body':'Where did this come from?','topic':'engineering'}
PROPOSAL={'action':'propose','title':'Label every action','body':'Record the source of each action.','kind':'project'}


def rows():
    with connect() as cx:
        return [dict(r) for r in cx.execute('SELECT * FROM action_provenance ORDER BY event_id')]


def test_demo_rows_are_scripted_unverified_and_have_no_model(isolated_db):
    seed_agents()
    demo()
    state=overview()
    recorded=rows()
    assert len(recorded)==state['metrics']['recorded_actions']>0
    assert all(r['source']=='demo' and r['verified']==0 and r['model'] is None and r['provider'] is None and r['client'] is None for r in recorded)
    assert all(e['source']=='demo' and e['verified']==0 and e['model'] is None for e in state['events'])
    assert all(p['source']=='demo' and p['model'] is None and p['verified']==0 for p in state['posts']+state['proposals'])
    assert state['metrics']['actions_by_source']=={'demo':len(recorded),'engine':0,'api':0,'unknown':0}


def test_engine_rows_record_provider_and_requested_model(isolated_db,monkeypatch):
    seed_agents(model='test-model-1')
    monkeypatch.setenv('OPENAI_API_KEY','fake-key-never-used')
    requested=[]
    def fake_infer(provider,model,prompt):
        requested.append((provider,model))
        return engine.Completion('{"action":"post","topic":"research","title":"Mocked","body":"Offline mocked provider response.","memory":"m"}',10,5)
    monkeypatch.setattr(engine,'infer',fake_infer)
    engine.run_round(limit=2)
    recorded=rows()
    assert requested==[('openai','test-model-1')]*2
    assert [(r['source'],r['provider'],r['model'],r['client'],r['verified'],r['kind']) for r in recorded]==[('engine','openai','test-model-1',None,1,'post')]*2
    state=overview()
    assert all(e['source']=='engine' and e['provider']=='openai' and e['model']=='test-model-1' and e['verified']==1 for e in state['events'])
    assert all(p['source']=='engine' and p['model']=='test-model-1' and p['verified']==1 for p in state['posts'])
    assert state['metrics']['actions_by_source']=={'demo':0,'engine':2,'api':0,'unknown':0}


def test_engine_failure_records_no_provenance(isolated_db,monkeypatch):
    seed_agents()
    monkeypatch.setenv('OPENAI_API_KEY','fake-key-never-used')
    monkeypatch.setattr(engine,'infer',lambda provider,model,prompt: engine.Completion('not json',10,5))
    engine.run_round(limit=1)
    assert rows()==[]


def test_api_rows_with_and_without_self_declared_headers(isolated_db):
    tokens=seed_agents()
    client=TestClient(app)
    plain=client.post('/api/agent/action',json=POST,headers={'Authorization':'Bearer '+tokens[0]['key']})
    declared=client.post('/api/agent/action',json=PROPOSAL,headers={
        'Authorization':'Bearer '+tokens[1]['key'],'X-Agent-Model':'vendor/some-model:1.0','X-Agent-Client':'my bot 0.3'})
    assert plain.status_code==200 and declared.status_code==200
    first,second=rows()
    assert (first['source'],first['provider'],first['model'],first['client'],first['verified'])==('api',None,None,None,0)
    # Declared values are stored verbatim but NEVER marked verified, and no provider is claimed.
    assert (second['source'],second['provider'],second['model'],second['client'],second['verified'])==('api',None,'vendor/some-model:1.0','my bot 0.3',0)
    assert (second['kind'],second['ref_id'])==('propose',declared.json()['id'])
    state=client.get('/api/overview').json()
    newest,oldest=state['events']
    assert {k:newest[k] for k in ('source','model','client','verified')}=={'source':'api','model':'vendor/some-model:1.0','client':'my bot 0.3','verified':0}
    assert {k:oldest[k] for k in ('source','model','client','verified')}=={'source':'api','model':None,'client':None,'verified':0}
    assert (state['posts'][0]['source'],state['posts'][0]['model'],state['posts'][0]['verified'])==('api',None,0)
    assert (state['proposals'][0]['source'],state['proposals'][0]['model'],state['proposals'][0]['verified'])==('api','vendor/some-model:1.0',0)
    assert state['metrics']['actions_by_source']=={'demo':0,'engine':0,'api':2,'unknown':0}


@pytest.mark.parametrize('header,value',[
    ('X-Agent-Model','x'*81),('X-Agent-Model','<script>alert(1)</script>'),('X-Agent-Model','model;drop'),
    ('X-Agent-Client','bot "quoted"'),('X-Agent-Client','a,b'),('X-Agent-Client',''),
])
def test_invalid_declaration_is_rejected_and_nothing_recorded(isolated_db,header,value):
    tokens=seed_agents()
    client=TestClient(app)
    auth={'Authorization':'Bearer '+tokens[0]['key']}
    assert client.post('/api/agent/action',json=POST,headers={**auth,header:value}).status_code==422
    state=overview()
    assert state['metrics']['posts']==0 and state['events']==[] and rows()==[]
    # The rejection consumed no quota: a clean request still goes through.
    assert client.post('/api/agent/action',json=POST,headers={**auth,header:'x'*80}).status_code==200


def test_malformed_provenance_is_refused_and_verified_is_never_caller_supplied(isolated_db):
    a=authenticate(seed_agents()[0]['key'])
    for bad in ({'source':'engine'},{'source':'api','verified':1},{'source':'demo'},{'source':'operator'},'engine'):
        with pytest.raises(ValueError):
            submit_action(a['id'],POST,provenance=bad)
    with pytest.raises(ValueError):
        submit_action(a['id'],POST,demo=True,quota=False,provenance={'source':'api'})
    assert rows()==[] and overview()['metrics']['posts']==0


def test_no_http_header_can_select_source_or_verified(isolated_db):
    tokens=seed_agents()
    client=TestClient(app)
    response=client.post('/api/agent/action',json=POST,headers={
        'Authorization':'Bearer '+tokens[0]['key'],'X-Agent-Source':'engine','X-Agent-Verified':'1','X-Agent-Provider':'openai'})
    assert response.status_code==200
    (row,)=rows()
    assert (row['source'],row['provider'],row['verified'])==('api',None,0)


def test_replay_creates_no_second_row(isolated_db):
    tokens=seed_agents()
    client=TestClient(app)
    headers={'Authorization':'Bearer '+tokens[0]['key'],'Idempotency-Key':'prov-key-0001','X-Agent-Model':'model-a'}
    first=client.post('/api/agent/action',json=POST,headers=headers)
    # Even a replay that declares something different leaves the original record untouched.
    replay=client.post('/api/agent/action',json=POST,headers={**headers,'X-Agent-Model':'model-b'})
    assert first.status_code==200 and replay.status_code==200 and replay.json()['replayed'] is True
    open_round()
    assert client.post('/api/agent/action',json=POST,headers=headers).json()['replayed'] is True
    (row,)=rows()
    assert row['model']=='model-a'
    assert overview()['metrics']['actions_by_source']['api']==1


def test_idle_records_nothing(isolated_db):
    a=authenticate(seed_agents()[0]['key'])
    submit_action(a['id'],{'action':'idle'},provenance={'source':'api','model':'m'})
    assert rows()==[]


def test_old_events_without_provenance_read_as_unknown_or_demo(isolated_db):
    a=authenticate(seed_agents()[0]['key'])
    submit_action(a['id'],POST)
    submit_action(a['id'],PROPOSAL,demo=True,quota=False)
    # Simulate a database written before the action_provenance table existed.
    with connect() as cx:
        cx.execute('DROP TABLE action_provenance')
    init_db()  # upgrade path: CREATE TABLE IF NOT EXISTS on an existing database
    assert rows()==[]
    state=overview()
    newest,oldest=state['events']
    assert {k:newest[k] for k in ('source','model','client','verified')}=={'source':'demo','model':None,'client':None,'verified':0}
    assert {k:oldest[k] for k in ('source','model','client','verified')}=={'source':'unknown','model':None,'client':None,'verified':0}
    assert (state['posts'][0]['source'],state['posts'][0]['model'])==('unknown',None)
    assert (state['proposals'][0]['source'],state['proposals'][0]['model'])==('demo',None)
    assert state['metrics']['actions_by_source']=={'demo':1,'engine':0,'api':0,'unknown':1}
    # New actions on the upgraded database are labelled again.
    open_round()
    submit_action(a['id'],POST)
    assert overview()['metrics']['actions_by_source']=={'demo':1,'engine':0,'api':1,'unknown':1}


def test_metrics_breakdown_mixed_sources(isolated_db,monkeypatch):
    tokens=seed_agents()
    demo()
    scripted=overview()['metrics']['recorded_actions']
    monkeypatch.setenv('OPENAI_API_KEY','fake-key-never-used')
    monkeypatch.setattr(engine,'infer',lambda provider,model,prompt: engine.Completion('{"action":"post","topic":"research","title":"Mocked","body":"Offline mocked provider response.","memory":"m"}',10,5))
    engine.run_round(limit=3)
    open_round()
    TestClient(app).post('/api/agent/action',json=POST,headers={'Authorization':'Bearer '+tokens[0]['key']})
    metrics=overview()['metrics']
    assert metrics['actions_by_source']=={'demo':scripted,'engine':3,'api':1,'unknown':0}
    assert sum(metrics['actions_by_source'].values())==metrics['recorded_actions']
