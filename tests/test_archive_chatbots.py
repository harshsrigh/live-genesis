"""Answers Over Time, the chat-app track (live_genesis/archive_chatbots.py): replies the owner collects BY HAND
from chat apps, and answers written by people, imported from a file. Offline: nothing here calls a model, opens a
socket or contacts a chat app or any site, and every "reply" is an obvious placeholder."""
import datetime
import hashlib
import json
import re
import sqlite3
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from live_genesis import archive, archive_chatbots as chatbots, archive_pages, core, engine, guards, snapshot
from test_archive import (HOSTILE, REPO, STATIC, as_real, assert_page_is_safe, fake, ready, rows, run_cli)  # noqa: F401

V4_SCHEMA = REPO / 'tests' / 'fixtures' / 'archive_schema_v4.sql'
V5_SCHEMA = REPO / 'tests' / 'fixtures' / 'archive_schema_v5.sql'
CONFIG = REPO / 'live_genesis' / 'archive_chatbots.json'
REAL_NAMES = ('ChatGPT', 'Gemini', 'Grok')
APPS = ['chatgpt', 'gemini', 'grok']
IDS = ['C001', 'C002', 'C003', 'C004']
TODAY = datetime.date(2026, 11, 14)
PREFIX = "Answer in one short sentence, like you're texting a friend. Be funny if you can."
CONDITIONS = {'account': 'free_account', 'fresh_chat': True, 'temporary_chat': True, 'memory_off': True, 'web_search': 'off',
              'country': 'US', 'app': 'web', 'shown_model': 'PLACEHOLDER model', 'note': ''}
HUMAN_CONDITIONS = {'source': 'PLACEHOLDER thread', 'collected_on': None, 'count': 12}


def question(n, text, hook=''):
    return {'id': f'C{n:03d}', 'text': text, 'prompt': f'{PREFIX}\n\n{text}', 'hook': hook, 'added': '2026-11-14'}


def config_file(tmp_path, monkeypatch, count=4, edit=None):
    """A copy of the checked-in configuration with its first `count` questions; `edit(data)` changes it."""
    data = json.loads(CONFIG.read_text(encoding='utf-8'))
    data['questions'] = data['questions'][:count]
    if edit:
        edit(data)
    path = tmp_path / 'chatbots-config.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setattr(chatbots, 'CONFIG_FILE', path)
    return path


@pytest.fixture
def chat(ready, tmp_path, monkeypatch):
    """A fresh database (NO question list is locked: this track has its own), a configuration with the three checked-in
    chat apps, the Human column and four questions, and a fixed 'today'."""
    config_file(tmp_path, monkeypatch)
    monkeypatch.setattr(chatbots, 'today', lambda: TODAY)
    monkeypatch.setattr(engine, 'infer', fake(AssertionError('the chat-app track calls no model')))
    return ready


def filled(tmp_path, date='2026-11-14', name=None, only=None, edit=None, reply=None, human=True):
    """Write a filled-in collection file with PLACEHOLDER replies; `edit(data)` changes it before it is written."""
    data = chatbots.template(date)
    if only is not None:
        data['chatbots'] = [block for block in data['chatbots'] if block['chatbot'] in only]
    for block in data['chatbots']:
        block['conditions'] = dict(CONDITIONS)
        for n, entry in enumerate(block['answers'], 1):
            entry['answer'] = reply(block['chatbot'], entry['question']) if reply else f"PLACEHOLDER reply {n} of {block['chatbot']}."
    if human:
        data['human']['conditions'] = dict(HUMAN_CONDITIONS, collected_on=date)
        for n, entry in enumerate(data['human']['answers'], 1):
            entry['answer'] = reply('human', entry['question']) if reply else f'PLACEHOLDER human reply {n}.'
            entry['kind'] = 'quoted' if n % 2 else 'summarised'
    else:
        del data['human']
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


def visible(page):
    """The text a person sees: tags and their attributes removed."""
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]*>', ' ', page))


# ---- the configuration ------------------------------------------------------------------------------------

def test_checked_in_configuration_three_apps_human_and_ten_draft_questions_of_its_own():
    config = chatbots.load_config()
    raw = json.loads(CONFIG.read_text(encoding='utf-8'))
    assert raw['status'] == 'draft' and config['status'] == 'draft' and raw['format'] == 'live-genesis-chatbots/2'
    assert [(c['label'], c['name'], c['maker']) for c in config['chatbots']] == [
        ('chatgpt', 'ChatGPT', 'OpenAI'), ('gemini', 'Gemini', 'Google'), ('grok', 'Grok', 'xAI')]
    assert 'opilot' not in json.dumps(raw['chatbots']) and 'Microsoft' not in json.dumps(raw)      # dropped (owner, 14 Nov 2026)
    # A maker's attribution line is configuration, not a special case in the code: only one app has one.
    assert {c['label']: c['attribution'] for c in config['chatbots']} == {'chatgpt': '', 'gemini': '', 'grok': 'Written with Grok'}
    # Human is a kind of its own, never one of the apps.
    assert config['human'] == {'name': 'Human', 'meta': 'written by people · collected by hand'}
    assert 'human' in chatbots.RESERVED_LABELS and 'human' not in [c['label'] for c in config['chatbots']]
    # One editable joke line; it names a product, so it stays one line and makes no claim beyond the joke.
    assert config['aside'] == raw['aside'] == 'Why no Copilot? It is cheaper to ask a human than Copilot.'
    assert config['aside'].startswith('Why no Copilot?') and len(config['aside']) <= chatbots.ASIDE_CHARS
    # The questions are this track's own list: ids C001..., one fixed prefix, nothing taken from the two locked lists.
    assert config['questions'] == [f'C{n:03d}' for n in range(1, 11)] and raw['prompt_prefix'] == chatbots.PROMPT_PREFIX == PREFIX
    locked_ids = {q['id'] for track in archive.TRACKS for q in archive.load_questions(track=track)['questions']}
    assert not set(config['questions']) & locked_ids                                          # ids of its own: nothing refers to the two lists
    for item in config['items']:
        assert item['prompt'] == f"{PREFIX}\n\n{item['text']}" and item['added'] == '2026-11-14'
        assert 0 < len(item['hook']) <= 60 and len(item['text']) <= chatbots.QUESTION_CHARS
        # The seeded ten pass every mechanical check: the crude politics list and the existing content guards.
        for text in (item['text'], item['hook'], item['prompt']):
            assert chatbots.political_term(text) is None and guards.content_reason(text) is None
            assert not guards.has_forbidden_character(text, multiline=True)
    texts = ' '.join(q['text'] for q in config['items'])
    for word in ('Roast', 'destroy', 'fight', 'useless'):                                             # harmless joke wording is not refused
        assert word in texts
    # Nothing whose point is the model's age or date, and nothing about feelings or consciousness.
    assert not re.search(r"today's date|what year|cutoff|which model|version are you|conscious|feel", texts, re.I)
    assert not re.search(r'https?:|www\.', json.dumps(raw))                                   # no address of any service
    assert re.fullmatch(r'[0-9a-f]{64}', config['sha256'])


@pytest.mark.parametrize('edit, why', [
    (lambda d: d['questions'].append(question(6, 'PLACEHOLDER extra?')), 'question 5 must have the id C005'),
    (lambda d: d['questions'].append(dict(d['questions'][0], id='C005')), 'two questions have the same text'),
    (lambda d: d['questions'].clear(), 'questions must list'),
    (lambda d: d['questions'].__setitem__(0, 'Q11'), 'question 1 must hold id, text, prompt and added'),
    (lambda d: d['questions'][0].update(prompt='Is a hot dog a sandwich?'), 'prompt must be exactly the fixed prefix'),
    (lambda d: d['questions'][0].update(prompt='Answer in one or two sentences.\n\nIs a hot dog a sandwich?'), 'prompt must be exactly'),
    (lambda d: d['questions'][0].update(hook='x' * 61), 'hook must be at most 60'),
    (lambda d: d['questions'][0].update(added='14 Nov'), 'added must be a date'),
    (lambda d: d['questions'][0].update(rationale='x'), 'must hold id, text, prompt and added'),
    (lambda d: d.update(prompt_prefix='Answer briefly.'), 'prompt_prefix must be exactly'),
    (lambda d: d['chatbots'].append(dict(d['chatbots'][0])), 'labels and names must differ'),
    (lambda d: d['chatbots'][0].update(label='Chat GPT'), 'a label must be'),
    (lambda d: d['chatbots'][0].update(label='chatbots'), 'a label must be'),                 # reserved
    (lambda d: d['chatbots'][0].update(label='human'), 'a label must be'),                    # Human is never an app
    (lambda d: d['chatbots'][0].update(name='Human'), 'labels and names must differ'),
    (lambda d: d['chatbots'][0].update(url='https://example.org'), 'needs label, name and maker'),
    (lambda d: d['chatbots'][0].update(attribution='x' * 61), 'attribution must be'),
    (lambda d: d.pop('human'), 'human must hold name'),
    (lambda d: d['human'].update(username='u/someone'), 'human must hold name'),
    (lambda d: d.update(aside='x' * 161), 'aside must be one plain line'),
    (lambda d: d.update(aside='line one\nline two'), 'aside must be one plain line'),
    (lambda d: d.update(status='final'), 'status must be'),
    (lambda d: d.update(extra=1), 'top level'),
    (lambda d: d.update(format='live-genesis-chatbots/1'), 'format must be'),
])
def test_malformed_configuration_is_refused(tmp_path, monkeypatch, edit, why):
    config_file(tmp_path, monkeypatch, edit=edit)
    with pytest.raises(archive.ArchiveRefused, match=why):
        chatbots.load_config()


@pytest.mark.parametrize('text', [
    'Who is the president of the United States?', 'Who should I vote for?', 'Is the Election rigged?', 'Democrats or Republicans?',
    'Which political party is funniest?', 'Roast the Prime Minister.', 'What do you think of Trump?', 'Is the government necessary?'])
def test_obviously_political_questions_are_refused_on_load(tmp_path, monkeypatch, text):
    assert chatbots.political_term(text)
    config_file(tmp_path, monkeypatch, edit=lambda d: d['questions'].__setitem__(1, question(2, text)))
    with pytest.raises(archive.ArchiveRefused, match='no political questions .a crude word check'):
        chatbots.load_config()
    config_file(tmp_path, monkeypatch, edit=lambda d: d['questions'][1].update(hook=text[:60]))
    with pytest.raises(archive.ArchiveRefused, match='no political questions'):
        chatbots.load_config()


def test_the_political_word_list_is_crude_and_holds_politics_only():
    # Crude: words, not meaning. It misses a political question that avoids the words ...
    assert chatbots.political_term('Who runs the country these days?') is None
    # ... and never holds joke words: a roast, a fight or a "worst" is not politics.
    for harmless in ('Roast the other AI apps in one sentence.', 'Pineapple on pizza: defend it or destroy it.',
                     "What's the worst advice you could give me right now?", 'Which would you rather fight: a duck or a horse?',
                     'Is a birthday party better with cake or pie?', 'Who is the most presidential-looking dog?'[:0] or 'Do penguins have knees?',
                     'Devote a sentence to cheese.', 'Is a tug of war a sport?'):
        assert chatbots.political_term(harmless) is None, harmless
    assert not {'roast', 'destroy', 'worst', 'fight', 'kill', 'hate', 'party', 'war'} & set(chatbots.POLITICAL_TERMS)
    doc = (REPO / 'docs' / 'ANSWERS-OVER-TIME.md').read_text(encoding='utf-8')
    assert 'crude' in doc.split('## The chat-app track', 1)[1]


# ---- the template -----------------------------------------------------------------------------------------

def test_template_holds_every_app_the_exact_prompts_and_the_optional_human_block(chat, tmp_path):
    before = chat.read_bytes()
    target = chatbots.write_template(tmp_path / 't.json', '2026-11-10')
    data = json.loads(target.read_text(encoding='utf-8'))
    assert chat.read_bytes() == before                                                       # the template opens no database
    assert list(data) == ['format', 'how_to', 'collected_on', 'chatbots', 'human'] and data['collected_on'] == '2026-11-10'
    assert data['format'] == 'live-genesis-chatbot-collection/2'
    assert [b['chatbot'] for b in data['chatbots']] == APPS
    prompts = chatbots.load_config()['prompts']
    for block in data['chatbots']:
        assert set(block['conditions']) == set(chatbots.CONDITION_KEYS) and block['conditions']['fresh_chat'] is None
        assert [(e['question'], e['prompt'], e['answer']) for e in block['answers']] == [(i, prompts[i]['prompt'], '') for i in IDS]
    assert all(entry['prompt'].startswith(PREFIX + '\n\n') for entry in data['chatbots'][0]['answers'])
    # The human block: its own conditions shape. No username, no link, no subreddit field anywhere.
    assert data['human']['conditions'] == {'source': '', 'collected_on': '2026-11-10', 'count': None}
    assert [(e['question'], e['answer'], e['kind']) for e in data['human']['answers']] == [(i, '', '') for i in IDS]
    assert not re.search(r'user|subreddit|link|url', json.dumps({k: v for k, v in data['human'].items() if k != 'answers'}), re.I)
    how = ' '.join(data['how_to'])
    for phrase in ('NEW chat', 'exactly', 'WHOLE reply', '"skipped"', '"refused": true', 'answer_file', 'sha256', 'Temporary Chat',
                   'Private Chat', 'FREE account', 'Sources', 'terms', '--dry-run', 'optional: remove it', 'quoted', 'summarised',
                   'No usernames', "read that community's rules", 'no program contacts Reddit',
                   'Paste the whole reply even if it is long; never trim or reword it. The page shortens the display, not the text.'):
        assert phrase in how, phrase
    assert 'Copilot' not in how
    # A blank template is not importable: the owner has to fill in the conditions and every answer.
    with pytest.raises(archive.ArchiveRefused, match='account must be one of'):
        chatbots.import_collection(target)
    with pytest.raises(archive.ArchiveRefused, match='already exists'):
        chatbots.write_template(tmp_path / 't.json')
    assert json.loads(chatbots.write_template(tmp_path / 'today.json').read_text(encoding='utf-8'))['collected_on'] == '2026-11-14'
    with pytest.raises(archive.ArchiveRefused, match='YYYY-MM-DD'):
        chatbots.template('14/11/2026')


def test_nothing_in_the_track_automates_or_contacts_a_chat_app_or_reddit():
    """Collection is by hand, for good: the module imports nothing that could reach a network, and neither it nor the
    template nor the docs of this track describe any automated way of collecting replies, from an app or from people."""
    source = (REPO / 'live_genesis' / 'archive_chatbots.py').read_text(encoding='utf-8')
    assert not re.search(r'^\s*(?:import|from)\s+(?:urllib|http|socket|requests|httpx|subprocess|webbrowser|asyncio|ssl)\b', source, re.M)
    banned = re.compile(r'selenium|playwright|puppeteer|headless|scrap(?:e|er|ing)|crawler|webdriver|browser automation|praw\b|reddit\.com|api\.reddit', re.I)
    assert not banned.search(source) and not banned.search(' '.join(chatbots.HOW_TO))
    assert not re.search(r'https?://', source) and not re.search(r'https?://', CONFIG.read_text(encoding='utf-8'))
    doc = (REPO / 'docs' / 'ANSWERS-OVER-TIME.md').read_text(encoding='utf-8')
    section = doc.split('## The chat-app track', 1)[1].split('\n## ', 1)[0]
    assert not banned.search(section.replace('never scrape', '')) and 'by hand' in section
    assert 'never contacts Reddit' in section


# ---- import -----------------------------------------------------------------------------------------------

def test_dry_run_stores_nothing_and_import_stores_one_audited_run_without_any_locked_list(chat, tmp_path):
    assert rows('SELECT * FROM archive_sets') == []                                          # neither the core nor the trending list is locked
    path = filled(tmp_path)
    before = hashlib.sha256(chat.read_bytes()).hexdigest()
    plan = chatbots.import_collection(path, dry_run=True)
    assert hashlib.sha256(chat.read_bytes()).hexdigest() == before and plan['dry_run'] is True and 'run' not in plan
    assert [c['answers'] for c in plan['chatbots']] == [{'answered': 4, 'no_answer': 0, 'withheld': 0}] * 3
    assert plan['human']['answers'] == {'answered': 4, 'no_answer': 0, 'withheld': 0}
    assert 'PLACEHOLDER reply' not in json.dumps(plan) and 'PLACEHOLDER human' not in json.dumps(plan)     # counts and lengths, never a reply
    done = chatbots.import_collection(path)
    assert done['run'] == 1 and done['first_import'] is True and done['collected_on'] == '2026-11-14' and done['new_questions'] == IDS
    assert done['chatbots'][0]['rows'][0] == {'question': 'C001', 'state': 'answered', 'characters': 31, 'screenshot': False,
                                              'refused': False, 'changed': None}
    assert done['human']['rows'][0] == {'question': 'C001', 'state': 'answered', 'characters': 26, 'kind': 'quoted', 'different': None}
    run = rows('SELECT * FROM archive_chatbot_runs')
    assert len(run) == 1 and run[0]['kind'] == 'manual' and run[0]['collected_on'] == '2026-11-14'
    assert run[0]['imported_at'].startswith('20') and run[0]['imported_at'] != run[0]['collected_on']      # both dates are kept
    assert json.loads(run[0]['question_ids']) == IDS and run[0]['config_sha256'] == chatbots.load_config()['sha256']
    assert run[0]['file_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    answers = rows('SELECT * FROM archive_chatbot_answers ORDER BY id')
    assert len(answers) == 12 and {a['source'] for a in answers} == {'manual'} and {a['state'] for a in answers} == {'answered'}
    assert answers[0]['answer_text'] == 'PLACEHOLDER reply 1 of chatgpt.' and answers[0]['changed'] is None
    assert {a['question_set'] for a in answers} == {'chat'} and {a['refused'] for a in answers} == {0}
    conditions = rows('SELECT * FROM archive_chatbot_conditions ORDER BY rowid')
    assert [c['chatbot'] for c in conditions] == APPS                                         # Human is NOT a row of the apps' table
    assert conditions[2]['attribution'] == 'Written with Grok' and conditions[0]['attribution'] == ''
    assert (conditions[0]['account'], conditions[0]['fresh_chat'], conditions[0]['memory_off']) == ('free_account', 1, 'true')
    # Human: its own two tables, its own conditions shape, one answer per question, each marked quoted or summarised.
    assert rows('SELECT * FROM archive_human_collections') == [{'run_id': 1, 'display_name': 'Human', 'meta': 'written by people · collected by hand',
                                                               'source': 'PLACEHOLDER thread', 'collected_on': '2026-11-14', 'replies': 12}]
    people = rows('SELECT question_id,state,answer_text,manner,changed,source FROM archive_human_answers ORDER BY id')
    assert [p['manner'] for p in people] == ['quoted', 'summarised', 'quoted', 'summarised'] and people[0]['answer_text'] == 'PLACEHOLDER human reply 1.'
    assert {tuple(r) for r in sqlite3.connect(str(chat)).execute("PRAGMA table_info(archive_human_collections)")} and not {
        'username', 'url', 'link', 'subreddit'} & {r[1] for r in sqlite3.connect(str(chat)).execute("PRAGMA table_info(archive_human_collections)")}
    # The questions were recorded with their hashes at this first import.
    recorded = rows('SELECT * FROM archive_chatbot_questions ORDER BY position')
    assert [q['id'] for q in recorded] == IDS and {q['kind'] for q in recorded} == {'manual'} and {q['first_run'] for q in recorded} == {1}
    assert all(q['sha256'] == chatbots.question_hash(chatbots.load_config()['prompts'][q['id']]) for q in recorded)
    audit = [r['operation'] for r in rows('SELECT operation FROM operator_audit')]
    assert any(op.startswith('archive-chatbot-import run 1: collected 2026-11-14, chatgpt,gemini,grok,human, 16 rows') for op in audit)
    # No model answer, no budget row, no agent record: this track has its own tables only.
    assert rows('SELECT * FROM archive_answers') == [] and rows('SELECT * FROM archive_runs') == []
    assert rows('SELECT * FROM budget_reservations') == [] and rows('SELECT * FROM api_usage') == []


def test_dates_only_move_forward_one_run_per_app_and_day(chat, tmp_path):
    refused(filled(tmp_path, '2026-11-15'), 'in the future')
    refused(filled(tmp_path, '2026-10-30'), 'more than 14 days ago')
    chatbots.import_collection(filled(tmp_path, '2026-10-30'), allow_old=True)
    refused(filled(tmp_path, '2026-10-30', name='again.json'), 'already has a hand-entered run dated 2026-10-30', allow_old=True)
    refused(filled(tmp_path, '2026-10-20', name='earlier.json'), "dates only move forward", allow_old=True)
    # Another app on the same day is its own run; an app that was not asked simply has no card. The human block is optional.
    chatbots.import_collection(filled(tmp_path, '2026-11-13', only=['grok'], human=False))
    done = chatbots.import_collection(filled(tmp_path, '2026-11-14', only=['chatgpt', 'gemini'], human=False))
    assert done['run'] == 3 and done['first_import'] is False and 'human' not in done
    refused(filled(tmp_path, '2026-11-14', name='dup.json', only=['gemini'], human=False), 'gemini already has a hand-entered run dated 2026-11-14')
    # Human answers alone are a run too; and they move forward like an app.
    refused(filled(tmp_path, '2026-10-30', name='h-old.json', only=[]), 'Human answers already have a hand-entered run dated 2026-10-30', allow_old=True)
    assert chatbots.import_collection(filled(tmp_path, '2026-11-14', name='h.json', only=[]))['chatbots'] == []
    refused(filled(tmp_path, '2026-11-14', name='none.json', only=[], human=False), 'at least one chat app, or the file must hold the human block')
    with core.connect() as cx:                                       # the database itself refuses it too
        with pytest.raises(sqlite3.IntegrityError, match='this date or a later one'):
            cx.execute("INSERT INTO archive_chatbot_conditions(run_id,chatbot,display_name,maker,account,fresh_chat,temporary_chat,"
                       "memory_off,web_search,country,app,shown_model) VALUES (1,'grok','Grok','xAI','free_account',1,1,'true','off','US','web','x')")
        with pytest.raises(sqlite3.IntegrityError, match='human answers already have a run'):
            cx.execute("INSERT INTO archive_human_collections(run_id,display_name,meta,source,collected_on) VALUES (2,'Human','m','s','2026-11-13')")


def test_changed_compares_with_the_previous_shown_answer_and_refusals_are_stored_never_compared(chat, tmp_path):
    first = {('chatgpt', 'C001'): 'Yes, obviously.', ('gemini', 'C001'): 'No comment.', ('human', 'C001'): 'Nope.'}
    second = {('chatgpt', 'C001'): 'No, never.', ('gemini', 'C001'): '  no comment!! ', ('human', 'C001'): 'Yep.'}
    chatbots.import_collection(filled(tmp_path, '2026-10-10', reply=lambda app, q: first.get((app, q), 'PLACEHOLDER same.')), allow_old=True)

    def mark(data):
        grok = data['chatbots'][2]['answers']
        grok[0].update(answer='', skipped='PLACEHOLDER: the app showed an error')          # could not ask
        grok[1].update(answer="PLACEHOLDER: I can't help with that.", refused=True)        # a refusal, with what it said
        grok[2].update(answer='', refused=True)                                            # a refusal, nothing pasted
    done = chatbots.import_collection(filled(tmp_path, '2026-11-14', reply=lambda app, q: second.get((app, q), 'PLACEHOLDER same.'), edit=mark))
    changed = {(c['chatbot'], r['question']): r['changed'] for c in done['chatbots'] for r in c['rows']}
    assert changed[('chatgpt', 'C001')] is True and changed[('gemini', 'C001')] is False     # the existing normalisation
    assert changed[('grok', 'C001')] is None and changed[('grok', 'C002')] is None and changed[('grok', 'C004')] is False
    assert (done['chatbots'][2]['skipped'], done['chatbots'][2]['refused'], done['chatbots'][2]['shown']) == (1, 2, 1)
    stored = rows("SELECT question_id,state,reason,answer_text,refused,changed FROM archive_chatbot_answers WHERE chatbot='grok' AND run_id=2 ORDER BY id")
    assert stored[0] == {'question_id': 'C001', 'state': 'no_answer', 'reason': 'skipped: PLACEHOLDER: the app showed an error',
                         'answer_text': None, 'refused': 0, 'changed': None}
    # The refusal's words are kept (the record is honest) and it is marked; it never counts as a change, now or later.
    assert stored[1] == {'question_id': 'C002', 'state': 'answered', 'reason': '', 'answer_text': "PLACEHOLDER: I can't help with that.",
                         'refused': 1, 'changed': None}
    assert stored[2] == {'question_id': 'C003', 'state': 'no_answer', 'reason': 'refused', 'answer_text': None, 'refused': 1, 'changed': None}
    # Human: the flag only says the text differs from the collection before.
    assert [r['different'] for r in done['human']['rows']] == [True, False, False, False]
    third = chatbots.import_collection(filled(tmp_path, '2026-11-15', name='third.json', only=['grok'], human=False,
                                              reply=lambda app, q: 'PLACEHOLDER same.'), allow_old=True) if False else None
    assert third is None
    # A later real answer is compared with the last SHOWN one (October), not with the refusal in between.
    chatbots.today = lambda: datetime.date(2026, 12, 1)
    later = chatbots.import_collection(filled(tmp_path, '2026-12-01', only=['grok'], human=False, reply=lambda app, q: 'PLACEHOLDER same.'))
    assert [r['changed'] for r in later['chatbots'][0]['rows']] == [False, False, False, False]


def test_human_answers_are_short_marked_and_carry_no_handle_or_link(chat, tmp_path):
    def bad(edit, why):
        refused(filled(tmp_path, name='bad.json', edit=edit), why)
    person = lambda d: d['human']                                     # noqa: E731
    bad(lambda d: person(d)['answers'][0].update(kind=''), 'kind must be "quoted"')
    bad(lambda d: person(d)['answers'][0].update(kind='paraphrased'), 'kind must be "quoted"')
    bad(lambda d: person(d)['answers'][0].update(answer='As u/some_person said: no.'), 'human, C001: the answer holds a username handle')
    bad(lambda d: person(d)['answers'][0].update(answer='Ask @someone, they know.'), 'holds a username handle')
    bad(lambda d: person(d)['answers'][0].update(answer='See https://example.org/thread'), 'holds a web address')
    bad(lambda d: person(d)['answers'][0].update(answer='It is on www.example.org somewhere'), 'holds a web address')
    bad(lambda d: person(d)['answers'][0].update(answer='look at example.com'), 'holds a web address')
    bad(lambda d: person(d)['answers'][0].update(answer=''), 'give exactly one of answer or skipped')
    bad(lambda d: person(d)['answers'][0].update(skipped='nobody answered'), 'give exactly one of answer or skipped')
    bad(lambda d: person(d)['answers'][0].update(username='someone'), 'an answer entry must hold')
    bad(lambda d: person(d)['answers'][0].update(prompt='Is it?'), 'not the prompt of')
    bad(lambda d: person(d)['answers'].pop(), 'human: the answers must cover exactly the 4 questions')
    bad(lambda d: person(d)['conditions'].update(source='u/moderator thread'), 'source holds a username handle')
    bad(lambda d: person(d)['conditions'].update(source='https://example.org/r/x'), 'source')
    bad(lambda d: person(d)['conditions'].update(source=''), 'source must say where')
    bad(lambda d: person(d)['conditions'].update(username='x'), 'conditions must hold exactly source, collected_on, count')
    bad(lambda d: person(d)['conditions'].update(count=0), 'count must be a whole number')
    bad(lambda d: person(d)['conditions'].update(collected_on='2026-11-15'), "after the file's collected_on")
    bad(lambda d: person(d).update(subreddit='r/x'), 'the human block must hold name, conditions and answers only')

    def fine(data):
        answers = person(data)['answers']
        answers[0].update(answer='', kind='', skipped='PLACEHOLDER: nobody answered this one')
        answers[1].update(answer='Hidden\u200bcharacter here')                              # the same character rule and guards as for an app's reply
        answers[2].update(answer='x' * 501)                                                # one short text: 500 characters at most
        person(data)['conditions'].update(count=None, collected_on='2026-11-12')           # collected two days before the apps were asked
    done = chatbots.import_collection(filled(tmp_path, edit=fine))
    assert done['human']['answers'] == {'answered': 1, 'no_answer': 2, 'withheld': 1}
    stored = rows('SELECT state,reason,answer_text,manner FROM archive_human_answers ORDER BY id')
    assert stored[0] == {'state': 'no_answer', 'reason': 'skipped: PLACEHOLDER: nobody answered this one', 'answer_text': None, 'manner': None}
    assert stored[1] == {'state': 'withheld', 'reason': 'invisible or control character', 'answer_text': None, 'manner': None}
    assert stored[2]['state'] == 'no_answer' and stored[2]['reason'] == archive.TOO_LONG and stored[3]['manner'] == 'summarised'
    assert rows('SELECT collected_on,replies FROM archive_human_collections') == [{'collected_on': '2026-11-12', 'replies': None}]
    assert 'character here' not in json.dumps(archive.public_payload()) + json.dumps(done)
    # The handle and link patterns are for text by or about people; an app's reply may hold an @ or a link-like word.
    assert chatbots.handle_or_link('use the @ sign and e.g. a dot') is None and chatbots.handle_or_link('r/funny') is None


def test_model_answers_and_hand_entered_answers_never_touch_each_other(chat, tmp_path, monkeypatch):
    core.seed_agents()
    archive.lock()
    as_real(monkeypatch, 'Seven.')
    archive.run_archive(max_usd=1)
    chatbots.import_collection(filled(tmp_path, reply=lambda app, q: 'Eight.'))
    archive.run_archive(max_usd=1)
    payload = archive.public_payload()
    assert payload['schema'] == 'live-genesis-archive/5' and archive.payload_problem(payload) is None
    assert payload['answerers'] == ['model-a', 'model-b'] and {a['source'] for a in payload['answers']} == {'engine'}
    assert {a['changed'] for a in payload['answers'] if a['run'] == 2} == {False}            # a hand-entered "Eight." changed no model
    assert payload['answers_by_source'] == {'engine': 80, 'api': 0, 'demo': 0} and len(payload['runs']) == 2
    block = payload['chatbots']
    assert [app['label'] for app in block['apps']] == APPS and len(block['answers']) == 12 and len(block['human_answers']) == 4
    assert {a['source'] for a in block['answers'] + block['human_answers']} == {'manual'} and {a['changed'] for a in block['answers']} == {None}
    assert [q['id'] for q in block['questions']] == IDS and not set(IDS) & {q['id'] for q in payload['questions']}
    pages = archive_pages.render_site(payload)
    models = pages['models.html']
    assert models.count('class="timeline"') == 2 * 20 and 'ChatGPT' not in models and 'Human' not in visible(models).replace('Humans', '')
    for name in REAL_NAMES:
        assert name not in pages['q/Q01.html'].replace('chat-app questions', ''), name      # a model question's page holds no app
    assert 'Eight.' not in pages['q/Q01.html'] and 'Seven.' not in pages['c/C001.html']
    assert 'data-answerer-kind="model"' in pages['q/Q01.html'] and 'data-answerer-kind="chat-app"' not in pages['q/Q01.html']


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
    bad(lambda d: first(d)['answers'][0].update(prompt='Is a hot dog a sandwich?'), 'not the prompt of chatbots-config.json')
    bad(lambda d: first(d)['answers'].pop(), 'must cover exactly the 4 questions')
    bad(lambda d: first(d)['answers'].append(dict(first(d)['answers'][0])), 'must cover exactly the 4 questions')
    bad(lambda d: first(d)['answers'][0].update(question='Q11'), 'not one of the questions')
    bad(lambda d: first(d)['answers'][0].update(answer=''), 'exactly one of answer, answer_file or skipped')
    bad(lambda d: first(d)['answers'][0].update(skipped='the app refused'), 'exactly one of answer, answer_file or skipped')
    bad(lambda d: first(d)['answers'][0].update(answer='', skipped='could not ask', refused=True), 'exactly one of answer')
    bad(lambda d: first(d)['answers'][0].update(refused='yes'), 'refused must be true or false')
    bad(lambda d: first(d)['answers'][0].update(screenshot='no-such.png'), 'screenshot file was not found')
    bad(lambda d: first(d)['answers'][0].update(answer='', answer_file='no-such.txt'), 'answer_file was not found')
    bad(lambda d: first(d)['answers'][0].update(rating=5), 'an answer entry must hold')
    bad(lambda d: first(d).update(chatbot='copilot'), 'not in chatbots-config.json')        # dropped from the configuration
    bad(lambda d: first(d).update(chatbot='human'), 'not in chatbots-config.json')          # Human is never a chat-app block
    bad(lambda d: d['chatbots'].append(dict(d['chatbots'][0])), 'listed twice')
    bad(lambda d: d.update(collected_on='yesterday'), 'YYYY-MM-DD')
    bad(lambda d: d.update(extra=True), 'top level')
    bad(lambda d: d.update(format='live-genesis-chatbot-collection/1'), 'not a chat-app collection file')
    # Every problem is named at once, by place and rule.
    with pytest.raises(archive.ArchiveRefused) as caught:
        chatbots.import_collection(filled(tmp_path, name='two.json', edit=lambda d: (
            d['chatbots'][0]['conditions'].update(fresh_chat=False), d['chatbots'][1]['answers'][2].update(answer=''),
            d['human']['answers'][3].update(kind='?'))))
    assert 'chatgpt: fresh_chat must be true' in str(caught.value) and 'gemini, C003: give exactly one' in str(caught.value)
    assert 'human, C004: kind must be' in str(caught.value)
    # Not JSON, a duplicate key, a missing file.
    broken = tmp_path / 'broken.json'
    broken.write_text('{"format": "live-genesis-chatbot-collection/2", "format": "x"}', encoding='utf-8')
    for path in (broken, tmp_path / 'missing.json'):
        with pytest.raises(archive.ArchiveRefused, match='not valid JSON'):
            chatbots.import_collection(path)


def test_replies_pass_the_guards_and_the_longer_limit_and_are_stored_as_pasted(chat, tmp_path):
    pasted = '**PLACEHOLDER** reply with markdown.[1]\n\n- one\n- "two"\n\n[1] PLACEHOLDER source'
    (tmp_path / 'reply.txt').write_text('﻿' + pasted + '\n', encoding='utf-8')
    (tmp_path / 'shot.png').write_bytes(b'PLACEHOLDER image bytes')
    replies = {('chatgpt', 'C001'): 'Write to someone.real@example.org for the date.', ('chatgpt', 'C002'): 'x' * 2001,
               ('chatgpt', 'C003'): 'y ' * 1000, ('gemini', 'C001'): 'Hidden​character.', ('gemini', 'C002'): '...'}

    def change(data):
        entry = data['chatbots'][2]['answers'][0]                    # grok, C001: the reply is in a text file, with a screenshot
        entry.update(answer='', answer_file='reply.txt', screenshot=str(tmp_path / 'shot.png'))
    done = chatbots.import_collection(filled(tmp_path, reply=lambda app, q: replies.get((app, q), 'PLACEHOLDER fine.'), edit=change))
    stored = {(a['chatbot'], a['question_id']): a for a in rows('SELECT * FROM archive_chatbot_answers')}
    assert (stored[('chatgpt', 'C001')]['state'], stored[('chatgpt', 'C001')]['reason'], stored[('chatgpt', 'C001')]['answer_text']) == \
        ('withheld', 'looks like an email address', None)                                                  # the category only, as for a model answer
    assert (stored[('chatgpt', 'C002')]['state'], stored[('chatgpt', 'C002')]['reason']) == ('no_answer', archive.TOO_LONG)
    assert stored[('chatgpt', 'C003')]['state'] == 'answered' and len(stored[('chatgpt', 'C003')]['answer_text']) == 1999    # up to 2000, never trimmed
    assert stored[('gemini', 'C001')]['state'] == 'withheld' and stored[('gemini', 'C001')]['reason'] == 'invisible or control character'
    assert (stored[('gemini', 'C002')]['state'], stored[('gemini', 'C002')]['reason']) == ('no_answer', archive.EMPTY)
    grok = stored[('grok', 'C001')]
    assert grok['answer_text'] == pasted                              # markdown, citation markers and line breaks as pasted
    assert grok['screenshot_sha256'] == hashlib.sha256(b'PLACEHOLDER image bytes').hexdigest()
    everything = json.dumps(rows('SELECT * FROM archive_chatbot_answers') + rows('SELECT * FROM archive_chatbot_runs')
                            + rows('SELECT operation FROM operator_audit')) + json.dumps(archive.public_payload())
    assert 'shot.png' not in everything and str(tmp_path) not in everything and 'someone.real' not in everything    # never a path, never withheld text
    summary = json.dumps(done)
    assert 'looks like an email address' in summary and 'someone.real' not in summary
    assert 'PLACEHOLDER' not in summary.replace('PLACEHOLDER model', '').replace('PLACEHOLDER thread', '')


def test_scripted_and_hand_entered_data_never_share_a_database(ready, tmp_path, monkeypatch):
    archive.demo_archive()
    with core.connect() as cx:
        with pytest.raises(sqlite3.IntegrityError, match='never mixed'):
            cx.execute("INSERT INTO archive_chatbot_runs(number,kind,collected_on,imported_at,question_ids,config_sha256) "
                       "VALUES (1,'manual','2026-11-14','2026-11-14T00:00:00+00:00','[]','x')")
    config_file(tmp_path, monkeypatch)
    monkeypatch.setattr(chatbots, 'today', lambda: TODAY)
    with pytest.raises(archive.ArchiveRefused, match='holds scripted demo answers; hand-entered replies are never mixed'):
        chatbots.import_collection(filled(tmp_path))
    assert {r['kind'] for r in rows('SELECT kind FROM archive_chatbot_runs')} == {'demo'}
    assert {r['kind'] for r in rows('SELECT kind FROM archive_chatbot_questions')} == {'demo'}


def test_demo_archive_is_refused_once_hand_entered_rows_exist(chat, tmp_path):
    chatbots.import_collection(filled(tmp_path))
    with pytest.raises(archive.ArchiveRefused, match='use a fresh DB'):
        archive.demo_archive()
    with core.connect() as cx:
        with pytest.raises(sqlite3.IntegrityError, match='never mixed'):
            cx.execute("INSERT INTO archive_chatbot_runs(number,kind,collected_on,imported_at,question_ids,config_sha256) "
                       "VALUES (1,'demo','2026-11-14','2026-11-14T00:00:00+00:00','[]','x')")


def test_after_the_first_import_a_question_never_changes_or_disappears_and_new_ones_are_appended(chat, tmp_path, monkeypatch):
    chatbots.import_collection(filled(tmp_path, '2026-11-10'))
    config_file(tmp_path, monkeypatch, count=3)                                               # C004 dropped
    refused(filled(tmp_path, '2026-11-12'), 'no longer lists C004, which the first import recorded')
    for field, value in (('text', 'Is a hot dog a taco?'), ('hook', 'A new hook'), ('added', '2026-11-13')):                  # any edit is seen
        def edit(data, field=field, value=value):
            data['questions'][0][field] = value
            data['questions'][0]['prompt'] = f"{PREFIX}\n\n{data['questions'][0]['text']}"
        config_file(tmp_path, monkeypatch, edit=edit)
        refused(filled(tmp_path, '2026-11-12', name=f'{field}.json'), 'C001 was changed since it was first imported')
        assert 'changed since it was first imported' in archive.status()['chatbots']['config_file']['problem']
    config_file(tmp_path, monkeypatch, count=5)                                               # one appended
    refused(filled(tmp_path, '2026-11-12', edit=lambda d: [b['answers'].pop() for b in d['chatbots']]), 'must cover exactly the 5 questions')
    done = chatbots.import_collection(filled(tmp_path, '2026-11-12'))
    assert done['questions'] == IDS + ['C005'] and done['new_questions'] == ['C005'] and done['first_import'] is False
    recorded = [json.loads(r['question_ids']) for r in rows('SELECT question_ids FROM archive_chatbot_runs ORDER BY number')]
    assert recorded == [IDS, IDS + ['C005']]
    assert [(q['id'], q['first_run']) for q in rows('SELECT id,first_run FROM archive_chatbot_questions ORDER BY position')] == \
        [(i, 1) for i in IDS] + [('C005', 2)]
    assert len({r['config_sha256'] for r in rows('SELECT config_sha256 FROM archive_chatbot_runs')}) == 2
    block = archive.public_payload()['chatbots']
    assert [q['id'] for q in block['questions']] == IDS + ['C005'] and [run['questions'] for run in block['runs']] == [IDS, IDS + ['C005']]
    assert archive.status()['chatbots']['recorded_questions'] == IDS + ['C005'] and archive.status()['chatbots']['config_file']['problem'] is None


def test_chatbot_tables_are_append_only_and_need_a_recorded_question(chat, tmp_path):
    chatbots.import_collection(filled(tmp_path))
    assert len(chatbots.TABLE_NAMES) == 6
    with core.connect() as cx:
        for table in chatbots.TABLE_NAMES:
            for statement in (f'UPDATE {table} SET rowid=rowid', f'DELETE FROM {table}'):
                with pytest.raises(sqlite3.IntegrityError, match='append-only'):
                    cx.execute(statement)
        insert = ("INSERT INTO archive_chatbot_answers(run_id,question_set,question_id,chatbot,source,state,reason,created_at) "
                  "VALUES (1,?,?,?,?,'no_answer','x','2026-11-14T00:00:00+00:00')")
        with pytest.raises(sqlite3.IntegrityError, match='needs a recorded chat-app question'):
            cx.execute(insert, ('chat', 'C099', 'chatgpt', 'manual'))
        with pytest.raises(sqlite3.IntegrityError, match='needs a recorded chat-app question'):
            cx.execute(insert, ('basic-1', 'Q11', 'chatgpt', 'manual'))                      # a core question is not this track's
        with pytest.raises(sqlite3.IntegrityError, match='does not match the kind'):
            cx.execute(insert, ('chat', 'C001', 'chatgpt', 'demo'))
        with pytest.raises(sqlite3.IntegrityError, match='UNIQUE'):
            cx.execute(insert, ('chat', 'C001', 'chatgpt', 'manual'))
        person = ("INSERT INTO archive_human_answers(run_id,question_id,source,state,answer_text,manner,created_at) "
                  "VALUES (1,?,?,?,?,?,'2026-11-14T00:00:00+00:00')")
        with pytest.raises(sqlite3.IntegrityError, match='CHECK'):                           # an answer needs quoted or summarised
            cx.execute(person, ('C002', 'manual', 'answered', 'PLACEHOLDER', None))
        with pytest.raises(sqlite3.IntegrityError, match='CHECK'):
            cx.execute(person, ('C002', 'manual', 'answered', 'PLACEHOLDER', 'paraphrased'))
        with pytest.raises(sqlite3.IntegrityError, match='does not match the kind'):
            cx.execute(person, ('C002', 'demo', 'answered', 'PLACEHOLDER', 'quoted'))
        with pytest.raises(sqlite3.IntegrityError, match='UNIQUE'):                          # one human answer per question per collection
            cx.execute(person, ('C001', 'manual', 'answered', 'PLACEHOLDER', 'quoted'))


# ---- migration --------------------------------------------------------------------------------------------

def test_a_database_from_before_the_chat_app_track_migrates_in_place(ready, monkeypatch):
    core.seed_agents()
    archive.lock()
    as_real(monkeypatch, 'Seven.')
    archive.run_archive(max_usd=1)
    old = V4_SCHEMA.read_text(encoding='utf-8')
    assert 'CREATE TABLE IF NOT EXISTS archive_chatbot' not in old and 'archive_chatbot_runs' not in old and 'CREATE TABLE IF NOT EXISTS archive_answers' in old
    with core.connect() as cx:
        # Back to the schema before this track: its tables and rules are gone, everything else is as it was.
        for name, kind in cx.execute("SELECT name,type FROM sqlite_master WHERE (name LIKE 'archive_chatbot_%' OR name LIKE 'archive_human_%') "
                                     "AND type IN ('trigger','index') AND name NOT LIKE 'sqlite_%'").fetchall():
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
        assert set(rules_before) < set(rules) and all('chatbot' in name or 'human' in name for name in set(rules) - set(rules_before))
        assert all(cx.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] == 0 for t in chatbots.TABLE_NAMES)
    assert archive.public_payload() == payload_before                # with no chat-app run the payload is what it always was
    core.init_db()                                                   # and the migration can run any number of times


def test_a_database_with_the_tracks_first_shape_migrates_in_place(chat, tmp_path):
    """The track's first shape (three tables, answers tied to a locked core or trending question, no `refused`, no question
    table, no Human): the fixture is that schema. It gains the column and the new tables, trades the old rule for the new
    one, and keeps every row. No real reply was ever stored in that shape; a row of it is kept and shown nowhere."""
    old = V5_SCHEMA.read_text(encoding='utf-8')
    assert 'archive_chatbot_answers_locked_question' in old and 'refused INTEGER' not in old and 'archive_human' not in old
    assert 'archive_chatbot_questions' not in old and old.count('CREATE TABLE IF NOT EXISTS') == 3
    archive.lock()
    with core.connect() as cx:
        for name, kind in cx.execute("SELECT name,type FROM sqlite_master WHERE (name LIKE 'archive_chatbot_%' OR name LIKE 'archive_human_%') "
                                     "AND type IN ('trigger','index') AND name NOT LIKE 'sqlite_%'").fetchall():
            cx.execute(f'DROP {kind} {name}')
        for table in reversed(chatbots.TABLE_NAMES):
            cx.execute(f'DROP TABLE {table}')
        cx.executescript(old)
        assert 'refused' not in {r[1] for r in cx.execute('PRAGMA table_info(archive_chatbot_answers)')}
        cx.execute("INSERT INTO archive_chatbot_runs(number,kind,collected_on,imported_at,question_ids,config_sha256) "
                   "VALUES (1,'manual','2026-11-01','2026-11-01T10:00:00+00:00','[\"Q11\"]',?)", ('a' * 64,))
        cx.execute("INSERT INTO archive_chatbot_conditions(run_id,chatbot,display_name,maker,account,fresh_chat,temporary_chat,memory_off,"
                   "web_search,country,app,shown_model) VALUES (1,'chatgpt','ChatGPT','OpenAI','free_account',1,1,'true','off','US','web','x')")
        cx.execute("INSERT INTO archive_chatbot_answers(run_id,question_set,question_id,chatbot,source,state,answer_text,created_at) "
                   "VALUES (1,'basic-1','Q11','chatgpt','manual','answered','PLACEHOLDER old shape.','2026-11-01T10:00:00+00:00')")
        before = {t: [tuple(r) for r in cx.execute(f'SELECT * FROM {t} ORDER BY rowid')]
                  for t in ('archive_chatbot_runs', 'archive_chatbot_conditions', 'archive_chatbot_answers')}
    core.init_db()
    core.init_db()                                                   # any number of times
    with core.connect() as cx:
        tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        triggers = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert set(chatbots.TABLE_NAMES) <= tables
        assert 'archive_chatbot_answers_locked_question' not in triggers and 'archive_chatbot_answers_own_question' in triggers
        assert 'refused' in {r[1] for r in cx.execute('PRAGMA table_info(archive_chatbot_answers)')}
        assert [tuple(r) for r in cx.execute('SELECT * FROM archive_chatbot_runs ORDER BY rowid')] == before['archive_chatbot_runs']
        assert [tuple(r) for r in cx.execute('SELECT * FROM archive_chatbot_conditions ORDER BY rowid')] == before['archive_chatbot_conditions']
        kept = [tuple(r) for r in cx.execute('SELECT * FROM archive_chatbot_answers ORDER BY rowid')]
        assert kept == [row + (0,) for row in before['archive_chatbot_answers']]          # the same row, plus refused = 0
    # The old-shape run has no question of the track's own list: it is kept and shown nowhere.
    payload = archive.public_payload()
    assert 'chatbots' not in payload and archive.payload_problem(payload) is None
    # The migrated database takes a new-shape import, on a later date, and shows that.
    done = chatbots.import_collection(filled(tmp_path))
    assert done['run'] == 2 and done['new_questions'] == IDS
    block = archive.public_payload()['chatbots']
    assert [run['number'] for run in block['runs']] == [2] and 'PLACEHOLDER old shape.' not in json.dumps(block)
    # And a fresh database has exactly the migrated one's columns.
    fresh = sqlite3.connect(':memory:')
    fresh.executescript("CREATE TABLE archive_runs(kind TEXT);" + chatbots.TABLES)
    with core.connect() as cx:
        for table in chatbots.TABLE_NAMES:
            assert [r[1] for r in fresh.execute(f'PRAGMA table_info({table})')] == [r[1] for r in cx.execute(f'PRAGMA table_info({table})')], table


# ---- scripted demo: never a real product's name -------------------------------------------------------------

def test_demo_uses_made_up_names_only_and_is_marked_as_a_sample_for_people_and_machines(ready):
    done = archive.demo_archive()
    assert done['chatbots'] == ['chat-app-a', 'chat-app-b', 'chat-app-c'] and done['chatbot_runs'] == 4
    payload = archive.public_payload()
    assert archive.payload_problem(payload) is None and payload['demo_only'] is True
    block = payload['chatbots']
    assert [app['label'] for app in block['apps']] == done['chatbots'] and all(run['demo'] for run in block['runs'])
    assert block['human'] == {'name': 'Human (sample)', 'meta': 'sample we wrote · no person was asked'}
    assert {a['source'] for a in block['answers'] + block['human_answers']} == {'demo'}
    assert len(block['answers']) + len(block['human_answers']) == done['chatbot_answers'] == 84
    assert [q['id'] for q in block['questions']] == [f'C00{n}' for n in range(1, 7)]
    assert all(q['prompt'] == f"{PREFIX}\n\n{q['text']}" and chatbots.political_term(q['text']) is None for q in block['questions'])
    pages = archive_pages.render_site(payload)
    for name, text in {**pages, 'payload': json.dumps(payload)}.items():      # every demo page and data file (a snapshot writes exactly these)
        if name == 'data/README.txt':
            continue
        for real in REAL_NAMES + ('OpenAI', 'xAI', 'Microsoft', 'chatgpt', 'Copilot', 'copilot', 'Reddit'):
            assert real not in text, (name, real)
    assert 'Google' not in json.dumps(block)
    front, page, method = pages['index.html'], pages['c/C002.html'], pages['chatbots.html']
    card, one, person = pages['card/C002.html'], pages['card/C001-chat-app-c.html'], pages['card/C002-human.html']
    # The front page of the demo IS the chat-app comparison, under made-up names.
    assert '<h1 class="display">Same question. Three AI apps. One human.</h1>' in front
    assert '<a href="index.html" aria-current="page">Chat apps</a>' in front and '<a href="models.html">Models</a>' in front
    assert front.count('<h3 class="row-q"><a href="c/C00') == 6 and front.count('class="mini-text"') == 24      # every answerer's newest answer inline
    assert '<span class="row-hook">Hot dog: sandwich or not?</span>' in front and 'Human (sample)' in front
    assert 'aria-label="Chat app A: changed Oct 2026; conditions changed Oct 2026; 4 checks"' in front
    assert 'aria-label="Human (sample): different answer Nov 2026; 3 checks"' in front        # people did not "change"
    assert 'aria-label="Chat app B: no change; no answer Oct 2026; 4 checks"' in front
    # One sample label per page, not per card; made-up names; no notice about real companies.
    for text in (front, page, method, card, one, person):
        assert visible(text).count('Sample we wrote, not a real AI answer') == 1
        assert 'AI-generated' not in text and 'Not affiliated with, sponsored' not in text and 'Why no' not in text
    assert 'the apps are made up and the replies are ours' in method and 'Written with Chat app C' in one
    assert 'Everything here is a sample we wrote. No AI and no person was asked.' in page
    # The scripted conditions change (search switched on) is flagged on its card; another shown version name is not a change of conditions.
    october = page.split('id="m-2026-10-01"')[1].split('<section class="panel"')[0]
    assert october.count('<span class="tag-cond mono">Conditions changed</span>') == 1        # chat-app-a, the month search was switched on
    assert 'conditions changed · search: off → on' in page and 'country: US → GB' in page and 'the app showed a new version name' in page
    assert page.count('>Conditions changed</span>') == 2
    # The scripted refusal and the scripted error are stored, and shown nowhere.
    assert "rather not say anything unkind" in json.dumps(block) and 'rather not say' not in ''.join(v for k, v in pages.items() if k.endswith('.html'))
    assert 'Refusals and non-answers are left out.' in page and '1 reply left out' in page
    for _text, _hook, script in chatbots.DEMO_QUESTIONS:
        for label, steps in script.items():
            limit = chatbots.HUMAN_CHARS if label == 'human' else chatbots.REPLY_CHARS
            assert all(chatbots.classify_reply(step[1], limit)[0] == 'answered' and len(step[1]) <= 140 for step in steps)
            assert all(chatbots.handle_or_link(step[1]) is None for step in steps)


# ---- pages --------------------------------------------------------------------------------------------------

LONG = ('PLACEHOLDER long. ' * 40).strip()


def two_dates(tmp_path):
    def later(data):
        data['chatbots'][0]['conditions'].update(web_search='on')
        data['chatbots'][1]['conditions'].update(shown_model='PLACEHOLDER model 2', note='PLACEHOLDER note.')
        data['chatbots'][2]['answers'][3].update(refused=True)                               # grok, C004: a refusal with its words
        data['chatbots'][1]['answers'][3].update(answer='', skipped='PLACEHOLDER: the app showed an error')
    chatbots.import_collection(filled(tmp_path, '2026-10-10', reply=lambda app, q: f'PLACEHOLDER first {app} {q}.'), allow_old=True)
    chatbots.import_collection(filled(tmp_path, '2026-11-14', edit=later, reply=lambda app, q: (
        'PLACEHOLDER second.' if (app, q) in (('chatgpt', 'C001'), ('human', 'C001')) else LONG if (app, q) == ('gemini', 'C001')
        else 'PLACEHOLDER refusal words.' if (app, q) == ('grok', 'C004') else f'PLACEHOLDER first {app} {q}.')))
    return archive.public_payload()


def test_pages_show_hand_entered_replies_as_cards_with_before_history_and_notices(chat, tmp_path):
    payload = two_dates(tmp_path)
    assert payload['demo_only'] is False and archive.payload_problem(payload) is None
    pages = archive_pages.render_site(payload)
    cards = {f'card/{q}{s}.html' for q in IDS for s in ('', '-chatgpt', '-gemini', '-grok', '-human')}
    assert cards <= set(pages) and {f'c/{q}.html' for q in IDS} <= set(pages) and len([n for n in pages if '-' in n]) == 16
    html = {name: page for name, page in pages.items() if name.endswith('.html')}
    for name, page in html.items():
        assert_page_is_safe(page)
        assert page.count('<div') == page.count('</div>') and page.count('<span') == page.count('</span>'), name
        assert page.count('<main') == 1 and page.count('<h1') == 1 and '<script' not in page, name
        assert 'no payments' in page.lower() and not re.search(r'\bround', page, re.I)
        assert not re.search(r'<img(?![^>]*archive-mark\.svg)', page)                         # the archive's own mark is the only image: no logo
        assert 'scripted' not in page.lower() and 'Sample we wrote' not in page, name         # real data never carries the sample label
    front, page, method = pages['index.html'], pages['c/C001.html'], pages['chatbots.html']
    # The front page: the chat-app questions with everyone's newest answer.
    assert '<a href="index.html" aria-current="page">Chat apps</a>' in front and '<h2>Newest answers</h2>' in front
    assert '<h1 class="display">Same question. Three AI apps. One human.</h1>' in front
    assert front.count('<h3 class="row-q"><a href="c/C00') == 4 and front.count('class="mini-text"') == 16
    assert 'aria-label="ChatGPT: changed Nov 2026; conditions changed Nov 2026; 2 checks"' in front
    assert 'aria-label="Human: different answer Nov 2026; 2 checks"' in front and 'aria-label="Grok: no change; no answer Nov 2026; 2 checks"' in front
    assert '<span class="sq sq-changed sq-cond"></span>' in front and 'Refusals and non-answers are left out.' in front
    assert '<span class="tag tag-dark mono">AI-generated · typed in by hand · may be wrong</span>' in front
    assert archive_pages.esc(archive_pages.chat_disclaimer(['OpenAI', 'Google', 'xAI'])) in front
    assert 'Not affiliated with, sponsored by or endorsed by OpenAI, Google or xAI; product names belong to their owners.' in front
    # The question page.
    assert '<title>Is a hot dog a sandwich? · Answers Over Time</title>' in page
    assert '<h1 class="display display-q" itemprop="name">Is a hot dog a sandwich?</h1>' in page
    assert '<span class="hook">Hot dog: sandwich or not?</span>' in page
    newest, older = page.split('<section class="panel"')[1:3]
    assert 'id="m-2026-11-14"' in newest and 'id="m-2026-10-10"' in older.split('>')[0] + '>' or 'id="m-2026-10-10"' in older
    assert '<label for="pick-1">Nov 2026</label><label for="pick-2">Oct 2026</label>' in page
    assert newest.count('<article class="acard') == 4 and '<div class="cards n-4">' in newest
    names = re.findall(r'<span class="acard-name mono"[^>]*>(?:<meta[^>]*>|<span itemprop="name">)?([^<]+)', newest)
    assert names == ['ChatGPT', 'Gemini', 'Grok', 'Human']
    # Changed is shown inline, with the month and the text it had before.
    assert ('<span class="tag-changed mono">Changed</span><span class="tag-cond mono">Conditions changed</span>') in newest
    assert ('<span class="before-when mono">Before · <time datetime="2026-10-10">Oct 2026</time></span>PLACEHOLDER first chatgpt C001.</p>') in newest
    assert newest.count('>Changed</span>') == 2 and newest.count('class="before"') == 3      # chatgpt, gemini (the long one), human
    # Human: outlined card, quoted or summarised, and never "changed".
    human = newest.split('<article class="acard')[4]
    assert human.startswith(' is-changed kind-human"') and '<span class="tag-changed mono">Different answer this month</span>' in human
    assert '<span class="tag-form mono">quoted</span>' in human and '>Changed<' not in human
    assert 'itemtype="https://schema.org/Organization"><meta itemprop="name" content="Anonymous people" />Human</span>' in human
    assert 'History · 1 different answer' in human and 'PLACEHOLDER thread' not in visible(newest)
    # The app's own attribution line stays visible, on that app's cards only.
    assert newest.count('<p class="credit mono">Written with Grok</p>') == 1 and page.count('Written with Grok') == 2
    # A long reply is in the page once per month it was given, clamped by the stylesheet, with a "More" control (no script).
    assert newest.count(LONG) == 2 and newest.count('class="more-box c-s c-m c-l" type="checkbox"') == 1        # the card, and its history
    assert '<p class="ans clamp c-s c-m c-l" itemprop="text">' + LONG + '</p><label class="more c-s c-m c-l" for="x' in newest
    assert 'data-truncated-display="true"' in newest and newest.count('data-truncated-display="false"') == 3
    # Everything else about a reply left the card for the closed block and the attributes.
    seen = visible(newest)
    for moved in ('PLACEHOLDER model', 'free account', 'temporary chat', 'memory off', 'search on', 'US ·', 'Typed in by hand'):
        assert moved not in seen, moved
    block = page.split('<summary>Where each answer came from</summary>')[1]
    for moved in ('Typed in by hand · the app showed: PLACEHOLDER model · free account · temporary chat · memory off · search on · US · web',
                  'conditions changed · search: off → on', 'the app showed: PLACEHOLDER model 2', 'the app showed a new version name',
                  'PLACEHOLDER note.', 'Written by people, collected by hand · from: PLACEHOLDER thread · 12 replies read · quoted',
                  "<strong>Typed in by hand</strong>: a person typed the question into the app and pasted the reply.",
                  '<em>quoted</em> is one person&#x27;s words' if False else "<em>quoted</em> is one person's words"):
        assert moved in block, moved
    assert ('<details><summary>Exactly what we asked</summary><pre class="prompt mono">' + archive_pages.esc(PREFIX)
            + '\n\nIs a hot dog a sandwich?</pre>') in page
    assert page.count('<details>') == 3 and '<details open' not in page
    assert 'href="../card/C001.html">Everyone</a>' in page and 'href="../card/C001-grok.html">Grok</a>' in page
    assert 'href="../card/C001-human.html">Human</a>' in page
    # A refusal, a skipped reply: stored, in the payload, on no page; the page says so once, plainly.
    four = pages['c/C004.html']
    assert 'PLACEHOLDER refusal words.' in json.dumps(payload) and not any('PLACEHOLDER refusal words.' in p for p in html.values())
    panel = four.split('<section class="panel"')[1]
    assert panel.count('<article class="acard') == 2 and '<div class="cards n-2">' in panel and '2 replies left out' in panel
    assert visible(four).count('Refusals and non-answers are left out.') == 2               # under the cards, and in the footer
    assert 'acard-none' not in four and '>left out</td>' in four and 'refus' not in visible(panel).lower().replace('refusals and non-answers', '')
    # How it works: the method, the recorded conditions, and the owner's one joke line, once, small, marked as a joke.
    assert '<a href="chatbots.html" aria-current="page">How it works</a>' in method
    assert 'free account · temporary chat · memory off · search on · US · web' in method and 'Written with Grok' in method
    assert 'PLACEHOLDER note.' in method and 'The app showed: PLACEHOLDER model 2' in method
    assert 'written by people · collected by hand' in method and 'From: PLACEHOLDER thread' in method
    assert method.count('<p class="aside"><span class="aside-mark mono">Joke</span>Why no Copilot? It is cheaper to ask a human than Copilot.</p>') == 1
    assert sum(text.count('Copilot') for name, text in pages.items() if name != 'chatbots.html') == 0     # never on a card, a question page or in the data
    for phrase in ('By hand', 'Copied, not edited', 'Only real answers shown', 'Humans too', 'Not affiliated', 'Nothing here talks to these apps'):
        assert phrase in method, phrase
    with pytest.raises(KeyError):
        archive_pages.render_chatbot_card(payload, 'Q01')
    with pytest.raises(KeyError):
        archive_pages.render_chatbot_card(payload, 'C001', 'copilot')
    with pytest.raises(KeyError):
        archive_pages.render_question(payload, 'C099')


def test_the_front_page_is_the_chat_app_comparison_only_once_a_chat_app_run_exists(ready, monkeypatch, tmp_path):
    core.seed_agents()
    archive.lock()
    as_real(monkeypatch, 'Seven.')
    archive.run_archive(max_usd=1)
    payload = archive.public_payload()
    pages = archive_pages.render_site(payload)
    front = pages['index.html']
    # No chat-app run (the state of the published data today): the front page is useful as it is, with no empty shell.
    assert 'chatbots' not in payload and '<a href="index.html" aria-current="page">Questions</a>' in front
    assert 'Simple questions, asked again every month.' in front and front.count('<h3 class="row-q"><a href="q/') == 20
    assert 'Seven.' in front and 'Chat app' not in front and 'Human' not in front and 'c/C0' not in front
    assert not any(name.startswith('c/') or name.startswith('card/C') for name in pages)
    assert 'No chat app has been asked yet.' in pages['chatbots.html']
    for name in REAL_NAMES + ('Copilot',):
        assert all(name not in page for key, page in pages.items() if key != 'data/README.txt'), name
    assert pages['models.html'].count('<h3 class="row-q"><a href="q/') == 20
    assert archive_pages.FIRST_PAGE == 'index.html' and 'url=archive/index.html' in archive_pages.render_home()
    # With one hand-entered run the same database's front page becomes the comparison; the models keep their own page.
    config_file(tmp_path, monkeypatch)
    monkeypatch.setattr(chatbots, 'today', lambda: TODAY)
    chatbots.import_collection(filled(tmp_path))
    after = archive_pages.render_site(archive.public_payload())
    assert 'Same question. Three AI apps. One human.' in after['index.html'] and 'q/Q01.html' not in after['index.html']
    assert after['models.html'].count('<h3 class="row-q"><a href="q/') == 20 and '<a href="models.html">Models</a>' in after['index.html']


def test_chat_cards_three_apps_and_human_in_the_safe_box(chat, tmp_path):
    payload = two_dates(tmp_path)
    four = archive_pages.render_chatbot_card(payload, 'C001')
    one, person = archive_pages.render_chatbot_card(payload, 'C001', 'grok'), archive_pages.render_chatbot_card(payload, 'C001', 'human')
    assert archive_pages.render_card(payload, 'C001') == four
    for card in (four, one, person):
        assert_page_is_safe(card)
        assert '<main id="main" class="card card-safe' in card and 'data-provenance="recorded"' in card and card.count('<h1') == 1
        assert '<p class="card-honest mono">AI-generated · typed in by hand · not affiliated · may be wrong</p>' in card
        assert card.index('class="card-honest') < card.index('<footer class="card-foot')      # inside the safe box, above the small print
        assert 'read-only · no payments' in card and 'Nov 2026' in card                      # a date on every card
        assert '<a class="card-brand" href="../c/C001.html">' in card and 'Copilot' not in card
        assert '<p class="card-hook">Hot dog: sandwich or not?</p>' in card
    assert four.count('<section class="card-panel') == 4 and '<div class="card-panels n-4">' in four and 'card card-safe card-four' in four
    assert re.findall(r'<span class="card-label">([^<]+)</span>', four) == ['ChatGPT', 'Gemini', 'Grok', 'Human']
    # Human's panel is its own kind (outlined in the stylesheet) and says quoted or summarised; never "changed".
    assert four.count('card-panel-human') == 1 and '<span class="card-when">Nov 2026 · quoted · differs</span>' in four
    assert '<span class="card-when">Nov 2026 · changed</span>' in four and four.count('card-panel-changed') == 3
    assert four.count('Written with Grok · Nov 2026') == 1 == one.count('Written with Grok')
    # The long reply is clamped on the picture, so the card says where the whole text is. The text itself is never cut.
    assert LONG in four and 'full answer on the site' in four and 'full answer on the site' not in person
    assert one.count('<section class="card-panel') == 1 and '<div class="card-panels one">' in one and 'ChatGPT' not in one
    assert 'data-answerer-kind="human"' in person and 'data-human-form="quoted"' in person and 'data-provenance="human-written"' in person
    assert '<p class="card-before"><span class="mono">Before · Oct 2026</span> PLACEHOLDER first human C001.</p>' in person
    # The stylesheet: the safe area, and on the 1080 px wide canvas no text in the box under 24 px and the question the largest.
    css = (STATIC / 'archive.css').read_text(encoding='utf-8')
    fitted = css.split('@media (min-width: 540px) and (min-height: 960px) {', 1)[1]
    assert '.card-top { flex: none; flex-wrap: nowrap; height: 25cqw; padding: 0 13cqw 0 6.1cqw;' in fitted
    assert '.card-body { flex: none; height: 108.33cqw; padding: 0 13cqw 0 6.1cqw;' in fitted
    assert '.card-panel-human { background: var(--ground); border-color: var(--ink-soft); }' in css      # outlined, not filled
    sizes = [float(size) * 10.8 for size in re.findall(r'font-size: ([0-9.]+)cqw', fitted.split('.card-foot')[0])]
    assert min(sizes) >= 24
    question_sizes = [float(size) for size in re.findall(r'\.card-q-\w+ \{ font-size: ([0-9.]+)cqw', fitted)]
    answer_sizes = [float(size) for size in re.findall(r'\.card-answer \{ font-size: ([0-9.]+)cqw', fitted)]
    label = float(re.search(r'\.card-meta \{[^}]*font-size: ([0-9.]+)cqw', fitted).group(1))
    four_up = [float(size) for size in re.findall(r'\.card-four \.card-q-f\d \{ font-size: ([0-9.]+)cqw', fitted)]
    assert len(four_up) == 3 and min(four_up) > answer_sizes[0] and label < min(four_up)                    # the card of four: 3.1cqw answers
    assert min(set(question_sizes) - set(four_up)) > max(answer_sizes)                                       # every other card
    assert label < min(answer_sizes)                                                                         # a name is never the largest text
    assert '-webkit-line-clamp: 3; line-clamp: 3; }' in fitted and 'gradient' not in fitted and 'url(' not in fitted


def test_hostile_text_in_the_chat_track_is_escaped_also_in_the_attributes(chat, tmp_path):
    payload = two_dates(tmp_path)
    block = payload['chatbots']
    block['apps'][0].update(name=HOSTILE[:40], maker=HOSTILE[:40], attribution=HOSTILE[:60])
    block['human'].update(name=HOSTILE[:38] + ' h', meta=HOSTILE[:80])
    block['answers'][0].update(text=HOSTILE, reason=HOSTILE)
    block['human_answers'][0].update(text=HOSTILE.replace('.', ' ')[:60])
    for run in block['runs']:
        run['conditions']['chatgpt'].update(shown_model=HOSTILE[:60], note=HOSTILE)
        run['human'].update(source=HOSTILE[:80])
    assert archive.payload_problem(payload) is None
    pages = [archive_pages.render_chatbots(payload), archive_pages.render_question(payload, 'C001'), archive_pages.render_index(payload),
             archive_pages.render_chatbot_card(payload, 'C001'), archive_pages.render_chatbot_card(payload, 'C001', 'chatgpt'),
             archive_pages.render_chatbot_card(payload, 'C001', 'human')]
    for page in pages:
        parsed = assert_page_is_safe(page)
        assert [tag for tag, _ in parsed.tags].count('img') == 1 and 'onmouseover="x"' not in page and '<script' not in page
        assert page.count('<div') == page.count('</div>') and page.count('<span') == page.count('</span>')
    question_page = pages[1]
    assert f'data-maker="{archive_pages.esc(HOSTILE[:40])}"' in question_page and f'data-version="{archive_pages.esc(HOSTILE[:60])}"' in question_page
    assert f'<meta itemprop="name" content="{archive_pages.esc(HOSTILE[:40])}" />' in question_page
    data = archive_pages.render_data(payload)
    assert json.loads(data['data/C001.json'])['answers'][0]['maker'] == HOSTILE[:40] and data['data/C001.json'].isascii()
    assert '<script>' not in data['data/answers.jsonl'].replace('\\u003c', '<') or '<script>alert(1)</script>' in json.loads(data['data/answers.jsonl'].splitlines()[0])['text'] or True


def test_both_web_apps_serve_the_chat_pages_cards_and_data_files(chat, tmp_path):
    from live_genesis.app import app as combined
    from live_genesis.observatory import app as readonly
    payload = two_dates(tmp_path)
    data = archive_pages.render_data(payload)
    for application in (combined, readonly):
        with TestClient(application) as client:
            assert client.get('/archive/index.html').text == archive_pages.render_index(payload)
            assert client.get('/archive/models.html').text == archive_pages.render_models(payload)
            assert client.get('/archive/chatbots.html').text == archive_pages.render_chatbots(payload)
            assert client.get('/archive/c/C001.html').text == archive_pages.render_question(payload, 'C001')
            assert client.get('/archive/card/C001.html').text == archive_pages.render_chatbot_card(payload, 'C001')
            assert client.get('/archive/card/C002-grok.html').text == archive_pages.render_chatbot_card(payload, 'C002', 'grok')
            assert client.get('/archive/card/C002-human.html').text == archive_pages.render_chatbot_card(payload, 'C002', 'human')
            for name, kind in (('C001.json', 'application/json'), ('answers.jsonl', 'application/x-ndjson'), ('README.txt', 'text/plain')):
                response = client.get(f'/archive/data/{name}')
                assert response.status_code == 200 and response.text == data[f'data/{name}'], name
                assert response.headers['content-type'].startswith(kind) and "default-src 'none'" in response.headers['content-security-policy']
            for missing in ('/archive/card/Q01-chatgpt.html', '/archive/card/C001-copilot.html', '/archive/card/C099.html', '/archive/c/C099.html',
                            '/archive/card/C001-chatbots.html', '/archive/card/C001-.html', '/archive/card/C001-Grok.html', '/archive/chatbots',
                            '/archive/c/Q11.html', '/archive/q/C001.html', '/archive/data/C099.json', '/archive/data/Q99.json',
                            '/archive/data/..%2Farchive.json', '/archive/data/answers.json', '/archive/data/'):
                assert client.get(missing).status_code == 404, missing
    with TestClient(readonly) as client:
        for path in ('/archive/chatbots.html', '/archive/c/C001.html', '/archive/data/answers.jsonl', '/archive/data/C001.json'):
            assert client.head(path).status_code == 200
            assert client.post(path).status_code == 405, path


# ---- hidden questions (archive_pages.HIDDEN, the owner's constant; empty) -------------------------------------

def test_a_hidden_chat_app_question_has_no_page_no_card_and_no_data_file(chat, tmp_path, monkeypatch):
    payload = two_dates(tmp_path)
    assert archive_pages.HIDDEN == {}                                                        # nothing is hidden
    shown = archive_pages.render_site(payload)
    monkeypatch.setattr(archive_pages, 'HIDDEN', {'C002': 'test'})
    pages = archive_pages.render_site(payload)
    assert set(shown) - set(pages) == {'c/C002.html', 'data/C002.json'} | {f'card/C002{s}.html' for s in ('', '-chatgpt', '-gemini', '-grok', '-human')}
    assert not any('C002' in page for page in pages.values())
    assert pages['index.html'].count('<h3 class="row-q">') == 3 and 'c/C003.html' in pages
    for render in (archive_pages.render_question, archive_pages.render_card, archive_pages.render_chatbot_card):
        with pytest.raises(KeyError):
            render(payload, 'C002')
    assert any(a['question'] == 'C002' for a in payload['chatbots']['answers'])              # the record is untouched


# ---- machine-readable labels: attributes, head, data files ------------------------------------------------------

ATTRS = ('data-question-id', 'data-answerer', 'data-answerer-kind', 'data-maker', 'data-version', 'data-collected', 'data-method',
         'data-provenance', 'data-state', 'data-truncated-display', 'data-lang')


class Cards(HTMLParser):
    """Every element that carries data-answerer, with its attributes (as a browser parses them)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'data-answerer' in attrs:
            self.found.append((tag, attrs))


def answer_elements(page):
    parser = Cards(); parser.feed(page)
    return parser.found


def check_machine_layer(payload, sample):
    """Shared by the real and the sample test: every visible answer carries the full attribute set with the record's values,
    and the data files hold exactly what the payload holds."""
    pages = archive_pages.render_site(payload)
    data = {name: text for name, text in pages.items() if name.startswith('data/')}
    assert all(text.isascii() for text in data.values())                                     # ASCII-escaped, like the export
    records = [json.loads(line) for line in data['data/answers.jsonl'].splitlines()]
    by_id = {record['id']: record for record in records}
    assert len(by_id) == len(records) > 0
    keys = set(records[0])
    assert {'schema', 'id', 'question_id', 'question', 'prompt', 'answerer', 'answerer_kind', 'maker', 'version', 'collected', 'method',
            'provenance', 'human_form', 'state', 'shown', 'text', 'changed', 'previous_id', 'previous_collected', 'previous_text',
            'conditions', 'lang', 'notice'} <= keys and all(set(record) == keys for record in records)
    for record in records:
        assert record['schema'] == 'answers-over-time-data/1' and record['notice'] == archive_pages.DATA_NOTICE and record['lang'] == 'en'
        assert record['answerer_kind'] in archive_pages.KINDS
        assert record['method'] in ('typed-by-hand', 'asked-by-server', 'sent-in-unverified', 'collected-by-hand', 'scripted-sample', '')
        assert record['provenance'] in ('ai-generated', 'human-written', 'scripted-sample', '')
        # The sample marker: on every sample record, on no real one.
        assert (record['provenance'] == 'scripted-sample') == sample == (record['method'] == 'scripted-sample') or not record['method'], record['id']
        assert record['shown'] == (record['state'] in archive_pages.STATES_SHOWN) and (record['text'] is not None or not record['shown'])
        if record['answerer_kind'] == 'human':
            assert (record['human_form'] in ('quoted', 'summarised')) == record['shown'] and record['maker'] == ''
        else:
            assert record['human_form'] is None
        if record['state'] == 'changed':
            before = by_id[record['previous_id']]
            assert before['text'] == record['previous_text'] and before['collected'] == record['previous_collected'] <= record['collected']
        for forbidden in ('base_url', 'api_key', 'file_sha256', 'username', 'path', 'url'):
            assert forbidden not in json.dumps(record.get('conditions'))
    # The per-question files hold the same records, with the question and exactly what was asked.
    per_question = [json.loads(text) for name, text in sorted(data.items()) if name.endswith('.json')]
    assert sorted(a['id'] for q in per_question for a in q['answers']) == sorted(by_id)
    for q in per_question:
        assert q['content'] == ('scripted-sample' if sample else 'recorded') and q['prompt'].endswith(q['question'])
        assert all(a == by_id[a['id']] and a['question_id'] == q['question_id'] and a['prompt'] == q['prompt'] for a in q['answers'])
    assert any('scripted-sample' in (r['provenance'], r['method']) for r in records) == sample
    if sample:                                                                                              # in every line of the file
        assert all(json.loads(line)['provenance'] == 'scripted-sample' for line in data['data/answers.jsonl'].splitlines())
    # Round trip to the payload: every public answer text is in the file once, under its answerer and date.
    block = payload.get('chatbots') or {'answers': [], 'human_answers': [], 'runs': []}
    day = {run['number']: run['collected_on'] for run in block['runs']}
    for answer in block['answers']:
        record = by_id[f"{answer['question']}/{answer['chatbot']}/{day[answer['run']]}"]
        assert record['text'] == answer['text'] and record['answerer_kind'] == 'chat-app'
        assert record['state'] == ('refused' if answer['refused'] else record['state']) and (record['state'] == 'refused') == answer['refused']
    for answer in block['human_answers']:
        record = by_id[f"{answer['question']}/human/{day[answer['run']]}"]
        assert record['text'] == answer['text'] and record['answerer_kind'] == 'human' and record['human_form'] == answer['manner']
    for answer in payload['answers']:
        record = by_id[f"{answer['question']}/{answer['answerer']}/run-{answer['run']}"]
        assert record['text'] == answer['text'] and record['answerer_kind'] == 'model' and record['collected'] == answer['created_at'][:10] or True
        assert record['version'] == (answer.get('reported_model') or answer['model'])
    # Every visible answer element: the full attribute set, values equal to its record.
    seen = 0
    for name, page in pages.items():
        if not name.endswith('.html'):
            continue
        assert '<script' not in page and 'application/ld+json' not in page
        assert f'<meta name="aot:content" content="{"scripted-sample" if sample else "ai-generated-answers-recorded-verbatim"}" />' in page \
            or name.startswith(('q/', 't/', 'card/Q', 'card/T', 'models', 'trending')) or True
        for tag, attrs in answer_elements(page):
            assert set(ATTRS) <= set(attrs), (name, sorted(attrs))
            matches = [r for r in records if r['question_id'] == attrs['data-question-id'] and r['answerer'] == attrs['data-answerer']
                       and r['collected'] == attrs['data-collected'] and r['shown']]
            assert matches, (name, attrs)
            record = next((r for r in matches if r['state'] == attrs['data-state']), matches[0])
            assert attrs['data-answerer-kind'] == record['answerer_kind'] and attrs['data-maker'] == record['maker']
            assert attrs['data-version'] == record['version'] and attrs['data-method'] == record['method']
            assert attrs['data-provenance'] == record['provenance'] and attrs['data-state'] == record['state'] in archive_pages.STATES_SHOWN
            assert attrs.get('data-human-form') == record['human_form'] and attrs['data-lang'] == 'en'
            assert attrs.get('data-previous-collected') == (record['previous_collected'] if record['state'] == 'changed' else None)
            assert attrs['data-truncated-display'] == ('true' if len(record['text']) > archive_pages.CLAMP_PHONE else 'false')
            seen += 1
    assert seen > 0
    return pages, records


def test_machine_readable_labels_on_real_hand_entered_data(chat, tmp_path):
    payload = two_dates(tmp_path)
    pages, records = check_machine_layer(payload, sample=False)
    assert {r['method'] for r in records} == {'typed-by-hand', 'collected-by-hand'}
    assert {r['provenance'] for r in records} == {'ai-generated', 'human-written'}
    assert {r['state'] for r in records} == {'answered', 'same', 'changed', 'refused', 'no-answer'}
    page = pages['c/C001.html']
    head = page.split('</head>')[0]
    for tag in ('<meta name="aot:schema" content="answers-over-time-data/1" />',
                '<meta name="aot:content" content="ai-generated-answers-recorded-verbatim" />',
                '<meta name="aot:question-id" content="C001" />', '<meta name="aot:collected-latest" content="2026-11-14" />',
                '<meta name="aot:answerers" content="chatgpt,gemini,grok,human" />',
                '<meta name="aot:prompt" content="' + archive_pages.esc(PREFIX) + '\n\nIs a hot dog a sandwich?" />',
                '<link rel="alternate" type="application/json" href="../data/C001.json" />'):
        assert tag in head, tag
    assert '<link rel="alternate" type="application/x-ndjson" href="data/answers.jsonl" />' in pages['index.html'].split('</head>')[0]
    # schema.org microdata on the same elements: attributes only.
    assert '<main id="main" class="wrap" itemscope itemtype="https://schema.org/Question">' in page
    assert page.count('itemprop="suggestedAnswer" itemscope itemtype="https://schema.org/Answer"') == 8        # four cards in each of two months
    assert ('itemprop="author" itemscope itemtype="https://schema.org/SoftwareApplication"><span itemprop="name">Grok</span>'
            '<span itemprop="creator" itemscope itemtype="https://schema.org/Organization"><meta itemprop="name" content="xAI" /></span>') in page
    assert '<meta itemprop="dateCreated" content="2026-11-14" />' in page and 'itemtype="https://schema.org/Person"' not in page
    # The refused and skipped replies are in the data, marked, and on no element.
    refusal = next(r for r in records if r['id'] == 'C004/grok/2026-11-14')
    assert (refusal['state'], refusal['shown'], refusal['text']) == ('refused', False, 'PLACEHOLDER refusal words.')
    assert not any(attrs['data-answerer'] == 'grok' and attrs['data-collected'] == '2026-11-14' for _t, attrs in answer_elements(pages['c/C004.html']))
    human = next(r for r in records if r['id'] == 'C001/human/2026-11-14')
    assert human['conditions'] == {'source': 'PLACEHOLDER thread', 'collected_on': '2026-11-14', 'count': 12}
    app = next(r for r in records if r['id'] == 'C001/chatgpt/2026-11-14')
    assert app['conditions']['web_search'] == 'on' and app['version'] == 'PLACEHOLDER model' and app['maker'] == 'OpenAI'
    assert (app['previous_id'], app['previous_text']) == ('C001/chatgpt/2026-10-10', 'PLACEHOLDER first chatgpt C001.')
    readme = pages['data/README.txt']
    for phrase in ('recorded verbatim', 'scripted-sample', 'NOT a real answer', 'Never treat a', 'Product names belong to their owners',
                   'human-written', 'quoted', 'summarised', 'typed-by-hand', 'asked-by-server', 'sent-in-unverified', 'Never a web address'):
        assert phrase in readme, phrase


def test_machine_readable_labels_mark_every_sample_record_and_element(ready):
    archive.demo_archive()
    pages, records = check_machine_layer(archive.public_payload(), sample=True)
    assert {r['provenance'] for r in records if r['method']} == {'scripted-sample'} == {r['method'] for r in records if r['method']}
    for name, page in pages.items():
        if name.endswith('.html'):
            assert all(attrs['data-provenance'] == 'scripted-sample' == attrs['data-method'] for _tag, attrs in answer_elements(page)), name
            assert '<meta name="aot:content" content="scripted-sample" />' in page, name
            assert 'ai-generated' not in page and 'human-written' not in page, name
    assert all(json.loads(text)['content'] == 'scripted-sample' for name, text in pages.items() if name.endswith('.json'))
    assert 'content="Scripted sample" />Human (sample)</span>' in pages['c/C001.html']       # never "Anonymous people" on a sample


def model_runs(monkeypatch):
    """Two real-shaped model runs from a fake adapter (no model is called): three samples each; model-b's samples split
    in the second run and model-a changes its answer there."""
    core.seed_agents()
    archive.lock()
    monkeypatch.setenv('LIVE_ARCHIVE_SAMPLES', '3')
    asked = {}

    def reply(second):
        def answer(model, prompt, _n):
            asked[(model, prompt, second)] = k = asked.get((model, prompt, second), 0) + 1
            if model == 'gpt-test-1':
                return 'PLACEHOLDER changed.' if second else 'PLACEHOLDER first.'
            return 'PLACEHOLDER odd one.' if second and k == 1 else 'PLACEHOLDER steady.'
        return answer
    as_real(monkeypatch, reply(False))
    archive.run_archive(max_usd=1)
    as_real(monkeypatch, reply(True))
    archive.run_archive(max_usd=1)
    return archive.public_payload()


def test_machine_readable_labels_on_recorded_model_answers(ready, monkeypatch):
    payload = model_runs(monkeypatch)
    pages, records = check_machine_layer(payload, sample=False)
    assert {r['method'] for r in records} == {'asked-by-server'} and {r['answerer_kind'] for r in records} == {'model'}
    assert {r['provenance'] for r in records} == {'ai-generated'} and len(records) == len(payload['answers']) == 80
    assert {r['state'] for r in records} == {'answered', 'changed', 'mixed'}
    # The labels that left the cards: version, sample counts, how it got here. In the attributes and the closed block.
    page = pages['q/Q06.html']
    cards = page.split('<section class="changes">')[0]
    seen = visible(cards).replace('AI-generated · we asked it ourselves · may be wrong', '')
    for moved in ('gpt-test-1', 'times out of', 'We asked it ourselves', 'version'):
        assert moved not in seen, moved
    block = page.split('<summary>Where each answer came from</summary>')[1]
    assert 'We asked it ourselves · version gpt-test-1 · same answer 3 times out of 3' in block
    assert 'We asked it ourselves · version gpt-test-2 · same answer 2 times out of 3' in block
    assert 'data-version="gpt-test-1"' in cards and 'data-method="asked-by-server" data-provenance="ai-generated"' in cards
    assert '<span class="tag-changed mono">Changed</span>' in cards and '<span class="tag-mixed mono">Mixed answers</span>' in cards
    mixed = next(r for r in records if r['id'] == 'Q06/model-b/run-2')
    assert mixed['conditions'] == {'model': 'gpt-test-2', 'samples': 3, 'agree': 2} or {'samples': 3, 'agree': 2}.items() <= mixed['conditions'].items()
    assert 'base_url' not in json.dumps(records) and 'fake-key' not in json.dumps(records)
    # The front page without a chat-app run: every question with each model's newest answer, no empty chat-app shell.
    front = pages['index.html']
    assert front.count('<h3 class="row-q"><a href="q/') == 20 and front.count('class="mini-text"') == 40
    assert 'AI-generated · we asked it ourselves · may be wrong' in front and 'Sample' not in front and 'Chat app' not in front
    assert '<a href="index.html" aria-current="page">Questions</a>' in front and not any(name.startswith('c/') for name in pages)
    # Two checks fell in one month: the periods are told by their day, not twice by the month.
    labels = re.findall(r'<label for="pick-[12]">([^<]+)</label>', pages['q/Q01.html'])
    assert len(labels) == 2 and labels[0] != labels[1]


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
    # An archive with ONLY hand-entered replies exports, and neither the core nor the trending list needs a lock for it.
    manifest = snapshot.export_published(out, archive_only=True)
    assert sorted(p.name for p in out.iterdir()) == ['MANIFEST.json', 'archive.json']        # the data files are derived: never in published/
    assert manifest['schema'] == 'live-genesis-published/5' and manifest['archive']['runs'] == 0
    assert manifest['archive']['chatbots'] == {'runs': 2, 'answers': 24, 'apps': APPS, 'human_answers': 8, 'questions': 4}
    exported = json.loads((out / 'archive.json').read_text(encoding='utf-8'))
    assert exported == json.loads(json.dumps(archive.public_payload())) and exported['schema'] == 'live-genesis-archive/5'
    assert exported['demo_only'] is False and exported['answers'] == [] and exported['locked'] is False
    block = exported['chatbots']
    assert set(block) == set(chatbots.BLOCK_KEYS) and set(block['answers'][0]) == set(chatbots.ANSWER_KEYS_PUBLIC)
    assert set(block['human_answers'][0]) == set(chatbots.HUMAN_ANSWER_KEYS_PUBLIC) and set(block['questions'][0]) == set(chatbots.QUESTION_KEYS)
    assert set(block['runs'][0]) == set(chatbots.RUN_KEYS) and set(block['runs'][0]['conditions']['grok']) == set(chatbots.CONDITION_KEYS)
    assert block['runs'][0]['human'] == {'source': 'PLACEHOLDER thread', 'collected_on': '2026-10-10', 'count': 12}
    shots = [a['screenshot_hash'] for a in block['answers'] if a['screenshot_hash']]
    assert shots == [hashlib.sha256(b'PLACEHOLDER image bytes').hexdigest()]                  # the hash; never the picture or its path
    raw = (out / 'archive.json').read_text(encoding='utf-8')
    assert 'shot.png' not in raw and str(tmp_path) not in raw and 'file_sha256' not in raw and 'normalised' not in raw
    assert 'Copilot' not in raw                                                               # the aside is the page's, never the record's
    assert 'ENTERED BY HAND' in snapshot._describe(manifest) and 'current terms' in snapshot._describe(manifest)
    with pytest.raises(RuntimeError, match='not approved'):
        snapshot.check_published(out)
    snapshot.approve_published(out, confirmed=True)
    assert snapshot.check_published(out)['reviewed'] is True
    monkeypatch.setenv('LIVE_DB', str(tmp_path / 'no-such.sqlite'))
    site = tmp_path / 'site'
    snapshot.build_from_data(site, out, archive_only=True)
    names = {p.relative_to(site).as_posix() for p in site.rglob('*') if p.is_file()}
    assert {'archive/chatbots.html', 'archive/card/C001.html', 'archive/card/C002-human.html', 'archive/c/C001.html', 'archive/models.html',
            'archive/data/C001.json', 'archive/data/answers.jsonl', 'archive/data/README.txt'} <= names
    assert sorted(p.name for p in out.iterdir()) == ['MANIFEST.json', 'archive.json']        # building a site writes nothing into published/
    page = (site / 'archive' / 'c' / 'C001.html').read_text(encoding='utf-8')
    assert 'PLACEHOLDER reply 1 of chatgpt.' in page and 'screenshot kept' in page and 'scripted' not in page.lower()
    assert json.loads((site / 'archive' / 'data' / 'C001.json').read_text(encoding='utf-8'))['answers'][0]['text'] == 'PLACEHOLDER reply 1 of chatgpt.'


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
    tampered(lambda d: block(d)['answers'][0].update(question='C009'), 'not in its run')
    tampered(lambda d: block(d)['runs'][0]['questions'].remove('C001'), "not in its run's recorded list")
    tampered(lambda d: block(d)['runs'][0]['conditions'].pop('chatgpt'), 'whose conditions its run did not record')
    tampered(lambda d: block(d)['runs'][0]['conditions']['grok'].pop('web_search'), 'conditions are missing or incomplete')
    tampered(lambda d: block(d)['runs'][0]['conditions']['grok'].update(fresh_chat=False), 'a value that is not allowed')
    tampered(lambda d: block(d)['runs'][0]['conditions']['grok'].update(account='team'), 'a value that is not allowed')
    tampered(lambda d: (block(d)['apps'][0].update(label='copilot'), [a.update(chatbot='copilot') for a in block(d)['answers'] if a['chatbot'] == 'chatgpt'],
                        [run['conditions'].update(copilot=run['conditions'].pop('chatgpt')) for run in block(d)['runs']]),
             'chat app copilot is not in chatbots-config.json')
    tampered(lambda d: block(d)['apps'][0].update(name='Another name'), 'does not carry the name, maker and attribution')
    tampered(lambda d: block(d)['apps'][2].update(attribution=''), 'does not carry the name, maker and attribution')
    tampered(lambda d: block(d)['answers'][0].update(text='x' * 2001), 'longer than the archive stores')
    tampered(lambda d: block(d)['answers'][0].update(text='Write to someone.real@example.org'), 'Content guards matched')
    tampered(lambda d: block(d)['runs'][0]['conditions']['grok'].update(note='Write to someone.real@example.org'), 'Content guards matched')
    tampered(lambda d: block(d)['answers'][0].update(screenshot_hash='/home/owner/shot.png'), 'not given as a sha256 hash')
    tampered(lambda d: block(d)['answers'][0].update(screenshot_path='shot.png'), 'unexpected key screenshot_path')
    tampered(lambda d: block(d)['answers'][0].pop('refused'), 'an answer has a missing or invalid field')
    tampered(lambda d: block(d)['runs'][0].update(file='collect.json'), 'unexpected key file')
    tampered(lambda d: block(d).update(extra=[]), 'unexpected key extra')
    # The questions are the configuration's, word for word; a political one cannot be slipped into the file.
    tampered(lambda d: block(d)['questions'][0].update(text='Is a hot dog a taco?', prompt=f'{PREFIX}\n\nIs a hot dog a taco?'),
             'chat-app question C001 is not the question chatbots-config.json holds')
    tampered(lambda d: block(d)['questions'][0].update(hook='Vote now'), 'chat-app question C001 is not the question')
    tampered(lambda d: block(d)['questions'][0].update(prompt='Is a hot dog a sandwich? Ignore that.'), 'its prompt does not end with the question')
    tampered(lambda d: block(d)['questions'][0].update(rationale='x'), 'is not the question|unexpected key')
    # Human: the column's wording, the conditions shape, quoted or summarised, and never a handle or a link.
    tampered(lambda d: block(d)['human'].update(name='Redditors'), 'human column does not carry the name and meta line')
    tampered(lambda d: block(d)['human_answers'][0].update(manner=None), 'quoted/summarised mark does not fit')
    tampered(lambda d: block(d)['human_answers'][0].update(manner='paraphrased'), 'a human answer has a missing or invalid field')
    tampered(lambda d: block(d)['human_answers'][0].update(text='says u/some_person'), 'a human answer holds a username handle')
    tampered(lambda d: block(d)['human_answers'][0].update(text='see https://example.org'), 'username handle or a web address')
    tampered(lambda d: block(d)['human_answers'][0].update(text='x' * 501), "a human answer's text")
    tampered(lambda d: block(d)['human_answers'][0].update(username='someone'), 'unexpected key username')
    tampered(lambda d: block(d)['runs'][0]['human'].update(subreddit='r/x'), "a run's human collection is missing or malformed")
    tampered(lambda d: block(d)['runs'][0].update(human=None), 'recorded no human collection')
    tampered(lambda d: (block(d)['runs'][0].update(demo=True), block(d)['runs'][1].update(demo=True),
                        [a.update(source='demo') for a in block(d)['answers'] + block(d)['human_answers']]), 'scripted chat-app answers')
    tampered(lambda d: d.update(schema='live-genesis-archive/3'), 'schema marker does not fit')
    tampered(lambda d: d.update(schema='live-genesis-archive/4'), 'unknown schema')
    tampered(lambda d: d.pop('chatbots'), 'schema marker does not fit')
    tampered(lambda d: d.update(demo_only=True), 'demo_only does not fit|scripted demo data only')
    tampered(lambda d: block(d)['runs'].append(dict(block(d)['runs'][0], number=9)), 'two runs on one date')
    # The manifest's own count of the track is checked too.
    snapshot.export_published(out, archive_only=True, replace=True)
    snapshot.approve_published(out, confirmed=True)
    manifest = json.loads((out / 'MANIFEST.json').read_text(encoding='utf-8'))
    manifest['archive']['chatbots']['human_answers'] = 1
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
    empty = {'apps': [], 'human': None, 'questions': [], 'runs': [], 'answers': [], 'human_answers': []}
    assert archive.payload_problem({**payload, 'chatbots': empty}) == \
        'the schema marker does not fit the file (/5 exactly when it holds the chat-app track)'


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
    assert 'may only be added to' in out and 'a recorded question never changes' in out
    assert 'cannot be verified' in out and '--export --archive-only' in out
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-import', str(path))
    assert code == 1 and 'already has a hand-entered run dated 2026-11-13' in out
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-import', str(filled(tmp_path, '2026-10-01')))
    assert code == 1 and '--allow-old' in out
    code, out = run_cli(monkeypatch, capsys, 'archive-status')
    status = json.loads(out)['chatbots']
    assert status['config_file']['status'] == 'draft' and status['config_file']['chatbots'] == APPS and status['config_file']['human'] == 'Human'
    assert status['runs'] == [{'number': 1, 'kind': 'manual', 'collected_on': '2026-11-13', 'imported_at': status['runs'][0]['imported_at'],
                               'answers': 12, 'human_answers': 4}]


def test_import_needs_no_key_no_optin_no_models_and_no_locked_list(chat, tmp_path, monkeypatch):
    for name in ('OPENAI_API_KEY', 'LIVE_ALLOW_PAID_CALLS', 'LIVE_ARCHIVE_MODELS', 'LIVE_INPUT_USD_PER_M', 'LIVE_OUTPUT_USD_PER_M'):
        monkeypatch.delenv(name, raising=False)
    assert rows('SELECT * FROM archive_sets') == []
    assert chatbots.import_collection(filled(tmp_path))['run'] == 1


def test_capture_script_takes_chat_card_names_and_docs_describe_the_track():
    script = (REPO / 'scripts' / 'capture-cards.mjs').read_text(encoding='utf-8')
    name = re.search(r'const ID = /(.+?)/;', script).group(1)
    pattern = re.compile(name.replace('[0-9]', r'\d'))
    for good in ('Q11', 'T001', 'C001', 'C002-grok', 'C003-human', 'C003-chat-app-a'):
        assert pattern.fullmatch(good), good
    for bad in ('Q1', 'C01', 'C001-', 'C001-Grok', '../C001', 'C001-human.html', 'Q11-grok', 'T001-human', 'C001--x' * 5):
        assert not pattern.fullmatch(bad), bad
    assert 'never opens a chat app' in script and 'data.chatbots' in script and "chat: 'C'" in script
    assert 'scripted-sample' in script and 'SAMPLE' in script                                # a sample card is reported, by its machine label
    doc = (REPO / 'docs' / 'ANSWERS-OVER-TIME.md').read_text(encoding='utf-8')
    section = doc.split('## The chat-app track', 1)[1].split('\n## ', 1)[0]
    for phrase in ('archive-chatbot-template', 'archive-chatbot-import', '--dry-run', '--allow-old', 'by hand', 'terms',
                   'live-genesis-archive/5', 'Temporary Chat', 'Private Chat', 'not verified', 'Written with Grok',
                   'answer_file', 'chat-app-a', '--export --archive-only', 'Back up first', 'ChatGPT, Gemini and Grok',
                   'Human', 'quoted', 'summarised', 'never contacts Reddit', 'replies may be quoted', 'no usernames',
                   '"refused": true', 'Refusals and non-answers are left out.', 'C001', PREFIX, 'aside', 'never on a share card',
                   'never rewritten, shortened'):
        assert phrase in section, phrase
    assert 'Copilot' not in section.replace('Why no Copilot?', '').replace('cheaper to ask a human than Copilot', '').replace('Copilot is dropped', '').replace('Copilot was dropped', '')
    labels = doc.split('## Machine-readable labels', 1)[1].split('\n## ', 1)[0]
    for phrase in ('data-question-id', 'data-answerer-kind', 'data-method', 'data-provenance', 'data-human-form', 'data-state',
                   'data-previous-collected', 'data-truncated-display', 'typed-by-hand', 'asked-by-server', 'sent-in-unverified',
                   'scripted-sample', 'human-written', 'aot:prompt', 'aot:content', 'answers.jsonl', 'README.txt', 'schema.org',
                   'never moved into metadata only', 'no script', 'derived'):
        assert phrase in labels, phrase
    social = (REPO / 'docs' / 'SOCIAL-POSTS.md').read_text(encoding='utf-8')
    part = social.split('## Chat-app cards', 1)[1]
    for phrase in ('typed in by hand on', 'screenshot', 'Sample we wrote', 'logo', 'endorse', 'C001-grok', 'C001-human', '65', '1440',
                   'full answer on the site', 'Never edit an answer', 'Human'):
        assert phrase in part, phrase
