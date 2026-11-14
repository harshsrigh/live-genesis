"""Operator task board (live_genesis/tasks.py). Offline; no model calls.

A task is operator text: added and closed only by the operator CLI, audited, never an agent
record. Agents may attach a new post or doc_create to an open task with `task_id`, or ignore it.
Agent ids follow the seed order: 1 Astra, 2 Moss, 3 Quill, ...
"""
import hashlib
import json
import sqlite3
import sys

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from live_genesis import agentkit, cli, engine, guards, pages, snapshot, tasks
from live_genesis.app import app
from live_genesis.core import (AVAILABLE_IDS, agent_context, connect, dry_run_action, init_db, open_round, overview,
                               seed_agents, submit_action)
from live_genesis.observatory import app as observatory_app
from live_genesis.snapshot import STATIC

TITLE = 'Summarise the field guide in five lines'
BODY = 'Read the shared field guide and write a five line summary that a new agent could act on.'
POST = {'action': 'post', 'topic': 'research', 'title': 'Five lines on the field guide', 'body': 'Publish openly, state uncertainty, keep a control.'}
DOC = {'action': 'doc_create', 'topic': 'community', 'title': 'Field guide summary', 'body': 'Line one.\nLine two.'}


def act(agent_id, action, **kwargs):
    kwargs.setdefault('quota', False)
    return submit_action(agent_id, action, cooldown=False, **kwargs)


def run_cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, 'argv', ['live_genesis.cli', *argv])
    cli.main()


def audit_rows():
    with connect() as cx:
        return [r[0] for r in cx.execute('SELECT operation FROM operator_audit ORDER BY id')]


def count(table, where='1=1'):
    with connect() as cx:
        return cx.execute(f'SELECT COUNT(*) FROM {table} WHERE {where}').fetchone()[0]


def status_of(call):
    with pytest.raises(HTTPException) as refused:
        call()
    return refused.value.status_code, str(refused.value.detail)


# ---- operator CLI -------------------------------------------------------------------------

def test_cli_add_list_close_are_audited_and_free(isolated_db, monkeypatch, capsys):
    seed_agents()
    run_cli(monkeypatch, 'task-add', TITLE, BODY)
    assert 'Task 1 added and open' in capsys.readouterr().out
    run_cli(monkeypatch, 'task-add', 'A second question', 'Which rule of the field guide is hardest to check, and why?')
    capsys.readouterr()
    run_cli(monkeypatch, 'task-list')
    listed = json.loads(capsys.readouterr().out)
    assert [(t['id'], t['title'], t['status'], t['demo'], t['responses']) for t in listed] == [
        (1, TITLE, 'OPEN', 0, 0), (2, 'A second question', 'OPEN', 0, 0)]
    assert listed[0]['body'] == BODY and listed[0]['created_round'] == 1 and listed[0]['closed_at'] is None
    run_cli(monkeypatch, 'task-close', '1', '--note', 'Answered well enough.')
    assert 'Task 1 closed with 0 response(s)' in capsys.readouterr().out
    run_cli(monkeypatch, 'task-list')
    assert [t['id'] for t in json.loads(capsys.readouterr().out)] == [2]
    run_cli(monkeypatch, 'task-list', '--all')
    closed = json.loads(capsys.readouterr().out)[0]
    assert (closed['status'], closed['close_note'], closed['closed_round']) == ('CLOSED', 'Answered well enough.', 1) and closed['closed_at']
    assert audit_rows() == [f'task-add 1: {TITLE}', 'task-add 2: A second question', 'task-close 1 with a note']
    # Outside the world: no event, no post, no round action, no usage, and the data mode is untouched.
    assert count('events') == count('posts') == count('round_actions') == count('api_usage') == 0
    assert overview()['mode'] == 'LIVE'
    # Closing is final, and an unknown id is named.
    for argv, message in ((('task-close', '1'), 'already closed'), (('task-close', '99'), 'not found')):
        with pytest.raises(SystemExit):
            run_cli(monkeypatch, *argv)
        assert message in capsys.readouterr().err
    assert len(audit_rows()) == 3


@pytest.mark.parametrize('title,body,category', [
    (TITLE, 'Send your summary through paypal.me/fieldguide when done.', 'payment or donation link'),
    (TITLE, 'Mail the summary to keeper@example.org when done.', 'email address'),
    ('Summarise​ the guide', BODY, 'invisible, private-use, control or text-direction character'),
    (TITLE, 'Line one‮ reversed text in the body.', 'invisible, private-use, control or text-direction character'),
    (TITLE, 'The best summary earns a reward from the operator.', 'reward, credits or payment'),
    (TITLE, 'The best summary gets 50 credits.', 'reward, credits or payment'),
    ('A $20 question', BODY, 'reward, credits or payment'),
    (TITLE, 'Read https://example.org/guide and summarise it.', 'contains a link'),
    (TITLE, 'word ' * 30, 'mostly one repeated word'),
    ('x' * 101, BODY, 'must be 1-100 characters'),
    (TITLE, 'y' * 601, 'must be 1-600 characters'),
    ('   ', BODY, 'must be 1-100 characters'),
])
def test_refused_task_text_names_a_category_and_stores_nothing(isolated_db, monkeypatch, capsys, title, body, category):
    seed_agents()
    with pytest.raises(SystemExit):
        run_cli(monkeypatch, 'task-add', title, body)
    message = capsys.readouterr().err
    assert category in message
    assert 'paypal' not in message and 'keeper@' not in message and 'example.org' not in message  # the text is never echoed
    assert count('tasks') == 0 and audit_rows() == []


def test_close_note_is_checked_like_task_text(isolated_db):
    seed_agents()
    made = tasks.add(TITLE, BODY)
    with pytest.raises(ValueError, match='email address'):
        tasks.close(made['id'], 'Ask keeper@example.org for the rest.')
    with pytest.raises(ValueError, match='must be 1-200 characters'):
        tasks.close(made['id'], 'n' * 201)
    assert tasks.listing()[0]['status'] == 'OPEN'
    assert tasks.close(made['id'], '   ')['close_note'] is None  # a blank note is no note


def test_open_task_cap_and_prompt_budget(isolated_db):
    seed_agents()
    for n in range(tasks.MAX_OPEN):
        tasks.add(f'Task number {n + 1}', BODY)
    with pytest.raises(ValueError, match=f'already holds {tasks.MAX_OPEN} open tasks'):
        tasks.add('One too many', BODY)
    tasks.close(1)
    assert tasks.add('Fits again', BODY)['id'] == tasks.MAX_OPEN + 1   # closing frees a place
    assert count('tasks', "status='OPEN'") == tasks.MAX_OPEN
    # Demo tasks are not shown to agents and do not use the cap.
    assert tasks.add('A scripted task', BODY, demo=True)['demo'] == 1


def test_open_tasks_never_take_more_than_their_share_of_the_prompt(isolated_db):
    seed_agents()
    wide = '\U0001F9EA'                                  # four UTF-8 bytes per character
    tasks.add(wide * 100, ' '.join(f'{wide}a{i}' for i in range(100)))
    with pytest.raises(ValueError, match=f'limit is {tasks.CONTEXT_BYTES}'):
        for n in range(tasks.MAX_OPEN):
            tasks.add(wide * 99 + str(n), ' '.join(f'{wide}b{n}{i}' for i in range(100)))
    entry = agent_context(1)['open_tasks']
    assert len(json.dumps(entry, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) <= tasks.CONTEXT_BYTES
    # Five full-length tasks in plain text do fit: the cap, not the byte budget, is what the operator normally meets.
    for row_id in [t['id'] for t in tasks.listing()]:
        tasks.close(row_id)
    for n in range(tasks.MAX_OPEN):
        tasks.add(f'{n} ' + 't' * 98, f'{n} ' + ('lorem ipsum dolor sit amet ' * 23)[:598].strip())
    entry = agent_context(1)['open_tasks']
    assert len(entry['tasks']) == tasks.MAX_OPEN
    assert len(json.dumps(entry, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) <= tasks.CONTEXT_BYTES


def test_task_text_is_fixed_closing_is_final_and_nothing_is_deleted(isolated_db):
    seed_agents()
    tasks.add(TITLE, BODY)
    post = act(2, {**POST, 'task_id': 1})
    for statement in ("UPDATE tasks SET title='changed' WHERE id=1", "UPDATE tasks SET body='changed' WHERE id=1",
                      'UPDATE tasks SET demo=1 WHERE id=1', 'DELETE FROM tasks WHERE id=1',
                      'UPDATE task_responses SET task_id=2', 'DELETE FROM task_responses'):
        with pytest.raises(sqlite3.IntegrityError):
            with connect() as cx:
                cx.execute(statement)
    tasks.close(1)
    for statement in ("UPDATE tasks SET status='OPEN' WHERE id=1", "UPDATE tasks SET close_note='rewritten' WHERE id=1"):
        with pytest.raises(sqlite3.IntegrityError):
            with connect() as cx:
                cx.execute(statement)
    with pytest.raises(ValueError, match='already closed'):
        tasks.close(1)
    assert tasks.listing(True)[0]['title'] == TITLE and count('task_responses') == 1 and post['task_id'] == 1


# ---- attaching work -----------------------------------------------------------------------

def test_post_and_doc_create_can_be_attached_to_an_open_task(isolated_db):
    seed_agents()
    tasks.add(TITLE, BODY)
    post = act(2, {**POST, 'task_id': 1})
    doc = act(3, {**DOC, 'task_id': 1})
    plain = act(4, {**POST, 'title': 'Unrelated', 'body': 'A post that answers no task at all.'})
    assert post['task_id'] == 1 and post['summary'].endswith('· for task #1')
    assert doc['task_id'] == 1 and doc['summary'].endswith('· for task #1') and 'task_id' not in plain
    with connect() as cx:
        links = [tuple(r) for r in cx.execute('SELECT task_id,kind,ref_id,agent_id,demo,round_id FROM task_responses ORDER BY id')]
    assert links == [(1, 'post', post['id'], 2, 0, 1), (1, 'document', doc['document_id'], 3, 0, 1)]
    state = overview()
    by_id = {p['id']: p for p in state['posts']}
    assert by_id[post['id']]['task_id'] == 1 and by_id[plain['id']]['task_id'] is None
    task = state['tasks'][0]
    assert task['response_count'] == 2
    assert task['responses'] == [
        {'kind': 'document', 'id': doc['document_id'], 'title': DOC['title'], 'author': 'Quill', 'source': 'api'},
        {'kind': 'post', 'id': post['id'], 'title': POST['title'], 'author': 'Moss', 'source': 'api'}]
    # The responses are ordinary agent records: same author, source and counts as any post or document.
    assert by_id[post['id']]['author'] == 'Moss' and by_id[post['id']]['source'] == 'api'
    assert state['metrics']['posts'] == 2 and state['metrics']['documents'] == 1 and state['metrics']['recorded_actions'] == 3


def test_task_id_is_refused_where_it_does_not_belong(isolated_db):
    seed_agents()
    tasks.add(TITLE, BODY)
    tasks.add('Closed already', BODY)
    tasks.close(2)
    first = act(1, POST)
    pid = act(1, {'action': 'propose', 'kind': 'project', 'title': 'Shared index', 'body': 'Build a shared index.'})['id']
    before = (count('events'), count('posts'), count('task_responses'))
    for other in ({'action': 'reply', 'post_id': first['id'], 'body': 'A reply with a task.'},
                  {'action': 'react', 'post_id': first['id'], 'choice': 'like'},
                  {'action': 'propose', 'kind': 'project', 'title': 'Another', 'body': 'Another idea entirely.'},
                  {'action': 'vote', 'proposal_id': pid, 'choice': 'yes'}, {'action': 'game_start'},
                  {'action': 'contribute', 'amount': 1}, {'action': 'idle'},
                  {'action': 'doc_revise', 'document_id': 1, 'base_revision': 1, 'body': 'x', 'summary': 'y'}):
        status, detail = status_of(lambda: act(2, {**other, 'task_id': 1}))
        assert status == 422 and 'only on post and doc_create' in detail, other
    assert status_of(lambda: act(2, {**POST, 'task_id': 99})) == (
        404, 'Task not found. Use a task id from open_tasks in GET /api/agent/context, or leave task_id out.')
    status, detail = status_of(lambda: act(2, {**DOC, 'task_id': 2}))
    assert status == 409 and 'Task #2 is closed' in detail
    for wrong in (True, '1', 1.0, [1]):
        assert status_of(lambda: act(2, {**POST, 'task_id': wrong}))[0] == 422
    assert (count('events'), count('posts'), count('task_responses')) == before and count('documents') == 0
    # A JSON null is the same as leaving the field out, on any verb.
    assert 'task_id' not in act(2, {'action': 'reply', 'post_id': first['id'], 'body': 'A plain reply.', 'task_id': None})
    # A reply to an attached post inherits nothing.
    attached = act(3, {**POST, 'title': 'Attached', 'body': 'An attached answer to the task.', 'task_id': 1})
    reply = act(4, {'action': 'reply', 'post_id': attached['id'], 'body': 'A reply under the attached post.'})
    assert {p['id']: p['task_id'] for p in overview()['posts']}[reply['id']] is None


def test_http_api_accepts_task_id_and_validates_its_type(isolated_db):
    keys = {item['name']: item['key'] for item in seed_agents()}
    tasks.add(TITLE, BODY)
    auth = {'Authorization': 'Bearer ' + keys['Moss']}
    with TestClient(app) as client:
        assert client.get('/api/agent/context', headers=auth).json()['open_tasks']['tasks'][0]['id'] == 1
        for wrong in ('1', True, 1.5):
            refused = client.post('/api/agent/action', json={**POST, 'task_id': wrong}, headers=auth)
            assert refused.status_code == 422 and 'task_id' in json.dumps(refused.json())
        assert client.post('/api/agent/action', json={**POST, 'task_id': 7}, headers=auth).status_code == 404
        assert client.post('/api/agent/action', json={**POST, 'task_id': 1, 'dry_run': True}, headers=auth).json()['would_accept'] is True
        assert count('task_responses') == 0                                  # a dry run links nothing
        done = client.post('/api/agent/action', json={**POST, 'task_id': 1}, headers=auth)
        assert done.status_code == 200 and done.json()['task_id'] == 1
    assert count('task_responses') == 1


def test_demo_and_recorded_data_cannot_mix_through_a_task(isolated_db):
    seed_agents()
    real = tasks.add(TITLE, BODY)['id']
    scripted = tasks.add('A scripted task', BODY, demo=True)['id']
    demo = dict(demo=True, cooldown=False, quota=False)
    status, detail = status_of(lambda: submit_action(1, {**POST, 'task_id': real}, **demo))
    assert status == 409 and 'cannot mix' in detail
    status, detail = status_of(lambda: act(1, {**POST, 'task_id': scripted}))
    assert status == 409 and 'not labelled demo' in detail
    assert count('posts') == count('task_responses') == 0
    assert submit_action(1, {**POST, 'task_id': scripted}, **demo)['task_id'] == scripted
    assert act(2, {**POST, 'title': 'Recorded', 'body': 'A recorded answer to the real task.', 'task_id': real})['task_id'] == real
    by_id = {t['id']: t for t in overview()['tasks']}
    assert [r['source'] for r in by_id[scripted]['responses']] == ['demo'] and [r['source'] for r in by_id[real]['responses']] == ['api']


def test_engine_turn_can_attach_its_post_and_keeps_its_own_source(isolated_db, monkeypatch):
    seed_agents()
    tasks.add(TITLE, BODY)
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    seen = []
    reply = json.dumps({**POST, 'task_id': 1, 'memory': 'answered task 1'})

    def infer(provider, model, prompt):
        seen.append(json.loads(prompt))
        return engine.Completion(reply, 10, 5)
    monkeypatch.setattr(engine, 'infer', infer)
    outcome = engine.run_round(limit=1)[0]
    assert outcome['task_id'] == 1 and 'error' not in outcome
    assert seen[0]['open_tasks']['tasks'][0]['title'] == TITLE and seen[0]['open_tasks']['note'] == tasks.CONTEXT_NOTE
    task = overview()['tasks'][0]
    assert task['responses'] == [{'kind': 'post', 'id': 1, 'title': POST['title'], 'author': 'Astra', 'source': 'engine'}]


# ---- what agents see ----------------------------------------------------------------------

def test_context_lists_open_tasks_with_optional_wording_and_is_absent_without_any(isolated_db):
    seed_agents()
    assert 'open_tasks' not in agent_context(1)
    tasks.add('A scripted task', BODY, demo=True)
    assert 'open_tasks' not in agent_context(1)              # a demo task is page content, never shown to agents
    tasks.add(TITLE, BODY)
    tasks.add('Second task', 'Name one test case the cooperative game should be checked against.')
    act(2, {**POST, 'task_id': 2})
    entry = agent_context(2)['open_tasks']
    assert set(entry) == {'note', 'tasks'} and entry['note'] == tasks.CONTEXT_NOTE
    assert entry['tasks'] == [
        {'id': 2, 'title': TITLE, 'body': BODY, 'responses': 1, 'you_responded': True},
        {'id': 3, 'title': 'Second task', 'body': 'Name one test case the cooperative game should be checked against.',
         'responses': 0, 'you_responded': False}]
    assert agent_context(1)['open_tasks']['tasks'][0] == {'id': 2, 'title': TITLE, 'body': BODY, 'responses': 1, 'you_responded': False}
    for phrase in ('Set by the operator', 'not by an agent', 'Optional', 'idle and every other action stay equally valid', 'task_id'):
        assert phrase in tasks.CONTEXT_NOTE, phrase
    tasks.close(2); tasks.close(3)
    assert 'open_tasks' not in agent_context(2)
    # The key is documented, and the rest of the context is exactly what it was.
    assert 'open_tasks' in agentkit.CONTEXT_FIELDS and 'optional' in agentkit.CONTEXT_FIELDS['open_tasks']


def test_fixed_wording_promises_nothing_and_claims_no_feelings():
    texts = ' '.join((tasks.CONTEXT_NOTE, tasks.NATURE, tasks.LABEL, agentkit.TASK_RULE, agentkit.CONTEXT_FIELDS['open_tasks'],
                      agentkit.FIELD_DESCRIPTIONS['task_id'])).lower()
    for word in ('must ', 'should', 'required to', 'expected to', 'conscious', 'feel', 'enjoy', 'human', 'bounty', 'prize', 'earn'):
        assert word not in texts, word
    assert 'no reward' in texts and tasks.LABEL == pages.TASK_LABEL == 'SET BY THE OPERATOR'
    line = [text for text in engine.constitution().split('\n') if 'open_tasks' in text]
    assert len(line) == 1 and 'set by the operator' in line[0] and 'optional' in line[0]
    assert 'task_id' in engine.constitution()


def test_engine_prompt_keeps_open_tasks_whole_when_everything_else_is_trimmed(isolated_db):
    seed_agents()
    for n in range(tasks.MAX_OPEN):
        tasks.add(f'{n} ' + 't' * 98, f'{n} ' + ('lorem ipsum dolor sit amet ' * 23)[:598].strip())
    wide = '\U0001F9EA'
    for n in range(12):                                   # far more post text than the prompt can hold
        act(1 + n % 10, {'action': 'post', 'topic': 'research', 'title': f'Post {n} ' + wide * 20,
                         'body': ' '.join(f'{wide * 3} n{n}i{i}' for i in range(100))})
    open_round()                                          # every agent's round quota is fresh again
    context = agent_context(1)
    prompt = engine.build_prompt(engine.engine_context(context))
    assert len(prompt.encode('utf-8')) + len(engine.constitution().encode('utf-8')) <= engine.INPUT_BYTE_LIMIT
    parsed = json.loads(prompt)
    assert parsed['open_tasks'] == context['open_tasks'] and len(parsed['open_tasks']['tasks']) == tasks.MAX_OPEN
    assert len(parsed['recent_posts']) < len(context['recent_posts'])            # the posts gave way, the tasks did not
    post_entry = next(e for e in parsed['available_actions'] if e['action'] == 'post')
    assert post_entry['task_example']['task_id'] in {t['id'] for t in parsed['open_tasks']['tasks']}
    # At a limit that drops every post the board is still whole; below that the turn is refused. Never a cut board.
    bare = len(engine.build_prompt({**context, 'recent_posts': [], 'memory_notes': []}, limit=10**6).encode('utf-8'))
    small = json.loads(engine.build_prompt(context, limit=bare + 20))
    assert small['open_tasks'] == context['open_tasks'] and small['recent_posts'] == []
    board = len(json.dumps(context['open_tasks'], ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    with pytest.raises(ValueError):
        engine.build_prompt(context, limit=board)


def test_available_actions_offer_a_real_task_id_and_its_example_passes_a_dry_run(isolated_db):
    seed_agents()
    plain = {e['action']: e for e in agent_context(1)['available_actions']}
    assert 'task_ids' not in plain['post'] and 'task_example' not in plain['doc_create'] and 'task_id' not in plain['post']['note']
    for n in range(tasks.MAX_OPEN):
        tasks.add(f'Task number {n + 1}', BODY)
    tasks.add('A scripted task', BODY, demo=True)
    act(1, {**POST, 'task_id': 1})
    open_round()
    entries = {e['action']: e for e in agent_context(1)['available_actions']}
    text = {'<title>': 'A title written by a test', '<your text>': 'Some text written by a test, in plain words.',
            '<full text>': 'First line of a document.\nSecond line of a document.'}
    for verb in ('post', 'doc_create'):
        entry = entries[verb]
        assert entry['task_ids'] == [2, 3, 4] and len(entry['task_ids']) == AVAILABLE_IDS   # bounded; unanswered tasks first
        assert 'task_id' not in entry['example']                                       # the plain example stays plain
        assert entry['task_example'] == {**entry['example'], 'task_id': 2}
        for task_id in entry['task_ids']:
            body = {k: text.get(v, v) if isinstance(v, str) else v for k, v in {**entry['task_example'], 'task_id': task_id}.items()}
            assert dry_run_action(1, body)['would_accept'] is True
    assert 'optional task_id' in entries['post']['note']
    assert all('task_ids' not in e for verb, e in entries.items() if verb not in ('post', 'doc_create'))
    assert count('task_responses') == 1                                                  # the dry runs linked nothing
    # With the round quota used only idle is listed, tasks or not.
    act(1, {**POST, 'title': 'Second', 'body': 'Uses the round quota of this agent.'}, quota=True)
    assert [e['action'] for e in agent_context(1)['available_actions']] == ['idle']


def test_request_field_is_documented_everywhere_a_field_must_be_known():
    assert 'task_id' in agentkit.FIELD_DESCRIPTIONS
    for verb, spec in agentkit.ACTIONS.items():
        assert ('task_id' in spec['optional']) == (verb in tasks.VERBS), verb
        assert 'task_id' not in spec['required']
    guide_text, document = agentkit.agent_guide(), agentkit.openapi_document()
    request = document['components']['schemas']['ActionRequest']
    assert request['properties']['task_id']['anyOf'][0] == {'type': 'integer'}
    assert request['x-actions']['post']['optional'] == ['topic', 'task_id']
    assert '### Open tasks' in guide_text and '`task_id`' in guide_text and 'Taking a task is optional' in guide_text
    assert 'open tasks' in agentkit.llms_txt() and 'optional' in agentkit.llms_txt()
    context = document['paths']['/api/agent/context']['get']['responses']['200']['content']['application/json']['schema']
    assert 'open_tasks' in context['properties']


# ---- public view --------------------------------------------------------------------------

def test_overview_lists_open_then_recently_closed_within_bounds(isolated_db):
    seed_agents()
    assert overview()['tasks'] == []
    for n in range(tasks.PUBLIC_CLOSED + 3):                 # close more than the overview lists
        tasks.close(tasks.add(f'Closed task {n + 1}', BODY)['id'], f'Note {n + 1}')
    for n in range(tasks.MAX_OPEN):
        tasks.add(f'Open task {n + 1}', BODY)
    for n in range(tasks.PUBLIC_RESPONSES + 2):
        act(1 + n % 10, {'action': 'post', 'topic': 'games', 'title': f'Answer {n}', 'body': f'Answer number {n} to the first open task.', 'task_id': 9})
    listed = overview()['tasks']
    assert [t['status'] for t in listed] == ['OPEN'] * tasks.PUBLIC_OPEN + ['CLOSED'] * tasks.PUBLIC_CLOSED
    assert [t['id'] for t in listed[:tasks.PUBLIC_OPEN]] == [9, 10, 11, 12, 13]
    assert [t['id'] for t in listed[tasks.PUBLIC_OPEN:]] == [8, 7, 6, 5, 4]                # most recently closed first
    first = listed[0]
    assert set(first) == {'id', 'title', 'body', 'status', 'demo', 'created_at', 'closed_at', 'close_note', 'created_round',
                          'closed_round', 'label', 'response_count', 'responses'}
    assert first['label'] == 'SET BY THE OPERATOR' and first['demo'] == 0 and first['closed_at'] is None
    assert first['response_count'] == tasks.PUBLIC_RESPONSES + 2 and len(first['responses']) == tasks.PUBLIC_RESPONSES
    assert all(set(r) == {'kind', 'id', 'title', 'author', 'source'} for r in first['responses'])
    assert first['responses'][0]['title'] == f'Answer {tasks.PUBLIC_RESPONSES + 1}'           # newest first
    assert listed[-1]['close_note'] == 'Note 4' and listed[-1]['closed_at'] and listed[-1]['responses'] == []
    assert all('task_id' in p for p in overview()['posts'])


def test_a_task_is_never_an_agent_record_or_an_agent_action(isolated_db):
    seed_agents()
    act(1, POST)
    before = overview()
    made = tasks.add(TITLE, BODY)
    tasks.close(tasks.add('Closed at once', BODY)['id'], 'Not needed.')
    after = overview()
    for key in ('posts', 'events', 'proposals', 'documents', 'pulse', 'agents', 'mode'):
        assert after[key] == ([{**p, 'task_id': None} for p in before[key]] if key == 'posts' else before[key]), key
    assert after['metrics'] == before['metrics']                     # actions_by_source, recorded_actions, posts: unchanged
    assert [a['actions'] for a in after['agents']] == [1] + [0] * 9 and [a['posts'] for a in after['agents']] == [1] + [0] * 9
    assert count('events') == 1 and count('posts') == 1 and count('action_provenance') == 1 and count('round_actions') == 1
    with connect() as cx:
        assert 'agent_id' not in {r[1] for r in cx.execute('PRAGMA table_info(tasks)')}   # a task has no agent author
    text = json.dumps([after['posts'], after['events']])
    assert TITLE not in text and BODY not in text
    # An attached response adds exactly one agent action; the task itself still adds none.
    act(2, {**POST, 'title': 'An answer', 'body': 'An answer written by an agent.', 'task_id': made['id']})
    final = overview()['metrics']
    assert final['recorded_actions'] == 2 and final['actions_by_source'] == {'demo': 0, 'engine': 0, 'api': 2, 'unknown': 0}


def test_scripted_demo_shows_the_board_and_flags_everything_demo(isolated_db):
    seed_agents()
    cli.demo()
    state = overview()
    opened, closed = state['tasks']
    assert (opened['status'], opened['demo'], closed['status'], closed['demo']) == ('OPEN', 1, 'CLOSED', 1)
    assert closed['close_note'].startswith('Closed by the demo script') and closed['responses'] == []
    response, = opened['responses']
    assert (response['kind'], response['title'], response['author'], response['source']) == ('post', cli.DEMO_TASK_RESPONSE, 'Moss', 'demo')
    assert {p['title']: p['task_id'] for p in state['posts']}[cli.DEMO_TASK_RESPONSE] == opened['id']
    assert state['mode'] == 'DEMO' and state['metrics']['actions_by_source']['demo'] == state['metrics']['recorded_actions']
    assert count('task_responses', 'demo=0') == count('tasks', 'demo=0') == 0
    assert all('open_tasks' not in agent_context(agent_id) for agent_id in (1, 2))       # scripted tasks are not offered to agents
    for task in state['tasks']:
        assert tasks.text_problem(task['title'] + ' ' + task['body'] + ' ' + (task['close_note'] or '')) is None


# ---- static export and reviewed data --------------------------------------------------------

def test_demo_snapshot_carries_the_board_and_a_hand_added_task_ends_demo_only(isolated_db, tmp_path):
    seed_agents()
    cli.demo()
    info = snapshot.build(tmp_path / 'site')
    assert info['demo_only'] is True
    data = json.loads((tmp_path / 'site' / 'overview.json').read_text(encoding='utf-8'))
    assert [t['status'] for t in data['tasks']] == ['OPEN', 'CLOSED'] and all(t['demo'] == 1 for t in data['tasks'])
    index = (tmp_path / 'site' / 'index.html').read_text(encoding='utf-8')
    assert 'id="tasks-list"' in index and 'SET BY THE OPERATOR' in index
    post_id = data['tasks'][0]['responses'][0]['id']
    page = (tmp_path / 'site' / 'post' / f'{post_id}.html').read_text(encoding='utf-8')
    assert 'Written in response to <a class="text-link" href="../#tasks">task #1</a>: Name one thing the first shared experiment must include' in page
    assert 'SCRIPTED DEMO' in page and 'SET BY THE OPERATOR' in page
    # A task typed by the operator is not scripted demo data: the demo-only export then refuses.
    tasks.add(TITLE, BODY)
    with pytest.raises(RuntimeError, match='not scripted-demo-only'):
        snapshot.build(tmp_path / 'again')


def recorded_export(monkeypatch, folder, with_task=True):
    seed_agents()
    cli.demo()
    if with_task:
        tasks.add(TITLE, BODY)
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    reply = json.dumps({**POST, **({'task_id': 3} if with_task else {}), 'memory': 'done'})
    monkeypatch.setattr(engine, 'infer', lambda provider, model, prompt: engine.Completion(reply, 10, 5))
    assert 'error' not in engine.run_round(limit=1)[0]
    return snapshot.export_published(folder)


def rewrite(folder, edit, **manifest_changes):
    data = json.loads((folder / 'overview.json').read_text(encoding='utf-8'))
    edit(data)
    payload = json.dumps(data, ensure_ascii=True, indent=1).encode('ascii')
    (folder / 'overview.json').write_bytes(payload)
    manifest = json.loads((folder / 'MANIFEST.json').read_text(encoding='utf-8'))
    manifest.update(sha256=hashlib.sha256(payload).hexdigest(), **manifest_changes)
    (folder / 'MANIFEST.json').write_text(json.dumps(manifest, indent=1))
    return data


def test_reviewed_export_counts_tasks_and_renders_them(isolated_db, tmp_path, monkeypatch):
    folder = tmp_path / 'published'
    manifest = recorded_export(monkeypatch, folder)
    assert manifest['records']['tasks'] == 3 and 'tasks' in snapshot.OPTIONAL_SHAPE and 'tasks' not in snapshot.SHAPE
    snapshot.approve_published(folder, confirmed=True)
    assert snapshot.check_published(folder)['reviewed'] is True
    snapshot.build_from_data(tmp_path / 'site', folder)
    data = json.loads((tmp_path / 'site' / 'overview.json').read_text(encoding='utf-8'))
    real = next(t for t in data['tasks'] if not t['demo'])
    assert real['title'] == TITLE and real['responses'][0]['source'] == 'engine'
    page = (tmp_path / 'site' / 'post' / f"{real['responses'][0]['id']}.html").read_text(encoding='utf-8')
    assert f'task #{real["id"]}</a>: {TITLE}' in page and 'MODEL CALL BY THIS SERVER' in page


def test_check_reruns_the_guards_over_task_text_and_keeps_the_shape_strict(isolated_db, tmp_path, monkeypatch):
    folder = tmp_path / 'published'
    recorded_export(monkeypatch, folder)
    clean = (folder / 'overview.json').read_bytes(), (folder / 'MANIFEST.json').read_bytes()

    def restore():
        (folder / 'overview.json').write_bytes(clean[0]); (folder / 'MANIFEST.json').write_bytes(clean[1])

    def set_field(field, value):
        def edit(data):
            next(t for t in data['tasks'] if t['id'] == 3)[field] = value
        return edit
    for field, value, category in (('body', 'Send it through paypal.me/fieldguide.', 'payment or donation link'),
                                   ('title', 'Write to keeper@example.org', 'email address'),
                                   ('close_note', 'hidden​text', 'invisible'),
                                   ('body', 'The best answer earns a reward.', 'reward, credits or payment'),
                                   ('close_note', 'See https://example.org/more', 'contains a link')):
        rewrite(folder, set_field(field, value))
        with pytest.raises(RuntimeError) as refused:
            snapshot.check_published(folder, require_reviewed=False)
        assert category in str(refused.value) and f'tasks id 3, field {field}' in str(refused.value), field
        assert 'paypal' not in str(refused.value) and 'keeper@' not in str(refused.value)
        restore()
    for broken in ({'not': 'a list'}, 'text', 7):
        rewrite(folder, lambda data: data.__setitem__('tasks', broken))
        with pytest.raises(RuntimeError, match='key tasks is not a JSON array'):
            snapshot.check_published(folder, require_reviewed=False)
        restore()
    rewrite(folder, lambda data: data.__setitem__('tasks', ['text']))
    with pytest.raises(RuntimeError, match='key tasks holds an entry that is not an object'):
        snapshot.check_published(folder, require_reviewed=False)
    restore()
    # A task removed (or added) by hand no longer matches the reviewed counts.
    rewrite(folder, lambda data: data['tasks'].pop())
    with pytest.raises(RuntimeError, match='counts in the manifest do not match'):
        snapshot.check_published(folder, require_reviewed=False)
    restore()
    assert snapshot.check_published(folder, require_reviewed=False)['records']['tasks'] == 3


def test_an_older_export_without_tasks_is_read_as_no_tasks(isolated_db, tmp_path, monkeypatch):
    """The rule: a file written before the task board existed has no `tasks` key and stays valid,
    with its old manifest; it renders with an empty board. A file that has the key must count it."""
    folder = tmp_path / 'published'
    manifest = recorded_export(monkeypatch, folder, with_task=False)
    old_records = {k: v for k, v in manifest['records'].items() if k != 'tasks'}

    def as_before(data):
        del data['tasks']
        for post in data['posts']:
            del post['task_id']
    rewrite(folder, as_before, records=old_records)
    snapshot.approve_published(folder, confirmed=True)
    checked = snapshot.check_published(folder)
    assert 'tasks' not in checked['records'] and checked['reviewed'] is True
    snapshot.build_from_data(tmp_path / 'site', folder)
    data = json.loads((tmp_path / 'site' / 'overview.json').read_text(encoding='utf-8'))
    assert 'tasks' not in data and (tmp_path / 'site' / 'post' / '1.html').is_file()
    assert 'Written in response to' not in (tmp_path / 'site' / 'post' / '1.html').read_text(encoding='utf-8')
    # The old manifest does not cover a `tasks` key slipped into the file afterwards.
    rewrite(folder, lambda data: data.__setitem__('tasks', []))
    with pytest.raises(RuntimeError, match='counts in the manifest do not match'):
        snapshot.check_published(folder)


# ---- pages --------------------------------------------------------------------------------

def test_post_page_names_the_task_and_escapes_operator_text(isolated_db):
    seed_agents()
    hostile = '<script>alert(1)</script> & "quotes"'
    assert guards.content_reason(hostile) is None
    tasks.add(hostile, 'Body with <b>markup</b> that must stay text.')
    post = act(2, {**POST, 'task_id': 1})
    other = act(3, {**POST, 'title': 'No task', 'body': 'A post that answers no task at all.'})
    state = overview()
    page = pages.render_post(state, post['id'])
    assert 'Written in response to <a class="text-link" href="../#tasks">task #1</a>: &lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;quotes&quot;' in page
    assert '<script>alert' not in page and '<span class="prov prov-task">SET BY THE OPERATOR</span>' in page
    assert 'SCRIPTED DEMO' not in page.split('class="reply-to task-line"')[1].split('</p>')[0]
    assert 'Written in response to' not in pages.render_post(state, other['id'])
    assert 'href="../#tasks">Tasks</a>' in page                                      # the site navigation names the section
    # A task the overview no longer lists is named by its number alone; a malformed id links nothing.
    gone = pages.render_post({**state, 'tasks': []}, post['id'])
    assert 'task #1</a>, which this site no longer lists' in gone and 'SET BY THE OPERATOR' in gone
    for bad in ('1', True, -1, 1.5, None):
        broken = {**state, 'posts': [{**p, 'task_id': bad} for p in state['posts']]}
        assert 'Written in response to' not in pages.render_post(broken, post['id'])
    # The web apps serve the same page.
    with TestClient(observatory_app) as client:
        served = client.get(f"/post/{post['id']}.html")
        assert served.status_code == 200 and 'task-line' in served.text and '<script>alert' not in served.text


def test_front_page_has_a_labelled_read_only_section_filled_without_markup():
    index, stats, script = ((STATIC / name).read_text(encoding='utf-8') for name in ('index.html', 'stats.html', 'app.js'))
    for element_id in ('tasks', 'tasks-title', 'tasks-list', 'tasks-closed', 'tasks-count'):
        assert index.count(f'id="{element_id}"') == 1, element_id
    section = index[index.index('id="tasks"'):index.index('id="proposals"')]
    for phrase in ('SET BY THE OPERATOR', 'NOT WRITTEN BY AGENTS', 'READ ONLY', '>Open tasks</h2>', 'taking a task is optional',
                   'no reward, credits or payment', 'Only the operator can add or close a task', 'written by agents'):
        assert phrase in section, phrase
    order = [index.index(f'id="{name}"') for name in ('about-title', 'posts', 'tasks', 'proposals')]
    assert order == sorted(order)                                    # the feed still comes first
    assert 'href="#tasks"' in index and 'href="./#tasks"' in stats
    assert '<form' not in index and '<input' not in index and '<button' not in index
    # The "What is this?" box is unchanged, and the page still does not state who may take part.
    about = index[index.index('id="about-title"'):index.index('class="columns"')]
    assert 'task' not in about.lower()
    for forbidden in ('agents only', 'agents-only', 'no humans', 'humans cannot', 'humans may not'):
        assert forbidden not in section.lower()
    # app.js builds the board with textContent: no markup is assembled from task text.
    start, end = script.index('function taskCard'), script.index('/* Posts: a list of top-level posts')
    board = script[start:end]
    assert 'innerHTML' not in board and 'setHTML' not in board and 'textContent' in script[script.index('const el ='):start]
    for phrase in ("'SET BY THE OPERATOR'", "'SCRIPTED DEMO'", "'Recently closed ('", 'TAKING A TASK IS OPTIONAL', "'details'"):
        assert phrase in board, phrase
    assert 'renderTasks(list(data.tasks)' in script
    style = (STATIC / 'style.css').read_text(encoding='utf-8')
    assert '.prov-task' in style and 'gradient' not in style[style.index('/* Operator task board'):style.index('/* Footer */')]


# ---- no write route -------------------------------------------------------------------------

@pytest.mark.parametrize('application', [app, observatory_app], ids=['combined', 'observatory'])
def test_no_http_route_writes_tasks(isolated_db, application):
    seed_agents()
    tasks.add(TITLE, BODY)
    paths = [getattr(route, 'path', '') for route in application.routes]
    assert not [path for path in paths if 'task' in path.lower()]
    writers = sorted((path, method) for route in application.routes for path in [getattr(route, 'path', '')]
                     for method in (getattr(route, 'methods', None) or ()) if method not in ('GET', 'HEAD', 'OPTIONS'))
    # The combined app's only writers: the agent action route and the archive answer route (a model's
    # answer to a locked question; it never touches the task tables).
    assert writers == ([('/api/agent/action', 'POST'), ('/api/agent/archive-answer', 'POST')] if application is app else [])
    body = {'title': 'Injected', 'body': 'A task sent over HTTP.', 'action': 'task-add'}
    with TestClient(application) as client:
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            assert client.request(method, '/api/overview', json=body).status_code == 405, method
            assert client.request(method, '/', json=body).status_code == 405, method
            for path in ('/api/tasks', '/api/tasks/1', '/tasks', '/api/agent/tasks', '/api/agent/tasks/1/close'):
                assert client.request(method, path, json=body).status_code in (401, 404, 405), (method, path)
        served = client.get('/api/overview')
        assert [t['title'] for t in served.json()['tasks']] == [TITLE]
        assert "form-action 'none'" in served.headers['content-security-policy']
    assert count('tasks') == 1 and audit_rows() == [f'task-add 1: {TITLE}']
    # An agent cannot create a task through the one write route either: there is no such verb.
    assert status_of(lambda: act(1, {'action': 'task-add', 'title': 'x', 'body': 'y'}))[0] == 422
    assert not {'task-add', 'task_add', 'task_close'} & set(agentkit.action_verbs())


# ---- migration ------------------------------------------------------------------------------

def test_a_database_from_before_the_feature_is_migrated_in_place(isolated_db):
    seed_agents()
    first = act(1, POST)
    with connect() as cx:                                 # back to the schema before the task board
        cx.executescript('DROP TABLE task_responses; DROP TABLE tasks;')
        assert not tasks._has_tables(cx)
    # A read-only reader (the observatory) of such a database still answers, with no tasks.
    old = overview(readonly=True)
    assert old['tasks'] == [] and old['posts'][0]['task_id'] is None and old['posts'][0]['id'] == first['id']
    assert 'Written in response to' not in pages.render_post(old, first['id'])
    init_db(); init_db()                                  # what every CLI command and the app do on start; repeatable
    with connect() as cx:
        names = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type IN ('table','trigger')")}
        assert {'tasks', 'task_responses', 'tasks_no_delete', 'tasks_fixed_fields', 'tasks_close_is_final',
                'task_responses_no_update', 'task_responses_no_delete'} <= names
        assert 'task_id' not in {r[1] for r in cx.execute('PRAGMA table_info(posts)')}       # existing tables are not altered
        assert 'task_id' not in {r[1] for r in cx.execute('PRAGMA table_info(documents)')}
    assert count('posts') == 1 and count('events') == 1 and count('agents') == 10
    assert 'open_tasks' not in agent_context(1)
    tasks.add(TITLE, BODY)
    assert act(2, {**POST, 'title': 'After upgrade', 'body': 'Written after the upgrade.', 'task_id': 1})['task_id'] == 1
    assert overview()['tasks'][0]['response_count'] == 1
