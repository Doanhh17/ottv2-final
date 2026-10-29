from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3
from threading import Event
import unittest
from unittest.mock import patch

from ottv2 import daily_quota, github_update, local_worker, match_queue, queue_admin, update_gate
from tests.test_local_worker import WorkerFixture


class UpdateAdmissionTests(WorkerFixture, unittest.TestCase):
    def request(self, root=None):
        return github_update.update_from_github(self.store if root is None else root, 'student01',
                                               'https://github.com/owner/repo', queue_database=self.db)

    def test_locked_request_does_not_download_or_consume_cooldown(self):
        queue_admin.set_updates_locked(self.db, True)
        current = (self.store / 'student01/current.json').read_bytes()
        with patch('ottv2.github_update.download_repository') as download:
            with self.assertRaisesRegex(ValueError, 'locked'):
                self.request()
        download.assert_not_called()
        self.assertFalse((self.store / 'student01/update-request.json').exists())
        self.assertFalse((self.store / 'student01/.request.lock').exists())
        self.assertEqual((self.store / 'student01/current.json').read_bytes(), current)

    def test_unlock_allows_request_and_failure_still_consumes_three_minutes(self):
        queue_admin.set_updates_locked(self.db, True)
        queue_admin.set_updates_locked(self.db, False)
        with patch('ottv2.update_gate.time.time', return_value=1000), patch('ottv2.github_update.download_repository', side_effect=OSError('network failed')) as download:
            with self.assertRaises(OSError):
                self.request()
            with self.assertRaisesRegex(ValueError, 'cooldown'):
                self.request()
        self.assertEqual(download.call_count, 1)
        state = json.loads((self.store / 'student01/update-request.json').read_bytes())
        self.assertEqual(state['next_allowed_at'], 1180)

    def test_acceptance_transaction_is_released_before_network_work(self):
        data = (self.root / 'bot.zip').read_bytes()
        def download(_):
            # This second writer would fail if admission held the transaction.
            queue_admin.set_updates_locked(self.db, True)
            return data, {'type': 'github', 'repository': 'https://github.com/owner/repo', 'commit': 'a' * 40}
        with patch('ottv2.github_update.download_repository', side_effect=download):
            record = self.request()
        self.assertEqual(record['bot_id'], 'student01')
        self.assertTrue(queue_admin.status(self.db)['control']['updates_locked'])

    def test_store_mismatch_is_refused_before_download_or_cooldown(self):
        other = self.root / 'other-store'
        with patch('ottv2.github_update.download_repository') as download:
            with self.assertRaisesRegex(ValueError, 'does not match'):
                self.request(other)
        download.assert_not_called()
        self.assertFalse((other / 'student01/update-request.json').exists())

    def test_blocked_bot_cannot_update_and_does_not_consume_cooldown(self):
        queue_admin.block_bot(self.db, 'student01', reason='operator review')
        with patch('ottv2.github_update.download_repository') as download:
            with self.assertRaisesRegex(ValueError, 'blocked'):
                self.request()
        download.assert_not_called()
        self.assertFalse((self.store / 'student01/update-request.json').exists())

    def test_block_cancels_only_that_waiting_request_without_charging(self):
        match_queue.cancel_job(self.db, self.match_id)
        match_queue.find_match(self.db, 'student01')
        answer = queue_admin.block_bot(self.db, 'student01')
        self.assertEqual(answer['cancelled_waiting'], 1)
        self.assertEqual(match_queue.status(self.db, 'student01')['state'], 'idle')
        self.assertEqual(daily_quota.status(self.db, 'student01')['active']['remaining'], 3)
        with self.assertRaisesRegex(ValueError, 'blocked'):
            match_queue.find_match(self.db, 'student01')
        queue_admin.block_bot(self.db, 'student01', False)
        self.assertEqual(match_queue.find_match(self.db, 'student01')['state'], 'waiting')

    def test_blocked_bot_is_not_selected_as_passive(self):
        match_queue.cancel_job(self.db, self.match_id)
        queue_admin.block_bot(self.db, 'student02')
        match_queue.find_match(self.db, 'student01')
        self.assertEqual(match_queue.dispatch(self.db)['state'], 'waiting')
        queue_admin.block_bot(self.db, 'student02', False)
        self.assertEqual(match_queue.dispatch(self.db)['state'], 'matched')

    def test_block_does_not_rewrite_pending_job_or_prevent_its_completion(self):
        queue_admin.block_bot(self.db, 'student01')
        self.assertEqual(match_queue.get_job(self.db, self.match_id), self.job)
        self.assertFalse(match_queue.find_match(self.db, 'student01')['created'])
        local_worker.run_once(self.db, self.results, self.fake_runner)
        self.assert_completed()
        with self.assertRaisesRegex(ValueError, 'blocked'):
            match_queue.find_match(self.db, 'student01')

    def test_pause_and_update_lock_are_independent_and_survive_other_admin_changes(self):
        queue_admin.set_updates_locked(self.db, True)
        queue_admin.block_bot(self.db, 'student02', reason='check')
        queue_admin.set_paused(self.db, True)
        queue_admin.change_map(self.db, self.map)
        queue_admin.set_paused(self.db, False)
        controls = queue_admin.status(self.db)['control']
        self.assertTrue(controls['updates_locked'])
        self.assertEqual(controls['blocked_bots'], {'student02': 'check'})
        self.assertFalse(controls['paused'])

    def test_invalid_operator_parameters_and_corrupt_controls_are_refused(self):
        before = self.db.read_bytes()
        for action in (lambda: queue_admin.set_updates_locked(self.db, 1),
                       lambda: queue_admin.block_bot(self.db, '../bad'),
                       lambda: queue_admin.block_bot(self.db, 'student01', reason='x' * 201)):
            with self.assertRaises(ValueError):
                action()
        self.assertEqual(self.db.read_bytes(), before)
        with match_queue._write(self.db) as connection:
            config = match_queue._config(connection)
            config['control'] = {'updates_locked': 'false'}
            connection.execute('UPDATE config SET data=?', (json.dumps(config),))
        with self.assertRaises(ValueError):
            queue_admin.status(self.db)

    def test_acceptance_holds_queue_transaction_until_cooldown_record_is_saved(self):
        entered, finish = Event(), Event()
        original = update_gate.replace_current
        def save(path, record):
            entered.set()
            if not finish.wait(3):
                raise RuntimeError('test timed out')
            return original(path, record)
        def accept():
            with update_gate.accepted_update(self.store, 'student01', admission=queue_admin.update_admission(self.db, self.store, 'student01')):
                return True
        with patch('ottv2.update_gate.replace_current', side_effect=save), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(accept)
            try:
                self.assertTrue(entered.wait(3))
                with closing(sqlite3.connect(self.db, timeout=0)) as connection:
                    with self.assertRaises(sqlite3.OperationalError):
                        connection.execute('BEGIN IMMEDIATE')
            finally:
                finish.set()
            self.assertTrue(future.result(timeout=3))
        queue_admin.set_updates_locked(self.db, True)
