"""The Windows operator launcher, offline: a saved catalog, scripted keyboard input, a stubbed SDK. No network, no
key, no real database. The key below is a sentinel that is searched for in every printed line and every table."""
import json
import os
from pathlib import Path

import pytest

from live_genesis import archive, operator_launcher as launcher

from test_archive import ready, rows  # noqa: F401
from test_archive_endpoints import every_cell, fake_sdk

KEY = 'SENTINEL-LAUNCHER-KEY-5e2a'
ALPHA, BETA = 'scripted/alpha:free', 'scripted/beta:free'


def row(model, **prices):
    return {'id': model, 'pricing': {'prompt': '0', 'completion': '0', **prices}}


@pytest.fixture
def desk(ready, tmp_path, monkeypatch):
    monkeypatch.setattr(os, 'environ', os.environ.copy())        # the launcher edits this process's environment: undo it
    for name in ('OPENROUTER_API_KEY', 'LIVE_ALLOW_PAID_CALLS', 'LIVE_ARCHIVE_ENDPOINTS'):
        os.environ.pop(name, None)
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps({'data': [row(ALPHA), row(BETA), row('vendor/paid-model', prompt='0.5'),
                                            row('openrouter/free')]}), encoding='utf-8')
    monkeypatch.setattr(archive.time, 'sleep', lambda seconds: None)    # the endpoint's 4 s pause between calls is real; not in tests
    return catalog, tmp_path / 'collector.sqlite'


def drive(monkeypatch, capsys, catalog, db, answers, *flags, key=KEY):
    typed = iter(answers)
    secrets = []

    def secret(prompt):
        secrets.append(prompt)
        return key
    monkeypatch.setattr(launcher.getpass, 'getpass', secret)
    args = launcher.argparse.Namespace(db=str(db), catalog=str(catalog), preview_only='--preview-only' in flags)
    code = launcher.run(args, ask=lambda prompt: next(typed), secret=secret)
    return code, capsys.readouterr().out, secrets


def test_full_run_needs_the_exact_confirmation_and_shows_the_saved_results(desk, monkeypatch, capsys):
    catalog, db = desk
    seen = fake_sdk(monkeypatch)
    code, out, secrets = drive(monkeypatch, capsys, catalog, db, ['1,2', '1', '', 'yes', 'RUN 40 CALLS'])
    assert code == 0 and len(secrets) == 1 and 'hidden' in secrets[0]
    assert 'COUNT: 20 questions x 2 models x 1 samples = 40 calls.' in out and 'Estimated cost: $0' in out
    assert scripted_names(out) == [ALPHA, BETA]                          # only the two free rows are offered
    assert 'vendor/paid-model' not in out and 'openrouter/free' not in out
    assert len(seen) == 40 and {c['api_key'] for c in seen} == {KEY}
    assert all(c['request']['extra_body']['provider']['allow_fallbacks'] is False for c in seen)
    assert 'Run 1: complete' in out and 'Saved results' in out and str(db) in out and 'dashboard' in out
    assert rows('SELECT COUNT(*) n FROM archive_answers')[0]['n'] == 40
    assert {r['source'] for r in rows('SELECT DISTINCT source FROM archive_answers')} == {'engine'}
    assert KEY not in out and KEY not in every_cell()
    assert 'LIVE_ALLOW_PAID_CALLS' not in os.environ                      # raised for the run only, in this process
    assert db.exists() and Path(launcher.REPO) not in db.parents


@pytest.mark.parametrize('confirmation', ['', 'run 40 calls', 'RUN 41 CALLS', 'yes'])
def test_anything_but_the_exact_phrase_calls_nothing(desk, monkeypatch, capsys, confirmation):
    catalog, db = desk
    seen = fake_sdk(monkeypatch)
    code, out, _ = drive(monkeypatch, capsys, catalog, db, ['1,2', '1', 'core', 'yes', confirmation])
    assert code == 1 and seen == [] and 'Nothing was called' in out
    assert rows('SELECT COUNT(*) n FROM archive_answers')[0]['n'] == 0 and rows('SELECT COUNT(*) n FROM budget_reservations')[0]['n'] == 0
    assert 'LIVE_ALLOW_PAID_CALLS' not in os.environ


def test_preview_needs_no_key_and_calls_and_records_nothing(desk, monkeypatch, capsys):
    catalog, db = desk
    os.environ['LIVE_DB'] = str(db)
    launcher.core.init_db()
    archive.lock()                                                         # the plan needs the lock; the preview never makes one
    seen = fake_sdk(monkeypatch)
    code, out, secrets = drive(monkeypatch, capsys, catalog, db, ['1', '3', 'core'], '--preview-only')
    assert code == 0 and secrets == [] and seen == []
    assert 'COUNT: 20 questions x 1 models x 3 samples = 60 calls.' in out and 'exceed 50 free requests per day' in out
    assert 'Preview only: nothing was called or recorded.' in out
    assert 'OPENROUTER_API_KEY' not in os.environ and rows('SELECT COUNT(*) n FROM archive_runs')[0]['n'] == 0


@pytest.mark.parametrize('answers', [['3'], ['0'], ['x'], ['1,1'], ['1,2,2'], ['1', 'x'], ['1', '9'], ['1', '1', 'sideways']])
def test_invalid_choices_stop_before_any_key_or_call(desk, monkeypatch, capsys, answers):
    catalog, db = desk
    seen = fake_sdk(monkeypatch)
    code, out, secrets = drive(monkeypatch, capsys, catalog, db, answers)
    assert code == 1 and seen == [] and secrets == []


def test_an_empty_key_stops_and_a_declined_lock_stops(desk, monkeypatch, capsys):
    catalog, db = desk
    seen = fake_sdk(monkeypatch)
    code, out, _ = drive(monkeypatch, capsys, catalog, db, ['1', '1', ''], key='   ')
    assert code == 1 and 'No key entered' in out and seen == []
    code, out, _ = drive(monkeypatch, capsys, catalog, db, ['1', '1', '', 'no'])
    assert code == 1 and 'Not locked' in out and seen == []


def test_the_database_must_live_outside_the_repository(desk, monkeypatch, capsys):
    catalog, _ = desk
    inside = launcher.REPO / 'collector.sqlite'
    code, out, secrets = drive(monkeypatch, capsys, catalog, inside, [])
    assert code == 1 and 'outside the repository' in out and not inside.exists() and secrets == []


def test_a_malformed_catalog_stops_cleanly(desk, monkeypatch, capsys):
    catalog, db = desk
    catalog.write_text('{"data": "nope"}', encoding='utf-8')
    code, out, _ = drive(monkeypatch, capsys, catalog, db, [])
    assert code == 1 and 'Stopped' in out


def test_a_provider_charge_on_the_launcher_path_stops_the_collection(desk, monkeypatch, capsys):
    catalog, db = desk
    from types import SimpleNamespace
    from test_archive_endpoints import reply

    def script(base_url, request, n):
        done = reply(model=request['model'])
        done.usage = SimpleNamespace(prompt_tokens=40, completion_tokens=12, cost=0.01)
        return done
    seen = fake_sdk(monkeypatch, script)
    code, out, _ = drive(monkeypatch, capsys, catalog, db, ['1', '1', '', 'yes', 'RUN 20 CALLS'])
    assert code == 1 and 'reported a charge' in out and 'reconcile' in out and len(seen) == 1
    assert rows('SELECT COUNT(*) n FROM archive_answers')[0]['n'] == 0
    assert [r['status'] for r in rows('SELECT status FROM budget_reservations')] == ['unconfirmed']


def scripted_names(out):
    return [line.split('. ', 1)[1].strip() for line in out.splitlines() if line.strip()[:1].isdigit() and ':free' in line]
