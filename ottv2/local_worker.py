"""Run one local queued job, recover its saved result, and finish Elo safely."""

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import random
import re
import sqlite3
import stat
import sys
from tempfile import TemporaryDirectory, gettempdir

from . import daily_quota, elo_store, log_retention, match_queue
from .bot_store import IsolatedPinnedSession, validate_bot_id
from .cpu_trial import run_trial
from .rules import supports_limits
from .isolated_session import BOX_ROOT, validate_box_pair
from .map_config import load_map
from .match_archive import validate_match_id


@contextmanager
def _host_gate(*, pool=False):
    if not sys.platform.startswith('linux'):
        raise RuntimeError('run the Worker inside Ubuntu')
    import fcntl
    path = Path(gettempdir()) / 'ottv2-worker-pool.lock'
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
    with os.fdopen(os.open(path, flags, 0o600), 'r+b') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('invalid host Worker lock')
        try:
            fcntl.flock(stream, (fcntl.LOCK_EX if pool else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('host already runs a Worker/pool; stop it before changing mode') from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


@contextmanager
def _worker_lock(box_ids=None):
    if not sys.platform.startswith('linux'):
        raise RuntimeError('run the local Worker inside Ubuntu')
    import fcntl
    pair = validate_box_pair(box_ids) if box_ids is not None else (0, 1)
    # Pool pairs have a host-wide name: a different Linux account cannot bypass
    # their lock. 0/1 retain the existing lock path for backward compatibility.
    name = f'ottv2-worker-{os.getuid()}.lock' if pair == (0, 1) else f'ottv2-box-pair-{pair[0]}.lock'
    path = Path(gettempdir()) / name
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
    with os.fdopen(os.open(path, flags, 0o600), 'r+b') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('invalid Worker lock file')
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('another local Worker is running for this Linux user') from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _claim(database, results):
    with match_queue._write(database) as connection:
        config = match_queue._config(connection)
        if config.get('execution_mode') == 'distributed':
            raise RuntimeError('queue uses distributed workers; start the master with --distributed')
        if config['ratings'] is None:
            queue_path = Path(database).absolute()
            name = 'elo.sqlite3' if queue_path.name == 'ratings.sqlite3' else 'ratings.sqlite3'
            config['ratings'] = str(elo_store._database_path(queue_path.parent / name))
            connection.execute('UPDATE config SET data=? WHERE id=1', (json.dumps(config),))
        row = connection.execute("""SELECT * FROM jobs WHERE state IN ('running','pending')
            ORDER BY CASE state WHEN 'running' THEN 0 ELSE 1 END, rowid LIMIT 1""").fetchone()
        if row is None:
            return None
        job = json.loads(row['payload'])
        output = row['result_path'] or str(results / (row['match_id'] + '.json'))
        connection.execute("UPDATE jobs SET state='running', result_path=? WHERE match_id=?",
                           (output, row['match_id']))
        return job, Path(output), config['ratings']


def _begin_attempt(database, match_id):
    with match_queue._write(database) as connection:
        row = connection.execute('SELECT state,attempts FROM jobs WHERE match_id=?', (match_id,)).fetchone()
        if row['state'] != 'running':
            raise ValueError('Worker job is no longer running')
        if row['attempts'] >= 3:
            return None
        attempt = row['attempts'] + 1
        connection.execute('UPDATE jobs SET attempts=?,last_error=NULL WHERE match_id=?', (attempt, match_id))
        return attempt


def _failed(database, match_id, error, terminal=False):
    with match_queue._write(database) as connection:
        row = connection.execute('SELECT state,payload FROM jobs WHERE match_id=?', (match_id,)).fetchone()
        if row['state'] != 'running':
            raise ValueError('Worker job is no longer running')
        connection.execute('UPDATE jobs SET state=?,last_error=? WHERE match_id=?',
                           ('failed' if terminal else 'running', str(error)[:1024], match_id))
        if terminal:
            daily_quota.settle(connection, json.loads(row['payload']), used=False)
            connection.execute('DELETE FROM busy WHERE match_id=?', (match_id,))


def _finish(database, match_id, receipt, result):
    with match_queue._write(database) as connection:
        row = connection.execute('SELECT state,payload FROM jobs WHERE match_id=?', (match_id,)).fetchone()
        if row['state'] != 'running' or receipt['match_id'] != match_id:
            raise ValueError('completion does not match a running job')
        daily_quota.settle(connection, json.loads(row['payload']), used=True)
        connection.execute("UPDATE jobs SET state='completed',receipt_json=?,last_error=NULL WHERE match_id=?",
                           (json.dumps(receipt, allow_nan=False), match_id))
        log_retention.record_completion(connection, match_id, result)
        connection.execute('DELETE FROM busy WHERE match_id=?', (match_id,))


def _validate_job(job):
    validate_match_id(job['match_id'])
    if (job.get('schema_version') != 1 or type(job.get('seed')) is not int
            or not 0 <= job['seed'] < 2**31
            or job.get('first') != random.Random(job['seed']).choice(('A', 'B'))):
        raise ValueError('invalid pinned job seed or first side')
    if not supports_limits(job.get('limits')):
        raise ValueError('unsupported pinned resource limits')
    identities = []
    for side in ('A', 'B'):
        player = job['players'][side]
        validate_bot_id(player['bot_id'])
        if (player.get('mode') not in daily_quota.LIMITS or not isinstance(player.get('version'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', player['version'])):
            raise ValueError('invalid pinned player version')
        identities.append(player['bot_id'])
    if identities[0] == identities[1]:
        raise ValueError('a job cannot play against itself')
    if (job['initial_state'].get('turn') != job['first'] or job['initial_state'].get('you') != job['first']
            or not isinstance(job['map'].get('sha256'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', job['map']['sha256'])):
        raise ValueError('invalid pinned initial state or map identity')


def _run_job(job, output, *, box_ids=(0, 1)):
    box_ids = validate_box_pair(box_ids)
    sessions = {box: IsolatedPinnedSession(job['bot_store'], player['bot_id'], player['version'],
                                          box, job['seed'], player.get('origin'))
                for box, player in zip(box_ids, (job['players'][side] for side in ('A', 'B')))}
    with TemporaryDirectory(prefix='ottv2-worker-map-') as temporary:
        path = Path(temporary) / 'map.json'
        config = {'schema_version': 1, 'name': job['map']['name'],
                  **{key: job['initial_state'][key] for key in ('board', 'goals', 'bombs', 'obstacles')}}
        path.write_text(json.dumps(config, allow_nan=False), encoding='utf-8')
        load_map(path, job['first'])  # Validate the saved board before starting either bot.
        run_trial(seed=job['seed'], bot_a='stored', bot_b='stored', emit=lambda _: None,
                  cpu_budget=job['limits']['cpu_budget_seconds'],
                  response_timeout=job['limits']['response_limit_seconds'],
                  max_valid_moves=job['limits'].get('max_valid_moves'),
                  move_limit_tiebreak=job['limits'].get('move_limit_tiebreak', 'draw'),
                  session_factory=lambda strategy, box, seed: sessions[box],
                  result_file=output, map_file=path, match_id=job['match_id'], map_identity=job['map'],
                  box_ids=box_ids)
    if not output.is_file():
        raise RuntimeError('runner produced no scoreable result')


def _validated_result(job, output):
    scored = elo_store._scoring_input(output)
    record, players, _ = scored
    if (record['match_id'] != job['match_id'] or record.get('seed') != job['seed']
            or record['initial_state'] != job['initial_state'] or record.get('map') != job['map']
            or record.get('limits') != job['limits']
            or any(players[side]['bot_id'] != job['players'][side]['bot_id']
                   or players[side]['version'] != job['players'][side]['version'] for side in ('A', 'B'))):
        raise ValueError('saved result does not match the pinned queue job')
    return scored


def run_once(database, results=None, runner=None):
    with _host_gate(), _worker_lock():
        return _run_once_locked(database, results, runner)


def _run_once_locked(database, results=None, runner=None):
    results = Path(results).absolute() if results is not None else Path(database).absolute().parent / 'matches'
    for directory in (results, *results.parents):
        if directory.is_symlink() or directory.is_junction():
            raise ValueError('Worker results directory must not contain links')
    if results.exists() and not results.is_dir():
        raise ValueError('Worker results path must be a directory')
    claimed = _claim(database, results)
    if claimed is None:
        return {'state': 'idle'}
    job, output, ratings = claimed
    for directory in (output.parent, *output.parent.parents):
        if directory.is_symlink() or directory.is_junction():
            raise ValueError('saved Worker results directory must not contain links')
    _validate_job(job)
    while not output.exists() and not output.is_symlink():
        if any((BOX_ROOT / str(box)).exists() or (BOX_ROOT / str(box)).is_symlink() for box in (0, 1)):
            raise RuntimeError('Isolate boxes occupied; refusing to reset or consume another attempt')
        attempt = _begin_attempt(database, job['match_id'])
        if attempt is None:
            _failed(database, job['match_id'], 'system retry limit reached', terminal=True)
            return {'state': 'failed', 'match_id': job['match_id']}
        try:
            (runner or _run_job)(job, output)
            if not output.is_file():
                raise RuntimeError('runner produced no scoreable result')
        except Exception as error:
            # A completed atomic log is recoverable even if the caller died
            # immediately after publishing it. Never rerun or overwrite it.
            if output.exists() or output.is_symlink():
                raise
            _failed(database, job['match_id'], error, terminal=attempt >= 3)
            if attempt >= 3:
                return {'state': 'failed', 'match_id': job['match_id'], 'error': str(error)[:1024]}
    scored = _validated_result(job, output)
    receipt = elo_store._apply_scored_input(ratings, *scored)
    # Separate durable databases: if this commit fails, busy stays held.
    # The next invocation reuses the log and Elo's duplicate receipt.
    _finish(database, job['match_id'], receipt, scored[0]['result'])
    return {'state': 'completed', 'match_id': job['match_id'], 'result_path': str(output),
            'elo_applied': receipt['applied']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=Path('output/queue.sqlite3'))
    parser.add_argument('--results', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(run_once(args.db, args.results), ensure_ascii=False, allow_nan=False, indent=2))
    except (ValueError, KeyError, OSError, RuntimeError, sqlite3.Error) as error:
        parser.exit(1, f'WORKER ERROR: {error}\n')


if __name__ == '__main__':
    main()
