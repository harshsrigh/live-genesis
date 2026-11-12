"""Budget reservations and operator reconciliation. Offline: no provider is contacted."""
import json
import threading

import pytest

from live_genesis import engine
from live_genesis.core import (BudgetExceeded, budget_status, close_reservation, connect, overview, record_reconciliation,
                               record_usage, reserve_budget, seed_agents, settle_reservation)

GOOD='{"action":"post","topic":"research","title":"Mocked","body":"Offline mocked provider response.","memory":"m"}'


def test_reservation_counts_until_settled_or_released(isolated_db):
    first=reserve_budget(None,'m',0.4,1.0)
    second=reserve_budget(None,'m',0.4,1.0)
    with pytest.raises(BudgetExceeded):
        reserve_budget(None,'m',0.4,1.0)
    close_reservation(first,'released')
    third=reserve_budget(None,'m',0.4,1.0)
    # Settling swaps the hold for the (much smaller) usage estimate.
    assert settle_reservation(second,1000,1000)==pytest.approx(0.0014)
    status=budget_status()
    assert status['open_reservations']==1 and status['reserved_usd']==0.4
    assert status['committed_usd']==pytest.approx(0.4014)
    with pytest.raises(ValueError):
        settle_reservation(second,1,1)  # no double settlement
    with pytest.raises(ValueError):
        close_reservation(first,'unconfirmed')
    with pytest.raises(ValueError):
        reserve_budget(None,'m',0,1.0)
    close_reservation(third,'unconfirmed')
    assert budget_status()['reserved_usd']==0.4  # unconfirmed still counts


def test_concurrent_reservations_cannot_overshoot(isolated_db):
    results=[]
    start=threading.Barrier(12)
    def worker():
        start.wait()
        try:
            results.append(reserve_budget(None,'m',0.25,1.0))
        except BudgetExceeded:
            results.append(None)
    threads=[threading.Thread(target=worker) for _ in range(12)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(results)==12
    assert sum(r is not None for r in results)==4
    assert budget_status()['committed_usd']==1.0


def test_round_respects_holds_from_another_runner(isolated_db,monkeypatch):
    seed_agents()
    monkeypatch.setenv('OPENAI_API_KEY','fake')
    monkeypatch.setenv('LIVE_SPEND_LIMIT_USD','0.005')  # one call's hold is $0.00318
    reserve_budget(None,'other-runner',0.003,0.005)
    monkeypatch.setattr(engine,'infer',lambda *a: (_ for _ in ()).throw(AssertionError('must not call')))
    result=engine.run_round(limit=1)
    assert 'ceiling' in result[0]['error']
    assert overview()['metrics']['tokens']==0


def test_failed_inference_stays_counted_as_unconfirmed(isolated_db,monkeypatch):
    seed_agents()
    monkeypatch.setenv('OPENAI_API_KEY','fake')
    def flaky(provider,model,prompt):
        if json.loads(prompt)['identity']['name']=='Astra':
            raise TimeoutError('secret provider detail')
        return engine.Completion(GOOD,100,50)
    monkeypatch.setattr(engine,'infer',flaky)
    out=engine.run_round(limit=2)
    assert out[0]['error']=='inference failed' and 'unconfirmed' in out[0]['budget']
    assert 'secret' not in json.dumps(out)
    # Rule ported from codex/local-0.2: an unconfirmed charge STOPS the round. (This test used
    # to expect the second agent to be called.) Nothing more is called until `reconcile`.
    assert len(out)==1 and out[0]['reservation']==1
    assert 'reconcile' in engine.run_round(limit=2)[0]['error']
    status=budget_status()
    assert status['open_reservations']==1
    assert status['reserved_usd']==pytest.approx(0.00318)
    assert status['committed_usd']==pytest.approx(0.00318+status['estimated_usd'])
    metrics=overview()['metrics']
    assert metrics['reserved_usd']==status['reserved_usd'] and metrics['provider_reported_usd'] is None
    with connect() as cx:
        assert [r[0] for r in cx.execute('SELECT status FROM budget_reservations ORDER BY id')]==['unconfirmed']


def test_setup_error_releases_the_hold(isolated_db,monkeypatch):
    seed_agents()
    monkeypatch.setenv('OPENAI_API_KEY','fake')
    def not_ready(*args):
        raise engine.ProviderNotReady('sdk missing')
    monkeypatch.setattr(engine,'infer',not_ready)
    assert engine.run_round(limit=1)[0]['error']=='ProviderNotReady'
    assert budget_status()['committed_usd']==0


def test_reconciliation_raises_but_never_lowers_the_gate(isolated_db):
    record_usage(None,'m',1_000_000,0)  # $0.20 estimated
    held=reserve_budget(None,'m',0.05,10)
    close_reservation(held,'unconfirmed')
    report=record_reconciliation(0.50,'  provider usage page  ')
    assert report['last_reconciliation']['difference_usd']==pytest.approx(0.30)
    assert report['last_reconciliation']['note']=='provider usage page'
    assert report['committed_usd']==pytest.approx(0.55)
    record_usage(None,'m',500_000,0)  # +$0.10 after the entry
    assert budget_status()['committed_usd']==pytest.approx(0.65)
    assert overview()['metrics']['provider_reported_usd']==0.50
    assert overview()['metrics']['cost_usd']==pytest.approx(0.30)  # estimate is reported unchanged
    with pytest.raises(BudgetExceeded):
        reserve_budget(None,'m',0.40,1.0)
    # A lower operator figure cannot push the gate under the local estimate.
    cleared=record_reconciliation(0.01,release_open=True)
    assert cleared['open_reservations']==0
    assert cleared['committed_usd']==pytest.approx(0.30)
    for bad in (-1, float('nan'), 'ten'):
        with pytest.raises(ValueError):
            record_reconciliation(bad)
