from contextlib import closing
import json
from pathlib import Path
import sqlite3
import unittest

from ottv2 import daily_quota, elo_store, local_runner, local_worker, match_queue, queue_admin
from tests.test_local_worker import WorkerFixture


class QueueAdminTests(WorkerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.new_map = Path(__file__).resolve().parent.parent / 'maps/pool-examples/open.json'

    def test_status_read_only_includes_existing_jobs_and_default_controls(self):
        before = self.db.read_bytes()
        status = queue_admin.status(self.db)
        self.assertFalse(status['control']['paused'])
        self.assertEqual(status['busy_players'], 2)
        self.assertEqual(status['jobs'], {'pending': 1})
        self.assertEqual(self.db.read_bytes(), before)

    def test_pause_keeps_existing_jobs_and_matched_find_is_idempotent(self):
        queue_admin.set_paused(self.db, True, 'map maintenance')
        self.assertEqual(match_queue.find_match(self.db, 'student01')['match_id'], self.match_id)
        self.assertEqual(match_queue.dispatch(self.db), {'state': 'paused'})
        self.assertEqual(local_runner.run_loop(self.db, self.results, max_cycles=1, runner=self.fake_runner)['completed'], 1)
        self.assert_completed()

    def test_pause_blocks_new_find_without_touching_quota_or_bot_store(self):
        match_queue.cancel_job(self.db, self.match_id)
        queue_admin.set_paused(self.db, True)
        before = self.db.read_bytes()
        with self.assertRaisesRegex(ValueError, 'paused'):
            match_queue.find_match(self.db, 'student01')
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(daily_quota.status(self.db, 'student01')['active']['remaining'], 3)

    def test_pause_resume_preserves_waiting_position_and_allows_cancellation(self):
        match_queue.cancel_job(self.db, self.match_id)
        waiting = match_queue.find_match(self.db, 'student01')
        queue_admin.set_paused(self.db, True)
        self.assertEqual(match_queue.status(self.db, 'student01')['joined_at_utc'], waiting['joined_at_utc'])
        queue_admin.set_paused(self.db, False)
        self.assertEqual(match_queue.status(self.db, 'student01')['joined_at_utc'], waiting['joined_at_utc'])
        queue_admin.set_paused(self.db, True)
        self.assertTrue(match_queue.cancel_request(self.db, 'student01')['cancelled'])

    def test_map_change_requires_pause_and_preserves_original_configuration_on_error(self):
        before = self.db.read_bytes()
        with self.assertRaisesRegex(ValueError, 'pause'):
            queue_admin.change_map(self.db, self.new_map)
        self.assertEqual(self.db.read_bytes(), before)

    def test_invalid_map_does_not_cancel_waiting_or_replace_map(self):
        match_queue.cancel_job(self.db, self.match_id)
        match_queue.find_match(self.db, 'student01')
        queue_admin.set_paused(self.db, True)
        broken = self.root / 'bad-map.json'
        broken.write_text('{}', encoding='utf-8')
        before = self.db.read_bytes()
        with self.assertRaises(ValueError):
            queue_admin.change_map(self.db, broken)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(match_queue.status(self.db, 'student01')['state'], 'waiting')

    def test_map_change_cancels_only_waiting_and_keeps_pinned_pending_job(self):
        with match_queue._write(self.db) as connection:
            connection.execute("INSERT INTO waiting(bot_id,joined_at) VALUES ('student03','old')")
        queue_admin.set_paused(self.db, True)
        answer = queue_admin.change_map(self.db, self.new_map)
        self.assertEqual(answer['cancelled_waiting'], 1)
        self.assertEqual(match_queue.status(self.db, 'student03')['state'], 'idle')
        self.assertEqual(match_queue.get_job(self.db, self.match_id), self.job)
        self.assertEqual(daily_quota.status(self.db, 'student01')['active']['reserved'], 1)
        self.assertTrue(queue_admin.status(self.db)['control']['paused'])

    def test_old_job_completes_after_map_change_and_new_job_uses_new_board(self):
        queue_admin.set_paused(self.db, True)
        queue_admin.change_map(self.db, self.new_map)
        local_worker.run_once(self.db, self.results, self.fake_runner)
        old_record = json.loads((self.results / (self.match_id + '.json')).read_bytes())
        self.assertEqual(old_record['initial_state'], self.job['initial_state'])
        before_elo = elo_store.standings(self.elo)
        queue_admin.set_paused(self.db, False)
        match_queue.find_match(self.db, 'student01')
        paired = match_queue.find_match(self.db, 'student02')
        job = match_queue.get_job(self.db, paired['match_id'])
        self.assertEqual(job['map']['name'], 'open')
        self.assertEqual(job['initial_state']['obstacles'], [])
        self.assertEqual(job['initial_state']['bombs'], [])
        self.assertEqual(elo_store.standings(self.elo), before_elo)
        self.assertEqual(daily_quota.status(self.db, 'student01')['active']['used'], 1)

    def test_identical_map_change_is_idempotent_and_does_not_cancel_new_waiters(self):
        match_queue.cancel_job(self.db, self.match_id)
        queue_admin.set_paused(self.db, True)
        queue_admin.change_map(self.db, self.new_map)
        with match_queue._write(self.db) as connection:
            connection.execute("INSERT INTO waiting(bot_id,joined_at) VALUES ('student01','old')")
        answer = queue_admin.change_map(self.db, self.new_map)
        self.assertFalse(answer['changed'])
        self.assertEqual(answer['cancelled_waiting'], 0)
        self.assertEqual(match_queue.status(self.db, 'student01')['state'], 'waiting')

    def test_failed_waiting_delete_rolls_back_map_change(self):
        match_queue.cancel_job(self.db, self.match_id)
        match_queue.find_match(self.db, 'student01')
        queue_admin.set_paused(self.db, True)
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("CREATE TRIGGER reject_delete BEFORE DELETE ON waiting BEGIN SELECT RAISE(ABORT,'storage failed'); END")
        before = self.db.read_bytes()
        with self.assertRaises(sqlite3.Error):
            queue_admin.change_map(self.db, self.new_map)
        self.assertEqual(self.db.read_bytes(), before)

    def test_reinitializing_same_configuration_does_not_reset_admin_pause(self):
        queue_admin.set_paused(self.db, True, 'maintenance')
        self.assertFalse(match_queue.initialize(self.db, self.store, self.map, self.elo)['initialized'])
        self.assertTrue(queue_admin.status(self.db)['control']['paused'])

    def test_invalid_controls_and_missing_database_fail_without_reset(self):
        for paused, reason in ((1, ''), (True, 'x' * 201), (False, None)):
            with self.assertRaises(ValueError):
                queue_admin.set_paused(self.db, paused, reason)
        missing = self.root / 'missing.sqlite3'
        with self.assertRaises(ValueError):
            queue_admin.status(missing)
        self.assertFalse(missing.exists())
        with match_queue._write(self.db) as connection:
            config = match_queue._config(connection)
            config['control'] = {'paused': 'no'}
            connection.execute('UPDATE config SET data=?', (json.dumps(config),))
        with self.assertRaises(ValueError):
            queue_admin.status(self.db)
