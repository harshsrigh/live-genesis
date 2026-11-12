"""Answers Over Time: optional repeat samples per question (LIVE_ARCHIVE_SAMPLES / --samples).
Offline: every model call is a replaced `engine.infer` or a fake SDK client; nothing here contacts a provider."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from live_genesis import archive, archive_pages, controls, core, engine, snapshot
from test_archive import (REPO, as_real, assert_page_is_safe, fake, question_file, ready, retarget_archive, rows,  # noqa: F401
                          run_cli, trending_file)
from test_archive_endpoints import Status, fake_sdk, reply, three, three_endpoints, var  # noqa: F401

V3_SCHEMA = REPO / 'tests' / 'fixtures' / 'archive_schema_v3.sql'
ARCHIVE_TABLES = ('archive_run_answerers', 'archive_run_sets', 'archive_answers', 'archive_runs', 'archive_questions',
                  'archive_set_versions', 'archive_sets')


def scripted(monkeypatch, script, calls=None):
    """Replace the adapter (as a REAL run). `script(model, k)` gives the reply to the k-th time (1, 2, ...) a model is
    asked the same question since `script` was installed: k is the sample index within one run."""
    asked = {}

    def answer(model, prompt, _n):
        asked[(model, prompt)] = asked.get((model, prompt), 0) + 1
        return script(model, asked[(model, prompt)])
    as_real(monkeypatch, answer, calls)


def cell(payload, question='Q01', answerer='model-b', run=None):
    found = [a for a in payload['answers'] if a['question'] == question and a['answerer'] == answerer and (run is None or a['run'] == run)]
    return found[-1]


# ---- the setting ---------------------------------------------------------------------------------------

def test_sample_count_default_setting_argument_and_refusals(ready, monkeypatch):
    assert archive.sample_count() == 1 and archive.DEFAULT_SAMPLES == 1 and archive.MAX_SAMPLES == 5
    monkeypatch.setenv('LIVE_ARCHIVE_SAMPLES', ' 3 ')
    assert archive.sample_count() == 3 and archive.sample_count(2) == 2          # --samples wins over the setting
    for bad in ('0', '6', '-1', 'three', '2.5', '1e1'):
        monkeypatch.setenv('LIVE_ARCHIVE_SAMPLES', bad)
        with pytest.raises(archive.ArchiveRefused, match='whole number from 1 to 5'):
            archive.sample_count()
        with pytest.raises(archive.ArchiveRefused, match='whole number from 1 to 5'):
            archive.plan()                                                        # refused, not clamped: it is part of the method
    monkeypatch.delenv('LIVE_ARCHIVE_SAMPLES')
    for bad in (0, 6, True, 2.0):
        with pytest.raises(archive.ArchiveRefused):
            archive.sample_count(bad)
    # One seed per sample, in a fixed way; sample 1 is sent exactly what a single-sample run sends.
    assert archive.sampling_for(1) == archive.SAMPLING
    assert [archive.sampling_for(n)['seed'] for n in (1, 2, 3)] == [archive.ARCHIVE_SEED, archive.ARCHIVE_SEED + 1, archive.ARCHIVE_SEED + 2]
    assert archive.SAMPLING == {'temperature': 0, 'seed': archive.ARCHIVE_SEED}   # the shared constant was not mutated


def test_plan_multiplies_calls_and_estimate_and_says_so_only_when_asked(ready, monkeypatch):
    one = archive.plan()
    text_one = archive.render_plan(one)
    assert one['samples'] == 1 and one['calls'] == 40 and 'sample' not in text_one.lower()
    three_ = archive.plan(samples=3)
    assert three_['samples'] == 3 and three_['calls'] == 120 and three_['holds'] == one['holds']          # a hold is still one call
    assert three_['worst_case_usd'] == pytest.approx(3 * one['worst_case_usd'])
    assert three_['worst_case_usd_per_model'] == pytest.approx(3 * one['worst_case_usd_per_model'])
    assert three_['largest_call_hold_usd'] == one['largest_call_hold_usd']
    assert [g['calls'] for g in three_['endpoints']] == [120] and three_['endpoints'][0]['worst_case_usd'] == pytest.approx(three_['worst_case_usd'])
    text = archive.render_plan(three_)
    assert 'Calls per run: 120 (20 questions x 2 models x 3 samples)' in text
    assert 'each question is asked 3 times of each model' in text and '3 times that of a single-sample run' in text
    monkeypatch.setenv('LIVE_ARCHIVE_SAMPLES', '2')
    assert archive.plan()['calls'] == 80 and archive.plan(samples=1)['calls'] == 40
    # The worst case of all samples must fit under --max-usd before the first call.
    calls = []
    as_real(monkeypatch, 'Seven.', calls)
    archive.lock()
    cap = one['worst_case_usd'] * 1.5
    with pytest.raises(archive.ArchiveRefused, match='for 80 calls exceeds --max-usd'):
        archive.run_archive(max_usd=cap)
    assert calls == [] and rows('SELECT * FROM archive_runs') == []


# ---- a run with three samples ----------------------------------------------------------------------------

def test_three_samples_one_model_unanimous_one_two_of_three(ready, monkeypatch):
    calls = []
    # model-a: the same answer three times, worded cosmetically differently. model-b: sample 1 differs from 2 and 3.
    scripted(monkeypatch, lambda model, k: ('Seven.', 'seven', '  SEVEN! ')[k - 1] if model == 'gpt-test-1'
             else ('Six, I think.', 'Seven.', 'Seven.')[k - 1], calls)
    archive.lock()
    done = archive.run_archive(max_usd=1, samples=3)
    assert done['status'] == 'complete' and done['samples'] == 3 and len(calls) == 120
    assert done['answers'] == {'answered': 120, 'no_answer': 0, 'withheld': 0}
    assert rows('SELECT samples FROM archive_runs') == [{'samples': 3}]
    # One row and one settled hold per call; the sample index runs 1..3 inside each (question, answerer).
    stored = rows('SELECT question_id,answerer,sample,reservation_id,changed FROM archive_answers ORDER BY id')
    assert len(stored) == 120 and len({r['reservation_id'] for r in stored}) == 120
    assert [(r['answerer'], r['question_id'], r['sample']) for r in stored[:4]] == [
        ('model-a', 'Q01', 1), ('model-a', 'Q01', 2), ('model-a', 'Q01', 3), ('model-a', 'Q02', 1)]
    assert rows("SELECT COUNT(*) AS n FROM budget_reservations WHERE status='settled' AND agent_id IS NULL")[0]['n'] == 120
    assert [a['recorded'] for a in done['answerers']] == [60, 60]
    payload = archive.public_payload()
    assert archive.payload_problem(payload) is None and archive.export_problem(payload) is None
    assert payload['runs'][0]['samples'] == 3 and len(payload['answers']) == 40           # one public answer per cell
    a, b = cell(payload, answerer='model-a'), cell(payload)
    assert (a['samples'], a['agree'], a['others'], a['text']) == (3, 3, [], 'Seven.')
    # The representative is from the largest group (samples 2 and 3), not sample 1; the other answer is listed with its count.
    assert (b['samples'], b['agree'], b['text']) == (3, 2, 'Seven.') and b['others'] == [{'text': 'Six, I think.', 'count': 1}]
    assert b['id'] == rows("SELECT id FROM archive_answers WHERE answerer='model-b' AND question_id='Q01' AND sample=2")[0]['id']
    assert archive.stability(a, None) == 'steady' and archive.stability(b, None) == 'unsettled'
    # Pages: the third state, the counts, the other answers, the legend row and the method sentence.
    page = archive_pages.render_question(payload, 'Q01')
    assert_page_is_safe(page)
    assert 'gpt-test-1 · server call · 3 of 3 agree' in page and 'gpt-test-2 · server call · 2 of 3 agree' in page
    assert page.count('cell-unsettled') == 1 and page.count('UNSETTLED') == 1 and 'CHANGED' not in page
    assert '<span class="mono">1 of 3</span> Six, I think.</span>' in page and 'role="list"' in page
    assert 'outlined cells: the samples did not agree' in page and '<strong>unsettled</strong> means' in page
    assert "model-b&#x27;s 3 samples did not agree: 2 gave the answer shown. Not counted as a change." in page
    assert 'model-a gave the same answer in every run recorded.' in page and 'model-b gave the same answer' not in page
    index = archive_pages.render_index(payload)
    assert_page_is_safe(index)
    assert index.count('sq sq-unsettled') == 21 and 'Unsettled: asked several times' in index      # 20 strips and the legend
    assert 'model-b: 0 changes across 1 run, 1 unsettled' in index and 'Asked more than once' in index
    assert 'No change in any model across 1 run' in index                                           # unsettled is not a change
    assert 'sq sq-unsettled' in archive_pages.render_trending(payload)                              # the legend there too
    card = archive_pages.render_card(payload, 'Q01')
    assert_page_is_safe(card)
    assert card.count('agree') == 1 and 'server call · 2 of 3 agree' in card and 'Six, I think.' not in card
    for text in (page, index, card):
        assert '<script' not in text and 'style=' not in text


def test_single_sample_archive_has_no_new_key_and_no_new_word(ready, monkeypatch):
    as_real(monkeypatch, lambda model, prompt, n: 'Seven.' if n <= 40 else 'Eight.')
    archive.lock()
    first = archive.run_archive(max_usd=1)
    archive.run_archive(max_usd=1)
    assert 'samples' not in first
    payload = archive.public_payload()
    assert all('samples' not in run for run in payload['runs'])
    assert all(not any(key in answer for key in archive.SAMPLE_FIELDS) for answer in payload['answers'])
    assert rows('SELECT DISTINCT sample FROM archive_answers') == [{'sample': 1}]
    assert rows('SELECT DISTINCT sampling FROM archive_answers') == [{'sampling': 'none'}]            # native openai: nothing pinned, as before
    labels = {archive.stability(a, None if a['run'] == 1 else cell(payload, a['question'], a['answerer'], 1)) for a in payload['answers']}
    assert labels == {'steady', 'changed'}                                                            # never unsettled
    pages = archive_pages.render_site(payload)
    for name, page in pages.items():
        assert 'unsettled' not in page.lower() and ' of 1 agree' not in page and 'Asked more than once' not in page, name
        assert 'outlined' not in page and 'samples' not in page.lower(), name
    assert 'CHANGED' in pages['q/Q01.html'] and 'sq sq-changed' in pages['index.html']
    status = archive.status()
    assert [run['samples'] for run in status['runs']] == [1, 1] and status['samples_configured'] is None


def test_stability_across_runs_and_changed_compares_representatives(ready, monkeypatch):
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', 'model-b=openai:gpt-test-2')
    archive.lock()
    script = {1: ('A.', 'A.', 'A.'), 2: ('B.', 'A.', 'A.'), 3: ('B.', 'B.', 'B.'), 4: ('B.', 'B.', 'B.'), 5: ('C.', 'C.', 'C.'),
              6: ('X.', 'Y.', 'Z.'), 7: ('Y.', 'X.', 'X.')}
    for run in range(1, 8):
        scripted(monkeypatch, lambda model, k, run=run: script[run][k - 1])
        assert archive.run_archive(max_usd=1, samples=3)['status'] == 'complete'
    payload = archive.public_payload()
    assert archive.payload_problem(payload) is None
    series = [cell(payload, run=run) for run in range(1, 8)]
    assert [(a['text'], a['agree'], a['samples'], a['changed']) for a in series] == [
        ('A.', 3, 3, None), ('A.', 2, 3, False), ('B.', 3, 3, True), ('B.', 3, 3, False), ('C.', 3, 3, True),
        ('X.', 1, 3, True),      # a three-way tie: the lowest sample index is shown
        ('X.', 2, 3, False)]     # compared with run 6's representative (X), not with its last sample (Z)
    assert series[5]['others'] == [{'text': 'Y.', 'count': 1}, {'text': 'Z.', 'count': 1}]
    labels = [archive.stability(a, series[n - 1] if n else None) for n, a in enumerate(series)]
    assert labels == ['steady', 'unsettled', 'unsettled', 'steady', 'changed', 'unsettled', 'unsettled']
    # Each sample row keeps its own comparison with the previous representative; the public flag is the representative's.
    assert [r['changed'] for r in rows("SELECT changed FROM archive_answers WHERE question_id='Q01' AND run_id=2 ORDER BY sample")] == [1, 0, 0]
    view = archive_pages._View(payload)
    assert [view.kind(a) for a in series] == ['same', 'unsettled', 'unsettled', 'same', 'changed', 'unsettled', 'unsettled']
    page = archive_pages.render_question(payload, 'Q01')
    assert page.count('cell-unsettled') == 4 and page.count('cell-changed') == 1
    assert '3 of 3 agree · the run before did not agree' in page                                    # run 3: unanimous, but after a split run
    assert 'differs from the run before, where its samples did not agree. Not counted as a change.' in page
    index = archive_pages.render_index(payload)
    assert 'model-b: 1 change across 7 runs, 4 unsettled' in index and 'Last change: model-b' in index
    # The card's "Before" panel follows the one counted change (run 5), not the unsettled runs after it.
    card = archive_pages.render_card(payload, 'Q01')
    assert 'card-panel-changed' in card and 'X.' in card and '2 of 3 agree' in card


def test_representative_rule():
    def row(sample, text, state='answered'):
        return {'sample': sample, 'state': state, 'answer_text': text if state == 'answered' else None,
                'answer_sha256': archive.answer_hash(text) if state == 'answered' else None}
    shown, received, agree, others = archive.representative([row(3, 'b'), row(1, 'a'), row(2, 'B.')])
    assert (shown['sample'], received, agree, others) == (2, 3, 2, [{'text': 'a', 'count': 1}])
    shown, received, agree, others = archive.representative([row(2, 'b'), row(1, 'a')])                 # a tie: lowest index
    assert (shown['sample'], received, agree, others) == (1, 2, 1, [{'text': 'b', 'count': 1}])
    shown, received, agree, others = archive.representative([row(1, '', 'no_answer'), row(2, 'a'), row(3, 'a')])
    assert (shown['sample'], received, agree, others) == (2, 2, 2, [])                                  # an empty sample is not a vote
    shown, received, agree, others = archive.representative([row(1, '', 'no_answer'), row(2, '', 'withheld')])
    assert (shown['sample'], received, agree, others) == (1, 0, 0, [])
    assert archive.representative([row(1, 'a')]) == (row(1, 'a'), 1, 1, [])
    assert not archive.split({'changed': True}) and not archive.split(None) and archive.split({'agree': 2, 'samples': 3})


def test_samples_without_an_answer_and_a_cell_with_none(ready, monkeypatch):
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', 'model-a=openai:gpt-test-1')
    archive.lock()
    cut = engine.Completion('Seven', 40, 120, 'length')
    scripted(monkeypatch, lambda model, k: cut if k == 1 else 'Seven.')
    archive.run_archive(max_usd=1, samples=3)
    scripted(monkeypatch, lambda model, k: cut)
    archive.run_archive(max_usd=1, samples=2)
    payload = archive.public_payload()
    assert archive.payload_problem(payload) is None and [run['samples'] for run in payload['runs']] == [3, 2]
    first, second = cell(payload, answerer='model-a', run=1), cell(payload, answerer='model-a', run=2)
    assert (first['state'], first['samples'], first['agree'], first['others'], first['text']) == ('answered', 2, 2, [], 'Seven.')
    assert (second['state'], second['samples'], second['agree'], second['others'], second['text']) == ('no_answer', 0, 0, [], None)
    page = archive_pages.render_question(payload, 'Q01')
    assert '2 of 2 agree · only 2 of 3 samples answered' in page and 'no sample gave an answer' in page
    assert 'UNSETTLED' not in page and 'sq-unsettled' not in archive_pages.render_index(payload).split('</header>', 1)[1]


# ---- seeds, and the failure rule per call ---------------------------------------------------------------

def test_each_sample_sends_its_own_seed_and_records_it(three, monkeypatch):
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', 'gemma=ollama:gemma3:4b,gem=goog:gemma-x')
    monkeypatch.setenv(var('goog', 'SEND_SAMPLING'), 'NO')
    seen = fake_sdk(monkeypatch)
    done = archive.run_archive(max_usd=0.01, samples=3)
    assert done['status'] == 'complete' and len(seen) == 120
    local = [c['request'] for c in seen if 'localhost' in c['base_url']]
    assert [r['seed'] for r in local[:6]] == [archive.ARCHIVE_SEED, archive.ARCHIVE_SEED + 1, archive.ARCHIVE_SEED + 2] * 2
    assert {r['temperature'] for r in local} == {0}
    assert all('seed' not in c['request'] and 'temperature' not in c['request'] for c in seen if 'goog.example' in c['base_url'])
    recorded = rows("SELECT sample,sampling FROM archive_answers WHERE question_id='Q01' ORDER BY answerer DESC,sample")
    assert recorded == [{'sample': n, 'sampling': f'temperature=0;seed={archive.ARCHIVE_SEED + n - 1}'} for n in (1, 2, 3)] \
        + [{'sample': n, 'sampling': 'none'} for n in (1, 2, 3)]
    assert all(a['sampling_pinned'] is (a['answerer'] == 'gemma') for a in archive.public_payload()['answers'])


def test_a_failed_sample_skips_a_free_model_and_stops_a_priced_one(three, monkeypatch):
    # Zero-priced: the second sample of the first question fails. That model is skipped; the others complete.
    seen = fake_sdk(monkeypatch, lambda base_url, request, n: Status(429) if 'cf.example' in base_url and n == 2 else reply())
    done = archive.run_archive(max_usd=0.01, samples=3)
    assert done['status'] == 'complete_with_gaps' and len(seen) == 122
    assert [(a['answerer'], a['outcome'], a['reason'], a['recorded']) for a in done['answerers']] == [
        ('gemma', 'complete', '', 60), ('llama', 'skipped', 'rate_limited', 1), ('gem', 'complete', '', 60)]
    assert rows('SELECT * FROM budget_reservations WHERE status!=\'settled\' AND status!=\'released\'') == []
    payload = archive.public_payload()
    assert archive.payload_problem(payload) is None and archive.export_problem(payload) is None
    partial = cell(payload, answerer='llama')
    assert (partial['samples'], partial['agree'], partial['others']) == (1, 1, [])
    page = archive_pages.render_question(payload, 'Q01')
    assert '1 of 1 agree · only 1 of 3 samples answered' in page
    assert 'this model was skipped after a failed call' in archive_pages.render_question(payload, 'Q02')
    # Priced: the hold of the failed sample stays unconfirmed and the run stops, as for a single-sample run.
    monkeypatch.setenv(var('cf', 'FREE_TIER'), 'NO')
    monkeypatch.setenv(var('cf', 'INPUT_USD_PER_M'), '0.50')
    monkeypatch.setenv(var('cf', 'OUTPUT_USD_PER_M'), '1.00')
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', 'llama=cf:llama-x')
    seen = fake_sdk(monkeypatch, lambda base_url, request, n: Status(500) if n == 5 else reply())
    done = archive.run_archive(max_usd=0.05, samples=3)
    assert done['status'] == 'incomplete' and len(seen) == 5 and 'kept as unconfirmed' in done['stop_reason']
    assert [(a['outcome'], a['reason'], a['recorded']) for a in done['answerers']] == [('stopped', 'server_error', 4)]
    assert len(core.budget_status()['unconfirmed_reservations']) == 1
    with pytest.raises(archive.ArchiveRefused, match='Unconfirmed provider charge'):
        archive.run_archive(max_usd=0.05, samples=3)


# ---- mock rows, the outside-client route, the database rules -------------------------------------------------

def test_mock_samples_are_never_public(ready, monkeypatch, tmp_path):
    done = archive.dry_run(samples=3)
    assert done['mock'] and done['samples'] == 3 and done['answers']['answered'] == 120
    assert archive.fake_answer(1, 'model-a', {'id': 'Q01'}, 1) == archive.fake_answer(1, 'model-a', {'id': 'Q01'})
    texts = rows("SELECT question_id,answerer,COUNT(DISTINCT answer_sha256) AS n FROM archive_answers GROUP BY 1,2")
    assert {r['n'] for r in texts} == {1, 2}                                     # some fake cells agree, some do not
    monkeypatch.setattr(engine, 'infer', fake('MOCKED-SAMPLE-TEXT'))             # REAL_INFER untouched: a mock run
    archive.lock()
    assert archive.run_archive(max_usd=1, samples=2)['mock']
    assert rows("SELECT DISTINCT source FROM archive_answers") == [{'source': 'mock'}]
    payload = archive.public_payload()
    assert payload['runs'] == [] and payload['answers'] == []
    assert 'MOCKED-SAMPLE-TEXT' not in json.dumps(payload) and 'DRY-RUN' not in ''.join(archive_pages.render_site(payload).values())
    assert [(r['kind'], r['samples']) for r in archive.status()['runs']] == [('mock', 3), ('mock', 2)]


def test_outside_client_keeps_one_answer_per_run_and_question(ready, monkeypatch):
    from live_genesis.app import app
    keys = {item['name']: item['key'] for item in core.seed_agents()}
    auth = {'Authorization': f"Bearer {keys['Astra']}", 'X-Agent-Model': 'vendor/outside-model-2'}
    body = {'run': 1, 'question_id': 'Q03', 'answer': 'Canberra.'}
    archive.lock()
    with TestClient(app) as client:
        monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')        # the web app dropped it from this process on import
        as_real(monkeypatch, 'Seven.')
        archive.run_archive(max_usd=1, samples=3)
        assert client.post('/api/agent/archive-answer', json=body, headers=auth).status_code == 200
        again = client.post('/api/agent/archive-answer', json=body, headers=auth)
        assert again.status_code == 409 and 'never replaced' in again.json()['detail']
    assert rows("SELECT sample FROM archive_answers WHERE source='api'") == [{'sample': 1}]
    with core.connect() as cx:                                             # and the database refuses a second sample from a client
        with pytest.raises(sqlite3.IntegrityError, match='sample index'):
            cx.execute("INSERT INTO archive_answers(run_id,question_set,question_id,answerer,agent_id,model,source,state,answer_text,"
                       "created_at,sample) VALUES (1,'basic-1','Q03','Astra',1,'m','api','answered','x','now',2)")
    payload = archive.public_payload()
    assert archive.payload_problem(payload) is None and archive.export_problem(payload) is None
    mine = cell(payload, 'Q03', 'Astra')
    assert mine['source'] == 'api' and not any(key in mine for key in archive.SAMPLE_FIELDS)
    page = archive_pages.render_question(payload, 'Q03')
    assert 'vendor/outside-model-2 · self-declared</span>' in page and 'gpt-test-1 · server call · 3 of 3 agree' in page


def test_database_rules_for_samples(ready, monkeypatch):
    archive.lock()
    as_real(monkeypatch, 'Seven.')
    archive.run_archive(max_usd=1)                       # run 1: one sample
    archive.run_archive(max_usd=1, samples=2)            # run 2: two
    insert = ("INSERT INTO archive_answers(run_id,question_set,question_id,answerer,model,source,reservation_id,state,answer_text,"
              "created_at,sample) VALUES (?,'basic-1','Q01','model-a','m','engine',1,'answered','x','now',?)")
    with core.connect() as cx:
        for run_id, sample, message in ((1, 2, 'sample index'), (2, 3, 'sample index'), (2, 2, 'UNIQUE'), (1, 1, 'UNIQUE'),
                                        (2, 0, 'CHECK'), (2, 6, 'sample index')):
            with pytest.raises(sqlite3.IntegrityError, match=message):
                cx.execute(insert, (run_id, sample))
        with pytest.raises(sqlite3.IntegrityError, match='can only be closed, once'):
            cx.execute('UPDATE archive_runs SET samples=3')
        with pytest.raises(sqlite3.IntegrityError, match='append-only'):
            cx.execute('UPDATE archive_answers SET sample=1')
        with pytest.raises(sqlite3.IntegrityError, match='CHECK'):
            cx.execute("INSERT INTO archive_runs(number,kind,question_set,set_sha256,started_at,samples) "
                       "SELECT 9,'mock',question_set,set_sha256,'now',6 FROM archive_runs LIMIT 1")


# ---- migration of a database from before repeat samples ---------------------------------------------------------

def old_database(tmp_path, monkeypatch):
    """A database with the archive schema from before repeat samples (fixture v3), a lock of each set and one recorded run."""
    trending_file(tmp_path, monkeypatch, 2)
    core.seed_agents()
    with core.connect() as cx:
        for table in ARCHIVE_TABLES:
            cx.execute(f'DROP TABLE {table}')
        cx.executescript(V3_SCHEMA.read_text(encoding='utf-8'))
        assert 'sample' not in {r[1] for r in cx.execute('PRAGMA table_info(archive_answers)')}
        assert 'samples' not in {r[1] for r in cx.execute('PRAGMA table_info(archive_runs)')}
    archive.lock()
    archive.lock('trending')
    data, trending = archive.load_questions(), archive.load_questions(track='trending')
    with core.connect() as cx:
        cx.execute("INSERT INTO archive_runs(number,kind,question_set,set_sha256,started_at,finished_at,status,max_usd,max_output_tokens) "
                   "VALUES (1,'real',?,?,'2026-10-02T09:00:00+00:00','2026-10-02T09:05:00+00:00','complete',0.05,120)",
                   (data['question_set'], data['sha256']))
        for track, part in (('core', data), ('trending', trending)):
            version, digest = cx.execute('SELECT version,sha256 FROM archive_set_versions WHERE question_set=?', (part['question_set'],)).fetchone()
            cx.execute('INSERT INTO archive_run_sets(run_id,track,question_set,version,sha256,questions) VALUES (1,?,?,?,?,?)',
                       (track, part['question_set'], version, digest, len(part['questions'])))
        for n, (question, text, state) in enumerate((('Q01', 'Seven.', 'answered'), ('Q02', None, 'no_answer'), ('T001', 'A phone.', 'answered')), 1):
            cx.execute("INSERT INTO budget_reservations(agent_id,model,reserved_usd,status,created_at,provider,input_rate,output_rate) "
                       "VALUES (NULL,'gpt-test-1',0.001,'settled','2026-10-02T09:00:00+00:00','openai',0.2,1.2)")
            cx.execute("INSERT INTO archive_answers(run_id,question_set,question_id,answerer,provider,model,source,reservation_id,state,reason,"
                       "answer_text,normalised,answer_sha256,changed,input_tokens,output_tokens,estimated_usd,created_at,endpoint,sampling,"
                       "pricing_basis,local) VALUES (1,?,?,'model-a','openai','gpt-test-1','engine',?,?,?,?,?,?,NULL,40,12,0.00002,"
                       "'2026-10-02T09:00:00+00:00','openai','none','rates',0)",
                       ((trending if question.startswith('T') else data)['question_set'], question, n, state,
                        '' if text else archive.EMPTY, text, archive.normalise(text) if text else None, archive.answer_hash(text) if text else None))
        cx.execute("INSERT INTO archive_run_answerers(run_id,answerer,endpoint,provider,model,pricing_basis,outcome,recorded) "
                   "VALUES (1,'model-a','openai','openai','gpt-test-1','rates','complete',3)")


def test_a_database_from_before_repeat_samples_migrates_in_place(ready, tmp_path, monkeypatch):
    old_database(tmp_path, monkeypatch)
    with core.connect() as cx:
        before_answers = [tuple(r) for r in cx.execute('SELECT * FROM archive_answers ORDER BY id')]
        before_runs = [tuple(r) for r in cx.execute('SELECT * FROM archive_runs')]
        before_other = {t: [tuple(r) for r in cx.execute(f'SELECT * FROM {t}')] for t in ARCHIVE_TABLES if t not in ('archive_answers', 'archive_runs')}
    # Read-only before any migration (the observatory right after the upgrade): the old record is shown as it was.
    before = archive.public_payload(readonly=True)
    assert archive.payload_problem(before) is None and len(before['answers']) == 3 and 'samples' not in before['runs'][0]
    pages_before = archive_pages.render_site(before)
    core.init_db()                                  # what every CLI command and the app do on start
    with core.connect() as cx:
        schema_once = [tuple(r) for r in cx.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')]
    core.init_db()                                  # idempotent
    with core.connect() as cx:
        assert [tuple(r) for r in cx.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')] == schema_once
        columns = [r[1] for r in cx.execute('PRAGMA table_info(archive_answers)')]
        assert columns[-1] == 'sample' and len(columns) == len(before_answers[0]) + 1
        # Nothing rewritten: same ids, same values, sample index 1; the run says one sample.
        assert [tuple(r) for r in cx.execute('SELECT * FROM archive_answers ORDER BY id')] == [row + (1,) for row in before_answers]
        assert [tuple(r) for r in cx.execute('SELECT * FROM archive_runs')] == [row + (1,) for row in before_runs]
        for table, content in before_other.items():
            assert [tuple(r) for r in cx.execute(f'SELECT * FROM {table}')] == content, table
        assert not cx.execute("SELECT 1 FROM sqlite_master WHERE name='archive_answers_rebuild'").fetchone()
        triggers = {r[0]: r[1] for r in cx.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")}
        assert {'archive_answers_no_update', 'archive_answers_no_delete', 'archive_answers_kind', 'archive_answers_real_question',
                'archive_answers_sample'} <= set(triggers) and 'NEW.samples' in triggers['archive_runs_close_only']
        assert cx.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='archive_answers_series'").fetchone()
        assert cx.execute('PRAGMA foreign_key_check').fetchall() == [] and cx.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        for sql in ("UPDATE archive_answers SET answer_text='x' WHERE id=1", 'DELETE FROM archive_answers WHERE id=1'):
            with pytest.raises(sqlite3.IntegrityError, match='append-only'):
                cx.execute(sql)
    assert archive.public_payload() == before and archive_pages.render_site(archive.public_payload()) == pages_before
    # The archive carries on: run 2 with three samples next to the old single-sample run.
    scripted(monkeypatch, lambda model, k: ('Seven.', 'Eight.', 'Seven.')[k - 1])
    assert 'WARNING: the last real run asked 1 sample(s) per question and this one would ask 3' in archive.render_plan(archive.plan(samples=3))
    assert 'WARNING' not in archive.render_plan(archive.plan())
    done = archive.run_archive(max_usd=1, samples=3, only='model-a')
    assert done['run'] == 2 and done['status'] == 'complete' and done['answers']['answered'] == 66
    payload = archive.public_payload()
    assert archive.payload_problem(payload) is None and archive.export_problem(payload) is None
    assert [run.get('samples') for run in payload['runs']] == [None, 3]
    old, new = cell(payload, 'Q01', 'model-a', 1), cell(payload, 'Q01', 'model-a', 2)
    assert 'samples' not in old and (new['samples'], new['agree'], new['changed']) == (3, 2, False)
    assert archive.stability(new, old) == 'unsettled'
    page = archive_pages.render_question(payload, 'Q01')
    assert 'gpt-test-1 · server call</span>' in page and 'gpt-test-1 · server call · 2 of 3 agree' in page


def test_fresh_and_migrated_databases_have_the_same_archive_columns_with_samples(isolated_db):
    def shape(cx):
        return {t: [tuple(r)[1:] for r in cx.execute(f'PRAGMA table_info({t})')] for t in ARCHIVE_TABLES}
    with core.connect() as cx:
        fresh = shape(cx)
        fresh_rules = sorted(r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type IN ('trigger','index') AND tbl_name LIKE 'archive_%'"))
        for table in ARCHIVE_TABLES:
            cx.execute(f'DROP TABLE {table}')
        cx.executescript(V3_SCHEMA.read_text(encoding='utf-8'))
    core.init_db()
    with core.connect() as cx:
        assert shape(cx) == fresh
        assert sorted(r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type IN ('trigger','index') AND tbl_name LIKE 'archive_%'")) == fresh_rules
    # The fixture is the schema as it was: one row per (run, question, answerer), no sample column.
    old = V3_SCHEMA.read_text(encoding='utf-8')
    assert 'UNIQUE (run_id, question_id, answerer),' in old and 'sample INTEGER' not in old and 'samples INTEGER' not in old


# ---- export and --check ----------------------------------------------------------------------------------------

def test_export_check_and_tampering_with_sample_fields(ready, tmp_path, monkeypatch):
    core.seed_agents()
    archive.lock()
    scripted(monkeypatch, lambda model, k: 'Seven.' if model == 'gpt-test-1' else ('Six.', 'Seven.', 'Seven.')[k - 1])
    archive.run_archive(max_usd=1, samples=3)
    out = tmp_path / 'published'
    manifest = snapshot.export_published(out)
    assert manifest['schema'] == 'live-genesis-published/4' and manifest['archive']['answers_by_source']['engine'] == 40
    exported = json.loads((out / 'archive.json').read_bytes())
    assert exported['schema'] == 'live-genesis-archive/3' and exported == json.loads(json.dumps(archive.public_payload()))
    assert exported['runs'][0]['samples'] == 3 and cell(exported)['others'] == [{'text': 'Six.', 'count': 1}]
    snapshot.approve_published(out, confirmed=True)
    assert snapshot.check_published(out)['reviewed'] is True
    site = tmp_path / 'site'
    snapshot.build_from_data(site, out)
    assert '2 of 3 agree' in (site / 'archive' / 'q' / 'Q01.html').read_text(encoding='utf-8')
    good = {name: (out / name).read_bytes() for name in ('archive.json', 'MANIFEST.json')}

    def refused(edit, match):
        retarget_archive(out, edit)
        with pytest.raises(RuntimeError, match=match):
            snapshot.check_published(out)
        for name, content in good.items():
            (out / name).write_bytes(content)
        assert snapshot.check_published(out)['reviewed'] is True

    def split_cell(data):
        return next(a for a in data['answers'] if a['answerer'] == 'model-b')

    refused(lambda d: split_cell(d).update(agree=3), 'do not add up')
    refused(lambda d: split_cell(d).update(samples=4, agree=3), 'sample counts are invalid')
    refused(lambda d: split_cell(d).update(others=[{'text': 'Six.', 'count': 1, 'url': 'x'}]), 'other sampled answers is invalid')
    refused(lambda d: split_cell(d).pop('others'), 'do not belong')
    refused(lambda d: [split_cell(d).pop(key) for key in archive.SAMPLE_FIELDS], 'lacks its sample counts')
    refused(lambda d: d['runs'][0].pop('samples'), 'do not belong')
    refused(lambda d: d['runs'][0].update(samples=6), "run's sample count")
    refused(lambda d: split_cell(d).update(state='no_answer', text=None), 'do not fit its state')
    # A guard hit in one of the other sampled answers blocks the file like one in the answer shown.
    refused(lambda d: split_cell(d)['others'][0].update(text='write to someone@example.org'), 'guard')


def test_payload_rules_for_sample_fields(ready, monkeypatch):
    archive.lock()
    as_real(monkeypatch, 'Seven.')
    archive.run_archive(max_usd=1)
    single = archive.public_payload()
    assert archive.payload_problem(single) is None
    single['answers'][0].update(samples=1, agree=1, others=[])           # sample counts in a single-sample run
    assert archive.payload_problem(single) == 'an answer carries sample counts that do not belong to it'
    single['answers'][0].pop('samples'); single['answers'][0].pop('agree'); single['answers'][0].pop('others')
    single['runs'][0]['samples'] = 1                                      # allowed when present, though never written
    assert archive.payload_problem(single) is None and archive.export_problem(single) is None
    single['runs'][0]['samples'] = True
    assert 'sample count' in archive.payload_problem(single)


# ---- CLI and docs ------------------------------------------------------------------------------------------------

def test_cli_samples_flag_on_plan_dry_run_run_and_status(ready, monkeypatch, capsys):
    calls = []
    as_real(monkeypatch, 'Seven.', calls)
    code, out = run_cli(monkeypatch, capsys, 'archive-plan')
    assert code == 0 and 'Calls per run: 40 (20 questions x 2 models), output limit' in out and 'Samples:' not in out
    code, out = run_cli(monkeypatch, capsys, 'archive-plan', '--samples', '3')
    assert code == 0 and 'Calls per run: 120 (20 questions x 2 models x 3 samples)' in out and 'Samples: each question is asked 3 times' in out
    code, out = run_cli(monkeypatch, capsys, 'archive-plan', '--samples', '6')
    assert code == 1 and 'whole number from 1 to 5' in out
    monkeypatch.setenv('LIVE_ARCHIVE_SAMPLES', '2')
    code, out = run_cli(monkeypatch, capsys, 'archive-plan')
    assert code == 0 and 'Calls per run: 80' in out
    code, out = run_cli(monkeypatch, capsys, 'archive-dry-run', '--samples', '3', '--models', 'model-a')
    assert code == 0 and '"samples": 3' in out and '"answered": 60' in out
    archive.lock()
    code, out = run_cli(monkeypatch, capsys, 'archive-run', '--max-usd', '0.05', '--models', 'model-a')
    assert code == 0 and len(calls) == 40 and '"samples": 2' in out and 'x 2 samples' in out and archive.MOCK_BANNER not in out
    code, out = run_cli(monkeypatch, capsys, 'archive-status')
    status = json.loads(out)
    assert code == 0 and status['samples_configured'] == '2'
    assert [(run['kind'], run['samples'], run['answers']) for run in status['runs']] == [('mock', 3, 60), ('real', 2, 40)]
    assert calls and controls.unconfirmed_ids is not None


def test_docs_and_env_example_describe_the_samples_option():
    doc = (REPO / 'docs' / 'ANSWERS-OVER-TIME.md').read_text(encoding='utf-8')
    env = (REPO / '.env.example').read_text(encoding='utf-8')
    for text in (doc, env):
        assert 'LIVE_ARCHIVE_SAMPLES' in text and 'before the first real run' in text
    assert '--samples' in doc and 'unsettled' in doc and 'agree' in doc and 'archive_schema_v3' not in doc
    assert 'three times' in doc and 'live-genesis-archive/3' in doc and archive.SAMPLES_RULE in doc
