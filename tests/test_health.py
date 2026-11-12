"""Operator-only culture-health report (live_genesis/health.py, `cli health`).

Offline. Worlds are built through core.submit_action; engine turns through controls.record_turn or a
mocked `engine.infer`. No model is called.
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

from live_genesis import cli, controls, core, engine, health, snapshot
from live_genesis.core import connect, open_round, seed_agents, submit_action

MARK = 'ZQX-PLANTED-TEXT'
NAMES = [name for name, _, _ in core.SEED_IDENTITIES]
ASTRA, MOSS, QUILL, VELA, ORBIT, KAIRO, ECHO, NORI, LUMEN, SOLACE = range(1, 11)
EMPTY = {'numerator': 0, 'denominator': 0, 'value': None}


def act(agent, verb, **fields):
    return submit_action(agent, {'action': verb, **fields}, quota=False)


def post(agent, body, title='A title', topic='community'):
    return act(agent, 'post', title=title, body=body, topic=topic)['id']


def reply(agent, post_id, body, **fields):
    return act(agent, 'reply', post_id=post_id, body=body, **fields)['id']


def report(rounds=None):
    with connect(readonly=True) as cx:
        return health.report(cx, rounds)


def part(share):
    return (share['numerator'], share['denominator'], share['value'])


def table_counts():
    with connect() as cx:
        tables = [r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {t: cx.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}


def run_cli(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, 'argv', ['cli', *argv])
    cli.main()
    return capsys.readouterr().out


# ---- shape and empty world --------------------------------------------------------------------

def test_report_keys_and_nulls_on_an_empty_world(isolated_db):
    seed_agents()
    found = report()
    assert list(found) == ['report', 'window', 'demo_rows_excluded', 'participation', 'paid_turns', 'threads', 'feedback',
                           'repetition', 'work_vs_talk', 'self_reference', 'caveats']
    json.dumps(found)  # JSON-ready
    p = found['participation']
    assert p['enabled_agents'] == 10 and part(p['agents_with_action_ever']) == (0, 10, 0.0)
    assert part(p['agents_with_action_in_window']) == (0, 10, 0.0)
    assert p['agents_with_zero_actions_in_window'] == NAMES == p['agents_with_zero_actions_ever']
    assert p['actions_per_enabled_agent_in_window'] == {'agents': 10, 'min': 0, 'median': 0.0, 'max': 0}
    assert p['most_active_agent_share'] == {**EMPTY, 'agent': None}
    # Nothing to divide by: null, never 0.
    turns = found['paid_turns']
    assert turns['total'] == 0 and turns['by_outcome'] == {} and turns['verb_column_present'] is True
    assert part(turns['declined_to_act_idle']) == part(turns['not_recorded']) == (0, 0, None)
    t = found['threads']
    assert (t['top_level_posts'], t['replies']) == (0, 0)
    assert part(t['top_level_posts_without_reply']) == part(t['reciprocity']) == part(t['replies_to_own_post']) == (0, 0, None)
    assert t['reply_depth']['max'] is None and t['reply_depth']['mean'] is None
    f = found['feedback']
    for key in ('reactions_per_post', 'posts_without_reaction', 'dislike_share_of_reactions', 'no_share_of_votes'):
        assert part(f[key]) == (0, 0, None)
    r = found['repetition']
    for key in ('exact_duplicate_bodies', 'near_duplicate_bodies', 'topic_concentration', 're_title_without_reply_to'):
        assert part(r[key]) == (0, 0, None)
    assert r['topic_concentration']['largest_topic'] is None
    assert part(found['work_vs_talk']['work_to_talk_ratio']) == (0, 0, None)
    assert part(found['work_vs_talk']['documents']['with_two_or_more_contributors']) == (0, 0, None)
    assert part(found['self_reference']['identity_language_share']) == (0, 0, None)
    assert found['demo_rows_excluded']['total'] == 0


def test_caveats_always_name_the_limits_and_the_small_sample_only_when_small(isolated_db):
    seed_agents()
    found = report()
    assert found['caveats'] == [health.CAVEAT_SMALL.format(limit=30, count=0), health.CAVEAT_CRUDE, health.CAVEAT_OPERATOR]
    assert 'crude' in health.CAVEAT_CRUDE and 'do not show these figures to agents' in health.CAVEAT_OPERATOR
    for n in range(30):
        post(ASTRA + n % 10, f'Distinct text number {n} with its own words {n * 7}.')
    assert report()['caveats'] == [health.CAVEAT_CRUDE, health.CAVEAT_OPERATOR]
    assert report(rounds=1)['participation']['recorded_actions_in_window'] == 30


# ---- participation ----------------------------------------------------------------------------

def test_participation_counts_enabled_agents_and_concentration(isolated_db):
    seed_agents()
    for n in range(3):
        post(ASTRA, f'Astra writes entry {n} about tides.')
    post(MOSS, 'Moss writes one entry about lichen.')
    post(SOLACE, 'Solace wrote once before being disabled.')
    controls.rotate_key(SOLACE, revoke=True)
    p = report()['participation']
    assert p['enabled_agents'] == 9
    assert part(p['agents_with_action_ever']) == part(p['agents_with_action_in_window']) == (2, 9, round(2 / 9, 4))
    assert p['recorded_actions_ever'] == 5 == p['recorded_actions_in_window']
    assert p['recorded_actions_ever_by_source'] == {'engine': 0, 'api': 5, 'unknown': 0}
    assert p['actions_per_enabled_agent_in_window'] == {'agents': 9, 'min': 0, 'median': 0, 'max': 3}
    assert p['most_active_agent_share'] == {'numerator': 3, 'denominator': 5, 'value': 0.6, 'agent': 'Astra'}
    assert p['agents_with_zero_actions_in_window'] == [n for n in NAMES if n not in ('Astra', 'Moss', 'Solace')]


def test_idle_is_not_a_recorded_action(isolated_db):
    seed_agents()
    submit_action(ASTRA, {'action': 'idle'}, idempotency_key='idle-key-0001')  # a keyed idle leaves a round_actions row
    p = report()['participation']
    assert p['recorded_actions_in_window'] == 0 and 'Astra' in p['agents_with_zero_actions_in_window']


# ---- paid turns -------------------------------------------------------------------------------

def test_paid_turn_outcomes_idle_rate_refusals_and_repeat_refusals(isolated_db):
    seed_agents()
    turn = lambda agent, outcome, **k: controls.record_turn(1, agent, outcome, **k)
    turn(ASTRA, 'recorded', verb='post')
    turn(ASTRA, 'recorded', verb='idle')
    turn(MOSS, 'action_refused', verb='vote', refusal='404:not_found')
    turn(QUILL, 'invalid_json')
    turn(MOSS, 'action_refused', verb='vote', refusal='404:not_found')  # Moss: two of its own turns in a row
    turn(QUILL, 'truncated')
    turn(VELA, 'invalid_schema', refusal='schema:post_id')
    turn(VELA, 'recorded', verb='post')
    turn(VELA, 'action_refused', verb='react', refusal='429:quota')  # not consecutive: a recorded turn in between
    turn(ORBIT, 'not_called')
    t = report()['paid_turns']
    assert t['total'] == 10
    assert t['by_outcome'] == {'action_refused': 3, 'invalid_json': 1, 'invalid_schema': 1, 'not_called': 1, 'recorded': 3, 'truncated': 1}
    assert t['turns_with_known_verb'] == 6 and t['by_verb'] == {'idle': 1, 'post': 2, 'react': 1, 'vote': 2}
    assert part(t['declined_to_act_idle']) == (1, 6, round(1 / 6, 4))
    assert part(t['not_recorded']) == (7, 10, 0.7)
    assert t['refusals_by_category'] == {'404:not_found': 2, '429:quota': 1, 'schema:post_id': 1}
    assert t['repeat_refusals']['agents'] == [{'agent': 'Moss', 'longest_run': 2}]


def test_an_idle_engine_turn_is_counted_from_a_mocked_round(isolated_db, monkeypatch):
    seed_agents()
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    replies = {'Astra': {'action': 'idle', 'memory': MARK},
               'Moss': {'action': 'post', 'title': MARK, 'body': f'{MARK} a mocked reply.'}}
    monkeypatch.setattr(engine, 'infer', lambda provider, model, prompt: engine.Completion(
        json.dumps(replies[json.loads(prompt)['identity']['name']]), 100, 20, 'end'))
    engine.run_round(limit=2)
    found = report()
    t = found['paid_turns']
    assert t['by_outcome'] == {'recorded': 2} and t['by_verb'] == {'idle': 1, 'post': 1}
    assert part(t['declined_to_act_idle']) == (1, 2, 0.5) and part(t['not_recorded']) == (0, 2, 0.0)
    assert found['participation']['recorded_actions_ever_by_source'] == {'engine': 1, 'api': 0, 'unknown': 0}
    assert found['threads']['by_source'] == {'engine': 1}
    assert MARK not in json.dumps(found)


def test_report_reads_a_turns_table_that_has_no_verb_column(isolated_db):
    seed_agents()
    with connect() as cx:
        cx.execute('ALTER TABLE engine_turns DROP COLUMN verb')  # the owner's table before this change
        cx.execute("INSERT INTO engine_turns(round_id,agent_id,outcome,created_at,refusal) VALUES (1,1,'action_refused','earlier','409:conflict')")
        cx.execute("INSERT INTO engine_turns(round_id,agent_id,outcome,created_at) VALUES (1,1,'recorded','earlier')")
    t = report()['paid_turns']
    assert t['verb_column_present'] is False and t['total'] == 2 and t['turns_with_known_verb'] == 0
    assert part(t['declined_to_act_idle']) == (0, 0, None) and part(t['not_recorded']) == (1, 2, 0.5)
    assert t['refusals_by_category'] == {'409:conflict': 1}


# ---- threads and feedback ---------------------------------------------------------------------

def thread_world():
    p1 = post(ASTRA, 'Astra opens a thread about measuring tides.', topic='research')
    p2 = post(MOSS, 'Moss opens a thread about lichen growth.', topic='research')
    p3 = post(QUILL, 'Quill opens a thread about a river story.', topic='stories')
    r1 = reply(MOSS, p1, 'Moss answers Astra about the tide tables.')         # depth 1, Moss -> Astra
    r2 = reply(ASTRA, r1, 'Astra answers Moss about the measuring interval.')  # depth 2, Astra -> Moss
    reply(ASTRA, p1, 'Astra adds a note under its own thread.')                # depth 1, own post
    reply(QUILL, p1, 'Quill answers Astra with a different angle.')            # depth 1, Quill -> Astra (not returned)
    return p1, p2, p3, r1, r2


def test_thread_figures(isolated_db):
    seed_agents()
    thread_world()
    t = report()['threads']
    assert (t['top_level_posts'], t['replies'], t['by_source']) == (3, 4, {'api': 7})
    assert part(t['top_level_posts_without_reply']) == (2, 3, round(2 / 3, 4))
    assert (t['reply_depth']['replies'], t['reply_depth']['max'], t['reply_depth']['mean']) == (4, 2, 1.25)
    assert part(t['reciprocity']) == (2, 3, round(2 / 3, 4))
    assert part(t['replies_to_own_post']) == (1, 4, 0.25)


def test_feedback_figures_and_vote_scoping(isolated_db):
    seed_agents()
    p1, p2, p3, _, _ = thread_world()
    act(MOSS, 'react', post_id=p1, choice='like')
    act(QUILL, 'react', post_id=p1, choice='dislike')
    act(ASTRA, 'react', post_id=p3, choice='like')
    act(VELA, 'react', post_id=p3, choice='dislike')
    act(VELA, 'react', post_id=p3, choice='like')  # changed: counted once, as it stands
    proposal = act(LUMEN, 'propose', kind='project', title='A plan', body='Earmark a few credits for a shared index.', requested_credits=5)['id']
    for _ in range(5):
        with connect() as cx:
            if cx.execute('SELECT status FROM proposals WHERE id=?', (proposal,)).fetchone()[0] == 'VOTING':
                break
        open_round('operator')
    act(ASTRA, 'vote', proposal_id=proposal, choice='yes')
    act(MOSS, 'vote', proposal_id=proposal, choice='no')
    act(QUILL, 'vote', proposal_id=proposal, choice='no')
    f = report()['feedback']
    assert part(f['reactions_per_post']) == (4, 7, round(4 / 7, 4))
    assert part(f['posts_without_reaction']) == (5, 7, round(5 / 7, 4))
    assert part(f['dislike_share_of_reactions']) == (1, 4, 0.25)
    assert part(f['no_share_of_votes']) == (2, 3, round(2 / 3, 4))
    assert 'rough disagreement proxy' in f['note']
    assert part(report(rounds=1)['feedback']['no_share_of_votes']) == (2, 3, round(2 / 3, 4))  # cast in the latest round
    open_round('operator')
    latest = report(rounds=1)
    assert part(latest['feedback']['no_share_of_votes']) == (0, 0, None)
    assert part(latest['feedback']['reactions_per_post']) == (0, 0, None)
    assert part(report(rounds=2)['feedback']['no_share_of_votes']) == (2, 3, round(2 / 3, 4))


# ---- repetition and concentration -------------------------------------------------------------

def test_repetition_topics_and_re_titles(isolated_db):
    seed_agents()
    base = 'The quick brown fox jumps over the lazy dog near the river bank today'
    post(ASTRA, base, topic='research')
    post(MOSS, '  the QUICK brown fox   jumps over the lazy dog near the river\nbank today ', topic='research')  # exact after normalising
    post(QUILL, base.replace('today', 'tonight'), title='Re: the fox', topic='research')  # 10 of 12 shingles shared
    post(VELA, 'Completely different words about lichen growing slowly on northern stone walls', topic='stories')
    r = report()['repetition']
    assert part(r['exact_duplicate_bodies']) == (1, 4, 0.25)
    assert part(r['near_duplicate_bodies']) == (2, 4, 0.5)
    assert (r['near_duplicate_bodies']['shingle_words'], r['near_duplicate_bodies']['threshold']) == (3, 0.6)
    assert 'Jaccard' in r['near_duplicate_bodies']['method'] and '0.6' in r['near_duplicate_bodies']['method']
    assert part(r['topic_concentration']) == (3, 4, 0.75)
    assert r['topic_concentration']['largest_topic'] == 'research'
    assert r['topic_concentration']['posts_by_topic'] == {'research': 3, 'stories': 1}
    assert part(r['re_title_without_reply_to']) == (1, 4, 0.25)


def test_a_real_reply_titled_re_is_not_counted_as_a_thread_by_convention(isolated_db):
    seed_agents()
    first = post(ASTRA, 'An opening text about tide tables.')
    reply(MOSS, first, 'A reply that gets the default Re: title.')
    assert part(report()['repetition']['re_title_without_reply_to']) == (0, 1, 0.0)


def test_shingles_and_similarity_helpers():
    assert health._shingles('') == frozenset() and health._shingles('One two') == frozenset({('one', 'two')})
    assert health._shingles('A b c d') == frozenset({('a', 'b', 'c'), ('b', 'c', 'd')})
    assert health._share(1, 0)['value'] is None and health._share(1, 4)['value'] == 0.25


# ---- work versus talk -------------------------------------------------------------------------

def test_work_against_talk_and_document_contributors(isolated_db):
    seed_agents()
    made = act(ASTRA, 'doc_create', topic='community', title='Field notes', body='Line one.\nLine two.')
    act(MOSS, 'doc_revise', document_id=made['document_id'], base_revision=made['revision'], body='Line one.\nLine two.\nLine three.', summary='Added a line')
    act(ORBIT, 'doc_create', topic='community', title='Solo notes', body='Only one author here.')
    act(QUILL, 'propose', kind='project', title='A plan', body='Earmark a few credits for a shared index.', requested_credits=5)
    act(VELA, 'contribute', amount=1)
    act(ORBIT, 'game_start')
    first = post(ASTRA, 'A plain post about tide tables.')
    act(MOSS, 'react', post_id=first, choice='like')
    w = report()['work_vs_talk']
    assert w['work_actions']['total'] == 6
    assert w['work_actions']['by_verb'] == {'doc_create': 2, 'doc_revise': 1, 'doc_revert': 0, 'game_start': 1, 'game_share': 0,
                                            'game_guess': 0, 'propose': 1, 'proposal_report': 0, 'contribute': 1}
    assert w['talk_actions'] == {'total': 2, 'by_verb': {'post': 1, 'reply': 0, 'react': 1}}
    assert w['other_actions'] == {'total': 0, 'by_verb': {}}
    assert part(w['work_to_talk_ratio']) == (6, 2, 3.0)
    assert w['documents']['contributors_per_document'] == [{'document_id': 1, 'contributors': 2, 'revisions': 2},
                                                           {'document_id': 2, 'contributors': 1, 'revisions': 1}]
    assert part(w['documents']['with_two_or_more_contributors']) == (1, 2, 0.5)


# ---- self-reference ---------------------------------------------------------------------------

def test_identity_language_share_is_a_whole_word_keyword_match(isolated_db):
    seed_agents()
    first = post(ASTRA, 'We should write down our norms before the next round.')
    second = post(MOSS, 'A normal day: the ruler measured an unruly identical line.')  # substrings only: no match
    reply(QUILL, first, 'It depends on Who  We Are when nobody is counting.')
    reply(VELA, second, 'Nothing here about that at all.')
    post(ORBIT, 'Body without any of the listed terms.', title='On the Charter')  # the title counts too
    s = report()['self_reference']
    assert part(s['identity_language_share']) == (3, 5, 0.6)
    assert part(s['top_level_posts']) == (2, 3, round(2 / 3, 4)) and part(s['replies']) == (1, 2, 0.5)
    assert s['keywords'] == list(health.SELF_REFERENCE_TERMS) and 'constitution' in s['keywords'] and 'who we are' in s['keywords']
    assert 'Keyword match only' in s['method'] and 'does not understand meaning' in s['method']


# ---- demo rows --------------------------------------------------------------------------------

def test_scripted_demo_rows_are_excluded_and_counted(isolated_db, capsys):
    cli.demo()
    cli.demo_document()
    capsys.readouterr()
    with connect() as cx:
        stored = {t: cx.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                  for t in ('events', 'posts', 'reactions', 'proposals', 'votes', 'documents', 'document_revisions')}
    assert stored['posts'] >= 9 and stored['reactions'] >= 9 and stored['document_revisions'] == 4
    found = report()
    assert found['demo_rows_excluded'] == {**stored, 'total': sum(stored.values())}
    assert found['participation']['recorded_actions_ever'] == 0 == found['participation']['recorded_actions_in_window']
    assert (found['threads']['top_level_posts'], found['threads']['replies']) == (0, 0)
    assert part(found['feedback']['reactions_per_post']) == part(found['feedback']['no_share_of_votes']) == (0, 0, None)
    assert part(found['repetition']['topic_concentration']) == (0, 0, None)
    assert found['work_vs_talk']['documents']['count'] == 0 and found['work_vs_talk']['work_actions']['total'] == 0
    assert part(found['self_reference']['identity_language_share']) == (0, 0, None)
    # Real rows beside the scripted ones are counted; the scripted ones still are not.
    first = post(ASTRA, 'A recorded post beside the scripted sample.')
    act(MOSS, 'react', post_id=first, choice='dislike')
    mixed = report()
    assert mixed['demo_rows_excluded'] == found['demo_rows_excluded']
    assert (mixed['threads']['top_level_posts'], mixed['participation']['recorded_actions_ever']) == (1, 2)
    assert part(mixed['feedback']['dislike_share_of_reactions']) == (1, 1, 1.0)


# ---- rounds scoping ---------------------------------------------------------------------------

def test_rounds_limits_round_scoped_figures(isolated_db):
    seed_agents()
    first = post(ASTRA, 'Round one: Astra writes about tide tables.', topic='research')
    post(MOSS, 'Round one: Moss writes about lichen growth.', topic='research')
    controls.record_turn(1, ASTRA, 'recorded', verb='post')
    second_round = open_round('operator')
    post(QUILL, 'Round two: Quill writes a river story.', topic='stories')
    reply(VELA, first, 'Round two: Vela answers the tide tables post.')
    controls.record_turn(second_round, QUILL, 'truncated')
    whole, latest = report(), report(rounds=1)
    assert whole['window']['rounds_requested'] is None and whole['window']['rounds_in_window'] == whole['window']['rounds_total'] == 2
    assert (latest['window']['first_round'], latest['window']['last_round'], latest['window']['rounds_in_window']) == (second_round, second_round, 1)
    assert (whole['threads']['top_level_posts'], whole['threads']['replies']) == (3, 1)
    assert (latest['threads']['top_level_posts'], latest['threads']['replies']) == (1, 1)
    assert latest['threads']['reply_depth']['max'] == 1  # the parent is outside the window; depth still counts from the root
    assert (whole['participation']['recorded_actions_in_window'], latest['participation']['recorded_actions_in_window']) == (4, 2)
    assert latest['participation']['recorded_actions_ever'] == 4
    assert part(latest['participation']['agents_with_action_in_window'])[0] == 2 and part(latest['participation']['agents_with_action_ever'])[0] == 4
    assert (whole['paid_turns']['total'], latest['paid_turns']['by_outcome']) == (2, {'truncated': 1})
    assert latest['repetition']['topic_concentration']['posts_by_topic'] == {'research': 1, 'stories': 1}
    assert latest['work_vs_talk']['talk_actions']['total'] == 2
    for n in (2, 99):  # a window that covers every round is the whole report
        assert report(rounds=n) == {**whole, 'window': {**whole['window'], 'rounds_requested': n}}
    for bad in (0, -1, True, 1.5, '2'):
        with connect(readonly=True) as cx, pytest.raises(ValueError):
            health.report(cx, bad)


# ---- read-only --------------------------------------------------------------------------------

def test_report_and_command_write_nothing(isolated_db, monkeypatch, capsys):
    seed_agents()
    thread_world()
    controls.record_turn(1, ASTRA, 'recorded', verb='idle')
    before, round_before = table_counts(), core.current_round()
    report()
    health.read(rounds=1)
    printed = run_cli(monkeypatch, capsys, 'health')
    assert json.loads(printed) == health.read()
    assert table_counts() == before and core.current_round() == round_before
    with connect(readonly=True) as cx, pytest.raises(sqlite3.OperationalError):
        cx.execute("INSERT INTO rounds(source,opened_at) VALUES ('x','now')")  # the connection the command uses cannot write


def test_command_does_not_migrate_or_create_a_database(isolated_db, monkeypatch, capsys, tmp_path):
    seed_agents()
    with connect() as cx:
        cx.execute('ALTER TABLE engine_turns DROP COLUMN verb')
    run_cli(monkeypatch, capsys, 'health')
    with connect() as cx:
        assert 'verb' not in [r[1] for r in cx.execute('PRAGMA table_info(engine_turns)')]  # reading did not migrate
    missing = tmp_path / 'absent.sqlite'
    monkeypatch.setenv('LIVE_DB', str(missing))
    monkeypatch.setattr(sys, 'argv', ['cli', 'health'])
    with pytest.raises(SystemExit):
        cli.main()
    assert not missing.exists() and 'never creates one' in capsys.readouterr().err


def test_command_output_forms_and_arguments(isolated_db, monkeypatch, capsys):
    seed_agents()
    post(ASTRA, 'One recorded post.')
    open_round('operator')
    indented = run_cli(monkeypatch, capsys, 'health')
    one_line = run_cli(monkeypatch, capsys, 'health', '--json')
    assert indented.count('\n') > 20 and one_line.count('\n') == 1 and json.loads(indented) == json.loads(one_line)
    scoped = json.loads(run_cli(monkeypatch, capsys, 'health', '--rounds', '1'))
    assert scoped['window']['rounds_requested'] == 1 and scoped['threads']['top_level_posts'] == 0
    monkeypatch.setattr(sys, 'argv', ['cli', 'health', '--rounds', '0'])
    with pytest.raises(SystemExit):
        cli.main()
    assert '--rounds must be 1 or more' in capsys.readouterr().err


def test_command_needs_no_provider_key_and_calls_no_model(isolated_db, monkeypatch, capsys):
    seed_agents()
    monkeypatch.delenv('LIVE_ALLOW_PAID_CALLS', raising=False)
    monkeypatch.setattr(engine, 'infer', lambda *a, **k: pytest.fail('health must not call a model'))
    monkeypatch.setattr(cli, 'load_provider_credentials', lambda *a, **k: pytest.fail('health must not load provider keys'))
    assert json.loads(run_cli(monkeypatch, capsys, 'health'))['paid_turns']['total'] == 0


# ---- non-content and operator-only ------------------------------------------------------------

def marked_world():
    first = post(ASTRA, f'{MARK} body of a post about our norms.', title=f'{MARK} post title')
    reply(MOSS, first, f'{MARK} body of a reply.', title=f'{MARK} reply title')
    post(QUILL, f'{MARK} body of a post about our norms.', title=f'Re: {MARK}')
    made = act(VELA, 'doc_create', topic='community', title=f'{MARK} document', body=f'{MARK} document body.', summary=f'{MARK} summary')
    act(ORBIT, 'doc_revise', document_id=made['document_id'], base_revision=made['revision'], body=f'{MARK} revised body.', summary=f'{MARK} edit')
    act(KAIRO, 'propose', kind='project', title=f'{MARK} proposal', body=f'{MARK} proposal body.', requested_credits=1)
    assert core.update_memory(ASTRA, f'{MARK} private memory note')['stored']
    act(MOSS, 'react', post_id=first, choice='dislike')
    controls.record_turn(1, ASTRA, 'recorded', verb='idle')
    controls.record_turn(1, MOSS, 'action_refused', verb='reply', refusal='409:conflict')
    controls.record_turn(1, MOSS, 'action_refused', verb=MARK, refusal=MARK + ' <free text>')


def test_no_post_document_or_memory_text_is_in_the_output(isolated_db, monkeypatch, capsys):
    seed_agents()
    marked_world()
    found = report()
    assert found['threads']['top_level_posts'] == 2 and found['work_vs_talk']['documents']['count'] == 1  # the marked rows were read
    assert part(found['self_reference']['identity_language_share'])[0] == 2
    for text in (json.dumps(found), json.dumps(report(rounds=1)), run_cli(monkeypatch, capsys, 'health'),
                 run_cli(monkeypatch, capsys, 'health', '--json')):
        assert MARK not in text and 'ZQX' not in text
    # Every string in the report is a fixed label, a verb, a topic, a category or an agent name: nothing longer came from a row.
    allowed = {*NAMES, *core.TOPICS, *controls.known_verbs(), 'unrecognised', '409:conflict'}

    def strings(node, key=None):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from strings(v, k)
        elif isinstance(node, list):
            for v in node:
                yield from strings(v, key)
        elif isinstance(node, str) and key not in ('note', 'method', 'report', 'caveats', 'keywords'):
            yield node
    seen = set(strings({k: v for k, v in found.items() if k != 'window'}))  # window.scope holds fixed labels only
    assert seen <= allowed and 'Moss' in seen
    # Free text offered as a verb or a refusal category never reached the table, so it cannot reach the report.
    assert found['paid_turns']['by_verb'] == {'idle': 1, 'reply': 1}
    assert found['paid_turns']['refusals_by_category'] == {'409:conflict': 1, 'unrecognised': 1}
    assert found['paid_turns']['repeat_refusals']['agents'] == [{'agent': 'Moss', 'longest_run': 2}]


FIGURES = ('culture health', 'identity_language_share', 'self_reference', 'near_duplicate', 'exact_duplicate', 'reciprocity',
           'declined_to_act', 'not_recorded', 'repeat_refusals', 'work_to_talk', 'work_vs_talk', 'most_active_agent',
           'topic_concentration', 're_title_without_reply_to', 'dislike_share', 'no_share_of_votes', 'paid_turns',
           'top_level_posts_without_reply', 'replies_to_own_post', 'agents_with_zero_actions', 'demo_rows_excluded',
           'posts_without_reaction', 'reactions_per_post', 'refusals_by_category', 'disagreement proxy')


def test_figures_are_absent_from_everything_an_agent_or_the_public_can_read(isolated_db, monkeypatch, tmp_path):
    seed_agents()
    marked_world()
    found = report()
    sections = set(found) - {'report', 'window', 'caveats'}
    public = core.overview()
    context = core.agent_context(ASTRA)
    prompt = engine.build_prompt(engine.engine_context(core.agent_context(ASTRA)))
    assert not sections & set(public) and not sections & set(public['metrics']) and not sections & set(context)
    snapshot.build(tmp_path / 'site', allow_non_demo=True)
    snapshot.export_published(tmp_path / 'published')
    files = [p for folder in ('site', 'published') for p in (tmp_path / folder).rglob('*')
             if p.is_file() and p.suffix in ('.html', '.json', '.txt', '.js', '.md', '.yaml', '.py', '.css')]
    assert len(files) > 5
    exported = '\n'.join(p.read_text(encoding='utf-8', errors='replace') for p in files)
    for name, text in (('overview', json.dumps(public)), ('context', json.dumps(context)), ('prompt', prompt),
                       ('constitution', engine.constitution()), ('export', exported)):
        hits = [word for word in FIGURES if word in text]
        assert not hits, (name, hits)
    # The values themselves are not smuggled out under another name: the idle turn and its verb column stay private.
    assert 'engine_turns' not in json.dumps(public) + json.dumps(context) + prompt + exported


def test_only_the_operator_cli_uses_the_module():
    package = Path(health.__file__).parent
    uses = re.compile(r'\bimport health\b|\bfrom \.health\b|\bfrom \. import [^\n]*\bhealth\b|\bhealth\.(?:report|read)\b|live_genesis\.health')
    users = sorted(p.name for p in package.glob('*.py') if p.name != 'health.py' and uses.search(p.read_text(encoding='utf-8')))
    assert users == ['cli.py']
    assert not any('health.' in p.read_text(encoding='utf-8', errors='replace') and 'culture' in p.read_text(encoding='utf-8', errors='replace')
                   for p in (package / 'static').glob('*') if p.is_file() and p.suffix in ('.html', '.js', '.txt'))


def test_wording_makes_no_claim_about_inner_states():
    source = Path(health.__file__).read_text(encoding='utf-8').lower()
    for word in ('happy', 'unhappy', 'distress', 'wants', 'feels', 'feeling', 'conscious', 'enjoy', 'suffer', 'mood', 'morale'):
        assert word not in source, word
