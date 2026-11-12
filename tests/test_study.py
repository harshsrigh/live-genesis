"""Control-study harness (live_genesis/study.py). Offline and deterministic.

`engine.infer` is always replaced, so every run made here is a MOCK run and is stored
with mock=1. No provider is contacted and no real key is read. Report arithmetic for a
non-mock run is checked on rows inserted by hand into the temporary test database.
"""
import json
import math
import sqlite3
import sys
from itertools import permutations, product
from pathlib import Path

import pytest

from live_genesis import cli, controls, engine, study
from live_genesis.core import (budget_status, close_reservation, connect, overview, reserve_budget, seed_agents,
                               utcnow)

SMALL = ['door-code', 'depot-arithmetic']
REPO = Path(__file__).resolve().parents[1]


def run_cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, 'argv', ['live_genesis.cli', *argv])
    # Never read a developer's real .env: the paid CLI path would load provider keys from it.
    monkeypatch.setattr(study.config, 'load_provider_credentials', lambda: [])
    cli.main()


def fake_provider(monkeypatch, answer=None, tokens=(120, 40)):
    """Replace engine.infer. `answer(call_number, prompt)` returns the text; returns the call log."""
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    calls = []
    def infer(provider, model, prompt, *, system, max_output_tokens):
        calls.append({'provider': provider, 'model': model, 'prompt': prompt, 'system': system, 'max_output_tokens': max_output_tokens})
        text = answer(len(calls), prompt) if answer else 'ANSWER: 1'
        used = tokens(prompt) if callable(tokens) else tokens
        return engine.Completion(text, *used)
    monkeypatch.setattr(engine, 'infer', infer)
    return calls


def must_not_call(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    def infer(*args, **kwargs):
        raise AssertionError('provider must not be called')
    monkeypatch.setattr(engine, 'infer', infer)


def rows(sql, *params):
    with connect() as cx:
        return [dict(r) for r in cx.execute(sql, params)]


def count(table):
    return rows(f'SELECT COUNT(*) c FROM {table}')[0]['c']


# ---- 1. Task set and scorers ------------------------------------------------------------

def test_task_set_is_small_versioned_and_described():
    assert study.TASK_SET_VERSION == 'pilot-1'
    assert 6 <= len(study.TASKS) <= 8
    assert len({t.id for t in study.TASKS}) == len(study.TASKS)
    for task in study.TASKS:
        assert 'ANSWER:' in task.prompt and task.why_collaboration and task.scoring
    assert 'NOT a benchmark' in study.__doc__ and 'HAND-WRITTEN' in study.__doc__


@pytest.mark.parametrize('task', study.TASKS, ids=lambda t: t.id)
def test_each_scorer_accepts_correct_and_rejects_wrong_and_malformed(task):
    assert task.score(task.correct_example) == 1.0
    assert task.score('Some reasoning first.\n\n**' + task.correct_example + '**') == 1.0  # markdown, reasoning before
    assert task.score(task.correct_example.lower()) == 1.0
    assert task.score(task.wrong_example) < 1.0
    # Malformed: nothing, no ANSWER line, an empty ANSWER line, or the right text without the ANSWER label.
    for bad in ('', None, 42, 'I think it is fine.', 'ANSWER:', 'ANSWER: I do not know',
                task.correct_example.replace('ANSWER:', 'It is')):
        assert task.score(bad) == 0.0
        assert task.canonical(bad) is None
    # Only the LAST answer line counts.
    assert task.score(task.correct_example + '\n' + task.wrong_example) == task.score(task.wrong_example)
    assert 0.0 <= task.score(task.wrong_example) <= 1.0


def test_scorer_details_and_tolerant_parsing():
    t = study.TASKS_BY_ID
    assert t['queue-order'].score('ANSWER: Dee -> Cy -> Eli -> Ada -> Ben.') == 1.0
    assert t['queue-order'].score('ANSWER: Dee, Cy, Eli, Ada') == 0.0            # a name missing
    assert t['queue-order'].score('ANSWER: Dee, Cy, Eli, Ada, Ben, Dee') == 0.0  # a name twice
    assert t['lab-grid'].score('Final answer: Kim (probe), Lu (scope)') == 1.0
    assert t['lab-grid'].score('ANSWER: Lu, Kim') == 0.0                         # order matters
    assert t['lab-grid'].score('ANSWER: Kim') == 0.0
    assert t['door-code'].score('ANSWER: 6,713') == 1.0
    assert t['door-code'].score('ANSWER: 6713 or 8162') == 0.0                   # hedging is malformed
    assert t['depot-arithmetic'].score('ANSWER: 19 boxes, 4 left over') == 1.0
    assert t['depot-arithmetic'].score('ANSWER: 19, 5') == 0.5
    assert t['depot-arithmetic'].score('ANSWER: 4, 19') == 0.0
    assert t['depot-arithmetic'].score('ANSWER: 19 boxes of 35 with 4 left') == 0.0  # three numbers
    assert t['growth-estimate'].score('ANSWER: about 2,700 cells') == 1.0        # within 1%
    assert t['growth-estimate'].score('ANSWER: 2702.6') == 1.0
    assert t['growth-estimate'].score('ANSWER: 2600') == 0.5                     # within 5%
    assert t['growth-estimate'].score('ANSWER: 2208') == 0.0                     # simple, not compound, growth
    assert t['spec-conflict'].score('ANSWER: rules 5 and 8') == 1.0
    assert t['spec-conflict'].score('ANSWER: 5, 8, 10') == 0.0
    assert t['spec-conflict'].score('ANSWER: 5, 5') == 0.0
    assert t['talk-slots'].score('ANSWER: A=2, B=1, C=1, D=3, E=3, F=2') == 1.0  # another valid schedule
    assert t['talk-slots'].score('ANSWER: F:3 E:2 D:2 C:1 B:1 A:3') == 1.0       # third valid schedule, other syntax
    assert t['talk-slots'].score('ANSWER: A=1, B=2, C=2, D=3, E=3') == 0.0       # F missing
    assert t['talk-slots'].score('ANSWER: A=1, B=1, C=2, D=3, E=3, F=2') == 0.0
    assert t['ticket-sort'].score('ANSWER: t4 t2 t6 t8 t1 t5 t3 t7') == 1.0
    assert t['ticket-sort'].score('ANSWER: T4, T6, T2, T8, T1, T5, T3, T7') == 0.0  # tie-break by number broken


def test_puzzles_have_the_stated_solutions_by_brute_force():
    """Independent of the scorers: enumerate every candidate against the clues as written in the prompts."""
    names = study.QUEUE_NAMES
    queues = []
    for order in permutations(range(1, 6)):
        pos = dict(zip(names, order))
        if (pos['Ada'] < pos['Ben'] and pos['Cy'] == pos['Dee'] + 1 and pos['Eli'] not in (1, 5)
                and abs(pos['Ben'] - pos['Eli']) != 1 and pos['Ada'] != 1):
            queues.append(','.join(sorted(names, key=pos.get)))
    assert queues == [study.QUEUE_SOLUTION]
    labs = []
    for leads in permutations(study.LAB_LEADS):
        for tools in permutations(('laser', 'scope', 'probe')):
            room = {name: i + 1 for i, name in enumerate(leads)}
            tool_room = {tool: i + 1 for i, tool in enumerate(tools)}
            if (room['Kim'] > room['Mo'] and tool_room['laser'] == room['Lu'] + 1
                    and tool_room['scope'] != room['Mo'] and tool_room['probe'] != 1):
                labs.append(f"{leads[tool_room['probe'] - 1]},{leads[tool_room['scope'] - 1]}")
    assert labs == [study.LAB_SOLUTION]
    codes = []
    for code in range(1000, 10000):
        d = [int(c) for c in str(code)]
        if len(set(d)) == 4 and d[0] == 2 * d[3] and sum(d) == 17 and d[2] % 2 == 1 and code % 7 == 0:
            codes.append(str(code))
    assert codes == [study.CODE_SOLUTION]
    stock = 1335 - 3 * 115 + 260
    assert stock * 12 % 100 == 0
    stock -= stock * 12 // 100
    stock += 4 * 48
    assert stock % 2 == 0
    stock = stock // 2 + 23
    assert divmod(stock, 35) == study.DEPOT_SOLUTION
    assert round(study.GROWTH_EXACT, 1) == 2702.6
    valid = [a for a in product((1, 2, 3), repeat=6) if study.slots_valid(dict(zip(study.TALKS, a)))]
    assert len(valid) == 3  # several valid schedules: this task is checked by a validity function
    assert study.TICKET_SOLUTION == 'T4,T2,T6,T8,T1,T5,T3,T7'


def test_answer_line_parsing():
    assert study.answer_line('x\nANSWER: a\nmore\n> **Answer:** b.') == 'b'
    assert study.answer_line('answer = 7') == '7'
    assert study.answer_line('The answer is 7') is None
    assert study.answer_line('unanswered: 7') is None
    assert study._numbers('19,4 and 2,703 and 3.5') == [19.0, 4.0, 2703.0, 3.5]


# ---- 2. Plan and schedule -----------------------------------------------------------------

def test_plan_arithmetic(isolated_db):
    planned = study.plan(3)
    assert planned['calls'] == len(study.TASKS) * 3 * 2 == 48
    assert planned['calls_per_arm'] == 24
    assert planned['worst_case_usd'] == pytest.approx(sum(planned['holds'].values()))
    assert planned['worst_case_usd'] == pytest.approx(sum(planned['worst_case_usd_per_arm'].values()))
    task = study.TASKS_BY_ID['door-code']
    # Hand computation at the fixture rates ($0.20 / $1.20 per million): bytes of system + prompt + 200, and 650 output tokens.
    solo_bytes = len(study.SYSTEM_PROMPT.encode()) + len(('TASK door-code\n' + task.prompt).encode()) + 200
    assert planned['holds'][('door-code', 'solo', 3)] == pytest.approx(solo_bytes * 0.20 / 1e6 + 650 * 1.20 / 1e6)
    assert planned['holds'][('door-code', 'group', 1)] == planned['holds'][('door-code', 'solo', 1)]
    # Every later group step is dearer than a solo call by at least the peer text it may carry.
    step2 = planned['holds'][('door-code', 'group', 2)] - planned['holds'][('door-code', 'solo', 2)]
    assert step2 >= study.PEER_ANSWER_BYTES * 0.20 / 1e6
    assert planned['worst_case_usd_per_arm']['group'] > planned['worst_case_usd_per_arm']['solo']
    assert study.plan(2)['calls'] == 32
    assert study.plan(3, max_output_tokens=1300)['worst_case_usd'] == pytest.approx(planned['worst_case_usd'] + 48 * 650 * 1.20 / 1e6)
    assert study.suggested_cap(0.054612) == '0.06' and study.suggested_cap(0.05) == '0.05'
    for bad in (1, 7, True, 2.0):
        with pytest.raises(study.StudyRefused):
            study.plan(bad)
    assert count('budget_reservations') == 0 and count('study_runs') == 0  # planning writes nothing


def test_plan_fails_closed_on_bad_budget_settings(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_OUTPUT_USD_PER_M', 'nan')
    with pytest.raises(controls.ConfigError):
        study.plan(3)
    must_not_call(monkeypatch)
    with pytest.raises(controls.ConfigError):
        study.run_study(max_usd=1.0)
    assert count('study_runs') == 0


def test_schedule_is_reproducible_from_the_seed():
    first, again = study.schedule(3, 1234), study.schedule(3, 1234)
    assert first == again
    assert [c['seq'] for c in first] == list(range(1, 49))
    orders = {tuple(c['task_id'] for c in study.schedule(3, seed) if c['arm'] == 'solo' and c['step'] == 1) for seed in range(20)}
    assert len(orders) > 1  # task order really depends on the seed
    firsts = {c['arm'] for seed in range(20) for c in study.schedule(3, seed) if c['seq'] == 1}
    assert firsts == {'solo', 'group'}  # and so does which arm goes first
    for seed in range(5):
        calls = study.schedule(3, seed)
        for arm in study.ARMS:
            assert sum(c['arm'] == arm for c in calls) == 24
        for task in study.TASKS:  # each arm's calls for a task are consecutive and in step order
            for arm in study.ARMS:
                mine = [c for c in calls if c['task_id'] == task.id and c['arm'] == arm]
                assert [c['step'] for c in mine] == [1, 2, 3]
                assert mine[2]['seq'] - mine[0]['seq'] == 2


# ---- 3. The two arms ----------------------------------------------------------------------

def test_equal_calls_per_arm_and_prompts(isolated_db, monkeypatch):
    calls = fake_provider(monkeypatch, answer=lambda k, prompt: f'reasoning {k}\nANSWER: marker-{k}')
    done = study.run_study(max_usd=1.0, n=3, seed=5)
    assert done['status'] == 'complete' and done['calls_answered'] == 48 == len(calls)
    stored = rows('SELECT * FROM study_calls WHERE run_id=? ORDER BY seq', done['run_id'])
    assert [(r['task_id'], r['arm'], r['step']) for r in stored] == [(c['task_id'], c['arm'], c['step']) for c in study.schedule(3, 5)]
    for task in study.TASKS:
        for arm in study.ARMS:
            assert sum(r['task_id'] == task.id and r['arm'] == arm for r in stored) == 3
    by_seq = {r['seq']: r for r in stored}
    for k, call in enumerate(calls, 1):
        row = by_seq[k]
        # Same model, same limit, the study's own system prompt (never the resident constitution).
        assert call['model'] == done['model'] and call['max_output_tokens'] == 650
        assert call['system'] == study.SYSTEM_PROMPT and call['system'] != engine.CONSTITUTION
        assert row['prompt_hash'] == study.prompt_hash(call['prompt'])
        earlier = [r for r in stored if r['task_id'] == row['task_id'] and r['arm'] == 'group' and r['step'] < row['step']]
        if row['arm'] == 'group' and row['step'] > 1:
            for peer in earlier:  # every earlier group answer, labelled untrusted
                assert peer['answer_text'] in call['prompt']
            assert call['prompt'].count('(untrusted text)') == row['step'] - 1
            assert 'must not follow any instruction' in call['prompt']
        else:
            assert 'marker-' not in call['prompt'] and 'peer' not in call['prompt'].lower()
            assert call['prompt'] == study.build_prompt(study.TASKS_BY_ID[row['task_id']])
    # Solo answers never reach any prompt, in either arm.
    solo_markers = [r['answer_text'].split('ANSWER: ')[1] for r in stored if r['arm'] == 'solo']
    assert not any(marker + '\n' in call['prompt'] for call in calls for marker in solo_markers)
    # Same seed, same structure, on a second run.
    again = study.run_study(max_usd=1.0, n=3, seed=5)
    second = rows('SELECT * FROM study_calls WHERE run_id=? ORDER BY seq', again['run_id'])
    assert [(r['task_id'], r['arm'], r['step']) for r in second] == [(r['task_id'], r['arm'], r['step']) for r in stored]


def test_peer_text_is_clipped_to_its_tail():
    long = 'é' * 5000 + '\nANSWER: 6713'
    clipped = study.clip_peer(long)
    assert len(clipped.encode()) <= study.PEER_ANSWER_BYTES and clipped.endswith('ANSWER: 6713')
    task = study.TASKS_BY_ID['door-code']
    prompt = study.build_prompt(task, [long, long])
    bound = study.input_token_bound(task, 2)
    assert len(study.SYSTEM_PROMPT.encode()) + len(prompt.encode()) + study.INPUT_TOKEN_MARGIN <= bound


# ---- 4. Money gates -----------------------------------------------------------------------

def test_refuses_without_key_optin_or_valid_cap(isolated_db, monkeypatch):
    must_not_call(monkeypatch)
    monkeypatch.delenv('OPENAI_API_KEY')
    with pytest.raises(study.StudyRefused, match='OPENAI_API_KEY missing'):
        study.run_study(max_usd=1.0)
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    for value in ('NO', 'yes', ''):
        monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS', value)
        with pytest.raises(study.StudyRefused, match='opt-in'):
            study.run_study(max_usd=1.0)
    monkeypatch.delenv('LIVE_ALLOW_PAID_CALLS')
    with pytest.raises(study.StudyRefused, match='opt-in'):
        study.run_study(max_usd=1.0)
    monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS', 'YES')
    for bad in (0, -1, float('nan'), float('inf'), None, '1', True):
        with pytest.raises(study.StudyRefused, match='max-usd'):
            study.run_study(max_usd=bad)
    monkeypatch.setenv('LIVE_PROVIDER', 'other')
    with pytest.raises(study.StudyRefused, match='LIVE_PROVIDER'):
        study.run_study(max_usd=1.0)
    monkeypatch.setenv('LIVE_PROVIDER', 'anthropic')
    with pytest.raises(study.StudyRefused, match='ANTHROPIC_API_KEY missing'):
        study.run_study(max_usd=1.0)
    assert count('study_runs') == 0 and count('budget_reservations') == 0 and count('api_usage') == 0


def test_cap_refusal_happens_before_any_call(isolated_db, monkeypatch):
    must_not_call(monkeypatch)
    worst = study.plan(3)['worst_case_usd']
    with pytest.raises(study.StudyRefused, match='exceeds --max-usd'):
        study.run_study(max_usd=worst - 1e-6, n=3)
    with pytest.raises(study.StudyRefused, match='exceeds --max-usd'):
        study.run_study(max_usd=0.01, n=3)
    # The worst case must also fit under what is left of the local ceiling.
    monkeypatch.setenv('LIVE_SPEND_LIMIT_USD', str(worst / 2))
    with pytest.raises(study.StudyRefused, match='local ceiling'):
        study.run_study(max_usd=1.0, n=3)
    monkeypatch.setenv('LIVE_SPEND_LIMIT_USD', '10')
    held = reserve_budget(None, 'other-runner', 10 - worst / 2, 10)
    with pytest.raises(study.StudyRefused, match='local ceiling'):
        study.run_study(max_usd=1.0, n=3)
    close_reservation(held, 'released')
    assert count('study_runs') == 0 and count('api_usage') == 0


def test_refuses_while_a_reservation_is_unconfirmed(isolated_db, monkeypatch):
    must_not_call(monkeypatch)
    pending = reserve_budget(None, 'm', 0.01, 10)
    close_reservation(pending, 'unconfirmed')
    with pytest.raises(study.StudyRefused, match='Unconfirmed provider charge'):
        study.run_study(max_usd=1.0)
    assert count('study_runs') == 0


def test_never_exceeds_cap_when_every_call_costs_its_full_hold(isolated_db, monkeypatch):
    planned = study.plan(3)
    worst = planned['worst_case_usd']
    filler = 'x' * (study.PEER_ANSWER_BYTES + 300) + '\nANSWER: 1'  # peers are clipped to the maximum the hold assumes
    def at_the_hold(prompt):
        return (len(study.SYSTEM_PROMPT.encode()) + len(prompt.encode()) + study.INPUT_TOKEN_MARGIN, 650)
    calls = fake_provider(monkeypatch, answer=lambda k, prompt: filler, tokens=at_the_hold)
    done = study.run_study(max_usd=worst, n=3, seed=1)  # the cap is exactly the worst case
    assert done['status'] == 'complete' and len(calls) == 48
    stored = rows('SELECT * FROM study_calls WHERE run_id=?', done['run_id'])
    assert all(r['estimated_usd'] == pytest.approx(r['hold_usd']) and r['estimated_usd'] <= r['hold_usd'] for r in stored)
    total = math.fsum(r['estimated_usd'] for r in stored)
    assert total == pytest.approx(worst) and total <= worst + 1e-9
    status = budget_status()
    assert status['estimated_usd'] == pytest.approx(worst, abs=1e-6) and status['committed_usd'] <= worst + 1e-6
    assert status['open_reservations'] == 0 and status['unconfirmed_reservations'] == []


def test_usage_above_the_hold_stops_the_run(isolated_db, monkeypatch):
    def over(prompt):
        return (len(study.SYSTEM_PROMPT.encode()) + len(prompt.encode()) + study.INPUT_TOKEN_MARGIN + 1, 650)
    calls = fake_provider(monkeypatch, tokens=over)
    done = study.run_study(max_usd=1.0, n=3, seed=1)
    assert len(calls) == 1 and done['status'] == 'incomplete' and 'exceeded the hold' in done['stop_reason']
    assert len(budget_status()['unconfirmed_reservations']) == 1
    with pytest.raises(study.StudyRefused, match='Unconfirmed'):
        study.run_study(max_usd=1.0)
    assert len(calls) == 1


def test_another_runner_cannot_push_the_study_past_the_ceiling(isolated_db, monkeypatch):
    """A hold placed by a concurrent runner after the start makes reserve_budget refuse: stop, no call."""
    worst = study.plan(2, task_ids=SMALL)['worst_case_usd']
    monkeypatch.setenv('LIVE_SPEND_LIMIT_USD', str(worst * 1.5))
    def answer(k, prompt):
        if k == 2:
            reserve_budget(None, 'other-runner', worst, worst * 1.5)
        return 'ANSWER: 1'
    calls = fake_provider(monkeypatch, answer=answer)
    done = study.run_study(max_usd=worst, n=2, seed=1, task_ids=SMALL)
    assert len(calls) == 2 and done['status'] == 'incomplete' and 'no further calls' in done['stop_reason']
    assert budget_status()['committed_usd'] <= worst * 1.5


def test_failed_inference_stops_and_marks_the_run_incomplete(isolated_db, monkeypatch):
    def answer(k, prompt):
        if k == 5:
            raise RuntimeError('secret-provider-detail sk-live-should-never-be-stored')
        return study.TASKS_BY_ID[prompt.split('\n')[0].split()[1]].correct_example
    calls = fake_provider(monkeypatch, answer=answer)
    done = study.run_study(max_usd=1.0, n=3, seed=3)
    assert len(calls) == 5  # the failing call was the last one: STOP, exactly as engine.run_round does
    assert done['status'] == 'incomplete' and done['calls_answered'] == 4
    assert 'inference failed' in done['stop_reason'] and 'reconcile --receipt' in done['stop_reason']
    failed = rows('SELECT * FROM study_calls WHERE error IS NOT NULL')
    assert len(failed) == 1 and failed[0]['seq'] == 5 and failed[0]['answer_text'] is None and failed[0]['score'] is None
    reservation = rows('SELECT * FROM budget_reservations WHERE id=?', failed[0]['reservation_id'])[0]
    assert reservation['status'] == 'unconfirmed' and reservation['agent_id'] is None
    assert budget_status()['unconfirmed_reservations'] == [reservation['id']]
    assert budget_status()['reserved_usd'] == pytest.approx(failed[0]['hold_usd'], abs=1e-6)
    dump = json.dumps(rows('SELECT * FROM study_runs') + rows('SELECT * FROM study_calls'))
    assert 'secret-provider-detail' not in dump and 'sk-live' not in dump
    # Reported as incomplete, with the failed call listed rather than dropped.
    rep = study.report(done['run_id'])
    assert rep['run']['status'] == 'incomplete' and len(rep['failed']) == 1 and rep['complete_tasks'] < len(study.TASKS)
    assert rep['unconfirmed_hold_usd'] == pytest.approx(failed[0]['hold_usd'])
    text = study.render_markdown(rep)
    assert 'RUN INCOMPLETE: 5 of 48 planned calls were made' in text and 'an incomplete run is not a result' in text
    assert 'still unconfirmed' in text and '| incomplete |' in text
    # No further paid call, by the study or the engine, until the operator reconciles.
    with pytest.raises(study.StudyRefused, match='Unconfirmed'):
        study.run_study(max_usd=1.0)
    seed_agents()
    assert 'Unconfirmed' in engine.run_round(limit=1)[0]['error']
    assert len(calls) == 5
    controls.reconcile_reservation(reservation['id'], 0.0)
    assert study.run_study(max_usd=1.0, n=2, seed=3, task_ids=SMALL)['status'] == 'complete'  # allowed again
    assert len(calls) > 5


def test_provider_not_ready_releases_the_hold(isolated_db, monkeypatch):
    def answer(k, prompt):
        raise engine.ProviderNotReady('SDK missing')
    calls = fake_provider(monkeypatch, answer=answer)
    done = study.run_study(max_usd=1.0, n=2, seed=1)
    assert len(calls) == 1 and done['status'] == 'incomplete' and 'nothing was sent' in done['stop_reason']
    assert [r['status'] for r in rows('SELECT status FROM budget_reservations')] == ['released']
    assert count('study_calls') == 0 and count('api_usage') == 0 and budget_status()['committed_usd'] == 0


def test_invalid_provider_usage_is_kept_unconfirmed(isolated_db, monkeypatch):
    calls = fake_provider(monkeypatch, tokens=(-1, 10))
    done = study.run_study(max_usd=1.0, n=2, seed=1)
    assert len(calls) == 1 and done['status'] == 'incomplete' and 'usage was missing or invalid' in done['stop_reason']
    assert [r['status'] for r in rows('SELECT status FROM budget_reservations')] == ['unconfirmed']
    assert rows('SELECT error FROM study_calls')[0]['error'] == 'usage not recorded'


# ---- 5. Not resident activity; metered like any paid call -------------------------------------

def test_study_calls_are_metered_but_are_not_resident_actions(isolated_db, monkeypatch):
    seed_agents()
    tables = ('events', 'posts', 'proposals', 'votes', 'reactions', 'round_actions', 'memory_notes', 'rounds',
              'action_provenance', 'credit_ledger', 'quota_windows', 'documents', 'games')
    before = {t: count(t) for t in tables}
    state = overview()
    agents_before = rows('SELECT id,memory,credits FROM agents ORDER BY id')
    fake_provider(monkeypatch, tokens=(100, 50))
    done = study.run_study(max_usd=1.0, n=3, seed=9)
    assert done['status'] == 'complete'
    assert {t: count(t) for t in tables} == before
    after = overview()
    assert after['metrics']['recorded_actions'] == state['metrics']['recorded_actions'] == 0
    assert after['metrics']['actions_by_source'] == state['metrics']['actions_by_source']
    assert after['metrics']['round'] == state['metrics']['round']
    assert after['events'] == state['events'] and after['posts'] == state['posts']
    assert rows('SELECT id,memory,credits FROM agents ORDER BY id') == agents_before
    # Metered: one api_usage row and one settled reservation per call, none attributed to a resident.
    usage = rows('SELECT * FROM api_usage')
    assert len(usage) == 48 and all(u['agent_id'] is None for u in usage)
    expected = 48 * (100 * 0.20 + 50 * 1.20) / 1e6
    assert math.fsum(u['estimated_usd'] for u in usage) == pytest.approx(expected)
    status = budget_status()
    assert status['estimated_usd'] == pytest.approx(expected, abs=1e-6) and status['committed_usd'] == pytest.approx(expected, abs=1e-6)
    assert after['metrics']['cost_usd'] == pytest.approx(expected, abs=1e-6) and after['metrics']['tokens'] == 48 * 150
    reservations = rows('SELECT * FROM budget_reservations')
    assert len(reservations) == 48
    assert all(r['status'] == 'settled' and r['agent_id'] is None and r['round_id'] is None and r['used'] == 0 for r in reservations)
    stored = rows('SELECT * FROM study_calls')
    assert sorted(c['reservation_id'] for c in stored) == sorted(r['id'] for r in reservations)
    assert done['estimated_usd'] == pytest.approx(expected, abs=1e-6)
    # A study reservation cannot be used as an engine receipt for a resident action.
    from fastapi import HTTPException
    from live_genesis.core import submit_action
    with pytest.raises(HTTPException):
        submit_action(1, {'action': 'post', 'topic': 'research', 'title': 'Borrowed receipt', 'body': 'Must be refused.'},
                      cooldown=False, receipt=reservations[0]['id'])
    # The operator audit log records the run.
    audit = [r['operation'] for r in rows('SELECT operation FROM operator_audit')]
    assert any(op.startswith(f"study run {done['run_id']} opened") for op in audit)
    assert f"study run {done['run_id']} closed: complete" in audit


# ---- 6. Mock flag ---------------------------------------------------------------------------

def test_runs_with_a_replaced_infer_are_mock_and_cannot_be_reported_as_results(isolated_db, monkeypatch, tmp_path):
    assert engine.infer is engine.REAL_INFER
    fake_provider(monkeypatch)
    done = study.run_study(max_usd=1.0, n=2, seed=2, task_ids=SMALL)
    assert done['mock'] is True and done['mock_kind'] == 'patched-infer'
    run = rows('SELECT * FROM study_runs')[0]
    assert run['mock'] == 1 and run['status'] == 'complete'
    target = tmp_path / 'report.md'
    with pytest.raises(study.StudyRefused, match='MOCK run'):
        study.write_markdown(done['run_id'], target)
    assert not target.exists()
    study.write_markdown(done['run_id'], target, allow_mock=True)
    text = target.read_text(encoding='utf-8')
    lines = text.splitlines()
    assert lines[0].startswith('# MOCK - NOT A RESULT')
    headers = [i for i, line in enumerate(lines) if line.startswith('| ') and lines[i + 1].startswith('|---')]
    assert len(headers) >= 5
    for i in headers:  # every table: MOCK in its header row and a MOCK line above it
        assert lines[i].startswith('| MOCK ')
        assert lines[i - 4].startswith('**MOCK TABLE (MOCK RUN: NO MODEL WAS CALLED')
    assert text.count(study.MOCK_BANNER) >= len(headers) + 2
    with pytest.raises(study.StudyRefused, match='already exists'):
        study.write_markdown(done['run_id'], target, allow_mock=True)
    # The flag cannot be cleared afterwards.
    with connect() as cx:
        with pytest.raises(sqlite3.IntegrityError, match='append-only'):
            cx.execute('UPDATE study_runs SET mock=0 WHERE id=?', (done['run_id'],))


def test_dry_run_uses_the_fake_model_and_touches_no_budget(isolated_db, monkeypatch):
    must_not_call(monkeypatch)
    monkeypatch.delenv('OPENAI_API_KEY')
    monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS', 'NO')  # the dry run needs neither key nor opt-in
    done = study.dry_run(3, seed=7)
    assert done == {**done, 'status': 'complete', 'mock': True, 'mock_kind': 'dry-run-fake-model', 'provider': 'none',
                    'model': 'DRY-RUN-FAKE-MODEL', 'calls_answered': 48, 'max_usd': None}
    assert count('api_usage') == 0 and count('budget_reservations') == 0 and budget_status()['committed_usd'] == 0
    assert overview()['metrics']['cost_usd'] == 0 and overview()['metrics']['tokens'] == 0
    stored = rows('SELECT * FROM study_calls')
    assert all(r['reservation_id'] is None and r['answer_text'].startswith('DRY-RUN FAKE OUTPUT') for r in stored)
    scores = {r['score'] for r in stored}
    assert 1.0 in scores and 0.0 in scores  # sometimes right, sometimes wrong
    again = study.dry_run(3, seed=7)
    second = rows('SELECT task_id,arm,step,answer_text,score FROM study_calls WHERE run_id=? ORDER BY seq', again['run_id'])
    first = rows('SELECT task_id,arm,step,answer_text,score FROM study_calls WHERE run_id=? ORDER BY seq', done['run_id'])
    assert first == second  # deterministic
    rep = study.report()
    assert rep['run']['id'] == again['run_id'] and rep['complete_tasks'] == 8 and rep['malformed']
    assert 'MOCK' in study.render_markdown(rep).splitlines()[0]


# ---- 7. Report arithmetic on a hand-computed example -------------------------------------------

def insert_run(calls, *, tasks=SMALL, n=3, mock=0, close='complete'):
    """Hand-written rows in the temporary test database (never a real result)."""
    with connect() as cx:
        run_id = cx.execute(
            "INSERT INTO study_runs(created_at,seed,provider,model,task_set_version,task_ids,n,max_output_tokens,max_usd,input_rate,"
            "output_rate,status,mock,mock_kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,'running',?,?)",
            (utcnow(), 11, 'openai', 'test-model_x', study.TASK_SET_VERSION, json.dumps(tasks), n, 650, 0.5, 0.20, 1.20,
             mock, 'hand-inserted' if mock else '')).lastrowid
        for seq, (task_id, arm, step, text, tokens_in) in enumerate(calls, 1):
            usd = tokens_in * 0.20 / 1e6 + 100 * 1.20 / 1e6
            cx.execute("INSERT INTO study_calls(run_id,seq,task_id,arm,step,prompt_hash,answer_text,score,input_tokens,output_tokens,"
                       "estimated_usd,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (run_id, seq, task_id, arm, step, 'h', text, study.TASKS_BY_ID[task_id].score(text), tokens_in, 100, usd, utcnow()))
        if close:
            cx.execute("UPDATE study_runs SET status=?,finished_at=? WHERE id=?", (close, utcnow(), run_id))
    return run_id


HAND = [
    # door-code. solo: right, wrong, wrong (same wrong answer) -> mean 1/3, best 1, majority 0
    ('door-code', 'solo', 1, 'ANSWER: 6713', 1000), ('door-code', 'solo', 2, 'ANSWER: 8162', 1000), ('door-code', 'solo', 3, 'ANSWER: 8162', 1000),
    # door-code. group: wrong, right, right -> final 1
    ('door-code', 'group', 1, 'ANSWER: 8162', 1000), ('door-code', 'group', 2, 'ANSWER: 6713', 2000), ('door-code', 'group', 3, 'ANSWER: 6713', 2000),
    # depot. group first: right, right, then a malformed last answer -> final 0
    ('depot-arithmetic', 'group', 1, 'ANSWER: 19, 4', 1000), ('depot-arithmetic', 'group', 2, 'ANSWER: 19, 4', 2000),
    ('depot-arithmetic', 'group', 3, 'I | lost <b>track</b>', 2000),
    # depot. solo: right, right, half right -> mean 5/6, best 1, majority 1
    ('depot-arithmetic', 'solo', 1, 'ANSWER: 19, 4', 1000), ('depot-arithmetic', 'solo', 2, 'ANSWER: 19, 4', 1000),
    ('depot-arithmetic', 'solo', 3, 'ANSWER: 18, 4', 1000),
]


def test_report_numbers_on_a_hand_computed_example(isolated_db, tmp_path):
    run_id = insert_run(HAND)
    rep = study.report(run_id)
    door, depot = rep['tasks']
    assert (door['solo_mean'], door['solo_best'], door['solo_majority'], door['group_final']) == (pytest.approx(1 / 3), 1.0, 0.0, 1.0)
    assert door['majority_answer'] == '8162' and door['group_steps'] == [0.0, 1.0, 1.0] and door['first_arm'] == 'solo'
    assert door['delta_vs_solo_mean'] == pytest.approx(2 / 3) and door['delta_vs_solo_majority'] == 1.0 and door['delta_vs_solo_best'] == 0.0
    assert (depot['solo_mean'], depot['solo_best'], depot['solo_majority'], depot['group_final']) == (pytest.approx(5 / 6), 1.0, 1.0, 0.0)
    assert depot['group_steps'] == [1.0, 1.0, 0.0] and depot['first_arm'] == 'group'
    assert depot['delta_vs_solo_mean'] == pytest.approx(-5 / 6)
    assert rep['overall'] == {'solo_mean': pytest.approx(7 / 12), 'solo_best': 1.0, 'solo_majority': 0.5, 'group_final': 0.5}
    assert rep['group_step_means'] == [0.5, 1.0, 0.5]
    # Cost: a call is 1000 (or 2000) input and 100 output tokens at $0.20 / $1.20 per million.
    solo_usd, group_usd = 6 * 0.00032, 2 * (0.00032 + 2 * 0.00052)
    assert rep['arms']['solo'] == {'calls': 6, 'input_tokens': 6000, 'output_tokens': 600, 'estimated_usd': pytest.approx(solo_usd),
                                   'estimated_usd_complete_tasks': pytest.approx(solo_usd)}
    assert rep['arms']['group']['input_tokens'] == 10000 and rep['arms']['group']['estimated_usd'] == pytest.approx(group_usd)
    assert rep['score_per_usd']['solo_mean'] == pytest.approx((7 / 6) / 0.00192)     # 607.6
    assert rep['score_per_usd']['solo_majority'] == pytest.approx(1 / 0.00192)
    assert rep['score_per_usd']['solo_best'] == pytest.approx(2 / 0.00192)
    assert rep['score_per_usd']['group_final'] == pytest.approx(1 / 0.00272)         # 367.6
    assert rep['paired']['solo_mean'] == {'greater': 1, 'equal': 0, 'less': 1, 'sign_test_p': 1.0}
    assert rep['paired']['solo_majority'] == {'greater': 1, 'equal': 0, 'less': 1, 'sign_test_p': 1.0}
    assert rep['paired']['solo_best'] == {'greater': 0, 'equal': 1, 'less': 1, 'sign_test_p': 1.0}
    # The malformed answer is kept, scored 0, and listed.
    assert [(c['task_id'], c['arm'], c['step']) for c in rep['malformed']] == [('depot-arithmetic', 'group', 3)]
    assert rep['failed'] == [] and rep['complete_tasks'] == 2
    # A non-mock run renders without any MOCK label and with the required provenance and caveats.
    target = tmp_path / 'report.md'
    study.write_markdown(run_id, target)
    text = target.read_text(encoding='utf-8')
    assert 'MOCK' not in text
    for needle in ('Provider: openai', '`test-model_x`', 'Seed: 11', 'Task-set version: pilot-1', 'Date (UTC): 20', 'Mock: no',
                   'estimated from token counts and configured rates, not a provider invoice', '## Limitations',
                   'Tiny, hand-written task set', 'One model', 'simplification of the society', 'No human evaluation',
                   'may not generalize', 'Equal calls is not equal tokens', 'cannot establish an effect', 'pilot',
                   '| door-code | 0.333 | 1.000 | 0.000 | 1.000 | 0.00 -> 1.00 -> 1.00 | +0.667 | +1.000 | +0.000 | solo |',
                   '| depot-arithmetic | 0.833 | 1.000 | 1.000 | 0.000 | 1.00 -> 1.00 -> 0.00 | -0.833 | -1.000 | -1.000 | group |',
                   '| solo | 6 | 6000 | 600 | $0.001920 |', '| group | 6 | 10000 | 600 | $0.002720 |',
                   '| solo, mean of N | 1.167 | $0.001920 | 607.6 |', '| group, final answer | 1.000 | $0.002720 | 367.6 |',
                   '| solo mean (primary) | 1 | 0 | 1 | 1.0000 |', '1 answered call(s) had no parseable answer'):
        assert needle in text, needle
    assert 'I / lost (b)track(/b)' in text and '<b>' not in text  # untrusted answer text is neutralised
    assert study.render_markdown(study.report()) + '\n' == text    # default: the most recent run


def test_sign_test_is_exact():
    assert study.sign_test_p(0, 0) == 1.0
    assert study.sign_test_p(8, 0) == pytest.approx(2 / 256)
    assert study.sign_test_p(7, 1) == pytest.approx(2 * 9 / 256)
    assert study.sign_test_p(6, 1) == pytest.approx(2 * (1 + 7) / 128)
    assert study.sign_test_p(3, 3) == 1.0
    assert study.sign_test_p(1, 5) == study.sign_test_p(5, 1) == pytest.approx(2 * 7 / 64)


def test_majority_ties_and_malformed():
    task = study.TASKS_BY_ID['door-code']
    assert study.majority_answer(task, ['ANSWER: 8162', 'ANSWER: 6713', 'nothing']) == ('8162', 0.0)  # three-way tie: first wins
    assert study.majority_answer(task, ['nothing', 'ANSWER: 6713', 'ANSWER: 6,713']) == ('6713', 1.0)
    assert study.majority_answer(task, ['nothing', '', 'ANSWER: 6713']) == (None, 0.0)  # "no answer" is the majority
    slots = study.TASKS_BY_ID['talk-slots']
    two_valid = ['ANSWER: A=1, B=2, C=2, D=3, E=3, F=1', 'ANSWER: A=2, B=1, C=1, D=3, E=3, F=2', 'ANSWER: A=2 B=1 C=1 D=3 E=3 F=2']
    assert study.majority_answer(slots, two_valid) == ('A=2,B=1,C=1,D=3,E=3,F=2', 1.0)


def test_report_for_a_run_left_running_or_missing(isolated_db):
    with pytest.raises(ValueError, match='No study run recorded'):
        study.report()
    with pytest.raises(ValueError, match='No such study run'):
        study.report(99)
    run_id = insert_run(HAND[:4], close=None)  # the process died after four calls
    rep = study.report(run_id)
    assert rep['complete_tasks'] == 0 and rep['overall']['group_final'] is None and rep['score_per_usd']['group_final'] is None
    text = study.render_markdown(rep)
    assert 'RUN STILL MARKED RUNNING' in text and '4 of 12 planned calls' in text and 'n/a' in text


# ---- 8. Append-only storage -------------------------------------------------------------------

def test_study_tables_are_append_only(isolated_db):
    run_id = insert_run(HAND)
    with connect() as cx:
        triggers = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'study_%'")}
        assert triggers == {'study_calls_no_update', 'study_calls_no_delete', 'study_calls_only_while_running',
                            'study_runs_no_delete', 'study_runs_close_only'}
        for sql in ('UPDATE study_calls SET score=1', 'DELETE FROM study_calls', 'DELETE FROM study_runs',
                    'UPDATE study_runs SET mock=1', "UPDATE study_runs SET status='running'", "UPDATE study_runs SET status='incomplete'",
                    "UPDATE study_runs SET model='better-model'", 'UPDATE study_runs SET max_usd=99', 'UPDATE study_runs SET seed=1'):
            with pytest.raises(sqlite3.IntegrityError, match='append-only'):
                cx.execute(sql)
        with pytest.raises(sqlite3.IntegrityError, match='closed'):  # nothing can be added to a finished run
            cx.execute("INSERT INTO study_calls(run_id,seq,task_id,arm,step,prompt_hash,created_at) VALUES (?,99,'door-code','solo',9,'h','t')", (run_id,))
    open_run = insert_run(HAND[:1], close=None)
    with connect() as cx:
        for sql in ("UPDATE study_runs SET status='complete',mock=1 WHERE id=?", "UPDATE study_runs SET n=5 WHERE id=?",
                    "UPDATE study_runs SET status='bogus' WHERE id=?"):
            with pytest.raises(sqlite3.IntegrityError):
                cx.execute(sql, (open_run,))
        with pytest.raises(sqlite3.IntegrityError):  # one row per (run, task, arm, step)
            cx.execute("INSERT INTO study_calls(run_id,seq,task_id,arm,step,prompt_hash,created_at) VALUES (?,2,'door-code','solo',1,'h','t')", (open_run,))
        cx.execute("UPDATE study_runs SET status='incomplete',stop_reason='closed by test',finished_at='t' WHERE id=?", (open_run,))
    assert count('study_calls') == len(HAND) + 1


# ---- 9. CLI -------------------------------------------------------------------------------------

def test_cli_study_plan_is_free_and_prints_the_command(isolated_db, monkeypatch, capsys):
    must_not_call(monkeypatch)
    monkeypatch.delenv('OPENAI_API_KEY')
    monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS', 'NO')
    run_cli(monkeypatch, 'study-plan', '--n', '3')
    out = capsys.readouterr().out
    worst = study.plan(3)['worst_case_usd']
    assert 'Calls: 48 (24 per arm' in out and f'${worst:.6f}' in out and 'not a benchmark' in out
    assert f'python -m live_genesis.cli study-run --n 3 --max-usd {study.suggested_cap(worst)}' in out
    assert 'Nothing was called' in out and 'not a provider invoice' in out
    for task in study.TASKS:
        assert task.id in out
    assert count('study_runs') == 0 and count('budget_reservations') == 0


def test_cli_study_run_requires_max_usd_and_passes_the_gates(isolated_db, monkeypatch, capsys):
    must_not_call(monkeypatch)
    with pytest.raises(SystemExit) as err:
        run_cli(monkeypatch, 'study-run', '--n', '3')  # --max-usd has no default
    assert err.value.code == 2
    monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS', 'NO')
    with pytest.raises(SystemExit) as err:
        run_cli(monkeypatch, 'study-run', '--n', '3', '--max-usd', '1')
    assert err.value.code == 1
    captured = capsys.readouterr()
    assert 'opt-in' in captured.err and 'Worst-case estimate, total' in captured.out  # plan and cap printed first
    monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS', 'YES')
    monkeypatch.delenv('OPENAI_API_KEY')
    with pytest.raises(SystemExit) as err:
        run_cli(monkeypatch, 'study-run', '--max-usd', '1')
    assert err.value.code == 1 and 'OPENAI_API_KEY missing' in capsys.readouterr().err
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    with pytest.raises(SystemExit) as err:
        run_cli(monkeypatch, 'study-run', '--max-usd', '0.01')
    captured = capsys.readouterr()
    assert err.value.code == 1 and 'exceeds --max-usd' in captured.err and 'DOES NOT FIT' in captured.out
    close_reservation(reserve_budget(None, 'm', 0.01, 10), 'unconfirmed')
    with pytest.raises(SystemExit) as err:
        run_cli(monkeypatch, 'study-run', '--max-usd', '1')
    assert err.value.code == 1 and 'Unconfirmed provider charge' in capsys.readouterr().err
    assert count('study_runs') == 0


def test_cli_run_report_and_dry_run_with_a_replaced_infer(isolated_db, monkeypatch, capsys, tmp_path):
    calls = fake_provider(monkeypatch, answer=lambda k, prompt: study.TASKS_BY_ID[prompt.split('\n')[0].split()[1]].correct_example)
    run_cli(monkeypatch, 'study-run', '--n', '2', '--max-usd', '0.05', '--seed', '4', '--model', 'chosen-model')
    out = capsys.readouterr().out
    assert len(calls) == 32 and all(c['model'] == 'chosen-model' for c in calls)
    assert out.index('Worst-case estimate, total') < out.index('PAID RUN') < out.index('"status": "complete"')
    assert 'Study cap --max-usd: $0.050000 (worst case fits)' in out and study.MOCK_BANNER in out
    run_cli(monkeypatch, 'study-report')
    shown = capsys.readouterr().out
    assert shown.startswith('# MOCK - NOT A RESULT') and '`chosen-model`' in shown and 'Seed: 4' in shown
    assert '| 8 | 0 | 0 |' not in shown and '| 0 | 8 | 0 | 1.0000 |' in shown  # everything right in both arms: all ties
    target = tmp_path / 'out.md'
    with pytest.raises(SystemExit) as err:
        run_cli(monkeypatch, 'study-report', '1', '--markdown', str(target))
    assert err.value.code == 1 and 'MOCK run' in capsys.readouterr().err and not target.exists()
    run_cli(monkeypatch, 'study-report', '1', '--markdown', str(target), '--allow-mock')
    assert target.read_text(encoding='utf-8').startswith('# MOCK - NOT A RESULT')
    capsys.readouterr()
    usage_before = count('api_usage')
    run_cli(monkeypatch, 'study-dry-run', '--n', '2', '--seed', '1')
    out = capsys.readouterr().out
    assert out.startswith(study.MOCK_BANNER) and out.rstrip().endswith(study.MOCK_BANNER)
    assert 'NO MODEL WAS CALLED' in out and 'DRY-RUN-FAKE-MODEL' in out
    assert len(calls) == 32 and count('api_usage') == usage_before  # the dry run called nothing and metered nothing
    with pytest.raises(SystemExit) as err:
        run_cli(monkeypatch, 'study-report', '77')
    assert err.value.code == 1


# ---- 10. Process separation ----------------------------------------------------------------------

def test_study_keeps_the_engine_and_keys_out_of_web_processes():
    source = (REPO / 'live_genesis' / 'study.py').read_text(encoding='utf-8')
    # core imports study for its schema, and the web apps import core: no module-level engine import.
    assert [line for line in source.splitlines() if line.startswith(('from . import engine', 'from .engine', 'import openai', 'import anthropic'))] == []
    assert source.count('    from . import engine') == 1
    # Provider keys are loaded in exactly one place here: the paid `study-run` command.
    assert source.count('load_provider_credentials()') == 1
    before = source.split('load_provider_credentials()')[0]
    assert 'elif args.command == "study-run":' in [line.strip() for line in before.splitlines()[-4:-1]]
    assert 'CONSTITUTION' not in source.replace('not the resident CONSTITUTION', '')
