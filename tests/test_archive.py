"""Answers Over Time (live_genesis/archive.py, archive_pages.py). Offline: every model call is a
replaced `engine.infer`; nothing here contacts a provider."""
import hashlib
import json
import re
import sqlite3
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from live_genesis import archive, archive_pages, cli, controls, core, engine, snapshot, web

REPO = Path(__file__).resolve().parents[1]
STATIC = REPO / 'live_genesis' / 'static'
HOSTILE = '<script>alert(1)</script> " onmouseover="x" \'><img src=x onerror=y> &amp; </div>'
MODELS = 'model-a=openai:gpt-test-1,model-b=openai:gpt-test-2'
QUESTIONS = REPO / 'live_genesis' / 'archive_questions.json'


def question_file(tmp_path, monkeypatch, status='locked', edit=None):
    data = json.loads(QUESTIONS.read_text(encoding='utf-8'))
    data['status'] = status
    if edit:
        edit(data)
    path = tmp_path / 'questions.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setattr(archive, 'QUESTIONS_FILE', path)
    return path


TREND = [('What is the newest iPhone?', 'new phones', '2026-05-18'), ('What is the price of gold today?', 'gold price', '2026-07-20'),
         ('Who is the president of the United States?', 'White House', '2026-09-21'),
         ('Which company is the most valuable in the world right now?', 'AI stocks', '2026-09-21')]


def trending_file(tmp_path, monkeypatch, count=0, status='locked', edit=None):
    """A trending question file with the first `count` questions of TREND. The `ready` fixture
    uses an empty one, so the core tests do not depend on the checked-in drafts the owner edits."""
    data = {'question_set': 'trending', 'status': status,
            'groups': [{'name': 'Trending', 'why': 'Taken from what people were searching for.'}],
            'questions': [{'id': f'T{n:03d}', 'group': 'Trending', 'text': text, 'rationale': f'Why question {n} is worth asking.',
                           'prompt': 'Answer in one or two sentences.\n\n' + text, 'added': added, 'topic': topic,
                           'trend_source': f'Google Trends US, {added}'} for n, (text, topic, added) in enumerate(TREND[:count], 1)]}
    if edit:
        edit(data)
    path = tmp_path / 'trending.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setattr(archive, 'TRENDING_FILE', path)
    return path


def fake(reply, calls=None):
    """A stand-in for engine.infer. `reply` is text, a Completion, an exception, or a function of
    (model, prompt, call number)."""
    seen = calls if calls is not None else []

    def infer(provider, model, prompt, *, system=None, max_output_tokens=None):
        seen.append({'provider': provider, 'model': model, 'prompt': prompt, 'system': system, 'max': max_output_tokens})
        value = reply(model, prompt, len(seen)) if callable(reply) else reply
        if isinstance(value, BaseException):
            raise value
        return value if isinstance(value, engine.Completion) else engine.Completion(value, 40, 12, 'end')
    return infer


def as_real(monkeypatch, reply, calls=None):
    """Replace the adapter AND the identity it is compared with: the run is then stored as a real
    engine run, as on the operator's machine, while still calling nothing."""
    infer = fake(reply, calls)
    monkeypatch.setattr(engine, 'infer', infer)
    monkeypatch.setattr(engine, 'REAL_INFER', infer)


@pytest.fixture
def ready(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', MODELS)
    monkeypatch.delenv('LIVE_ARCHIVE_MAX_OUTPUT_TOKENS', raising=False)
    question_file(tmp_path, monkeypatch)
    trending_file(tmp_path, monkeypatch)
    return isolated_db


def rows(sql, *args):
    with core.connect() as cx:
        return [dict(r) for r in cx.execute(sql, args)]


# ---- normalisation and the changed flag ----------------------------------------------------

def test_normalise_ignores_case_spacing_and_edge_punctuation_only():
    same = ['Canberra.', '  canberra ', '"CANBERRA"', '“Canberra”!', 'Canberra\n', 'Ｃanberra']   # NFKC: full-width C
    assert {archive.normalise(t) for t in same} == {'canberra'}
    assert archive.normalise('It  is\t2026.') == archive.normalise('it is 2026')
    assert archive.normalise('Straße') == 'strasse'                       # case-folding, not lower()
    assert archive.normalise("can't") != archive.normalise('cant')              # inner punctuation is kept
    assert archive.normalise('100.5') != archive.normalise('1005')
    assert archive.normalise('...') == '' and archive.normalise('') == ''
    assert archive.answer_hash('Canberra.') == hashlib.sha256(b'canberra').hexdigest()
    assert 'NFKC' in archive.CHANGED_RULE and 'nothing about which answer is right' in archive.CHANGED_RULE


def test_changed_flag_first_identical_cosmetic_and_missing_run(ready, monkeypatch):
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', 'model-a=openai:gpt-test-1')
    script = [engine.Completion('Canberra.', 40, 5, 'end'),            # run 1: first answer -> None
              engine.Completion('  canberra ', 40, 5, 'end'),          # run 2: case/space/punctuation only -> same
              engine.Completion('Canb', 40, 120, 'length'),            # run 3: cut off -> no answer
              engine.Completion('Sydney', 40, 5, 'end'),               # run 4: compared with run 2, not run 3 -> changed
              engine.Completion('Sydney', 40, 5, 'end')]               # run 5: identical -> same
    archive.lock()
    for number, completion in enumerate(script, 1):
        as_real(monkeypatch, completion)
        done = archive.run_archive(max_usd=1)
        assert done['run'] == number and done['status'] == 'complete' and not done['mock']
    got = rows("SELECT r.number,a.state,a.changed,a.reason,a.answer_text FROM archive_answers a JOIN archive_runs r ON r.id=a.run_id "
               "WHERE a.question_id='Q03' ORDER BY r.number")
    assert [(g['state'], g['changed']) for g in got] == [('answered', None), ('answered', 0), ('no_answer', None),
                                                         ('answered', 1), ('answered', 0)]
    assert got[2]['reason'] == archive.TRUNCATED and got[2]['answer_text'] is None       # a truncated reply is never an answer
    assert got[1]['answer_text'] == 'canberra'                                            # stored stripped, not normalised
    stored = rows("SELECT normalised,answer_sha256,source,reservation_id,provider,model,agent_id FROM archive_answers WHERE id=1")[0]
    assert stored == {'normalised': 'canberra', 'answer_sha256': hashlib.sha256(b'canberra').hexdigest(), 'source': 'engine',
                      'reservation_id': 1, 'provider': 'openai', 'model': 'gpt-test-1', 'agent_id': None}


def test_classify_empty_long_and_withheld():
    assert archive.classify('  ') == ('no_answer', archive.EMPTY, None)
    assert archive.classify('!?') == ('no_answer', archive.EMPTY, None)
    assert archive.classify('x' * (archive.ANSWER_CHARS + 1)) == ('no_answer', archive.TOO_LONG, None)
    assert archive.classify('fine', 'length') == ('no_answer', archive.TRUNCATED, None)
    state, reason, text = archive.classify('Write to someone@example.org for it.')
    assert (state, text) == ('withheld', None) and reason == 'looks like an email address'
    assert archive.classify('zero​width')[:2] == ('withheld', 'invisible or control character')
    assert archive.classify(' Seven. ') == ('answered', '', 'Seven.')


# ---- the question file and the lock -----------------------------------------------------------

def test_checked_in_question_file_is_the_locked_list():
    data = archive.load_questions(REPO / 'live_genesis' / 'archive_questions.json')
    assert data['status'] == 'locked' and len(data['questions']) == 20 and len(data['groups']) == 4
    assert [g['name'] for g in data['groups']] == ['Everyday facts', 'Health and home', 'Right now', 'About the AI']
    for group in data['groups']:
        assert sum(q['group'] == group['name'] for q in data['questions']) == 5
    for q in data['questions']:
        assert q['prompt'] == 'Answer in one or two sentences.\n\n' + q['text']
        assert not re.search(r'conscious|feel|sentien|emotion|inner experience|diagnos', q['text'] + q['rationale'], re.I), q['id']
    assert [q['id'] for q in data['questions']] == [f'Q{n:02d}' for n in range(1, 21)]
    # Every draft question has a scripted demo answer for each fictional model.
    assert {q['text'] for q in data['questions']} == set(archive.DEMO_SCRIPT)
    assert all(set(per) == set(archive.DEMO_MODELS) for per in archive.DEMO_SCRIPT.values())


def test_hash_ignores_layout_and_status_but_not_wording(tmp_path, monkeypatch):
    base = archive.load_questions()['sha256']
    path = question_file(tmp_path, monkeypatch, status='locked')
    assert archive.load_questions()['sha256'] == base
    path.write_text(json.dumps(json.loads(path.read_text(encoding='utf-8')), indent=4) + '\r\n')
    assert archive.load_questions()['sha256'] == base
    question_file(tmp_path, monkeypatch, edit=lambda d: d['questions'][0].update(prompt=d['questions'][0]['prompt'] + ' Please.'))
    assert archive.load_questions()['sha256'] != base


@pytest.mark.parametrize('edit', [
    lambda d: d.update(status='final'), lambda d: d['groups'].pop(), lambda d: d['questions'][1].update(id='Q01'),
    lambda d: d['questions'][0].update(id='../x'), lambda d: d['questions'][0].update(group='Other'),
    lambda d: d['questions'][0].update(prompt='Something else entirely.'),
    lambda d: d['questions'][0].update(rationale='mail me at someone@example.org'),
    lambda d: d.update(questions=[]), lambda d: d.update(question_set='Not A Name')])
def test_malformed_question_files_are_refused(tmp_path, monkeypatch, edit):
    question_file(tmp_path, monkeypatch, edit=edit)
    with pytest.raises(archive.ArchiveRefused):
        archive.load_questions()


def test_lock_needs_locked_status_is_audited_and_happens_once(ready, tmp_path, monkeypatch):
    question_file(tmp_path, monkeypatch, status='draft')
    with pytest.raises(archive.ArchiveRefused, match='still says "status": "draft"'):
        archive.lock()
    assert rows('SELECT * FROM archive_sets') == []
    question_file(tmp_path, monkeypatch)
    done = archive.lock()
    assert done['questions'] == 20 and done['sha256'] == archive.load_questions()['sha256']
    assert rows('SELECT COUNT(*) n FROM archive_questions')[0]['n'] == 20
    audit = rows("SELECT operation FROM operator_audit WHERE operation LIKE 'archive-lock%'")
    assert len(audit) == 1 and done['sha256'] in audit[0]['operation']
    with pytest.raises(archive.ArchiveRefused, match='Already locked'):
        archive.lock()
    question_file(tmp_path, monkeypatch, edit=lambda d: d['questions'][0].update(rationale='Reworded later.'))
    with pytest.raises(archive.ArchiveRefused, match='never change once locked'):
        archive.lock()
    assert rows('SELECT COUNT(*) n FROM archive_sets')[0]['n'] == 1


def test_real_answers_need_the_lock_and_the_unchanged_file(ready, tmp_path, monkeypatch):
    calls = []
    as_real(monkeypatch, 'Seven.', calls)
    with pytest.raises(archive.ArchiveRefused, match='not locked'):
        archive.run_archive(max_usd=1)
    archive.lock()
    question_file(tmp_path, monkeypatch, edit=lambda d: d['questions'][4].update(text='At what temperature does water boil?',
                                                                               prompt='At what temperature does water boil?'))
    with pytest.raises(archive.ArchiveRefused, match='does not match the locked question list'):
        archive.run_archive(max_usd=1)
    question_file(tmp_path, monkeypatch, status='draft')          # same questions, but the file no longer says locked
    with pytest.raises(archive.ArchiveRefused, match='does not match the locked question list'):
        archive.run_archive(max_usd=1)
    assert calls == [] and rows('SELECT * FROM archive_runs') == [] and rows('SELECT * FROM budget_reservations') == []
    question_file(tmp_path, monkeypatch)
    assert archive.run_archive(max_usd=1)['answers'] == {'answered': 40, 'no_answer': 0, 'withheld': 0}
    # The database enforces it too: a real run row cannot exist for another hash.
    with core.connect() as cx:
        with pytest.raises(sqlite3.IntegrityError, match='needs the locked question set'):
            cx.execute("INSERT INTO archive_runs(number,kind,question_set,set_sha256,started_at) VALUES (9,'real','basic-1','0',?)",
                       (core.utcnow(),))


def test_archive_tables_are_append_only(ready, monkeypatch):
    archive.lock()
    as_real(monkeypatch, 'Seven.')
    archive.run_archive(max_usd=1)
    for sql, message in (
            ("UPDATE archive_answers SET answer_text='x' WHERE id=1", 'archive_answers is append-only'),
            ("UPDATE archive_answers SET changed=1 WHERE id=1", 'archive_answers is append-only'),
            ("DELETE FROM archive_answers WHERE id=1", 'archive_answers is append-only'),
            ("DELETE FROM archive_runs", 'archive_runs is append-only'),
            ("UPDATE archive_runs SET status='incomplete'", 'can only be closed, once'),
            ("UPDATE archive_runs SET kind='demo'", 'can only be closed, once'),
            ("UPDATE archive_sets SET sha256='0'", 'archive_sets is append-only'),
            ("DELETE FROM archive_sets", 'archive_sets is append-only'),
            ("UPDATE archive_questions SET text='x'", 'archive_questions is append-only'),
            ("DELETE FROM archive_questions", 'archive_questions is append-only')):
        with core.connect() as cx:
            with pytest.raises(sqlite3.IntegrityError, match=message):
                cx.execute(sql)
    with core.connect() as cx:    # one answer per (run, question, answerer); sources cannot cross run kinds
        with pytest.raises(sqlite3.IntegrityError):
            cx.execute("INSERT INTO archive_answers(run_id,question_set,question_id,answerer,model,source,reservation_id,state,"
                       "answer_text,created_at) VALUES (1,'basic-1','Q01','model-a','m','engine',1,'answered','x','now')")
        for source in ('demo', 'mock'):
            with pytest.raises(sqlite3.IntegrityError, match='does not match the kind of its run'):
                cx.execute("INSERT INTO archive_answers(run_id,question_set,question_id,answerer,model,source,state,answer_text,"
                           "created_at) VALUES (1,'basic-1','Q01','other','m',?,'answered','x','now')", (source,))


# ---- the paid run: gates, prompt, budget ---------------------------------------------------------

def test_models_setting_is_validated(ready, monkeypatch):
    assert archive.answerers() == [{'label': 'model-a', 'provider': 'openai', 'model': 'gpt-test-1'},
                                   {'label': 'model-b', 'provider': 'openai', 'model': 'gpt-test-2'}]
    assert [a['label'] for a in archive.answerers('model-b')] == ['model-b']
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', 'local=openai-compatible:vendor/some-model-1.0')
    assert archive.answerers()[0] == {'label': 'local', 'provider': 'openai-compatible', 'model': 'vendor/some-model-1.0'}
    for bad in ('', 'model-a', 'model-a=openai', 'Model A=openai:x', 'a=madeup:x', 'a=openai:has space', 'a=openai:x,a=openai:y',
                'a=openai:evil.example.org', ','.join(f'm{n}=openai:x' for n in range(9))):
        monkeypatch.setenv('LIVE_ARCHIVE_MODELS', bad)
        with pytest.raises(archive.ArchiveRefused):
            archive.answerers()
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', MODELS)
    with pytest.raises(archive.ArchiveRefused, match='--models'):
        archive.answerers('model-z')


def test_plan_arithmetic_and_output_limit(ready, monkeypatch):
    planned = archive.plan()
    assert planned['calls'] == 40 and planned['max_output_tokens'] == 120
    q = planned['questions'][0]
    tokens_in = len(archive.SYSTEM_LINE.encode()) + len(q['prompt'].encode()) + 200
    assert planned['holds']['Q01'] == pytest.approx(tokens_in * 0.20 / 1e6 + 120 * 1.20 / 1e6)
    assert planned['worst_case_usd'] == pytest.approx(2 * sum(planned['holds'].values()))
    text = archive.render_plan(planned, locked=None)
    assert 'python -m live_genesis.cli archive-run --max-usd 0.01' in text and 'NOT locked' in text and 'not a provider invoice' in text
    monkeypatch.setenv('LIVE_ARCHIVE_MAX_OUTPUT_TOKENS', '5000')
    assert archive.max_output_tokens() == 1000
    monkeypatch.setenv('LIVE_ARCHIVE_MAX_OUTPUT_TOKENS', '1')
    assert archive.max_output_tokens() == 40
    monkeypatch.setenv('LIVE_ARCHIVE_MAX_OUTPUT_TOKENS', 'many')
    with pytest.raises(archive.ArchiveRefused):
        archive.plan()
    monkeypatch.delenv('LIVE_ARCHIVE_MAX_OUTPUT_TOKENS')
    monkeypatch.setenv('LIVE_INPUT_USD_PER_M', '0')
    with pytest.raises(controls.ConfigError):
        archive.plan()


def test_zero_rates_never_price_a_hosted_model(ready, monkeypatch):
    monkeypatch.setenv('LIVE_PROVIDER', 'openai-compatible')
    monkeypatch.setenv('LIVE_COMPAT_BASE_URL', 'http://localhost:11434/v1')
    monkeypatch.setenv('LIVE_INPUT_USD_PER_M', '0')
    monkeypatch.setenv('LIVE_OUTPUT_USD_PER_M', '0')
    with pytest.raises(archive.ArchiveRefused, match='Zero rates'):
        archive.plan()                                             # MODELS lists hosted openai models
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', 'local=openai-compatible:some-local-model')
    assert archive.plan()['worst_case_usd'] == 0


def test_refused_without_key_optin_or_valid_cap_before_any_call(ready, monkeypatch):
    calls = []
    as_real(monkeypatch, 'Seven.', calls)
    archive.lock()
    monkeypatch.delenv('OPENAI_API_KEY')
    with pytest.raises(archive.ArchiveRefused, match='OPENAI_API_KEY missing.*archive-dry-run'):
        archive.run_archive(max_usd=1)
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    for value in ('NO', 'yes', ''):
        monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS', value)
        with pytest.raises(archive.ArchiveRefused, match='LIVE_ALLOW_PAID_CALLS=YES'):
            archive.run_archive(max_usd=1)
    monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS', 'YES')
    for cap in (0, -1, float('nan'), float('inf'), None, True, '1'):
        with pytest.raises(archive.ArchiveRefused, match='--max-usd'):
            archive.run_archive(max_usd=cap)
    worst = archive.plan()['worst_case_usd']
    with pytest.raises(archive.ArchiveRefused, match='exceeds --max-usd'):
        archive.run_archive(max_usd=worst * 0.99)
    monkeypatch.setenv('LIVE_SPEND_LIMIT_USD', str(worst * 0.5))
    with pytest.raises(archive.ArchiveRefused, match='does not fit under the local ceiling'):
        archive.run_archive(max_usd=1)
    monkeypatch.setenv('LIVE_SPEND_LIMIT_USD', '10')
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', '')
    with pytest.raises(archive.ArchiveRefused, match='No models configured'):
        archive.run_archive(max_usd=1)
    assert calls == [] and rows('SELECT * FROM archive_runs') == [] and rows('SELECT * FROM budget_reservations') == []


def test_prompt_is_exactly_the_question_and_calls_are_not_resident_actions(ready, monkeypatch):
    calls = []
    as_real(monkeypatch, 'Seven.', calls)
    archive.lock()
    before = {t: rows(f'SELECT COUNT(*) n FROM {t}')[0]['n'] for t in ('events', 'posts', 'rounds', 'round_actions', 'memory_notes',
                                                                         'action_provenance', 'engine_turns')}
    done = archive.run_archive(max_usd=1)
    questions = archive.load_questions()['questions']
    assert len(calls) == 40 and done['answers']['answered'] == 40
    assert [c['prompt'] for c in calls[:20]] == [q['prompt'] for q in questions]
    assert {c['system'] for c in calls} == {archive.SYSTEM_LINE} and '\n' not in archive.SYSTEM_LINE
    assert {c['max'] for c in calls} == {120}
    assert [c['model'] for c in calls] == ['gpt-test-1'] * 20 + ['gpt-test-2'] * 20
    # No earlier answer, no other model's answer and no persona ever reaches a prompt.
    as_real(monkeypatch, 'Eight.', calls)
    archive.run_archive(max_usd=1)
    assert all('Seven' not in c['prompt'] + c['system'] for c in calls[40:])
    assert before == {t: rows(f'SELECT COUNT(*) n FROM {t}')[0]['n'] for t in before}
    reservations = rows('SELECT agent_id,status,provider FROM budget_reservations')
    assert len(reservations) == 80 and {(r['agent_id'], r['status'], r['provider']) for r in reservations} == {(None, 'settled', 'openai')}
    assert done['estimated_usd'] == pytest.approx(40 * (40 * 0.20 + 12 * 1.20) / 1e6)
    assert core.budget_status()['estimated_usd'] == pytest.approx(2 * done['estimated_usd'], abs=1e-6)


def test_failed_inference_stops_the_run_and_blocks_the_next(ready, monkeypatch):
    calls = []
    as_real(monkeypatch, lambda model, prompt, n: RuntimeError('sk-secret-text-from-provider') if n == 3 else 'Seven.', calls)
    archive.lock()
    done = archive.run_archive(max_usd=1)
    assert done['status'] == 'incomplete' and len(calls) == 3 and done['answers']['answered'] == 2
    assert 'kept as unconfirmed (reservation 3)' in done['stop_reason'] and 'reconcile --receipt 3' in done['stop_reason']
    assert 'sk-secret' not in json.dumps(done) and 'sk-secret' not in json.dumps(rows('SELECT * FROM archive_runs'))
    assert rows('SELECT status FROM budget_reservations WHERE id=3')[0]['status'] == 'unconfirmed'
    with pytest.raises(archive.ArchiveRefused, match='Unconfirmed provider charge'):
        archive.run_archive(max_usd=1)
    assert len(calls) == 3


def test_provider_not_ready_releases_and_bad_usage_is_unconfirmed(ready, monkeypatch):
    archive.lock()
    as_real(monkeypatch, engine.ProviderNotReady('local setup'))
    done = archive.run_archive(max_usd=1)
    assert done['status'] == 'incomplete' and 'nothing was sent' in done['stop_reason']
    assert rows('SELECT status FROM budget_reservations')[0]['status'] == 'released'
    as_real(monkeypatch, engine.Completion('Seven.', -1, 5, 'end'))
    done = archive.run_archive(max_usd=1)
    assert done['status'] == 'incomplete' and 'usage was missing or invalid' in done['stop_reason']
    assert rows('SELECT status FROM budget_reservations ORDER BY id DESC LIMIT 1')[0]['status'] == 'unconfirmed'
    assert rows('SELECT COUNT(*) n FROM archive_answers')[0]['n'] == 0


def test_usage_above_the_hold_stops_the_run(ready, monkeypatch):
    archive.lock()
    as_real(monkeypatch, engine.Completion('Seven.', 5000, 5000, 'end'))
    done = archive.run_archive(max_usd=1)
    assert done['status'] == 'incomplete' and 'exceeded the hold' in done['stop_reason'] and done['answers']['answered'] == 1


def test_withheld_answer_keeps_category_and_no_text(ready, monkeypatch):
    archive.lock()
    as_real(monkeypatch, lambda model, prompt, n: 'Ask at someone@example.org.' if n == 1 else 'Seven.')
    archive.run_archive(max_usd=1)
    first = rows('SELECT state,reason,answer_text,normalised,answer_sha256,changed FROM archive_answers WHERE id=1')[0]
    assert first == {'state': 'withheld', 'reason': 'looks like an email address', 'answer_text': None, 'normalised': None,
                     'answer_sha256': None, 'changed': None}
    assert 'example.org' not in json.dumps(rows('SELECT * FROM archive_answers'))
    page = archive_pages.render_question(archive.public_payload(), 'Q01')
    assert 'Withheld by content check (looks like an email address)' in page and 'example.org' not in page


# ---- mock, dry run, demo: kept apart ------------------------------------------------------------

def test_replaced_infer_and_dry_run_are_mock_and_never_public(ready, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, 'infer', fake('MOCKED-REPLY-TEXT'))      # REAL_INFER untouched: this is a mock run
    done = archive.run_archive(max_usd=1)                                  # needs no lock: nothing real is recorded
    assert done['mock'] and done['mock_kind'] == 'patched-infer' and done['kind'] == 'mock'
    dry = archive.dry_run()
    assert dry['mock'] and dry['mock_kind'] == 'dry-run-fake-model' and dry['run'] == 2 and dry['answers']['answered'] == 40
    assert {r['source'] for r in rows('SELECT DISTINCT source FROM archive_answers')} == {'mock'}
    assert rows("SELECT COUNT(*) n FROM budget_reservations")[0]['n'] == 40          # the dry run reserved nothing
    payload = archive.public_payload()
    assert payload['answers'] == [] and payload['runs'] == [] and payload['answerers'] == [] and payload['demo_only'] is True
    pages = archive_pages.render_site(payload)
    assert not any('MOCKED-REPLY' in page or 'DRY-RUN' in page for page in pages.values())
    assert 'No answers recorded yet' in pages['index.html']
    # A real run numbers from 1 regardless of mock runs, and mock rows never reach an export.
    archive.lock()
    as_real(monkeypatch, 'Seven.')
    assert archive.run_archive(max_usd=1)['run'] == 1
    out = tmp_path / 'published'
    snapshot.export_published(out)
    exported = (out / 'archive.json').read_text(encoding='utf-8')
    assert 'MOCKED-REPLY' not in exported and 'DRY-RUN' not in exported and '"mock"' not in exported
    # Even a payload that smuggles a mock row in is not rendered.
    payload = archive.public_payload()
    payload['answers'][0] = {**payload['answers'][0], 'source': 'mock', 'text': 'SMUGGLED-MOCK'}
    assert not any('SMUGGLED-MOCK' in page for page in archive_pages.render_site(payload).values())


def test_dry_run_needs_no_key_no_optin_and_no_models(isolated_db, tmp_path, monkeypatch):
    trending_file(tmp_path, monkeypatch)
    monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS', 'NO')
    monkeypatch.delenv('LIVE_ARCHIVE_MODELS', raising=False)
    monkeypatch.setattr(engine, 'infer', fake(AssertionError('a dry run calls no model')))
    first, second, third = archive.dry_run(), archive.dry_run(), archive.dry_run()
    assert first['answers']['answered'] == 60 and third['run'] == 3
    assert rows('SELECT * FROM budget_reservations') == [] and rows('SELECT * FROM api_usage') == []
    changed = rows('SELECT changed,COUNT(*) n FROM archive_answers GROUP BY changed')
    assert {c['changed'] for c in changed} == {None, 0, 1}                 # the fake produces same and changed cells


def test_demo_archive_is_scripted_dated_monthly_and_separate(ready, monkeypatch):
    monkeypatch.setattr(engine, 'infer', fake(AssertionError('the demo calls no model')))
    done = archive.demo_archive()
    assert done == {'runs': 8, 'answerers': ['model-a', 'model-b', 'model-c'], 'answers': 8 * 20 * 3 - 2 + (6 + 4 + 2) * 3,
                    'trending_questions': 3}
    runs = rows('SELECT number,kind,status,started_at FROM archive_runs ORDER BY number')
    assert [r['started_at'][:7] for r in runs] == [f'2026-{m:02d}' for m in range(4, 12)]
    assert {(r['kind'], r['status']) for r in runs} == {('demo', 'complete')}
    assert {(r['source'], r['state']) for r in rows('SELECT DISTINCT source,state FROM archive_answers')} == {('demo', 'answered')}
    assert rows('SELECT * FROM budget_reservations') == [] and rows('SELECT * FROM api_usage') == []
    date = rows("SELECT a.changed,a.model FROM archive_answers a JOIN archive_runs r ON r.id=a.run_id "
                "WHERE a.answerer='model-a' AND a.question_id='Q11' ORDER BY r.number")
    assert [d['changed'] for d in date] == [None, 0, 0, 0, 1, 0, 0, 1]
    assert [d['model'] for d in date] == ['model-a-1'] * 4 + ['model-a-2'] * 4
    payload = archive.public_payload()
    assert payload['demo_only'] is True and payload['locked'] is False and all(r['demo'] for r in payload['runs'])
    with pytest.raises(archive.ArchiveRefused, match='already exists'):
        archive.demo_archive()
    # Real answers are never mixed into a database with scripted ones.
    with pytest.raises(archive.ArchiveRefused, match='scripted demo answers'):
        archive.lock()
    as_real(monkeypatch, 'Seven.')
    with pytest.raises(archive.ArchiveRefused, match='not locked'):
        archive.run_archive(max_usd=1)


def test_demo_archive_is_refused_next_to_real_data(ready, monkeypatch):
    archive.lock()
    with pytest.raises(archive.ArchiveRefused, match='use a fresh DB'):
        archive.demo_archive()                                            # a locked list alone marks the database as real
    as_real(monkeypatch, 'Seven.')
    archive.run_archive(max_usd=1)
    with pytest.raises(archive.ArchiveRefused, match='use a fresh DB'):
        archive.demo_archive()
    assert rows("SELECT COUNT(*) n FROM archive_runs WHERE kind='demo'")[0]['n'] == 0


def test_demo_archive_is_refused_after_real_world_activity(isolated_db):
    agent = core.seed_agents()[0]
    core.submit_action(1, {'action': 'post', 'topic': 'research', 'title': 'A real post', 'body': 'Recorded by an outside client.'})
    with pytest.raises(archive.ArchiveRefused, match='use a fresh DB'):
        archive.demo_archive()
    assert agent['name']


# ---- pages -----------------------------------------------------------------------------------------

def hostile_payload():
    payload = archive.empty_payload()
    payload.update(answerers=['model-a', 'model-b'], demo_only=False, locked=True, locked_at='2026-11-01T00:00:00+00:00',
                   runs=[{'number': 1, 'date': '2026-10-03T09:00:00+00:00', 'demo': False, 'asked': {'core': 20, 'trending': 0}},
                         {'number': 2, 'date': '2026-11-02T09:00:00+00:00', 'demo': False, 'asked': {'core': 20, 'trending': 0}}])
    payload['questions'][0].update(text=HOSTILE, rationale=HOSTILE, prompt=HOSTILE)
    payload['groups'][0].update(why=HOSTILE)
    answer = dict(question='Q01', reason='', created_at='2026-11-02T09:00:00+00:00')
    payload['answers'] = [
        {**answer, 'id': 1, 'run': 1, 'answerer': 'model-a', 'model': 'm-1', 'source': 'engine', 'state': 'answered', 'text': 'Plain.', 'changed': None},
        {**answer, 'id': 2, 'run': 2, 'answerer': 'model-a', 'model': HOSTILE, 'source': 'engine', 'state': 'answered', 'text': HOSTILE, 'changed': True},
        {**answer, 'id': 3, 'run': 2, 'answerer': 'model-b', 'model': 'm-2', 'source': 'api', 'state': 'withheld', 'text': None, 'changed': None,
         'reason': HOSTILE}]
    return payload


ALLOWED_TAGS = {'html', 'head', 'meta', 'title', 'link', 'body', 'div', 'span', 'a', 'img', 'nav', 'header', 'h1', 'h2', 'p',
                'strong', 'section', 'article', 'main', 'aside', 'pre', 'footer'}
ALLOWED_ATTRS = {'lang', 'charset', 'name', 'content', 'http-equiv', 'rel', 'type', 'href', 'class', 'src', 'alt', 'width', 'height',
                 'aria-label', 'aria-current', 'role', 'id', 'tabindex'}


class Tags(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.urls, self.text = [], [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        self.urls += [value for name, value in attrs if name in ('href', 'src')]

    def handle_data(self, data):
        self.text.append(data)


def page_urls(page):
    parser = Tags(); parser.feed(page)
    return parser.urls


def assert_page_is_safe(page):
    """Parsed as a browser would: no script, no inline style or handler (the Content-Security-Policy
    would block them), only known elements and attributes, and only relative same-site addresses."""
    parser = Tags(); parser.feed(page)
    for tag, attrs in parser.tags:
        assert tag in ALLOWED_TAGS, tag
        assert set(attrs) <= ALLOWED_ATTRS, (tag, sorted(attrs))
    assert parser.urls
    for url in parser.urls:
        assert not re.match(r'[a-z][a-z0-9+.-]*:|//|/', url, re.I), url
    metas = [attrs for tag, attrs in parser.tags if tag == 'meta' and attrs.get('http-equiv') == 'Content-Security-Policy']
    assert len(metas) == 1 and metas[0]['content'] == web.CONTENT_SECURITY_POLICY and "script-src 'self'" in metas[0]['content']
    return parser


def test_hostile_text_is_escaped_on_both_pages():
    payload = hostile_payload()
    index, question = archive_pages.render_index(payload), archive_pages.render_question(payload, 'Q01')
    for page in (index, question):
        parsed = assert_page_is_safe(page)
        assert [tag for tag, _ in parsed.tags].count('img') == 1 and 'onmouseover="' not in page       # only the mark
        assert HOSTILE in ''.join(parsed.text)                          # the browser shows the text itself, as text
        assert '&lt;script&gt;alert(1)&lt;/script&gt; &quot; onmouseover=&quot;x&quot; &#x27;&gt;&lt;img src=x onerror=y&gt; &amp;amp; &lt;/div&gt;' in page
    assert question.count('&lt;script&gt;') >= 5          # heading, rationale, prompt, description, answer, model id, reason
    # Tag balance survives the hostile text: every opened div is closed.
    for page in (index, question):
        assert page.count('<div') == page.count('</div>') and page.count('<span') == page.count('</span>')


def test_pages_for_the_demo_archive(ready):
    archive.demo_archive()
    payload = archive.public_payload()
    pages = archive_pages.render_site(payload)
    core_ids, trend_ids = [f'Q{n:02d}' for n in range(1, 21)], ['T001', 'T002', 'T003']
    assert set(pages) == ({'index.html', 'trending.html'} | {f'q/{i}.html' for i in core_ids} | {f't/{i}.html' for i in trend_ids}
                          | {f'card/{i}.html' for i in core_ids + trend_ids})
    index, date = pages['index.html'], pages['q/Q11.html']
    for page in pages.values():
        assert_page_is_safe(page)
        assert 'Scripted demo answers and dates · no model was called' in page          # page-level tag on every page
        assert not re.search(r'\bround', page, re.I)                                     # time is told in runs and dates
        assert not re.search(r'agents?[- ]only|no humans|humans (?:may|can)not|software agents only', page, re.I)
        assert 'no payments' in page.lower()
    assert 'Simple questions, asked again every month.' in index and 'How the record is kept' in index
    assert 'One square per run, oldest on the left.' in index and 'It does not say which answer is right.' in index
    assert index.count('class="strip"') == 60 and index.count('role="img"') == 60
    assert 'aria-label="model-a: 2 changes across 8 runs"' in index
    assert 'aria-label="model-c: 1 change across 8 runs, 1 without an answer"' in index
    assert 'Last change: model-a, Nov 2026' in index and 'No change in any model across 8 runs' in index
    assert [g for g in ('Everyday facts', 'Health and home', 'Right now', 'About the AI') if f'<h2>{g}</h2>' in index] == \
        ['Everyday facts', 'Health and home', 'Right now', 'About the AI']
    assert 'href="q/Q11.html"' in index and 'archive-mark.svg?v=' in index and 'rel="icon"' in index
    assert re.search(r'href="\.\./static/archive\.css\?v=[0-9a-f]{12}"', index)
    assert 'This list of twenty questions is a draft' in index
    # The question page: newest first, one column per answerer, every cell labelled as scripted.
    assert date.index('Nov 2026') < date.index('Oct 2026') < date.index('Apr 2026')
    assert date.count('class="cell cell-changed"') == 3 and date.count('>CHANGED<') == 3
    assert date.count('class="cell cell-same"') == 20 and date.count('class="cell cell-none"') == 1
    assert date.count('· scripted demo') == 23 and 'server call</span>' not in date
    assert 'model-a-2 · scripted demo · new model ID' in date and '1 Nov 2026' in date
    assert '<pre class="prompt mono">Answer in one or two sentences.\n\nWhat is today&#x27;s date?</pre>' in date
    assert 'This list is generated from the comparison. No model wrote it.' in date
    assert 'model-a&#x27;s answer changed. Its model ID changed from model-a-1 to model-a-2.' in date
    assert 'model-b gave the same answer in every run recorded.' in date
    assert '<strong>self-declared</strong> means an outside client sent the answer' in date and '<strong>scripted demo</strong>' in date
    assert re.search(r'href="\.\./\.\./static/archive\.css\?v=[0-9a-f]{12}"', date) and 'href="../index.html"' in date
    with pytest.raises(KeyError):
        archive_pages.render_question(payload, 'Q99')
    with pytest.raises(ValueError, match='malformed'):
        archive_pages.render_site({**payload, 'questions': [{**payload['questions'][0], 'id': '../../etc'}]})


def test_archive_stylesheet_uses_tokens_local_fonts_and_no_gradient():
    css = (STATIC / 'archive.css').read_text(encoding='utf-8')
    assert 'gradient' not in css and 'box-shadow' not in css and '@import' not in css and 'http' not in css
    for token in ('#1b1d36', '#4338ca', '#c9cdf0', '#f4f4fb', '#312e81'):
        assert css.count(token) == 1, token                                # each colour is defined once, as a custom property
    assert css.count('#ffffff') == 2                                       # the ground, and text on dark panels
    rules = css.split(':root', 1)[1].split('}', 1)[1]                     # everything after the token block
    assert not re.findall(r'#[0-9a-fA-F]{3,8}\b', rules) and 'var(--changed)' in rules and 'var(--same)' in rules
    mark = (STATIC / 'archive-mark.svg').read_text(encoding='utf-8')
    assert mark.count('<rect') == 3 and mark.count('#c9cdf0') == 2 and mark.count('#312e81') == 1 and '<script' not in mark
    assert archive_pages.MAX_COLUMNS == 18 and all(f'.grid.n-{n} ' in css for n in range(1, 19))


def test_snapshot_holds_the_archive_pages_and_links_them(ready, tmp_path, monkeypatch):
    core.seed_agents()
    cli.demo()
    archive.demo_archive()
    out = tmp_path / 'site'
    info = snapshot.build(out)
    assert info['demo_only'] is True
    assert (out / 'archive' / 'index.html').is_file() and len(list((out / 'archive' / 'q').glob('Q*.html'))) == 20
    assert json.loads((out / 'archive.json').read_text(encoding='utf-8'))['answers_by_source'] == {'engine': 0, 'api': 0, 'demo': 514}
    home = (out / 'index.html').read_text(encoding='utf-8')
    assert home.count('href="archive/index.html"') == 1 and 'Answers over time</a>' in home
    for name in ('archive.css', 'archive-mark.svg', 'fonts/inter.woff2', 'fonts/bricolage-grotesque.woff2', 'fonts/jetbrains-mono.woff2'):
        assert (out / 'static' / name).is_file(), name
    page = (out / 'archive' / 'q' / 'Q11.html').read_text(encoding='utf-8')
    assert_page_is_safe(page)
    version = hashlib.sha256((STATIC / 'archive.css').read_bytes()).hexdigest()[:12]
    assert f'../../static/archive.css?v={version}"' in page
    # Every relative address in the archive pages resolves to a file inside the snapshot.
    for html in (out / 'archive').rglob('*.html'):
        for url in page_urls(html.read_text(encoding='utf-8')):
            target = (html.parent / url.split('#')[0].split('?')[0]).resolve()
            assert out.resolve() in target.parents or target == out.resolve(), url
            assert target.is_file() or (target.is_dir() and (target / 'index.html').is_file()), url


def test_snapshot_refuses_recorded_archive_answers_without_review(ready, tmp_path, monkeypatch):
    core.seed_agents()
    cli.demo()                                                # the world itself is scripted demo data
    archive.lock()
    key = core.seed_agents()                                   # no new agents: keys are not needed here
    assert key == []
    with core.connect() as cx:
        cx.execute("INSERT INTO archive_runs(number,kind,question_set,set_sha256,started_at) SELECT 1,'real',question_set,sha256,? FROM archive_sets",
                   (core.utcnow(),))
        archive._store(cx, 1, 'basic-1', 'Q01', 'Astra', model='some-model-1', source='api', state='answered', text='365.', agent_id=1)
    assert archive.public_payload()['demo_only'] is False
    with pytest.raises(RuntimeError, match='not scripted-demo-only'):
        snapshot.build(tmp_path / 'site')
    assert not (tmp_path / 'site').exists()


def test_both_web_apps_serve_the_same_pages(ready):
    from live_genesis.app import app as combined
    from live_genesis.observatory import app as readonly
    archive.demo_archive()
    payload = archive.public_payload()
    for application in (combined, readonly):
        with TestClient(application) as client:
            index = client.get('/archive/index.html')
            assert index.status_code == 200 and index.text == archive_pages.render_index(payload)
            assert 'text/html' in index.headers['content-type'] and "default-src 'none'" in index.headers['content-security-policy']
            assert client.get('/archive/q/Q11.html').text == archive_pages.render_question(payload, 'Q11')
            assert client.get('/archive', follow_redirects=False).headers['location'] == '/archive/index.html'
            for missing in ('/archive/q/Q99.html', '/archive/q/Q11', '/archive/q/..%2Fx.html', '/archive/q/Q1.html'):
                assert client.get(missing).status_code == 404, missing
            assert client.get('/static/archive.css').status_code == 200
            assert client.get('/static/fonts/inter.woff2').status_code == 200
    with TestClient(readonly) as client:
        assert client.post('/archive/index.html').status_code == 405


def test_archive_pages_answer_503_without_a_database(tmp_path, monkeypatch):
    from live_genesis.observatory import app as readonly
    monkeypatch.setenv('LIVE_DB', str(tmp_path / 'missing.sqlite'))
    with TestClient(readonly) as client:
        response = client.get('/archive/index.html')
        assert response.status_code == 503 and response.json() == {'detail': web.UNAVAILABLE}
    assert not (tmp_path / 'missing.sqlite').exists()


# ---- reviewed data: published/archive.json -------------------------------------------------------

def recorded_archive(monkeypatch, reply='Seven.'):
    core.seed_agents()
    archive.lock()
    as_real(monkeypatch, reply)
    return archive.run_archive(max_usd=1)


def retarget_archive(folder, edit):
    """Hand-edit archive.json and make the manifest agree with it again (hash and counts)."""
    data = json.loads((folder / 'archive.json').read_text(encoding='utf-8'))
    edit(data)
    payload = json.dumps(data, ensure_ascii=True, indent=1).encode('ascii')
    (folder / 'archive.json').write_bytes(payload)
    manifest = json.loads((folder / 'MANIFEST.json').read_text(encoding='utf-8'))
    manifest['archive'] = {**snapshot._archive_counts(data), 'sha256': hashlib.sha256(payload).hexdigest()}
    (folder / 'MANIFEST.json').write_text(json.dumps(manifest, indent=1))


def test_export_round_trip_with_archive_json(ready, tmp_path, monkeypatch):
    recorded_archive(monkeypatch, 'Café answers: seven.')
    out = tmp_path / 'published'
    manifest = snapshot.export_published(out)
    assert sorted(p.name for p in out.iterdir()) == ['MANIFEST.json', 'archive.json', 'overview.json']
    raw = (out / 'archive.json').read_bytes()
    assert raw.isascii() and b'Caf\\u00e9' in raw
    assert manifest['schema'] == 'live-genesis-published/5'
    assert manifest['archive'] == {'file': 'archive.json', 'sha256': hashlib.sha256(raw).hexdigest(), 'runs': 1, 'questions': 20,
                                   'answers_by_source': {'engine': 40, 'api': 0, 'demo': 0, 'mock': 0},
                                   'question_set': 'basic-1', 'set_hash': archive.load_questions()['sha256'],
                                   'trending': {'questions': 0, 'question_set': 'trending', 'version': 0,
                                                'set_hash': archive.load_questions(track='trending')['sha256']}}
    exported = json.loads(raw)
    assert exported == json.loads(json.dumps(archive.public_payload()))          # exactly the public payload
    assert not any(key in json.dumps(exported) for key in ('reservation', 'estimated_usd', 'input_tokens', 'normalised'))
    with pytest.raises(RuntimeError, match='not approved'):
        snapshot.check_published(out)
    with pytest.raises(RuntimeError, match='Not approved'):
        snapshot.approve_published(out)
    snapshot.approve_published(out, confirmed=True)
    assert snapshot.check_published(out)['reviewed'] is True
    assert any(line.startswith('archive answers id 1, field text') for line in snapshot.published_non_ascii(out))
    # Rendering from the reviewed files opens no database and gives the pages a database build gives.
    from_db = tmp_path / 'from-db'
    snapshot.build(from_db, allow_non_demo=True)
    monkeypatch.setenv('LIVE_DB', str(tmp_path / 'no-such.sqlite'))
    site = tmp_path / 'site'
    snapshot.build_from_data(site, out)
    assert (site / 'archive.json').read_bytes() == raw
    for page in ['index.html'] + [f'q/Q{n:02d}.html' for n in range(1, 21)]:
        assert (site / 'archive' / page).read_bytes() == (from_db / 'archive' / page).read_bytes(), page
    page = (site / 'archive' / 'q' / 'Q01.html').read_text(encoding='utf-8')
    assert 'Café answers: seven.' in page and 'gpt-test-1 · server call' in page and 'Recorded answers' in page
    assert 'scripted' not in page.lower() and 'run 1' in page


def test_published_archive_tamper_guard_and_mock_are_refused(ready, tmp_path, monkeypatch):
    recorded_archive(monkeypatch)
    out = tmp_path / 'published'
    snapshot.export_published(out)
    snapshot.approve_published(out, confirmed=True)
    good = {name: (out / name).read_bytes() for name in ('archive.json', 'MANIFEST.json')}

    def restore():
        for name, content in good.items():
            if (out / name).is_symlink():
                (out / name).unlink()
            (out / name).write_bytes(content)
        assert snapshot.check_published(out)['reviewed'] is True

    def refused(match):
        for call in (lambda: snapshot.check_published(out), lambda: snapshot.approve_published(out, confirmed=True),
                     lambda: snapshot.build_from_data(tmp_path / 'site', out)):
            with pytest.raises(RuntimeError, match=match):
                call()
        assert not (tmp_path / 'site').exists()
        restore()

    # 1. The file changed after export: the hash no longer matches.
    (out / 'archive.json').write_bytes(good['archive.json'].replace(b'Seven.', b'Eight.'))
    refused('archive.json does not match the sha256')
    # 2. The file is gone, or a symlink.
    (out / 'archive.json').unlink()
    refused('archive.json is missing')
    outside = tmp_path / 'outside.json'; outside.write_bytes(good['archive.json'])
    (out / 'archive.json').unlink()
    try:
        (out / 'archive.json').symlink_to(outside)
    except (OSError, NotImplementedError):   # Windows without the privilege: the remaining checks still run
        pass
    else:
        refused('archive.json is missing or unreadable')
        assert outside.read_bytes() == good['archive.json'] and not (out / 'archive.json').is_symlink()
    # 3. A hand edit with the manifest made to agree again still meets the content guards ...
    retarget_archive(out, lambda d: d['answers'][0].update(text='Write to someone@example.org'))
    refused('Content guards matched:\n  archive answers id 1, field text: looks like an email address')
    retarget_archive(out, lambda d: d['answers'][0].update(text='hidden​character'))
    refused('archive answers id 1, field text')
    retarget_archive(out, lambda d: d['questions'][0].update(rationale='pay at paypal.me/someone'))
    refused('payment or donation link')
    # ... and mock or scripted rows, bad ids and broken shapes are refused whatever the manifest says.
    retarget_archive(out, lambda d: d['answers'][0].update(source='mock'))
    refused('mock answers, which are never published')
    retarget_archive(out, lambda d: d['answers'][0].update(source='demo'))
    refused('scripted demo answers')
    retarget_archive(out, lambda d: d['runs'][0].update(demo=True))
    refused('scripted demo answers')
    retarget_archive(out, lambda d: d.update(locked=False))
    refused('question list is not locked')
    retarget_archive(out, lambda d: (d['questions'][0].update(id='../x'), [a.update(question='../x') for a in d['answers'] if a['question'] == 'Q01']))
    refused('question ids must be distinct and look like Q01')
    retarget_archive(out, lambda d: d['answers'].append(dict(d['answers'][0], id=999)))
    refused('two answers share one run')
    retarget_archive(out, lambda d: d['answers'][0].update(state='withheld'))
    refused("text does not fit its state")
    retarget_archive(out, lambda d: d.update(schema='something-else/9'))
    refused('unknown schema')
    # 4. Counts in the manifest must be the counts in the file.
    manifest = json.loads(good['MANIFEST.json']); manifest['archive']['answers_by_source']['engine'] = 39
    (out / 'MANIFEST.json').write_text(json.dumps(manifest))
    refused('counts in the manifest do not match archive.json')
    manifest = json.loads(good['MANIFEST.json']); del manifest['archive']
    (out / 'MANIFEST.json').write_text(json.dumps(manifest))
    refused('manifest does not describe archive.json')
    # 5. Non-ASCII bytes and loose JSON cannot hide behind a recomputed hash either.
    data = json.loads(good['archive.json']); data['answers'][0]['text'] = 'café'
    payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
    manifest = json.loads(good['MANIFEST.json']); manifest['archive']['sha256'] = hashlib.sha256(payload).hexdigest()
    (out / 'archive.json').write_bytes(payload); (out / 'MANIFEST.json').write_text(json.dumps(manifest))
    refused('archive.json holds a non-ASCII byte')
    payload = good['archive.json'].replace(b'"locked": true', b'"locked": true, "locked": true', 1)
    manifest = json.loads(good['MANIFEST.json']); manifest['archive']['sha256'] = hashlib.sha256(payload).hexdigest()
    (out / 'archive.json').write_bytes(payload); (out / 'MANIFEST.json').write_text(json.dumps(manifest))
    refused('archive.json is not strict JSON')


def test_export_refuses_a_guard_hit_in_a_stored_answer(ready, tmp_path, monkeypatch):
    recorded_archive(monkeypatch)
    with core.connect() as cx:      # a row written before a guard existed, simulated by going around classify()
        cx.execute("INSERT INTO archive_runs(number,kind,question_set,set_sha256,started_at) SELECT 2,'real',question_set,sha256,? FROM archive_sets",
                   (core.utcnow(),))
        archive._store(cx, 2, 'basic-1', 'Q01', 'Astra', model='some-model-1', source='api', state='answered',
                       text='Send it to someone@example.org', agent_id=1)
    with pytest.raises(RuntimeError, match='archive answers id 41, field text: looks like an email address'):
        snapshot.export_published(tmp_path / 'published')
    assert not (tmp_path / 'published').exists()


# ---- outside clients -------------------------------------------------------------------------------

def test_outside_client_answers_are_self_declared_guarded_and_limited(ready, monkeypatch):
    from live_genesis.app import app
    keys = {item['name']: item['key'] for item in core.seed_agents()}
    auth = {'Authorization': f"Bearer {keys['Astra']}", 'X-Agent-Model': 'vendor/outside-model-2'}
    body = {'run': 1, 'question_id': 'Q03', 'answer': ' Canberra. '}
    with TestClient(app) as client:
        assert client.post('/api/agent/archive-answer', json=body).status_code == 401
        refused = client.post('/api/agent/archive-answer', json=body, headers=auth)
        assert refused.status_code == 409 and 'not accepting answers' in refused.json()['detail']       # not locked yet
        archive.lock()
        assert client.post('/api/agent/archive-answer', json=body, headers=auth).status_code == 409       # no run yet
        monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')        # the web app dropped it from this process on import
        as_real(monkeypatch, 'Seven.')
        archive.run_archive(max_usd=1)
        done = client.post('/api/agent/archive-answer', json=body, headers=auth)
        assert done.status_code == 200 and done.json() == {
            'recorded': True, 'run': 1, 'question_id': 'Q03', 'answerer': 'Astra', 'source': 'api',
            'model': 'vendor/outside-model-2', 'model_verified': False, 'changed': None}
        stored = rows("SELECT source,agent_id,model,provider,reservation_id,answer_text,estimated_usd FROM archive_answers WHERE source='api'")
        assert stored == [{'source': 'api', 'agent_id': 1, 'model': 'vendor/outside-model-2', 'provider': None,
                           'reservation_id': None, 'answer_text': 'Canberra.', 'estimated_usd': None}]
        again = client.post('/api/agent/archive-answer', json=body, headers=auth)
        assert again.status_code == 409 and 'never replaced' in again.json()['detail']
        for change, status in (({'run': 2}, 409), ({'run': 0}, 422), ({'run': '1'}, 422), ({'question_id': 'Q99'}, 404),
                               ({'question_id': '../Q1'}, 422), ({'answer': ''}, 422), ({'answer': 'x' * 1001}, 422),
                               ({'answer': 'mail someone@example.org', 'question_id': 'Q04'}, 422),
                               ({'answer': 'zero​width', 'question_id': 'Q04'}, 422), ({'answer': '...', 'question_id': 'Q04'}, 422),
                               ({'extra': 1, 'question_id': 'Q04'}, 422)):
            response = client.post('/api/agent/archive-answer', json={**body, **change}, headers=auth)
            assert response.status_code == status, change
            assert 'someone@' not in response.text
        for headers in ({'Authorization': auth['Authorization']}, {**auth, 'X-Agent-Model': 'evil.example.org'},
                        {**auth, 'X-Agent-Model': 'two words'}):
            response = client.post('/api/agent/archive-answer', json={**body, 'question_id': 'Q04'}, headers=headers)
            assert response.status_code == 422 and 'X-Agent-Model' in response.json()['detail']
        # Not a resident action: no event, no round quota used, no provenance row.
        assert rows('SELECT COUNT(*) n FROM events')[0]['n'] == 0 and rows('SELECT COUNT(*) n FROM round_actions')[0]['n'] == 0
        assert rows("SELECT COUNT(*) n FROM archive_answers WHERE source='api'")[0]['n'] == 1
        # Its own small per-minute window.
        monkeypatch.setenv('LIVE_ARCHIVE_ANSWERS_PER_MINUTE', '2')
        limited = [client.post('/api/agent/archive-answer', json={**body, 'question_id': f'Q{n:02d}'}, headers=auth) for n in (5, 6, 7)]
        assert [r.status_code for r in limited][-1] == 429 and 'Retry-After' in limited[-1].headers
        assert 'archive-minute' in limited[-1].json()['detail']
        # A later run compares the client's answer with its own earlier one.
        monkeypatch.delenv('LIVE_ARCHIVE_ANSWERS_PER_MINUTE')
        controls.api_limiter.reset()
        archive.run_archive(max_usd=1)
        with core.connect() as cx:
            cx.execute('DELETE FROM quota_windows')
        old = client.post('/api/agent/archive-answer', json=body, headers=auth)
        assert old.status_code == 409 and 'latest run, which is run 2' in old.json()['detail']
        new = client.post('/api/agent/archive-answer', json={**body, 'run': 2, 'answer': 'Sydney'}, headers=auth)
        assert new.status_code == 200 and new.json()['changed'] is True
    page = archive_pages.render_question(archive.public_payload(), 'Q03')
    assert 'vendor/outside-model-2 · self-declared' in page and 'gpt-test-1 · server call' in page
    assert '>ASTRA<' in page and archive.public_payload()['answers_by_source'] == {'engine': 80, 'api': 3, 'demo': 0}


def test_engine_label_may_not_equal_an_agent_name(ready, monkeypatch):
    core.seed_agents()
    archive.lock()
    as_real(monkeypatch, 'Seven.')
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', 'astra=openai:gpt-test-1')
    with pytest.raises(archive.ArchiveRefused, match='equals an agent name'):
        archive.run_archive(max_usd=1)


# ---- command line -------------------------------------------------------------------------------------

def run_cli(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, 'argv', ['cli', *argv])
    monkeypatch.setattr(archive.config, 'load_provider_credentials', lambda: [])
    code = 0
    try:
        cli.main()
    except SystemExit as stop:
        code = stop.code
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_cli_plan_status_lock_dry_run_and_demo(ready, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(engine, 'infer', fake(AssertionError('no free command calls a model')))
    code, out = run_cli(monkeypatch, capsys, 'archive-plan')
    assert code == 0 and 'Calls per run: 40' in out and 'archive-run --max-usd 0.01' in out and 'Nothing was called' in out
    code, out = run_cli(monkeypatch, capsys, 'archive-plan', '--models', 'model-b')
    assert code == 0 and 'Calls per run: 20' in out
    code, out = run_cli(monkeypatch, capsys, 'archive-dry-run')
    assert code == 0 and out.count(archive.MOCK_BANNER) == 2
    code, out = run_cli(monkeypatch, capsys, 'archive-status')
    status = json.loads(out)
    assert code == 0 and status['locked'] is None and status['runs'][0]['kind'] == 'mock' and status['question_file']['status'] == 'locked'
    code, out = run_cli(monkeypatch, capsys, 'archive-lock')
    assert code == 0 and 'Locked question set basic-1: 20 questions' in out
    code, out = run_cli(monkeypatch, capsys, 'archive-lock')
    assert code == 1 and 'Already locked' in out
    code, out = run_cli(monkeypatch, capsys, 'demo-archive')
    assert code == 1 and 'use a fresh DB' in out
    code, out = run_cli(monkeypatch, capsys, 'archive-status')
    assert json.loads(out)['file_matches_lock'] is True


def test_cli_demo_archive_on_a_fresh_database(ready, monkeypatch, capsys):
    code, out = run_cli(monkeypatch, capsys, 'demo-archive')
    assert code == 0 and 'SCRIPTED DEMO ARCHIVE' in out and 'NO model was called' in out


def test_cli_archive_run_requires_max_usd_and_passes_the_gates(ready, monkeypatch, capsys):
    calls = []
    as_real(monkeypatch, 'Seven.', calls)
    code, out = run_cli(monkeypatch, capsys, 'archive-run')
    assert code == 2 and '--max-usd' in out and calls == []
    code, out = run_cli(monkeypatch, capsys, 'archive-run', '--max-usd', '0.05')
    assert code == 1 and 'not locked' in out and calls == []
    archive.lock()
    monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS', 'NO')
    code, out = run_cli(monkeypatch, capsys, 'archive-run', '--max-usd', '0.05')
    assert code == 1 and 'LIVE_ALLOW_PAID_CALLS=YES' in out and calls == []
    monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS', 'YES')
    code, out = run_cli(monkeypatch, capsys, 'archive-run', '--max-usd', '0.000001')
    assert code == 1 and 'exceeds --max-usd' in out and calls == []
    monkeypatch.setenv('LIVE_COMPAT_JSON_MODE', 'on')
    code, out = run_cli(monkeypatch, capsys, 'archive-run', '--max-usd', '0.05', '--models', 'model-a')
    assert code == 0 and len(calls) == 20 and 'PAID RUN' in out and '"status": "complete"' in out and archive.MOCK_BANNER not in out
    assert 'snapshot --export' in out
    import os
    assert os.environ['LIVE_COMPAT_JSON_MODE'] == 'off'       # answers are sentences, never forced into JSON


def test_archive_keeps_the_engine_and_keys_out_of_web_processes():
    source = (REPO / 'live_genesis' / 'archive.py').read_text(encoding='utf-8')
    assert source.count('load_provider_credentials()') == 1
    before = source.split('load_provider_credentials()')[0]
    assert before.rstrip().splitlines()[-2].strip().startswith('print("PAID RUN')
    assert before.rsplit('elif args.command ==', 1)[1].startswith(' "archive-run":')
    # The engine is imported inside the paid function only; the page renderer never imports it.
    assert re.findall(r'^(?:from|import) .*engine.*$', source, re.M) == []
    assert source.count('from . import engine') == 1
    pages = (REPO / 'live_genesis' / 'archive_pages.py').read_text(encoding='utf-8')
    assert 'import engine' not in pages and 'load_provider_credentials' not in pages and 'connect(' not in pages


def test_workflow_builds_the_scripted_archive_for_the_demo_site():
    text = (REPO / '.github' / 'workflows' / 'snapshot.yml').read_text(encoding='utf-8')
    assert text.count('python -m live_genesis.cli demo-archive\n') == 1
    assert text.index('cli demo\n') < text.index('cli demo-archive') < text.index('Export static snapshot')
    assert 'archive-run' not in text and 'archive-lock' not in text
