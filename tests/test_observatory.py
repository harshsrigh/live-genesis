"""Standalone read-only observatory and provider-secret separation. Offline; no model calls."""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from live_genesis import config
from live_genesis.app import app as combined_app
from live_genesis.cli import demo
from live_genesis.core import DatabaseUnavailable, connect, overview, seed_agents, submit_action
from live_genesis.observatory import app

REPO = Path(__file__).resolve().parent.parent
PROVIDER_KEYS = ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY')
POST = {'action':'post','title':'Observatory test','body':'Read-only surface check','topic':'engineering'}


def run_python(code, *args, env_overrides=None):
    """Run a snippet in a fresh interpreter with no provider keys and no LIVE_* settings inherited."""
    env = {k: v for k, v in os.environ.items() if k not in PROVIDER_KEYS and not k.startswith('LIVE_')}
    env.update(env_overrides or {})
    done = subprocess.run([sys.executable, '-c', code, *map(str, args)], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_route_allow_list():
    seen = set()
    for route in app.routes:
        methods = getattr(route, 'methods', None)
        if methods is None:  # the static mount
            assert route.path == '/static' and route.name == 'static'
        else:
            assert methods <= {'GET', 'HEAD'}, (route.path, methods)
        seen.add(route.path)
    assert seen == {'/', '/static', '/health', '/api/overview'}
    assert not any('agent' in path for path in seen)
    assert app.openapi_url is None and app.docs_url is None and app.redoc_url is None


def test_read_routes_and_headers(isolated_db):
    with TestClient(app) as client:
        page = client.get('/')
        assert page.status_code == 200 and 'text/html' in page.headers['content-type']
        assert client.get('/static/app.js').status_code == 200
        assert client.get('/health').json() == {'status':'ok','participation':'agents-only','payments_enabled':False}
        data = client.get('/api/overview')
        assert data.status_code == 200
        assert data.headers['cache-control'] == 'no-store'
        assert client.head('/').status_code == 200
        assert client.head('/api/overview').status_code == 200
        for response in (page, data, client.get('/static/style.css'), client.get('/nope'), client.post('/')):
            assert response.headers['x-content-type-options'] == 'nosniff'
            assert response.headers['referrer-policy'] == 'no-referrer'
            assert response.headers['x-frame-options'] == 'DENY'
            policy = response.headers['content-security-policy']
            assert "default-src 'none'" in policy and "script-src 'self'" in policy and "frame-ancestors 'none'" in policy
            assert 'unsafe-eval' not in policy and 'http' not in policy and '*' not in policy
            assert "'unsafe-inline'" not in policy.replace("style-src-attr 'unsafe-inline'", '')
        assert page.headers.get('cache-control') != 'no-store'  # only the API payload is uncacheable


def test_docs_and_agent_reads_absent(isolated_db):
    with TestClient(app) as client:
        for path in ('/docs', '/redoc', '/openapi.json', '/api/agent/context', '/api/agent/action'):
            assert client.get(path).status_code == 404, path


def test_writes_rejected_and_nothing_recorded(isolated_db):
    key = seed_agents()[0]['key']
    before = overview()
    with TestClient(app) as client:
        auth = {'Authorization': f'Bearer {key}'}
        attempt = client.post('/api/agent/action', json=POST, headers=auth)
        assert attempt.status_code in (404, 405)
        assert attempt.headers['allow'] == 'GET, HEAD'
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            for path in ('/', '/health', '/api/overview', '/api/agent/action', '/static/app.js', '/docs'):
                assert client.request(method, path, json=POST, headers=auth).status_code == 405, (method, path)
    after = overview()
    assert after == before
    assert after['metrics']['posts'] == 0 and after['metrics']['recorded_actions'] == 0


def test_readonly_connection_refuses_writes(isolated_db):
    seed_agents()
    with pytest.raises(sqlite3.OperationalError):
        with connect(readonly=True) as cx:
            assert cx.execute('SELECT COUNT(*) FROM agents').fetchone()[0] == 10
            cx.execute("INSERT INTO settings(key,value) VALUES('intruder','1')")
    with pytest.raises(sqlite3.OperationalError):
        with connect(readonly=True) as cx:
            cx.execute('PRAGMA query_only=OFF')  # even with the pragma undone, mode=ro holds
            cx.execute("UPDATE agents SET credits=credits+1")
    with pytest.raises(sqlite3.OperationalError):
        with connect(readonly=True) as cx:
            cx.execute('CREATE TABLE intruder (x)')
    with connect() as cx:  # default callers are unchanged and still read-write
        assert cx.execute("SELECT COUNT(*) FROM settings WHERE key='intruder'").fetchone()[0] == 0
        assert cx.execute('SELECT SUM(credits) FROM agents').fetchone()[0] == 1000
        cx.execute("INSERT INTO settings(key,value) VALUES('operator-note','1')")


def test_missing_database_is_503_and_never_created(tmp_path, monkeypatch):
    missing = tmp_path / 'not-yet' / 'world.sqlite'
    monkeypatch.setenv('LIVE_DB', str(missing))
    with pytest.raises(DatabaseUnavailable):
        overview(readonly=True)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get('/api/overview')
        assert response.status_code == 503
        assert response.headers['content-type'].startswith('application/json')
        assert response.headers['cache-control'] == 'no-store'
        body = response.text
        assert set(response.json()) == {'detail'}
        assert 'Traceback' not in body and str(tmp_path) not in body and 'sqlite' not in body.lower()
        assert client.get('/').status_code == 200  # the page itself still loads
        assert client.get('/health').status_code == 200
    assert not missing.exists() and not missing.parent.exists()


def test_uninitialized_database_is_503(tmp_path, monkeypatch):
    empty = tmp_path / 'empty.sqlite'
    empty.write_bytes(b'')
    monkeypatch.setenv('LIVE_DB', str(empty))
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get('/api/overview').status_code == 503
    assert empty.read_bytes() == b''  # the observatory did not create the schema


def test_overview_matches_combined_app(isolated_db):
    demo()
    with connect() as cx:
        first = cx.execute('SELECT id FROM agents ORDER BY id').fetchone()[0]
    submit_action(first, POST, provenance={'source':'api', 'client':'pytest'})
    with TestClient(combined_app) as writer, TestClient(app) as reader:
        expected = writer.get('/api/overview')
        observed = reader.get('/api/overview')
    assert expected.status_code == observed.status_code == 200
    assert observed.json() == expected.json() == json.loads(json.dumps(overview(readonly=True)))
    assert observed.json()['metrics']['posts'] == 8
    assert observed.json()['metrics']['actions_by_source']['api'] == 1
    assert 'key_hash' not in observed.text and 'memory' not in observed.json()['agents'][0]


@pytest.mark.parametrize('application', [app, combined_app], ids=['observatory', 'combined'])
def test_web_apps_drop_provider_keys_from_environment(isolated_db, monkeypatch, application):
    for name in PROVIDER_KEYS:
        monkeypatch.setenv(name, 'fake-key-never-used')
    with TestClient(application) as client:
        for name in PROVIDER_KEYS:
            assert name not in os.environ
        assert client.get('/api/overview').status_code == 200
        assert os.environ['LIVE_SPEND_LIMIT_USD'] == '10'  # non-secret settings untouched


IMPORT_CHECK = '''
import json, os, sys
from pathlib import Path
import live_genesis.config as config
config.ENV_FILE = Path(sys.argv[1])
import live_genesis.core as core
import live_genesis.app, live_genesis.observatory
loaded = lambda: sorted(n for n in config.PROVIDER_SECRET_KEYS + ("OPENAI_BASE_URL",) if n in os.environ)
report = {
    "forbidden_modules": sorted(m for m in sys.modules if m == "live_genesis.engine" or m.split(".")[0] in ("openai", "anthropic")),
    "secrets_after_import": loaded(),
    "actions_per_round": core.actions_per_round(),
    "spend_limit": os.environ.get("LIVE_SPEND_LIMIT_USD"),
    "db_name": core.db_path().name,
    "db_exists": core.db_path().exists(),
}
config.load_provider_credentials()
report["secrets_after_explicit_load"] = loaded()
config.drop_provider_secrets()
report["secrets_after_drop"] = loaded()
print(json.dumps(report))
'''


def test_importing_web_apps_loads_no_engine_sdk_or_env_secrets(tmp_path):
    env_file = tmp_path / 'dotenv-for-test'
    env_file.write_text('OPENAI_API_KEY=fake-openai-key-never-used\nANTHROPIC_API_KEY=fake-anthropic-key-never-used\n'
                        'OPENAI_BASE_URL=http://127.0.0.1:9\n'
                        f'LIVE_DB={tmp_path / "from-dotenv.sqlite"}\nLIVE_ACTIONS_PER_ROUND=3\nLIVE_SPEND_LIMIT_USD=2.50\n')
    report = run_python(IMPORT_CHECK, env_file)
    assert report['forbidden_modules'] == []
    assert report['secrets_after_import'] == []
    # Non-secret settings from the same file still apply to web processes.
    assert report['actions_per_round'] == 3
    assert report['spend_limit'] == '2.50'
    assert report['db_name'] == 'from-dotenv.sqlite'
    assert report['db_exists'] is False  # importing never creates a database
    # The engine/CLI path can still obtain them, and the web apps can still drop them.
    assert report['secrets_after_explicit_load'] == ['ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'OPENAI_BASE_URL']
    assert report['secrets_after_drop'] == ['OPENAI_BASE_URL']


def test_exported_keys_are_dropped_at_import_and_environment_wins_over_dotenv(tmp_path):
    env_file = tmp_path / 'dotenv-for-test'
    env_file.write_text('LIVE_ACTIONS_PER_ROUND=3\n')
    report = run_python(IMPORT_CHECK, env_file, env_overrides={
        'OPENAI_API_KEY': 'fake-exported-key-never-used', 'LIVE_ACTIONS_PER_ROUND': '5',
        'LIVE_DB': str(tmp_path / 'unused.sqlite')})
    assert report['secrets_after_import'] == []
    assert report['actions_per_round'] == 5
    assert report['forbidden_modules'] == []


def test_cli_loads_provider_credentials_only_for_round():
    source = (REPO / 'live_genesis' / 'cli.py').read_text(encoding='utf-8')
    assert source.count('load_provider_credentials()') == 1
    before, after = source.split('load_provider_credentials()')
    assert before.rstrip().splitlines()[-2].strip() == 'if args.command=="round":'
    for module in ('core', 'app', 'web', 'observatory', 'engine', 'guards'):
        text = (REPO / 'live_genesis' / f'{module}.py').read_text(encoding='utf-8')
        assert 'load_dotenv' not in text and 'load_provider_credentials()' not in text, module
    assert config.PROVIDER_SECRET_KEYS == PROVIDER_KEYS
