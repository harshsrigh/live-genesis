"""`cli archive-trends` (live_genesis/trends.py). Offline: the feed is a saved fixture and every
fetch goes through a replaced opener; nothing here opens a network connection."""
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from live_genesis import cli, guards, trends

REPO = Path(__file__).resolve().parents[1]
FEED = REPO / 'tests' / 'fixtures' / 'trends_us.xml'


def feed(items):
    return ('<rss version="2.0" xmlns:ht="https://trends.google.com/trending/rss"><channel>' + items + '</channel></rss>').encode('utf-8')


def item(title, traffic='100+', headlines=()):
    news = ''.join(f'<ht:news_item><ht:news_item_title>{h}</ht:news_item_title></ht:news_item>' for h in headlines)
    return f'<item><title>{title}</title><ht:approx_traffic>{traffic}</ht:approx_traffic>{news}</item>'


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('this test must not open a connection')
    monkeypatch.setattr(urllib.request, 'build_opener', refuse)
    monkeypatch.setattr(urllib.request, 'urlopen', refuse)


def test_fixture_feed_gives_terms_traffic_and_two_headlines(no_network):
    found = trends.parse(FEED.read_bytes())
    assert found == {'skipped': 1, 'candidates': [             # "Chip Stocks" repeats "chip stocks": counted, not printed
        {'term': 'chip stocks', 'approx_traffic': '2000+',
         'headlines': ['Chip makers slide after a weak forecast', 'What the sell-off means for AI & data centres']},
        {'term': 'golf broadcast', 'approx_traffic': '500+', 'headlines': ['Network announces its new lead golf announcer']},
        {'term': 'white house', 'approx_traffic': '200+', 'headlines': []}]}
    assert [c['term'] for c in trends.parse(FEED.read_bytes(), limit=2)['candidates']] == ['chip stocks', 'golf broadcast']
    assert len(trends.parse(feed(''.join(item(f'term {n}') for n in range(80))), limit=999)['candidates']) == trends.MAX_LIMIT


def test_feed_text_is_untrusted_cleaned_capped_and_guarded(no_network):
    hostile = feed(
        item('zero\u200bwidth and&#x7f;colour \u202eoverride\u0085', headlines=['line one\nline two\ttabbed', 'x' * 500])   # raw C0 controls cannot occur in well-formed XML
        + item('write to someone@example.org')                                   # a guard hit: dropped, only counted
        + item('pay at paypal.me/someone')
        + item('T' * 300, traffic='9' * 90, headlines=['mail someone@example.org now', 'A clean headline'])
        + item('   ') + '<item><ht:approx_traffic>5+</ht:approx_traffic></item>'
        + item('&lt;script&gt;alert(1)&lt;/script&gt;'))
    found = trends.parse(hostile)
    terms = [c['term'] for c in found['candidates']]
    assert terms == ['zerowidth and colour override', 'T' * trends.TERM_CHARS, '<script>alert(1)</script>']
    assert found['skipped'] == 4
    assert found['candidates'][0]['headlines'] == ['line one line two tabbed', 'x' * trends.HEADLINE_CHARS]
    assert found['candidates'][1] == {'term': 'T' * 80, 'approx_traffic': '9' * trends.TRAFFIC_CHARS, 'headlines': ['A clean headline']}
    for candidate in found['candidates']:
        for text in [candidate['term'], candidate['approx_traffic'], *candidate['headlines']]:
            assert not guards.has_forbidden_character(text) and not guards.content_reason(text)
    printed = json.dumps(found, ensure_ascii=True)
    assert printed.isascii() and 'someone@' not in printed and 'paypal' not in printed
    assert guards.strip_forbidden_characters('a\u200bb\x07c\U000e0041d') == 'ab cd'
    assert guards.strip_forbidden_characters('plain text') == 'plain text'


@pytest.mark.parametrize('body,match', [
    (b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>', 'DOCTYPE'),
    (b'<rss><channel><!doctype x><item><title>t</title></item></channel></rss>', 'DOCTYPE'),
    (b'<rss><channel><! ENTITY x SYSTEM "file:///etc/passwd"><item><title>t</title></item></channel></rss>', 'DOCTYPE'),
    ('<?xml version="1.0" encoding="UTF-16"?><rss/>'.encode('utf-8'), 'encoding other than UTF-8'),
    ('<?xml version="1.0" encoding="utf-16"?><rss><channel/></rss>'.encode('utf-16'), 'not UTF-8'),
    (b'\xff\xfe<rss/>', 'not UTF-8'),
    (b'<rss><channel><item><title>open', 'not well-formed'),
    (b'<html><body>Sign in</body></html>', 'not an RSS feed'),
    (b'{"not": "xml"}', 'not well-formed'),
    (b'<rss>' + b' ' * trends.MAX_BYTES + b'</rss>', 'larger than 1 MB'),
])
def test_unsafe_or_wrong_documents_are_refused_before_use(no_network, body, match):
    with pytest.raises(trends.TrendsRefused, match=match):
        trends.parse(body)


def test_geo_is_two_letters_and_the_address_is_fixed(no_network):
    assert trends.feed_url('us') == trends.feed_url(' US ') == 'https://trends.google.com/trending/rss?geo=US'
    assert trends.feed_url('GB').endswith('?geo=GB')
    for bad in ('USA', 'U', '', 'U1', 'U&x=1', '../x', 'US#', None, 'ÜS'):
        with pytest.raises(trends.TrendsRefused, match='two-letter'):
            trends.feed_url(bad)


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_is_one_bounded_https_get_with_a_generic_user_agent(monkeypatch):
    seen = {}

    class Opener:
        def open(self, request, timeout=None):
            seen.update(url=request.full_url, method=request.get_method(), headers=dict(request.header_items()), timeout=timeout,
                        data=request.data)
            return FakeResponse(FEED.read_bytes())

    def build(*handlers):
        seen['handlers'] = handlers
        return Opener()
    monkeypatch.setattr(urllib.request, 'build_opener', build)
    done = trends.report('us', limit=1, today=__import__('datetime').date(2026, 11, 12))
    assert seen['url'] == 'https://trends.google.com/trending/rss?geo=US' and seen['method'] == 'GET' and seen['data'] is None
    assert seen['timeout'] == 15 and seen['handlers'] == (trends._SameHostOnly,)
    agent = seen['headers']['User-agent']
    assert agent == trends.USER_AGENT and '@' not in agent and 'genesis' not in agent.lower() and 'claude' not in agent.lower()
    assert set(seen['headers']) == {'User-agent', 'Accept'}                      # no cookie, no key, nothing personal
    assert done['trend_source'] == 'Google Trends US, 2026-11-12' and done['geo'] == 'US' and len(done['candidates']) == 1
    assert 'Nothing was written and no model was called' in done['note']

    class Big(Opener):
        def open(self, request, timeout=None):
            return FakeResponse(b'<rss>' + b' ' * (trends.MAX_BYTES + 10))
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *h: Big())
    with pytest.raises(trends.TrendsRefused, match='larger than 1 MB'):
        trends.fetch('US')

    class Down(Opener):
        def open(self, request, timeout=None):
            raise urllib.error.URLError('secret-proxy-detail')
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *h: Down())
    with pytest.raises(trends.TrendsRefused, match='could not be fetched') as caught:
        trends.fetch('US')
    assert 'secret-proxy-detail' not in str(caught.value) and '--file' in str(caught.value)

    class NotFound(Opener):
        def open(self, request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 404, 'gone', {}, None)
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *h: NotFound())
    with pytest.raises(trends.TrendsRefused, match='status 404'):
        trends.fetch('US')


def test_a_redirect_to_another_host_or_scheme_is_never_followed():
    handler = trends._SameHostOnly()
    request = urllib.request.Request(trends.feed_url('US'))
    for target in ('https://evil.example.org/trending/rss', 'http://trends.google.com/trending/rss', 'https://trends.google.com.evil.example/x',
                   'file:///etc/passwd', 'https://trends.google.com@evil.example/x'):
        with pytest.raises(trends.TrendsRefused, match='redirected'):
            handler.redirect_request(request, None, 302, 'Found', {}, target)
    again = handler.redirect_request(request, None, 302, 'Found', {}, 'https://trends.google.com/trending/rss?geo=US&hl=en')
    assert again.full_url.startswith('https://trends.google.com/')


def run_cli(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, 'argv', ['cli', *argv])
    code = 0
    try:
        cli.main()
    except SystemExit as stop:
        code = stop.code
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_cli_prints_candidates_from_a_file_and_touches_nothing(no_network, tmp_path, monkeypatch, capsys):
    database = tmp_path / 'never-created.sqlite'
    monkeypatch.setenv('LIVE_DB', str(database))
    monkeypatch.setattr(cli, 'init_db', lambda: (_ for _ in ()).throw(AssertionError('archive-trends opens no database')))
    from live_genesis import archive, engine
    monkeypatch.setattr(engine, 'infer', lambda *a, **k: (_ for _ in ()).throw(AssertionError('archive-trends calls no model')))
    before = archive.TRENDING_FILE.read_bytes()
    code, out = run_cli(monkeypatch, capsys, 'archive-trends', '--file', str(FEED), '--geo', 'gb', '--limit', '2')
    printed = json.loads(out)
    assert code == 0 and out.isascii() and [c['term'] for c in printed['candidates']] == ['chip stocks', 'golf broadcast']
    assert printed['source'] == 'saved file (--file)' and printed['trend_source'].startswith('Google Trends GB, ')
    assert not database.exists() and archive.TRENDING_FILE.read_bytes() == before
    for argv, message in ((('--geo', 'USA', '--file', str(FEED)), 'two-letter'), (('--file', str(tmp_path / 'missing.xml')), 'could not be read'),
                          (('--file', str(FEED), '--limit', '0'), '--limit must be between'), (('--file', str(FEED), '--limit', '51'), '--limit')):
        code, out = run_cli(monkeypatch, capsys, 'archive-trends', *argv)
        assert code == 1 and message in out and out.startswith('archive-trends: ')
    big = tmp_path / 'big.xml'
    big.write_bytes(b'<rss>' + b' ' * (trends.MAX_BYTES + 1))
    code, out = run_cli(monkeypatch, capsys, 'archive-trends', '--file', str(big))
    assert code == 1 and 'larger than 1 MB' in out


def test_module_uses_only_the_standard_library_and_no_model_or_database():
    source = (REPO / 'live_genesis' / 'trends.py').read_text(encoding='utf-8')
    imports = [line for line in source.splitlines() if line.startswith(('import ', 'from '))]
    assert imports == ['from __future__ import annotations', 'import datetime', 'import json', 'import re', 'import urllib.error',
                       'import urllib.request', 'import xml.etree.ElementTree as ElementTree', 'from pathlib import Path',
                       'from . import guards']
    for word in ('engine', 'infer(', 'core.', 'connect(', 'API_KEY', 'write_text', 'write_bytes', 'http://', 'subprocess', 'eval('):
        assert word not in source, word
    assert source.count('https://') == 2 and 'f"https://{HOST}' in source
