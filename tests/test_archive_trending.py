"""Answers Over Time, the trending track: a question set that grows (archive.py), its pages and
share cards (archive_pages.py), and the upgrade of a database from before it. Offline: every
model call is a replaced `engine.infer`."""
import hashlib
import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from live_genesis import archive, archive_pages, controls, core, engine, snapshot
from test_archive import (HOSTILE, MODELS, REPO, STATIC, TREND, as_real, assert_page_is_safe, fake, question_file,  # noqa: F401
                          ready, retarget_archive, rows, run_cli, trending_file)

OLD_SCHEMA = REPO / 'tests' / 'fixtures' / 'archive_schema_v1.sql'


# ---- the trending question file and its rules ----------------------------------------------------

def test_checked_in_trending_file_is_a_valid_draft_within_the_rules():
    data = archive.load_questions(REPO / 'live_genesis' / 'archive_trending.json', track='trending')
    raw = json.loads((REPO / 'live_genesis' / 'archive_trending.json').read_text(encoding='utf-8'))
    assert data['question_set'] == 'trending' and data['track'] == 'trending'
    assert [q['id'] for q in data['questions']] == [f'T{n:03d}' for n in range(1, len(data['questions']) + 1)]
    for q in data['questions']:
        assert q['prompt'] == 'Answer in one or two sentences.\n\n' + q['text'] and q['text'].endswith('?')
        assert re.fullmatch(r'\d{4}-\d{2}-\d{2}', q['added']) and q['topic'] and q['trend_source']
        assert archive.trending_rule(q['text']) is None and archive.trending_rule(q['topic']) is None
    if data['status'] == 'draft':            # while a draft: every example is marked as a draft example
        assert len(data['questions']) >= 5 and 'DRAFT' in raw['note']
        assert all('DRAFT EXAMPLE' in q['trend_source'] for q in data['questions'])
    # The core file is untouched by the second track: same list, same hash as before it existed.
    assert archive.load_questions()['sha256'] == 'a41c6c9273e1' + archive.load_questions()['sha256'][12:]


@pytest.mark.parametrize('text,rule', [
    ('Do you have feelings?', 'consciousness'), ('Are you conscious?', 'consciousness'),
    ('Should I buy Nvidia stock?', 'advice'), ('Is it safe to take ibuprofen every day?', 'advice'),
    ('What are the symptoms of flu?', 'advice'), ('Can I sue my landlord?', 'advice'),
    ('Who will win the next election?', 'election'), ('How do I vote by mail?', 'election'), ('Was the ballot count rigged?', 'election'),
    ('Why was the senator arrested?', 'accusation'), ('How did the singer die?', 'accusation'),
    ('Does the actor have cancer?', 'accusation'), ('Is the chief executive guilty of fraud?', 'accusation')])
def test_mechanical_rules_refuse_what_they_can_detect(tmp_path, monkeypatch, text, rule):
    assert rule in archive.trending_rule(text)
    trending_file(tmp_path, monkeypatch, 1, edit=lambda d: d['questions'][0].update(text=text, prompt='Answer in one or two sentences.\n\n' + text))
    with pytest.raises(archive.ArchiveRefused, match='not allowed as a trending question'):
        archive.load_questions(track='trending')


def test_allowed_examples_pass_the_rules():
    for text in ('Who is the president of the United States?', 'What is the newest iPhone?', "What is Nvidia's share price today?",
                 'Which company is the most valuable in the world right now?', 'What is the price of gold today?'):
        assert archive.trending_rule(text) is None, text


@pytest.mark.parametrize('edit', [
    lambda d: d['questions'][0].update(id='T002'),                               # ids run in order from T001
    lambda d: d['questions'][1].update(id='Q02'),
    lambda d: d['questions'][0].update(prompt='Answer briefly.\n\n' + d['questions'][0]['text']),
    lambda d: d['questions'][0].update(text='The newest iPhone', prompt='Answer in one or two sentences.\n\nThe newest iPhone'),
    lambda d: d['questions'][0].update(text='New iPhone? Which one?', prompt='Answer in one or two sentences.\n\nNew iPhone? Which one?'),
    lambda d: d['questions'][0].update(added='18 May 2026'), lambda d: d['questions'][0].update(added='2026-02-30'),
    lambda d: d['questions'][1].update(added='2026-01-01'),                      # earlier than the question before it
    lambda d: d['questions'][0].pop('topic'), lambda d: d['questions'][0].update(topic='x' * 41),
    lambda d: d['questions'][0].update(topic='singer arrested'), lambda d: d['questions'][0].pop('trend_source'),
    lambda d: d['questions'][0].update(trend_source='see someone@example.org'),
    lambda d: d['questions'][0].update(topic='zero' + chr(0x200b) + 'width'),
    lambda d: d['questions'][0].update(group='Other'), lambda d: d.update(groups=[]),
    lambda d: d.update(groups=[{'name': f'G{n}', 'why': 'w'} for n in range(5)]),
    lambda d: d.update(status='final'), lambda d: d.update(questions={})])
def test_malformed_trending_files_are_refused(tmp_path, monkeypatch, edit):
    trending_file(tmp_path, monkeypatch, 2, edit=edit)
    with pytest.raises(archive.ArchiveRefused):
        archive.load_questions(track='trending')


# ---- the lock: append-only, versioned ----------------------------------------------------------------

def test_trending_lock_appends_versions_and_never_changes_a_locked_question(ready, tmp_path, monkeypatch):
    trending_file(tmp_path, monkeypatch, 2, status='draft')
    with pytest.raises(archive.ArchiveRefused, match='still says "status": "draft"'):
        archive.lock('trending')
    trending_file(tmp_path, monkeypatch)
    with pytest.raises(archive.ArchiveRefused, match='holds no question'):
        archive.lock('trending')
    assert rows('SELECT * FROM archive_sets') == [] and rows('SELECT * FROM archive_set_versions') == []
    trending_file(tmp_path, monkeypatch, 2)
    first = archive.lock('trending')
    assert (first['version'], first['added'], first['questions'], first['track']) == (1, 2, 2, 'trending')
    with pytest.raises(archive.ArchiveRefused, match='Already locked'):
        archive.lock('trending')
    # Every way of touching a locked question is refused; nothing is written.
    for edit, why in ((lambda d: d['questions'][0].update(rationale='Reworded later.'), 'T001 differs'),
                      (lambda d: d['questions'][1].update(topic='gold'), 'T002 differs'),
                      (lambda d: d['questions'][0].update(trend_source='Another source'), 'T001 differs'),
                      (lambda d: d['groups'][0].update(why='A new reason.'), 'T001 differs'),
                      (lambda d: d['questions'].__delitem__(slice(1, None)), '1 locked question'),
                      (lambda d: d.update(question_set='trending-2'), 'set name differs')):
        trending_file(tmp_path, monkeypatch, 3, edit=edit)
        with pytest.raises(archive.ArchiveRefused, match='does not match the locked question list') as refused:
            archive.lock('trending')
        assert why in str(refused.value)
    trending_file(tmp_path, monkeypatch, 3, edit=lambda d: d['questions'].pop(0))            # removing the first shifts the ids
    with pytest.raises(archive.ArchiveRefused, match='must have the id T001'):
        archive.lock('trending')
    assert rows('SELECT COUNT(*) n FROM archive_questions')[0]['n'] == 2
    # Appending is the one thing allowed.
    trending_file(tmp_path, monkeypatch, 4)
    second = archive.lock('trending')
    assert (second['version'], second['added'], second['questions']) == (2, 2, 4)
    versions = rows('SELECT version,sha256,questions FROM archive_set_versions ORDER BY version')
    assert [(v['version'], v['questions']) for v in versions] == [(1, 2), (2, 4)]
    assert versions[0]['sha256'] == first['sha256'] != versions[1]['sha256'] == second['sha256']
    stored = rows('SELECT id,position,version,added,topic,trend_source,text FROM archive_questions ORDER BY position')
    assert [(q['id'], q['version']) for q in stored] == [('T001', 1), ('T002', 1), ('T003', 2), ('T004', 2)]
    assert [(q['text'], q['topic'], q['added']) for q in stored] == TREND
    # The chain: each version hashes the one before it plus exactly what it added.
    data = archive.load_questions(track='trending')
    with_why = [{**q, 'group_why': data['groups'][0]['why']} for q in data['questions']]
    assert archive.chain_hash('', 'trending', with_why[:2]) == first['sha256']
    assert archive.chain_hash(first['sha256'], 'trending', with_why[2:]) == second['sha256']
    audit = [r['operation'] for r in rows("SELECT operation FROM operator_audit WHERE operation LIKE 'archive-lock%' ORDER BY id")]
    assert len(audit) == 2 and 'version 1: 2 added, 2 in all' in audit[0] and second['sha256'] in audit[1]
    # The core lock is separate and unchanged: once, all or nothing.
    core_lock = archive.lock()
    assert core_lock['questions'] == 20 and core_lock['sha256'] == archive.load_questions()['sha256'] and core_lock['version'] == 1
    with pytest.raises(archive.ArchiveRefused, match='Already locked'):
        archive.lock()
    assert {r['track']: r['question_set'] for r in rows('SELECT track,question_set FROM archive_sets')} == {'core': 'basic-1', 'trending': 'trending'}


def test_database_itself_keeps_locked_sets_append_only(ready, tmp_path, monkeypatch):
    trending_file(tmp_path, monkeypatch, 2)
    archive.lock('trending'); archive.lock()
    q = "INSERT INTO archive_questions(question_set,id,position,group_name,text,rationale,prompt,version) VALUES "
    for sql, message in (
            ("UPDATE archive_set_versions SET sha256='0'", 'archive_set_versions is append-only'),
            ("DELETE FROM archive_set_versions", 'archive_set_versions is append-only'),
            ("UPDATE archive_questions SET text='x' WHERE id='T001'", 'archive_questions is append-only'),
            ("DELETE FROM archive_questions WHERE id='T001'", 'archive_questions is append-only'),
            ("UPDATE archive_sets SET track='trending'", 'archive_sets is append-only'),
            # the core set is locked once: no second version, no extra question
            ("INSERT INTO archive_set_versions VALUES ('basic-1',2,'0',21,'now')", 'only the trending set grows'),
            (q + "('basic-1','Q21',21,'g','t','r','p',1)", 'must belong to a recorded lock'),
            (q + "('basic-1','Q21',21,'g','t','r','p',2)", 'must belong to a recorded lock'),
            # a trending version must be the next number and must hold more questions
            ("INSERT INTO archive_set_versions VALUES ('trending',3,'0',5,'now')", 'only the trending set grows'),
            ("INSERT INTO archive_set_versions VALUES ('trending',2,'0',2,'now')", 'only the trending set grows'),
            ("INSERT INTO archive_set_versions VALUES ('trending',1,'0',9,'now')", 'only the trending set grows'),
            ("INSERT INTO archive_set_versions VALUES ('nowhere',1,'0',1,'now')", 'only the trending set grows'),
            # a question cannot be added outside a recorded lock, or in front of the locked ones
            (q + "('trending','T003',3,'g','t','r','p',1)", 'must belong to a recorded lock'),
            (q + "('trending','T009',3,'g','t','r','p',2)", 'must belong to a recorded lock'),
            ("INSERT INTO archive_sets(question_set,sha256,questions,locked_at,track) VALUES ('other','0',1,'now','core')", 'UNIQUE'),
            ("INSERT INTO archive_sets(question_set,sha256,questions,locked_at,track) VALUES ('other','0',1,'now','side')", 'CHECK')):
        with core.connect() as cx:
            with pytest.raises(sqlite3.IntegrityError, match=message):
                cx.execute(sql)
    with core.connect() as cx:           # a real run or run-set row needs exactly a locked version
        with pytest.raises(sqlite3.IntegrityError, match='needs the locked question set'):
            cx.execute("INSERT INTO archive_runs(number,kind,question_set,set_sha256,started_at) VALUES (1,'real','trending','0','now')")
        cx.execute("INSERT INTO archive_runs(number,kind,question_set,set_sha256,started_at) SELECT 1,'real',question_set,sha256,'now' "
                   "FROM archive_set_versions WHERE question_set='trending'")
        for values in ("(1,'trending','trending',1,'0',2)", "(1,'trending','trending',2,(SELECT sha256 FROM archive_set_versions WHERE question_set='trending'),2)",
                       "(1,'trending','trending',1,(SELECT sha256 FROM archive_set_versions WHERE question_set='trending'),3)",
                       "(1,'core','trending',1,(SELECT sha256 FROM archive_set_versions WHERE question_set='trending'),2)"):
            with pytest.raises(sqlite3.IntegrityError, match='needs the locked question set'):
                cx.execute("INSERT INTO archive_run_sets(run_id,track,question_set,version,sha256,questions) VALUES " + values)
        cx.execute("INSERT INTO archive_run_sets(run_id,track,question_set,version,sha256,questions) "
                   "SELECT 1,'trending',question_set,version,sha256,questions FROM archive_set_versions WHERE question_set='trending'")
        for sql in ("UPDATE archive_run_sets SET questions=1", "DELETE FROM archive_run_sets"):
            with pytest.raises(sqlite3.IntegrityError, match='archive_run_sets is append-only'):
                cx.execute(sql)
        with pytest.raises(sqlite3.IntegrityError, match='needs a locked question'):         # T003 is not locked
            cx.execute("INSERT INTO archive_answers(run_id,question_set,question_id,answerer,agent_id,model,source,state,answer_text,"
                       "created_at) VALUES (1,'trending','T003','x',NULL,'m','engine','no_answer',NULL,'now')")


# ---- runs over both tracks ------------------------------------------------------------------------------

def answered(run):
    return rows('SELECT question_id FROM archive_answers a JOIN archive_runs r ON r.id=a.run_id WHERE r.number=? AND r.kind=? '
                "AND a.answerer='model-a' ORDER BY a.id", run, 'real')


def test_a_run_asks_every_locked_question_of_both_sets_and_set_limits_it(ready, tmp_path, monkeypatch):
    calls = []
    as_real(monkeypatch, lambda model, prompt, n: 'Answer to: ' + prompt.split('\n\n')[1], calls)
    trending_file(tmp_path, monkeypatch, 2)
    archive.lock()
    # Trending is not locked yet: the default run leaves it out, and asking for it alone is refused.
    assert archive.plan()['calls'] == 40 and [t['asked'] for t in archive.plan()['tracks']] == [True, False]
    with pytest.raises(archive.ArchiveRefused, match='trending question list is not locked'):
        archive.run_archive(max_usd=1, question_set='trending')
    first = archive.run_archive(max_usd=1)
    assert first['answers']['answered'] == 40 and first['questions_asked'] == {'core': 20}
    archive.lock('trending')
    planned = archive.plan()
    assert planned['calls'] == 44 and [q['id'] for q in planned['questions']][-3:] == ['Q20', 'T001', 'T002']
    assert set(planned['holds']) == {q['id'] for q in planned['questions']}
    text = archive.render_plan(planned)
    assert 'Calls per run: 44 (22 questions x 2 models)' in text and 'Also asked: question set trending (2 questions' in text
    assert '  T001  [new phones] What is the newest iPhone?' in text and 'version 1' in text
    second = archive.run_archive(max_usd=1)
    assert second['answers']['answered'] == 44 and second['questions_asked'] == {'core': 20, 'trending': 2}
    assert [a['question_id'] for a in answered(2)] == [f'Q{n:02d}' for n in range(1, 21)] + ['T001', 'T002']
    assert calls[-1]['prompt'] == 'Answer in one or two sentences.\n\nWhat is the price of gold today?'
    assert calls[-1]['system'] == archive.SYSTEM_LINE
    assert rows("SELECT DISTINCT question_set FROM archive_answers WHERE question_id LIKE 'T%'") == [{'question_set': 'trending'}]
    # --set limits a run to one track.
    assert archive.plan(question_set='core')['calls'] == 40 and archive.plan(question_set='trending')['calls'] == 4
    third = archive.run_archive(max_usd=1, question_set='trending')
    assert third['answers']['answered'] == 4 and third['questions_asked'] == {'trending': 2} and third['question_set'] == 'trending'
    fourth = archive.run_archive(max_usd=1, question_set='core')
    assert fourth['questions_asked'] == {'core': 20} and [a['question_id'] for a in answered(4)][-1] == 'Q20'
    with pytest.raises(archive.ArchiveRefused, match='--set must be core or trending'):
        archive.run_archive(max_usd=1, question_set='basic-1')
    # A question added to the file but not locked is never asked; once locked, the next run asks it.
    trending_file(tmp_path, monkeypatch, 3)
    assert archive.plan()['calls'] == 44 and archive.plan()['tracks'][1]['pending'] == 1
    assert '1 more in trending.json are not locked yet and are not asked' in archive.render_plan(archive.plan())
    assert archive.run_archive(max_usd=1)['questions_asked'] == {'core': 20, 'trending': 2}
    archive.lock('trending')
    sixth = archive.run_archive(max_usd=1)
    assert sixth['questions_asked'] == {'core': 20, 'trending': 3} and sixth['answers']['answered'] == 46
    sets = rows("SELECT s.track,s.version,s.questions FROM archive_run_sets s JOIN archive_runs r ON r.id=s.run_id WHERE r.number=6 ORDER BY s.track")
    assert sets == [{'track': 'core', 'version': 1, 'questions': 20}, {'track': 'trending', 'version': 2, 'questions': 3}]
    # The changed flag works per question across the runs that asked it.
    t1 = rows("SELECT a.changed FROM archive_answers a JOIN archive_runs r ON r.id=a.run_id WHERE a.question_id='T001' AND a.answerer='model-a' ORDER BY r.number")
    assert [r['changed'] for r in t1] == [None, 0, 0, 0]
    # The public payload says what each run asked; a trending question has no cell before its first run.
    payload = archive.public_payload()
    assert archive.payload_problem(payload) is None and payload['schema'] == 'live-genesis-archive/3'
    assert [r['asked'] for r in payload['runs']] == [{'core': 20, 'trending': 0}, {'core': 20, 'trending': 2}, {'core': 0, 'trending': 2},
                                                     {'core': 20, 'trending': 0}, {'core': 20, 'trending': 2}, {'core': 20, 'trending': 3}]
    assert payload['trending']['locked'] is True and payload['trending']['version'] == 2
    assert [q['id'] for q in payload['trending']['questions']] == ['T001', 'T002', 'T003']
    assert payload['trending']['questions'][0] == {
        'id': 'T001', 'group': 'Trending', 'text': 'What is the newest iPhone?', 'rationale': 'Why question 1 is worth asking.',
        'prompt': 'Answer in one or two sentences.\n\nWhat is the newest iPhone?', 'added': '2026-05-18', 'topic': 'new phones',
        'trend_source': 'Google Trends US, 2026-05-18'}
    listing = archive_pages.render_trending(payload)
    assert 'aria-label="model-a: no change; 1 check"' in listing                  # T003: one square, no "missing" ones before it
    assert 'aria-label="model-a: no change; 4 checks"' in listing                 # T001: runs 2, 3, 5, 6
    assert listing.count('sq-none') == 1                                          # only the key's sample square
    page = archive_pages.render_question(payload, 'T003')
    assert page.count('role="rowheader"') == 1 and 'acard-none' not in page and 'id="m-run-6"' in page and 'm-run-5' not in page
    core_page = archive_pages.render_question(payload, 'Q01')
    assert core_page.count('role="rowheader"') == 5 and 'run 3' not in core_page    # run 3 asked the trending set only
    assert 'aria-label="model-a: no change; 5 checks"' in archive_pages.render_index(payload)


def test_a_changed_or_missing_trending_file_stops_real_answers(ready, tmp_path, monkeypatch):
    calls = []
    as_real(monkeypatch, 'Seven.', calls)
    trending_file(tmp_path, monkeypatch, 2)
    archive.lock(); archive.lock('trending')
    for edit in (lambda d: d['questions'][0].update(rationale='Changed.'), lambda d: d['questions'].pop(), lambda d: d.update(status='draft')):
        trending_file(tmp_path, monkeypatch, 2, edit=edit)
        for scope in (None, 'trending'):
            with pytest.raises(archive.ArchiveRefused, match='does not match the locked question list'):
                archive.run_archive(max_usd=1, question_set=scope)
        assert archive.status()['trending']['file_matches_lock'] is False
    (tmp_path / 'trending.json').unlink()
    with pytest.raises(archive.ArchiveRefused, match='missing or not valid JSON'):
        archive.run_archive(max_usd=1)
    assert calls == [] and rows('SELECT * FROM archive_runs') == [] and rows('SELECT * FROM budget_reservations') == []
    # The core set alone still runs: a broken trending file does not take the core archive down with it.
    assert archive.run_archive(max_usd=1, question_set='core')['answers']['answered'] == 40
    trending_file(tmp_path, monkeypatch, 2)
    assert archive.run_archive(max_usd=1)['answers']['answered'] == 44


def test_dry_run_covers_both_files_and_is_never_public(ready, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, 'infer', fake(AssertionError('a dry run calls no model')))
    trending_file(tmp_path, monkeypatch, 3, status='draft')
    done = archive.dry_run()
    assert done['mock'] and done['answers']['answered'] == 46 and done['questions_asked'] == {'core': 20, 'trending': 3}
    assert archive.dry_run(question_set='trending')['answers']['answered'] == 6
    assert archive.dry_run(question_set='core')['answers']['answered'] == 40
    payload = archive.public_payload()
    assert payload['runs'] == [] and payload['answers'] == [] and payload['trending']['locked'] is False
    assert [q['id'] for q in payload['trending']['questions']] == ['T001', 'T002', 'T003']       # shown as drafts, from the file
    assert 'Draft question' in archive_pages.render_question(payload, 'T001')
    assert 'These questions are drafts.' in archive_pages.render_trending(payload)


# ---- upgrade of a database from before the trending track ---------------------------------------------------

def old_database(monkeypatch):
    """Rebuild the archive tables as first released and fill them the way that release did: a
    locked core set, one run, answers. Returns the locked hash."""
    core.seed_agents()
    data = archive.load_questions()
    with core.connect() as cx:
        for table in ('archive_run_sets', 'archive_answers', 'archive_runs', 'archive_questions', 'archive_set_versions', 'archive_sets'):
            cx.execute(f'DROP TABLE {table}')
        cx.executescript(OLD_SCHEMA.read_text(encoding='utf-8'))
        cx.execute("INSERT INTO archive_sets VALUES (?,?,20,'2026-10-01T10:00:00+00:00')", (data['question_set'], data['sha256']))
        why = {g['name']: g['why'] for g in data['groups']}
        for q in data['questions']:
            cx.execute('INSERT INTO archive_questions(question_set,id,position,group_name,group_why,text,rationale,prompt) VALUES (?,?,?,?,?,?,?,?)',
                       (data['question_set'], q['id'], q['position'], q['group'], why[q['group']], q['text'], q['rationale'], q['prompt']))
        cx.execute("INSERT INTO archive_runs(number,kind,question_set,set_sha256,started_at,finished_at,status) "
                   "VALUES (1,'real',?,?,'2026-10-02T09:00:00+00:00','2026-10-02T09:05:00+00:00','complete')", (data['question_set'], data['sha256']))
        for n, q in enumerate(data['questions'], 1):
            digest = hashlib.sha256(b'canberra').hexdigest()
            cx.execute("INSERT INTO archive_answers(run_id,question_set,question_id,answerer,agent_id,model,source,state,answer_text,normalised,"
                       "answer_sha256,changed,created_at) VALUES (1,?,?,'Astra',1,'outside-model-1','api','answered','Canberra.','canberra',?,NULL,"
                       "'2026-10-02T09:00:00+00:00')", (data['question_set'], q['id'], digest))
        names = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE name LIKE 'archive%'")}
        assert 'archive_set_versions' not in names and 'archive_runs_real_needs_lock' in names
        assert 'track' not in {r[1] for r in cx.execute('PRAGMA table_info(archive_sets)')}
    return data['sha256']


def test_a_database_from_before_the_trending_track_keeps_working(ready, tmp_path, monkeypatch):
    digest = old_database(monkeypatch)
    trending_file(tmp_path, monkeypatch, 2)
    # Read-only, before any migration (the observatory right after the upgrade): the old record is shown as it was.
    before = archive.public_payload(readonly=True)
    assert archive.payload_problem(before) is None
    assert before['locked'] is True and before['set_hash'] == digest and before['locked_at'] == '2026-10-01T10:00:00+00:00'
    assert len(before['answers']) == 20 and before['runs'][0]['asked'] == {'core': 20, 'trending': 0}
    assert before['trending']['locked'] is False
    core.init_db()                                                   # what every CLI command and the app do on start
    core.init_db()                                                   # and it is idempotent
    with core.connect() as cx:
        names = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE name LIKE 'archive%'")}
        assert 'archive_runs_real_needs_lock' not in names and {'archive_runs_real_needs_version', 'archive_set_versions',
                                                                'archive_run_sets', 'archive_questions_in_version'} <= names
    assert rows('SELECT * FROM archive_sets') == [{'question_set': 'basic-1', 'sha256': digest, 'questions': 20,
                                                   'locked_at': '2026-10-01T10:00:00+00:00', 'track': 'core'}]
    assert rows('SELECT * FROM archive_set_versions') == [{'question_set': 'basic-1', 'version': 1, 'sha256': digest, 'questions': 20,
                                                           'locked_at': '2026-10-01T10:00:00+00:00'}]
    assert {(q['version'], q['added'], q['topic']) for q in rows('SELECT version,added,topic FROM archive_questions')} == {(1, '', '')}
    assert rows('SELECT COUNT(*) n FROM archive_answers')[0]['n'] == 20 and rows('SELECT COUNT(*) n FROM archive_run_sets')[0]['n'] == 0
    after = archive.public_payload()
    assert {k: v for k, v in after.items() if k != 'trending'} == {k: v for k, v in before.items() if k != 'trending'}
    state = archive.status()
    assert state['file_matches_lock'] is True and state['locked']['sha256'] == digest and state['trending']['locked'] is None
    # The old lock still is the lock: it cannot be taken again, and the old protections hold.
    with pytest.raises(archive.ArchiveRefused, match='Already locked'):
        archive.lock()
    for sql, message in (("UPDATE archive_answers SET answer_text='x'", 'archive_answers is append-only'),
                         ("DELETE FROM archive_sets", 'archive_sets is append-only'),
                         ("UPDATE archive_questions SET text='x'", 'archive_questions is append-only'),
                         ("INSERT INTO archive_runs(number,kind,question_set,set_sha256,started_at) VALUES (9,'real','basic-1','0','now')",
                          'needs the locked question set')):
        with core.connect() as cx:
            with pytest.raises(sqlite3.IntegrityError, match=message):
                cx.execute(sql)
    # New runs continue the old series: same lock, next run number, compared with the old answers.
    as_real(monkeypatch, 'Canberra.')
    done = archive.run_archive(max_usd=1)
    assert done['run'] == 2 and done['answers']['answered'] == 40 and done['questions_asked'] == {'core': 20}
    assert core.seed_agents() == []
    with core.connect() as cx:
        cx.execute('BEGIN IMMEDIATE')
        stored = archive._store(cx, 2, 'basic-1', 'Q03', 'Astra', model='outside-model-2', source='api', state='answered',
                                text='Sydney.', agent_id=1)
    assert stored['changed'] is True                                 # against the answer recorded before the upgrade
    # The trending track starts on the same database, and the next run covers both.
    assert archive.lock('trending')['version'] == 1
    assert archive.run_archive(max_usd=1)['questions_asked'] == {'core': 20, 'trending': 2}
    payload = archive.public_payload()
    assert archive.payload_problem(payload) is None
    assert [r['asked'] for r in payload['runs']] == [{'core': 20, 'trending': 0}, {'core': 20, 'trending': 0}, {'core': 20, 'trending': 2}]
    pages = archive_pages.render_site(payload)
    assert 'Canberra.' in pages['q/Q03.html'] and 'id="m-run-1"' in pages['q/Q03.html'] and 't/T001.html' in pages


# ---- outside clients ------------------------------------------------------------------------------------------

def test_outside_client_can_answer_a_trending_question_a_run_asked(ready, tmp_path, monkeypatch):
    from live_genesis.app import app
    keys = {item['name']: item['key'] for item in core.seed_agents()}
    auth = {'Authorization': f"Bearer {keys['Astra']}", 'X-Agent-Model': 'vendor/outside-model-2'}
    body = {'run': 1, 'question_id': 'T001', 'answer': 'The newest one I know about came out in 2025.'}
    trending_file(tmp_path, monkeypatch, 1)
    archive.lock()
    as_real(monkeypatch, 'Seven.')
    with TestClient(app) as client:
        monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')        # the web app dropped it from this process on import
        archive.run_archive(max_usd=1)                                # run 1: core only
        refused = client.post('/api/agent/archive-answer', json=body, headers=auth)
        assert refused.status_code == 409 and 'not accepting answers' in refused.json()['detail']       # trending not locked
        archive.lock('trending')
        late = client.post('/api/agent/archive-answer', json=body, headers=auth)
        assert late.status_code == 409 and 'Run 1 did not ask this question' in late.json()['detail']
        assert client.post('/api/agent/archive-answer', json={**body, 'question_id': 'Q03'}, headers=auth).status_code == 200
        archive.run_archive(max_usd=1)                                # run 2: both
        done = client.post('/api/agent/archive-answer', json={**body, 'run': 2}, headers=auth)
        assert done.status_code == 200 and done.json()['question_id'] == 'T001' and done.json()['source'] == 'api'
        for question, status in (('T002', 404), ('T01', 422), ('T0001', 422), ('X001', 422)):
            response = client.post('/api/agent/archive-answer', json={**body, 'run': 2, 'question_id': question}, headers=auth)
            assert response.status_code == status, question
        trending_file(tmp_path, monkeypatch, 2)                       # T002 is in the file but not locked: still unknown
        assert client.post('/api/agent/archive-answer', json={**body, 'run': 2, 'question_id': 'T002'}, headers=auth).status_code == 404
        archive.lock('trending')                                      # locked now, but run 2 did not ask it
        response = client.post('/api/agent/archive-answer', json={**body, 'run': 2, 'question_id': 'T002'}, headers=auth)
        assert response.status_code == 409 and 'did not ask' in response.json()['detail']
    stored = rows("SELECT question_set,question_id,source FROM archive_answers WHERE source='api' ORDER BY id")
    assert stored == [{'question_set': 'basic-1', 'question_id': 'Q03', 'source': 'api'},
                      {'question_set': 'trending', 'question_id': 'T001', 'source': 'api'}]
    assert 'Sent in by someone else, not checked · version vendor/outside-model-2' in archive_pages.render_question(archive.public_payload(), 'T001')


# ---- reviewed export -----------------------------------------------------------------------------------------------

def test_export_and_check_understand_both_sets(ready, tmp_path, monkeypatch):
    core.seed_agents()
    trending_file(tmp_path, monkeypatch, 2)
    archive.lock(); archive.lock('trending')
    as_real(monkeypatch, 'Seven.')
    archive.run_archive(max_usd=1, question_set='core')
    archive.run_archive(max_usd=1)
    out = tmp_path / 'published'
    manifest = snapshot.export_published(out)
    assert sorted(p.name for p in out.iterdir()) == ['MANIFEST.json', 'archive.json', 'overview.json']        # still one archive file
    trend_hash = rows("SELECT sha256 FROM archive_set_versions WHERE question_set='trending'")[0]['sha256']
    assert manifest['schema'] == 'live-genesis-published/5' and manifest['archive']['runs'] == 2
    assert manifest['archive']['trending'] == {'questions': 2, 'question_set': 'trending', 'set_hash': trend_hash, 'version': 1}
    assert manifest['archive']['answers_by_source'] == {'engine': 84, 'api': 0, 'demo': 0, 'mock': 0}
    exported = json.loads((out / 'archive.json').read_bytes())
    assert exported == json.loads(json.dumps(archive.public_payload())) and exported['schema'] == 'live-genesis-archive/3'
    snapshot.approve_published(out, confirmed=True)
    good = {name: (out / name).read_bytes() for name in ('archive.json', 'MANIFEST.json')}
    site = tmp_path / 'site'
    monkeypatch.setenv('LIVE_DB', str(tmp_path / 'no-such.sqlite'))
    snapshot.build_from_data(site, out)
    for name in ('trending.html', 't/T001.html', 't/T002.html', 'card/T001.html', 'card/Q01.html', 'q/Q01.html'):
        assert (site / 'archive' / name).is_file(), name
    page = (site / 'archive' / 'card' / 'T001.html').read_text(encoding='utf-8')
    assert 'AI-generated · we asked it ourselves · may be wrong' in page and 'scripted' not in page.lower()
    assert 'data-method="asked-by-server"' in page and 'data-provenance="recorded"' in page

    def refused(edit, match):
        retarget_archive(out, edit)
        for call in (lambda: snapshot.check_published(out), lambda: snapshot.build_from_data(tmp_path / 'again', out)):
            with pytest.raises(RuntimeError, match=match):
                call()
        for name, content in good.items():
            (out / name).write_bytes(content)
        assert snapshot.check_published(out)['reviewed'] is True

    refused(lambda d: d['trending'].update(locked=False, version=0), 'trending questions that are not locked')
    refused(lambda d: d['trending'].update(locked=False), 'version does not fit its lock state')
    assert archive.payload_problem({k: v for k, v in json.loads(good['archive.json']).items() if k != 'trending'}) == \
        'key trending is missing or has the wrong type'
    broken = json.loads(good['archive.json']); del broken['trending']['set_hash']
    assert archive.payload_problem(broken) == 'trending: key set_hash is missing or has the wrong type'
    refused(lambda d: d['trending']['questions'][0].update(id='../x'), 'trending question ids must be distinct and look like T001')
    refused(lambda d: d['trending']['questions'][1].update(id='T001'), 'trending question ids must be distinct')
    refused(lambda d: d['trending']['questions'][0].pop('topic'), 'a question lacks a field')
    refused(lambda d: d['trending']['questions'][0].update(added='May 2026'), 'added date is not YYYY-MM-DD')
    refused(lambda d: d['trending']['questions'][0].update(topic='mail someone@example.org'), 'looks like an email address')
    refused(lambda d: d['trending']['questions'][0].update(trend_source='pay at paypal.me/someone'), 'payment or donation link')
    refused(lambda d: d['runs'][0].pop('asked'), 'does not say how many questions of each track it asked')
    refused(lambda d: d['runs'][0]['asked'].update(trending=3), 'does not say how many questions')
    refused(lambda d: d['runs'][0]['asked'].update(core=True), 'does not say how many questions')
    refused(lambda d: d['runs'][1]['asked'].update(trending=1), 'a question its run did not ask')          # an answer to T002 is there
    refused(lambda d: [a.update(run=1) for a in d['answers'] if a['question'] == 'T001'], 'a question its run did not ask')
    refused(lambda d: d.update(schema='live-genesis-archive/1'), 'unknown schema')
    # The manifest's own figures for the trending set are compared too.
    manifest = json.loads(good['MANIFEST.json']); manifest['archive']['trending']['questions'] = 3
    (out / 'MANIFEST.json').write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match='counts in the manifest do not match archive.json'):
        snapshot.check_published(out)
    manifest = json.loads(good['MANIFEST.json']); manifest['schema'] = 'live-genesis-published/2'
    (out / 'MANIFEST.json').write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match='not a live-genesis-published/5 manifest'):                       # an export made before the upgrade
        snapshot.check_published(out)


def test_a_trending_only_archive_can_be_exported(ready, tmp_path, monkeypatch):
    core.seed_agents()
    trending_file(tmp_path, monkeypatch, 1)
    archive.lock('trending')
    as_real(monkeypatch, 'Seven.')
    done = archive.run_archive(max_usd=1)                            # the core list is still a draft: only the locked set is asked
    assert done['questions_asked'] == {'trending': 1} and done['answers']['answered'] == 2
    out = tmp_path / 'published'
    snapshot.export_published(out)
    snapshot.approve_published(out, confirmed=True)
    exported = json.loads((out / 'archive.json').read_bytes())
    assert exported['locked'] is False and exported['trending']['locked'] is True and exported['runs'][0]['asked'] == {'core': 0, 'trending': 1}
    retarget_archive(out, lambda d: d['runs'][0]['asked'].update(core=20))
    with pytest.raises(RuntimeError, match='question list is not locked'):
        snapshot.check_published(out)


# ---- the scripted demo --------------------------------------------------------------------------------------------------

def test_demo_adds_scripted_trending_questions_that_start_late(ready, monkeypatch):
    monkeypatch.setattr(engine, 'infer', fake(AssertionError('the demo calls no model')))
    archive.demo_archive()
    counts = rows("SELECT r.number, COALESCE((SELECT questions FROM archive_run_sets s WHERE s.run_id=r.id AND s.track='trending'),0) n "
                  "FROM archive_runs r ORDER BY r.number")
    assert [c['n'] for c in counts] == [0, 0, 1, 1, 2, 2, 3, 3]
    assert {r['source'] for r in rows("SELECT DISTINCT source FROM archive_answers WHERE question_id LIKE 'T%'")} == {'demo'}
    assert rows('SELECT * FROM archive_sets') == [] and rows('SELECT * FROM archive_questions') == []        # nothing is locked by a demo
    payload = archive.public_payload()
    assert archive.payload_problem(payload) is None and payload['demo_only'] is True
    trending = payload['trending']
    assert trending['locked'] is False and trending['question_set'] == 'trending-sample'
    assert [(q['id'], q['added'][:7], q['topic']) for q in trending['questions']] == [
        ('T001', '2026-05', 'new phones'), ('T002', '2026-07', 'chip stocks'), ('T003', '2026-09', 'AI stocks')]
    assert {q['trend_source'] for q in trending['questions']} == {'Scripted sample, not a real trend'}
    for item, question in zip(archive.DEMO_TRENDING, trending['questions']):
        assert set(item['script']) == set(archive.DEMO_MODELS) and archive.trending_rule(question['text']) is None
        assert question['added'] < archive.DEMO_DATES[item['first_run'] - 1] and question['added'] > archive.DEMO_DATES[item['first_run'] - 2]
    # The demo's questions do not come from the owner's file: emptying it changes nothing.
    assert archive.TRENDING_FILE.name == 'trending.json' and json.loads(archive.TRENDING_FILE.read_text(encoding='utf-8'))['questions'] == []
    # Nothing of the checked-in drafts leaks into a demo either way.
    assert archive.demo_trending()['sha256'] == trending['set_hash']


def test_trending_pages_for_the_demo(ready):
    archive.demo_archive()
    payload = archive.public_payload()
    pages = {name: page for name, page in archive_pages.render_site(payload).items() if name.endswith('.html')}
    models, listing, t2 = pages['models.html'], pages['trending.html'], pages['t/T002.html']
    for name, page in pages.items():
        assert_page_is_safe(page)
        assert 'Sample we wrote, not a real AI answer' in page, name
        assert '<script' not in page and ' style=' not in page and 'onclick' not in page
    # Navigation: Trending stays reachable from every page; the model list has its own page.
    for page in (pages['index.html'], models, listing, t2, pages['q/Q01.html'], pages['c/C001.html']):
        assert re.search(r'<a href="(\.\./)?trending\.html"( aria-current="page")?>Trending</a>', page)
        assert re.search(r'<a href="(\.\./)?models\.html"( aria-current="page")?>Models</a>', page)
    assert '<a href="trending.html" aria-current="page">Trending</a>' in listing and '<a href="models.html" aria-current="page">Models</a>' in models
    assert '<a href="../trending.html" aria-current="page">Trending</a>' in t2
    assert models.count('class="timeline"') == 60 and 't/T00' not in models       # the model list keeps to the fixed list
    # The list: newest first, each model's newest answer inline, timelines that start late.
    assert listing.index('t/T003.html') < listing.index('t/T002.html') < listing.index('t/T001.html')
    assert listing.count('class="timeline"') == 9 and listing.count('class="mini-text"') == 9
    assert '<h3 class="row-q"><a href="t/T002.html">What is Nvidia&#x27;s share price today?</a></h3>' in listing
    assert 'aria-label="model-a: no change; 4 checks"' in listing and 'aria-label="model-a: no change; 2 checks"' in listing
    assert re.search(r'aria-label="model-b: changed Oct 2026; 4 checks"', listing)
    assert listing.count('<span class="sq sq-none"></span>') == 1                # the key only: no dashed squares before a question existed
    assert listing.count('<span class="sq ') == 3 + 3 * (6 + 4 + 2)
    assert 'written by hand' in listing and 'one square per check, oldest first' in listing
    assert listing.count('<main') == 1 and listing.count('<h1') == 1
    # One trending question: the same layout plus topic and source.
    assert '<span class="q-id mono">T002</span><span class="chip chip-topic">chip stocks</span><span class="chip-note">added Jul 2026</span>' in t2
    assert 'Topic taken from: Scripted sample, not a real trend. Added 20 Jul 2026;' in t2
    assert t2.count('role="rowheader"') == 4 and 'Aug 2026' in t2 and '<label for="pick-4">Aug 2026</label>' in t2 and 'pick-5' not in t2
    assert t2.index('id="m-run-8"') < t2.index('id="m-run-5"') and 'acard-none' not in t2
    assert t2.count('<span class="tag-changed mono">Changed</span>') == 1 and '<span class="t-word mono">changed</span>' in t2
    assert '<pre class="prompt mono">Answer in one or two sentences.\n\nWhat is Nvidia&#x27;s share price today?</pre>' in t2
    assert '<a class="back" href="../trending.html">All trending questions</a>' in t2
    assert '<a class="share" href="../card/T002.html">Share card</a>' in t2
    assert '<a class="share" href="../card/Q11.html">Share card</a>' in pages['q/Q11.html']
    assert 'Draft question' not in t2
    with pytest.raises(KeyError):
        archive_pages.render_question(payload, 'T099')
    with pytest.raises(KeyError):
        archive_pages.render_card(payload, 'T099')


# ---- share cards -----------------------------------------------------------------------------------------------------------

def test_share_card_shows_the_latest_answers_and_one_change(ready):
    archive.demo_archive()
    payload = archive.public_payload()
    card = archive_pages.render_card(payload, 'Q11')
    parsed = assert_page_is_safe(card)
    # Model cards sit in the same safe box as the chat-app cards now.
    assert '<body class="card-page">' in card and '<main id="main" class="card card-safe" data-question-id="Q11" data-provenance="scripted-sample">' in card
    assert [attrs['src'].split('?')[0] for tag, attrs in parsed.tags if tag == 'img'] == ['../../static/archive-mark.svg']
    assert 'Answers Over Time</a>' in card and '<a class="card-brand" href="../q/Q11.html">' in card
    assert '<h1 class="card-q card-q-xl card-q-f1">What is today&#x27;s date?</h1>' in card
    assert card.count('<section class="card-panel') == 3 and '<div class="card-panels n-3">' in card
    # model-a changed last (Nov 2026): its panel is marked. A card of several panels has no room for the answer before;
    # the question page shows it.
    assert card.count('card-panel-changed') == 1 and 'card-before' not in card
    assert '<span class="card-label">model-a</span><span class="card-when">Nov 2026 · changed</span></div>' in card
    assert '<p class="card-answer">I have no clock, so I cannot know. My training suggests 2026 or later.</p>' in card
    assert '<span class="card-label">model-c</span><span class="card-when">Nov 2026</span>' in card
    assert "I don&#x27;t have access to the current date." in card                     # model-c's latest, not its first
    assert '<p class="card-honest mono">Sample we wrote, not a real AI answer</p>' in card and 'no payments' in card
    assert 'AI-generated' not in card
    assert card.index('class="card-honest') < card.index('</div>\n  <footer class="card-foot')   # the honesty line is inside the safe box
    plain = archive_pages.render_card(payload, 'Q02')
    assert 'card-panel-changed' not in plain and plain.count('<section class="card-panel"') == 3
    trend = archive_pages.render_card(payload, 'T002')
    assert '<span class="card-kicker">chip stocks</span>' in trend and 'href="../t/T002.html"' in trend
    assert '<span class="card-kicker">Right now</span>' in card
    # The size class follows the length of the question, so a long one cannot push the panels out.
    sizes = {len(q['text']): re.search(r'card-q card-q-(\w+)', archive_pages.render_card(payload, q['id'])).group(1)
             for q in payload['questions'] + payload['trending']['questions']}
    assert all(size == ('xl' if n <= 26 else 'l' if n <= 48 else 'm' if n <= 90 else 's') for n, size in sizes.items())
    # No script, no style attribute, no animation. The fitted 9:16 picture exists only at the capture size; outside it the
    # card is an ordinary page in rem sizes with nothing clamped, so it can be read and zoomed on a phone.
    css = (STATIC / 'archive.css').read_text(encoding='utf-8')
    flowing, fitted = css.split('/* ---- share card', 1)[1].split('@media (min-width: 540px) and (min-height: 960px) {', 1)
    assert 'line-clamp' not in flowing and 'cqw;' not in flowing and '.card-answer { font-size: 1rem;' in flowing
    assert 'container-type: size' in fitted and 'min(100vw, 56.25vh)' in fitted and 'min(100vh, 177.7778vw)' in fitted
    assert fitted.count('line-clamp') >= 14 and 'overflow: hidden' in fitted
    assert '.card-top { flex: none; flex-wrap: nowrap; height: 25cqw; padding: 0 13cqw 0 6.1cqw;' in fitted      # y 270, x 65-940
    assert '.card-body { flex: none; height: 108.33cqw; padding: 0 13cqw 0 6.1cqw;' in fitted                    # y 270-1440
    for banned in ('animation', 'transition', '@keyframes', 'px;', ' transform'):
        assert banned not in fitted, banned
    assert re.findall(r'\d(?:px|rem|em)\b', fitted.replace('0.08em', '').replace('0.035em', '')) == []
    for name in ('.card-q-xl', '.card-q-l', '.card-q-m', '.card-q-s', '.one .card-answer', '.n-2 .card-answer', '.card-before', '.card-four'):
        assert name in fitted, name


def card_payload(labels, answers):
    payload = archive.empty_payload()
    payload.update(answerers=labels, demo_only=False, locked=True, locked_at='2026-09-01T00:00:00+00:00',
                   runs=[{'number': n, 'date': f'2026-{8 + n:02d}-03T09:00:00+00:00', 'demo': False, 'asked': {'core': 20, 'trending': 0}}
                         for n in (1, 2, 3)])
    payload['answers'] = [{'id': n, 'run': run, 'question': 'Q01', 'answerer': label, 'model': 'm', 'source': source, 'state': state,
                           'text': text, 'changed': changed, 'reason': '', 'created_at': '2026-09-03T09:00:00+00:00'}
                          for n, (run, label, source, state, text, changed) in enumerate(answers, 1)]
    assert archive.payload_problem(payload) is None
    return payload


def test_share_card_picks_three_models_and_labels_recorded_answers():
    labels = ['m1', 'm2', 'm3', 'm4', 'm5']
    payload = card_payload(labels, [
        (1, 'm1', 'engine', 'answered', 'one', None), (1, 'm2', 'engine', 'answered', 'two', None), (1, 'm3', 'engine', 'answered', 'three', None),
        (1, 'm4', 'api', 'answered', 'four old', None), (1, 'm5', 'engine', 'answered', 'five old', None),
        (2, 'm4', 'api', 'answered', 'four new', True), (2, 'm5', 'engine', 'answered', 'five new', True),
        (3, 'm5', 'engine', 'answered', 'five newest', True), (3, 'm4', 'api', 'no_answer', None, None),
        (3, 'm2', 'engine', 'withheld', None, None)])
    card = archive_pages.render_card(payload, 'Q01')
    assert_page_is_safe(card)
    shown = re.findall(r'<span class="card-label">(\w+)</span>', card)
    assert shown == ['m1', 'm4', 'm5']                # the two that changed, then the first other; in their usual order
    assert card.count('card-panel-changed') == 2      # both whose latest answer differs from the one before
    assert '<span class="card-when">Nov 2026 · changed</span></div><p class="card-answer">five newest</p>' in card
    assert '<span class="card-when">Oct 2026 · changed</span></div><p class="card-answer">four new</p>' in card     # its latest ANSWER, with its month
    assert '<span class="card-when">Sep 2026</span>' in card
    # One answer was sent in by an outside program, so the card does not claim that we asked them all ourselves.
    assert '<p class="card-honest mono">AI-generated · may be wrong</p>' in card and 'scripted' not in card.lower() and 'sample' not in card.lower()
    assert 'data-method="sent-in-unverified"' in card and 'data-method="asked-by-server"' in card
    # One or two models get larger text; none at all is said plainly.
    assert '<div class="card-panels n-2">' in archive_pages.render_card(card_payload(['a', 'b'], [
        (1, 'a', 'engine', 'answered', 'x', None), (1, 'b', 'engine', 'answered', 'y', None)]), 'Q01')
    empty = archive_pages.render_card(archive.empty_payload(), 'Q01')
    assert '<div class="card-panels n-1">' in empty and 'No answers recorded yet.' in empty and '<p class="card-honest mono">No answers recorded yet</p>' in empty
    assert archive_pages.render_card(payload, 'Q02').count('No answers recorded yet.') == 1


def test_hostile_text_is_escaped_on_trending_pages_and_cards():
    payload = card_payload(['model-a'], [(1, 'model-a', 'engine', 'answered', 'Plain.', None), (2, 'model-a', 'engine', 'answered', HOSTILE, True),
                                         (3, 'model-a', 'engine', 'answered', HOSTILE + ' again', True)])
    payload['questions'][0].update(text=HOSTILE, rationale=HOSTILE, prompt=HOSTILE)
    payload['trending'].update(locked=True, version=1, locked_at='2026-09-01T00:00:00+00:00')
    payload['trending']['groups'] = [{'name': 'Trending', 'why': HOSTILE}]
    payload['trending']['questions'] = [{'id': 'T001', 'group': 'Trending', 'text': HOSTILE, 'rationale': HOSTILE, 'prompt': HOSTILE,
                                         'added': '2026-09-02', 'topic': HOSTILE, 'trend_source': HOSTILE}]
    for run in payload['runs']:
        run['asked']['trending'] = 1
    payload['answers'] += [{**payload['answers'][n], 'id': 10 + n, 'question': 'T001'} for n in range(3)]
    assert archive.payload_problem(payload) is None
    escaped = '&lt;script&gt;alert(1)&lt;/script&gt; &quot; onmouseover=&quot;x&quot; &#x27;&gt;&lt;img src=x onerror=y&gt; &amp;amp; &lt;/div&gt;'
    pages = {'index': archive_pages.render_index(payload), 'trending': archive_pages.render_trending(payload),
             't': archive_pages.render_question(payload, 'T001'), 'card-q': archive_pages.render_card(payload, 'Q01'),
             'card-t': archive_pages.render_card(payload, 'T001')}
    for name, page in pages.items():
        parsed = assert_page_is_safe(page)
        assert [tag for tag, _ in parsed.tags].count('img') == 1 and 'onmouseover="' not in page, name
        assert escaped in page and HOSTILE in ''.join(parsed.text), name
        for tag in ('div', 'span', 'section', 'main', 'header', 'footer', 'article'):
            assert page.count(f'<{tag}') == page.count(f'</{tag}>'), (name, tag)
    assert pages['card-t'].count('&lt;script&gt;') >= 5              # title, description, the prompt in the head, topic, question, answer
    assert pages['t'].count('&lt;script&gt;') >= 7                   # also rationale, prompt, trend source


# ---- web apps, snapshot, command line ----------------------------------------------------------------------------------------

def test_both_web_apps_serve_the_trending_pages_and_cards(ready):
    from live_genesis.app import app as combined
    from live_genesis.observatory import app as readonly
    archive.demo_archive()
    payload = archive.public_payload()
    expected = {'/archive/trending.html': archive_pages.render_trending(payload),
                '/archive/t/T002.html': archive_pages.render_question(payload, 'T002'),
                '/archive/card/T002.html': archive_pages.render_card(payload, 'T002'),
                '/archive/card/Q11.html': archive_pages.render_card(payload, 'Q11')}
    for application in (combined, readonly):
        with TestClient(application) as client:
            for path, page in expected.items():
                response = client.get(path)
                assert response.status_code == 200 and response.text == page, path
                assert 'text/html' in response.headers['content-type'] and "default-src 'none'" in response.headers['content-security-policy']
            for missing in ('/archive/t/T099.html', '/archive/t/T002', '/archive/t/Q11.html', '/archive/q/T002.html', '/archive/t/T02.html',
                            '/archive/card/T099.html', '/archive/card/Q99.html', '/archive/card/Q11', '/archive/card/..%2Fx.html',
                            '/archive/card/index.html', '/archive/trending', '/archive/t/'):
                assert client.get(missing).status_code == 404, missing
    with TestClient(readonly) as client:
        for path in expected:
            assert client.head(path).status_code == 200
            for method in ('post', 'put', 'delete', 'patch'):
                assert getattr(client, method)(path).status_code == 405, (method, path)


def test_snapshot_holds_trending_pages_and_cards_at_the_same_addresses(ready, tmp_path):
    from live_genesis import cli
    core.seed_agents()
    cli.demo()
    archive.demo_archive()
    out = tmp_path / 'site'
    snapshot.build(out)
    names = sorted(str(p.relative_to(out / 'archive')) for p in (out / 'archive').rglob('*.html'))
    chat = [f'C00{n}' for n in range(1, 7)]
    assert names == sorted(['index.html', 'models.html', 'trending.html'] + [f'q/Q{n:02d}.html' for n in range(1, 21)]
                           + [f't/T00{n}.html' for n in (1, 2, 3)] + [f'c/{q}.html' for q in chat]
                           + [f'card/Q{n:02d}.html' for n in range(1, 21)] + [f'card/T00{n}.html' for n in (1, 2, 3)]
                           + ['chatbots.html'] + [f'card/{q}{s}.html' for q in chat
                                                  for s in ('', '-chat-app-a', '-chat-app-b', '-chat-app-c', '-human')])
    payload = json.loads((out / 'archive.json').read_text(encoding='utf-8'))
    # /5: the scripted archive also holds the scripted chat-app track
    assert payload['schema'] == 'live-genesis-archive/5' and len(payload['trending']['questions']) == 3
    assert (out / 'archive' / 'card' / 'T001.html').read_text(encoding='utf-8') == archive_pages.render_card(payload, 'T001')
    assert (out / 'archive' / 'trending.html').read_text(encoding='utf-8') == archive_pages.render_trending(payload)
    for html in (out / 'archive').rglob('*.html'):                               # every relative address resolves inside the snapshot
        parsed = assert_page_is_safe(html.read_text(encoding='utf-8'))
        for url in parsed.urls:
            if url == '#main':                                                   # the skip link: a place on the same page
                continue
            target = (html.parent / url.split('#')[0].split('?')[0]).resolve()
            assert out.resolve() in target.parents or target == out.resolve(), (html.name, url)
            assert target.is_file() or (target / 'index.html').is_file(), (html.name, url)


def test_cli_understands_both_sets(ready, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(engine, 'infer', fake(AssertionError('no free command calls a model')))
    trending_file(tmp_path, monkeypatch, 2)
    code, out = run_cli(monkeypatch, capsys, 'archive-plan')
    assert code == 0 and 'Calls per run: 44' in out and 'archive-lock --set trending' in out and 'NOT locked in this database' in out
    code, out = run_cli(monkeypatch, capsys, 'archive-plan', '--set', 'trending', '--models', 'model-a')
    assert code == 0 and 'Calls per run: 2 ' in out and 'Q01' not in out
    code, out = run_cli(monkeypatch, capsys, 'archive-plan', '--set', 'everything')
    assert code == 2
    code, out = run_cli(monkeypatch, capsys, 'archive-dry-run', '--set', 'trending')
    assert code == 0 and '"answered": 4' in out and out.count(archive.MOCK_BANNER) == 2
    code, out = run_cli(monkeypatch, capsys, 'archive-lock', '--set', 'trending')
    assert code == 0 and 'Locked trending question set trending, version 1: 2 added, 2 in all, sha256 ' in out
    code, out = run_cli(monkeypatch, capsys, 'archive-lock', '--set', 'trending')
    assert code == 1 and 'Already locked' in out
    code, out = run_cli(monkeypatch, capsys, 'archive-plan')
    assert code == 0 and 'Calls per run: 4 ' in out and 'Not asked: question set basic-1 (20 questions' in out
    code, out = run_cli(monkeypatch, capsys, 'archive-lock')
    assert code == 0 and 'Locked question set basic-1: 20 questions' in out
    trending_file(tmp_path, monkeypatch, 3)
    code, out = run_cli(monkeypatch, capsys, 'archive-status')
    status = json.loads(out)
    assert code == 0 and status['file_matches_lock'] is True and status['locked']['track'] == 'core'
    assert status['trending']['file_matches_lock'] is True and status['trending']['question_file']['not_locked_yet'] == 1
    assert status['trending']['locked']['version'] == 1 and [v['questions'] for v in status['trending']['versions']] == [2]
    as_real(monkeypatch, 'Seven.', calls := [])
    code, out = run_cli(monkeypatch, capsys, 'archive-run', '--max-usd', '0.05', '--set', 'trending', '--models', 'model-a')
    assert code == 0 and len(calls) == 2 and '"trending": 2' in out and '"status": "complete"' in out
    code, out = run_cli(monkeypatch, capsys, 'archive-run', '--max-usd', '0.05')
    assert code == 0 and len(calls) == 2 + 44
    assert controls is not None and MODELS and question_file and Path
