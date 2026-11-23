"""Answers Over Time (live_genesis/archive.py, archive_pages.py). Offline: every model call is a
replaced `engine.infer`; nothing here contacts a provider."""
import copy
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


def tables():
    return sorted(r['name'] for r in rows("SELECT name FROM sqlite_master WHERE type='table'"))


def publish_refusal(payload):
    """Why a whole payload (model track included) could not be published, in the order the removed full export
    asked: the payload and publication rules, the content guards, then the question hashes. '' when nothing does."""
    return archive.publish_problem(payload) or '; '.join(snapshot._scan(payload)) or archive.set_hash_problem(payload) or ''


# What a database made before 14 November 2026 also holds: the agents table of the removed society. The older
# archive schemas (tests/fixtures/archive_schema_v*.sql) refer to it, as the real ones do.
AGENTS_TABLE = '''CREATE TABLE agents (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, role TEXT NOT NULL, persona TEXT NOT NULL, key_hash TEXT UNIQUE NOT NULL,
    model TEXT NOT NULL, memory TEXT NOT NULL DEFAULT '', credits INTEGER NOT NULL DEFAULT 100, created_at TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1, key_expires REAL NOT NULL DEFAULT 0, external INTEGER NOT NULL DEFAULT 0)'''


def society_tables(cx):
    """Give an old-schema test database its agents table, with the one agent older `api` answers name."""
    cx.execute(AGENTS_TABLE)
    cx.execute("INSERT INTO agents(name,role,persona,key_hash,model,memory,created_at) VALUES ('Astra','Scientist','p','hash','m',"
               "'a private note','2026-01-01T00:00:00+00:00')")


def society_state():
    """The agents table as it stands: its definition and every row. The reduced code must leave both alone."""
    return rows("SELECT sql FROM sqlite_master WHERE name='agents'") + rows('SELECT * FROM agents')


def rendered_site(out):
    """The default rendering (what the local web app serves: every page, the model track included) written as
    files, with the static files beside it as in a snapshot. Returns the payload it was made from."""
    payload = archive.public_payload(readonly=True)
    for name, text in archive_pages.render_site(payload).items():
        target = out / 'archive' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
    for name in archive_pages.static_files():
        target = out / 'static' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((STATIC / name).read_bytes())
    return payload


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


def test_prompt_is_exactly_the_question_and_a_run_makes_no_other_table(ready, monkeypatch):
    calls = []
    as_real(monkeypatch, 'Seven.', calls)
    archive.lock()
    before = tables()
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
    # A run writes to the archive's tables and the spend accounting; no table of the removed society exists or appears.
    assert tables() == before and not {'agents', 'events', 'posts', 'rounds', 'round_actions', 'memory_notes', 'action_provenance',
                                       'engine_turns'} & set(before)
    assert {t for t in before if not t.startswith('archive_')} == {'api_usage', 'budget_reservations', 'cost_reconciliations', 'operator_audit'}
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


def test_an_interrupted_priced_call_is_unconfirmed_and_blocks_the_next_run(ready, monkeypatch):
    # Ctrl+C while a priced request is out: it may have been sent and billed, so the hold must not stay merely `held`.
    archive.lock()

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(engine, 'infer', interrupted)
    monkeypatch.setattr(engine, 'REAL_INFER', interrupted)
    with pytest.raises(KeyboardInterrupt):
        archive.run_archive(max_usd=1)
    assert [r['status'] for r in rows('SELECT status FROM budget_reservations')] == ['unconfirmed']
    run = rows('SELECT status,stop_reason FROM archive_runs')[0]
    assert run['status'] == 'incomplete' and 'interrupted during a call' in run['stop_reason'] and 'cli reconcile' in run['stop_reason']
    assert rows('SELECT outcome,reason,reservation_id FROM archive_run_answerers ORDER BY rowid LIMIT 1') == [
        {'outcome': 'stopped', 'reason': 'other', 'reservation_id': 1}]
    calls = []
    as_real(monkeypatch, engine.Completion('Seven.', 40, 12, 'end'), calls)
    with pytest.raises(archive.ArchiveRefused, match='Unconfirmed provider charge'):
        archive.run_archive(max_usd=1)
    assert calls == [] and rows('SELECT COUNT(*) n FROM archive_answers')[0]['n'] == 0


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
    assert 'Hidden: broke our content rules (looks like an email address)' in page and 'example.org' not in page


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
    assert 'No answers yet' in pages['index.html']
    # A real run numbers from 1 regardless of mock runs, and mock rows never reach an export.
    archive.lock()
    as_real(monkeypatch, 'Seven.')
    assert archive.run_archive(max_usd=1)['run'] == 1
    exported = json.dumps(archive.public_payload())
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
                    'trending_questions': 3,
                    # the chat-app track, scripted under made-up names (tests/test_archive_chatbots.py)
                    'chatbot_runs': 4, 'chatbots': ['chat-app-a', 'chat-app-b', 'chat-app-c'], 'chatbot_answers': 84}
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


def test_demo_archive_is_refused_after_recorded_model_usage(isolated_db):
    core.record_usage(None, 'some-model', 10, 5)
    with pytest.raises(archive.ArchiveRefused, match='use a fresh DB'):
        archive.demo_archive()


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


# The elements and attributes the archive pages may use. Extended on purpose for the redesign of November 2026:
# * `input` (type radio or checkbox ONLY) and `label`: the month switch and "More" without script. Safe because no page
#   holds a <form>, a button or a submit control (checked below), the inputs carry no `value`, `form` or `formaction`, and
#   the Content-Security-Policy keeps `form-action 'none'`: nothing can be sent anywhere. `name` only groups the radios.
# * `details` / `summary`: the browser's own open-and-close. No script, no request, no external load.
# * `table` and its parts, `time`, lists, `h3`, `em`: plain structure.
# * `itemscope` / `itemtype` / `itemprop` (schema.org microdata) and `data-*` (a closed list): labels for machines. They load
#   nothing: an itemtype is an identifier, never fetched, and is required to be a schema.org type.
ALLOWED_TAGS = {'html', 'head', 'meta', 'title', 'link', 'body', 'div', 'span', 'a', 'img', 'nav', 'header', 'h1', 'h2', 'h3', 'p',
                'strong', 'em', 'section', 'article', 'main', 'pre', 'footer', 'input', 'label', 'details', 'summary',
                'table', 'caption', 'thead', 'tbody', 'tr', 'th', 'td', 'time', 'ol', 'ul', 'li'}
ALLOWED_ATTRS = {'lang', 'charset', 'name', 'content', 'http-equiv', 'rel', 'type', 'href', 'class', 'src', 'alt', 'width', 'height',
                 'aria-label', 'aria-current', 'aria-hidden', 'role', 'id', 'for', 'checked', 'reversed', 'scope', 'datetime',
                 'itemscope', 'itemtype', 'itemprop',
                 'property',
                 'download'}   # a save name on the link to a card's own PNG picture; same-site file, no script   # Open Graph link-preview tags (<meta property="og:...">): text and this site's own addresses only
DATA_ATTRS = {'data-question-id', 'data-answerer', 'data-answerer-kind', 'data-maker', 'data-version', 'data-collected', 'data-method',
              'data-provenance', 'data-human-form', 'data-state', 'data-previous-collected', 'data-truncated-display', 'data-lang',
              'data-label'}


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
        assert tag in ALLOWED_TAGS, tag                      # so: no script, style, form, button, select, textarea, iframe, object
        assert set(attrs) <= ALLOWED_ATTRS | DATA_ATTRS, (tag, sorted(attrs))
        assert not any(name.startswith('on') or name == 'style' for name in attrs), (tag, sorted(attrs))
        if tag == 'input':                                   # a switch for the stylesheet, never a field that is sent
            assert attrs.get('type') in ('radio', 'checkbox') and not {'value', 'form', 'formaction', 'src'} & set(attrs), attrs
        if 'itemtype' in attrs:
            assert re.fullmatch(r'https://schema\.org/[A-Za-z]+', attrs['itemtype']), attrs['itemtype']
    assert "form-action 'none'" in web.CONTENT_SECURITY_POLICY
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
    assert question.count('&lt;script&gt;') >= 5          # heading, rationale, prompt, description, answer, version, reason
    # The machine-readable attributes carry untrusted text too (the version a service reported): escaped like the rest.
    assert f'data-version="{archive_pages.esc(HOSTILE)}"' in question and 'data-version="<' not in question
    # Tag balance survives the hostile text: every opened div is closed.
    for page in (index, question):
        assert page.count('<div') == page.count('</div>') and page.count('<span') == page.count('</span>')


def html_pages(pages):
    return {name: page for name, page in pages.items() if name.endswith('.html')}


def test_pages_for_the_demo_archive(ready):
    archive.demo_archive()
    payload = archive.public_payload()
    pages = archive_pages.render_site(payload)
    core_ids, trend_ids = [f'Q{n:02d}' for n in range(1, 21)], ['T001', 'T002', 'T003']
    chat_ids = [f'C{n:03d}' for n in range(1, 7)]
    chat_cards = {f'card/{q}-{s}.html' for q in chat_ids for s in ('chat-app-a', 'chat-app-b', 'chat-app-c', 'human')}
    every = core_ids + trend_ids + chat_ids
    assert set(pages) == ({'index.html', 'models.html', 'trending.html', 'chatbots.html'} | {f'q/{i}.html' for i in core_ids}
                          | {f't/{i}.html' for i in trend_ids} | {f'c/{i}.html' for i in chat_ids}
                          | {f'card/{i}.html' for i in every} | chat_cards
                          | {f'data/{i}.json' for i in every} | {'data/answers.jsonl', 'data/README.txt'})
    index, models, date = pages['index.html'], pages['models.html'], pages['q/Q11.html']
    for name, page in html_pages(pages).items():
        assert_page_is_safe(page)
        assert 'Sample we wrote, not a real AI answer' in page, name                     # the one sample label, on every page
        assert page.count('<main') == 1 and page.count('<h1') == 1 and '<script' not in page, name
        assert '<meta name="color-scheme" content="light" />' in page and '<meta name="aot:content" content="scripted-sample" />' in page
        assert not re.search(r'\bround', page, re.I)                                     # time is told in checks and dates
        assert not re.search(r'agents?[- ]only|no humans|humans (?:may|can)not|software agents only', page, re.I)
        # A share card carries no "read-only · no payments" line (owner's decision, 20 Nov 2026); every other page does.
        assert 'no payments' not in page.lower(), name                    # owner's decision, 20 Nov 2026: on no page
        if not name.startswith('card/'):
            assert f'<a class="skip" href="{name.rsplit("/", 1)[-1]}#main">' in page and '<header class="bar">' in page and '<main id="main"' in page, name
            assert 'READ-ONLY ARCHIVE' not in page and 'Read-only. No payments. Just answers.' not in page   # owner, 20 Nov 2026
    # The model list: every core question with each model's newest answer inline and a timeline that says WHEN it changed.
    assert 'The same questions, straight to the models.' in models and 'How we keep track' in models
    assert 'It does not say which answer is right.' in models and 'One square each time we asked, oldest first:' in models
    assert models.count('class="timeline"') == 60 and models.count('role="img"') == 60
    assert models.count('<h3 class="row-q"><a class="row-open" href="q/') == 20 and models.count('class="mini-text"') == 60
    assert 'aria-label="model-a: changed Aug 2026 and Nov 2026; asked 8 times"' in models
    assert 'aria-label="model-c: changed Jun 2026; no answer Sep 2026; asked 8 times"' in models
    assert 'aria-label="model-a: no change; asked 8 times"' in models
    assert [g for g in ('Everyday facts', 'Health and home', 'Right now', 'About the AI') if f'<h2>{g}</h2>' in models] == \
        ['Everyday facts', 'Health and home', 'Right now', 'About the AI']
    assert 'href="q/Q11.html"' in models and 'archive-mark.svg?v=' in models and 'rel="icon"' in models
    assert re.search(r'href="\.\./static/archive\.css\?v=[0-9a-f]{12}"', models)
    assert 'This list of twenty questions is a draft' in models
    # The front page of the scripted demo is the chat-app comparison (tests/test_archive_chatbots.py).
    assert 'Same question. Three AI apps. One human.' in re.sub(r'<[^>]*>', '', index) and 'href="c/C001.html"' in index and 'href="q/' not in index
    # The question page: the newest month is the first panel, one card per answerer, older months one radio away.
    assert '<title>What is today&#x27;s date? · Answers Over Time</title>' in date
    assert date.index('id="m-run-8"') < date.index('id="m-run-7"') < date.index('id="m-run-1"')
    assert date.count('<section class="panel"') == 8 and date.count('class="m-radio" type="radio" name="month"') == 8
    assert date.count(' checked />') == 1 and '<input class="m-radio" type="radio" name="month" id="pick-1" checked />' in date
    # Month pills; the newest one says in words how many changed.
    assert ('<label for="pick-1"><span class="m-name">Nov 2026</span><span class="m-mark">1 changed</span></label>'
            '<label for="pick-2"><span class="m-name">Oct 2026</span></label>') in date
    assert date.count('<article class="acard') == 24 and date.count('acard is-changed') == 3 and date.count('>Changed<') == 3
    assert date.count('class="acard acard-none who-') == 1 and 'No answer this time' in date
    cards = date.split('<section class="changes">')[0]
    # Per-cell and per-row repeats are gone from the cards; the labels live in the closed block and in the attributes.
    assert 'scripted' not in re.sub(r'<[^>]*>', '', cards).lower() and 'model-a-2' not in re.sub(r'<[^>]*>', '', cards)
    assert cards.count('data-method="scripted-sample" data-provenance="scripted-sample"') == 23 and 'data-version="model-a-2"' in cards
    assert '<details><summary>The details behind each answer</summary>' in date
    assert 'version model-a-2 · new version: version changed from model-a-1 to model-a-2' in date
    # Changed shown inline: what it was before, with its month.
    assert ('<span class="before-when mono">Before · <time datetime="2026-10-01">Oct 2026</time></span>'
            'It is probably some time in 2026.</p>') in date
    assert '<summary>History · 2 changes</summary>' in date and 'Aug–Oct 2026 · asked 3 times' in date
    # What changed: a real table with headers; an unchanged cell is one short line.
    assert '<table class="history" role="table">' in date and '<caption>' in date and date.count('<th scope="col" role="columnheader">') == 4
    assert date.count('<th scope="row" role="rowheader"><time datetime="') == 8 and '>same again</td>' in date
    assert date.count('<span class="t-word mono">changed</span>') == 3 and date.count('<span class="t-word mono">first answer</span>') == 3
    assert '<details><summary>The exact words we used</summary><pre class="prompt mono">Answer in one or two sentences.\n\nWhat is today&#x27;s date?</pre>' in date
    assert '<details><summary>Why we ask this</summary>' in date and ' open' not in date.split('<section class="more-info">')[1]
    assert re.search(r'href="\.\./\.\./static/archive\.css\?v=[0-9a-f]{12}"', date) and 'href="../models.html"' in date
    for gone in ('class="side"', 'grid-scroll', 'class="grid n-', 'scripted date', 'class="legend"', 'What moved', 'The exact prompt'):
        assert all(gone not in page for page in html_pages(pages).values()), gone
    with pytest.raises(KeyError):
        archive_pages.render_question(payload, 'Q99')
    with pytest.raises(ValueError, match='malformed'):
        archive_pages.render_site({**payload, 'questions': [{**payload['questions'][0], 'id': '../../etc'}]})


def test_archive_stylesheet_uses_tokens_local_fonts_and_no_gradient():
    css = (STATIC / 'archive.css').read_text(encoding='utf-8')
    assert 'gradient' not in css and '@import' not in css and 'http' not in css
    # The "Group chat" look (owner's choice) lifts the old "no box-shadow" rule by exactly ONE soft shadow: a single token,
    # and every box-shadow in the sheet is that token. Still no glow, no text shadow, no filter, no gradient.
    assert css.count('--shadow:') == 1 and css.count('box-shadow') == css.count('box-shadow: var(--shadow);') >= 1
    for banned in ('text-shadow', 'drop-shadow', 'filter', 'glow', 'inset'):
        assert banned not in css.replace('clip-path: inset(50%)', ''), banned
    tokens, rules = css.split(':root {', 1)[1].split('}', 1)                # the token block, and everything after it
    for token in ('#f4efe6', '#fffdf9', '#1f1b2e', '#4338ca', '#c9cdf0', '#312e81', '#ffffff'):
        assert css.count(token) == 1 and token in tokens, token             # each colour is defined once, as a custom property
    assert not re.findall(r'#[0-9a-fA-F]{3,8}\b', rules) and 'rgb' not in rules and 'var(--changed)' in rules and 'var(--same)' in rules
    assert tokens.count('rgba(') == 1                                       # the shadow's own colour, nowhere else
    # Eight position colours (ink and tint), each defined once and used only through .who-N.
    for n in range(1, archive_pages.PALETTE + 1):
        assert tokens.count(f'--w{n}:') == 1 and tokens.count(f'--w{n}-t:') == 1
        assert f'.who-{n} {{ --hue: var(--w{n}); --tint: var(--w{n}-t); }}' in rules
    assert f'--w{archive_pages.PALETTE + 1}' not in css and '.av-b {' in rules and '.av-c {' in rules
    mark = (STATIC / 'archive-mark.svg').read_text(encoding='utf-8')
    assert mark.count('<rect') == 3 and mark.count('#c9cdf0') == 2 and mark.count('#312e81') == 1 and '<script' not in mark
    # No sidebar, no sideways-scrolling grid, no min-width ladder; one rule set per month panel and per group instead.
    for gone in ('.grid-scroll', '.grid.n-', '.side', '.legend', 'min-width: 296px'):
        assert gone not in css, gone
    assert all(f'.m-radio:nth-of-type({n}):checked ~ .panel:nth-of-type({n}) ' in css for n in range(1, archive_pages.MAX_PANELS + 1))
    from live_genesis import archive_chatbots
    assert all(f'.g-radio:nth-of-type({n}):checked ~ .qs .minis-g:not(.g-{n}) {{ display: none; }}' in css
               for n in range(1, archive_chatbots.MAX_GROUPS + 1))          # a checked radio hides the OTHER groups: all show without it
    assert f'.g-radio:nth-of-type({archive_chatbots.MAX_GROUPS + 1})' not in css
    assert '.month-strip { position: sticky;' in css and '.more-box:checked + .clamp' in css
    assert '@media (min-width: 1100px)' in css and '@media (min-width: 700px)' in css and '@media (min-width: 900px)' in css
    # Month and group pills are about 44px tall (2.75rem), whatever they hold.
    assert '.group-strip label, .month-strip label { flex: 1 0 auto; min-height: 2.75rem;' in css
    # Timeline squares keep an edge, also in forced colours; the fitted card layout exists only at the capture size.
    assert '.sq { ' in css and 'border: 1px solid var(--muted)' in css and '.sq-none { border-style: dashed;' in css
    assert '@media (forced-colors: active)' in css and '@media (min-width: 540px) and (min-height: 960px)' in css
    # Sizes are in rem with a floor: nothing under 12px (0.75rem) outside the fitted card.
    flowing = css.split('@media (min-width: 540px) and (min-height: 960px)')[0]
    assert all(float(size) >= 0.75 for size in re.findall(r'font-size: ([0-9.]+)rem', flowing))
    assert not re.findall(r'font-size: [0-9.]+px', flowing) and 'text-transform: uppercase' in flowing
    assert not re.findall(r'[a-z-]+: [0-9.]+cqw', flowing)                     # no cqw size outside the fitted card
    # Motion: every animation and transition sits inside "no-preference", is short, and only describes where a bubble
    # comes FROM (`backwards`): the resting state is the visible one, so nothing is hidden without animation support.
    before, motion = css.split('@media (prefers-reduced-motion: no-preference) {', 1)
    motion, after = motion.split('/* Forced colours', 1)
    for word in ('animation', 'transition', '@keyframes', '@view-transition'):
        assert word not in before and word not in after and word in motion, word
    assert '@keyframes pop { from { opacity: 0;' in motion and ' to {' not in motion and 'infinite' not in motion
    assert 'animation: pop 240ms' in motion and motion.count('backwards') == 1
    delays = [int(ms) for ms in re.findall(r'animation-delay: ([0-9]+)ms', motion)]
    assert max(delays) + 240 < 400 and not re.findall(r'[0-9.]+s\b', motion.replace('ms', ''))
    # Nothing rests invisible: the only other "opacity: 0" is on the radio and checkbox inputs behind the labels.
    assert css.count('opacity: 0') == 2 and 'opacity: 0; pointer-events: none; }' in before and 'visibility: hidden' not in css


def test_the_default_rendering_holds_the_archive_pages_and_links_them(ready, tmp_path, monkeypatch):
    archive.demo_archive()
    out = tmp_path / 'site'
    payload = rendered_site(out)
    assert payload['demo_only'] is True
    assert (out / 'archive' / 'index.html').is_file() and len(list((out / 'archive' / 'q').glob('Q*.html'))) == 20
    assert payload['answers_by_source'] == {'engine': 0, 'api': 0, 'demo': 514}
    for name in ('archive.css', 'archive-mark.svg', 'fonts/inter.woff2', 'fonts/bricolage-grotesque.woff2', 'fonts/jetbrains-mono.woff2'):
        assert (out / 'static' / name).is_file(), name
    page = (out / 'archive' / 'q' / 'Q11.html').read_text(encoding='utf-8')
    assert_page_is_safe(page)
    version = hashlib.sha256((STATIC / 'archive.css').read_bytes()).hexdigest()[:12]
    assert f'../../static/archive.css?v={version}"' in page
    # Every relative address in the archive pages resolves to a file: no page links out of the archive any more
    # (the link to the observatory page went with that page).
    for html in (out / 'archive').rglob('*.html'):
        for url in page_urls(html.read_text(encoding='utf-8')):
            target = (html.parent / url.split('#')[0].split('?')[0]).resolve()
            assert out.resolve() in target.parents, url
            assert target.is_file(), url
        assert 'Observatory' not in html.read_text(encoding='utf-8'), html.name


def test_snapshot_refuses_recorded_archive_answers_without_review(ready, tmp_path, monkeypatch):
    archive.lock()
    with core.connect() as cx:
        cx.execute("INSERT INTO archive_runs(number,kind,question_set,set_sha256,started_at) SELECT 1,'real',question_set,sha256,? FROM archive_sets",
                   (core.utcnow(),))
        archive._store(cx, 1, 'basic-1', 'Q01', 'Astra', model='some-model-1', source='api', state='answered', text='365.', agent_id=1)
    assert archive.public_payload()['demo_only'] is False
    with pytest.raises(RuntimeError, match='not scripted-demo-only'):
        snapshot.build(tmp_path / 'site')
    assert not (tmp_path / 'site').exists()


def test_the_web_app_serves_the_same_pages(ready):
    from live_genesis.app import app
    archive.demo_archive()
    payload = archive.public_payload()
    with TestClient(app) as client:
        index = client.get('/archive/index.html')
        assert index.status_code == 200 and index.text == archive_pages.render_index(payload)
        assert 'text/html' in index.headers['content-type'] and "default-src 'none'" in index.headers['content-security-policy']
        assert client.get('/archive/q/Q11.html').text == archive_pages.render_question(payload, 'Q11')
        assert client.get('/archive', follow_redirects=False).headers['location'] == '/archive/index.html'
        assert client.get('/', follow_redirects=False).headers['location'] == '/archive/index.html'
        assert client.get('/archive/models.html').text == archive_pages.render_models(payload)
        assert client.get('/archive/c/C001.html').text == archive_pages.render_question(payload, 'C001')
        for missing in ('/archive/q/Q99.html', '/archive/q/Q11', '/archive/q/..%2Fx.html', '/archive/q/Q1.html'):
            assert client.get(missing).status_code == 404, missing
        assert client.get('/static/archive.css').status_code == 200
        assert client.get('/static/fonts/inter.woff2').status_code == 200
        assert client.post('/archive/index.html').status_code == 405


def test_archive_pages_answer_503_without_a_database(tmp_path, monkeypatch):
    from live_genesis.app import app
    monkeypatch.setenv('LIVE_DB', str(tmp_path / 'missing.sqlite'))
    with TestClient(app) as client:
        response = client.get('/archive/index.html')
        assert response.status_code == 503 and response.json() == {'detail': web.UNAVAILABLE}
    assert not (tmp_path / 'missing.sqlite').exists()


# ---- a recorded model run: shown locally, never published -----------------------------------------

def recorded_archive(monkeypatch, reply='Seven.'):
    archive.lock()
    as_real(monkeypatch, reply)
    return archive.run_archive(max_usd=1)


def test_a_recorded_model_run_reads_as_asked_by_us_and_is_not_built_into_a_site_unreviewed(ready, tmp_path, monkeypatch):
    recorded_archive(monkeypatch, 'Café answers: seven.')
    payload = archive.public_payload()
    assert payload['answers_by_source'] == {'engine': 40, 'api': 0, 'demo': 0} and payload['demo_only'] is False
    assert not any(key in json.dumps(payload) for key in ('reservation', 'estimated_usd', 'input_tokens', 'normalised'))
    # Written as a file would hold it (ASCII, every other character escaped), it renders the same pages.
    raw = snapshot._encode(payload)
    assert raw.isascii() and b'Caf\\u00e9' in raw
    assert archive_pages.render_site(json.loads(raw)) == archive_pages.render_site(payload)
    page = archive_pages.render_question(payload, 'Q01')
    assert 'Café answers: seven.' in page and 'We asked it ourselves · version gpt-test-1' in page
    assert 'AI-generated · we asked it ourselves · may be wrong' in page and 'data-method="asked-by-server" data-provenance="ai-generated"' in page
    assert 'scripted' not in page.lower() and 'sample' not in page.lower()
    # The site build is for scripted demo data unless the operator says otherwise; and even then the model run is not written.
    with pytest.raises(RuntimeError, match='not scripted-demo-only'):
        snapshot.build(tmp_path / 'site')
    assert not (tmp_path / 'site').exists()
    snapshot.build(tmp_path / 'site', allow_non_demo=True)
    assert 'seven' not in ''.join(path.read_text(encoding='utf-8') for path in (tmp_path / 'site').rglob('*')
                                  if path.suffix in ('.html', '.json', '.jsonl', '.txt'))
    # And there is nothing to export for review: the export is the chat-app track, and none was entered.
    with pytest.raises(RuntimeError, match='holds no chat-app reply entered by hand'):
        snapshot.export_published(tmp_path / 'published')
    assert not (tmp_path / 'published').exists()


def test_a_hand_edited_model_payload_meets_the_payload_rules_and_the_guards(ready, tmp_path, monkeypatch):
    recorded_archive(monkeypatch)
    good = json.loads(json.dumps(archive.public_payload()))

    def edited(edit):
        data = copy.deepcopy(good)
        edit(data)
        return data

    assert publish_refusal(good) == ''
    # The content guards name the record and the category, never the text.
    assert snapshot._scan(edited(lambda d: d['answers'][0].update(text='Write to someone@example.org'))) == \
        ['answers id 1, field text: looks like an email address']
    assert any(hit.startswith('answers id 1, field text') for hit in snapshot._scan(edited(lambda d: d['answers'][0].update(text='hidden\u200bcharacter'))))
    assert any('payment or donation link' in hit for hit in snapshot._scan(edited(lambda d: d['questions'][0].update(rationale='pay at paypal.me/someone'))))
    # Mock and scripted rows are never published, and neither are answers to a list that is not locked.
    for edit, match in ((lambda d: d['answers'][0].update(source='mock'), 'mock answers, which are never published'),
                        (lambda d: d['answers'][0].update(source='demo'), 'scripted demo answers'),
                        (lambda d: d['runs'][0].update(demo=True), 'scripted demo answers'),
                        (lambda d: d.update(locked=False), 'question list is not locked')):
        assert re.search(match, publish_refusal(edited(edit))), match
    # Bad ids and broken shapes are refused before anything is rendered.
    for edit, match in ((lambda d: (d['questions'][0].update(id='../x'), [a.update(question='../x') for a in d['answers'] if a['question'] == 'Q01']),
                         'question ids must be distinct and look like Q01'),
                        (lambda d: d['answers'].append(dict(d['answers'][0], id=999)), 'two answers share one run'),
                        (lambda d: d['answers'][0].update(state='withheld'), 'text does not fit its state'),
                        (lambda d: d.update(schema='something-else/9'), 'unknown schema')):
        broken = edited(edit)
        assert re.search(match, archive.payload_problem(broken)), match
        with pytest.raises(ValueError):
            archive_pages.render_site(broken)


def test_the_guard_scan_names_a_hit_in_a_stored_answer(ready, tmp_path, monkeypatch):
    recorded_archive(monkeypatch)
    with core.connect() as cx:      # a row written before a guard existed, simulated by going around classify()
        cx.execute("INSERT INTO archive_runs(number,kind,question_set,set_sha256,started_at) SELECT 2,'real',question_set,sha256,? FROM archive_sets",
                   (core.utcnow(),))
        archive._store(cx, 2, 'basic-1', 'Q01', 'Astra', model='some-model-1', source='api', state='answered',
                       text='Send it to someone@example.org', agent_id=1)
    assert snapshot._scan(json.loads(json.dumps(archive.public_payload()))) == ['answers id 41, field text: looks like an email address']


# ---- outside clients: the route is gone, older rows still read ---------------------------------------

def test_an_older_outside_answer_reads_as_self_declared_and_the_route_is_gone(ready, monkeypatch):
    from live_genesis.app import app
    recorded_archive(monkeypatch)
    with core.connect() as cx:      # as the removed POST /api/agent/archive-answer stored one: source api, the sender's name, its id
        archive._store(cx, 1, 'basic-1', 'Q03', 'Astra', model='vendor/outside-model-2', source='api', state='answered',
                       text='Canberra.', agent_id=1)
    stored = rows("SELECT source,agent_id,model,provider,reservation_id,answer_text,estimated_usd FROM archive_answers WHERE source='api'")
    assert stored == [{'source': 'api', 'agent_id': 1, 'model': 'vendor/outside-model-2', 'provider': None,
                       'reservation_id': None, 'answer_text': 'Canberra.', 'estimated_usd': None}]
    page = archive_pages.render_question(archive.public_payload(), 'Q03')
    assert 'Sent in by someone else, not checked · version vendor/outside-model-2' in page and 'We asked it ourselves · version gpt-test-1' in page
    assert 'data-method="sent-in-unverified"' in page and 'AI-generated · may be wrong' in page
    assert '>Astra<' in page and archive.public_payload()['answers_by_source'] == {'engine': 40, 'api': 1, 'demo': 0}
    # Nothing takes such an answer any more: no function, no route, no agent key.
    assert not hasattr(archive, 'submit_api_answer')
    body = {'run': 1, 'question_id': 'Q04', 'answer': 'Canberra.'}
    with TestClient(app) as client:
        assert client.post('/api/agent/archive-answer', json=body, headers={'X-Agent-Model': 'm-1'}).status_code == 405
        assert client.get('/api/agent/archive-answer').status_code == 404
    assert rows("SELECT COUNT(*) n FROM archive_answers WHERE source='api'")[0]['n'] == 1


def test_a_model_label_may_not_equal_an_older_outside_answerer(ready, monkeypatch):
    recorded_archive(monkeypatch)
    with core.connect() as cx:
        archive._store(cx, 1, 'basic-1', 'Q03', 'Astra', model='some-model-1', source='api', state='answered', text='Canberra.', agent_id=1)
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', 'astra=openai:gpt-test-1')
    with pytest.raises(archive.ArchiveRefused, match='equals the name of an outside answerer'):
        archive.run_archive(max_usd=1)
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', 'model-c=openai:gpt-test-1')
    assert archive.run_archive(max_usd=1)['status'] == 'complete'


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
    assert 'Model answers are not published' in out and 'snapshot --export' not in out
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
    assert source.count('from . import engine') == 2     # run_archive, and the one-call helper it shares with archive-chatbot-ask
    chat = (REPO / 'live_genesis' / 'archive_chatbots.py').read_text(encoding='utf-8')
    assert re.findall(r'^(?:from|import) .*engine.*$', chat, re.M) == [] and chat.count('from . import engine') == 1
    pages = (REPO / 'live_genesis' / 'archive_pages.py').read_text(encoding='utf-8')
    assert 'import engine' not in pages and 'load_provider_credentials' not in pages and 'connect(' not in pages


def test_workflow_builds_the_scripted_archive_for_the_demo_site():
    text = (REPO / '.github' / 'workflows' / 'snapshot.yml').read_text(encoding='utf-8')
    assert text.count('python -m live_genesis.cli demo-archive\n') == 1
    assert text.index('cli demo-archive') < text.index('Export static snapshot')
    assert 'cli demo\n' not in text and 'seed_agents' not in text
    assert 'archive-run' not in text and 'archive-lock' not in text
