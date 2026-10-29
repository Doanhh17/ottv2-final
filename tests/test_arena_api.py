from http.client import HTTPConnection
import json
from threading import Thread
import unittest

from ottv2 import local_api, local_worker, match_queue, queue_admin
from tests.test_local_worker import WorkerFixture


class ArenaApiTests(WorkerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.server = local_api.create_server(self.store, 0, self.results, self.db)
        self.thread = Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.thread.join(2)
        self.server.server_close()

    def request(self, path, method='GET'):
        client = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            client.request(method, path)
            answer = client.getresponse()
            data = answer.read()
            return answer.status, dict(answer.getheaders()), json.loads(data) if data else None
        finally:
            client.close()

    def test_queue_controls_counts_and_map_do_not_leak_paths_or_blocked_ids(self):
        queue_admin.block_bot(self.db, 'student03', reason='private review')
        queue_admin.set_paused(self.db, True, 'maintenance')
        queue_admin.set_updates_locked(self.db, True)
        before = self.db.read_bytes()
        code, headers, data = self.request('/queue')
        self.assertEqual(code, 200)
        self.assertTrue(data['control']['paused'])
        self.assertTrue(data['control']['updates_locked'])
        self.assertEqual(data['blocked_bot_count'], 1)
        self.assertEqual(data['jobs'], {'pending': 1})
        self.assertEqual(data['busy_players'], 2)
        self.assertNotIn('student03', json.dumps(data))
        self.assertNotIn(str(self.store), json.dumps(data))
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(self.db.read_bytes(), before)

    def test_quota_reads_reservation_and_completed_use_from_worker(self):
        code, _, data = self.request('/quota/student01')
        self.assertEqual(code, 200)
        self.assertEqual(data['active']['reserved'], 1)
        self.assertEqual(data['passive']['remaining'], 3)
        local_worker.run_once(self.db, self.results, self.fake_runner)
        self.assertEqual(self.request('/quota/student01')[2]['active']['used'], 1)

    def test_ratings_empty_before_first_game_and_ordered_after_completion(self):
        self.assertEqual(self.request('/ratings')[2], {'ratings': [], 'limit': 20})
        self.assertFalse(self.elo.exists())
        local_worker.run_once(self.db, self.results, self.fake_runner)
        data = self.request('/ratings')[2]
        self.assertEqual(len(data['ratings']), 2)
        self.assertEqual([row['played'] for row in data['ratings']], [1, 1])
        self.assertGreaterEqual(data['ratings'][0]['rating'], data['ratings'][1]['rating'])

    def test_head_and_write_methods_do_not_mutate_queue(self):
        before = self.db.read_bytes()
        for path in ('/queue', '/quota/student01', '/ratings'):
            self.assertEqual(self.request(path, 'HEAD')[0], 200)
            for method in ('POST', 'PATCH', 'PUT', 'DELETE'):
                self.assertEqual(self.request(path, method)[0], 405)
        self.assertEqual(self.db.read_bytes(), before)

    def test_invalid_ids_queries_and_unknown_routes_are_rejected(self):
        for path in ('/quota/../bad', '/quota/A', '/quota/student01%2fmain.py', '/quota/'):
            self.assertEqual(self.request(path)[0], 400)
        self.assertEqual(self.request('/queue?command=resume')[0], 400)
        self.assertEqual(self.request('/queue/resume')[0], 404)

    def test_unconfigured_queue_does_not_create_databases(self):
        self.server.queue = None
        for path in ('/queue', '/quota/student01', '/ratings'):
            self.assertEqual(self.request(path)[0], 503)
        self.assertFalse(self.elo.exists())

    def test_corrupt_queue_or_ratings_reports_conflict_without_path_leak(self):
        local_worker.run_once(self.db, self.results, self.fake_runner)
        self.elo.write_bytes(b'broken db')
        code, _, data = self.request('/ratings')
        self.assertEqual(code, 409)
        self.assertNotIn(str(self.elo), json.dumps(data))
        self.assertEqual(self.request('/health')[0], 200)

    def test_server_refuses_mismatched_store_and_missing_queue(self):
        other = self.root / 'other-store'
        other.mkdir()
        with self.assertRaisesRegex(ValueError, 'does not match'):
            local_api.create_server(other, 0, self.results, self.db)
        missing = self.root / 'missing.sqlite3'
        with self.assertRaises(ValueError):
            local_api.create_server(self.store, 0, self.results, missing)
        self.assertFalse(missing.exists())
