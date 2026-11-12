"""Commons Challenge: three agents pool private digits to unlock a shared beacon."""
import json
import secrets
import time

from fastapi import HTTPException


def play(cx, agent_id, action, source):
    verb = action['action']
    if verb == 'game_start':
        if cx.execute("SELECT 1 FROM games WHERE state='ACTIVE'").fetchone():
            raise HTTPException(409,'A Commons Challenge is already active')
        peers = [r[0] for r in cx.execute('SELECT id FROM agents WHERE enabled=1 AND id!=? ORDER BY id LIMIT 2',(agent_id,))]
        if len(peers) != 2:
            raise HTTPException(409,'Three active agents are required')
        gid = cx.execute('INSERT INTO games(source) VALUES (?)',(source,)).lastrowid
        for pos, member in enumerate([agent_id]+peers):
            cx.execute('INSERT INTO clues(game_id,agent_id,position,value) VALUES (?,?,?,?)',
                       (gid,member,pos,secrets.randbelow(10)))
        detail = 'Three private clues assigned'
    else:
        gid = action.get('game_id')
        if type(gid) is not int:
            raise HTTPException(422,'game_id integer required')
        game = cx.execute('SELECT * FROM games WHERE id=?',(gid,)).fetchone()
        if not game:
            raise HTTPException(404,'Game not found')
        if game['state'] != 'ACTIVE':
            raise HTTPException(409,'Game is finished')
        if (game['source'] == 'simulation') != (source == 'simulation'):
            raise HTTPException(409,'Simulation and nonsimulation games cannot mix')
        clue = cx.execute('SELECT * FROM clues WHERE game_id=? AND agent_id=?',(gid,agent_id)).fetchone()
        if not clue:
            raise HTTPException(403,'Only assigned software agents can play')
        if verb == 'game_share':
            if clue['shared']:
                raise HTTPException(409,'Clue already shared')
            cx.execute('UPDATE clues SET shared=1 WHERE game_id=? AND agent_id=?',(gid,agent_id))
            detail = f"Position {clue['position']+1} is {clue['value']}"
        else:
            answer = action.get('answer')
            if not isinstance(answer,list) or len(answer)!=3 or any(type(n) is not int or not 0<=n<=9 for n in answer):
                raise HTTPException(422,'answer must contain three integer digits')
            clues = cx.execute('SELECT * FROM clues WHERE game_id=? ORDER BY position',(gid,)).fetchall()
            if not all(c['shared'] for c in clues):
                raise HTTPException(409,'All three agents must share their clues first')
            attempts = game['attempts']+1
            won = answer == [c['value'] for c in clues]
            state = 'WON' if won else 'LOST' if attempts>=3 else 'ACTIVE'
            score = 4-attempts if won else 0
            cx.execute('UPDATE games SET state=?,attempts=?,score=? WHERE id=?',(state,attempts,score,gid))
            detail = json.dumps({'answer':answer,'correct':won})
        if source != game['source']:
            cx.execute("UPDATE games SET source='mixed_unverified' WHERE id=?",(gid,))
    cx.execute('INSERT INTO game_moves(game_id,agent_id,move,detail,source,created) VALUES (?,?,?,?,?,?)',
               (gid,agent_id,verb,detail,source,time.time()))
    game = dict(cx.execute('SELECT * FROM games WHERE id=?',(gid,)).fetchone())
    return {'game_id':gid,'state':game['state'],'score':game['score']}


def public_games(cx):
    games = [dict(r) for r in cx.execute('SELECT * FROM games ORDER BY id DESC LIMIT 10')]
    for game in games:
        game['clues'] = [dict(r) for r in cx.execute('''SELECT c.agent_id,a.name,c.position,c.shared,
            CASE WHEN c.shared=1 THEN c.value ELSE NULL END value FROM clues c
            JOIN agents a ON a.id=c.agent_id WHERE game_id=? ORDER BY position''',(game['id'],))]
        game['moves'] = [dict(r) for r in cx.execute('SELECT * FROM game_moves WHERE game_id=? ORDER BY id',(game['id'],))]
    return games


def context(cx, agent_id):
    games = public_games(cx)
    for game in games:
        clue = cx.execute('SELECT position,value FROM clues WHERE game_id=? AND agent_id=?',(game['id'],agent_id)).fetchone()
        game['your_private_clue'] = dict(clue) if clue else None
    return games
