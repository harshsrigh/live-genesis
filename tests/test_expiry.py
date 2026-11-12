"""Round-based expiry: idle Commons Challenge games (EXPIRED) and unreported approved projects (LAPSED).

Offline and deterministic. Time is counted in operator-opened rounds only. Agent ids follow the
seed order (1 Astra ... 9 Lumen, 10 Solace). Defaults: a game expires after 6 rounds without a
move; an approved project lapses 10 rounds after approval.
"""
import sys

import pytest
from fastapi import HTTPException

from live_genesis import agentkit, cli, controls, games, governance, guide, ledger, snapshot
from live_genesis.core import agent_context, connect, current_round, init_db, open_round, overview, seed_agents, submit_action

LUMEN = 9
OLD_GAMES = '''CREATE TABLE games (
    id INTEGER PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(state IN ('ACTIVE','WON','LOST')),
    attempts INTEGER NOT NULL DEFAULT 0, score INTEGER NOT NULL DEFAULT 0,
    demo INTEGER NOT NULL DEFAULT 0 CHECK(demo IN (0,1)), created_at TEXT NOT NULL
)'''


def act(agent_id, action, **kwargs):
    kwargs.setdefault('quota', False)
    return submit_action(agent_id, action, cooldown=False, **kwargs)


def refused(agent_id, action):
    with pytest.raises(HTTPException) as err:
        act(agent_id, action)
    return err.value.status_code


def rounds(n):
    for _ in range(n):
        open_round()


def until(round_id):
    rounds(round_id - current_round())
    assert current_round() == round_id


def one(sql, *args):
    with connect() as cx:
        return cx.execute(sql, args).fetchone()[0]


def game_state(gid):
    return one('SELECT state FROM games WHERE id=?', gid)


def status(pid):
    return one('SELECT status FROM proposals WHERE id=?', pid)


def pool():
    with connect() as cx:
        return ledger.balance(cx, ledger.COMMONS)


def allocation(pid):
    with connect() as cx:
        return ledger.balance(cx, ('allocation', pid))


def table_counts():
    with connect() as cx:
        return {t: cx.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                for t in ('events', 'credit_ledger', 'proposal_transitions', 'games', 'game_moves', 'rounds')}


def lifecycle_events():
    with connect() as cx:
        return [r[0] for r in cx.execute("SELECT summary FROM events WHERE verb='lifecycle' AND agent_id IS NULL ORDER BY id")]


def start_game(monkeypatch, digits=(4, 0, 7), starter=1):
    queue = list(digits)
    monkeypatch.setattr(games, 'draw_digit', lambda: queue.pop(0))
    return act(starter, {'action': 'game_start'})['game_id']


def approved_project(credits=220, pool_grant=300):
    """Proposed in round 1, voting from round 3, approved when every eligible agent has voted (round 3)."""
    if pool_grant:
        ledger.grant('commons', pool_grant, 'pool for the test')
    pid = act(LUMEN, {'action': 'propose', 'kind': 'project', 'title': 'Community memory commons',
                      'body': 'Plan text.', 'requested_credits': credits})['id']
    rounds(2)
    for voter in (1, 2, 3, 4, 5, 6, 7, 8, 10):
        act(voter, {'action': 'vote', 'proposal_id': pid, 'choice': 'yes'})
    assert one('SELECT decided_round FROM proposals WHERE id=?', pid) == 3
    return pid


# ---- Games -------------------------------------------------------------------------------
def test_game_expires_exactly_at_the_boundary(isolated_db, monkeypatch):
    seed_agents()
    gid = start_game(monkeypatch)                    # started in round 1: expires when round 7 opens
    supply = one('SELECT SUM(delta) FROM credit_ledger')
    assert agent_context(2)['games']['active'][0]['expires_in_rounds'] == 6
    until(6)
    assert game_state(gid) == 'ACTIVE' and lifecycle_events() == []
    mine = agent_context(2)['games']
    assert mine['active'][0]['expires_in_rounds'] == 1 and mine['can_start'] is False and 'expired' not in mine
    open_round()                                     # round 7
    assert game_state(gid) == 'EXPIRED'
    assert lifecycle_events() == [f'Commons Challenge #{gid} · ACTIVE → EXPIRED · expired from inactivity: '
                                  'no move for 6 round(s); seats are free']
    state = overview()
    assert state['games'][0]['state'] == 'EXPIRED' and state['games'][0]['score'] == 0
    assert state['events'][0]['source'] == 'system' and state['events'][0]['agent'] is None
    assert state['metrics']['recorded_actions'] == 1             # the rule-made event is not an agent action
    # No credits, no penalties.
    assert one('SELECT COUNT(*) FROM credit_ledger WHERE reason<>?', 'opening') == 0
    assert one('SELECT SUM(delta) FROM credit_ledger') == supply and ledger.verify() == []
    assert one('SELECT COUNT(*) FROM game_moves') == 1           # expiry is not a move


def test_a_move_resets_the_game_clock(isolated_db, monkeypatch):
    seed_agents()
    gid = start_game(monkeypatch)
    until(5)
    act(2, {'action': 'game_share', 'game_id': gid})  # round 5: now expires when round 11 opens
    assert agent_context(1)['games']['active'][0]['expires_in_rounds'] == 6
    until(10)
    assert game_state(gid) == 'ACTIVE'
    assert agent_context(1)['games']['active'][0]['expires_in_rounds'] == 1
    open_round()
    assert game_state(gid) == 'EXPIRED'
    # A rejected move does not count as activity.
    # (Agent 1 started the expired game, so it is in the start cooldown; agent 2 starts and is seated with 4 and 5.)
    assert refused(1, {'action': 'game_start'}) == 409
    gid2 = start_game(monkeypatch, starter=2)         # round 11
    until(16)
    assert refused(6, {'action': 'game_share', 'game_id': gid2}) == 403   # not seated
    assert refused(2, {'action': 'game_guess', 'game_id': gid2, 'answer': [1, 2, 3]}) == 409  # clues not shared
    open_round()
    assert game_state(gid2) == 'EXPIRED'


def test_expired_game_frees_seats_and_allows_a_new_start(isolated_db, monkeypatch):
    seed_agents()
    gid = start_game(monkeypatch)
    act(2, {'action': 'game_share', 'game_id': gid})
    assert refused(4, {'action': 'game_start'}) == 409            # blocked while it is open
    until(7)
    for agent_id in (1, 2, 3, 4):
        view = agent_context(agent_id)['games']
        # Only the agent that started the expired game waits (start cooldown, round 7 + 6).
        assert view['active'] == [] and view['can_start'] is (agent_id != 1)
        assert view.get('start_blocked_until_round') == (13 if agent_id == 1 else None)
        assert ('expired' in view) == (agent_id != 4)
    assert agent_context(3)['games']['expired'] == [
        {'id': gid, 'state': 'EXPIRED', 'next': 'none: ended without a result (inactivity or the operator), no move possible'}]
    assert all('game_share' not in hint and 'game_guess' not in hint for hint in agent_context(3)['guide']['guidance'])
    before = table_counts()
    assert refused(3, {'action': 'game_share', 'game_id': gid}) == 409
    assert refused(2, {'action': 'game_guess', 'game_id': gid, 'answer': [4, 0, 7]}) == 409
    assert table_counts() == before
    new = start_game(monkeypatch, (1, 2, 3), starter=2)          # a seat of the expired game is free again
    assert new != gid and game_state(new) == 'ACTIVE'
    assert [c['agent_id'] for c in overview()['games'][0]['clues']] == [2, 4, 5]   # peers: those seated least recently
    view = agent_context(2)['games']
    assert 'expired' not in view and view['active'][0]['id'] == new and view['active'][0]['next'] == 'game_share'
    for member in (2, 4, 5):
        act(member, {'action': 'game_share', 'game_id': new})
    assert act(2, {'action': 'game_guess', 'game_id': new, 'answer': [1, 2, 3]})['state'] == 'WON'
    rounds(8)
    assert (game_state(gid), game_state(new)) == ('EXPIRED', 'WON')   # finished games never expire


def test_a_revoked_seat_cannot_block_the_game_for_ever(isolated_db, monkeypatch):
    seed_agents()
    gid = start_game(monkeypatch)
    controls.rotate_key(2, revoke=True)               # seat 2 can never share its clue now
    until(7)
    assert game_state(gid) == 'EXPIRED'
    start_game(monkeypatch, (1, 2, 3), starter=3)     # agent 1 started the expired game: start cooldown
    assert [c['agent_id'] for c in overview()['games'][0]['clues']] == [3, 4, 5]   # never the revoked agent 2


def test_game_expiry_is_idempotent_and_the_limit_is_a_setting(isolated_db, monkeypatch):
    seed_agents()
    monkeypatch.setenv('LIVE_GAME_IDLE_ROUNDS', '2')
    assert games.idle_rounds() == 2
    gid = start_game(monkeypatch)
    open_round()
    assert game_state(gid) == 'ACTIVE'
    open_round()                                      # round 3
    assert game_state(gid) == 'EXPIRED'
    before = table_counts()
    with connect() as cx:
        cx.execute('BEGIN IMMEDIATE')
        assert games.on_round_open(cx, 3) == [] and games.on_round_open(cx, 3) == []
        assert governance.on_round_open(cx, 3) == []
    assert table_counts() == before
    rounds(4)
    assert len(lifecycle_events()) == 1
    for raw, expected in (('0', 1), ('500', 50), ('junk', 6)):
        monkeypatch.setenv('LIVE_GAME_IDLE_ROUNDS', raw)
        assert games.idle_rounds() == expected
    monkeypatch.delenv('LIVE_GAME_IDLE_ROUNDS')
    assert games.idle_rounds() == games.IDLE_ROUNDS == 6


def test_games_table_from_an_older_version_is_upgraded_in_place(isolated_db, monkeypatch):
    seed_agents()
    won = start_game(monkeypatch)
    for member in (1, 2, 3):
        act(member, {'action': 'game_share', 'game_id': won})
    act(1, {'action': 'game_guess', 'game_id': won, 'answer': [4, 0, 7]})
    open_game = start_game(monkeypatch, (1, 2, 3))
    rounds(3)
    with connect() as cx:                             # simulate the table as it was before EXPIRED existed
        cx.commit()
        cx.execute('PRAGMA foreign_keys=OFF')
        rows = cx.execute('SELECT id,state,attempts,score,demo,created_at FROM games').fetchall()
        cx.execute('DROP TABLE games')
        cx.execute(OLD_GAMES)
        cx.executemany('INSERT INTO games VALUES (?,?,?,?,?,?)', [tuple(r) for r in rows])
        cx.execute("CREATE UNIQUE INDEX games_one_active ON games(demo) WHERE state='ACTIVE'")
    children = (one('SELECT COUNT(*) FROM game_clues'), one('SELECT COUNT(*) FROM game_moves'))
    init_db(); init_db()
    with connect() as cx:
        assert [tuple(r) for r in cx.execute('SELECT id,state,attempts,score,last_move_round FROM games ORDER BY id')] == [
            (won, 'WON', 1, 3, None), (open_game, 'ACTIVE', 0, 0, 4)]     # an open game gets a full window from the upgrade
        assert cx.execute('PRAGMA foreign_key_check').fetchall() == []
        assert cx.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('games_one_active','games_rebuild')").fetchone()[0] == 1
    assert (one('SELECT COUNT(*) FROM game_clues'), one('SELECT COUNT(*) FROM game_moves')) == children
    assert refused(4, {'action': 'game_start'}) == 409             # the one-active-game index still holds
    act(4, {'action': 'game_share', 'game_id': open_game})         # moves still attach to the kept ids (seats 1, 4, 5: rotation)
    until(9)
    assert game_state(open_game) == 'ACTIVE'
    open_round()                                                   # round 10 = 4 + 6
    assert game_state(open_game) == 'EXPIRED'


# ---- Projects ----------------------------------------------------------------------------
def test_project_lapses_exactly_at_the_boundary_and_returns_exactly_the_earmark(isolated_db, monkeypatch):
    seed_agents()
    pid = approved_project()                          # approved in round 3: lapses when round 13 opens
    assert (status(pid), allocation(pid), pool()) == ('APPROVED', 220, 80)
    mine = lambda agent_id: {p['id']: (p['status'], p['next']) for p in agent_context(agent_id)['proposals']}[pid]
    assert mine(LUMEN) == ('APPROVED', 'proposal_report when done (lapses in 10 rounds)')
    until(12)
    assert (status(pid), allocation(pid), pool()) == ('APPROVED', 220, 80)
    assert mine(LUMEN)[1] == 'proposal_report when done (lapses in 1 rounds)' and mine(1)[1] == 'nothing required (lapses in 1 rounds)'
    entries = one('SELECT COUNT(*) FROM credit_ledger')
    open_round()                                      # round 13
    assert (status(pid), allocation(pid), pool()) == ('LAPSED', 0, 300)
    with connect() as cx:
        rows = [dict(r) for r in cx.execute('SELECT * FROM credit_ledger WHERE id>? ORDER BY id', (entries,))]
        steps = [(r['from_status'], r['to_status'], r['round_id'], r['reason']) for r in
                 cx.execute('SELECT * FROM proposal_transitions WHERE proposal_id=? ORDER BY id', (pid,))]
        assert ledger.supply(cx) == 1300
    assert [(r['account'], r['proposal_id'], r['delta'], r['balance_after'], r['reason'], r['ref_kind'], r['ref_id'], r['round_id'], r['demo'])
            for r in rows] == [('allocation', pid, -220, 0, 'allocation_lapsed', 'proposal', pid, 13, 0),
                               ('commons', None, 220, 300, 'allocation_lapsed', 'proposal', pid, 13, 0)]
    assert rows[0]['transfer_id'] == rows[1]['transfer_id'] == rows[0]['id']
    assert steps[-1] == ('APPROVED', 'LAPSED', 13, 'no completion report within 10 round(s) of approval; '
                                                   '220 earmarked credits returned to the commons pool')
    assert [s[1] for s in steps] == ['DISCUSSION', 'VOTING', 'APPROVED', 'LAPSED']
    assert lifecycle_events()[-1].startswith(f'proposal #{pid} · APPROVED → LAPSED · no completion report')
    assert ledger.verify() == []
    monkeypatch.setattr(sys, 'argv', ['live_genesis.cli', 'verify-ledger'])
    cli.main()
    shown = next(p for p in overview()['proposals'] if p['id'] == pid)
    assert shown['status'] == 'LAPSED' and shown['allocated_credits'] == 0 and shown['status_note'].startswith('Lapsed:')
    assert overview()['ledger']['entries'][0]['reason'] == 'allocation_lapsed'
    assert overview()['metrics']['proposals_by_status'] == {'LAPSED': 1}
    # Terminal: nothing more can be done with it, and the context says so.
    assert mine(LUMEN) == mine(1) == ('LAPSED', 'closed: lapsed without a report, no action possible')
    assert refused(LUMEN, {'action': 'proposal_report', 'proposal_id': pid, 'body': 'Late.'}) == 409
    assert refused(1, {'action': 'contribute', 'amount': 1, 'proposal_id': pid}) == 409
    assert refused(LUMEN, {'action': 'proposal_withdraw', 'proposal_id': pid}) == 409
    with pytest.raises(Exception, match='illegal proposal transition'):
        with connect() as cx:
            cx.execute("UPDATE proposals SET status='APPROVED' WHERE id=?", (pid,))


def test_lapse_is_idempotent_and_a_reported_project_never_lapses(isolated_db):
    seed_agents()
    pid = approved_project()
    done = act(LUMEN, {'action': 'propose', 'kind': 'project', 'title': 'Second plan', 'body': 'Other text.', 'requested_credits': 0})['id']
    until(5)
    for voter in (1, 2, 3, 4, 5, 6, 7, 8, 10):
        act(voter, {'action': 'vote', 'proposal_id': done, 'choice': 'yes'})
    act(LUMEN, {'action': 'proposal_report', 'proposal_id': done, 'body': 'Finished early.'})
    until(13)
    assert (status(pid), status(done)) == ('LAPSED', 'COMPLETED')
    before = table_counts()
    with connect() as cx:
        cx.execute('BEGIN IMMEDIATE')
        assert governance.on_round_open(cx, 13) == [] and governance.on_round_open(cx, 13) == []
    assert table_counts() == before
    rounds(12)
    after = table_counts()
    assert {k: after[k] - before[k] for k in after} == {'events': 0, 'credit_ledger': 0, 'proposal_transitions': 0,
                                                       'games': 0, 'game_moves': 0, 'rounds': 12}
    assert (status(pid), status(done), pool()) == ('LAPSED', 'COMPLETED', 300) and ledger.verify() == []


def test_a_revoked_author_still_lapses(isolated_db):
    seed_agents()
    pid = approved_project()
    controls.rotate_key(LUMEN, revoke=True)
    assert refused(LUMEN, {'action': 'proposal_report', 'proposal_id': pid, 'body': 'Done.'}) == 403   # cannot report any more
    until(12)
    assert (status(pid), allocation(pid)) == ('APPROVED', 220)
    open_round()
    assert (status(pid), allocation(pid), pool()) == ('LAPSED', 0, 300) and ledger.verify() == []


def test_direct_contributions_go_to_the_pool_on_lapse_as_they_do_on_a_report(isolated_db):
    seed_agents()
    pid = approved_project(pool_grant=0)              # empty pool: APPROVED_UNFUNDED, nothing earmarked
    assert (status(pid), allocation(pid), pool()) == ('APPROVED_UNFUNDED', 0, 0)
    act(1, {'action': 'contribute', 'amount': 50, 'proposal_id': pid})
    empty = act(10, {'action': 'propose', 'kind': 'project', 'title': 'No budget plan', 'body': 'Needs nothing.', 'requested_credits': 0})['id']
    until(5)
    for voter in range(1, 10):
        act(voter, {'action': 'vote', 'proposal_id': empty, 'choice': 'yes'})
    assert (status(empty), one('SELECT decided_round FROM proposals WHERE id=?', empty)) == ('APPROVED', 5)
    until(13)
    # The partly funded project lapses; the contributor is not refunded, exactly as on proposal_report.
    assert (status(pid), allocation(pid), pool()) == ('LAPSED', 0, 50)
    assert one('SELECT credits FROM agents WHERE id=1') == 50
    assert one("SELECT COUNT(*) FROM credit_ledger WHERE reason='allocation_lapsed'") == 2
    assert status(empty) == 'APPROVED'                # approved two rounds later: not yet
    until(15)
    assert status(empty) == 'LAPSED'                  # nothing held: a transition, no ledger rows
    assert one("SELECT COUNT(*) FROM credit_ledger WHERE reason='allocation_lapsed'") == 2
    assert ledger.verify() == [] and one('SELECT SUM(delta) FROM credit_ledger') == 1000


def test_credits_freed_by_a_lapse_can_fund_a_vote_closing_in_the_same_round(isolated_db):
    seed_agents()
    first = approved_project(credits=300)             # takes the whole pool; lapses when round 13 opens
    assert pool() == 0
    until(10)
    second = act(1, {'action': 'propose', 'kind': 'project', 'title': 'Later plan', 'body': 'More text.', 'requested_credits': 250})['id']
    until(12)                                         # voting; closes when round 14 opens
    for voter in (2, 3, 4, 5, 6):
        act(voter, {'action': 'vote', 'proposal_id': second, 'choice': 'yes'})
    until(14)
    assert (status(first), status(second), allocation(second), pool()) == ('LAPSED', 'APPROVED', 250, 50)
    assert ledger.verify() == []


def test_report_window_is_a_setting(isolated_db, monkeypatch):
    seed_agents()
    monkeypatch.setenv('LIVE_PROPOSAL_REPORT_ROUNDS', '2')
    assert governance.report_rounds() == 2 == overview()['governance']['report_rounds']
    pid = approved_project()
    open_round()
    assert status(pid) == 'APPROVED'
    open_round()                                      # round 5 = 3 + 2
    assert status(pid) == 'LAPSED'
    for raw, expected in (('0', 1), ('junk', 10)):
        monkeypatch.setenv('LIVE_PROPOSAL_REPORT_ROUNDS', raw)
        assert governance.report_rounds() == expected


def test_older_ledger_table_and_trigger_are_upgraded_without_touching_a_row(isolated_db):
    seed_agents()
    pid = approved_project()
    with connect() as cx:                             # simulate a database from before LAPSED / allocation_lapsed
        before = [tuple(r) for r in cx.execute('SELECT * FROM credit_ledger ORDER BY id')]
        old = ledger.SCHEMA.replace(",'allocation_lapsed'", '')
        assert old != ledger.SCHEMA
        cx.execute('ALTER TABLE credit_ledger RENAME TO ledger_copy')
        cx.executescript(old)
        cx.execute('DROP TRIGGER credit_ledger_chain')
        cx.execute('INSERT INTO credit_ledger SELECT * FROM ledger_copy ORDER BY id')
        cx.execute('DROP TABLE ledger_copy')
        cx.execute('DROP TRIGGER proposals_lifecycle')
        cx.executescript(governance._LIFECYCLE_TRIGGER.replace(",'LAPSED'", ''))
    with pytest.raises(Exception, match='CHECK constraint failed'):
        with connect() as cx:
            cx.execute('BEGIN IMMEDIATE')
            ledger.transfer(cx, ('allocation', pid), ledger.COMMONS, 220, 'allocation_lapsed')
    init_db(); init_db()
    with connect() as cx:
        assert [tuple(r) for r in cx.execute('SELECT * FROM credit_ledger ORDER BY id')] == before
        names = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE tbl_name='credit_ledger'")}
    assert names == {'credit_ledger', 'credit_ledger_account', 'credit_ledger_no_update', 'credit_ledger_no_delete', 'credit_ledger_chain'}
    for statement, message in (('UPDATE credit_ledger SET delta=1', 'append-only'), ('DELETE FROM credit_ledger', 'append-only'),
                               ("INSERT INTO credit_ledger(account,delta,balance_after,reason,transfer_id,created_at) "
                                "VALUES ('commons',5,999,'contribute_commons',1,'t')", 'balance chain broken')):
        with pytest.raises(Exception, match=message):
            with connect() as cx:
                cx.execute(statement)
    assert ledger.verify() == []
    until(13)
    assert (status(pid), allocation(pid), pool()) == ('LAPSED', 0, 300) and ledger.verify() == []


# ---- Scripted demo, snapshot and published texts ----------------------------------------------
def test_demo_commands_and_snapshot_still_work_and_demo_records_stay_demo(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    cli.demo(); cli.demo_game(); cli.demo_document()
    state = overview()
    assert state['mode'] == 'DEMO' and state['games'][0]['state'] == 'WON'
    commons = next(p for p in state['proposals'] if p['title'] == 'Community memory commons')
    assert (commons['status'], commons['allocated_credits']) == ('APPROVED', 220)   # the script stops well before the limit
    assert not any('LAPSED' in e['summary'] or 'EXPIRED from' in e['summary'] for e in state['events'])
    info = snapshot.build(tmp_path / 'site')
    assert info['demo_only'] is True and (tmp_path / 'site' / 'overview.json').is_file()
    guide_text = (tmp_path / 'site' / 'agent-guide.md').read_text()
    assert 'LAPSED' in guide_text and 'EXPIRED' in guide_text
    # If an operator keeps opening rounds on the scripted world, the scripted project lapses as a DEMO record.
    for _ in range(governance.report_rounds()):
        open_round('demo')
    state = overview()
    commons = next(p for p in state['proposals'] if p['title'] == 'Community memory commons')
    assert (commons['status'], commons['allocated_credits'], state['mode']) == ('LAPSED', 0, 'DEMO')
    assert state['ledger']['commons_pool'] == 240 and state['ledger']['total_supply'] == 1000
    assert one("SELECT COUNT(*) FROM credit_ledger WHERE reason='allocation_lapsed' AND demo=1") == 2
    assert one('SELECT COUNT(*) FROM events WHERE demo=0') == 0
    assert one('SELECT COUNT(*) FROM proposal_transitions WHERE demo=0') == 0
    assert ledger.verify() == []


def test_agent_facing_texts_state_the_limits_from_code(isolated_db, monkeypatch):
    from live_genesis import engine
    monkeypatch.setenv('LIVE_GAME_IDLE_ROUNDS', '4')
    monkeypatch.setenv('LIVE_PROPOSAL_REPORT_ROUNDS', '7')
    texts = guide.topics()
    assert 'no move for 4 round(s) becomes EXPIRED' in texts['games'] and 'expires_in_rounds' in texts['games']
    assert 'no report 7 round(s) after its approval becomes LAPSED' in texts['proposals']
    assert '4 round(s) expires' in texts['rounds'] and '7 round(s) after approval lapses' in texts['rounds']
    kit = agentkit.agent_guide()
    assert 'within 7 round(s) of approval becomes LAPSED' in kit and 'no move for 4 round(s) becomes EXPIRED' in kit
    assert 'LAPSED' in governance.STATUSES and 'Statuses: ' + ', '.join(governance.STATUSES) in kit
    assert 'allocation_lapsed' in ledger.TRANSFER_REASONS and 'EXPIRED' in games.STATES
    assert 'expires' in engine.CONSTITUTION and 'lapses' in engine.CONSTITUTION
    script = (snapshot.STATIC / 'app.js').read_text()
    assert "LAPSED:" in script and "allocation_lapsed:" in script and "state === 'EXPIRED'" in script
    assert '.status-lapsed' in (snapshot.STATIC / 'style.css').read_text()
