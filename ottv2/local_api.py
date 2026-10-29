"""Read-only loopback API for bot status and saved match logs."""

import argparse
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sqlite3
from threading import BoundedSemaphore
from urllib.parse import unquote, urlsplit

from .bot_store import bot_directory, load_bot, validate_bot_id
from .rules import CURRENT_LIMITS
from .match_archive import archive_directory, list_matches, read_match, validate_match_id
from .update_gate import COOLDOWN_SECONDS, update_status
from . import daily_quota, elo_store, match_queue, queue_admin


class LocalHTTPServer(HTTPServer):
    # A fixed executor and bounded admission keep slow clients from creating
    # unlimited threads. SQLite/lease transactions retain their own locks.
    request_queue_size = 128

    def __init__(self, *args, max_handlers=8, **kwargs):
        if type(max_handlers) is not int or not 1 <= max_handlers <= 32:
            raise ValueError('HTTP handlers must be from 1 to 32')
        self._executor = None
        self._admission = BoundedSemaphore(max_handlers * 4)
        super().__init__(*args, **kwargs)
        self._executor = ThreadPoolExecutor(max_workers=max_handlers, thread_name_prefix='ottv2-http')

    def process_request(self, request, client_address):
        if not self._admission.acquire(blocking=False):
            try:
                request.settimeout(1)
                body = b'{"error":"Server busy; retry shortly."}'
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nConnection: close\r\n'
                                b'Content-Type: application/json\r\nRetry-After: 2\r\nContent-Length: '
                                + str(len(body)).encode() + b'\r\n\r\n' + body)
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            self._executor.submit(self._serve_request, request, client_address)
        except BaseException:
            self._admission.release()
            self.shutdown_request(request)
            raise

    def _serve_request(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)
            self._admission.release()

    def server_close(self):
        super().server_close()
        if self._executor is not None:
            self._executor.shutdown(wait=True)


class LocalHandler(BaseHTTPRequestHandler):
    server_version = 'OTTv2Local/1'
    sys_version = ''

    def setup(self):
        self.request.settimeout(3)
        super().setup()

    def log_message(self, *args):
        pass

    def reply(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.reply_bytes(status, data)

    def reply_bytes(self, status, data, filename=None, content_type='application/json; charset=utf-8', cache_control='no-store'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', cache_control)
        self.send_header('X-Content-Type-Options', 'nosniff')
        if content_type.startswith('text/html'):
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; "
                             "style-src 'unsafe-inline'; img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header('Referrer-Policy', 'no-referrer')
        if filename is not None:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        if status == 405:
            self.send_header('Allow', 'GET, HEAD')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def do_GET(self):
        port = self.server.server_port
        if self.headers.get('Host') not in (f'127.0.0.1:{port}', f'localhost:{port}'):
            self.reply(400, {'error': 'invalid_host'})
            return
        try:
            url = urlsplit(self.path)
        except ValueError:
            self.reply(400, {'error': 'invalid_request_path'})
            return
        if url.scheme or url.netloc or url.query or url.fragment:
            self.reply(400, {'error': 'invalid_request_path'})
            return
        if url.path == '/':
            try:
                page = Path(__file__).resolve().parent.parent / 'web' / 'history.html'
                self.reply_bytes(200, page.read_bytes(), content_type='text/html; charset=utf-8')
            except OSError:
                self.reply(500, {'error': 'history_page_unavailable'})
            return
        if url.path == '/health':
            self.reply(200, {'ok': True, 'mode': 'local_read_only', 'api_version': 1,
                            'cooldown_seconds': COOLDOWN_SECONDS,
                            'limits': dict(CURRENT_LIMITS)})
            return
        if url.path == '/matches' or url.path.startswith('/matches/'):
            self.matches(url.path)
            return
        if url.path in ('/queue', '/ratings') or url.path.startswith('/quota/'):
            self.arena(url.path)
            return
        if not url.path.startswith('/bots/'):
            self.reply(404, {'error': 'not_found'})
            return
        bot_id = unquote(url.path[len('/bots/'):])
        try:
            validate_bot_id(bot_id)
        except ValueError:
            self.reply(400, {'error': 'invalid_bot_id'})
            return
        path = self.server.store / bot_id
        if not path.exists() and not path.is_symlink():
            self.reply(404, {'error': 'bot_not_found'})
            return
        try:
            directory = bot_directory(self.server.store, bot_id)
            current = None
            if (directory / 'current.json').exists() or (directory / 'current.json').is_symlink():
                _, metadata = load_bot(self.server.store, bot_id)
                current = {'version': metadata['version'], 'ready_checked': True}
                if 'origin' in metadata:
                    current['origin'] = metadata['origin']
            status = update_status(self.server.store, bot_id)
            self.reply(200, {'bot_id': bot_id, 'current': current,
                            'update': {'busy': status['busy'], 'remaining_seconds': status['remaining_seconds'],
                                       'cooldown_seconds': COOLDOWN_SECONDS}})
        except (ValueError, RuntimeError):
            self.reply(409, {'error': 'invalid_bot_state'})
        except OSError:
            self.reply(500, {'error': 'store_read_failed'})

    def matches(self, path):
        match_id = None
        if path != '/matches':
            match_id = unquote(path[len('/matches/'):])
            try:
                validate_match_id(match_id)
            except ValueError:
                self.reply(400, {'error': 'invalid_match_id'})
                return
        try:
            if match_id is None:
                self.reply(200, list_matches(self.server.matches))
            else:
                data, _ = read_match(self.server.matches, match_id)
                self.reply_bytes(200, data, filename=match_id + '.json')
        except FileNotFoundError:
            self.reply(404, {'error': 'match_not_found'})
        except ValueError:
            self.reply(409, {'error': 'invalid_match_archive'})
        except OSError:
            self.reply(500, {'error': 'match_read_failed'})

    do_HEAD = do_GET

    def arena(self, path):
        identity = None
        if path.startswith('/quota/'):
            identity = unquote(path[len('/quota/'):])
            try:
                validate_bot_id(identity)
            except ValueError:
                self.reply(400, {'error': 'invalid_bot_id'})
                return
        if self.server.queue is None:
            self.reply(503, {'error': 'queue_not_configured'})
            return
        try:
            if path == '/queue':
                data = queue_admin.status(self.server.queue)
                controls = data['control']
                data['control'] = {key: controls[key] for key in ('paused', 'pause_reason', 'updates_locked')}
                data['blocked_bot_count'] = len(controls['blocked_bots'])
                self.reply(200, data)
            elif path == '/ratings':
                with closing(elo_store._connect(self.server.queue, readonly=True)) as connection:
                    match_queue._schema(connection)
                    config = match_queue._config(connection)
                rows = elo_store.standings(config['ratings']) if config['ratings'] is not None else []
                self.reply(200, {'ratings': rows, 'limit': 20})
            else:
                data = daily_quota.status(self.server.queue, identity)
                self.reply(200, data)
        except (ValueError, KeyError, sqlite3.DatabaseError):
            self.reply(409, {'error': 'invalid_queue_or_rating_state'})
        except OSError:
            self.reply(500, {'error': 'queue_read_failed'})

    def readonly(self):
        self.reply(405, {'error': 'read_only_api'})

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = readonly


def create_server(store, port=8765, matches=None, queue=None):
    store = Path(store)
    if store.is_symlink() or store.is_junction() or not store.is_dir():
        raise ValueError('API store must be an existing directory, not a link')
    matches = archive_directory(Path('output/matches') if matches is None else matches).absolute()
    if queue is not None:
        queue = elo_store._database_path(queue, 'Queue')
        queue_admin.status(queue)
        with closing(elo_store._connect(queue, readonly=True)) as connection:
            match_queue._schema(connection)
            if Path(match_queue._config(connection)['store']).resolve() != store.resolve():
                raise ValueError('API store does not match this queue')
    server = LocalHTTPServer(('127.0.0.1', port), LocalHandler)
    server.store = store.resolve()
    server.matches = matches
    server.queue = queue
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--matches', type=Path, default=Path('output/matches'))
    parser.add_argument('--queue', type=Path)
    args = parser.parse_args()
    try:
        with create_server(args.store, args.port, args.matches, args.queue) as server:
            print(f'LOCAL API: http://127.0.0.1:{server.server_port} | read-only', flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError, OverflowError, sqlite3.Error) as error:
        parser.exit(1, f'LOCAL API ERROR: {error}\n')


if __name__ == '__main__':
    main()
