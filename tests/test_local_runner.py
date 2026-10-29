from contextlib import closing
import json
import sqlite3
import sys
from threading import Event
import unittest
from unittest.mock import patch

from ottv2 import daily_quota, local_runner, local_worker, match_queue
from tests.test_local_worker import WorkerFixture


class LocalRunnerTests(WorkerFixture, unittest.TestCase):
    def loop(self, **kwargs):
        return local_runner.run_loop(self.db, self.results, runner=self.fake_runner,
                                     interval=0.1, max_cycles=1, **kwargs)

    def test_existing_job_is_completed_before_another_dispatch(self):
        with patch('ottv2.match_queue.dispatch', side_effect=AssertionError('must finish old job first')):
            self.assertEqual(self.loop()['completed'], 1)
        self.assert_completed()

    def test_waiter_automatically_gets_passive_match_and_result(self):
        match_queue.cancel_job(self.db, self.match_id)
        match_queue.find_match(self.db, 'student01')
        self.assertEqual(self.loop()['completed'], 1)
        self.assertEqual(daily_quota.status(self.db, 'student02')['passive']['used'], 1)

    def test_idle_loop_emits_once_and_stops_at_bounded_cycle_count(self):
        match_queue.cancel_job(self.db, self.match_id)
        events = []
        with patch('threading.Event.wait', return_value=False) as wait:
            answer = local_runner.run_loop(self.db, self.results, max_cycles=3, emit=events.append)
        self.assertEqual(answer, {'cycles': 3, 'completed': 0, 'failed': 0, 'state': 'stopped'})
        self.assertEqual(events, [{'state': 'idle'}])
        self.assertEqual(wait.call_count, 2)

    def test_waiting_loop_does_not_consume_quota_or_spam_identical_state(self):
        match_queue.cancel_job(self.db, self.match_id)
        (self.store / 'student02/current.json').unlink()
        match_queue.find_match(self.db, 'student01')
        events = []
        with patch('threading.Event.wait', return_value=False):
            local_runner.run_loop(self.db, self.results, max_cycles=3, emit=events.append)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['state'], 'waiting')
        self.assertEqual(daily_quota.status(self.db, 'student01')['active']['reserved'], 0)

    def test_stop_event_before_start_does_not_claim_job(self):
        stop = Event()
        stop.set()
        self.assertEqual(self.loop(stop_event=stop)['cycles'], 0)
        self.assertEqual(match_queue.get_job(self.db, self.match_id)['state'], 'pending')

    def test_stop_after_result_keeps_completed_result_and_releases_slots(self):
        stop = Event()
        events = []
        def emit(answer):
            events.append(answer)
            stop.set()
        answer = local_runner.run_loop(self.db, self.results, runner=self.fake_runner, stop_event=stop, emit=emit)
        self.assertEqual(answer['completed'], 1)
        self.assertEqual(len(events), 1)
        self.assert_completed()

    def test_interrupt_before_log_preserves_running_then_recovers(self):
        with self.assertRaises(KeyboardInterrupt):
            local_runner.run_loop(self.db, self.results, max_cycles=1,
                                  runner=lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        self.assertEqual(match_queue.get_job(self.db, self.match_id)['state'], 'running')
        self.assertEqual(daily_quota.status(self.db, 'student01')['active']['reserved'], 1)
        self.assertEqual(self.loop()['completed'], 1)
        self.assert_completed()

    def test_interrupt_after_log_recovers_without_rerunning(self):
        def interrupted(job, output):
            self.fake_runner(job, output)
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            local_runner.run_loop(self.db, self.results, runner=interrupted, max_cycles=1)
        answer = local_runner.run_loop(self.db, self.results, max_cycles=1,
                                       runner=lambda *_: self.fail('reuse saved log'))
        self.assertEqual(answer['completed'], 1)
        self.assert_completed()

    def test_terminal_system_failure_is_reported_then_loop_can_idle(self):
        events = []
        with patch('threading.Event.wait', return_value=False):
            answer = local_runner.run_loop(self.db, self.results, max_cycles=2, emit=events.append,
                                           runner=lambda *_: (_ for _ in ()).throw(OSError('system failed')))
        self.assertEqual(answer['failed'], 1)
        self.assertEqual([event['state'] for event in events], ['failed', 'idle'])
        self.assertFalse(self.elo.exists())

    def test_corrupt_saved_log_stops_loop_for_inspection(self):
        with match_queue._write(self.db) as connection:
            path = self.results / (self.match_id + '.json')
            self.results.mkdir()
            path.write_text('broken json', encoding='utf-8')
            connection.execute('UPDATE jobs SET result_path=? WHERE match_id=?', (str(path), self.match_id))
        with self.assertRaises(ValueError):
            self.loop()
        self.assertEqual(path.read_text(), 'broken json')
        self.assertEqual(match_queue.get_job(self.db, self.match_id)['attempts'], 0)

    def test_invalid_loop_options_do_not_touch_queue(self):
        before = self.db.read_bytes()
        for interval in (0, -1, 61, float('nan'), float('inf'), True):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                local_runner.run_loop(self.db, interval=interval, max_cycles=1)
        for cycles in (0, -1, True, 1.5):
            with self.subTest(cycles=cycles), self.assertRaises(ValueError):
                local_runner.run_loop(self.db, max_cycles=cycles)
        self.assertEqual(self.db.read_bytes(), before)

    def test_loop_does_not_change_pinned_payload_or_resource_limits(self):
        with closing(sqlite3.connect(self.db)) as connection:
            before = connection.execute('SELECT payload FROM jobs').fetchone()[0]
        self.loop()
        with closing(sqlite3.connect(self.db)) as connection:
            after = connection.execute('SELECT payload FROM jobs').fetchone()[0]
        self.assertEqual(json.loads(before), json.loads(after))


@unittest.skipUnless(sys.platform.startswith('linux'), 'real runner requires Linux/Isolate')
class LocalRunnerIntegrationTests(WorkerFixture, unittest.TestCase):
    real = True

    def test_runner_and_manual_worker_share_the_same_global_lock(self):
        with local_worker._worker_lock():
            with self.assertRaisesRegex(RuntimeError, 'another local Worker'):
                local_runner.run_loop(self.db, self.results, max_cycles=1)
        self.assertEqual(match_queue.get_job(self.db, self.match_id)['state'], 'pending')

    def test_real_loop_dispatches_passive_then_completes_and_idles(self):
        match_queue.cancel_job(self.db, self.match_id)
        match_queue.find_match(self.db, 'student01')
        events = []
        answer = local_runner.run_loop(self.db, self.results, interval=0.1, max_cycles=2, emit=events.append)
        self.assertEqual(answer['completed'], 1)
        self.assertEqual([event['state'] for event in events], ['completed', 'idle'])
        self.assertEqual(daily_quota.status(self.db, 'student02')['passive']['used'], 1)
        self.assertTrue(all(not (local_worker.BOX_ROOT / str(box)).exists() for box in (0, 1)))
