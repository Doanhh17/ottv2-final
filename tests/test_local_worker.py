from contextlib import closing, nullcontext
import hashlib
import json
from pathlib import Path
import random
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from ottv2 import elo_store, local_worker, match_queue
from ottv2.bot_store import import_zip, load_bot
from ottv2.bots import greedy_bot
from ottv2.engine import Game, Piece
from ottv2.protocol import state_message
from ottv2.result_log import write_result


class WorkerFixture:
    real = False

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db, self.store = self.root / 'queue.sqlite3', self.root / 'bots'
        self.elo, self.results = self.root / 'elo.sqlite3', self.root / 'matches'
        self.map = self.root / 'map.json'
        project = Path(__file__).resolve().parent.parent
        self.map.write_bytes((project / 'maps/nine-piece.json').read_bytes())
        if not self.real:
            for target, effect in (
                    ('ottv2.bot_store.check_ready', lambda source: {'ready': True, 'source_sha256': hashlib.sha256(source).hexdigest()}),
                    ('ottv2.local_worker._worker_lock', lambda: nullcontext())):
                mock = patch(target, side_effect=effect)
                mock.start()
                self.addCleanup(mock.stop)
        source = (project / 'examples/greedy/main.py').read_bytes()
        for identity in ('student01', 'student02'):
            self.import_source(identity, source)
        match_queue.initialize(self.db, self.store, self.map, self.elo)
        match_queue.find_match(self.db, 'student01')
        self.match_id = match_queue.find_match(self.db, 'student02')['match_id']
        self.job = match_queue.get_job(self.db, self.match_id)

    def import_source(self, identity, source):
        path = self.root / 'bot.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('main.py', source)
        return import_zip(self.store, identity, path)

    def fake_runner(self, job, output):
        initial = job['initial_state']
        game = Game({pos: Piece(**piece) for pos, piece in initial['board'].items()},
                    initial['goals'], set(initial['bombs']), set(initial['obstacles']), job['first'],
                    max_valid_moves=job['limits'].get('max_valid_moves'),
                    move_limit_tiebreak=job['limits'].get('move_limit_tiebreak','draw'))
        rng = random.Random(job['seed'])
        for _ in range(1000):
            if game.result is not None:
                break
            game.play(greedy_bot(game, rng))
        self.assertIsNotNone(game.result)
        record = {'schema_version': 1, 'match_id': job['match_id'], 'seed': job['seed'],
                  'created_at_utc': job['created_at_utc'], 'bots': {'A': 'stored', 'B': 'stored'},
                  'initial_state': initial, 'final_state': state_message(game), 'history': game.history,
                  'map': job['map'], 'limits': job['limits'], 'cleanup_complete': True,
                  'result': {'winner': game.result, 'reason': game.reason, 'valid_move_count': len(game.history)},
                  'bot_sources': {side: {'bot_id': job['players'][side]['bot_id'],
                                         'version': job['players'][side]['version'],
                                         'sha256': job['players'][side]['version'], 'entrypoint': 'main.py'}
                                  for side in ('A', 'B')}}
        write_result(output, record)

    def assert_completed(self):
        job = match_queue.get_job(self.db, self.match_id)
        self.assertEqual(job['state'], 'completed')
        self.assertIsNotNone(job['elo_receipt'])
        self.assertTrue(all(match_queue.status(self.db, identity)['state'] == 'idle'
                            for identity in ('student01', 'student02')))
        self.assertTrue(all(row['played'] == 1 for row in elo_store.standings(self.elo)))
        return job


class LocalWorkerTests(WorkerFixture, unittest.TestCase):
    def test_complete_release_both_and_second_invocation_is_idle(self):
        result = local_worker.run_once(self.db, self.results, self.fake_runner)
        self.assertEqual(result['state'], 'completed')
        self.assertTrue(result['elo_applied'])
        job = self.assert_completed()
        self.assertEqual(job['attempts'], 1)
        before = elo_store.standings(self.elo)
        self.assertEqual(local_worker.run_once(self.db, self.results, self.fake_runner), {'state': 'idle'})
        self.assertEqual(elo_store.standings(self.elo), before)

    def test_system_errors_retry_same_snapshot_only_twice_then_succeed(self):
        attempts = []
        def runner(job, output):
            attempts.append(json.dumps(job, sort_keys=True))
            if len(attempts) < 3:
                raise OSError('transient infrastructure failure')
            self.fake_runner(job, output)
        self.assertEqual(local_worker.run_once(self.db, self.results, runner)['state'], 'completed')
        self.assertEqual(len(set(attempts)), 1)
        self.assertEqual(self.assert_completed()['attempts'], 3)

    def test_three_system_failures_release_both_without_elo(self):
        calls = []
        def runner(job, output):
            calls.append(job['match_id'])
            raise OSError('broken infrastructure')
        self.assertEqual(local_worker.run_once(self.db, self.results, runner)['state'], 'failed')
        self.assertEqual(len(calls), 3)
        self.assertEqual(match_queue.get_job(self.db, self.match_id)['attempts'], 3)
        self.assertTrue(all(match_queue.status(self.db, identity)['state'] == 'idle'
                            for identity in ('student01', 'student02')))
        self.assertFalse(self.elo.exists())

    def test_interruption_without_log_resumes_next_attempt(self):
        with self.assertRaises(KeyboardInterrupt):
            local_worker.run_once(self.db, self.results, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        self.assertEqual(match_queue.get_job(self.db, self.match_id)['attempts'], 1)
        self.assertEqual(local_worker.run_once(self.db, self.results, self.fake_runner)['state'], 'completed')
        self.assertEqual(self.assert_completed()['attempts'], 2)

    def test_interruption_after_log_reuses_it_without_rerunning(self):
        def interrupted(job, output):
            self.fake_runner(job, output)
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            local_worker.run_once(self.db, self.results, interrupted)
        with self.assertRaises(ValueError):
            match_queue.cancel_job(self.db, self.match_id)
        result = local_worker.run_once(self.db, self.results, lambda *_: self.fail('must reuse log'))
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(self.assert_completed()['attempts'], 1)

    def test_failure_after_elo_commit_recovers_without_double_points(self):
        with patch('ottv2.local_worker._finish', side_effect=OSError('queue commit failed')):
            with self.assertRaises(OSError):
                local_worker.run_once(self.db, self.results, self.fake_runner)
        before = elo_store.standings(self.elo)
        self.assertEqual(match_queue.status(self.db, 'student01')['state'], 'matched')
        result = local_worker.run_once(self.db, self.results, lambda *_: self.fail('must reuse log'))
        self.assertFalse(result['elo_applied'])
        self.assertEqual(elo_store.standings(self.elo), before)
        self.assert_completed()

    def test_failed_elo_write_retries_completion_without_rerunning(self):
        with patch('ottv2.elo_store._apply_scored_input', side_effect=OSError('Elo storage failed')):
            with self.assertRaises(OSError):
                local_worker.run_once(self.db, self.results, self.fake_runner)
        self.assertFalse(self.elo.exists())
        self.assertEqual(local_worker.run_once(self.db, self.results, lambda *_: self.fail('must reuse log'))['state'], 'completed')
        self.assert_completed()

    def test_wrong_result_snapshot_cannot_change_elo_or_overwrite_saved_log(self):
        def wrong(job, output):
            self.fake_runner(job, output)
            record = json.loads(output.read_bytes())
            record['seed'] += 1
            output.write_text(json.dumps(record), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'does not match'):
            local_worker.run_once(self.db, self.results, wrong)
        path = self.results / (self.match_id + '.json')
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            local_worker.run_once(self.db, self.results, lambda *_: self.fail('must not overwrite'))
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(self.elo.exists())
        self.assertEqual(match_queue.status(self.db, 'student01')['state'], 'matched')

    def test_occupied_boxes_are_not_reset_and_do_not_consume_an_attempt(self):
        boxes = self.root / 'boxes'
        occupied = boxes / '0'
        occupied.mkdir(parents=True)
        with patch('ottv2.local_worker.BOX_ROOT', boxes):
            with self.assertRaisesRegex(RuntimeError, 'occupied'):
                local_worker.run_once(self.db, self.results, self.fake_runner)
        self.assertTrue(occupied.is_dir())
        self.assertEqual(match_queue.get_job(self.db, self.match_id)['attempts'], 0)
        local_worker.run_once(self.db, self.results, self.fake_runner)
        self.assert_completed()

    def test_default_elo_path_is_bound_once_when_queue_has_none(self):
        with match_queue._write(self.db) as connection:
            row = connection.execute('SELECT data FROM config').fetchone()
            config = json.loads(row['data'])
            config['ratings'] = None
            connection.execute('UPDATE config SET data=?', (json.dumps(config),))
        local_worker.run_once(self.db, self.results, self.fake_runner)
        self.assertFalse(self.elo.exists())
        default = self.root / 'ratings.sqlite3'
        self.assertEqual(len(elo_store.standings(default)), 2)
        with match_queue._write(self.db) as connection:
            self.assertEqual(match_queue._config(connection)['ratings'], str(default))

    def test_v1_upgrade_preserves_job_waiter_and_busy_foreign_keys(self):
        old = self.root / 'v1.sqlite3'
        with closing(sqlite3.connect(self.db)) as current:
            config = current.execute('SELECT data FROM config').fetchone()[0]
            payload = current.execute('SELECT payload FROM jobs').fetchone()[0]
        with closing(sqlite3.connect(old)) as connection, connection:
            connection.execute('CREATE TABLE config (id INTEGER PRIMARY KEY, data TEXT NOT NULL)')
            connection.execute('CREATE TABLE waiting (sequence INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT UNIQUE, joined_at TEXT)')
            connection.execute("CREATE TABLE jobs (match_id TEXT PRIMARY KEY, state TEXT CHECK(state IN ('pending','cancelled')), payload TEXT)")
            connection.execute('CREATE TABLE busy (bot_id TEXT PRIMARY KEY, match_id TEXT REFERENCES jobs(match_id))')
            connection.execute('INSERT INTO config VALUES (1, ?)', (config,))
            connection.execute('INSERT INTO jobs VALUES (?, ?, ?)', (self.match_id, 'pending', payload))
            connection.execute("INSERT INTO waiting(bot_id,joined_at) VALUES ('student03','old request')")
            for identity in ('student01', 'student02'):
                connection.execute('INSERT INTO busy VALUES (?, ?)', (identity, self.match_id))
            connection.execute(f'PRAGMA application_id={match_queue.APPLICATION_ID}')
            connection.execute('PRAGMA user_version=1')
        self.assertEqual(match_queue.get_job(old, self.match_id)['state'], 'pending')
        self.assertEqual(local_worker.run_once(old, self.results, self.fake_runner)['state'], 'completed')
        self.assertEqual(match_queue.status(old, 'student03')['state'], 'waiting')
        with closing(sqlite3.connect(old)) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])


@unittest.skipUnless(sys.platform.startswith('linux'), 'real Worker requires Linux/Isolate')
class LocalWorkerIntegrationTests(WorkerFixture, unittest.TestCase):
    real = True

    def test_real_isolate_uses_pinned_versions_and_saved_map_then_scores_once(self):
        crash = b'''import json,sys
print(json.dumps({'protocol_version':1,'type':'ready'}),flush=True)
for line in sys.stdin:
    raise RuntimeError('new current must not run in old job')
'''
        changed = self.import_source('student01', crash)
        self.map.unlink()
        result = local_worker.run_once(self.db, self.results)
        self.assertEqual(result['state'], 'completed')
        job = self.assert_completed()
        record = json.loads(Path(job['result_path']).read_bytes())
        self.assertEqual(record['match_id'], self.match_id)
        self.assertEqual(record['initial_state'], self.job['initial_state'])
        self.assertEqual(record['map'], self.job['map'])
        for side in ('A', 'B'):
            self.assertEqual(record['bot_sources'][side]['version'], self.job['players'][side]['version'])
        original = next(p for p in self.job['players'].values() if p['bot_id'] == 'student01')
        self.assertNotEqual(original['version'], changed['version'])
        self.assertEqual(load_bot(self.store, 'student01')[1]['version'], changed['version'])
        self.assertNotEqual(record['result']['reason'], 'runtime_error')
        self.assertEqual(local_worker.run_once(self.db, self.results), {'state': 'idle'})
        self.assertTrue(all(not (local_worker.BOX_ROOT / str(box)).exists() for box in (0, 1)))

    def test_global_linux_lock_rejects_second_worker_before_claim(self):
        with local_worker._worker_lock():
            with self.assertRaisesRegex(RuntimeError, 'another local Worker'):
                local_worker.run_once(self.db, self.results)
        self.assertEqual(match_queue.get_job(self.db, self.match_id)['state'], 'pending')
        self.assertEqual(match_queue.get_job(self.db, self.match_id)['attempts'], 0)

    def test_real_bot_fault_is_scored_once_without_system_retry(self):
        crash = b'''import json,sys
print(json.dumps({'protocol_version':1,'type':'ready'}),flush=True)
for line in sys.stdin:
    raise RuntimeError('intentional bot fault')
'''
        match_queue.cancel_job(self.db, self.match_id)
        self.import_source('student01', crash)
        match_queue.find_match(self.db, 'student01')
        self.match_id = match_queue.find_match(self.db, 'student02')['match_id']
        self.assertEqual(local_worker.run_once(self.db, self.results)['state'], 'completed')
        job = self.assert_completed()
        self.assertEqual(job['attempts'], 1)
        self.assertEqual(job['elo_receipt']['reason'], 'runtime_error')
        winner = job['elo_receipt']['winner']
        self.assertEqual(job['players'][winner]['bot_id'], 'student02')
