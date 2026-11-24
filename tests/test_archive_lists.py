"""Answers Over Time, the chat-app track's QUESTION LISTS (owner's decision of 23 November 2026): a question of
`archive_chatbots.json` may be marked `"list": "trending"`, `cli archive-chatbot-ask --list trending` asks only those
(it is `--questions` with that list's ids), and the site gets a "Trending" tab (`archive/trending-now.html`) that exists
only when a trending question has an answer to show. The mark is a display attribute of the configuration: it is in no
hash, table or payload, so a database or a published file made before it is what it was.

Offline: `engine.infer` AND `engine.REAL_INFER` are replaced by a stand-in in every test that asks (tests/test_archive_ask.py
`adapter`), so nothing here contacts a provider, and every "reply" is an obvious placeholder."""
import datetime
import hashlib
import json
import re

import pytest
from fastapi.testclient import TestClient

from live_genesis import archive, archive_chatbots as chatbots, archive_pages, config, core, engine, guards, profiles, snapshot
from live_genesis.app import app
from test_archive import REPO, assert_page_is_safe, ready, rows, run_cli  # noqa: F401
from test_archive_ask import LOCAL, LOCAL_URL, MODELS, SECRETS, adapter, nothing_stored
from test_archive_chatbots import (CONFIG, IDS, PREFIX, chat, config_file, filled, question, visible)  # noqa: F401

API = {'label': 'api-one', 'kind': 'model', 'name': 'Sample model M', 'maker': 'Made-up maker M', 'answerer': 'mini', 'group': 'ai-models'}
TREND = [dict(question(5, 'Is a made-up trend a real trend?', 'Made-up trend?'), added='2026-11-20', list='trending'),
         dict(question(6, 'Would a placeholder say yes to this?', 'Placeholder says?'), added='2026-11-20', list='trending')]
UNMARKED = [{key: value for key, value in q.items() if key != 'list'} for q in TREND]
TREND_IDS = ['C005', 'C006']
NOV_19, NOV_24, DEC_1 = datetime.date(2026, 11, 19), datetime.date(2026, 11, 24), datetime.date(2026, 12, 1)
REAL_NAMES = ('ChatGPT', 'Gemini', 'Grok', 'Copilot', 'Claude', 'Phi-4', 'Gemma', 'Granite', 'Qwen')
# What the code before question lists recorded for the thirteen questions (the owner's real database and the published
# file hold exactly these): pinned here, so no change to a recorded hash can go unnoticed.
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
    'C011': 'ea2ed833fd92d636d739252de0fcfe1488fc54fb5257caee1d962cd26cbfa56f',
    'C012': 'c18d964f23e1713d3c33cce97ecf380274787573fdc299fc2d33c50c6b44e7e2',
    'C013': '26612619aaef99980c2e42489398e7cb2c55401e5b323e38d7a5f4b043885138',
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


def main_then_trending(tmp_path, monkeypatch, added=TREND):
    """19 November: every question there is (the four main ones) is asked. Then two questions are added to the
    configuration, and on 24 November only those are asked: `--list trending` when they are marked."""
    adapter(monkeypatch, reply('first'))
    first = chatbots.ask(max_usd=1)
    models_only(tmp_path, monkeypatch, trending=added)
    monkeypatch.setattr(chatbots, 'today', lambda: NOV_24)
    calls = adapter(monkeypatch, reply('first'))
    second = chatbots.ask(max_usd=1, questions=chatbots.list_ids('trending') if added is TREND else 'C005,C006')
    return first, second, calls


def standalone(payload):
    return archive_pages.render_site(chatbots.site_payload(payload), standalone=True)


def nav(page):
    return re.findall(r'>([^<>]+)</a>', page.split('<nav', 1)[1].split('</nav>', 1)[0])


def old_hash(item):
    """The hash as the code before question lists worked it out: the five keys, nothing else."""
    body = {key: item[key] for key in ('id', 'text', 'prompt', 'hook', 'added')}
    return hashlib.sha256(json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')).hexdigest()


# ---- the configuration field: a display attribute, outside every hash ----------------------------------------------

def test_the_three_new_questions_are_marked_trending_and_no_recorded_hash_changes(tmp_path, monkeypatch):
    raw = json.loads(CONFIG.read_text(encoding='utf-8'))
    loaded = chatbots.load_config()
    assert raw['format'] == 'live-genesis-chatbots/3'                                   # an optional field: the file format is the same
    assert [q['id'] for q in raw['questions'] if 'list' in q] == ['C011', 'C012', 'C013', 'C014', 'C015'] and 'main' not in json.dumps(raw['questions'])
    # C014 and C015 were added on 24 Nov 2026, marked from the start and not yet asked.
    assert loaded['lists'] == {i: 'trending' for i in ('C011', 'C012', 'C013', 'C014', 'C015')} == chatbots.question_lists()
    assert chatbots.LISTS == ('trending',) and chatbots.question_lists(demo=True) == {'C007': 'trending', 'C008': 'trending'}
    # A question is the five fields it always was, marked or not, and its recorded hash is what the version before worked
    # out, to the byte: the real database and the published file recorded all thirteen WITHOUT the mark.
    assert chatbots.QUESTION_KEYS == ('id', 'text', 'prompt', 'hook', 'added')
    for item in loaded['items']:
        assert set(item) == set(chatbots.QUESTION_KEYS) and chatbots.question_hash(item) == old_hash(item), item['id']
    assert {i: chatbots.question_hash(loaded['prompts'][i]) for i in RECORDED} == RECORDED and len(RECORDED) == 13
    # Nor does the configuration hash a run records: the same file without the marks hashes the same.
    for entry in raw['questions']:
        entry.pop('list', None)
    plain = tmp_path / 'unmarked.json'
    plain.write_text(json.dumps(raw), encoding='utf-8')
    assert chatbots.load_config(plain)['sha256'] == loaded['sha256'] and chatbots.load_config(plain)['lists'] == {}
    assert chatbots.load_config(plain)['items'] == loaded['items']
    # The politics word check and the content guards apply to a trending question as to any other.
    for question_id in loaded['lists']:
        item = loaded['prompts'][question_id]
        for text in (item['text'], item['hook']):
            assert chatbots.political_term(text) is None and guards.content_reason(text) is None
    # A configuration that cannot be read marks nothing: every question is then listed on the front page.
    monkeypatch.setattr(chatbots, 'CONFIG_FILE', tmp_path / 'no-such.json')
    assert chatbots.question_lists() == {}


def test_a_list_must_be_trending_and_a_trending_question_passes_the_same_checks(chat, tmp_path, monkeypatch):
    models_only(tmp_path, monkeypatch, models=(), trending=UNMARKED)
    unmarked = chatbots.load_config()
    models_only(tmp_path, monkeypatch, models=(), trending=TREND)
    loaded = chatbots.load_config()
    assert loaded['questions'] == IDS + TREND_IDS and loaded['lists'] == {'C005': 'trending', 'C006': 'trending'}
    assert (loaded['sha256'], loaded['items']) == (unmarked['sha256'], unmarked['items']) and unmarked['lists'] == {}
    for bad, why in [(dict(list='main'), 'C005: list must be "trending", or left out for the main list'),
                     (dict(list='hot'), 'C005: list must be "trending"'), (dict(list=None), 'C005: list must be "trending"'),
                     (dict(list=['trending']), 'C005: list must be "trending"'),
                     (dict(topic='x'), 'question 5 must hold id, text, prompt and added, and may have hook and list'),
                     (dict(text='Who wins the election?', prompt=f'{PREFIX}\n\nWho wins the election?'), 'C005: holds the word "election"'),
                     (dict(text='Mail me at someone@example.com', prompt=f'{PREFIX}\n\nMail me at someone@example.com'), 'C005: text must be')]:
        models_only(tmp_path, monkeypatch, models=(), trending=[{**TREND[0], **bad}])
        with pytest.raises(archive.ArchiveRefused, match=re.escape(why)):
            chatbots.load_config()


# ---- questions recorded before the mark existed --------------------------------------------------------------------

def test_questions_recorded_without_the_mark_still_load_ask_and_export_and_then_show_as_trending(asking, tmp_path, monkeypatch):
    # As main recorded C011 to C013: the questions are in the file WITHOUT a list, asked with --questions on a later date.
    main_then_trending(tmp_path, monkeypatch, added=UNMARKED)
    recorded = rows('SELECT * FROM archive_chatbot_questions ORDER BY position')
    assert [r['id'] for r in recorded] == IDS + TREND_IDS and 'list' not in recorded[0]      # no column: the tables are what they were
    payload_before = json.dumps(archive.public_payload())
    before = standalone(archive.public_payload())
    assert 'trending-now.html' not in before and nav(before['index.html']) == ['AI models', 'How it works']
    assert '<li>6 questions</li>' in before['index.html']
    out = tmp_path / 'published'
    snapshot.export_published(out, archive_only=True)
    snapshot.approve_published(out, confirmed=True)
    exported = (out / 'archive.json').read_bytes()
    # Now the owner marks the two in the file. Nothing recorded has to change for it.
    models_only(tmp_path, monkeypatch, trending=TREND)
    with core.connect() as cx:
        assert chatbots.questions_problem(cx, chatbots.load_config()) is None
    assert json.dumps(archive.public_payload()) == payload_before                            # the payload does not carry the mark
    assert archive.public_payload()['schema'] == 'live-genesis-archive/7'
    assert snapshot.check_published(out)['reviewed'] is True and (out / 'archive.json').read_bytes() == exported
    # The site built from the SAME approved file now lists them under Trending: no new export is needed.
    monkeypatch.setenv('LIVE_DB', str(tmp_path / 'no-such.sqlite'))
    snapshot.build_from_data(tmp_path / 'site', out, archive_only=True)
    monkeypatch.setenv('LIVE_DB', str(asking))
    site = tmp_path / 'site' / 'archive'
    front, trending = (site / 'index.html').read_text(encoding='utf-8'), (site / 'trending-now.html').read_text(encoding='utf-8')
    assert nav(front) == ['AI models', 'Trending', 'How it works'] and '<li>4 questions</li>' in front and '<li>2 questions</li>' in trending
    assert re.findall(r'class="row-open" href="c/(C\d{3})\.html"', front) == IDS
    assert re.findall(r'class="row-open" href="c/(C\d{3})\.html"', trending) == TREND_IDS
    assert json.loads((tmp_path / 'site' / 'archive.json').read_text(encoding='utf-8')) == json.loads(exported)
    # And the database still asks: the trending list alone, then everything together; a new export passes the check.
    monkeypatch.setattr(chatbots, 'today', lambda: datetime.date(2026, 11, 27))
    adapter(monkeypatch, reply('first'))
    done = chatbots.ask(max_usd=1, questions=chatbots.list_ids('trending'))
    assert (done['run'], done['questions'], done['new_questions'], done['first_run']) == (3, TREND_IDS, [], False)
    monkeypatch.setattr(chatbots, 'today', lambda: DEC_1)
    assert chatbots.ask(max_usd=1)['questions'] == IDS + TREND_IDS
    again = tmp_path / 'published-2'
    snapshot.export_published(again, archive_only=True)
    snapshot.approve_published(again, confirmed=True)
    assert snapshot.check_published(again)['reviewed'] is True
    assert json.loads((again / 'archive.json').read_text(encoding='utf-8'))['schema'] == 'live-genesis-chat-archive/3'
    # Taking the mark away again is as harmless: the mark only decides where a question is listed.
    models_only(tmp_path, monkeypatch, trending=UNMARKED)
    assert 'trending-now.html' not in standalone(archive.public_payload())


# ---- asking only the questions of a list ---------------------------------------------------------------------------

def test_dry_run_for_a_list_shows_what_is_asked_and_what_is_left_out_and_calls_nothing(asking, tmp_path, monkeypatch, capsys):
    models_only(tmp_path, monkeypatch, trending=TREND)
    monkeypatch.delenv('LIVE_ALLOW_PAID_CALLS')
    calls = adapter(monkeypatch, AssertionError('a dry run calls no model'))
    assert chatbots.list_ids('trending') == 'C005,C006' and chatbots.list_ids('main') == 'C001,C002,C003,C004'
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', '--list', 'trending', '--dry-run')
    assert code == 0 and 'DRY RUN: nothing was called and nothing was stored.' in out
    for line in ('Run dated 2026-11-19.', 'Questions (2 of the 6 on the list, named with --questions), each asked once of each model',
                 'C005  Is a made-up trend a real trend?', 'C006  Would a placeholder say yes to this?',
                 'Left out this time (4): C001, C002, C003, C004. They are not asked, and this run records nothing about them',
                 'Calls: 4 (2 questions x 2 models).', 'Worst-case estimate: $0.00',
                 'python -m live_genesis.cli archive-chatbot-ask --max-usd 0.01 --questions C005,C006'):
        assert line in out, line
    assert 'C001  ' not in out and not [secret for secret in SECRETS if secret in out] and 'PAID RUN' not in out
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', '--list', 'main', '--dry-run')
    assert code == 0 and 'Questions (4 of the 6 on the list' in out and 'Left out this time (2): C005, C006.' in out
    # Without the option the plan is what it always was: every question, and no line about leaving any out.
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', '--dry-run')
    assert code == 0 and 'Questions (6), each asked once' in out and 'Calls: 12 (6 questions x 2 models).' in out
    assert 'Left out' not in out and out.rstrip().splitlines()[-2].endswith('archive-chatbot-ask --max-usd 0.01')
    nothing_stored(calls)


def test_an_unknown_list_is_refused_before_anything_else_and_every_gate_holds(asking, tmp_path, monkeypatch, capsys):
    models_only(tmp_path, monkeypatch, trending=TREND)
    calls = adapter(monkeypatch)
    monkeypatch.delenv('LIVE_ALLOW_PAID_CALLS')
    for argv, why in [(('--list', 'hot', '--max-usd', '1'), '--list must be one of: main, trending. Nothing was asked.'),
                      (('--list', '', '--dry-run'), '--list must be one of: main, trending'),
                      (('--list', 'trending', '--questions', 'C005', '--dry-run'), 'Give --list or --questions, not both'),
                      (('--questions', 'C099', '--dry-run'), '--questions names an id that is not a question')]:
        code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', *argv)
        assert code != 0 and why in out, argv
        assert 'Model answerers' not in out and 'PAID RUN' not in out and 'DRY RUN' not in out      # before the plan is even printed
    for name in ('hot', '', 'Trending', 'C005'):
        with pytest.raises(archive.ArchiveRefused, match='--list must be one of: main, trending'):
            chatbots.list_ids(name)
    nothing_stored(calls)
    # A list nobody is on: refused too (the four-question configuration has no trending question).
    models_only(tmp_path, monkeypatch)
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', '--list', 'trending', '--max-usd', '1')
    assert code != 0 and 'No question of chatbots-config.json is on the list "trending"' in out
    # Every safety gate of a full ask holds for an ask of one list: the opt-in, the cap, the worst case under the cap.
    models_only(tmp_path, monkeypatch, trending=TREND)
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', '--list', 'trending', '--max-usd', '1')
    assert code != 0 and 'Explicit opt-in required for paid calls' in out
    monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS', 'YES')
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', '--list', 'trending')
    assert code != 0 and '--max-usd is required' in out
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', '--list', 'trending', '--max-usd', '0.0000001')
    assert code != 0 and 'for 4 calls exceeds --max-usd' in out
    nothing_stored(calls)


def test_an_ask_of_the_trending_list_records_exactly_what_it_asked(asking, tmp_path, monkeypatch, capsys):
    adapter(monkeypatch, reply('first'))
    assert chatbots.ask(max_usd=1)['questions'] == IDS
    models_only(tmp_path, monkeypatch, trending=TREND)
    monkeypatch.setattr(chatbots, 'today', lambda: NOV_24)
    calls = adapter(monkeypatch, reply('first'))
    code, out = run_cli(monkeypatch, capsys, 'archive-chatbot-ask', '--list', 'trending', '--max-usd', '0.01')
    assert code == 0 and 'PAID RUN: provider requests follow.' in out and 'Stored as run 2, collected 2026-11-24.' in out
    assert '"status": "complete"' in out and 'PLACEHOLDER' not in out and not [secret for secret in SECRETS if secret in out]
    # Only the two trending prompts went out, once per model, through the paid path: one settled reservation per call.
    assert [c['prompt'] for c in calls] == [q['prompt'] for q in TREND] * 2 and {c['system'] for c in calls} == {archive.SYSTEM_LINE}
    runs = rows('SELECT number,kind,collected_on,question_ids FROM archive_chatbot_runs ORDER BY number')
    assert [(r['number'], r['kind'], r['collected_on'], json.loads(r['question_ids'])) for r in runs] == [
        (1, 'asked', '2026-11-19', IDS), (2, 'asked', '2026-11-24', TREND_IDS)]
    answers = rows('SELECT a.question_id,a.changed,a.reservation_id,r.number FROM archive_chatbot_answers a '
                   'JOIN archive_chatbot_runs r ON r.id=a.run_id ORDER BY a.id')
    assert [(a['number'], a['question_id']) for a in answers] == [(1, q) for q in IDS] * 2 + [(2, q) for q in TREND_IDS] * 2
    assert {a['changed'] for a in answers} == {None}                                      # every one a first answer; nothing "changed"
    assert len({a['reservation_id'] for a in answers}) == 12 == len(rows("SELECT * FROM budget_reservations WHERE status='settled'"))
    assert not rows("SELECT * FROM budget_reservations WHERE status!='settled'")
    assert rows('SELECT operation FROM operator_audit ORDER BY id')[-1]['operation'].endswith('questions C005,C006')
    assert [r['first_run'] for r in rows('SELECT first_run FROM archive_chatbot_questions ORDER BY position')] == [1] * 4 + [2] * 2


def test_a_subset_may_not_jump_over_a_question_nothing_has_asked(asking, tmp_path, monkeypatch):
    # Questions are recorded in the file's order and a payload with a gap is malformed, so the gap is refused up front.
    calls = adapter(monkeypatch)
    for ids, why in (('C002', 'C001 comes before C002 in chatbots-config.json and has never been asked'),
                     ('C001,C004', 'C002, C003 come before C004 in chatbots-config.json and have never been asked')):
        with pytest.raises(archive.ArchiveRefused, match=why + '.*No paid call was made'):
            chatbots.ask(max_usd=1, questions=ids)
    nothing_stored(calls)
    assert chatbots.ask(max_usd=1, questions='C001,C002')['questions'] == ['C001', 'C002']
    monkeypatch.setattr(chatbots, 'today', lambda: NOV_24)
    with pytest.raises(archive.ArchiveRefused, match='C003 comes before C004'):
        chatbots.ask(max_usd=1, questions='C004')
    assert chatbots.ask(max_usd=1, questions='C001,C003')['new_questions'] == ['C003']      # an earlier one again, and the next: fine
    assert archive.payload_problem(archive.public_payload()) is None and len(calls) == 8


# ---- the pages when one date holds only some questions -------------------------------------------------------------

def test_two_dates_all_main_then_trending_only_no_false_change_no_gap_and_a_trending_tab(asking, tmp_path, monkeypatch):
    main_then_trending(tmp_path, monkeypatch)
    payload = archive.public_payload()
    block = payload['chatbots']
    # The payload is the shape it always was: no mark on a question, the markers of a file with model answerers.
    assert archive.payload_problem(payload) is None and payload['schema'] == 'live-genesis-archive/7'
    assert all(set(q) == set(chatbots.QUESTION_KEYS) for q in block['questions'])
    assert [(run['collected_on'], run['questions']) for run in block['runs']] == [('2026-11-19', IDS), ('2026-11-24', TREND_IDS)]
    site_data = chatbots.site_payload(payload)
    assert site_data['schema'] == 'live-genesis-chat-archive/3' and chatbots.site_problem(site_data) is None
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
    assert [(a['collected'], a['state'], a['changed']) for a in data['answers']] == [('2026-11-24', 'answered', None)] * 2
    old = json.loads(site['data/C001.json'])
    assert set(data) == set(old) and [(a['collected'], a['state']) for a in old['answers']] == [('2026-11-19', 'answered')] * 2
    records = [json.loads(line) for line in site['data/answers.jsonl'].splitlines()]
    assert len(records) == 12 and sorted({(r['question_id'], r['collected']) for r in records}) == (
        [(q, '2026-11-19') for q in IDS] + [(q, '2026-11-24') for q in TREND_IDS])
    assert all(r['state'] == 'answered' and r['previous_id'] is None for r in records)
    assert site['data/README.txt'] == archive_pages.README_CHAT_MODELS
    # "Just for fun": each model's style counts all six of its shown answers, whichever date they came on.
    styles = archive_pages._styles(archive_pages._View(site_data))
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
    assert (done['run'], done['questions']) == (3, IDS + TREND_IDS)
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


# ---- the tab only when there is something to show ------------------------------------------------------------------

def test_no_trending_tab_while_no_trending_question_has_an_answer_to_show(asking, tmp_path, monkeypatch):
    adapter(monkeypatch, reply('first'))
    chatbots.ask(max_usd=1)
    # Trending questions in the file that nothing has asked: nowhere, and the bar is what it was.
    models_only(tmp_path, monkeypatch, trending=TREND)
    payload = archive.public_payload()
    plain = standalone(payload)
    assert 'trending-now.html' not in plain and all('rending' not in page for name, page in plain.items() if name.endswith('.html'))
    assert nav(plain['index.html']) == ['AI models', 'How it works'] and '<li>4 questions</li>' in plain['index.html']
    with pytest.raises(KeyError):
        archive_pages.render_chat_trending(payload)
    # They are asked, and every reply is one the pages never show (cut off at the output limit).
    monkeypatch.setattr(chatbots, 'today', lambda: NOV_24)
    adapter(monkeypatch, lambda model, prompt, n: engine.Completion('PLACEHOLDER cut', 40, 12, 'length', finish_reason='length'))
    done = chatbots.ask(max_usd=1, questions=chatbots.list_ids('trending'))
    assert done['answers'] == {'answered': 0, 'no_answer': 4, 'withheld': 0}
    payload = archive.public_payload()
    site = standalone(payload)
    # No tab and no Trending page; the two questions are not lost: they are listed on the front page, with nothing to show yet.
    assert 'trending-now.html' not in site and nav(site['index.html']) == ['AI models', 'How it works']
    assert '<li>6 questions</li>' in site['index.html'] and site['index.html'].count('No answer yet') == 4
    assert 'Back to all the questions' in site['c/C005.html'] and '<a href="../index.html" aria-current="page">AI models</a>' in site['c/C005.html']
    assert 'Trending question · added' in site['c/C005.html']
    with pytest.raises(KeyError):
        archive_pages.render_chat_trending(payload)
    with TestClient(app) as client:                                                       # the local app: no such page yet
        assert client.get('/archive/trending-now.html').status_code == 404


def test_the_published_folder_passes_the_check_and_lists_each_question_once_by_rule(monkeypatch, tmp_path):
    """The live `published/` folder changes whenever the owner publishes, so nothing here is a pinned figure: the rules
    are checked against whatever it holds and whatever the checked-in configuration marks as trending."""
    published = REPO / 'published'
    before = {path.name: path.read_bytes() for path in published.iterdir() if path.is_file()}
    data = json.loads((published / 'archive.json').read_text(encoding='utf-8'))
    block = data['chatbots']
    assert snapshot.check_published(published)['reviewed'] is True                        # --check accepts the file as it is
    assert all(set(q) == set(chatbots.QUESTION_KEYS) for q in block['questions'])        # the file carries no list mark
    monkeypatch.setenv('LIVE_DB', str(tmp_path / 'no-such.sqlite'))
    snapshot.build_from_data(tmp_path / 'site', published, archive_only=True)
    assert {path.name: path.read_bytes() for path in published.iterdir() if path.is_file()} == before   # never written to
    site = tmp_path / 'site' / 'archive'
    marked = chatbots.question_lists()
    shown = {a['question'] for a in block['answers'] if a['state'] == 'answered' and not a['refused']}
    trending = [q['id'] for q in block['questions'] if q['id'] in marked]
    main = [q['id'] for q in block['questions'] if q['id'] not in marked]
    front = (site / 'index.html').read_text(encoding='utf-8')
    listed = lambda page: sorted(re.findall(r'class="row-open" href="c/(C\d{3})\.html"', page))  # noqa: E731
    if not set(trending) & shown:
        assert not (site / 'trending-now.html').exists() and 'Trending' not in nav(front)
        assert listed(front) == [q['id'] for q in block['questions']]
        return
    page = (site / 'trending-now.html').read_text(encoding='utf-8')
    assert nav(front)[1] == nav(page)[1] == 'Trending' and nav(front)[-1] == 'How it works' and len(nav(front)) == 3
    assert listed(front) == main and listed(page) == sorted(trending)
    assert f'<li>{len(main)} question{"" if len(main) == 1 else "s"}</li>' in front
    assert f'<li>{len(trending)} question{"" if len(trending) == 1 else "s"}</li>' in page
    for question_id in trending:
        own = (site / 'c' / f'{question_id}.html').read_text(encoding='utf-8')
        assert own.count('Trending question · added') == 1 and 'href="../trending-now.html">Back to the trending questions' in own
        assert (site / 'card' / f'{question_id}.html').is_file() and (site / 'data' / f'{question_id}.json').is_file()
    for question_id in main:
        assert 'Trending question' not in (site / 'c' / f'{question_id}.html').read_text(encoding='utf-8')


def test_hand_entered_replies_cover_trending_questions_too(chat, tmp_path, monkeypatch):
    models_only(tmp_path, monkeypatch, models=(), trending=TREND)
    sheet = chatbots.template('2026-11-14')
    assert [a['question'] for a in sheet['chatbots'][0]['answers']] == IDS + TREND_IDS    # a sheet holds every question, as before
    chatbots.import_collection(filled(tmp_path))
    payload = archive.public_payload()
    assert payload['schema'] == 'live-genesis-archive/6' and archive.payload_problem(payload) is None
    site_data = chatbots.site_payload(payload)
    assert site_data['schema'] == 'live-genesis-chat-archive/2' and chatbots.export_problem(site_data['chatbots']) is None
    site = standalone(payload)
    assert nav(site['index.html']) == ['Chat apps', 'Trending', 'How it works'] and '<li>4 questions</li>' in site['index.html']
    assert site['trending-now.html'].count('<li class="mini ') == 2 * 4                   # three apps and the Human, per question
    # A mark inside a published file is not part of its shape: the closed key check refuses it.
    extra = json.loads(json.dumps(site_data))
    extra['chatbots']['questions'][4]['list'] = 'trending'
    assert chatbots.closed_problem(extra['chatbots']) == 'chat-app question C005: unexpected key list'


# ---- the scripted sample and the docs ------------------------------------------------------------------------------

def test_the_scripted_sample_shows_the_tab_with_made_up_questions_labelled_as_a_sample(ready):
    archive.demo_archive()
    payload = archive.public_payload()
    block = payload['chatbots']
    assert [(q['id'], q['added']) for q in block['questions'][6:]] == [('C007', '2026-10-01'), ('C008', '2026-10-01')]
    assert [run['questions'][-1] for run in block['runs']] == ['C006', 'C006', 'C008', 'C008']   # they join at the third scripted date
    assert [r['first_run'] for r in rows("SELECT first_run FROM archive_chatbot_questions WHERE id IN ('C007','C008')")] == [3, 3]
    site = standalone(payload)
    trending = site['trending-now.html']
    assert_page_is_safe(trending)
    seen = visible(trending)
    assert 'Sample we wrote, not a real AI answer' in seen and 'here we made them up' in seen and 'what people are searching for; here' in seen
    assert archive_pages.TRENDING_LEDE not in seen                                        # a sample never claims to come from real searches
    assert 'Everything here is a sample we wrote' in seen and trending.count('<article class="row">') == 2
    assert trending.count('tag-changed') == 1 and 'Socks with sandals: brave or wrong?' in trending   # one scripted change, at the fourth date
    assert '<li>6 questions</li>' in site['index.html'] and 'Socks with sandals' not in site['index.html']
    # The sample's list is its own: the checked-in configuration's marks (C011 ...) play no part in a scripted site.
    assert re.findall(r'class="row-open" href="c/(C\d{3})\.html"', trending) == ['C007', 'C008']
    for name in REAL_NAMES:
        assert name not in json.dumps(block) and all(name not in page for page in site.values())
    two = site['c/C007.html']
    assert re.findall(r'<span class="m-name">([^<]+)</span>', two) == ['Nov 2026', 'Oct 2026'] and 'Trending question · added' in two


def test_docs_say_how_to_ask_only_the_trending_questions():
    month = (REPO / 'docs' / 'MONTHLY-ROUND.md').read_text(encoding='utf-8')
    for told in ('archive-chatbot-ask --list trending --dry-run', 'archive-chatbot-ask --list trending --max-usd', 'December'):
        assert told in month, told
    doc = (REPO / 'docs' / 'ANSWERS-OVER-TIME.md').read_text(encoding='utf-8')
    section = doc.split('## The chat-app track', 1)[1].split('\n## ', 1)[0]
    for told in ('"list": "trending"', '--list trending', '--questions', 'trending-now.html'):
        assert told in section, told
    for name in ('SESSION-STATE.md', 'ROADMAP.md'):
        assert '--list trending' in (REPO / 'docs' / name).read_text(encoding='utf-8'), name
