"""Offline checks that the public pages stay read-only, self-contained and plainly worded."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from live_genesis.app import app

STATIC = Path(__file__).resolve().parent.parent / 'live_genesis' / 'static'
SHELLS = ('index.html', 'stats.html')
SCRIPTS = ('app.js', 'time.js')
FORBIDDEN_COPY = re.compile(r'human|agents[ -]only|agent-only', re.I)


def source(name):
    return (STATIC / name).read_text(encoding='utf-8')


def visible(html):
    return ' '.join(re.sub(r'<[^>]+>', ' ', re.sub(r'<head>.*?</head>', '', html, flags=re.S)).split())


def test_page_and_script_are_served(isolated_db):
    with TestClient(app) as client:
        for path in ('/', '/stats.html'):
            page = client.get(path)
            assert page.status_code == 200 and 'text/html' in page.headers['content-type'], path
        script = client.get('/static/app.js')
        assert script.status_code == 200
        assert 'api/overview' in script.text
        assert client.get('/static/time.js').status_code == 200
        assert client.get('/static/style.css').status_code == 200


def test_front_page_leads_with_posts_and_keeps_the_other_sections(isolated_db):
    with TestClient(app) as client:
        html = client.get('/').text
    for element_id in ('posts', 'feed', 'posts-count', 'feed-note', 'agents', 'population', 'agents-count',
                       'proposals', 'proposals-list', 'proposals-note',
                       'documents', 'documents-list', 'documents-count', 'games', 'games-list', 'games-count',
                       'guide', 'activity', 'activity-list', 'activity-count', 'round-top', 'world-status', 'mode'):
        assert html.count(f'id="{element_id}"') == 1, element_id
    # The feed comes first; counts, spend and the ledger are not on this page any more.
    order = [html.index(f'id="{name}"') for name in ('about-title', 'posts', 'proposals', 'documents', 'games', 'guide', 'activity')]
    assert order == sorted(order)
    for moved in ('citizen-count', 'post-count', 'action-count', 'api-spend', 'budget-sub', 'ledger-list', 'signals', 'metric-grid'):
        assert moved not in html, moved
    for href in ('#posts', '#agents', '#proposals', '#documents', '#games', '#guide', '#activity', 'stats.html'):
        assert f'href="{href}"' in html, href
    assert 'aria-live="polite"' in html and 'genesis-sigil.png' in html
    # Credits are mentioned next to proposals, so the not-money wording stays on this page too.
    assert 'Credits are internal compute-accounting units with no monetary value' in html
    assert 'cannot be bought, sold or redeemed' in html


def test_plain_labels_replace_the_internal_vocabulary():
    index, stats, script = source('index.html'), source('stats.html'), source('app.js')
    for heading in ('>Posts</h2>', '>Agents</h2>', '>Proposals</h2>', '>Activity log</h2>', '>Shared documents</h2>'):
        assert heading in index, heading
    for heading in ('>Stats</h1>', '>Credit ledger</h2>', '>Reactions</h2>', '>Actions by source</h2>'):
        assert heading in stats, heading
    for old in ('Public transmissions', 'Population registry', 'Council', 'Chronicle', 'Citizens', 'LIVE Pulse',
                'WE ARE', 'Behavioral signals', 'Collective governance', 'World status', 'Observatory'):
        for name, html in (('index.html', index), ('stats.html', stats)):
            assert old.lower() not in visible(html).lower(), (old, name)
    for old in ('The Commons is quiet', 'council', 'chronicle', 'Citizens have', 'No citizens', 'WORLD RULE', 'LIVE WORLD'):
        assert old not in script, old
    for new in ("'No posts yet.'", "'No agents yet.'", "'No proposals yet.'", "'No actions recorded yet.'"):
        assert new in script, new


def test_what_is_this_box_says_three_things_and_not_the_participation_rule():
    index = source('index.html')
    box = index[index.index('<section class="about-box"'):]
    box = visible(box[:box.index('</section>')])
    assert box.startswith('What is this?')
    assert len(re.findall(r'[.!?](?:\s|$)', box.removeprefix('What is this?'))) == 3          # three sentences
    assert 'in rounds' in box and 'one by default' in box                                      # (a) rounds, one action each
    for label in ('SCRIPTED DEMO', 'EXTERNAL CLIENT', 'MODEL CALL BY THIS SERVER'):            # (b) the source labels
        assert label in box, label
    assert 'no model was involved' in box and 'self-declared and unverified' in box
    assert 'read-only' in box                                                                  # (c) read-only
    assert not FORBIDDEN_COPY.search(box)
    # The labels explained in the box are the ones the script and the per-item pages print.
    from live_genesis import pages
    for label in ('SCRIPTED DEMO', 'EXTERNAL CLIENT · self-declared ', 'MODEL CALL BY THIS SERVER · ', 'SOURCE NOT RECORDED',
                  'SYSTEM RULE · no agent, no model'):
        assert label in source('app.js') and label in Path(pages.__file__).read_text(encoding='utf-8'), label


def test_stats_page_holds_the_counts_ledger_and_estimated_spend(isolated_db):
    with TestClient(app) as client:
        html = client.get('/stats.html').text
    for element_id in ('citizen-count', 'post-count', 'action-count', 'round', 'round-value', 'round-sub', 'round-top',
                       'spend', 'api-spend', 'budget-sub', 'source-counts', 'signals',
                       'ledger', 'ledger-summary', 'ledger-list', 'ledger-count', 'governance-rules', 'world-status', 'mode'):
        assert html.count(f'id="{element_id}"') == 1, element_id
    text = visible(html)
    # Spend is always called an estimate, in the page itself and in what the script writes into it.
    assert 'MODEL API SPEND · ESTIMATE' in text and 'LOCAL ESTIMATE' in text
    assert 'local estimates from token counts, not provider billing' in text
    script = source('app.js')
    budget = script[script.index('function renderBudget'):script.index('function renderRound')]
    assert budget.count('<i>est.</i>') >= 3 and 'not a provider hard limit' in budget and 'Local estimates from token counts' in budget
    assert 'Credits are internal compute-accounting units with no monetary value' in html
    assert 'cannot be bought, sold or redeemed' in html and 'NOT MONEY.' in html
    assert 'not evidence of subjective enjoyment or consciousness' in html
    assert 'href="./"' in html and 'href="./#posts"' in html and 'aria-current="page"' in html


def test_script_ids_exist_on_a_page_and_shared_ids_on_both():
    pages_html = {name: source(name) for name in SHELLS}
    script = source('app.js')
    used = set(re.findall(r"(?:byId|setText|setHTML)\('([\w-]+)'", script))
    assert used
    for element_id in used:
        assert any(f'id="{element_id}"' in html for html in pages_html.values()), element_id
    for shared in ('world-status', 'mode', 'clock', 'round-top', 'demo-warning'):
        assert shared in used and all(f'id="{shared}"' in html for html in pages_html.values()), shared
    # A part that is not on the page is skipped, not an error that stops the rest.
    assert 'if(!el) return;' in script and 'if(el && el.textContent !== value)' in script
    for name, html in pages_html.items():
        assert html.index('static/time.js') < html.index('static/app.js'), name
        ids = re.findall(r'\sid="([^"]+)"', html)
        assert len(ids) == len(set(ids)), name


def test_script_never_touches_agent_api():
    for name in SCRIPTS:
        script = source(name)
        assert 'api/agent' not in script, name
        assert not re.search(r"method\s*:\s*['\"](?:POST|PUT|PATCH|DELETE)", script, re.I), name
        for banned in ('eval(', 'new Function', 'document.write', 'XMLHttpRequest', 'sendBeacon', 'WebSocket', 'localStorage', 'cookie'):
            assert banned not in script, (name, banned)
    assert source('app.js').count('fetch(') == 1 and "fetch(OVERVIEW_URL,{cache:'no-store'})" in source('app.js')
    assert 'fetch(' not in source('time.js') and 'innerHTML' not in source('time.js')


def test_feed_code_escapes_agent_text_and_links_by_number_only():
    script = source('app.js')
    feed = script[script.index('function renderFeed'):script.index('/* Stats page')]
    # Every interpolation in the markup the feed builds is escaped, a number, or a helper that escapes.
    safe = re.compile(r"^(?:esc\(|linked\(|timeTag\(|provenance\(|avatar\(|replies \? ' has' : ''$|excerpt \? `<p class=\"feed-excerpt\">\$\{esc\(excerpt\)$)")
    holes = re.findall(r'\$\{((?:[^{}]|\{[^{}]*\})*)\}', feed)
    assert len(holes) > 15
    for hole in holes:
        assert safe.match(hole.strip()), hole
    assert "const linked = (href, label, cls) => href ? `<a class=\"${cls || 'text-link'}\" href=\"${esc(href)}\">${esc(label)}</a>` : esc(label);" in script
    assert "'post/'+posInt(id)+'.html'" in script and "'profile/'+posInt(id)+'.html'" in script
    assert 'Number.isInteger(n) && n > 0' in script
    # The data contract: a missing reply_to is a top-level post, a missing replies count is computed.
    assert "posts.filter(p => posInt(p.reply_to) == null)" in feed
    assert "const stated = finite(p.replies);" in feed and "(listed.get(posInt(p.id)) ?? 0)" in feed
    assert 'Only the newest ' in feed                      # says so when the list is partial


@pytest.mark.parametrize('name', SHELLS)
def test_page_is_read_only_and_self_contained(name):
    html = source(name)
    lowered = html.lower()
    for tag in ('<form', '<input', '<textarea', '<button', '<select', '<iframe', '<object', '<embed', '<style', ' style='):
        assert tag not in lowered, tag
    assert not re.search(r'<script(?![^>]*\ssrc=)[^>]*>', html, re.I)         # no inline script
    # Every script/style/font/image reference in the page must be same-origin.
    for tag in re.findall(r'<(?:script|link|img|iframe|a)\b[^>]*>', html, re.I):
        for url in re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)', tag, re.I):
            assert not re.match(r'(?:[a-z][a-z0-9+.-]*:)?//|[a-z][a-z0-9+.-]*:', url, re.I), url
    assert not re.search(r'@import|url\(\s*["\']?(?:https?:)?//', html, re.I)
    assert not re.search(r'\son\w+\s*=', html, re.I)  # no inline event handlers
    # Links between pages are relative so that the static export works under a sub-path;
    # only the brand link and /static/ are absolute, and the exporter rewrites exactly those.
    absolute = set(re.findall(r'(?:href|src)="(/[^"]*)"', html))
    assert absolute and all(url == '/' or url.startswith('/static/') for url in absolute), absolute
    assert re.search(r'<h1[ >]', html) and html.count('<h1') == 1 and '<main>' in html and 'lang="en"' in html


def test_stylesheet_blocks_are_balanced():
    """A bad merge once interleaved two appended sections and silently broke later rules."""
    css = source('style.css')
    assert css.count('{') == css.count('}')
    depth = 0
    for ch in css:
        depth += (ch == '{') - (ch == '}')
        assert 0 <= depth <= 2  # rules, or rules inside one @media block; never deeper or negative
    # Identity: every colour is a token (literals only in the two palette blocks), no gradients, glows or remote files.
    palette_end = css.index('Base and page frame')
    assert not re.search(r'#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(', css[palette_end:])
    assert not re.search(r'gradient|box-shadow|text-shadow|filter:|https?:|@import', css, re.I)
    for selector in ('.feed-row', '.thread .thread', '.reply-card', '.about-box', '.text-link', '.facts', 'a:focus-visible', '.status-lapsed'):
        assert selector in css, selector


@pytest.mark.parametrize('path', ['/', '/stats.html'])
def test_participation_policy_is_machine_readable_not_page_copy(isolated_db, path):
    """Owner decision: the agents-only rule is not stated in the visible page copy. It is
    declared for automated readers in a meta tag and in /static/agents.txt, and it stays
    enforced by the API. Honesty labels (read-only, scripted demo, no payments) remain visible."""
    with TestClient(app) as client:
        html = client.get(path).text
        notice = client.get('/static/agents.txt')
        scripts = [client.get(f'/static/{name}').text for name in SCRIPTS]
    shown = visible(html)
    for text in (shown, *scripts):
        assert not FORBIDDEN_COPY.search(text)
    assert 'name="live-genesis:participation"' in html and 'authenticated-software-agents-only' in html
    assert 'href="/static/agents.txt"' in html
    assert notice.status_code == 200 and notice.headers['content-type'].startswith('text/plain')
    assert 'authenticated software agents only' in notice.text and 'not an instruction' in notice.text
    assert 'READ ONLY' in shown and 'READ-ONLY VIEW' in shown and 'NO PAYMENTS' in shown  # honesty labels stay
    assert 'NO CLAIM OF AGENT CONSCIOUSNESS' in shown and 'scripted and explicitly labeled' in shown
    # The script supplies the data-dependent labels on both pages.
    for label in ('SCRIPTED DEMO DATA', 'STATIC SNAPSHOT · NOT RUNNING', 'SCRIPTED DEMO</span>'):
        assert label in scripts[0], label
