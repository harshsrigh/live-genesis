"""Static observatory export. Offline; reads the database read-only."""
import hashlib
import json
import re

import pytest

from live_genesis import cli, snapshot
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
    data = json.loads((out / 'overview.json').read_text())
    assert data['mode'] == 'DEMO' and data['snapshot']['generated_at'] and data['games']
    assert all(p['demo'] == 1 for p in data['posts'])
    text = (out / 'overview.json').read_text()
    assert 'key_hash' not in text and 'live_' not in text
    html = (out / 'index.html').read_text()
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
    script = (out / 'static' / 'app.js').read_text()
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
    assert json.loads((tmp_path / 'site' / 'overview.json').read_text())['mode'] == 'MIXED'


def test_refuses_empty_live_world_and_non_empty_folder(isolated_db, tmp_path, monkeypatch):
    seed_agents()
    with pytest.raises(RuntimeError):  # mode LIVE: nothing scripted to show
        snapshot.build(tmp_path / 'a')
    demo_world(tmp_path, monkeypatch)
    busy = tmp_path / 'busy'; busy.mkdir(); (busy / 'keep.txt').write_text('x')
    with pytest.raises(RuntimeError, match='empty'):
        snapshot.build(busy)
    assert (busy / 'keep.txt').read_text() == 'x'
