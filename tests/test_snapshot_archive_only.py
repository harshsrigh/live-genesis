"""Reviewed export of Answers Over Time alone (`snapshot --export --archive-only`): archive.json and
MANIFEST.json, no overview.json, and the society tables are never read. Owner decision: the public site
is the chat-app comparison only, so this export holds the chat-app track ALONE (replies entered by hand)
and nothing of the model track, whatever the database holds. Offline; no model is called."""
import contextlib
import hashlib
import json
import re
import sqlite3

import pytest

from live_genesis import archive, archive_chatbots as chatbots, core, engine, snapshot
from test_archive import as_real, fake, ready, recorded_archive, retarget_archive, trending_file  # noqa: F401
from test_archive_chatbots import APPS, IDS, TODAY, config_file, filled
from test_archive_chatbots import retarget as retarget_chat
from test_published import symlinks_work
from test_snapshot import ARCHIVE_STATIC, assert_every_address_resolves, files_of, run_main

SOCIETY_TEXT = 'society-only-marker-violet-heron-42'
MODEL_TEXT = 'Model-only marker: seven grey herons.'       # what the recorded MODEL run answered; never in an archive-only export
CHAT_TEXT = 'PLACEHOLDER reply 1 of chatgpt.'
ARCHIVE_KEYS = ['schema', 'contents', 'file', 'exported_at', 'archive', 'reviewed', 'reviewed_at']
MODEL_KEYS = ('answers', 'answerers', 'questions', 'trending', 'runs', 'set_hash', 'question_set', 'groups', 'answers_by_source',
              'locked', 'locked_at', 'demo_only', 'system_line', 'changed_rule')
CHAT_COUNTS = {'runs': 1, 'answers': 12, 'apps': APPS, 'human_answers': 4, 'questions': 4}
# Exactly what a site built from such an export holds, outside static/: the chat-app track and nothing else.
CHAT_SITE = ({'index.html', '.nojekyll', 'archive.json', 'archive/index.html', 'archive/chatbots.html',
              'archive/data/answers.jsonl', 'archive/data/README.txt'}
             | {f'archive/c/{name}.html' for name in IDS} | {f'archive/data/{name}.json' for name in IDS}
             | {f'archive/card/{name}{suffix}.html' for name in IDS for suffix in ('', '-chatgpt', '-gemini', '-grok', '-human')})


def society_post():
    core.submit_action(1, {'action': 'post', 'topic': 'research', 'title': 'A society post', 'body': f'Private to the society: {SOCIETY_TEXT}.'})
    with core.connect(readonly=True) as cx:
        assert cx.execute('SELECT COUNT(*) FROM posts WHERE body LIKE ?', (f'%{SOCIETY_TEXT}%',)).fetchone()[0] == 1


def hand_entered(monkeypatch, tmp_path, reply=None):
    """One collection of chat-app replies and human answers, entered by hand (placeholders)."""
    config_file(tmp_path, monkeypatch)
    monkeypatch.setattr(chatbots, 'today', lambda: TODAY)
    chatbots.import_collection(filled(tmp_path, reply=reply))


def world(monkeypatch, tmp_path, chat_reply=None):
    """A recorded MODEL run, hand-entered chat-app replies and a society post. Only the chat-app replies
    may reach an archive-only export."""
    recorded_archive(monkeypatch, MODEL_TEXT)
    society_post()
    hand_entered(monkeypatch, tmp_path, chat_reply)
    assert any(a['text'] == MODEL_TEXT for a in archive.public_payload()['answers'])


def no_society(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('an archive-only export must not read the society')
    monkeypatch.setattr(snapshot, '_capture', refuse)
    monkeypatch.setattr(snapshot, 'overview', refuse)


def approved(monkeypatch, tmp_path, name='published'):
    world(monkeypatch, tmp_path)
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


def all_text(root):
    return ''.join(path.read_text(encoding='utf-8') for path in root.rglob('*') if path.suffix in ('.html', '.json', '.jsonl', '.txt'))


def test_archive_only_export_writes_two_files_the_chat_track_only_and_no_society_text(ready, tmp_path, monkeypatch):
    world(monkeypatch, tmp_path, chat_reply=lambda app, question: f'Café answer of {app} to {question}.')
    before = hashlib.sha256(ready.read_bytes()).hexdigest()
    no_society(monkeypatch)                                   # the society tables are not consulted at all
    out = tmp_path / 'published'
    manifest = snapshot.export_published(out, archive_only=True)
    assert hashlib.sha256(ready.read_bytes()).hexdigest() == before
    assert files_of(out) == {'archive.json', 'MANIFEST.json'} and sorted(p.name for p in out.iterdir()) == ['MANIFEST.json', 'archive.json']
    raw = (out / 'archive.json').read_bytes()
    assert raw.isascii() and b'Caf\\u00e9' in raw
    exported = json.loads(raw)
    # Exactly the chat-app block of the public payload under its own schema marker: nothing of the model track.
    payload = json.loads(json.dumps(archive.public_payload()))
    assert payload['answers'] and payload['runs'] and payload['answerers']                 # the database does hold a model run
    assert list(exported) == ['schema', 'chatbots'] == list(chatbots.SITE_KEYS)
    assert exported == {'schema': 'live-genesis-chat-archive/1', 'chatbots': payload['chatbots']} == chatbots.site_payload(payload)
    assert chatbots.SITE_SCHEMA == 'live-genesis-chat-archive/1' and not set(exported) & set(MODEL_KEYS)
    text = raw.decode('ascii')
    assert MODEL_TEXT not in text and 'model-a' not in text and '"answerer"' not in text and 'set_hash' not in text
    assert manifest == json.loads((out / 'MANIFEST.json').read_text(encoding='utf-8'))
    assert list(manifest) == ARCHIVE_KEYS                                              # no mode, records, actions_by_source or top-level sha256
    assert manifest['schema'] == snapshot.SCHEMA == 'live-genesis-published/5'
    assert manifest['contents'] == 'archive' and manifest['file'] == 'archive.json'
    assert manifest['reviewed'] is False and manifest['reviewed_at'] is None and manifest['exported_at']
    # The manifest counts the chat-app track and nothing else: no model run, answer source or set hash.
    assert manifest['archive'] == {'file': 'archive.json', 'chatbots': CHAT_COUNTS, 'sha256': hashlib.sha256(raw).hexdigest()}
    assert manifest['archive'] == {**snapshot._chat_counts(exported), 'sha256': hashlib.sha256(raw).hexdigest()}
    for name in files_of(out):
        text = (out / name).read_text(encoding='utf-8')
        assert SOCIETY_TEXT not in text and 'A society post' not in text and '"posts"' not in text and '"agents"' not in text, name
        assert MODEL_TEXT not in text, name


def test_archive_only_round_trip_renders_the_chat_track_and_nothing_else(ready, tmp_path, monkeypatch):
    world(monkeypatch, tmp_path)
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
    described = snapshot._describe(manifest)
    assert 'overview.json' not in described and 'Answers Over Time only: the chat-app track' in described
    assert 'no model answer is exported' in described and 'archive.json: 1 chat-app run(s), 12 chat-app answer row(s), 4 human answer row(s)' in described
    assert 'ENTERED BY HAND' in described and 'answers by source' not in described
    assert snapshot.published_non_ascii(out) == []
    # Rendering opens no database and reads no society data.
    no_society(monkeypatch)
    monkeypatch.setenv('LIVE_DB', str(tmp_path / 'no-such.sqlite'))
    with pytest.raises(RuntimeError, match='holds Answers Over Time only; render it with --archive-only'):
        snapshot.build_from_data(tmp_path / 'full-site', out)
    assert not (tmp_path / 'full-site').exists()
    site = tmp_path / 'site'
    info = snapshot.build_from_data(site, out, archive_only=True)
    assert info['demo_only'] is False and info['generated_at'] == manifest['exported_at'] and info['manifest'] == manifest
    written = files_of(site)
    assert written == CHAT_SITE | ARCHIVE_STATIC                                       # exactly the chat-app files
    assert not any(name.startswith(('archive/q/', 'archive/t/', 'archive/card/Q', 'archive/card/T', 'archive/data/Q', 'archive/data/T'))
                   for name in written)
    assert (site / 'archive.json').read_bytes() == (out / 'archive.json').read_bytes()  # the reviewed file, byte for byte
    assert list(json.loads((site / 'archive.json').read_text(encoding='utf-8'))) == ['schema', 'chatbots']
    assert_every_address_resolves(site)                                                # no link to a page that is not published
    page = (site / 'archive' / 'c' / 'C001.html').read_text(encoding='utf-8')
    assert CHAT_TEXT in page and 'AI-generated · typed in by hand · may be wrong' in page and 'Typed in by hand' in page
    assert 'Not affiliated with, sponsored by or endorsed by OpenAI, Google or xAI' in page and 'Read-only archive.' in page and 'No payments' in page
    assert 'scripted' not in page.lower() and not re.search(r'(?i)sample', page)
    everything = all_text(site)
    assert MODEL_TEXT not in everything and SOCIETY_TEXT not in everything
    for gone in ('models.html', 'trending.html', '>Models<', '>Trending<', 'Want the plain models', 'We asked it ourselves', 'asked-by-server',
                 'model-a', 'Q01', 'T001'):
        assert gone not in everything, gone
    lines = [json.loads(line) for line in (site / 'archive' / 'data' / 'answers.jsonl').read_text(encoding='utf-8').splitlines()]
    assert lines and {line['answerer_kind'] for line in lines} == {'chat-app', 'human'} and all(line['question_id'] in IDS for line in lines)
    # With the other snapshot named, the bar links it and nothing else is added.
    paired = tmp_path / 'paired'
    snapshot.build_from_data(paired, out, sibling='demo/', archive_only=True)
    assert files_of(paired) == written
    assert (paired / 'archive' / 'index.html').read_text(encoding='utf-8').count('<a href="../demo/archive/index.html">Sample site</a>') == 1
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
    refused('reviewed is not true or false')
    edit_manifest(out, reviewed=False, reviewed_at=None)
    with pytest.raises(RuntimeError, match='not approved'):
        snapshot.check_published(out)
    (out / 'MANIFEST.json').write_bytes(good['MANIFEST.json'])
    # archive.json itself: every rule still runs on the chat-app track.
    assert CHAT_TEXT.encode() in good['archive.json']
    (out / 'archive.json').write_bytes(good['archive.json'].replace(CHAT_TEXT.encode(), b'PLACEHOLDER reply 9 of chatgpt.'))
    refused('archive.json does not match the sha256')
    (out / 'archive.json').unlink()
    refused('archive.json is missing')
    block = lambda d: d['chatbots']                                   # noqa: E731
    retarget_chat(out, lambda d: block(d)['answers'][0].update(text='Write to someone@example.org'))
    refused('Content guards matched:\n  archive chatbots id 1, field text: looks like an email address')
    retarget_chat(out, lambda d: (block(d)['runs'][0].update(demo=True), [a.update(source='demo') for a in block(d)['answers'] + block(d)['human_answers']]))
    refused('scripted chat-app answers')
    retarget_chat(out, lambda d: block(d)['answers'][0].update(source='demo'))
    refused("an answer's source does not fit its run")
    retarget_chat(out, lambda d: block(d)['apps'][0].update(name='Another name'))
    refused('does not carry the name, maker and attribution')
    retarget_chat(out, lambda d: (block(d)['answers'].clear(), block(d)['human_answers'].clear()))
    refused('archive.json is refused: it holds no answers')
    manifest = json.loads(good['MANIFEST.json']); manifest['archive']['chatbots']['answers'] = 11
    (out / 'MANIFEST.json').write_text(json.dumps(manifest))
    refused('counts in the manifest do not match archive.json')
    # A count the code does not write is refused as well: the block is compared whole.
    manifest = json.loads(good['MANIFEST.json']); manifest['archive']['answers_by_source'] = {'engine': 40}
    (out / 'MANIFEST.json').write_text(json.dumps(manifest))
    refused('counts in the manifest do not match archive.json')
    data = json.loads(good['archive.json']); data['chatbots']['answers'][0]['text'] = 'café'
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


def test_full_export_still_round_trips_and_its_archive_only_site_has_no_model_track(ready, tmp_path, monkeypatch):
    world(monkeypatch, tmp_path)
    out = tmp_path / 'published'
    manifest = snapshot.export_published(out)
    assert sorted(p.name for p in out.iterdir()) == ['MANIFEST.json', 'archive.json', 'overview.json']
    assert list(manifest) == ['schema', 'contents', 'file', 'sha256', 'exported_at', 'mode', 'actions_by_source', 'records',
                              'archive', 'reviewed', 'reviewed_at']
    assert manifest['schema'] == 'live-genesis-published/5' and manifest['contents'] == 'full' and manifest['file'] == 'overview.json'
    assert manifest['sha256'] == hashlib.sha256((out / 'overview.json').read_bytes()).hexdigest()
    assert SOCIETY_TEXT in (out / 'overview.json').read_text(encoding='utf-8')
    # The full export is unchanged: its archive.json is the whole public payload, model answers included.
    exported = json.loads((out / 'archive.json').read_text(encoding='utf-8'))
    assert exported == json.loads(json.dumps(archive.public_payload())) and exported['schema'] == 'live-genesis-archive/5'
    assert manifest['archive']['answers_by_source'] == {'engine': 40, 'api': 0, 'demo': 0, 'mock': 0} and manifest['archive']['runs'] == 1
    assert manifest['archive']['chatbots'] == CHAT_COUNTS and MODEL_TEXT in (out / 'archive.json').read_text(encoding='utf-8')
    with pytest.raises(RuntimeError, match=r'Read overview\.json and archive\.json, then run'):
        snapshot.check_published(out)
    with pytest.raises(RuntimeError, match='overview.json and .*archive.json in full'):
        snapshot.approve_published(out)
    snapshot.approve_published(out, confirmed=True)
    assert snapshot.check_published(out)['contents'] == 'full'
    assert 'data mode' in snapshot._describe(manifest) and 'records in file' in snapshot._describe(manifest)
    assert 'archive.json: 1 run(s), answers by source: engine 40' in snapshot._describe(manifest)
    edit_manifest(out, contents=KeyError)
    with pytest.raises(RuntimeError, match='does not say what the export holds'):
        snapshot.check_published(out)
    edit_manifest(out, contents='full')
    monkeypatch.setenv('LIVE_DB', str(tmp_path / 'no-such.sqlite'))
    info = snapshot.build_from_data(tmp_path / 'site', out)
    assert info['demo_only'] is False and (tmp_path / 'site' / 'overview.json').is_file() and (tmp_path / 'site' / 'stats.html').is_file()
    # A full site keeps every model page and the whole archive.json.
    whole = files_of(tmp_path / 'site')
    assert {'archive/models.html', 'archive/trending.html', 'archive/q/Q01.html', 'archive/card/Q01.html', 'archive/data/Q01.json',
            'archive/c/C001.html', 'archive/chatbots.html'} <= whole
    assert (tmp_path / 'site' / 'archive.json').read_bytes() == (out / 'archive.json').read_bytes()
    assert MODEL_TEXT in (tmp_path / 'site' / 'archive' / 'q' / 'Q01.html').read_text(encoding='utf-8')
    # A full export may still be rendered as the public site: the whole export is checked, the chat-app track alone is written.
    only = tmp_path / 'only'
    snapshot.build_from_data(only, out, archive_only=True)
    assert files_of(only) == CHAT_SITE | ARCHIVE_STATIC
    site_data = json.loads((only / 'archive.json').read_text(encoding='utf-8'))
    assert site_data == {'schema': chatbots.SITE_SCHEMA, 'chatbots': exported['chatbots']}
    assert (only / 'archive.json').read_bytes() == snapshot._encode(site_data)
    assert MODEL_TEXT not in all_text(only) and SOCIETY_TEXT not in all_text(only) and CHAT_TEXT in all_text(only)
    assert_every_address_resolves(only)


def test_archive_only_export_refuses_when_no_chat_reply_was_entered_by_hand(ready, tmp_path, monkeypatch):
    out = tmp_path / 'published'
    core.seed_agents()
    society_post()
    nothing = r'holds no chat-app reply entered by hand.*leaves the model track out.*nothing to review'
    # No archive rows at all: a recorded society does not make an archive export possible.
    with pytest.raises(RuntimeError, match=nothing):
        snapshot.export_published(out, archive_only=True)
    # Mock answers only (the adapter was replaced; nothing real is recorded).
    monkeypatch.setattr(engine, 'infer', fake('MOCKED-REPLY-TEXT'))
    assert archive.run_archive(max_usd=1)['mock']
    with pytest.raises(RuntimeError, match=nothing):
        snapshot.export_published(out, archive_only=True)
    assert not out.exists()
    # A real MODEL run with every answer recorded: still nothing for this export, which leaves the model track out.
    archive.lock()
    as_real(monkeypatch, MODEL_TEXT)
    assert archive.run_archive(max_usd=1)['status'] == 'complete'
    payload = archive.public_payload()
    assert payload['demo_only'] is False and len(payload['answers']) == 40 and 'chatbots' not in payload
    with pytest.raises(RuntimeError, match=nothing):
        snapshot.export_published(out, archive_only=True)
    assert not out.exists()
    assert run_main(monkeypatch, '--export', str(out), '--archive-only') == 2 and not out.exists()
    full = snapshot.export_published(tmp_path / 'full')                    # the full export is as before: it carries the model answers
    assert full['archive']['answers_by_source']['engine'] == 40 and 'chatbots' not in full['archive']
    # An export folder whose archive.json holds a run and no answer row is refused by the check as well.
    hand_entered(monkeypatch, tmp_path)
    snapshot.export_published(out, archive_only=True)
    snapshot.approve_published(out, confirmed=True)
    retarget_chat(out, lambda d: (d['chatbots']['answers'].clear(), d['chatbots']['human_answers'].clear()))
    with pytest.raises(RuntimeError, match='archive.json is refused: it holds no answers, so there is nothing to publish'):
        snapshot.check_published(out)


def test_archive_only_export_refuses_scripted_demo_answers(ready, tmp_path):
    archive.demo_archive()
    payload = archive.public_payload()
    assert payload['answers'] and payload['chatbots']['answers'] and all(run['demo'] for run in payload['chatbots']['runs'])
    with pytest.raises(RuntimeError, match='holds no chat-app reply entered by hand'):
        snapshot.export_published(tmp_path / 'published', archive_only=True)
    assert not (tmp_path / 'published').exists()


def test_archive_only_export_runs_the_guards_and_respects_an_existing_folder(ready, tmp_path, monkeypatch):
    world(monkeypatch, tmp_path)
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
    with core.connect() as cx:      # a MODEL row written before a guard existed, simulated by going around classify()
        cx.execute("INSERT INTO archive_runs(number,kind,question_set,set_sha256,started_at,status,finished_at) "
                   "SELECT 2,'real',question_set,sha256,?,'complete',? FROM archive_sets", (core.utcnow(), core.utcnow()))
        archive._store(cx, 2, 'basic-1', 'Q01', 'Astra', model='some-model-1', source='api', state='answered',
                       text='Send it to someone@example.org', agent_id=1)
    # The full export carries that row, so the guards stop it; the archive-only export does not carry it at all.
    with pytest.raises(RuntimeError, match='Content guards matched:\n  archive answers id 41, field text: looks like an email address'):
        snapshot.export_published(tmp_path / 'second')
    assert not (tmp_path / 'second').exists()
    snapshot.export_published(tmp_path / 'second', archive_only=True)
    assert 'someone@example.org' not in (tmp_path / 'second' / 'archive.json').read_text(encoding='utf-8')
    # A chat-app reply that matches a guard stops the archive-only export (simulated: the import itself withholds such a reply).
    payload = archive.public_payload()
    payload['chatbots']['answers'][0]['text'] = 'Send it to someone@example.org'
    monkeypatch.setattr(snapshot, '_capture_archive', lambda: payload)
    with pytest.raises(RuntimeError, match='Content guards matched:\n  archive chatbots id 1, field text: looks like an email address'):
        snapshot.export_published(tmp_path / 'third', archive_only=True)
    assert not (tmp_path / 'third').exists()


def test_archive_only_export_command_line(ready, tmp_path, monkeypatch, capsys):
    world(monkeypatch, tmp_path)
    out, site = tmp_path / 'published', tmp_path / 'site'
    no_society(monkeypatch)
    assert run_main(monkeypatch, '--export', str(out), '--archive-only') == 0
    printed = capsys.readouterr().out
    assert 'NOT yet approved' in printed and 'Answers Over Time only: the chat-app track' in printed
    assert 'archive.json: 1 chat-app run(s), 12 chat-app answer row(s), 4 human answer row(s)' in printed and 'ENTERED BY HAND' in printed
    assert 'Answers Over Time run' not in printed and 'answers by source' not in printed          # no model run is listed: none is exported
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
    assert 'rendered from approved data' in printed and 'INCLUDES NON-DEMO DATA' in printed
    assert 'Archive only: the chat-app comparison' in printed and 'No model-track page or data' in printed
    assert files_of(site) == CHAT_SITE | ARCHIVE_STATIC
    assert not (site / 'overview.json').exists() and not (site / 'stats.html').exists()
    assert not (site / 'post').exists() and not (site / 'profile').exists()


def test_full_export_command_line_is_unchanged(ready, tmp_path, monkeypatch, capsys):
    world(monkeypatch, tmp_path)
    out = tmp_path / 'published'
    assert run_main(monkeypatch, '--export', str(out)) == 0
    printed = capsys.readouterr().out
    assert f"Next: read {out / 'overview.json'} and {out / 'archive.json'} in full" in printed and 'data mode' in printed
    assert 'archive.json: 1 run(s), answers by source: engine 40' in printed and 'Answers Over Time run 1: complete, 40 answer(s) recorded' in printed
    assert run_main(monkeypatch, '--approve', str(out), '--i-have-read-it') == 0
    printed = capsys.readouterr().out
    assert f"every agent-written text in {out / 'overview.json'} and every answer in {out / 'archive.json'}." in printed
    assert run_main(monkeypatch, '--check', str(out)) == 0
    assert json.loads((out / 'MANIFEST.json').read_text(encoding='utf-8'))['contents'] == 'full'


# ---- review findings: exact keys, outside agents' answers, unfinished runs, manifest hygiene ----------

def second_run(close=True, text='Twelve.'):
    """Model run 2, holding one answer an outside agent submitted (source api); left `running` unless closed."""
    with core.connect() as cx:
        cx.execute("INSERT INTO archive_runs(number,kind,question_set,set_sha256,started_at) SELECT 2,'real',question_set,sha256,? FROM archive_sets",
                   (core.utcnow(),))
        archive._store(cx, 2, 'basic-1', 'Q01', 'Astra', model='some-model-1', source='api', state='answered', text=text, agent_id=1)
        if close:
            cx.execute("UPDATE archive_runs SET status='complete',finished_at=? WHERE id=2", (core.utcnow(),))


def every_gate_refuses(out, tmp_path, match, good, full=False):
    """check, approve and render all refuse with `match`; then the good files are put back."""
    for call in (lambda: snapshot.check_published(out), lambda: snapshot.approve_published(out, confirmed=True),
                 lambda: snapshot.build_from_data(tmp_path / 'site', out, archive_only=not full)):
        with pytest.raises(RuntimeError, match=match) as err:
            call()
        assert full or 'overview.json' not in str(err.value)
        assert SOCIETY_TEXT not in str(err.value)
    assert not (tmp_path / 'site').exists()
    for entry in list(out.iterdir()):
        entry.unlink()
    for name, content in good.items():
        (out / name).write_bytes(content)
    assert snapshot.check_published(out)['reviewed'] is True


def test_archive_only_archive_json_must_hold_exactly_the_chat_track_keys(ready, tmp_path, monkeypatch):
    out = approved(monkeypatch, tmp_path)
    good = {name: (out / name).read_bytes() for name in ('archive.json', 'MANIFEST.json')}
    whole = json.loads(json.dumps(archive.public_payload()))
    assert chatbots.site_problem(json.loads(good['archive.json'])) is None and chatbots.site_problem(whole) == 'unknown schema'

    def refused(edit, match):
        retarget_chat(out, edit)                          # the manifest's hash and counts agree with the edited file
        every_gate_refuses(out, tmp_path, match, good)

    planted = [{'id': 1, 'body': f'Private to the society: {SOCIETY_TEXT}.'}]
    refused(lambda d: d.update(posts=planted), 'archive.json is refused: top level: unexpected key posts')
    # Nothing of the model track may be added to the file: every key of the full payload is refused by name.
    for key in MODEL_KEYS:
        refused(lambda d, key=key: d.update({key: whole[key]}), f'archive.json is refused: top level: unexpected key {key}')
    # The whole public payload in place of the file (what an earlier version exported) is refused as a whole.
    refused(lambda d: (d.clear(), d.update(whole)), 'it holds the model track; an export of Answers Over Time only holds the chat-app track alone')
    refused(lambda d: d.update(schema='live-genesis-archive/5'), 'it holds the model track')
    refused(lambda d: d.update(schema='live-genesis-archive/3'), 'it holds the model track')
    refused(lambda d: d.update(schema='live-genesis-chat-archive/2'), 'archive.json is refused: unknown schema')
    refused(lambda d: d.pop('schema'), 'archive.json is refused: unknown schema')
    refused(lambda d: d.pop('chatbots'), 'top level: key chatbots is missing')
    refused(lambda d: d.update(chatbots=None), 'chatbots: a key is missing or has the wrong type')
    # The name of an unknown key is shown only when it is a plain word; its value never is.
    refused(lambda d: d.update({f'<b>{SOCIETY_TEXT}</b>': 1}), r'top level: unexpected key \(its name is not a plain word\)')
    # Inside the block: closed keys on every object.
    refused(lambda d: d['chatbots'].update(posts=planted), 'chatbots: unexpected key posts')
    refused(lambda d: d['chatbots'].update(answerers=['model-a']), 'chatbots: unexpected key answerers')
    refused(lambda d: d['chatbots']['answers'][0].update(note=SOCIETY_TEXT), 'chat-app answer id 1: unexpected key note')
    refused(lambda d: d['chatbots']['answers'][0].update(model='some-model-1'), 'chat-app answer id 1: unexpected key model')
    refused(lambda d: d['chatbots']['runs'][0].update(operator=SOCIETY_TEXT), 'chat-app run 1: unexpected key operator')
    refused(lambda d: d['chatbots']['apps'][0].update(extra='x'), 'a chat app: unexpected key extra')
    refused(lambda d: d['chatbots']['human_answers'][0].update(username='someone'), 'unexpected key username')
    # A question reworded in the file is not the question of the checked-in configuration.
    refused(lambda d: d['chatbots']['questions'][0].update(hook='Vote now'), 'chat-app question C001 is not the question')
    # Not a JSON object at all.
    (out / 'archive.json').write_bytes(b'[]')
    edit_manifest(out, archive={'file': 'archive.json', 'sha256': hashlib.sha256(b'[]').hexdigest()})
    every_gate_refuses(out, tmp_path, 'archive.json is refused: top level is not an object', good)


def test_full_export_archive_json_is_key_closed_too(ready, tmp_path, monkeypatch):
    world(monkeypatch, tmp_path)
    out = tmp_path / 'published'
    snapshot.export_published(out)
    snapshot.approve_published(out, confirmed=True)
    good = {name: (out / name).read_bytes() for name in ('overview.json', 'archive.json', 'MANIFEST.json')}
    assert archive.closed_problem(archive.public_payload()) is None and archive.set_hash_problem(archive.public_payload()) is None
    for edit, match in ((lambda d: d.update(posts=[{'id': 1}]), 'archive.json is refused: top level: unexpected key posts'),
                        (lambda d: d['answers'][0].update(note='x'), 'answer id 1: unexpected key note'),
                        (lambda d: d['answers'][0].update(samples=1), 'sample counts that do not belong'),
                        (lambda d: d['runs'][0].update(operator='x'), 'run 1: unexpected key operator'),
                        (lambda d: d['trending'].update(posts=[]), 'trending: unexpected key posts'),
                        (lambda d: d['answerers'].append('ghost'), "answerers is not the list of the answers' own answerers"),
                        (lambda d: d['answers_by_source'].update(engine=39), 'answers_by_source does not match the answers'),
                        (lambda d: d.update(system_line='x'), 'system_line or changed_rule'),
                        (lambda d: d['questions'][0].update(text='What is the capital of Atlantis?'), 'set_hash does not match'),
                        (lambda d: d['groups'][0].update(why='Another reason.'), 'set_hash does not match the questions'),
                        (lambda d: d['trending'].update(question_set='other'), 'trending: set_hash does not match the questions')):
        retarget_archive(out, edit)
        every_gate_refuses(out, tmp_path, match, good, full=True)
    # A full export's archive.json is the whole payload: the chat-app-only form is not accepted in its place.
    raw = snapshot._encode(chatbots.site_payload(json.loads(good['archive.json'])))
    manifest = json.loads(good['MANIFEST.json'])
    manifest['archive']['sha256'] = hashlib.sha256(raw).hexdigest()
    (out / 'archive.json').write_bytes(raw)
    (out / 'MANIFEST.json').write_text(json.dumps(manifest, indent=1))
    every_gate_refuses(out, tmp_path, 'archive.json is refused: key question_set is missing or has the wrong type', good, full=True)


def test_trending_set_hash_is_recomputed_for_the_first_lock(ready, tmp_path, monkeypatch):
    trending_file(tmp_path, monkeypatch, count=2)
    core.seed_agents()
    archive.lock()
    archive.lock('trending')
    as_real(monkeypatch, 'Seven.')
    archive.run_archive(max_usd=1)
    payload = archive.public_payload()
    assert payload['trending']['version'] == 1 and archive.set_hash_problem(payload) is None
    out = tmp_path / 'published'
    snapshot.export_published(out)                            # the model track is part of a full export only
    snapshot.approve_published(out, confirmed=True)
    good = {name: (out / name).read_bytes() for name in ('overview.json', 'archive.json', 'MANIFEST.json')}
    retarget_archive(out, lambda d: d['trending']['questions'][0].update(rationale='Another reason.'))
    every_gate_refuses(out, tmp_path, 'trending: set_hash does not match the questions', good, full=True)
    retarget_archive(out, lambda d: d['trending']['questions'][0].update(note='x'))
    every_gate_refuses(out, tmp_path, 'trending question T001: unexpected key note', good, full=True)
    # A later lock chains over the earlier ones, whose boundaries are not published: it is not recomputed.
    trending_file(tmp_path, monkeypatch, count=3)
    archive.lock('trending')
    later = archive.public_payload()
    assert later['trending']['version'] == 2 and archive.set_hash_problem(later) is None
    later['trending']['questions'][0]['rationale'] = 'Another reason.'
    assert archive.set_hash_problem(later) is None


def test_an_outside_agents_answer_is_in_the_full_export_only_and_the_summary_says_so(ready, tmp_path, monkeypatch, capsys):
    world(monkeypatch, tmp_path)
    out = tmp_path / 'published'
    lines = []
    assert 'outside agents' not in snapshot._describe(snapshot.export_published(out, notes=lines))
    assert lines == ['  Answers Over Time run 1: complete, 40 answer(s) recorded']
    second_run()
    lines = []
    manifest = snapshot.export_published(out, replace=True, notes=lines)
    assert lines == ['  Answers Over Time run 1: complete, 40 answer(s) recorded', '  Answers Over Time run 2: complete, 1 answer(s) recorded']
    assert manifest['archive']['answers_by_source'] == {'engine': 40, 'api': 1, 'demo': 0, 'mock': 0}
    outside = [a for a in json.loads((out / 'archive.json').read_text(encoding='utf-8'))['answers'] if a['source'] == 'api']
    assert len(outside) == 1 and outside[0]['answerer'] == 'Astra' and outside[0]['model'] == 'some-model-1' and outside[0]['text'] == 'Twelve.'
    assert all(outside[0][key] is None for key in archive.PUBLIC_CALL_FIELDS)
    note = ("NOTE: 1 answer(s) in archive.json were submitted by outside agents over the API. Each one carries that agent's "
            "name and its self-declared model.")
    assert note in snapshot._describe(manifest)
    assert run_main(monkeypatch, '--approve', str(out), '--i-have-read-it') == 0
    assert note in capsys.readouterr().out
    assert run_main(monkeypatch, '--check', str(out)) == 0
    assert note in capsys.readouterr().out
    assert run_main(monkeypatch, '--export', str(out), '--replace') == 0
    printed = capsys.readouterr().out
    assert note in printed and 'Answers Over Time run 2: complete, 1 answer(s) recorded' in printed
    # The archive-only export of the same database carries neither that answer nor a word about it.
    lines = []
    only = snapshot.export_published(tmp_path / 'only', archive_only=True, notes=lines)
    assert lines == [] and 'outside agents' not in snapshot._describe(only)
    raw = (tmp_path / 'only' / 'archive.json').read_text(encoding='utf-8')
    assert 'Twelve.' not in raw and 'Astra' not in raw and 'some-model-1' not in raw and MODEL_TEXT not in raw


def test_full_export_refuses_a_run_that_is_still_running_and_the_archive_only_export_does_not_carry_it(ready, tmp_path, monkeypatch, capsys):
    world(monkeypatch, tmp_path)
    out = tmp_path / 'published'
    snapshot.export_published(out)
    snapshot.approve_published(out, confirmed=True)
    before = {path.name: path.read_bytes() for path in out.iterdir()}
    second_run(close=False)
    assert archive.run_states() == [{'number': 1, 'answers': 40, 'status': 'complete'}, {'number': 2, 'answers': 1, 'status': 'running'}]
    digest = hashlib.sha256(ready.read_bytes()).hexdigest()
    running = r'run 2 is still marked running: an archive-run is in progress, or it was killed.*pass --include-unfinished'
    with pytest.raises(RuntimeError, match=running):
        snapshot.export_published(out, replace=True)
    with pytest.raises(RuntimeError, match=running):
        snapshot.export_published(tmp_path / 'fresh')
    # A refused --replace removed nothing: the earlier approved export is there byte for byte.
    assert {path.name: path.read_bytes() for path in out.iterdir()} == before and not (tmp_path / 'fresh').exists()
    assert snapshot.check_published(out)['reviewed'] is True
    assert run_main(monkeypatch, '--export', str(out), '--replace') == 2
    assert 'pass --include-unfinished to export it as it stands' in capsys.readouterr().err
    assert {path.name: path.read_bytes() for path in out.iterdir()} == before
    for step in (('--check', str(out)), ('--approve', str(out)), (str(tmp_path / 'site'), '--from-data', str(out), '--archive-only')):
        assert run_main(monkeypatch, *step, '--include-unfinished') == 2
        assert '--include-unfinished is used with --export only' in capsys.readouterr().err
    # The override exports the run as it stands and says so.
    assert run_main(monkeypatch, '--export', str(out), '--replace', '--include-unfinished') == 0
    printed = capsys.readouterr().out
    assert '  Answers Over Time run 1: complete, 40 answer(s) recorded\n' in printed
    assert '  Answers Over Time run 2: running, 1 answer(s) recorded (UNFINISHED: exported as it stands)\n' in printed
    assert len(json.loads((out / 'archive.json').read_text(encoding='utf-8'))['runs']) == 2
    assert snapshot.check_published(out, require_reviewed=False)['reviewed'] is False
    # The archive-only export holds no model run, so an unfinished one neither stops it nor is listed.
    lines = []
    only = snapshot.export_published(tmp_path / 'only', archive_only=True, notes=lines)
    assert only['contents'] == 'archive' and lines == [] and only['archive']['chatbots'] == CHAT_COUNTS
    assert run_main(monkeypatch, '--export', str(tmp_path / 'only'), '--archive-only', '--replace') == 0
    assert 'Answers Over Time run' not in capsys.readouterr().out
    assert hashlib.sha256(ready.read_bytes()).hexdigest() == digest             # the database was only read


def test_a_run_that_stopped_early_is_exported_in_full_and_its_status_is_shown(ready, tmp_path, monkeypatch):
    core.seed_agents()
    archive.lock()
    as_real(monkeypatch, lambda model, prompt, n: RuntimeError('provider failed') if n == 3 else 'Seven.')
    assert archive.run_archive(max_usd=1)['status'] == 'incomplete'
    lines = []
    manifest = snapshot.export_published(tmp_path / 'published', notes=lines)
    assert lines == ['  Answers Over Time run 1: incomplete, 2 answer(s) recorded']
    assert manifest['archive']['answers_by_source']['engine'] == 2


def test_archive_only_export_reads_no_society_table(ready, tmp_path, monkeypatch):
    world(monkeypatch, tmp_path)
    read, modes, real = set(), [], core.connect

    @contextlib.contextmanager
    def watched(readonly=False):
        modes.append(readonly)
        with real(readonly=readonly) as cx:
            def note(action, first, second, database, source):
                if action == sqlite3.SQLITE_READ:
                    read.add(first)
                return sqlite3.SQLITE_OK
            cx.set_authorizer(note)
            yield cx

    with monkeypatch.context() as patch:
        patch.setattr(core, 'connect', watched)
        snapshot.export_published(tmp_path / 'published', archive_only=True)
    assert modes and all(modes)                                              # every connection was read-only
    society = {'agents', 'posts', 'documents', 'document_revisions', 'proposals', 'votes', 'games', 'events', 'memories',
               'credit_ledger', 'tasks', 'task_responses', 'actions', 'usage', 'audit_log'}
    assert read and all(name in ('sqlite_master', 'sqlite_schema') or name.startswith('archive_') for name in read), sorted(read)
    assert not read & society and 'archive_chatbot_answers' in read and 'archive_chatbot_runs' in read
    # The same watch on a full export does see the society, so the watch works.
    read.clear()
    with monkeypatch.context() as patch:
        patch.setattr(core, 'connect', watched)
        snapshot.export_published(tmp_path / 'full')
    assert {'posts', 'agents'} <= read


def test_archive_kind_refuses_links_in_place_of_its_files(ready, tmp_path, monkeypatch):
    out = approved(monkeypatch, tmp_path)
    if not symlinks_work(tmp_path):
        pytest.skip('symbolic links cannot be created here')
    good = {name: (out / name).read_bytes() for name in ('archive.json', 'MANIFEST.json')}
    for name in ('archive.json', 'MANIFEST.json'):
        outside = tmp_path / f'outside-{name}'
        outside.write_bytes(good[name])                     # the link's target hashes correctly; it is refused all the same
        (out / name).unlink()
        (out / name).symlink_to(outside)
        every_gate_refuses(out, tmp_path, re.escape(name) + r' is missing or unreadable \(must be a regular file\)', good)
        assert outside.read_bytes() == good[name] and not (out / name).is_symlink()


def test_manifest_review_fields_and_timestamps_are_checked(ready, tmp_path, monkeypatch):
    out = approved(monkeypatch, tmp_path)
    good = {name: (out / name).read_bytes() for name in ('archive.json', 'MANIFEST.json')}
    stamp = json.loads(good['MANIFEST.json'])['exported_at']
    wide, arabic = stamp.replace('2026', '２０２６'), stamp.translate({ord(str(n)): 0x0660 + n for n in range(10)})
    assert wide != stamp and re.fullmatch(r'\d{4}-.*', wide) and re.fullmatch(r'\d{4}-\d{2}-\d{2}T.*', arabic)
    for value in ('yes', 'true', 1, 0, None, [True]):
        edit_manifest(out, reviewed=value)
        every_gate_refuses(out, tmp_path, "the manifest's reviewed is not true or false", good)
    for value in (None, 5, True, 'yesterday', '', '<b>2026</b>', wide, arabic, stamp + '\n'):
        edit_manifest(out, reviewed_at=value)
        every_gate_refuses(out, tmp_path, r"reviewed_at is not a timestamp \(it must be one when reviewed is true\)", good)
    for value in (wide, arabic, stamp + '\n', ' ' + stamp):
        edit_manifest(out, exported_at=value)
        every_gate_refuses(out, tmp_path, 'exported_at is not a timestamp', good)
    # Not reviewed: reviewed_at is null or a timestamp, nothing else.
    edit_manifest(out, reviewed=False, reviewed_at='soon')
    with pytest.raises(RuntimeError, match='reviewed_at is not a timestamp or null'):
        snapshot.check_published(out, require_reviewed=False)
    edit_manifest(out, reviewed=False, reviewed_at=None)
    assert snapshot.check_published(out, require_reviewed=False)['reviewed'] is False
    with pytest.raises(RuntimeError, match='not approved'):
        snapshot.check_published(out)
    # The full kind gets the same three checks.
    full = tmp_path / 'full'
    snapshot.export_published(full)
    snapshot.approve_published(full, confirmed=True)
    kept = (full / 'MANIFEST.json').read_bytes()
    for changes, match in (({'reviewed': 'yes'}, 'reviewed is not true or false'), ({'reviewed': KeyError}, 'reviewed is not true or false'),
                           ({'reviewed_at': None}, 'reviewed_at is not a timestamp'), ({'reviewed_at': wide}, 'reviewed_at is not a timestamp'),
                           ({'exported_at': arabic}, 'exported_at is not a timestamp')):
        edit_manifest(full, **changes)
        with pytest.raises(RuntimeError, match=match):
            snapshot.check_published(full)
        (full / 'MANIFEST.json').write_bytes(kept)
    assert snapshot.check_published(full)['reviewed'] is True


def test_refusals_before_the_kind_is_known_do_not_name_overview_json(ready, tmp_path, monkeypatch):
    out = approved(monkeypatch, tmp_path)
    good = {name: (out / name).read_bytes() for name in ('archive.json', 'MANIFEST.json')}
    (out / 'MANIFEST.json').write_text('{not json')
    every_gate_refuses(out, tmp_path, r'MANIFEST\.json is missing or unreadable \(JSONDecodeError\)', good)
    (out / 'MANIFEST.json').write_text('{"schema": 1, "schema": 2}')
    every_gate_refuses(out, tmp_path, r'MANIFEST\.json is missing or unreadable \(_Malformed\)', good)
    (out / 'MANIFEST.json').unlink()
    every_gate_refuses(out, tmp_path, r'MANIFEST\.json is missing or unreadable \(must be a regular file\)', good)
    (out / 'notes.txt').write_text('x')
    every_gate_refuses(out, tmp_path, r'unexpected entries \(1\); an export folder must hold only its data file\(s\) and MANIFEST\.json', good)
    with pytest.raises(RuntimeError, match=r'the export folder is missing or unreadable \(not a plain folder\)') as err:
        snapshot.check_published(tmp_path / 'absent')
    assert 'overview.json' not in str(err.value)
