from http.client import HTTPConnection
import json
import socket
from threading import Event
import unittest

from ottv2 import accounts, elo_store, local_arena, match_queue
from contextlib import closing
from tests.test_local_worker import WorkerFixture


class LocalArenaTests(WorkerFixture, unittest.TestCase):
    def request(self, port, path, method='GET'):
        connection = HTTPConnection('127.0.0.1', port, timeout=3)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            data = response.read()
            return response.status, json.loads(data) if data else None
        finally:
            connection.close()

    def assert_closed(self, port):
        with socket.socket() as connection:
            connection.settimeout(0.5)
            self.assertNotEqual(connection.connect_ex(('127.0.0.1', port)), 0)

    def test_one_command_serves_read_only_api_then_finishes_existing_job_and_closes(self):
        ports = []
        def emit(answer):
            if answer['state'] == 'started':
                port = int(answer['url'].rsplit(':', 1)[1])
                ports.append(port)
                self.assertEqual(self.request(port, '/queue')[0], 200)
                self.assertEqual(self.request(port, '/quota/student01')[1]['active']['reserved'], 1)
                self.assertEqual(self.request(port, '/queue', 'POST')[0], 405)
        answer = local_arena.run_arena(self.db, self.results, port=0, max_cycles=1, runner=self.fake_runner, emit=emit)
        self.assertEqual(answer['completed'], 1)
        self.assert_completed()
        self.assert_closed(ports[0])

    def test_worker_exception_closes_api_and_keeps_job_for_inspection(self):
        ports = []
        def emit(answer):
            if answer['state'] == 'started':
                ports.append(int(answer['url'].rsplit(':', 1)[1]))
        with self.assertRaises(KeyboardInterrupt):
            local_arena.run_arena(self.db, self.results, port=0, max_cycles=1, emit=emit,
                                  runner=lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        self.assert_closed(ports[0])
        self.assertEqual(match_queue.get_job(self.db, self.match_id)['state'], 'running')

    def test_stopped_before_first_cycle_still_closes_api_without_claim(self):
        stop = Event()
        stop.set()
        self.assertEqual(local_arena.run_arena(self.db, self.results, port=0, stop_event=stop)['cycles'], 0)
        self.assertEqual(match_queue.get_job(self.db, self.match_id)['state'], 'pending')

    def test_missing_queue_is_not_created(self):
        missing = self.root / 'missing.sqlite3'
        with self.assertRaisesRegex(ValueError, 'initialize'):
            local_arena.run_arena(missing, port=0, max_cycles=1)
        self.assertFalse(missing.exists())

    def test_busy_api_port_does_not_claim_any_job(self):
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            with self.assertRaises(OSError):
                local_arena.run_arena(self.db, self.results, port=occupied.getsockname()[1], max_cycles=1)
        self.assertEqual(match_queue.get_job(self.db, self.match_id)['state'], 'pending')

    def assert_local_mode(self):
        with closing(elo_store._connect(self.db,readonly=True)) as connection:
            self.assertNotEqual(match_queue._config(connection).get('execution_mode'),'distributed')
        self.assertEqual(match_queue.get_job(self.db,self.match_id)['state'],'pending')

    def test_distributed_start_with_busy_port_does_not_change_queue_mode(self):
        account_db=self.root/'accounts.sqlite3';accounts.initialize(account_db,self.db)
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1',0));occupied.listen()
            with self.assertRaises(OSError):
                local_arena.run_arena(self.db,account_db=account_db,
                    port=occupied.getsockname()[1],distributed=True,max_cycles=1)
        self.assert_local_mode()

    def test_distributed_start_with_invalid_origin_does_not_change_queue_mode(self):
        account_db=self.root/'accounts.sqlite3';accounts.initialize(account_db,self.db)
        with self.assertRaises(ValueError):
            local_arena.run_arena(self.db,account_db=account_db,port=0,distributed=True,
                                  public_origin='http://example.com',max_cycles=1)
        self.assert_local_mode()

    def test_invalid_loop_option_closes_started_api(self):
        ports = []
        def emit(answer):
            if answer['state'] == 'started':
                ports.append(int(answer['url'].rsplit(':', 1)[1]))
        with self.assertRaises(ValueError):
            local_arena.run_arena(self.db, self.results, port=0, interval=0, emit=emit)
        self.assert_closed(ports[0])
