"""Reviewed-data publishing: export -> approve -> check -> render. Offline; no model is called."""
import hashlib
import json
import re
from pathlib import Path

import pytest

from live_genesis import cli, engine, snapshot
from live_genesis.core import connect, overview, seed_agents

REPO = Path(__file__).resolve().parent.parent
KEY_SHAPED = re.compile(r'live_[A-Za-z0-9_-]{20,}')
PRIVATE_NOTE = 'private-note-zebra-quartz'


def demo_world():
    seed_agents()
    cli.demo(); cli.demo_game(); cli.demo_document()


def engine_round(monkeypatch, limit=2):
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    reply = json.dumps({'action': 'post', 'topic': 'research', 'title': 'Mocked', 'body': 'Offline mocked provider response.',
                        'memory': PRIVATE_NOTE})
    monkeypatch.setattr(engine, 'infer', lambda provider, model, prompt: engine.Completion(reply, 10, 5))
    engine.run_round(limit=limit)


def keys_named(node, name):
    if isinstance(node, dict):
        return sum(keys_named(v, name) for v in node.values()) + (name in node)
    if isinstance(node, list):
        return sum(keys_named(v, name) for v in node)
    return 0


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_export_from_demo_database(isolated_db, tmp_path):
    demo_world()
    before = sha(isolated_db)
    out = tmp_path / 'published'
    manifest = snapshot.export_published(out)
    assert sha(isolated_db) == before                                   # read-only
    assert sorted(p.name for p in out.iterdir()) == ['MANIFEST.json', 'overview.json']
    assert manifest == json.loads((out / 'MANIFEST.json').read_text())
    assert manifest['schema'] == snapshot.SCHEMA and manifest['sha256'] == sha(out / 'overview.json')
    assert manifest['reviewed'] is False and manifest['reviewed_at'] is None and manifest['mode'] == 'DEMO'
    assert re.fullmatch(r'\d{4}-\d\d-\d\dT[\d:.]+(\+00:00|Z)', manifest['exported_at'])
    data = json.loads((out / 'overview.json').read_text(encoding='utf-8'))
    assert manifest['actions_by_source'] == data['metrics']['actions_by_source']
    assert manifest['actions_by_source']['demo'] > 0 and manifest['actions_by_source']['engine'] == 0
    assert manifest['records']['posts'] == len(data['posts']) > 0
    # Exactly the public overview plus the snapshot note: nothing else is added.
    stamp = data.pop('snapshot')
    assert stamp['generated_at'] == manifest['exported_at'] and stamp['demo_only'] is True
    assert data == overview(readonly=True)


def test_export_with_model_actions_exposes_nothing_private(isolated_db, tmp_path, monkeypatch):
    seed_agents(model='test-model-1')
    engine_round(monkeypatch)
    out = tmp_path / 'published'
    manifest = snapshot.export_published(out)
    assert manifest['mode'] == 'LIVE'
    assert manifest['actions_by_source'] == {'demo': 0, 'api': 0, 'engine': 2, 'unknown': 0}
    text = (out / 'overview.json').read_text(encoding='utf-8') + (out / 'MANIFEST.json').read_text()
    assert 'key_hash' not in text and not KEY_SHAPED.search(text) and 'fake-key' not in text
    with connect() as cx:  # the note exists in the database and stays there
        assert cx.execute('SELECT COUNT(*) FROM memory_notes WHERE note LIKE ?', ('%' + PRIVATE_NOTE + '%',)).fetchone()[0] > 0
    assert PRIVATE_NOTE not in text
    data = json.loads((out / 'overview.json').read_text(encoding='utf-8'))
    assert keys_named(data, 'memory') == keys_named(overview(readonly=True), 'memory')
    assert all(p['source'] == 'engine' and p['verified'] == 1 for p in data['posts'])
    assert data['snapshot']['demo_only'] is False


@pytest.mark.parametrize('column,value,category', [
    ('body', 'Write to someone.real@example.org for details.', 'email address'),
    ('title', 'Support us at paypal.me/somebody', 'payment or donation link'),
    ('body', 'The key is sk-abcdefghijklmnopqrstuvwx', 'API key or credential'),
])
def test_guard_hit_refuses_and_writes_nothing(isolated_db, tmp_path, column, value, category):
    demo_world()
    with connect() as cx:
        post_id = cx.execute('SELECT MIN(id) FROM posts').fetchone()[0]
        cx.execute(f'UPDATE posts SET {column}=? WHERE id=?', (value, post_id))
    out = tmp_path / 'published'
    with pytest.raises(RuntimeError) as err:
        snapshot.export_published(out)
    message = str(err.value)
    assert f'posts id {post_id}, field {column}' in message and category in message
    assert value not in message and value.split()[-1] not in message     # the match is never echoed
    assert not out.exists()
    # --replace does not bypass the guards, and an earlier export is left as it was.
    with connect() as cx:
        cx.execute(f'UPDATE posts SET {column}=? WHERE id=?', ('Harmless text.', post_id))
    first = snapshot.export_published(out)
    with connect() as cx:
        cx.execute(f'UPDATE posts SET {column}=? WHERE id=?', (value, post_id))
    with pytest.raises(RuntimeError, match='nothing was written'):
        snapshot.export_published(out, replace=True)
    assert snapshot.check_published(out, require_reviewed=False) == first


def test_hash_fields_are_exempt_only_when_well_formed():
    assert snapshot.guard_hits({'id': 3, 'head_hash': 'a' * 64}, 'documents') == []
    assert snapshot.guard_hits({'id': 3, 'head_hash': 'someone@example.org'}, 'documents') == \
        ['documents id 3, field head_hash: looks like an email address']
    assert snapshot.guard_hits({'id': 3, 'revisions': [{'summary': 'b' * 64}]}, 'documents')  # not a hash field


def test_refuses_to_overwrite_without_replace(isolated_db, tmp_path):
    demo_world()
    out = tmp_path / 'published'
    snapshot.export_published(out)
    snapshot.approve_published(out, confirmed=True)
    kept = (out / 'MANIFEST.json').read_bytes()
    with pytest.raises(RuntimeError, match='--replace'):
        snapshot.export_published(out)
    assert (out / 'MANIFEST.json').read_bytes() == kept
    again = snapshot.export_published(out, replace=True)
    assert again['reviewed'] is False                                     # a new export is unreviewed again
    with pytest.raises(RuntimeError, match='not approved'):
        snapshot.check_published(out)


def test_approval_is_explicit_and_bound_to_the_hash(isolated_db, tmp_path):
    demo_world()
    out = tmp_path / 'published'
    snapshot.export_published(out)
    with pytest.raises(RuntimeError, match='not approved'):               # the check CI runs
        snapshot.check_published(out)
    with pytest.raises(RuntimeError, match='--i-have-read-it'):
        snapshot.approve_published(out)
    assert json.loads((out / 'MANIFEST.json').read_text())['reviewed'] is False
    approved = snapshot.approve_published(out, confirmed=True)
    assert approved['reviewed'] is True and approved['reviewed_at']
    assert snapshot.check_published(out) == approved
    # Any later change to the data invalidates the approval.
    original = (out / 'overview.json').read_bytes()
    (out / 'overview.json').write_bytes(original.replace(b'"mode": "DEMO"', b'"mode": "LIVE"'))
    assert (out / 'overview.json').read_bytes() != original
    for call in (lambda: snapshot.check_published(out), lambda: snapshot.approve_published(out, confirmed=True),
                 lambda: snapshot.build_from_data(tmp_path / 'site', out)):
        with pytest.raises(RuntimeError, match='does not match the sha256'):
            call()
    assert not (tmp_path / 'site').exists()
    (out / 'overview.json').write_bytes(original)
    assert snapshot.check_published(out)['reviewed'] is True


def test_approve_refuses_tampered_unreviewed_data_and_missing_files(isolated_db, tmp_path):
    demo_world()
    out = tmp_path / 'published'
    snapshot.export_published(out)
    (out / 'overview.json').write_bytes((out / 'overview.json').read_bytes() + b'\n')
    with pytest.raises(RuntimeError, match='does not match the sha256'):
        snapshot.approve_published(out, confirmed=True)
    assert json.loads((out / 'MANIFEST.json').read_text())['reviewed'] is False
    (out / 'MANIFEST.json').unlink()
    with pytest.raises(RuntimeError, match='missing or unreadable'):
        snapshot.check_published(out)
    (out / 'MANIFEST.json').write_text('{"schema": "something-else", "reviewed": true}')
    with pytest.raises(RuntimeError, match='manifest is not a'):
        snapshot.check_published(out)
    with pytest.raises(RuntimeError, match='missing or unreadable'):
        snapshot.check_published(tmp_path / 'absent')


def test_command_line_steps(isolated_db, tmp_path, monkeypatch, capsys):
    demo_world()
    out = str(tmp_path / 'published')

    def run(*argv):
        monkeypatch.setattr('sys.argv', ['snapshot', *argv])
        try:
            snapshot.main()
            return 0
        except SystemExit as stop:
            return stop.code

    assert run('--export', out) == 0
    assert 'NOT yet approved' in capsys.readouterr().out
    assert run('--check', out) == 2                                       # CI fails loudly on an unapproved export
    assert 'not approved' in capsys.readouterr().err
    assert run('--export', out) == 2 and run('--approve', out) == 2
    printed = capsys.readouterr()
    assert 'actions by source: demo' in printed.out and 'git history' in printed.out and '--i-have-read-it' in printed.err
    assert run('--approve', out, '--i-have-read-it') == 0
    assert run('--check', out) == 0
    assert 'intact and approved' in capsys.readouterr().out
    assert run(str(tmp_path / 'site'), '--from-data', out, '--sibling', 'demo/') == 0
    assert 'rendered from approved data' in capsys.readouterr().out
    assert run('--check', out, str(tmp_path / 'x')) == 2 and run() == 2
    assert run(str(tmp_path / 'bad'), '--sibling', 'https://example.org/') == 2
    assert not (tmp_path / 'bad').exists() and not (tmp_path / 'x').exists()


def test_render_from_data_equals_render_from_database(isolated_db, tmp_path, monkeypatch):
    demo_world()
    engine_round(monkeypatch)                                             # MIXED: scripted + model records
    published = tmp_path / 'published'
    manifest = snapshot.export_published(published)
    assert manifest['mode'] == 'MIXED' and manifest['actions_by_source']['engine'] == 2
    with pytest.raises(RuntimeError, match='not approved'):
        snapshot.build_from_data(tmp_path / 'early', published)
    assert not (tmp_path / 'early').exists()
    snapshot.approve_published(published, confirmed=True)
    from_db = tmp_path / 'from-db'
    monkeypatch.setattr(snapshot, 'utcnow', lambda: '2030-01-01T00:00:00+00:00')         # the build time, not the export time
    snapshot.build(from_db, allow_non_demo=True, sibling='demo/')
    # Rendering from data opens no database at all.
    monkeypatch.setenv('LIVE_DB', str(tmp_path / 'no-such.sqlite'))
    from_data = tmp_path / 'from-data'
    info = snapshot.build_from_data(from_data, published, sibling='demo/')
    assert not (tmp_path / 'no-such.sqlite').exists()
    assert info['generated_at'] == manifest['exported_at'] and info['demo_only'] is False

    def files(root):
        return {p.relative_to(root).as_posix(): p for p in root.rglob('*') if p.is_file()}
    a, b = files(from_data), files(from_db)
    assert set(a) == set(b) and {'index.html', 'overview.json', '.nojekyll', 'llms.txt', 'openapi.json', 'static/app.js'} <= set(a)
    for name in a:
        if name != 'overview.json':
            assert a[name].read_bytes() == b[name].read_bytes(), name
    # The data file is the reviewed one byte for byte; only the timestamp source differs.
    assert a['overview.json'].read_bytes() == (published / 'overview.json').read_bytes()
    left, right = (json.loads(p.read_text(encoding='utf-8')) for p in (a['overview.json'], b['overview.json']))
    assert left['snapshot'].pop('generated_at') == manifest['exported_at'] != right['snapshot'].pop('generated_at')
    assert left == right
    html = a['index.html'].read_text(encoding='utf-8')
    assert '<html lang="en" data-overview="overview.json" data-sibling="demo/">' in html
    assert html.count('http-equiv="Content-Security-Policy"') == 1 and f'content="{snapshot.CONTENT_SECURITY_POLICY}"' in html
    assert not re.search(r'(?:href|src)="/', html) and re.search(r'"static/app\.js\?v=[0-9a-f]{12}"', html)
    plain = tmp_path / 'plain'
    snapshot.build_from_data(plain, published)
    assert 'data-sibling' not in (plain / 'index.html').read_text(encoding='utf-8')
    for bad in ('https://example.org/', '/demo/', 'demo', '../../', 'demo/" onload="x'):
        with pytest.raises(RuntimeError, match='--sibling'):
            snapshot.build_from_data(tmp_path / 'bad', published, sibling=bad)
        assert not (tmp_path / 'bad').exists()


def test_page_labels_and_link_wording():
    script = (snapshot.STATIC / 'app.js').read_text(encoding='utf-8')
    for label in ('STATIC SNAPSHOT · NOT RUNNING', 'SCRIPTED DEMO DATA', 'RECORDED DATA · NO DEMO', "'CAPTURED '+shortDate(snap.generated_at)",
                  'VIEW THE SCRIPTED DEMO', 'VIEW THE RECORDED SNAPSHOT', 'dataset.sibling',
                  'external client (self-declared, unverified)'):
        assert label in script, label
    added = script[script.index('function showSibling'):script.index('async function refresh')]
    assert not re.search(r'human|agents[ -]only|agent-only', added, re.I)
    # The source page carries no link: it appears only when a build names the other snapshot.
    assert 'sibling' not in (snapshot.STATIC / 'index.html').read_text(encoding='utf-8')


def test_published_folder_is_committable_but_private_files_stay_ignored():
    rules = [line.strip() for line in (REPO / '.gitignore').read_text().splitlines() if line.strip() and not line.startswith('#')]
    assert not any('published' in rule for rule in rules)
    assert {'.env', '*.db', '*.sqlite', 'secrets/*'} <= set(rules)
    assert 'published/* -text' in (REPO / '.gitattributes').read_text()
    assert snapshot.PUBLISHED == REPO / 'published'


def test_workflow_uses_the_python_check_and_keeps_its_permissions():
    workflow = (REPO / '.github' / 'workflows' / 'snapshot.yml').read_text(encoding='utf-8')
    assert 'python -m live_genesis.snapshot --check published' in workflow
    assert '--from-data published' in workflow and '--sibling demo/' in workflow and '--sibling ../' in workflow
    assert 'runner.temp' not in workflow                                  # not allowed in job-level env
    assert workflow.count('contents: write') == 1 and workflow.count('contents: read') == 1
