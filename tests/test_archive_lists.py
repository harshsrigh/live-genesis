"""Answers Over Time, the chat-app track's QUESTION LISTS (owner's decision of 23 November 2026): a question may be marked
`"list": "trending"`, `cli archive-chatbot-ask --list trending` (or `--questions`) asks only some questions, and the site
gets a "Trending" tab (`archive/trending-now.html`) that exists only when a trending question has an answer to show.

Offline: `engine.infer` AND `engine.REAL_INFER` are replaced by a stand-in in every test that asks (tests/test_archive_ask.py
`adapter`), so nothing here contacts a provider, and every "reply" is an obvious placeholder."""
import datetime
import hashlib
import json
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from live_genesis import archive, archive_chatbots as chatbots, archive_pages, config, core, engine, profiles, snapshot
from live_genesis.app import app
from test_archive import REPO, assert_page_is_safe, ready, rows, run_cli  # noqa: F401
from test_archive_ask import LOCAL, LOCAL_URL, MODELS, SECRETS, adapter, nothing_stored
from test_archive_chatbots import (CONFIG, IDS, PREFIX, chat, config_file, filled, question, visible)  # noqa: F401

V8_SCHEMA = REPO / 'tests' / 'fixtures' / 'archive_schema_v8.sql'
API = {'label': 'api-one', 'kind': 'model', 'name': 'Sample model M', 'maker': 'Made-up maker M', 'answerer': 'mini', 'group': 'ai-models'}
TREND = [dict(question(5, 'Is a made-up trend a real trend?', 'Made-up trend?'), added='2026-11-20', list='trending'),
         dict(question(6, 'Would a placeholder say yes to this?', 'Placeholder says?'), added='2026-11-20', list='trending')]
TREND_IDS = ['C005', 'C006']
NOV_19, NOV_24, DEC_1 = datetime.date(2026, 11, 19), datetime.date(2026, 11, 24), datetime.date(2026, 12, 1)
# What the code before question lists recorded for the ten questions of 14 November 2026 (the owner's real database
# holds exactly these): pinned here, so no change to the hash can go unnoticed.
RECORDED = {
    'C001': '3cba88e83617062619222b6790e774fb7ac9e4325b39204aca26343536bd7f51',
    'C002': '9f5ce6da927e340f20d781f1f78ec1bb3e615ca216defa5d747ffbf9a51d559a',
    'C003': 'b87383f165c2d687e055df879a2b3a72fdb174d98af0010fee052d12c9faf2a7',
    'C004': '2bde447f98575ae5ade299a5549b0ba28fafe88f7e923ced9ed3c0a4ae46777a',
    'C005': '20324cc97d34f974118798c66a671c531eff81de4d9f2936c602336f4d085077',
    'C006': 'adf8f46f4c95ff24b1cd0aba106478753ae6ef13199fb67b5e703aba816ad350',
    'C007': '1e3a6ef5d2b27db75388944c83c13aaa249c86c4ad676ba32abaf6d0925dff8e',
    'C008': '77312d93d836c6f78cb8721e588e7b5a5de01a1efe12516702b0114e503bde1e',
    'C009': 'b2d2a5776465ae543b576ed55828090575085e7a6a7900b388e523b48486c9dc',
    'C010': '302a0e42fdbac0fb615b9f16de139ce41295d7975497dae65154c62f5529cd6f',
}


def models_only(tmp_path, monkeypatch, trending=(), models=(LOCAL, API), count=4):
    """The checked-in configuration reduced to its first `count` questions (all on the main list), with the model
    answerers `models` in place of the checked-in ones and the questions `trending` appended."""
    def change(data):
        data['chatbots'] = data['chatbots'] + [dict(model) for model in models]
        data['questions'] = data['questions'] + [dict(q) for q in trending]
    return config_file(tmp_path, monkeypatch, count=count, edit=change)


@pytest.fixture
def asking(chat, tmp_path, monkeypatch):
    """Two made-up model answerers (one local and zero-priced, one priced), four main questions, no trending question
    yet; today is 19 November 2026. The adapter fails the test if it is called before a test replaces it."""
    models_only(tmp_path, monkeypatch)
    monkeypatch.setenv('LIVE_ARCHIVE_ENDPOINTS', 'local')
    monkeypatch.setenv(config.endpoint_variable('local', 'BASE_URL'), LOCAL_URL)
    monkeypatch.setenv('LIVE_ARCHIVE_MODELS', MODELS)
    monkeypatch.setattr(chatbots, 'today', lambda: NOV_19)
    return chat


def reply(tag):
    return lambda model, prompt, n: f"PLACEHOLDER {tag} {model} to: {prompt.split(chr(10))[-1]}"


def main_then_trending(tmp_path, monkeypatch):
    """19 November: every question there is (the four main ones) is asked. Then two trending questions are added to the
    configuration, and on 24 November only those are asked."""
    adapter(monkeypatch, reply('first'))
    first = chatbots.ask(max_usd=1)
    models_only(tmp_path, monkeypatch, trending=TREND)
    monkeypatch.setattr(chatbots, 'today', lambda: NOV_24)
    calls = adapter(monkeypatch, reply('first'))
    second = chatbots.ask(max_usd=1, question_list='trending')
    return first, second, calls


def standalone(payload):
    return archive_pages.render_site(chatbots.site_payload(payload), standalone=True)


def nav(page):
    return re.findall(r'>([^<>]+)</a>', page.split('<nav', 1)[1].split('</nav>', 1)[0])


def old_hash(item):
    """The hash as the code before question lists worked it out: the five keys, nothing else."""
    body = {key: item[key] for key in ('id', 'text', 'prompt', 'hook', 'added')}
    return hashlib.sha256(json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')).hexdigest()


# ---- the configuration field and the recorded identity ---------------------------------------------------------

def test_the_three_new_questions_are_marked_trending_and_the_ten_recorded_ones_hash_as_before():
    raw = json.loads(CONFIG.read_text(encoding='utf-8'))
    loaded = chatbots.load_config()
    assert raw['format'] == 'live-genesis-chatbots/3'                                   # an optional field: the file format is the same
    assert [q['id'] for q in raw['questions'] if q.get('list') == 'trending'] == ['C011', 'C012', 'C013']
    assert all('list' not in q for q in raw['questions'][:10]) and all('list' not in q for q in loaded['items'][:10])
    assert [chatbots.list_of(q) for q in loaded['items']] == ['main'] * 10 + ['trending'] * 3
    assert chatbots.LISTS == ('trending',) and 'main' not in json.dumps(raw['questions'])
    # A main question's recorded hash is what the version before worked out, to the byte; two are pinned as literals.
    for item in loaded['items'][:10]:
        assert set(item) == {'id', 'text', 'prompt', 'hook', 'added'} and chatbots.question_hash(item) == old_hash(item), item['id']
    assert {i: chatbots.question_hash(loaded['prompts'][i]) for i in RECORDED} == RECORDED
    # The list is part of a trending question's identity: it cannot be dropped or added later without the hash changing.
    for item in loaded['items'][10:]:
        assert item['list'] == 'trending' and item['added'] == '2026-11-20'
        assert chatbots.question_hash(item) != old_hash(item) == chatbots.question_hash({k: v for k, v in item.items() if k != 'list'})
    # The politics word check and the content guards apply to a trending question as to any other.
    for item in loaded['items'][10:]:
        for text in (item['text'], item['hook']):
            assert chatbots.political_term(text) is None and chatbots.guards.content_reason(text) is None


def test_a_list_must_be_trending_and_a_trending_question_passes_the_same_checks(chat, tmp_path, monkeypatch):
    plain = chatbots.load_config()['sha256']
    models_only(tmp_path, monkeypatch, models=(), trending=TREND)
    loaded = chatbots.load_config()
    assert loaded['questions'] == IDS + TREND_IDS and [q.get('list') for q in loaded['items']] == [None] * 4 + ['trending'] * 2
    assert loaded['sha256'] != plain
    for bad, why in [(dict(list='main'), 'C005: list must be "trending", or left out for the main list'),
                     (dict(list='hot'), 'C005: list must be "trending"'), (dict(list=None), 'C005: list must be "trending"'),
                     (dict(list=['trending']), 'C005: list must be "trending"'),
                     (dict(topic='x'), 'question 5 must hold id, text, prompt and added, and may have hook and list'),
                     (dict(text='Who wins the election?', prompt=f'{PREFIX}\n\nWho wins the election?'), 'C005: holds the word "election"'),
                     (dict(text='Mail me at someone@example.com', prompt=f'{PREFIX}\n\nMail me at someone@example.com'), 'C005: text must be')]:
        models_only(tmp_path, monkeypatch, models=(), trending=[{**TREND[0], **bad}])
        with pytest.raises(archive.ArchiveRefused, match=re.escape(why)):
            chatbots.load_config()
    # Without a trending question the configuration, its hash and its items are what they were before lists.
    models_only(tmp_path, monkeypatch, models=())
    assert chatbots.load_config()['sha256'] == plain


def test_a_recorded_question_never_moves_to_another_list(asking, tmp_path, monkeypatch):
    main_then_trending(tmp_path, monkeypatch)
    recorded = rows('SELECT id,list,sha256,first_run FROM archive_chatbot_questions ORDER BY position')
    assert [(r['id'], r['list'], r['first_run']) for r in recorded] == [(i, '', 1) for i in IDS] + [(i, 'trending', 2) for i in TREND_IDS]
    assert [r['sha256'] for r in recorded] == [chatbots.question_hash(q) for q in chatbots.load_config()['items']]
    assert [r['sha256'] for r in recorded[:4]] == [old_hash(q) for q in chatbots.load_config()['items'][:4]]
    with core.connect() as cx:
        assert chatbots.questions_problem(cx, chatbots.load_config()) is None
        with pytest.raises(sqlite3.IntegrityError):
            cx.execute("INSERT INTO archive_chatbot_questions(id,position,kind,text,prompt,added,sha256,first_run,list) "
                       "VALUES ('C009',9,'asked','t','p','2026-11-20','h',1,'hot')")
        cx.rollback()
    monkeypatch.setattr(chatbots, 'today', lambda: DEC_1)
    calls = adapter(monkeypatch)
    for moved, ids in (([dict(TREND[0], list=None), TREND[1]], 'C005'),                 # a trending question made a main one
                       (None, 'C001')):                                                   # a main question made a trending one
        def change(data, moved=moved):
            data['chatbots'] = data['chatbots'] + [dict(LOCAL), dict(API)]
            if moved is None:
                data['questions'][0]['list'] = 'trending'
                data['questions'] += [dict(q) for q in TREND]
            else:
                data['questions'] += [{k: v for k, v in q.items() if v is not None} for q in moved]
        config_file(tmp_path, monkeypatch, edit=change)
        with pytest.raises(archive.ArchiveRefused, match=f'{ids} was changed since it was first imported .text, prompt, hook, date or list.'):
            chatbots.ask(max_usd=1)
        with pytest.raises(archive.ArchiveRefused, match=f'{ids} was changed since it was first imported'):
            chatbots.import_collection(filled(tmp_path, '2026-12-01', name=f'moved-{ids}.json'))
    assert calls == [] and len(rows('SELECT * FROM archive_chatbot_runs')) == 2


# ---- a database made before lists ------------------------------------------------------------------------------

def test_a_database_made_before_lists_gains_the_column_keeps_every_row_and_still_asks_and_exports(asking, tmp_path, monkeypatch):
    old = V8_SCHEMA.read_text(encoding='utf-8')
    assert "'asked'" in old and old.count('CREATE TABLE IF NOT EXISTS') == 6 and 'list TEXT' not in old
    adapter(monkeypatch, reply('first'))
    assert chatbots.ask(max_usd=1)['run'] == 1                                            # four main questions, two models
    payload_before = json.dumps(archive.public_payload())
    with core.connect() as cx:
        saved = {t: [dict(r) for r in cx.execute(f'SELECT * FROM {t} ORDER BY rowid')] for t in chatbots.TABLE_NAMES}
        # The same rows in a database as the version before made it: its tables, index and triggers (the fixture).
        for name, kind in cx.execute("SELECT name,type FROM sqlite_master WHERE (name LIKE 'archive_chatbot_%' OR name LIKE 'archive_human_%') "
                                     "AND type IN ('trigger','index') AND name NOT LIKE 'sqlite_%'").fetchall():
            cx.execute(f'DROP {kind} {name}')
        cx.commit()
        cx.execute('PRAGMA foreign_keys=OFF')
        for table in reversed(chatbots.TABLE_NAMES):
            cx.execute(f'DROP TABLE {table}')
        cx.execute('PRAGMA foreign_keys=ON')
        cx.executescript(old)
        assert 'list' not in {r[1] for r in cx.execute('PRAGMA table_info(archive_chatbot_questions)')}
        for table in ('archive_chatbot_runs', 'archive_chatbot_questions', 'archive_chatbot_conditions', 'archive_chatbot_answers'):
            columns = [r[1] for r in cx.execute(f'PRAGMA table_info({table})')]
            for row in saved[table]:
                cx.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES ({','.join('?' * len(columns))})", [row[c] for c in columns])
        tables = chatbots.TABLE_NAMES + ('operator_audit', 'budget_reservations', 'api_usage')
        before = {t: [tuple(r) for r in cx.execute(f'SELECT * FROM {t} ORDER BY rowid')] for t in tables}
        hashes = [r[0] for r in cx.execute('SELECT sha256 FROM archive_chatbot_questions ORDER BY position')]
    assert [len(before[t]) for t in chatbots.TABLE_NAMES] == [1, 2, 8, 4, 0, 0]
    # Before any migration a read-only reader (the local web app) still gets the payload it always got.
    assert json.dumps(archive.public_payload(readonly=True)) == payload_before
    core.init_db()
    core.init_db()                                                                        # any number of times
    with core.connect() as cx:
        for table, expected in before.items():
            now = [tuple(r) for r in cx.execute(f'SELECT * FROM {table} ORDER BY rowid')]
            # Every row is kept, value for value; a recorded question gains '' (the main list) as its last column.
            assert now == [row + (('',) if table == 'archive_chatbot_questions' else ()) for row in expected], table
        assert [r[0] for r in cx.execute('SELECT sha256 FROM archive_chatbot_questions ORDER BY position')] == hashes
        fresh = sqlite3.connect(':memory:')
        fresh.executescript('CREATE TABLE archive_runs(kind TEXT);' + chatbots.TABLES + chatbots.RULES)
        for table in chatbots.TABLE_NAMES:                                                # a new database has exactly the upgraded one's columns
            assert [r[1:] for r in fresh.execute(f'PRAGMA table_info({table})')] == [r[1:] for r in cx.execute(f'PRAGMA table_info({table})')], table
        assert chatbots.questions_problem(cx, chatbots.load_config()) is None            # the recorded hashes still fit the file
        with pytest.raises(sqlite3.IntegrityError, match='append-only'):
            cx.execute("UPDATE archive_chatbot_questions SET list='trending'")
    assert json.dumps(archive.public_payload()) == payload_before                         # the same payload, byte for byte
    assert archive.public_payload()['schema'] == 'live-genesis-archive/7'
    # The upgraded database takes the trending questions and an ask of only those, then everything together.
    models_only(tmp_path, monkeypatch, trending=TREND)
    monkeypatch.setattr(chatbots, 'today', lambda: NOV_24)
    adapter(monkeypatch, reply('first'))
    done = chatbots.ask(max_usd=1, question_list='trending')
    assert done['run'] == 2 and done['new_questions'] == TREND_IDS and done['questions'] == TREND_IDS and done['first_run'] is False
    monkeypatch.setattr(chatbots, 'today', lambda: DEC_1)
    assert chatbots.ask(max_usd=1)['questions'] == IDS + TREND_IDS
    out = tmp_path / 'published'
    snapshot.export_published(out, archive_only=True)
    snapshot.approve_published(out, confirmed=True)
    assert snapshot.check_published(out)['reviewed'] is True
    assert json.loads((out / 'archive.json').read_text(encoding='utf-8'))['schema'] == 'live-genesis-chat-archive/4'


# ---- asking only some questions -----------------------------------------------------------------------------------

def test_dry_run_for_a_list_shows_what_is_asked_and_what_is_left_out_and_calls_nothing(asking, tmp_path, monkeypatch, capsys):
    models_only(tmp_path, monkeypatch, trending=TREND)
    monkeypatch.delenv('LIVE_ALLOW_PAID_CALLS')
    calls = adapter(monkeypatch, AssertionError('a dry run calls no model'))
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', '--list', 'trending', '--dry-run')
    assert code == 0 and 'DRY RUN: nothing was called and nothing was stored.' in out
    for line in ('Run dated 2026-11-19.', 'Questions (2 of 6), each asked once of each model with its exact prompt:',
                 'C005  Is a made-up trend a real trend?', 'C006  Would a placeholder say yes to this?',
                 'Left out this time (4): C001, C002, C003, C004. They are not asked, and this run records nothing about them',
                 'Calls: 4 (2 questions x 2 models).', 'Worst-case estimate: $0.00',
                 'python -m live_genesis.cli archive-chatbot-ask --list trending --max-usd 0.01'):
        assert line in out, line
    assert 'C001  ' not in out and not [secret for secret in SECRETS if secret in out] and 'PAID RUN' not in out
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', '--questions', 'C006, C002', '--models', 'api-one', '--dry-run')
    assert code == 0 and 'Questions (2 of 6)' in out and 'Left out this time (4): C001, C003, C004, C005.' in out
    assert 'Calls: 2 (2 questions x 1 models).' in out and 'archive-chatbot-ask --questions C002,C006 --max-usd' in out
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', '--list', 'main', '--dry-run')
    assert code == 0 and 'Questions (4 of 6)' in out and 'Left out this time (2): C005, C006.' in out
    # Without the option the plan is what it always was: every question, and no line about leaving any out.
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', '--dry-run')
    assert code == 0 and 'Questions (6), each asked once' in out and 'Calls: 12 (6 questions x 2 models).' in out
    assert 'Left out' not in out and 'python -m live_genesis.cli archive-chatbot-ask --max-usd 0.01' in out
    nothing_stored(calls)
    planned = chatbots.ask_plan(question_list='trending')
    assert [q['id'] for q in planned['questions']] == TREND_IDS and planned['left_out'] == IDS and planned['calls'] == 4
    assert {label: sorted(holds) for label, holds in planned['holds'].items()} == {'local-one': TREND_IDS, 'api-one': TREND_IDS}
    assert planned['worst_case_usd'] < chatbots.ask_plan()['worst_case_usd']


def test_an_unknown_list_or_id_is_refused_before_anything_else(asking, tmp_path, monkeypatch, capsys):
    models_only(tmp_path, monkeypatch, trending=TREND)
    calls = adapter(monkeypatch)
    for options, why in [(dict(question_list='hot'), '--list must be one of: main, trending. Nothing was asked.'),
                         (dict(question_list=''), '--list must be one of: main, trending'),
                         (dict(questions='C099'), '--questions names an id that is not a question of chatbots-config.json'),
                         (dict(questions='C005,Q01'), '--questions names an id that is not a question'),
                         (dict(questions=' , '), '--questions names an id that is not a question'),
                         (dict(questions='C005', question_list='trending'), 'Give --list or --questions, not both')]:
        with pytest.raises(archive.ArchiveRefused, match=re.escape(why)):
            chatbots.ask_plan(**options)
        # The refusal comes before every other gate: no cap, no opt-in, an unknown model label.
        monkeypatch.delenv('LIVE_ALLOW_PAID_CALLS', raising=False)
        with pytest.raises(archive.ArchiveRefused, match=re.escape(why)):
            chatbots.ask(max_usd=float('nan'), only='nobody', **options)
        monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS', 'YES')
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', '--list', 'hot', '--max-usd', '1')
    assert code != 0 and '--list must be one of: main, trending' in out and 'PAID RUN' not in out and 'Model answerers' not in out
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', '--questions', 'C099', '--dry-run')
    assert code != 0 and '--questions names an id' in out and 'DRY RUN' not in out
    nothing_stored(calls)
    # A list nobody is on: refused too (the four-question configuration has no trending question).
    models_only(tmp_path, monkeypatch)
    with pytest.raises(archive.ArchiveRefused, match='No question of chatbots-config.json is on the list "trending"'):
        chatbots.ask(max_usd=1, question_list='trending')
    nothing_stored(calls)
    # Every safety gate of a full ask holds for a partial one.
    models_only(tmp_path, monkeypatch, trending=TREND)
    monkeypatch.delenv('LIVE_ALLOW_PAID_CALLS')
    with pytest.raises(archive.ArchiveRefused, match='Explicit opt-in required for paid calls'):
        chatbots.ask(max_usd=1, question_list='trending')
    monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS', 'YES')
    for cap in (None, 0, -1, float('inf')):
        with pytest.raises(archive.ArchiveRefused, match='--max-usd is required'):
            chatbots.ask(max_usd=cap, question_list='trending')
    with pytest.raises(archive.ArchiveRefused, match=r'Worst-case estimate \$0\.000\d+ for 4 calls exceeds --max-usd'):
        chatbots.ask(max_usd=0.0000001, question_list='trending')
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', '--list', 'trending')
    assert code != 0 and '--max-usd is required' in out
    nothing_stored(calls)


def test_an_ask_of_the_trending_list_records_exactly_what_it_asked(asking, tmp_path, monkeypatch, capsys):
    first, second, calls = main_then_trending(tmp_path, monkeypatch)
    assert (first['run'], first['questions'], first['left_out'], first['new_questions']) == (1, IDS, [], IDS)
    assert (second['run'], second['status'], second['collected_on']) == (2, 'complete', '2026-11-24')
    assert (second['questions'], second['left_out'], second['new_questions'], second['first_run']) == (TREND_IDS, IDS, TREND_IDS, False)
    assert second['answers'] == {'answered': 4, 'no_answer': 0, 'withheld': 0} and 'PLACEHOLDER' not in json.dumps(second)
    # Only the two trending prompts went out, once per model, through the paid path: one settled reservation per call.
    prompts = [q['prompt'] for q in TREND]
    assert [c['prompt'] for c in calls] == prompts * 2 and {c['system'] for c in calls} == {archive.SYSTEM_LINE}
    runs = rows('SELECT number,kind,collected_on,question_ids FROM archive_chatbot_runs ORDER BY number')
    assert [(r['number'], r['kind'], r['collected_on'], json.loads(r['question_ids'])) for r in runs] == [
        (1, 'asked', '2026-11-19', IDS), (2, 'asked', '2026-11-24', TREND_IDS)]
    answers = rows('SELECT a.question_id,a.chatbot,a.changed,a.reservation_id,r.number FROM archive_chatbot_answers a '
                   'JOIN archive_chatbot_runs r ON r.id=a.run_id ORDER BY a.id')
    assert [(a['number'], a['question_id']) for a in answers] == [(1, q) for q in IDS] * 2 + [(2, q) for q in TREND_IDS] * 2
    assert {a['changed'] for a in answers} == {None}                                      # every one a first answer; nothing "changed"
    assert len({a['reservation_id'] for a in answers}) == 12 == len(rows("SELECT * FROM budget_reservations WHERE status='settled'"))
    assert not rows("SELECT * FROM budget_reservations WHERE status!='settled'")
    audit = [r['detail'] if 'detail' in r else json.dumps(r) for r in rows('SELECT * FROM operator_audit')]
    assert any('archive-chatbot-ask run 2' in line and 'questions C005,C006' in line for line in audit)
    assert not any('archive-chatbot-ask run 1' in line and 'questions C0' in line for line in audit)
    # The same day cannot hold a second run of an answerer, partial or not; --questions works like --list.
    with pytest.raises(archive.ArchiveRefused, match='local-one already has a run dated 2026-11-24'):
        chatbots.ask(max_usd=1, questions='C001')
    monkeypatch.setattr(chatbots, 'today', lambda: datetime.date(2026, 11, 25))
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', '--questions', 'C006', '--models', 'api-one', '--max-usd', '0.01')
    assert code == 0 and '"questions": [\n  "C006"\n ]' in out and 'Stored as run 3, collected 2026-11-25' in out and 'PLACEHOLDER' not in out
    assert json.loads(rows('SELECT question_ids FROM archive_chatbot_runs WHERE number=3')[0]['question_ids']) == ['C006']
    assert rows('SELECT a.changed FROM archive_chatbot_answers a JOIN archive_chatbot_runs r ON r.id=a.run_id WHERE r.number=3') == [{'changed': 0}]


# ---- the pages when one date holds only some questions ----------------------------------------------------------------

def test_two_dates_all_main_then_trending_only_no_false_change_no_gap_and_a_trending_tab(asking, tmp_path, monkeypatch):
    main_then_trending(tmp_path, monkeypatch)
    payload = archive.public_payload()
    block = payload['chatbots']
    assert archive.payload_problem(payload) is None and payload['schema'] == 'live-genesis-archive/8'
    assert [q.get('list') for q in block['questions']] == [None] * 4 + ['trending'] * 2
    assert all(set(q) == set(chatbots.QUESTION_KEYS) for q in block['questions'][:4])   # a main question is what it always was
    assert [(run['collected_on'], run['questions']) for run in block['runs']] == [('2026-11-19', IDS), ('2026-11-24', TREND_IDS)]
    site_data = chatbots.site_payload(payload)
    assert site_data['schema'] == 'live-genesis-chat-archive/4' and chatbots.site_problem(site_data) is None
    assert chatbots.export_problem(site_data['chatbots']) is None
    site = standalone(payload)
    pages = {name: page for name, page in site.items() if name.endswith('.html')}
    for name, page in pages.items():
        assert_page_is_safe(page)
        assert not re.search(r'(?i)\bround\b', visible(page)), name
        assert name.startswith('card/') or nav(page) == ['AI models', 'Trending', 'How it works'], name
        for false in ('tag-changed', 'is-changed', 'From <time', 'not asked', 'left out', 'No answer yet', 'Nothing here yet', 'same again'):
            assert false not in page, (name, false)
    assert {f'c/{q}.html' for q in IDS + TREND_IDS} | {'index.html', 'chatbots.html', 'trending-now.html'} <= set(pages)
    assert 'trending.html' not in site and not [name for name in site if name.startswith(('t/', 'q/'))]
    # The front page: the main questions only, and its count is theirs.
    front, trending = pages['index.html'], pages['trending-now.html']
    assert re.findall(r'class="row-open" href="c/(C\d{3})\.html"', front) == IDS
    assert '<ol class="steps"><li>4 questions</li>' in front and 'C005' not in front and TREND[0]['text'] not in front
    assert '<a href="index.html" aria-current="page">AI models</a>' in front and 'Two AI models.' in visible(front)
    assert 'Newer questions are under <a href="trending-now.html">Trending</a>.' in front
    # The Trending page: the trending questions with the same question cards, one stretched link each, and a friendly lede.
    assert re.findall(r'class="row-open" href="c/(C\d{3})\.html"', trending) == TREND_IDS
    assert '<a href="trending-now.html" aria-current="page">Trending</a>' in trending and '<title>Trending · Answers Over Time</title>' in trending
    assert trending.count('<article class="row">') == 2 and trending.count('class="row-open"') == 2 and trending.count('<li class="mini ') == 4
    assert '<ol class="steps"><li>2 questions</li>' in trending and trending.count('class="row-share small"') == 2
    seen = visible(trending)
    assert ('Newer questions, picked from what people are searching for and added along the way. Each one is asked again every month '
            'from when it was added.') in seen
    assert 'AI-generated · may be wrong' in seen and 'not affiliated' in seen and seen.count('When an app refuses or dodges, we leave it out.') == 1
    assert not re.search(r'(?i)by software|by hand|asked it ourselves|typed|collected', seen)
    assert 'PLACEHOLDER first sample-local:1b to: Is a made-up trend a real trend?' in trending
    assert 'href="card/C005.html"' in trending and '<a href="index.html">the first page</a>' in trending
    # A main question's page: one month, the one from 19 November. The later date is no gap and no row of its table.
    main = pages['c/C001.html']
    assert main.count('<section class="panel"') == 1 and 'm-2026-11-19' in main and '2026-11-24' not in main and 'm-radio' not in main
    assert main.count('<tr role="row">') == 2 and main.count('first answer') == 2 and 'Trending question' not in main
    assert 'Back to all the questions' in main and '<a href="../index.html" aria-current="page">AI models</a>' in main
    # A trending question's page says so once, in plain words, and leads back to the Trending page.
    trend = pages['c/C005.html']
    assert trend.count('<p class="q-since">Trending question · added <time datetime="2026-11-20">Nov 2026</time></p>') == 1
    assert trend.count('Trending question') == 1 and '<a class="back" href="../trending-now.html">Back to the trending questions</a>' in trend
    assert '<a href="../trending-now.html" aria-current="page">Trending</a>' in trend and 'aria-current="page">AI models' not in trend
    assert trend.count('<section class="panel"') == 1 and 'm-2026-11-24' in trend and '2026-11-19' not in trend and trend.count('first answer') == 2
    # Share cards, data files and preview tags work for a trending question as for any other.
    assert {'card/C005.html', 'card/C005-local-one.html', 'card/C005-api-one.html', 'data/C005.json'} <= set(site)
    assert '<meta property="og:title" content="Is a made-up trend a real trend?" />' in trend
    data = json.loads(site['data/C005.json'])
    assert data['list'] == 'trending' and [(a['collected'], a['state'], a['changed']) for a in data['answers']] == [('2026-11-24', 'answered', None)] * 2
    old = json.loads(site['data/C001.json'])
    assert 'list' not in old and [(a['collected'], a['state']) for a in old['answers']] == [('2026-11-19', 'answered')] * 2
    records = [json.loads(line) for line in site['data/answers.jsonl'].splitlines()]
    assert len(records) == 12 and sorted({(r['question_id'], r['collected']) for r in records}) == (
        [(q, '2026-11-19') for q in IDS] + [(q, '2026-11-24') for q in TREND_IDS])
    assert all(r['state'] == 'answered' and r['previous_id'] is None for r in records)
    assert site['data/README.txt'] == archive_pages.README_CHAT_MODELS + archive_pages.README_LISTS
    # "Just for fun": each model's style counts all six of its shown answers, whichever date they came on.
    view = archive_pages._View(site_data)
    styles = archive_pages._styles(view)
    for label, name in (('local-one', 'sample-local:1b'), ('api-one', 'gpt-test-mini')):
        texts = [f"PLACEHOLDER first {name} to: {q['text']}" for q in chatbots.load_config()['items']]
        assert styles[label][0] == profiles.profile(texts) and styles[label][0]['count'] == 6 and styles[label][0]['step'] is not None
    how = visible(pages['chatbots.html'])
    assert how.count('from 6 answers so far') == 2 and how.count('asked 24 Nov 2026 · 2 times in all') == 2
    # The local app has both tracks: the chat-app list is "Trending", the model track's is named apart.
    local = archive_pages.render_site(payload)
    assert nav(local['index.html']) == ['AI models', 'Trending', 'Model trends', 'Models', 'How it works']
    assert local['trending-now.html'] != local['trending.html'] and 'Questions from what people search for' in local['trending.html']
    with TestClient(app) as client:
        assert client.get('/archive/trending-now.html').status_code == 200 and client.get('/archive/trending.html').status_code == 200
        assert 'Is a made-up trend a real trend?' in client.get('/archive/trending-now.html').text
        assert client.post('/archive/trending-now.html').status_code == 405


def test_december_asks_everything_together_and_compares_each_question_with_its_own_last_answer(asking, tmp_path, monkeypatch):
    main_then_trending(tmp_path, monkeypatch)
    monkeypatch.setattr(chatbots, 'today', lambda: DEC_1)
    new = {('sample-local:1b', 'C001'), ('gpt-test-mini', 'C006')}

    def december(model, prompt, n):
        ids = {q['prompt']: q['id'] for q in chatbots.load_config()['items']}
        return reply('second' if (model, ids[prompt]) in new else 'first')(model, prompt, n)
    adapter(monkeypatch, december)
    done = chatbots.ask(max_usd=1)
    assert (done['run'], done['questions'], done['left_out']) == (3, IDS + TREND_IDS, [])
    changed = {(a['chatbot'], a['question_id']): a['changed'] for a in rows(
        'SELECT a.chatbot,a.question_id,a.changed FROM archive_chatbot_answers a JOIN archive_chatbot_runs r ON r.id=a.run_id WHERE r.number=3')}
    # C001 is compared with 19 November, C006 with 24 November: each with its own answer before, whatever lay between.
    assert changed == {(label, q): int((model, q) in new) for label, model in (('local-one', 'sample-local:1b'), ('api-one', 'gpt-test-mini'))
                       for q in IDS + TREND_IDS}
    payload = archive.public_payload()
    site = standalone(payload)
    front, trending = site['index.html'], site['trending-now.html']
    assert front.count('tag-changed') == 1 and trending.count('tag-changed') == 1 and 'From <time' not in front + trending
    assert 'Before · <time datetime="2026-11-19">Nov 2026</time>' in front and 'Before · <time datetime="2026-11-24">Nov 2026</time>' in trending
    for name, first in (('c/C001.html', '2026-11-19'), ('c/C006.html', '2026-11-24')):
        page = site[name]
        assert page.count('<section class="panel"') == 2 and f'm-{first}' in page and 'm-2026-12-01' in page
        assert re.findall(r'<span class="m-name">([^<]+)</span>', page) == ['Dec 2026', 'Nov 2026'] and '1 changed' in page
        assert page.count('<tr role="row">') == 3 and 'not asked' not in page and 'left out' not in page
        assert page.count('History · 1 change') == 1
    data = json.loads(site['data/C001.json'])
    assert [(a['collected'], a['state']) for a in data['answers']] == [('2026-11-19', 'answered')] * 2 + [('2026-12-01', 'changed'), ('2026-12-01', 'same')]
    assert archive_pages._styles(archive_pages._View(chatbots.site_payload(payload)))['local-one'][0]['count'] == 12


def test_a_question_recorded_but_never_asked_is_on_no_page_until_it_is(asking, tmp_path, monkeypatch):
    # The first run of all is a partial one: every question is recorded (fixed) with it, only the main ones are asked.
    models_only(tmp_path, monkeypatch, trending=TREND)
    adapter(monkeypatch, reply('first'))
    done = chatbots.ask(max_usd=1, question_list='main')
    assert done['first_run'] is True and done['new_questions'] == IDS + TREND_IDS and done['questions'] == IDS
    payload = archive.public_payload()
    assert [q['id'] for q in payload['chatbots']['questions']] == IDS + TREND_IDS and payload['schema'] == 'live-genesis-archive/8'
    site = standalone(payload)
    assert not [name for name in site if 'C005' in name or 'C006' in name] and 'trending-now.html' not in site
    assert nav(site['index.html']) == ['AI models', 'How it works'] and '<li>4 questions</li>' in site['index.html']
    assert all(TREND[0]['text'] not in text for text in site.values())
    with pytest.raises(KeyError):
        archive_pages.render_chat_trending(payload)
    with TestClient(app) as client:
        assert client.get('/archive/trending-now.html').status_code == 404


# ---- the tab only when there is something to show ---------------------------------------------------------------------

def test_no_trending_tab_while_no_trending_question_has_an_answer_to_show(asking, tmp_path, monkeypatch):
    adapter(monkeypatch, reply('first'))
    chatbots.ask(max_usd=1)
    plain = standalone(archive.public_payload())
    assert 'trending-now.html' not in plain and all('rending' not in page for name, page in plain.items() if name.endswith('.html'))
    assert nav(plain['index.html']) == ['AI models', 'How it works'] and archive.public_payload()['schema'] == 'live-genesis-archive/7'
    # The trending questions are asked, and every reply is one the pages never show (cut off at the output limit).
    models_only(tmp_path, monkeypatch, trending=TREND)
    monkeypatch.setattr(chatbots, 'today', lambda: NOV_24)
    adapter(monkeypatch, lambda model, prompt, n: engine.Completion('PLACEHOLDER cut', 40, 12, 'length', finish_reason='length'))
    done = chatbots.ask(max_usd=1, question_list='trending')
    assert done['answers'] == {'answered': 0, 'no_answer': 4, 'withheld': 0}
    payload = archive.public_payload()
    assert payload['schema'] == 'live-genesis-archive/8'
    site = standalone(payload)
    # No tab and no Trending page; the two questions are not lost: they are listed on the front page, with nothing to show yet.
    assert 'trending-now.html' not in site and nav(site['index.html']) == ['AI models', 'How it works']
    assert '<li>6 questions</li>' in site['index.html'] and site['index.html'].count('No answer yet') == 4
    assert 'Back to all the questions' in site['c/C005.html'] and '<a href="../index.html" aria-current="page">AI models</a>' in site['c/C005.html']
    assert 'Trending question · added' in site['c/C005.html']
    with pytest.raises(KeyError):
        archive_pages.render_chat_trending(payload)


def test_the_published_site_of_today_is_unchanged_and_still_passes_the_check(monkeypatch, tmp_path):
    published = REPO / 'published'
    data = json.loads((published / 'archive.json').read_text(encoding='utf-8'))
    if any('list' in q for q in data['chatbots']['questions']):
        pytest.skip('the owner has exported trending answers since: this test described the site before that')
    assert snapshot.check_published(published)['reviewed'] is True                        # --check accepts the file as it is
    assert data['schema'] == chatbots.site_schema(data['chatbots']) and data['schema'] in ('live-genesis-chat-archive/2', 'live-genesis-chat-archive/3')
    monkeypatch.setenv('LIVE_DB', str(tmp_path / 'no-such.sqlite'))
    snapshot.build_from_data(tmp_path / 'site', published, archive_only=True)
    site = tmp_path / 'site' / 'archive'
    assert not (site / 'trending-now.html').exists()
    front = (site / 'index.html').read_text(encoding='utf-8')
    count = len(data['chatbots']['questions'])
    assert 'How it works' in nav(front) and 'Trending' not in nav(front) and len(nav(front)) == 2
    assert f'<li>{count} questions</li>' in front and front.count('<article class="row">') == count
    for page in site.rglob('*.html'):
        text = page.read_text(encoding='utf-8')
        assert 'rending' not in text and 'q-since' not in text, page.name


def test_hand_entered_replies_cover_trending_questions_too_and_the_schema_says_so(chat, tmp_path, monkeypatch):
    models_only(tmp_path, monkeypatch, models=(), trending=TREND)
    sheet = chatbots.template('2026-11-14')
    assert [a['question'] for a in sheet['chatbots'][0]['answers']] == IDS + TREND_IDS    # a sheet holds every question, as before
    chatbots.import_collection(filled(tmp_path))
    payload = archive.public_payload()
    assert payload['schema'] == 'live-genesis-archive/8' and archive.payload_problem(payload) is None
    site_data = chatbots.site_payload(payload)
    assert site_data['schema'] == 'live-genesis-chat-archive/4' and chatbots.export_problem(site_data['chatbots']) is None
    assert not chatbots.has_models(site_data['chatbots']) and chatbots.has_lists(site_data['chatbots'])
    site = standalone(payload)
    assert nav(site['index.html']) == ['Chat apps', 'Trending', 'How it works'] and '<li>4 questions</li>' in site['index.html']
    assert site['trending-now.html'].count('<li class="mini ') == 2 * 4                   # three apps and the Human, per question
    # The marker must fit the file both ways, and a list other than "trending" is refused.
    for edit, why in [(lambda d: d.update(schema='live-genesis-chat-archive/2'), 'the schema marker does not fit the file'),
                      (lambda d: d.update(schema='live-genesis-chat-archive/3'), 'the schema marker does not fit the file'),
                      (lambda d: d['chatbots']['questions'][4].update(list='hot'), 'chatbots: a question names a list that does not exist'),
                      (lambda d: d['chatbots']['questions'][4].update(list=None), 'chatbots: a question names a list that does not exist')]:
        bad = json.loads(json.dumps(site_data))
        edit(bad)
        assert chatbots.site_problem(bad) == why or why in chatbots.site_problem(bad)
    moved = json.loads(json.dumps(site_data))
    del moved['chatbots']['questions'][4]['list']
    assert chatbots.site_problem(moved) is None                                           # well-formed, but not what the file's configuration says
    assert chatbots.export_problem(moved['chatbots']) == 'chat-app question C005 is not the question chatbots-config.json holds under that id'
    extra = json.loads(json.dumps(site_data))
    extra['chatbots']['questions'][0]['topic'] = 'x'
    assert chatbots.closed_problem(extra['chatbots']) == 'chat-app question C001: unexpected key topic'
    assert chatbots.closed_problem(site_data['chatbots']) is None                         # `list` is the one optional key of a question


# ---- the scripted sample and the docs ---------------------------------------------------------------------------------

def test_the_scripted_sample_shows_the_tab_with_made_up_questions_labelled_as_a_sample(ready):
    archive.demo_archive()
    payload = archive.public_payload()
    block = payload['chatbots']
    assert [(q['id'], q['added']) for q in block['questions'] if q.get('list') == 'trending'] == [('C007', '2026-10-01'), ('C008', '2026-10-01')]
    assert [run['questions'][-1] for run in block['runs']] == ['C006', 'C006', 'C008', 'C008']   # they join at the third scripted date
    assert [r['first_run'] for r in rows("SELECT first_run FROM archive_chatbot_questions WHERE list='trending'")] == [3, 3]
    site = standalone(payload)
    trending = site['trending-now.html']
    assert_page_is_safe(trending)
    seen = visible(trending)
    assert 'Sample we wrote, not a real AI answer' in seen and 'here we made them up' in seen and 'what people are searching for; here' in seen
    assert archive_pages.TRENDING_LEDE not in seen                                        # a sample never claims to come from real searches
    assert 'Everything here is a sample we wrote' in seen and trending.count('<article class="row">') == 2
    assert trending.count('tag-changed') == 1 and 'Socks with sandals: brave or wrong?' in trending   # one scripted change, at the fourth date
    assert '<li>6 questions</li>' in site['index.html'] and 'Socks with sandals' not in site['index.html']
    for name in REAL_NAMES:
        assert name not in json.dumps(block) and all(name not in page for page in site.values())
    two = site['c/C007.html']
    assert re.findall(r'<span class="m-name">([^<]+)</span>', two) == ['Nov 2026', 'Oct 2026'] and 'Trending question · added' in two
    assert site['data/README.txt'] == archive_pages.README_CHAT + archive_pages.README_LISTS


REAL_NAMES = ('ChatGPT', 'Gemini', 'Grok', 'Copilot', 'Claude', 'Phi-4', 'Gemma', 'Granite', 'Qwen')


def test_docs_say_how_to_ask_only_the_trending_questions():
    month = (REPO / 'docs' / 'MONTHLY-ROUND.md').read_text(encoding='utf-8')
    for told in ('archive-chatbot-ask --list trending --dry-run', 'archive-chatbot-ask --list trending --max-usd', 'December'):
        assert told in month, told
    doc = (REPO / 'docs' / 'ANSWERS-OVER-TIME.md').read_text(encoding='utf-8')
    section = doc.split('## The chat-app track', 1)[1].split('\n## ', 1)[0]
    for told in ('"list": "trending"', '--list trending', '--questions', 'trending-now.html', 'archive_schema_v8.sql',
                 'live-genesis-chat-archive/4', 'live-genesis-archive/8'):
        assert told in section, told
    for name in ('SESSION-STATE.md', 'ROADMAP.md'):
        assert '--list trending' in (REPO / 'docs' / name).read_text(encoding='utf-8'), name
