"""Create synthetic data and serve an isolated loopback-only student load test."""

import argparse
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ottv2 import accounts, cluster, elo_store, match_queue, portal_api


def seed(directory, students):
    if not 2 <= students <= 4000:
        raise ValueError('Use 2..4000 synthetic students.')
    directory = Path(directory).absolute()
    if directory.is_symlink() or directory.exists():
        raise ValueError('Choose a new directory; existing databases are never reused.')
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.umask(0o077)
    project = Path(__file__).resolve().parent.parent
    store = directory/'bots'; store.mkdir()
    matches = directory/'matches'; matches.mkdir()
    queue = directory/'queue.sqlite3'; account_db = directory/'accounts.sqlite3'
    ratings = directory/'ratings.sqlite3'
    match_queue.initialize(queue, store, project/'maps/nine-piece.json', ratings)
    accounts.initialize(account_db, queue)
    cluster.enable(queue)
    # Reuse one fixture password hash to avoid measuring account provisioning.
    # Actual /api/login still performs all 600,000 PBKDF2 rounds normally.
    password = secrets.token_urlsafe(24)
    salt = secrets.token_hex(16)
    digest = accounts._hash(password.encode(), salt)
    source = (project/'examples/greedy/main.py').read_bytes()
    version = hashlib.sha256(source).hexdigest()
    users=[]; sessions=[]; clients=[]
    expires = time.time()+accounts.SESSION_SECONDS
    for i in range(students):
        identity=f'load{i+1:05d}'
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
        users.append((identity,identity,'student',salt,digest,''))
        sessions.append((hashlib.sha256(token.encode()).hexdigest(),identity,csrf,expires))
        clients.append({'identity':identity,'cookie':'ottv2_session='+token,'csrf':csrf})
        folder=store/identity; (folder/'versions').mkdir(parents=True)
        (folder/'versions'/(version+'.py')).write_bytes(source)
        (folder/'current.json').write_text(json.dumps({'schema_version':1,'bot_id':identity,
            'version':version,'entrypoint':'main.py',
            'validation':{'ready':True,'source_sha256':version}}))
    with accounts.database(account_db,write=True) as db:
        db.executemany('INSERT INTO users VALUES (?,?,?,?,?,?)',users)
        db.executemany('INSERT INTO sessions VALUES (?,?,?,?)',sessions)
    with closing(sqlite3.connect(ratings)) as db, db:
        elo_store._check_schema(db,create=True)
        db.executemany('INSERT INTO ratings VALUES (?,1000,0,0,0,0)',[(u[0],) for u in users])
    fixture={'synthetic':True,'fixture_id':secrets.token_hex(16),'students':students,
             'password':password,'clients':clients,
             'note':'Synthetic sessions/READY metadata seeded directly. No real accounts, worker keys or Google client.'}
    private=directory/'clients.private.json'
    private.write_text(json.dumps(fixture));private.chmod(0o600)
    (directory/'load-test-marker.json').write_text(json.dumps({k:fixture[k] for k in ('synthetic','fixture_id','students')}))
    return {'state':'synthetic_seeded','students':students,'directory':str(directory)}


def seed_history(directory, games=10):
    from ottv2 import daily_quota, log_retention
    from ottv2.bots import greedy_bot
    from ottv2.engine import Game, Piece
    from ottv2.protocol import state_message
    import random
    directory=Path(directory).resolve()
    marker_path=directory/'load-test-marker.json'
    marker=json.loads(marker_path.read_text())
    if marker.get('synthetic') is not True or marker.get('history_seeded') or not 1<=games<=10:
        raise ValueError('History requires a marked synthetic fixture without earlier history.')
    queue=directory/'queue.sqlite3';matches=directory/'matches'
    with closing(sqlite3.connect(queue)) as db:
        jobs=[json.loads(row[0]) for row in db.execute("SELECT payload FROM jobs WHERE state='pending'")]
        assert db.execute("SELECT count(*) FROM jobs WHERE state!='pending'").fetchone()[0]==0
    assert len(jobs)*2==marker['students']
    assert not any(matches.iterdir())
    templates={};log_bytes=0
    for first in ('A','B'):
        initial=deepcopy(jobs[0]['initial_state'])
        game=Game({p:Piece(**v) for p,v in initial['board'].items()},initial['goals'],
                  set(initial['bombs']),set(initial['obstacles']),first,
                  max_valid_moves=jobs[0]['limits'].get('max_valid_moves'),
                  move_limit_tiebreak=jobs[0]['limits'].get('move_limit_tiebreak','draw'))
        initial=state_message(game);rng=random.Random(1)
        for _ in range(500):
            if game.result is not None:break
            game.play(greedy_bot(game,rng))
        assert game.result is not None
        templates[first]={'initial_state':initial,'final_state':state_message(game),'history':game.history,
                         'result':{'winner':game.result,'reason':game.reason,'valid_move_count':len(game.history)}}
    with match_queue._write(queue) as db:
        log_retention._schema(db)
        for job in jobs:
            for n in range(games):
                old=deepcopy(job)
                match_id=hashlib.sha256((marker['fixture_id']+job['match_id']+str(n)).encode()).hexdigest()[:32]
                created=datetime.now(timezone.utc)-timedelta(days=n//3+1)
                old.update(match_id=match_id,created_at_utc=created.isoformat(),quota_date=daily_quota.vietnam_day(created))
                record={'schema_version':1,'match_id':match_id,'created_at_utc':created.isoformat(),
                        'bots':{'A':'stored','B':'stored'},'limits':old['limits'],**templates[job['first']],
                        'bot_sources':{s:{'bot_id':p['bot_id'],'version':p['version'],'sha256':p['version'],'entrypoint':'main.py'}
                                       for s,p in old['players'].items()}}
                text=json.dumps(record,ensure_ascii=False)
                target=matches/(match_id+'.json');target.write_text(text,encoding='utf-8');log_bytes+=len(text.encode())
                summary=record['result']
                db.execute("INSERT INTO jobs(match_id,state,payload,attempts,result_path,receipt_json) VALUES (?,'completed',?,1,?,?)",
                           (match_id,json.dumps(old),str(target),json.dumps(summary)))
                for player in old['players'].values():
                    db.execute("INSERT INTO quota_entries VALUES (?,?,?,?,'used')",
                               (match_id,player['bot_id'],old['quota_date'],player['mode']))
                db.execute('INSERT INTO replay_retention(match_id,completed_at,expires_at,result_json) VALUES (?,?,?,?)',
                           (match_id,time.time(),time.time()+7*86400,json.dumps(summary)))
    marker['history_seeded']=games;marker_path.write_text(json.dumps(marker))
    return {'state':'synthetic_history_seeded','completed_fixture_jobs':len(jobs)*games,
            'history_per_student':games,'log_bytes':log_bytes,'note':'Synthetic history; no bot execution or Elo scoring.'}


def serve(directory, port):
    import resource
    directory=Path(directory).resolve()
    marker=json.loads((directory/'load-test-marker.json').read_text())
    if marker.get('synthetic') is not True or port not in (18765,18767):
        raise ValueError('Only marked synthetic fixtures and isolated ports are allowed.')
    queue=directory/'queue.sqlite3';account_db=directory/'accounts.sqlite3'
    with accounts.database(account_db) as db:
        assert db.execute('SELECT count(*) FROM users').fetchone()[0]==marker['students']
        assert db.execute("SELECT count(*) FROM users WHERE identity NOT LIKE 'load%'").fetchone()[0]==0
    server=portal_api.create_server(directory/'bots',port,directory/'matches',queue,account_db)
    class Handler(portal_api.PortalHandler):
        def do_GET(self):
            if self.path=='/_loadtest':
                with closing(sqlite3.connect(queue)) as db:
                    jobs=dict(db.execute('SELECT state,count(*) FROM jobs GROUP BY state'))
                    busy=db.execute('SELECT count(*) FROM busy').fetchone()[0]
                usage=resource.getrusage(resource.RUSAGE_SELF)
                self.reply(200,{**marker,'jobs':jobs,'busy_players':busy,
                    'cpu_seconds':usage.ru_utime+usage.ru_stime,'max_rss_kib':usage.ru_maxrss,
                    'server_model':type(server).__name__,'python':sys.version.split()[0]})
            else:super().do_GET()
    server.RequestHandlerClass=Handler
    server.coordinator=cluster.Coordinator(queue,directory/'matches')
    # No Google sync, Isolate, worker loop, public listener or production DB.
    signal.signal(signal.SIGTERM,lambda *_:sys.exit(0))
    print(json.dumps({'state':'isolated_test_server','port':port,'students':marker['students']}),flush=True)
    with server:server.serve_forever(poll_interval=0.05)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('seed','serve','history'))
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--students',type=int,default=4000)
    parser.add_argument('--port',type=int,default=18765)
    args=parser.parse_args()
    if args.command=='seed':print(json.dumps(seed(args.directory,args.students)))
    elif args.command=='history':print(json.dumps(seed_history(args.directory)))
    else:serve(args.directory,args.port)
