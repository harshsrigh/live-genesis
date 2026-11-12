"""Operator controls ported from codex/local-0.2: credentials, receipts, budget gates,
quotas, resumable rounds, paid opt-in, HTTP rate limit, migration, backup.

Offline and deterministic: `engine.infer` is mocked, time is injected, no provider is contacted.
"""
import json
import sqlite3
import sys
import threading

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from live_genesis import cli, controls, engine
from live_genesis.app import app
from live_genesis.core import (BudgetExceeded, ReconciliationRequired, agent_context, authenticate, budget_status,
                               close_reservation, connect, current_round, init_db, open_round, overview,
                               record_reconciliation, reserve_budget, seed_agents, settle_reservation, submit_action)

POST={'action':'post','title':'Controls test','body':'Ported operator controls.','topic':'engineering'}
IDLE={'action':'idle'}
GOOD='{"action":"post","topic":"research","title":"Mocked","body":"Offline mocked provider response.","memory":"m"}'


class Clock:
    def __init__(self,start=1_800_000_000.0): self.t=start
    def __call__(self): return self.t
    def advance(self,seconds): self.t+=seconds


@pytest.fixture
def clock(monkeypatch):
    fake=Clock()
    monkeypatch.setattr(controls,'now',fake)
    return fake


def status_of(call):
    with pytest.raises(HTTPException) as err:
        call()
    return err.value.status_code


def audit_rows():
    with connect() as cx:
        return [(r['operation'],r['agent_id']) for r in cx.execute('SELECT * FROM operator_audit ORDER BY id')]


def run_cli(monkeypatch,*argv):
    monkeypatch.setattr(sys,'argv',['live_genesis.cli',*argv])
    cli.main()


def fake_provider(monkeypatch,text=GOOD,tokens=(100,50)):
    """Mock engine.infer; returns the list of agent names it was called for."""
    monkeypatch.setenv('OPENAI_API_KEY','fake-key-never-used')
    called=[]
    def infer(provider,model,prompt):
        called.append(json.loads(prompt)['identity']['name'])
        return engine.Completion(text,*tokens)
    monkeypatch.setattr(engine,'infer',infer)
    return called


def must_not_call(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','fake-key-never-used')
    monkeypatch.setattr(engine,'infer',lambda *a: (_ for _ in ()).throw(AssertionError('provider must not be called')))


def settled_receipt(agent_id,round_id=None):
    receipt=reserve_budget(agent_id,'model-x',0.4,10,provider='openai',round_id=round_id)
    settle_reservation(receipt,10,10)
    return receipt


# ---- 1. Credential lifecycle ----------------------------------------------------------

def test_rotation_revocation_and_expiry(isolated_db,clock):
    old=seed_agents()[0]['key']
    aid=authenticate(old)['id']
    new=controls.rotate_key(aid)
    assert status_of(lambda: authenticate(old))==401  # old key dies immediately
    agent=authenticate(new)
    assert agent['id']==aid and agent['key_expires']==clock.t+90*86400
    clock.advance(90*86400-1)
    authenticate(new)
    clock.advance(1)
    assert status_of(lambda: authenticate(new))==401  # expired
    newer=controls.rotate_key(aid)  # rotation renews
    authenticate(newer)
    assert controls.rotate_key(aid,revoke=True) is None
    assert status_of(lambda: authenticate(newer))==401  # the key itself is gone
    assert status_of(lambda: submit_action(aid,IDLE))==403  # and the agent is disabled
    assert status_of(lambda: submit_action(aid,POST))==403
    with connect() as cx:
        assert aid not in controls.active_agent_ids(cx) and len(controls.active_agent_ids(cx))==9
    again=controls.rotate_key(aid)  # a rotate re-enables, as in the reference
    assert authenticate(again)['enabled']==1
    with pytest.raises(ValueError):
        controls.rotate_key(999)
    assert audit_rows()==[('rotate-key',aid),('rotate-key',aid),('revoke-key',aid),('rotate-key',aid)]
    dump=json.dumps(audit_rows())
    assert not any(k in dump for k in (old,new,newer,again)) and 'live_' not in dump


def test_disabled_agent_with_valid_key_is_403(isolated_db):
    key=seed_agents()[0]['key']
    with connect() as cx:
        cx.execute('UPDATE agents SET enabled=0 WHERE id=1')
    assert status_of(lambda: authenticate(key))==403
    assert TestClient(app).get('/api/agent/context',headers={'Authorization':'Bearer '+key}).status_code==403


def test_init_keys_expire_in_90_days_and_migrated_keys_do_not(isolated_db,clock):
    key=seed_agents()[0]['key']
    assert authenticate(key)['key_expires']==clock.t+controls.KEY_LIFETIME_SECONDS
    with connect() as cx:
        cx.execute('UPDATE agents SET key_expires=0 WHERE id=1')  # what a migrated database holds
    clock.advance(10*365*86400)
    authenticate(key)


def test_rotation_between_auth_and_write(isolated_db):
    tokens=seed_agents()
    agent=authenticate(tokens[0]['key'])
    controls.rotate_key(agent['id'])
    # The request authenticated before the rotation; its write happens after it.
    assert status_of(lambda: submit_action(agent['id'],POST,credential_hash=agent['key_hash']))==401
    assert status_of(lambda: submit_action(agent['id'],IDLE,credential_hash=agent['key_hash']))==401
    other=authenticate(tokens[1]['key'])
    controls.rotate_key(other['id'],revoke=True)
    assert status_of(lambda: submit_action(other['id'],POST,credential_hash=other['key_hash']))==403
    assert overview()['events']==[] and overview()['metrics']['posts']==0
    third=authenticate(tokens[2]['key'])  # an untouched credential still writes
    assert submit_action(third['id'],POST,credential_hash=third['key_hash'])['id']==1


def test_expiry_between_auth_and_write(isolated_db,clock):
    agent=authenticate(seed_agents()[0]['key'])
    clock.advance(controls.KEY_LIFETIME_SECONDS)
    assert status_of(lambda: submit_action(agent['id'],POST,credential_hash=agent['key_hash']))==401
    assert overview()['events']==[]


def test_http_action_passes_the_authenticated_credential(isolated_db,monkeypatch):
    tokens=seed_agents()
    seen={}
    from live_genesis import app as app_module
    real=app_module.submit_action
    def rotate_then_submit(agent_id,action,**kw):
        seen.update(kw)
        controls.rotate_key(agent_id)  # lands between authentication and the write
        return real(agent_id,action,**kw)
    monkeypatch.setattr(app_module,'submit_action',rotate_then_submit)
    response=TestClient(app).post('/api/agent/action',json=POST,headers={'Authorization':'Bearer '+tokens[0]['key']})
    assert response.status_code==401 and seen['credential_hash']
    assert overview()['metrics']['posts']==0


def test_cli_rotate_and_revoke(isolated_db,monkeypatch,capsys,tmp_path):
    old=seed_agents()[0]['key']
    key_path=tmp_path/'secrets'/'agent-keys.json'
    assert cli.key_file()==key_path  # conftest points the CLI away from the repository
    run_cli(monkeypatch,'rotate-key','1')
    out=capsys.readouterr().out
    new=json.loads(key_path.read_text())['Astra']
    assert new!=old and new not in out and 'live_' not in out  # not printed unless asked
    assert authenticate(new)['id']==1 and status_of(lambda: authenticate(old))==401
    run_cli(monkeypatch,'rotate-key','1','--show-key')
    shown=json.loads(key_path.read_text())['Astra']
    assert capsys.readouterr().out.count(shown)==1
    run_cli(monkeypatch,'revoke-key','1')
    out=capsys.readouterr().out
    assert 'live_' not in out and 'Astra' not in json.loads(key_path.read_text())
    assert status_of(lambda: authenticate(shown))==401
    with pytest.raises(SystemExit):
        run_cli(monkeypatch,'rotate-key','999')
    assert [op for op,_ in audit_rows()]==['rotate-key','rotate-key','revoke-key']


def test_operator_audit_is_append_only(isolated_db):
    seed_agents()
    controls.rotate_key(1)
    with connect() as cx:
        with pytest.raises(sqlite3.DatabaseError):
            cx.execute('UPDATE operator_audit SET operation=?',('edited',))
        with pytest.raises(sqlite3.DatabaseError):
            cx.execute('DELETE FROM operator_audit')
    assert audit_rows()==[('rotate-key',1)]


# ---- 2. Receipt-bound engine provenance -----------------------------------------------

def test_receipt_once_and_only_once(isolated_db):
    seed_agents()
    receipt=settled_receipt(1)
    saved=submit_action(1,POST,receipt=receipt)
    with connect() as cx:
        row=dict(cx.execute('SELECT * FROM action_provenance').fetchone())
        usage=cx.execute('SELECT u.id FROM budget_reservations b JOIN api_usage u ON u.id=b.usage_id WHERE b.id=?',(row['reservation_id'],)).fetchone()
        assert cx.execute('SELECT used FROM budget_reservations WHERE id=?',(receipt,)).fetchone()[0]==1
    # action -> reservation -> usage row; provider and model come from the reservation.
    assert (row['source'],row['verified'],row['provider'],row['model'],row['reservation_id'],row['ref_id'])==('engine',1,'openai','model-x',receipt,saved['id'])
    assert usage is not None
    open_round()
    assert status_of(lambda: submit_action(1,POST,receipt=receipt))==409
    assert status_of(lambda: submit_action(1,IDLE,receipt=receipt))==409
    assert overview()['metrics']['actions_by_source']=={'demo':0,'engine':1,'api':0,'unknown':0}


def test_bad_receipts_are_refused_and_nothing_recorded(isolated_db):
    seed_agents()
    foreign=settled_receipt(2)
    held=reserve_budget(1,'model-x',0.4,10,provider='openai')
    released=reserve_budget(1,'model-x',0.4,10,provider='openai')
    close_reservation(released,'released')
    for receipt in (foreign,held,released,9999,True,'1',0):
        assert status_of(lambda: submit_action(1,POST,receipt=receipt))==409
    # Declaring the source is not a receipt; a demo record can never carry one.
    assert status_of(lambda: submit_action(1,POST,provenance={'source':'engine','provider':'openai','model':'m'}))==409
    mine=settled_receipt(1)
    assert status_of(lambda: submit_action(1,POST,demo=True,quota=False,receipt=mine))==409
    assert status_of(lambda: submit_action(1,POST,receipt=mine,provenance={'source':'api'}))==409
    assert overview()['events']==[]
    # A failed call that the operator reconciled is closed but is not a model response.
    failed=reserve_budget(1,'model-x',0.4,10,provider='openai')
    close_reservation(failed,'unconfirmed')
    controls.reconcile_reservation(failed,0.01)
    assert status_of(lambda: submit_action(1,POST,receipt=failed))==409
    # None of the refusals consumed the valid receipts.
    with connect() as cx:
        assert [r[0] for r in cx.execute('SELECT used FROM budget_reservations WHERE id IN (?,?)',(foreign,mine))]==[0,0]
    assert submit_action(1,POST,receipt=mine)['id']==1


def test_rejected_action_keeps_receipt_and_idle_consumes_it(isolated_db):
    seed_agents()
    receipt=settled_receipt(1)
    assert status_of(lambda: submit_action(1,{'action':'post','topic':'nope','title':'t','body':'b'},receipt=receipt))==422
    with connect() as cx:
        assert cx.execute('SELECT used FROM budget_reservations WHERE id=?',(receipt,)).fetchone()[0]==0
    assert submit_action(1,IDLE,receipt=receipt)=={'action':'idle','agent':'Astra'}
    assert status_of(lambda: submit_action(1,POST,receipt=receipt))==409  # the turn was spent on idle


def test_callers_without_receipt_are_api_or_demo_as_before(isolated_db):
    seed_agents()
    submit_action(1,POST)
    submit_action(2,POST,demo=True,quota=False)
    with connect() as cx:
        assert [(r['source'],r['verified'],r['reservation_id']) for r in cx.execute('SELECT * FROM action_provenance ORDER BY event_id')]==[('api',0,None),('demo',0,None)]


def test_engine_round_uses_each_receipt_once(isolated_db,monkeypatch):
    seed_agents(model='test-model-1')
    fake_provider(monkeypatch)
    out=engine.run_round(limit=2)
    with connect() as cx:
        rows=[dict(r) for r in cx.execute('''SELECT ap.source,ap.verified,ap.model,ap.provider,ap.reservation_id,b.used,b.status,b.round_id,b.agent_id,b.usage_id
            FROM action_provenance ap JOIN budget_reservations b ON b.id=ap.reservation_id ORDER BY ap.event_id''')]
    assert [r['reservation_id'] for r in rows]==[o['reservation'] for o in out]==[1,2]
    assert all((r['source'],r['verified'],r['model'],r['provider'],r['used'],r['status'],r['round_id'])==('engine',1,'test-model-1','openai',1,'settled',2) for r in rows)
    assert [r['agent_id'] for r in rows]==[1,2] and all(r['usage_id'] for r in rows)


# ---- 3. Budget gates ------------------------------------------------------------------

def test_project_budget_and_pre_validation_gate(isolated_db,monkeypatch):
    monkeypatch.setenv('LIVE_PROJECT_BUDGET_USD','4000')
    with pytest.raises(controls.ConfigError):
        reserve_budget(None,'m',0.1,10)
    monkeypatch.setenv('LIVE_PROJECT_BUDGET_USD','3999')
    monkeypatch.setenv('LIVE_OTHER_COMMITTED_USD','1000')  # the $1,000 gate is already used up
    with pytest.raises(BudgetExceeded):
        reserve_budget(None,'m',0.1,10)
    monkeypatch.setenv('LIVE_OTHER_COMMITTED_USD','999.5')  # $0.50 left under the gate, limit is $10
    reserve_budget(None,'m',0.4,10)
    with pytest.raises(BudgetExceeded):
        reserve_budget(None,'m',0.2,10)
    assert budget_status()['ceilings']=={'spend_limit_usd':10.0,'project_budget_usd':3999.0,'other_committed_usd':999.5,
                                         'pre_validation_gate_usd':1000.0,'effective_usd':0.5}
    monkeypatch.setenv('LIVE_OTHER_COMMITTED_USD','0')
    monkeypatch.setenv('LIVE_PROJECT_BUDGET_USD','0.5')  # project ceiling below the spend limit
    with pytest.raises(BudgetExceeded):
        reserve_budget(None,'m',0.2,10)
    monkeypatch.setenv('LIVE_PROJECT_BUDGET_USD','0.7')
    reserve_budget(None,'m',0.2,10)


@pytest.mark.parametrize('name',['LIVE_SPEND_LIMIT_USD','LIVE_PROJECT_BUDGET_USD','LIVE_INPUT_USD_PER_M','LIVE_OUTPUT_USD_PER_M'])
@pytest.mark.parametrize('value',['nan','inf','-inf','-1','0','ten',''])
def test_invalid_budget_fails_closed_before_any_call(isolated_db,monkeypatch,name,value):
    seed_agents()
    must_not_call(monkeypatch)
    monkeypatch.setenv(name,value)
    with pytest.raises(RuntimeError,match=name):
        engine.run_round(limit=1)
    assert current_round()==1 and budget_status()['open_reservations']==0  # no round opened, nothing held
    assert 'error' in budget_status()['ceilings'] or name.endswith('PER_M')  # status reads never raise


@pytest.mark.parametrize('value',['nan','inf','-1','ten','4000','5000'])
def test_invalid_commitments_or_project_budget_fail_closed(isolated_db,monkeypatch,value):
    seed_agents()
    must_not_call(monkeypatch)
    monkeypatch.setenv('LIVE_PROJECT_BUDGET_USD' if value in ('4000','5000') else 'LIVE_OTHER_COMMITTED_USD',value)
    with pytest.raises(controls.ConfigError):
        engine.run_round(limit=1)
    with pytest.raises(controls.ConfigError):
        reserve_budget(None,'m',0.1,10)
    overview()  # the public read still works


@pytest.mark.parametrize('limit',[float('nan'),float('inf'),0,-1,'10',None,True])
def test_reserve_refuses_an_invalid_limit(isolated_db,limit):
    with pytest.raises(controls.ConfigError):
        reserve_budget(None,'m',0.1,limit)
    assert budget_status()['open_reservations']==0


def test_concurrent_reservations_cannot_overshoot_the_project_gate(isolated_db,monkeypatch):
    monkeypatch.setenv('LIVE_PROJECT_BUDGET_USD','1.0')  # tighter than the $10 spend limit
    results=[]
    start=threading.Barrier(12)
    def worker():
        start.wait()
        try:
            results.append(reserve_budget(None,'m',0.25,10))
        except BudgetExceeded:
            results.append(None)
    threads=[threading.Thread(target=worker) for _ in range(12)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(results)==12 and sum(r is not None for r in results)==4
    assert budget_status()['committed_usd']==1.0


def test_rates_are_fixed_at_reserve_time(isolated_db,monkeypatch):
    receipt=reserve_budget(None,'m',0.4,10)  # fixture rates: 0.20 in / 1.20 out
    monkeypatch.setenv('LIVE_INPUT_USD_PER_M','50')
    monkeypatch.setenv('LIVE_OUTPUT_USD_PER_M','90')
    assert settle_reservation(receipt,1000,1000)==pytest.approx(0.0014)
    explicit=reserve_budget(None,'m',0.4,10,input_rate=1.0,output_rate=2.0)
    assert settle_reservation(explicit,1000,1000)==pytest.approx(0.003)
    with pytest.raises(controls.ConfigError):
        reserve_budget(None,'m',0.4,10,input_rate=float('nan'))
    for bad in ((-1,1),(1.5,1),(True,1)):
        held=reserve_budget(None,'m',0.1,10)
        with pytest.raises(ValueError):
            settle_reservation(held,*bad)
        close_reservation(held,'released')


# ---- 4. Unconfirmed charges block; reconcile unblocks ---------------------------------

def test_uncertain_failure_stops_round_and_requires_reconciliation(isolated_db,monkeypatch):
    seed_agents()
    monkeypatch.setenv('OPENAI_API_KEY','fake-key-never-used')
    calls=[]
    def fail(*args):
        calls.append(1)
        raise TimeoutError('secret must not leak')
    monkeypatch.setattr(engine,'infer',fail)
    result=engine.run_round(limit=3)
    assert len(calls)==1 and len(result)==1 and 'secret' not in json.dumps(result)
    receipt=result[0]['reservation']
    blocked=engine.run_round(limit=3)
    assert len(calls)==1 and current_round()==2  # no call, and no new round opened either
    assert 'reconcile' in blocked[0]['error'] and f'--receipt {receipt}' in blocked[0]['error']
    assert engine.run_round(limit=3,resume=True)==blocked
    with pytest.raises(ReconciliationRequired,match='provider'):
        reserve_budget(None,'m',0.001,10)
    assert budget_status()['unconfirmed_reservations']==[receipt]
    record_reconciliation(0.01,'cumulative figure only')  # without --release-open it does not unblock
    with pytest.raises(ReconciliationRequired):
        reserve_budget(None,'m',0.001,10)
    report=controls.reconcile_reservation(receipt,0.01)
    assert report['unconfirmed_reservations']==[] and report['open_reservations']==0
    assert overview()['metrics']['cost_usd']==0.01
    assert audit_rows()==[(f'reconcile reservation {receipt}: 0.01',1)]
    with pytest.raises(ValueError):
        controls.reconcile_reservation(receipt,0.01)  # once only
    monkeypatch.setattr(engine,'infer',lambda *a: engine.Completion(GOOD,100,50))
    # Resuming skips the agent whose call was reconciled: it is not paid for twice.
    assert [o['agent'] for o in engine.run_round(limit=3,resume=True)]==['Moss','Quill']


def test_reconcile_reservation_validation_and_release_open(isolated_db):
    held=reserve_budget(None,'m',0.4,10)
    for bad in (-1,float('nan'),float('inf'),'1',True,None):
        with pytest.raises(ValueError):
            controls.reconcile_reservation(held,bad)
    with pytest.raises(ValueError):
        controls.reconcile_reservation(999,1)
    close_reservation(held,'unconfirmed')
    with pytest.raises(ReconciliationRequired):
        reserve_budget(None,'m',0.1,10)
    cleared=record_reconciliation(0.5,release_open=True)  # the cumulative command still unblocks
    assert cleared['unconfirmed_reservations']==[]
    reserve_budget(None,'m',0.1,10)
    assert audit_rows()==[('reconcile cumulative: release-open',None)]


def test_overrun_reconciliation_does_not_double_count(isolated_db):
    seed_agents()
    receipt=reserve_budget(1,'m',0.001,10,provider='openai')
    cost=settle_reservation(receipt,100000,100000)  # $0.14 of usage against a $0.001 hold
    assert cost==pytest.approx(0.14)
    status=budget_status()
    assert status['unconfirmed_reservations']==[receipt]
    assert status['committed_usd']==pytest.approx(cost) and status['reserved_usd']==0  # usage once, hold not added
    with pytest.raises(ReconciliationRequired):
        reserve_budget(2,'m',0.001,10)
    controls.reconcile_reservation(receipt,cost)
    assert overview()['metrics']['cost_usd']==pytest.approx(cost)
    with connect() as cx:
        assert cx.execute('SELECT COUNT(*) FROM api_usage').fetchone()[0]==1  # no adjustment row needed
    reserve_budget(2,'m',0.001,10)


def test_overrun_reconciled_to_a_different_figure_and_release_open(isolated_db):
    first=reserve_budget(None,'m',0.001,10)
    settle_reservation(first,100000,100000)
    controls.reconcile_reservation(first,0.20)  # the bill said more than the estimate
    assert overview()['metrics']['cost_usd']==pytest.approx(0.20)
    second=reserve_budget(None,'m',0.001,10)
    settle_reservation(second,100000,100000)
    record_reconciliation(0.34,release_open=True)
    with connect() as cx:  # an overrun keeps its usage: closed as settled, not released
        assert cx.execute('SELECT status FROM budget_reservations WHERE id=?',(second,)).fetchone()[0]=='settled'
    assert budget_status()['committed_usd']==pytest.approx(0.34)


def test_engine_overrun_records_the_paid_action_then_stops(isolated_db,monkeypatch):
    seed_agents()
    called=fake_provider(monkeypatch,tokens=(50000,650))  # above the 12,000-token bound that was held
    out=engine.run_round(limit=3)
    assert called==['Astra'] and out[0]['action']=='post' and 'reconcile' in out[1]['error']
    status=budget_status()
    assert status['committed_usd']==pytest.approx(status['estimated_usd']) and status['unconfirmed_reservations']==[1]
    assert overview()['metrics']['actions_by_source']['engine']==1
    controls.reconcile_reservation(1,status['estimated_usd'])
    assert [o['agent'] for o in engine.run_round(limit=3,resume=True)]==['Moss','Quill']


def test_cli_reconcile_one_reservation(isolated_db,monkeypatch,capsys):
    held=reserve_budget(None,'m',0.4,10)
    close_reservation(held,'unconfirmed')
    for bad in (['--receipt','1'],['--actual-usd','1'],['--receipt','1','--actual-usd','1','--provider-usd','2'],['--receipt','7','--actual-usd','1']):
        with pytest.raises(SystemExit):
            run_cli(monkeypatch,'reconcile',*bad)
    capsys.readouterr()
    run_cli(monkeypatch,'reconcile','--receipt',str(held),'--actual-usd','0.02')
    report=json.loads(capsys.readouterr().out)
    assert report['unconfirmed_reservations']==[] and report['estimated_usd']==0.02 and report['spend_limit_usd']==10.0
    run_cli(monkeypatch,'reconcile')
    assert json.loads(capsys.readouterr().out)['committed_usd']==0.02


# ---- 5. Rate limits and per-agent overrides -------------------------------------------

def test_agent_specific_round_override_and_audit(isolated_db):
    seed_agents()
    controls.set_limit(1,'actions-round',3)
    for _ in range(3):
        submit_action(1,POST)
    assert status_of(lambda: submit_action(1,POST))==429
    assert agent_context(1)['round']=={'id':1,'actions_used':3,'actions_allowed':3}
    submit_action(2,POST)
    assert status_of(lambda: submit_action(2,POST))==429  # default still 1 for others
    assert agent_context(2)['round']['actions_allowed']==1
    controls.set_limit(3,'actions-round',0)  # zero blocks without revoking the key
    assert status_of(lambda: submit_action(3,POST))==429
    controls.set_limit(3,'actions-round',None)
    submit_action(3,POST)
    assert open_round()==2  # rounds keep their integer ids and never expire
    submit_action(1,POST)
    assert audit_rows()==[('set-quota actions-round: 3',1),('set-quota actions-round: 0',3),('set-quota actions-round: default',3)]


@pytest.mark.parametrize('agent_id,kind,maximum',[
    (999,'actions-round',1),(1,'made-up',1),(1,'actions-round',-1),
    (1,'actions-round',10001),(1,'actions-round',True),(1,'actions-round',1.5),
])
def test_invalid_overrides_are_refused(isolated_db,agent_id,kind,maximum):
    seed_agents()
    with pytest.raises(ValueError):
        controls.set_limit(agent_id,kind,maximum)
    with connect() as cx:
        assert cx.execute('SELECT COUNT(*) FROM agent_limits').fetchone()[0]==0
    assert audit_rows()==[]


def test_window_quotas_with_injected_clock(isolated_db,monkeypatch,clock):
    seed_agents()
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND','50')
    monkeypatch.setenv('LIVE_ACTIONS_PER_MINUTE','2')
    monkeypatch.setenv('LIVE_ACTIONS_PER_DAY','5')
    clock.t=clock.t//86400*86400  # start of a day (and of a minute)
    submit_action(1,POST); submit_action(1,IDLE)  # idle writes nothing but still counts as a request
    with pytest.raises(HTTPException) as err:
        submit_action(1,POST)
    assert err.value.status_code==429 and 'actions-minute' in err.value.detail
    submit_action(2,POST)  # another agent has its own window
    clock.advance(59)
    assert status_of(lambda: submit_action(1,POST))==429
    clock.advance(1)  # next minute
    submit_action(1,POST); submit_action(1,POST)
    clock.advance(60)
    submit_action(1,POST)  # fifth today
    clock.advance(60)
    with pytest.raises(HTTPException) as err:
        submit_action(1,POST)
    assert err.value.status_code==429 and 'actions-day' in err.value.detail
    clock.advance(86400)
    submit_action(1,POST)
    assert overview()['metrics']['posts']==6
    # Scripted demo records are outside every quota.
    monkeypatch.setenv('LIVE_ACTIONS_PER_MINUTE','1')
    for _ in range(3):
        submit_action(3,POST,demo=True,quota=False)


def test_window_defaults_and_unreadable_values(isolated_db,monkeypatch):
    for name in ('LIVE_ACTIONS_PER_MINUTE','LIVE_ACTIONS_PER_DAY','LIVE_CALLS_PER_HOUR'):
        monkeypatch.delenv(name)
    assert [controls.default_limit(k) for k in sorted(controls.LIMITS)]==[100,10,1,12,30]
    monkeypatch.setenv('LIVE_ACTIONS_PER_MINUTE','lots')
    monkeypatch.setenv('LIVE_ACTIONS_PER_DAY','-5')
    assert controls.default_limit('actions-minute')==10 and controls.default_limit('actions-day')==1


def test_window_overrides_are_agent_specific(isolated_db,monkeypatch,clock):
    seed_agents()
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND','50')
    controls.set_limit(1,'actions-minute',1)
    submit_action(1,POST)
    assert status_of(lambda: submit_action(1,POST))==429
    submit_action(2,POST); submit_action(2,POST)
    controls.set_limit(2,'actions-day',0)
    assert status_of(lambda: submit_action(2,IDLE))==429
    controls.set_limit(2,'actions-day',None)
    submit_action(2,IDLE)


def test_rejected_and_replayed_actions_do_not_consume_window_quota(isolated_db,monkeypatch,clock):
    seed_agents()
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND','50')
    monkeypatch.setenv('LIVE_ACTIONS_PER_MINUTE','2')
    assert status_of(lambda: submit_action(1,{'action':'post','title':''}))==422
    assert status_of(lambda: submit_action(1,{'action':'transfer_money','amount':5}))==422
    assert status_of(lambda: submit_action(1,{'action':'vote','proposal_id':999,'choice':'yes'}))==404
    assert status_of(lambda: submit_action(1,POST,receipt=12345))==409
    with connect() as cx:
        assert cx.execute('SELECT COUNT(*) FROM quota_windows').fetchone()[0]==0
    first=submit_action(1,POST,idempotency_key='retry-0001')
    for _ in range(5):
        assert submit_action(1,POST,idempotency_key='retry-0001')=={**first,'replayed':True}  # no 429
    with connect() as cx:
        assert [tuple(r) for r in cx.execute('SELECT kind,count FROM quota_windows WHERE agent_id=1 ORDER BY kind')]==[('actions-day',1),('actions-minute',1)]
    submit_action(1,POST,idempotency_key='retry-0002')
    assert status_of(lambda: submit_action(1,POST,idempotency_key='retry-0003'))==429
    assert submit_action(1,POST,idempotency_key='retry-0001')['replayed'] is True  # replays still answered
    assert overview()['metrics']['posts']==2


def test_calls_per_hour_per_agent_in_run_round(isolated_db,monkeypatch,clock):
    seed_agents()
    called=fake_provider(monkeypatch)
    monkeypatch.setenv('LIVE_CALLS_PER_HOUR','2')
    controls.set_limit(1,'inference-hour',0)  # Astra may not be called at all
    out=engine.run_round(limit=2)
    assert 'inference-hour' in out[0]['error'] and out[1]['action']=='post' and called==['Moss']
    with connect() as cx:  # the refused call left no hold
        assert cx.execute('SELECT COUNT(*) FROM budget_reservations WHERE agent_id=1').fetchone()[0]==0
    engine.run_round(limit=2)
    out=engine.run_round(limit=2)  # Moss has used 2 calls this hour
    assert called==['Moss','Moss'] and all('inference-hour' in o['error'] for o in out)
    clock.advance(3600)
    assert engine.run_round(limit=2)[1]['action']=='post' and called==['Moss']*3
    assert budget_status()['open_reservations']==0


def test_cli_set_quota(isolated_db,monkeypatch,capsys):
    seed_agents()
    run_cli(monkeypatch,'set-quota','2','actions-round','4')
    assert agent_context(2)['round']['actions_allowed']==4
    run_cli(monkeypatch,'set-quota','2','actions-round','default')
    assert agent_context(2)['round']['actions_allowed']==1
    for bad in (['2','actions-round','-1'],['2','actions-round','many'],['2','per-year','1'],['99','actions-round','1'],['2','actions-round','10001']):
        with pytest.raises(SystemExit):
            run_cli(monkeypatch,'set-quota',*bad)
    assert [op for op,_ in audit_rows()]==['set-quota actions-round: 4','set-quota actions-round: default']


# ---- 6. Resumable rounds --------------------------------------------------------------

def test_resumed_round_does_not_recall_agents(isolated_db,monkeypatch):
    seed_agents()
    called=fake_provider(monkeypatch,text='{"action":"idle"}',tokens=(5,5))
    engine.run_round(limit=2)
    assert called==['Astra','Moss'] and current_round()==2
    assert engine.run_round(limit=2,resume=True)==[]  # idle left no action, but the calls were paid
    assert called==['Astra','Moss'] and current_round()==2
    out=engine.run_round(limit=4,resume=True)  # the same round continues with the agents not yet called
    assert called==['Astra','Moss','Quill','Vela'] and current_round()==2 and len(out)==2
    with connect() as cx:
        assert [tuple(r) for r in cx.execute('SELECT agent_id,round_id,status FROM budget_reservations ORDER BY id')]==[(1,2,'settled'),(2,2,'settled'),(3,2,'settled'),(4,2,'settled')]
    engine.run_round(limit=1)  # without --resume a new round opens and everyone may be called again
    assert called[-1]=='Astra' and current_round()==3


def test_resume_skips_recorded_actions_and_interrupted_calls(isolated_db,monkeypatch):
    seed_agents()
    called=fake_provider(monkeypatch)
    open_round()
    submit_action(1,POST)  # Astra already acted in this round through the API
    reserve_budget(2,'m',0.00318,10,provider='openai',round_id=current_round())  # Moss: a run died mid-call (still held)
    gone=reserve_budget(3,'m',0.00318,10,provider='openai',round_id=current_round())
    close_reservation(gone,'released')  # Quill: nothing was sent, so calling again is safe
    out=engine.run_round(limit=4,resume=True)
    assert called==['Quill','Vela'] and [o['agent'] for o in out]==['Quill','Vela']


# ---- 7. Paid opt-in -------------------------------------------------------------------

@pytest.mark.parametrize('value',[None,'NO','yes','1','true',''])
def test_paid_opt_in(isolated_db,monkeypatch,value):
    seed_agents()
    must_not_call(monkeypatch)
    if value is None:
        monkeypatch.delenv('LIVE_ALLOW_PAID_CALLS')
    else:
        monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS',value)
    with pytest.raises(RuntimeError,match='opt-in'):
        engine.run_round(limit=1)
    assert current_round()==1 and budget_status()['open_reservations']==0


# ---- 8. HTTP rate limit ---------------------------------------------------------------

def test_failed_auth_is_throttled_per_address_without_locking_out_agents(isolated_db,monkeypatch):
    # Before the fix one pre-auth bucket per peer address counted everything, so three keyless
    # requests locked every agent behind the same address out. See also tests/test_hardening.py.
    keys=seed_agents()
    fake=Clock()
    for limiter in (controls.api_limiter,controls.auth_failure_limiter):
        monkeypatch.setattr(limiter,'clock',fake)
    monkeypatch.setattr(controls.api_limiter,'maximum',3)
    monkeypatch.setattr(controls.auth_failure_limiter,'maximum',3)
    good={'Authorization':'Bearer '+keys[0]['key']}
    with TestClient(app) as c:
        assert c.get('/api/agent/context').status_code==401
        assert c.get('/api/agent/context',headers={'Authorization':'Bearer live_guess'}).status_code==401
        # Two failures did not touch the agent's own allowance of three requests.
        assert [c.get('/api/agent/context',headers=good).status_code for _ in range(4)]==[200,200,200,429]
        assert c.post('/api/agent/action',json=POST,headers={'Authorization':'Bearer live_guess2'}).status_code==401
        # Bucket used up: further FAILED requests get 429; the valid key is still served (here: its own limit).
        blocked=c.get('/api/agent/context',headers={'Authorization':'Bearer live_guess3'})
        assert blocked.status_code==429 and 'failed authentications' in blocked.json()['detail'] and blocked.headers['retry-after']
        assert 'for this agent' in c.get('/api/agent/context',headers=good).json()['detail']
        assert c.get('/api/agent/context',headers={'Authorization':'Bearer '+keys[1]['key']}).status_code==200
        assert c.get('/api/overview').status_code==200 and c.get('/health').status_code==200  # public reads unaffected
        assert overview()['metrics']['posts']==0
        fake.advance(60)
        assert c.get('/api/agent/context',headers=good).status_code==200


def test_rate_limiter_is_per_client_and_configurable(monkeypatch):
    fake=Clock()
    limiter=controls.RateLimiter(maximum=2,seconds=60,clock=fake)
    assert [limiter.allow('a'),limiter.allow('a'),limiter.allow('a'),limiter.allow('b')]==[True,True,False,True]
    fake.advance(60)
    assert limiter.allow('a')
    limiter.reset()
    monkeypatch.setenv('LIVE_API_REQUESTS_PER_MINUTE','7')
    assert controls.RateLimiter().limit()==7
    monkeypatch.setenv('LIVE_API_REQUESTS_PER_MINUTE','junk')
    assert controls.RateLimiter().limit()==120


def test_observatory_has_no_rate_limit_middleware():
    from live_genesis.observatory import app as observatory_app
    assert 'agent_api_rate_limit' not in repr([m for m in observatory_app.user_middleware])
    assert not [r.path for r in observatory_app.routes if r.path.startswith('/api/agent')]


# ---- Migration and backup -------------------------------------------------------------

OLD_SCHEMA='''
CREATE TABLE agents (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
    role TEXT NOT NULL, persona TEXT NOT NULL, key_hash TEXT UNIQUE NOT NULL,
    model TEXT NOT NULL, memory TEXT NOT NULL DEFAULT '',
    credits INTEGER NOT NULL DEFAULT 100, created_at TEXT NOT NULL
);
CREATE TABLE api_usage (
    id INTEGER PRIMARY KEY, agent_id INTEGER REFERENCES agents(id),
    model TEXT NOT NULL, input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL, estimated_usd REAL NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE events (
    id INTEGER PRIMARY KEY, agent_id INTEGER REFERENCES agents(id),
    verb TEXT NOT NULL, summary TEXT NOT NULL,
    demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE budget_reservations (
    id INTEGER PRIMARY KEY, agent_id INTEGER REFERENCES agents(id), model TEXT NOT NULL,
    reserved_usd REAL NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('held','settled','released','unconfirmed')),
    usage_id INTEGER REFERENCES api_usage(id), created_at TEXT NOT NULL, closed_at TEXT
);
CREATE TABLE action_provenance (
    event_id INTEGER PRIMARY KEY REFERENCES events(id),
    source TEXT NOT NULL CHECK(source IN ('demo','engine','api')),
    provider TEXT, model TEXT, client TEXT,
    verified INTEGER NOT NULL DEFAULT 0 CHECK(verified IN (0,1)),
    kind TEXT NOT NULL, ref_id INTEGER
);
'''


def test_migration_from_old_schema_database(tmp_path,monkeypatch,isolated_db):
    import hashlib
    path=tmp_path/'old.sqlite'
    monkeypatch.setenv('LIVE_DB',str(path))
    key='live_old-database-key-000000000000000000000000'
    old=sqlite3.connect(path)
    old.executescript(OLD_SCHEMA)
    old.execute("INSERT INTO agents(name,role,persona,key_hash,model,created_at) VALUES ('Astra','Scientist','p',?,'m','2026-01-01T00:00:00+00:00')",
                (hashlib.sha256(key.encode()).hexdigest(),))
    old.execute("INSERT INTO events(agent_id,verb,summary,created_at) VALUES (1,'post','published · old','2026-01-01T00:00:00+00:00')")
    old.execute("INSERT INTO action_provenance(event_id,source,kind) VALUES (1,'api','post')")
    old.execute("INSERT INTO budget_reservations(agent_id,model,reserved_usd,status,created_at) VALUES (1,'m',0.4,'held','2026-01-01T00:00:00+00:00')")
    old.commit(); old.close()
    init_db()
    init_db()  # repeatable
    with connect() as cx:
        columns={t:{r[1] for r in cx.execute(f'PRAGMA table_info({t})')} for t in ('agents','budget_reservations','action_provenance')}
        tables={r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        old_prov=dict(cx.execute('SELECT * FROM action_provenance').fetchone())
    assert {'enabled','key_expires'}<=columns['agents']
    assert {'provider','round_id','input_rate','output_rate','used','reconciled_usd'}<=columns['budget_reservations']
    assert 'reservation_id' in columns['action_provenance'] and old_prov['reservation_id'] is None and old_prov['source']=='api'
    assert {'operator_audit','agent_limits','quota_windows'}<=tables
    agent=authenticate(key)  # the old key keeps working: enabled, no expiry invented
    assert (agent['enabled'],agent['key_expires'])==(1,0)
    assert submit_action(agent['id'],POST,credential_hash=agent['key_hash'])['id']==1
    # A hold made before the migration has no stored rates: it settles at the current settings.
    assert settle_reservation(1,1000,1000)==pytest.approx(0.0014)
    assert len(seed_agents())==9 and controls.rotate_key(1)
    assert len(overview()['events'])==2


def test_backup(isolated_db,tmp_path,monkeypatch,capsys):
    key=seed_agents()[0]['key']
    submit_action(1,POST)
    target=tmp_path/'copy.sqlite'
    assert controls.backup(target)==target
    copy=sqlite3.connect(target)
    assert copy.execute('SELECT COUNT(*) FROM agents').fetchone()[0]==10
    assert copy.execute('SELECT title FROM posts').fetchone()[0]=='Controls test'
    copy.close()
    before=target.read_bytes()
    with pytest.raises(FileExistsError):
        controls.backup(target)  # never overwrites
    with pytest.raises(FileNotFoundError):
        controls.backup(tmp_path/'missing-dir'/'copy.sqlite')
    assert target.read_bytes()==before
    with pytest.raises(SystemExit):
        run_cli(monkeypatch,'backup',str(target))
    capsys.readouterr()
    run_cli(monkeypatch,'backup',str(tmp_path/'second.sqlite'))
    out=capsys.readouterr().out
    assert 'private agent memory' in out and 'key hashes' in out and key not in out
    assert (tmp_path/'second.sqlite').is_file()
    assert overview()['metrics']['posts']==1  # the live database is untouched


def test_revoked_agents_are_not_called_by_the_engine(isolated_db,monkeypatch):
    seed_agents()
    called=fake_provider(monkeypatch)
    controls.rotate_key(1,revoke=True)
    engine.run_round(limit=2)
    assert called==['Moss','Quill']
    with pytest.raises(controls.CallRefused):
        reserve_budget(1,'m',0.001,10)
    assert budget_status()['open_reservations']==0
