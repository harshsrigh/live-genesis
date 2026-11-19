"""Operator controls: budget gates, reconciliation, the audit log, the request limiter, an old database, backup.

Offline and deterministic: time is injected, no provider is contacted. The paid path itself
(`archive-run`: opt-in, plan, holds, failures) is covered in tests/test_archive*.py.
"""
import json
import sqlite3
import sys
import threading

import pytest

from live_genesis import archive, cli, controls
from live_genesis.core import (BudgetExceeded, ReconciliationRequired, budget_status, close_reservation, connect, init_db,
                               record_reconciliation, reserve_budget, settle_reservation, spent_usd)


class Clock:
    def __init__(self,start=1_800_000_000.0): self.t=start
    def __call__(self): return self.t
    def advance(self,seconds): self.t+=seconds


def audit_rows():
    with connect() as cx:
        return [(r['operation'],r['agent_id']) for r in cx.execute('SELECT * FROM operator_audit ORDER BY id')]


def run_cli(monkeypatch,*argv):
    monkeypatch.setattr(sys,'argv',['live_genesis.cli',*argv])
    cli.main()


def test_operator_audit_is_append_only(isolated_db):
    with connect() as cx:
        controls.audit(cx,'a test entry')
    with connect() as cx:
        with pytest.raises(sqlite3.DatabaseError):
            cx.execute('UPDATE operator_audit SET operation=?',('edited',))
        with pytest.raises(sqlite3.DatabaseError):
            cx.execute('DELETE FROM operator_audit')
    assert audit_rows()==[('a test entry',None)]


# ---- Budget gates ---------------------------------------------------------------------

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
    monkeypatch.setenv(name,value)
    with pytest.raises(controls.ConfigError,match=name):
        controls.budget_config()  # what every paid run validates before anything is reserved or sent
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS','model-a=openai:gpt-test-1')
    with pytest.raises(controls.ConfigError,match=name):
        archive.plan()
    assert budget_status()['open_reservations']==0  # nothing held
    assert 'error' in budget_status()['ceilings'] or name.endswith('PER_M')  # status reads never raise


@pytest.mark.parametrize('value',['nan','inf','-1','ten','4000','5000'])
def test_invalid_commitments_or_project_budget_fail_closed(isolated_db,monkeypatch,value):
    monkeypatch.setenv('LIVE_PROJECT_BUDGET_USD' if value in ('4000','5000') else 'LIVE_OTHER_COMMITTED_USD',value)
    with pytest.raises(controls.ConfigError):
        controls.budget_config()
    with pytest.raises(controls.ConfigError):
        reserve_budget(None,'m',0.1,10)
    assert 'error' in budget_status()['ceilings']  # the status read still works


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


# ---- Unconfirmed charges block; reconcile unblocks ------------------------------------

def test_an_unconfirmed_hold_blocks_every_reservation_until_it_is_reconciled(isolated_db):
    receipt=reserve_budget(None,'m',0.4,10,provider='openai')
    close_reservation(receipt,'unconfirmed')  # what a paid run does when a call failed after it may have been sent
    with pytest.raises(ReconciliationRequired,match='provider') as err:
        reserve_budget(None,'m',0.001,10)
    assert 'reconcile' in str(err.value) and f'--receipt {receipt}' in str(err.value)
    assert budget_status()['unconfirmed_reservations']==[receipt]
    record_reconciliation(0.01,'cumulative figure only')  # without --release-open it does not unblock
    with pytest.raises(ReconciliationRequired):
        reserve_budget(None,'m',0.001,10)
    report=controls.reconcile_reservation(receipt,0.01)
    assert report['unconfirmed_reservations']==[] and report['open_reservations']==0
    assert spent_usd()==0.01
    assert audit_rows()==[(f'reconcile reservation {receipt}: 0.01',None)]
    with pytest.raises(ValueError):
        controls.reconcile_reservation(receipt,0.01)  # once only
    reserve_budget(None,'m',0.001,10)


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
    receipt=reserve_budget(None,'m',0.001,10,provider='openai')
    cost=settle_reservation(receipt,100000,100000)  # $0.14 of usage against a $0.001 hold
    assert cost==pytest.approx(0.14)
    status=budget_status()
    assert status['unconfirmed_reservations']==[receipt]
    assert status['committed_usd']==pytest.approx(cost) and status['reserved_usd']==0  # usage once, hold not added
    with pytest.raises(ReconciliationRequired):
        reserve_budget(None,'m',0.001,10)
    controls.reconcile_reservation(receipt,cost)
    assert spent_usd()==pytest.approx(cost)
    with connect() as cx:
        assert cx.execute('SELECT COUNT(*) FROM api_usage').fetchone()[0]==1  # no adjustment row needed
    reserve_budget(None,'m',0.001,10)


def test_overrun_reconciled_to_a_different_figure_and_release_open(isolated_db):
    first=reserve_budget(None,'m',0.001,10)
    settle_reservation(first,100000,100000)
    controls.reconcile_reservation(first,0.20)  # the bill said more than the estimate
    assert spent_usd()==pytest.approx(0.20)
    second=reserve_budget(None,'m',0.001,10)
    settle_reservation(second,100000,100000)
    record_reconciliation(0.34,release_open=True)
    with connect() as cx:  # an overrun keeps its usage: closed as settled, not released
        assert cx.execute('SELECT status FROM budget_reservations WHERE id=?',(second,)).fetchone()[0]=='settled'
    assert budget_status()['committed_usd']==pytest.approx(0.34)


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


# ---- The in-memory request limiter ----------------------------------------------------

def test_rate_limiter_is_per_client_and_configurable(monkeypatch):
    fake=Clock()
    limiter=controls.RateLimiter(maximum=2,seconds=60,clock=fake)
    assert [limiter.allow('a'),limiter.allow('a'),limiter.allow('a'),limiter.allow('b')]==[True,True,False,True]
    fake.advance(60)
    assert limiter.allow('a')
    limiter.reset()
    monkeypatch.setenv('LIVE_PUBLIC_REQUESTS_PER_MINUTE','7')
    assert controls.RateLimiter().limit()==7
    monkeypatch.setenv('LIVE_PUBLIC_REQUESTS_PER_MINUTE','junk')
    assert controls.RateLimiter().limit()==300


def test_client_address_rule(monkeypatch):
    monkeypatch.delenv('LIVE_TRUSTED_PROXIES', raising=False)
    assert controls.client_address('192.0.2.1', ['203.0.113.5']) == '192.0.2.1'
    assert controls.client_address(None) == 'unknown'
    monkeypatch.setenv('LIVE_TRUSTED_PROXIES', ' 192.0.2.1 , 192.0.2.2,, ::1 ')
    assert controls.trusted_proxies() == {'192.0.2.1', '192.0.2.2', '::1'}
    assert controls.client_address('192.0.2.1', ['6.6.6.6, 203.0.113.5']) == '203.0.113.5'
    assert controls.client_address('192.0.2.1', ['6.6.6.6', '203.0.113.5, 192.0.2.2']) == '203.0.113.5'   # two header lines, proxy chain
    assert controls.client_address('0:0:0:0:0:0:0:1', ['2001:DB8::7']) == '2001:db8::7'
    assert controls.client_address('192.0.2.1', ['192.0.2.2']) == '192.0.2.1'          # only proxies listed
    assert controls.client_address('192.0.2.1', []) == '192.0.2.1'
    assert controls.client_address('192.0.2.1', ['203.0.113.5, not-an-address']) == '192.0.2.1'
    assert controls.client_address('198.51.100.9', ['203.0.113.5']) == '198.51.100.9'  # peer is not a listed proxy


def test_limiter_memory_is_bounded():
    clock = Clock()
    limiter = controls.RateLimiter(maximum=2, seconds=60, clock=clock, max_keys=50)
    assert all(limiter.allow(f'198.51.100.{i}') for i in range(200))   # rotating addresses: allowed, not all remembered
    assert limiter.size() == 50
    assert [limiter.allow('198.51.100.0') for _ in range(2)] == [True, False]   # a remembered key is still limited
    assert limiter.blocked('198.51.100.0') and not limiter.blocked('198.51.100.199')
    clock.advance(60)
    assert not limiter.blocked('198.51.100.0') and limiter.size() == 0
    assert controls.public_limiter.max_keys == controls.LIMITER_MAX_KEYS == 10_000
    assert controls.public_limiter.limit() == 300


# ---- The command line -----------------------------------------------------------------

def test_cli_commands_are_the_archive_and_the_accounting_only(isolated_db,monkeypatch,capsys):
    with pytest.raises(SystemExit):
        run_cli(monkeypatch,'--help')
    listed=capsys.readouterr().out
    commands={'init','reconcile','backup','check-provider','archive-check-endpoints','archive-lock','archive-plan','archive-dry-run',
              'archive-run','archive-status','demo-archive','archive-chatbot-template','archive-chatbot-import','archive-chatbot-ask','archive-trends'}
    choices=[name.strip() for name in listed[listed.index('{')+1:listed.index('}')].split(',')]
    assert set(choices)==commands, sorted(set(choices)^commands)
    # The commands of the removed agent society are gone: argparse refuses them (exit 2) and nothing runs.
    for gone in ('round','open-round','study-run','demo','demo-game','status','turns','health','rotate-key','set-quota','task-add'):
        with pytest.raises(SystemExit) as err:
            run_cli(monkeypatch,gone)
        assert err.value.code==2, gone
    capsys.readouterr()
    run_cli(monkeypatch,'init')
    assert 'Database ready' in capsys.readouterr().out


# ---- An existing database and backup --------------------------------------------------

# A database as the first versions of this project made it: the society's tables, and the two accounting
# tables with their foreign key to `agents` and without the columns added later.
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
SOCIETY_TABLES=('agents','events','action_provenance')


def test_an_old_database_opens_and_its_society_tables_are_left_untouched(tmp_path,monkeypatch,isolated_db):
    path=tmp_path/'old.sqlite'
    monkeypatch.setenv('LIVE_DB',str(path))
    old=sqlite3.connect(path)
    old.executescript(OLD_SCHEMA)
    old.execute("INSERT INTO agents(name,role,persona,key_hash,model,created_at) VALUES ('Astra','Scientist','p','hash','m','2026-01-01T00:00:00+00:00')")
    old.execute("INSERT INTO events(agent_id,verb,summary,created_at) VALUES (1,'post','published · old','2026-01-01T00:00:00+00:00')")
    old.execute("INSERT INTO action_provenance(event_id,source,kind) VALUES (1,'api','post')")
    old.execute("INSERT INTO budget_reservations(agent_id,model,reserved_usd,status,created_at) VALUES (1,'m',0.4,'held','2026-01-01T00:00:00+00:00')")
    old.commit()
    def society():
        return {t:(old.execute("SELECT sql FROM sqlite_master WHERE name=?",(t,)).fetchone()[0],old.execute(f'SELECT * FROM {t}').fetchall())
                for t in SOCIETY_TABLES}
    before=society()
    init_db()
    init_db()  # repeatable
    assert society()==before  # neither altered nor dropped, and every row is as it was
    old.close()
    with connect() as cx:
        columns={r[1] for r in cx.execute('PRAGMA table_info(budget_reservations)')}
        tables={r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'provider','input_rate','output_rate','reconciled_usd'}<=columns
    assert {'operator_audit','archive_runs','archive_answers','archive_chatbot_runs'}<=tables
    # A hold made for an agent before the columns existed has no stored rates: it settles at the current settings,
    # and its usage row keeps the agent id.
    assert settle_reservation(1,1000,1000)==pytest.approx(0.0014)
    with connect() as cx:
        assert cx.execute('SELECT agent_id FROM api_usage').fetchone()[0]==1
    # New holds work in the old tables (foreign key to agents and all), with no agent.
    held=reserve_budget(None,'m',0.1,10,provider='openai')
    assert settle_reservation(held,1000,1000)==pytest.approx(0.0014)
    assert budget_status()['estimated_usd']==pytest.approx(0.0028)


def test_backup(isolated_db,tmp_path,monkeypatch,capsys):
    with connect() as cx:
        controls.audit(cx,'before the backup')
    target=tmp_path/'copy.sqlite'
    assert controls.backup(target)==target
    copy=sqlite3.connect(target)
    assert copy.execute('SELECT operation FROM operator_audit').fetchone()[0]=='before the backup'
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
    assert 'everything the database holds' in out and 'key hashes' in out
    assert (tmp_path/'second.sqlite').is_file()
    assert audit_rows()==[('before the backup',None)]  # the live database is untouched
