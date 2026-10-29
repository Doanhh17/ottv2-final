"""Authenticated local student/operator portal; intentionally bound to loopback."""

import hmac
from http.cookies import SimpleCookie
import json
import re
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from . import accounts, board_view, local_api, log_retention, match_queue, portal_data, queue_admin
from .map_config import reject_duplicates

COOKIE = 'ottv2_session'
MAX_BODY = 320 * 1024


class PortalHandler(local_api.LocalHandler):
    def end_headers(self):
        if getattr(self, 'cookie_header', None):
            self.send_header('Set-Cookie', self.cookie_header)
        super().end_headers()

    def route(self):
        port = self.server.server_port
        hosts = (f'127.0.0.1:{port}', f'localhost:{port}')
        if self.server.public_origin:
            hosts += (urlsplit(self.server.public_origin).netloc,)
        if self.headers.get_all('Host', []) not in ([host] for host in hosts):
            raise ValueError('invalid_host')
        url = urlsplit(self.path)
        if url.scheme or url.netloc or url.query or url.fragment or '%' in url.path:
            raise ValueError('invalid_path')
        return url.path

    def token(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            return cookie[COOKIE].value if COOKIE in cookie else ''
        except Exception:
            return ''

    def user(self):
        return accounts.session(self.server.accounts, self.token())

    def body(self):
        # Browser writes require the exact origin and a non-form JSON body.
        origin = self.server.public_origin or 'http://' + self.headers.get('Host', '')
        if (self.headers.get('Origin') != origin
                or self.headers.get('Sec-Fetch-Site', 'same-origin') not in ('same-origin', 'none')):
            raise PermissionError('Hãy mở trang trên máy này rồi thử lại.')
        if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
            raise ValueError('Cần gửi dữ liệu JSON.')
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get('Transfer-Encoding') or len(lengths) != 1 or not lengths[0].isdigit():
            raise ValueError('Độ dài dữ liệu chưa đúng.')
        length = int(lengths[0])
        if not 0 < length <= MAX_BODY:
            raise ValueError('Dữ liệu quá lớn hoặc trống.')
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError('Chưa nhận đủ dữ liệu.')
        obj = json.loads(raw, object_pairs_hook=reject_duplicates,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Số không hợp lệ.')))
        if not isinstance(obj, dict):
            raise ValueError('Dữ liệu phải là một đối tượng JSON.')
        return obj

    def set_cookie(self, token, age=accounts.SESSION_SECONDS):
        self.cookie_header = f'{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}'
        if self.server.public_origin:
            self.cookie_header += '; Secure'

    def do_GET(self):
        try:
            path = self.route()
            if path == '/assets/uet-logo.png':
                logo = Path(__file__).resolve().parent.parent / 'web/assets/uet-logo.png'
                self.reply_bytes(200, logo.read_bytes(), content_type='image/png', cache_control='public, max-age=86400')
                return
            if path == '/standings':
                self.send_response(303)
                self.send_header('Location', 'https://docs.google.com/spreadsheets/d/1ovIv69XS_Z1LllldzG0L0Axfgg4oRLJ722S1_lTJJyA/edit')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            if path in ('/', '/arena', '/bot', '/results', '/standings', '/guide', '/account', '/manage', '/students', '/system'):
                page = Path(__file__).resolve().parent.parent / 'web' / 'portal.html'
                self.reply_bytes(200, page.read_bytes(), content_type='text/html; charset=utf-8')
                return
            if path == '/board.svg' or re.fullmatch(r'/board/[a-f0-9]{64}\.svg', path):
                cfg = portal_data.config(self.server.queue)
                if path != '/board.svg' and path != '/board/'+cfg['map']['sha256']+'.svg':
                    self.reply(409, {'error':'Bản đồ vừa đổi. Hãy làm mới trang.'})
                else:
                    self.reply_bytes(200, board_view.render(cfg['initial_state'],'Bản đồ hiện tại'), content_type='image/svg+xml')
                return
            if path == '/health':
                self.reply(200, {'ok': True, 'mode': 'local_accounts'})
                return
            if path == '/api/practice-config':
                from .practice_config import export
                self.reply(200,export(portal_data.config(self.server.queue)))
                return
            if path in ('/replay.html','/practice.html'):
                page=Path(__file__).resolve().parent.parent/('replay/index.html' if path=='/replay.html' else 'practice/index.html')
                self.reply_bytes(200,page.read_bytes(),filename='OTTv2-'+('replay' if path=='/replay.html' else 'dau-thu')+'.html',content_type='application/octet-stream')
                return
            user = self.user()
            if user is None:
                self.reply(401, {'error': 'Bạn cần đăng nhập.'})
                return
            if path == '/api/admin/map-guide':
                if user['role'] != 'admin':
                    self.reply(403, {'error': 'Chỉ quản trị viên xem hướng dẫn này.'})
                    return
                guide = Path(__file__).resolve().parent.parent / 'docs/BAN_DO.md'
                self.reply_bytes(200, guide.read_bytes(), filename='OTTv2-huong-dan-ban-do.md', content_type='text/plain; charset=utf-8')
                return
            if path == '/api/me':
                self.reply(200, portal_data.dashboard(self.server.queue, self.server.accounts, self.server.matches, user))
            elif path == '/api/status':
                self.reply(200, portal_data.dashboard(self.server.queue, self.server.accounts,
                           self.server.matches, user, include_history=False))
            elif path == '/api/history':
                self.reply(200, {'matches': portal_data.history(self.server.queue, self.server.matches, user['identity'])})
            elif path.startswith('/api/logs/'):
                identity = path[len('/api/logs/'):]
                data, _ = portal_data.own_log(self.server.queue, self.server.matches, user['identity'], identity,
                                              admin=user['role'] == 'admin')
                self.reply_bytes(200, data)
            else:
                self.reply(404, {'error': 'Không có trang này.'})
        except log_retention.LogExpired as error:
            self.reply(410, {'error': str(error)})
        except FileNotFoundError:
            self.reply(404, {'error': 'Chưa tìm thấy dữ liệu cần xem.'})
        except (ValueError, RuntimeError, KeyError, TypeError, sqlite3.Error):
            self.reply(409, {'error': 'Chưa đọc được dữ liệu. Báo người quản lý kiểm tra giúp bạn.'})
        except OSError:
            self.reply(503, {'error': 'Máy đang bận. Hãy thử lại.'})

    do_HEAD = do_GET

    def do_POST(self):
        if self.path == '/api/worker':
            self.worker_request()
            return
        try:
            path, body = self.route(), self.body()
            if path == '/api/login':
                if set(body) != {'identity', 'password'}:
                    raise ValueError('Nhập tên đăng nhập và mật khẩu.')
                token, user = accounts.login(self.server.accounts, **body)
                self.set_cookie(token)
                self.reply(200, {'user': user})
                return
            user = self.user()
            if user is None:
                self.reply(401, {'error': 'Phiên đăng nhập đã hết. Hãy đăng nhập lại.'})
                return
            if not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), user['csrf']):
                raise PermissionError('Trang đã cũ. Tải lại trang rồi thử lại.')
            identity = user['identity']
            if path.startswith('/api/admin/'):
                if user['role'] != 'admin':
                    raise PermissionError('Chỉ người quản lý dùng được nút này.')
                answer = self.admin(path, body)
            elif path == '/api/logout' and not body:
                accounts.logout(self.server.accounts, self.token())
                self.set_cookie('', 0)
                answer = {'ok': True}
            elif user['role'] == 'student':
                if path == '/api/find' and not body:
                    answer = match_queue.find_match(self.server.queue, identity)
                elif path == '/api/cancel' and not body:
                    answer = match_queue.cancel_request(self.server.queue, identity)
                elif path == '/api/update' and not body:
                    answer = portal_data.request_update(self.server.queue, self.server.accounts, identity, user['repository'])
                else:
                    self.reply(404, {'error': 'Thao tác chưa đúng.'})
                    return
            else:
                self.reply(404, {'error': 'Thao tác chưa đúng.'})
                return
            self.reply(200, answer)
        except accounts.LoginLimited as error:
            self.reply(429, {'error': str(error)})
        except PermissionError as error:
            self.reply(403, {'error': str(error)})
        except (ValueError, RuntimeError) as error:
            self.reply(400, {'error': friendly_error(str(error))})
        except (KeyError, TypeError, UnicodeError, RecursionError):
            self.reply(400, {'error': 'Thông tin nhập chưa đúng. Hãy kiểm tra lại.'})
        except (OSError, sqlite3.Error):
            self.reply(503, {'error': 'Máy đang bận hoặc chưa đọc được dữ liệu. Hãy thử lại.'})

    def worker_request(self):
        from . import cluster
        from .rules import RULES_HEADER, SUPPORTED_RULES_VERSIONS
        try:
            self.route()
            coordinator=getattr(self.server,'coordinator',None)
            if coordinator is None:
                self.reply(404,{'error':'worker_endpoint_disabled'});return
            values=self.headers.get_all('Authorization',[])
            if len(values)!=1 or not values[0].startswith('Bearer '):
                raise PermissionError('worker authentication required')
            worker=coordinator.authenticate(values[0][7:])
            sizes=self.headers.get_all('Content-Length',[])
            if (self.headers.get('Transfer-Encoding') or len(sizes)!=1 or not sizes[0].isdigit()
                    or not 0<int(sizes[0])<=cluster.MAX_RPC_BYTES
                    or self.headers.get('Content-Type','').split(';')[0]!='application/json'):
                raise ValueError('invalid worker request size/type')
            raw=self.rfile.read(int(sizes[0]))
            if len(raw)!=int(sizes[0]): raise ValueError('truncated worker request')
            request=json.loads(raw,object_pairs_hook=reject_duplicates,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid number')))
            versions=self.headers.get_all(RULES_HEADER,[])
            if len(versions)>1 or versions and versions[0] not in tuple(str(v) for v in SUPPORTED_RULES_VERSIONS):
                raise ValueError('unsupported worker rules version')
            version=int(versions[0]) if versions else 1
            self.reply(200,coordinator.call(worker,request,rules_version=version))
        except PermissionError:
            self.reply(401,{'error':'worker_authentication_required'})
        except cluster.LeaseRejected:
            self.reply(409,{'error':'lease_expired_or_replaced'})
        except (ValueError,KeyError,TypeError,UnicodeError,RecursionError):
            self.reply(400,{'error':'invalid_worker_request'})
        except (OSError,RuntimeError,sqlite3.Error):
            self.reply(503,{'error':'worker_request_retry'})

    def admin(self, path, body):
        queue = self.server.queue
        if path == '/api/admin/account-lock' and set(body) == {'identity', 'locked', 'reason'}:
            return portal_data.set_student_locked(queue, self.server.accounts, **body)
        if path == '/api/admin/pause' and set(body) == {'paused', 'reason'}:
            return queue_admin.set_paused(queue, body['paused'], body['reason'])
        if path == '/api/admin/updates' and set(body) == {'locked'}:
            return queue_admin.set_updates_locked(queue, body['locked'])
        if path == '/api/admin/block' and set(body) == {'identity', 'blocked', 'reason'}:
            return queue_admin.block_bot(queue, body['identity'], body['blocked'], body['reason'])
        if path == '/api/admin/map' and set(body) in ({'map'},{'map','practice_bots'}) and isinstance(body['map'], str):
            if not queue_admin.status(queue)['control']['paused']:
                raise ValueError('pause new matches before changing the map')
            if 'practice_bots' in body:
                from .practice_config import validate_bots
                validate_bots(body['practice_bots'])
            with TemporaryDirectory(prefix='ottv2-map-') as tmp:
                file = Path(tmp) / 'map.json'
                file.write_text(body['map'], encoding='utf-8')
                return queue_admin.change_map(queue, file,body.get('practice_bots'))
        if path == '/api/admin/student' and set(body) in (
                {'identity', 'display_name', 'password'},
                {'identity', 'display_name', 'password', 'repository'}):
            if not isinstance(body.get('repository', ''), str):
                raise ValueError('Link GitHub cần là một địa chỉ kho mã nguồn.')
            accounts.create_user(self.server.accounts, **body)
            return {'ok': True}
        if path == '/api/admin/password' and set(body) == {'identity', 'password'}:
            accounts.validate_bot_id(body['identity'])
            with accounts.database(self.server.accounts) as connection:
                row = connection.execute('SELECT role FROM users WHERE identity=?', (body['identity'],)).fetchone()
            if row is None or row['role'] != 'student':
                raise ValueError('Chọn một tài khoản sinh viên.')
            accounts.change_password(self.server.accounts, **body)
            return {'ok': True}
        raise ValueError('Thông tin quản lý chưa đúng.')


def friendly_error(message):
    known = (
        ('cooldown', 'Chưa đủ 3 phút từ lần cập nhật trước. Xem thời gian chờ trên trang.'),
        ('blocked', 'Tài khoản đang bị chặn. Hãy liên hệ người quản lý.'),
        ('updates are locked', 'Người quản lý đang khóa cập nhật bot. Hãy thử lại sau.'),
        ('matches are paused', 'Đang tạm dừng nhận trận mới. Hãy thử lại sau.'),
        ('pause new matches', 'Chưa đổi bản đồ: bạn cần bấm Tạm dừng nhận trận trước.'),
        ('quota', 'Bạn đã dùng hết lượt trong ngày hoặc lượt đang được giữ cho trận khác.'),
        ('cannot be cancelled', 'Đã ghép đối thủ nên không hủy được. Hãy chờ kết quả.'),
        ('repository', 'Nhập link GitHub dạng https://github.com/tên-người-dùng/tên-kho.'),
        ('GitHub', 'Kiểm tra lại link GitHub công khai của bạn.'),
        ('bot ID', 'Tên tài khoản chỉ dùng chữ thường, số, dấu gạch ngang hoặc gạch dưới.'),
        ('bot id', 'Chưa có bot hợp lệ hoặc tên tài khoản chưa đúng. Tên dùng 1–40 chữ thường, số, dấu gạch ngang hoặc gạch dưới.'),
        ('map', 'Bản đồ chưa đúng: cần bàn 9×9, mỗi bên 3 búa, 3 bao, 3 kéo và các ô hợp lệ.'),
    )
    for part, explanation in known:
        if part in message:
            return explanation
    if any(ord(char) > 127 for char in message) and len(message) <= 250:
        return message
    return 'Chưa thực hiện được. Kiểm tra bot và thông tin vừa nhập, rồi thử lại.'


def create_server(store, port, matches, queue, account_db, *, public_origin=None):
    if public_origin is not None:
        parsed = urlsplit(public_origin)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment
                or not re.fullmatch(r'[A-Za-z0-9.-]+(?::[0-9]{1,5})?', parsed.netloc)
                or parsed.port not in (None,443)):
            raise ValueError('public origin must be an HTTPS domain without path or query')
        with accounts.database(account_db) as connection:
            admins = connection.execute("SELECT * FROM users WHERE role='admin'").fetchall()
            demos = connection.execute("SELECT * FROM users WHERE identity IN ('student01','student02','student03')").fetchall()
        if not admins:
            raise ValueError('create a private admin account before public mode')
        for row in [*admins,*demos]:
            if hmac.compare_digest(row['password_hash'], accounts._hash(b'OTTv2-demo-2026',row['salt'])):
                raise ValueError('public mode refuses published demo passwords; prepare a separate production database')
    accounts.check_queue(account_db, queue)
    server = local_api.create_server(store, port, matches, queue)
    server.RequestHandlerClass = PortalHandler
    server.accounts = Path(account_db).absolute()
    server.public_origin = public_origin
    return server
