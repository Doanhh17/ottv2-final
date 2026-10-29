"""Bounded, read-only access to locally saved match logs."""

from itertools import islice
import json
import os
from pathlib import Path
import re
import stat


MAX_LOG_BYTES = 2 * 1024 * 1024  # Same ceiling as the offline replay reader.
MAX_DIRECTORY_ENTRIES = 1000
LIST_LIMIT = 10
MAX_LOGS_INSPECTED = 50
MATCH_ID = re.compile(r'[0-9a-f]{32}\Z')


def validate_match_id(match_id):
    if not isinstance(match_id, str) or not MATCH_ID.fullmatch(match_id):
        raise ValueError('invalid match ID')
    return match_id


def archive_directory(path):
    path = Path(path)
    if path.is_symlink() or path.is_junction() or (path.exists() and not path.is_dir()):
        raise ValueError('invalid match directory')
    return path


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _constant(value):
    raise ValueError('non-finite JSON number')


def read_match(directory, match_id):
    validate_match_id(match_id)
    directory = archive_directory(directory)
    path = directory / (match_id + '.json')
    if path.is_symlink() or path.is_junction():
        raise ValueError('linked match log')
    # NOFOLLOW also protects the final component from a replacement on Linux;
    # NONBLOCK prevents a replaced FIFO from waiting for a writer.
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    flags |= getattr(os, 'O_NONBLOCK', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_LOG_BYTES:
            raise ValueError('invalid match file or size')
        data = stream.read(MAX_LOG_BYTES + 1)
    if len(data) > MAX_LOG_BYTES:
        raise ValueError('match log too large')
    try:
        record = json.loads(data.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('invalid match JSON') from error
    # This is archive metadata validation, not a second game adjudicator.
    if not isinstance(record, dict) or type(record.get('schema_version')) is not int:
        raise ValueError('invalid match schema')
    result, bots, history = record.get('result'), record.get('bots'), record.get('history')
    if (record['schema_version'] != 1 or record.get('match_id') != match_id
            or not isinstance(record.get('created_at_utc'), str)
            or not isinstance(record.get('initial_state'), dict)
            or not isinstance(record.get('final_state'), dict)
            or not isinstance(bots, dict)
            or any(not isinstance(bots.get(side), str) for side in ('A', 'B'))
            or not isinstance(history, list) or len(history) > 1000
            or not isinstance(result, dict) or result.get('winner') not in ('A', 'B', 'draw', None)
            or not isinstance(result.get('reason'), str)
            or type(result.get('valid_move_count')) is not int
            or result['valid_move_count'] != len(history)):
        raise ValueError('invalid match metadata')
    return data, record


def list_matches(directory):
    directory = archive_directory(directory)
    if not directory.exists():
        return {'matches': [], 'skipped_count': 0, 'limited': False}
    with os.scandir(directory) as entries:
        bounded = list(islice(entries, MAX_DIRECTORY_ENTRIES + 1))
    if len(bounded) > MAX_DIRECTORY_ENTRIES:
        raise ValueError('match directory exceeds local scan limit')
    candidates = []
    for entry in bounded:
        if entry.name.endswith('.json') and MATCH_ID.fullmatch(entry.name[:-5]):
            if entry.is_file(follow_symlinks=False):
                candidates.append((entry.stat(follow_symlinks=False).st_mtime_ns, entry.name[:-5]))
    candidates.sort(reverse=True)
    matches, skipped, inspected = [], 0, 0
    for _, match_id in candidates[:MAX_LOGS_INSPECTED]:
        inspected += 1
        try:
            _, record = read_match(directory, match_id)
        except (ValueError, OSError):
            skipped += 1
            continue
        # Bound the list payload even if a stored label/reason is very long.
        matches.append({'match_id': match_id, 'created_at_utc': record['created_at_utc'][:128],
                        'bots': {side: record['bots'][side][:128] for side in ('A', 'B')},
                        'result': {'winner': record['result']['winner'],
                                   'reason': record['result']['reason'][:128],
                                   'valid_move_count': record['result']['valid_move_count']},
                        'log_url': '/matches/' + match_id,
                        'download_url': '/matches/' + match_id})
        if len(matches) == LIST_LIMIT:
            break
    return {'matches': matches, 'skipped_count': skipped, 'limited': len(candidates) > inspected}
