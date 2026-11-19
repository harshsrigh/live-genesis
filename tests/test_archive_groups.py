"""Answers Over Time, the "Group chat" pages: any number of answerers, answerer groups, the position palette, share-card
pagination, the headline counted from the data, measured contrast, and the in-place upgrade of a database made before
groups. Offline; every "reply" is a placeholder and every app name is made up."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from live_genesis import archive, archive_chatbots as chatbots, archive_pages, core
from test_archive import REPO, STATIC, as_real, assert_page_is_safe, ready, rows  # noqa: F401
from test_archive_chatbots import CONDITIONS, IDS, PREFIX, TODAY, chat, config_file, filled, visible  # noqa: F401

V6_SCHEMA = REPO / 'tests' / 'fixtures' / 'archive_schema_v6.sql'
AI = {'id': 'ai-apps', 'name': 'AI chat apps'}
DESK = {'id': 'help-desk', 'name': 'Help-desk bots'}


def bots(count, group=None, first=0):
    """`count` made-up chat apps: labels bot-a ..., names "Sample bot A" ..."""
    return [{'label': f'bot-{chr(97 + n)}', 'name': f'Sample bot {chr(65 + n)}', 'maker': f'Made-up maker {chr(65 + n)}',
             **({'group': group} if group else {})} for n in range(first, first + count)]


def set_apps(tmp_path, monkeypatch, apps, groups=None):
    def edit(data):
        data['chatbots'] = apps
        if groups is None:
            data.pop('groups', None)
        else:
            data['groups'] = groups
    return config_file(tmp_path, monkeypatch, edit=edit)


def payload_for(tmp_path, monkeypatch, apps, groups=None, human=True, long=False):
    """One import (10 October) and a second (14 November) in which the first two apps and the Human answer C001 differently."""
    set_apps(tmp_path, monkeypatch, apps, groups)
    labels = [app['label'] for app in apps]
    text = ('PLACEHOLDER words. ' * 7).strip() if long else None
    first = lambda app, q: text or f'PLACEHOLDER first {app} {q}.'                                                     # noqa: E731
    second = lambda app, q: (f'PLACEHOLDER second {app}.' if q == 'C001' and app in labels[:2] + ['human'] else first(app, q))   # noqa: E731
    chatbots.import_collection(filled(tmp_path, '2026-10-10', reply=first, human=human), allow_old=True)
    chatbots.import_collection(filled(tmp_path, '2026-11-14', reply=second, human=human))
    payload = archive.public_payload()
    assert archive.payload_problem(payload) is None
    return payload


def looks(html, element):
    """(who class, mark shape, mark text) of every answerer bubble of one kind on a page, in order."""
    found = re.findall(r'<(?:article|li|section) class="' + element + r'([^"]*)"[^>]*><span class="av([^"]*)" aria-hidden="true">([^<]*)</span>', html)
    return [((re.search(r'who-\d', classes) or re.search(r'kind-human|card-panel-human', classes)).group(0), shape.strip(), mark)
            for classes, shape, mark in found]


# ---- configuration: groups are optional, and a later group is only an addition ------------------------------

def test_groups_in_the_configuration_default_validation_and_limits(tmp_path, monkeypatch):
    assert chatbots.CONFIG_FORMAT == 'live-genesis-chatbots/3' and chatbots.MAX_CHATBOTS == 24 and chatbots.MAX_GROUPS == 6
    # No `groups` key: the one default group, and every app in it.
    set_apps(tmp_path, monkeypatch, bots(3))
    config = chatbots.load_config()
    assert config['groups'] == [chatbots.DEFAULT_GROUP] == [AI] and {c['group'] for c in config['chatbots']} == {'ai-apps'}
    plain = config['sha256']
    # A second group is a configuration addition: its own apps name it; an app that names none belongs to the first.
    set_apps(tmp_path, monkeypatch, bots(3) + bots(2, 'help-desk', first=3), [AI, DESK])
    config = chatbots.load_config()
    assert config['groups'] == [AI, DESK] and [c['group'] for c in config['chatbots']] == ['ai-apps'] * 3 + ['help-desk'] * 2
    assert config['sha256'] != plain and config['questions'] == IDS                       # every group is asked the same questions
    # The hash recorded for a QUESTION does not know about groups: adding one never touches a recorded question.
    assert set(chatbots.QUESTION_KEYS) == {'id', 'text', 'prompt', 'hook', 'added'}
    # The template holds every app of every group; nothing in it changes shape.
    data = chatbots.template('2026-11-14')
    assert [block['chatbot'] for block in data['chatbots']] == [f'bot-{c}' for c in 'abcde'] and data['format'] == 'live-genesis-chatbot-collection/2'
    assert all(set(block) == {'chatbot', 'name', 'maker', 'conditions', 'answers'} for block in data['chatbots'])
    for apps, groups, why in [
        (bots(2, 'nowhere'), [AI], 'its group is not one of the groups'),
        (bots(2), [AI, dict(AI, name='Another name')], 'group ids and group names must differ'),
        (bots(2), [AI, dict(DESK, name='ai CHAT apps')], 'group ids and group names must differ'),
        (bots(2), [{'id': 'Help Desk', 'name': 'x'}], 'a group id must be'),
        (bots(2), [{'id': 'human', 'name': 'x'}], 'a group id must be'),
        (bots(2), [{'id': 'bots-2', 'name': 'x'}], 'a group id must be'),                  # would read as a second card
        (bots(2), [{'id': 'ai-apps'}], 'each group needs exactly id and name'),
        (bots(2), [dict(AI, url='x')], 'each group needs exactly id and name'),
        (bots(2), [], 'groups must list'),
        (bots(2), 'ai-apps', 'groups must list'),
        (bots(2), [{'id': f'group-{c}', 'name': f'Group {c}'} for c in 'abcdefg'], 'there must be 1 to 6 groups'),
        ([dict(bots(1)[0], label='ai-apps')], [AI], 'a label may not be a group id'),       # card/C001-ai-apps.html would be two things
        ([dict(bots(1)[0], label='ai-apps-2')], [AI], 'a label may not be a group id'),
        ([dict(bots(1)[0], group=3)], [AI], "a chat app's group must be a group id"),
        (bots(25), None, 'chatbots must list 1 to 24'),
    ]:
        set_apps(tmp_path, monkeypatch, apps, groups)
        with pytest.raises(archive.ArchiveRefused, match=why):
            chatbots.load_config()
    set_apps(tmp_path, monkeypatch, bots(24))
    assert len(chatbots.load_config()['chatbots']) == 24
    # A group may be declared before its first answerer (it is then recorded and shown nowhere); a PUBLISHED block never
    # holds an empty group.
    set_apps(tmp_path, monkeypatch, bots(2), [AI, DESK])
    assert chatbots.load_config()['groups'] == [AI, DESK]
    apps = [dict(app, group='ai-apps') for app in bots(2)]
    assert chatbots.groups_problem([AI, DESK], apps) == 'group help-desk has no chat app'
    assert chatbots.groups_problem([AI, DESK], apps, allow_empty=True) is None


# ---- any number of answerers ---------------------------------------------------------------------------------

@pytest.mark.parametrize('count, human', [(1, True), (1, False), (2, True), (3, True), (3, False), (5, True), (8, True), (8, False),
                                          (12, True), (12, False)])
def test_a_thread_renders_any_number_of_answerers_and_no_two_look_the_same(chat, tmp_path, monkeypatch, count, human):
    payload = payload_for(tmp_path, monkeypatch, bots(count), human=human)
    block = payload['chatbots']
    assert len(block['apps']) == count and block['groups'] == [AI] and (block['human'] is not None) == human
    pages = archive_pages.render_site(payload)
    for name, page in pages.items():
        if name.endswith('.html'):
            assert_page_is_safe(page)
            assert page.count('<h1') == 1 and page.count('<div') == page.count('</div>') and page.count('<span') == page.count('</span>'), name
            assert 'Sample we wrote' not in page and not re.search(r'\bround', page, re.I), name
    total = count + (1 if human else 0)
    front, page = pages['index.html'], pages['c/C001.html']
    newest = page.split('<section class="panel"')[1]
    # One bubble per answerer, on the list and on the question page, in the configured order; the Human is last.
    assert newest.count('<article class="acard') == total and front.count('class="mini-text"') == total * len(IDS)
    assert f'<div class="cards n-{min(total, 6)}{" n-wide" if total >= 5 else ""}">' in newest
    names = re.findall(r'<span class="acard-name mono"[^>]*>(?:<meta[^>]*>|<span itemprop="name">)?([^<]+)', newest)
    assert names == [f'Sample bot {chr(65 + n)}' for n in range(count)] + (['Human'] if human else [])
    # The look comes from the POSITION: eight colour pairs, then the same eight with another shape of the mark.
    seen = looks(newest, 'acard')
    assert len(seen) == total and len(set(seen)) == total
    apps = seen[:count]
    assert [who for who, _shape, _mark in apps] == [f'who-{n % 8 + 1}' for n in range(count)]
    assert [shape for _who, shape, _mark in apps] == ['' if n < 8 else 'av-b' for n in range(count)]
    assert len({(who, shape) for who, shape, _mark in apps}) == count                    # never the same colour AND shape twice
    # "Sample bot A", "Sample bot B": the letter that tells them apart. One answerer alone keeps its first letter.
    assert [mark for _who, _shape, mark in apps] == ([chr(65 + n) for n in range(count)] if count > 1 else ['S'])
    if human:
        assert seen[-1] == ('kind-human', '', 'H')
    assert looks(front.split('<article class="row">')[1], 'mini') == seen               # the same look on the list
    # No group name anywhere with one group, and no group switch.
    for text in (front, page, pages['chatbots.html']):
        assert 'g-radio' not in text and 'group-strip' not in text and 'thread-name' not in text and 'apps-name' not in text
        assert 'AI chat apps' not in text
    # Changed: the word, and the earlier answer directly above the new one (dashed and struck through by the stylesheet).
    assert newest.count('<span class="tag-changed mono">Changed</span>') == min(count, 2)
    assert newest.count('<span class="tag-changed mono">Different answer this month</span>') == (1 if human else 0)
    first = newest.split('<article class="acard')[1]
    assert first.index('<div class="before">') < first.index('<div class="bubble">') and 'PLACEHOLDER first bot-a C001.' in first
    assert f'<span class="m-mark">{min(count, 2)} changed</span>' in page                   # the newest month's pill; people do not "change"
    # The table turns to blocks at every width once there are many columns.
    assert ('class="history history-tall"' in page) == (total > 5)
    # Data files: one record per answerer and date, as before.
    data = json.loads(pages['data/C001.json'])
    assert len(data['answers']) == 2 * total and [a['answerer'] for a in data['answerers']][:count] == [f'bot-{chr(97 + n)}' for n in range(count)]


def test_marks_tell_names_apart_and_never_repeat_in_a_thread():
    marks = archive_pages._marks
    assert marks(['Chat app A', 'Chat app B', 'Chat app C']) == ['A', 'B', 'C']             # the first letters are all C
    assert marks(['Alpha', 'Beta', 'Gamma']) == ['A', 'B', 'G']
    assert marks(['model-a', 'model-b']) == ['A', 'B']
    assert marks(['Alpha', 'Gamma', 'Grape']) == ['A', 'G', 'Gr']                           # two share a letter: the second takes two
    assert marks(['Same', 'Same', 'Same']) == ['S', 'Sa', 'S3'] and marks(['']) == ['?'] and marks(['<b>']) == ['B']
    many = marks([f'Bot {n}' for n in range(24)] + ['Bot 1'])
    assert len(set(many)) == 25 and all(0 < len(mark) <= 3 for mark in many)
    columns = [{'name': f'Sample bot {n}', 'kind': 'chat-app'} for n in range(24)] + [{'name': 'Human', 'kind': 'human'}]
    archive_pages._dress(columns)
    assert len({(c['who'], c['shape']) for c in columns[:24]}) == 24 and columns[-1]['who'] == ''
    assert [c['shape'] for c in columns[:24:8]] == ['', ' av-b', ' av-c']
    css = (STATIC / 'archive.css').read_text(encoding='utf-8')
    assert '.av-b { border-radius: 30%; }' in css and '.av-c { background: var(--tint); color: var(--hue); }' in css


def test_the_headline_and_copy_are_counted_from_the_data(chat, tmp_path, monkeypatch):
    def headline(payload):
        return archive_pages.chat_headline(archive_pages._View(payload).chat)
    payload = payload_for(tmp_path, monkeypatch, bots(5))
    assert headline(payload) == ['Same question.', 'Five AI apps.', 'One human.']
    front = archive_pages.render_index(payload)
    assert 'Same question. Five AI apps. One human.' in visible(front) and 'Three' not in visible(front)
    assert '<ol class="steps"><li>4 questions</li><li>Same words every month</li><li>Every answer kept</li></ol>' in front
    block = payload['chatbots']
    one = json.loads(json.dumps(payload))
    one['chatbots'].update(apps=block['apps'][:1], human=None, human_answers=[],
                           answers=[a for a in block['answers'] if a['chatbot'] == 'bot-a'],
                           runs=[dict(run, human=None, conditions={'bot-a': run['conditions']['bot-a']}) for run in block['runs']])
    assert archive.payload_problem(one) is None and headline(one) == ['Same question.', 'One AI app.']
    lone = archive_pages.render_index(one)
    assert 'One human.' not in visible(lone) and 'kind-human' not in lone and lone.count('class="mini-text"') == len(IDS)
    # A single group of another kind: no claim that they are AI apps.
    other = json.loads(json.dumps(payload))
    other['chatbots']['groups'] = [DESK]
    for app in other['chatbots']['apps']:
        app['group'] = 'help-desk'
    assert archive.payload_problem(other) is None and headline(other) == ['Same question.', 'Five chatbots.', 'One human.']
    from types import SimpleNamespace
    many = SimpleNamespace(labels=['x'] * 13, groups=[AI], human=None, columns=[])
    assert archive_pages.chat_headline(many) == ['Same question.', '13 AI apps.']            # no word for it: the number
    assert archive_pages.chat_headline(SimpleNamespace(labels=[], groups=[], human={'name': 'Human'}, columns=[])) == ['Same question.', 'One human.']


# ---- several groups ------------------------------------------------------------------------------------------

def two_groups(tmp_path, monkeypatch, human=True):
    return payload_for(tmp_path, monkeypatch, bots(3) + bots(8, 'help-desk', first=3), [AI, DESK], human=human)


def test_two_groups_a_switch_on_the_front_page_and_one_thread_per_group_on_a_question_page(chat, tmp_path, monkeypatch):
    payload = two_groups(tmp_path, monkeypatch)
    block = payload['chatbots']
    assert block['groups'] == [AI, DESK] and [app['group'] for app in block['apps']] == ['ai-apps'] * 3 + ['help-desk'] * 8
    assert payload['schema'] == 'live-genesis-archive/6' and chatbots.site_payload(payload)['schema'] == 'live-genesis-chat-archive/2'
    assert rows("SELECT DISTINCT group_id, group_name FROM archive_chatbot_conditions ORDER BY group_id") == [
        {'group_id': 'ai-apps', 'group_name': 'AI chat apps'}, {'group_id': 'help-desk', 'group_name': 'Help-desk bots'}]
    pages = archive_pages.render_site(payload)
    for name, page in pages.items():
        if name.endswith('.html'):
            assert_page_is_safe(page)
            assert page.count('<h1') == 1 and page.count('<div') == page.count('</div>'), name
    front, page, method = pages['index.html'], pages['c/C001.html'], pages['chatbots.html']
    # The headline is neutral and counted; the groups are named in the lede.
    assert 'Same question. Eleven chatbots in two groups. One human.' in visible(front) and 'AI apps.' not in visible(front)
    assert 'AI chat apps and Help-desk bots. Asked again every month.' in visible(front)
    # The group switch: one radio per group, the first checked, labels with the name and a count. No script.
    assert front.count('<input class="g-radio" type="radio" name="group"') == 2
    assert '<input class="g-radio" type="radio" name="group" id="grp-1" checked />' in front and front.count(' checked />') == 1
    assert ('<div class="group-strip" role="group" aria-label="Choose a group"><label for="grp-1"><span class="g-name">AI chat apps</span>'
            '<span class="g-count">3 asked</span></label><label for="grp-2"><span class="g-name">Help-desk bots</span>'
            '<span class="g-count">8 asked</span></label></div>') in front
    assert front.index('id="grp-2"') < front.index('class="group-strip"') < front.index('<div class="qs">')      # siblings before the list: the stylesheet's "~"
    thread = front.split('<article class="row">')[1]
    assert thread.count('<ol class="minis minis-g g-1" aria-label="AI chat apps">') == 1
    assert thread.count('<ol class="minis minis-g g-2" aria-label="Help-desk bots">') == 1 and thread.count('<ol class="minis minis-human">') == 1
    parts = re.split(r'<ol class="minis[^>]*>', thread)[1:]
    assert [part.count('class="mini-text"') for part in parts] == [3, 8, 1]              # the Human's answer once, whatever group is chosen
    assert front.count('class="mini-text"') == 12 * len(IDS)
    # Each group's colours start again at its first place: position in the THREAD, not in the whole list.
    assert [who for who, _s, _m in looks(parts[0], 'mini')] == ['who-1', 'who-2', 'who-3']
    assert [who for who, _s, _m in looks(parts[1], 'mini')] == [f'who-{n}' for n in range(1, 9)]
    assert len(set(looks(parts[1], 'mini'))) == 8
    # The question page: one thread per group under its name, and the Human in a thread of its own.
    newest = page.split('<section class="panel"')[1]
    assert re.findall(r'<h3 class="thread-name">([^<]+)</h3>', newest) == ['AI chat apps', 'Help-desk bots', 'Human']
    panes = newest.split('<h3 class="thread-name">')[1:]
    assert [pane.count('<article class="acard') for pane in panes] == [3, 8, 1]
    assert '<div class="cards n-3">' in panes[0] and '<div class="cards n-6 n-wide">' in panes[1] and '<div class="cards n-1">' in panes[2]
    assert page.count('<h1') == 1 and 'class="history history-tall"' in page and page.count('data-label="Sample bot K"') == 2
    # How it works: the apps under their group's name.
    assert re.findall(r'<h3 class="apps-name">([^<]+)</h3>', method) == ['AI chat apps', 'Help-desk bots', 'Human']
    assert method.count('<div class="app-row') == 12
    # The reviewed export accepts the groups of the checked-in configuration and refuses any other.
    assert chatbots.export_problem(block) is None
    renamed = json.loads(json.dumps(block))
    renamed['groups'][1]['name'] = 'Another name'
    assert 'group help-desk is not a group of' in chatbots.export_problem(renamed)
    moved = json.loads(json.dumps(block))
    moved['apps'][0]['group'] = 'help-desk'
    assert 'does not carry the name, maker, attribution and group' in chatbots.export_problem(moved)
    for edit, why in [(lambda b: b['groups'].pop(), 'its group is not one of the groups'),
                      (lambda b: b['groups'].append({'id': 'empty', 'name': 'Empty'}), 'group empty has no chat app'),
                      (lambda b: b['groups'][0].pop('name'), 'a group lacks its id or name'),
                      (lambda b: b['apps'][0].pop('group'), 'lacks its label, name, maker, attribution or group'),
                      (lambda b: b.pop('groups'), 'a key is missing')]:
        broken = json.loads(json.dumps(payload))
        edit(broken['chatbots'])
        assert why in archive.payload_problem(broken), why
    # A group's name is untrusted text like any other.
    hostile = json.loads(json.dumps(payload))
    hostile['chatbots']['groups'][1]['name'] = '<b onmouseover="x">&amp;</b>'
    for text in (archive_pages.render_index(hostile), archive_pages.render_question(hostile, 'C001'),
                 archive_pages.render_chatbots(hostile), archive_pages.render_chatbot_card(hostile, 'C001', 'help-desk')):
        assert_page_is_safe(text)
        assert '<b onmouseover' not in text and '&lt;b onmouseover=&quot;x&quot;&gt;&amp;amp;&lt;/b&gt;' in text


def test_a_group_added_later_starts_as_new_columns_from_its_first_month(chat, tmp_path, monkeypatch):
    set_apps(tmp_path, monkeypatch, bots(3))
    chatbots.import_collection(filled(tmp_path, '2026-10-10'), allow_old=True)
    before = rows('SELECT * FROM archive_chatbot_answers ORDER BY id') + rows('SELECT * FROM archive_chatbot_questions ORDER BY id')
    single = archive_pages.render_question(archive.public_payload(), 'C001')
    assert 'thread-name' not in single
    # The configuration gains a group and two apps; the recorded questions are untouched and still accepted.
    set_apps(tmp_path, monkeypatch, bots(3) + bots(2, 'help-desk', first=3), [AI, DESK])
    done = chatbots.import_collection(filled(tmp_path, '2026-11-14'))
    assert done['new_questions'] == [] and [c['chatbot'] for c in done['chatbots']] == [f'bot-{c}' for c in 'abcde']
    after = rows('SELECT * FROM archive_chatbot_answers ORDER BY id') + rows('SELECT * FROM archive_chatbot_questions ORDER BY id')
    assert after[:12] == before[:12] and after[-4:] == before[-4:]                      # every old row as it was
    payload = archive.public_payload()
    assert payload['chatbots']['groups'] == [AI, DESK] and archive.payload_problem(payload) is None
    page = archive_pages.render_question(payload, 'C001')
    newest, older = page.split('<section class="panel"')[1:3]
    assert re.findall(r'<h3 class="thread-name">([^<]+)</h3>', newest) == ['AI chat apps', 'Help-desk bots', 'Human']
    assert re.findall(r'<h3 class="thread-name">([^<]+)</h3>', older) == ['AI chat apps', 'Human']       # not asked then: no empty thread
    assert older.count('<article class="acard') == 4 and newest.count('<article class="acard') == 6
    assert page.count('>not asked</td>') == 2                                             # the table says so, once per new column
    assert 'aria-label="Sample bot D: no change; 1 check"' in archive_pages.render_index(payload)


# ---- share cards: at most four bubbles, more answerers on further cards ------------------------------------------

def card_names(pages, question='C001'):
    return sorted(name[len(f'card/{question}'):-5] for name in pages if name.startswith(f'card/{question}') and name.endswith('.html'))


def test_cards_hold_at_most_four_bubbles_and_paginate_instead_of_shrinking(chat, tmp_path, monkeypatch):
    payload = payload_for(tmp_path, monkeypatch, bots(8))
    pages = archive_pages.render_site(payload)
    singles = [f'-bot-{c}' for c in 'abcdefgh'] + ['-human']
    assert card_names(pages) == sorted(['', '-2', '-3'] + singles)
    first, second, third = (pages[f'card/C001{suffix}.html'] for suffix in ('', '-2', '-3'))
    labels = [re.findall(r'<span class="card-label">([^<]+)</span>', card) for card in (first, second, third)]
    assert labels == [['Sample bot A', 'Sample bot B', 'Sample bot C', 'Human'], [f'Sample bot {c}' for c in 'DEFG'], ['Sample bot H']]
    for number, card in enumerate((first, second, third), 1):
        assert_page_is_safe(card)
        assert card.count('<section class="card-panel') <= archive_pages.CARD_APPS == 4 and card.count('<h1') == 1
        assert f'read-only · no payments · card {number} of 3' in card and '<main id="main" class="card card-safe' in card
        assert '<p class="card-honest mono">AI-generated · not affiliated · may be wrong</p>' in card
    assert archive_pages.render_card(payload, 'C001') == first == archive_pages.render_chatbot_card(payload, 'C001')
    # The same bubble classes as a card of three or four: the answers keep their size, whatever the number of answerers.
    assert '<div class="card-panels n-4">' in first and '<div class="card-panels n-4">' in second and '<div class="card-panels n-1">' in third
    css = (STATIC / 'archive.css').read_text(encoding='utf-8')
    fitted = css.split('@media (min-width: 540px) and (min-height: 960px) {', 1)[1]
    assert '.card-answer { font-size: 3.1cqw;' in fitted and '.n-3 .card-answer { font-size: 3.5cqw; }' in fitted
    assert not re.findall(r'\.n-[5-9] ', fitted)                                           # there is no smaller size for more bubbles
    # A single answerer's card is what it was.
    one = pages['card/C001-bot-h.html']
    assert one.count('<section class="card-panel') == 1 and '<div class="card-panels one">' in one and 'card 1 of' not in one
    # The question page links every card.
    page = pages['c/C001.html']
    assert ('<a href="../card/C001.html">All answers</a><a href="../card/C001-2.html">All answers 2</a><a href="../card/C001-3.html">All answers 3</a>'
            '<a href="../card/C001-bot-a.html">Sample bot A</a>') in page
    for missing in ('4', '0', 'bot-z', 'ai-apps', ''):
        with pytest.raises(KeyError):
            archive_pages.render_chatbot_card(payload, 'C001', missing)
    # Four answers in all fit one card: no second card, no "card 1 of".
    assert archive_pages.CARD_NAME.fullmatch('C001-2').groups() == ('C001', '2') and not archive_pages.CARD_NAME.fullmatch('C001-0')
    from live_genesis.app import app as combined
    with TestClient(combined) as client:
        assert client.get('/archive/card/C001-2.html').status_code == 200 and 'card 2 of 3' in client.get('/archive/card/C001-2.html').text
        assert client.get('/archive/card/C001-4.html').status_code == 404 and client.get('/archive/card/C001-bot-z.html').status_code == 404


def test_with_several_groups_each_group_has_its_own_cards(chat, tmp_path, monkeypatch):
    payload = two_groups(tmp_path, monkeypatch)
    pages = archive_pages.render_site(payload)
    singles = [f'-bot-{c}' for c in 'abcdefghijk'] + ['-human']
    assert card_names(pages) == sorted(['', '-help-desk', '-help-desk-2', '-help-desk-3'] + singles)
    labels = {suffix: re.findall(r'<span class="card-label">([^<]+)</span>', pages[f'card/C001{suffix}.html'])
              for suffix in ('', '-help-desk', '-help-desk-2', '-help-desk-3')}
    assert labels == {'': ['Sample bot A', 'Sample bot B', 'Sample bot C', 'Human'],
                      '-help-desk': ['Sample bot D', 'Sample bot E', 'Sample bot F', 'Human'],       # the Human on each group's first card
                      '-help-desk-2': [f'Sample bot {c}' for c in 'GHIJ'], '-help-desk-3': ['Sample bot K']}
    # The group is named inside the safe box (with the hook), and the part in the small print.
    assert '<p class="card-hook">Help-desk bots · Hot dog: sandwich or not?</p>' in pages['card/C001-help-desk-2.html']
    assert '<p class="card-hook">AI chat apps · Hot dog: sandwich or not?</p>' in pages['card/C001.html']
    assert 'card 2 of 3' in pages['card/C001-help-desk-2.html'] and 'card 1 of' not in pages['card/C001.html']
    assert '<p class="card-hook">Hot dog: sandwich or not?</p>' in pages['card/C001-bot-e.html']                # one answerer: as before
    page = pages['c/C001.html']
    assert ('<a href="../card/C001.html">AI chat apps</a><a href="../card/C001-help-desk.html">Help-desk bots</a>'
            '<a href="../card/C001-help-desk-2.html">Help-desk bots 2</a>') in page
    with pytest.raises(KeyError):
        archive_pages.render_chatbot_card(payload, 'C001', 'help-desk-4')
    with pytest.raises(KeyError):
        archive_pages.render_chatbot_card(payload, 'C001', '2')                             # the first group has one card
    # The capture script takes every one of these names.
    script = (REPO / 'scripts' / 'capture-cards.mjs').read_text(encoding='utf-8')
    pattern = re.compile(re.search(r'const ID = /(.+?)/;', script).group(1))
    assert all(pattern.fullmatch(f'C001{suffix}') for suffix in card_names(pages))


def test_the_earlier_answer_is_on_a_card_of_several_only_when_there_is_surely_room(chat, tmp_path, monkeypatch):
    short = payload_for(tmp_path, monkeypatch, bots(3))
    card = archive_pages.render_chatbot_card(short, 'C001')
    # Short answers: both changed apps and the Human show what they said before, directly above the new bubble.
    assert card.count('class="card-before"') == 3 and card.count('card-panel-changed') == 3
    panel = card.split('<section class="card-panel')[1]
    assert panel.index('<p class="card-before">') < panel.index('<div class="bubble">') and 'Before · Oct 2026</span> PLACEHOLDER first bot-a C001.' in panel
    view = archive_pages._View(short)
    board = archive_pages._chat_board(view, 'C001')
    panels = archive_pages._chat_cards(board)[''][0]
    assert archive_pages._card_room(board, panels) is True
    # Long answers: the estimate says the bubbles may not all fit with it, so the card says "changed" in words only.
    for _column, _period, cell in panels:
        cell['text'] = ('PLACEHOLDER words. ' * 7).strip()
    assert archive_pages._card_room(board, panels) is False
    assert archive_pages._lines('x' * 300, 3.1, 3) == 3 and archive_pages._lines('', 3.1, 3) == 1


# ---- contrast, measured from the stylesheet's own tokens ------------------------------------------------------------

def _luminance(colour):
    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    red, green, blue = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def test_measured_contrast_of_every_bubble_tint_name_and_mark():
    css = (STATIC / 'archive.css').read_text(encoding='utf-8')
    tokens = dict(re.findall(r'--([a-z0-9-]+): (#[0-9a-f]{6});', css.split(':root {', 1)[1].split('}', 1)[0]))

    def ratio(front, back):
        high, low = sorted((_luminance(tokens[front]), _luminance(tokens[back])), reverse=True)
        return (high + 0.05) / (low + 0.05)
    pairs = []
    for n in range(1, archive_pages.PALETTE + 1):
        pairs += [(f'w{n}', f'w{n}-t'),            # the name on its bubble, and the letter of a tint-filled mark (.av-c)
                  ('ink', f'w{n}-t'),              # the answer
                  ('ink-soft', f'w{n}-t'),         # attribution line, "History", the date on a card
                  ('on-dark', f'w{n}')]            # the letter on the mark
    pairs += [('on-dark', 'ink'), ('on-dark-soft', 'ink'),                                   # the Human's bubble: text, name
              ('ink', 'pane'), ('ink', 'ground'), ('ink', 'chip'), ('ink', 'pane-soft'),
              ('muted', 'pane'), ('muted', 'ground'), ('muted', 'pane-soft'), ('muted', 'chip'),     # "Before" text, small print, hooks
              ('ink-soft', 'pane'), ('ink-soft', 'ground'), ('accent', 'pane'), ('accent', 'ground')]
    for front, back in pairs:
        assert ratio(front, back) >= 4.5, (front, back, round(ratio(front, back), 2))
    assert ratio('dash', 'pane') >= 3 and ratio('muted', 'pane') >= 3                         # edges that carry meaning: dashed bubble, squares
    # The eight tints differ from each other and none is the pane or the Human's ink.
    tints = [tokens[f'w{n}-t'] for n in range(1, 9)] + [tokens[f'w{n}'] for n in range(1, 9)]
    assert len(set(tints)) == 16 and not set(tints) & {tokens['pane'], tokens['ink'], tokens['ground']}


# ---- the scripted demo stays at one group ----------------------------------------------------------------------------

def test_the_demo_keeps_one_group_and_shows_no_group_furniture(ready):
    archive.demo_archive()
    payload = archive.public_payload()
    assert payload['chatbots']['groups'] == [chatbots.DEFAULT_GROUP] and {a['group'] for a in payload['chatbots']['apps']} == {'ai-apps'}
    pages = archive_pages.render_site(payload, standalone=True)
    for name, page in pages.items():
        if name.endswith('.html'):
            for gone in ('g-radio', 'group-strip', 'thread-name', 'apps-name', 'minis-g', 'AI chat apps', 'Help-desk'):
                assert gone not in page, (name, gone)
    assert card_names(pages, 'C002') == ['', '-chat-app-a', '-chat-app-b', '-chat-app-c', '-human']
    front = pages['index.html']
    assert looks(front.split('<article class="row">')[1], 'mini') == [('who-1', '', 'A'), ('who-2', '', 'B'), ('who-3', '', 'C'), ('kind-human', '', 'H')]
    # The sample card of the roast shows both earlier answers above the new ones.
    assert pages['card/C002.html'].count('class="card-before"') == 2


# ---- upgrade: a database made by the version before groups ---------------------------------------------------------------

def test_a_database_made_before_groups_upgrades_in_place_and_keeps_every_row(chat, tmp_path, monkeypatch):
    old = V6_SCHEMA.read_text(encoding='utf-8')
    assert 'group_id TEXT' not in old and old.count('CREATE TABLE IF NOT EXISTS') == 6 and 'archive_human_answers' in old
    # A database as the version before groups made it: model answers from a recorded run, and one hand-entered chat-app run.
    archive.lock()
    as_real(monkeypatch, 'Seven.')
    archive.run_archive(max_usd=1)
    with core.connect() as cx:
        for name, kind in cx.execute("SELECT name,type FROM sqlite_master WHERE (name LIKE 'archive_chatbot_%' OR name LIKE 'archive_human_%') "
                                     "AND type IN ('trigger','index') AND name NOT LIKE 'sqlite_%'").fetchall():
            cx.execute(f'DROP {kind} {name}')
        for table in reversed(chatbots.TABLE_NAMES):
            cx.execute(f'DROP TABLE {table}')
        cx.executescript(old)
        assert 'group_id' not in {r[1] for r in cx.execute('PRAGMA table_info(archive_chatbot_conditions)')}
        stamp, prompt = '2026-10-10T10:00:00+00:00', f'{PREFIX}\n\nIs a hot dog a sandwich?'
        item = {'id': 'C001', 'text': 'Is a hot dog a sandwich?', 'prompt': prompt, 'hook': 'Hot dog: sandwich or not?', 'added': '2026-11-14'}
        cx.execute("INSERT INTO archive_chatbot_runs(number,kind,collected_on,imported_at,question_ids,config_sha256,file_sha256) "
                   "VALUES (1,'manual','2026-10-10',?,'[\"C001\"]',?,?)", (stamp, 'a' * 64, 'b' * 64))
        cx.execute("INSERT INTO archive_chatbot_questions(id,position,kind,text,prompt,hook,added,sha256,first_run) VALUES (?,?,?,?,?,?,?,?,1)",
                   ('C001', 1, 'manual', item['text'], prompt, item['hook'], item['added'], chatbots.question_hash(item)))
        for label, name, maker in (('chatgpt', 'ChatGPT', 'OpenAI'), ('gemini', 'Gemini', 'Google')):
            cx.execute("INSERT INTO archive_chatbot_conditions(run_id,chatbot,display_name,maker,attribution,account,fresh_chat,temporary_chat,"
                       "memory_off,web_search,country,app,shown_model,note) VALUES (1,?,?,?,'','free_account',1,1,'true','off','US','web','x','')",
                       (label, name, maker))
            cx.execute("INSERT INTO archive_chatbot_answers(run_id,question_set,question_id,chatbot,source,state,answer_text,normalised,"
                       "answer_sha256,created_at,refused) VALUES (1,'chat','C001',?,'manual','answered',?,?,?,?,0)",
                       (label, f'PLACEHOLDER old {label}.', f'placeholder old {label}', 'c' * 64, stamp))
        cx.execute("INSERT INTO archive_human_collections(run_id,display_name,meta,source,collected_on,replies) "
                   "VALUES (1,'Human','written by people · collected by hand','PLACEHOLDER thread','2026-10-10',3)")
        cx.execute("INSERT INTO archive_human_answers(run_id,question_id,source,state,answer_text,normalised,answer_sha256,manner,created_at) "
                   "VALUES (1,'C001','manual','answered','PLACEHOLDER old human.','placeholder old human','d' * 64,'quoted',?)", (stamp,))
        tables = ('archive_answers', 'archive_runs', 'archive_run_sets', 'archive_questions', 'archive_sets', 'operator_audit') + chatbots.TABLE_NAMES
        before = {t: [tuple(r) for r in cx.execute(f'SELECT * FROM {t} ORDER BY rowid')] for t in tables}
    assert len(before['archive_answers']) == 40 and len(before['archive_chatbot_answers']) == 2 and len(before['archive_human_answers']) == 1
    core.init_db()
    core.init_db()                                                   # any number of times
    with core.connect() as cx:
        columns = [r[1] for r in cx.execute('PRAGMA table_info(archive_chatbot_conditions)')]
        assert columns[columns.index('note') + 1:][:3] == ['group_id', 'group_name', 'kind']
        gained = {'archive_chatbot_conditions': ('', '', 'app') + (None,) * 8, 'archive_chatbot_answers': (None,)}
        for table, expected in before.items():
            now = [tuple(r) for r in cx.execute(f'SELECT * FROM {table} ORDER BY rowid')]
            # Every row is kept, value for value; the conditions gain the two group columns, empty, and what came with model
            # answerers (the kind 'app' and eight NULL columns), the answers a NULL reservation.
            assert now == [row + gained.get(table, ()) for row in expected], table
        import sqlite3
        fresh = sqlite3.connect(':memory:')
        fresh.executescript('CREATE TABLE archive_runs(kind TEXT);' + chatbots.TABLES)
        for table in chatbots.TABLE_NAMES:                           # and a new database has exactly the upgraded one's columns
            assert [r[1] for r in fresh.execute(f'PRAGMA table_info({table})')] == [r[1] for r in cx.execute(f'PRAGMA table_info({table})')], table
        with pytest.raises(sqlite3.IntegrityError, match='append-only'):
            cx.execute("UPDATE archive_chatbot_conditions SET group_id='x'")
    # The old rows read as the default group; the model answers are all still there.
    payload = archive.public_payload()
    block = payload['chatbots']
    assert archive.payload_problem(payload) is None and payload['schema'] == 'live-genesis-archive/6' and len(payload['answers']) == 40
    assert block['groups'] == [chatbots.DEFAULT_GROUP] and [app['group'] for app in block['apps']] == ['ai-apps', 'ai-apps']
    page = archive_pages.render_question(payload, 'C001')
    assert 'PLACEHOLDER old chatgpt.' in page and 'PLACEHOLDER old human.' in page and 'thread-name' not in page
    assert 'Seven.' in archive_pages.render_question(payload, 'Q01')
    # The upgraded database takes a new import under a configuration with a second group, on a later date.
    def edit(data):
        data['groups'] = [AI, DESK]
        data['chatbots'] = data['chatbots'][:2] + bots(2, 'help-desk')
        data['questions'] = data['questions'][:1]
    config_file(tmp_path, monkeypatch, edit=edit)
    done = chatbots.import_collection(filled(tmp_path, '2026-11-14'))
    assert done['run'] == 2 and done['new_questions'] == [] and done['first_import'] is False
    block = archive.public_payload()['chatbots']
    assert block['groups'] == [AI, DESK] and [app['group'] for app in block['apps']] == ['ai-apps', 'ai-apps', 'help-desk', 'help-desk']
    assert [a['changed'] for a in block['answers'] if a['chatbot'] == 'chatgpt'] == [None, True]      # compared with the old row
    page = archive_pages.render_question(archive.public_payload(), 'C001')
    assert '<span class="before-when mono">Before · <time datetime="2026-10-10">Oct 2026</time></span>PLACEHOLDER old chatgpt.</p>' in page


def test_docs_describe_groups_the_look_and_the_card_names():
    doc = (REPO / 'docs' / 'ANSWERS-OVER-TIME.md').read_text(encoding='utf-8')
    section = doc.split('## The chat-app track', 1)[1].split('\n## ', 1)[0]
    for phrase in ('live-genesis-chatbots/3', '"groups"', '"group"', 'ai-apps', 'Group chat', 'card/C001-2.html', 'at most four bubbles',
                   'live-genesis-chat-archive/2', 'group_id', 'Back up first', 'Every group is asked the same questions', 'by hand'):
        assert phrase in section, phrase
    plan = (REPO / 'docs' / 'PLAN-CHATBOT-COMPARISON.md').read_text(encoding='utf-8')
    assert 'Group chat' in plan and 'groups' in plan
    assert 'Group chat' in (REPO / 'docs' / 'ROADMAP.md').read_text(encoding='utf-8')
    # No document, test or source names a real airline or phone company as a future group: such names are the owner's to add.
    note = json.loads((REPO / 'live_genesis' / 'archive_chatbots.json').read_text(encoding='utf-8'))['note']
    assert 'groups' in note and 'same questions' in note
