"""Answers Over Time, the chat-app track (live_genesis/archive_chatbots.py): replies the owner collects BY HAND
and imports from a file. Offline: nothing here calls a model, opens a socket or contacts a chat app, and
every "reply" is an obvious placeholder."""
import datetime
import hashlib
import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from live_genesis import archive, archive_chatbots as chatbots, archive_pages, core, engine, snapshot
from test_archive import (HOSTILE, REPO, STATIC, as_real, assert_page_is_safe, fake, question_file, ready, rows,  # noqa: F401
                          run_cli, trending_file)

V4_SCHEMA = REPO / 'tests' / 'fixtures' / 'archive_schema_v4.sql'
REAL_NAMES = ('ChatGPT', 'Gemini', 'Grok', 'Copilot')
IDS = ['Q11', 'Q16', 'Q03', 'T002']
TODAY = datetime.date(2026, 11, 14)
CONDITIONS = {'account': 'free_account', 'fresh_chat': True, 'temporary_chat': True, 'memory_off': True, 'web_search': 'off',
              'country': 'US', 'app': 'web', 'shown_model': 'PLACEHOLDER model', 'note': ''}


def config_file(tmp_path, monkeypatch, ids=IDS, edit=None):
    data = json.loads((REPO / 'live_genesis' / 'archive_chatbots.json').read_text(encoding='utf-8'))
    data['questions'] = list(ids)
    if edit:
        edit(data)
    path = tmp_path / 'chatbots-config.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setattr(chatbots, 'CONFIG_FILE', path)
    return path


@pytest.fixture
def chat(ready, tmp_path, monkeypatch):
    """A database with both question sets locked (four test trending questions), a configuration with the
    four checked-in chat apps and four question ids, and a fixed 'today'."""
    trending_file(tmp_path, monkeypatch, count=4)
    config_file(tmp_path, monkeypatch)
    monkeypatch.setattr(chatbots, 'today', lambda: TODAY)
    monkeypatch.setattr(engine, 'infer', fake(AssertionError('the chat-app track calls no model')))
    archive.lock()
    archive.lock('trending')
    return ready


def filled(tmp_path, date='2026-11-14', name=None, only=None, edit=None, reply=None):
    """Write a filled-in collection file with PLACEHOLDER replies; `edit(data)` changes it before it is written."""
    data = chatbots.template(date)
    if only:
        data['chatbots'] = [block for block in data['chatbots'] if block['chatbot'] in only]
    for block in data['chatbots']:
        block['conditions'] = dict(CONDITIONS)
        for n, entry in enumerate(block['answers'], 1):
            entry['answer'] = reply(block['chatbot'], entry['question']) if reply else f"PLACEHOLDER reply {n} of {block['chatbot']}."
    if edit:
        edit(data)
    path = tmp_path / (name or f'collect-{date}.json')
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


def refused(path, match, **options):
    before = rows('SELECT COUNT(*) AS n FROM archive_chatbot_runs')[0]['n']
    with pytest.raises(archive.ArchiveRefused, match=match):
        chatbots.import_collection(path, **options)
    assert rows('SELECT COUNT(*) AS n FROM archive_chatbot_runs')[0]['n'] == before          # all or nothing


# ---- the configuration ------------------------------------------------------------------------------------

def test_checked_in_configuration_names_four_apps_and_ten_locked_questions():
    config = chatbots.load_config()
    raw = json.loads((REPO / 'live_genesis' / 'archive_chatbots.json').read_text(encoding='utf-8'))
    assert raw['status'] == 'draft' and config['status'] == 'draft'
    assert [(c['label'], c['name'], c['maker']) for c in config['chatbots']] == [
        ('chatgpt', 'ChatGPT', 'OpenAI'), ('gemini', 'Gemini', 'Google'), ('grok', 'Grok', 'xAI'), ('copilot', 'Copilot', 'Microsoft')]
    # A maker's attribution line is configuration, not a special case in the code: only one app has one.
    assert {c['label']: c['attribution'] for c in config['chatbots']} == {'chatgpt': '', 'gemini': '', 'grok': 'Written with Grok', 'copilot': ''}
    assert len(config['questions']) == 10 == len(set(config['questions']))
    locked = {q['id']: q for track in archive.TRACKS for q in archive.load_questions(track=track)['questions']}
    for question_id in config['questions']:                         # ids only: text and prompt come from the locked files
        assert config['prompts'][question_id]['prompt'] == locked[question_id]['prompt']
    assert set(raw) == {'format', 'status', 'note', 'chatbots', 'questions'} and 'prompt' not in json.dumps(raw['questions'])
    assert not re.search(r'https?:|www\.', json.dumps(raw))                                   # no address of any service
    assert re.fullmatch(r'[0-9a-f]{64}', config['sha256'])


@pytest.mark.parametrize('edit, why', [
    (lambda d: d['questions'].append('Q99'), 'Q99 is not a question'),
    (lambda d: d['questions'].append('Q11'), 'question ids must differ'),
    (lambda d: d['questions'].clear(), 'questions must list'),
    (lambda d: d['chatbots'].append(dict(d['chatbots'][0])), 'labels and names must differ'),
    (lambda d: d['chatbots'][0].update(label='Chat GPT'), 'a label must be'),
    (lambda d: d['chatbots'][0].update(label='chatbots'), 'a label must be'),                 # reserved: the four-up card's name
    (lambda d: d['chatbots'][0].update(url='https://example.org'), 'needs label, name and maker'),
    (lambda d: d['chatbots'][0].update(attribution='x' * 61), 'attribution must be'),
    (lambda d: d.update(status='final'), 'status must be'),
    (lambda d: d.update(extra=1), 'top level'),
    (lambda d: d.update(format='other'), 'format must be'),
])
def test_malformed_configuration_is_refused(ready, tmp_path, monkeypatch, edit, why):
    trending_file(tmp_path, monkeypatch, count=4)
    config_file(tmp_path, monkeypatch, edit=edit)
    with pytest.raises(archive.ArchiveRefused, match=why):
        chatbots.load_config()


def test_configuration_ids_must_refer_to_a_locked_question_file(ready, tmp_path, monkeypatch):
    trending_file(tmp_path, monkeypatch, count=4, status='draft')
    config_file(tmp_path, monkeypatch)
    with pytest.raises(archive.ArchiveRefused, match='T002 refers to trending.json, which is not locked yet'):
        chatbots.load_config()


# ---- the template -----------------------------------------------------------------------------------------

def test_template_holds_every_app_the_exact_prompts_and_empty_answers(chat, tmp_path):
    before = isolated = chat.read_bytes()
    target = chatbots.write_template(tmp_path / 't.json', '2026-11-10')
    data = json.loads(target.read_text(encoding='utf-8'))
    assert chat.read_bytes() == before == isolated                                           # the template opens no database
    assert list(data)[:3] == ['format', 'how_to', 'collected_on'] and data['collected_on'] == '2026-11-10'
    assert [b['chatbot'] for b in data['chatbots']] == ['chatgpt', 'gemini', 'grok', 'copilot']
    prompts = chatbots.load_config()['prompts']
    for block in data['chatbots']:
        assert set(block['conditions']) == set(chatbots.CONDITION_KEYS) and block['conditions']['fresh_chat'] is None
        assert [(e['question'], e['prompt'], e['answer']) for e in block['answers']] == [(i, prompts[i]['prompt'], '') for i in IDS]
    how = ' '.join(data['how_to'])
    for phrase in ('NEW chat', 'exactly', 'WHOLE reply', 'Do not edit', '"skipped"', 'answer_file', 'sha256', 'Temporary Chat',
                   'Private Chat', 'FREE account', 'Sources', 'terms', '--dry-run'):
        assert phrase in how, phrase
    # A blank template is not importable: the owner has to fill in the conditions and every answer.
    with pytest.raises(archive.ArchiveRefused, match='account must be one of'):
        chatbots.import_collection(target)
    with pytest.raises(archive.ArchiveRefused, match='already exists'):
        chatbots.write_template(tmp_path / 't.json')
    assert json.loads(chatbots.write_template(tmp_path / 'today.json').read_text(encoding='utf-8'))['collected_on'] == '2026-11-14'
    with pytest.raises(archive.ArchiveRefused, match='YYYY-MM-DD'):
        chatbots.template('14/11/2026')


def test_nothing_in_the_track_automates_or_contacts_a_chat_app():
    """Collection is by hand, for good: the module imports nothing that could reach a network, and neither it nor the
    template nor the docs of this track describe any automated way of collecting replies."""
    source = (REPO / 'live_genesis' / 'archive_chatbots.py').read_text(encoding='utf-8')
    assert not re.search(r'^\s*(?:import|from)\s+(?:urllib|http|socket|requests|httpx|subprocess|webbrowser|asyncio|ssl)\b', source, re.M)
    banned = re.compile(r'selenium|playwright|puppeteer|headless|scrap(?:e|er|ing)|crawler|webdriver|browser automation', re.I)
    assert not banned.search(source) and not banned.search(' '.join(chatbots.HOW_TO))
    assert not re.search(r'https?://', source) and not re.search(r'https?://', (REPO / 'live_genesis' / 'archive_chatbots.json').read_text(encoding='utf-8'))
    doc = (REPO / 'docs' / 'ANSWERS-OVER-TIME.md').read_text(encoding='utf-8')
    section = doc.split('## The chat-app track', 1)[1].split('\n## ', 1)[0]
    assert not banned.search(section.replace('never scrape', '')) and 'by hand' in section


# ---- import -----------------------------------------------------------------------------------------------

def test_dry_run_stores_nothing_and_import_stores_one_audited_run(chat, tmp_path, capsys):
    path = filled(tmp_path)
    before = hashlib.sha256(chat.read_bytes()).hexdigest()
    plan = chatbots.import_collection(path, dry_run=True)
    assert hashlib.sha256(chat.read_bytes()).hexdigest() == before and plan['dry_run'] is True and 'run' not in plan
    assert [c['answers'] for c in plan['chatbots']] == [{'answered': 4, 'no_answer': 0, 'withheld': 0}] * 4
    assert 'PLACEHOLDER reply' not in json.dumps(plan)                                       # counts and lengths, never a reply
    done = chatbots.import_collection(path)
    assert done['run'] == 1 and done['first_import'] is True and done['collected_on'] == '2026-11-14'
    assert done['chatbots'][0]['rows'][0] == {'question': 'Q11', 'state': 'answered', 'characters': 31, 'screenshot': False, 'changed': None}
    run = rows('SELECT * FROM archive_chatbot_runs')
    assert len(run) == 1 and run[0]['kind'] == 'manual' and run[0]['collected_on'] == '2026-11-14'
    assert run[0]['imported_at'].startswith('20') and run[0]['imported_at'] != run[0]['collected_on']      # both dates are kept
    assert json.loads(run[0]['question_ids']) == IDS and run[0]['config_sha256'] == chatbots.load_config()['sha256']
    assert run[0]['file_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    answers = rows('SELECT * FROM archive_chatbot_answers ORDER BY id')
    assert len(answers) == 16 and {a['source'] for a in answers} == {'manual'} and {a['state'] for a in answers} == {'answered'}
    assert answers[0]['answer_text'] == 'PLACEHOLDER reply 1 of chatgpt.' and answers[0]['changed'] is None
    assert {a['question_set'] for a in answers} == {'basic-1', 'trending'}
    conditions = rows('SELECT * FROM archive_chatbot_conditions ORDER BY rowid')
    assert [c['chatbot'] for c in conditions] == ['chatgpt', 'gemini', 'grok', 'copilot']
    assert conditions[2]['attribution'] == 'Written with Grok' and conditions[0]['attribution'] == ''
    assert (conditions[0]['account'], conditions[0]['fresh_chat'], conditions[0]['memory_off']) == ('free_account', 1, 'true')
    audit = [r['operation'] for r in rows('SELECT operation FROM operator_audit')]
    assert any(op.startswith('archive-chatbot-import run 1: collected 2026-11-14, chatgpt,gemini,grok,copilot, 16 rows') for op in audit)
    # No model answer, no budget row, no agent record: this track has its own tables only.
    assert rows('SELECT * FROM archive_answers') == [] and rows('SELECT * FROM archive_runs') == []
    assert rows('SELECT * FROM budget_reservations') == [] and rows('SELECT * FROM api_usage') == []


def test_dates_only_move_forward_one_run_per_app_and_day(chat, tmp_path):
    refused(filled(tmp_path, '2026-11-15'), 'in the future')
    refused(filled(tmp_path, '2026-10-30'), 'more than 14 days ago')
    chatbots.import_collection(filled(tmp_path, '2026-10-30'), allow_old=True)
    refused(filled(tmp_path, '2026-10-30', name='again.json'), 'already has a hand-entered run dated 2026-10-30', allow_old=True)
    refused(filled(tmp_path, '2026-10-20', name='earlier.json'), "dates only move forward", allow_old=True)
    # Another app on the same day is its own run; an app that was not asked simply has no cell.
    chatbots.import_collection(filled(tmp_path, '2026-11-13', only=['grok']))
    done = chatbots.import_collection(filled(tmp_path, '2026-11-14', only=['chatgpt', 'gemini']))
    assert done['run'] == 3 and done['first_import'] is False
    refused(filled(tmp_path, '2026-11-14', name='dup.json', only=['copilot', 'gemini']), 'gemini already has a hand-entered run dated 2026-11-14')
    with core.connect() as cx:                                       # the database itself refuses it too
        with pytest.raises(sqlite3.IntegrityError, match='this date or a later one'):
            cx.execute("INSERT INTO archive_chatbot_conditions(run_id,chatbot,display_name,maker,account,fresh_chat,temporary_chat,"
                       "memory_off,web_search,country,app,shown_model) VALUES (1,'grok','Grok','xAI','free_account',1,1,'true','off','US','web','x')")


def test_changed_compares_with_that_apps_previous_hand_entered_answer(chat, tmp_path):
    first = {('chatgpt', 'Q11'): 'It is 2025.', ('gemini', 'Q11'): 'I cannot know.'}
    second = {('chatgpt', 'Q11'): 'It is 2026.', ('gemini', 'Q11'): '  i cannot know!! '}
    chatbots.import_collection(filled(tmp_path, '2026-10-10', reply=lambda app, q: first.get((app, q), 'PLACEHOLDER same.')), allow_old=True)

    def skip_one(data):
        entry = data['chatbots'][3]['answers'][0]                    # copilot, Q11: the app refused
        entry['answer'] = ''
        entry['skipped'] = 'PLACEHOLDER: the app refused'
    done = chatbots.import_collection(filled(tmp_path, '2026-11-14', reply=lambda app, q: second.get((app, q), 'PLACEHOLDER same.'), edit=skip_one))
    changed = {(c['chatbot'], r['question']): r['changed'] for c in done['chatbots'] for r in c['rows']}
    assert changed[('chatgpt', 'Q11')] is True and changed[('gemini', 'Q11')] is False       # the existing normalisation
    assert changed[('grok', 'Q11')] is False and changed[('copilot', 'Q11')] is None and done['chatbots'][3]['skipped'] == 1
    row = rows("SELECT state,reason,answer_text FROM archive_chatbot_answers WHERE chatbot='copilot' AND question_id='Q11' ORDER BY id")[-1]
    assert row == {'state': 'no_answer', 'reason': 'skipped: PLACEHOLDER: the app refused', 'answer_text': None}


def test_model_answers_and_hand_entered_answers_never_touch_each_other(chat, tmp_path, monkeypatch):
    core.seed_agents()
    as_real(monkeypatch, 'Seven.')
    archive.run_archive(max_usd=1)
    chatbots.import_collection(filled(tmp_path, reply=lambda app, q: 'Eight.'))
    archive.run_archive(max_usd=1)
    payload = archive.public_payload()
    assert payload['schema'] == 'live-genesis-archive/4' and archive.payload_problem(payload) is None
    assert payload['answerers'] == ['model-a', 'model-b'] and {a['source'] for a in payload['answers']} == {'engine'}
    assert {a['changed'] for a in payload['answers'] if a['run'] == 2} == {False}            # a hand-entered "Eight." changed no model
    assert payload['answers_by_source'] == {'engine': 96, 'api': 0, 'demo': 0} and len(payload['runs']) == 2
    block = payload['chatbots']
    assert [app['label'] for app in block['apps']] == ['chatgpt', 'gemini', 'grok', 'copilot'] and len(block['answers']) == 16
    assert {a['source'] for a in block['answers']} == {'manual'} and {a['changed'] for a in block['answers']} == {None}
    pages = archive_pages.render_site(payload)
    assert pages['index.html'].count('class="strip"') == 2 * 20 and 'ChatGPT' not in pages['index.html']    # model strips only
    for name in REAL_NAMES:
        assert name not in pages['q/Q01.html'], name                 # a question that was not asked of the apps
    models = pages['q/Q11.html'].split('<div id="chat-apps"')[0]
    assert 'Eight.' not in models and 'entered by hand' not in models.split('Source of each answer')[0]


def test_strict_validation_refuses_the_whole_file_and_never_echoes_a_reply(chat, tmp_path):
    secret = 'PLACEHOLDER-SECRET-REPLY-TEXT'

    def bad(edit, why, **options):
        def change(data):
            data['chatbots'][0]['answers'][1]['answer'] = secret
            edit(data)
        path = filled(tmp_path, name='bad.json', edit=change)
        with pytest.raises(archive.ArchiveRefused, match=why) as caught:
            chatbots.import_collection(path, **options)
        assert secret not in str(caught.value) and rows('SELECT * FROM archive_chatbot_runs') == []
    first = lambda d: d['chatbots'][0]                                # noqa: E731
    bad(lambda d: first(d)['conditions'].update(fresh_chat=False), 'fresh_chat must be true')
    bad(lambda d: first(d)['conditions'].update(account='team'), 'account must be one of logged_out')
    bad(lambda d: first(d)['conditions'].update(temporary_chat='yes'), 'temporary_chat must be true or false')
    bad(lambda d: first(d)['conditions'].update(memory_off='maybe'), 'memory_off must be')
    bad(lambda d: first(d)['conditions'].update(web_search='sometimes'), 'web_search must be one of on')
    bad(lambda d: first(d)['conditions'].update(country='usa'), 'country must be two capital letters')
    bad(lambda d: first(d)['conditions'].update(app='watch'), 'app must be one of web')
    bad(lambda d: first(d)['conditions'].update(shown_model=''), 'shown_model must be')
    bad(lambda d: first(d)['conditions'].update(shown_model='mail me: someone.real@example.org'), 'shown_model must be')
    bad(lambda d: first(d)['conditions'].pop('country'), 'conditions must hold exactly')
    bad(lambda d: first(d)['conditions'].update(vpn=True), 'conditions must hold exactly')
    bad(lambda d: first(d)['answers'][0].update(prompt='What day is it?'), 'not the locked prompt')
    bad(lambda d: first(d)['answers'].pop(), 'must cover exactly the 4 questions')
    bad(lambda d: first(d)['answers'].append(dict(first(d)['answers'][0])), 'must cover exactly the 4 questions')
    bad(lambda d: first(d)['answers'][0].update(question='Q01'), 'not one of the questions')
    bad(lambda d: first(d)['answers'][0].update(answer=''), 'exactly one of answer, answer_file or skipped')
    bad(lambda d: first(d)['answers'][0].update(skipped='the app refused'), 'exactly one of answer, answer_file or skipped')
    bad(lambda d: first(d)['answers'][0].update(screenshot='no-such.png'), 'screenshot file was not found')
    bad(lambda d: first(d)['answers'][0].update(answer='', answer_file='no-such.txt'), 'answer_file was not found')
    bad(lambda d: first(d)['answers'][0].update(rating=5), 'an answer entry must hold')
    bad(lambda d: first(d).update(chatbot='claude'), 'not in chatbots-config.json')
    bad(lambda d: d['chatbots'].append(dict(d['chatbots'][0])), 'listed twice')
    bad(lambda d: d.update(chatbots=[]), 'at least one chat app')
    bad(lambda d: d.update(collected_on='yesterday'), 'YYYY-MM-DD')
    bad(lambda d: d.update(extra=True), 'top level')
    bad(lambda d: d.update(format='x'), 'not a chat-app collection file')
    # Every problem is named at once, by place and rule.
    with pytest.raises(archive.ArchiveRefused) as caught:
        chatbots.import_collection(filled(tmp_path, name='two.json', edit=lambda d: (
            d['chatbots'][0]['conditions'].update(fresh_chat=False), d['chatbots'][1]['answers'][2].update(answer=''))))
    assert 'chatgpt: fresh_chat must be true' in str(caught.value) and 'gemini, Q03: give exactly one' in str(caught.value)
    # Not JSON, a duplicate key, a missing file.
    broken = tmp_path / 'broken.json'
    broken.write_text('{"format": "live-genesis-chatbot-collection/1", "format": "x"}', encoding='utf-8')
    for path in (broken, tmp_path / 'missing.json'):
        with pytest.raises(archive.ArchiveRefused, match='not valid JSON'):
            chatbots.import_collection(path)


def test_replies_pass_the_guards_and_the_longer_limit_and_are_stored_as_pasted(chat, tmp_path):
    pasted = '**PLACEHOLDER** reply with markdown.[1]\n\n- one\n- "two"\n\n[1] PLACEHOLDER source'
    (tmp_path / 'reply.txt').write_text('﻿' + pasted + '\n', encoding='utf-8')
    (tmp_path / 'shot.png').write_bytes(b'PLACEHOLDER image bytes')
    replies = {('chatgpt', 'Q11'): 'Write to someone.real@example.org for the date.', ('chatgpt', 'Q16'): 'x' * 2001,
               ('chatgpt', 'Q03'): 'y ' * 1000, ('gemini', 'Q11'): 'Hidden​character.', ('gemini', 'Q16'): '...'}

    def change(data):
        entry = data['chatbots'][2]['answers'][0]                    # grok, Q11: the reply is in a text file, with a screenshot
        entry.update(answer='', answer_file='reply.txt', screenshot=str(tmp_path / 'shot.png'))
    done = chatbots.import_collection(filled(tmp_path, reply=lambda app, q: replies.get((app, q), 'PLACEHOLDER fine.'), edit=change))
    stored = {(a['chatbot'], a['question_id']): a for a in rows('SELECT * FROM archive_chatbot_answers')}
    assert (stored[('chatgpt', 'Q11')]['state'], stored[('chatgpt', 'Q11')]['reason'], stored[('chatgpt', 'Q11')]['answer_text']) == \
        ('withheld', 'looks like an email address', None)                                                  # the category only, as for a model answer
    assert (stored[('chatgpt', 'Q16')]['state'], stored[('chatgpt', 'Q16')]['reason']) == ('no_answer', archive.TOO_LONG)
    assert stored[('chatgpt', 'Q03')]['state'] == 'answered' and len(stored[('chatgpt', 'Q03')]['answer_text']) == 1999    # up to 2000
    assert stored[('gemini', 'Q11')]['state'] == 'withheld' and stored[('gemini', 'Q11')]['reason'] == 'invisible or control character'
    assert (stored[('gemini', 'Q16')]['state'], stored[('gemini', 'Q16')]['reason']) == ('no_answer', archive.EMPTY)
    grok = stored[('grok', 'Q11')]
    assert grok['answer_text'] == pasted                              # markdown, citation markers and line breaks as pasted
    assert grok['screenshot_sha256'] == hashlib.sha256(b'PLACEHOLDER image bytes').hexdigest()
    everything = json.dumps(rows('SELECT * FROM archive_chatbot_answers') + rows('SELECT * FROM archive_chatbot_runs')
                            + rows('SELECT operation FROM operator_audit')) + json.dumps(archive.public_payload())
    assert 'shot.png' not in everything and str(tmp_path) not in everything and 'someone.real' not in everything    # never a path, never withheld text
    summary = json.dumps(done)
    assert 'looks like an email address' in summary and 'someone.real' not in summary and 'PLACEHOLDER' not in summary.replace('PLACEHOLDER model', '')


def test_import_needs_both_locks_and_never_runs_next_to_scripted_data(ready, tmp_path, monkeypatch):
    trending_file(tmp_path, monkeypatch, count=4)
    config_file(tmp_path, monkeypatch)
    monkeypatch.setattr(chatbots, 'today', lambda: TODAY)
    path = filled(tmp_path)
    with pytest.raises(archive.ArchiveRefused, match='question list is not locked'):
        chatbots.import_collection(path)
    archive.lock()
    with pytest.raises(archive.ArchiveRefused, match='trending question list is not locked'):
        chatbots.import_collection(path, dry_run=True)
    assert rows('SELECT * FROM archive_chatbot_runs') == []


def test_demo_database_refuses_hand_entered_rows_and_the_reverse(ready, tmp_path, monkeypatch):
    archive.demo_archive()
    with core.connect() as cx:
        with pytest.raises(sqlite3.IntegrityError, match='never mixed'):
            cx.execute("INSERT INTO archive_chatbot_runs(number,kind,collected_on,imported_at,question_ids,config_sha256) "
                       "VALUES (1,'manual','2026-11-14','2026-11-14T00:00:00+00:00','[]','x')")
    # The importer itself stops earlier: a database with scripted answers has no locked question set, and cannot get one.
    trending_file(tmp_path, monkeypatch, count=4)
    config_file(tmp_path, monkeypatch)
    monkeypatch.setattr(chatbots, 'today', lambda: TODAY)
    with pytest.raises(archive.ArchiveRefused, match='not locked'):
        chatbots.import_collection(filled(tmp_path))
    assert {r['kind'] for r in rows('SELECT kind FROM archive_chatbot_runs')} == {'demo'}


def test_demo_archive_is_refused_once_hand_entered_rows_exist(chat, tmp_path):
    chatbots.import_collection(filled(tmp_path))
    with pytest.raises(archive.ArchiveRefused, match='use a fresh DB'):
        archive.demo_archive()
    with core.connect() as cx:
        with pytest.raises(sqlite3.IntegrityError, match='never mixed'):
            cx.execute("INSERT INTO archive_chatbot_runs(number,kind,collected_on,imported_at,question_ids,config_sha256) "
                       "VALUES (1,'demo','2026-11-14','2026-11-14T00:00:00+00:00','[]','x')")


def test_after_the_first_import_question_ids_are_only_added(chat, tmp_path, monkeypatch):
    chatbots.import_collection(filled(tmp_path, '2026-11-10'))
    config_file(tmp_path, monkeypatch, ids=['Q11', 'Q16', 'Q03'])                             # T002 dropped
    with pytest.raises(archive.ArchiveRefused, match='no longer lists T002, which already has hand-entered answers'):
        chatbots.import_collection(filled(tmp_path, '2026-11-12'))
    config_file(tmp_path, monkeypatch, ids=IDS + ['Q12'])                                     # one added
    refused(filled(tmp_path, '2026-11-12', edit=lambda d: [b['answers'].pop() for b in d['chatbots']]), 'must cover exactly the 5 questions')
    done = chatbots.import_collection(filled(tmp_path, '2026-11-12'))
    assert done['questions'] == IDS + ['Q12']
    recorded = [json.loads(r['question_ids']) for r in rows('SELECT question_ids FROM archive_chatbot_runs ORDER BY number')]
    assert recorded == [IDS, IDS + ['Q12']]
    assert len({r['config_sha256'] for r in rows('SELECT config_sha256 FROM archive_chatbot_runs')}) == 2
    block = archive.public_payload()['chatbots']
    assert block['questions'] == IDS + ['Q12'] and [run['questions'] for run in block['runs']] == [IDS, IDS + ['Q12']]
    # A question that is in the configuration but not locked in this database is named.
    config_file(tmp_path, monkeypatch, ids=IDS + ['Q12'])
    trending_file(tmp_path, monkeypatch, count=4)


def test_chatbot_tables_are_append_only_and_need_a_locked_question(chat, tmp_path):
    chatbots.import_collection(filled(tmp_path))
    with core.connect() as cx:
        for table in chatbots.TABLE_NAMES:
            for statement in (f'UPDATE {table} SET rowid=rowid', f'DELETE FROM {table}'):
                with pytest.raises(sqlite3.IntegrityError, match='append-only'):
                    cx.execute(statement)
        insert = ("INSERT INTO archive_chatbot_answers(run_id,question_set,question_id,chatbot,source,state,reason,created_at) "
                  "VALUES (1,?,?,?,?,'no_answer','x','2026-11-14T00:00:00+00:00')")
        with pytest.raises(sqlite3.IntegrityError, match='locked question'):
            cx.execute(insert, ('basic-1', 'Q99', 'chatgpt', 'manual'))
        with pytest.raises(sqlite3.IntegrityError, match='does not match the kind'):
            cx.execute(insert, ('basic-1', 'Q12', 'chatgpt', 'demo'))
        with pytest.raises(sqlite3.IntegrityError, match='UNIQUE'):
            cx.execute(insert, ('basic-1', 'Q11', 'chatgpt', 'manual'))
        with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):                     # no answer without recorded conditions
            cx.execute(insert, ('basic-1', 'Q12', 'claude', 'manual'))
            cx.commit()


# ---- migration --------------------------------------------------------------------------------------------

def test_a_database_from_before_the_chat_app_track_migrates_in_place(ready, monkeypatch):
    core.seed_agents()
    archive.lock()
    as_real(monkeypatch, 'Seven.')
    archive.run_archive(max_usd=1)
    old = V4_SCHEMA.read_text(encoding='utf-8')
    assert 'CREATE TABLE IF NOT EXISTS archive_chatbot' not in old and 'archive_chatbot_runs' not in old and 'CREATE TABLE IF NOT EXISTS archive_answers' in old
    with core.connect() as cx:
        # Back to the schema before this track: the three tables and their rules are gone, everything else is as it was.
        for name, kind in cx.execute("SELECT name,type FROM sqlite_master WHERE name LIKE 'archive_chatbot_%' AND type IN ('trigger','index') "
                                     "AND name NOT LIKE 'sqlite_%'").fetchall():
            cx.execute(f'DROP {kind} {name}')
        for table in reversed(chatbots.TABLE_NAMES):
            cx.execute(f'DROP TABLE {table}')
        cx.executescript(old)                                        # the old schema applies cleanly to what is left
        before = {t: [tuple(r) for r in cx.execute(f'SELECT * FROM {t} ORDER BY rowid')]
                  for t in ('archive_answers', 'archive_runs', 'archive_run_sets', 'archive_questions', 'archive_sets', 'operator_audit')}
        rules_before = sorted(r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type IN ('trigger','index') AND tbl_name LIKE 'archive_%'"))
    payload_before = archive.public_payload()                        # an unmigrated database still reads (read-only, e.g. the observatory)
    assert 'chatbots' not in payload_before and payload_before['schema'] == 'live-genesis-archive/3'
    core.init_db()
    with core.connect() as cx:
        tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert set(chatbots.TABLE_NAMES) <= tables
        for table, expected in before.items():
            assert [tuple(r) for r in cx.execute(f'SELECT * FROM {table} ORDER BY rowid')] == expected, table
        rules = sorted(r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type IN ('trigger','index') AND tbl_name LIKE 'archive_%'"))
        assert set(rules_before) < set(rules) and all('chatbot' in name for name in set(rules) - set(rules_before))
        assert all(cx.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] == 0 for t in chatbots.TABLE_NAMES)
    assert archive.public_payload() == payload_before                # with no chat-app run the payload is what it always was
    core.init_db()                                                   # and the migration can run any number of times


# ---- scripted demo: never a real product's name -------------------------------------------------------------

def test_demo_uses_made_up_apps_only_and_every_cell_says_scripted(ready, tmp_path):
    done = archive.demo_archive()
    assert done['chatbots'] == ['chat-app-a', 'chat-app-b', 'chat-app-c', 'chat-app-d'] and done['chatbot_runs'] == 4
    payload = archive.public_payload()
    assert archive.payload_problem(payload) is None and payload['demo_only'] is True
    block = payload['chatbots']
    assert [app['label'] for app in block['apps']] == done['chatbots'] and all(run['demo'] for run in block['runs'])
    assert {a['source'] for a in block['answers']} == {'demo'} and len(block['answers']) == done['chatbot_answers'] == 90
    pages = archive_pages.render_site(payload)
    for name, text in {**pages, 'payload': json.dumps(payload)}.items():      # every demo page (a snapshot writes exactly these) and the payload
        for real in REAL_NAMES + ('OpenAI', 'xAI', 'Microsoft', 'chatgpt', 'copilot'):
            assert real not in text, (name, real)
    assert 'Google' not in json.dumps(block)
    listing, date, card, one = pages['chatbots.html'], pages['q/Q11.html'], pages['card/Q11-chatbots.html'], pages['card/Q03-chat-app-c.html']
    grid = date.split('<div id="chat-apps"')[1].split('</main>')[0]
    cells = re.findall(r'<div class="cell cell-chat cell-(?:same|changed)".*?</div>(?=<div class="cell|</div>\n)', grid, re.S)
    assert len(cells) == 15 and all('· scripted demo' in cell for cell in cells) and 'entered by hand</span>' not in grid
    assert grid.count('scripted date') == 4 and 'asked by hand' not in grid
    assert 'the apps are made up and the replies are scripted' in listing and 'made-up app names' in card
    assert 'Scripted demo answers and dates · no model was called' in card and 'Written with Chat app C' in one
    assert 'AI-generated' not in card and 'trademarks' not in listing     # the notice about real companies is for real replies only
    # The scripted conditions change (search switched on) is flagged; another shown model name is not a change of conditions.
    assert grid.count('>CONDITIONS CHANGED<') == 2 and 'conditions changed · search: off → on' in grid and 'country: US → GB' in grid
    assert 'B mini 2 · scripted demo · new shown model' in grid and 'Not asked on this date' in grid
    assert 'aria-label="Chat app A: 2 changes across 4 dates, conditions changed on 1 date"' in listing
    assert 'aria-label="Chat app B: 0 changes across 4 dates, 1 without a reply"' in listing
    assert 'Chat app C: 0 changes across 4 dates, conditions changed on 1 date' in listing   # country US -> GB at the fourth date
    for text in chatbots.DEMO_SCRIPT.values():
        for steps in text.values():
            assert all(chatbots.classify_reply(reply)[0] == 'answered' for _run, reply in steps)


# ---- pages --------------------------------------------------------------------------------------------------

def two_dates(tmp_path):
    def later(data):
        data['chatbots'][0]['conditions'].update(web_search='on')
        data['chatbots'][1]['conditions'].update(shown_model='PLACEHOLDER model 2')
        data['chatbots'][3]['conditions'].update(note='PLACEHOLDER note.')
    chatbots.import_collection(filled(tmp_path, '2026-10-10', reply=lambda app, q: f'PLACEHOLDER first {app} {q}.'), allow_old=True)
    chatbots.import_collection(filled(tmp_path, '2026-11-14', edit=later, reply=lambda app, q: (
        'PLACEHOLDER second.' if (app, q) == ('chatgpt', 'Q11') else 'PLACEHOLDER long. ' * 40 if (app, q) == ('gemini', 'Q11')
        else f'PLACEHOLDER first {app} {q}.')))
    return archive.public_payload()


def test_pages_show_hand_entered_replies_with_names_conditions_and_notices(chat, tmp_path):
    payload = two_dates(tmp_path)
    assert payload['demo_only'] is False and archive.payload_problem(payload) is None
    pages = archive_pages.render_site(payload)
    cards = {f'card/{q}-{s}.html' for q in IDS for s in ('chatbots', 'chatgpt', 'gemini', 'grok', 'copilot')}
    assert cards <= set(pages) and 'chatbots.html' in pages and len([n for n in pages if '-' in n]) == 20
    for name, page in pages.items():
        assert_page_is_safe(page)
        assert page.count('<div') == page.count('</div>') and page.count('<span') == page.count('</span>'), name
        assert 'no payments' in page.lower() and not re.search(r'\bround', page, re.I)
        assert not re.search(r'<img(?![^>]*archive-mark\.svg)', page)                         # the archive's own mark is the only image: no logo
        if not name.startswith('card/'):
            assert '<a href="chatbots.html"' in page or '<a href="../chatbots.html"' in page  # the nav entry
    listing, date = pages['chatbots.html'], pages['q/Q11.html']
    assert '<a href="chatbots.html" aria-current="page">Chatbots</a>' in listing
    assert '<h1 class="display">The same questions, typed into the chat apps.</h1>' in listing
    assert 'A person asked each chat app by hand, in a new chat, and pasted the replies here.' in listing
    assert '<h2>Four questions</h2>' in listing and listing.count('class="strip"') == 16
    assert 'href="q/Q11.html#chat-apps"' in listing and 'href="t/T002.html#chat-apps"' in listing
    assert 'aria-label="ChatGPT: 1 change across 2 dates, conditions changed on 1 date"' in listing
    assert '<span class="sq sq-changed sq-cond"></span>' in listing and listing.count('sq-cond') == 4 + 1        # one per question, and the legend
    assert 'Last change: ChatGPT, Nov 2026' in listing and 'No change in any app across 2 dates' in listing
    for phrase in ('Asked by hand', 'Pasted exactly', 'Conditions recorded and shown', 'Cannot be verified', 'Depends on the day',
                   'Not affiliated', 'not affiliated with or endorsed by the companies named',
                   'Names are used only to say which app was asked', 'No software contacts these apps'):
        assert phrase in listing, phrase
    assert archive_pages.esc(archive_pages.chat_disclaimer(['OpenAI', 'Google', 'xAI', 'Microsoft'])) in listing
    assert ('not affiliated with, sponsored by or endorsed by OpenAI, Google, xAI or Microsoft; product names are their owners&#x27; '
            'trademarks. The answers may be inaccurate and are not advice.') in listing
    assert 'free account · temporary chat · memory off · search on · US · web' in listing and 'Written with Grok' in listing
    assert 'PLACEHOLDER note.' in listing and 'Model shown by the app: PLACEHOLDER model 2' in listing
    # The question page: the second grid, below the model grid, one column per app, newest first.
    assert date.index('Every answer, newest first') < date.index('<h2>Chat apps, entered by hand</h2>') < date.index('The exact prompt')
    grid = date.split('<div id="chat-apps"')[1].split('</main>')[0]
    assert '<span role="columnheader">CHATGPT</span><span role="columnheader">GEMINI</span><span role="columnheader">GROK</span>' in grid
    assert grid.index('Nov 2026') < grid.index('Oct 2026') and grid.count('asked by hand') == 2 and 'class="grid n-4"' in grid
    assert grid.count('· entered by hand') == 8 and grid.count('>CHANGED<') == 2 and grid.count('>CONDITIONS CHANGED<') == 1
    assert 'conditions changed · search: off → on' in grid and 'PLACEHOLDER model 2 · entered by hand · new shown model' in grid
    assert grid.count('<span class="cell-meta mono">Written with Grok</span>') == 2           # every cell of that app, no other
    assert ('PLACEHOLDER long. ' * 40).strip() in grid and grid.count('class="cell-text cell-scroll" tabindex="0"') == 1    # the whole reply
    assert '<strong>entered by hand</strong> means a person typed the question into the chat app and pasted its reply.' in date
    assert 'href="../card/Q11-chatbots.html">All chat apps</a>' in date and 'href="../card/Q11-grok.html">Grok</a>' in date
    assert 'chat-apps' not in pages['q/Q01.html'] and 'entered by hand' not in pages['q/Q01.html']
    with pytest.raises(KeyError):
        archive_pages.render_chatbot_card(payload, 'Q01')
    with pytest.raises(KeyError):
        archive_pages.render_chatbot_card(payload, 'Q11', 'claude')


def test_chat_cards_carry_date_notice_and_attribution_and_keep_the_question_largest(chat, tmp_path):
    payload = two_dates(tmp_path)
    four, one = archive_pages.render_chatbot_card(payload, 'Q11'), archive_pages.render_chatbot_card(payload, 'Q11', 'grok')
    for card in (four, one):
        assert_page_is_safe(card)
        assert '<main class="card card-safe"><div class="card-in">' in card and '<h1 class="card-q card-q-xl">' in card
        assert '<span>AI-generated · typed in by hand · not affiliated · may be wrong</span>' in card
        assert 'read-only · no payments' in card and '14 Nov 2026 · entered by hand' in card  # a date on every card
        assert '<a class="card-brand" href="../q/Q11.html">' in card and card.count('<h1') == 1
    assert four.count('<section class="card-panel') == 4 and '<div class="card-panels n-4">' in four
    assert four.count('card-panel-changed') == 2 and '14 Nov 2026 · entered by hand · changed' in four
    assert [m for m in re.findall(r'<span class="card-label">([^<]+)</span>', four)] == list(REAL_NAMES)
    assert four.count('<span class="card-credit mono">Written with Grok</span>') == 1 == one.count('Written with Grok')
    assert one.count('<section class="card-panel') == 1 and '<div class="card-panels one">' in one
    assert '<span class="card-cond mono">free account · temporary chat · memory off · search off · US · web</span>' in one
    assert 'card-cond' not in four and 'ChatGPT' not in one
    # The stylesheet: the safe area, the smallest sizes on a 1080 px wide canvas, and nothing but cqw units in the card rules.
    css = (STATIC / 'archive.css').read_text(encoding='utf-8')
    safe = css.split('/* ---- chat-app share cards', 1)[1].split('@media', 1)[0]
    assert '.card-safe .card-top { height: 25cqw;' in safe and 'height: 108.33cqw; padding: 0 13cqw 0 6.1cqw;' in safe
    sizes = {name: float(size) * 10.8 for name, size in re.findall(r'\.card-safe (\.card-q-\w+|(?:\.n-\d |\.one )?\.card-answer)[^{]*\{ font-size: ([0-9.]+)cqw', safe)}
    assert min(v for k, v in sizes.items() if k.startswith('.card-q')) >= 72                 # the question: at least 72 px
    assert sizes['.card-answer'] >= 30 and float(re.search(r'\.one \.card-answer \{ font-size: ([0-9.]+)cqw', safe).group(1)) * 10.8 >= 40
    assert max(v for k, v in sizes.items() if 'answer' in k) < min(v for k, v in sizes.items() if k.startswith('.card-q'))
    assert float(re.search(r'\.card-safe \.card-meta \{ font-size: ([0-9.]+)cqw', safe).group(1)) < 6.8      # an app name is never the largest text
    assert re.findall(r'\d(?:px|rem|em)\b', safe) == [] and 'gradient' not in safe and 'url(' not in safe


def test_hostile_text_in_the_chat_track_is_escaped(chat, tmp_path):
    payload = two_dates(tmp_path)
    block = payload['chatbots']
    block['apps'][0].update(name=HOSTILE[:40], maker=HOSTILE[:40], attribution=HOSTILE[:60])
    block['answers'][0].update(text=HOSTILE, reason=HOSTILE)
    for run in block['runs']:
        run['conditions']['chatgpt'].update(shown_model=HOSTILE[:60], note=HOSTILE)
    assert archive.payload_problem(payload) is None
    for page in (archive_pages.render_chatbots(payload), archive_pages.render_question(payload, 'Q11'),
                 archive_pages.render_chatbot_card(payload, 'Q11'), archive_pages.render_chatbot_card(payload, 'Q11', 'chatgpt')):
        parsed = assert_page_is_safe(page)
        assert [tag for tag, _ in parsed.tags].count('img') == 1 and 'onmouseover="' not in page and '<script' not in page
        assert page.count('<div') == page.count('</div>') and page.count('<span') == page.count('</span>')


def test_both_web_apps_serve_the_chat_pages_and_cards(chat, tmp_path):
    from live_genesis.app import app as combined
    from live_genesis.observatory import app as readonly
    payload = two_dates(tmp_path)
    for application in (combined, readonly):
        with TestClient(application) as client:
            assert client.get('/archive/chatbots.html').text == archive_pages.render_chatbots(payload)
            assert client.get('/archive/q/Q11.html').text == archive_pages.render_question(payload, 'Q11')
            assert client.get('/archive/card/Q11-chatbots.html').text == archive_pages.render_chatbot_card(payload, 'Q11')
            assert client.get('/archive/card/T002-grok.html').text == archive_pages.render_chatbot_card(payload, 'T002', 'grok')
            assert client.get('/archive/card/Q11.html').text == archive_pages.render_card(payload, 'Q11')
            for missing in ('/archive/card/Q01-chatbots.html', '/archive/card/Q11-claude.html', '/archive/card/Q99-chatbots.html',
                            '/archive/card/Q11-chatbots', '/archive/card/Q11-.html', '/archive/card/Q11-Grok.html', '/archive/chatbots'):
                assert client.get(missing).status_code == 404, missing
    with TestClient(readonly) as client:
        assert client.post('/archive/chatbots.html').status_code == 405


# ---- export and --check -------------------------------------------------------------------------------------

def retarget(folder, edit):
    """Hand-edit archive.json and make the manifest agree with it again (hash and counts)."""
    data = json.loads((folder / 'archive.json').read_text(encoding='utf-8'))
    edit(data)
    raw = json.dumps(data, ensure_ascii=True, indent=1).encode('ascii')
    (folder / 'archive.json').write_bytes(raw)
    manifest = json.loads((folder / 'MANIFEST.json').read_text(encoding='utf-8'))
    manifest['archive'] = {**snapshot._archive_counts(data), 'sha256': hashlib.sha256(raw).hexdigest()}
    (folder / 'MANIFEST.json').write_text(json.dumps(manifest, indent=1))


def test_hand_entered_answers_go_through_the_reviewed_export(chat, tmp_path, monkeypatch):
    (tmp_path / 'shot.png').write_bytes(b'PLACEHOLDER image bytes')
    chatbots.import_collection(filled(tmp_path, '2026-10-10'), allow_old=True)
    chatbots.import_collection(filled(tmp_path, '2026-11-14', edit=lambda d: d['chatbots'][0]['answers'][0].update(screenshot='shot.png')))
    with pytest.raises(RuntimeError, match='not scripted-demo-only'):
        snapshot.build(tmp_path / 'unreviewed', archive_only=True)                            # hand-entered rows are recorded data
    out = tmp_path / 'published'
    manifest = snapshot.export_published(out, archive_only=True)                              # an archive with ONLY hand-entered replies exports
    assert sorted(p.name for p in out.iterdir()) == ['MANIFEST.json', 'archive.json']
    assert manifest['schema'] == 'live-genesis-published/5' and manifest['archive']['runs'] == 0
    assert manifest['archive']['chatbots'] == {'runs': 2, 'answers': 32, 'apps': ['chatgpt', 'gemini', 'grok', 'copilot']}
    exported = json.loads((out / 'archive.json').read_text(encoding='utf-8'))
    assert exported == json.loads(json.dumps(archive.public_payload())) and exported['schema'] == 'live-genesis-archive/4'
    assert exported['demo_only'] is False and exported['answers'] == []
    block = exported['chatbots']
    assert set(block) == set(chatbots.BLOCK_KEYS) and set(block['answers'][0]) == set(chatbots.ANSWER_KEYS_PUBLIC)
    assert set(block['runs'][0]) == set(chatbots.RUN_KEYS) and set(block['runs'][0]['conditions']['grok']) == set(chatbots.CONDITION_KEYS)
    shots = [a['screenshot_hash'] for a in block['answers'] if a['screenshot_hash']]
    assert shots == [hashlib.sha256(b'PLACEHOLDER image bytes').hexdigest()]                  # the hash; never the picture or its path
    raw = (out / 'archive.json').read_text(encoding='utf-8')
    assert 'shot.png' not in raw and str(tmp_path) not in raw and 'file_sha256' not in raw and 'normalised' not in raw
    assert 'ENTERED BY HAND' in snapshot._describe(manifest) and 'current terms' in snapshot._describe(manifest)
    with pytest.raises(RuntimeError, match='not approved'):
        snapshot.check_published(out)
    snapshot.approve_published(out, confirmed=True)
    assert snapshot.check_published(out)['reviewed'] is True
    monkeypatch.setenv('LIVE_DB', str(tmp_path / 'no-such.sqlite'))
    site = tmp_path / 'site'
    snapshot.build_from_data(site, out, archive_only=True)
    names = {p.relative_to(site).as_posix() for p in site.rglob('*') if p.is_file()}
    assert {'archive/chatbots.html', 'archive/card/Q11-chatbots.html', 'archive/card/T002-copilot.html'} <= names
    page = (site / 'archive' / 'q' / 'Q11.html').read_text(encoding='utf-8')
    assert 'PLACEHOLDER reply 1 of chatgpt.' in page and 'screenshot kept' in page and 'scripted' not in page.lower()


def test_check_refuses_a_tampered_chat_track(chat, tmp_path, monkeypatch):
    two_dates(tmp_path)
    out = tmp_path / 'published'

    def tampered(edit, why):
        snapshot.export_published(out, archive_only=True, replace=True)
        snapshot.approve_published(out, confirmed=True)
        retarget(out, edit)
        with pytest.raises(RuntimeError, match=why):
            snapshot.check_published(out)
    block = lambda d: d['chatbots']                                   # noqa: E731
    tampered(lambda d: block(d)['answers'][0].update(question='Q12'), 'names an unknown question|not in its run')
    tampered(lambda d: block(d)['runs'][0]['questions'].remove('Q11'), "not in its run's recorded list")
    tampered(lambda d: block(d)['runs'][0]['conditions'].pop('chatgpt'), 'whose conditions its run did not record')
    tampered(lambda d: block(d)['runs'][0]['conditions']['grok'].pop('web_search'), 'conditions are missing or incomplete')
    tampered(lambda d: block(d)['runs'][0]['conditions']['grok'].update(fresh_chat=False), 'a value that is not allowed')
    tampered(lambda d: block(d)['runs'][0]['conditions']['grok'].update(account='team'), 'a value that is not allowed')
    tampered(lambda d: (block(d)['apps'][0].update(label='claude'), [a.update(chatbot='claude') for a in block(d)['answers'] if a['chatbot'] == 'chatgpt'],
                        [run['conditions'].update(claude=run['conditions'].pop('chatgpt')) for run in block(d)['runs']]),
             'chat app claude is not in chatbots-config.json')
    tampered(lambda d: block(d)['apps'][0].update(name='Another name'), 'does not carry the name, maker and attribution')
    tampered(lambda d: block(d)['apps'][2].update(attribution=''), 'does not carry the name, maker and attribution')
    tampered(lambda d: block(d)['answers'][0].update(text='x' * 2001), 'longer than the archive stores')
    tampered(lambda d: block(d)['answers'][0].update(text='Write to someone.real@example.org'), 'Content guards matched')
    tampered(lambda d: block(d)['runs'][0]['conditions']['grok'].update(note='Write to someone.real@example.org'), 'Content guards matched')
    tampered(lambda d: block(d)['answers'][0].update(screenshot_hash='/home/owner/shot.png'), 'not given as a sha256 hash')
    tampered(lambda d: block(d)['answers'][0].update(screenshot_path='shot.png'), 'unexpected key screenshot_path')
    tampered(lambda d: block(d)['runs'][0].update(file='collect.json'), 'unexpected key file')
    tampered(lambda d: block(d).update(extra=[]), 'unexpected key extra')
    tampered(lambda d: (block(d)['runs'][0].update(demo=True), block(d)['runs'][1].update(demo=True),
                        [a.update(source='demo') for a in block(d)['answers']]), 'scripted chat-app answers')
    tampered(lambda d: d.update(schema='live-genesis-archive/3'), 'schema marker does not fit')
    tampered(lambda d: d.pop('chatbots'), 'schema marker does not fit')
    tampered(lambda d: d.update(demo_only=True), 'demo_only does not fit|scripted demo data only')
    tampered(lambda d: d['trending'].update(locked=False, version=0, locked_at=None), 'not locked')
    tampered(lambda d: block(d)['runs'].append(dict(block(d)['runs'][0], number=9)), 'two runs on one date')
    # The manifest's own count of the track is checked too.
    snapshot.export_published(out, archive_only=True, replace=True)
    snapshot.approve_published(out, confirmed=True)
    manifest = json.loads((out / 'MANIFEST.json').read_text(encoding='utf-8'))
    manifest['archive']['chatbots']['answers'] = 1
    (out / 'MANIFEST.json').write_text(json.dumps(manifest, indent=1))
    with pytest.raises(RuntimeError, match='counts in the manifest do not match'):
        snapshot.check_published(out)


def test_a_payload_without_the_track_is_exactly_what_it_was(ready, monkeypatch):
    core.seed_agents()
    archive.lock()
    as_real(monkeypatch, 'Seven.')
    archive.run_archive(max_usd=1)
    payload = archive.public_payload()
    assert 'chatbots' not in payload and payload['schema'] == 'live-genesis-archive/3' and archive.payload_problem(payload) is None
    assert 'chatbots' not in snapshot._archive_counts(payload)
    assert archive.payload_problem({**payload, 'chatbots': {'apps': [], 'questions': [], 'runs': [], 'answers': []}}) == \
        'the schema marker does not fit the file (/4 exactly when it holds the chat-app track)'
    pages = archive_pages.render_site(payload)
    assert 'No chat app has been asked yet.' in pages['chatbots.html'] and not any('-' in name for name in pages)
    for name in REAL_NAMES:
        assert name not in pages['chatbots.html']                    # the checked-in names appear only with real collected replies


# ---- CLI, capture script, docs --------------------------------------------------------------------------------

def test_cli_template_dry_run_import_and_status(chat, tmp_path, monkeypatch, capsys):
    target = tmp_path / 'cli.json'
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-template', '--out', str(target), '--date', '2026-11-13')
    assert code == 0 and 'Template written to' in out and 'BY HAND' in out and 'Never use a program' in out
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-import', str(target))
    assert code == 1 and 'was refused; nothing was stored' in out and 'fresh_chat must be true' in out
    path = filled(tmp_path, '2026-11-13', reply=lambda app, q: 'PLACEHOLDER-NEVER-PRINTED reply.')
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-import', str(path), '--dry-run')
    assert code == 0 and 'DRY RUN' in out and 'Nothing was stored' in out and 'NEVER-PRINTED' not in out and '"characters": 32' in out
    assert rows('SELECT * FROM archive_chatbot_runs') == []
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-import', str(path))
    assert code == 0 and 'Stored as hand-entered run 1, collected 2026-11-13' in out and 'NEVER-PRINTED' not in out
    assert 'may only be added to' in out and 'cannot be verified' in out and '--export --archive-only' in out
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-import', str(path))
    assert code == 1 and 'already has a hand-entered run dated 2026-11-13' in out
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-import', str(filled(tmp_path, '2026-10-01')))
    assert code == 1 and '--allow-old' in out
    code, out = run_cli(monkeypatch, capsys, 'archive-status')
    status = json.loads(out)['chatbots']
    assert status['config_file']['status'] == 'draft' and status['config_file']['chatbots'] == ['chatgpt', 'gemini', 'grok', 'copilot']
    assert status['runs'] == [{'number': 1, 'kind': 'manual', 'collected_on': '2026-11-13', 'imported_at': status['runs'][0]['imported_at'], 'answers': 16}]


def test_import_needs_no_key_no_optin_and_no_models(chat, tmp_path, monkeypatch):
    for name in ('OPENAI_API_KEY', 'LIVE_ALLOW_PAID_CALLS', 'LIVE_ARCHIVE_MODELS', 'LIVE_INPUT_USD_PER_M', 'LIVE_OUTPUT_USD_PER_M'):
        monkeypatch.delenv(name, raising=False)
    assert chatbots.import_collection(filled(tmp_path))['run'] == 1


def test_capture_script_takes_chat_card_names_and_docs_describe_the_track():
    script = (REPO / 'scripts' / 'capture-cards.mjs').read_text(encoding='utf-8')
    name = re.search(r'const ID = /(.+?)/;', script).group(1)
    pattern = re.compile(name.replace('[0-9]', r'\d'))
    for good in ('Q11', 'T001', 'Q11-chatbots', 'T002-grok', 'Q03-chat-app-a'):
        assert pattern.fullmatch(good), good
    for bad in ('Q1', 'Q11-', 'Q11-Grok', '../Q11', 'Q11-chatbots.html', 'Q11--x' * 5):
        assert not pattern.fullmatch(bad), bad
    assert 'never opens a chat app' in script and 'data.chatbots' in script
    doc = (REPO / 'docs' / 'ANSWERS-OVER-TIME.md').read_text(encoding='utf-8')
    section = doc.split('## The chat-app track', 1)[1].split('\n## ', 1)[0]
    for phrase in ('archive-chatbot-template', 'archive-chatbot-import', '--dry-run', '--allow-old', 'entered by hand', 'terms',
                   'live-genesis-archive/4', 'Temporary Chat', 'Private Chat', 'personal use', 'not verified', 'Written with Grok',
                   'answer_file', 'conditions changed', 'chat-app-a', '--export --archive-only', 'Back up first'):
        assert phrase in section, phrase
    social = (REPO / 'docs' / 'SOCIAL-POSTS.md').read_text(encoding='utf-8')
    part = social.split('## Chat-app cards', 1)[1]
    for phrase in ('typed in by hand on', 'screenshot', 'scripted demo', 'logo', 'endorse', 'Q11-chatbots', '65', '1440'):
        assert phrase in part, phrase
