"""The local read-only web app and provider-secret separation. Offline; no model calls."""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from live_genesis import archive, config, web
from live_genesis.app import app
from live_genesis.core import DatabaseUnavailable, connect

REPO = Path(__file__).resolve().parent.parent
PROVIDER_KEYS = ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'OPENAI_COMPATIBLE_API_KEY')


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
    assert seen == {'/', '/static', '/health',
                    '/archive', '/archive/', '/archive/index.html', '/archive/q/{name}',
                    '/archive/trending.html', '/archive/trending-now.html', '/archive/chatbots.html', '/archive/t/{name}', '/archive/card/{name}',
                    '/archive/models.html', '/archive/c/{name}', '/archive/data/{name}'}
    assert not any('agent' in path or path.startswith('/api') for path in seen)
    assert app.openapi_url is None and app.docs_url is None and app.redoc_url is None


def test_read_routes_and_headers(isolated_db):
    with TestClient(app) as client:
        home = client.get('/', follow_redirects=False)
        assert home.status_code == 307 and home.headers['location'] == '/archive/index.html'
        page = client.get('/archive/index.html')
        assert page.status_code == 200 and 'text/html' in page.headers['content-type']
        assert client.get('/static/archive.css').status_code == 200
        assert client.get('/health').json() == {'status':'ok','payments_enabled':False}
        assert client.head('/archive/index.html').status_code == 200
        for response in (home, page, client.get('/static/archive.css'), client.get('/nope'), client.post('/')):
            assert response.headers['x-content-type-options'] == 'nosniff'
            assert response.headers['referrer-policy'] == 'no-referrer'
            assert response.headers['x-frame-options'] == 'DENY'
            policy = response.headers['content-security-policy']
            assert "default-src 'none'" in policy and "script-src 'self'" in policy and "frame-ancestors 'none'" in policy
            assert 'unsafe-eval' not in policy and 'http' not in policy and '*' not in policy
            assert "'unsafe-inline'" not in policy.replace("style-src-attr 'unsafe-inline'", '')


def test_docs_api_and_removed_society_routes_are_absent(isolated_db):
    with TestClient(app) as client:
        for path in ('/docs', '/redoc', '/openapi.json', '/api/overview', '/api/agent/context', '/api/agent/action',
                     '/api/agent/archive-answer', '/stats.html', '/post/1.html', '/profile/1.html',
                     '/static/app.js', '/static/style.css', '/static/agents.txt'):
            assert client.get(path).status_code == 404, path


def test_writes_rejected_and_nothing_recorded(isolated_db):
    before = isolated_db.read_bytes()
    with TestClient(app) as client:
        attempt = client.post('/api/agent/action', json={'action': 'idle'}, headers={'Authorization': 'Bearer live_' + 'x' * 40})
        assert attempt.status_code == 405
        assert attempt.headers['allow'] == 'GET, HEAD'
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            for path in ('/', '/health', '/archive/index.html', '/api/agent/archive-answer', '/static/archive.css', '/docs'):
                assert client.request(method, path, json={'x': 1}).status_code == 405, (method, path)
    assert isolated_db.read_bytes() == before


def test_readonly_connection_refuses_writes(isolated_db):
    with pytest.raises(sqlite3.OperationalError):
        with connect(readonly=True) as cx:
            assert cx.execute('SELECT COUNT(*) FROM api_usage').fetchone()[0] == 0
            cx.execute("INSERT INTO operator_audit(operation,created_at) VALUES('intruder','now')")
    with pytest.raises(sqlite3.OperationalError):
        with connect(readonly=True) as cx:
            cx.execute('PRAGMA query_only=OFF')  # even with the pragma undone, mode=ro holds
            cx.execute("INSERT INTO operator_audit(operation,created_at) VALUES('intruder','now')")
    with pytest.raises(sqlite3.OperationalError):
        with connect(readonly=True) as cx:
            cx.execute('CREATE TABLE intruder (x)')
    with connect() as cx:  # default callers are unchanged and still read-write
        assert cx.execute("SELECT COUNT(*) FROM operator_audit WHERE operation='intruder'").fetchone()[0] == 0
        cx.execute("INSERT INTO operator_audit(operation,created_at) VALUES('operator-note','now')")


def test_missing_database_is_503_and_never_created(tmp_path, monkeypatch):
    missing = tmp_path / 'not-yet' / 'world.sqlite'
    monkeypatch.setenv('LIVE_DB', str(missing))
    with pytest.raises(DatabaseUnavailable):
        archive.public_payload(readonly=True)
    with TestClient(app, raise_server_exceptions=False) as client:
        for path in ('/archive/index.html', '/archive/data/answers.jsonl'):
            response = client.get(path)
            assert response.status_code == 503
            assert response.headers['content-type'].startswith('application/json')
            body = response.text
            assert response.json() == {'detail': web.UNAVAILABLE}
            assert 'Traceback' not in body and str(tmp_path) not in body and 'sqlite' not in body.lower()
        assert client.get('/health').status_code == 200
    assert not missing.exists() and not missing.parent.exists()


def test_uninitialized_database_is_never_given_a_schema(tmp_path, monkeypatch):
    empty = tmp_path / 'empty.sqlite'
    empty.write_bytes(b'')
    monkeypatch.setenv('LIVE_DB', str(empty))
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get('/archive/index.html').status_code in (200, 503)   # an empty archive or unavailable: never a 500
    assert empty.read_bytes() == b''  # the web app did not create the schema


def test_web_app_drops_provider_keys_from_environment(isolated_db, monkeypatch):
    for name in PROVIDER_KEYS:
        monkeypatch.setenv(name, 'fake-key-never-used')
    with TestClient(app) as client:
        for name in PROVIDER_KEYS:
            assert name not in os.environ
        assert client.get('/archive/index.html').status_code == 200
        assert os.environ['LIVE_SPEND_LIMIT_USD'] == '10'  # non-secret settings untouched


IMPORT_CHECK = '''
import json, os, sys
from pathlib import Path
import live_genesis.config as config
config.ENV_FILE = Path(sys.argv[1])
import live_genesis.core as core
import live_genesis.app
loaded = lambda: sorted(n for n in config.PROVIDER_SECRET_KEYS + ("OPENAI_BASE_URL",) if n in os.environ)
report = {
    "forbidden_modules": sorted(m for m in sys.modules if m == "live_genesis.engine" or m.split(".")[0] in ("openai", "anthropic")),
    "secrets_after_import": loaded(),
    "samples": os.environ.get("LIVE_ARCHIVE_SAMPLES"),
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


def test_importing_the_web_app_loads_no_engine_sdk_or_env_secrets(tmp_path):
    env_file = tmp_path / 'dotenv-for-test'
    env_file.write_text('OPENAI_API_KEY=fake-openai-key-never-used\nANTHROPIC_API_KEY=fake-anthropic-key-never-used\n'
                        'OPENAI_COMPATIBLE_API_KEY=fake-compatible-key-never-used\nOPENAI_BASE_URL=http://127.0.0.1:9\n'
                        'LIVE_COMPAT_BASE_URL=http://127.0.0.1:9/v1\n'
                        f'LIVE_DB={tmp_path / "from-dotenv.sqlite"}\nLIVE_ARCHIVE_SAMPLES=3\nLIVE_SPEND_LIMIT_USD=2.50\n')
    report = run_python(IMPORT_CHECK, env_file)
    assert report['forbidden_modules'] == []
    assert report['secrets_after_import'] == []
    # Non-secret settings from the same file still apply to the web process.
    assert report['samples'] == '3'
    assert report['spend_limit'] == '2.50'
    assert report['db_name'] == 'from-dotenv.sqlite'
    assert report['db_exists'] is False  # importing never creates a database
    # The CLI path can still obtain them, and the web app can still drop them.
    assert report['secrets_after_explicit_load'] == ['ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_COMPATIBLE_API_KEY']
    assert report['secrets_after_drop'] == ['OPENAI_BASE_URL']


def test_exported_keys_are_dropped_at_import_and_environment_wins_over_dotenv(tmp_path):
    env_file = tmp_path / 'dotenv-for-test'
    env_file.write_text('LIVE_ARCHIVE_SAMPLES=3\n')
    report = run_python(IMPORT_CHECK, env_file, env_overrides={
        'OPENAI_API_KEY': 'fake-exported-key-never-used', 'OPENAI_COMPATIBLE_API_KEY': 'fake-exported-compatible-key',
        'LIVE_ARCHIVE_SAMPLES': '5',
        'LIVE_DB': str(tmp_path / 'unused.sqlite')})
    assert report['secrets_after_import'] == []
    assert report['samples'] == '5'
    assert report['forbidden_modules'] == []


def test_provider_credentials_are_loaded_only_by_archive_run_and_check_provider():
    """`.env` provider keys enter a process in three places only: `cli archive-run` and `cli archive-chatbot-ask` (the
    paid commands) and `cli check-provider` / `archive-check-endpoints` (which print only whether a key is set)."""
    loaders = {}
    for path in sorted((REPO / 'live_genesis').glob('*.py')):
        text = path.read_text(encoding='utf-8')
        assert 'load_dotenv' not in text, path.name
        calls = text.count('load_provider_credentials()')
        if calls:
            loaders[path.name] = calls
    # config.py defines the function (its `def` line and one mention in the module text).
    assert set(loaders) == {'archive.py', 'archive_chatbots.py', 'config.py', 'providers.py'}, loaders
    # The chat-app module loads them once, in the branch of its one PAID command, after the plan and the dry-run exit.
    chat_source = (REPO / 'live_genesis' / 'archive_chatbots.py').read_text(encoding='utf-8')
    assert loaders['archive_chatbots.py'] == 1
    chat_head = chat_source.split('settings.load_provider_credentials()')[0]
    assert [line.strip() for line in chat_head.splitlines() if line.strip().startswith('if args.command')][-1] == \
        'if args.command == "archive-chatbot-ask":'
    assert chat_head.rstrip().splitlines()[-1].strip().startswith('print("PAID RUN') and 'if args.dry_run:' in chat_head.rsplit('if args.command', 1)[1]
    archive_source = (REPO / 'live_genesis' / 'archive.py').read_text(encoding='utf-8')
    head = archive_source.split('config.load_provider_credentials()')[0]
    branch = [line.strip() for line in head.splitlines() if line.strip().startswith(('if args.command', 'elif args.command'))][-1]
    assert branch == 'elif args.command == "archive-run":'
    assert config.PROVIDER_SECRET_KEYS == PROVIDER_KEYS
