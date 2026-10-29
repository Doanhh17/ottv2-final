from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
from pathlib import Path
import random
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch
import zipfile

from ottv2 import elo_store
from ottv2.bot_store import import_zip, load_bot, load_version
from ottv2.match_queue import initialize, find_match, cancel_request, status, get_job, cancel_job


class MatchQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db, self.store = self.root / 'queue.sqlite3', self.root / 'bots'
        self.ratings = self.root / 'ratings.sqlite3'
        self.map = self.root / 'map.json'
        self.map.write_bytes((Path(__file__).resolve().parent.parent / 'maps/nine-piece.json').read_bytes())
        ready = patch('ottv2.bot_store.check_ready', side_effect=lambda source:
                      {'ready': True, 'source_sha256': hashlib.sha256(source).hexdigest()})
        ready.start()
        self.addCleanup(ready.stop)
        for number in range(1, 5):
            self.import_source('student0' + str(number), b'pass\n')
        initialize(self.db, self.store, self.map, self.ratings)

    def import_source(self, bot_id, source):
        archive = self.root / 'bot.zip'
        with zipfile.ZipFile(archive, 'w') as stream:
            stream.writestr('main.py', source)
        return import_zip(self.store, bot_id, archive)

    def seed_waiting(self, identities):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            for identity in identities:
                connection.execute('INSERT INTO waiting(bot_id, joined_at) VALUES (?, ?)',
                                   (identity, '2026-10-23T00:00:00+00:00'))

    def seed_ratings(self, ratings):
        with closing(elo_store._connect(self.ratings)) as connection:
            connection.execute('BEGIN IMMEDIATE')
            elo_store._check_schema(connection, create=True)
            for identity, rating in ratings.items():
                connection.execute('INSERT INTO ratings(bot_id, rating) VALUES (?, ?)', (identity, rating))
            connection.execute('COMMIT')

    def test_wait_cancel_and_duplicate_do_not_reset_position(self):
        first = find_match(self.db, 'student01')
        self.assertEqual(first['state'], 'waiting')
        self.assertEqual(find_match(self.db, 'student01'), {**first, 'created': False})
        self.assertEqual(status(self.db, 'student01')['joined_at_utc'], first['joined_at_utc'])
        self.assertTrue(cancel_request(self.db, 'student01')['cancelled'])
        self.assertFalse(cancel_request(self.db, 'student01')['cancelled'])
        self.assertEqual(status(self.db, 'student01')['state'], 'idle')
        self.assertFalse(self.ratings.exists())

    def test_pairing_pins_id_versions_board_first_and_limits(self):
        find_match(self.db, 'student01')
        paired = find_match(self.db, 'student02')
        self.assertEqual(paired['state'], 'matched')
        job = get_job(self.db, paired['match_id'])
        self.assertEqual(job['state'], 'pending')
        self.assertEqual({p['bot_id'] for p in job['players'].values()}, {'student01', 'student02'})
        self.assertEqual(len(job['initial_state']['board']), 18)
        self.assertEqual(job['first'], random.Random(job['seed']).choice(('A', 'B')))
        self.assertEqual(job['initial_state']['turn'], job['first'])
        self.assertEqual(job['limits'], {'cpu_budget_seconds': 7, 'response_limit_seconds': 2.5,
                                         'memory_limit_mib_per_bot': 64, 'max_valid_moves': 50, 'move_limit_tiebreak': 'piece_count'})
        for player in job['players'].values():
            source = load_version(self.store, player['bot_id'], player['version'])
            self.assertEqual(hashlib.sha256(source).hexdigest(), player['version'])
        self.assertEqual(status(self.db, 'student01')['match_id'], paired['match_id'])

    def test_matched_find_is_idempotent_and_student_cannot_cancel_job(self):
        find_match(self.db, 'student01')
        paired = find_match(self.db, 'student02')
        self.assertEqual(find_match(self.db, 'student01')['match_id'], paired['match_id'])
        self.assertFalse(find_match(self.db, 'student02')['created'])
        with self.assertRaises(ValueError):
            cancel_request(self.db, 'student01')
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 1)

    def test_current_update_before_pairing_is_used_but_after_pairing_is_not(self):
        find_match(self.db, 'student01')
        updated = self.import_source('student01', b'# second\npass\n')
        paired = find_match(self.db, 'student02')
        job = get_job(self.db, paired['match_id'])
        player = next(p for p in job['players'].values() if p['bot_id'] == 'student01')
        self.assertEqual(player['version'], updated['version'])
        self.import_source('student01', b'# third\npass\n')
        self.map.unlink()
        self.assertEqual(get_job(self.db, paired['match_id']), job)
        self.assertEqual(load_version(self.store, 'student01', player['version']), b'# second\npass\n')
        self.assertNotEqual(load_bot(self.store, 'student01')[1]['version'], player['version'])

    def test_snapshot_map_does_not_need_original_file_after_initialize(self):
        self.map.unlink()
        find_match(self.db, 'student01')
        paired = find_match(self.db, 'student02')
        self.assertEqual(len(get_job(self.db, paired['match_id'])['initial_state']['board']), 18)

    def test_closest_elo_band_precedes_wider_candidates(self):
        self.seed_ratings({'student01': 1000, 'student02': 1090, 'student03': 1200, 'student04': 1500})
        self.seed_waiting(['student02', 'student03', 'student04'])
        paired = find_match(self.db, 'student01')
        players = get_job(self.db, paired['match_id'])['players'].values()
        self.assertEqual({p['bot_id'] for p in players}, {'student01', 'student02'})
        self.assertEqual({p['rating_at_pairing'] for p in players}, {1000, 1090})
        self.assertEqual(status(self.db, 'student03')['state'], 'waiting')

    def test_widening_reaches_two_hundred_four_hundred_and_unbounded(self):
        for rating in (1150, 1350, 1800):
            with self.subTest(rating=rating):
                if self.ratings.exists():
                    self.ratings.unlink()
                self.seed_ratings({'student01': 1000, 'student02': rating})
                find_match(self.db, 'student01')
                paired = find_match(self.db, 'student02')
                self.assertEqual(paired['state'], 'matched')
                cancel_job(self.db, paired['match_id'])

    def test_missing_unvalidated_or_bad_identity_does_not_queue(self):
        for identity in ('missing01', '../bad', 'A'):
            with self.assertRaises(ValueError):
                find_match(self.db, identity)
        current = self.store / 'student01/current.json'
        metadata = json.loads(current.read_bytes())
        metadata.pop('validation')
        current.write_text(json.dumps(metadata), encoding='utf-8')
        with self.assertRaises(ValueError):
            find_match(self.db, 'student01')
        self.assertEqual(status(self.db, 'student01')['state'], 'idle')

    def test_invalid_waiting_bot_is_skipped_without_losing_request(self):
        self.seed_waiting(['student02', 'student03'])
        current = self.store / 'student02/current.json'
        current.unlink()
        paired = find_match(self.db, 'student01')
        self.assertEqual({p['bot_id'] for p in get_job(self.db, paired['match_id'])['players'].values()},
                         {'student01', 'student03'})
        self.assertEqual(status(self.db, 'student02')['state'], 'waiting')

    def test_concurrent_duplicate_find_creates_one_request(self):
        barrier = Barrier(6)
        def find(_):
            barrier.wait(timeout=5)
            return find_match(self.db, 'student01')
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(find, range(6)))
        self.assertEqual(sum(result['created'] for result in results), 1)
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM waiting').fetchone()[0], 1)

    def test_concurrent_unique_find_never_assigns_a_player_twice(self):
        identities = ['student0' + str(index) for index in range(1, 5)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda identity: find_match(self.db, identity), identities))
        states = [status(self.db, identity) for identity in identities]
        self.assertTrue(all(state['state'] == 'matched' for state in states))
        self.assertEqual(len({state['match_id'] for state in states}), 2)

    def test_second_busy_insert_failure_rolls_back_entire_pairing(self):
        find_match(self.db, 'student01')
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("""CREATE TRIGGER fail_second BEFORE INSERT ON busy
                WHEN (SELECT COUNT(*) FROM busy) > 0
                BEGIN SELECT RAISE(ABORT, 'storage failure'); END""")
        with self.assertRaisesRegex(sqlite3.Error, 'storage failure'):
            find_match(self.db, 'student02')
        self.assertEqual(status(self.db, 'student01')['state'], 'waiting')
        self.assertEqual(status(self.db, 'student02')['state'], 'idle')
        with closing(sqlite3.connect(self.db)) as connection, connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)
            connection.execute('DROP TRIGGER fail_second')
        self.assertEqual(find_match(self.db, 'student02')['state'], 'matched')

    def test_operator_cancellation_is_idempotent_and_releases_both(self):
        find_match(self.db, 'student01')
        paired = find_match(self.db, 'student02')
        self.assertTrue(cancel_job(self.db, paired['match_id'])['changed'])
        self.assertFalse(cancel_job(self.db, paired['match_id'])['changed'])
        self.assertEqual(get_job(self.db, paired['match_id'])['state'], 'cancelled')
        for identity in ('student01', 'student02'):
            self.assertEqual(status(self.db, identity)['state'], 'idle')
        self.assertFalse(self.ratings.exists())

    def test_reinitialization_read_only_status_and_unrelated_database_are_safe(self):
        find_match(self.db, 'student01')
        self.assertFalse(initialize(self.db, self.store, self.map, self.ratings)['initialized'])
        before = self.db.read_bytes()
        status(self.db, 'student01')
        self.assertEqual(self.db.read_bytes(), before)
        changed = json.loads(self.map.read_bytes())
        changed['name'] = 'different-map'
        self.map.write_text(json.dumps(changed), encoding='utf-8')
        with self.assertRaises(ValueError):
            initialize(self.db, self.store, self.map, self.ratings)
        self.assertEqual(status(self.db, 'student01')['state'], 'waiting')
        other = self.root / 'other.sqlite3'
        with closing(sqlite3.connect(other)) as connection, connection:
            connection.execute('CREATE TABLE keep(value TEXT)')
        previous = other.read_bytes()
        with self.assertRaises(ValueError):
            initialize(other, self.store, self.map)
        self.assertEqual(other.read_bytes(), previous)
        missing = self.root / 'missing' / 'queue.sqlite3'
        self.assertEqual(status(missing, 'student01')['state'], 'idle')
        self.assertFalse(missing.parent.exists())

    def test_pinned_version_reader_rejects_traversal_and_corruption(self):
        metadata = load_bot(self.store, 'student01')[1]
        with self.assertRaises(ValueError):
            load_version(self.store, 'student01', '../current')
        (self.store / 'student01/versions' / (metadata['version'] + '.py')).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            load_version(self.store, 'student01', metadata['version'])
