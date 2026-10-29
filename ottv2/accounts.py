"""Local accounts, password hashes and revocable browser sessions (stdlib only)."""

import argparse
from contextlib import closing, contextmanager
import getpass
import hashlib
import hmac
from pathlib import Path
import secrets
import sqlite3
import time

from . import elo_store
from .bot_store import validate_bot_id
from .github_update import repository_name

APP_ID = 0x4F545441
ROUNDS = 600_000
SESSION_SECONDS = 30 * 60
RELOGIN_WAIT_SECONDS = 7 * 60


@contextmanager
def database(path, *, write=False):
    path = elo_store._database_path(path, 'Accounts')
    if not path.exists():
        raise ValueError('initialize accounts first')
    with closing(elo_store._connect(path, readonly=not write)) as connection:
        try:
            if connection.execute('PRAGMA application_id').fetchone()[0] != APP_ID:
                raise ValueError('invalid accounts database')
            if connection.execute('PRAGMA user_version').fetchone()[0] != 1:
                raise ValueError('unsupported accounts database')
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield connection
            if write:
                connection.commit()
        except BaseException:
            connection.rollback()
            raise


def initialize(path, queue):
    path = elo_store._database_path(path, 'Accounts')
    queue = elo_store._database_path(queue, 'Queue').resolve()
    if not queue.exists():
        raise ValueError('initialize the queue first')
    if path.exists():
        check_queue(path, queue)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation: never turn an unrelated SQLite file into an account store.
    with path.open('xb'):
        pass
    path.chmod(0o600)
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript('''
            BEGIN IMMEDIATE;
            CREATE TABLE settings (queue TEXT NOT NULL);
            CREATE TABLE users (
                identity TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('student','admin')),
                salt TEXT NOT NULL, password_hash TEXT NOT NULL,
                repository TEXT NOT NULL DEFAULT '');
            CREATE TABLE sessions (
                token_hash TEXT PRIMARY KEY, identity TEXT NOT NULL REFERENCES users(identity),
                csrf TEXT NOT NULL, expires REAL NOT NULL);
            CREATE INDEX session_user ON sessions(identity);
            CREATE TABLE attempts (key TEXT PRIMARY KEY, started REAL NOT NULL, count INTEGER NOT NULL);
            CREATE TABLE updates (
                id TEXT PRIMARY KEY, identity TEXT NOT NULL REFERENCES users(identity),
                repository TEXT NOT NULL, state TEXT NOT NULL,
                created REAL NOT NULL, finished REAL, message TEXT NOT NULL DEFAULT '', version TEXT);
            CREATE UNIQUE INDEX one_active_update ON updates(identity) WHERE state IN ('pending','running');
            CREATE INDEX update_user ON updates(identity, created DESC);
        ''')
        connection.execute('INSERT INTO settings VALUES (?)', (str(queue),))
        connection.execute(f'PRAGMA application_id={APP_ID}')
        connection.execute('PRAGMA user_version=1')
        connection.commit()


def check_queue(path, queue):
    with database(path) as connection:
        if Path(connection.execute('SELECT queue FROM settings').fetchone()[0]).resolve() != Path(queue).resolve():
            raise ValueError('accounts belong to a different queue')


def _password(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError('Mật khẩu cần từ 12 đến 256 ký tự.')
    return password.encode('utf-8')


def _hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password, bytes.fromhex(salt), ROUNDS).hex()


def _record_password_change(connection, identity, *, at=None):
    # Lazy creation also upgrades existing local databases without altering users.
    connection.execute('CREATE TABLE IF NOT EXISTS password_changes ('
                       'identity TEXT PRIMARY KEY REFERENCES users(identity), changed_at REAL NOT NULL)')
    connection.execute('INSERT INTO password_changes VALUES (?,?) ON CONFLICT(identity) '
                       'DO UPDATE SET changed_at=excluded.changed_at',
                       (identity, time.time() if at is None else at))


def _record_repository_change(connection, identity, *, at=None):
    connection.execute('CREATE TABLE IF NOT EXISTS repository_changes ('
                       'identity TEXT PRIMARY KEY REFERENCES users(identity), changed_at REAL NOT NULL)')
    connection.execute('INSERT INTO repository_changes VALUES (?,?) ON CONFLICT(identity) '
                       'DO UPDATE SET changed_at=excluded.changed_at',
                       (identity, time.time() if at is None else at))


def create_user(path, identity, password, display_name='', role='student', repository=''):
    validate_bot_id(identity)
    password = _password(password)
    if role not in ('student', 'admin'):
        raise ValueError('Vai trò tài khoản chưa đúng.')
    if not isinstance(display_name, str) or len(display_name) > 100:
        raise ValueError('Tên hiển thị dài tối đa 100 ký tự.')
    if repository:
        repository = 'https://github.com/' + repository_name(repository)
    salt = secrets.token_hex(16)
    hashed = _hash(password, salt)
    with database(path, write=True) as connection:
        if connection.execute('SELECT 1 FROM users WHERE identity=?', (identity,)).fetchone():
            raise ValueError('Tài khoản đã có; dữ liệu cũ được giữ nguyên.')
        connection.execute('INSERT INTO users VALUES (?,?,?,?,?,?)',
                           (identity, display_name or identity, role, salt, hashed, repository))
        _record_password_change(connection, identity)
        _record_repository_change(connection, identity)


def public_user(row):
    return {key: row[key] for key in ('identity', 'display_name', 'role', 'repository')}


def _has_locks(connection):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='account_locks'").fetchone() is not None


def _is_locked(connection, identity):
    return _has_locks(connection) and connection.execute(
        'SELECT 1 FROM account_locks WHERE identity=?', (identity,)).fetchone() is not None


class LoginLimited(ValueError):
    pass


def _expire_sessions(connection, now):
    connection.execute('CREATE TABLE IF NOT EXISTS login_cooldowns ('
                       'identity TEXT PRIMARY KEY REFERENCES users(identity), until REAL NOT NULL)')
    expired = connection.execute("SELECT sessions.identity,MAX(expires) FROM sessions JOIN users USING(identity) "
                                 "WHERE expires<=? AND users.role='student' GROUP BY sessions.identity", (now,)).fetchall()
    for identity, ended in expired:
        connection.execute('INSERT INTO login_cooldowns VALUES (?,?) ON CONFLICT(identity) '
                           'DO UPDATE SET until=MAX(until,excluded.until)',
                           (identity, ended + RELOGIN_WAIT_SECONDS))
    connection.execute('DELETE FROM sessions WHERE expires<=?', (now,))
    connection.execute('DELETE FROM login_cooldowns WHERE until<=?', (now,))


def login(path, identity, password, *, now=None):
    now = time.time() if now is None else now
    if not isinstance(identity, str) or len(identity) > 64 or not isinstance(password, str) or len(password) > 256:
        raise ValueError('Tên đăng nhập hoặc mật khẩu chưa đúng.')
    key = hashlib.sha256(identity.encode()).hexdigest()
    # Commit attempt counters even when authentication fails.
    with database(path, write=True) as connection:
        connection.execute('DELETE FROM attempts WHERE started < ?', (now - 300,))
        _expire_sessions(connection, now)
        for limit_key, maximum in ((key, 10), ('global', 100)):
            row = connection.execute('SELECT count FROM attempts WHERE key=?', (limit_key,)).fetchone()
            if row and row['count'] >= maximum:
                raise LoginLimited('Bạn đã thử nhiều lần. Chờ 5 phút rồi đăng nhập lại.')
        for limit_key in (key, 'global'):
            connection.execute('INSERT INTO attempts VALUES (?,?,1) ON CONFLICT(key) DO UPDATE SET count=count+1',
                               (limit_key, now))
        user = connection.execute('SELECT * FROM users WHERE identity=?', (identity,)).fetchone()
    salt = user['salt'] if user else '0' * 32
    digest = _hash(password.encode(), salt)
    if user is None or not hmac.compare_digest(digest, user['password_hash']):
        raise ValueError('Tên đăng nhập hoặc mật khẩu chưa đúng.')
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    denied = None
    with database(path, write=True) as connection:
        current = connection.execute('SELECT password_hash FROM users WHERE identity=?', (identity,)).fetchone()
        if not hmac.compare_digest(current[0], digest):
            raise ValueError('Mật khẩu vừa được đổi. Hãy đăng nhập lại.')
        connection.execute('DELETE FROM attempts WHERE key=?', (key,))
        # Successful users must not consume the shared failed-login allowance.
        # Keep failures from other accounts; do not clear the global counter.
        connection.execute("UPDATE attempts SET count=MAX(0,count-1) WHERE key='global'")
        _expire_sessions(connection, now)
        cooldown = connection.execute('SELECT until FROM login_cooldowns WHERE identity=?', (identity,)).fetchone()
        if user['role'] == 'student' and _is_locked(connection, identity):
            denied = ValueError('Tài khoản đang bị khóa. Liên hệ ban tổ chức.')
        elif connection.execute('SELECT 1 FROM sessions WHERE identity=? AND expires>?', (identity, now)).fetchone():
            denied = ValueError('Đang đăng nhập ở chỗ khác. Hãy đăng xuất ở đó hoặc chờ phiên kết thúc.')
        elif user['role'] == 'student' and cooldown and cooldown[0] > now:
            wait = max(1, __import__('math').ceil((cooldown[0] - now) / 60))
            denied = LoginLimited(f'Phiên 30 phút đã hết. Chờ khoảng {wait} phút rồi đăng nhập lại.')
        else:
            connection.execute('INSERT INTO sessions VALUES (?,?,?,?)',
                               (hashlib.sha256(token.encode()).hexdigest(), identity, csrf, now + SESSION_SECONDS))
    if denied is not None:
        raise denied
    return token, {**public_user(user), 'csrf': csrf, 'session_expires_at': now + SESSION_SECONDS}


def session(path, token, *, now=None):
    if not isinstance(token, str) or not 20 <= len(token) <= 100:
        return None
    with database(path) as connection:
        row = connection.execute('SELECT users.*, sessions.csrf, sessions.expires FROM sessions JOIN users USING(identity) '
                                 'WHERE token_hash=? AND expires>?',
                                 (hashlib.sha256(token.encode()).hexdigest(), time.time() if now is None else now)).fetchone()
        if row and row['role'] == 'student' and _is_locked(connection, row['identity']):
            return None
        return {**public_user(row), 'csrf': row['csrf'], 'session_expires_at': row['expires']} if row else None


def logout(path, token, *, now=None):
    with database(path, write=True) as connection:
        _expire_sessions(connection, time.time() if now is None else now)
        connection.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))


def change_password(path, identity, password):
    validate_bot_id(identity)
    salt = secrets.token_hex(16)
    digest = _hash(_password(password), salt)
    with database(path, write=True) as connection:
        _expire_sessions(connection, time.time())
        if connection.execute('UPDATE users SET salt=?,password_hash=? WHERE identity=?', (salt, digest, identity)).rowcount != 1:
            raise ValueError('Không có tài khoản này.')
        connection.execute('DELETE FROM sessions WHERE identity=?', (identity,))
        _record_password_change(connection, identity)


def set_repository(path, identity, repository):
    repository = 'https://github.com/' + repository_name(repository)
    with database(path, write=True) as connection:
        if connection.execute('UPDATE users SET repository=? WHERE identity=?', (repository, identity)).rowcount != 1:
            raise ValueError('Không có tài khoản này.')
        _record_repository_change(connection, identity)
    return repository


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    init.add_argument('--queue', type=Path, required=True)
    add = commands.add_parser('add')
    add.add_argument('identity')
    add.add_argument('--name', default='')
    add.add_argument('--admin', action='store_true')
    reset = commands.add_parser('password')
    reset.add_argument('identity')
    args = parser.parse_args()
    try:
        if args.command == 'init':
            initialize(args.db, args.queue)
        else:
            password = getpass.getpass('Mật khẩu mới (ít nhất 12 ký tự): ')
            if password != getpass.getpass('Nhập lại mật khẩu: '):
                raise ValueError('Hai lần nhập chưa giống nhau.')
            if args.command == 'add':
                create_user(args.db, args.identity, password, args.name, 'admin' if args.admin else 'student')
            else:
                change_password(args.db, args.identity, password)
        print('Đã lưu.')
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
