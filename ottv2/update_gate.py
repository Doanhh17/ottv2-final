"""Persistent three-minute acceptance gate for local GitHub update requests."""

import argparse
from contextlib import contextmanager, nullcontext
import json
import math
import os
import time

from .bot_store import bot_directory, read_regular, replace_current

COOLDOWN_SECONDS = 3 * 60


def request_state(directory, bot_id):
    path = directory / 'update-request.json'
    if not path.exists() and not path.is_symlink():
        return None
    try:
        obj = json.loads(read_regular(path, 4096).decode('utf-8'))
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError('invalid update-request state; refusing to reset cooldown') from error
    if (not isinstance(obj, dict) or type(obj.get('schema_version')) is not int
            or obj['schema_version'] != 1 or obj.get('bot_id') != bot_id):
        raise ValueError('invalid update-request metadata')
    for key in ('accepted_at', 'next_allowed_at'):
        value = obj.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError('invalid update-request timestamp')
    if obj['next_allowed_at'] not in (obj['accepted_at'] + COOLDOWN_SECONDS, obj['accepted_at'] + 300, obj['accepted_at'] + 1800):
        raise ValueError('invalid update-request cooldown interval')
    # Honor the new policy for old 30-minute records without resetting acceptance.
    return {**obj, 'next_allowed_at': obj['accepted_at'] + COOLDOWN_SECONDS}


def remaining_seconds(state, now):
    if not math.isfinite(now) or now < 0:
        raise RuntimeError('invalid host clock')
    if state is None:
        return 0
    if now < state['accepted_at']:
        raise RuntimeError('host clock moved backwards; refusing to reset cooldown')
    return max(0, math.ceil(state['next_allowed_at'] - now))


def update_status(root, bot_id):
    directory = bot_directory(root, bot_id)
    state = request_state(directory, bot_id)
    return {'bot_id': bot_id, 'remaining_seconds': remaining_seconds(state, time.time()),
            'busy': ((directory / '.request.lock').exists() or (directory / '.request.lock').is_symlink()),
            'last_request': state}


@contextmanager
def accepted_update(root, bot_id, *, admission=None):
    directory = bot_directory(root, bot_id, create=True)
    lock = directory / '.request.lock'
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise ValueError('a GitHub update request is already running for this bot') from error
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(str(os.getpid()))
        with admission if admission is not None else nullcontext():
            state = request_state(directory, bot_id)
            now = time.time()
            remaining = remaining_seconds(state, now)
            if remaining:
                raise ValueError(f'update cooldown: retry in {remaining} seconds')
            state = {'schema_version': 1, 'bot_id': bot_id, 'accepted_at': now,
                     'next_allowed_at': now + COOLDOWN_SECONDS}
            # Queue admission is committed before network or sandbox work.
            replace_current(directory / 'update-request.json', state)
        yield state
    finally:
        lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', required=True)
    parser.add_argument('--bot-id', required=True)
    args = parser.parse_args()
    try:
        status = update_status(args.store, args.bot_id)
        print(f"UPDATE STATUS: {args.bot_id} | busy={status['busy']} | wait_seconds={status['remaining_seconds']}")
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f'UPDATE STATUS ERROR: {error}\n')


if __name__ == '__main__':
    main()
