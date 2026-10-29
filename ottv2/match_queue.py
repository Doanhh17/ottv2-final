"""Local active/passive pairing with pinned jobs and recoverable Worker states."""

import argparse
from contextlib import closing, contextmanager
from datetime import datetime
import json
import math
from pathlib import Path
import random
import secrets
import sqlite3
from uuid import uuid4

from .bot_store import load_bot, validate_bot_id
from . import daily_quota, elo_store
from .rules import CURRENT_LIMITS
from .map_config import load_map
from .match_archive import validate_match_id
from .protocol import state_message

APPLICATION_ID = 0x4F545451


def _jobs_table(name):
    return f'''CREATE TABLE {name} (
        match_id TEXT PRIMARY KEY NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','running','completed','failed','cancelled')),
        payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
        result_path TEXT, last_error TEXT, receipt_json TEXT)'''


def _schema(connection, create=False, upgrade=False):
    identity = connection.execute('PRAGMA application_id').fetchone()[0]
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if identity == APPLICATION_ID and version in (1, 2, 3):
        if version == 1 and upgrade:
            # Rebuild child and parent together, with foreign keys still enabled.
            connection.execute(_jobs_table('jobs_v2'))
            connection.execute('INSERT INTO jobs_v2(match_id,state,payload) SELECT match_id,state,payload FROM jobs')
            connection.execute('CREATE TABLE busy_v2 (bot_id TEXT PRIMARY KEY NOT NULL, match_id TEXT NOT NULL REFERENCES jobs_v2(match_id))')
            connection.execute('INSERT INTO busy_v2 SELECT * FROM busy')
            connection.execute('DROP TABLE busy')
            connection.execute('DROP TABLE jobs')
            connection.execute('ALTER TABLE jobs_v2 RENAME TO jobs')
            connection.execute('ALTER TABLE busy_v2 RENAME TO busy')
            connection.execute('PRAGMA user_version = 2')
            version = 2
        if version == 2 and upgrade:
            daily_quota.create_schema(connection)
            connection.execute('PRAGMA user_version = 3')
        return
    objects = connection.execute("SELECT name FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'").fetchall()
    if not create or identity or version or objects:
        raise ValueError('not an OTTv2 queue database, or unsupported schema')
    for statement in (
        'CREATE TABLE config (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)',
        'CREATE TABLE waiting (sequence INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT NOT NULL UNIQUE, joined_at TEXT NOT NULL)',
        _jobs_table('jobs'),
        'CREATE TABLE busy (bot_id TEXT PRIMARY KEY NOT NULL, match_id TEXT NOT NULL REFERENCES jobs(match_id))',
    ):
        connection.execute(statement)
    connection.execute(f'PRAGMA application_id = {APPLICATION_ID}')
    daily_quota.create_schema(connection)
    connection.execute('PRAGMA user_version = 3')


@contextmanager
def _write(database, create=False):
    path = elo_store._database_path(database, 'Queue')
    if not create and not path.exists():
        raise ValueError('initialize the queue first')
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    with closing(elo_store._connect(path)) as connection:
        connection.execute('PRAGMA foreign_keys = ON')
        try:
            connection.execute('BEGIN IMMEDIATE')
            _schema(connection, create, upgrade=True)
            yield connection
            connection.execute('COMMIT')
        except BaseException:
            if connection.in_transaction:
                connection.execute('ROLLBACK')
            raise


def initialize(database, store, map_file, ratings=None):
    store = Path(store).absolute()
    if store.is_symlink() or store.is_junction() or not store.is_dir():
        raise ValueError('bot store must be an existing directory, not a link')
    game, metadata = load_map(map_file)
    rating_path = str(elo_store._database_path(ratings)) if ratings is not None else None
    if rating_path == str(elo_store._database_path(database, 'Queue')):
        raise ValueError('queue and Elo must use separate databases')
    config = {'store': str(store.resolve()), 'ratings': rating_path,
              'map': metadata, 'initial_state': state_message(game)}
    with _write(database, create=True) as connection:
        old = connection.execute('SELECT data FROM config WHERE id=1').fetchone()
        if old is not None:
            old_config = json.loads(old['data'])
            _controls(old_config)
            old_config.pop('control', None)
            old_config.pop('practice_bots', None)
            if old_config != config:
                raise ValueError('queue configuration differs; refusing to reset waiting requests or jobs')
            return {'initialized': False}
        connection.execute('INSERT INTO config VALUES (1, ?)', (json.dumps(config),))
    return {'initialized': True}


def _config(connection):
    row = connection.execute('SELECT data FROM config WHERE id=1').fetchone()
    if row is None:
        raise ValueError('initialize the queue first')
    return json.loads(row['data'])


def _controls(config):
    controls = config.get('control', {})
    if (not isinstance(controls, dict) or type(controls.get('paused', False)) is not bool
            or not isinstance(controls.get('pause_reason', ''), str)
            or len(controls.get('pause_reason', '')) > 200
            or type(controls.get('updates_locked', False)) is not bool
            or not isinstance(controls.get('blocked_bots', {}), dict)):
        raise ValueError('invalid queue control state')
    locks = controls.get('locked_accounts', {})
    if not isinstance(locks, dict):
        raise ValueError('invalid account lock state')
    for identity, reason in {**controls.get('blocked_bots', {}), **locks}.items():
        validate_bot_id(identity)
        if not isinstance(reason, str) or len(reason) > 200:
            raise ValueError('invalid blocked bot reason')
    result = {'paused': False, 'pause_reason': '', 'updates_locked': False, 'blocked_bots': {}, **controls}
    result['blocked_bots'] = {**result['blocked_bots'], **locks}
    return result


def _status(connection, bot_id):
    row = connection.execute('SELECT match_id FROM busy WHERE bot_id=?', (bot_id,)).fetchone()
    if row is not None:
        return {'bot_id': bot_id, 'state': 'matched', 'match_id': row['match_id']}
    row = connection.execute('SELECT joined_at FROM waiting WHERE bot_id=?', (bot_id,)).fetchone()
    if row is not None:
        return {'bot_id': bot_id, 'state': 'waiting', 'joined_at_utc': row['joined_at']}
    return {'bot_id': bot_id, 'state': 'idle'}


def _ratings(config):
    if config['ratings'] is None:
        return {}
    path = elo_store._database_path(config['ratings'])
    if not path.exists():
        return {}
    with closing(elo_store._connect(path, readonly=True)) as connection:
        elo_store._check_schema(connection)
        ratings = dict(connection.execute('SELECT bot_id, rating FROM ratings').fetchall())
        if any(not math.isfinite(rating) for rating in ratings.values()):
            raise ValueError('invalid Elo rating')
        return ratings


def _previous_opponents(connection, bot_id, day):
    # Meeting history only breaks ties inside an Elo band for active pairs.
    return {row['bot_id'] for row in connection.execute('''
        SELECT other.bot_id FROM quota_entries own
        JOIN quota_entries other ON other.match_id=own.match_id AND other.bot_id<>own.bot_id
        WHERE own.bot_id=? AND own.day=? AND own.state IN ('reserved','used')
          AND other.state IN ('reserved','used')''', (bot_id, day))}


def _previous_passive_opponents(connection, bot_id, day):
    # Directed invitations: A(active)->B(passive) does not block B->A.
    # Active-active games do not consume either invitation direction.
    return {row['bot_id'] for row in connection.execute('''
        SELECT other.bot_id FROM quota_entries own
        JOIN quota_entries other ON other.match_id=own.match_id AND other.bot_id<>own.bot_id
        WHERE own.bot_id=? AND own.day=? AND own.mode='active' AND other.mode='passive'
          AND own.state IN ('reserved','used') AND other.state IN ('reserved','used')''', (bot_id, day))}


def _candidate_metadata(store, identity, cache):
    if identity not in cache:
        try:
            cache[identity] = load_bot(store, identity)[1]
        except (ValueError, FileNotFoundError):
            cache[identity] = None
    return cache[identity]


def _choose_opponent(store, candidates, ratings, own_rating, previous, cache):
    # Elo proximity comes first; prefer a fresh opponent inside the same band.
    checked = set()
    for band in (100, 200, 400, None):
        for repeated in (False, True):
            choices = [identity for identity in candidates if identity not in checked
                       and (identity in previous) == repeated
                       and (band is None or abs(ratings.get(identity, elo_store.INITIAL_RATING) - own_rating) <= band)]
            secrets.SystemRandom().shuffle(choices)
            for identity in choices:
                checked.add(identity)
                metadata = _candidate_metadata(store, identity, cache)
                if metadata is not None:
                    return identity, metadata
    return None


def _waiting(connection, day, blocked=()):
    return [row['bot_id'] for row in connection.execute('''
        SELECT waiting.bot_id FROM waiting LEFT JOIN busy USING(bot_id)
        WHERE busy.bot_id IS NULL ORDER BY sequence''')
            if row['bot_id'] not in blocked and daily_quota.usage(connection, row['bot_id'], day, 'active')['remaining'] > 0]


def _create_job(connection, config, ratings, now, requester, own, selected, mode):
    other, opponent = selected
    pairs = [(other, opponent, mode), (requester, own, 'active')]
    if secrets.randbelow(2):
        pairs.reverse()
    seed = secrets.randbits(31)
    first = random.Random(seed).choice(('A', 'B'))
    initial_state = dict(config['initial_state'], turn=first, you=first)
    job = {'schema_version': 1, 'match_id': uuid4().hex, 'created_at_utc': now,
           'bot_store': config['store'], 'seed': seed, 'first': first,
           'quota_date': daily_quota.vietnam_day(datetime.fromisoformat(now)),
           'map': config['map'], 'initial_state': initial_state,
           'limits': dict(CURRENT_LIMITS),
           'players': {side: {'bot_id': identity, 'version': metadata['version'],
                              'mode': role, 'rating_at_pairing': ratings.get(identity, elo_store.INITIAL_RATING)}
                       for side, (identity, metadata, role) in zip(('A', 'B'), pairs)}}
    for side, (_, metadata, _) in zip(('A', 'B'), pairs):
        if 'origin' in metadata:
            job['players'][side]['origin'] = metadata['origin']
    connection.execute('INSERT INTO jobs(match_id,state,payload) VALUES (?, ?, ?)', (job['match_id'], 'pending', json.dumps(job)))
    daily_quota.reserve(connection, job)
    for identity, _, _ in pairs:
        connection.execute('DELETE FROM waiting WHERE bot_id=?', (identity,))
        connection.execute('INSERT INTO busy VALUES (?, ?)', (identity, job['match_id']))
    return {**_status(connection, requester), 'created': True}


def find_match(database, bot_id):
    validate_bot_id(bot_id)
    with _write(database) as connection:
        config = _config(connection)
        current = _status(connection, bot_id)
        if current['state'] == 'matched':
            return {**current, 'created': False}
        controls = _controls(config)
        if bot_id in controls['blocked_bots']:
            raise ValueError('bot is blocked by the operator')
        if controls['paused']:
            raise ValueError('new matches are paused by the operator')
        now = daily_quota.utc_now().isoformat()
        day = daily_quota.vietnam_day(datetime.fromisoformat(now))
        daily_quota.require_available(connection, bot_id, day, 'active')
        _, own = load_bot(config['store'], bot_id)
        ratings = _ratings(config)
        own_rating = ratings.get(bot_id, elo_store.INITIAL_RATING)
        waiting = [other for other in _waiting(connection, day, controls['blocked_bots']) if other != bot_id]
        selected = _choose_opponent(config['store'], waiting, ratings, own_rating,
                                    _previous_opponents(connection, bot_id, day), {})
        if selected is None:
            if current['state'] == 'waiting':
                return {**current, 'created': False}
            connection.execute('INSERT INTO waiting(bot_id, joined_at) VALUES (?, ?)', (bot_id, now))
            return {**_status(connection, bot_id), 'created': True}
        return _create_job(connection, config, ratings, now, bot_id, own, selected, 'active')


def dispatch(database):
    """Pair at most one waiting request; active-active precedes passive fallback."""
    with _write(database) as connection:
        config = _config(connection)
        controls = _controls(config)
        if controls['paused']:
            return {'state': 'paused'}
        now = daily_quota.utc_now().isoformat()
        day = daily_quota.vietnam_day(datetime.fromisoformat(now))
        waiting = _waiting(connection, day, controls['blocked_bots'])
        if not waiting:
            return {'state': 'idle'}
        ratings, cache = _ratings(config), {}
        previous = {identity: _previous_opponents(connection, identity, day) for identity in waiting}
        for identity in waiting:
            if _candidate_metadata(config['store'], identity, cache) is None:
                continue
            selected = _choose_opponent(config['store'], [other for other in waiting if other != identity],
                                        ratings, ratings.get(identity, elo_store.INITIAL_RATING),
                                        previous[identity], cache)
            if selected is not None:
                return _create_job(connection, config, ratings, now, identity, cache[identity], selected, 'active')
        # A waiting request must never be silently treated as passive, including
        # an exhausted or currently invalid requester. Busy bots are also excluded.
        excluded = {row['bot_id'] for row in connection.execute('SELECT bot_id FROM waiting UNION SELECT bot_id FROM busy')}
        excluded.update(controls['blocked_bots'])
        passive = []
        for path in Path(config['store']).iterdir():
            if path.name in excluded or path.is_symlink() or path.is_junction() or not path.is_dir():
                continue
            try:
                validate_bot_id(path.name)
            except ValueError:
                continue
            if daily_quota.usage(connection, path.name, day, 'passive')['remaining'] > 0:
                passive.append(path.name)
        for identity in waiting:
            if cache[identity] is None:
                continue
            invited = _previous_passive_opponents(connection, identity, day)
            selected = _choose_opponent(config['store'], [other for other in passive if other not in invited],
                                        ratings, ratings.get(identity, elo_store.INITIAL_RATING), set(), cache)
            if selected is not None:
                return _create_job(connection, config, ratings, now, identity, cache[identity], selected, 'passive')
        return {'state': 'waiting', 'waiting_requests': len(waiting)}


def cancel_request(database, bot_id):
    validate_bot_id(bot_id)
    with _write(database) as connection:
        if _status(connection, bot_id)['state'] == 'matched':
            raise ValueError('a created match cannot be cancelled as a waiting request')
        changed = connection.execute('DELETE FROM waiting WHERE bot_id=?', (bot_id,)).rowcount
        return {**_status(connection, bot_id), 'cancelled': bool(changed)}


def status(database, bot_id):
    validate_bot_id(bot_id)
    path = elo_store._database_path(database, 'Queue')
    if not path.exists():
        return {'bot_id': bot_id, 'state': 'idle'}
    with closing(elo_store._connect(path, readonly=True)) as connection:
        _schema(connection)
        return _status(connection, bot_id)


def get_job(database, match_id):
    validate_match_id(match_id)
    path = elo_store._database_path(database, 'Queue')
    with closing(elo_store._connect(path, readonly=True)) as connection:
        _schema(connection)
        row = connection.execute('SELECT * FROM jobs WHERE match_id=?', (match_id,)).fetchone()
        if row is None:
            raise ValueError('queue job not found')
        extra = dict(row)
        receipt = extra.get('receipt_json')
        payload = json.loads(row['payload'])
        return {**payload, 'quota_date': payload.get('quota_date', daily_quota.vietnam_day(datetime.fromisoformat(payload['created_at_utc']))),
                'state': row['state'],
                'attempts': extra.get('attempts', 0), 'result_path': extra.get('result_path'),
                'last_error': extra.get('last_error'),
                'elo_receipt': json.loads(receipt) if receipt else None}


def cancel_job(database, match_id):
    """Operator-only cancellation of an unstarted job; no Elo changes."""
    validate_match_id(match_id)
    with _write(database) as connection:
        row = connection.execute('SELECT state,payload FROM jobs WHERE match_id=?', (match_id,)).fetchone()
        if row is None:
            raise ValueError('queue job not found')
        if row['state'] not in ('pending', 'cancelled'):
            raise ValueError('only an unstarted pending job can be cancelled')
        changed = row['state'] == 'pending'
        if changed:
            daily_quota.settle(connection, json.loads(row['payload']), used=False)
            connection.execute("UPDATE jobs SET state='cancelled' WHERE match_id=?", (match_id,))
            connection.execute('DELETE FROM busy WHERE match_id=?', (match_id,))
        return {'match_id': match_id, 'state': 'cancelled', 'changed': changed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=Path('output/queue.sqlite3'))
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    init.add_argument('--store', type=Path, required=True)
    init.add_argument('--map', type=Path, required=True)
    init.add_argument('--ratings', type=Path)
    commands.add_parser('dispatch')
    for name in ('find', 'cancel', 'status', 'job', 'cancel-job', 'quota'):
        command = commands.add_parser(name)
        command.add_argument('identity')
    args = parser.parse_args()
    try:
        if args.command == 'init':
            result = initialize(args.db, args.store, args.map, args.ratings)
        elif args.command == 'dispatch':
            result = dispatch(args.db)
        else:
            function = {'find': find_match, 'cancel': cancel_request, 'status': status,
                        'job': get_job, 'cancel-job': cancel_job, 'quota': daily_quota.status}[args.command]
            result = function(args.db, args.identity)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f'QUEUE ERROR: {error}\n')


if __name__ == '__main__':
    main()
