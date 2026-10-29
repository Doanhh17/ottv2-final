"""Per-student daily allowances, stored in the queue's own transaction."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json

from .bot_store import validate_bot_id
from .match_archive import validate_match_id

VIETNAM = timezone(timedelta(hours=7), name='Asia/Ho_Chi_Minh')
LIMITS = {'active': 3, 'passive': 3}


def utc_now():
    return datetime.now(timezone.utc)


def vietnam_day(moment=None):
    moment = utc_now() if moment is None else moment
    if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError('quota time must include its timezone')
    return moment.astimezone(VIETNAM).date().isoformat()


def _members(job):
    validate_match_id(job['match_id'])
    day = vietnam_day(datetime.fromisoformat(job['created_at_utc']))
    if job.get('quota_date', day) != day:
        raise ValueError('job allowance date does not match its creation date')
    members = []
    for side in ('A', 'B'):
        player = job['players'][side]
        validate_bot_id(player['bot_id'])
        if player['mode'] not in LIMITS:
            raise ValueError('unknown quota mode')
        members.append((player['bot_id'], player['mode']))
    if members[0][0] == members[1][0]:
        raise ValueError('quota job must contain two distinct students')
    return day, members


def create_schema(connection):
    connection.execute('''CREATE TABLE quota_entries (
        match_id TEXT NOT NULL REFERENCES jobs(match_id), bot_id TEXT NOT NULL,
        day TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('active','passive')),
        state TEXT NOT NULL CHECK(state IN ('reserved','used','released')),
        PRIMARY KEY(match_id, bot_id))''')
    connection.execute('CREATE INDEX quota_lookup ON quota_entries(bot_id,day,mode,state)')
    # Existing jobs were already accepted. Record them truthfully even if the
    # pre-quota history exceeds a new limit; do not cancel or reset them.
    rows = connection.execute('SELECT match_id,state,payload FROM jobs').fetchall()
    for row in rows:
        job = json.loads(row['payload'])
        if row['match_id'] != job['match_id']:
            raise ValueError('legacy job ID mismatch')
        day, members = _members(job)
        state = ('used' if row['state'] == 'completed' else
                 'reserved' if row['state'] in ('pending', 'running') else 'released')
        for identity, mode in members:
            connection.execute('INSERT INTO quota_entries VALUES (?, ?, ?, ?, ?)',
                               (row['match_id'], identity, day, mode, state))


def usage(connection, identity, day, mode):
    if mode not in LIMITS:
        raise ValueError('unknown quota mode')
    rows = connection.execute('''SELECT state,COUNT(*) AS count FROM quota_entries
        WHERE bot_id=? AND day=? AND mode=? AND state IN ('used','reserved') GROUP BY state''',
                              (identity, day, mode)).fetchall()
    counts = {row['state']: row['count'] for row in rows}
    used, reserved = counts.get('used', 0), counts.get('reserved', 0)
    return {'limit': LIMITS[mode], 'used': used, 'reserved': reserved,
            'remaining': max(0, LIMITS[mode] - used - reserved)}


def require_available(connection, identity, day, mode):
    if usage(connection, identity, day, mode)['remaining'] == 0:
        raise ValueError(f'{identity}: daily {mode} quota exhausted for {day} (Vietnam time)')


def reserve(connection, job):
    day, members = _members(job)
    for identity, mode in members:
        old = connection.execute('SELECT day,mode,state FROM quota_entries WHERE match_id=? AND bot_id=?',
                                 (job['match_id'], identity)).fetchone()
        if old is not None:
            if (old['day'], old['mode'], old['state']) != (day, mode, 'reserved'):
                raise ValueError('cannot replace a settled or different quota reservation')
            continue
        require_available(connection, identity, day, mode)
        connection.execute('INSERT INTO quota_entries VALUES (?, ?, ?, ?, ?)',
                           (job['match_id'], identity, day, mode, 'reserved'))


def settle(connection, job, *, used):
    day, members = _members(job)
    rows = connection.execute('SELECT bot_id,day,mode,state FROM quota_entries WHERE match_id=?',
                              (job['match_id'],)).fetchall()
    expected = {(identity, day, mode) for identity, mode in members}
    if {(row['bot_id'], row['day'], row['mode']) for row in rows} != expected:
        raise ValueError('job quota reservations are missing or mismatched')
    target = 'used' if used else 'released'
    if any(row['state'] not in ('reserved', target) for row in rows):
        raise ValueError('cannot change an already settled quota entry')
    connection.execute("UPDATE quota_entries SET state=? WHERE match_id=? AND state='reserved'",
                       (target, job['match_id']))


def status_from_connection(connection, identity, now=None):
    """Read quota buckets within the caller's existing queue snapshot."""
    # Local imports keep the transactional helpers usable by match_queue.
    from . import elo_store, match_queue
    validate_bot_id(identity)
    day = vietnam_day(now)
    result = {'bot_id': identity, 'day': day, 'timezone': 'Asia/Ho_Chi_Minh',
              **{mode: {'limit': limit, 'used': 0, 'reserved': 0, 'remaining': limit}
                 for mode, limit in LIMITS.items()}, 'reserved_other_days': 0}
    if connection is None:
        return result
    match_queue._schema(connection)
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if version >= 3:
        for mode in LIMITS:
            result[mode] = usage(connection, identity, day, mode)
        result['reserved_other_days'] = connection.execute('''SELECT COUNT(*) FROM quota_entries
            WHERE bot_id=? AND day<>? AND state='reserved' ''', (identity, day)).fetchone()[0]
    else:
        # Read-only inspection of v1/v2 must not pretend that history is empty.
        for row in connection.execute('SELECT state,payload FROM jobs'):
            job = json.loads(row['payload'])
            job_day, members = _members(job)
            for member, mode in members:
                if member != identity:
                    continue
                state = row['state']
                if job_day == day:
                    if state == 'completed':
                        result[mode]['used'] += 1
                    elif state in ('pending', 'running'):
                        result[mode]['reserved'] += 1
                elif state in ('pending', 'running'):
                    result['reserved_other_days'] += 1
        for mode in LIMITS:
            bucket = result[mode]
            bucket['remaining'] = max(0, bucket['limit'] - bucket['used'] - bucket['reserved'])
    return result


def status(database, identity, now=None):
    from . import elo_store
    path = elo_store._database_path(database, 'Queue')
    if not path.exists():
        return status_from_connection(None, identity, now)
    with closing(elo_store._connect(path, readonly=True)) as connection:
        connection.execute('BEGIN')
        return status_from_connection(connection, identity, now)
