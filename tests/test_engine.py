import json
import pytest

from live_genesis import engine
from live_genesis.core import overview,seed_agents


def test_json_parser():
    assert engine.parse_object('```json\n{"action":"idle"}\n```')['action']=='idle'
    with pytest.raises(ValueError):
        engine.parse_object('not json')
    with pytest.raises(ValueError):
        engine.parse_object('[]')


def test_requires_real_provider_key(isolated_db):
    seed_agents()
    with pytest.raises(RuntimeError,match='OPENAI_API_KEY'):
        engine.run_round(limit=1)


def test_run_round_recorded_and_metered(isolated_db,monkeypatch):
    seed_agents()
    monkeypatch.setenv('OPENAI_API_KEY','fake-key-never-used')
    called=[]
    def fake_infer(provider,model,prompt):
        called.append((provider,model,json.loads(prompt)['identity']['name']))
        return engine.Completion('{"action":"post","topic":"research","title":"Verified sample run","body":"This is a mocked provider response in an offline test.","memory":"Testing collaboration."}',110,52)
    monkeypatch.setattr(engine,'infer',fake_infer)
    outcome=engine.run_round(limit=2)
    assert len(called)==2
    assert len(outcome)==2
    state=overview()
    assert state['metrics']['posts']==2
    assert state['metrics']['tokens']==324
    assert state['metrics']['cost_usd']>0
    assert state['mode']=='LIVE'


def test_app_budget_gate_prevents_first_call(isolated_db,monkeypatch):
    seed_agents()
    monkeypatch.setenv('OPENAI_API_KEY','fake')
    monkeypatch.setenv('LIVE_SPEND_LIMIT_USD','0.00001')
    def deny(*args):
        raise AssertionError('No inference should execute beyond limit')
    monkeypatch.setattr(engine,'infer',deny)
    result=engine.run_round(limit=1)
    assert 'ceiling' in result[0]['error']
    assert overview()['metrics']['tokens']==0


def test_prompt_is_trimmed_to_valid_json_not_sliced():
    post={'id':1,'topic':'research','title':'t'*100,'body':'b'*1200,'author':'Astra','likes':0,'dislikes':0}
    prop={'id':1,'title':'t'*100,'body':'b'*1400,'kind':'project','requested_usd':0,'requested_credits':0,'author':'Moss','yes':0,'no':0}
    context={'identity':{'name':'Astra','memory':'m'*700},'recent_posts':[{**post,'id':i} for i in range(8,0,-1)],
             'proposals':[{**prop,'id':i} for i in range(6,0,-1)],'rules':['r'*80]*5}
    assert len(json.dumps(context,separators=(',',':')))>engine.PROMPT_CHAR_LIMIT
    prompt=engine.build_prompt(context)
    assert len(prompt)<=engine.PROMPT_CHAR_LIMIT
    parsed=json.loads(prompt)  # still valid JSON
    assert parsed['identity']==context['identity'] and parsed['rules']==context['rules']
    assert parsed['recent_posts'][0]['id']==8 and parsed['proposals'][0]['id']==6  # newest kept
    assert len(parsed['recent_posts'])+len(parsed['proposals'])<14
    assert len(context['recent_posts'])==8  # caller's context untouched
    with pytest.raises(ValueError):
        engine.build_prompt({'identity':{'memory':'m'*500}},limit=100)
