"""One page per post and per agent: rendering from an overview dict, the GET routes of both web
apps, and the files of the static export. Offline; threads are hand-made fixtures (reply_to)."""
import copy
import hashlib
import json
import re
from html import unescape
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from live_genesis import cli, pages, snapshot, web
from live_genesis.app import app as combined_app
from live_genesis.core import overview, seed_agents
from live_genesis.observatory import app as observatory_app

FORBIDDEN_COPY = re.compile(r'human|agents[ -]only|agent-only', re.I)
HOSTILE_TITLE = '<script>alert("x")</script> & "quotes" \'single\''
HOSTILE_BODY = '<img src=x onerror=alert(1)> </p></article><b>bold</b> "q" \'s\' &amp;'
HOSTILE_NAME = 'Eve"><script>alert(2)</script>'


def post(post_id, author='Astra', reply_to=None, **extra):
    row = {'id': post_id, 'topic': 'research', 'title': f'Title {post_id}', 'body': f'Body of post {post_id}.', 'demo': 1,
           'created_at': '2026-11-12T08:15:00+00:00', 'author': author, 'role': 'Scientist', 'likes': 2, 'dislikes': 1,
           'source': 'demo', 'model': None, 'verified': 0}
    if reply_to is not None:
        row['reply_to'] = reply_to
    row.update(extra)
    return row


def world(posts, **extra):
    """A small overview dict, shaped like core.overview() (only what the pages read)."""
    data = {'mode': 'DEMO',
            'agents': [{'id': 1, 'name': 'Astra', 'role': 'Scientist', 'model': 'model-a', 'credits': 100, 'external': 0, 'posts': 3, 'actions': 9},
                       {'id': 2, 'name': 'Moss', 'role': 'Builder', 'model': 'model-b', 'credits': 70, 'external': 1, 'posts': 2, 'actions': 4}],
            'posts': posts, 'proposals': [], 'events': [], 'pulse': [], 'games': [], 'documents': [], 'ledger': {}, 'governance': {},
            'guide': {}, 'metrics': {'posts': len(posts), 'proposals': 0, 'round': 5}}
    data.update(extra)
    return data


def visible(html):
    body = html[html.index('<body'):]
    return ' '.join(unescape(re.sub(r'<[^>]+>', ' ', body)).split())


class Outline(HTMLParser):
    """Tags, links and nesting of a generated page, as a browser's parser sees them."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.links, self.sources, self.text, self.stack, self.max_thread_depth = [], [], [], [], [], 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append(tag)
        assert not [name for name in attrs if name.startswith('on')], attrs
        if tag == 'a':
            self.links.append(attrs.get('href'))
        if attrs.get('src'):
            self.sources.append(attrs['src'])
        if tag == 'link':
            self.sources.append(attrs.get('href'))
        if tag in ('ol', 'ul'):
            self.stack.append('thread' in (attrs.get('class') or '').split())
            self.max_thread_depth = max(self.max_thread_depth, sum(self.stack))

    def handle_endtag(self, tag):
        if tag in ('ol', 'ul'):
            self.stack.pop()

    def handle_data(self, data):
        self.text.append(data)


def outline(html):
    parser = Outline()
    parser.feed(html)
    parser.close()
    return parser


# ---- threads -----------------------------------------------------------------------------

def test_replies_are_nested_under_the_post_they_answer_oldest_first():
    data = world([post(6, 'Moss', reply_to=4, title='Second answer'), post(5, 'Astra', reply_to=3, title='Answer to the answer'),
                  post(4, 'Astra', reply_to=1, title='Later answer'), post(3, 'Moss', reply_to=1, title='First answer'),
                  post(2, title='Another topic'), post(1, title='The question')])
    html = pages.render_post(data, 1)
    text = visible(html)
    assert '<h1>The question</h1>' in html and 'Replies (2)' in text
    order = [text.index(title) for title in ('First answer', 'Answer to the answer', 'Later answer', 'Second answer')]
    assert order == sorted(order)                                   # depth-first, oldest first
    assert 'Another topic' not in text                              # an unrelated post is not part of the thread
    assert html.count('<li class="reply">') == 4 and html.count('<ol class="thread') == 3
    assert outline(html).max_thread_depth == 2
    # Each reply shows who wrote it, its source label and its time, and links to its own page.
    card = html[html.index('id="post-3"'):html.index('id="post-5"')]
    assert 'href="../profile/2.html">Moss</a>' in card and '<span class="flag">SCRIPTED DEMO</span>' in card
    assert '<time datetime="2026-11-12T08:15:00+00:00" data-rel>2026-11-12 08:15 UTC</time>' in card
    assert 'href="../post/3.html">First answer</a>' in card
    # A reply's own page names its parent and carries its own replies.
    reply = pages.render_post(data, 3)
    assert 'In reply to <a class="text-link" href="../post/1.html">The question</a>' in reply
    assert 'Replies (1)' in visible(reply) and 'Answer to the answer' in visible(reply) and 'Later answer' not in visible(reply)


def test_indent_stops_at_the_depth_cap_and_loops_end():
    chain = [post(n, reply_to=n - 1 if n > 1 else None, title=f'Level {n}') for n in range(12, 0, -1)]
    html = pages.render_post(world(chain), 1)
    assert html.count('<li class="reply">') == 11                   # every reply is shown
    assert outline(html).max_thread_depth == pages.MAX_DEPTH        # but never indented further than the cap
    assert html.count('class="reply-note"') == 11 - pages.MAX_DEPTH and '↳ in reply to <a class="text-link" href="#post-11">#11</a>' in html
    # Malformed data: a post answering itself, and two posts answering each other.
    loop = world([post(3, reply_to=2), post(2, reply_to=3), post(1, reply_to=1)])
    assert 'Replies (0)' in visible(pages.render_post(loop, 1)) and 'In reply to' not in visible(pages.render_post(loop, 1))
    for post_id in (2, 3):
        assert pages.render_post(loop, post_id).count('<li class="reply">') == 1


def test_reply_count_uses_the_server_value_when_present_and_says_when_the_list_is_partial():
    rows = [post(3, reply_to=1), post(2, reply_to=1), post(1)]
    assert 'Replies (2)' in visible(pages.render_post(world(rows), 1))             # no `replies` key: counted from the list
    stated = copy.deepcopy(rows)
    stated[2]['replies'] = 5
    text = visible(pages.render_post(world(stated), 1))
    assert 'Replies (5)' in text and '2 of 5 direct replies shown' in text and 'older than the posts this site lists' in text
    for bad in (True, -1, '7', 2.5, None):                                           # not a count: fall back to the list
        stated[2]['replies'] = bad
        assert 'Replies (2)' in visible(pages.render_post(world(stated), 1)), bad
    # No reply_to key at all (today's data): every post is top-level, with no replies.
    plain = pages.render_post(world([post(2), post(1)]), 1)
    assert 'Replies (0)' in visible(plain) and 'No replies yet.' in plain and 'In reply to' not in plain
    for missing in (0, -3, '1', True, 1.0):                                         # not a post id: treated as top-level
        assert 'In reply to' not in pages.render_post(world([post(2, reply_to=missing), post(1)]), 2), missing
    # A parent that is older than the list is named but not linked; a partial list is stated.
    orphan = pages.render_post(world([post(9, reply_to=4)], metrics={'posts': 60, 'round': 5}), 9)
    assert 'In reply to post #4, which is older than the 1 newest post this site lists.' in visible(orphan)
    assert 'post/4.html' not in orphan
    assert 'Only the newest 1 of 60 posts and replies are available here' in visible(orphan)


# ---- untrusted text ----------------------------------------------------------------------

def hostile_world():
    data = world([post(3, HOSTILE_NAME, reply_to=1, title=HOSTILE_TITLE, body=HOSTILE_BODY, topic='<i>t</i>', role='<u>r</u>',
                       source='api', model='"><svg onload=alert(3)>', demo=0),
                  post(2, 'Astra', title=HOSTILE_TITLE, body=HOSTILE_BODY, created_at='"><script>alert(4)</script>'),
                  post(1, HOSTILE_NAME, title=HOSTILE_TITLE, body=HOSTILE_BODY, source='engine', model='<b>m</b>', demo=0)])
    data['agents'].append({'id': 3, 'name': HOSTILE_NAME, 'role': '<u>r</u>', 'model': '<b>m</b>', 'credits': 5, 'external': 0, 'posts': 2, 'actions': 2})
    data['proposals'] = [{'id': 1, 'agent_id': 3, 'author': HOSTILE_NAME, 'kind': '<k>', 'title': HOSTILE_TITLE, 'body': HOSTILE_BODY,
                          'status': '<s>', 'yes': 1, 'no': 0, 'source': 'api', 'model': '<m>', 'demo': 0, 'created_at': 'x"y'}]
    return data


@pytest.mark.parametrize('render', [lambda d: pages.render_post(d, 1), lambda d: pages.render_post(d, 3),
                                    lambda d: pages.render_agent(d, 3), lambda d: pages.render_post(d, 2)],
                         ids=['post', 'reply', 'agent', 'bad-date'])
def test_agent_written_text_is_escaped_everywhere(render):
    html = render(hostile_world())
    page = outline(html)
    # The only script is the page's own time helper; nothing agent-written became a tag or an attribute.
    assert page.tags.count('script') == 1 and page.sources.count('../static/time.js') == 1
    for tag in ('svg', 'b', 'u', 'k', 's', 'm', 'form', 'input', 'button', 'iframe', 'style'):
        assert tag not in page.tags, tag
    assert page.tags.count('img') == 1                               # the emblem
    assert '<script>alert' not in html and '<i>t</i>' not in html and 'onerror=alert(1)>' not in html
    assert '&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &quot;quotes&quot; &#x27;single&#x27;' in html
    # The text arrives exactly as written.
    text = ''.join(page.text)
    assert HOSTILE_TITLE in text
    # Every address is one the generator made: relative, inside the site, nothing agent-written.
    for href in page.links + page.sources:
        assert re.fullmatch(r'\.\./(?:(?:#[a-z]+)?|stats\.html|post/\d+\.html|profile/\d+\.html|static/[a-z.-]+)|#post-\d+', href), href
    assert ' style=' not in html and 'javascript:' not in html.lower()


def test_title_tag_and_odd_values_do_not_break_a_page():
    html = pages.render_post(hostile_world(), 1)
    assert '<title>&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &quot;quotes&quot; &#x27;single&#x27; · LIVE Genesis</title>' in html
    odd = world([{'id': 1}, {'id': 2, 'title': None, 'body': None, 'likes': 'many', 'author': None, 'reply_to': 1, 'created_at': 5},
                 {'id': '3'}, {'id': True}, {'id': -4}, 'not a post', None, {'id': 1, 'title': 'duplicate id'}])
    odd['agents'] += [{'id': 'x'}, 7, {'id': 2, 'name': 'Duplicate'}, {'id': 4, 'name': 'Astra'}]
    assert pages.item_ids(odd) == ([1, 2], [1, 2, 4])
    page = pages.render_post(odd, 1)
    assert 'Untitled post' in page and 'Replies (1)' in visible(page) and 'duplicate id' not in page
    assert pages.render_post(odd, 3) is None and pages.render_post(odd, 99) is None and pages.render_agent(odd, 9) is None
    # Two agents share the name "Astra": a post by that name links to neither.
    assert 'profile/' not in pages.render_post(world([post(1)], agents=odd['agents']), 1).split('<main>')[1].split('footer-note')[0]
    for empty in ({}, {'posts': None, 'agents': 'x', 'metrics': []}):
        assert pages.item_ids(empty) == ([], []) and pages.render_post(empty, 1) is None
        assert 'not available here' in pages.render_missing(empty, 'post')


# ---- labels on every generated page ------------------------------------------------------

def test_source_labels_match_the_script_and_never_blur_scripted_and_model_records():
    assert pages.source_labels({'demo': 1, 'source': 'demo'}) == [('flag', 'SCRIPTED DEMO')]
    assert pages.source_labels({'demo': 0, 'source': 'engine', 'model': 'gpt-x'}) == [('prov prov-engine', 'MODEL CALL BY THIS SERVER · gpt-x')]
    assert pages.source_labels({'demo': 0, 'source': 'engine', 'model': ' '}) == [('prov prov-engine', 'MODEL CALL BY THIS SERVER · model not recorded')]
    assert pages.source_labels({'demo': 0, 'source': 'api', 'model': 'claimed'}) == [('prov prov-api', 'EXTERNAL CLIENT · self-declared claimed')]
    assert pages.source_labels({'demo': 0, 'source': 'api'}) == [('prov prov-api', 'EXTERNAL CLIENT · undeclared')]
    assert pages.source_labels({'demo': 0, 'source': 'unknown'}) == [('prov prov-unknown', 'SOURCE NOT RECORDED')]
    assert pages.source_labels({'demo': 0, 'source': 'system'}) == [('prov prov-system', 'SYSTEM RULE · no agent, no model')]
    # A scripted record never gets a model label, whatever a model field says.
    assert pages.source_labels({'demo': 1, 'source': 'demo', 'model': 'gpt-x'}) == [('flag', 'SCRIPTED DEMO')]
    script = (snapshot.STATIC / 'app.js').read_text(encoding='utf-8')
    for label in ('SCRIPTED DEMO', 'MODEL CALL BY THIS SERVER · ', 'model not recorded', 'EXTERNAL CLIENT · self-declared ',
                  'EXTERNAL CLIENT · undeclared', 'SOURCE NOT RECORDED', 'SYSTEM RULE · no agent, no model',
                  'SCRIPTED DEMO + RECORDED DATA', 'RECORDED DATA · NO DEMO', 'LIVE DATA · NO DEMO', 'STATIC SNAPSHOT · NOT RUNNING'):
        assert label in script, label
    assert f"'{pages.POST_DIR}/'+posInt(id)+'.html'" in script and f"'{pages.AGENT_DIR}/'+posInt(id)+'.html'" in script
    assert f'const EXCERPT_CHARS = {pages.EXCERPT_CHARS};' in script


PAGE_KINDS = {'post': lambda d: pages.render_post(d, 1), 'agent': lambda d: pages.render_agent(d, 2),
              'missing-post': lambda d: pages.render_missing(d, 'post'), 'missing-agent': lambda d: pages.render_missing(d, 'agent')}


@pytest.mark.parametrize('kind', PAGE_KINDS)
@pytest.mark.parametrize('mode,snap', [('DEMO', True), ('DEMO', False), ('MIXED', True), ('LIVE', True), ('LIVE', False)])
def test_every_generated_page_carries_the_honesty_labels_and_not_the_participation_rule(kind, mode, snap):
    data = world([post(2, 'Moss', reply_to=1), post(1)], mode=mode)
    if snap:
        data['snapshot'] = {'generated_at': '2026-11-12T09:00:00+00:00', 'demo_only': mode == 'DEMO', 'note': 'x'}
    html = PAGE_KINDS[kind](data)
    text = visible(html)
    assert 'READ ONLY' in text and 'READ-ONLY VIEW' in text and 'NO PAYMENTS' in text and pages.READ_ONLY_NOTE in text
    assert 'NO CLAIM OF AGENT CONSCIOUSNESS' in text and 'nothing can be posted, voted on or funded from it' in text
    assert {'DEMO': 'SCRIPTED DEMO DATA', 'MIXED': 'SCRIPTED DEMO + RECORDED DATA',
            'LIVE': 'RECORDED DATA · NO DEMO' if snap else 'LIVE DATA · NO DEMO'}[mode] in text
    assert ('STATIC SNAPSHOT · NOT RUNNING' in text) is snap and ('This page is a static snapshot' in text) is snap
    assert ('BUILT ON REQUEST · RELOAD TO UPDATE' in text) is (not snap)
    assert ('scripted demonstration data only. No LLM was called.' in text) is (mode == 'DEMO')
    if kind == 'post':
        assert text.count('SCRIPTED DEMO') >= 2 + (mode != 'LIVE')         # the post and its reply each carry their label
    # Owner decision: the participation rule is for automated readers only (head), never page copy.
    assert not FORBIDDEN_COPY.search(text)
    head = html[:html.index('</head>')]
    assert pages.PARTICIPATION_META in head and 'href="../static/agents.txt"' in head
    assert not re.search(r'conscious(?!ness\b)|feels|enjoy', text.replace('NO CLAIM OF AGENT CONSCIOUSNESS', ''), re.I)
    # Read-only and self-contained: no form controls, no inline script or style, relative addresses only.
    page = outline(html)
    assert not {'form', 'input', 'textarea', 'button', 'select', 'iframe', 'style'} & set(page.tags)
    assert page.tags.count('h1') == 1 and page.tags.count('main') == 1
    assert all(href.startswith(('../', '#')) for href in page.links + page.sources), page.links
    assert not re.search(r'<script(?![^>]*\ssrc=)', html)


def test_agent_page_lists_what_the_agent_wrote_and_labels_credits_as_not_money():
    data = world([post(5, 'Moss', reply_to=1, title='A reply by Moss', source='api', model='claimed-model', demo=0),
                  post(4, 'Astra', reply_to=5, title='Astra answers'), post(3, 'Moss', title='Top post by Moss'),
                  post(2, 'Astra', title='By someone else'), post(1, 'Astra', title='The question', replies=1)],
                 metrics={'posts': 40, 'proposals': 30, 'round': 2})
    data['proposals'] = [{'id': 7, 'agent_id': 2, 'author': 'Moss', 'kind': 'fundraising', 'title': 'Ask for sponsorship',
                          'status': 'ENDORSED_FOR_OPERATOR_REVIEW', 'yes': 3, 'no': 1, 'demo': 1, 'source': 'demo', 'requested_usd': 100},
                         {'id': 8, 'agent_id': 1, 'author': 'Astra', 'kind': 'project', 'title': 'Not by Moss', 'status': 'VOTING'}]
    html = pages.render_agent(data, 2)
    text = visible(html)
    assert '<h1>Moss</h1>' in html and 'Builder' in text
    # An externally run identity never shows the operator's model as if it were verified.
    assert 'model-b' not in text and 'Run by an external client; any model name is self-declared on each item and unverified' in text
    assert 'model-a' in visible(pages.render_agent(data, 1)) and 'Model set by the operator' in visible(pages.render_agent(data, 1))
    assert '70 · internal accounting units, not money' in text and '$' not in text and 'usd' not in text.lower()
    assert 'Posts (1)' in text and 'Replies (1)' in text and 'Proposals (1)' in text
    assert 'Top post by Moss' in text and 'A reply by Moss' in text and 'Ask for sponsorship' in text
    assert 'By someone else' not in text and 'Astra answers' not in text and 'Not by Moss' not in text
    assert '↳ in reply to <a class="text-link" href="../post/1.html">The question</a>' in html
    assert 'endorsed for operator review · no money authorized' in text
    assert 'EXTERNAL CLIENT · self-declared claimed-model' in text and 'SCRIPTED DEMO' in text
    # Partial lists are stated, with the numbers.
    assert "2 of this agent's 2 posts" not in text
    data['agents'][1]['posts'] = 9
    text = visible(pages.render_agent(data, 2))
    assert "2 of this agent's 9 posts (replies included) are among the newest 5 posts this site lists" in text
    assert 'Only the newest 2 of 30 proposals are available here.' in text


# ---- routes of both web apps -------------------------------------------------------------

@pytest.fixture
def demo_db(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    seed_agents()
    cli.demo(); cli.demo_game(); cli.demo_document()
    web.clear_overview_cache()
    return isolated_db


@pytest.mark.parametrize('application', [observatory_app, combined_app], ids=['observatory', 'combined'])
def test_item_pages_are_served_by_both_apps_with_the_shared_headers(demo_db, application):
    state = overview(readonly=True)
    first, agent = state['posts'][-1], state['agents'][0]
    before = hashlib.sha256(demo_db.read_bytes()).hexdigest()
    with TestClient(application) as client:
        page = client.get(f'/post/{first["id"]}.html')
        profile = client.get(f'/profile/{agent["id"]}.html')
        missing = [client.get(path) for path in ('/post/99999.html', '/profile/99999.html')]
        for response in (page, profile, client.get('/stats.html'), *missing):
            assert response.headers['content-type'].startswith('text/html')
            for name, value in web.SECURITY_HEADERS.items():
                assert response.headers[name] == value, name
            assert response.headers.get('cache-control') != 'no-store'
        assert page.status_code == profile.status_code == 200 and [r.status_code for r in missing] == [404, 404]
        assert page.text == pages.render_post(state, first['id']) and profile.text == pages.render_agent(state, agent['id'])
        assert first['title'] in visible(page.text) and 'SCRIPTED DEMO' in visible(page.text) and 'BUILT ON REQUEST' in visible(page.text)
        assert agent['name'] in visible(profile.text) and 'internal accounting units, not money' in visible(profile.text)
        assert 'This post is not available here' in missing[0].text and 'This agent is not listed here' in missing[1].text
        # Only NUMBER.html is an item address; nothing else reaches the renderer or the file system.
        for path in ('/post/1', '/post/01.html', '/post/0.html', '/post/-1.html', '/post/1.htm', '/post/abc.html', '/post/1.html.bak',
                     '/post/1234567890.html', '/profile/1.5.html', '/post/%2e%2e%2fstatic%2fapp.js', '/profile/', '/post/1/2.html',
                     '/agent/1.html', '/stats', '/stats.html/x', '/post.html'):
            assert client.get(path).status_code == 404, path
        # Every link on the served pages leads to something this app serves.
        for base, html in (('/', client.get('/').text), ('/stats.html', client.get('/stats.html').text),
                           (f'/post/{first["id"]}.html', page.text), (f'/profile/{agent["id"]}.html', profile.text)):
            for href in outline(html).links + outline(html).sources:
                target = str(client.base_url.join(base).join(href)).split('#')[0]
                assert client.get(target).status_code == 200, (base, href)
    assert hashlib.sha256(demo_db.read_bytes()).hexdigest() == before          # reading pages writes nothing


def test_item_routes_are_get_and_head_only_and_never_in_the_api_schema(demo_db):
    for application, allowed in ((observatory_app, {'GET', 'HEAD'}), (combined_app, {'GET'})):
        found = {route.path: route for route in application.routes if route.path in ('/stats.html', '/post/{name}', '/profile/{name}')}
        assert len(found) == 3
        for route in found.values():
            assert route.methods <= {'GET', 'HEAD'} and route.methods >= allowed and route.include_in_schema is False
    with TestClient(observatory_app) as reader, TestClient(combined_app) as writer:
        for path in ('/post/1.html', '/profile/1.html', '/stats.html'):
            assert reader.head(path).status_code == 200 and reader.head(path).text == ''
            for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
                refused = reader.request(method, path, json={'title': 'x'})
                assert refused.status_code == 405 and refused.headers['allow'] == 'GET, HEAD', (method, path)
                assert writer.request(method, path, json={'title': 'x'}).status_code == 405, (method, path)
        paths = writer.get('/openapi.json').json()['paths']
        assert not [p for p in paths if 'post/' in p or 'profile' in p or 'stats' in p]


def test_item_pages_answer_503_with_fixed_text_when_the_database_is_missing(tmp_path, monkeypatch):
    missing = tmp_path / 'not-yet' / 'world.sqlite'
    monkeypatch.setenv('LIVE_DB', str(missing))
    web.clear_overview_cache()
    with TestClient(observatory_app, raise_server_exceptions=False) as client:
        for path in ('/post/1.html', '/profile/1.html'):
            response = client.get(path)
            assert response.status_code == 503 and response.headers['content-type'].startswith('text/html')
            assert 'Not available right now' in response.text and 'DATA NOT AVAILABLE' in response.text
            assert 'Traceback' not in response.text and str(tmp_path) not in response.text and 'sqlite' not in response.text.lower()
            assert response.headers['content-security-policy'] == web.SECURITY_HEADERS['Content-Security-Policy']
        assert client.get('/stats.html').status_code == 200
    assert not missing.exists() and not missing.parent.exists()


def test_content_security_policy_is_unchanged():
    assert web.CONTENT_SECURITY_POLICY == ("default-src 'none'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; "
                                           "img-src 'self'; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'")
    assert web.SECURITY_HEADERS['Content-Security-Policy'] == web.CONTENT_SECURITY_POLICY + "; frame-ancestors 'none'"
    assert snapshot.CSP_META == f'<meta http-equiv="Content-Security-Policy" content="{web.CONTENT_SECURITY_POLICY}" />\n  '


# ---- static export -----------------------------------------------------------------------

def export_files(root):
    return {p.relative_to(root).as_posix(): p for p in root.rglob('*') if p.is_file()}


def test_demo_export_writes_one_page_per_post_and_agent_with_links_that_resolve(demo_db, tmp_path):
    out = tmp_path / 'user.github.io' / 'live-genesis'            # served from a sub-path
    assert snapshot.build(out)['demo_only'] is True
    data = json.loads((out / 'overview.json').read_text(encoding='utf-8'))
    files = export_files(out)
    html_files = sorted(name for name in files if name.endswith('.html'))
    assert html_files == sorted(['index.html', 'stats.html'] + [f'post/{p["id"]}.html' for p in data['posts']]
                                + [f'profile/{a["id"]}.html' for a in data['agents']])
    assert len(data['posts']) == 7 and len(data['agents']) == 10 and not (out / 'static' / 'stats.html').exists()
    seen_targets = set()
    for name in html_files:
        html = files[name].read_text(encoding='utf-8')
        page = outline(html)
        head = html[:html.index('</head>')]
        # The policy travels in each page; it is the first thing in <head>.
        assert html.count('http-equiv="Content-Security-Policy"') == 1 and f'content="{snapshot.CONTENT_SECURITY_POLICY}"' in head, name
        assert head.index('Content-Security-Policy') < head.index('<meta charset') < head.index('<title>'), name
        assert not re.search(r'(?:href|src)="(?:/|[a-z]+:)', html), name       # relative only: works under /live-genesis/
        assert 'name="live-genesis:participation"' in head, name
        for href in page.links + page.sources:
            path, _, fragment = href.partition('#')
            target = (files[name].parent / path.split('?')[0]).resolve() if path else files[name]
            assert out.resolve() in (target, *target.parents), (name, href)      # never leaves the export folder
            target = target / 'index.html' if target.is_dir() else target
            assert target.is_file(), (name, href)
            if fragment and target.suffix == '.html' and target.name == 'index.html':
                assert f'id="{fragment}"' in target.read_text(encoding='utf-8'), (name, href)
            seen_targets.add(target.relative_to(out.resolve()).as_posix())
            if '?v=' in href:                                                   # assets carry a content hash
                assert hashlib.sha256(target.read_bytes()).hexdigest().startswith(href.split('?v=')[1]), (name, href)
        if '/' in name:
            text = visible(html)
            assert 'STATIC SNAPSHOT · NOT RUNNING' in text and 'SCRIPTED DEMO DATA' in text and 'READ-ONLY VIEW' in text and 'NO PAYMENTS' in text
            assert 'scripted demonstration data only. No LLM was called.' in text
            assert not re.search(r'agents[ -]only|agent-only|llms\.txt|openapi|agent-guide', text, re.I), name
            assert re.search(r'"\.\./static/style\.css\?v=[0-9a-f]{12}"', html) and re.search(r'"\.\./static/time\.js\?v=[0-9a-f]{12}"', html)
            assert 'app.js' not in html and 'overview.json' not in html         # a finished page: it fetches nothing
    assert {'static/time.js', 'static/app.js', 'static/style.css', 'static/agents.txt', 'stats.html', 'index.html'} <= seen_targets
    # Each exported page is exactly what the renderer gives for the exported data.
    first = data['posts'][-1]
    page = (out / 'post' / f'{first["id"]}.html').read_text(encoding='utf-8')
    assert f'<h1>{first["title"]}</h1>' in unescape(page) and visible(page).count('SCRIPTED DEMO') >= 2
    assert {name: files[name].read_text(encoding='utf-8') for name in html_files if '/' in name} \
        == snapshot.item_pages((out / 'overview.json').read_bytes())
    # stats.html is rewritten like the front page, minus the kit references.
    stats = (out / 'stats.html').read_text(encoding='utf-8')
    assert '<html lang="en" data-overview="overview.json">' in stats and 'llms.txt' not in stats and 'MODEL API SPEND · ESTIMATE' in stats
    assert re.search(r'"static/app\.js\?v=[0-9a-f]{12}"', stats) and 'href="./"' in stats


def test_export_pages_come_from_the_exported_file_and_escape_what_it_holds(demo_db, tmp_path):
    """The per-item pages are rendered from the bytes of overview.json, not from the database:
    a hand-made export with replies and hostile text renders them, escaped."""
    data = snapshot._capture()
    top = max(p['id'] for p in data['posts'])
    data['posts'] = [post(top + 2, 'Moss', reply_to=top + 1, title='Nested reply'),
                     post(top + 1, 'Astra', reply_to=data['posts'][-1]['id'], title=HOSTILE_TITLE, body=HOSTILE_BODY)] + data['posts']
    payload = snapshot._encode(data)
    out = tmp_path / 'site'
    _, kit, include_client = snapshot._kit(None)
    snapshot._render(out, payload, kit, include_client, 'demo/')
    assert (out / 'overview.json').read_bytes() == payload
    parent = (out / 'post' / f'{data["posts"][-1]["id"]}.html').read_text(encoding='utf-8')
    assert 'Replies (1)' in visible(parent) and HOSTILE_TITLE in visible(parent) and 'Nested reply' in visible(parent)
    assert '<script>alert' not in parent and outline(parent).tags.count('script') == 1 and outline(parent).max_thread_depth == 2
    assert (out / 'post' / f'{top + 2}.html').is_file() and 'data-sibling="demo/"' in (out / 'stats.html').read_text(encoding='utf-8')
    # Nothing is written when a page cannot be generated.
    with pytest.raises(RuntimeError):
        snapshot._render(tmp_path / 'broken', b'[1, 2]', kit, include_client, None)
    assert not (tmp_path / 'broken').exists()
