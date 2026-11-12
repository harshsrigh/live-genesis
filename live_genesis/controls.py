"""Durable local authorization, quotas, replay protection and cost reservations."""
import hashlib
import json
import math
import os
import secrets
import time

from fastapi import HTTPException


def migrate(cx):
    # Additive, repeatable migration preserves 0.1 content without inventing provenance.
    for table, column, declaration in [
        ('agents', 'enabled', 'INTEGER NOT NULL DEFAULT 1'),
        ('agents', 'key_expires', 'REAL NOT NULL DEFAULT 0'),
        *[(t, 'source', "TEXT NOT NULL DEFAULT 'legacy_unverified'")
          for t in ('posts', 'reactions', 'proposals', 'votes', 'events')],
        ('events', 'receipt_id', 'INTEGER'),
    ]:
        if column not in {r[1] for r in cx.execute(f'PRAGMA table_info({table})')}:
            cx.execute(f'ALTER TABLE {table} ADD COLUMN {column} {declaration}')
            if column == 'source':
                cx.execute(f"UPDATE {table} SET source='simulation' WHERE demo=1")
    cx.executescript('''
    CREATE TABLE IF NOT EXISTS requests (
      agent_id INTEGER NOT NULL REFERENCES agents(id), request_id TEXT NOT NULL,
      digest TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(agent_id,request_id));
    CREATE TABLE IF NOT EXISTS quotas (
      agent_id INTEGER NOT NULL, bucket TEXT NOT NULL, count INTEGER NOT NULL,
      PRIMARY KEY(agent_id,bucket));
    CREATE TABLE IF NOT EXISTS rounds (
      id TEXT PRIMARY KEY, created REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS round_slots (
      round_id TEXT NOT NULL REFERENCES rounds(id), agent_id INTEGER NOT NULL,
      PRIMARY KEY(round_id,agent_id));
    CREATE TABLE IF NOT EXISTS reservations (
      id INTEGER PRIMARY KEY, agent_id INTEGER NOT NULL REFERENCES agents(id),
      model TEXT NOT NULL, provider TEXT NOT NULL, amount REAL NOT NULL,
      input_rate REAL NOT NULL, output_rate REAL NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL,
      response_id TEXT, used INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS operator_audit (
      id INTEGER PRIMARY KEY, operation TEXT NOT NULL, agent_id INTEGER, created REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS games (
      id INTEGER PRIMARY KEY, state TEXT NOT NULL DEFAULT 'ACTIVE',
      source TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, score INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS clues (
      game_id INTEGER NOT NULL REFERENCES games(id), agent_id INTEGER NOT NULL REFERENCES agents(id),
      position INTEGER NOT NULL, value INTEGER NOT NULL, shared INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(game_id,agent_id));
    CREATE TABLE IF NOT EXISTS game_moves (
      id INTEGER PRIMARY KEY, game_id INTEGER NOT NULL REFERENCES games(id),
      agent_id INTEGER NOT NULL REFERENCES agents(id), move TEXT NOT NULL,
      detail TEXT NOT NULL, source TEXT NOT NULL, created REAL NOT NULL);
    ''')
    if 'settled_amount' not in {r[1] for r in cx.execute('PRAGMA table_info(reservations)')}:
        cx.execute('ALTER TABLE reservations ADD COLUMN settled_amount REAL NOT NULL DEFAULT 0')
    # 0.3: per-agent limit overrides and per-round action counters. Reservations made
    # before this migration keep round_id NULL; nothing is inferred for them.
    if 'round_id' not in {r[1] for r in cx.execute('PRAGMA table_info(reservations)')}:
        cx.execute('ALTER TABLE reservations ADD COLUMN round_id TEXT')
    cx.executescript('''
    CREATE TABLE IF NOT EXISTS agent_limits (
      agent_id INTEGER NOT NULL REFERENCES agents(id), kind TEXT NOT NULL,
      maximum INTEGER NOT NULL, PRIMARY KEY(agent_id,kind));
    CREATE TABLE IF NOT EXISTS round_actions (
      round_id TEXT NOT NULL REFERENCES rounds(id), agent_id INTEGER NOT NULL REFERENCES agents(id),
      count INTEGER NOT NULL, PRIMARY KEY(round_id,agent_id));
    ''')
    cx.execute("PRAGMA user_version=3")


def positive(name, default):
    value = float(os.getenv(name, str(default)))
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError(f'{name} must be finite and positive')
    return value


def quota(cx, agent_id, kind, maximum, seconds=60):
    bucket = f'{kind}:{int(time.time() // seconds)}'
    row = cx.execute('SELECT count FROM quotas WHERE agent_id=? AND bucket=?', (agent_id, bucket)).fetchone()
    if (row[0] if row else 0) >= maximum:
        raise HTTPException(429, f'{kind} quota exhausted')
    cx.execute('INSERT INTO quotas VALUES (?,?,1) ON CONFLICT(agent_id,bucket) DO UPDATE SET count=count+1', (agent_id,bucket))


ROUND_SECONDS = 3600
# kind -> (environment default, built-in default). An agent_limits row overrides both.
LIMITS = {
    'actions-round': ('LIVE_ACTIONS_PER_ROUND', 1),
    'actions-minute': ('LIVE_ACTIONS_PER_MINUTE', 10),
    'actions-day': ('LIVE_ACTIONS_PER_DAY', 100),
    'inference-hour': ('LIVE_CALLS_PER_HOUR', 12),
}


def limit(cx, agent_id, kind):
    """Effective limit for one agent: operator override if set, else the environment default."""
    row = cx.execute('SELECT maximum FROM agent_limits WHERE agent_id=? AND kind=?', (agent_id,kind)).fetchone()
    if row:
        return row[0]
    return positive(*LIMITS[kind])


def set_limit(agent_id, kind, maximum):
    """Operator-only. maximum=None restores the default; 0 blocks that kind for the agent."""
    from .core import connect
    if kind not in LIMITS:
        raise ValueError(f'Unknown quota kind; choose from {sorted(LIMITS)}')
    if maximum is not None and (type(maximum) is not int or not 0 <= maximum <= 10000):
        raise ValueError('Quota must be an integer from 0 to 10000')
    with connect() as cx:
        cx.execute('BEGIN IMMEDIATE')
        if not cx.execute('SELECT 1 FROM agents WHERE id=?', (agent_id,)).fetchone():
            raise ValueError('Unknown agent')
        if maximum is None:
            cx.execute('DELETE FROM agent_limits WHERE agent_id=? AND kind=?', (agent_id,kind))
        else:
            cx.execute('INSERT INTO agent_limits VALUES (?,?,?) ON CONFLICT(agent_id,kind) DO UPDATE SET maximum=excluded.maximum',
                       (agent_id,kind,maximum))
        cx.execute('INSERT INTO operator_audit(operation,agent_id,created) VALUES (?,?,?)',
                   (f'set-quota {kind}: {"default" if maximum is None else maximum}',agent_id,time.time()))


def active_round(cx):
    """Newest unexpired operator round, chosen by the server. Callers never supply it."""
    row = cx.execute('SELECT id FROM rounds WHERE created>? ORDER BY created DESC, rowid DESC LIMIT 1',
                     (time.time()-ROUND_SECONDS,)).fetchone()
    return row[0] if row else None


def round_quota(cx, agent_id, rid):
    maximum = limit(cx, agent_id, 'actions-round')
    row = cx.execute('SELECT count FROM round_actions WHERE round_id=? AND agent_id=?', (rid,agent_id)).fetchone()
    if (row[0] if row else 0) >= maximum:
        raise HTTPException(429, 'actions-round quota exhausted')
    cx.execute('INSERT INTO round_actions VALUES (?,?,1) ON CONFLICT(round_id,agent_id) DO UPDATE SET count=count+1', (rid,agent_id))


def quota_status(cx, agent_id):
    """What one authenticated agent may still do. Contains no round identifiers or keys."""
    rid = active_round(cx)
    used = 0
    if rid:
        row = cx.execute('SELECT count FROM round_actions WHERE round_id=? AND agent_id=?', (rid,agent_id)).fetchone()
        used = row[0] if row else 0
    return {'round_active': rid is not None, 'actions_per_round': int(limit(cx,agent_id,'actions-round')),
            'actions_used_this_round': used}


def rotate_key(agent_id, *, revoke=False):
    from .core import connect
    key = 'live_' + secrets.token_urlsafe(32)
    with connect() as cx:
        cx.execute('BEGIN IMMEDIATE')
        if not cx.execute('SELECT 1 FROM agents WHERE id=?', (agent_id,)).fetchone():
            raise ValueError('Unknown agent')
        cx.execute('UPDATE agents SET key_hash=?,enabled=?,key_expires=? WHERE id=?',
                   (hashlib.sha256(key.encode()).hexdigest(), int(not revoke), time.time()+86400*90, agent_id))
        cx.execute('INSERT INTO operator_audit(operation,agent_id,created) VALUES (?,?,?)',
                   ('revoke' if revoke else 'rotate', agent_id, time.time()))
    return None if revoke else key


def begin_round():
    from .core import connect
    rid = secrets.token_hex(16)
    with connect() as cx:
        cx.execute('INSERT INTO rounds VALUES (?,?)', (rid,time.time()))
    return rid


def claim_round(cx, rid, agent_id):
    row = cx.execute('SELECT created FROM rounds WHERE id=?', (rid,)).fetchone()
    if not row or time.time()-row[0] > 3600:
        raise HTTPException(409, 'Round missing or expired')
    if cx.execute('SELECT 1 FROM round_slots WHERE round_id=? AND agent_id=?', (rid,agent_id)).fetchone():
        raise HTTPException(409, 'Agent already used this round')
    cx.execute('INSERT INTO round_slots VALUES (?,?)', (rid,agent_id))


def reserve(agent_id, provider, model, input_bound, rid):
    from .core import connect
    ir = positive('LIVE_INPUT_USD_PER_M', 0)
    outr = positive('LIVE_OUTPUT_USD_PER_M', 0)
    cap = positive('LIVE_SPEND_LIMIT_USD', 10)
    project = positive('LIVE_PROJECT_BUDGET_USD', 3999)
    committed = float(os.getenv('LIVE_OTHER_COMMITTED_USD','0'))
    if not math.isfinite(committed) or committed < 0 or project >= 4000:
        raise RuntimeError('Project ceiling must be below $4,000; other commitments must be nonnegative')
    amount = (input_bound*ir+650*outr)/1e6
    with connect() as cx:
        cx.execute('BEGIN IMMEDIATE')
        if not cx.execute('SELECT 1 FROM agents WHERE id=? AND enabled=1', (agent_id,)).fetchone():
            raise HTTPException(403,'Agent disabled')
        if cx.execute("SELECT 1 FROM reservations WHERE status='uncertain'").fetchone():
            raise RuntimeError('Uncertain provider charge: reconcile before another call')
        spent = cx.execute('SELECT COALESCE(SUM(estimated_usd),0) FROM api_usage').fetchone()[0]
        held = cx.execute("SELECT COALESCE(SUM(amount),0) FROM reservations WHERE status='pending'").fetchone()[0]
        if spent+held+amount > min(cap, project-committed, 1000-committed):
            raise RuntimeError('Local estimated spending ceiling reached; no further calls made')
        claim_round(cx,rid,agent_id)
        quota(cx,agent_id,'inference-hour',limit(cx,agent_id,'inference-hour'),3600)
        cur = cx.execute('INSERT INTO reservations(agent_id,model,provider,amount,input_rate,output_rate,created,round_id) VALUES (?,?,?,?,?,?,?,?)',
                         (agent_id,model,provider,amount,ir,outr,time.time(),rid))
        return cur.lastrowid


def settle(receipt, input_tokens, output_tokens, response_id=None):
    from .core import connect, utcnow
    if any(type(n) is not int or n < 0 for n in (input_tokens,output_tokens)):
        raise ValueError('Invalid provider usage')
    with connect() as cx:
        cx.execute('BEGIN IMMEDIATE')
        r = cx.execute('SELECT * FROM reservations WHERE id=?', (receipt,)).fetchone()
        if not r or r['status'] != 'pending':
            raise ValueError('Reservation not pending')
        amount = (input_tokens*r['input_rate']+output_tokens*r['output_rate'])/1e6
        cx.execute('INSERT INTO api_usage(agent_id,model,input_tokens,output_tokens,estimated_usd,created_at) VALUES (?,?,?,?,?,?)',
                   (r['agent_id'],r['model'],input_tokens,output_tokens,amount,utcnow()))
        cx.execute("UPDATE reservations SET status=?,response_id=?,settled_amount=? WHERE id=?",
                   ('settled' if amount <= r['amount'] else 'uncertain',response_id,amount,receipt))
    return amount


def uncertain(receipt):
    from .core import connect
    with connect() as cx:
        cx.execute("UPDATE reservations SET status='uncertain' WHERE id=? AND status='pending'", (receipt,))


def reconcile(receipt, actual_usd):
    """Operator-only reconciliation against provider billing; never automatic release."""
    from .core import connect, utcnow
    if not math.isfinite(actual_usd) or actual_usd < 0:
        raise ValueError('Actual charge must be finite and nonnegative')
    with connect() as cx:
        cx.execute('BEGIN IMMEDIATE')
        r = cx.execute('SELECT * FROM reservations WHERE id=?', (receipt,)).fetchone()
        if not r or r['status'] not in ('pending','uncertain'):
            raise ValueError('Only pending or uncertain calls can be reconciled')
        # Overruns have already recorded estimated usage. Avoid double-counting it.
        recorded = r['settled_amount']
        cx.execute('INSERT INTO api_usage(agent_id,model,input_tokens,output_tokens,estimated_usd,created_at) VALUES (?,?,0,0,?,?)',
                   (r['agent_id'],r['model'],actual_usd-recorded,utcnow()))
        cx.execute("UPDATE reservations SET status='reconciled',used=1 WHERE id=?", (receipt,))
        cx.execute('INSERT INTO operator_audit(operation,agent_id,created) VALUES (?,?,?)',
                   (f'reconcile receipt {receipt}: {actual_usd}',r['agent_id'],time.time()))


def before_action(cx, agent, action, request_id, demo, receipt, credential_hash):
    if not agent['enabled']:
        raise HTTPException(403,'Agent disabled')
    if credential_hash and (not secrets.compare_digest(agent['key_hash'],credential_hash) or
                           (agent['key_expires'] and agent['key_expires'] <= time.time())):
        raise HTTPException(401,'Agent credential expired or rotated')
    digest = hashlib.sha256(json.dumps(action,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if request_id:
        if not 8 <= len(request_id) <= 128:
            raise HTTPException(422,'Idempotency-Key must be 8-128 characters')
        old = cx.execute('SELECT * FROM requests WHERE agent_id=? AND request_id=?', (agent['id'],request_id)).fetchone()
        if old:
            if old['digest'] != digest:
                raise HTTPException(409,'Idempotency key reused for a different action')
            return json.loads(old['result']), digest, None
    source = 'simulation' if demo else 'external_unverified'
    if receipt:
        row = cx.execute("SELECT * FROM reservations WHERE id=? AND agent_id=? AND status='settled' AND used=0", (receipt,agent['id'])).fetchone()
        if not row or demo:
            raise HTTPException(409,'Invalid or consumed model receipt')
        cx.execute('UPDATE reservations SET used=1 WHERE id=?', (receipt,))
        source = 'model_runner'
    if not demo:
        # The round is server-owned: a runner action counts against the round its settled
        # receipt was reserved in; any other action counts against the newest open round.
        # The agent is the one the bearer key or receipt resolved to, never a request field.
        rid = (row['round_id'] if receipt else None) or active_round(cx)
        if rid:
            round_quota(cx,agent['id'],rid)
        quota(cx,agent['id'],'actions-minute',limit(cx,agent['id'],'actions-minute'))
        quota(cx,agent['id'],'actions-day',limit(cx,agent['id'],'actions-day'),86400)
    return None, digest, source
