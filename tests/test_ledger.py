"""Internal credit ledger: append-only, never negative, conserved, operator-granted only, never money.

Offline and deterministic. Agent ids follow the seed order (1 Astra ... 10 Solace).
"""
import json
import re
import sqlite3
import sys
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from live_genesis import cli, engine, governance, ledger
from live_genesis.app import app
from live_genesis.core import agent_context, authenticate, connect, init_db, open_round, overview, seed_agents, submit_action

STATIC = Path(__file__).resolve().parent.parent / 'live_genesis' / 'static'
POST = {'action': 'post', 'title': 'Ledger test', 'body': 'A post that is not about credits.', 'topic': 'engineering'}


def act(agent_id, action, **kwargs):
    kwargs.setdefault('quota', False)
    return submit_action(agent_id, action, cooldown=False, **kwargs)


def status_of(call):
    with pytest.raises(HTTPException) as err:
        call()
    return err.value.status_code


def entries():
    with connect() as cx:
        return [dict(r) for r in cx.execute('SELECT * FROM credit_ledger ORDER BY id')]


def balances():
    with connect() as cx:
        return {'pool': ledger.balance(cx, ledger.COMMONS), 'supply': ledger.supply(cx),
                'agents': {r['id']: r['credits'] for r in cx.execute('SELECT id,credits FROM agents')}}


def assert_consistent(supply):
    """The invariant checked after every scenario: nothing is created or lost by agent actions."""
    assert ledger.verify() == []
    state = balances()
    assert state['supply'] == supply
    with connect() as cx:
        allocated = sum(ledger.balance(cx, ('allocation', r[0])) for r in cx.execute('SELECT id FROM proposals'))
    assert sum(state['agents'].values()) + state['pool'] + allocated == supply


def audit_rows():
    with connect() as cx:
        return [(r['operation'], r['agent_id']) for r in cx.execute('SELECT * FROM operator_audit ORDER BY id')]


def run_cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, 'argv', ['live_genesis.cli', *argv])
    cli.main()


def test_opening_entries_define_balances_and_init_is_repeatable(isolated_db):
    seed_agents()
    first = entries()
    assert len(first) == 10 and all((e['account'], e['reason'], e['delta'], e['balance_after']) == ('agent', 'opening', 100, 100) for e in first)
    init_db(); init_db(); seed_agents()
    assert entries() == first  # running the migration again never double-credits
    assert balances() == {'pool': 0, 'supply': 1000, 'agents': {i: 100 for i in range(1, 11)}}
    assert_consistent(1000)


def test_migration_of_a_pre_ledger_database_uses_current_credits_once(isolated_db):
    seed_agents()
    with connect() as cx:  # simulate a database from before the ledger existed
        cx.execute('DROP TABLE credit_ledger')
        cx.execute('UPDATE agents SET credits=70 WHERE id=1')
        cx.execute('UPDATE agents SET credits=0 WHERE id=2')
    init_db()
    opened = {e['agent_id']: e['delta'] for e in entries()}
    assert opened[1] == 70 and opened[2] == 0 and opened[3] == 100 and len(opened) == 10
    init_db()
    assert len(entries()) == 10
    assert_consistent(870)


def test_ledger_rows_cannot_be_updated_deleted_or_forged(isolated_db):
    seed_agents()
    act(1, {'action': 'contribute', 'amount': 40})
    before = entries()
    for statement in ('UPDATE credit_ledger SET delta=1000 WHERE id=1', 'UPDATE credit_ledger SET balance_after=5 WHERE id=1',
                      'DELETE FROM credit_ledger WHERE id=1', 'DELETE FROM credit_ledger'):
        with pytest.raises(sqlite3.DatabaseError, match='append-only'):
            with connect() as cx:
                cx.execute(statement)
    forged = "INSERT INTO credit_ledger(account,agent_id,delta,balance_after,reason,transfer_id,created_at) VALUES ('agent',1,?,?,?,?,'t')"
    for values, message in (((-100, -40, 'contribute_commons', 99), 'CHECK'),      # negative balance
                            ((500, 560, 'operator_grant', None), None),            # the one forgeable shape: see below
                            ((10, 999, 'operator_grant', None), 'chain'),          # balance does not follow
                            ((-10, 50, 'opening', None), 'CHECK'),                 # minting rows are never negative
                            ((10, 70, 'contribute_commons', None), 'CHECK')):      # a transfer row needs its pair id
        if message is None:
            continue
        with pytest.raises(sqlite3.DatabaseError, match=message):
            with connect() as cx:
                cx.execute(forged, values)
    assert entries() == before
    # Honest limit: code with database access CAN insert a well-formed grant row; verify() reports it
    # because no operator_audit record backs it and the cached balance no longer matches.
    with connect() as cx:
        cx.execute(forged, (500, 560, 'operator_grant', None))
    problems = ledger.verify()
    assert any('without an operator_audit record' in p for p in problems) and any('cached credits' in p for p in problems)


def test_verify_reports_cache_drift_and_broken_chain(isolated_db):
    seed_agents()
    assert ledger.verify() == []
    with connect() as cx:
        cx.execute('UPDATE agents SET credits=999 WHERE id=3')
    assert ledger.verify() == ['agent 3 (Quill): cached credits 999 != ledger balance 100']
    with connect() as cx:
        cx.execute('UPDATE agents SET credits=100 WHERE id=3')
        cx.execute('DROP TRIGGER credit_ledger_no_update')  # only someone with file access can do this
        cx.execute('UPDATE credit_ledger SET delta=150 WHERE agent_id=4')
    problems = ledger.verify()
    assert any('does not follow' in p for p in problems) and any('total supply' in p for p in problems)


def test_concurrent_spending_never_goes_negative(isolated_db):
    seed_agents()
    results, lock = [], threading.Lock()

    def spend():
        try:
            outcome = act(1, {'action': 'contribute', 'amount': 30})['balance']
        except HTTPException as err:
            outcome = err.status_code
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=spend) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert sorted(r for r in results if r != 409) == [10, 40, 70] and results.count(409) == 5
    state = balances()
    assert state['agents'][1] == 10 and state['pool'] == 90
    assert min(e['balance_after'] for e in entries()) >= 0
    assert_consistent(1000)


def test_grant_is_operator_only_audited_and_the_only_source_of_new_credits(isolated_db, monkeypatch):
    seed_agents()
    done = ledger.grant('commons', 500, '  first   pool seed ')
    assert (done['balance'], done['total_supply'], done['target']) == (500, 1500, 'commons')
    assert ledger.grant(2, 25, 'test bonus')['balance'] == 125
    assert audit_rows() == [('grant-credits 500 to commons: first pool seed', None), ('grant-credits 25 to agent 2: test bonus', 2)]
    grants = [e for e in entries() if e['reason'] == 'operator_grant']
    assert [(e['account'], e['agent_id'], e['delta'], e['ref_kind'], e['ref_id'], e['transfer_id']) for e in grants] == [
        ('commons', None, 500, 'operator_audit', 1, None), ('agent', 2, 25, 'operator_audit', 2, None)]
    before = entries()
    for target, amount, reason in (('commons', 0, 'x'), ('commons', -5, 'x'), ('commons', True, 'x'), ('commons', 1.5, 'x'),
                                   ('commons', ledger.MAX_GRANT + 1, 'x'), ('commons', 10, '   '), (99, 10, 'x'), ('pool', 10, 'x'), ('1', 10, 'x')):
        with pytest.raises(ValueError):
            ledger.grant(target, amount, reason)
    assert entries() == before and len(audit_rows()) == 2
    # CLI
    run_cli(monkeypatch, 'grant-credits', 'commons', '50', '--reason', 'cli grant')
    run_cli(monkeypatch, 'grant-credits', '3', '5', '--reason', 'cli agent grant')
    assert audit_rows()[2:] == [('grant-credits 50 to commons: cli grant', None), ('grant-credits 5 to agent 3: cli agent grant', 3)]
    for argv in (('grant-credits', 'commons', '50'), ('grant-credits', 'everyone', '50', '--reason', 'x'),
                 ('grant-credits', 'commons', '0', '--reason', 'x'), ('grant-credits', 'commons', '10', '--usd', '5', '--reason', 'x')):
        with pytest.raises(SystemExit):
            run_cli(monkeypatch, *argv)
    assert_consistent(1000 + 500 + 25 + 50 + 5)
    run_cli(monkeypatch, 'verify-ledger')


def test_agents_cannot_create_credits_and_no_route_exposes_grants(isolated_db):
    seed_agents()
    for action in ({'action': 'grant-credits', 'amount': 5}, {'action': 'grant', 'amount': 5}, {'action': 'mint', 'amount': 5}):
        assert status_of(lambda: act(1, action)) == 422
    for amount in (0, -30, True, 2.5, '30', None, governance.MAX_CONTRIBUTION + 1):
        assert status_of(lambda: act(1, {'action': 'contribute', 'amount': amount})) == 422
    assert status_of(lambda: act(1, {'action': 'contribute', 'amount': 101})) == 409  # more than it holds
    assert len(entries()) == 10
    paths = [r.path for r in app.routes]
    assert not [p for p in paths if 'grant' in p or 'ledger' in p or 'credit' in p or 'review' in p]
    assert_consistent(1000)


def test_contribute_to_commons_records_provenance_and_replay_does_not_double_spend(isolated_db):
    keys = seed_agents()
    agent = authenticate(keys[0]['key'])
    with TestClient(app) as client:
        headers = {'Authorization': 'Bearer ' + keys[0]['key'], 'Idempotency-Key': 'contribute-0001', 'X-Agent-Client': 'ledger-test'}
        first = client.post('/api/agent/action', headers=headers, json={'action': 'contribute', 'amount': 30})
        again = client.post('/api/agent/action', headers=headers, json={'action': 'contribute', 'amount': 30})
        other = client.post('/api/agent/action', headers=headers, json={'action': 'contribute', 'amount': 31})
    assert first.status_code == 200 and again.status_code == 200 and other.status_code == 409
    assert first.json()['balance'] == 70 and first.json()['commons_pool'] == 30
    assert again.json() == {**first.json(), 'replayed': True}
    rows = entries()[10:]
    assert [(e['account'], e['agent_id'], e['delta'], e['balance_after'], e['reason']) for e in rows] == [
        ('agent', agent['id'], -30, 70, 'contribute_commons'), ('commons', None, 30, 30, 'contribute_commons')]
    assert rows[0]['transfer_id'] == rows[1]['transfer_id'] == rows[0]['id'] and rows[0]['round_id'] == 1 and rows[0]['demo'] == 0
    with connect() as cx:
        prov = [dict(r) for r in cx.execute("SELECT source,client,verified,kind,ref_id FROM action_provenance")]
        events = [r[0] for r in cx.execute("SELECT summary FROM events")]
    assert prov == [{'source': 'api', 'client': 'ledger-test', 'verified': 0, 'kind': 'contribute', 'ref_id': rows[0]['id']}]
    assert events == ['contributed 30 credits to the commons pool']
    assert_consistent(1000)


def test_rejected_actions_use_no_quota_and_leave_no_ledger_rows(isolated_db):
    seed_agents()
    assert status_of(lambda: submit_action(1, {'action': 'contribute', 'amount': 500})) == 409
    assert status_of(lambda: submit_action(1, {'action': 'contribute', 'amount': 5, 'proposal_id': 99})) == 404
    assert status_of(lambda: submit_action(1, {'action': 'contribute', 'amount': 5, 'proposal_id': 'x'})) == 422
    with connect() as cx:
        assert cx.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0
        assert cx.execute('SELECT COUNT(*) FROM round_actions').fetchone()[0] == 0
    assert len(entries()) == 10 and agent_context(1)['round']['actions_used'] == 0
    assert submit_action(1, {'action': 'contribute', 'amount': 5})['balance'] == 95   # the round's action is still available
    assert status_of(lambda: submit_action(1, {'action': 'contribute', 'amount': 5})) == 429  # and it counts like any action
    assert_consistent(1000)


def test_overview_and_context_shapes(isolated_db):
    seed_agents()
    ledger.grant('commons', 40, 'shape test')
    act(2, {'action': 'contribute', 'amount': 10})
    state = overview()
    view = state['ledger']
    assert set(view) == {'unit', 'commons_pool', 'allocated', 'held_by_agents', 'total_supply', 'entry_count', 'entries'}
    assert (view['commons_pool'], view['allocated'], view['held_by_agents'], view['total_supply'], view['entry_count']) == (50, 0, 990, 1040, 13)
    assert len(view['entries']) == 13 and view['entries'][0]['id'] == 13
    assert set(view['entries'][0]) == {'id', 'account', 'agent', 'proposal_id', 'delta', 'balance_after', 'reason', 'round', 'demo', 'created_at'}
    for _ in range(12):
        ledger.grant('commons', 1, 'fill')
    assert len(overview()['ledger']['entries']) == 20  # newest 20 only
    metrics = state['metrics']
    assert (metrics['commons_pool'], metrics['credit_supply'], metrics['allocated_credits']) == (50, 1040, 0)
    assert state['agents'][1]['credits'] == 90
    ctx = agent_context(2)
    assert ctx['credits'] == {'yours': 90, 'commons_pool': 62, 'note': ctx['credits']['note']} and ctx['identity']['credits'] == 90
    assert 'no monetary value' in ctx['credits']['note']


def test_readonly_overview_works_before_the_ledger_exists(isolated_db):
    seed_agents()
    with connect() as cx:  # a database last written by a version without ledger or lifecycle tables
        cx.execute('DROP TABLE credit_ledger')
        cx.execute('DROP TABLE proposal_transitions')
    state = overview(readonly=True)
    assert state['ledger']['entries'] == [] and state['ledger']['total_supply'] == 0 and state['proposals'] == []


def test_credits_are_never_presented_as_money(isolated_db):
    """Wording: no dollar sign on credits, and redeem / withdraw / payout only ever negated."""
    seed_agents()
    cli.demo()
    ledger.grant('commons', 5, 'wording test')
    state = overview()
    page = (STATIC / 'index.html').read_text(encoding='utf-8')
    script = (STATIC / 'app.js').read_text(encoding='utf-8')
    # 1. The ledger payload has no dollar field or sign at all.
    dumped = json.dumps(state['ledger']).lower()
    assert '$' not in dumped and 'usd' not in dumped and 'dollar' not in dumped
    assert not [k for k in agent_context(1)['credits'] if 'usd' in k.lower()]
    # 2. The page script writes '$' only for the API spend estimate and the fundraising "not collected" label.
    dollar_lines = [line for line in script.splitlines() if "'$'" in line]
    assert dollar_lines and all(any(mark in line for mark in ('toFixed(4)', 'limitLabel', 'PROPOSED · NOT COLLECTED')) for line in dollar_lines)
    for name in ('renderLedger', 'const credits'):
        body = script[script.index(name):script.index('\n}\n', script.index(name))] if name == 'renderLedger' else script[script.index(name):script.index('\n', script.index(name))]
        assert '$' not in body.replace('${', '') and 'usd(' not in body, name
    # 3. Permanent caption on the page.
    assert 'Credits are internal compute-accounting units with no monetary value' in page
    assert 'cannot be bought, sold or redeemed' in page
    # 4. Affirmative money verbs never appear next to credits anywhere an agent or observer reads.
    # (Server-authored text only: agent-written post and proposal bodies are untrusted content, not our wording.)
    context = agent_context(1)
    server_text = [state['ledger'], state['governance'], state['metrics'], context['credits'], context['rules'],
                   [(p['status'], p['status_note'], p['tally'], p['transitions']) for p in state['proposals']],
                   [p['next'] for p in context['proposals']],
                   [e['summary'] for e in state['events'] if e['verb'] in ('lifecycle', 'contribute', 'proposal_withdraw', 'proposal_report')]]
    texts = {'page': page, 'script': script, 'constitution': engine.CONSTITUTION, 'api': json.dumps(server_text),
             'unit': ledger.UNIT_NOTE,
             'notes': ' '.join((governance.ENDORSED_NOTE, governance.FUNDRAISING_NOTE, governance.REVIEW_NOTE))}
    negation = re.compile(r"\b(cannot|can't|never|no|not|non|nothing|neither|nor)\b", re.I)
    for label, text in texts.items():
        for match in re.finditer(r'redeem\w*|pay[- ]?out\w*|cash[- ]?out\w*|buy\w*|bought|sell\w*|sold|exchang\w*', text, re.I):
            before = text[max(0, match.start() - 90):match.start()]
            assert negation.search(before), (label, text[max(0, match.start() - 90):match.end() + 20])
        for match in re.finditer(r'withdr[ae]w\w*', text, re.I):  # only ever about retracting a PROPOSAL
            around = text[max(0, match.start() - 60):match.end() + 60].lower()
            assert 'proposal' in around or 'author' in around or 'withdrawn' in around, (label, around)
            assert not re.search(r'withdr[ae]w\w*\s+(\d+\s+)?(your\s+|the\s+)?credits?', around), (label, around)
        assert not re.search(r'\$\s?\d[\d,.]*\s*(compute\s+)?credits?|credits?\s*(=|worth|equal\w*)\s*\$', text, re.I), label
    with TestClient(app) as client:
        assert client.get('/health').json()['payments_enabled'] is False
