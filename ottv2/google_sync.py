"""Receive verified Forms every minute; publish standings every two minutes.

Google credentials belong in an ignored, private file, never in the web page.
The Apps Script endpoint is in google/Code.gs. No Google SDK is required.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
from pathlib import Path
import re
import secrets
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from . import accounts, elo_store, queue_admin
from .bot_store import validate_bot_id
from .github_update import repository_name

MAX_BYTES = 4 * 1024 * 1024
DOMAIN = 'vnu.edu.vn'
SYNC_INTERVAL_SECONDS = 60
RANKING_INTERVAL_SECONDS = 120


def initialize(account_db):
    """Add Google bookkeeping without changing existing users or sessions."""
    with accounts.database(account_db, write=True) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS verified_google_users (
            identity TEXT PRIMARY KEY REFERENCES users(identity), email TEXT UNIQUE NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS google_receipts (
            response_id TEXT PRIMARY KEY, status TEXT NOT NULL, message TEXT NOT NULL,
            identity TEXT, created REAL NOT NULL, ack_pending INTEGER NOT NULL DEFAULT 1)''')
        db.execute('''CREATE TABLE IF NOT EXISTS google_password_receipts (
            response_id TEXT PRIMARY KEY, status TEXT NOT NULL, message TEXT NOT NULL,
            identity TEXT, created REAL NOT NULL, ack_pending INTEGER NOT NULL DEFAULT 1)''')
        db.execute('CREATE TABLE IF NOT EXISTS password_changes ('
                   'identity TEXT PRIMARY KEY REFERENCES users(identity), changed_at REAL NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS google_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS repository_changes ('
                   'identity TEXT PRIMARY KEY REFERENCES users(identity), changed_at REAL NOT NULL)')
        # Old profiles predate repository-change tracking. Fence old submissions
        # when upgrading; subsequent initializations leave established times alone.
        db.execute('INSERT OR IGNORE INTO repository_changes SELECT identity,? FROM users', (time.time(),))


def _text(value, label, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{label} chưa đúng hoặc quá dài.')
    if any(ord(c) < 32 for c in value):
        raise ValueError(f'{label} chứa ký tự không hợp lệ.')
    return value.strip()


def apply_registration(account_db, response):
    """One transaction for account, email ownership and durable acknowledgment.

    Input comes ONLY from the authenticated Apps Script bridge, which reads
    FormResponse.getRespondentEmail(), never an email question on the form.
    Duplicate IDs return the original receipt and cannot reset a password.
    """
    if not isinstance(response, dict):
        raise ValueError('Phản hồi Google chưa đúng.')
    response_id = _text(response.get('response_id'), 'Mã phản hồi', 256)
    with accounts.database(account_db, write=True) as db:
        old = db.execute('SELECT * FROM google_receipts WHERE response_id=?', (response_id,)).fetchone()
        if old:
            return dict(old)
        identity = None
        try:
            email = _text(response.get('email'), 'Email', 254).casefold()
            if not re.fullmatch(r'[a-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@vnu\.edu\.vn', email):
                raise ValueError('Cần dùng email trường có đuôi @vnu.edu.vn.')
            identity = _text(response.get('identity'), 'Mã sinh viên', 64)
            try:
                validate_bot_id(identity)
            except ValueError:
                raise ValueError('Mã sinh viên cần 1–40 chữ thường, chữ số, dấu gạch ngang hoặc gạch dưới.') from None
            name = _text(response.get('name'), 'Họ tên', 100)
            try:
                repository = 'https://github.com/' + repository_name(response.get('repository'))
            except ValueError:
                raise ValueError('Link GitHub cần có dạng https://github.com/ten/kho-bot.') from None
            password = accounts._password(response.get('password'))
            if db.execute('SELECT 1 FROM users WHERE identity=?', (identity,)).fetchone():
                raise ValueError('Mã sinh viên đã đăng ký. Tài khoản cũ được giữ nguyên.')
            if db.execute('SELECT 1 FROM verified_google_users WHERE email=?', (email,)).fetchone():
                raise ValueError('Email đã đăng ký. Tài khoản cũ được giữ nguyên.')
            if db.execute("SELECT count(*) FROM users WHERE role='student'").fetchone()[0] >= 4000:
                raise ValueError('Đã đủ số tài khoản. Liên hệ người quản lý.')
            submitted = _submitted_at(response, optional=True)
            salt = secrets.token_hex(16)
            digest = accounts._hash(password, salt)
            db.execute('INSERT INTO users VALUES (?,?,?,?,?,?)',
                       (identity, name, 'student', salt, digest, repository))
            db.execute('INSERT INTO verified_google_users VALUES (?,?)', (identity, email))
            accounts._record_password_change(db, identity, at=submitted)
            accounts._record_repository_change(db, identity, at=submitted)
            status, message = 'accepted', 'Đã tạo tài khoản. Đăng nhập bằng mã sinh viên và bấm Cập nhật bot.'
        except ValueError as error:
            status, message = 'rejected', str(error)
        db.execute('INSERT INTO google_receipts(response_id,status,message,identity,created) VALUES (?,?,?,?,?)',
                   (response_id, status, message, identity, time.time()))
        return dict(db.execute('SELECT * FROM google_receipts WHERE response_id=?', (response_id,)).fetchone())


def _submitted_at(response, *, optional=False):
    value = response.get('created_at')
    if value is None and optional:
        return time.time()
    try:
        if not isinstance(value, str):
            raise ValueError()
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if moment.tzinfo is None:
            raise ValueError()
        timestamp = moment.timestamp()
        if timestamp <= 0 or timestamp > time.time() + 300:
            raise ValueError()
        return timestamp
    except (ValueError, OverflowError):
        raise ValueError('Thời điểm gửi yêu cầu chưa đúng.') from None


def apply_password_reset(account_db, response):
    """Apply verified password/repository requests atomically; accept old resets too."""
    if not isinstance(response, dict):
        raise ValueError('Phản hồi Google chưa đúng.')
    response_id = _text(response.get('response_id'), 'Mã phản hồi', 256)
    with accounts.database(account_db, write=True) as db:
        old = db.execute('SELECT * FROM google_password_receipts WHERE response_id=?', (response_id,)).fetchone()
        if old:
            return dict(old)
        identity = None
        try:
            email = _text(response.get('email'), 'Email', 254).casefold()
            identity = _text(response.get('identity'), 'Mã sinh viên', 64)
            user = db.execute("SELECT users.identity FROM users JOIN verified_google_users USING(identity) "
                              "WHERE users.identity=? AND email=? AND role='student'", (identity, email)).fetchone()
            if not user:
                raise ValueError('Email và mã sinh viên phải trùng với tài khoản đã đăng ký.')
            change = response.get('change', 'password')
            if change not in ('password', 'repository', 'both'):
                raise ValueError('Chọn đổi mật khẩu, link GitHub hoặc cả hai.')
            password_change = change in ('password', 'both')
            repository_change = change in ('repository', 'both')
            if not password_change and response.get('password') not in (None, ''):
                raise ValueError('Chỉ đổi link GitHub thì để trống mật khẩu; muốn đổi cả hai hãy chọn lại nội dung.')
            if not repository_change and response.get('repository') not in (None, ''):
                raise ValueError('Chỉ đổi mật khẩu thì để trống link GitHub; muốn đổi cả hai hãy chọn lại nội dung.')
            password = accounts._password(response.get('password')) if password_change else None
            repository = None
            if repository_change:
                repository = 'https://github.com/' + repository_name(response.get('repository'))
                queue = db.execute('SELECT queue FROM settings').fetchone()[0]
                if identity in queue_admin.status(queue)['control']['blocked_bots']:
                    raise ValueError('Tài khoản đang bị chặn. Hãy liên hệ người quản lý.')
            submitted = _submitted_at(response)
            for needed, table, label in ((password_change, 'password_changes', 'mật khẩu'),
                                         (repository_change, 'repository_changes', 'link GitHub')):
                if needed:
                    previous = db.execute(f'SELECT changed_at FROM {table} WHERE identity=?', (identity,)).fetchone()
                    if previous and submitted <= previous[0]:
                        raise ValueError(f'Yêu cầu cũ hơn lần đổi {label} gần nhất nên không được áp dụng.')
            # Validate both fields and timestamps before touching either field.
            if password_change:
                accounts._expire_sessions(db, time.time())
                salt = secrets.token_hex(16)
                digest = accounts._hash(password, salt)
                db.execute('UPDATE users SET salt=?,password_hash=? WHERE identity=?', (salt, digest, identity))
                db.execute('DELETE FROM sessions WHERE identity=?', (identity,))
                db.execute('DELETE FROM attempts WHERE key=?', (hashlib.sha256(identity.encode()).hexdigest(),))
                accounts._record_password_change(db, identity, at=submitted)
            if repository_change:
                db.execute('UPDATE users SET repository=? WHERE identity=?', (repository, identity))
                accounts._record_repository_change(db, identity, at=submitted)
            status = 'accepted'
            if change == 'password':
                message = 'Đã đổi mật khẩu và đăng xuất các phiên cũ. Hãy đăng nhập bằng mật khẩu mới.'
            elif change == 'repository':
                message = 'Đã lưu link GitHub mới. Vào Bot của tôi và bấm Cập nhật bot khi hết thời gian chờ.'
            else:
                message = 'Đã đổi mật khẩu và link GitHub. Đăng nhập lại rồi bấm Cập nhật bot khi hết thời gian chờ.'
        except ValueError as error:
            status, message = 'rejected', str(error)
        db.execute('INSERT INTO google_password_receipts(response_id,status,message,identity,created) VALUES (?,?,?,?,?)',
                   (response_id, status, message, identity, time.time()))
        return dict(db.execute('SELECT * FROM google_password_receipts WHERE response_id=?', (response_id,)).fetchone())


def pending_acks(account_db, *, password=False):
    table = 'google_password_receipts' if password else 'google_receipts'
    with accounts.database(account_db) as db:
        return [dict(r) for r in db.execute(f'SELECT response_id,status,message FROM {table} '
                                           'WHERE ack_pending=1 ORDER BY created LIMIT 50')]


def _state(account_db, key, value=None):
    with accounts.database(account_db, write=value is not None) as db:
        if value is not None:
            db.execute('INSERT INTO google_state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (key, value))
        row = db.execute('SELECT value FROM google_state WHERE key=?', (key,)).fetchone()
        return row[0] if row else None


def status(account_db):
    with accounts.database(account_db) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='google_state'").fetchone():
            return {'enabled': False, 'last_success': None, 'last_error': ''}
        values = dict(db.execute('SELECT key,value FROM google_state'))
    return {'enabled': True, 'last_success': values.get('last_success'), 'last_error': values.get('last_error', '')}


def ranking_rows(account_db, ratings, *, now=None):
    moment = datetime.fromtimestamp(time.time() if now is None else now, timezone.utc)
    ledger = {r['bot_id']: r for r in elo_store.standings(ratings, limit=4000)} if ratings else {}
    with accounts.database(account_db) as db:
        students = db.execute("SELECT identity,display_name FROM users WHERE role='student'").fetchall()
    result = []
    for student in students:
        row = ledger.get(student['identity'], {})
        result.append([student['identity'], student['display_name'], row.get('rating', 1000.0),
                       *[row.get(k, 0) for k in ('played', 'wins', 'draws', 'losses')]])
    result.sort(key=lambda row: (-row[2], row[0]))
    stamp = moment.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).isoformat(timespec='seconds')
    return [[rank, *row, stamp] for rank, row in enumerate(result, 1)]


class BridgeError(RuntimeError):
    pass


class _GoogleRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if (target.scheme != 'https' or target.hostname not in ('script.google.com', 'script.googleusercontent.com')
                or target.username or target.password or target.port not in (None, 443)):
            raise BridgeError('Google chuyển sang địa chỉ không hợp lệ.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Bridge:
    def __init__(self, settings):
        self.url = settings.get('url', '')
        if not re.fullmatch(r'https://script\.google\.com/macros/s/[A-Za-z0-9_-]+/exec', self.url):
            raise ValueError('Chưa có địa chỉ Apps Script đã triển khai.')
        self.secret = settings.get('secret', '')
        if not re.fullmatch(r'[a-f0-9]{64}', self.secret):
            raise ValueError('Khóa kết nối Google cần đúng 64 ký tự hex.')

    def call(self, action, **data):
        payload = json.dumps({'action': action, **data}, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        stamp, nonce = int(time.time()), secrets.token_hex(16)
        message = f'{stamp}\n{nonce}\n{payload}'
        signature = hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        envelope = json.dumps({'timestamp': stamp, 'nonce': nonce, 'data': payload, 'signature': signature}, ensure_ascii=False)
        # Forms localizes its no-destination exception by request language.
        # Match the Vietnamese Apps Script setup even on an English Linux host.
        request = urllib.request.Request(self.url, envelope.encode(),
                                         {'Content-Type': 'application/json', 'Accept-Language': 'vi'}, method='POST')
        try:
            with urllib.request.build_opener(_GoogleRedirect()).open(request, timeout=15) as response:
                raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise BridgeError('Phản hồi Google quá lớn.')
            answer = json.loads(raw)
            if not isinstance(answer, dict) or answer.get('ok') is not True:
                raise BridgeError('Google chưa xử lý được. Kiểm tra khóa và cài đặt Apps Script.')
            return answer
        except (OSError, ValueError, urllib.error.URLError) as error:
            # Never put response bodies, credentials, or submitted passwords in logs.
            raise BridgeError('Chưa kết nối được Google. Tài khoản và điểm trên máy vẫn được giữ.') from None


def _sync_forms(account_db, bridge):
    def acknowledge(*, password=False):
        rows = pending_acks(account_db, password=password)
        if not rows:
            return
        answer = bridge.call('ack_reset' if password else 'ack', receipts=rows)
        ids = answer.get('acknowledged')
        expected = {r['response_id'] for r in rows}
        if not isinstance(ids, list) or any(not isinstance(i, str) or i not in expected for i in ids):
            raise BridgeError('Google trả kết quả xác nhận chưa đúng.')
        with accounts.database(account_db, write=True) as db:
            table = 'google_password_receipts' if password else 'google_receipts'
            db.executemany(f'UPDATE {table} SET ack_pending=0 WHERE response_id=?', [(i,) for i in ids])
    acknowledge()
    answer = bridge.call('pull')
    rows = answer.get('registrations')
    if not isinstance(rows, list) or len(rows) > 10:
        raise BridgeError('Danh sách đăng ký Google chưa đúng.')
    for row in rows:
        apply_registration(account_db, row)
    acknowledge()
    acknowledge(password=True)
    resets = bridge.call('pull_reset').get('resets')
    if not isinstance(resets, list) or len(resets) > 10:
        raise BridgeError('Danh sách yêu cầu mật khẩu Google chưa đúng.')
    for row in resets:
        apply_password_reset(account_db, row)
    acknowledge(password=True)
    return len(rows), len(resets)


def _ranking_wait(account_db, now):
    last = _state(account_db, 'ranking_exported_at')
    if last is None:
        return 0
    stamp = float(last)
    if not math.isfinite(stamp) or stamp > now:
        return 0
    return max(0, RANKING_INTERVAL_SECONDS - (now - stamp))


def sync_once(account_db, ratings, bridge, *, now=None, sync_forms=True):
    now = time.time() if now is None else now
    registrations, resets = _sync_forms(account_db, bridge) if sync_forms else (0, 0)
    if _ranking_wait(account_db, now) == 0:
        bridge.call('ranking', rows=ranking_rows(account_db, ratings, now=now), generated_at=now)
        # Mark only a confirmed export; failures must be retried after restart.
        _state(account_db, 'ranking_exported_at', str(now))
    _state(account_db, 'last_success', datetime.fromtimestamp(now, timezone.utc).isoformat())
    _state(account_db, 'last_error', '')
    return {'registrations_checked': registrations, 'password_requests_checked': resets, 'state': 'google_synced'}


def load_client(client_file):
    path = elo_store._database_path(client_file, 'Google client')
    if path.stat().st_size > 8192:
        raise ValueError('Tệp kết nối Google quá lớn.')
    if path.stat().st_mode & 0o077 and __import__('os').name != 'nt':
        raise ValueError('Tệp khóa Google cần quyền 600: chmod 600 <tệp>.')
    return Bridge(json.loads(path.read_text(encoding='utf-8')))


def run_loop(account_db, ratings, client_file, stop_event, *, emit=None):
    bridge = load_client(client_file)
    initialize(account_db)
    next_forms = time.monotonic()
    while not stop_event.is_set():
        forms_due = time.monotonic() >= next_forms
        failed = False
        try:
            result = (sync_once(account_db, ratings, bridge) if forms_due else
                      sync_once(account_db, ratings, bridge, sync_forms=False))
            if emit:
                emit(result)
        except (ValueError, OSError, sqlite3.Error, BridgeError):
            failed = True
            message = f'Đồng bộ Google chưa thành công; sẽ thử lại sau {SYNC_INTERVAL_SECONDS} giây.'
            _state(account_db, 'last_error', message)
            if emit:
                emit({'state': 'google_retry', 'message': message})
        if forms_due:
            next_forms = time.monotonic() + SYNC_INTERVAL_SECONDS
        until_forms = max(0, next_forms - time.monotonic())
        until_ranking = _ranking_wait(account_db, time.time())
        if failed or until_ranking == 0:
            until_ranking = SYNC_INTERVAL_SECONDS
        stop_event.wait(max(1, math.ceil(min(until_forms, until_ranking))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--accounts', type=Path, required=True)
    parser.add_argument('--ratings', type=Path)
    parser.add_argument('--client', type=Path, required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    try:
        if args.once:
            initialize(args.accounts)
            print(json.dumps(sync_once(args.accounts, args.ratings, load_client(args.client))))
        else:
            from threading import Event
            run_loop(args.accounts, args.ratings, args.client, Event(), emit=lambda row: print(json.dumps(row), flush=True))
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, sqlite3.Error, BridgeError):
        parser.exit(1, 'Chưa đồng bộ được Google. Xem docs/VAN_HANH.md.\n')


if __name__ == '__main__':
    main()
