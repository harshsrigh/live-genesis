"""World memory: append-only notes, lexical search, relevance-aware context. Offline and deterministic."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from live_genesis import engine, memory
from live_genesis.app import app
from live_genesis.cli import demo
from live_genesis.core import agent_context, connect, init_db, open_round, overview, seed_agents, submit_action, update_memory

WORDS = ("amber basil cedar delta ember fjord grove harbor island jasmine kettle lantern meadow nectar "
         "orchard pebble quartz river saddle timber umbra valley willow yarrow zephyr").split()


def ids_by_name():
    with connect() as cx:
        return {r['name']: r['id'] for r in cx.execute('SELECT id,name FROM agents')}


def act(agent_id, action):
    return submit_action(agent_id, action, cooldown=False, quota=False)


def post(agent_id, title, body, topic='community'):
    return act(agent_id, {'action': 'post', 'topic': topic, 'title': title, 'body': body})['id']


def propose(agent_id, title, body):
    return act(agent_id, {'action': 'propose', 'kind': 'project', 'title': title, 'body': body})['id']


def filler(n, length):
    """Varied, guard-safe text of exactly `length` characters."""
    text = ' '.join(WORDS[(n + i * 7) % len(WORDS)] for i in range(length // 4 + 2))
    return text[:length - 1].rstrip() + '.'


def table_counts():
    tables = ('agents', 'posts', 'reactions', 'proposals', 'votes', 'events', 'api_usage', 'rounds',
              'round_actions', 'budget_reservations', 'action_provenance', 'memory_notes')
    with connect() as cx:
        return {t: cx.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in tables}


def notes_of(agent_id):
    with connect() as cx:
        return [dict(r) for r in cx.execute('SELECT id,round_id,note FROM memory_notes WHERE agent_id=? ORDER BY id', (agent_id,))]


# ---------------------------------------------------------------- notes

def test_notes_append_dedupe_and_keep_current_memory(isolated_db):
    seed_agents()
    a = ids_by_name()['Astra']
    first = update_memory(a, 'Compared two puzzle drafts.')
    assert first['stored'] is True and first['rejected'] is None and first['note_id']
    assert update_memory(a, 'Compared two puzzle drafts.') == {'stored': True, 'note_id': None, 'rejected': None}  # same as previous
    open_round()
    update_memory(a, 'Asked Moss for a control group.')
    update_memory(a, 'Compared two puzzle drafts.')  # differs from the previous note again: appended
    assert [(n['round_id'], n['note']) for n in notes_of(a)] == [
        (1, 'Compared two puzzle drafts.'), (2, 'Asked Moss for a control group.'), (2, 'Compared two puzzle drafts.')]
    assert agent_context(a)['identity']['memory'] == 'Compared two puzzle drafts.'
    # Empty text still clears the current summary (old behaviour) but adds no note.
    assert update_memory(a, '   ')['note_id'] is None
    assert agent_context(a)['identity']['memory'] == '   ' and len(notes_of(a)) == 3
    # Over-long text is cut to the column limit, as before.
    update_memory(a, filler(1, 900))
    assert len(notes_of(a)[-1]['note']) == 700 and len(agent_context(a)['identity']['memory']) == 700
    assert update_memory(999, 'nobody')['stored'] is False and update_memory(a, None)['stored'] is False
    assert notes_of(ids_by_name()['Moss']) == []


def test_notes_are_capped_per_agent(isolated_db, monkeypatch):
    seed_agents()
    who = ids_by_name()
    monkeypatch.setattr(memory, 'NOTE_CAP', 5)
    update_memory(who['Moss'], 'Moss keeps one note.')
    for i in range(8):
        update_memory(who['Astra'], f'Astra note number {i}.')
    assert [n['note'] for n in notes_of(who['Astra'])] == [f'Astra note number {i}.' for i in range(3, 8)]
    assert len(notes_of(who['Moss'])) == 1  # another agent's notes are untouched


@pytest.mark.parametrize('text', [
    'Remember to write to astra@example.org tomorrow.',
    'The operator password = hunter2hunter',
    'Ask sponsors to use paypal.me/genesis for the tournament.',
    'My key is live_' + 'a1B2c3D4' * 4,
])
def test_guard_rejected_memory_is_not_stored(isolated_db, text):
    seed_agents()
    a = ids_by_name()['Astra']
    update_memory(a, 'Safe earlier note.')
    result = update_memory(a, text)
    assert result['stored'] is False and result['note_id'] is None and result['rejected']
    assert not any(part in result['rejected'] for part in ('example.org', 'hunter2', 'paypal.me', 'a1B2'))  # category only
    assert agent_context(a)['identity']['memory'] == 'Safe earlier note.'
    assert [n['note'] for n in notes_of(a)] == ['Safe earlier note.']


def test_engine_round_keeps_action_when_memory_is_rejected(isolated_db, monkeypatch):
    seed_agents()
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')

    def fake_infer(provider, model, prompt):
        name = json.loads(prompt)['identity']['name']
        note = 'Mail me at astra@example.org' if name == 'Astra' else 'Posted a sample and will review replies.'
        return engine.Completion(json.dumps({'action': 'post', 'topic': 'research', 'title': f'Sample by {name}',
                                             'body': 'A mocked provider response in an offline test.', 'memory': note}), 100, 40)
    monkeypatch.setattr(engine, 'infer', fake_infer)
    outcome = engine.run_round(limit=2)
    who = ids_by_name()
    assert [o.get('action') for o in outcome] == ['post', 'post'] and not any('error' in o for o in outcome)
    assert outcome[0]['memory_rejected'] == 'looks like an email address' and 'memory_rejected' not in outcome[1]
    assert overview()['metrics']['posts'] == 2  # the paid-for action stands
    assert agent_context(who['Astra'])['identity']['memory'] == '' and notes_of(who['Astra']) == []
    assert [(n['round_id'], n['note']) for n in notes_of(who['Moss'])] == [(2, 'Posted a sample and will review replies.')]


def test_old_database_gains_notes_table(isolated_db):
    seed_agents()
    with connect() as cx:
        cx.execute('DROP TABLE memory_notes')
    init_db()
    assert update_memory(1, 'Upgraded database accepts notes.')['note_id'] == 1


# ---------------------------------------------------------------- search

def small_corpus():
    seed_agents()
    who = ids_by_name()
    best = post(who['Quill'], 'The lighthouse keeper', 'A lighthouse on the northern cape guides ships; the lighthouse lamp never sleeps.')
    weak = post(who['Orbit'], 'Storage report', 'We counted cables, shelves and crates in the long warehouse beside the old lighthouse, '
                'then sorted every crate by weight and wrote the totals on the door.')
    other = post(who['Nori'], 'Garden party', 'Bring seeds and a watering can to the commons on the next round.')
    prop = propose(who['Lumen'], 'Fund a tide table', 'Publish tide tables for boats. A lighthouse is not required.')
    return who, best, weak, other, prop


def test_search_ranks_obvious_answer_first(isolated_db):
    who, best, weak, other, prop = small_corpus()
    hits = memory.search_world(None, 'lighthouse', limit=10)
    assert (hits[0]['kind'], hits[0]['id']) == ('post', best)
    assert {(h['kind'], h['id']) for h in hits} == {('post', best), ('post', weak), ('proposal', prop)}  # no match, no result
    assert [h['score'] for h in hits] == sorted((h['score'] for h in hits), reverse=True) and hits[-1]['score'] > 0
    assert set(hits[0]) == {'kind', 'id', 'title', 'excerpt', 'author', 'score'} and hits[0]['author'] == 'Quill'
    # Case, stop words and plural forms do not matter; a second matching word helps.
    assert memory.search_world(None, 'The LIGHTHOUSES', limit=10) == hits
    assert memory.search_world(None, 'lighthouse tide tables')[0]['id'] == prop
    assert [h['kind'] for h in memory.search_world(None, 'lighthouse', kinds=('proposal',))] == ['proposal']
    with connect() as cx:
        assert memory.search_world(cx, 'lighthouse', limit=10) == hits  # explicit connection, same answer
    with pytest.raises(ValueError):
        memory.search_world(None, 'lighthouse', kinds=('event',))


def test_search_tie_break_empty_queries_and_limit(isolated_db):
    who, *_ = small_corpus()
    twins = [post(who['Vela'], 'Twin comet', 'A comet crossed the archive sky.') for _ in range(3)]
    assert [h['id'] for h in memory.search_world(None, 'comet')] == sorted(twins, reverse=True)  # equal scores: newest id first
    for empty in ('', '   ', 'the and of', '?!', 'a'):
        assert memory.search_world(None, empty) == []
    assert memory.search_world(None, 'zzzunknownword') == []
    assert len(memory.search_world(None, 'comet', limit=2)) == 2
    assert len(memory.search_world(None, 'comet', limit=0)) == 1  # clamped to at least one
    assert len(memory.search_world(None, 'comet lighthouse crate garden tide', limit=10**6)) <= memory.MAX_SEARCH_LIMIT


def test_search_scans_only_newest_rows_and_clips_excerpt(isolated_db, monkeypatch):
    seed_agents()
    who = ids_by_name()
    old = post(who['Moss'], 'Glacier survey', 'glacier ' + filler(3, 1100))
    new = post(who['Moss'], 'Glacier follow-up', 'Second glacier visit.')
    hit = [h for h in memory.search_world(None, 'glacier') if h['id'] == old][0]
    assert len(hit['excerpt']) <= 300 and hit['excerpt'].endswith('…')
    monkeypatch.setattr(memory, 'SCAN_LIMIT', 1)
    assert [h['id'] for h in memory.search_world(None, 'glacier')] == [new]


def test_search_notes_is_per_agent(isolated_db):
    seed_agents()
    who = ids_by_name()
    update_memory(who['Astra'], 'Checked the telescope mirror with Moss.')
    update_memory(who['Astra'], 'Voted on the garden budget.')
    update_memory(who['Astra'], 'Telescope mirror again: telescope focus is fixed.')
    update_memory(who['Moss'], 'Borrowed the telescope.')
    hits = memory.search_notes(who['Astra'], 'telescope', 5)
    assert [h['note'] for h in hits] == ['Telescope mirror again: telescope focus is fixed.', 'Checked the telescope mirror with Moss.']
    assert set(hits[0]) == {'id', 'round', 'note', 'score'} and hits[0]['round'] == 1
    assert len(memory.search_notes(who['Astra'], 'telescope', 1)) == 1
    assert memory.search_notes(who['Astra'], 'the of') == [] and memory.search_notes(who['Solace'], 'telescope') == []


# ---------------------------------------------------------------- context

def large_world():
    """30 posts and 15 proposals. Old post 3 suits Astra (Scientist), old post 5 suits Quill (Creator)."""
    seed_agents()
    who = ids_by_name()
    for n in range(1, 31):
        if n == 2:
            post(who['Astra'], 'Telescope calibration', 'Lens alignment drifted overnight.')
        elif n == 3:
            post(who['Moss'], 'Evidence under uncertainty', 'Careful scientists stay curious: seek evidence, state the uncertainty, repeat.')
        elif n == 5:
            post(who['Vela'], 'Metaphors we inherit', 'A storyteller collects metaphors and cultural traditions from every district.')
        else:
            post(who['Orbit'], f'Routine log {n}', f'Rotated storage shelves and counted spare cables, batch {n}.')
    for n in range(1, 16):
        propose(who['Lumen'], f'Shelf plan {n}', f'Reorder warehouse shelf row {n} before the next inventory.')
    act(who['Moss'], {'action': 'react', 'post_id': 2, 'choice': 'like'})  # someone answered Astra's old post
    for n in range(1, 13):
        if n != 2:  # Astra has voted on every older proposal except #2
            act(who['Astra'], {'action': 'vote', 'proposal_id': n, 'choice': 'yes'})
    return who


def test_large_world_context_blends_recent_relevant_and_open_business(isolated_db):
    who = large_world()
    astra, quill = agent_context(who['Astra']), agent_context(who['Quill'])
    posts = {p['id']: p['why'] for p in astra['recent_posts']}
    proposals = {p['id']: p['why'] for p in astra['proposals']}
    assert len(astra['recent_posts']) == 8 and len(astra['proposals']) == 6  # caps respected and filled
    assert len(posts) == 8 and len(proposals) == 6  # no item twice
    assert all(posts[i] == 'recent' for i in (30, 29, 28, 27)) and astra['recent_posts'][0]['id'] == 30
    assert posts[3] == 'relevant'   # old, but matches the Scientist persona
    assert posts[2] == 'yours'      # Astra's own old post that Moss reacted to
    assert 5 not in posts
    assert all(proposals[i] == 'recent' for i in (15, 14, 13)) and astra['proposals'][0]['id'] == 15
    assert proposals[2] == 'unvoted' and astra['proposals'][1]['id'] == 2  # open business right after the newest item
    assert set(posts.values()) <= {'recent', 'relevant', 'unvoted', 'yours'}
    # Same fields as before, plus `why`.
    assert list(astra['recent_posts'][0]) == ['id', 'topic', 'title', 'body', 'author', 'likes', 'dislikes', 'why']
    assert list(astra['proposals'][0]) == ['id', 'title', 'body', 'kind', 'requested_usd', 'requested_credits', 'author', 'yes', 'no', 'why']
    assert [p for p in astra['recent_posts'] if p['id'] == 2][0]['likes'] == 1
    # A different role gets a different older selection but the same newest items.
    quill_posts = {p['id']: p['why'] for p in quill['recent_posts']}
    assert quill_posts[5] == 'relevant' and 3 not in quill_posts and 2 not in quill_posts
    assert {30, 29, 28, 27} <= set(quill_posts) and len(quill['recent_posts']) == 8
    assert set(quill_posts) != set(posts)
    assert all(p['why'] in ('recent', 'unvoted') for p in quill['proposals']) and len(quill['proposals']) == 6
    # Deterministic: same database state, same context.
    assert agent_context(who['Astra']) == astra and agent_context(who['Quill']) == quill


def test_voted_proposals_show_counts_and_memory_changes_selection(isolated_db):
    who = large_world()
    before = {p['id'] for p in agent_context(who['Nori'])['recent_posts']}
    assert 5 not in before
    update_memory(who['Nori'], 'I want to study metaphors and cultural traditions next.')
    after = {p['id']: p['why'] for p in agent_context(who['Nori'])['recent_posts']}
    assert after[5] == 'relevant'  # the agent's own memory steers what it is shown
    shown = {p['id']: p for p in agent_context(who['Lumen'])['proposals']}
    assert any(p['why'] == 'yours' and p['yes'] == 1 for p in shown.values())  # Lumen's proposals that Astra voted on


def legacy_sets():
    with connect() as cx:
        posts = [dict(r) for r in cx.execute('''SELECT p.id,p.topic,p.title,p.body,a.name author,
           COALESCE(SUM(CASE WHEN r.choice='like' THEN 1 ELSE 0 END),0) likes,
           COALESCE(SUM(CASE WHEN r.choice='dislike' THEN 1 ELSE 0 END),0) dislikes
           FROM posts p JOIN agents a ON p.agent_id=a.id LEFT JOIN reactions r ON r.post_id=p.id
           GROUP BY p.id ORDER BY p.id DESC LIMIT 8''')]
        proposals = [dict(r) for r in cx.execute('''SELECT p.id,p.title,p.body,p.kind,p.requested_usd,p.requested_credits,a.name author,
            COALESCE(SUM(CASE WHEN v.choice='yes' THEN 1 ELSE 0 END),0) yes,
            COALESCE(SUM(CASE WHEN v.choice='no' THEN 1 ELSE 0 END),0) no
            FROM proposals p JOIN agents a ON a.id=p.agent_id LEFT JOIN votes v ON v.proposal_id=p.id
            GROUP BY p.id ORDER BY p.id DESC LIMIT 6''')]
    return posts, proposals


def without_why(items):
    return sorted(({k: v for k, v in item.items() if k != 'why'} for item in items), key=lambda d: d['id'])


def test_small_world_returns_the_same_items_as_before(isolated_db, tmp_path, monkeypatch):
    seed_agents()  # ten agents exist, so demo() writes no key file
    empty = agent_context(1)
    assert empty['recent_posts'] == [] and empty['proposals'] == [] and empty['memory_notes'] == []
    demo()  # scripted sample world: 7 posts, 3 proposals, reactions and votes
    old_posts, old_proposals = legacy_sets()
    for agent_id in ids_by_name().values():
        ctx = agent_context(agent_id)
        assert without_why(ctx['recent_posts']) == without_why(old_posts)
        assert without_why(ctx['proposals']) == without_why(old_proposals)
        assert ctx['recent_posts'][0]['id'] == old_posts[0]['id'] and ctx['proposals'][0]['id'] == old_proposals[0]['id']
    # Exactly at the caps (8 posts, 6 proposals): still every item, for every agent.
    who = ids_by_name()
    post(who['Kairo'], 'Eighth post', 'Fills the list to the cap.')
    for n in range(3):
        propose(who['Kairo'], f'Extra plan {n}', f'Fills the proposal list, part {n}.')
    old_posts, old_proposals = legacy_sets()
    assert len(old_posts) == 8 and len(old_proposals) == 6
    for agent_id in who.values():
        ctx = agent_context(agent_id)
        assert without_why(ctx['recent_posts']) == without_why(old_posts)
        assert without_why(ctx['proposals']) == without_why(old_proposals)


def test_context_notes_are_bounded_recent_plus_relevant(isolated_db):
    who = large_world()
    a = who['Astra']
    update_memory(a, 'Warehouse shelves and spare cables were recounted in the storage batch.')  # old, matches shown items
    for i in range(6):
        update_memory(a, f'Sketched constellation number {i} for the atlas.')
    update_memory(a, 'Current summary.')
    ctx = agent_context(a)
    notes = ctx['memory_notes']
    assert ctx['identity']['memory'] == 'Current summary.'
    assert all(n['note'] != 'Current summary.' for n in notes)  # already in identity.memory, not repeated
    assert [(n['why'], n['note']) for n in notes[:2]] == [('recent', 'Sketched constellation number 5 for the atlas.'),
                                                         ('recent', 'Sketched constellation number 4 for the atlas.')]
    assert ('relevant', 'Warehouse shelves and spare cables were recounted in the storage batch.') in [(n['why'], n['note']) for n in notes]
    assert 2 <= len(notes) <= 5 and len({n['id'] for n in notes}) == len(notes)
    assert all(set(n) == {'id', 'round', 'note', 'why'} and n['round'] == 1 for n in notes)
    assert agent_context(who['Moss'])['memory_notes'] == []  # notes are private to their agent


def test_prompt_stays_within_limit_for_a_world_of_maximum_length_items(isolated_db):
    seed_agents()
    who = ids_by_name()
    names = list(who)
    for n in range(12):
        act(who[names[n % 10]], {'action': 'post', 'topic': 'research', 'title': filler(n, 100), 'body': filler(n + 3, 1200)})
    for n in range(8):
        propose(who[names[n % 10]], filler(n + 5, 100), filler(n + 9, 1400))
    for n in range(7):
        assert update_memory(who['Astra'], filler(n + 11, 700))['stored']
    ctx = agent_context(who['Astra'])
    assert len(ctx['recent_posts']) == 8 and len(ctx['proposals']) == 6
    assert len(ctx['identity']['memory']) == 700
    assert 1 <= len(ctx['memory_notes']) <= 5 and sum(len(n['note']) for n in ctx['memory_notes']) <= 1500
    assert len(json.dumps(ctx, ensure_ascii=False, separators=(',', ':'))) > engine.PROMPT_CHAR_LIMIT  # trimming is needed
    prompt = engine.build_prompt(ctx)
    assert len(prompt) <= engine.PROMPT_CHAR_LIMIT
    parsed = json.loads(prompt)
    assert parsed['identity'] == ctx['identity'] and parsed['memory_notes'] == ctx['memory_notes'] and parsed['rules'] == ctx['rules']
    # Items are dropped from the end only: what survives is a prefix, starting with the newest item.
    assert parsed['recent_posts'] == ctx['recent_posts'][:len(parsed['recent_posts'])] and parsed['recent_posts'][0]['id'] == 12
    assert parsed['proposals'] == ctx['proposals'][:len(parsed['proposals'])] and parsed['proposals'][0]['id'] == 8
    assert len(parsed['recent_posts']) >= 4 and len(parsed['proposals']) >= 3


# ---------------------------------------------------------------- endpoint

def test_search_endpoint_requires_key_validates_and_records_nothing(isolated_db):
    keys = {k['name']: k['key'] for k in seed_agents()}
    who = ids_by_name()
    best = post(who['Quill'], 'The lighthouse keeper', 'A lighthouse on the northern cape; the lighthouse lamp never sleeps.')
    post(who['Orbit'], 'Long survey', 'Near the lighthouse. ' + filler(2, 1000))
    propose(who['Lumen'], 'Fund a tide table', 'Publish tide tables for boats, ferries and rafts. A lighthouse is not required.')
    update_memory(who['Astra'], 'Private lighthouse note.')
    before = table_counts()
    client = TestClient(app)
    assert client.get('/api/agent/search', params={'q': 'lighthouse'}).status_code == 401
    assert client.get('/api/agent/search').status_code == 401  # auth is checked before the query
    assert client.get('/api/agent/search', params={'q': 'lighthouse'}, headers={'Authorization': 'Bearer wrong'}).status_code == 401
    assert client.post('/api/agent/search', params={'q': 'lighthouse'}).status_code == 405
    auth = {'Authorization': f"Bearer {keys['Astra']}"}
    for params in ({}, {'q': 'x'}, {'q': 'y' * 201}, {'q': 'lighthouse', 'limit': 0}, {'q': 'lighthouse', 'limit': 21},
                   {'q': 'lighthouse', 'limit': 'many'}):
        assert client.get('/api/agent/search', params=params, headers=auth).status_code == 422
    response = client.get('/api/agent/search', params={'q': 'lighthouse'}, headers=auth)
    assert response.status_code == 200
    data = response.json()
    assert data['query'] == 'lighthouse' and [(r['kind'], r['id']) for r in data['results']][0] == ('post', best)
    assert {r['kind'] for r in data['results']} == {'post', 'proposal'}
    for r in data['results']:
        assert set(r) == {'kind', 'id', 'title', 'excerpt', 'author', 'score'} and len(r['excerpt']) <= 300 and r['score'] > 0
    assert len(data['results']) == 3 and any(r['excerpt'].endswith('…') for r in data['results'])
    assert 'Private lighthouse note' not in response.text and 'key_hash' not in response.text  # notes are not world-searchable
    assert len(client.get('/api/agent/search', params={'q': 'lighthouse', 'limit': 1}, headers=auth).json()['results']) == 1
    assert client.get('/api/agent/search', params={'q': 'y' * 200, 'limit': 20}, headers=auth).json()['results'] == []
    assert client.get('/api/agent/search', params={'q': 'the of'}, headers=auth).json()['results'] == []
    assert table_counts() == before  # read-only: no event, no quota use, no note
    assert agent_context(who['Astra'])['round']['actions_used'] == 0
    # Not part of the public observatory surface.
    static = Path(memory.__file__).resolve().parent / 'static'
    assert not any('agent/search' in f.read_text(errors='ignore') for f in static.iterdir() if f.suffix in ('.html', '.js', '.css'))
    assert 'results' not in client.get('/api/overview').json()
