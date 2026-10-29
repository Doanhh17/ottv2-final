"""Account-scoped local views and durable bot-update requests."""

from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory
import time

from . import accounts, bot_store, daily_quota, elo_store, github_update, log_retention, match_archive, match_queue, queue_admin, update_gate
from .rules import CPU_BUDGET_SECONDS, RESPONSE_LIMIT_SECONDS, MEMORY_LIMIT_MIB, MAX_MOVES_PER_SIDE

POLL_INTERVAL_SECONDS = 60


def config(database):
    with closing(elo_store._connect(Path(database).absolute(), readonly=True)) as connection:
        match_queue._schema(connection)
        return match_queue._config(connection)


def personal_jobs(database, identity, *, limit=10):
    with closing(elo_store._connect(Path(database).absolute(), readonly=True)) as connection:
        match_queue._schema(connection)
        return [dict(row) for row in connection.execute('''
            SELECT jobs.match_id,jobs.state,jobs.payload FROM jobs
            JOIN quota_entries ON jobs.match_id=quota_entries.match_id
            WHERE quota_entries.bot_id=? ORDER BY jobs.rowid DESC LIMIT ?
        ''', (identity, limit))]


def own_log(database, directory, identity, match_id, *, admin=False):
    match_archive.validate_match_id(match_id)
    with closing(elo_store._connect(Path(database).absolute(), readonly=True)) as connection:
        row = connection.execute('SELECT jobs.state FROM jobs JOIN quota_entries USING(match_id) '
                                 'WHERE match_id=? AND (? OR bot_id=?) LIMIT 1', (match_id, admin, identity)).fetchone()
        if row is None or row['state'] != 'completed':
            raise FileNotFoundError('Không tìm thấy trận của bạn.')
        saved = log_retention.metadata(connection, match_id)
        if saved and (saved['deleted_at'] is not None or saved['expires_at'] <= time.time()):
            raise log_retention.LogExpired('Bản ghi đã hết hạn lưu 7 ngày. Điểm và kết quả vẫn được giữ.')
    data, record = match_archive.read_match(directory, match_id)
    sources = record.get('bot_sources', {})
    if not admin and identity not in {sources.get(side, {}).get('bot_id') for side in ('A', 'B')}:
        raise ValueError('stored log ownership mismatch')
    return data, record


def history(database, directory, identity):
    rows = []
    jobs = personal_jobs(database, identity)
    with closing(elo_store._connect(Path(database).absolute(), readonly=True)) as connection:
        saved_rows = {}
        if jobs and log_retention._exists(connection):
            placeholders = ','.join('?' for _ in jobs)
            saved_rows = {r['match_id']: dict(r) for r in connection.execute(
                'SELECT * FROM replay_retention WHERE match_id IN (' + placeholders + ')',
                [r['match_id'] for r in jobs])}
    for row in jobs:
        payload = json.loads(row['payload'])
        item = {'match_id': row['match_id'], 'state': row['state'],
                'created_at_utc': payload['created_at_utc'], 'map': payload['map']['name'],
                'players': {side: payload['players'][side]['bot_id'] for side in ('A', 'B')},
                'result': None, 'log_available': False}
        if row['state'] in ('pending', 'running'):
            item['wait_seconds'] = _wait_seconds(payload)
        if row['state'] == 'completed':
            saved = saved_rows.get(row['match_id'])
            if saved:
                item['result'] = json.loads(saved['result_json'])
                if saved['deleted_at'] is not None or saved['expires_at'] <= time.time():
                    item['log_expired'] = True
                    item['log_error'] = 'Bản ghi đã hết hạn lưu 7 ngày. Điểm và kết quả vẫn được giữ.'
                    rows.append(item)
                    continue
                # The completion transaction already validated and stored this
                # summary. Never parse an entire replay to render its list row.
                try:
                    target = match_archive.archive_directory(directory) / (row['match_id'] + '.json')
                    item['log_available'] = (not target.is_symlink() and not target.is_junction()
                                             and target.is_file())
                except (OSError, ValueError):
                    item['log_available'] = False
                if not item['log_available']:
                    item['log_error'] = 'Chưa đọc được bản ghi trận. Báo người quản lý giúp bạn.'
                rows.append(item)
                continue
            try:
                _, record = own_log(database, directory, identity, row['match_id'])
                item['result'] = {key: record['result'][key] for key in ('winner', 'reason', 'valid_move_count')}
                item['log_available'] = True
            except (OSError, ValueError):
                item['log_error'] = 'Chưa đọc được bản ghi trận. Báo người quản lý giúp bạn.'
        rows.append(item)
    return rows


def _wait_seconds(job):
    return max(0, int(time.time() - datetime.fromisoformat(job['created_at_utc']).timestamp()))


def latest_update(account_db, identity):
    with accounts.database(account_db) as connection:
        row = connection.execute('SELECT id,state,created,finished,message,version FROM updates '
                                 'WHERE identity=? ORDER BY created DESC LIMIT 1', (identity,)).fetchone()
        return dict(row) if row else None


def prepare_update_queue(account_db):
    with accounts.database(account_db, write=True) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS update_retries ('
                           'request_id TEXT PRIMARY KEY REFERENCES updates(id), retry_at REAL NOT NULL)')


def _student_queue_snapshot(database, identity, *, include_history):
    """Quota, pairing and latest result must describe the same queue snapshot."""
    with closing(elo_store._connect(Path(database).absolute(), readonly=True)) as connection:
        connection.execute('BEGIN')
        match_queue._schema(connection)
        cfg = match_queue._config(connection)
        quota = daily_quota.status_from_connection(connection, identity)
        request = match_queue._status(connection, identity)
        if request['state'] == 'matched':
            row = connection.execute('SELECT state,payload FROM jobs WHERE match_id=?',
                                     (request['match_id'],)).fetchone()
            if row is None:
                raise ValueError('queue job not found')
            request['job_state'] = row['state']
            if row['state'] in ('pending', 'running'):
                request['wait_seconds'] = _wait_seconds(json.loads(row['payload']))
        data = {'quota': quota, 'request': request}
        if not include_history:
            row = connection.execute('''SELECT jobs.match_id,jobs.state FROM jobs
                JOIN quota_entries ON jobs.match_id=quota_entries.match_id
                WHERE quota_entries.bot_id=? ORDER BY jobs.rowid DESC LIMIT 1''',
                (identity,)).fetchone()
            data['latest_match'] = dict(row) if row else None
        return cfg, data


def dashboard(database, account_db, directory, user, *, include_history=True):
    identity = user['identity']
    if user['role'] == 'admin':
        cfg = config(database)
        student_state = None
    else:
        cfg, student_state = _student_queue_snapshot(database, identity, include_history=include_history)
    state = queue_admin.status(database) if user['role'] == 'admin' else None
    controls = match_queue._controls(cfg)
    data = {'user': user, 'map': cfg['map'],
            'poll_after_seconds':POLL_INTERVAL_SECONDS,
            'paused': controls['paused'], 'pause_reason': controls['pause_reason'],
            'updates_locked': controls['updates_locked'],
            'blocked': identity in controls['blocked_bots'],
            'limits': {'cpu_seconds': CPU_BUDGET_SECONDS,
                       'response_seconds': RESPONSE_LIMIT_SECONDS, 'memory_mib': MEMORY_LIMIT_MIB,
                       'max_moves_per_side': MAX_MOVES_PER_SIDE}}
    data['elo'] = elo_store.INITIAL_RATING
    if cfg['ratings'] and Path(cfg['ratings']).exists():
        with closing(elo_store._connect(Path(cfg['ratings']), readonly=True)) as connection:
            elo_store._check_schema(connection)
            row = connection.execute('SELECT rating FROM ratings WHERE bot_id=?', (identity,)).fetchone()
            if row is not None:
                data['elo'] = row['rating']
    if user['role'] == 'admin':
        from .practice_config import export
        data['practice_config']=export(cfg)
        from . import google_sync
        data['google_sync'] = google_sync.status(account_db)
        if cfg.get('execution_mode')=='distributed':
            from . import cluster
            data['cluster']=cluster.status(database)
        data['admin'] = state
        with accounts.database(account_db) as connection:
            locked = {row[0] for row in connection.execute('SELECT identity FROM account_locks')} if accounts._has_locks(connection) else set()
            data['students'] = [{**accounts.public_user(row), 'locked': row['identity'] in locked} for row in connection.execute(
                "SELECT * FROM users WHERE role='student' ORDER BY identity LIMIT 4000")]
        with closing(elo_store._connect(Path(database).absolute(), readonly=True)) as connection:
            data['failures'] = [dict(row) for row in connection.execute(
                "SELECT match_id,attempts,last_error FROM jobs WHERE state='failed' ORDER BY rowid DESC LIMIT 10")]
        return data
    data.update(student_state)
    if include_history:
        data['matches'] = history(database, directory, identity)
    data['update'] = latest_update(account_db, identity)
    data['bot'] = None
    data['cooldown_seconds'] = 0
    path = Path(cfg['store']) / identity
    if path.exists() or path.is_symlink():
        current = path / 'current.json'
        if current.exists() or current.is_symlink():
            metadata = bot_store.load_metadata(cfg['store'], identity)
            data['bot'] = {'version': metadata['version'], 'origin': metadata.get('origin')}
        data['cooldown_seconds'] = update_gate.update_status(cfg['store'], identity)['remaining_seconds']
    return data


def set_student_locked(database, account_db, identity, locked, reason=''):
    """Change account access and matchmaking together in SQLite's rollback journal.

    Keep profiles, pinned jobs, quotas, ratings and historical logs intact.
    The attached queue and account store commit together; no intermediate unblock.
    """
    bot_store.validate_bot_id(identity)
    if type(locked) is not bool or not isinstance(reason, str) or len(reason) > 200:
        raise ValueError('Thông tin khóa tài khoản chưa đúng.')
    accounts.check_queue(account_db, database)
    queue = elo_store._database_path(database, 'Queue')
    with accounts.database(account_db, write=True) as connection:
        user = connection.execute('SELECT role FROM users WHERE identity=?', (identity,)).fetchone()
        if user is None or user['role'] != 'student':
            raise ValueError('Chọn một tài khoản sinh viên.')
        connection.execute('ATTACH DATABASE ? AS queue_state', (str(queue),))
        if connection.execute('PRAGMA queue_state.application_id').fetchone()[0] != match_queue.APPLICATION_ID:
            raise ValueError('Không đúng dữ liệu hàng đợi.')
        if any(connection.execute(f'PRAGMA {schema}.journal_mode').fetchone()[0] not in ('delete','persist','truncate')
               for schema in ('main','queue_state')):
            raise ValueError('Cần dùng rollback journal để khóa tài khoản và bot cùng lúc.')
        cfg = json.loads(connection.execute('SELECT data FROM queue_state.config WHERE id=1').fetchone()[0])
        controls = match_queue._controls(cfg)
        connection.execute('CREATE TABLE IF NOT EXISTS account_locks ('
                           'identity TEXT PRIMARY KEY REFERENCES users(identity), reason TEXT NOT NULL, locked_at REAL NOT NULL)')
        if locked:
            connection.execute('INSERT INTO account_locks VALUES (?,?,?) ON CONFLICT(identity) '
                               'DO UPDATE SET reason=excluded.reason,locked_at=excluded.locked_at', (identity, reason, time.time()))
            connection.execute('DELETE FROM sessions WHERE identity=?', (identity,))
            connection.execute("UPDATE updates SET state='failed',finished=?,message=? WHERE identity=? AND state='pending'",
                               (time.time(), 'Tài khoản đã bị khóa; yêu cầu chưa chạy được dừng.', identity))
            controls['blocked_bots'][identity] = reason
            controls.setdefault('locked_accounts', {})[identity] = reason
            cancelled = connection.execute('DELETE FROM queue_state.waiting WHERE bot_id=?', (identity,)).rowcount
        else:
            connection.execute('DELETE FROM account_locks WHERE identity=?', (identity,))
            controls['blocked_bots'].pop(identity, None)
            controls.setdefault('locked_accounts', {}).pop(identity, None)
            connection.execute('DELETE FROM attempts WHERE key=?', (hashlib.sha256(identity.encode()).hexdigest(),))
            cancelled = 0
        cfg['control'] = controls
        connection.execute('UPDATE queue_state.config SET data=? WHERE id=1', (json.dumps(cfg),))
    return {'identity': identity, 'locked': locked, 'cancelled_waiting': cancelled}


def request_update(database, account_db, identity, repository):
    repository = 'https://github.com/' + github_update.repository_name(repository)
    cfg = config(database)
    request_id = secrets.token_hex(16)
    with accounts.database(account_db, write=True) as connection:
        if connection.execute("SELECT 1 FROM updates WHERE identity=? AND state IN ('pending','running')", (identity,)).fetchone():
            raise ValueError('Bot đang chờ cập nhật. Bạn không cần bấm thêm.')
        with update_gate.accepted_update(cfg['store'], identity,
                                        admission=queue_admin.update_admission(database, cfg['store'], identity)):
            connection.execute('INSERT INTO updates(id,identity,repository,state,created) VALUES (?,?,?,?,?)',
                               (request_id, identity, repository, 'pending', time.time()))
    return {'id': request_id, 'state': 'pending'}


def process_update(database, account_db):
    """Called only between matches, while the runner owns the global sandbox lock."""
    prepare_update_queue(account_db)
    with accounts.database(account_db, write=True) as connection:
        # A GitHub IP/token rate limit applies to the whole downloader, not
        # just one student. Do not hammer GitHub with the remaining queue.
        retry_at = connection.execute('SELECT max(retry_at) FROM update_retries').fetchone()[0]
        if retry_at is not None and retry_at > time.time():
            return
        # An interrupted operation is not retried silently or charged twice.
        connection.execute("UPDATE updates SET state='failed',finished=?,message=? WHERE state='running'",
                           (time.time(), 'Máy đã dừng trong lúc cập nhật. Kiểm tra phiên bản hiện tại rồi thử lại khi hết thời gian chờ.'))
        row = connection.execute("SELECT u.* FROM updates u LEFT JOIN update_retries r ON r.request_id=u.id "
                                 "WHERE u.state='pending' AND coalesce(r.retry_at,0)<=? "
                                 "ORDER BY u.created LIMIT 1", (time.time(),)).fetchone()
        if row is None:
            return
        connection.execute("UPDATE updates SET state='running' WHERE id=?", (row['id'],))
    cfg = config(database)
    version = None
    try:
        data, origin = github_update.download_repository(row['repository'])
        with TemporaryDirectory(prefix='ottv2-portal-update-') as tmp:
            path = Path(tmp) / 'source.zip'
            path.write_bytes(data)
            metadata = bot_store.import_zip(cfg['store'], row['identity'], path, origin=origin)
        version, state, message = metadata['version'], 'completed', 'Đã cập nhật bot thành công.'
    except github_update.GitHubRateLimited as error:
        with accounts.database(account_db, write=True) as connection:
            connection.execute('INSERT INTO update_retries VALUES (?,?) ON CONFLICT(request_id) '
                               'DO UPDATE SET retry_at=excluded.retry_at', (row['id'], error.retry_at))
            connection.execute("UPDATE updates SET state='pending',message=? WHERE id=?",
                               ('GitHub đang giới hạn tải. Yêu cầu đã được giữ và sẽ tự thử lại.', row['id']))
        return {'state': 'bot_update_deferred', 'request_id': row['id'], 'retry_at': error.retry_at}
    except (ValueError, OSError, RuntimeError, UnicodeError) as error:
        state = 'failed'
        # Error details belong in the operator's console, not another student's browser.
        print(f'BOT UPDATE ERROR {row["id"]}: {error}', flush=True)
        message = 'Chưa cập nhật được. Kiểm tra link GitHub công khai và main.py. Bản bot đang dùng được giữ lại.'
    with accounts.database(account_db, write=True) as connection:
        connection.execute('UPDATE updates SET state=?,finished=?,message=?,version=? WHERE id=?',
                           (state, time.time(), message, version, row['id']))
    return {'state': 'bot_updated' if state == 'completed' else 'bot_update_failed', 'request_id': row['id']}
