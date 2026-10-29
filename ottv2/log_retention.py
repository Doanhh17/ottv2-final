"""Keep replay files seven days, preserving queue receipts, Elo and quotas."""

import argparse
from contextlib import closing
from datetime import datetime
import json
import math
from pathlib import Path
import sqlite3
import stat
import time

from . import elo_store, match_archive, match_queue

KEEP_SECONDS = 7 * 24 * 3600
BATCH_SIZE = 100
CHECK_INTERVAL = 60


class LogExpired(FileNotFoundError):
    pass


def _schema(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS replay_retention (
        match_id TEXT PRIMARY KEY REFERENCES jobs(match_id), completed_at REAL NOT NULL,
        expires_at REAL NOT NULL, result_json TEXT NOT NULL, deleted_at REAL,
        checked_at REAL NOT NULL DEFAULT 0)''')
    connection.execute('CREATE INDEX IF NOT EXISTS replay_expiry ON replay_retention(deleted_at,checked_at)')
    connection.execute('CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state)')


def _exists(connection):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='replay_retention'").fetchone()


def record_completion(connection, match_id, result, *, now=None):
    """Called inside the queue completion transaction, after Elo is durable."""
    _schema(connection)
    now = time.time() if now is None else now
    summary = {key: result[key] for key in ('winner', 'reason', 'valid_move_count')}
    connection.execute('INSERT OR IGNORE INTO replay_retention '
                       '(match_id,completed_at,expires_at,result_json) VALUES (?,?,?,?)',
                       (match_id, now, now + KEEP_SECONDS, json.dumps(summary, allow_nan=False)))


def metadata(connection, match_id):
    if not _exists(connection):
        return None
    row = connection.execute('SELECT * FROM replay_retention WHERE match_id=?', (match_id,)).fetchone()
    return dict(row) if row else None


def _directory(database, directory):
    path = Path(directory).absolute() if directory is not None else Path(database).absolute().parent / 'matches'
    for component in (path, *path.parents):
        if component.is_symlink() or component.is_junction():
            raise ValueError('replay directory must not contain links')
    return match_archive.archive_directory(path)


def _path(directory, row):
    match_archive.validate_match_id(row['match_id'])
    target = directory / (row['match_id'] + '.json')
    # Never follow a path from the database outside the configured archive.
    if not row['result_path'] or Path(row['result_path']).absolute() != target:
        raise ValueError('result path does not match archive')
    if target.is_symlink() or target.is_junction():
        raise ValueError('linked replay file')
    if target.exists() and not stat.S_ISREG(target.stat().st_mode):
        raise ValueError('replay is not a regular file')
    return target


def _legacy(directory, row):
    """Old queues lack a finish time: use the later job/file time conservatively."""
    payload, receipt = json.loads(row['payload']), json.loads(row['receipt_json'])
    moment = datetime.fromisoformat(payload['created_at_utc'])
    if moment.tzinfo is None:
        raise ValueError('job time has no timezone')
    completed = moment.timestamp()
    summary = {'winner': receipt['winner'], 'reason': receipt['reason'], 'valid_move_count': None}
    try:
        target = _path(directory, row)
        if target.exists():
            completed = max(completed, target.stat().st_mtime)
            _, record = match_archive.read_match(directory, row['match_id'])
            summary['valid_move_count'] = record['result']['valid_move_count']
    except (OSError, ValueError):
        # Keep unreadable legacy files at least seven more days. Persisting the
        # visit also lets the next bounded pass reach other historical jobs.
        completed = max(completed, time.time())
    return {'completed_at': completed, 'expires_at': completed + KEEP_SECONDS,
            'result_json': json.dumps(summary, allow_nan=False)}


def prune(database, directory=None, *, apply=False, now=None):
    """Bounded pass. The caller must hold the global Worker lock when applying.

    Preview performs no database or file writes. Only completed jobs with a
    durable receipt can be eligible; pending/running recovery files are excluded.
    """
    now = time.time() if now is None else now
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
        raise ValueError('invalid cleanup time')
    database = elo_store._database_path(database, 'Queue')
    directory = _directory(database, directory)
    with closing(elo_store._connect(database, readonly=True)) as connection:
        match_queue._schema(connection)
        if _exists(connection):
            rows = connection.execute('''SELECT jobs.* FROM jobs LEFT JOIN replay_retention r USING(match_id)
                WHERE jobs.state='completed' AND jobs.receipt_json IS NOT NULL
                  AND (r.match_id IS NULL OR (r.deleted_at IS NULL AND r.expires_at<=?))
                ORDER BY coalesce(r.checked_at,0),jobs.rowid LIMIT ?''', (now, BATCH_SIZE)).fetchall()
            existing = {row['match_id']: metadata(connection, row['match_id']) for row in rows}
        else:
            rows = connection.execute("SELECT * FROM jobs WHERE state='completed' AND receipt_json IS NOT NULL "
                                      'ORDER BY rowid LIMIT ?', (BATCH_SIZE,)).fetchall()
            existing = {}
    answer = {'state': 'logs_pruned' if apply else 'log_cleanup_preview',
              'checked': len(rows), 'eligible': 0, 'deleted': 0, 'skipped': 0}
    for row in rows:
        try:
            info = existing.get(row['match_id']) or _legacy(directory, row)
            if apply:
                with match_queue._write(database) as connection:
                    _schema(connection)
                    connection.execute('INSERT OR IGNORE INTO replay_retention '
                                       '(match_id,completed_at,expires_at,result_json) VALUES (?,?,?,?)',
                                       (row['match_id'], info['completed_at'], info['expires_at'], info['result_json']))
                    connection.execute('UPDATE replay_retention SET checked_at=? WHERE match_id=?', (now, row['match_id']))
            target = _path(directory, row)
            if now < info['expires_at']:
                continue
            answer['eligible'] += 1
            if target.exists():
                record, _, fingerprint = elo_store._scoring_input(target)
                receipt = json.loads(row['receipt_json'])
                if record['match_id'] != row['match_id'] or fingerprint != receipt['record_sha256']:
                    raise ValueError('replay does not match scored receipt')
            if apply:
                # Recheck the parent and final component immediately before unlink.
                _directory(database, directory)
                _path(directory, row).unlink(missing_ok=True)
                with match_queue._write(database) as connection:
                    connection.execute('UPDATE replay_retention SET deleted_at=? WHERE match_id=?', (now, row['match_id']))
                answer['deleted'] += 1
        except (ValueError, OSError, KeyError, TypeError, OverflowError):
            # Keep suspicious/corrupt files for the operator; never guess a target.
            answer['skipped'] += 1
    return answer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=Path('output/local-arena/queue.sqlite3'))
    parser.add_argument('--results', type=Path)
    parser.add_argument('--apply', action='store_true', help='delete expired replay files; otherwise preview only')
    args = parser.parse_args()
    try:
        if args.apply:
            from .local_worker import _worker_lock
            with _worker_lock():
                answer = prune(args.db, args.results, apply=True)
        else:
            answer = prune(args.db, args.results)
        print(json.dumps(answer, ensure_ascii=False))
    except (ValueError, OSError, RuntimeError, sqlite3.Error):
        parser.exit(1, 'Chưa dọn được bản ghi. Kiểm tra thư mục và dừng Worker trước khi chạy --apply.\n')


if __name__ == '__main__':
    main()
