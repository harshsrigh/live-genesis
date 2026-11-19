"""Task B: an offline end-to-end proof of the free-only collector, through the REAL archive and the REAL
`engine.infer` with the SDK client replaced. No network, no key, no real database (the `isolated_db`
fixture is a temporary file), and nothing here may become a real or public record: every reply below is
scripted text. The scenario is three collections on two free OpenRouter models."""
import json
import sqlite3
from collections import Counter

import pytest

from live_genesis import archive, archive_pages, core, engine, openrouter_setup as setup

from test_archive import ready, rows  # noqa: F401
from test_archive_endpoints import Status, every_cell, fake_sdk, reply

ALPHA, BETA = 'scripted/alpha:free', 'scripted/beta:free'
KEY = 'SENTINEL-OPENROUTER-KEY-0c1d'
REFUSAL = "I'm sorry, but I can't help with that."


@pytest.fixture
def free(ready, monkeypatch):
    values = setup.settings([ALPHA, BETA], [ALPHA, BETA])
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv('LIVE_ARCHIVE_ENDPOINT_OPENROUTER_FREE_PAUSE_SECONDS', '0')
    monkeypatch.setenv('OPENROUTER_API_KEY', KEY)
    monkeypatch.setenv('LIVE_ALLOW_PAID_CALLS', 'YES')       # the harness gate for the stubbed SDK; no request leaves the process
    for name in ('LIVE_INPUT_USD_PER_M', 'LIVE_OUTPUT_USD_PER_M'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    archive.lock()
    return ready


def scripted(plan):
    """An SDK script: `plan[model]` maps the call number for that model (1 = Q01 ...) to text, a
    (text, finish_reason) pair or an exception. Anything absent answers 'Seven.'."""
    count = Counter()

    def script(base_url, request, n):
        model = request['model']
        count[model] += 1
        value = plan.get(model, {}).get(count[model], 'Seven.')
        if isinstance(value, BaseException):
            return value
        text, finish = value if isinstance(value, tuple) else (value, 'stop')
        return reply(text=text, model=model, finish=finish)
    return script


def answers(run, answerer, *columns):
    return rows(f"SELECT question_id,{','.join(columns)} FROM archive_answers a JOIN archive_runs r ON r.id=a.run_id "
                "WHERE r.number=? AND a.answerer=? ORDER BY a.question_id", run, answerer)


def test_three_collections_changed_unchanged_refusal_truncation_rate_limit_interruption(free, monkeypatch):
    # ---- run 1: alpha answers, with a truncated reply (Q02), a refusal (Q03) and a guarded reply (Q04);
    #      beta is rate limited on its 3rd call and must be skipped for the rest of the run.
    seen = fake_sdk(monkeypatch, scripted({
        ALPHA: {2: ('Seve', 'length'), 3: REFUSAL, 4: 'Write to someone@example.org.'},
        BETA: {3: Status(429)}}))
    first = archive.run_archive(max_usd=1)
    assert first['kind'] == 'real' and not first['mock'] and first['status'] == 'complete_with_gaps'
    assert [(a['answerer'], a['outcome'], a['reason'], a['recorded']) for a in first['answerers']] == [
        ('or-free-1', 'complete', '', 20), ('or-free-2', 'skipped', 'rate_limited', 2)]
    assert Counter(c['request']['model'] for c in seen) == {ALPHA: 20, BETA: 3}       # no retry, no further question to beta
    assert {(c['follow_redirects'], c['max_retries']) for c in seen} == {(False, 0)}
    assert all(c['request']['extra_body']['provider']['allow_fallbacks'] is False for c in seen)   # the policy is on every request
    assert all(c['api_key'] == KEY and 'example' not in c['base_url'] for c in seen)
    got = {r['question_id']: r for r in answers(1, 'or-free-1', 'state', 'reason', 'answer_text', 'changed')}
    assert (got['Q01']['state'], got['Q01']['answer_text'], got['Q01']['changed']) == ('answered', 'Seven.', None)
    assert (got['Q02']['state'], got['Q02']['reason'], got['Q02']['answer_text']) == ('no_answer', archive.TRUNCATED, None)
    assert (got['Q03']['state'], got['Q03']['answer_text']) == ('answered', REFUSAL)     # a refusal is the model's answer: kept verbatim
    assert (got['Q04']['state'], got['Q04']['answer_text']) == ('withheld', None)        # a guarded reply keeps a category, never text
    assert len(answers(1, 'or-free-2', 'state')) == 2                                    # the gap is two answers, not 20
    original = rows('SELECT id,question_id,answerer,state,answer_text,answer_sha256,reservation_id,source FROM archive_answers')
    assert {r['source'] for r in original} == {'engine'}                                  # asked by us through the adapter, never 'manual'
    # Receipts: one settled $0 reservation per answered call, the failed call's hold released, none left unconfirmed.
    holds = rows('SELECT status,reserved_usd FROM budget_reservations')
    assert len(holds) == 23 and [h['status'] for h in holds].count('released') == 1 and {h['reserved_usd'] for h in holds} == {0.0}
    assert core.budget_status()['unconfirmed_reservations'] == []
    assert all(r['reservation_id'] for r in original)

    # ---- run 2: Q01 unchanged, Q02 first real answer, Q03 changed from the refusal; beta now completes.
    seen = fake_sdk(monkeypatch, scripted({ALPHA: {2: 'Eight.', 3: 'Seven.'}, BETA: {}}))
    second = archive.run_archive(max_usd=1)
    assert second['run'] == 2 and second['status'] == 'complete' and second['answers']['answered'] == 40
    got = {r['question_id']: r for r in answers(2, 'or-free-1', 'state', 'answer_text', 'changed')}
    assert got['Q01']['changed'] == 0                                  # identical to run 1
    assert got['Q02']['changed'] is None                               # run 1 held no answer to compare with: a gap, not a change
    assert got['Q03']['changed'] == 1                                  # the refusal text was an answer, and this one differs
    assert got['Q04']['changed'] is None and got['Q04']['state'] == 'answered'   # the withheld cell is not compared either
    beta = answers(2, 'or-free-2', 'state', 'changed')
    assert len(beta) == 20 and {r['changed'] for r in beta[:2]} == {0} and beta[2]['changed'] is None   # Q01-Q02 equal run 1; Q03 was a gap in run 1: not a change
    # What was stored in run 1 is exactly what it was.
    assert [r for r in rows('SELECT id,question_id,answerer,state,answer_text,answer_sha256,reservation_id,source FROM archive_answers')
            if r['id'] <= max(o['id'] for o in original)] == original

    # ---- duplicate protection and immutability, at the database.
    with core.connect() as cx:
        with pytest.raises(sqlite3.IntegrityError):
            cx.execute("INSERT INTO archive_answers(run_id,question_set,question_id,answerer,model,source,reservation_id,state,"
                       "answer_text,created_at) SELECT run_id,question_set,question_id,answerer,model,source,reservation_id,state,"
                       "answer_text,created_at FROM archive_answers WHERE id=1")
        for sql in ("UPDATE archive_answers SET answer_text='edited' WHERE id=1", "DELETE FROM archive_answers WHERE id=1"):
            with pytest.raises(sqlite3.IntegrityError, match='append-only'):
                cx.execute(sql)

    # ---- run 3: interrupted (Ctrl+C) on alpha's 5th call. What was recorded stays; the run closes incomplete.
    fake_sdk(monkeypatch, scripted({ALPHA: {5: KeyboardInterrupt()}}))
    with pytest.raises(KeyboardInterrupt):
        archive.run_archive(max_usd=1)
    run = rows('SELECT status,stop_reason FROM archive_runs WHERE number=3')[0]
    assert run['status'] == 'incomplete' and 'interrupted' in run['stop_reason']
    assert len(answers(3, 'or-free-1', 'state')) == 4                                   # four recorded before the interrupt, none invented
    assert answers(3, 'or-free-2', 'state') == []                                       # beta never started: a gap, not an answer
    # A $0 free call interrupted mid-request leaves its $0 hold `held` (a priced one would become `unconfirmed`):
    # it blocks nothing and is still on record. Observed behaviour, pinned here; see docs/SONNET-PROGRESS.md.
    last = rows('SELECT status,reserved_usd FROM budget_reservations ORDER BY id DESC LIMIT 1')[0]
    assert last == {'status': 'held', 'reserved_usd': 0.0} and core.budget_status()['unconfirmed_reservations'] == []
    fake_sdk(monkeypatch, scripted({}))
    assert archive.run_archive(max_usd=1)['run'] == 4                                   # the next collection is not blocked

    # ---- provenance and honesty: stub text never becomes a published mock; engine-source rows are model-track, local only.
    assert {r['source'] for r in rows('SELECT DISTINCT source FROM archive_answers')} == {'engine'}
    assert rows('SELECT DISTINCT kind FROM archive_runs') == [{'kind': 'real'}]
    stored = rows("SELECT endpoint,model,reported_model,pricing_basis,sampling FROM archive_answers WHERE answerer='or-free-1' LIMIT 1")[0]
    assert stored['endpoint'] == 'openrouter-free' and stored['model'] == ALPHA and stored['pricing_basis'] == 'declared-free'
    assert stored['reported_model'] == ALPHA          # requested and reported model are kept as separate fields
    # No key and no base URL in any table.
    assert KEY not in every_cell() and 'openrouter.ai' not in every_cell()
    # The local pages can show the track; the reviewed chat-app export does not carry it (docs/OPENROUTER-COLLECTOR.md).
    payload = archive.public_payload()
    assert archive.payload_problem(payload) is None
    assert 'Eight.' in '\n'.join(archive_pages.render_site(payload).values())


def test_a_nonzero_reported_charge_on_a_free_call_is_never_recorded_as_a_free_answer(free, monkeypatch):
    """The charge guard on the whole archive path."""
    class Usage:
        prompt_tokens, completion_tokens, cost = 40, 12, 0.003

    def script(base_url, request, n):
        done = reply(model=request['model'])
        done.usage = Usage()
        return done
    seen = fake_sdk(monkeypatch, script)
    done = archive.run_archive(max_usd=1)
    # A reported charge on a free-only call is not a gap to skip past: the answer is not stored, the run STOPS after
    # that one request (the second model is not asked), and the hold stays unconfirmed, so every later run is refused
    # until the owner has looked at the provider's billing page and reconciled.
    assert done['status'] != 'complete' and done['status'] != 'complete_with_gaps'
    assert 'reported a charge' in done['stop_reason'] and 'reconcile' in done['stop_reason'] and len(seen) == 1
    assert rows('SELECT COUNT(*) n FROM archive_answers')[0]['n'] == 0
    assert [r['status'] for r in rows('SELECT status FROM budget_reservations')] == ['unconfirmed']
    assert any('reported a charge on a free-only call' in r['operation'] for r in rows('SELECT operation FROM operator_audit'))
    fake_sdk(monkeypatch)
    with pytest.raises(archive.ArchiveRefused, match='Unconfirmed provider charge'):
        archive.run_archive(max_usd=1)
