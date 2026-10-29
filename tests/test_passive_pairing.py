from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from ottv2 import daily_quota, elo_store, local_worker, match_queue
from ottv2.bot_store import load_bot, load_version
from tests.test_local_worker import WorkerFixture


class PassiveFixture(WorkerFixture):
    def setUp(self):
        self.clock = datetime(2026, 10, 23, 16, 59, 59, tzinfo=timezone.utc)
        clock = patch('ottv2.daily_quota.utc_now', side_effect=lambda: self.clock)
        clock.start()
        self.addCleanup(clock.stop)
        super().setUp()
        match_queue.cancel_job(self.db, self.match_id)

    def add_bot(self, identity):
        return self.import_source(identity, load_bot(self.store, 'student01')[0])

    def wait(self, *identities):
        # Seed simultaneous requests without the find command pairing them first.
        with match_queue._write(self.db) as connection:
            for identity in identities:
                connection.execute('INSERT INTO waiting(bot_id,joined_at) VALUES (?,?)',
                                   (identity, self.clock.isoformat()))

    def history(self, first='student01', second='student02', mode='active', state='completed'):
        job = json.loads(json.dumps(self.job))
        job.update(match_id=uuid4().hex, created_at_utc=self.clock.isoformat(),
                   quota_date=daily_quota.vietnam_day(self.clock))
        job['players']['A'].update(bot_id=first, mode='active')
        job['players']['B'].update(bot_id=second, mode=mode)
        with match_queue._write(self.db) as connection:
            connection.execute('INSERT INTO jobs(match_id,state,payload) VALUES (?,?,?)',
                               (job['match_id'], 'pending', json.dumps(job)))
            daily_quota.reserve(connection, job)
            daily_quota.settle(connection, job, used=state == 'completed')
            connection.execute('UPDATE jobs SET state=? WHERE match_id=?', (state, job['match_id']))

    def roles(self, match_id):
        return {player['bot_id']: player['mode'] for player in match_queue.get_job(self.db, match_id)['players'].values()}

    def ratings(self, values):
        with closing(elo_store._connect(self.elo)) as connection:
            connection.execute('BEGIN IMMEDIATE')
            elo_store._check_schema(connection, create=True)
            for identity, rating in values.items():
                connection.execute('INSERT INTO ratings(bot_id,rating) VALUES (?,?)', (identity, rating))
            connection.execute('COMMIT')


class PassivePairingTests(PassiveFixture, unittest.TestCase):
    def test_wait_then_dispatch_reserves_correct_roles_and_worker_scores_both_once(self):
        self.assertEqual(match_queue.find_match(self.db, 'student01')['state'], 'waiting')
        self.assertEqual(daily_quota.status(self.db, 'student01')['active']['remaining'], 3)
        paired = match_queue.dispatch(self.db)
        self.match_id = paired['match_id']
        self.assertEqual(self.roles(self.match_id), {'student01': 'active', 'student02': 'passive'})
        self.assertEqual(daily_quota.status(self.db, 'student01')['active']['reserved'], 1)
        self.assertEqual(daily_quota.status(self.db, 'student02')['passive']['reserved'], 1)
        self.assertEqual(daily_quota.status(self.db, 'student02')['active']['remaining'], 3)
        self.assertFalse(match_queue.find_match(self.db, 'student02')['created'])
        local_worker.run_once(self.db, self.results, self.fake_runner)
        self.assert_completed()
        self.assertEqual(daily_quota.status(self.db, 'student01')['active']['used'], 1)
        self.assertEqual(daily_quota.status(self.db, 'student02')['passive']['used'], 1)
        self.assertEqual(local_worker.run_once(self.db, self.results, self.fake_runner)['state'], 'idle')

    def test_no_request_does_not_create_passive_passive_games(self):
        self.assertEqual(match_queue.dispatch(self.db), {'state': 'idle'})
        self.assertFalse(self.elo.exists())
        self.assertEqual(daily_quota.status(self.db, 'student02')['passive']['reserved'], 0)

    def test_active_pair_precedes_a_closer_passive_opponent(self):
        self.add_bot('student03')
        self.ratings({'student01': 1000, 'student02': 1800, 'student03': 1001})
        self.wait('student01', 'student02')
        paired = match_queue.dispatch(self.db)
        self.assertEqual(self.roles(paired['match_id']), {'student01': 'active', 'student02': 'active'})
        self.assertEqual(match_queue.status(self.db, 'student03')['state'], 'idle')

    def test_passive_choice_uses_nearest_available_elo_band(self):
        self.add_bot('student03')
        self.ratings({'student01': 1000, 'student02': 1390, 'student03': 1090})
        match_queue.find_match(self.db, 'student01')
        paired = match_queue.dispatch(self.db)
        self.assertEqual(self.roles(paired['match_id']), {'student01': 'active', 'student03': 'passive'})

    def test_active_nearby_repeat_precedes_fresh_distant_opponent(self):
        self.add_bot('student03')
        self.history()
        self.ratings({'student01': 1000, 'student02': 1001, 'student03': 1800})
        self.wait('student02', 'student03')
        paired = match_queue.find_match(self.db, 'student01')
        self.assertEqual(self.roles(paired['match_id']), {'student01': 'active', 'student02': 'active'})

    def test_both_active_can_repeat_even_after_a_passive_game(self):
        self.history(mode='passive')
        match_queue.find_match(self.db, 'student01')
        paired = match_queue.find_match(self.db, 'student02')
        self.assertEqual(set(self.roles(paired['match_id']).values()), {'active'})

    def test_active_active_history_does_not_consume_passive_invitation(self):
        self.history()
        match_queue.find_match(self.db, 'student02')
        paired = match_queue.dispatch(self.db)
        self.assertEqual(self.roles(paired['match_id']), {'student02':'active','student01':'passive'})

    def test_passive_history_allows_reverse_invitation_on_same_day(self):
        self.history(mode='passive')
        match_queue.find_match(self.db, 'student02')
        paired = match_queue.dispatch(self.db)
        self.assertEqual(self.roles(paired['match_id']), {'student02': 'active', 'student01': 'passive'})
        self.assertEqual(match_queue.get_job(self.db, paired['match_id'])['quota_date'], '2026-10-23')
        self.assertEqual(daily_quota.status(self.db,'student02')['passive']['used'],1)
        self.assertEqual(daily_quota.status(self.db,'student02')['active']['reserved'],1)
        self.assertEqual(daily_quota.status(self.db,'student01')['passive']['reserved'],1)

    def test_same_invitation_direction_blocked_until_next_vietnam_day(self):
        self.history(mode='passive')
        match_queue.find_match(self.db,'student01')
        self.assertEqual(match_queue.dispatch(self.db)['state'],'waiting')
        self.clock += timedelta(seconds=1)
        paired=match_queue.dispatch(self.db)
        self.assertEqual(self.roles(paired['match_id']),{'student01':'active','student02':'passive'})

    def test_both_invitation_directions_can_be_used_once_independently(self):
        self.history(mode='passive')
        self.history('student02','student01',mode='passive')
        match_queue.find_match(self.db,'student01')
        self.assertEqual(match_queue.dispatch(self.db)['state'],'waiting')
        match_queue.cancel_request(self.db,'student01')
        match_queue.find_match(self.db,'student02')
        self.assertEqual(match_queue.dispatch(self.db)['state'],'waiting')

    def test_passive_allowance_can_be_used_by_three_different_requesters(self):
        self.add_bot('student03');self.add_bot('student04');self.add_bot('student05')
        for identity in ('student01','student03','student04'):
            self.history(identity,'student02',mode='passive')
        with match_queue._write(self.db) as db:
            self.assertEqual(daily_quota.usage(db,'student02','2026-10-23','passive')['remaining'],0)
            self.assertEqual(match_queue._previous_passive_opponents(db,'student01','2026-10-23'),{'student02'})

    def test_cancelled_and_failed_jobs_release_quota_and_allow_same_pair(self):
        match_queue.find_match(self.db, 'student01')
        first = match_queue.dispatch(self.db)
        match_queue.cancel_job(self.db, first['match_id'])
        match_queue.find_match(self.db, 'student01')
        second = match_queue.dispatch(self.db)
        def failed(*_):
            raise OSError('infrastructure failed')
        self.assertEqual(local_worker.run_once(self.db, self.results, failed)['state'], 'failed')
        self.assertEqual(daily_quota.status(self.db, 'student02')['passive']['remaining'], 3)
        match_queue.find_match(self.db, 'student01')
        third = match_queue.dispatch(self.db)
        self.assertEqual(self.roles(third['match_id']), {'student01': 'active', 'student02': 'passive'})
        self.assertEqual(len({first['match_id'], second['match_id'], third['match_id']}), 3)
        self.assertFalse(self.elo.exists())

    def test_exhausted_passive_allowance_does_not_prevent_active_pairing(self):
        for _ in range(3):
            self.history('student03', 'student02', mode='passive')
        match_queue.find_match(self.db, 'student01')
        self.assertEqual(match_queue.dispatch(self.db)['state'], 'waiting')
        paired = match_queue.find_match(self.db, 'student02')
        self.assertEqual(set(self.roles(paired['match_id']).values()), {'active'})

    def test_exhausted_active_allowance_does_not_prevent_passive_invitation(self):
        for _ in range(3):
            self.history('student02', 'student03')
        match_queue.find_match(self.db, 'student01')
        paired = match_queue.dispatch(self.db)
        self.assertEqual(self.roles(paired['match_id']), {'student01': 'active', 'student02': 'passive'})
        self.assertEqual(daily_quota.status(self.db, 'student02')['active']['remaining'], 0)
        self.assertEqual(daily_quota.status(self.db, 'student02')['passive']['reserved'], 1)

    def test_busy_bot_is_excluded_even_when_elo_is_closer(self):
        self.add_bot('student03')
        self.add_bot('student04')
        match_queue.find_match(self.db, 'student03')
        busy = match_queue.find_match(self.db, 'student04')['match_id']
        self.ratings({'student01': 1000, 'student02': 1800, 'student03': 1001, 'student04': 1002})
        match_queue.find_match(self.db, 'student01')
        paired = match_queue.dispatch(self.db)
        self.assertEqual(self.roles(paired['match_id']), {'student01': 'active', 'student02': 'passive'})
        self.assertEqual(match_queue.status(self.db, 'student03')['match_id'], busy)

    def test_exhausted_waiter_is_never_silently_treated_as_passive(self):
        for _ in range(3):
            self.history('student02', 'student03')
        self.wait('student02')
        match_queue.find_match(self.db, 'student01')
        self.assertEqual(match_queue.dispatch(self.db)['state'], 'waiting')
        self.assertEqual(daily_quota.status(self.db, 'student02')['passive']['reserved'], 0)

    def test_corrupt_or_unvalidated_passive_bots_and_junk_are_skipped(self):
        self.add_bot('student03')
        self.add_bot('student04')
        bot = load_bot(self.store, 'student02')[1]
        (self.store / 'student02/versions' / (bot['version'] + '.py')).write_bytes(b'corrupt')
        current = self.store / 'student03/current.json'
        record = json.loads(current.read_bytes())
        record.pop('validation')
        current.write_text(json.dumps(record), encoding='utf-8')
        (self.store / 'INVALID').mkdir()
        (self.store / 'readme.txt').write_text('not a bot', encoding='utf-8')
        match_queue.find_match(self.db, 'student01')
        paired = match_queue.dispatch(self.db)
        self.assertEqual(self.roles(paired['match_id']), {'student01': 'active', 'student04': 'passive'})

    def test_current_is_read_at_dispatch_and_later_update_does_not_change_job(self):
        match_queue.find_match(self.db, 'student01')
        current = self.import_source('student02', b'# second version\npass\n')
        paired = match_queue.dispatch(self.db)
        job = match_queue.get_job(self.db, paired['match_id'])
        self.import_source('student02', b'# third version\npass\n')
        player = next(p for p in job['players'].values() if p['bot_id'] == 'student02')
        self.assertEqual(player['version'], current['version'])
        self.assertEqual(load_version(self.store, 'student02', player['version']), b'# second version\npass\n')
        self.assertEqual(match_queue.get_job(self.db, paired['match_id']), job)

    def test_second_busy_insert_failure_rolls_back_job_quota_and_waiting_removal(self):
        match_queue.find_match(self.db, 'student01')
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("""CREATE TRIGGER reject_second BEFORE INSERT ON busy
                WHEN (SELECT COUNT(*) FROM busy)>0
                BEGIN SELECT RAISE(ABORT,'storage failed'); END""")
        before = self.db.read_bytes()
        with self.assertRaisesRegex(sqlite3.Error, 'storage failed'):
            match_queue.dispatch(self.db)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(match_queue.status(self.db, 'student01')['state'], 'waiting')
        self.assertEqual(daily_quota.status(self.db, 'student02')['passive']['reserved'], 0)
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute('DROP TRIGGER reject_second')
        self.assertEqual(match_queue.dispatch(self.db)['state'], 'matched')

    def test_concurrent_dispatch_creates_one_job_and_reserves_once(self):
        match_queue.find_match(self.db, 'student01')
        barrier = Barrier(5)
        def dispatch(_):
            barrier.wait(timeout=5)
            return match_queue.dispatch(self.db)
        with ThreadPoolExecutor(max_workers=5) as pool:
            answers = list(pool.map(dispatch, range(5)))
        self.assertEqual(sum(answer['state'] == 'matched' for answer in answers), 1)
        self.assertEqual(daily_quota.status(self.db, 'student02')['passive']['reserved'], 1)

    def test_dispatch_racing_opponents_find_never_assigns_a_person_twice(self):
        match_queue.find_match(self.db, 'student01')
        barrier = Barrier(2)
        def run(dispatch):
            barrier.wait(timeout=5)
            return match_queue.dispatch(self.db) if dispatch else match_queue.find_match(self.db, 'student02')
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(run, (True, False)))
        first = match_queue.status(self.db, 'student01')['match_id']
        self.assertEqual(match_queue.status(self.db, 'student02')['match_id'], first)
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM jobs WHERE state='pending'").fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM busy').fetchone()[0], 2)
        quota = daily_quota.status(self.db, 'student02')
        self.assertEqual(quota['active']['reserved'] + quota['passive']['reserved'], 1)

    def test_dispatch_captures_one_date_for_matching_and_job(self):
        match_queue.find_match(self.db, 'student01')
        with patch('ottv2.daily_quota.utc_now', side_effect=[self.clock, self.clock + timedelta(seconds=1)]) as clock:
            paired = match_queue.dispatch(self.db)
        self.assertEqual(clock.call_count, 1)
        self.assertEqual(match_queue.get_job(self.db, paired['match_id'])['quota_date'], '2026-10-23')

    def test_no_eligible_opponent_keeps_waiting_position_and_does_not_consume(self):
        (self.store / 'student02/current.json').unlink()
        original = match_queue.find_match(self.db, 'student01')
        for _ in range(2):
            self.assertEqual(match_queue.dispatch(self.db), {'state': 'waiting', 'waiting_requests': 1})
        self.assertEqual(match_queue.status(self.db, 'student01')['joined_at_utc'], original['joined_at_utc'])
        self.assertEqual(daily_quota.status(self.db, 'student01')['active']['remaining'], 3)

    def test_storage_error_rolls_back_instead_of_looking_like_no_opponent(self):
        match_queue.find_match(self.db, 'student01')
        before = self.db.read_bytes()
        def load(store, identity):
            if identity == 'student02':
                raise OSError('disk failed')
            return load_bot(store, identity)
        with patch('ottv2.match_queue.load_bot', side_effect=load):
            with self.assertRaisesRegex(OSError, 'disk failed'):
                match_queue.dispatch(self.db)
        self.assertEqual(self.db.read_bytes(), before)

    def test_invalid_oldest_waiter_does_not_block_other_request_or_lose_its_position(self):
        self.add_bot('student03')
        self.wait('student01', 'student02')
        (self.store / 'student01/current.json').unlink()
        paired = match_queue.dispatch(self.db)
        self.assertEqual(self.roles(paired['match_id']), {'student02': 'active', 'student03': 'passive'})
        self.assertEqual(match_queue.status(self.db, 'student01')['state'], 'waiting')


@unittest.skipUnless(sys.platform.startswith('linux'), 'real passive match requires Linux/Isolate')
class PassivePairingIntegrationTests(PassiveFixture, unittest.TestCase):
    real = True

    def test_real_passive_match_pinned_source_quota_elo_and_repeat_block(self):
        match_queue.find_match(self.db, 'student01')
        self.match_id = match_queue.dispatch(self.db)['match_id']
        job = match_queue.get_job(self.db, self.match_id)
        self.assertEqual(self.roles(self.match_id), {'student01': 'active', 'student02': 'passive'})
        crash = b'''import json,sys
print(json.dumps({'protocol_version':1,'type':'ready'}),flush=True)
for line in sys.stdin:
    raise RuntimeError('new current must not replace pinned passive bot')
'''
        self.import_source('student02', crash)
        self.map.unlink()
        self.assertEqual(local_worker.run_once(self.db, self.results)['state'], 'completed')
        completed = self.assert_completed()
        record = json.loads(Path(completed['result_path']).read_bytes())
        self.assertNotEqual(record['result']['reason'], 'runtime_error')
        for side in ('A', 'B'):
            self.assertEqual(record['bot_sources'][side]['version'], job['players'][side]['version'])
        self.assertEqual(daily_quota.status(self.db, 'student01')['active']['used'], 1)
        self.assertEqual(daily_quota.status(self.db, 'student02')['passive']['used'], 1)
        match_queue.find_match(self.db, 'student01')
        self.assertEqual(match_queue.dispatch(self.db)['state'], 'waiting')
        self.assertTrue(all(not (local_worker.BOX_ROOT / str(box)).exists() for box in (0, 1)))
