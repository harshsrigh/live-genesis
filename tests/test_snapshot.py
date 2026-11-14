"""Static observatory export. Offline; reads the database read-only."""
import hashlib
import json
import re

import pytest

from live_genesis import agentkit, cli, snapshot
from live_genesis.core import authenticate, seed_agents, submit_action


def demo_world(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    keys = seed_agents()
    cli.demo(); cli.demo_game()
    return keys


def test_export_is_self_contained_and_labelled(isolated_db, tmp_path, monkeypatch):
    demo_world(tmp_path, monkeypatch)
    before = hashlib.sha256(isolated_db.read_bytes()).hexdigest()
    out = tmp_path / 'site'
    info = snapshot.build(out)
    assert info['demo_only'] is True
    assert hashlib.sha256(isolated_db.read_bytes()).hexdigest() == before  # database untouched
    data = json.loads((out / 'overview.json').read_text(encoding='utf-8'))
    assert data['mode'] == 'DEMO' and data['snapshot']['generated_at'] and data['games']
    assert all(p['demo'] == 1 for p in data['posts'])
    text = (out / 'overview.json').read_text(encoding='utf-8')
    assert 'key_hash' not in text and 'live_' not in text
    html = (out / 'index.html').read_text(encoding='utf-8')
    assert 'data-overview="overview.json"' in html
    assert html.count('http-equiv="Content-Security-Policy"') == 1 and "script-src 'self'" in html
    # Relative paths only, so it works under a sub-path such as /live-genesis/ .
    assert not re.search(r'(?:href|src)="/', html)
    for name in ('app.js', 'style.css', 'genesis-sigil.png'):
        assert (out / 'static' / name).is_file()
    assert not (out / 'static' / 'index.html').exists()
    assert (out / '.nojekyll').exists()
    # Asset addresses carry a content hash, so a changed file is never served from an old cache.
    refs = re.findall(r'"static/([^"?]+)\?v=([0-9a-f]{12})"', html)
    assert {name for name, _ in refs} >= {'app.js', 'style.css', 'genesis-sigil.png'}
    assert not re.search(r'"static/[^"?]+"', html)
    for name, digest in refs:
        assert hashlib.sha256((out / 'static' / name).read_bytes()).hexdigest().startswith(digest)
    script = (out / 'static' / 'app.js').read_text(encoding='utf-8')
    assert 'dataset.overview' in script and 'STATIC SNAPSHOT' in script and 'api/agent' not in script


def test_refuses_non_demo_data_unless_allowed(isolated_db, tmp_path, monkeypatch):
    keys = demo_world(tmp_path, monkeypatch)
    agent = authenticate(keys[0]['key'])
    submit_action(agent['id'], {'action': 'post', 'topic': 'research', 'title': 'Real action', 'body': 'Recorded through the API, not scripted.'})
    with pytest.raises(RuntimeError, match='not scripted-demo-only'):
        snapshot.build(tmp_path / 'site')
    assert not (tmp_path / 'site').exists()
    info = snapshot.build(tmp_path / 'site', allow_non_demo=True)
    assert info['demo_only'] is False
    assert json.loads((tmp_path / 'site' / 'overview.json').read_text(encoding='utf-8'))['mode'] == 'MIXED'


def test_refuses_empty_live_world_and_non_empty_folder(isolated_db, tmp_path, monkeypatch):
    seed_agents()
    with pytest.raises(RuntimeError):  # mode LIVE: nothing scripted to show
        snapshot.build(tmp_path / 'a')
    demo_world(tmp_path, monkeypatch)
    busy = tmp_path / 'busy'; busy.mkdir(); (busy / 'keep.txt').write_text('x')
    with pytest.raises(RuntimeError, match='empty'):
        snapshot.build(busy)
    assert (busy / 'keep.txt').read_text(encoding='utf-8') == 'x'


# ---- onboarding kit for agent developers (llms.txt, agent-guide.md, openapi.json) ---------

KEY_SHAPED = re.compile(r'live_[A-Za-z0-9_-]{20,}')
EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+')
KIT = ('llms.txt', 'agent-guide.md', 'openapi.json')
STATIC_PAGE = snapshot.STATIC / 'index.html'


def body_of(html):
    return html[html.index('<body'):]


def visible_text(html):
    return ' '.join(re.sub(r'<[^>]+>', ' ', body_of(html)).split())


def test_export_contains_the_agent_kit(isolated_db, tmp_path, monkeypatch):
    demo_world(tmp_path, monkeypatch)
    out = tmp_path / 'live-genesis'
    info = snapshot.build(out)
    assert info['api_base'] is None
    for name in KIT + ('agent_client.py', 'static/agents.txt'):
        assert (out / name).is_file() and (out / name).stat().st_size > 200, name
    assert (out / 'llms.txt').read_text(encoding='utf-8') == agentkit.llms_txt()
    assert (out / 'agent-guide.md').read_text(encoding='utf-8') == agentkit.agent_guide()
    assert (out / 'agent_client.py').read_bytes() == agentkit.REFERENCE_CLIENT.read_bytes()
    document = json.loads((out / 'openapi.json').read_text(encoding='utf-8'))
    assert document == agentkit.openapi_document() and '/api/agent/action' in document['paths']

    def refs(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == '$ref':
                    yield value
                else:
                    yield from refs(value)
        elif isinstance(node, list):
            for item in node:
                yield from refs(item)
    for ref in set(refs(document)):
        node = document
        for part in ref.removeprefix('#/').split('/'):
            node = node[part]
    # Every link in the two markdown files is relative and points at a file of the export,
    # so the kit works under a sub-path such as /live-genesis/ .
    for name in ('llms.txt', 'agent-guide.md'):
        targets = re.findall(r'\]\(([^)]+)\)', (out / name).read_text(encoding='utf-8'))
        assert targets, name
        for target in targets:
            assert not re.match(r'[a-z]+:|/', target) and (out / target).is_file(), (name, target)


def test_page_refers_to_the_kit_in_head_only_and_visible_text_is_unchanged(isolated_db, tmp_path, monkeypatch):
    demo_world(tmp_path, monkeypatch)
    out = tmp_path / 'site'
    snapshot.build(out)
    html = (out / 'index.html').read_text(encoding='utf-8')
    head = html[:html.index('</head>')]
    for link in snapshot.KIT_LINKS:
        assert head.count(link) == 1 and link not in body_of(html)
    for target in KIT:
        assert f'href="{target}"' in head and target not in body_of(html)
    assert 'rel="service-desc" type="application/vnd.oai.openapi+json" href="openapi.json"' in head
    assert 'href="static/agents.txt?v=' in head                      # the policy notice stays linked and versioned
    assert not re.search(r'(?:href|src)="/', html)
    # The policy is the same single meta tag: nothing was loosened for the new references.
    assert html.count('http-equiv="Content-Security-Policy"') == 1
    assert f'content="{snapshot.CONTENT_SECURITY_POLICY}"' in head
    assert "default-src 'none'" in snapshot.CONTENT_SECURITY_POLICY and 'unsafe-eval' not in snapshot.CONTENT_SECURITY_POLICY
    # Visible copy: byte-identical to the source page apart from the documented path rewrites.
    source = STATIC_PAGE.read_text(encoding='utf-8')
    exported = re.sub(r'"static/([^"?]+)\?v=[0-9a-f]{12}"', r'"/static/\1"', body_of(html)).replace('href="./"', 'href="/"')
    assert exported == body_of(source)
    assert visible_text(html) == visible_text(source)
    assert not re.search(r'human|agents[ -]only|agent-only|llms\.txt|openapi|agent-guide', visible_text(html), re.I)
    # The source page itself was not given the links: the live app serves no such files.
    assert 'llms.txt' not in source and 'openapi.json' not in source


def test_kit_states_no_address_no_key_and_no_enrolment(isolated_db, tmp_path, monkeypatch):
    demo_world(tmp_path, monkeypatch)
    out = tmp_path / 'site'
    snapshot.build(out)
    for name in KIT:
        text = (out / name).read_text(encoding='utf-8')
        assert agentkit.API_BASE_PLACEHOLDER in text and 'not publicly hosted yet' in text, name
        assert not re.search(r'https?://', text), name
        assert not re.search(r'sign[ -]?up|register here', text, re.I), name
        assert not EMAIL.search(text), name
    assert 'servers' not in json.loads((out / 'openapi.json').read_text(encoding='utf-8'))
    for path in out.rglob('*'):
        if path.is_file() and path.suffix in ('.txt', '.md', '.json', '.py', '.html', '.js'):
            assert not KEY_SHAPED.search(path.read_text(encoding='utf-8')), path.name
    for name in ('llms.txt', 'agent-guide.md'):
        text = (out / name).read_text(encoding='utf-8')
        assert 'issued manually by the operator' in text and 'offers no way to obtain a key' in text, name
        assert 'Operator contact' not in text, name


def test_api_base_and_operator_contact_appear_only_when_given(isolated_db, tmp_path, monkeypatch):
    demo_world(tmp_path, monkeypatch)
    for bad in ('api.example.org', 'https://x.example/?a=b', 'https://x.example/"'):
        with pytest.raises(RuntimeError, match='--api-base'):
            snapshot.build(tmp_path / 'bad', api_base=bad)
        assert not (tmp_path / 'bad').exists()
    monkeypatch.setattr(agentkit, 'OPERATOR_CONTACT', 'operator@example.org')
    out = tmp_path / 'hosted'
    monkeypatch.setattr('sys.argv', ['snapshot', str(out), '--api-base', 'https://api.example.org/'])
    snapshot.main()
    for name in KIT:
        text = (out / name).read_text(encoding='utf-8')
        assert 'https://api.example.org' in text and agentkit.API_BASE_PLACEHOLDER not in text, name
        assert 'not publicly hosted' not in text, name
    assert json.loads((out / 'openapi.json').read_text(encoding='utf-8'))['servers'][0]['url'] == 'https://api.example.org'
    for name in ('llms.txt', 'agent-guide.md'):
        assert 'Operator contact: operator@example.org' in (out / name).read_text(encoding='utf-8')
    assert 'operator@example.org' not in (out / 'index.html').read_text(encoding='utf-8')
    assert visible_text((out / 'index.html').read_text(encoding='utf-8')) == visible_text(STATIC_PAGE.read_text(encoding='utf-8'))


# ---- --archive-only: the public site is Answers Over Time alone (owner decision, 13 Nov 2026) ----

SOCIETY_FILES = ('overview.json', 'stats.html', 'llms.txt', 'agent-guide.md', 'openapi.json', 'agent_client.py',
                 'static/app.js', 'static/time.js', 'static/style.css', 'static/genesis-sigil.png', 'static/agents.txt',
                 'static/fonts/dm-sans.ttf', 'static/fonts/space-grotesk.ttf')
ARCHIVE_STATIC = {'static/archive.css', 'static/archive-mark.svg', 'static/archive-icon.svg',
                  'static/fonts/bricolage-grotesque.woff2', 'static/fonts/bricolage-grotesque-OFL.txt',
                  'static/fonts/inter.woff2', 'static/fonts/inter-OFL.txt',
                  'static/fonts/jetbrains-mono.woff2', 'static/fonts/jetbrains-mono-OFL.txt'}
OBSERVATORY_LINK = re.compile(r' *<a href="(?:\.\./)+">Observatory</a>\n')
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
    from live_genesis import archive
    keys = demo_world(tmp_path, monkeypatch)
    cli.demo_document()
    archive.demo_archive()
    return keys


def run_main(monkeypatch, *argv):
    monkeypatch.setattr('sys.argv', ['snapshot', *argv])
    try:
        snapshot.main()
        return 0
    except SystemExit as stop:
        return stop.code


def test_archive_only_writes_the_archive_and_nothing_of_the_society(isolated_db, tmp_path, monkeypatch):
    from live_genesis import archive, archive_pages
    archive_demo_world(tmp_path, monkeypatch)
    before = hashlib.sha256(isolated_db.read_bytes()).hexdigest()
    out = tmp_path / 'site'
    info = snapshot.build(out, archive_only=True)
    assert info['demo_only'] is True and info['api_base'] is None
    assert hashlib.sha256(isolated_db.read_bytes()).hexdigest() == before  # database untouched
    payload = archive.public_payload(readonly=True)
    ids = [q['id'] for q in payload['questions']] + [q['id'] for q in payload['trending']['questions']]
    assert payload['questions'] and payload['trending']['questions']
    chat = payload['chatbots']
    ids += [f'{question}-{suffix}' for question in chat['questions'] for suffix in ['chatbots'] + [app['label'] for app in chat['apps']]]
    expected = ({'index.html', '.nojekyll', 'archive.json', 'archive/index.html', 'archive/trending.html', 'archive/chatbots.html'}
                | ARCHIVE_STATIC
                | {f'archive/q/{q["id"]}.html' for q in payload['questions']}
                | {f'archive/t/{q["id"]}.html' for q in payload['trending']['questions']}
                | {f'archive/card/{name}.html' for name in ids})
    written = files_of(out)
    assert written == expected
    # Nothing of the society: no data file, no page, no script, no agent kit, no emblem.
    for name in SOCIETY_FILES:
        assert name not in written, name
    assert not any(name.startswith(('post/', 'profile/')) or name.endswith(('.js', '.py', '.md')) for name in written)
    assert [name for name in written if name.endswith('.json')] == ['archive.json']
    bodies = [post['body'] for post in snapshot._capture()['posts']]
    assert len(bodies) > 3 and all(len(body) > 20 for body in bodies)
    everything = ''.join((out / name).read_text(encoding='utf-8') for name in written if name.endswith(('.html', '.json')))
    for body in bodies:
        assert body not in everything and json.dumps(body)[1:-1] not in everything
    assert '<script' not in everything and 'overview.json' not in everything and 'app.js' not in everything
    # The archive data is the public payload, exactly as the default mode writes it.
    assert (out / 'archive.json').read_bytes() == snapshot._encode(payload)
    # No dead link, and no link to the observatory at all.
    assert_every_address_resolves(out)
    pages = {name: (out / name).read_text(encoding='utf-8') for name in written if name.endswith('.html')}
    for name, html in pages.items():
        assert 'Observatory' not in html and 'href="../"' not in html and 'href="../../"' not in html, name
        assert html.count('http-equiv="Content-Security-Policy"') == 1 and f'content="{archive_pages.esc(snapshot.CONTENT_SECURITY_POLICY)}"' in html, name
        # Owner decision on public copy: nothing visible states the agents-only rule.
        assert not RULE_WORDING.search(visible_text(html)), name
    # The honesty labels stay.
    front = pages['archive/index.html']
    assert 'Scripted demo answers and dates · no model was called' in front and 'READ-ONLY ARCHIVE' in front
    assert 'Sample answers on this page are scripted, not model output.' in front and 'No payments' in front
    assert 'scripted, not model output' in pages['archive/q/Q01.html'] and 'scripted demo' in pages['archive/q/Q01.html']
    # Apart from that one bar link, each page is the page every other snapshot and both web apps show.
    usual = archive_pages.render_site(payload)
    assert {'archive/' + name for name in usual} == set(pages) - {'index.html'}
    for name, html in usual.items():
        assert OBSERVATORY_LINK.sub('', html) == pages['archive/' + name], name
        assert len(OBSERVATORY_LINK.findall(html)) == (0 if name.startswith('card/') else 1), name


def test_archive_only_root_page_leads_to_the_archive_without_script(isolated_db, tmp_path, monkeypatch):
    archive_demo_world(tmp_path, monkeypatch)
    out = tmp_path / 'site'
    snapshot.build(out, archive_only=True)
    home = (out / 'index.html').read_text(encoding='utf-8')
    assert home.count('<meta http-equiv="refresh" content="0; url=archive/trending.html" />') == 1
    assert home.index('http-equiv="Content-Security-Policy"') < home.index('http-equiv="refresh"') < home.index('</head>')
    assert body_of(home).count('<a href="archive/trending.html">') == 1          # for a browser that does not follow the refresh
    assert '<script' not in home and 'data-overview' not in home and 'onload' not in home
    assert sorted(set(addresses(home))) == sorted({'archive/trending.html', *re.findall(r'"(static/archive[^"]+)"', home)})
    assert 'Read-only archive · static snapshot' in home and 'Nothing is running behind this page.' in home
    assert not re.search(r'human|resident', visible_text(home), re.I) and not RULE_WORDING.search(visible_text(home))
    assert (out / 'archive' / 'index.html').is_file()                            # the existing archive address is unchanged


def test_default_export_is_unchanged_by_the_archive_only_mode(isolated_db, tmp_path, monkeypatch):
    from live_genesis import archive, archive_pages
    archive_demo_world(tmp_path, monkeypatch)
    monkeypatch.setattr(snapshot, 'utcnow', lambda: '2030-01-01T00:00:00+00:00')
    full, only = tmp_path / 'full', tmp_path / 'only'
    snapshot.build(full)
    snapshot.build(only, archive_only=True)
    written = files_of(full)
    for name in SOCIETY_FILES:
        assert name in written, name
    assert any(name.startswith('post/') for name in written) and any(name.startswith('profile/') for name in written)
    home = (full / 'index.html').read_text(encoding='utf-8')
    assert 'data-overview="overview.json"' in home and 'http-equiv="refresh"' not in home
    assert visible_text(home) == visible_text(STATIC_PAGE.read_text(encoding='utf-8'))
    assert files_of(only) < written                                                 # the mode only ever leaves files out
    assert {name for name in written if name.startswith('static/')} == {
        'static/' + path.relative_to(snapshot.STATIC).as_posix() for path in snapshot.STATIC.rglob('*')
        if path.is_file() and path.suffix != '.html'}
    # The archive pages of a default export still link the observatory and are the renderer's default output.
    payload = archive.public_payload(readonly=True)
    for name, html in archive_pages.render_site(payload).items():
        assert (full / 'archive' / name).read_text(encoding='utf-8') == html, name
    assert '<a href="../">Observatory</a>' in (full / 'archive' / 'index.html').read_text(encoding='utf-8')
    assert '<a href="../../">Observatory</a>' in (full / 'archive' / 'q' / 'Q01.html').read_text(encoding='utf-8')
    # A sibling changes nothing in the archive pages unless the archive stands alone.
    assert archive_pages.render_site(payload, sibling='demo/') == archive_pages.render_site(payload)
    # Files both modes write are the same bytes; only the root page and the bar of the archive pages differ.
    for name in files_of(only) - {'index.html'}:
        if not (name.startswith('archive/') and name.endswith('.html') and '/card/' not in name):
            assert (only / name).read_bytes() == (full / name).read_bytes(), name


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
    assert inner.count('<a href="../../archive/trending.html">Main archive</a>') == 1
    deep = (site / 'demo' / 'archive' / 'q' / 'Q01.html').read_text(encoding='utf-8')
    assert deep.count('<a href="../../../archive/trending.html">Main archive</a>') == 1
    assert '<a href="../demo/archive/trending.html">' in (site / 'archive' / 'trending.html').read_text(encoding='utf-8')
    assert 'Observatory' not in inner + deep and not re.search(r'>[^<]*recorded[^<]*</a>', inner + deep, re.I)


def test_archive_only_command_line(isolated_db, tmp_path, monkeypatch, capsys):
    keys = archive_demo_world(tmp_path, monkeypatch)
    out = tmp_path / 'site'
    assert run_main(monkeypatch, str(out), '--archive-only') == 0
    printed = capsys.readouterr().out
    assert 'scripted demo data only' in printed and 'Archive only' in printed and 'Agent kit' not in printed
    assert 'No model was called' in printed
    assert not (out / 'overview.json').exists() and (out / 'archive' / 'index.html').is_file()
    # The agent kit is not written in this mode, so an address for it is refused rather than dropped.
    assert run_main(monkeypatch, str(tmp_path / 'kit'), '--archive-only', '--api-base', 'https://api.example.org') == 2
    assert '--api-base cannot be used with --archive-only' in capsys.readouterr().err and not (tmp_path / 'kit').exists()
    assert run_main(monkeypatch, '--check', str(tmp_path / 'published'), '--archive-only') == 2
    capsys.readouterr()
    # The demo-only gate is the same as without the flag.
    agent = authenticate(keys[0]['key'])
    submit_action(agent['id'], {'action': 'post', 'topic': 'research', 'title': 'Real action', 'body': 'Recorded through the API, not scripted.'})
    assert run_main(monkeypatch, str(tmp_path / 'mixed'), '--archive-only') == 2 and not (tmp_path / 'mixed').exists()
    assert 'not scripted-demo-only' in capsys.readouterr().err
    assert run_main(monkeypatch, str(tmp_path / 'mixed'), '--archive-only', '--allow-non-demo') == 0
    assert 'Recorded through the API' not in ''.join(
        path.read_text(encoding='utf-8') for path in (tmp_path / 'mixed').rglob('*') if path.suffix in ('.html', '.json'))
