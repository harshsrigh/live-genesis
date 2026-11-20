"""Answers Over Time, the chat-app questions put to MODELS by software (`cli archive-chatbot-ask`,
live_genesis/archive_chatbots.py): the second kind of answerer, its command and safety gates, its storage and the
one-time table rebuild, its pages and its way through the reviewed export.

Offline: `engine.infer` AND `engine.REAL_INFER` are replaced by a stand-in in every test that asks, so nothing here
contacts a provider, and every "reply" is an obvious placeholder. The opt-in for model calls is set by the test
fixtures only (tests/conftest.py)."""
import datetime
import hashlib
import json
import re
import sqlite3

import pytest

from live_genesis import archive, archive_chatbots as chatbots, archive_pages, config, controls, core, engine, snapshot
from test_archive import REPO, ready, rows, run_cli  # noqa: F401
from test_archive_chatbots import (APPS, IDS, PREFIX, TODAY, assert_site_does_not_say_how_replies_were_collected, chat,  # noqa: F401
                                   config_file, filled, retarget, two_dates, visible)

V7_SCHEMA = REPO / 'tests' / 'fixtures' / 'archive_schema_v7.sql'
LOCAL = {'label': 'local-one', 'kind': 'model', 'name': 'Sample model L', 'maker': 'Made-up maker L', 'answerer': 'loc',
         'group': 'ai-models'}
API = {'label': 'api-one', 'kind': 'model', 'name': 'Sample model M', 'answerer': 'mini', 'group': 'ai-models'}
MODELS = 'loc=local:sample-local:1b,mini=openai:gpt-test-mini'
LOCAL_URL = 'http://localhost:11999/private-local-path/v1'
SECRET = 'SENTINEL-PROVIDER-TEXT-51c2 https://api.example.invalid/private-path/v1'
SECRETS = ('SENTINEL-PROVIDER-TEXT', 'example.invalid', 'private-path', 'private-local-path', 'localhost:11999', 'fake-key-never-used')
AI_MODELS = {'id': 'ai-models', 'name': 'AI models'}


def with_models(tmp_path, monkeypatch, models=(LOCAL, API), edit=None):
    def change(data):
        data['chatbots'] = data['chatbots'] + [dict(model) for model in models]
        if edit:
            edit(data)
    return config_file(tmp_path, monkeypatch, edit=change)


def adapter(monkeypatch, reply=None, calls=None):
    """Replace the adapter AND the identity it is compared with (as tests/test_archive.py `as_real` does), with a stand-in
    that also takes the `endpoint` and `sampling` a named endpoint is called with. `reply` is text, a Completion, an
    exception, or a function of (model, prompt, call number). Returns the list of calls."""
    seen = calls if calls is not None else []

    def infer(provider, model, prompt, *, system=None, max_output_tokens=None, **extra):
        seen.append({'provider': provider, 'model': model, 'prompt': prompt, 'system': system, 'max': max_output_tokens,
                     'endpoint': getattr(extra.get('endpoint'), 'name', None), 'sampling': extra.get('sampling')})
        value = reply(model, prompt, len(seen)) if callable(reply) else reply
        if value is None:
            value = f'PLACEHOLDER asked {model} {len(seen)}.'
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, engine.Completion):
            return value
        return engine.Completion(value, 40, 12, 'end', reported_model=f'{model}-r', finish_reason='stop', sampling=extra.get('sampling') or {})
    monkeypatch.setattr(engine, 'infer', infer)
    monkeypatch.setattr(engine, 'REAL_INFER', infer)
    return seen


@pytest.fixture
def asking(chat, tmp_path, monkeypatch):
    """The `chat` world plus two model answerers in the group "AI models": one on a local (zero-priced) named endpoint,
    one on a priced native provider. The adapter is still the `chat` fixture's, which fails the test if it is called."""
    with_models(tmp_path, monkeypatch)
    monkeypatch.setenv('LIVE_ARCHIVE_ENDPOINTS', 'local')
    monkeypatch.setenv(config.endpoint_variable('local', 'BASE_URL'), LOCAL_URL)
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', MODELS)
    return chat


def every_cell():
    """Every value of every table and the schema itself, as one string."""
    with core.connect() as cx:
        names = [r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return json.dumps([[tuple(r) for r in cx.execute(f'SELECT * FROM {name}')] for name in names], default=str) \
            + ' '.join(r[0] or '' for r in cx.execute('SELECT sql FROM sqlite_master'))


def nothing_stored(calls):
    assert calls == [] and rows('SELECT * FROM archive_chatbot_runs') == [] and rows('SELECT * FROM budget_reservations') == []
    assert rows('SELECT * FROM archive_chatbot_questions') == [] and rows('SELECT * FROM api_usage') == []


class Status(Exception):
    """Shaped like an SDK status error: a status code, and text that must never be stored or printed."""
    def __init__(self, status_code):
        super().__init__(f'response body with {SECRET}')
        self.status_code = status_code


# ---- the configuration: a second kind of answerer ------------------------------------------------------------

def test_a_model_group_without_an_answerer_shows_nowhere(chat, tmp_path):
    # The checked-in file keeps its apps free of `kind` and `answerer`; any model in it sits in the group "AI models".
    # The `chat` configuration is that file without its models: the group is then empty.
    raw = json.loads((REPO / 'live_genesis' / 'archive_chatbots.json').read_text(encoding='utf-8'))
    assert raw['groups'][1] == AI_MODELS and all(
        entry.get('group') == 'ai-models' and entry.get('answerer') if entry.get('kind') == 'model'
        else 'kind' not in entry and 'answerer' not in entry for entry in raw['chatbots'])
    plain = chatbots.load_config()
    assert plain['models'] == [] and [c['label'] for c in plain['chatbots']] == APPS and AI_MODELS in plain['groups']
    # The empty group does not even change the configuration hash a run records: it is what it was before the group.
    assert plain['sha256'] == hashlib.sha256(chatbots._canonical({
        'chatbots': plain['chatbots'], 'questions': plain['items'], 'human': plain['human'],
        'groups': [chatbots.DEFAULT_GROUP]})).hexdigest()
    assert all('kind' not in app for app in plain['chatbots'])
    # With real replies in the database the empty group is on no page, in no data file and not in the payload.
    two_dates(tmp_path)
    payload = archive.public_payload()
    assert payload['schema'] == 'live-genesis-archive/6' and payload['chatbots']['groups'] == [chatbots.DEFAULT_GROUP]
    site = archive_pages.render_site(chatbots.site_payload(payload), standalone=True)
    assert site and all('AI models' not in text and 'ai-models' not in text and 'g-radio' not in text and 'thread-name' not in text
                        for text in site.values())


def test_a_model_answerer_has_its_own_shape_and_never_an_apps_name(chat, tmp_path, monkeypatch):
    plain = chatbots.load_config()['sha256']
    with_models(tmp_path, monkeypatch)
    loaded = chatbots.load_config()
    assert [c['label'] for c in loaded['chatbots']] == APPS                              # the apps are the apps; a model is never one
    assert loaded['models'] == [
        {'label': 'local-one', 'name': 'Sample model L', 'maker': 'Made-up maker L', 'attribution': '', 'group': 'ai-models',
         'kind': 'model', 'answerer': 'loc'},
        {'label': 'api-one', 'name': 'Sample model M', 'maker': '', 'attribution': '', 'group': 'ai-models', 'kind': 'model',
         'answerer': 'mini'}]
    assert loaded['sha256'] != plain and chatbots.CONFIG_FORMAT == 'live-genesis-chatbots/3'
    assert chatbots.SOURCES == ('manual', 'demo', 'asked') and chatbots.ASKED == 'asked'
    # Reading the configuration reads no model settings: LIVE_ARCHIVE_MODELS is looked at only when a model is asked.
    monkeypatch.delenv('LIVE_ARCHIVE_MODELS', raising=False)
    assert len(chatbots.load_config()['models']) == 2
    for change, why in [
        (dict(name='ChatGPT'), 'may not carry the name of a chat app \\("ChatGPT"\\)'),
        (dict(name='chatgpt 5 via API'), 'may not carry the name of a chat app'),
        (dict(name='The GROK model'), 'may not carry the name of a chat app \\("Grok"\\)'),
        (dict(name='Open Gemini-like'), 'may not carry the name of a chat app \\("Gemini"\\)'),
        (dict(attribution='Written with a model'), 'never attribution'),
        (dict(answerer=None), 'answerer must be a label of LIVE_ARCHIVE_MODELS'),
        (dict(answerer='Not A Label'), 'answerer must be a label of LIVE_ARCHIVE_MODELS'),
        (dict(answerer='mini'), 'two model answerers name the same answerer'),
        (dict(group='ai-apps'), 'group ai-apps holds both chat apps and model answerers'),
        (dict(group='nowhere'), 'its group is not one of the groups'),
        (dict(group=3), 'group a group id'),
        (dict(label='chatgpt'), 'labels and names must differ'),
        (dict(label='human'), 'a label must be'),
        (dict(label='ai-models'), 'a label may not be a group id'),
        (dict(name='Sample model M'), 'labels and names must differ'),
        (dict(name=''), 'name \\(and maker, when given\\) must be'),
        (dict(maker=''), 'name \\(and maker, when given\\) must be'),
        (dict(url='http://localhost:11434/v1'), 'a model answerer needs label, kind, name, answerer and group'),
        (dict(kind='bot'), 'each chat app needs label, name and maker'),
    ]:
        with_models(tmp_path, monkeypatch, [dict(LOCAL, **change), API])
        with pytest.raises(archive.ArchiveRefused, match=why):
            chatbots.load_config()
    for missing in ('answerer', 'group', 'name'):
        with_models(tmp_path, monkeypatch, [{key: value for key, value in LOCAL.items() if key != missing}])
        with pytest.raises(archive.ArchiveRefused, match='a model answerer needs label, kind, name, answerer and group'):
            chatbots.load_config()
    # `maker` is optional, and a group of models alone is a group like any other.
    with_models(tmp_path, monkeypatch, [API])
    assert chatbots.load_config()['models'][0]['maker'] == ''


def test_the_template_lists_no_model_answerer_and_an_import_refuses_answers_for_one(asking, tmp_path):
    data = chatbots.template('2026-11-14')
    assert [block['chatbot'] for block in data['chatbots']] == APPS and 'local-one' not in json.dumps(data) and 'Sample model' not in json.dumps(data)

    def smuggled(data):
        block = json.loads(json.dumps(data['chatbots'][0]))
        block.update(chatbot='local-one', name='Sample model L', maker='Made-up maker L')
        data['chatbots'].append(block)
    with pytest.raises(archive.ArchiveRefused, match=r'local-one: is a model asked by software \(cli archive-chatbot-ask\); a file cannot carry answers'):
        chatbots.import_collection(filled(tmp_path, edit=smuggled))
    assert rows('SELECT * FROM archive_chatbot_runs') == []
    assert chatbots.import_collection(filled(tmp_path))['run'] == 1                      # the file without it is fine


# ---- the command, end to end ---------------------------------------------------------------------------------

def test_ask_stores_one_run_with_the_model_facts_usage_and_settled_holds_and_fixes_the_questions(asking, tmp_path, monkeypatch):
    calls = adapter(monkeypatch)
    done = chatbots.ask(max_usd=1)
    assert done['run'] == 1 and done['status'] == 'complete' and done['stop_reason'] == '' and done['collected_on'] == '2026-11-14'
    assert done['first_run'] is True and done['new_questions'] == IDS and done['answers'] == {'answered': 8, 'no_answer': 0, 'withheld': 0}
    assert done['max_usd'] == 1.0 and 0 < done['estimated_usd'] < 0.001
    assert [(m['chatbot'], m['answerer'], m['endpoint'], m['model'], m['pricing_basis'], m['outcome'], m['recorded']) for m in done['models']] == [
        ('local-one', 'loc', 'local', 'sample-local:1b', 'local', 'complete', 4),         # zero-priced first, as on the model track
        ('api-one', 'mini', 'openai', 'gpt-test-mini', 'rates', 'complete', 4)]
    assert 'PLACEHOLDER' not in json.dumps(done)                                           # counts only, never a reply
    # Each question's exact prompt, once per model, with the model track's fixed system line; pinned sampling where it is sent.
    prompts = [item['prompt'] for item in chatbots.load_config()['items']]
    assert [c['prompt'] for c in calls] == prompts * 2 and all(p.startswith(PREFIX + '\n\n') for p in prompts)
    assert {c['system'] for c in calls} == {archive.SYSTEM_LINE} and [c['model'] for c in calls] == ['sample-local:1b'] * 4 + ['gpt-test-mini'] * 4
    assert [c['endpoint'] for c in calls] == ['local'] * 4 + [None] * 4
    assert [c['sampling'] for c in calls] == [{'temperature': 0, 'seed': 1234}] * 4 + [None] * 4
    # One run of kind `asked` dated today; the answers carry the new source and the reservation their call settled.
    run = rows('SELECT * FROM archive_chatbot_runs')
    assert len(run) == 1 and (run[0]['number'], run[0]['kind'], run[0]['collected_on'], run[0]['file_sha256']) == (1, 'asked', '2026-11-14', '')
    assert json.loads(run[0]['question_ids']) == IDS and run[0]['config_sha256'] == chatbots.load_config()['sha256']
    answers = rows('SELECT * FROM archive_chatbot_answers ORDER BY id')
    assert len(answers) == 8 and {a['source'] for a in answers} == {'asked'} and {a['state'] for a in answers} == {'answered'}
    assert [a['answer_text'] for a in answers[:2]] == ['PLACEHOLDER asked sample-local:1b 1.', 'PLACEHOLDER asked sample-local:1b 2.']
    assert sorted(a['reservation_id'] for a in answers) == list(range(1, 9)) and all(a['refused'] == 0 and a['screenshot_sha256'] is None for a in answers)
    assert [a['changed'] for a in answers] == [None] * 8
    # In place of an app's conditions: the requested id, provider, endpoint NAME, reported name, digest, pricing basis, sampling.
    facts = {r['chatbot']: r for r in rows('SELECT * FROM archive_chatbot_conditions')}
    assert set(facts) == {'local-one', 'api-one'}
    local = facts['local-one']
    assert (local['kind'], local['display_name'], local['maker'], local['group_id'], local['group_name']) == \
        ('model', 'Sample model L', 'Made-up maker L', 'ai-models', 'AI models')
    assert (local['model'], local['provider'], local['endpoint'], local['reported_model'], local['model_digest'], local['pricing_basis'],
            local['sampling'], local['local']) == ('sample-local:1b', 'openai-compatible', 'local', 'sample-local:1b-r', None, 'local',
                                                   'temperature=0;seed=1234', 1)
    api = facts['api-one']
    assert (api['model'], api['provider'], api['endpoint'], api['reported_model'], api['pricing_basis'], api['sampling'], api['local'], api['maker']) == \
        ('gpt-test-mini', 'openai', 'openai', 'gpt-test-mini-r', 'rates', 'none', 0, '')
    for row in facts.values():                                                           # none of an app's conditions
        assert all(row[key] is None for key in ('account', 'fresh_chat', 'temporary_chat', 'memory_off', 'web_search', 'country', 'app', 'shown_model'))
        assert row['attribution'] == '' and row['note'] == ''
    # Every hold settled, usage recorded; the zero-priced calls are recorded at $0.
    assert [r['status'] for r in rows('SELECT status FROM budget_reservations ORDER BY id')] == ['settled'] * 8
    usage = rows('SELECT model,input_tokens,output_tokens,estimated_usd FROM api_usage ORDER BY id')
    assert len(usage) == 8 and all((u['input_tokens'], u['output_tokens']) == (40, 12) for u in usage)
    assert [u['estimated_usd'] for u in usage[:4]] == [0] * 4 and all(u['estimated_usd'] > 0 for u in usage[4:])
    assert controls.unconfirmed_ids(sqlite3.connect(str(asking))) == []
    audit = [r['operation'] for r in rows('SELECT operation FROM operator_audit')]
    assert any(line.startswith('archive-chatbot-ask run 1: collected 2026-11-14, complete, local-one complete 4, api-one complete 4') for line in audit)
    # Nothing of the model track was written, and no key, URL or host is in any table.
    assert rows('SELECT * FROM archive_runs') == [] and rows('SELECT * FROM archive_answers') == []
    cells = every_cell()
    assert not [secret for secret in SECRETS if secret in cells]
    # The first recorded run of ANY kind fixes the question list: recorded with this run, never changed afterwards.
    recorded = rows('SELECT id,kind,first_run FROM archive_chatbot_questions ORDER BY position')
    assert [(q['id'], q['kind'], q['first_run']) for q in recorded] == [(i, 'asked', 1) for i in IDS]

    def reword(data):
        data['questions'][0].update(text='Is a hot dog a taco?', prompt=f'{PREFIX}\n\nIs a hot dog a taco?')
    with_models(tmp_path, monkeypatch, edit=reword)
    before = len(calls)
    with pytest.raises(archive.ArchiveRefused, match='C001 was changed since it was first imported'):
        chatbots.import_collection(filled(tmp_path))
    monkeypatch.setattr(chatbots, 'today', lambda: datetime.date(2026, 11, 15))
    with pytest.raises(archive.ArchiveRefused, match='C001 was changed since it was first imported.*No paid call was made'):
        chatbots.ask(max_usd=1)
    with_models(tmp_path, monkeypatch, edit=lambda data: data['questions'].pop())
    with pytest.raises(archive.ArchiveRefused, match='no longer lists C004'):
        chatbots.ask(max_usd=1)
    assert len(calls) == before
    # Hand-entered and asked runs share the database: the import is run 2, and it is not "the first" any more.
    with_models(tmp_path, monkeypatch)
    monkeypatch.setattr(chatbots, 'today', lambda: TODAY)
    imported = chatbots.import_collection(filled(tmp_path))
    assert imported['run'] == 2 and imported['first_import'] is False and imported['new_questions'] == []
    assert [(r['number'], r['kind']) for r in rows('SELECT number,kind FROM archive_chatbot_runs ORDER BY id')] == [(1, 'asked'), (2, 'manual')]


def test_one_run_per_model_answerer_and_day_dates_only_move_forward_and_changed_is_the_apps_rule(asking, tmp_path, monkeypatch):
    calls = adapter(monkeypatch, lambda model, prompt, n: f'PLACEHOLDER first {model}.')
    assert chatbots.ask(max_usd=1)['run'] == 1
    with pytest.raises(archive.ArchiveRefused, match='local-one already has a run dated 2026-11-14. One run per model answerer and day'):
        chatbots.ask(max_usd=1)
    monkeypatch.setattr(chatbots, 'today', lambda: datetime.date(2026, 11, 13))
    with pytest.raises(archive.ArchiveRefused, match='already has a run dated 2026-11-14.*the dates only move forward'):
        chatbots.ask(max_usd=1, only='api-one')
    assert len(calls) == 8 and len(rows('SELECT * FROM archive_chatbot_runs')) == 1
    # The database enforces the same rule on its own, and keeps a label one kind of answerer for good.
    with core.connect() as cx:
        cx.execute("INSERT INTO archive_chatbot_runs(number,kind,collected_on,imported_at,question_ids,config_sha256) "
                   "VALUES (9,'asked','2026-11-14','2026-11-14T10:00:00+00:00','[\"C001\"]',?)", ('a' * 64,))
        run_id = cx.execute('SELECT MAX(id) FROM archive_chatbot_runs').fetchone()[0]
        model_row = ("INSERT INTO archive_chatbot_conditions(run_id,chatbot,display_name,maker,kind,model,provider,endpoint,pricing_basis,local) "
                     "VALUES (?,?,'x','','model','m','openai','openai','rates',0)")
        with pytest.raises(sqlite3.IntegrityError, match='already has a run on this date or a later one'):
            cx.execute(model_row, (run_id, 'local-one'))
        cx.rollback()
    # A later day: the reply is compared with that answerer's previous shown reply, exactly as an app's is.
    monkeypatch.setattr(chatbots, 'today', lambda: datetime.date(2026, 12, 14))
    adapter(monkeypatch, lambda model, prompt, n: 'PLACEHOLDER second.' if n == 1 else f'placeholder FIRST {model}')
    assert chatbots.ask(max_usd=1)['run'] == 2
    later = rows("SELECT a.chatbot,a.question_id,a.changed FROM archive_chatbot_answers a JOIN archive_chatbot_runs r ON r.id=a.run_id "
                 "WHERE r.number=2 ORDER BY a.id")
    assert (later[0]['chatbot'], later[0]['question_id'], later[0]['changed']) == ('local-one', 'C001', 1)
    assert [row['changed'] for row in later[1:]] == [0] * 7                             # case and end punctuation do not count
    # --models asks a subset; a label that is not a model answerer is refused.
    monkeypatch.setattr(chatbots, 'today', lambda: datetime.date(2026, 12, 15))
    for only in ('chatgpt', 'nobody', ' , '):
        with pytest.raises(archive.ArchiveRefused, match='--models names a label that is not a model answerer'):
            chatbots.ask(max_usd=1, only=only)
    done = chatbots.ask(max_usd=1, only='api-one')
    assert done['run'] == 3 and [m['chatbot'] for m in done['models']] == ['api-one'] and done['first_run'] is False


def test_the_two_kinds_of_answerer_are_kept_apart_by_the_tables(asking, tmp_path, monkeypatch):
    adapter(monkeypatch)
    chatbots.ask(max_usd=1)                                                              # run 1: asked
    chatbots.import_collection(filled(tmp_path, human=False))                            # run 2: manual
    app_row = ("INSERT INTO archive_chatbot_conditions(run_id,chatbot,display_name,maker,account,fresh_chat,temporary_chat,memory_off,"
               "web_search,country,app,shown_model{more}) VALUES (?,?,'x','y','free_account',1,1,'true','off','US','web','x'{values})")
    model_row = ("INSERT INTO archive_chatbot_conditions(run_id,chatbot,display_name,maker,kind,model,provider,endpoint,pricing_basis,local{more}) "
                 "VALUES (?,?,'x','','model','m','openai','openai','rates',0{values})")
    attempts = [
        (app_row.format(more='', values=''), (1, 'new-app'), 'a model answerer belongs to an asked run'),            # an app in an asked run
        (model_row.format(more='', values=''), (2, 'new-model'), 'a model answerer belongs to an asked run'),        # a model in a manual run
        (app_row.format(more=',kind', values=",'model'"), (1, 'half'), 'CHECK constraint failed'),                    # a model with conditions
        (app_row.format(more=',model', values=",'m'"), (2, 'half'), 'CHECK constraint failed'),                       # an app with a model id
        (model_row.format(more=',account', values=",'free_account'"), (1, 'half'), 'CHECK constraint failed'),
        (model_row.format(more=',attribution', values=",'Written with x'"), (1, 'half'), 'CHECK constraint failed'),
        (model_row.replace("'openai','rates'", "'http://localhost/v1','paid'").format(more='', values=''), (1, 'half'), 'CHECK constraint failed'),
        ("INSERT INTO archive_human_collections(run_id,display_name,meta,source,collected_on) VALUES (?,'Human','m','s',?)", (1, '2026-11-14'),
         'an asked run holds no human collection'),
        ("INSERT INTO archive_chatbot_answers(run_id,question_set,question_id,chatbot,source,state,created_at) VALUES (?,'chat','C001',?,'manual','no_answer','x')",
         (1, 'local-one'), 'does not match the kind of its run'),
        ("INSERT INTO archive_chatbot_answers(run_id,question_set,question_id,chatbot,source,state,created_at) VALUES (?,'chat','C001',?,'asked','no_answer','x')",
         (1, 'local-one'), 'CHECK constraint failed'),                                                                # asked without a reservation
        ("INSERT INTO archive_chatbot_answers(run_id,question_set,question_id,chatbot,source,state,created_at,reservation_id) "
         "VALUES (?,'chat','C001',?,'manual','no_answer','x',5)", (2, 'chatgpt'), 'CHECK constraint failed'),          # a reservation on a hand-entered row
        ("UPDATE archive_chatbot_conditions SET reported_model='x' WHERE run_id=? AND chatbot=?", (1, 'local-one'), 'append-only'),
        ("DELETE FROM archive_chatbot_answers WHERE run_id=? AND chatbot=?", (1, 'local-one'), 'append-only'),
    ]
    for sql, values, why in attempts:
        with core.connect() as cx, pytest.raises(sqlite3.IntegrityError, match=why):
            cx.execute(sql, values)
    # A label is one kind for good, in the tables and in the two commands' own messages.
    with core.connect() as cx:
        cx.execute("INSERT INTO archive_chatbot_runs(number,kind,collected_on,imported_at,question_ids,config_sha256) "
                   "VALUES (8,'manual','2027-01-01','x','[\"C001\"]',?)", ('a' * 64,))
        with pytest.raises(sqlite3.IntegrityError, match='already belongs to the other kind of answerer'):
            cx.execute(app_row.format(more='', values=''), (cx.execute('SELECT MAX(id) FROM archive_chatbot_runs').fetchone()[0], 'local-one'))
        cx.rollback()
    monkeypatch.setattr(chatbots, 'today', lambda: datetime.date(2026, 11, 20))
    with_models(tmp_path, monkeypatch, [dict(LOCAL, label='grok-two', name='Sample model G')],
                edit=lambda data: data['chatbots'].__setitem__(2, {'label': 'local-one', 'name': 'Sample app', 'maker': 'Made-up maker'}))
    with pytest.raises(archive.ArchiveRefused, match='local-one is recorded in this database as a model asked by software'):
        chatbots.import_collection(filled(tmp_path, '2026-11-20', human=False))
    with_models(tmp_path, monkeypatch, [dict(LOCAL, label='chatgpt', name='Sample model C')], edit=lambda data: data['chatbots'].pop(0))
    with pytest.raises(archive.ArchiveRefused, match='chatgpt is recorded in this database as a chat app'):
        chatbots.ask(max_usd=1)
    # Scripted demo data shares a database with neither kind of recorded run.
    with pytest.raises(archive.ArchiveRefused):
        archive.demo_archive()
    with core.connect() as cx, pytest.raises(sqlite3.IntegrityError, match='never mixed'):
        cx.execute("INSERT INTO archive_chatbot_runs(number,kind,collected_on,imported_at,question_ids,config_sha256) "
                   "VALUES (7,'demo','2027-02-01','x','[]',?)", ('a' * 64,))


# ---- every safety gate ---------------------------------------------------------------------------------------

def test_dry_run_prints_the_plan_and_calls_nothing_even_without_the_switch(asking, monkeypatch, capsys):
    monkeypatch.delenv('LIVE_ALLOW_PAID_CALLS')
    calls = adapter(monkeypatch, AssertionError('a dry run calls no model'))
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', '--dry-run')
    assert code == 0 and 'DRY RUN: nothing was called and nothing was stored.' in out
    for line in ('Run dated 2026-11-14.', 'local-one = local:sample-local:1b, shown as "Sample model L", local (recorded as $0 estimated)',
                 'api-one = openai:gpt-test-mini, shown as "Sample model M", priced at its rates', 'C001  Is a hot dog a sandwich?',
                 'Calls: 8 (4 questions x 2 models).', 'Worst-case estimate: $0.00', 'estimate', 'If a call fails:',
                 'python -m live_genesis.cli archive-chatbot-ask --max-usd 0.01'):
        assert line in out, line
    assert not [secret for secret in SECRETS if secret in out] and 'PAID RUN' not in out
    nothing_stored(calls)
    planned = chatbots.ask_plan('api-one')
    assert planned['calls'] == 4 and [m['label'] for m in planned['models']] == ['api-one'] and 0 < planned['worst_case_usd'] < 0.01
    assert planned['worst_case_usd'] == pytest.approx(sum(planned['holds']['api-one'].values()))
    nothing_stored(calls)


def test_every_gate_refuses_before_any_call_and_before_anything_is_stored(asking, tmp_path, monkeypatch, capsys):
    # The adapter was replaced but is not "the real one" (the `chat` fixture): there is no mock mode, so nothing is stored.
    with pytest.raises(archive.ArchiveRefused, match='The inference adapter was replaced. archive-chatbot-ask has no mock mode'):
        chatbots.ask(max_usd=1)
    calls = adapter(monkeypatch, AssertionError('refused before any call'))
    # The switch: required for every run, a free one included; never set by the code.
    monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS', 'yes')
    with pytest.raises(archive.ArchiveRefused, match='Explicit opt-in required for paid calls: set LIVE_ALLOW_PAID_CALLS=YES'):
        chatbots.ask(max_usd=1, only='local-one')
    monkeypatch.delenv('LIVE_ALLOW_PAID_CALLS')
    with pytest.raises(archive.ArchiveRefused, match='Explicit opt-in required'):
        chatbots.ask(max_usd=1)
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', '--max-usd', '1')
    assert code == 1 and 'Explicit opt-in required' in out
    monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS', 'YES')                                    # in this test only
    # --max-usd: required, finite, positive; and the worst case must fit under it before the first call.
    for bad in (None, 0, -1, float('nan'), float('inf'), True, '1'):
        with pytest.raises(archive.ArchiveRefused, match='--max-usd is required and must be a finite positive number'):
            chatbots.ask(max_usd=bad)
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask')
    assert code == 1 and '--max-usd is required' in out and 'PAID RUN' not in out
    with pytest.raises(archive.ArchiveRefused, match=r'Worst-case estimate \$0\.\d+ for 8 calls exceeds --max-usd \$0.000001. No paid call was made'):
        chatbots.ask(max_usd=0.000001)
    # The local ceiling and the budget settings (fail closed).
    monkeypatch.setenv('LIVE_SPEND_LIMIT_USD', '0.0001')
    with pytest.raises((archive.ArchiveRefused, controls.ConfigError), match='ceiling|LIVE_SPEND_LIMIT_USD'):
        chatbots.ask(max_usd=1)
    monkeypatch.setenv('LIVE_SPEND_LIMIT_USD', 'ten')
    with pytest.raises((archive.ArchiveRefused, controls.ConfigError)):
        chatbots.ask(max_usd=1)
    monkeypatch.setenv('LIVE_SPEND_LIMIT_USD', '10')
    # The model settings: the answerer must be in LIVE_ARCHIVE_MODELS, its endpoint known, its key present.
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', 'mini=openai:gpt-test-mini')
    with pytest.raises(archive.ArchiveRefused, match=r'LIVE_ARCHIVE_MODELS does not list the answerer of: local-one \(answerer loc\)'):
        chatbots.ask(max_usd=1)
    monkeypatch.delenv('LIVE_ARCHIVE_MODELS')
    with pytest.raises(archive.ArchiveRefused, match='No models configured. Set LIVE_ARCHIVE_MODELS'):
        chatbots.ask(max_usd=1)
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', MODELS)
    monkeypatch.delenv('OPENAI_API_KEY')
    with pytest.raises(archive.ArchiveRefused, match='OPENAI_API_KEY'):
        chatbots.ask(max_usd=1)
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    monkeypatch.delenv(config.endpoint_variable('local', 'BASE_URL'))
    with pytest.raises(archive.ArchiveRefused) as err:
        chatbots.ask(max_usd=1)
    assert 'local' in str(err.value) and not [secret for secret in SECRETS if secret in str(err.value)]
    monkeypatch.setenv(config.endpoint_variable('local', 'BASE_URL'), LOCAL_URL)
    # No model answerer in the configuration (as checked in): nothing to ask.
    config_file(tmp_path, monkeypatch)
    with pytest.raises(archive.ArchiveRefused, match='No model answerer is configured'):
        chatbots.ask(max_usd=1)
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', '--dry-run')
    assert code == 1 and 'No model answerer is configured' in out
    with_models(tmp_path, monkeypatch)
    nothing_stored(calls)
    # An unconfirmed charge blocks every paid call until the owner reconciles it.
    reservation = core.reserve_budget(None, 'gpt-test-mini', 0.001, 10.0, provider='openai', input_rate=0.2, output_rate=1.2)
    core.close_reservation(reservation, 'unconfirmed')
    with pytest.raises(archive.ArchiveRefused, match=r'Unconfirmed provider charge \(reservation 1\): no further paid calls'):
        chatbots.ask(max_usd=1)
    assert calls == [] and rows('SELECT * FROM archive_chatbot_runs') == []
    assert out.count('PLACEHOLDER') == 0


def test_a_database_with_scripted_demo_answers_is_refused(asking, monkeypatch):
    archive.demo_archive()
    calls = adapter(monkeypatch, AssertionError('refused before any call'))
    with pytest.raises(archive.ArchiveRefused, match='This database holds scripted demo answers; real replies are never mixed with them'):
        chatbots.ask(max_usd=1)
    assert calls == [] and rows("SELECT * FROM archive_chatbot_runs WHERE kind!='demo'") == [] and rows('SELECT * FROM budget_reservations') == []


def test_a_failed_priced_call_is_unconfirmed_stops_the_run_and_blocks_the_next(asking, monkeypatch, capsys):
    calls = adapter(monkeypatch, lambda model, prompt, n: Status(500) if n == 6 else None)
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', '--max-usd', '1')
    done = json.loads(out[out.index('{\n "run"'):out.index('\n}\n') + 3])
    assert code == 0 and 'RUN INCOMPLETE: inference failed;' in out and 'PAID RUN: provider requests follow.' in out
    assert done['status'] == 'incomplete' and 'kept as unconfirmed (reservation 6)' in done['stop_reason'] and 'reconcile --receipt 6' in done['stop_reason']
    assert len(calls) == 6                                                                # no further call
    assert [(m['chatbot'], m['outcome'], m['reason'], m['recorded'], m['reservation']) for m in done['models']] == [
        ('local-one', 'complete', '', 4, None), ('api-one', 'stopped', 'server_error', 1, 6)]
    assert [r['status'] for r in rows('SELECT status FROM budget_reservations ORDER BY id')] == ['settled'] * 5 + ['unconfirmed']
    assert rows('SELECT COUNT(*) n FROM archive_chatbot_answers')[0]['n'] == 5 and rows('SELECT COUNT(*) n FROM api_usage')[0]['n'] == 5
    # The provider's text (it can quote a key or a URL) is nowhere: not printed, not returned, not in any table.
    assert not [secret for secret in SECRETS if secret in out + every_cell()]
    with pytest.raises(archive.ArchiveRefused, match=r'Unconfirmed provider charge \(reservation 6\)'):
        chatbots.ask(max_usd=1, only='api-one')
    archive.lock()
    with pytest.raises(archive.ArchiveRefused, match='Unconfirmed provider charge'):       # the model track's paid command too
        archive.run_archive(max_usd=1)
    assert len(calls) == 6


def test_a_failed_priced_first_call_stores_nothing_of_that_model(asking, monkeypatch):
    calls = adapter(monkeypatch, Status(401))
    done = chatbots.ask(max_usd=1, only='api-one')
    assert done['run'] is None and done['status'] == 'incomplete' and done['answers'] == {'answered': 0, 'no_answer': 0, 'withheld': 0}
    assert done['models'][0]['reason'] == 'auth' and len(calls) == 1
    assert rows('SELECT * FROM archive_chatbot_runs') == [] and rows('SELECT * FROM archive_chatbot_questions') == []
    assert [r['status'] for r in rows('SELECT status FROM budget_reservations')] == ['unconfirmed']
    audit = rows('SELECT operation FROM operator_audit')[-1]['operation']
    assert audit.startswith('archive-chatbot-ask (nothing stored): collected 2026-11-14, incomplete, api-one stopped 0')
    assert not [secret for secret in SECRETS if secret in json.dumps(done) + every_cell()]


def test_a_failed_zero_priced_call_releases_its_hold_skips_that_model_and_the_run_goes_on(asking, monkeypatch):
    calls = adapter(monkeypatch, lambda model, prompt, n: Status(404) if model == 'sample-local:1b' else None)
    done = chatbots.ask(max_usd=1)
    assert done['status'] == 'complete_with_gaps' and done['stop_reason'] == 'skipped, no further question asked of: local-one (not_found)'
    assert [(m['chatbot'], m['outcome'], m['reason'], m['recorded']) for m in done['models']] == [
        ('local-one', 'skipped', 'not_found', 0), ('api-one', 'complete', '', 4)]
    assert len(calls) == 5 and [r['status'] for r in rows('SELECT status FROM budget_reservations ORDER BY id')] == ['released'] + ['settled'] * 4
    assert [r['chatbot'] for r in rows('SELECT chatbot FROM archive_chatbot_conditions')] == ['api-one']   # the skipped model has no row at all
    assert any('local-one skipped (not_found); $0 hold 1 released (pricing basis local)' in r['operation'] for r in rows('SELECT operation FROM operator_audit'))
    assert not [secret for secret in SECRETS if secret in json.dumps(done) + every_cell()]
    # Nothing blocks the next paid call, and the skipped model can be asked on its own the same day.
    adapter(monkeypatch)
    again = chatbots.ask(max_usd=1, only='local-one')
    assert again['run'] == 2 and again['status'] == 'complete' and again['first_run'] is False
    # A zero-priced call that is not ready, or comes back without usable usage, is skipped the same way.
    monkeypatch.setattr(chatbots, 'today', lambda: datetime.date(2026, 11, 15))
    for failure, category in ((engine.ProviderNotReady('local setup'), 'not_ready'), (engine.Completion('PLACEHOLDER.', -1, 12, 'end'), 'no_usage')):
        adapter(monkeypatch, failure)
        done = chatbots.ask(max_usd=1, only='local-one')
        assert done['run'] is None and done['status'] == 'complete_with_gaps' and done['models'][0]['reason'] == category
    assert 'unconfirmed' not in {r['status'] for r in rows('SELECT status FROM budget_reservations')}


def test_an_interrupted_priced_call_is_unconfirmed_and_blocks_the_next_run(asking, monkeypatch):
    def interrupted(model, prompt, n):
        if model == 'gpt-test-mini':
            raise KeyboardInterrupt
    calls = adapter(monkeypatch, interrupted)
    with pytest.raises(KeyboardInterrupt):
        chatbots.ask(max_usd=1)
    assert len(calls) == 5
    assert [r['status'] for r in rows('SELECT status FROM budget_reservations ORDER BY id')] == ['settled'] * 4 + ['unconfirmed']
    assert rows('SELECT COUNT(*) n FROM archive_chatbot_answers')[0]['n'] == 4            # what was settled before is kept
    assert rows('SELECT operation FROM operator_audit')[-1]['operation'].startswith(
        'archive-chatbot-ask run 1: collected 2026-11-14, incomplete, local-one complete 4, api-one stopped 0')
    adapter(monkeypatch, AssertionError('blocked'))
    monkeypatch.setattr(chatbots, 'today', lambda: datetime.date(2026, 11, 15))
    with pytest.raises(archive.ArchiveRefused, match=r'Unconfirmed provider charge \(reservation 5\)'):
        chatbots.ask(max_usd=1)
    # An interrupted ZERO-priced call leaves no unconfirmed charge behind (nothing could be charged).
    controls.reconcile_reservation(5, 0)
    adapter(monkeypatch, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        chatbots.ask(max_usd=1, only='local-one')
    assert 'unconfirmed' not in {r['status'] for r in rows('SELECT status FROM budget_reservations')}


def test_usage_above_the_hold_and_a_priced_call_without_usage_stop_the_run(asking, monkeypatch):
    adapter(monkeypatch, engine.Completion('PLACEHOLDER.', 40, 12, 'end'))
    done = chatbots.ask(max_usd=1, only='local-one')
    assert done['status'] == 'complete' and rows('SELECT reported_model,sampling FROM archive_chatbot_conditions') == [
        {'reported_model': None, 'sampling': None}]                                       # the service did not say: recorded as not known
    # Usage above the hold: the reply is kept, the reservation is unconfirmed, and no further call is made.
    calls = adapter(monkeypatch, engine.Completion('PLACEHOLDER.', 10 ** 6, 10 ** 6, 'end'))
    done = chatbots.ask(max_usd=1, only='api-one')
    assert done['status'] == 'incomplete' and done['stop_reason'].startswith('recorded usage exceeded the hold; reservation 5 is unconfirmed.')
    assert len(calls) == 1 and done['run'] == 2 and done['models'][0]['recorded'] == 1 and done['models'][0]['reservation'] == 5
    assert rows('SELECT status FROM budget_reservations WHERE id=5') == [{'status': 'unconfirmed'}]
    with pytest.raises(archive.ArchiveRefused, match=r'Unconfirmed provider charge \(reservation 5\)'):
        chatbots.ask(max_usd=1, only='api-one')
    controls.reconcile_reservation(5, 1.4)
    # A priced call that comes back without usable usage: unconfirmed, stop, and nothing of that reply is stored.
    monkeypatch.setattr(chatbots, 'today', lambda: datetime.date(2026, 11, 15))
    calls = adapter(monkeypatch, engine.Completion('PLACEHOLDER.', None, 12, 'end'))
    done = chatbots.ask(max_usd=1, only='api-one')
    assert done['status'] == 'incomplete' and 'usage was missing or invalid' in done['stop_reason'] and len(calls) == 1 and done['run'] is None
    assert rows('SELECT status FROM budget_reservations ORDER BY id DESC LIMIT 1') == [{'status': 'unconfirmed'}]
    assert rows("SELECT COUNT(*) n FROM archive_chatbot_answers WHERE chatbot='api-one'")[0]['n'] == 1


# ---- the reply itself: the same judgement as a pasted reply --------------------------------------------------

def test_an_asked_reply_is_judged_like_an_imported_one_and_a_withheld_reply_is_stored_without_text_and_never_shown(asking, monkeypatch):
    assert chatbots.classify_asked('  PLACEHOLDER.  ', 'end') == chatbots.classify_reply('  PLACEHOLDER.  ') == ('answered', '', 'PLACEHOLDER.')
    assert chatbots.classify_asked('PLACEHOLDER cut', 'length') == ('no_answer', archive.TRUNCATED, None)
    long = 'x' * 1500                                                                    # longer than the model track stores, fine here
    replies = {1: 'Write to someone.real@example.org for the answer.', 2: engine.Completion('PLACEHOLDER cut off', 40, 120, 'length'),
               3: 'y' * 2001, 4: long, 5: '   ', 6: 'PLACEHOLDER\u200bhidden character.'}
    adapter(monkeypatch, lambda model, prompt, n: replies.get(n))
    done = chatbots.ask(max_usd=1)
    assert done['status'] == 'complete' and done['answers'] == {'answered': 3, 'no_answer': 3, 'withheld': 2}
    stored = rows('SELECT chatbot,question_id,state,reason,answer_text,normalised,answer_sha256,changed FROM archive_chatbot_answers ORDER BY id')
    assert [(s['state'], s['reason']) for s in stored[:6]] == [
        ('withheld', 'looks like an email address'), ('no_answer', archive.TRUNCATED), ('no_answer', archive.TOO_LONG), ('answered', ''),
        ('no_answer', archive.EMPTY), ('withheld', 'invisible or control character')]
    assert stored[3]['answer_text'] == long and all(s['answer_text'] is None and s['normalised'] is None for s in stored[:3] + stored[4:6])
    cells = every_cell()
    assert 'someone.real' not in cells and 'PLACEHOLDER cut off' not in cells and 'y' * 50 not in cells
    assert 'someone.real' not in json.dumps(done)
    # The page shows the answered reply; a withheld or missing one is on no page, card or data text.
    payload = archive.public_payload()
    assert archive.payload_problem(payload) is None
    site = archive_pages.render_site(chatbots.site_payload(payload), standalone=True)
    everything = ' '.join(site.values())
    assert 'someone.real' not in everything and 'PLACEHOLDER cut off' not in everything and long in site['c/C004.html']
    record = json.loads(site['data/C001.json'])['answers'][0]
    assert (record['answerer'], record['state'], record['shown'], record['text']) == ('local-one', 'hidden', False, None)
    assert snapshot._scan(chatbots.site_payload(payload)) == []
    # Only models answered here, so the top bar says "AI models" on every page, never "Chat apps".
    bars = [text.split('<nav aria-label="Archive navigation">')[1].split('</nav>')[0] for name, text in site.items()
            if name.endswith('.html') and '<nav aria-label="Archive navigation">' in text]
    assert bars and all('>AI models</a>' in bar and 'Chat apps' not in bar for bar in bars)


# ---- upgrade: a database made by the version before model answerers ------------------------------------------

def test_a_database_made_before_model_answerers_is_rebuilt_once_and_keeps_every_row(asking, tmp_path, monkeypatch):
    old = V7_SCHEMA.read_text(encoding='utf-8')
    assert "'asked'" not in old and "kind IN ('manual','demo')" in old and 'reservation_id' not in old and old.count('CREATE TABLE IF NOT EXISTS') == 6
    # Two hand-entered runs (the second with a refusal, a skipped question and a changed answer), then the same rows in a
    # database as the version before made it: its tables, its index and its triggers (the fixture), every row put in under
    # that version's own rules.
    config_file(tmp_path, monkeypatch)
    two_dates(tmp_path)
    payload_before = archive.public_payload()
    tables = chatbots.TABLE_NAMES + ('operator_audit', 'budget_reservations', 'api_usage')
    with core.connect() as cx:
        saved = {t: [dict(r) for r in cx.execute(f'SELECT * FROM {t} ORDER BY rowid')] for t in chatbots.TABLE_NAMES}
        for name, kind in cx.execute("SELECT name,type FROM sqlite_master WHERE (name LIKE 'archive_chatbot_%' OR name LIKE 'archive_human_%') "
                                     "AND type IN ('trigger','index') AND name NOT LIKE 'sqlite_%'").fetchall():
            cx.execute(f'DROP {kind} {name}')
        cx.commit()
        cx.execute('PRAGMA foreign_keys=OFF')
        for table in reversed(chatbots.TABLE_NAMES):
            cx.execute(f'DROP TABLE {table}')
        cx.execute('PRAGMA foreign_keys=ON')
        cx.executescript(old)
        assert 'kind' not in {r[1] for r in cx.execute('PRAGMA table_info(archive_chatbot_conditions)')}
        for table in ('archive_chatbot_runs', 'archive_chatbot_questions', 'archive_chatbot_conditions', 'archive_chatbot_answers',
                      'archive_human_collections', 'archive_human_answers'):
            columns = [r[1] for r in cx.execute(f'PRAGMA table_info({table})')]
            for row in saved[table]:
                cx.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES ({','.join('?' * len(columns))})", [row[c] for c in columns])
        before = {t: [tuple(r) for r in cx.execute(f'SELECT * FROM {t} ORDER BY rowid')] for t in tables}
        ids = {t: [r[0] for r in cx.execute(f'SELECT rowid FROM {t} ORDER BY rowid')] for t in chatbots.TABLE_NAMES}
    assert [len(before[t]) for t in chatbots.TABLE_NAMES] == [2, 6, 24, 4, 2, 8]
    core.init_db()
    core.init_db()                                                   # any number of times
    gained = {'archive_chatbot_conditions': ('app',) + (None,) * 8, 'archive_chatbot_answers': (None,)}
    with core.connect() as cx:
        for table, expected in before.items():
            now = [tuple(r) for r in cx.execute(f'SELECT * FROM {table} ORDER BY rowid')]
            # Every row is kept, value for value, under the id it had; an app row gains the kind 'app' and eight NULL model columns,
            # an answer a NULL reservation.
            assert now == [row + gained.get(table, ()) for row in expected], table
        for table in chatbots.TABLE_NAMES:
            assert [r[0] for r in cx.execute(f'SELECT rowid FROM {table} ORDER BY rowid')] == ids[table], table
        fresh = sqlite3.connect(':memory:')
        fresh.executescript('CREATE TABLE archive_runs(kind TEXT);' + chatbots.TABLES + chatbots.RULES)
        for table in chatbots.TABLE_NAMES:                           # a new database has exactly the upgraded one's columns
            assert [r[1:] for r in fresh.execute(f'PRAGMA table_info({table})')] == [r[1:] for r in cx.execute(f'PRAGMA table_info({table})')], table
        names = lambda db, kind: {r[0] for r in db.execute(  # noqa: E731
            "SELECT name FROM sqlite_master WHERE type=? AND (name LIKE 'archive_chatbot_%' OR name LIKE 'archive_human_%')", (kind,))}
        assert names(fresh, 'trigger') == names(cx, 'trigger') and names(fresh, 'index') == names(cx, 'index') and names(fresh, 'table') == names(cx, 'table')
        assert 'archive_chatbot_conditions_kind' in names(cx, 'trigger') and not [n for n in names(cx, 'table') if n.endswith('_rebuild')]
        assert chatbots._stale_tables(cx) == [] and cx.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert [tuple(r) for table in chatbots.TABLE_NAMES for r in cx.execute(f'PRAGMA foreign_key_check({table})')] == []
        with pytest.raises(sqlite3.IntegrityError, match='append-only'):
            cx.execute("UPDATE archive_chatbot_answers SET answer_text='x'")
        with pytest.raises(sqlite3.IntegrityError, match='append-only'):
            cx.execute('DELETE FROM archive_chatbot_runs')
    # The same payload, byte for byte, before and after the rebuild.
    assert json.dumps(archive.public_payload()) == json.dumps(payload_before)
    # The upgraded database takes an asked run and a later hand-entered one; `changed` still compares with the old rows.
    with_models(tmp_path, monkeypatch)
    adapter(monkeypatch)
    assert chatbots.ask(max_usd=1)['run'] == 3
    monkeypatch.setattr(chatbots, 'today', lambda: datetime.date(2026, 12, 14))
    done = chatbots.import_collection(filled(tmp_path, '2026-12-14', reply=lambda app, q: f'PLACEHOLDER first {app} {q}.'))
    assert done['run'] == 4 and done['first_import'] is False
    block = archive.public_payload()['chatbots']
    assert [a['changed'] for a in block['answers'] if (a['chatbot'], a['question']) == ('chatgpt', 'C001')] == [None, True, True]
    assert [app.get('kind') for app in block['apps']] == [None, None, None, 'model', 'model']


def test_a_failed_copy_changes_nothing(asking, tmp_path, monkeypatch):
    """The rebuild is one transaction: if a copy does not hold every row, every table is what it was."""
    with core.connect() as cx:
        for name, kind in cx.execute("SELECT name,type FROM sqlite_master WHERE (name LIKE 'archive_chatbot_%' OR name LIKE 'archive_human_%') "
                                     "AND type IN ('trigger','index') AND name NOT LIKE 'sqlite_%'").fetchall():
            cx.execute(f'DROP {kind} {name}')
        for table in reversed(chatbots.TABLE_NAMES):
            cx.execute(f'DROP TABLE {table}')
        cx.executescript(V7_SCHEMA.read_text(encoding='utf-8'))
        cx.execute("INSERT INTO archive_chatbot_runs(number,kind,collected_on,imported_at,question_ids,config_sha256) "
                   "VALUES (1,'manual','2026-10-10','x','[\"C001\"]',?)", ('a' * 64,))
        # A row the old table allowed and the new one's foreign keys cannot place: an answer whose conditions row is missing.
        cx.execute('PRAGMA foreign_keys=OFF')
    raw = sqlite3.connect(str(asking))
    raw.execute("INSERT INTO archive_chatbot_questions(id,position,kind,text,prompt,hook,added,sha256,first_run) VALUES ('C001',1,'manual','t','p','','2026-11-14','h',1)")
    raw.execute("INSERT INTO archive_chatbot_answers(run_id,question_set,question_id,chatbot,source,state,created_at) VALUES (1,'chat','C001','ghost','manual','no_answer','x')")
    raw.commit()
    schema = raw.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'archive_chatbot_%' ORDER BY name").fetchall()
    raw.close()
    with pytest.raises(RuntimeError, match='archive_chatbot_answers could not be upgraded: a row would refer to a missing one. Nothing was changed'):
        core.init_db()
    raw = sqlite3.connect(str(asking))
    assert raw.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'archive_chatbot_%' ORDER BY name").fetchall() == schema
    assert raw.execute('SELECT COUNT(*) FROM archive_chatbot_answers').fetchone()[0] == 1
    raw.close()


# ---- pages ---------------------------------------------------------------------------------------------------

def world(tmp_path, monkeypatch):
    """Hand-entered app replies and human answers on two dates, and asked model replies on the same two dates; on the
    second, local-one answers C001 differently and the service reports another model name for it."""
    two_dates(tmp_path)
    monkeypatch.setattr(chatbots, 'today', lambda: datetime.date(2026, 10, 10))
    adapter(monkeypatch, lambda model, prompt, n: f'PLACEHOLDER model reply {model} {(n - 1) % 4 + 1}.')
    assert chatbots.ask(max_usd=1)['run'] == 3
    monkeypatch.setattr(chatbots, 'today', lambda: TODAY)

    def second(model, prompt, n):
        text = 'PLACEHOLDER model reply, new.' if n == 1 else f'PLACEHOLDER model reply {model} {(n - 1) % 4 + 1}.'
        return engine.Completion(text, 40, 12, 'end', reported_model=f'{model}-r2' if model.startswith('sample') else f'{model}-r',
                                 sampling={'temperature': 0, 'seed': 1234} if model.startswith('sample') else {})
    adapter(monkeypatch, second)
    assert chatbots.ask(max_usd=1)['run'] == 4
    payload = archive.public_payload()
    assert archive.payload_problem(payload) is None
    return payload


FORBIDDEN = ('asked by software', 'by software', 'via API', 'through the API', 'asked-by', 'We asked it ourselves', 'kept the receipt',
             'archive-chatbot-ask', 'LIVE_ARCHIVE', 'reservation')


def test_pages_show_two_groups_a_switch_the_model_fact_and_no_word_about_how(asking, tmp_path, monkeypatch):
    payload = world(tmp_path, monkeypatch)
    block = payload['chatbots']
    assert payload['schema'] == 'live-genesis-archive/7' and block['groups'] == [chatbots.DEFAULT_GROUP, AI_MODELS]
    assert [(app['label'], app.get('kind'), app['group'], app['maker']) for app in block['apps']] == [
        ('chatgpt', None, 'ai-apps', 'OpenAI'), ('gemini', None, 'ai-apps', 'Google'), ('grok', None, 'ai-apps', 'xAI'),
        ('local-one', 'model', 'ai-models', 'Made-up maker L'), ('api-one', 'model', 'ai-models', '')]
    asked_runs = [run for run in block['runs'] if 'models' in run]
    assert [(run['number'], run['collected_on'], run['conditions'], run['human']) for run in asked_runs] == [
        (3, '2026-10-10', {}, None), (4, '2026-11-14', {}, None)]
    assert asked_runs[1]['models'] == {
        'local-one': {'model': 'sample-local:1b', 'endpoint': 'local', 'reported_model': 'sample-local:1b-r2', 'model_digest': None,
                      'pricing_basis': 'local', 'sampling_pinned': True, 'local': True},
        'api-one': {'model': 'gpt-test-mini', 'endpoint': 'openai', 'reported_model': 'gpt-test-mini-r', 'model_digest': None,
                    'pricing_basis': 'rates', 'sampling_pinned': False, 'local': False}}
    assert {a['source'] for a in block['answers']} == {'manual', 'asked'} and all('models' not in run for run in block['runs'][:2] if run['conditions'])
    site_data = chatbots.site_payload(payload)
    assert site_data['schema'] == 'live-genesis-chat-archive/3' and chatbots.site_problem(site_data) is None
    site = archive_pages.render_site(site_data, standalone=True)
    front, page, how = site['index.html'], site['c/C001.html'], site['chatbots.html']
    # A card of models alone says "AI models" in its corner, never "Chat apps" or "Chatbots"; an app's card keeps its word.
    corner = {name: re.findall(r'<span class="card-kicker">([^<]+)</span>', text) for name, text in site.items() if name.startswith('card/C001')}
    assert corner['card/C001-local-one.html'] == corner['card/C001-api-one.html'] == corner['card/C001-ai-models.html'] == ['AI models']
    assert corner['card/C001.html'] == corner['card/C001-chatgpt.html'] == corner['card/C001-human.html'] == ['Chatbots']
    assert '<a href="index.html" aria-current="page">Chat apps</a>' in front    # apps and models together: the bar keeps its word
    # The front page: a neutral count, a group switch with both groups, the models in their own group.
    assert 'Same question. Five AIs in two groups. One human.' in visible(front)
    assert front.count('class="g-radio"') == 2 and 'AI chat apps and AI models. We ask again every month and keep every reply.' in visible(front)
    assert re.findall(r'<span class="g-name">([^<]+)</span><span class="g-count">([^<]+)</span>', front) == [('AI chat apps', '3 asked'), ('AI models', '2 asked')]
    # The question page: one thread per group; a model answerer is a bubble with its name, like an app.
    assert re.findall(r'class="thread-name[^"]*"[^>]*>([^<]+)<', page)[:2] == ['AI chat apps', 'AI models']
    for shown in ('Sample model L', 'Sample model M', 'PLACEHOLDER model reply, new.', 'PLACEHOLDER model reply gpt-test-mini 1.'):
        assert shown in page, shown
    for gone in ('The exact words we used', 'The details behind each answer'):   # not on the public site (owner, 20 Nov 2026)
        assert gone not in page, gone
    # The local app keeps the block; the public site does not.
    sources = archive_pages.render_site(site_data)['c/C001.html'].split('<summary>The details behind each answer</summary>')[1]
    # In the place an app shows "the app showed: ...": the model the service reported, plainly. No method label.
    assert '<strong>Sample model L (Made-up maker L)</strong><ul><li><span class="mono">Nov 2026</span> model: sample-local:1b-r2 · run locally · ' \
           'a different model name than before</li><li><span class="mono">Oct 2026</span> model: sample-local:1b-r · run locally</li></ul>' in sources
    assert '<strong>Sample model M</strong><ul><li><span class="mono">Nov 2026</span> model: gpt-test-mini-r</li>' in sources
    assert 'the app showed: PLACEHOLDER model · free account' in sources                 # an app's line is what it was
    # "Changed" works as for an app: the same comparison, the same words, the earlier answer above.
    local = page.split('data-answerer="local-one"')[1].split('</article>')[0]
    assert 'data-state="changed"' in local and 'data-answerer-kind="model"' in local and 'data-provenance="ai-generated"' in local
    assert 'data-version="sample-local:1b-r2"' in local and 'data-maker="Made-up maker L"' in local and 'data-method' not in page
    assert 'PLACEHOLDER model reply sample-local:1b 1.' in local and 'Before' in local   # what it said before
    assert 'data-state="same"' in page.split('data-answerer="api-one"')[1].split('</article>')[0]
    # The labels and the notice: as for apps, and the notice names the makers of model answerers that have one.
    notice = 'not affiliated with, sponsored by or endorsed by OpenAI, Google, xAI or Made-up maker L; product names belong to their owners.'
    for text in (front, page, how):
        assert notice in text and 'These answers are AI-generated and shown exactly as they came. They can be wrong' in text
        assert 'Read-only. No payments. Just answers.' not in text                 # owner's decision, 20 Nov 2026
        assert '<span class="tag tag-dark mono">AI-generated · may be wrong</span>' in text
    # How it works: a model answerer's row says which model, when, how often; never an app's conditions.
    assert '<strong>Sample model L (Made-up maker L)</strong></div><div class="app-how"><span>model: sample-local:1b-r2</span>' \
           '<span class="muted small">asked 14 Nov 2026 · 2 times in all</span>' in how
    assert '<strong>Sample model M</strong></div><div class="app-how"><span>model: gpt-test-mini-r</span>' in how
    assert re.findall(r'<h3 class="apps-name">([^<]+)</h3>', how)[:2] == ['AI chat apps', 'AI models']
    # Cards: one per model answerer and one for the group, with the same foot as an app's card.
    for name in ('card/C001-local-one.html', 'card/C001-api-one.html', 'card/C001-ai-models.html'):
        assert '<p class="card-honest mono">AI-generated · not affiliated · may be wrong</p>' in site[name] and 'no payments' not in site[name], name
    assert 'PLACEHOLDER model reply, new.' in site['card/C001-ai-models.html'] and 'Sample model M' in site['card/C001-ai-models.html']
    assert 'PLACEHOLDER model reply' not in site['card/C001.html']                        # the first card is the apps' group
    # The data files: the record of such an answer carries its model, provenance and no method.
    data = json.loads(site['data/C001.json'])
    assert [(a['answerer'], a['kind'], a['maker']) for a in data['answerers']][3:5] == [('local-one', 'model', 'Made-up maker L'), ('api-one', 'model', '')]
    record = next(a for a in data['answers'] if a['id'] == 'C001/local-one/2026-11-14')
    assert (record['answerer_kind'], record['version'], record['provenance'], record['state'], record['changed'], record['attribution']) == \
        ('model', 'sample-local:1b-r2', 'ai-generated', 'changed', True, '')
    assert 'method' not in record and record['previous_text'] == 'PLACEHOLDER model reply sample-local:1b 1.'
    assert record['conditions'] == asked_runs[1]['models']['local-one'] and record['text'] == 'PLACEHOLDER model reply, new.'
    readme = site['data/README.txt']
    assert 'chat-app | model | human' in readme and "or the model's name (" in readme and "the model's name and where it ran for a model" in readme
    assert not re.search(r'(?m)^method\b', readme)
    # No page, card or data file says how any reply was collected, and none leaks a setting.
    for name, text in site.items():
        found = [phrase for phrase in FORBIDDEN + SECRETS if phrase.lower() in text.lower()]
        assert not found, (name, found)
    # The local web app serves the same pages from the whole payload.
    assert 'model: sample-local:1b-r2' in archive_pages.render_question(payload, 'C001')


def test_a_group_of_models_alone_and_a_model_without_a_maker(asking, tmp_path, monkeypatch):
    adapter(monkeypatch)
    chatbots.ask(max_usd=1, only='api-one')
    payload = archive.public_payload()
    assert archive.payload_problem(payload) is None and payload['chatbots']['groups'] == [AI_MODELS] and payload['chatbots']['human'] is None
    site = archive_pages.render_site(chatbots.site_payload(payload), standalone=True)
    front = site['index.html']
    assert 'Same question. One AI model.' in visible(front), visible(front)[:400]
    assert 'g-radio' not in front and 'One AI app' not in visible(front) and 'chatbot' not in visible(front)
    assert ('not affiliated with, sponsored by or endorsed by the makers of the products named; product names belong to their owners.'
            in site['c/C001.html'])
    assert 'PLACEHOLDER asked gpt-test-mini 1.' in site['c/C001.html'] and 'PLACEHOLDER asked gpt-test-mini 1.' in site['card/C001.html']


# ---- the reviewed export: export -> approve -> check -> from-data ---------------------------------------------

def test_asked_answers_go_through_the_reviewed_export_and_the_built_site_says_nothing_about_how(asking, tmp_path, monkeypatch):
    payload = world(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match='not scripted-demo-only'):
        snapshot.build(tmp_path / 'unreviewed', archive_only=True)                        # asked rows are recorded data: reviewed first
    out, site = tmp_path / 'published', tmp_path / 'site'
    manifest = snapshot.export_published(out, archive_only=True)
    assert manifest['archive']['chatbots'] == {'runs': 4, 'answers': 40, 'apps': APPS + ['local-one', 'api-one'], 'human_answers': 8,
                                               'questions': 4, 'models': ['local-one', 'api-one']}
    exported = json.loads((out / 'archive.json').read_text(encoding='utf-8'))
    assert exported == {'schema': 'live-genesis-chat-archive/3', 'chatbots': json.loads(json.dumps(payload['chatbots']))}
    asked = [a for a in exported['chatbots']['answers'] if a['source'] == 'asked']
    assert len(asked) == 16 and all(set(a) == set(chatbots.ANSWER_KEYS_PUBLIC) for a in asked)
    assert [set(app) - set(chatbots.APP_KEYS) for app in exported['chatbots']['apps']] == [set(), set(), set(), {'kind'}, {'kind'}]
    assert all(set(run) - set(chatbots.RUN_KEYS) == ({'models'} if run['number'] > 2 else set()) for run in exported['chatbots']['runs'])
    raw = (out / 'archive.json').read_text(encoding='utf-8')
    assert not [secret for secret in SECRETS if secret in raw] and 'reservation' not in raw and 'provider' not in raw and '"method"' not in raw
    told = snapshot._describe(manifest)
    assert 'local-one, api-one are MODELS this software asked' in told and 'ENTERED BY HAND' in told
    with pytest.raises(RuntimeError, match='not approved'):
        snapshot.check_published(out)
    snapshot.approve_published(out, confirmed=True)
    assert snapshot.check_published(out)['reviewed'] is True
    monkeypatch.setenv('LIVE_DB', str(tmp_path / 'no-such.sqlite'))
    snapshot.build_from_data(site, out, archive_only=True)
    texts = assert_site_does_not_say_how_replies_were_collected(site)
    assert {'archive/card/C001-local-one.html', 'archive/card/C001-ai-models.html'} <= set(texts)
    assert json.loads(texts['archive.json']) == exported
    for name, text in texts.items():
        found = [phrase for phrase in FORBIDDEN + SECRETS if phrase.lower() in text.lower()]
        assert not found, (name, found)
    assert 'model: sample-local:1b-r2' not in texts['archive/c/C001.html'] and 'sample-local:1b-r2' in texts['archive/data/C001.json'] and 'Five AIs in two groups.' in visible(texts['archive/index.html'])
    records = [json.loads(line) for line in texts['archive/data/answers.jsonl'].splitlines()]
    assert {r['provenance'] for r in records} == {'ai-generated', 'human-written'} and {r['answerer_kind'] for r in records} == {'chat-app', 'model', 'human'}
    assert all('method' not in r for r in records)


def test_check_refuses_a_tampered_model_answerer(asking, tmp_path, monkeypatch):
    world(tmp_path, monkeypatch)
    out = tmp_path / 'published'

    def tampered(edit, why):
        snapshot.export_published(out, archive_only=True, replace=True)
        snapshot.approve_published(out, confirmed=True)
        retarget(out, edit)
        with pytest.raises(RuntimeError, match=why):
            snapshot.check_published(out)
    block = lambda d: d['chatbots']                                   # noqa: E731
    facts = lambda d: block(d)['runs'][3]['models']['local-one']      # noqa: E731
    assert snapshot.export_published(out, archive_only=True, replace=True)['archive']['chatbots']['models'] == ['local-one', 'api-one']
    # The answerer: the configuration's model answerer, under its name, maker and group; never an app's name, never an app.
    tampered(lambda d: block(d)['apps'][3].update(name='Another model'), 'model answerer local-one does not carry the name, maker and group')
    tampered(lambda d: block(d)['apps'][3].update(maker='OpenAI'), 'model answerer local-one does not carry the name, maker and group')
    tampered(lambda d: block(d)['apps'][3].update(name='ChatGPT 5'), 'a model answerer carries the name of a chat app')
    tampered(lambda d: block(d)['apps'][3].update(attribution='Written with x'), 'a chat app lacks its label, name, maker, attribution or group')
    tampered(lambda d: block(d)['apps'][3].update(kind='app'), 'a chat app lacks its label, name, maker, attribution or group')
    tampered(lambda d: block(d)['apps'][3].pop('kind'), "a run's model answerers are malformed or unknown|both chat apps and model answerers")
    tampered(lambda d: block(d)['apps'][0].update(kind='model'), 'a run lacks its conditions or names an unknown chat app|both chat apps and model answerers')
    tampered(lambda d: block(d)['apps'][3].update(group='ai-apps'), 'holds both chat apps and model answerers')
    tampered(lambda d: block(d)['apps'][3].update(provider='openai'), 'a chat app: unexpected key provider')
    tampered(lambda d: (block(d)['apps'][3].update(label='other-one'), [a.update(chatbot='other-one') for a in block(d)['answers'] if a['chatbot'] == 'local-one'],
                        [run['models'].update({'other-one': run['models'].pop('local-one')}) for run in block(d)['runs'] if 'models' in run]),
             'model answerer other-one is not a model answerer of chatbots-config.json')
    # The per-run facts: complete, closed, and never a URL, a key or free text.
    tampered(lambda d: facts(d).update(endpoint='http://localhost:11434/v1'), "endpoint is not a plain name")
    tampered(lambda d: facts(d).update(endpoint=None), 'a value that is not allowed')
    tampered(lambda d: facts(d).update(model='https://api.example.org/v1/models/x'), 'a value that is not allowed')
    tampered(lambda d: facts(d).update(model='sample model with spaces'), 'a value that is not allowed')
    tampered(lambda d: facts(d).update(reported_model='error: invalid api key PLACEHOLDER'), 'reported model name is not well-formed')
    tampered(lambda d: facts(d).update(reported_model='x://y'), 'a value that is not allowed')
    tampered(lambda d: facts(d).update(model_digest='sha256:' + 'a' * 64), 'model digest is not 12 hex digits')
    tampered(lambda d: facts(d).update(pricing_basis='cheap'), 'pricing basis is unknown')
    tampered(lambda d: facts(d).update(pricing_basis=None), 'a value that is not allowed')
    tampered(lambda d: facts(d).update(local='yes'), 'a value that is not allowed')
    tampered(lambda d: facts(d).update(base_url='http://localhost:11434/v1'), "a model answerer's record is missing or incomplete")
    tampered(lambda d: facts(d).pop('reported_model'), "a model answerer's record is missing or incomplete")
    tampered(lambda d: facts(d).update(api_key='x'), "a model answerer's record is missing or incomplete")
    tampered(lambda d: block(d)['runs'][3].update(models={}), "a run's model answerers are malformed or unknown")
    tampered(lambda d: block(d)['runs'][3]['models'].pop('local-one'), 'whose conditions its run did not record')
    tampered(lambda d: block(d)['runs'][3].update(conditions={'chatgpt': block(d)['runs'][2]['conditions']['chatgpt']}), 'share a run with a chat app')
    tampered(lambda d: block(d)['runs'][3].update(human=block(d)['runs'][2]['human']), 'share a run with a chat app, a human')
    tampered(lambda d: block(d)['runs'][2].update(models=block(d)['runs'][3]['models']), 'share a run with a chat app')
    tampered(lambda d: block(d)['runs'][3].update(error='Connection refused'), 'chat-app run 4: unexpected key error')
    # The answers: the source fits the kind of answerer, both ways; an asked reply is never a marked refusal.
    asked = lambda d: next(a for a in block(d)['answers'] if a['source'] == 'asked' and a['state'] == 'answered')   # noqa: E731
    tampered(lambda d: asked(d).update(source='manual'), "an answer's source does not fit its run")
    tampered(lambda d: block(d)['answers'][0].update(source='asked'), "an answer's source does not fit its run")
    tampered(lambda d: asked(d).update(source='engine'), 'an answer has a missing or invalid field')
    tampered(lambda d: asked(d).update(refused=True, changed=None), 'an asked reply carries a refusal mark or a screenshot')
    tampered(lambda d: asked(d).update(screenshot_hash='a' * 64), 'an asked reply carries a refusal mark or a screenshot')
    tampered(lambda d: asked(d).update(text='x' * 2001), 'longer than the archive stores')
    tampered(lambda d: asked(d).update(text='Write to someone.real@example.org'), 'Content guards matched')
    tampered(lambda d: asked(d).update(reservation=5), 'unexpected key reservation')
    tampered(lambda d: asked(d).update(method='asked-by-server'), 'unexpected key method')
    tampered(lambda d: block(d)['human_answers'][0].update(source='asked'), 'a human answer has a missing or invalid field')
    # The schema marker: /3 exactly when the file holds a model answerer.
    tampered(lambda d: d.update(schema='live-genesis-chat-archive/2'), 'the schema marker does not fit the file')
    tampered(lambda d: d.update(schema='live-genesis-archive/7'), 'it holds the model track')
    # The manifest's own list of model answerers is checked with the counts.
    snapshot.export_published(out, archive_only=True, replace=True)
    snapshot.approve_published(out, confirmed=True)
    manifest = json.loads((out / 'MANIFEST.json').read_text(encoding='utf-8'))
    manifest['archive']['chatbots']['models'] = ['local-one']
    (out / 'MANIFEST.json').write_text(json.dumps(manifest, indent=1))
    with pytest.raises(RuntimeError, match='counts in the manifest do not match'):
        snapshot.check_published(out)
    # A model answerer the configuration no longer lists cannot be published.
    snapshot.export_published(out, archive_only=True, replace=True)
    snapshot.approve_published(out, confirmed=True)
    with_models(tmp_path, monkeypatch, [API])
    with pytest.raises(RuntimeError, match='model answerer local-one is not a model answerer of chatbots-config.json'):
        snapshot.check_published(out)
    # The whole payload (the local pages) has its own marker for it.
    whole = json.loads(json.dumps(archive.public_payload()))
    assert whole['schema'] == 'live-genesis-archive/7' and archive.payload_problem(whole) is None
    assert 'schema marker does not fit' in archive.payload_problem(dict(whole, schema='live-genesis-archive/6'))
    for app in whole['chatbots']['apps']:
        app.pop('kind', None)
    assert archive.payload_problem(dict(whole, schema='live-genesis-archive/6')) is not None      # a model answerer cannot pass as an app


# ---- nothing changes where no model answerer was asked -------------------------------------------------------

def test_with_no_model_answerer_asked_every_page_and_the_published_file_are_what_they_were(chat, tmp_path, monkeypatch):
    two_dates(tmp_path)
    payload = archive.public_payload()

    def everything(folder):
        out, site = tmp_path / f'{folder}-published', tmp_path / f'{folder}-site'
        snapshot.export_published(out, archive_only=True)
        snapshot.approve_published(out, confirmed=True)
        snapshot.build_from_data(site, out, archive_only=True)
        files = {p.relative_to(site).as_posix(): p.read_bytes() for p in site.rglob('*') if p.is_file()}
        manifest = json.loads((out / 'MANIFEST.json').read_text(encoding='utf-8'))
        return (out / 'archive.json').read_bytes(), files, manifest['archive']
    before = everything('before')
    # The markers and key sets of the version before model answerers: nothing new appears anywhere.
    exported = json.loads(before[0])
    assert exported['schema'] == 'live-genesis-chat-archive/2' and payload['schema'] == 'live-genesis-archive/6'
    assert all(set(app) == set(chatbots.APP_KEYS) for app in exported['chatbots']['apps'])
    assert all(set(run) == set(chatbots.RUN_KEYS) for run in exported['chatbots']['runs'])
    assert {a['source'] for a in exported['chatbots']['answers']} == {'manual'} and set(before[2]['chatbots']) == {
        'runs', 'answers', 'apps', 'human_answers', 'questions'}
    assert before[1]['archive/data/README.txt'].decode() == archive_pages.README_CHAT and 'model' not in archive_pages.README_CHAT.split('Fields')[1].split('maker')[0]
    # Configuring model answerers (and never asking them) changes no byte of the file, the manifest's counts or any page.
    with_models(tmp_path, monkeypatch)
    assert json.dumps(archive.public_payload()) == json.dumps(payload)
    after = everything('after')
    assert after[0] == before[0] and after[2] == before[2] and after[1] == before[1] and len(after[1]) > 20


# ---- the command line and the docs ----------------------------------------------------------------------------

def test_cli_ask_end_to_end_and_status(asking, tmp_path, monkeypatch, capsys):
    calls = adapter(monkeypatch)
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', '--max-usd', '0.01', '--models', 'api-one')
    assert code == 0 and len(calls) == 4
    for line in ('api-one = openai:gpt-test-mini, shown as "Sample model M"', 'PAID RUN: provider requests follow.', '"status": "complete"',
                 'Stored as run 1, collected 2026-11-14. Recorded in the operator audit log.',
                 'This was the first recorded run: from now on the questions in chatbots-config.json may only be added to',
                 'These are replies of MODELS this program asked, not of any chat app.', 'python -m live_genesis.snapshot --export --archive-only'):
        assert line in out, line
    assert 'PLACEHOLDER' not in out and not [secret for secret in SECRETS if secret in out]
    code, out = run_cli(monkeypatch, capsys, 'archive-status')
    status = json.loads(out)['chatbots']
    assert code == 0 and status['config_file']['models'] == [{'label': 'local-one', 'answerer': 'loc'}, {'label': 'api-one', 'answerer': 'mini'}]
    assert [(run['number'], run['kind'], run['answers']) for run in status['runs']] == [(1, 'asked', 4)]
    code, out = run_cli(monkeypatch, capsys, '--help')
    assert code == 0 and 'PAID: Answers Over Time, chat-app questions: ask every MODEL answerer' in re.sub(r'\s+', ' ', out)


def test_docs_name_the_command_as_paid_and_describe_the_model_group():
    rules = (REPO / 'CLAUDE.md').read_text(encoding='utf-8')
    one, two = (re.search(rf'(?m)^{n}\. .*$', rules).group(0) for n in (1, 2))
    assert '`archive-run`' in one and '`archive-chatbot-ask`' in one and '--max-usd' in one and 'explicitly' in one
    assert 'LIVE_ALLOW_PAID_CALLS=YES' in two and 'Never set it yourself' in two and '`archive-chatbot-ask`' in two
    commands = rules.split('## Commands')[1].split('## Rules')[0]
    paid = commands.split('# PAID.')[1]
    assert 'archive-chatbot-ask --max-usd' in paid and 'archive-chatbot-ask --dry-run' in commands.split('# PAID.')[0]
    assert 'archive-chatbot-ask' in rules.split('## Architecture map')[1].split('## Commands')[0]
    doc = (REPO / 'docs' / 'ANSWERS-OVER-TIME.md').read_text(encoding='utf-8')
    section = doc.split('## The chat-app track', 1)[1].split('\n## ', 1)[0]
    for told in ('archive-chatbot-ask', '"kind": "model"', 'ai-models', '`asked`', 'LIVE_ARCHIVE_MODELS', '--max-usd', 'LIVE_ALLOW_PAID_CALLS=YES',
                 'archive_schema_v7.sql', 'live-genesis-chat-archive/3'):
        assert told in section, told
    plan = (REPO / 'docs' / 'PLAN-CHATBOT-COMPARISON.md').read_text(encoding='utf-8')
    assert ('14 November 2026: the owner asked for the chat-app questions to be put to models by software as a second group; app columns '
            'stay reserved for replies from the apps themselves.') in plan
    for name in ('SECURITY.md', 'SESSION-STATE.md', 'ROADMAP.md', 'FREE-MODELS.md'):
        assert 'archive-chatbot-ask' in (REPO / 'docs' / name).read_text(encoding='utf-8'), name
    assert 'archive-chatbot-ask' in (REPO / '.claude' / 'skills' / 'live-genesis-dev' / 'SKILL.md').read_text(encoding='utf-8')
