"""Reviewed export of Answers Over Time alone (`snapshot --export --archive-only`): archive.json and
MANIFEST.json, no overview.json, and the society tables are never read. Offline; no model is called."""
import hashlib
import json
import re

import pytest

from live_genesis import archive, core, engine, snapshot
from test_archive import fake, ready, recorded_archive, retarget_archive  # noqa: F401
from test_snapshot import files_of, run_main

SOCIETY_TEXT = 'society-only-marker-violet-heron-42'
ARCHIVE_KEYS = ['schema', 'contents', 'file', 'exported_at', 'archive', 'reviewed', 'reviewed_at']


def society_post():
    core.submit_action(1, {'action': 'post', 'topic': 'research', 'title': 'A society post', 'body': f'Private to the society: {SOCIETY_TEXT}.'})
    with core.connect(readonly=True) as cx:
        assert cx.execute('SELECT COUNT(*) FROM posts WHERE body LIKE ?', (f'%{SOCIETY_TEXT}%',)).fetchone()[0] == 1


def world(monkeypatch, reply='Seven.'):
    """Recorded archive answers next to a society post whose text must never reach an archive export."""
    recorded_archive(monkeypatch, reply)
    society_post()


def no_society(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('an archive-only export must not read the society')
    monkeypatch.setattr(snapshot, '_capture', refuse)
    monkeypatch.setattr(snapshot, 'overview', refuse)


def approved(monkeypatch, tmp_path, name='published'):
    world(monkeypatch)
    out = tmp_path / name
    snapshot.export_published(out, archive_only=True)
    snapshot.approve_published(out, confirmed=True)
    return out


def edit_manifest(folder, **changes):
    manifest = json.loads((folder / 'MANIFEST.json').read_text(encoding='utf-8'))
    for key, value in changes.items():
        if value is KeyError:
            manifest.pop(key, None)
        else:
            manifest[key] = value
    (folder / 'MANIFEST.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')


def test_archive_only_export_writes_two_files_and_no_society_text(ready, tmp_path, monkeypatch):
    world(monkeypatch, 'Café answers: seven.')
    before = hashlib.sha256(ready.read_bytes()).hexdigest()
    no_society(monkeypatch)                                   # the society tables are not consulted at all
    out = tmp_path / 'published'
    manifest = snapshot.export_published(out, archive_only=True)
    assert hashlib.sha256(ready.read_bytes()).hexdigest() == before
    assert files_of(out) == {'archive.json', 'MANIFEST.json'} and sorted(p.name for p in out.iterdir()) == ['MANIFEST.json', 'archive.json']
    raw = (out / 'archive.json').read_bytes()
    assert raw.isascii() and b'Caf\\u00e9' in raw
    assert json.loads(raw) == json.loads(json.dumps(archive.public_payload()))        # exactly the public payload
    assert manifest == json.loads((out / 'MANIFEST.json').read_text(encoding='utf-8'))
    assert list(manifest) == ARCHIVE_KEYS                                              # no mode, records, actions_by_source or top-level sha256
    assert manifest['schema'] == snapshot.SCHEMA == 'live-genesis-published/5'
    assert manifest['contents'] == 'archive' and manifest['file'] == 'archive.json'
    assert manifest['reviewed'] is False and manifest['reviewed_at'] is None and manifest['exported_at']
    assert manifest['archive'] == {**snapshot._archive_counts(json.loads(raw)), 'sha256': hashlib.sha256(raw).hexdigest()}
    assert manifest['archive']['answers_by_source'] == {'engine': 40, 'api': 0, 'demo': 0, 'mock': 0}
    for name in files_of(out):
        text = (out / name).read_text(encoding='utf-8')
        assert SOCIETY_TEXT not in text and 'A society post' not in text and '"posts"' not in text and '"agents"' not in text, name


def test_archive_only_round_trip_renders_the_archive_and_nothing_else(ready, tmp_path, monkeypatch):
    world(monkeypatch)
    out = tmp_path / 'published'
    snapshot.export_published(out, archive_only=True)
    with pytest.raises(RuntimeError, match=r'not approved \("reviewed" is not true\)\. Read archive\.json, then run') as err:
        snapshot.check_published(out)
    assert 'overview.json' not in str(err.value)
    with pytest.raises(RuntimeError, match='Not approved') as err:
        snapshot.approve_published(out)
    assert 'archive.json in full' in str(err.value) and 'overview.json' not in str(err.value)
    with pytest.raises(RuntimeError, match='not approved'):
        snapshot.build_from_data(tmp_path / 'early', out, archive_only=True)
    assert not (tmp_path / 'early').exists()
    done = snapshot.approve_published(out, confirmed=True)
    assert done['reviewed'] is True and done['reviewed_at'] and list(done) == ARCHIVE_KEYS
    manifest = snapshot.check_published(out)
    assert manifest == done and files_of(out) == {'archive.json', 'MANIFEST.json'}
    assert 'overview.json' not in snapshot._describe(manifest) and 'Answers Over Time only' in snapshot._describe(manifest)
    assert snapshot.published_non_ascii(out) == []
    # Rendering opens no database and reads no society data.
    no_society(monkeypatch)
    monkeypatch.setenv('LIVE_DB', str(tmp_path / 'no-such.sqlite'))
    with pytest.raises(RuntimeError, match='holds Answers Over Time only; render it with --archive-only'):
        snapshot.build_from_data(tmp_path / 'full-site', out)
    assert not (tmp_path / 'full-site').exists()
    site = tmp_path / 'site'
    info = snapshot.build_from_data(site, out, sibling='demo/', archive_only=True)
    assert info['demo_only'] is False and info['generated_at'] == manifest['exported_at'] and info['manifest'] == manifest
    written = files_of(site)
    assert 'archive/index.html' in written and 'archive/q/Q01.html' in written and 'index.html' in written
    assert (site / 'archive.json').read_bytes() == (out / 'archive.json').read_bytes()
    assert not any(name in written for name in ('overview.json', 'stats.html', 'MANIFEST.json'))
    assert not any(name.startswith(('post/', 'profile/')) or name.endswith('.js') for name in written)
    page = (site / 'archive' / 'q' / 'Q01.html').read_text(encoding='utf-8')
    assert 'Seven.' in page and 'server call' in page and 'Recorded answers' in page and 'not model output' not in page
    assert len(re.findall(r'(?i)scripted', page)) == len(re.findall(r'(?i)>scripted demo</a>', page)) == 1   # only the link to the sibling
    for name in written:
        if name.endswith(('.html', '.json')):
            assert SOCIETY_TEXT not in (site / name).read_text(encoding='utf-8'), name
    with pytest.raises(RuntimeError, match='--api-base cannot be used with --archive-only'):
        snapshot.build_from_data(tmp_path / 'kit', out, api_base='https://api.example.org', archive_only=True)


def test_archive_only_folder_and_manifest_tampering_is_refused(ready, tmp_path, monkeypatch):
    out = approved(monkeypatch, tmp_path)
    good = {name: (out / name).read_bytes() for name in ('archive.json', 'MANIFEST.json')}
    full = tmp_path / 'full'
    snapshot.export_published(full)
    snapshot.approve_published(full, confirmed=True)
    overview_bytes = (full / 'overview.json').read_bytes()
    assert SOCIETY_TEXT.encode() in overview_bytes

    def refused(match):
        for call in (lambda: snapshot.check_published(out), lambda: snapshot.approve_published(out, confirmed=True),
                     lambda: snapshot.build_from_data(tmp_path / 'site', out, archive_only=True),
                     lambda: snapshot.build_from_data(tmp_path / 'site', out)):
            with pytest.raises(RuntimeError, match=match):
                call()
        assert not (tmp_path / 'site').exists()
        for entry in list(out.iterdir()):
            entry.rmdir() if entry.is_dir() else entry.unlink()
        for name, content in good.items():
            (out / name).write_bytes(content)
        assert snapshot.check_published(out)['reviewed'] is True

    # An overview.json dropped into the folder is never published unchecked: it is refused outright ...
    (out / 'overview.json').write_bytes(overview_bytes)
    refused(r'unexpected entries \(1\).*overview\.json is there')
    (out / 'overview.json').mkdir()
    refused('unexpected entries')
    (out / 'notes.txt').write_text('x')
    refused('unexpected entries')
    # ... and calling the folder "full" by hand does not help: every full-export check then applies.
    (out / 'overview.json').write_bytes(overview_bytes)
    edit_manifest(out, contents='full')
    refused('manifest is not a live-genesis-published/5 manifest')
    (out / 'overview.json').write_bytes(overview_bytes)
    edit_manifest(out, contents='full', file='overview.json')
    refused('overview.json does not match the sha256')
    edit_manifest(out, contents='full', file='overview.json')            # "archive" -> "full" with no overview.json
    refused('overview.json is missing')
    # The manifest must say what it holds, in the current schema, with the archive fields and no others.
    for value in (KeyError, None, 'society', '', ['archive'], 5, True):
        edit_manifest(out, contents=value)
        refused('does not say what the export holds')
    edit_manifest(out, schema='live-genesis-published/4')
    refused('not a live-genesis-published/5 manifest')
    edit_manifest(out, schema='live-genesis-published/4', contents=KeyError)
    refused('not a live-genesis-published/5 manifest')
    edit_manifest(out, file='overview.json')
    refused('not a live-genesis-published/5 manifest')
    for extra in ({'mode': 'LIVE'}, {'sha256': '0' * 64}, {'records': {}}, {'exported_at': KeyError}):
        edit_manifest(out, **extra)
        refused('must hold exactly')
    for value in (None, 5, 'yesterday', '<b>2026</b>'):
        edit_manifest(out, exported_at=value)
        refused('exported_at is not a timestamp')
    edit_manifest(out, archive=KeyError, extra=1)
    refused('must hold exactly')
    edit_manifest(out, archive='archive.json')
    refused('manifest does not describe archive.json')
    edit_manifest(out, reviewed='yes')
    with pytest.raises(RuntimeError, match='not approved'):
        snapshot.check_published(out)
    (out / 'MANIFEST.json').write_bytes(good['MANIFEST.json'])
    # archive.json itself: every existing rule still runs.
    (out / 'archive.json').write_bytes(good['archive.json'].replace(b'Seven.', b'Eight.'))
    refused('archive.json does not match the sha256')
    (out / 'archive.json').unlink()
    refused('archive.json is missing')
    retarget_archive(out, lambda d: d['answers'][0].update(text='Write to someone@example.org'))
    refused('Content guards matched:\n  archive answers id 1, field text: looks like an email address')
    retarget_archive(out, lambda d: d['answers'][0].update(source='mock'))
    refused('mock answers, which are never published')
    retarget_archive(out, lambda d: d['answers'][0].update(source='demo'))
    refused('scripted demo answers')
    retarget_archive(out, lambda d: d.update(locked=False))
    refused('question list is not locked')
    retarget_archive(out, lambda d: d.update(demo_only=True))
    refused('scripted demo data only')
    retarget_archive(out, lambda d: (d['answers'].clear(), d['answerers'].clear(), d['answers_by_source'].update(engine=0)))
    refused('archive.json is refused: it holds no answers')
    manifest = json.loads(good['MANIFEST.json']); manifest['archive']['answers_by_source']['engine'] = 39
    (out / 'MANIFEST.json').write_text(json.dumps(manifest))
    refused('counts in the manifest do not match archive.json')
    data = json.loads(good['archive.json']); data['answers'][0]['text'] = 'café'
    payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
    manifest = json.loads(good['MANIFEST.json']); manifest['archive']['sha256'] = hashlib.sha256(payload).hexdigest()
    (out / 'archive.json').write_bytes(payload); (out / 'MANIFEST.json').write_text(json.dumps(manifest))
    refused('archive.json holds a non-ASCII byte')

    # The other direction: a full export called "archive" by hand still has its overview.json, so it is refused.
    edit_manifest(full, contents='archive')
    with pytest.raises(RuntimeError, match='manifest is not a live-genesis-published/5 manifest'):
        snapshot.check_published(full)
    edit_manifest(full, contents='archive', file='archive.json')
    for call in (lambda: snapshot.check_published(full), lambda: snapshot.build_from_data(tmp_path / 'site', full, archive_only=True)):
        with pytest.raises(RuntimeError, match='unexpected entries'):
            call()
    (full / 'overview.json').unlink()                                    # ... and without it, the full manifest's fields give it away
    with pytest.raises(RuntimeError, match='must hold exactly'):
        snapshot.check_published(full)
    assert not (tmp_path / 'site').exists()


def test_full_export_still_round_trips_and_says_so(ready, tmp_path, monkeypatch):
    world(monkeypatch)
    out = tmp_path / 'published'
    manifest = snapshot.export_published(out)
    assert sorted(p.name for p in out.iterdir()) == ['MANIFEST.json', 'archive.json', 'overview.json']
    assert list(manifest) == ['schema', 'contents', 'file', 'sha256', 'exported_at', 'mode', 'actions_by_source', 'records',
                              'archive', 'reviewed', 'reviewed_at']
    assert manifest['schema'] == 'live-genesis-published/5' and manifest['contents'] == 'full' and manifest['file'] == 'overview.json'
    assert manifest['sha256'] == hashlib.sha256((out / 'overview.json').read_bytes()).hexdigest()
    assert SOCIETY_TEXT in (out / 'overview.json').read_text(encoding='utf-8')
    with pytest.raises(RuntimeError, match=r'Read overview\.json and archive\.json, then run'):
        snapshot.check_published(out)
    with pytest.raises(RuntimeError, match='overview.json and .*archive.json in full'):
        snapshot.approve_published(out)
    snapshot.approve_published(out, confirmed=True)
    assert snapshot.check_published(out)['contents'] == 'full'
    assert 'data mode' in snapshot._describe(manifest) and 'records in file' in snapshot._describe(manifest)
    edit_manifest(out, contents=KeyError)
    with pytest.raises(RuntimeError, match='does not say what the export holds'):
        snapshot.check_published(out)
    edit_manifest(out, contents='full')
    monkeypatch.setenv('LIVE_DB', str(tmp_path / 'no-such.sqlite'))
    info = snapshot.build_from_data(tmp_path / 'site', out)
    assert info['demo_only'] is False and (tmp_path / 'site' / 'overview.json').is_file() and (tmp_path / 'site' / 'stats.html').is_file()
    snapshot.build_from_data(tmp_path / 'only', out, archive_only=True)     # a full export may still be rendered as the archive alone
    assert 'overview.json' not in files_of(tmp_path / 'only') and (tmp_path / 'only' / 'archive' / 'index.html').is_file()


def test_archive_only_export_refuses_when_there_is_nothing_recorded(ready, tmp_path, monkeypatch):
    out = tmp_path / 'published'
    core.seed_agents()
    society_post()
    # No archive rows at all: a recorded society does not make an archive export possible.
    with pytest.raises(RuntimeError, match=r'holds no recorded answers.*nothing to review'):
        snapshot.export_published(out, archive_only=True)
    # Mock answers only (the adapter was replaced; nothing real is recorded).
    monkeypatch.setattr(engine, 'infer', fake('MOCKED-REPLY-TEXT'))
    assert archive.run_archive(max_usd=1)['mock']
    with pytest.raises(RuntimeError, match='holds no recorded answers'):
        snapshot.export_published(out, archive_only=True)
    assert not out.exists()
    # A real run that recorded no answer: not demo-only, and still nothing to publish.
    archive.lock()
    with core.connect() as cx:
        cx.execute("INSERT INTO archive_runs(number,kind,question_set,set_sha256,started_at) SELECT 1,'real',question_set,sha256,? FROM archive_sets",
                   (core.utcnow(),))
    payload = archive.public_payload()
    assert payload['demo_only'] is False and payload['answers'] == [] and len(payload['runs']) == 1
    with pytest.raises(RuntimeError, match='Answers Over Time: it holds no answers, so there is nothing to publish'):
        snapshot.export_published(out, archive_only=True)
    assert not out.exists()
    snapshot.export_published(tmp_path / 'full')                           # the full export is as before: the society is recorded data


def test_archive_only_export_refuses_scripted_demo_answers(ready, tmp_path):
    archive.demo_archive()
    assert archive.public_payload()['answers']
    with pytest.raises(RuntimeError, match='holds no recorded answers'):
        snapshot.export_published(tmp_path / 'published', archive_only=True)
    assert not (tmp_path / 'published').exists()


def test_archive_only_export_runs_the_guards_and_respects_an_existing_folder(ready, tmp_path, monkeypatch):
    world(monkeypatch)
    out = tmp_path / 'published'
    snapshot.export_published(out)                                          # a full export is there already
    with pytest.raises(RuntimeError, match='already holds an export. Pass --replace'):
        snapshot.export_published(out, archive_only=True)
    assert (out / 'overview.json').is_file()
    manifest = snapshot.export_published(out, replace=True, archive_only=True)
    assert files_of(out) == {'archive.json', 'MANIFEST.json'} and manifest['contents'] == 'archive' and manifest['reviewed'] is False
    snapshot.approve_published(out, confirmed=True)
    again = snapshot.export_published(out, replace=True)                    # and back: the new export starts unreviewed
    assert again['contents'] == 'full' and again['reviewed'] is False and (out / 'overview.json').is_file()
    with core.connect() as cx:      # a row written before a guard existed, simulated by going around classify()
        cx.execute("INSERT INTO archive_runs(number,kind,question_set,set_sha256,started_at) SELECT 2,'real',question_set,sha256,? FROM archive_sets",
                   (core.utcnow(),))
        archive._store(cx, 2, 'basic-1', 'Q01', 'Astra', model='some-model-1', source='api', state='answered',
                       text='Send it to someone@example.org', agent_id=1)
    with pytest.raises(RuntimeError, match='Content guards matched:\n  archive answers id 41, field text: looks like an email address'):
        snapshot.export_published(tmp_path / 'second', archive_only=True)
    assert not (tmp_path / 'second').exists()


def test_archive_only_export_command_line(ready, tmp_path, monkeypatch, capsys):
    world(monkeypatch)
    out, site = tmp_path / 'published', tmp_path / 'site'
    no_society(monkeypatch)
    assert run_main(monkeypatch, '--export', str(out), '--archive-only') == 0
    printed = capsys.readouterr().out
    assert 'NOT yet approved' in printed and 'Answers Over Time only' in printed and 'archive.json: 1 run(s)' in printed
    assert f"Next: read {out / 'archive.json'} in full" in printed and 'overview.json' not in printed and 'data mode' not in printed
    assert files_of(out) == {'archive.json', 'MANIFEST.json'}
    assert run_main(monkeypatch, '--export', str(out), '--archive-only') == 2
    assert 'already holds an export' in capsys.readouterr().err
    assert run_main(monkeypatch, '--export', str(out), '--archive-only', '--replace') == 0
    capsys.readouterr()
    # The kind is read from the manifest: --approve and --check take no --archive-only, and no step takes OUTDIR.
    for step in ('--approve', '--check'):
        assert run_main(monkeypatch, step, str(out), '--archive-only') == 2
        assert 'take no --archive-only' in capsys.readouterr().err
    assert run_main(monkeypatch, str(site), '--export', str(out), '--archive-only') == 2
    assert 'take no OUTDIR' in capsys.readouterr().err
    assert run_main(monkeypatch, '--check', str(out)) == 2
    assert 'Read archive.json, then run' in capsys.readouterr().err
    assert run_main(monkeypatch, '--approve', str(out)) == 2
    captured = capsys.readouterr()
    assert 'Approving means you read every answer in' in captured.out and 'agent-written text' not in captured.out
    assert 'overview.json' not in captured.out + captured.err and 'archive.json in full' in captured.err
    assert json.loads((out / 'MANIFEST.json').read_text(encoding='utf-8'))['reviewed'] is False
    assert run_main(monkeypatch, '--approve', str(out), '--i-have-read-it') == 0
    captured = capsys.readouterr()
    assert 'Approved at' in captured.out and 'overview.json' not in captured.out
    assert run_main(monkeypatch, '--check', str(out)) == 0
    printed = capsys.readouterr().out
    assert 'OK: export is intact and approved.' in printed and 'Answers Over Time only' in printed and 'overview.json' not in printed
    # What the workflow runs: --from-data published --sibling demo/ --archive-only.
    monkeypatch.setenv('LIVE_DB', str(tmp_path / 'no-such.sqlite'))
    assert run_main(monkeypatch, str(site), '--from-data', str(out)) == 2
    assert 'render it with --archive-only' in capsys.readouterr().err and not site.exists()
    assert run_main(monkeypatch, str(site), '--from-data', str(out), '--sibling', 'demo/', '--archive-only') == 0
    printed = capsys.readouterr().out
    assert 'rendered from approved data' in printed and 'INCLUDES NON-DEMO DATA' in printed and 'Archive only' in printed
    assert (site / 'archive' / 'index.html').is_file() and not (site / 'overview.json').exists() and not (site / 'stats.html').exists()
    assert not (site / 'post').exists() and not (site / 'profile').exists()


def test_full_export_command_line_is_unchanged(ready, tmp_path, monkeypatch, capsys):
    world(monkeypatch)
    out = tmp_path / 'published'
    assert run_main(monkeypatch, '--export', str(out)) == 0
    printed = capsys.readouterr().out
    assert f"Next: read {out / 'overview.json'} and {out / 'archive.json'} in full" in printed and 'data mode' in printed
    assert run_main(monkeypatch, '--approve', str(out), '--i-have-read-it') == 0
    printed = capsys.readouterr().out
    assert f"every agent-written text in {out / 'overview.json'} and every answer in {out / 'archive.json'}." in printed
    assert run_main(monkeypatch, '--check', str(out)) == 0
    assert json.loads((out / 'MANIFEST.json').read_text(encoding='utf-8'))['contents'] == 'full'
