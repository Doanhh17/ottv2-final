import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch
import zipfile

from ottv2.bot_store import import_zip
from ottv2.local_api import create_server
from ottv2.engine import sample_game
from ottv2.match_archive import MAX_LOG_BYTES
from ottv2.protocol import state_message
from ottv2.update_gate import accepted_update


class LocalApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Path(self.tmp.name) / 'store'
        path = Path(self.tmp.name) / 'bot.zip'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('main.py', b'pass')
        with patch('ottv2.bot_store.check_ready', return_value={
                'ready': True, 'source_sha256': hashlib.sha256(b'pass').hexdigest()}):
            import_zip(self.store, 'student01', path)
        self.matches = Path(self.tmp.name) / 'matches'
        self.server = create_server(self.store, 0, self.matches)
        self.thread = Thread(target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.thread.join(2)
        self.server.server_close()

    def request(self, path, method='GET', headers=None, raw=False):
        client = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            client.request(method, path, headers=headers or {})
            response = client.getresponse()
            body = response.read()
            return response.status, dict(response.getheaders()), body if raw else json.loads(body) if body else None
        finally:
            client.close()

    def test_health_is_loopback_only_and_reports_three_minute_policy(self):
        self.assertEqual(self.server.server_address[0], '127.0.0.1')
        status, headers, body = self.request('/health')
        self.assertEqual(status, 200)
        self.assertEqual(body['cooldown_seconds'], 180)
        self.assertEqual(body['limits']['memory_limit_mib_per_bot'], 64)
        self.assertEqual(body['limits']['cpu_budget_seconds'], 7)
        self.assertEqual(body['limits']['response_limit_seconds'], 2.5)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(self.request('/health', 'HEAD')[2], None)

    def test_home_page_serves_only_fixed_html_without_embedding_logs(self):
        path = self.save_match()
        status, headers, body = self.request('/', raw=True)
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'text/html; charset=utf-8')
        self.assertIn('frame-ancestors', headers['Content-Security-Policy'])
        self.assertIn('Sao chép log'.encode(), body)
        self.assertNotIn(path.read_bytes(), body)
        self.assertNotIn(('a' * 32).encode(), body)
        status, head, body = self.request('/', 'HEAD', raw=True)
        self.assertEqual(status, 200)
        self.assertEqual(head['Content-Length'], headers['Content-Length'])
        self.assertEqual(body, b'')
        self.assertEqual(self.request('/web/history.html')[0], 404)
        self.assertEqual(self.request('/../docs/THIET_KE.md')[0], 404)

    def test_bot_version_and_cooldown_are_read_without_writing_store(self):
        with patch('ottv2.update_gate.time.time', return_value=1000), accepted_update(self.store, 'student01'):
            pass
        before = {str(p.relative_to(self.store)): p.read_bytes() for p in self.store.rglob('*') if p.is_file()}
        with patch('ottv2.update_gate.time.time', return_value=1100):
            status, _, body = self.request('/bots/student01')
        self.assertEqual(status, 200)
        self.assertEqual(body['current']['version'], hashlib.sha256(b'pass').hexdigest())
        self.assertTrue(body['current']['ready_checked'])
        self.assertEqual(body['update']['remaining_seconds'], 80)
        self.assertNotIn(str(self.store), json.dumps(body))
        self.assertEqual(before, {str(p.relative_to(self.store)): p.read_bytes()
                                  for p in self.store.rglob('*') if p.is_file()})

    def test_bot_without_current_reports_null_and_active_request(self):
        with accepted_update(self.store, 'pending01'):
            status, _, body = self.request('/bots/pending01')
            self.assertEqual(status, 200)
            self.assertIsNone(body['current'])
            self.assertTrue(body['update']['busy'])

    def test_bad_routes_ids_and_host_are_rejected(self):
        self.assertEqual(self.request('/unknown')[0], 404)
        self.assertEqual(self.request('/bots/missing01')[0], 404)
        self.assertEqual(self.request('/bots/..%2foutside')[0], 400)
        self.assertEqual(self.request('/bots/student01?path=main.py')[0], 400)
        self.assertEqual(self.request('/health', headers={'Host': 'evil.example'})[0], 400)

    def test_write_methods_cannot_update_the_bot(self):
        before = (self.store / 'student01/current.json').read_bytes()
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            self.assertEqual(self.request('/bots/student01', method)[0], 405)
        self.assertEqual((self.store / 'student01/current.json').read_bytes(), before)

    def test_corrupt_metadata_is_reported_without_leaking_paths(self):
        (self.store / 'student01/current.json').write_bytes(b'{broken')
        status, _, body = self.request('/bots/student01')
        self.assertEqual(status, 409)
        self.assertEqual(body, {'error': 'invalid_bot_state'})

    def save_match(self, match_id='a' * 32):
        game = sample_game()
        state = state_message(game)
        record = {'schema_version': 1, 'match_id': match_id,
                  'created_at_utc': '2026-10-23T01:00:00+00:00',
                  'bots': {'A': 'alpha', 'B': 'guardian'},
                  'initial_state': state, 'final_state': state, 'history': [],
                  'limits': {'cpu_budget_seconds': 5, 'memory_limit_mib_per_bot': 64,
                             'response_limit_seconds': 1},
                  'result': {'winner': 'B', 'reason': 'startup_timeout', 'valid_move_count': 0}}
        self.matches.mkdir(exist_ok=True)
        path = self.matches / (match_id + '.json')
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return path

    def test_empty_archive_is_not_created_by_listing(self):
        status, _, body = self.request('/matches')
        self.assertEqual(status, 200)
        self.assertEqual(body, {'matches': [], 'skipped_count': 0, 'limited': False})
        self.assertFalse(self.matches.exists())

    def test_download_preserves_every_byte_map_and_clocks(self):
        path = self.save_match()
        record = json.loads(path.read_bytes())
        record['clock_history'] = [{'A': 5, 'B': 5}]
        path.write_text(json.dumps(record) + '\n', encoding='utf-8')
        before = path.read_bytes()
        status, headers, raw = self.request('/matches/' + 'a' * 32, raw=True)
        self.assertEqual(status, 200)
        self.assertEqual(raw, before)
        self.assertEqual(len(json.loads(raw)['initial_state']['board']), 18)
        self.assertEqual(json.loads(raw)['clock_history'], record['clock_history'])
        self.assertEqual(headers['Content-Disposition'], 'attachment; filename="' + 'a' * 32 + '.json"')
        self.assertEqual(headers['Content-Length'], str(len(before)))
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        status, head, body = self.request('/matches/' + 'a' * 32, 'HEAD')
        self.assertEqual(status, 200)
        self.assertEqual(head['Content-Length'], headers['Content-Length'])
        self.assertIsNone(body)
        self.assertEqual(path.read_bytes(), before)

    def test_list_is_bounded_newest_first_and_contains_download_links(self):
        for index in range(52):
            path = self.save_match(f'{index:032x}')
            os.utime(path, (1000 + index, 1000 + index))
        (self.matches / 'current.json').write_text('{"secret": 1}', encoding='utf-8')
        status, _, body = self.request('/matches')
        self.assertEqual(status, 200)
        self.assertEqual(len(body['matches']), 10)
        self.assertTrue(body['limited'])
        self.assertEqual(body['matches'][0]['match_id'], f'{51:032x}')
        self.assertEqual(body['matches'][0]['download_url'], '/matches/' + f'{51:032x}')
        self.assertEqual(body['matches'][0]['log_url'], '/matches/' + f'{51:032x}')
        self.assertNotIn(str(self.matches), json.dumps(body))
        self.assertNotIn('initial_state', body['matches'][0])
        self.assertEqual(len(list(self.matches.glob('*.json'))), 53)

    def test_corrupt_newest_log_does_not_hide_older_valid_matches(self):
        for index in range(12):
            path = self.save_match(f'{index:032x}')
            os.utime(path, (1000 + index, 1000 + index))
        path.write_bytes(b'{broken')
        status, _, body = self.request('/matches')
        self.assertEqual(status, 200)
        self.assertEqual(len(body['matches']), 10)
        self.assertEqual(body['matches'][0]['match_id'], f'{10:032x}')
        self.assertEqual(body['skipped_count'], 1)
        self.assertTrue(body['limited'])

    def test_list_labels_are_bounded_without_changing_original_log(self):
        path = self.save_match()
        record = json.loads(path.read_bytes())
        record['bots']['A'] = 'x' * 100000
        record['result']['reason'] = 'y' * 100000
        path.write_text(json.dumps(record), encoding='utf-8')
        before = path.read_bytes()
        status, _, body = self.request('/matches')
        self.assertEqual(status, 200)
        self.assertEqual(len(body['matches'][0]['bots']['A']), 128)
        self.assertEqual(len(body['matches'][0]['result']['reason']), 128)
        self.assertLess(len(json.dumps(body)), 4096)
        self.assertEqual(self.request('/matches/' + 'a' * 32, raw=True)[2], before)

    def test_missing_traversal_and_write_requests_are_rejected(self):
        self.assertEqual(self.request('/matches/' + 'b' * 32)[0], 404)
        for bad in ('..%2fcurrent', 'a' * 32 + '.json', 'A' * 32, '', '%00'):
            self.assertEqual(self.request('/matches/' + bad)[0], 400)
        path = self.save_match()
        before = path.read_bytes()
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            self.assertEqual(self.request('/matches/' + 'a' * 32, method)[0], 405)
        self.assertEqual(path.read_bytes(), before)

    def test_corrupt_or_mismatched_records_are_skipped_and_cannot_download(self):
        path = self.save_match()
        valid = path.read_bytes()
        for invalid in (b'{broken', b'[]', valid.replace(b'"schema_version": 1', b'"schema_version": true'),
                        valid.replace(b'"' + b'a' * 32 + b'"', b'"' + b'b' * 32 + b'"'),
                        valid.replace(b'"valid_move_count": 0', b'"valid_move_count": 1'),
                        b'{"x": NaN}', b'{"x": 1, "x": 2}', b'\xff', b'[' * 1500):
            path.write_bytes(invalid)
            self.assertEqual(self.request('/matches/' + 'a' * 32)[0], 409)
            status, _, body = self.request('/matches')
            self.assertEqual(status, 200)
            self.assertEqual(body['matches'], [])
            self.assertEqual(body['skipped_count'], 1)

    def test_oversize_log_is_rejected_without_serving_it(self):
        path = self.save_match()
        with path.open('wb') as stream:
            stream.truncate(MAX_LOG_BYTES + 1)
        self.assertEqual(self.request('/matches/' + 'a' * 32)[0], 409)

    def test_engine_draw_result_is_listed_and_readable(self):
        path = self.save_match()
        record = json.loads(path.read_bytes())
        record['result'].update(winner='draw', reason='threefold_repetition')
        path.write_text(json.dumps(record), encoding='utf-8')
        self.assertEqual(self.request('/matches')[2]['matches'][0]['result']['winner'], 'draw')
        self.assertEqual(self.request('/matches/' + 'a' * 32)[0], 200)

    def test_directory_scan_limit_fails_explicitly(self):
        self.matches.mkdir()
        for index in range(1001):
            (self.matches / str(index)).touch()
        self.assertEqual(self.request('/matches')[0], 409)

    def test_linked_logs_and_replaced_archive_are_rejected(self):
        if os.name == 'nt':
            self.skipTest('symlink creation requires Windows privileges; checked on Linux')
        target = self.save_match('b' * 32)
        (self.matches / ('a' * 32 + '.json')).symlink_to(target)
        self.assertEqual(self.request('/matches/' + 'a' * 32)[0], 409)
        self.assertEqual(len(self.request('/matches')[2]['matches']), 1)
        moved = self.matches.rename(Path(self.tmp.name) / 'moved')
        self.matches.symlink_to(moved, target_is_directory=True)
        self.assertEqual(self.request('/matches')[0], 409)
        self.assertEqual(self.request('/matches/' + 'b' * 32)[0], 409)


if __name__ == '__main__':
    unittest.main()
