"""The static site: the chat-app comparison of Answers Over Time and nothing else. Offline; reads the database read-only."""
import hashlib
import json
import re

import pytest

from live_genesis import archive, archive_chatbots, archive_pages, snapshot, web


def body_of(html):
    return html[html.index('<body'):]


def visible_text(html):
    return ' '.join(re.sub(r'<[^>]+>', ' ', body_of(html)).split())


# ---- The public site is Answers Over Time alone (owner decision, 13 Nov 2026) ----
# What a site held while the agent society existed; none of it may come back.
SOCIETY_FILES = ('overview.json', 'stats.html', 'llms.txt', 'agent-guide.md', 'openapi.json', 'agent_client.py',
                 'static/app.js', 'static/time.js', 'static/style.css', 'static/genesis-sigil.png', 'static/agents.txt',
                 'static/fonts/dm-sans.ttf', 'static/fonts/space-grotesk.ttf')
ARCHIVE_STATIC = {'static/archive.css', 'static/archive-mark.svg', 'static/archive-icon.svg',
                  'static/fonts/bricolage-grotesque.woff2', 'static/fonts/bricolage-grotesque-OFL.txt',
                  'static/fonts/inter.woff2', 'static/fonts/inter-OFL.txt',
                  'static/fonts/jetbrains-mono.woff2', 'static/fonts/jetbrains-mono-OFL.txt'}
RULE_WORDING = re.compile(r'agents[ -]only|agent-only|no humans|humans (?:may|can)not|llms\.txt|openapi|agent-guide', re.I)


def files_of(root):
    return {path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_file()}


def addresses(html):
    """Every href, src and meta-refresh target of one page."""
    return re.findall(r'(?:href|src)="([^"]*)"', html) + re.findall(r'http-equiv="refresh" content="[^"]*url=([^"]+)"', html)


def assert_every_address_resolves(root):
    """No dead internal link: every address of every page is relative and names a file under root."""
    pages = sorted(root.rglob('*.html'))
    assert pages
    for page in pages:
        for url in addresses(page.read_text(encoding='utf-8')):
            assert url and not re.match(r'[a-z][a-z0-9+.-]*:|/', url), (page.name, url)
            target = (page.parent / url.split('#')[0].split('?')[0]).resolve()
            assert root.resolve() in target.parents, (page.name, url)
            assert target.is_file(), (page.relative_to(root).as_posix(), url)
    for sheet in root.rglob('*.css'):
        for url in re.findall(r'url\("([^"]+)"\)', sheet.read_text(encoding='utf-8')):
            assert (sheet.parent / url).is_file(), url


def archive_demo_world(tmp_path, monkeypatch):
    archive.demo_archive()


def run_main(monkeypatch, *argv):
    monkeypatch.setattr('sys.argv', ['snapshot', *argv])
    try:
        snapshot.main()
        return 0
    except SystemExit as stop:
        return stop.code


# Owner decision ("Remove anything from previous models"): the public site is the chat-app comparison only.
# Exactly what a standalone build of the scripted demo writes outside static/: listed by name, not derived.
CHAT_DEMO_IDS = ['C001', 'C002', 'C003', 'C004', 'C005', 'C006']
CHAT_DEMO_SITE = ({'index.html', '.nojekyll', 'archive.json', 'archive/index.html', 'archive/chatbots.html',
                   'archive/data/answers.jsonl', 'archive/data/README.txt'}
                  | {f'archive/c/{name}.html' for name in CHAT_DEMO_IDS} | {f'archive/data/{name}.json' for name in CHAT_DEMO_IDS}
                  | {f'archive/card/{name}{suffix}.html' for name in CHAT_DEMO_IDS
                     for suffix in ('', '-chat-app-a', '-chat-app-b', '-chat-app-c', '-human')})
MODEL_PAGES = {'archive/models.html', 'archive/trending.html', 'archive/q/Q01.html', 'archive/t/T001.html', 'archive/card/Q01.html',
               'archive/card/T001.html', 'archive/data/Q01.json', 'archive/data/T001.json'}
MODEL_KEYS = ('answers', 'answerers', 'questions', 'trending', 'runs', 'set_hash', 'question_set', 'groups', 'answers_by_source',
              'locked', 'locked_at', 'demo_only', 'system_line', 'changed_rule')
# Text that only the model track has: its page names and links, its labels, the scripted models and their versions.
MODEL_ONLY_TEXT = ('models.html', 'trending.html', '>Models<', '>Trending<', 'Want the plain models', 'straight to the models',
                   'Questions from what people search for', 'We asked it ourselves', 'asked-by-server', 'sent-in-unverified',
                   'model-a', 'model-b', 'model-c', 'Scripted sample, not a real trend', 'set_hash', 'Q01', 'T001')


def text_files(root, names):
    return {name: (root / name).read_text(encoding='utf-8') for name in names if name.endswith(('.html', '.json', '.jsonl', '.txt'))}


def assert_no_model_track(root):
    """Nothing of the model track in a built site: no page, card or data file, no link, no label, no answer."""
    written = files_of(root)
    assert not any(name.startswith(('archive/q/', 'archive/t/', 'archive/card/Q', 'archive/card/T', 'archive/data/Q', 'archive/data/T'))
                   or name in MODEL_PAGES for name in written), sorted(written)
    for name, text in text_files(root, written).items():
        for gone in MODEL_ONLY_TEXT:
            assert gone not in text, (name, gone)
        if name.endswith(('.html', '.txt')):
            # "model" is not a word a visitor reads anywhere (the one data key `shown_model` is a chat app's version name).
            assert not re.search(r'(?i)\bmodels?\b', visible_text(text) if name.endswith('.html') else text), name
            assert not re.search(r'(?i)trending', text), name
    data = json.loads((root / 'archive.json').read_text(encoding='utf-8'))
    assert list(data) == ['schema', 'chatbots'] and data['schema'] == 'live-genesis-chat-archive/2' and not set(data) & set(MODEL_KEYS)
    assert list(data['chatbots']) == ['groups', 'apps', 'human', 'questions', 'runs', 'answers', 'human_answers']
    return data


def test_archive_only_writes_the_chat_app_track_and_nothing_of_the_models_or_the_society(isolated_db, tmp_path, monkeypatch):
    archive_demo_world(tmp_path, monkeypatch)
    before = hashlib.sha256(isolated_db.read_bytes()).hexdigest()
    out = tmp_path / 'site'
    info = snapshot.build(out, archive_only=True)
    assert info['demo_only'] is True and info['generated_at']
    assert hashlib.sha256(isolated_db.read_bytes()).hexdigest() == before  # database untouched
    payload = archive.public_payload(readonly=True)
    # The database does hold the model track (514 scripted answers of three made-up models); none of it is published.
    assert payload['questions'] and payload['trending']['questions'] and len(payload['answers']) > 500
    assert payload['answerers'] == ['model-a', 'model-b', 'model-c']
    chat = payload['chatbots']
    assert [q['id'] for q in chat['questions']] == CHAT_DEMO_IDS and [app['label'] for app in chat['apps']] == ['chat-app-a', 'chat-app-b', 'chat-app-c']
    written = files_of(out)
    assert written == CHAT_DEMO_SITE | ARCHIVE_STATIC
    data = assert_no_model_track(out)
    everything_text = ''.join(text_files(out, written).values())
    for answer in payload['answers']:
        if answer['text'] and len(answer['text']) > 25:
            assert answer['text'] not in everything_text and json.dumps(answer['text'])[1:-1] not in everything_text, answer['id']
    for question in payload['questions'] + payload['trending']['questions']:
        assert question['text'] not in everything_text, question['id']
    # Nothing of the removed society: no data file, no page, no script, no agent kit, no emblem.
    for name in SOCIETY_FILES:
        assert name not in written, name
    assert not any(name.startswith(('post/', 'profile/')) or name.endswith(('.js', '.py', '.md')) for name in written)
    # The only data: the chat-app payload and the files derived from it for machines (archive/data/).
    assert [name for name in written if name.endswith('.json') and not name.startswith('archive/data/')] == ['archive.json']
    lines = [json.loads(line) for line in (out / 'archive' / 'data' / 'answers.jsonl').read_text(encoding='utf-8').splitlines()]
    assert lines and {line['answerer_kind'] for line in lines} == {'chat-app', 'human'}
    assert {line['question_id'] for line in lines} == set(CHAT_DEMO_IDS) and {line['provenance'] for line in lines} == {'scripted-sample'}
    readme = (out / 'archive' / 'data' / 'README.txt').read_text(encoding='utf-8')
    assert readme == archive_pages.README_CHAT != archive_pages.README and 'scripted-sample' in readme and 'typed-by-hand' not in readme and 'by hand' not in readme
    assert 'chat-app | human' in readme and 'not affiliated' in readme and 'It may be wrong.' in readme
    everything = ''.join((out / name).read_text(encoding='utf-8') for name in written if name.endswith(('.html', '.json')))
    assert '<script' not in everything and 'overview.json' not in everything and 'app.js' not in everything
    # The archive data is the chat-app block of the public payload under its own schema marker, and nothing else.
    assert data == {'schema': archive_chatbots.SITE_SCHEMA, 'chatbots': json.loads(json.dumps(chat))}
    assert (out / 'archive.json').read_bytes() == snapshot._encode(archive_chatbots.site_payload(payload))
    # No dead link, and no link out of the archive to a page above it.
    assert_every_address_resolves(out)
    pages = {name: (out / name).read_text(encoding='utf-8') for name in written if name.endswith('.html')}
    for name, html in pages.items():
        assert 'Observatory' not in html and 'href="../"' not in html and 'href="../../"' not in html, name
        assert html.count('http-equiv="Content-Security-Policy"') == 1 and f'content="{archive_pages.esc(web.CONTENT_SECURITY_POLICY)}"' in html, name
        # Owner decision on public copy: nothing visible states the agents-only rule.
        assert not RULE_WORDING.search(visible_text(html)), name
    # The honesty labels stay.
    front = pages['archive/index.html']
    assert 'Sample we wrote, not a real AI answer' in front and 'Read-only archive.' in front
    assert 'Everything here is a sample we wrote. No AI and no person was asked.' in front and 'No payments' in front
    assert 'Everything here is a sample we wrote.' in pages['archive/c/C001.html'] and 'Sample we wrote, not a real AI answer' in pages['archive/c/C001.html']
    for name in ('archive/c/C001.html', 'archive/chatbots.html', 'archive/card/C001.html', 'archive/card/C001-human.html'):
        assert 'Sample we wrote, not a real AI answer' in pages[name], name
    assert 'Refusals and non-answers are left out.' in front and 'On this sample site the apps are made up' in pages['archive/chatbots.html']
    # The bar: the comparison and how it is made. No "Trending", no "Models".
    for name in ('archive/index.html', 'archive/chatbots.html', 'archive/c/C001.html'):
        nav = pages[name].split('<nav aria-label="Archive navigation">', 1)[1].split('</nav>', 1)[0]
        assert re.findall(r'>([^<>]+)</a>', nav) == ['Chat apps', 'How it works'], name
    # The pages are what render_site makes for a standalone archive, from the whole payload or from the reduced one.
    alone = archive_pages.render_site(payload, standalone=True)
    assert alone == archive_pages.render_site(archive_chatbots.site_payload(payload), standalone=True)
    assert {'archive/' + name for name in alone} == written - ARCHIVE_STATIC - {'index.html', '.nojekyll', 'archive.json'}
    for name, text in alone.items():
        assert text == (out / 'archive' / name).read_text(encoding='utf-8'), name
    # The local web app uses the default rendering, which keeps every model page.
    usual = archive_pages.render_site(payload)
    assert {name[len('archive/'):] for name in MODEL_PAGES} <= set(usual) and set(alone) < set(usual)
    assert '<a href="trending.html">Trending</a>' in usual['index.html'] and '<a href="models.html">Models</a>' in usual['index.html']
    assert 'Want the plain models instead?' in usual['index.html'] and usual['data/README.txt'] == archive_pages.README
    for name in alone:
        if name.startswith('card/') or (name.startswith('data/C') and name.endswith('.json')):
            assert usual[name] == alone[name], name                        # a chat-app card and data file are the same everywhere
        elif name.endswith('.html'):
            assert 'Observatory' not in usual[name] and 'Observatory' not in alone[name], name   # the link went with the page


def test_archive_only_root_page_leads_to_the_archive_without_script(isolated_db, tmp_path, monkeypatch):
    archive_demo_world(tmp_path, monkeypatch)
    out = tmp_path / 'site'
    snapshot.build(out, archive_only=True)
    home = (out / 'index.html').read_text(encoding='utf-8')
    assert home.count('<meta http-equiv="refresh" content="0; url=archive/index.html" />') == 1
    assert home.index('http-equiv="Content-Security-Policy"') < home.index('http-equiv="refresh"') < home.index('</head>')
    assert body_of(home).count('<a href="archive/index.html">') == 1             # for a browser that does not follow the refresh
    assert '<script' not in home and 'data-overview' not in home and 'onload' not in home
    assert sorted(set(addresses(home))) == sorted({'archive/index.html', *re.findall(r'"(static/archive[^"]+)"', home)})
    assert 'Read-only archive · static snapshot' in home and 'Nothing is running behind this page.' in home
    assert not re.search(r'human|resident', visible_text(home), re.I) and not RULE_WORDING.search(visible_text(home))
    assert (out / 'archive' / 'index.html').is_file()                            # the existing archive address is unchanged


def test_the_archive_only_flag_changes_nothing_and_the_default_rendering_keeps_the_model_pages(isolated_db, tmp_path, monkeypatch):
    archive_demo_world(tmp_path, monkeypatch)
    monkeypatch.setattr(snapshot, 'utcnow', lambda: '2030-01-01T00:00:00+00:00')
    plain, flagged = tmp_path / 'plain', tmp_path / 'flagged'
    assert snapshot.build(plain) == snapshot.build(flagged, archive_only=True)
    written = files_of(plain)
    assert written == files_of(flagged) == CHAT_DEMO_SITE | ARCHIVE_STATIC
    for name in written:
        assert (plain / name).read_bytes() == (flagged / name).read_bytes(), name
    for name in SOCIETY_FILES:
        assert name not in written, name
    assert_no_model_track(plain)
    # Every file under static/ is one the site uses: nothing of the removed observatory page is left there.
    assert {name for name in written if name.startswith('static/')} == ARCHIVE_STATIC == {
        'static/' + path.relative_to(snapshot.STATIC).as_posix() for path in snapshot.STATIC.rglob('*') if path.is_file()}
    # The renderer's default output (what the local web app serves) keeps every model page.
    payload = archive.public_payload(readonly=True)
    usual = archive_pages.render_site(payload)
    assert {name[len('archive/'):] for name in MODEL_PAGES} <= set(usual) and 'model-a' in usual['models.html']
    # A sibling changes nothing in the archive pages unless the archive stands alone.
    assert archive_pages.render_site(payload, sibling='demo/') == usual
    # Files the site and the default rendering both hold are the same, except the pages with a bar and the two data
    # files that hold the model track in the default rendering (the list of every answer, the data README).
    for name in written - {'index.html', 'archive.json', 'archive/data/answers.jsonl', 'archive/data/README.txt', '.nojekyll'} - ARCHIVE_STATIC:
        if not (name.endswith('.html') and '/card/' not in name):
            assert (plain / name).read_text(encoding='utf-8') == usual[name[len('archive/'):]], name


def test_archive_only_pair_links_each_other_as_the_workflow_builds_it(isolated_db, tmp_path, monkeypatch):
    archive_demo_world(tmp_path, monkeypatch)
    site = tmp_path / 'site'
    for bad in ('https://example.org/', '/demo/', '../../', 'demo/" onload="x'):
        with pytest.raises(RuntimeError, match='--sibling'):
            snapshot.build(tmp_path / 'bad', sibling=bad, archive_only=True)
        assert not (tmp_path / 'bad').exists()
    snapshot.build(site, sibling='demo/', archive_only=True)
    snapshot.build(site / 'demo', sibling='../', archive_only=True)
    assert_every_address_resolves(site)
    assert {'demo/' + name for name in files_of(site / 'demo')} == {name for name in files_of(site) if name.startswith('demo/')}
    assert files_of(site / 'demo') == {name for name in files_of(site) if not name.startswith('demo/')}
    # A scripted page calls the other snapshot the main archive, never "recorded".
    inner = (site / 'demo' / 'archive' / 'index.html').read_text(encoding='utf-8')
    assert inner.count('<a href="../../archive/index.html">Main archive</a>') == 1
    deep = (site / 'demo' / 'archive' / 'c' / 'C001.html').read_text(encoding='utf-8')
    assert deep.count('<a href="../../../archive/index.html">Main archive</a>') == 1
    assert '<a href="../demo/archive/index.html">' in (site / 'archive' / 'chatbots.html').read_text(encoding='utf-8')
    assert_no_model_track(site)
    assert_no_model_track(site / 'demo')
    assert 'Observatory' not in inner + deep and not re.search(r'>[^<]*recorded[^<]*</a>', inner + deep, re.I)


def test_archive_only_with_no_chat_app_run_says_so_and_never_lists_the_models(isolated_db, tmp_path, monkeypatch):
    out = tmp_path / 'site'                                                        # Answers Over Time is empty
    snapshot.build(out, archive_only=True)
    assert files_of(out) == ARCHIVE_STATIC | {'index.html', '.nojekyll', 'archive.json', 'archive/index.html', 'archive/chatbots.html',
                                              'archive/data/answers.jsonl', 'archive/data/README.txt'}
    data = assert_no_model_track(out)
    assert data['chatbots'] == {'groups': [], 'apps': [], 'human': None, 'questions': [], 'runs': [], 'answers': [], 'human_answers': []}
    assert_every_address_resolves(out)
    front = (out / 'archive' / 'index.html').read_text(encoding='utf-8')
    assert 'No answers recorded yet' in front and 'No chat app has been asked yet.' in front and '<a href="chatbots.html">Here is how we ask</a>' in front
    assert 'Read-only archive.' in front and 'No payments' in front and '<script' not in front and 'style="' not in front
    assert (out / 'archive' / 'data' / 'answers.jsonl').read_text(encoding='utf-8') == ''
    # The same empty payload in the local web app still shows the model lists: only the standalone archive leaves them out.
    assert 'Simple questions, asked again every month.' in archive_pages.render_index(archive.public_payload(readonly=True))


def test_archive_only_command_line(isolated_db, tmp_path, monkeypatch, capsys):
    archive_demo_world(tmp_path, monkeypatch)
    out = tmp_path / 'site'
    assert run_main(monkeypatch, str(out), '--archive-only') == 0
    printed = capsys.readouterr().out
    assert 'scripted demo data only' in printed and 'Agent kit' not in printed
    assert 'Archive only: the chat-app comparison of Answers Over Time and its static files. No model-track page or data' in printed
    assert 'No model was called' in printed
    assert not (out / 'overview.json').exists() and (out / 'archive' / 'index.html').is_file()
    # Without the flag: the same site (the flag is accepted and changes nothing).
    assert run_main(monkeypatch, str(tmp_path / 'plain')) == 0
    assert 'Archive only: the chat-app comparison' in capsys.readouterr().out and files_of(tmp_path / 'plain') == files_of(out)
    # The agent kit went with the society, and so did its option.
    assert run_main(monkeypatch, str(tmp_path / 'kit'), '--archive-only', '--api-base', 'https://api.example.org') == 2
    assert 'unrecognized arguments' in capsys.readouterr().err and not (tmp_path / 'kit').exists()
    assert run_main(monkeypatch, '--check', str(tmp_path / 'published'), '--archive-only') == 2
    capsys.readouterr()
    # The demo-only gate: an archive that holds anything recorded is built only on the operator's explicit word.
    recorded = {**archive.public_payload(readonly=True), 'demo_only': False}
    monkeypatch.setattr(snapshot, '_capture_archive', lambda: recorded)
    assert run_main(monkeypatch, str(tmp_path / 'mixed'), '--archive-only') == 2 and not (tmp_path / 'mixed').exists()
    assert 'not scripted-demo-only' in capsys.readouterr().err
    assert run_main(monkeypatch, str(tmp_path / 'mixed'), '--archive-only', '--allow-non-demo') == 0
    assert 'INCLUDES NON-DEMO DATA' in capsys.readouterr().out
