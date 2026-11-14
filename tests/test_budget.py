"""Budget reservations and operator reconciliation. Offline: no provider is contacted."""
import threading

import pytest

from live_genesis.core import (BudgetExceeded, budget_status, close_reservation, record_reconciliation,
                               record_usage, reserve_budget, settle_reservation, spent_usd)


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
    assert budget_status()['last_reconciliation']['provider_reported_usd']==0.50
    assert spent_usd()==pytest.approx(0.30)  # estimate is reported unchanged
    with pytest.raises(BudgetExceeded):
        reserve_budget(None,'m',0.40,1.0)
    # A lower operator figure cannot push the gate under the local estimate.
    cleared=record_reconciliation(0.01,release_open=True)
    assert cleared['open_reservations']==0
    assert cleared['committed_usd']==pytest.approx(0.30)
    for bad in (-1, float('nan'), 'ten'):
        with pytest.raises(ValueError):
            record_reconciliation(bad)
