"""Offline checks that the observatory page stays read-only and self-contained."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from live_genesis.app import app

STATIC = Path(__file__).resolve().parent.parent / 'live_genesis' / 'static'


def test_page_and_script_are_served(isolated_db):
    with TestClient(app) as client:
        page = client.get('/')
        assert page.status_code == 200
        assert 'text/html' in page.headers['content-type']
        script = client.get('/static/app.js')
        assert script.status_code == 200
        assert 'api/overview' in script.text
        assert client.get('/static/style.css').status_code == 200


def test_page_has_new_sections(isolated_db):
    with TestClient(app) as client:
        html = client.get('/').text
    for element_id in ('chronicle', 'chronicle-list', 'chronicle-count', 'round-value', 'round-sub',
                       'round-top', 'api-spend', 'budget-sub',
                       'games', 'games-list', 'games-count',
                       'documents', 'documents-list', 'documents-count',
                       'ledger', 'ledger-summary', 'ledger-list', 'ledger-count', 'governance-rules'):
        assert f'id="{element_id}"' in html, element_id
    assert 'href="#ledger"' in html
    # Permanent, static caption: present even before any data loads.
    assert 'Credits are internal compute-accounting units with no monetary value' in html
    assert 'cannot be bought, sold or redeemed' in html
    assert 'aria-live="polite"' in html
    assert 'href="#games"' in html
    assert 'href="#documents"' in html
    assert 'genesis-sigil.png' in html


def test_script_ids_exist_in_page():
    html = (STATIC / 'index.html').read_text(encoding='utf-8')
    script = (STATIC / 'app.js').read_text(encoding='utf-8')
    used = set(re.findall(r"(?:byId|setText|setHTML)\('([\w-]+)'", script))
    assert used
    for element_id in used:
        assert f'id="{element_id}"' in html, element_id


def test_script_never_touches_agent_api():
    script = (STATIC / 'app.js').read_text(encoding='utf-8')
    assert 'api/agent' not in script
    assert not re.search(r"method\s*:\s*['\"](?:POST|PUT|PATCH|DELETE)", script, re.I)
    assert 'eval(' not in script


def test_page_is_read_only_and_self_contained():
    html = (STATIC / 'index.html').read_text(encoding='utf-8')
    lowered = html.lower()
    for tag in ('<form', '<input', '<textarea', '<button', '<select'):
        assert tag not in lowered, tag
    # Every script/style/font/image reference in the page must be same-origin.
    for tag in re.findall(r'<(?:script|link|img|iframe)\b[^>]*>', html, re.I):
        for url in re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)', tag, re.I):
            assert not re.match(r'(?:https?:)?//', url, re.I), url
    assert not re.search(r'@import|url\(\s*["\']?(?:https?:)?//', html, re.I)
    assert not re.search(r'\son\w+\s*=', html, re.I)  # no inline event handlers
