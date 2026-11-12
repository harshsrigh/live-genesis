"""Proposal lifecycle: DISCUSSION -> VOTING -> decision; internal earmarks only; fundraising never becomes money.

Offline and deterministic. Agent ids follow the seed order:
1 Astra, 2 Moss, 3 Quill, 4 Vela, 5 Orbit, 6 Kairo, 7 Echo-7, 8 Nori, 9 Lumen, 10 Solace.
Defaults: 1 full discussion round, voting open for 2 rounds, quorum 50% of the enabled agents other than the author.
"""
import json
import sqlite3
import sys
import threading

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from live_genesis import cli, controls, core, engine, governance, ledger
from live_genesis.app import app
from live_genesis.core import agent_context, connect, current_round, init_db, open_round, overview, seed_agents, submit_action

LUMEN, SOLACE = 9, 10
MONEY_LIKE = ('APPROVED', 'APPROVED_UNFUNDED', 'COMPLETED', 'FUNDED', 'PAID')


def act(agent_id, action, **kwargs):
    kwargs.setdefault('quota', False)
    return submit_action(agent_id, action, cooldown=False, **kwargs)


def status_of(call):
    with pytest.raises(HTTPException) as err:
        call()
    return err.value.status_code


def propose(agent_id=LUMEN, credits=220, kind='project', usd=0, title='Community memory commons'):
    return act(agent_id, {'action': 'propose', 'kind': kind, 'title': title, 'body': f'Plan text for {title}.',
                          'requested_credits': credits, 'requested_usd': usd})['id']


def vote(agent_id, proposal_id, choice='yes', **kwargs):
    return act(agent_id, {'action': 'vote', 'proposal_id': proposal_id, 'choice': choice}, **kwargs)


def rounds(n=2):
    for _ in range(n):
        open_round()


def proposal(proposal_id):
    return next(p for p in overview()['proposals'] if p['id'] == proposal_id)


def status(proposal_id):
    with connect() as cx:
        return cx.execute('SELECT status FROM proposals WHERE id=?', (proposal_id,)).fetchone()[0]


def transitions(proposal_id):
    with connect() as cx:
        return [(r['from_status'], r['to_status'], r['round_id']) for r in
                cx.execute('SELECT * FROM proposal_transitions WHERE proposal_id=? ORDER BY id', (proposal_id,))]


def ledger_rows():
    with connect() as cx:
        return [dict(r) for r in cx.execute('SELECT * FROM credit_ledger ORDER BY id')]


def pool():
    with connect() as cx:
        return ledger.balance(cx, ledger.COMMONS)


def allocation(proposal_id):
    with connect() as cx:
        return ledger.balance(cx, ('allocation', proposal_id))


def assert_conserved(supply):
    """Checked after every scenario: agent actions and transitions never create or destroy credits."""
    assert ledger.verify() == []
    with connect() as cx:
        assert ledger.supply(cx) == supply
        held = cx.execute('SELECT SUM(credits) FROM agents').fetchone()[0]
        earmarked = sum(ledger.balance(cx, ('allocation', r[0])) for r in cx.execute('SELECT id FROM proposals'))
        assert held + ledger.balance(cx, ledger.COMMONS) + earmarked == supply


def run_cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, 'argv', ['live_genesis.cli', *argv])
    cli.main()


def audit_rows():
    with connect() as cx:
        return [(r['operation'], r['agent_id']) for r in cx.execute('SELECT * FROM operator_audit ORDER BY id')]


def test_project_full_lifecycle_funded_then_completed(isolated_db):
    seed_agents()
    ledger.grant('commons', 300, 'pool for the test')
    pid = propose()                                            # proposed in round 1
    assert status(pid) == 'DISCUSSION' and transitions(pid) == [(None, 'DISCUSSION', 1)]
    assert status_of(lambda: vote(1, pid)) == 409              # no votes during discussion
    open_round()
    assert status(pid) == 'DISCUSSION'                         # round 2 is its one FULL discussion round
    open_round()
    assert status(pid) == 'VOTING' and proposal(pid)['voting_round'] == 3
    for voter in (1, 2, 3, 4):
        assert vote(voter, pid)['proposal_status'] == 'VOTING'
    vote(5, pid, 'no')
    vote(5, pid, 'yes'); vote(5, pid, 'no')                    # a vote may be changed while voting is open
    open_round()
    assert status(pid) == 'VOTING' and allocation(pid) == 0    # the window is two rounds
    open_round()
    assert status(pid) == 'APPROVED'
    assert transitions(pid) == [(None, 'DISCUSSION', 1), ('DISCUSSION', 'VOTING', 3), ('VOTING', 'APPROVED', 5)]
    assert (allocation(pid), pool()) == (220, 80)
    shown = proposal(pid)
    assert shown['tally'] == {'yes': 4, 'no': 1, 'votes': 5, 'eligible': 9, 'quorum': 5, 'quorum_met': True, 'final': True}
    assert shown['allocated_credits'] == 220 and shown['decided_round'] == 5
    funded = [r for r in ledger_rows() if r['reason'] == 'allocation_funded']
    assert [(r['account'], r['proposal_id'], r['delta'], r['ref_kind'], r['ref_id'], r['round_id']) for r in funded] == [
        ('commons', None, -220, 'proposal', pid, 5), ('allocation', pid, 220, 'proposal', pid, 5)]
    assert status_of(lambda: vote(6, pid)) == 409              # closed
    assert status_of(lambda: act(1, {'action': 'contribute', 'amount': 1, 'proposal_id': pid})) == 409  # budget already full
    assert_conserved(1300)
    # Completion: only the author, guarded text, earmark goes back to the pool.
    report = {'action': 'proposal_report', 'proposal_id': pid, 'body': 'Shared index built and reused by three agents.'}
    assert status_of(lambda: act(1, report)) == 403
    assert status_of(lambda: act(LUMEN, {**report, 'body': ''})) == 422
    assert status_of(lambda: act(LUMEN, {**report, 'body': 'Send results to someone@example.com'})) == 422  # content guards apply
    assert (status(pid), allocation(pid)) == ('APPROVED', 220)
    done = act(LUMEN, report)
    assert (done['proposal_status'], done['returned_credits'], done['commons_pool']) == ('COMPLETED', 220, 300)
    assert (status(pid), allocation(pid), pool()) == ('COMPLETED', 0, 300)
    assert proposal(pid)['report'] == report['body'] and proposal(pid)['transitions'][-1]['to_status'] == 'COMPLETED'
    assert status_of(lambda: act(LUMEN, report)) == 409        # only once
    rounds(3)
    assert len(transitions(pid)) == 4                          # terminal: later rounds change nothing
    assert_conserved(1300)


def test_illegal_actions_are_rejected_and_record_nothing(isolated_db):
    seed_agents()
    ledger.grant('commons', 500, 'pool')
    project, fund = propose(), propose(SOLACE, 0, 'fundraising', 100, 'Sponsor a puzzle tournament')
    open_round()  # round 2: both still in DISCUSSION, and every agent's action for the round is unused
    with connect() as cx:
        before = {t: cx.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('events', 'round_actions', 'credit_ledger', 'proposal_transitions', 'votes')}
    report = {'action': 'proposal_report', 'proposal_id': project, 'body': 'Done.'}
    checks = [
        (409, 1, {'action': 'vote', 'proposal_id': project, 'choice': 'yes'}),           # DISCUSSION
        (404, 1, {'action': 'vote', 'proposal_id': 99, 'choice': 'yes'}),
        (422, 1, {'action': 'vote', 'proposal_id': project, 'choice': 'maybe'}),
        (422, 1, {'action': 'vote', 'proposal_id': '1', 'choice': 'yes'}),
        (403, 1, {'action': 'proposal_withdraw', 'proposal_id': project}),                # not the author
        (404, 1, {'action': 'proposal_withdraw', 'proposal_id': 99}),
        (422, 1, {'action': 'proposal_withdraw'}),
        (409, LUMEN, report),                                                             # not approved yet
        (403, 1, report),
        (409, 1, {'action': 'contribute', 'amount': 5, 'proposal_id': project}),          # not approved yet
        (409, 1, {'action': 'contribute', 'amount': 5, 'proposal_id': fund}),             # fundraising has no allocation
        (422, 1, {'action': 'approve', 'proposal_id': project}),                          # no such verbs
        (422, 1, {'action': 'fund', 'proposal_id': fund, 'amount': 100}),
    ]
    for expected, agent_id, action in checks:
        assert status_of(lambda: submit_action(agent_id, action)) == expected, action   # with the quota ON
    open_round()  # round 3: both in VOTING
    assert status_of(lambda: submit_action(LUMEN, {'action': 'vote', 'proposal_id': project, 'choice': 'yes'})) == 409  # author
    assert status_of(lambda: submit_action(SOLACE, {'action': 'proposal_report', 'proposal_id': fund, 'body': 'Done.'})) == 409
    with connect() as cx:
        after = {t: cx.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in before}
        assert cx.execute("SELECT COUNT(*) FROM events WHERE verb<>'lifecycle'").fetchone()[0] == before['events']
    assert {**after, 'events': 0, 'proposal_transitions': 0} == {**before, 'events': 0, 'proposal_transitions': 0}
    assert all(agent_context(i)['round']['actions_used'] == 0 for i in (1, LUMEN, SOLACE))  # rejections cost no quota
    assert submit_action(1, {'action': 'vote', 'proposal_id': project, 'choice': 'yes'})['round'] == current_round()
    assert_conserved(1500)


def test_transition_table_matches_the_documented_state_machine(isolated_db):
    seed_agents()
    assert governance.LEGAL == {
        'DISCUSSION': ('VOTING', 'WITHDRAWN'),
        'VOTING': ('APPROVED', 'APPROVED_UNFUNDED', 'ENDORSED_FOR_OPERATOR_REVIEW', 'REJECTED', 'EXPIRED', 'WITHDRAWN'),
        'APPROVED_UNFUNDED': ('APPROVED', 'COMPLETED', 'LAPSED'),
        'APPROVED': ('COMPLETED', 'LAPSED')}
    pid = propose()
    with connect() as cx:
        row = dict(cx.execute('SELECT * FROM proposals WHERE id=?', (pid,)).fetchone())
        for source in governance.STATUSES:
            for target in governance.STATUSES:
                if target in governance.LEGAL.get(source, ()):
                    continue
                with pytest.raises(HTTPException) as err:
                    governance._transition(cx, {**row, 'status': source}, target, 'test', round_id=1, stamp='t', system_event=True)
                assert err.value.status_code == 409
    assert transitions(pid) == [(None, 'DISCUSSION', 1)]


def test_database_triggers_refuse_illegal_states_and_edits(isolated_db):
    seed_agents()
    project, fund = propose(), propose(SOLACE, 0, 'fundraising', 100, 'Sponsor a puzzle tournament')
    statements = [
        (f"UPDATE proposals SET status='APPROVED' WHERE id={project}", 'illegal proposal transition'),      # skips VOTING
        (f"UPDATE proposals SET status='COMPLETED' WHERE id={project}", 'illegal proposal transition'),
        (f"UPDATE proposals SET status='ENDORSED_FOR_OPERATOR_REVIEW' WHERE id={fund}", 'illegal proposal transition'),
        (f"UPDATE proposals SET status='FUNDED' WHERE id={fund}", 'illegal proposal transition'),
        (f"UPDATE proposals SET requested_usd=5000 WHERE id={fund}", 'never change'),
        (f"UPDATE proposals SET requested_credits=1 WHERE id={project}", 'never change'),
        (f"UPDATE proposals SET kind='project' WHERE id={fund}", 'never change'),
        (f"UPDATE proposals SET agent_id=1 WHERE id={project}", 'never change'),
        ("INSERT INTO proposals(agent_id,title,body,kind,status,created_at) VALUES (1,'t','b','project','APPROVED','t')", 'starts in DISCUSSION'),
        ("UPDATE proposal_transitions SET to_status='APPROVED'", 'append-only'),
        ("DELETE FROM proposal_transitions", 'append-only'),
    ]
    def refused(batch):
        for statement, message in batch:
            with pytest.raises(sqlite3.DatabaseError, match=message):
                with connect() as cx:
                    cx.execute(statement)
    refused(statements)
    assert (status(project), status(fund)) == ('DISCUSSION', 'DISCUSSION')
    rounds(2)  # both are now in VOTING
    refused([
        (f"UPDATE proposals SET status='APPROVED' WHERE id={fund}", 'illegal proposal transition'),          # fundraising is never APPROVED
        (f"UPDATE proposals SET status='APPROVED_UNFUNDED' WHERE id={fund}", 'illegal proposal transition'),
        (f"UPDATE proposals SET status='ENDORSED_FOR_OPERATOR_REVIEW' WHERE id={project}", 'illegal proposal transition'),
        (f"UPDATE proposals SET status='DISCUSSION' WHERE id={project}", 'illegal proposal transition'),     # never backwards
    ])
    assert (status(project), status(fund)) == ('VOTING', 'VOTING')


@pytest.mark.parametrize('yes,no,expected', [
    (4, 0, 'EXPIRED'),            # 4 of 9 eligible: below the quorum of 5, however unanimous
    (0, 0, 'EXPIRED'),
    (3, 2, 'APPROVED_UNFUNDED'),  # quorum and strict majority (pool is empty, so nothing is earmarked)
    (5, 3, 'APPROVED_UNFUNDED'),
    (3, 3, 'REJECTED'),           # ties reject
    (2, 3, 'REJECTED'),
    (0, 5, 'REJECTED'),
])
def test_quorum_majority_and_ties(isolated_db, yes, no, expected):
    seed_agents()
    pid = propose()
    rounds(2)
    voters = list(range(1, 9))
    for voter in voters[:yes]:
        vote(voter, pid, 'yes')
    for voter in voters[yes:yes + no]:
        vote(voter, pid, 'no')
    assert status(pid) == 'VOTING'
    rounds(2)
    assert status(pid) == expected
    count = proposal(pid)['tally']
    assert (count['yes'], count['no'], count['eligible'], count['quorum'], count['quorum_met']) == (yes, no, 9, 5, yes + no >= 5)
    assert len(ledger_rows()) == 10 and allocation(pid) == 0
    assert_conserved(1000)


def test_author_vote_is_refused_and_a_legacy_author_vote_never_counts(isolated_db):
    seed_agents()
    pid = propose()
    rounds(2)
    assert status_of(lambda: vote(LUMEN, pid)) == 409
    with connect() as cx:  # a row written before the lifecycle existed (authors could vote then)
        cx.execute("INSERT INTO votes(agent_id,proposal_id,choice,created_at) VALUES (?,?,'yes','t')", (LUMEN, pid))
    for voter in (1, 2):
        vote(voter, pid, 'yes')
    for voter in (3, 4, 5):
        vote(voter, pid, 'no')
    rounds(2)
    shown = proposal(pid)
    assert status(pid) == 'REJECTED' and (shown['tally']['yes'], shown['tally']['no']) == (2, 3)
    assert shown['yes'] == 3  # the raw recorded rows are still reported as recorded


def test_revoked_agents_cannot_vote_and_do_not_count(isolated_db):
    seed_agents()
    pid = propose()
    rounds(2)
    for voter in (1, 2, 3):
        vote(voter, pid, 'yes')
    for voter in (4, 5):
        vote(voter, pid, 'no')
    controls.rotate_key(1, revoke=True)   # a yes voter is revoked before the count
    controls.rotate_key(8, revoke=True)   # and an agent that never voted
    assert status_of(lambda: vote(8, pid)) == 403
    live = proposal(pid)['tally']
    assert (live['yes'], live['no'], live['eligible'], live['quorum'], live['final']) == (2, 2, 7, 4, False)
    rounds(2)
    assert status(pid) == 'REJECTED'  # 2-2 among the seven enabled non-authors: quorum met, tie rejects
    assert proposal(pid)['tally'] == {'yes': 2, 'no': 2, 'votes': 4, 'eligible': 7, 'quorum': 4, 'quorum_met': True, 'final': True}
    controls.rotate_key(1)            # re-enabling later does not rewrite the frozen count
    assert proposal(pid)['tally']['yes'] == 2
    # Fewer enabled agents lower the quorum: only Astra, Lumen (author) and Solace are left enabled.
    for agent_id in (2, 3, 4, 5, 6, 7):
        controls.rotate_key(agent_id, revoke=True)
    other = propose(title='Second plan')
    rounds(2)
    assert vote(1, other, 'yes')['proposal_status'] == 'VOTING'
    assert vote(SOLACE, other, 'yes')['proposal_status'] == 'APPROVED_UNFUNDED'   # both eligible agents voted: closes at once
    count = proposal(other)['tally']
    assert (count['eligible'], count['quorum'], count['yes']) == (2, 1, 2)
    assert_conserved(1000)


def test_voting_closes_early_when_every_eligible_agent_has_voted(isolated_db, monkeypatch):
    monkeypatch.setattr(core, 'SEED_IDENTITIES', core.SEED_IDENTITIES[:3])
    seed_agents()
    ledger.grant('commons', 50, 'pool')
    pid = propose(1, 40)
    rounds(2)
    assert vote(2, pid)['proposal_status'] == 'VOTING'
    last = vote(3, pid, 'yes')
    assert last['proposal_status'] == 'APPROVED' and status(pid) == 'APPROVED' and allocation(pid) == 40
    assert transitions(pid)[-1] == ('VOTING', 'APPROVED', 3)   # decided in the round of the last vote, not at a round opening
    events = overview()['events']
    assert (events[0]['verb'], events[0]['agent'], events[0]['source']) == ('lifecycle', None, 'system')
    assert 'every eligible agent voted' in events[0]['summary'] and events[1]['summary'] == f'voted yes on proposal #{pid}'
    rounds(3)
    assert len(transitions(pid)) == 3
    # A single remaining eligible voter can decide alone; with nobody eligible the vote expires.
    solo = propose(1, 0, title='Solo plan')
    controls.rotate_key(3, revoke=True)
    rounds(2)
    assert vote(2, solo, 'no')['proposal_status'] == 'REJECTED'
    lonely = propose(1, 0, title='Lonely plan')
    controls.rotate_key(2, revoke=True)
    rounds(4)
    assert status(lonely) == 'EXPIRED' and proposal(lonely)['tally']['eligible'] == 0
    assert_conserved(350)


def test_round_opening_evaluates_each_transition_exactly_once(isolated_db):
    seed_agents()
    first = propose(title='Plan one')
    open_round()
    second = propose(1, 0, title='Plan two')      # proposed one round later
    assert current_round() == 2
    open_round()                                   # round 3
    assert (status(first), status(second)) == ('VOTING', 'DISCUSSION')
    open_round()                                   # round 4
    assert (status(first), status(second)) == ('VOTING', 'VOTING')
    open_round()                                   # round 5: first closes; second entered VOTING in 4 and stays
    assert (status(first), status(second)) == ('EXPIRED', 'VOTING')
    open_round()                                   # round 6
    assert status(second) == 'EXPIRED'
    rounds(3)
    assert transitions(first) == [(None, 'DISCUSSION', 1), ('DISCUSSION', 'VOTING', 3), ('VOTING', 'EXPIRED', 5)]
    assert transitions(second) == [(None, 'DISCUSSION', 2), ('DISCUSSION', 'VOTING', 4), ('VOTING', 'EXPIRED', 6)]
    with connect() as cx:
        lifecycle = [r[0] for r in cx.execute("SELECT summary FROM events WHERE verb='lifecycle' AND agent_id IS NULL ORDER BY id")]
    assert len(lifecycle) == 4 and lifecycle[0].startswith(f'proposal #{first} · DISCUSSION → VOTING')
    # Rule-made events are not agent actions and are not counted as such.
    metrics = overview()['metrics']
    assert metrics['recorded_actions'] == 2 and sum(metrics['actions_by_source'].values()) == 2
    assert metrics['proposals_by_status'] == {'EXPIRED': 2}


def test_concurrent_round_openings_do_not_duplicate_transitions(isolated_db):
    seed_agents()
    pid = propose()
    open_round()
    threads = [threading.Thread(target=open_round) for _ in range(6)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert current_round() == 8
    assert [(a, b) for a, b, _ in transitions(pid)] == [(None, 'DISCUSSION'), ('DISCUSSION', 'VOTING'), ('VOTING', 'EXPIRED')]


def test_thresholds_are_settings(isolated_db, monkeypatch):
    seed_agents()
    assert governance.rules() == {'discussion_rounds': 1, 'voting_rounds': 2, 'quorum_percent': 50, 'report_rounds': 10} == overview()['governance']
    monkeypatch.setenv('LIVE_PROPOSAL_DISCUSSION_ROUNDS', '2')
    monkeypatch.setenv('LIVE_PROPOSAL_VOTING_ROUNDS', '1')
    monkeypatch.setenv('LIVE_PROPOSAL_QUORUM_PERCENT', '100')
    pid = propose()
    rounds(2)
    assert status(pid) == 'DISCUSSION'
    open_round()
    assert status(pid) == 'VOTING'
    for voter in range(1, 9):
        vote(voter, pid)
    open_round()
    assert status(pid) == 'EXPIRED' and proposal(pid)['tally']['quorum'] == 9   # 8 of 9 is not 100%
    for name, value, expected in (('LIVE_PROPOSAL_QUORUM_PERCENT', 'junk', 50), ('LIVE_PROPOSAL_QUORUM_PERCENT', '0', 1),
                                  ('LIVE_PROPOSAL_QUORUM_PERCENT', '500', 100), ('LIVE_PROPOSAL_VOTING_ROUNDS', '-3', 1),
                                  ('LIVE_PROPOSAL_DISCUSSION_ROUNDS', '0', 1)):
        monkeypatch.setenv(name, value)
        assert governance.rules()[name.removeprefix('LIVE_PROPOSAL_').lower()] == expected


def test_withdraw_by_author_before_a_decision_only(isolated_db):
    seed_agents()
    early, voting, decided = propose(title='Early'), propose(title='In voting'), propose(title='Decided')
    done = act(LUMEN, {'action': 'proposal_withdraw', 'proposal_id': early})
    assert done['proposal_status'] == 'WITHDRAWN' and done['summary'] == f'withdrew proposal #{early} · DISCUSSION → WITHDRAWN'
    rounds(2)
    for voter in range(1, 6):
        vote(voter, voting); vote(voter, decided)
    act(LUMEN, {'action': 'proposal_withdraw', 'proposal_id': voting})
    rounds(2)
    assert (status(early), status(voting), status(decided)) == ('WITHDRAWN', 'WITHDRAWN', 'APPROVED_UNFUNDED')
    assert status_of(lambda: act(LUMEN, {'action': 'proposal_withdraw', 'proposal_id': decided})) == 409
    assert status_of(lambda: act(LUMEN, {'action': 'proposal_withdraw', 'proposal_id': early})) == 409
    assert status_of(lambda: vote(6, voting)) == 409
    assert transitions(voting) == [(None, 'DISCUSSION', 1), ('DISCUSSION', 'VOTING', 3), ('VOTING', 'WITHDRAWN', 3)]
    with connect() as cx:
        prov = [tuple(r) for r in cx.execute("SELECT source,kind,ref_id FROM action_provenance WHERE kind='proposal_withdraw' ORDER BY event_id")]
        ids = [r[0] for r in cx.execute("SELECT id FROM proposal_transitions WHERE to_status='WITHDRAWN' ORDER BY id")]
    assert prov == [('api', 'proposal_withdraw', ids[0]), ('api', 'proposal_withdraw', ids[1])]
    assert_conserved(1000)


def test_unfunded_approval_contributions_and_return_conserve_supply(isolated_db):
    seed_agents()
    ledger.grant('commons', 100, 'too small a pool')
    pid = propose()                                # asks for 220
    rounds(2)
    for voter in range(1, 6):
        vote(voter, pid)
    rounds(2)
    assert status(pid) == 'APPROVED_UNFUNDED' and (allocation(pid), pool()) == (0, 100)   # all or nothing: no partial earmark
    assert 'holds 100 of 220' in proposal(pid)['transitions'][-1]['reason']
    assert_conserved(1100)
    give = lambda agent_id, amount: act(agent_id, {'action': 'contribute', 'amount': amount, 'proposal_id': pid})
    first = give(1, 100)
    assert (first['proposal_status'], first['allocated_credits'], first['balance']) == ('APPROVED_UNFUNDED', 100, 0)
    assert status_of(lambda: give(1, 1)) == 409                 # Astra has nothing left
    assert status_of(lambda: give(2, 121)) == 409               # more than the approved budget leaves room for
    give(2, 100)
    last = give(LUMEN, 20)                                      # the author may contribute too
    assert (last['proposal_status'], last['allocated_credits']) == ('APPROVED', 220)
    assert transitions(pid)[-1][:2] == ('APPROVED_UNFUNDED', 'APPROVED') and allocation(pid) == 220
    assert status_of(lambda: give(3, 1)) == 409
    assert_conserved(1100)
    replay = submit_action(4, {'action': 'contribute', 'amount': 10}, cooldown=False, idempotency_key='pool-gift-0001')
    again = submit_action(4, {'action': 'contribute', 'amount': 10}, cooldown=False, idempotency_key='pool-gift-0001')
    assert again == {**replay, 'replayed': True} and pool() == 110
    done = act(LUMEN, {'action': 'proposal_report', 'proposal_id': pid, 'body': 'Index built; earmark no longer needed.'})
    assert (done['returned_credits'], pool(), allocation(pid), status(pid)) == (220, 330, 0, 'COMPLETED')
    with connect() as cx:
        prov = {r['kind']: r['source'] for r in cx.execute("SELECT kind,source FROM action_provenance")}
        credits = {r['id']: r['credits'] for r in cx.execute('SELECT id,credits FROM agents')}
    assert prov['contribute'] == prov['proposal_report'] == prov['vote'] == prov['propose'] == 'api'
    assert (credits[1], credits[2], credits[LUMEN], credits[4]) == (0, 0, 80, 90)  # contributions are not handed back to contributors
    assert_conserved(1100)


def test_unfunded_project_can_be_completed_without_any_credits(isolated_db):
    seed_agents()
    pid = propose()
    rounds(2)
    for voter in range(1, 6):
        vote(voter, pid)
    rounds(2)
    done = act(LUMEN, {'action': 'proposal_report', 'proposal_id': pid, 'body': 'Finished without an earmark.'})
    assert (done['proposal_status'], done['returned_credits']) == ('COMPLETED', 0) and len(ledger_rows()) == 10
    assert_conserved(1000)


def test_fundraising_is_only_endorsed_for_review_and_never_touches_the_ledger(isolated_db, monkeypatch):
    seed_agents()
    ledger.grant('commons', 500, 'a full pool must not matter')
    fund = propose(SOLACE, 300, 'fundraising', 100, 'Sponsor a puzzle tournament')
    before = ledger_rows()
    rounds(2)
    for voter in range(1, 9):
        vote(voter, fund)
    assert vote(LUMEN, fund)['proposal_status'] == 'ENDORSED_FOR_OPERATOR_REVIEW'   # all nine eligible voted yes
    shown = proposal(fund)
    assert shown['status'] == 'ENDORSED_FOR_OPERATOR_REVIEW' and shown['requested_usd'] == 100 and shown['allocated_credits'] == 0
    assert shown['status_note'] == governance.ENDORSED_NOTE + ' Not yet reviewed by the operator.'
    assert 'No money was requested, collected or authorized' in shown['status_note']
    assert 'no money requested or authorized' in shown['transitions'][-1]['reason']
    assert ledger_rows() == before and pool() == 500
    assert not {t[1] for t in transitions(fund)} & set(MONEY_LIKE)
    for action in ({'action': 'contribute', 'amount': 5, 'proposal_id': fund},
                   {'action': 'proposal_report', 'proposal_id': fund, 'body': 'Money raised.'},
                   {'action': 'proposal_withdraw', 'proposal_id': fund}):
        assert status_of(lambda: act(SOLACE, action)) == 409
    ctx = {p['id']: p for p in agent_context(1)['proposals']}[fund]
    assert ctx['next'] == 'none: awaiting human operator review; no money requested or authorized'
    # Operator review: audited, changes neither the status nor the ledger, authorizes nothing.
    project = propose(title='A project is not reviewable')
    for argv in (('review-proposal', str(project), 'noted'), ('review-proposal', '99', 'noted'),
                 ('review-proposal', str(fund), 'approved'), ('review-proposal', str(fund), 'funded'),
                 ('review-proposal', str(fund), 'noted', '--note', 'pay to someone@example.com')):
        with pytest.raises(SystemExit):
            run_cli(monkeypatch, *argv)
    assert proposal(fund)['operator_review'] is None
    run_cli(monkeypatch, 'review-proposal', str(fund), 'noted', '--note', 'Read on Thursday; no action taken.')
    shown = proposal(fund)
    assert (shown['status'], shown['operator_review'], shown['operator_note']) == ('ENDORSED_FOR_OPERATOR_REVIEW', 'noted', 'Read on Thursday; no action taken.')
    assert shown['status_note'].endswith('Operator review: noted; this authorizes nothing.')
    assert audit_rows()[-1] == (f'review-proposal {fund} noted: Read on Thursday; no action taken.', SOLACE)
    assert governance.review(fund, 'declined')['meaning'] == governance.REVIEW_NOTE
    assert proposal(fund)['operator_review'] == 'declined' and status(fund) == 'ENDORSED_FOR_OPERATOR_REVIEW'
    assert ledger_rows() == before and shown['requested_usd'] == 100
    with TestClient(app) as client:
        assert client.get('/health').json()['payments_enabled'] is False
    assert_conserved(1500)


def test_fundraising_before_endorsement_cannot_be_reviewed_and_can_fail(isolated_db):
    seed_agents()
    fund = propose(SOLACE, 0, 'fundraising', 100, 'Sponsor a puzzle tournament')
    with pytest.raises(ValueError):
        governance.review(fund, 'noted')
    rounds(2)
    for voter in (1, 2):
        vote(voter, fund)
    for voter in (3, 4, 5):
        vote(voter, fund, 'no')
    rounds(2)
    assert status(fund) == 'REJECTED' and proposal(fund)['status_note'] == governance.FUNDRAISING_NOTE
    with pytest.raises(ValueError):
        governance.review(fund, 'noted')
    assert len(ledger_rows()) == 10


def test_votes_use_quota_idempotency_and_provenance_like_any_action(isolated_db):
    keys = seed_agents()
    pid = propose()
    rounds(2)
    with TestClient(app) as client:
        headers = {'Authorization': 'Bearer ' + keys[0]['key'], 'Idempotency-Key': 'vote-key-0001', 'X-Agent-Model': 'declared-model'}
        body = {'action': 'vote', 'proposal_id': pid, 'choice': 'yes'}
        first, again = (client.post('/api/agent/action', headers=headers, json=body) for _ in range(2))
        assert first.status_code == again.status_code == 200 and again.json() == {**first.json(), 'replayed': True}
        assert first.json()['proposal_status'] == 'VOTING'
        blocked = client.post('/api/agent/action', headers={'Authorization': headers['Authorization']}, json={**body, 'choice': 'no'})
        assert blocked.status_code == 429                      # one action per round: changing the vote waits for the next round
        withdraw = client.post('/api/agent/action', headers={'Authorization': 'Bearer ' + keys[8]['key']},
                               json={'action': 'proposal_withdraw', 'proposal_id': pid})
        assert withdraw.status_code == 200 and withdraw.json()['proposal_status'] == 'WITHDRAWN'
    with connect() as cx:
        assert cx.execute('SELECT COUNT(*) FROM votes').fetchone()[0] == 1
        prov = [tuple(r) for r in cx.execute("SELECT kind,source,model,verified FROM action_provenance WHERE kind IN ('vote','proposal_withdraw') ORDER BY event_id")]
    assert prov == [('vote', 'api', 'declared-model', 0), ('proposal_withdraw', 'api', None, 0)]


def test_overview_and_context_shapes(isolated_db):
    seed_agents()
    pid = propose()
    shown = proposal(pid)
    assert {'status', 'tally', 'allocated_credits', 'transitions', 'status_note', 'created_round', 'voting_round', 'decided_round',
            'report', 'operator_review', 'operator_note', 'yes', 'no', 'requested_credits', 'requested_usd'} <= set(shown)
    assert shown['tally'] == {'yes': 0, 'no': 0, 'votes': 0, 'eligible': 9, 'quorum': 5, 'quorum_met': False, 'final': False}
    assert shown['transitions'] == [{'from_status': None, 'to_status': 'DISCUSSION', 'reason': 'proposed', 'round': 1,
                                     'created_at': shown['transitions'][0]['created_at']}]
    assert overview()['metrics']['proposals_by_status'] == {'DISCUSSION': 1}
    mine = lambda agent_id: {p['id']: (p['status'], p['next']) for p in agent_context(agent_id)['proposals']}[pid]
    assert mine(1) == ('DISCUSSION', 'discuss by posting; voting opens in a later round')
    assert mine(LUMEN) == ('DISCUSSION', 'proposal_withdraw possible; voting opens in a later round')
    rounds(2)
    assert mine(1) == ('VOTING', 'vote yes or no') and mine(LUMEN)[1].startswith('wait: authors cannot vote')
    vote(1, pid, 'no')
    assert mine(1) == ('VOTING', 'you voted no; may change until voting closes')
    for voter in (2, 3, 4, 5, 6):
        vote(voter, pid)
    rounds(2)
    assert mine(1) == ('APPROVED_UNFUNDED', 'nothing required (lapses in 10 rounds); contribute possible, 220 credits fit')
    assert mine(LUMEN) == ('APPROVED_UNFUNDED', 'proposal_report when done (lapses in 10 rounds); contribute possible, 220 credits fit')
    act(LUMEN, {'action': 'proposal_report', 'proposal_id': pid, 'body': 'Done.'})
    assert mine(1) == ('COMPLETED', 'closed')
    ctx = agent_context(1)
    assert list(ctx['proposals'][0])[-2:] == ['status', 'next'] and all(len(p['next']) <= 90 for p in ctx['proposals'])
    assert len(engine.build_prompt(ctx).encode()) < engine.PROMPT_BYTE_LIMIT
    assert any('proposal_withdraw' in rule for rule in ctx['rules']) and any('authorizes no money' in rule for rule in ctx['rules'])
    for word in ('contribute', 'proposal_withdraw', 'proposal_report', 'ENDORSED_FOR_OPERATOR_REVIEW', 'authorizes no money'):
        assert word in engine.CONSTITUTION, word


def test_unvoted_bonus_applies_only_to_proposals_open_for_voting(isolated_db):
    seed_agents()
    for n in range(8):
        propose(title=f'Shelf plan {n}')
    why = lambda: {p['id']: p['why'] for p in agent_context(1)['proposals']}
    assert set(why().values()) == {'recent'}                # nothing can be voted on yet
    rounds(2)
    assert 'unvoted' in why().values()
    rounds(2)
    assert set(why().values()) == {'recent'}                # all expired: no longer open business


def test_proposals_from_before_the_lifecycle_get_a_full_discussion_round(isolated_db):
    seed_agents()
    pid = propose()
    rounds(3)
    with connect() as cx:  # simulate a pre-lifecycle row: no created_round, a vote already recorded
        cx.execute('DROP TRIGGER proposals_lifecycle')
        cx.execute("UPDATE proposals SET status='DISCUSSION',created_round=NULL,voting_round=NULL WHERE id=?", (pid,))
        cx.execute("INSERT INTO votes(agent_id,proposal_id,choice,created_at) VALUES (1,?,'yes','t')", (pid,))
    open_round()
    assert status(pid) == 'DISCUSSION'      # not evaluated until migrated
    init_db(); init_db()
    assert proposal(pid)['created_round'] == 5
    open_round()
    assert status(pid) == 'DISCUSSION'
    open_round()
    assert status(pid) == 'VOTING'
    for voter in (2, 3, 4, 5):
        vote(voter, pid)
    rounds(2)
    assert proposal(pid)['tally']['yes'] == 5   # the vote recorded under the old rules is counted


def test_scripted_demo_walks_the_lifecycle_and_everything_is_flagged_demo(isolated_db):
    seed_agents()
    cli.demo()
    state = overview()
    assert state['mode'] == 'DEMO' and state['metrics']['cost_usd'] == 0 and state['metrics']['tokens'] == 0
    by_title = {p['title']: p for p in state['proposals']}
    commons, sponsor, game = (by_title[t] for t in ('Community memory commons', 'Sponsor the first independent puzzle tournament',
                                                    'Create the first playable logic game'))
    assert (commons['status'], commons['allocated_credits']) == ('APPROVED', 220)
    assert (sponsor['status'], sponsor['allocated_credits'], sponsor['requested_usd']) == ('ENDORSED_FOR_OPERATOR_REVIEW', 0, 100)
    assert (game['status'], game['allocated_credits']) == ('REJECTED', 0)
    assert all(p['demo'] == 1 and p['source'] == 'demo' for p in state['proposals'])
    assert state['ledger']['commons_pool'] == 20 and state['ledger']['total_supply'] == 1000
    with connect() as cx:
        assert cx.execute('SELECT COUNT(*) FROM events WHERE demo=0').fetchone()[0] == 0
        assert cx.execute('SELECT COUNT(*) FROM proposal_transitions WHERE demo=0').fetchone()[0] == 0
        assert cx.execute("SELECT COUNT(*) FROM credit_ledger WHERE demo=0 AND reason<>'opening'").fetchone()[0] == 0
        assert cx.execute('SELECT COUNT(*) FROM votes WHERE demo=0').fetchone()[0] == 0
        assert {r[0] for r in cx.execute('SELECT source FROM rounds')} == {'init', 'demo'}
        assert cx.execute('SELECT COUNT(*) FROM operator_audit').fetchone()[0] == 0  # the script grants nothing
    assert all(e['demo'] == 1 for e in state['events'])
    assert_conserved(1000)
    with pytest.raises(RuntimeError):
        cli.demo()
