"""Local operator controls: pause new matches and replace the map safely."""

import argparse
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3

from . import elo_store, match_queue
from .bot_store import validate_bot_id
from .map_config import load_map
from .protocol import state_message


def status(database):
    path = elo_store._database_path(database, 'Queue')
    if not path.exists():
        raise ValueError('initialize the queue first')
    with closing(elo_store._connect(path, readonly=True)) as connection:
        connection.execute('BEGIN')
        match_queue._schema(connection)
        config = match_queue._config(connection)
        return {'control': match_queue._controls(config), 'map': config['map'],
                'waiting': connection.execute('SELECT COUNT(*) FROM waiting').fetchone()[0],
                'busy_players': connection.execute('SELECT COUNT(*) FROM busy').fetchone()[0],
                'jobs': dict(connection.execute('SELECT state,COUNT(*) FROM jobs GROUP BY state'))}


def set_paused(database, paused, reason=''):
    if type(paused) is not bool or not isinstance(reason, str) or len(reason) > 200:
        raise ValueError('pause needs a boolean and a reason of at most 200 characters')
    with match_queue._write(database) as connection:
        config = match_queue._config(connection)
        controls = match_queue._controls(config)
        updated = {**controls, 'paused': paused, 'pause_reason': reason if paused else ''}
        config['control'] = updated
        connection.execute('UPDATE config SET data=? WHERE id=1', (json.dumps(config),))
        return {'changed': controls != updated, 'control': updated}


def change_map(database, map_file, practice_bots=None):
    with match_queue._write(database) as connection:
        config = match_queue._config(connection)
        if not match_queue._controls(config)['paused']:
            raise ValueError('pause new matches before changing the map')
        game, metadata = load_map(map_file)
        if practice_bots is not None:
            from .practice_config import validate_bots
            practice_bots=validate_bots(practice_bots)
        map_changed=config['map']!=metadata
        bots_changed=practice_bots is not None and config.get('practice_bots')!=practice_bots
        if not map_changed and not bots_changed:
            return {'changed': False, 'map': metadata, 'cancelled_waiting': 0}
        cancelled = connection.execute('SELECT COUNT(*) FROM waiting').fetchone()[0] if map_changed else 0
        config.update(map=metadata, initial_state=state_message(game))
        if practice_bots is not None:config['practice_bots']=practice_bots
        connection.execute('UPDATE config SET data=? WHERE id=1', (json.dumps(config),))
        if map_changed:connection.execute('DELETE FROM waiting')
        return {'changed': True, 'map': metadata, 'cancelled_waiting': cancelled,
                'paused': True}


def set_updates_locked(database, locked):
    if type(locked) is not bool:
        raise ValueError('update lock must be a boolean')
    with match_queue._write(database) as connection:
        config = match_queue._config(connection)
        controls = match_queue._controls(config)
        changed = controls['updates_locked'] != locked
        config['control'] = {**controls, 'updates_locked': locked}
        connection.execute('UPDATE config SET data=? WHERE id=1', (json.dumps(config),))
        return {'changed': changed, 'control': config['control']}


def block_bot(database, identity, blocked=True, reason=''):
    validate_bot_id(identity)
    if type(blocked) is not bool or not isinstance(reason, str) or len(reason) > 200:
        raise ValueError('block needs a boolean and a reason of at most 200 characters')
    with match_queue._write(database) as connection:
        config = match_queue._config(connection)
        controls = match_queue._controls(config)
        previous = dict(controls['blocked_bots'])
        if not blocked and identity in controls.get('locked_accounts', {}):
            raise ValueError('Tài khoản đang bị khóa. Dùng nút Mở khóa tài khoản trong trang quản trị.')
        if blocked:
            controls['blocked_bots'][identity] = reason
            cancelled = connection.execute('DELETE FROM waiting WHERE bot_id=?', (identity,)).rowcount
        else:
            controls['blocked_bots'].pop(identity, None)
            cancelled = 0
        config['control'] = controls
        connection.execute('UPDATE config SET data=? WHERE id=1', (json.dumps(config),))
        return {'bot_id': identity, 'blocked': blocked, 'changed': previous != controls['blocked_bots'],
                'cancelled_waiting': cancelled}


@contextmanager
def update_admission(database, store, identity):
    """Serialize acceptance with operator controls, never hold it for network/READY."""
    validate_bot_id(identity)
    with match_queue._write(database) as connection:
        config = match_queue._config(connection)
        controls = match_queue._controls(config)
        if Path(store).resolve() != Path(config['store']).resolve():
            raise ValueError('update store does not match this queue')
        if controls['updates_locked']:
            raise ValueError('bot updates are locked by the operator')
        if identity in controls['blocked_bots']:
            raise ValueError('bot is blocked by the operator')
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=Path('output/queue.sqlite3'))
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status')
    pause = commands.add_parser('pause')
    pause.add_argument('--reason', default='')
    commands.add_parser('resume')
    change = commands.add_parser('map')
    change.add_argument('path', type=Path)
    commands.add_parser('lock-updates')
    commands.add_parser('unlock-updates')
    for name in ('block', 'unblock'):
        command = commands.add_parser(name)
        command.add_argument('identity')
        if name == 'block':
            command.add_argument('--reason', default='')
    args = parser.parse_args()
    try:
        if args.command == 'status':
            answer = status(args.db)
        elif args.command == 'map':
            answer = change_map(args.db, args.path)
        elif args.command in ('lock-updates', 'unlock-updates'):
            answer = set_updates_locked(args.db, args.command == 'lock-updates')
        elif args.command in ('block', 'unblock'):
            answer = block_bot(args.db, args.identity, args.command == 'block', getattr(args, 'reason', ''))
        else:
            answer = set_paused(args.db, args.command == 'pause', getattr(args, 'reason', ''))
        print(json.dumps(answer, ensure_ascii=False, allow_nan=False, indent=2))
    except (ValueError, KeyError, OSError, sqlite3.Error) as error:
        parser.exit(1, f'QUEUE ADMIN ERROR: {error}\n')


if __name__ == '__main__':
    main()
