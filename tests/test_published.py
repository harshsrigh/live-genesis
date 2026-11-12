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


def recorded_world(monkeypatch):
    demo_world()
    engine_round(monkeypatch)


def retarget(folder, edit, **manifest_changes):
    """Hand-edit an export the way a careless or hostile committer could: change the data,
    then make the manifest agree with it again."""
    data = json.loads((folder / 'overview.json').read_text(encoding='utf-8'))
    edit(data)
    payload = json.dumps(data, ensure_ascii=True, indent=1).encode('ascii')   # as an export writes it: ASCII-escaped
    (folder / 'overview.json').write_bytes(payload)
    manifest = json.loads((folder / 'MANIFEST.json').read_text())
    manifest.update(sha256=hashlib.sha256(payload).hexdigest(), **manifest_changes)
    (folder / 'MANIFEST.json').write_text(json.dumps(manifest, indent=1))


def keys_named(node, name):
    if isinstance(node, dict):
        return sum(keys_named(v, name) for v in node.values()) + (name in node)
    if isinstance(node, list):
        return sum(keys_named(v, name) for v in node)
    return 0


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_export_refuses_scripted_demo_only_database(isolated_db, tmp_path):
    demo_world()
    out = tmp_path / 'published'
    with pytest.raises(RuntimeError, match='nothing to review.*already the public site'):
        snapshot.export_published(out)
    assert not out.exists()


def test_export_from_mixed_database(isolated_db, tmp_path, monkeypatch):
    recorded_world(monkeypatch)
    before = sha(isolated_db)
    out = tmp_path / 'published'
    manifest = snapshot.export_published(out)
    assert sha(isolated_db) == before                                   # read-only
    assert sorted(p.name for p in out.iterdir()) == ['MANIFEST.json', 'archive.json', 'overview.json']
    assert manifest == json.loads((out / 'MANIFEST.json').read_text())
    assert manifest['schema'] == snapshot.SCHEMA and manifest['sha256'] == sha(out / 'overview.json')
    assert manifest['reviewed'] is False and manifest['reviewed_at'] is None and manifest['mode'] == 'MIXED'
    assert re.fullmatch(r'\d{4}-\d\d-\d\dT[\d:.]+(\+00:00|Z)', manifest['exported_at'])
    data = json.loads((out / 'overview.json').read_text(encoding='utf-8'))
    assert manifest['actions_by_source'] == data['metrics']['actions_by_source']
    assert manifest['actions_by_source']['demo'] > 0 and manifest['actions_by_source']['engine'] == 2
    assert manifest['records']['posts'] == len(data['posts']) > 0
    # Exactly the public overview plus the snapshot note: nothing else is added.
    stamp = data.pop('snapshot')
    assert stamp['generated_at'] == manifest['exported_at'] and stamp['demo_only'] is False
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
def test_guard_hit_refuses_and_writes_nothing(isolated_db, tmp_path, monkeypatch, column, value, category):
    recorded_world(monkeypatch)
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


def test_refuses_to_overwrite_without_replace(isolated_db, tmp_path, monkeypatch):
    recorded_world(monkeypatch)
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


def test_folder_holds_exactly_its_three_regular_files(isolated_db, tmp_path, monkeypatch):
    recorded_world(monkeypatch)
    out = tmp_path / 'published'
    snapshot.export_published(out)
    snapshot.approve_published(out, confirmed=True)
    outside = tmp_path / 'outside.json'
    outside.write_bytes((out / 'overview.json').read_bytes())
    # Any extra entry fails the check, the approval and the render.
    for make in (lambda: (out / 'notes.txt').write_text('x'), lambda: (out / 'sub').mkdir(),
                 lambda: (out / 'link.json').symlink_to(outside)):
        make()
        for call in (lambda: snapshot.check_published(out), lambda: snapshot.approve_published(out, confirmed=True),
                     lambda: snapshot.build_from_data(tmp_path / 'site', out)):
            with pytest.raises(RuntimeError, match='unexpected entries'):
                call()
        assert not (tmp_path / 'site').exists()
        for entry in out.iterdir():
            if entry.name not in ('overview.json', 'archive.json', 'MANIFEST.json'):
                entry.rmdir() if entry.is_dir() and not entry.is_symlink() else entry.unlink()
        assert snapshot.check_published(out)['reviewed'] is True
    # A symlink in place of the data file is refused even though its content hashes correctly.
    (out / 'overview.json').unlink()
    (out / 'overview.json').symlink_to(outside)
    with pytest.raises(RuntimeError, match='must be regular files'):
        snapshot.check_published(out)
    # --replace clears stale extras (and the symlink) and leaves exactly the three files.
    (out / 'notes.txt').write_text('stale'); (out / 'sub').mkdir(); (out / 'sub' / 'old.json').write_text('{}')
    snapshot.export_published(out, replace=True)
    assert sorted(p.name for p in out.iterdir()) == ['MANIFEST.json', 'archive.json', 'overview.json']
    assert not (out / 'overview.json').is_symlink() and outside.exists()   # the link was removed, not its target
    snapshot.check_published(out, require_reviewed=False)
    # A symlinked folder is refused as a whole.
    alias = tmp_path / 'alias'; alias.symlink_to(out, target_is_directory=True)
    with pytest.raises(RuntimeError, match='not a plain folder'):
        snapshot.check_published(alias, require_reviewed=False)
    with pytest.raises(RuntimeError, match='not a plain folder'):
        snapshot.export_published(alias, replace=True)


def test_approval_is_explicit_and_bound_to_the_hash(isolated_db, tmp_path, monkeypatch):
    recorded_world(monkeypatch)
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
    (out / 'overview.json').write_bytes(original.replace(b'"mode": "MIXED"', b'"mode": "LIVE"'))
    assert (out / 'overview.json').read_bytes() != original
    for call in (lambda: snapshot.check_published(out), lambda: snapshot.approve_published(out, confirmed=True),
                 lambda: snapshot.build_from_data(tmp_path / 'site', out)):
        with pytest.raises(RuntimeError, match='does not match the sha256'):
            call()
    assert not (tmp_path / 'site').exists()
    (out / 'overview.json').write_bytes(original)
    assert snapshot.check_published(out)['reviewed'] is True


def test_hand_edited_manifest_cannot_carry_guarded_text_past_the_check(isolated_db, tmp_path, monkeypatch):
    """The review finding: new text, a recomputed sha256 and a hand-set "reviewed": true."""
    recorded_world(monkeypatch)
    out = tmp_path / 'published'
    snapshot.export_published(out)
    secret = 'someone.real@example.org'

    def plant(data):
        data['posts'][0]['body'] = 'Write to ' + secret
    post_id = json.loads((out / 'overview.json').read_text(encoding='utf-8'))['posts'][0]['id']
    # Unreviewed but self-consistent: approval is refused by the guards.
    retarget(out, plant)
    with pytest.raises(RuntimeError) as err:
        snapshot.approve_published(out, confirmed=True)
    assert f'posts id {post_id}, field body: looks like an email address' in str(err.value) and secret not in str(err.value)
    assert json.loads((out / 'MANIFEST.json').read_text())['reviewed'] is False
    # Hand-approved: the CI check and the renderer both refuse, and nothing is rendered.
    retarget(out, plant, reviewed=True, reviewed_at='2026-01-01T00:00:00+00:00')
    assert json.loads((out / 'MANIFEST.json').read_text())['sha256'] == sha(out / 'overview.json')
    for call in (lambda: snapshot.check_published(out), lambda: snapshot.build_from_data(tmp_path / 'site', out)):
        with pytest.raises(RuntimeError) as err:
            call()
        assert 'Content guards matched' in str(err.value) and 'email address' in str(err.value) and secret not in str(err.value)
    assert not (tmp_path / 'site').exists()
    monkeypatch.setattr('sys.argv', ['snapshot', '--check', str(out)])
    with pytest.raises(SystemExit) as stop:
        snapshot.main()
    assert stop.value.code == 2


def test_manifest_counts_must_match_the_data(isolated_db, tmp_path, monkeypatch):
    recorded_world(monkeypatch)
    out = tmp_path / 'published'
    honest = snapshot.export_published(out)
    snapshot.approve_published(out, confirmed=True)
    good = (out / 'MANIFEST.json').read_text()
    for field, value in (('actions_by_source', {**honest['actions_by_source'], 'engine': 0, 'demo': 999}),
                         ('records', {**honest['records'], 'posts': 0}), ('actions_by_source', None)):
        (out / 'MANIFEST.json').write_text(json.dumps({**json.loads(good), field: value}))
        for call in (lambda: snapshot.check_published(out), lambda: snapshot.approve_published(out, confirmed=True),
                     lambda: snapshot.build_from_data(tmp_path / 'site', out)):
            with pytest.raises(RuntimeError, match='counts in the manifest do not match'):
                call()
    assert not (tmp_path / 'site').exists()
    (out / 'MANIFEST.json').write_text(good)
    assert snapshot.check_published(out)['reviewed'] is True
    # Data relabelled as demo-only (with a matching hash) is not a reviewed export either.
    retarget(out, lambda data: data['snapshot'].update(demo_only=True))
    with pytest.raises(RuntimeError, match='scripted demo data only'):
        snapshot.check_published(out)


def test_approve_refuses_tampered_unreviewed_data_and_missing_files(isolated_db, tmp_path, monkeypatch):
    recorded_world(monkeypatch)
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
    recorded_world(monkeypatch)
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
    recorded_world(monkeypatch)                                           # MIXED: scripted + model records
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
                  'VIEW THE SCRIPTED DEMO', 'BACK TO THE MAIN SNAPSHOT', 'dataset.sibling',
                  'external client (self-declared, unverified)'):
        assert label in script, label
    added = script[script.index('function showSibling'):script.index('async function refresh')]
    assert not re.search(r'human|agents[ -]only|agent-only', added, re.I)
    # The link shown on a scripted page never calls the other page (or this one) "recorded".
    assert "recorded ? 'VIEW THE SCRIPTED DEMO →' : 'BACK TO THE MAIN SNAPSHOT →'" in added
    assert 'RECORDED' not in added
    # The source page carries no link: it appears only when a build names the other snapshot.
    assert 'sibling' not in (snapshot.STATIC / 'index.html').read_text(encoding='utf-8')


def test_published_folder_is_committable_but_private_files_stay_ignored():
    rules = [line.strip() for line in (REPO / '.gitignore').read_text().splitlines() if line.strip() and not line.startswith('#')]
    assert not any('published' in rule for rule in rules)
    assert {'.env', '*.db', '*.sqlite', 'secrets/*', 'data/*'} <= set(rules)
    assert 'published/* -text' in (REPO / '.gitattributes').read_text()
    assert snapshot.PUBLISHED == REPO / 'published'


# ---- the workflow must fail closed: parsed structurally (line-based; no YAML dependency) ------

WORKFLOW = REPO / '.github' / 'workflows' / 'snapshot.yml'
CHECK_LINE = 'python -m live_genesis.snapshot --check published'


def parse_jobs(text):
    """{job: {'keys': {key: value}, 'steps': [{'keys': {key: value}, 'run': str}]}} for the
    two-space-indented layout this workflow uses."""
    jobs, job, step, in_run = {}, None, None, False
    lines = text.splitlines()
    start = lines.index('jobs:')
    for line in lines[start + 1:]:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 2:
            job = jobs.setdefault(line.strip().rstrip(':'), {'keys': {}, 'steps': []}); step = None; in_run = False
        elif indent == 4 and job is not None:
            key, _, value = line.strip().partition(':')
            job['keys'][key] = value.strip(); step = None; in_run = False
        elif indent == 6 and line.lstrip().startswith('- '):
            key, _, value = line.strip()[2:].partition(':')
            step = {'keys': {key: value.strip()}, 'run': ''}; job['steps'].append(step)
            in_run = key == 'run'
            if in_run and value.strip() != '|':
                step['run'] = value.strip()
        elif indent == 8 and step is not None and not in_run:
            key, _, value = line.strip().partition(':')
            step['keys'][key] = value.strip()
            in_run = key == 'run'
            if in_run and value.strip() != '|':
                step['run'] = value.strip()
        elif indent == 8 and step is not None and in_run and re.match(r'[a-z][a-z-]*:', line.strip()) and step['keys'].get('run') != '|':
            key, _, value = line.strip().partition(':')
            step['keys'][key] = value.strip(); in_run = False
        elif step is not None and in_run:
            step['run'] += line.strip() + '\n'
        elif step is not None:
            step['keys'].setdefault('_nested', ''); step['keys']['_nested'] += line.strip() + '\n'
    return jobs


def workflow_problems(text):
    problems = []
    jobs = parse_jobs(text)
    if set(jobs) != {'test', 'snapshot'}:
        return [f'unexpected jobs {sorted(jobs)}']
    if not any('python -m pytest' in step['run'] for step in jobs['test']['steps']):
        problems.append('test job does not run pytest')
    job = jobs['snapshot']
    if job['keys'].get('needs') != 'test':
        problems.append('snapshot job must need the test job')
    if job['keys'].get('if') != "github.event_name != 'pull_request' && github.ref == 'refs/heads/main'":
        problems.append('snapshot job condition changed')
    if 'continue-on-error' in job['keys'] or 'continue-on-error' in jobs['test']['keys']:
        problems.append('a job continues on error')
    steps = job['steps']
    for step in steps:
        for banned in ('if', 'continue-on-error'):
            if banned in step['keys']:
                problems.append(f"a snapshot step has `{banned}`")
        if '||' in step['run'] or 'set +e' in step['run']:
            problems.append('a snapshot step swallows a failure')
    checks = [i for i, step in enumerate(steps) if '--check' in step['run']]
    flags = [i for i, step in enumerate(steps) if re.search(r'HAVE_PUBLISHED=1"? *>>', step['run'])]
    if len(checks) != 1 or flags != checks:
        problems.append('exactly one step may run the check, and only it may set HAVE_PUBLISHED')
    else:
        run = steps[checks[0]]['run'].splitlines()
        check_at = [n for n, line in enumerate(run) if line == CHECK_LINE]
        flag_at = [n for n, line in enumerate(run) if 'HAVE_PUBLISHED' in line]
        if len(check_at) != 1 or len(flag_at) != 1 or check_at[0] > flag_at[0]:
            problems.append('the check must be one plain command that precedes HAVE_PUBLISHED')
        if run[0] != 'if [ -e published ]; then':
            problems.append('the check must run whenever published/ exists')
        later = [i for i, step in enumerate(steps) if 'live_genesis.snapshot "$RUNNER_TEMP' in step['run'] or 'git push' in step['run']]
        if not later or min(later) < checks[0]:
            problems.append('the check must come before any build or publish step')
    pushes = [i for i, step in enumerate(steps) if 'git push' in step['run']]
    if pushes != [len(steps) - 1]:
        problems.append('the publish step must be the single last step')
    builds = ''.join(step['run'] for step in steps)
    for needed in ('--from-data published --sibling demo/', '"$RUNNER_TEMP/site/demo" --sibling ../', 'if [ "${HAVE_PUBLISHED:-}" = "1" ]; then'):
        if needed not in builds:
            problems.append(f'missing: {needed}')
    if 'runner.temp' in text:
        problems.append('runner.temp is not allowed in job-level env')
    if text.count('contents: write') != 1 or text.count('contents: read') != 1 or jobs['snapshot']['keys'].get('permissions') != '':
        problems.append('permissions changed')
    return problems


def test_workflow_fails_closed():
    assert workflow_problems(WORKFLOW.read_text(encoding='utf-8')) == []


def mutate(old, new):
    def apply(text):
        assert text.count(old) == 1, old
        return text.replace(old, new)
    return apply


CHECK_STEP = '      - name: Reviewed data must be intact and approved (fails the job, nothing is published)\n'
MUTATIONS = {
    'check || true': mutate(CHECK_LINE + '\n', CHECK_LINE + ' || true\n'),
    'continue-on-error on the check step': mutate(CHECK_STEP, CHECK_STEP + '        continue-on-error: true\n'),
    'HAVE_PUBLISHED before the check': mutate(
        '            ' + CHECK_LINE + '\n            echo "HAVE_PUBLISHED=1" >> "$GITHUB_ENV"\n',
        '            echo "HAVE_PUBLISHED=1" >> "$GITHUB_ENV"\n            ' + CHECK_LINE + '\n'),
    'if: always() on the publish step': mutate('      - name: Publish to gh-pages\n', '      - name: Publish to gh-pages\n        if: always()\n'),
    'needs: test deleted': mutate('    needs: test\n', ''),
    'check step deleted': lambda text: text[:text.index(CHECK_STEP)] + text[text.index('      - name: Build scripted demo world'):],
    'check moved after the build': mutate('          python -m live_genesis.snapshot "$RUNNER_TEMP/site"\n',
                                         '          python -m live_genesis.snapshot "$RUNNER_TEMP/site"\n          ' + CHECK_LINE + '\n'),
    'check only under a narrower condition': mutate('if [ -e published ]; then', 'if [ -f published/APPROVED ]; then'),
    'job-level continue-on-error': mutate('    needs: test\n', '    needs: test\n    continue-on-error: true\n'),
}


@pytest.mark.parametrize('name', sorted(MUTATIONS))
def test_workflow_mutations_are_caught(name):
    mutated = MUTATIONS[name](WORKFLOW.read_text(encoding='utf-8'))
    assert workflow_problems(mutated), name
