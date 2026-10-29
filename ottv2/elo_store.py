"""Apply trusted local match results to an Elo ledger exactly once."""

import argparse
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

from .bot_store import validate_bot_id
from .match_archive import read_match

INITIAL_RATING = 1000.0
K_FACTOR = 32
APPLICATION_ID = 0x4F545432
SCHEMA_VERSION = 1
WIN_REASONS = frozenset({
    'goal', 'missing_kind', 'material_advantage', 'cpu_timeout', 'response_timeout', 'format_error',
    'runtime_error', 'memory_limit', 'startup_timeout', 'startup_format_error',
    'startup_runtime_error', 'startup_memory_limit',
})


def next_ratings(rating_a, rating_b, winner):
    for value in (rating_a, rating_b):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('ratings must be finite numbers')
    if winner not in ('A', 'B', 'draw'):
        raise ValueError('Elo needs A, B or draw as the result')
    # Equivalent to 1/(1+10**((B-A)/400)), without positive exponent overflow.
    ratio = 10 ** (-abs(rating_b - rating_a) / 400)
    expected_a = 1 / (1 + ratio) if rating_a >= rating_b else ratio / (1 + ratio)
    score_a = {'A': 1.0, 'B': 0.0, 'draw': 0.5}[winner]
    delta = K_FACTOR * (score_a - expected_a)
    return rating_a + delta, rating_b - delta


def _database_path(path, label='Elo'):
    path = Path(path).absolute()
    for component in (path, *path.parents):
        if component.is_symlink() or component.is_junction():
            raise ValueError(f'{label} database path must not contain links')
    if path.exists() and not path.is_file():
        raise ValueError(f'{label} database must be a regular file')
    for suffix in ('-journal', '-wal', '-shm'):
        sidecar = Path(str(path) + suffix)
        if sidecar.is_symlink() or sidecar.is_junction():
            raise ValueError(f'{label} database sidecar must not be a link')
    return path


def _connect(path, *, readonly=False):
    target = path.as_uri() + '?mode=ro' if readonly else str(path)
    connection = sqlite3.connect(target, uri=readonly, isolation_level=None, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def _check_schema(connection, *, create=False):
    identity = connection.execute('PRAGMA application_id').fetchone()[0]
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if (identity, version) == (APPLICATION_ID, SCHEMA_VERSION):
        return
    tables = connection.execute("SELECT name FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'").fetchall()
    if not create or identity or version or tables:
        raise ValueError('not an OTTv2 Elo database, or unsupported schema')
    # Each statement remains in the caller's transaction. executescript() would
    # implicitly commit first on supported Python versions, so do not use it.
    connection.execute('''CREATE TABLE ratings (
        bot_id TEXT PRIMARY KEY NOT NULL, rating REAL NOT NULL,
        played INTEGER NOT NULL DEFAULT 0 CHECK(played >= 0),
        wins INTEGER NOT NULL DEFAULT 0 CHECK(wins >= 0),
        draws INTEGER NOT NULL DEFAULT 0 CHECK(draws >= 0),
        losses INTEGER NOT NULL DEFAULT 0 CHECK(losses >= 0),
        CHECK(wins + draws + losses = played))''')
    connection.execute('''CREATE TABLE scored_matches (
        match_id TEXT PRIMARY KEY NOT NULL, record_sha256 TEXT NOT NULL,
        receipt_json TEXT NOT NULL)''')
    connection.execute(f'PRAGMA application_id = {APPLICATION_ID}')
    connection.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')


def _scoring_input(log_path):
    path = Path(log_path)
    if path.suffix != '.json':
        raise ValueError('match log filename must be <match_id>.json')
    _, record = read_match(path.parent, path.stem)
    if record.get('cleanup_complete') is not True:
        raise ValueError('match cleanup must finish before scoring')
    winner, reason = record['result']['winner'], record['result']['reason']
    if not ((winner in ('A', 'B') and reason in WIN_REASONS)
            or (winner == 'draw' and reason in ('threefold_repetition', 'move_limit'))):
        raise ValueError('match has no scoreable tournament result')
    if reason in ('move_limit', 'material_advantage'):
        from .cluster import _replay
        from .rules import supports_limits
        limits = record.get('limits')
        if (not supports_limits(limits) or 'max_valid_moves' not in limits
                or len(record['history']) != limits['max_valid_moves']):
            raise ValueError('move-limit result needs the pinned limit and a complete history')
        _replay({'initial_state': record['initial_state'], 'first': record['initial_state']['turn'],
                 'limits': record['limits']}, record)
    sources = record.get('bot_sources')
    players = {}
    for side in ('A', 'B'):
        source = sources.get(side) if isinstance(sources, dict) else None
        if record['bots'].get(side) != 'stored' or not isinstance(source, dict):
            raise ValueError('both sides need pinned stored bot identities')
        bot_id, version = source.get('bot_id'), source.get('version')
        validate_bot_id(bot_id)
        if (not isinstance(version, str) or not re.fullmatch(r'[0-9a-f]{64}', version)
                or source.get('sha256') != version or source.get('entrypoint') != 'main.py'):
            raise ValueError('invalid pinned bot version')
        players[side] = {'bot_id': bot_id, 'version': version}
    if players['A']['bot_id'] == players['B']['bot_id']:
        raise ValueError('a student cannot play against their own bot')
    try:
        canonical = json.dumps(record, sort_keys=True, ensure_ascii=False,
                               allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (ValueError, RecursionError) as error:
        raise ValueError('invalid result JSON values') from error
    return record, players, hashlib.sha256(canonical).hexdigest()


def apply_result(database, log_path):
    # Reject unscoreable input before creating even an empty database.
    return _apply_scored_input(database, *_scoring_input(log_path))


def _apply_scored_input(database, record, players, fingerprint):
    """Apply a single validated snapshot without reopening a mutable log path."""
    path = _database_path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(path)) as connection:
        try:
            # Serialize the duplicate check and both rating changes together.
            connection.execute('BEGIN IMMEDIATE')
            _check_schema(connection, create=True)
            previous = connection.execute(
                'SELECT record_sha256, receipt_json FROM scored_matches WHERE match_id = ?',
                (record['match_id'],)).fetchone()
            if previous is not None:
                if previous['record_sha256'] != fingerprint:
                    raise ValueError('match ID already scored with different data')
                receipt = json.loads(previous['receipt_json'])
                connection.execute('COMMIT')
                return {**receipt, 'applied': False}
            for player in players.values():
                connection.execute('INSERT OR IGNORE INTO ratings(bot_id, rating) VALUES (?, ?)',
                                   (player['bot_id'], INITIAL_RATING))
                player['before'] = connection.execute('SELECT rating FROM ratings WHERE bot_id = ?',
                                                      (player['bot_id'],)).fetchone()['rating']
            winner = record['result']['winner']
            after = next_ratings(players['A']['before'], players['B']['before'], winner)
            for side, rating in zip(('A', 'B'), after):
                player = players[side]
                player.update(after=rating, delta=rating - player['before'])
                won, drawn = int(winner == side), int(winner == 'draw')
                connection.execute('''UPDATE ratings SET rating = ?, played = played + 1,
                    wins = wins + ?, draws = draws + ?, losses = losses + ? WHERE bot_id = ?''',
                                   (rating, won, drawn, 1 - won - drawn, player['bot_id']))
            receipt = {'match_id': record['match_id'], 'record_sha256': fingerprint,
                       'winner': winner, 'reason': record['result']['reason'],
                       'k_factor': K_FACTOR, 'players': players}
            connection.execute('INSERT INTO scored_matches VALUES (?, ?, ?)',
                               (record['match_id'], fingerprint, json.dumps(receipt, allow_nan=False)))
            connection.execute('COMMIT')
            return {**receipt, 'applied': True}
        except BaseException:
            if connection.in_transaction:
                connection.execute('ROLLBACK')
            raise


def standings(database, limit=20):
    if type(limit) is not int or not 1 <= limit <= 4000:
        raise ValueError('standings limit must be 1..4000')
    path = _database_path(database)
    if not path.exists():
        return []
    with closing(_connect(path, readonly=True)) as connection:
        _check_schema(connection)
        rows = connection.execute('''SELECT bot_id, rating, played, wins, draws, losses
            FROM ratings ORDER BY rating DESC, bot_id ASC LIMIT ?''', (limit,)).fetchall()
        return [dict(row) for row in rows]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=Path('output/ratings.sqlite3'))
    commands = parser.add_subparsers(dest='command', required=True)
    apply = commands.add_parser('apply')
    apply.add_argument('log', type=Path)
    show = commands.add_parser('show')
    show.add_argument('--limit', type=int, default=20)
    args = parser.parse_args()
    try:
        result = apply_result(args.db, args.log) if args.command == 'apply' else standings(args.db, args.limit)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f'ELO ERROR: {error}\n')


if __name__ == '__main__':
    main()
