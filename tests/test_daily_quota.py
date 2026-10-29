from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from ottv2 import daily_quota, local_worker, match_queue
from tests.test_local_worker import WorkerFixture


class DailyQuotaTests(WorkerFixture, unittest.TestCase):
    def setUp(self):
        self.clock = datetime(2026, 10, 23, 16, 59, 59, tzinfo=timezone.utc)
        clock = patch('ottv2.daily_quota.utc_now', side_effect=lambda: self.clock)
        clock.start()
        self.addCleanup(clock.stop)
        super().setUp()

    def quota(self, identity='student01', now=None):
        return daily_quota.status(self.db, identity, now)

    def add_job(self, mode_b='active', completed=False, identities=('student01', 'student02')):
        job = json.loads(json.dumps(self.job))
        for key in ('state', 'attempts', 'result_path', 'last_error', 'elo_receipt'):
            job.pop(key, None)
        job.update(match_id=uuid4().hex, created_at_utc=self.clock.isoformat(),
                   quota_date=daily_quota.vietnam_day(self.clock))
        for side, identity in zip(('A', 'B'), identities):
            job['players'][side]['bot_id'] = identity
        job['players']['B']['mode'] = mode_b
        with match_queue._write(self.db) as connection:
            connection.execute('INSERT INTO jobs(match_id,state,payload) VALUES (?,?,?)',
                               (job['match_id'], 'pending', json.dumps(job)))
            daily_quota.reserve(connection, job)
            if completed:
                daily_quota.settle(connection, job, used=True)
                connection.execute("UPDATE jobs SET state='completed' WHERE match_id=?", (job['match_id'],))
        return job

    def test_vietnam_midnight_and_aware_time_validation(self):
        self.assertEqual(daily_quota.vietnam_day(self.clock), '2026-10-23')
        self.assertEqual(daily_quota.vietnam_day(self.clock + timedelta(seconds=1)), '2026-10-24')
        for value in (datetime(2026, 10, 23), '2026-10-23'):
            with self.assertRaises(ValueError):
                daily_quota.vietnam_day(value)

    def test_waiting_and_cancelling_request_do_not_reserve_or_use_quota(self):
        match_queue.cancel_job(self.db, self.match_id)
        match_queue.find_match(self.db, 'student01')
        self.assertEqual(self.quota()['active'], {'limit': 3, 'used': 0, 'reserved': 0, 'remaining': 3})
        match_queue.cancel_request(self.db, 'student01')
        self.assertEqual(self.quota()['active']['remaining'], 3)

    def test_pair_reserves_once_and_duplicate_find_changes_nothing(self):
        before = self.quota()
        self.assertEqual(before['active'], {'limit': 3, 'used': 0, 'reserved': 1, 'remaining': 2})
        self.assertEqual(before['passive']['remaining'], 3)
        match_queue.find_match(self.db, 'student01')
        with match_queue._write(self.db) as connection:
            daily_quota.reserve(connection, self.job)
        self.assertEqual(self.quota(), before)

    def test_pairing_captures_one_time_for_checks_and_creation_across_midnight(self):
        match_queue.cancel_job(self.db, self.match_id)
        match_queue.find_match(self.db, 'student01')
        with patch('ottv2.daily_quota.utc_now', side_effect=[self.clock, self.clock + timedelta(seconds=1)]) as clock:
            paired = match_queue.find_match(self.db, 'student02')
        self.assertEqual(clock.call_count, 1)
        self.assertEqual(match_queue.get_job(self.db, paired['match_id'])['quota_date'], '2026-10-23')

    def test_pending_cancellation_releases_both_only_once(self):
        match_queue.cancel_job(self.db, self.match_id)
        match_queue.cancel_job(self.db, self.match_id)
        for identity in ('student01', 'student02'):
            self.assertEqual(self.quota(identity)['active']['remaining'], 3)
        self.assertFalse(self.elo.exists())

    def test_completion_and_repeated_worker_count_one_used_game(self):
        local_worker.run_once(self.db, self.results, self.fake_runner)
        self.assertEqual(self.quota()['active'], {'limit': 3, 'used': 1, 'reserved': 0, 'remaining': 2})
        before = self.quota()
        self.assertEqual(local_worker.run_once(self.db, self.results, self.fake_runner)['state'], 'idle')
        self.assertEqual(self.quota(), before)

    def test_system_retries_hold_one_slot_and_completion_uses_one(self):
        calls = []
        def runner(job, output):
            calls.append(job['match_id'])
            self.assertEqual(self.quota()['active']['reserved'], 1)
            if len(calls) < 3:
                raise OSError('transient infrastructure failure')
            self.fake_runner(job, output)
        local_worker.run_once(self.db, self.results, runner)
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.quota()['active']['used'], 1)
        self.assertEqual(self.quota()['active']['reserved'], 0)

    def test_three_system_failures_release_slot_and_do_not_score(self):
        def failed(*_):
            raise OSError('infrastructure failed')
        self.assertEqual(local_worker.run_once(self.db, self.results, failed)['state'], 'failed')
        self.assertEqual(self.quota()['active'], {'limit': 3, 'used': 0, 'reserved': 0, 'remaining': 3})
        self.assertFalse(self.elo.exists())

    def test_queue_commit_failure_rolls_back_quota_then_recovery_does_not_charge_twice(self):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("""CREATE TRIGGER reject_completed BEFORE UPDATE ON jobs
                WHEN NEW.state='completed' BEGIN SELECT RAISE(ABORT,'queue failure'); END""")
        with self.assertRaises(sqlite3.Error):
            local_worker.run_once(self.db, self.results, self.fake_runner)
        self.assertTrue(self.elo.exists())
        self.assertEqual(self.quota()['active']['used'], 0)
        self.assertEqual(self.quota()['active']['reserved'], 1)
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute('DROP TRIGGER reject_completed')
        result = local_worker.run_once(self.db, self.results, lambda *_: self.fail('must reuse log'))
        self.assertFalse(result['elo_applied'])
        self.assertEqual(self.quota()['active']['used'], 1)
        self.assertEqual(self.quota()['active']['remaining'], 2)

    def test_midnight_preserves_old_reservation_but_starts_new_daily_bucket(self):
        old = self.clock
        self.clock += timedelta(seconds=1)
        today = self.quota()
        self.assertEqual(today['day'], '2026-10-24')
        self.assertEqual(today['active']['remaining'], 3)
        self.assertEqual(today['reserved_other_days'], 1)
        local_worker.run_once(self.db, self.results, self.fake_runner)
        self.assertEqual(self.quota(now=old)['active']['used'], 1)
        self.assertEqual(self.quota()['active']['used'], 0)
        self.assertEqual(self.quota()['active']['remaining'], 3)
        self.assertEqual(self.quota()['reserved_other_days'], 0)

    def test_third_active_game_blocks_fourth_and_does_not_use_passive_bucket(self):
        match_queue.cancel_job(self.db, self.match_id)
        for _ in range(3):
            self.add_job(completed=True)
        self.assertEqual(self.quota()['active']['used'], 3)
        self.assertEqual(self.quota()['active']['remaining'], 0)
        self.assertEqual(self.quota()['passive']['remaining'], 3)
        with self.assertRaisesRegex(ValueError, 'quota exhausted'):
            match_queue.find_match(self.db, 'student01')
        self.assertEqual(match_queue.status(self.db, 'student01')['state'], 'idle')

    def test_third_passive_game_blocks_fourth_without_taking_active_slots(self):
        match_queue.cancel_job(self.db, self.match_id)
        for _ in range(3):
            self.add_job(mode_b='passive', completed=True)
        before = self.quota('student02')
        self.assertEqual(before['passive']['used'], 3)
        self.assertEqual(before['passive']['remaining'], 0)
        self.assertEqual(before['active']['remaining'], 3)
        with self.assertRaisesRegex(ValueError, 'passive quota exhausted'):
            self.add_job(mode_b='passive', identities=('student03', 'student02'))
        self.assertEqual(self.quota('student02'), before)
        self.assertEqual(self.quota()['active']['reserved'], 0)
        self.assertEqual(match_queue.find_match(self.db, 'student02')['state'], 'waiting')

    def test_concurrent_last_slot_is_reserved_by_one_transaction(self):
        match_queue.cancel_job(self.db, self.match_id)
        for _ in range(2):
            self.add_job(completed=True)
        barrier = Barrier(5)
        def reserve(_):
            barrier.wait(timeout=5)
            try:
                self.add_job()
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(reserve, range(5)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.quota()['active'], {'limit': 3, 'used': 2, 'reserved': 1, 'remaining': 0})

    def test_exhausted_waiter_is_skipped_and_can_be_matched_after_midnight(self):
        match_queue.cancel_job(self.db, self.match_id)
        for _ in range(3):
            self.add_job(completed=True, identities=('student01', 'student03'))
        with match_queue._write(self.db) as connection:
            connection.execute("INSERT INTO waiting(bot_id,joined_at) VALUES ('student01','old request')")
        waiting = match_queue.find_match(self.db, 'student02')
        self.assertEqual(waiting['state'], 'waiting')
        self.assertFalse(match_queue.find_match(self.db, 'student02')['created'])
        self.clock += timedelta(seconds=1)
        matched = match_queue.find_match(self.db, 'student02')
        self.assertEqual(matched['state'], 'matched')
        self.assertEqual(match_queue.get_job(self.db, matched['match_id'])['quota_date'], '2026-10-24')

    def test_v2_read_does_not_write_and_upgrade_does_not_reset_old_used_or_reserved(self):
        self.add_job(mode_b='passive', completed=True, identities=('student03', 'student04'))
        with match_queue._write(self.db) as connection:
            connection.execute('DROP TABLE quota_entries')
            connection.execute('PRAGMA user_version=2')
        before = self.db.read_bytes()
        self.assertEqual(self.quota()['active']['reserved'], 1)
        self.assertEqual(self.quota('student04')['passive']['used'], 1)
        self.assertEqual(self.db.read_bytes(), before)
        match_queue.cancel_job(self.db, self.match_id)
        self.assertEqual(self.quota()['active']['remaining'], 3)
        self.assertEqual(self.quota('student04')['passive']['used'], 1)
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_legacy_over_limit_history_is_preserved_and_blocks_new_games(self):
        with match_queue._write(self.db) as connection:
            template = json.loads(connection.execute('SELECT payload FROM jobs').fetchone()[0])
            for _ in range(12):
                template['match_id'] = uuid4().hex
                connection.execute('INSERT INTO jobs(match_id,state,payload) VALUES (?,?,?)',
                                   (template['match_id'], 'completed', json.dumps(template)))
            connection.execute('DROP TABLE quota_entries')
            connection.execute('PRAGMA user_version=2')
        self.assertEqual(self.quota()['active']['used'], 12)
        match_queue.cancel_job(self.db, self.match_id)
        self.assertEqual(self.quota()['active']['used'], 12)
        self.assertEqual(self.quota()['active']['remaining'], 0)
        with self.assertRaises(ValueError):
            match_queue.find_match(self.db, 'student01')

    def test_read_only_missing_database_and_settled_entry_protection(self):
        missing = self.root / 'missing' / 'queue.sqlite3'
        self.assertEqual(daily_quota.status(missing, 'student01')['active']['remaining'], 3)
        self.assertFalse(missing.parent.exists())
        before = self.db.read_bytes()
        self.quota()
        self.assertEqual(self.db.read_bytes(), before)
        with self.assertRaises(ValueError):
            daily_quota.status(self.db, '../bad')
        match_queue.cancel_job(self.db, self.match_id)
        with self.assertRaisesRegex(ValueError, 'settled'):
            with match_queue._write(self.db) as connection:
                daily_quota.reserve(connection, self.job)

    def test_passive_job_completion_uses_passive_allowance_and_scores_both(self):
        match_queue.cancel_job(self.db, self.match_id)
        job = self.add_job(mode_b='passive')
        self.match_id = job['match_id']
        with match_queue._write(self.db) as connection:
            for identity in ('student01', 'student02'):
                connection.execute('INSERT INTO busy VALUES (?,?)', (identity, self.match_id))
        local_worker.run_once(self.db, self.results, self.fake_runner)
        self.assert_completed()
        self.assertEqual(self.quota('student01')['active']['used'], 1)
        self.assertEqual(self.quota('student02')['passive']['used'], 1)
        self.assertEqual(self.quota('student02')['active']['used'], 0)
