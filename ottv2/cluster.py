"""Authenticated worker registry, expiring job leases and fenced result commits."""

import argparse
import base64
from contextlib import closing
from contextlib import contextmanager
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
from .rules import RULES_VERSION, SUPPORTED_RULES_VERSIONS, UPDATE_REQUIRED, supports_limits
from tempfile import TemporaryDirectory
from threading import Event, RLock
import time
from urllib.parse import urlsplit

from . import bot_store, elo_store, local_worker, log_retention, match_queue
from .engine import Game, Piece
from .match_archive import MAX_LOG_BYTES, validate_match_id
from .protocol import state_message
from .result_log import write_result

LEASE_SECONDS = 180
MAX_ATTEMPT_SECONDS = 300
MAX_RPC_BYTES = 16 * 1024 * 1024
_LOCK = RLock()


class LeaseRejected(ValueError):
    pass


def master_url(value):
    parsed = urlsplit(value)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')
            or not parsed.hostname or parsed.port == 0
            or parsed.scheme not in ('http', 'https')
            or parsed.scheme == 'http' and parsed.hostname not in ('127.0.0.1', 'localhost', '::1')):
        raise ValueError('worker URL requires HTTPS; HTTP is allowed only on loopback')
    return value.rstrip('/')


def initialize(database):
    with match_queue._write(database) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS cluster_workers (
            worker_id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1, last_seen REAL NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '')''')
        db.execute('''CREATE TABLE IF NOT EXISTS cluster_leases (
            match_id TEXT PRIMARY KEY REFERENCES jobs(match_id), worker_id TEXT NOT NULL,
            token_hash TEXT NOT NULL, expires_at REAL NOT NULL, deadline REAL NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('active','expired','failed','completed')))''')
        db.execute('CREATE INDEX IF NOT EXISTS cluster_worker_leases ON cluster_leases(worker_id,state)')


def enable(database):
    initialize(database)
    with match_queue._write(database) as db:
        cfg = match_queue._config(db)
        cfg['execution_mode'] = 'distributed'
        if cfg['ratings'] is None:
            path = Path(database).absolute()
            cfg['ratings'] = str(path.parent / ('elo.sqlite3' if path.name == 'ratings.sqlite3' else 'ratings.sqlite3'))
        db.execute('UPDATE config SET data=? WHERE id=1', (json.dumps(cfg),))


def use_local(database):
    initialize(database)
    with _LOCK, match_queue._write(database) as db:
        if db.execute("SELECT 1 FROM jobs WHERE state='running' LIMIT 1").fetchone():
            raise ValueError('finish or recover running distributed jobs before switching to local mode')
        cfg = match_queue._config(db)
        cfg['execution_mode'] = 'local'
        db.execute('UPDATE config SET data=? WHERE id=1', (json.dumps(cfg),))


def register(database, identity, url, destination):
    bot_store.validate_bot_id(identity)
    url = master_url(url)
    target = elo_store._database_path(destination, 'Worker credential')
    if target.exists():
        raise FileExistsError('worker credential file already exists')
    initialize(database)
    token = secrets.token_hex(32)
    target.parent.mkdir(parents=True, exist_ok=True)
    # File and registry must both succeed; exclusive creation never replaces a key.
    with _LOCK, match_queue._write(database) as db:
        db.execute('INSERT INTO cluster_workers(worker_id,token_hash) VALUES (?,?)',
                   (identity, hashlib.sha256(token.encode()).hexdigest()))
        try:
            with target.open('x', encoding='utf-8') as stream:
                target.chmod(0o600)
                json.dump({'worker_id': identity, 'url': url, 'token': token}, stream)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            # A partially written credential is never a usable registration.
            raise
    return {'state': 'worker_registered', 'worker_id': identity, 'credential_file': str(target)}


def revoke(database, identity):
    bot_store.validate_bot_id(identity)
    initialize(database)
    with _LOCK, match_queue._write(database) as db:
        if db.execute('UPDATE cluster_workers SET enabled=0 WHERE worker_id=?', (identity,)).rowcount != 1:
            raise ValueError('unknown worker')
        db.execute("UPDATE cluster_leases SET expires_at=0 WHERE worker_id=? AND state='active'", (identity,))
    return {'state': 'worker_revoked', 'worker_id': identity}


def status(database, *, now=None):
    now = time.time() if now is None else now
    with closing(elo_store._connect(Path(database).absolute(),readonly=True)) as db:
        cfg = match_queue._config(db)
        if cfg.get('execution_mode') != 'distributed':
            return {'mode': 'local', 'workers': []}
        rows = db.execute('''SELECT w.worker_id,w.enabled,w.last_seen,w.last_error,
            l.match_id,l.expires_at FROM cluster_workers w LEFT JOIN cluster_leases l
            ON l.worker_id=w.worker_id AND l.state='active' ORDER BY w.worker_id''').fetchall()
    labels = cfg.get('worker_labels', {})
    workers = []
    for row in rows:
        item = dict(row)
        host, separator, slot = row['worker_id'].rpartition('-')
        item['display_name'] = labels.get(host, host) + ' / ' + slot if separator and slot.isdigit() else row['worker_id']
        ceiling = cfg.get('worker_slot_limits', {}).get(host)
        item['slot_enabled'] = not (separator and slot.isdigit() and ceiling is not None and int(slot) > ceiling)
        item['online'] = bool(row['enabled'] and row['last_seen'] > now-45)
        workers.append(item)
    return {'mode': 'distributed', 'workers': workers}


def _replay(job, record):
    initial = job['initial_state']
    game = Game({p: Piece(**v) for p, v in initial['board'].items()}, initial['goals'],
                set(initial['bombs']), set(initial['obstacles']), job['first'],
                max_valid_moves=job['limits'].get('max_valid_moves'),
                move_limit_tiebreak=job['limits'].get('move_limit_tiebreak', 'draw'))
    for event in record['history']:
        if game.play({'from': event['from'], 'to': event['to']}) != event:
            raise ValueError('worker history differs from Engine')
    if state_message(game) != record['final_state'] or record.get('cleanup_complete') is not True:
        raise ValueError('worker final state or cleanup does not match')
    result = record['result']
    if game.result is not None:
        if (game.result, game.reason) != (result['winner'], result['reason']):
            raise ValueError('worker game outcome differs from Engine')
    elif result['reason'] in ('goal', 'missing_kind', 'threefold_repetition', 'move_limit', 'material_advantage'):
        raise ValueError('worker declared an unfinished game complete')


class Coordinator:
    def __init__(self, database, directory):
        self.database = Path(database).absolute()
        self.directory = elo_store._database_path(Path(directory)/'path-check', 'Result').parent
        initialize(self.database)

    def authenticate(self, token):
        if not isinstance(token, str) or not re.fullmatch('[a-f0-9]{64}', token):
            raise PermissionError('worker authentication required')
        digest = hashlib.sha256(token.encode()).hexdigest()
        with match_queue._write(self.database) as db:
            row = db.execute('SELECT worker_id,token_hash FROM cluster_workers WHERE token_hash=? AND enabled=1',
                             (digest,)).fetchone()
        if row is None or not hmac.compare_digest(row['token_hash'], digest):
            raise PermissionError('worker authentication required')
        return row['worker_id']

    def _lease(self, db, worker, match_id, token, now, *, completed=False):
        validate_match_id(match_id)
        if not isinstance(token, str) or not re.fullmatch('[a-f0-9]{64}', token):
            raise LeaseRejected('invalid lease')
        row = db.execute('''SELECT l.*,j.state AS job_state FROM cluster_leases l
            JOIN jobs j USING(match_id) JOIN cluster_workers w ON w.worker_id=l.worker_id
            WHERE l.match_id=? AND l.worker_id=? AND w.enabled=1''', (match_id, worker)).fetchone()
        if (row is None or not hmac.compare_digest(row['token_hash'], hashlib.sha256(token.encode()).hexdigest())
                or not (completed and row['job_state']=='completed')
                and (row['state']!='active' or row['job_state']!='running' or row['expires_at']<=now)):
            raise LeaseRejected('lease expired or replaced')
        return row

    def _finish(self, job, output, ratings):
        scored = local_worker._validated_result(job, output)
        _replay(job, scored[0])
        receipt = elo_store._apply_scored_input(ratings, *scored)
        local_worker._finish(self.database, job['match_id'], receipt, scored[0]['result'])
        with match_queue._write(self.database) as db:
            db.execute("UPDATE cluster_leases SET state='completed' WHERE match_id=?", (job['match_id'],))
        return {'state': 'completed', 'match_id': job['match_id'], 'elo_applied': receipt['applied']}

    def recover(self, *, now=None):
        now = time.time() if now is None else now
        with _LOCK:
            with match_queue._write(self.database) as db:
                cfg = match_queue._config(db)
                # Queue completion and lease retirement are separate commits.
                # Repair a crash in between before a worker asks for another job.
                db.execute("""UPDATE cluster_leases SET state=(
                    SELECT CASE j.state WHEN 'completed' THEN 'completed'
                        WHEN 'failed' THEN 'failed' ELSE 'expired' END
                    FROM jobs j WHERE j.match_id=cluster_leases.match_id)
                    WHERE state='active' AND match_id IN (
                        SELECT match_id FROM jobs WHERE state!='running')""")
                rows = db.execute('''SELECT j.*,l.expires_at,l.state AS lease_state FROM jobs j
                    LEFT JOIN cluster_leases l USING(match_id) WHERE j.state='running' ORDER BY j.rowid LIMIT 100''').fetchall()
            for row in rows:
                output = Path(row['result_path']) if row['result_path'] else self.directory/(row['match_id']+'.json')
                job = json.loads(row['payload'])
                if output.exists() or output.is_symlink():
                    self._finish(job, output, cfg['ratings'])
                elif row['expires_at'] is None or row['expires_at']<=now or row['lease_state']!='active':
                    terminal = row['attempts']>=3
                    local_worker._failed(self.database, row['match_id'], 'worker lease expired', terminal=terminal)
                    with match_queue._write(self.database) as db:
                        if not terminal:
                            db.execute("UPDATE jobs SET state='pending' WHERE match_id=?", (row['match_id'],))
                        db.execute("UPDATE cluster_leases SET state='expired' WHERE match_id=?", (row['match_id'],))

    def call(self, worker, request, *, now=None, rules_version=RULES_VERSION):
        now = time.time() if now is None else now
        if (not math.isfinite(now) or not isinstance(request, dict)
                or type(rules_version) is not int or rules_version not in SUPPORTED_RULES_VERSIONS):
            raise ValueError('invalid worker request')
        action = request.get('action')
        allowed = {'claim': {'action'}, 'heartbeat': {'action','match_id','lease_token'},
                   'complete': {'action','match_id','lease_token','record'},
                   'fail': {'action','match_id','lease_token','message'}}
        if action not in allowed or set(request)!=allowed[action]:
            raise ValueError('invalid worker action')
        with _LOCK:
            with match_queue._write(self.database) as db:
                cfg = match_queue._config(db)
                if cfg.get('execution_mode')!='distributed':
                    raise ValueError('distributed mode is not enabled')
                if db.execute('UPDATE cluster_workers SET last_seen=? WHERE worker_id=? AND enabled=1',
                              (now, worker)).rowcount!=1:
                    raise PermissionError('worker disabled')
            if action=='claim':
                return self._claim(worker, now, rules_version)
            with match_queue._write(self.database) as db:
                lease = self._lease(db, worker, request['match_id'], request['lease_token'], now,
                                    completed=action=='complete')
                if action=='heartbeat':
                    expiry = min(now+LEASE_SECONDS, lease['deadline'])
                    if expiry<=now:
                        raise LeaseRejected('attempt deadline exceeded')
                    db.execute('UPDATE cluster_leases SET expires_at=? WHERE match_id=?',
                               (expiry, request['match_id']))
                    return {'state':'renewed','expires_at':expiry}
            if action=='fail':
                message=request['message']
                if not isinstance(message,str) or len(message)>1024:
                    raise ValueError('invalid worker error')
                job=match_queue.get_job(self.database, request['match_id'])
                # Once a result has been published, only recovery may complete it.
                if job['result_path'] and Path(job['result_path']).exists():
                    raise LeaseRejected('result already published')
                terminal=job['attempts']>=3
                local_worker._failed(self.database, job['match_id'], 'remote worker system failure', terminal)
                with match_queue._write(self.database) as db:
                    if not terminal:
                        db.execute("UPDATE jobs SET state='pending' WHERE match_id=?", (job['match_id'],))
                    db.execute("UPDATE cluster_leases SET state='failed' WHERE match_id=?", (job['match_id'],))
                    db.execute('UPDATE cluster_workers SET last_error=? WHERE worker_id=?', (message,worker))
                return {'state':'failed' if terminal else 'retry_pending','match_id':job['match_id']}
            return self._complete(worker, request, lease, cfg)

    def _claim(self, worker, now, rules_version):
        self.recover(now=now)
        with match_queue._write(self.database) as db:
            if rules_version < RULES_VERSION:
                db.execute('UPDATE cluster_workers SET last_error=? WHERE worker_id=?', (UPDATE_REQUIRED,worker))
            else:
                db.execute("UPDATE cluster_workers SET last_error='' WHERE worker_id=? AND last_error=?",
                           (worker,UPDATE_REQUIRED))
            if db.execute("SELECT 1 FROM cluster_leases WHERE worker_id=? AND state='active' AND expires_at>?",
                          (worker,now)).fetchone():
                return {'state':'busy'}
            cfg = match_queue._config(db)
            host, separator, slot = worker.rpartition('-')
            ceiling = cfg.get('worker_slot_limits', {}).get(host)
            if separator and slot.isdigit() and ceiling is not None and int(slot) > ceiling:
                return {'state':'idle','reason':'slot_limit'}
            row=db.execute("SELECT * FROM jobs WHERE state='pending' ORDER BY rowid LIMIT 1").fetchone()
            if row is None:
                return {'state':'idle'}
            job=json.loads(row['payload'])
            local_worker._validate_job(job)
            if not supports_limits(job['limits'], rules_version):
                # Old clients understand idle. Do not lease a job they would fail.
                return {'state':'idle','reason':'worker_update_required'}
            if row['attempts']>=3:
                raise ValueError('pending job has exhausted retry count')
            sources={side:base64.b64encode(bot_store.load_version(job['bot_store'],p['bot_id'],p['version'])).decode('ascii')
                     for side,p in job['players'].items()}
            token=secrets.token_hex(32)
            expiry=now+LEASE_SECONDS
            db.execute('''INSERT INTO cluster_leases VALUES (?,?,?,?,?, 'active')
                ON CONFLICT(match_id) DO UPDATE SET worker_id=excluded.worker_id,token_hash=excluded.token_hash,
                expires_at=excluded.expires_at,deadline=excluded.deadline,state='active' ''',
                (job['match_id'],worker,hashlib.sha256(token.encode()).hexdigest(),expiry,now+MAX_ATTEMPT_SECONDS))
            output=str(self.directory/(job['match_id']+'.json'))
            db.execute("UPDATE jobs SET state='running',attempts=attempts+1,result_path=?,last_error=NULL WHERE match_id=?",
                       (output,job['match_id']))
        job.pop('bot_store',None)
        return {'state':'assigned','job':job,'sources':sources,'lease_token':token,'expires_at':expiry}

    def _complete(self, worker, request, lease, cfg):
        job=match_queue.get_job(self.database, request['match_id'])
        output=Path(job['result_path'])
        record=request['record']
        if not isinstance(record,dict) or len(json.dumps(record,allow_nan=False).encode('utf-8'))>MAX_LOG_BYTES:
            raise ValueError('worker result too large or invalid')
        self.directory.mkdir(parents=True,exist_ok=True)
        with TemporaryDirectory(prefix='.validate-',dir=self.directory) as temp:
            candidate=Path(temp)/(job['match_id']+'.json')
            write_result(candidate,record)
            scored=local_worker._validated_result(job,candidate)
            _replay(job,record)
            if job['state']=='completed':
                if job['elo_receipt']['record_sha256']!=scored[2]:
                    raise LeaseRejected('completed job has a different result')
                return {'state':'completed','match_id':job['match_id'],'elo_applied':False}
            if output.exists() or output.is_symlink():
                saved=local_worker._validated_result(job,output)
                if saved[2]!=scored[2]:
                    raise LeaseRejected('published result differs')
            else:
                write_result(output,record)
        return self._finish(job,output,cfg['ratings'])


@contextmanager
def master_lock():
    # Separate from the sandbox lock: the same host may run a worker as well.
    import fcntl
    from tempfile import gettempdir
    path=Path(gettempdir())/f'ottv2-master-{os.getuid()}.lock'
    flags=os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC
    with os.fdopen(os.open(path,flags,0o600),'r+b') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid():
            raise ValueError('invalid master lock')
        try: fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error: raise RuntimeError('another distributed master is running') from error
        try: yield
        finally: fcntl.flock(stream,fcntl.LOCK_UN)


def run_loop(coordinator, *, stop_event=None, interval=2.0, max_cycles=None, maintenance=None, emit=None):
    if (type(interval) not in (int,float) or not math.isfinite(interval) or not 0.1<=interval<=60
            or max_cycles is not None and (type(max_cycles) is not int or max_cycles<1)):
        raise ValueError('invalid master loop interval/cycles')
    stop=stop_event if stop_event is not None else Event()
    cycles=0
    next_cleanup=0
    with master_lock():
        while not stop.is_set():
            coordinator.recover()
            if maintenance is not None:
                try:
                    with local_worker._worker_lock():
                        answer=maintenance()
                        if answer and emit: emit(answer)
                except RuntimeError as error:
                    if 'another local Worker' not in str(error): raise
            for _ in range(32):
                if match_queue.dispatch(coordinator.database)['state']!='matched': break
            if time.monotonic()>=next_cleanup:
                next_cleanup=time.monotonic()+log_retention.CHECK_INTERVAL
                try:
                    cleanup=log_retention.prune(coordinator.database,coordinator.directory,apply=True)
                    if emit and (cleanup['deleted'] or cleanup['skipped']): emit(cleanup)
                except (ValueError,OSError,sqlite3.Error):
                    if emit:
                        emit({'state':'log_cleanup_retry',
                              'message':'Chưa dọn được bản ghi cũ; sẽ thử lại sau một phút.'})
            cycles+=1
            if max_cycles is not None and cycles>=max_cycles: break
            if stop.wait(interval): break
    return {'state':'stopped','cycles':cycles,'mode':'distributed'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True,type=Path)
    sub=parser.add_subparsers(dest='command',required=True)
    add=sub.add_parser('add');add.add_argument('identity');add.add_argument('--url',required=True);add.add_argument('--output',required=True,type=Path)
    delete=sub.add_parser('revoke');delete.add_argument('identity')
    sub.add_parser('status');sub.add_parser('local')
    args=parser.parse_args()
    try:
        if args.command=='add': result=register(args.db,args.identity,args.url,args.output)
        elif args.command=='revoke': result=revoke(args.db,args.identity)
        elif args.command=='local':
            with master_lock(), local_worker._worker_lock(): use_local(args.db)
            result={'state':'local_mode'}
        else: result=status(args.db)
        print(json.dumps(result,ensure_ascii=False))
    except (ValueError,OSError,RuntimeError,KeyError,sqlite3.Error):
        parser.exit(1,'Không cấu hình được máy chấm. Kiểm tra ID, URL và tệp khóa mới.\n')


if __name__=='__main__': main()
