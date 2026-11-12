"""Items left open by the two security audits, now closed. Offline and deterministic; no model, no network.

1 per-author cap in agent contexts, 2 hidden characters in rows older than the character rule (read-side
stripping and `cli scrub-hidden`), 3 look-alike letters, 4 per-address limit on the public routes, and no
HTTP redirect followed by any provider client. Agent ids follow the seed order (1 Astra, 2 Moss, ...).
"""
import json
import sys
import types
import unicodedata

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from live_genesis import cli, controls, core, documents, engine, guards, memory, pages, snapshot, web
from live_genesis.app import app
from live_genesis.core import agent_context, connect, open_round, overview, seed_agents, submit_action, update_memory
from live_genesis.observatory import app as observatory_app


def act(agent_id, action, **kwargs):
    kwargs.setdefault('quota', False)
    return submit_action(agent_id, action, cooldown=False, **kwargs)


def post(agent_id, n, **fields):
    return act(agent_id, {'action': 'post', 'title': f'Note {agent_id}-{n}', 'body': f'Agent {agent_id} writes entry {n} about cedar and quartz.', **fields})['id']


def propose(agent_id, n):
    return act(agent_id, {'action': 'propose', 'kind': 'project', 'title': f'Plan {agent_id}-{n}',
                          'body': f'Agent {agent_id} plans step {n} for the harbor shelves.'})['id']


def refusal(call):
    with pytest.raises(HTTPException) as err:
        call()
    return err.value.status_code, err.value.detail


def name_of(agent_id):
    with connect() as cx:
        return cx.execute('SELECT name FROM agents WHERE id=?', (agent_id,)).fetchone()[0]


# ---- 1. Per-author cap -------------------------------------------------------------------

def flooded_world():
    """Three agents write two posts and one proposal each; then agent 2 floods: 30 posts, 5 replies, 12 proposals."""
    seed_agents()
    quiet = [post(a, n) for a in (3, 4, 5) for n in range(2)]
    quiet_plans = [propose(a, 0) for a in (3, 4, 5)]
    flood = [post(2, n) for n in range(30)]
    flood += [act(2, {'action': 'reply', 'post_id': quiet[0], 'body': f'Agent 2 answers again, round {n} of the flood.'})['id'] for n in range(5)]
    flood_plans = [propose(2, n) for n in range(12)]
    return quiet, quiet_plans, flood, flood_plans


def test_a_flood_from_one_author_cannot_crowd_out_the_others(isolated_db, monkeypatch):
    quiet, quiet_plans, flood, flood_plans = flooded_world()
    assert memory.context_per_author() == memory.CONTEXT_PER_AUTHOR == 2
    for reader in (1, 6):
        ctx = agent_context(reader)
        posts, plans = ctx['recent_posts'], ctx['proposals']
        by_flooder = [p['id'] for p in posts if p['author'] == name_of(2)]
        assert by_flooder == flood[-1:-3:-1]                     # exactly the cap, and its NEWEST two (replies are posts)
        assert set(quiet) <= {p['id'] for p in posts}            # every post of the quiet authors is still shown
        assert [p['id'] for p in plans if p['author'] == name_of(2)] == flood_plans[-1:-3:-1]
        assert set(quiet_plans) <= {p['id'] for p in plans}
        assert len(posts) <= memory.CONTEXT_POSTS and len(plans) <= memory.CONTEXT_PROPOSALS
        assert len({p['id'] for p in posts}) == len(posts) and posts[0]['id'] == flood[-1]   # no item twice; newest first
        for items in (posts, plans):
            counts = {}
            for item in items:
                counts[item['author']] = counts.get(item['author'], 0) + 1
            assert max(counts.values()) <= 2
    # Without the cap the same world shows the reader nothing but the flooder (what the audits reported).
    monkeypatch.setenv('LIVE_CONTEXT_PER_AUTHOR', '8')
    uncapped = agent_context(1)
    assert {p['author'] for p in uncapped['recent_posts']} == {name_of(2)} and len(uncapped['recent_posts']) == 8
    assert {p['author'] for p in uncapped['proposals']} == {name_of(2)}


def test_own_items_are_never_counted_or_dropped(isolated_db):
    seed_agents()
    own = [post(2, n) for n in range(6)]
    ctx = agent_context(2)
    assert [p['id'] for p in ctx['recent_posts']] == own[::-1]   # all six of its own, although the cap is two
    assert len(agent_context(1)['recent_posts']) == 2            # another reader gets two of them


def test_total_size_bounds_are_unchanged(isolated_db):
    seed_agents()
    for n in range(3):
        for agent in range(1, 11):
            act(agent, {'action': 'post', 'title': f'Long {agent}-{n}', 'body': f'Entry {agent}-{n}: ' + 'meadow lantern orchard pebble ' * 38})
            propose(agent, n)
    ctx = agent_context(1)
    assert len(ctx['recent_posts']) == memory.CONTEXT_POSTS == 8 and len(ctx['proposals']) == memory.CONTEXT_PROPOSALS == 6   # still filled
    prompt = engine.build_prompt(engine.engine_context(ctx))
    assert len(prompt.encode()) <= engine.INPUT_BYTE_LIMIT - len(engine.constitution().encode())
    assert engine.INPUT_BYTE_LIMIT == 24000 and memory.NOTES_CHARS == 1500


def test_engine_prompt_and_http_context_show_the_same_capped_selection(isolated_db):
    seed_agents()
    keys = {}
    with connect() as cx:  # a known key for agent 1 (seed keys are not kept by this test)
        import hashlib
        cx.execute('UPDATE agents SET key_hash=? WHERE id=1', (hashlib.sha256(b'live_test_key_for_agent_one_only').hexdigest(),))
    keys['Authorization'] = 'Bearer live_test_key_for_agent_one_only'
    for agent in (3, 4):
        post(agent, 0)
    for n in range(12):
        post(2, n)
    direct = [p['id'] for p in agent_context(1)['recent_posts']]
    prompt = [p['id'] for p in json.loads(engine.build_prompt(engine.engine_context(agent_context(1))))['recent_posts']]
    with TestClient(app) as client:
        served = [p['id'] for p in client.get('/api/agent/context', headers=keys).json()['recent_posts']]
    assert direct == prompt == served and len(direct) == 4       # two of the flooder, one each of the two others


@pytest.mark.parametrize('raw,expected', [('', 2), ('3', 3), ('0', 1), ('-4', 1), ('99', 8), ('many', 2), ('1.5', 2)])
def test_the_cap_is_a_clamped_setting(isolated_db, monkeypatch, raw, expected):
    monkeypatch.setenv('LIVE_CONTEXT_PER_AUTHOR', raw)
    assert memory.context_per_author() == expected
    seed_agents()
    for n in range(10):
        post(2, n)
    assert len(agent_context(1)['recent_posts']) == min(expected, 8)


# ---- 2. Rows older than the character rule -------------------------------------------------

ZW, TAG, RLO = '​', '\U000e0041', '‮'
MARK = 'quietmarker'


def old_world(monkeypatch):
    """A database as one written BEFORE the character rule could hold it: direct SQL for the plain tables, and a
    shared document recorded while the rule is switched off (so its revision hashes are valid, as an old row's are)."""
    seed_agents()
    act(1, {'action': 'post', 'title': 'A clean post', 'body': 'Nothing hidden in this one at all.'})
    with monkeypatch.context() as patch:
        patch.setattr(guards, 'check_characters', lambda *args, **kwargs: None)
        doc = act(3, {'action': 'doc_create', 'title': 'Old guide', 'body': f'First {MARK} line{ZW}{TAG} of the old guide.\nSecond line{RLO} stays.'})
    with connect() as cx:
        cx.execute("INSERT INTO posts(agent_id,topic,title,body,demo,created_at) VALUES (2,'community',?,?,0,?)",
                   (f'Old {MARK} title{ZW}', f'Old body{TAG} line one\nline two{RLO} here\x07.', core.utcnow()))
        cx.execute("INSERT INTO proposals(agent_id,title,body,kind,created_at) VALUES (2,?,?,'project',?)",
                   (f'Old plan{ZW}', f'Old plan body{TAG} text.', core.utcnow()))
        cx.execute("INSERT INTO events(agent_id,verb,summary,demo,created_at) VALUES (2,'post',?,0,?)", (f'published · Old title{ZW}', core.utcnow()))
        cx.execute("INSERT INTO memory_notes(agent_id,round_id,note,created_at) VALUES (1,1,?,?)", (f'An old note{ZW} of mine.', core.utcnow()))
        cx.execute("UPDATE agents SET memory=? WHERE id=1", (f'Current memory{TAG} text.',))
        cx.execute("INSERT INTO operator_audit(operation,agent_id,created_at) VALUES (?,NULL,?)", (f'an old audit row{ZW}', core.utcnow()))
    return doc


def hidden_in(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return any(mark in text for mark in (ZW, TAG, RLO, '\x07'))


def stored(sql):
    with connect() as cx:
        return [tuple(r) for r in cx.execute(sql)]


def test_every_read_path_strips_hidden_characters_from_old_rows(isolated_db, monkeypatch):
    old_world(monkeypatch)
    state, ctx = overview(), agent_context(1)
    assert not hidden_in(state) and not hidden_in(ctx)
    # The visible text is all there, and line breaks of a body survive.
    old_post = next(p for p in state['posts'] if MARK in p['title'])
    assert old_post['title'] == f'Old {MARK} title' and old_post['body'] == 'Old body line one\nline two here .'
    assert any(MARK in d.get('title', '') + json.dumps(d) for d in state['documents'])   # the protected document is shown, cleaned
    assert ctx['identity']['memory'] == 'Current memory text.' and ctx['memory_notes'][0]['note'] == 'An old note of mine.'
    assert MARK in json.dumps(ctx['documents']) and MARK in json.dumps(ctx['recent_posts'])
    # The prompt an engine turn would send, the search results, the pages and both HTTP overviews.
    assert not hidden_in(engine.build_prompt(engine.engine_context(ctx)))
    found = memory.search_world(None, f'{MARK} old guide plan')
    assert {r['kind'] for r in found} == {'post', 'document', 'proposal'} and not hidden_in(found)
    assert not hidden_in(memory.search_notes(1, 'old note'))
    assert not hidden_in(pages.render_post(state, old_post['id'])) and not hidden_in(pages.render_agent(state, 2))
    for application in (app, observatory_app):
        with TestClient(application) as client:
            assert not hidden_in(client.get('/api/overview').text) and not hidden_in(client.get(f"/post/{old_post['id']}.html").text)
    # Reading changed nothing in the database.
    assert hidden_in(stored('SELECT title,body FROM posts')) and hidden_in(stored('SELECT body FROM document_revisions'))


def test_clean_text_is_returned_unchanged_by_the_read_side():
    ordinary = 'Zoë 日本語 مرحبا \U0001F469‍\U0001F469‍\U0001F467 ✌️ 1️⃣ na‌me\nline two\ttab\r\nline three'
    assert guards.strip_forbidden_characters(ordinary, multiline=True) == ordinary
    node = {'a': [ordinary, 3, None, {'b': ordinary}], 'n': 1.5, 't': True}
    assert guards.clean_for_reading(node) == node
    for dirty in ('a\x00b', f'x{ZW}y', f'x{TAG}', 'a️', f'❤{ZW}️{ZW}️', 'cr\r alone', '', '\ud800'):
        cleaned = guards.strip_forbidden_characters(dirty, multiline=True)
        assert not guards.has_forbidden_character(cleaned, multiline=True), ascii(dirty)
    assert guards.strip_forbidden_characters('a\nb') == 'a b' and guards.strip_forbidden_characters('a\nb', multiline=True) == 'a\nb'


def test_an_old_row_cannot_block_an_export(isolated_db, monkeypatch, tmp_path):
    old_world(monkeypatch)
    out = tmp_path / 'published'
    snapshot.export_published(out)                       # refused before the read-side stripping
    payload = (out / 'overview.json').read_bytes()
    assert payload.isascii() and not hidden_in(json.loads(payload)) and MARK in payload.decode()
    snapshot.approve_published(out, confirmed=True)
    assert snapshot.check_published(out)['reviewed'] is True


def test_scrub_hidden_reports_without_text_and_changes_nothing(isolated_db, monkeypatch, capsys):
    old_world(monkeypatch)
    before = stored('SELECT * FROM posts') + stored('SELECT * FROM memory_notes') + stored('SELECT id,operation FROM operator_audit')
    report = controls.scrub_hidden()
    found = {(e['table'], e['field']): e for e in report['found']}
    assert set(found) == {('posts', 'title'), ('posts', 'body'), ('proposals', 'title'), ('proposals', 'body'), ('events', 'summary'),
                          ('memory_notes', 'note'), ('agents', 'memory'), ('document_revisions', 'body'), ('operator_audit', 'operation')}
    assert all(e['rows'] == 1 and len(e['row_ids']) == 1 for e in found.values())
    assert {key for key, e in found.items() if e['action'] == 'left'} == {('document_revisions', 'body'), ('operator_audit', 'operation')}
    assert all(e['action'] == 'would change' for key, e in found.items() if key[0] not in ('document_revisions', 'operator_audit'))
    assert report == {**report, 'applied': False, 'values_changed': 0, 'values_left': 2, 'values_would_change': 7}
    text = json.dumps(report, ensure_ascii=False)
    assert MARK not in text and 'Old' not in text and not hidden_in(text)      # counts, names and ids only
    assert before == stored('SELECT * FROM posts') + stored('SELECT * FROM memory_notes') + stored('SELECT id,operation FROM operator_audit')
    # The command prints the same and reminds the operator of the backup.
    monkeypatch.setattr(sys, 'argv', ['live_genesis.cli', 'scrub-hidden'])
    cli.main()
    printed = capsys.readouterr().out
    assert '"applied": false' in printed and 'nothing was changed' in printed and 'backup' in printed and MARK not in printed
    assert printed.isascii()


def test_scrub_hidden_apply_rewrites_display_fields_and_leaves_protected_rows(isolated_db, monkeypatch, capsys):
    doc = old_world(monkeypatch)
    revisions, audit_rows = stored('SELECT * FROM document_revisions'), stored('SELECT id,operation FROM operator_audit')
    monkeypatch.setattr(sys, 'argv', ['live_genesis.cli', 'scrub-hidden', '--apply'])
    cli.main()
    printed = capsys.readouterr().out
    report = json.loads(printed[:printed.rindex('}') + 1])
    assert report['applied'] is True and report['values_changed'] == 7 and report['values_left'] == 2 and MARK not in printed
    assert 'Rewrote 7 value(s); left 2' in printed
    assert not hidden_in(stored('SELECT title,body FROM posts') + stored('SELECT title,body FROM proposals') + stored('SELECT summary FROM events')
                         + stored('SELECT note FROM memory_notes') + stored('SELECT memory FROM agents'))
    assert stored('SELECT title,body FROM posts WHERE id=2') == [(f'Old {MARK} title', 'Old body line one\nline two here .')]
    assert stored('SELECT title,body FROM posts WHERE id=1') == [('A clean post', 'Nothing hidden in this one at all.')]
    # Protected rows are byte-identical, their hash chain still verifies, and the audit log only grew by one row.
    assert stored('SELECT * FROM document_revisions') == revisions and documents.verify(doc['document_id']) == []
    audit_now = stored('SELECT id,operation FROM operator_audit')
    assert audit_now[:len(audit_rows)] == audit_rows and len(audit_now) == len(audit_rows) + 1
    assert audit_now[-1][1] == 'scrub-hidden apply: 7 value(s) rewritten, 2 left'
    for entry in report['found']:
        assert ('append-only' in entry['why']) == (entry['table'] in ('document_revisions', 'operator_audit'))
    # A second run finds only what must stay, and still shows it cleaned.
    again = controls.scrub_hidden()
    assert {(e['table'], e['action']) for e in again['found']} == {('document_revisions', 'left'), ('operator_audit', 'left')}
    assert not hidden_in(overview()) and not hidden_in(agent_context(1))


def test_scrub_hidden_never_updates_a_field_a_trigger_guards(isolated_db, monkeypatch):
    seed_agents()
    with connect() as cx:
        assert controls._update_protected(cx, 'document_revisions') is None and controls._update_protected(cx, 'credit_ledger') is None
        assert controls._update_protected(cx, 'archive_answers') is None and controls._update_protected(cx, 'posts') == set()
        assert 'title' in controls._update_protected(cx, 'documents') and 'title' not in controls._update_protected(cx, 'proposals')
    # Even a listed field is left when a trigger names it.
    monkeypatch.setitem(controls.SCRUB_FIELDS, 'documents', ('title',))
    with monkeypatch.context() as patch:
        patch.setattr(guards, 'check_characters', lambda *args, **kwargs: None)
        act(3, {'action': 'doc_create', 'title': f'Old guide{ZW}', 'body': 'A plain first line of the old guide.'})
    report = controls.scrub_hidden(apply=True)
    assert [(e['table'], e['field'], e['action']) for e in report['found'] if e['table'] == 'documents'] == [('documents', 'title', 'left')]
    assert stored('SELECT title FROM documents') == [(f'Old guide{ZW}',)] and not hidden_in(overview()['documents'])
    assert 'backup' in cli_help(monkeypatch).lower()


def cli_help(monkeypatch):
    import contextlib
    import io
    monkeypatch.setattr(sys, 'argv', ['live_genesis.cli', 'scrub-hidden', '--help'])
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
        cli.main()
    return out.getvalue()


# ---- 3. Look-alike letters ----------------------------------------------------------------

CYR_A, CYR_O, CYR_E, CYR_P, CYR_C, CYR_K, GRK_O, GRK_A = 'а', 'о', 'е', 'р', 'с', 'к', 'ο', 'α'
DISGUISED_LINK = f'Support the garden at p{CYR_A}yp{CYR_A}l.me/garden today.'
ORDINARY_TEXTS = [
    'Привет, как дела? Это обычный русский текст о науке.',            # whole words in Cyrillic
    'Καλημέρα κόσμε, αυτό είναι ένα κανονικό κείμενο.',                 # whole words in Greek
    'Zoë naïve café señor Ångström crème brûlée façade.',               # accented Latin
    '日本語のテキストと中文文本，还有한국어 문장.',                        # CJK
    'Привет world, καλημέρα friends: 日本語 too.',                      # several scripts, each word in one
    'A garden \U0001F331 note ❤️ with \U0001F469‍\U0001F469‍\U0001F467 and 1️⃣.',  # emoji
    'Measured 5 µm and 20 °C; ratio α-helix to β-sheet unchanged.',     # micro sign, Greek letters as separate words
]


def test_the_confusables_table_is_cyrillic_and_greek_to_ascii_letters():
    for code, latin in guards._CONFUSABLES.items():
        name = unicodedata.name(chr(code))
        assert name.startswith(('CYRILLIC', 'GREEK')) and len(latin) == 1 and latin.isascii() and latin.isalpha(), name
        assert unicodedata.normalize('NFKC', chr(code)) == chr(code)
    assert len(guards._CONFUSABLES) == 62


@pytest.mark.parametrize('text,reason', [
    (f'see p{CYR_A}ypal.me/garden', 'contains a payment or donation link'),
    (f'see {CYR_P}{CYR_A}у{CYR_P}{CYR_A}ӏ.{CYR_C}{CYR_O}м/garden', 'contains a payment or donation link'),   # no Latin letter at all
    (f'p{GRK_A}tre{GRK_O}n.com/someone', 'contains a payment or donation link'),
    (f'write to s{CYR_O}meone@ex{CYR_A}mple.org', 'looks like an email address'),
    (f's{CYR_K}-abcdefghijklmnopqrstuvwx', 'looks like an API key or credential'),
    (f'p{CYR_A}ssw{GRK_O}rd=hunter2', 'looks like a password or secret'),
    (f'bit{CYR_C}{CYR_O}in:bc1qexampleexampleexample', 'looks like a cryptocurrency wallet address'),
])
def test_guards_match_words_disguised_with_look_alike_letters(text, reason):
    assert guards.content_reason(text) == reason
    assert guards.normalize(f'p{CYR_A}yp{CYR_A}l') == guards.normalize('paypal')      # so the duplicate check sees through it too


def test_disguised_text_is_refused_at_every_entry_point(isolated_db):
    seed_agents()
    doc = act(1, {'action': 'doc_create', 'title': 'Field guide', 'body': 'First line of the guide.'})
    cases = [
        {'action': 'post', 'title': 'A plain title', 'body': DISGUISED_LINK},
        {'action': 'post', 'title': f'p{CYR_A}ypal.me/garden', 'body': 'An ordinary body with several different words.'},
        {'action': 'reply', 'post_id': 1, 'body': DISGUISED_LINK},
        {'action': 'propose', 'kind': 'project', 'title': 'Shared index', 'body': DISGUISED_LINK},
        {'action': 'doc_create', 'title': 'Other guide', 'body': DISGUISED_LINK},
        {'action': 'doc_revise', 'document_id': doc['document_id'], 'base_revision': 1, 'body': DISGUISED_LINK, 'summary': 'Edit'},
        {'action': 'doc_revise', 'document_id': doc['document_id'], 'base_revision': 1, 'body': 'A new body text.', 'summary': f'see p{CYR_A}ypal.me/x'},
    ]
    act(1, {'action': 'post', 'title': 'A plain title', 'body': 'An ordinary body with several different words.'})
    for action in cases:
        status, detail = refusal(lambda: act(2, action))
        assert status == 422 and CYR_A not in str(detail), action
    kept = update_memory(2, DISGUISED_LINK)
    assert kept == {'stored': False, 'note_id': None, 'rejected': 'contains a payment or donation link'}
    assert overview()['metrics']['posts'] == 1 and overview()['metrics']['proposals'] == 0


def test_mixed_script_words_are_refused_in_titles_and_names_only(isolated_db):
    seed_agents()
    fake = f'Astr{CYR_A}'                                     # looks like the agent name Astra
    body = 'An ordinary body with several different words.'
    for action in ({'action': 'post', 'title': f'A note from {fake}', 'body': body},
                   {'action': 'propose', 'kind': 'project', 'title': f'{fake} plan', 'body': body},
                   {'action': 'doc_create', 'title': f'Guide by {fake}', 'body': body},
                   {'action': 'doc_create', 'title': 'Guide', 'body': body, 'summary': f'by {fake}'},
                   {'action': 'post', 'title': f'Gr{GRK_O}wth report', 'body': body}):
        status, detail = refusal(lambda: act(2, action))
        assert status == 422 and guards.MIXED_SCRIPT_RULE in detail and fake not in detail, action
    # Declared model and client names stay ASCII-only by core.declared_ok, which is not loosened.
    for name in (f'gpt-5{CYR_A}', f'cl{CYR_A}ude-5', f'{GRK_O}penai/gpt-5', 'Привет'):
        assert not core.declared_ok(name)
        assert refusal(lambda: act(2, {'action': 'post', 'title': 'T', 'body': body}, provenance={'source': 'api', 'model': name}))[0] == 422
    assert core.declared_ok('vendor/model-1.0')
    # A body may hold such a word (bodies are only pattern-guarded): it is recorded.
    assert act(2, {'action': 'post', 'title': 'A plain title', 'body': f'I met {fake} in the garden and we counted cedar trees.'})['id'] == 1
    assert guards.has_mixed_script_word(f'x {fake} y') and guards.has_mixed_script_word(f'{CYR_A}{GRK_A}')   # Cyrillic with Greek too
    assert not guards.has_mixed_script_word('été naïve')                                    # combining marks


@pytest.mark.parametrize('text', ORDINARY_TEXTS)
def test_ordinary_non_latin_text_keeps_working_everywhere(isolated_db, text):
    seed_agents()
    assert guards.content_reason(text) is None and not guards.has_mixed_script_word(text)
    title = text[:60].replace('\n', ' ').strip()
    made = act(1, {'action': 'post', 'title': title, 'body': text})
    act(2, {'action': 'reply', 'post_id': made['id'], 'title': title, 'body': text + ' Agreed.'})
    act(3, {'action': 'propose', 'kind': 'project', 'title': title, 'body': text})
    doc = act(4, {'action': 'doc_create', 'title': title, 'body': text, 'summary': title})
    act(5, {'action': 'doc_revise', 'document_id': doc['document_id'], 'base_revision': 1, 'body': text + '\n' + text, 'summary': title})
    assert update_memory(1, text)['stored'] is True
    state = overview()
    assert state['posts'][-1]['body'] == text and state['posts'][-1]['title'] == title    # shown exactly as written
    assert agent_context(1)['identity']['memory'] == text[:memory.MEMORY_CHARS]


# ---- 4. Per-address limit on the public routes ----------------------------------------------

PUBLIC_PATHS = ('/', '/stats.html', '/api/overview', '/static/style.css', '/post/1.html', '/profile/1.html', '/archive/index.html', '/no-such-page')


@pytest.mark.parametrize('application', [app, observatory_app], ids=['combined', 'observatory'])
def test_public_routes_are_limited_per_address_and_health_stays_reachable(isolated_db, monkeypatch, application):
    monkeypatch.setenv('LIVE_PUBLIC_REQUESTS_PER_MINUTE', '5')
    clock = [1_000_000.0]
    monkeypatch.setattr(controls, 'now', lambda: clock[0])
    keys = seed_agents()
    with TestClient(application) as client:
        assert [client.get('/api/overview').status_code for _ in range(5)] == [200] * 5
        for path in PUBLIC_PATHS:
            refused = client.get(path)
            assert refused.status_code == 429 and refused.json() == {'detail': web.TOO_MANY_PUBLIC}, path
            assert refused.headers['retry-after'] == '20' and refused.headers['x-content-type-options'] == 'nosniff'
            assert refused.headers['x-frame-options'] == 'DENY' and 'content-security-policy' in refused.headers
        assert client.get('/api/overview').headers['cache-control'] == 'no-store'
        for _ in range(20):                                   # health is never counted and never refused
            assert client.get('/health').json()['status'] == 'ok'
        if application is app:                                # the agent API keeps its own limits and is not touched by this one
            auth = {'Authorization': 'Bearer ' + keys[0]['key']}
            assert client.get('/api/agent/context', headers=auth).status_code == 200
            assert client.post('/api/agent/action', json={'action': 'idle'}, headers=auth).status_code == 200
            assert client.get('/api/agent/context').status_code == 401
        else:
            assert client.post('/api/overview').status_code in (405, 429)
        clock[0] += 20                                        # the next minute: served again
        assert client.get('/api/overview').status_code == 200 and client.get('/').status_code == 200
    assert controls.public_limiter.limit() == 5


def test_public_limit_default_clamp_and_what_is_exempt(monkeypatch):
    assert controls.public_limiter.limit() == 300 and controls.public_limiter.max_keys == controls.LIMITER_MAX_KEYS == 10_000
    for raw, expected in (('0', 1), ('-3', 1), ('lots', 300), ('1000', 1000)):
        monkeypatch.setenv('LIVE_PUBLIC_REQUESTS_PER_MINUTE', raw)
        assert controls.public_limiter.limit() == expected
    assert not web.public_limit_applies('/health') and not web.public_limit_applies('/api/agent/context') and not web.public_limit_applies('/api/agent')
    for path in ('/', '/api/overview', '/static/app.js', '/healthz', '/health/', '/api/agents', '/docs', '/archive/q/Q01.html'):
        assert web.public_limit_applies(path), path


def test_public_limit_follows_trusted_proxies_and_keeps_bounded_memory(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_PUBLIC_REQUESTS_PER_MINUTE', '2')
    with TestClient(observatory_app) as client:
        # Not behind a listed proxy: X-Forwarded-For is client-supplied and ignored; everyone shares the peer address.
        codes = [client.get('/api/overview', headers={'X-Forwarded-For': f'203.0.113.{n}'}).status_code for n in range(4)]
        assert codes == [200, 200, 429, 429]
        # Behind a listed proxy: each forwarded client has its own allowance, and a spoofed left-hand entry does not help.
        controls.public_limiter.reset()
        monkeypatch.setenv('LIVE_TRUSTED_PROXIES', 'testclient')
        one, two = {'X-Forwarded-For': '198.51.100.1'}, {'X-Forwarded-For': '198.51.100.2'}
        assert [client.get('/api/overview', headers=one).status_code for _ in range(3)] == [200, 200, 429]
        assert [client.get('/api/overview', headers=two).status_code for _ in range(3)] == [200, 200, 429]
        assert client.get('/api/overview', headers={'X-Forwarded-For': '192.0.2.77, 198.51.100.1'}).status_code == 429
        assert client.get('/api/overview', headers={'X-Forwarded-For': '198.51.100.1, 192.0.2.77'}).status_code == 200
        # Bounded: at most max_keys addresses are remembered in a window; further ones are let through, not stored.
        controls.public_limiter.reset()
        monkeypatch.setattr(controls.public_limiter, 'max_keys', 3)
        for n in range(10):
            assert client.get('/health/x', headers={'X-Forwarded-For': f'198.51.100.{n}'}).status_code == 404
        assert controls.public_limiter.size() == 3


def test_the_overview_cache_still_serves_within_the_limit(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_OVERVIEW_CACHE_SECONDS', '30')
    seed_agents()
    calls = []
    real = web.overview
    monkeypatch.setattr(web, 'overview', lambda readonly=False: calls.append(readonly) or real(readonly=readonly))
    with TestClient(observatory_app) as client:
        assert [client.get('/api/overview').status_code for _ in range(5)] == [200] * 5
    assert len(calls) == 1
    web.clear_overview_cache()


# ---- No provider client follows a redirect ---------------------------------------------------

def mock_transport(monkeypatch, handler):
    """Every httpx.Client built from now on answers from `handler` (no socket). Returns the constructor kwargs seen."""
    import httpx
    real, built = httpx.Client, []

    class Client(real):                      # the SDK checks isinstance(http_client, httpx.Client)
        def __init__(self, **kwargs):
            built.append(kwargs)
            super().__init__(transport=httpx.MockTransport(handler), trust_env=False, **kwargs)
    monkeypatch.setattr(httpx, 'Client', Client)
    return built


def redirecting(requests, sent_to):
    import httpx

    def handler(request):
        requests.append((request.url.host, request.url.path, request.headers.get('authorization')))
        if request.url.host == sent_to:
            return httpx.Response(307, headers={'location': 'https://elsewhere.example.invalid' + request.url.path})
        return httpx.Response(200, json={'never': 'reached'})
    return handler


def test_the_single_compatible_endpoint_follows_no_redirect(monkeypatch):
    monkeypatch.setenv('LIVE_COMPAT_BASE_URL', 'https://compat.example.invalid/private-path/v1')
    monkeypatch.setenv('OPENAI_COMPATIBLE_API_KEY', 'fake-compatible-key')
    for name in ('LIVE_COMPAT_JSON_MODE', 'LIVE_COMPAT_REASONING_EFFORT', 'LIVE_INFER_TIMEOUT_SECONDS', 'OPENAI_BASE_URL'):
        monkeypatch.delenv(name, raising=False)
    requests = []
    built = mock_transport(monkeypatch, redirecting(requests, 'compat.example.invalid'))
    with pytest.raises(Exception) as err:
        engine.infer('openai-compatible', 'model-x', 'the prompt', system='s', max_output_tokens=50)
    assert getattr(err.value, 'status_code', None) == 307
    # One request, to the configured host, with the key; nothing (no prompt, no key) went to the redirect target.
    assert requests == [('compat.example.invalid', '/private-path/v1/chat/completions', 'Bearer fake-compatible-key')]
    assert built == [{'follow_redirects': False, 'timeout': 35.0}]


def test_the_native_openai_client_follows_no_redirect(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-openai-key')
    for name in ('OPENAI_BASE_URL', 'OPENAI_ORG_ID', 'OPENAI_PROJECT_ID', 'LIVE_INFER_TIMEOUT_SECONDS'):
        monkeypatch.delenv(name, raising=False)
    requests = []
    built = mock_transport(monkeypatch, redirecting(requests, 'api.openai.com'))
    with pytest.raises(Exception) as err:
        engine.infer('openai', 'model-x', 'the prompt', system='s', max_output_tokens=50)
    assert getattr(err.value, 'status_code', None) == 307
    assert requests == [('api.openai.com', '/v1/responses', 'Bearer fake-openai-key')]
    assert built == [{'follow_redirects': False, 'timeout': 35.0}]


def test_the_anthropic_client_is_built_without_redirects(monkeypatch):
    """The Anthropic SDK is optional and not installed for the tests: a stand-in module records how the client is built."""
    seen = []

    class Anthropic:
        def __init__(self, **kwargs):
            seen.append(kwargs)
            self.messages = types.SimpleNamespace(create=lambda **request: types.SimpleNamespace(
                content=[types.SimpleNamespace(type='text', text='{"action":"idle"}')], stop_reason='end_turn', model='reported',
                usage=types.SimpleNamespace(input_tokens=11, output_tokens=4)))
    monkeypatch.setitem(sys.modules, 'anthropic', types.SimpleNamespace(Anthropic=Anthropic))
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'fake-anthropic-key')
    monkeypatch.delenv('LIVE_INFER_TIMEOUT_SECONDS', raising=False)
    done = engine.infer('anthropic', 'model-x', 'the prompt', system='s', max_output_tokens=50)
    assert (done.text, done.input_tokens, done.output_tokens, done.stop) == ('{"action":"idle"}', 11, 4, 'end')
    http = seen[0].pop('http_client')
    assert http.follow_redirects is False and http.is_closed and seen == [{'timeout': 35.0, 'max_retries': 0}]
