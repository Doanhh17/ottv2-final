"""Continuously recover queued jobs, pair waiting bots, and run one match at a time."""

import argparse
import json
import math
from pathlib import Path
import sqlite3
import time
from threading import Event

from . import local_worker, log_retention, match_queue


def tick(database, results=None, runner=None):
    """Caller must hold the same global Worker lock for the entire tick."""
    answer = local_worker._run_once_locked(database, results, runner)
    if answer['state'] != 'idle':
        return answer
    paired = match_queue.dispatch(database)
    if paired['state'] == 'matched':
        return local_worker._run_once_locked(database, results, runner)
    return paired


def run_loop(database, results=None, *, interval=2.0, max_cycles=None,
             stop_event=None, runner=None, emit=None, maintenance=None):
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not math.isfinite(interval) or not 0.1 <= interval <= 60:
        raise ValueError('interval must be between 0.1 and 60 seconds')
    if max_cycles is not None and (type(max_cycles) is not int or max_cycles < 1):
        raise ValueError('max_cycles must be a positive integer')
    stop = stop_event if stop_event is not None else Event()
    counts = {'cycles': 0, 'completed': 0, 'failed': 0}
    last_state = None
    next_cleanup = 0
    with local_worker._host_gate(), local_worker._worker_lock():
        while not stop.is_set():
            if time.monotonic() >= next_cleanup:
                next_cleanup = time.monotonic() + log_retention.CHECK_INTERVAL
                try:
                    cleanup = log_retention.prune(database, results, apply=True)
                    if emit is not None and (cleanup['deleted'] or cleanup['skipped']):
                        emit(cleanup)
                except (ValueError, OSError, sqlite3.Error):
                    if emit is not None:
                        emit({'state': 'log_cleanup_retry', 'message': 'Chưa dọn được bản ghi cũ; sẽ thử lại sau một phút.'})
            if maintenance is not None:
                event = maintenance()
                if event is not None and emit is not None:
                    emit(event)
            answer = tick(database, results, runner)
            counts['cycles'] += 1
            state = answer['state']
            if state in ('completed', 'failed'):
                counts[state] += 1
            if emit is not None and (state in ('completed', 'failed') or state != last_state):
                emit(answer)
            last_state = state
            if max_cycles is not None and counts['cycles'] >= max_cycles:
                break
            if stop.wait(interval):
                break
    return {**counts, 'state': 'stopped'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=Path('output/queue.sqlite3'))
    parser.add_argument('--results', type=Path)
    parser.add_argument('--interval', type=float, default=2.0)
    parser.add_argument('--max-cycles', type=int)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    try:
        def emit(event):
            print(json.dumps(event, ensure_ascii=False, allow_nan=False), flush=True)
        answer = run_loop(args.db, args.results, interval=args.interval,
                          max_cycles=1 if args.once else args.max_cycles, emit=emit)
        emit(answer)
    except KeyboardInterrupt:
        print(json.dumps({'state': 'stopped', 'reason': 'keyboard_interrupt'}), flush=True)
    except (ValueError, KeyError, OSError, RuntimeError, sqlite3.Error) as error:
        parser.exit(1, f'LOCAL RUNNER ERROR: {error}\n')


if __name__ == '__main__':
    main()
