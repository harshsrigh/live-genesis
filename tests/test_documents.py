"""Shared documents with attributable, hash-chained revision history. Offline; no model calls."""
import json
import sqlite3

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from live_genesis import cli, controls, documents, engine, guards, memory, snapshot
from live_genesis.app import app
from live_genesis.core import (agent_context, connect, open_round, overview, reserve_budget, seed_agents,
                               settle_reservation, submit_action)
from live_genesis.observatory import app as observatory_app

TITLE = 'Field notes on shared editing'
V1 = 'alpha\nbeta\ngamma'
V2 = 'alpha\nbeta changed\ngamma\ndelta'
V3 = 'beta changed\ngamma\ndelta\nepsilon'
TABLES = ('events', 'action_provenance', 'round_actions', 'documents', 'document_revisions', 'document_blame')
WORDS = ('lantern', 'harbor', 'ledger', 'orchard', 'signal', 'meadow', 'compass', 'archive', 'bridge', 'canyon', 'ember', 'garden')


def engine_receipt(agent_id):
    """A settled, unused budget reservation: the only way an action may be labelled 'engine'."""
    receipt = reserve_budget(agent_id, 'test-model-1', 0.4, 10, provider='openai')
    settle_reservation(receipt, 10, 10)
    return receipt


def ids():
    with connect() as cx:
        return [r[0] for r in cx.execute('SELECT id FROM agents ORDER BY id')]


def counts():
    with connect() as cx:
        return {t: cx.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in TABLES}


def create(agent_id, body=V1, title=TITLE, **kwargs):
    open_round()  # every helper call starts a round, so one agent may act repeatedly in a test
    extra = {k: kwargs.pop(k) for k in ('summary', 'topic') if k in kwargs}
    return submit_action(agent_id, {'action': 'doc_create', 'title': title, 'body': body, **extra}, **kwargs)


def revise(agent_id, doc, base, body, summary='Edited the text', **kwargs):
    open_round()
    return submit_action(agent_id, {'action': 'doc_revise', 'document_id': doc, 'base_revision': base,
                                    'body': body, 'summary': summary}, **kwargs)


def revert(agent_id, doc, base, revision, **kwargs):
    open_round()
    return submit_action(agent_id, {'action': 'doc_revert', 'document_id': doc, 'base_revision': base, 'revision': revision}, **kwargs)


def rejected(status, call, *args, **kwargs):
    with pytest.raises(HTTPException) as err:
        call(*args, **kwargs)
    assert err.value.status_code == status, err.value.detail
    return err.value


def long_body(seed=0, size=documents.BODY_CHARS):
    """Varied text full of characters that JSON must escape (quotes, backslashes, newlines)."""
    lines, i = [], seed
    while len('\n'.join(lines)) < size and len(lines) < documents.BODY_LINES:
        lines.append(f'"{WORDS[i % 12]}" answers the {WORDS[(i * 7 + 3) % 12]} keeper \\ near the {WORDS[(i * 5 + seed) % 12]} gate, verse {WORDS[(i // 12) % 12]}')
        i += 1
    return '\n'.join(lines)[:size].strip()


def three_revisions():
    """Astra creates V1, Moss revises to V2, Quill to V3. Returns (document id, astra, moss, quill)."""
    seed_agents()
    a, b, c = ids()[:3]
    doc = create(a)['document_id']
    revise(b, doc, 1, V2, 'Reworded beta and added delta')
    revise(c, doc, 2, V3, 'Dropped alpha, added epsilon')
    return doc, a, b, c


# ---- create / revise / revert ---------------------------------------------------------

def test_create_revise_revert(isolated_db):
    seed_agents()
    a, b, c = ids()[:3]
    made = create(a, topic='research', summary='First draft')
    assert {k: made[k] for k in ('action', 'agent', 'document_id', 'revision', 'lines_added', 'lines_removed')} == {
        'action': 'doc_create', 'agent': 'Astra', 'document_id': 1, 'revision': 1, 'lines_added': 3, 'lines_removed': 0}
    assert made['summary'] == f'started shared document #1 · {TITLE}'
    second = revise(b, 1, 1, V2, 'Reworded beta and added delta')
    assert (second['revision'], second['lines_added'], second['lines_removed']) == (2, 2, 1)
    assert second['summary'] == 'revised document #1 · revision 2 (+2/-1 lines) · Reworded beta and added delta'
    back = revert(c, 1, 2, 1)
    assert (back['revision'], back['summary']) == (3, 'reverted document #1 to revision 1 (now revision 3)')
    doc = documents.read_document(1)
    assert (doc['head'], doc['body'], doc['topic'], doc['created_by'], doc['last_editor']) == (3, V1, 'research', 'Astra', 'Quill')
    history = doc['revisions']
    assert [(r['number'], r['kind'], r['agent'], r['base'], r['reverted_to'], r['summary']) for r in history] == [
        (3, 'doc_revert', 'Quill', 2, 1, 'Reverted to revision 1'),
        (2, 'doc_revise', 'Moss', 1, None, 'Reworded beta and added delta'),
        (1, 'doc_create', 'Astra', 0, None, 'First draft')]
    assert [r['size_delta'] for r in history] == [len(V1) - len(V2), len(V2) - len(V1), len(V1)]
    assert documents.verify() == []
    assert overview()['metrics']['documents'] == 1
    with connect() as cx:
        assert [r[0] for r in cx.execute("SELECT verb FROM events ORDER BY id")] == ['doc_create', 'doc_revise', 'doc_revert']


def test_validation_errors_record_nothing(isolated_db):
    seed_agents()
    a, b = ids()[:2]
    doc = create(a)['document_id']
    before = counts()
    open_round()
    bad = [
        (422, {'action': 'doc_create', 'title': '', 'body': V1}),
        (422, {'action': 'doc_create', 'title': 't' * 101, 'body': V1}),
        (422, {'action': 'doc_create', 'title': 'Too long', 'body': long_body() + ' and more'}),
        (422, {'action': 'doc_create', 'title': 'Too many lines', 'body': '\n'.join(f'{WORDS[i % 12]} line' for i in range(201))}),
        (422, {'action': 'doc_create', 'title': 'Bad topic', 'body': V1, 'topic': 'gossip'}),
        (422, {'action': 'doc_create', 'title': 'Long summary', 'body': V1, 'summary': 's' * 201}),
        (422, {'action': 'doc_revise', 'document_id': doc, 'base_revision': 1, 'body': V2}),                      # summary required
        (422, {'action': 'doc_revise', 'document_id': doc, 'base_revision': 1, 'body': V2, 'summary': 's' * 201}),
        (422, {'action': 'doc_revise', 'document_id': doc, 'body': V2, 'summary': 'No base'}),
        (422, {'action': 'doc_revise', 'document_id': '1', 'base_revision': 1, 'body': V2, 'summary': 'Text id'}),
        (422, {'action': 'doc_revise', 'document_id': doc, 'base_revision': True, 'body': V2, 'summary': 'Bool base'}),
        (404, {'action': 'doc_revise', 'document_id': 99, 'base_revision': 1, 'body': V2, 'summary': 'Missing'}),
        (422, {'action': 'doc_revert', 'document_id': doc, 'base_revision': 1}),
        (404, {'action': 'doc_revert', 'document_id': doc, 'base_revision': 1, 'revision': 7}),
        (409, {'action': 'doc_revert', 'document_id': doc, 'base_revision': 1, 'revision': 1}),                  # already the head
    ]
    for status, action in bad:
        rejected(status, submit_action, b, action)
    assert counts() == before
    assert agent_context(b)['round']['actions_used'] == 0


def test_line_endings_are_normalized(isolated_db):
    seed_agents()
    create(1, 'alpha\r\nbeta\rgamma  \n')
    assert documents.read_document(1)['body'] == V1


# ---- optimistic concurrency and no-op -------------------------------------------------

def test_stale_base_is_refused_with_the_current_head_and_uses_no_quota(isolated_db):
    seed_agents()
    a, b, c = ids()[:3]
    doc = create(a)['document_id']
    revise(b, doc, 1, V2)
    before = counts()
    open_round()
    stale = rejected(409, submit_action, c, {'action': 'doc_revise', 'document_id': doc, 'base_revision': 1, 'body': V3, 'summary': 'Late'},
                     idempotency_key='stale-edit-01')
    assert 'now at revision 2' in stale.detail and 'base_revision 2' in stale.detail
    rejected(409, submit_action, c, {'action': 'doc_revise', 'document_id': doc, 'base_revision': 3, 'body': V3, 'summary': 'Future'})
    rejected(409, submit_action, c, {'action': 'doc_revert', 'document_id': doc, 'base_revision': 1, 'revision': 1})
    assert counts() == before and agent_context(c)['round']['actions_used'] == 0
    # Same round, same idempotency key, corrected base: the key was not consumed by the refusal.
    ok = submit_action(c, {'action': 'doc_revise', 'document_id': doc, 'base_revision': 2, 'body': V3, 'summary': 'Late'},
                       idempotency_key='stale-edit-02')
    assert ok['revision'] == 3 and agent_context(c)['round']['actions_used'] == 1
    rejected(429, submit_action, c, {'action': 'doc_revise', 'document_id': doc, 'base_revision': 3, 'body': V1, 'summary': 'Quota'})


def test_noop_revision_is_rejected(isolated_db):
    seed_agents()
    a, b = ids()[:2]
    doc = create(a)['document_id']
    before = counts()
    open_round()
    same = rejected(409, submit_action, b, {'action': 'doc_revise', 'document_id': doc, 'base_revision': 1, 'body': '  ' + V1 + '\n', 'summary': 'Nothing'})
    assert 'identical' in same.detail
    assert counts() == before
    revise(b, doc, 1, V2)
    revise(a, doc, 2, V1, 'Back to the first wording')          # returning to earlier text is a real edit
    rejected(409, revert, b, doc, 3, 1)                         # ...so reverting to revision 1 now changes nothing
    assert documents.read_document(doc)['head'] == 3


# ---- content guards -------------------------------------------------------------------

@pytest.mark.parametrize('secret,category', [
    ('reach me at quill.writer@example.org today', 'email'),
    ('sk-abcdefghijklmnopqrstuvwx1234', 'API key'),
    ('send funds via paypal.me/commonsfund', 'payment'),
])
def test_guards_cover_title_body_and_summary_without_echo(isolated_db, secret, category):
    seed_agents()
    a, b = ids()[:2]
    doc = create(a)['document_id']
    before = counts()
    open_round()
    attempts = [
        {'action': 'doc_create', 'title': secret[:100], 'body': 'A clean body about gardens.'},
        {'action': 'doc_create', 'title': 'Clean title', 'body': 'Line one\n' + secret},
        {'action': 'doc_create', 'title': 'Clean title', 'body': 'A clean body about gardens.', 'summary': secret},
        {'action': 'doc_revise', 'document_id': doc, 'base_revision': 1, 'body': V2 + '\n' + secret, 'summary': 'Added a line'},
        {'action': 'doc_revise', 'document_id': doc, 'base_revision': 1, 'body': V2, 'summary': secret},
        {'action': 'doc_revert', 'document_id': doc, 'base_revision': 1, 'revision': 1, 'summary': secret},
    ]
    for action in attempts[:5]:
        err = rejected(422, submit_action, b, action)
        assert err.detail.startswith('Content rejected: ') and category in err.detail
        assert secret not in err.detail and secret.split()[-1] not in err.detail
    assert counts() == before
    revise(a, doc, 1, V2)                                        # make revision 1 revertable
    before = counts()
    open_round()
    err = rejected(422, submit_action, b, {**attempts[5], 'base_revision': 2})
    assert secret not in err.detail
    assert counts() == before


def test_filler_body_is_rejected(isolated_db):
    seed_agents()
    open_round()
    err = rejected(422, submit_action, 1, {'action': 'doc_create', 'title': 'Filler', 'body': 'spam ' * 30})
    assert 'repeated' in err.detail


def test_duplicate_rules_for_documents(isolated_db, monkeypatch):
    monkeypatch.setenv('LIVE_DUPLICATE_GUARD', '1')
    seed_agents()
    a, b, c = ids()[:3]
    post = {'action': 'post', 'title': 'On lanterns', 'body': 'A lantern is a promise that someone will return.', 'topic': 'stories'}
    submit_action(a, post)
    # A copy of another agent's post is refused as a new document: same title+body, or the body under a new title.
    assert 'duplicate' in rejected(409, create, b, post['body'], post['title']).detail
    assert 'duplicate' in rejected(409, create, b, post['body'].upper() + '!!', 'A new name for it').detail
    assert 'duplicate' in rejected(409, create, a, post['body'], 'My post again').detail          # the author's own, too
    doc = create(b, V1)['document_id']
    # A copy of another document's current text is refused as a new document and as a revision of a different one.
    assert 'another shared document' in rejected(409, create, c, V1, 'Fork under another title').detail
    other = create(c, 'north\nsouth\neast')['document_id']
    assert 'another shared document' in rejected(409, revise, a, other, 1, V1).detail
    # Within ONE document, near-identical and even previously seen text is a legitimate edit.
    assert revise(c, doc, 1, V1 + '.')['revision'] == 2          # normalizes to the same text as revision 1
    assert revise(a, doc, 2, V2)['revision'] == 3
    assert revise(b, doc, 3, V1)['revision'] == 4                # exact earlier body again
    assert revert(c, doc, 4, 3)['revision'] == 5
    assert guards.normalize(V1 + '.') == guards.normalize(V1)
    assert documents.verify() == []


# ---- append-only storage and the hash chain -------------------------------------------

def test_revisions_are_append_only_and_documents_keep_their_identity(isolated_db):
    doc, *_ = three_revisions()
    with connect() as cx:
        for statement in ("UPDATE document_revisions SET body='edited' WHERE number=1",
                          "UPDATE document_revisions SET summary='edited'",
                          'DELETE FROM document_revisions WHERE number=3',
                          'DELETE FROM documents',
                          "UPDATE documents SET title='Renamed'",
                          'UPDATE documents SET created_by=2, demo=1',
                          'UPDATE documents SET head=1',
                          'UPDATE documents SET head=9'):
            with pytest.raises(sqlite3.DatabaseError):
                cx.execute(statement)
    assert documents.read_document(doc)['head'] == 3 and documents.verify() == []


def tamper(statement, *args):
    with connect() as cx:
        cx.execute('DROP TRIGGER IF EXISTS document_revisions_no_update')
        cx.execute('DROP TRIGGER IF EXISTS document_revisions_no_delete')
        cx.execute('DROP TRIGGER IF EXISTS documents_fixed_fields')
        cx.execute(statement, args)


def test_verify_detects_tampering_through_raw_sql(isolated_db):
    doc, *_ = three_revisions()
    other = create(1, 'north\nsouth\neast', 'An untouched document')['document_id']
    assert documents.verify() == [] and documents.verify(doc) == []
    tamper("UPDATE document_revisions SET body='alpha\nBETA REWRITTEN\ngamma\ndelta' WHERE document_id=? AND number=2", doc)
    assert documents.verify() == [{'document_id': doc, 'revision': 2, 'problem': 'body does not match content_hash'}]
    assert documents.verify(other) == []
    # Covering the tracks by updating content_hash as well breaks the chain link instead.
    tamper('UPDATE document_revisions SET content_hash=? WHERE document_id=? AND number=2',
           documents.content_hash('alpha\nBETA REWRITTEN\ngamma\ndelta'), doc)
    assert documents.verify(doc) == [{'document_id': doc, 'revision': 2, 'problem': 'chain_hash does not match the previous link and stored fields'}]


@pytest.mark.parametrize('statement,revision', [
    ("UPDATE document_revisions SET summary='Rewritten summary' WHERE number=2", 2),
    ('UPDATE document_revisions SET agent_id=1 WHERE number=3', 3),
    ("UPDATE document_revisions SET created_at='2020-01-01T00:00:00+00:00' WHERE number=1", 1),
    ('UPDATE document_revisions SET lines_added=40 WHERE number=2', 2),
])
def test_verify_detects_changed_metadata(isolated_db, statement, revision):
    doc, *_ = three_revisions()
    tamper(statement)
    assert [(b['revision'], b['problem'][:10]) for b in documents.verify(doc)] == [(revision, 'chain_hash')]


def test_verify_detects_deleted_revisions_and_a_renamed_document(isolated_db):
    doc, *_ = three_revisions()
    tamper('DELETE FROM document_revisions WHERE number=2')
    problems = {(b['revision'], b['problem']) for b in documents.verify(doc)}
    assert (3, 'revision numbering is not continuous') in problems
    assert (3, 'head does not match the last stored revision') not in problems
    tamper('DELETE FROM document_revisions WHERE number=3')
    assert {'document_id': doc, 'revision': 3, 'problem': 'head does not match the last stored revision'} in documents.verify(doc)
    seed = create(1, 'north\nsouth\neast', 'Original title')['document_id']
    tamper("UPDATE documents SET title='Forged title' WHERE id=?", seed)
    assert [b['revision'] for b in documents.verify(seed)] == [1]


def test_a_fully_recomputed_chain_passes_verify_but_changes_the_head_hash(isolated_db):
    """Documents the limit: internal consistency is all verify() can show. An attacker with database
    access can rewrite the chain; only a head hash kept elsewhere (overview/snapshot copy) reveals it."""
    doc, *_ = three_revisions()
    published = overview()['documents'][0]['head_hash']
    forged = 'alpha\nforged line\ngamma'
    tamper('UPDATE document_revisions SET body=?, content_hash=? WHERE document_id=? AND number=1', forged, documents.content_hash(forged), doc)
    with connect() as cx:
        record = cx.execute('SELECT * FROM documents WHERE id=?', (doc,)).fetchone()
        previous = ''
        for rev in cx.execute('SELECT * FROM document_revisions WHERE document_id=? ORDER BY number', (doc,)).fetchall():
            previous = documents.chain_hash(previous, record, rev)
            cx.execute('UPDATE document_revisions SET chain_hash=? WHERE id=?', (previous, rev['id']))
    assert documents.verify() == []
    assert overview()['documents'][0]['head_hash'] != published


# ---- diff counts, blame and contributions ---------------------------------------------

def test_diff_counts_and_blame_on_a_hand_checked_example(isolated_db):
    doc, a, b, c = three_revisions()
    with connect() as cx:
        rows = cx.execute('SELECT number,lines_added,lines_removed,size_delta FROM document_revisions ORDER BY number').fetchall()
        # r1 adds alpha/beta/gamma; r2 changes beta (1 removed, 1 added) and appends delta; r3 drops alpha, appends epsilon.
        assert [tuple(r) for r in rows] == [(1, 3, 0, 16), (2, 2, 1, 14), (3, 1, 1, 2)]
        # Head is: beta changed (Moss), gamma (Astra), delta (Moss), epsilon (Quill).
        assert documents.blame(cx, doc) == [b, a, b, c]
        cached = cx.execute('SELECT revision,authors FROM document_blame WHERE document_id=?', (doc,)).fetchone()
        assert (cached['revision'], json.loads(cached['authors'])) == (3, [b, a, b, c])
        assert documents.contributors(cx, doc, 3, V3) == [
            {'agent': 'Moss', 'role': 'Scientist', 'revisions': 1, 'lines': 2},
            {'agent': 'Astra', 'role': 'Scientist', 'revisions': 1, 'lines': 1},
            {'agent': 'Quill', 'role': 'Creator', 'revisions': 1, 'lines': 1}]
    # A revert restores the attribution of the revision it returns to: Moss reverting to r2 gains no lines.
    back = revert(b, doc, 3, 2)
    assert (back['lines_added'], back['lines_removed']) == (1, 1)          # diff against the head it replaced
    with connect() as cx:
        assert json.loads(cx.execute('SELECT authors FROM document_blame').fetchone()[0]) == [a, b, a, b]
        assert documents.blame(cx, doc) == [a, b, a, b] and documents.blame(cx, doc, upto=1) == [a, a, a]
    revise(c, doc, 4, V2 + '\nzeta')
    back = revert(c, doc, 5, 1)
    with connect() as cx:
        assert documents.blame(cx, doc) == [a, a, a]
        cx.execute('DELETE FROM document_blame')                 # the cache is derived: reads fall back to a replay
    people = overview()['documents'][0]['contributors']
    assert [(p['agent'], p['revisions'], p['lines']) for p in people] == [('Astra', 1, 3), ('Quill', 3, 0), ('Moss', 2, 0)]


def test_compare_is_a_pure_line_diff():
    assert documents.compare([], [], ['x', 'y'], 7) == (2, 0, [7, 7])
    assert documents.compare(['x', 'y'], [1, 2], ['x', 'y'], 7) == (0, 0, [1, 2])
    assert documents.compare(['x', 'y', 'z'], [1, 2, 3], ['y', 'w', 'z'], 7) == (1, 1, [2, 7, 3])
    assert documents.compare(['same'] * 3, [1, 1, 1], ['same'] * 2, 7) == (0, 1, [1, 1])
    assert documents.lines_of(None) == [] and documents.lines_of('a\n\nb') == ['a', '', 'b']


def test_blank_lines_are_not_counted_as_contributions(isolated_db):
    seed_agents()
    a, b = ids()[:2]
    doc = create(a, 'alpha\n\nbeta')['document_id']
    revise(b, doc, 1, 'alpha\n\nbeta\n\ngamma')
    people = overview()['documents'][0]['contributors']
    assert [(p['agent'], p['lines']) for p in people] == [('Astra', 2), ('Moss', 1)]


def test_worst_case_edit_stays_fast(isolated_db):
    seed_agents()
    a, b = ids()[:2]
    lines = [WORDS[i % 12] + ' ' + WORDS[(i * 5) % 12] for i in range(documents.BODY_LINES)]
    doc = create(a, '\n'.join(lines))['document_id']
    done = revise(b, doc, 1, '\n'.join(reversed(lines)))
    assert done['revision'] == 2 and done['lines_added'] == done['lines_removed'] > 0


def test_revision_limit(isolated_db, monkeypatch):
    monkeypatch.setattr(documents, 'MAX_REVISIONS', 3)
    doc, a, b, c = three_revisions()
    assert 'limit of 3 revisions' in rejected(409, revise, a, doc, 3, V1).detail


# ---- idempotency, quota, provenance ---------------------------------------------------

def test_idempotent_replay_of_a_revise(isolated_db):
    seed_agents()
    a, b, c = ids()[:3]
    doc = create(a)['document_id']
    action = {'action': 'doc_revise', 'document_id': doc, 'base_revision': 1, 'body': V2, 'summary': 'Retried edit'}
    first = submit_action(b, action, idempotency_key='revise-key-01')
    assert 'replayed' not in first and first['revision'] == 2
    revise(c, doc, 2, V3)                                        # the head moves on
    before = counts()
    again = submit_action(b, action, idempotency_key='revise-key-01')
    assert again == {**first, 'replayed': True} and counts() == before
    rejected(409, submit_action, b, {**action, 'body': V1}, idempotency_key='revise-key-01')
    assert documents.read_document(doc)['head'] == 3


def test_each_edit_uses_the_round_quota(isolated_db):
    seed_agents()
    a = ids()[0]
    doc = create(a)['document_id']
    action = {'action': 'doc_revise', 'document_id': doc, 'base_revision': 1, 'body': V2, 'summary': 'Second action'}
    assert 'quota' in rejected(429, submit_action, a, action).detail
    open_round()
    assert submit_action(a, action)['round'] == 3


def test_provenance_rows_and_source_labels(isolated_db):
    seed_agents(model='test-model-1')
    a, b, c = ids()[:3]
    api_doc = create(a, provenance={'source': 'api', 'model': 'declared-model', 'client': 'pytest'})
    with connect() as cx:
        row = cx.execute("SELECT * FROM action_provenance WHERE kind='doc_create'").fetchone()
        assert (row['source'], row['model'], row['client'], row['verified'], row['ref_id']) == ('api', 'declared-model', 'pytest', 0, api_doc['revision_id'])
    assert overview()['documents'][0]['source'] == 'api'
    revise(b, 1, 1, V2, receipt=engine_receipt(b))
    mixed = overview()['documents'][0]
    assert mixed['source'] == 'mixed'
    assert [(r['kind'], r['source'], r['model'], r['verified']) for r in mixed['revisions']] == [
        ('doc_revise', 'engine', 'test-model-1', 1), ('doc_create', 'api', 'declared-model', 0)]
    engine_doc = create(c, 'north\nsouth\neast', 'Engine made', receipt=engine_receipt(c))['document_id']
    revert_target = revise(b, engine_doc, 1, 'north\nsouth\neast\nwest', receipt=engine_receipt(b))
    revert(c, engine_doc, 2, 1, receipt=engine_receipt(c))
    assert overview()['documents'][0]['source'] == 'engine'
    with connect() as cx:
        linked = cx.execute('''SELECT COUNT(*) FROM document_revisions r JOIN action_provenance ap ON ap.kind=r.kind AND ap.ref_id=r.id
                               JOIN events e ON e.id=ap.event_id AND e.verb=r.kind AND e.agent_id=r.agent_id''').fetchone()[0]
        assert linked == cx.execute('SELECT COUNT(*) FROM document_revisions').fetchone()[0] == 5
    assert revert_target['revision_id'] == 4
    submit_action(a, {'action': 'doc_create', 'title': 'Scripted sample', 'body': 'one\ntwo\nthree'}, demo=True, cooldown=False, quota=False)
    demo = overview()['documents'][0]
    assert (demo['source'], demo['demo'], demo['revisions'][0]['source']) == ('demo', 1, 'demo')


def test_demo_and_agent_documents_cannot_mix(isolated_db):
    seed_agents()
    a, b = ids()[:2]
    script = dict(demo=True, cooldown=False, quota=False)
    scripted = submit_action(a, {'action': 'doc_create', 'title': 'Scripted sample', 'body': V1}, **script)['document_id']
    real = create(a, 'north\nsouth\neast', 'Agent document')['document_id']
    before = counts()
    open_round()
    for agent, doc, kwargs in ((b, scripted, {}), (b, real, script)):
        err = rejected(409, submit_action, agent, {'action': 'doc_revise', 'document_id': doc, 'base_revision': 1, 'body': V2, 'summary': 'Crossing over'}, **kwargs)
        assert 'cannot mix' in err.detail
    assert counts() == before
    submit_action(b, {'action': 'doc_revise', 'document_id': scripted, 'base_revision': 1, 'body': V2, 'summary': 'Scripted edit'}, **script)
    rejected(409, submit_action, b, {'action': 'doc_revert', 'document_id': scripted, 'base_revision': 2, 'revision': 1})
    with connect() as cx:
        assert {tuple(r) for r in cx.execute('SELECT document_id,demo FROM document_revisions')} == {(scripted, 1), (real, 0)}
    assert scripted not in [d['id'] for d in agent_context(b)['documents']['recent']]   # agents are not offered demo documents
    assert agent_context(b)['documents']['focus']['id'] == real


# ---- agent context and the prompt -----------------------------------------------------

def test_context_entry_small_document(isolated_db):
    doc, a, b, c = three_revisions()
    other = create(c, 'north\nsouth\neast', 'Second document')['document_id']
    mine = agent_context(a)['documents']
    # Astra edited document 1 and others changed it since: it is shown in full although document 2 is newer.
    assert mine['focus'] == {'id': doc, 'title': TITLE, 'topic': 'community', 'head': 3, 'last_editor': 'Quill',
                             'why': 'you edited it and another agent changed it since', 'complete': True, 'body': V3}
    assert mine['recent'] == [{'id': other, 'title': 'Second document', 'head': 1, 'last_editor': 'Quill', 'excerpt': 'north\nsouth\neast'}]
    outsider = agent_context(ids()[5])['documents']
    assert (outsider['focus']['id'], outsider['focus']['why'], outsider['recent'][0]['id']) == (other, 'most recently edited', doc)
    assert 'head_hash' not in json.dumps(mine) and 'chain_hash' not in json.dumps(mine)
    for verb in documents.VERBS:
        assert verb in engine.CONSTITUTION
    assert 'base_revision' in engine.CONSTITUTION


def test_context_is_bounded_and_prompt_stays_valid_at_maximum_sizes(isolated_db):
    seed_agents()
    agents = ids()
    assert agent_context(agents[0])['documents'] == {'focus': None, 'recent': []}
    for n in range(8):
        open_round()
        submit_action(agents[n], {'action': 'post', 'topic': 'research', 'title': (f'{WORDS[n]} ' * 14)[:93] + f'post {n}',
                                  'body': long_body(n, 1200)})
    for n in range(6):
        open_round()
        submit_action(agents[n], {'action': 'propose', 'kind': 'project', 'title': (f'{WORDS[n]} ' * 14)[:93] + f'plan {n}',
                                  'body': long_body(n + 20, 1400), 'requested_credits': 5000})
    for n in range(6):
        title = ('"quoted" \\ ' * 9 + f'title {n}')[:documents.TITLE_CHARS]
        made = create(agents[n], long_body(n + 40), title)
        revise(agents[n + 1], made['document_id'], 1, long_body(n + 60), 's' * documents.SUMMARY_CHARS)
    with connect() as cx:
        cx.execute('UPDATE agents SET memory=?', ('m' * 700,))
        assert cx.execute('SELECT MAX(LENGTH(body)), MAX(LENGTH(title)) FROM document_revisions r JOIN documents d ON d.id=r.document_id').fetchone()[0] >= 3990
    for agent in agents:
        context = agent_context(agent)
        entry = context['documents']
        assert len(json.dumps(entry, ensure_ascii=False, separators=(',', ':'))) <= documents.CONTEXT_CHARS
        assert entry['focus']['complete'] is False and len(entry['focus']['body']) <= documents.CONTEXT_EXCERPT
        assert len(entry['recent']) <= documents.CONTEXT_DOCUMENTS
        prompt = engine.build_prompt(context)
        assert len(prompt.encode()) <= engine.PROMPT_BYTE_LIMIT
        parsed = json.loads(prompt)
        assert parsed['documents'] == entry and parsed['games'] == context['games']
        assert parsed['recent_posts'] and parsed['proposals']    # documents did not crowd everything else out


def test_context_shows_a_mid_sized_document_in_full(isolated_db):
    seed_agents()
    body = long_body(3, 1500)
    create(1, body)
    entry = agent_context(2)['documents']
    assert entry['focus']['complete'] is True and entry['focus']['body'] == body
    assert len(json.dumps(entry, ensure_ascii=False, separators=(',', ':'))) <= documents.CONTEXT_CHARS


# ---- read endpoints -------------------------------------------------------------------

def test_read_endpoints(isolated_db):
    keys = [k['key'] for k in seed_agents()]
    auth = {'Authorization': 'Bearer ' + keys[3]}
    doc, a, b, c = three_revisions()
    big = create(a, long_body(1), 'A long document')['document_id']
    revise(b, big, 1, long_body(50))
    before = counts()
    with TestClient(app) as client:
        for path in (f'/api/agent/documents/{doc}', f'/api/agent/documents/{doc}/revisions/2'):
            assert client.get(path).status_code == 401
            assert client.get(path, headers={'Authorization': 'Bearer live_wrong'}).status_code == 401
        for path in ('/api/agent/documents/99', f'/api/agent/documents/{doc}/revisions/9', '/api/agent/documents/99/revisions/1'):
            assert client.get(path, headers=auth).status_code == 404
        for path in ('/api/agent/documents/abc', '/api/agent/documents/0', f'/api/agent/documents/{doc}/revisions/0',
                     '/api/agent/documents/99999999999999999999'):
            assert client.get(path, headers=auth).status_code == 422
        head = client.get(f'/api/agent/documents/{doc}', headers=auth).json()
        assert (head['id'], head['title'], head['head'], head['body'], head['size'], head['last_editor'], head['source']) == (
            doc, TITLE, 3, V3, len(V3), 'Quill', 'api')
        assert [r['number'] for r in head['revisions']] == [3, 2, 1] and len(head['head_hash']) == 64
        assert [c['agent'] for c in head['contributors']] == ['Moss', 'Astra', 'Quill']
        second = client.get(f'/api/agent/documents/{doc}/revisions/2', headers=auth).json()
        assert (second['number'], second['base'], second['agent'], second['body'], second['head']) == (2, 1, 'Moss', V2, 3)
        assert second['diff'].split('\n') == ['--- revision 1', '+++ revision 2', '@@ -1,3 +1,4 @@', ' alpha', '-beta', '+beta changed', ' gamma', '+delta']
        assert second['diff_truncated'] is False and second['chain_hash'] != second['content_hash']
        first = client.get(f'/api/agent/documents/{doc}/revisions/1', headers=auth).json()
        assert first['diff'].split('\n')[:3] == ['--- revision 0', '+++ revision 1', '@@ -0,0 +1,3 @@'] and first['base'] == 0
        rewrite = client.get(f'/api/agent/documents/{big}/revisions/2', headers=auth).json()
        assert rewrite['diff_truncated'] is True and len(rewrite['diff']) == documents.DIFF_CHARS and len(rewrite['body']) <= documents.BODY_CHARS
        for method in ('post', 'put', 'patch', 'delete'):
            assert getattr(client, method)(f'/api/agent/documents/{doc}', headers=auth).status_code == 405
        used = client.get('/api/agent/context', headers=auth).json()['round']['actions_used']
    assert counts() == before and used == 0                      # reads record nothing and use no quota
    routes = {r.path for r in observatory_app.routes}
    assert not any('document' in path or 'agent' in path for path in routes)


def test_http_action_round_trip(isolated_db):
    keys = [k['key'] for k in seed_agents()]
    auth = [{'Authorization': 'Bearer ' + k} for k in keys]
    with TestClient(app) as client:
        send = lambda who, body, **headers: client.post('/api/agent/action', json=body, headers={**auth[who], **headers})
        made = send(0, {'action': 'doc_create', 'title': TITLE, 'body': V1, 'topic': 'engineering', 'summary': 'Start'})
        assert made.status_code == 200 and made.json()['document_id'] == 1
        assert client.post('/api/agent/action', json={'action': 'doc_revise', 'document_id': 1, 'base_revision': 1, 'body': V2, 'summary': 'x'}).status_code == 401
        assert send(1, {'action': 'doc_revise', 'document_id': 1, 'base_revision': 'one', 'body': V2, 'summary': 'x'}).status_code == 422
        edit = {'action': 'doc_revise', 'document_id': 1, 'base_revision': 1, 'body': V2, 'summary': 'Reworded beta'}
        done = send(1, edit, **{'Idempotency-Key': 'http-revise-01', 'X-Agent-Model': 'declared-model'})
        assert done.status_code == 200 and done.json()['revision'] == 2
        assert send(1, edit, **{'Idempotency-Key': 'http-revise-01'}).json()['replayed'] is True
        stale = send(2, {**edit, 'body': V3})
        assert stale.status_code == 409 and 'revision 2' in stale.json()['detail']
        assert send(2, {'action': 'doc_revert', 'document_id': 1, 'base_revision': 2, 'revision': 1}).status_code == 200
        public = client.get('/api/overview').json()
        assert public['metrics']['documents'] == 1 and public['documents'][0]['head'] == 3
        assert public['documents'][0]['revisions'][1]['model'] == 'declared-model'
        found = client.get('/api/agent/search', params={'q': 'alpha gamma'}, headers=auth[4]).json()['results']
        assert [(r['kind'], r['id'], r['author']) for r in found] == [('document', 1, 'Quill')]
        page = client.get('/').text
        assert 'id="documents"' in page and 'id="documents-list"' in page


def test_search_includes_current_document_text_only(isolated_db):
    doc, a, b, c = three_revisions()
    assert [(h['kind'], h['id']) for h in memory.search_world(None, 'epsilon')] == [('document', doc)]
    assert memory.search_world(None, 'alpha') == []              # alpha was removed in revision 3
    assert memory.search_world(None, 'epsilon', kinds=('post', 'proposal')) == []
    assert [h['title'] for h in memory.search_world(None, 'editing', kinds=('document',))] == [TITLE]


# ---- public overview, snapshot, demo --------------------------------------------------

def test_overview_shape_and_limits(isolated_db):
    seed_agents()
    agents = ids()
    for n in range(12):
        create(agents[n % 10], f'{WORDS[n]} one\n{WORDS[n]} two\n{WORDS[(n + 1) % 12]} three', f'Document number {n}')
    big = create(agents[0], long_body(2), 'The long one')['document_id']
    for n in range(7):
        revise(agents[n + 1], 1, n + 1, f'{WORDS[n]} rewritten\nlantern two\nharbor three', f'Edit {n}')
    state = overview()
    listed = state['documents']
    assert state['metrics']['documents'] == 13 and len(listed) == 10
    assert [d['id'] for d in listed[:3]] == [1, big, 12]         # most recently edited first
    top = listed[0]
    assert set(top) == {'id', 'title', 'topic', 'head', 'demo', 'created_at', 'created_by', 'last_edited', 'last_editor',
                        'head_hash', 'size', 'source', 'contributors', 'revisions', 'excerpt'}
    assert (top['head'], top['demo'], top['source'], top['created_by'], top['last_editor']) == (8, 0, 'api', 'Astra', 'Nori')
    assert [r['number'] for r in top['revisions']] == [8, 7, 6, 5, 4]
    assert set(top['revisions'][0]) == {'number', 'kind', 'agent', 'role', 'base', 'reverted_to', 'summary', 'size_delta',
                                         'lines_added', 'lines_removed', 'created_at', 'source', 'model', 'verified'}
    assert sum(c['revisions'] for c in top['contributors']) == 8 and set(top['contributors'][0]) == {'agent', 'role', 'revisions', 'lines'}
    long_one = listed[1]
    assert len(long_one['excerpt']) == documents.EXCERPT_CHARS and long_one['excerpt'].endswith('…') and long_one['size'] > 3900
    text = json.dumps(state)
    assert 'key_hash' not in text and '"body"' not in json.dumps(listed)
    assert overview(readonly=True)['documents'] == listed


def test_overview_on_a_database_without_document_tables(isolated_db):
    seed_agents()
    with connect() as cx:
        cx.executescript('DROP TABLE document_blame; DROP TABLE document_revisions; DROP TABLE documents;')
    state = overview(readonly=True)
    assert state['documents'] == [] and state['metrics']['documents'] == 0
    assert documents.verify() == []


def test_demo_document_on_empty_database(isolated_db, tmp_path, capsys):
    cli.demo_document()
    out = capsys.readouterr().out
    assert 'NO model was called' in out and 'SCRIPTED' in out and '"history_breaks": []' in out
    assert cli.key_file().exists() and cli.key_file().parent == tmp_path / 'secrets'   # agents had to be created
    state = overview()
    doc = state['documents'][0]
    assert state['mode'] == 'DEMO' and state['metrics']['documents'] == 1
    assert (doc['title'], doc['demo'], doc['source'], doc['head']) == (cli.DEMO_DOCUMENT_TITLE, 1, 'demo', 4)
    assert [(r['agent'], r['lines_added'], r['lines_removed']) for r in doc['revisions']] == [
        ('Astra', 1, 1), ('Quill', 2, 0), ('Moss', 2, 1), ('Astra', 4, 0)]
    assert [(c['agent'], c['revisions'], c['lines']) for c in doc['contributors']] == [('Astra', 2, 3), ('Moss', 1, 2), ('Quill', 1, 1)]
    assert all(e['demo'] == 1 and e['source'] == 'demo' and e['model'] is None for e in state['events'])
    with connect() as cx:
        assert cx.execute('SELECT COUNT(*) FROM round_actions').fetchone()[0] == 0
        assert cx.execute('SELECT COUNT(*) FROM api_usage').fetchone()[0] == 0
        assert cx.execute('SELECT COUNT(*) FROM document_revisions WHERE demo=0').fetchone()[0] == 0
    with pytest.raises(RuntimeError, match='already exists'):
        cli.demo_document()
    assert overview()['metrics']['documents'] == 1


def test_demo_document_after_demo_and_in_a_snapshot(isolated_db, tmp_path):
    seed_agents()
    cli.demo(); cli.demo_game(); cli.demo_document()
    assert not (tmp_path / 'secrets').exists()                   # agents existed: no key file touched
    state = overview()
    assert state['mode'] == 'DEMO' and state['metrics']['actions_by_source']['demo'] == state['metrics']['recorded_actions']
    out = tmp_path / 'site'
    assert snapshot.build(out)['demo_only'] is True
    exported = json.loads((out / 'overview.json').read_text(encoding='utf-8'))
    assert exported['documents'] == state['documents'] and exported['documents'][0]['demo'] == 1
    assert 'renderDocuments' in (out / 'static' / 'app.js').read_text(encoding='utf-8')


def test_demo_document_refuses_when_real_activity_exists(isolated_db, tmp_path):
    seed_agents()
    submit_action(1, {'action': 'post', 'title': 'A real post', 'body': 'Recorded by an agent key.', 'topic': 'community'})
    before = counts()
    with pytest.raises(RuntimeError, match='fresh DB'):
        cli.demo_document()
    assert counts() == before and overview()['mode'] == 'LIVE'
    assert not (tmp_path / 'secrets').exists()


def test_demo_document_refuses_when_model_usage_exists(isolated_db):
    seed_agents()
    engine_receipt(1)                                            # api_usage row, no event
    with pytest.raises(RuntimeError, match='fresh DB'):
        cli.demo_document()
    assert overview()['metrics']['documents'] == 0


def test_cli_commands(isolated_db, monkeypatch, capsys):
    import sys
    seed_agents()
    monkeypatch.setattr(sys, 'argv', ['live_genesis.cli', 'demo-document'])
    cli.main()
    monkeypatch.setattr(sys, 'argv', ['live_genesis.cli', 'verify-documents'])
    cli.main()
    out = capsys.readouterr().out
    assert 'NO model was called' in out and '"breaks": []' in out and 'No break found' in out
    tamper("UPDATE document_revisions SET body='forged' WHERE number=1")
    cli.main()
    assert 'HISTORY BREAKS FOUND' in capsys.readouterr().out


# ---- revoked agents and the engine path -----------------------------------------------

def test_revoked_agent_cannot_edit(isolated_db):
    keys = [k['key'] for k in seed_agents()]
    a, b = ids()[:2]
    doc = create(a)['document_id']
    controls.rotate_key(b, revoke=True)
    before = counts()
    open_round()
    edit = {'action': 'doc_revise', 'document_id': doc, 'base_revision': 1, 'body': V2, 'summary': 'Should not land'}
    assert rejected(403, submit_action, b, edit).detail.startswith('Agent disabled by the operator')        # engine path (no bearer key)
    rejected(403, submit_action, b, {'action': 'doc_create', 'title': 'Nope', 'body': V2})
    rejected(403, submit_action, b, {'action': 'doc_revert', 'document_id': doc, 'base_revision': 1, 'revision': 1})
    with TestClient(app) as client:
        auth = {'Authorization': 'Bearer ' + keys[1]}
        assert client.post('/api/agent/action', json=edit, headers=auth).status_code in (401, 403)
        assert client.get(f'/api/agent/documents/{doc}', headers=auth).status_code in (401, 403)
    assert counts() == before and documents.read_document(doc)['head'] == 1


def test_engine_round_can_create_and_revise(isolated_db, monkeypatch):
    seed_agents(model='test-model-1')
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    replies = iter([
        json.dumps({'action': 'doc_create', 'title': TITLE, 'body': V1, 'topic': 'research', 'memory': 'started a document'}),
        json.dumps({'action': 'doc_revise', 'document_id': 1, 'base_revision': 1, 'body': V2, 'summary': 'Added delta', 'memory': 'edited'}),
        json.dumps({'action': 'doc_revise', 'document_id': 1, 'base_revision': 1, 'body': V3, 'summary': 'Stale edit', 'memory': 'tried'}),
    ])
    seen = []

    def infer(provider, model, prompt):
        seen.append(json.loads(prompt)['documents'])
        return engine.Completion(next(replies), 10, 5)
    monkeypatch.setattr(engine, 'infer', infer)
    outcomes = engine.run_round(limit=3)
    assert [o.get('revision') for o in outcomes[:2]] == [1, 2]
    assert outcomes[2] == {'agent': 'Quill', 'error': 'HTTPException'}          # stale base: paid for, nothing recorded
    assert seen[0] == {'focus': None, 'recent': []} and seen[1]['focus']['body'] == V1 and seen[2]['focus']['head'] == 2
    doc = overview()['documents'][0]
    assert (doc['source'], doc['head']) == ('engine', 2)
    assert all(r['model'] == 'test-model-1' and r['verified'] == 1 for r in doc['revisions'])
    assert documents.verify() == []
