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
