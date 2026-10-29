from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from ottv2.elo_store import apply_result, next_ratings, standings
from ottv2.engine import small_sample_game
from ottv2.protocol import state_message


class EloStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'elo.sqlite3'

    def log(self, number=1, winner='A', reason='goal'):
        game = small_sample_game()
        initial = state_message(game)
        if winner == 'draw':
            cycle = [('a1', 'a2'), ('i9', 'i8'), ('a2', 'a1'), ('i8', 'i9')] * 2
            for start, end in cycle:
                game.play({'from': start, 'to': end})
            self.assertEqual(game.result, 'draw')
            reason = game.reason
        record = {'schema_version': 1, 'match_id': f'{number:032x}',
                  'created_at_utc': '2026-10-23T00:00:00+00:00',
                  'initial_state': initial, 'final_state': state_message(game),
                  'history': game.history, 'bots': {'A': 'stored', 'B': 'stored'},
                  'result': {'winner': winner, 'reason': reason, 'valid_move_count': len(game.history)},
                  'cleanup_complete': True,
                  'bot_sources': {side: {'bot_id': 'student0' + str(index),
                                         'version': str(index) * 64, 'sha256': str(index) * 64,
                                         'entrypoint': 'main.py'}
                                  for side, index in (('A', 1), ('B', 2))}}
        path = self.root / (record['match_id'] + '.json')
        path.write_text(json.dumps(record), encoding='utf-8')
        return path

    def change(self, path, callback):
        record = json.loads(path.read_bytes())
        callback(record)
        path.write_text(json.dumps(record), encoding='utf-8')

    def test_equal_ratings_win_loss_and_draw(self):
        self.assertEqual(next_ratings(1000, 1000, 'A'), (1016, 984))
        self.assertEqual(next_ratings(1000, 1000, 'B'), (984, 1016))
        self.assertEqual(next_ratings(1000, 1000, 'draw'), (1000, 1000))

    def test_unequal_ratings_draw_conservation_and_extreme_gap(self):
        a, b = next_ratings(1000, 1400, 'draw')
        self.assertAlmostEqual(a - 1000, 32 * (0.5 - 1/11))
        self.assertAlmostEqual(a + b, 2400)
        for winner in ('A', 'B', 'draw'):
            a, b = next_ratings(-1000000, 1000000, winner)
            self.assertTrue(math.isfinite(a) and math.isfinite(b))
            self.assertAlmostEqual(a + b, 0)

    def test_invalid_numeric_values_and_results(self):
        for value in (True, '1000', None, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                next_ratings(value, 1000, 'A')
        with self.assertRaises(ValueError):
            next_ratings(1000, 1000, None)

    def test_first_score_is_durable_and_updates_both_sides(self):
        receipt = apply_result(self.db, self.log())
        self.assertTrue(receipt['applied'])
        self.assertEqual(receipt['players']['A']['after'], 1016)
        self.assertEqual(receipt['players']['B']['after'], 984)
        rows = standings(self.db)
        self.assertEqual([row['rating'] for row in rows], [1016, 984])
        self.assertEqual((rows[0]['wins'], rows[1]['losses']), (1, 1))

    def test_show_missing_database_does_not_create_it(self):
        path = self.root / 'missing' / 'elo.sqlite3'
        self.assertEqual(standings(path), [])
        self.assertFalse(path.parent.exists())
        for limit in (True, 0, 4001):
            with self.assertRaises(ValueError):
                standings(path, limit)

    def test_duplicate_reformatted_json_does_not_add_rating_or_game(self):
        path = self.log()
        first = apply_result(self.db, path)
        path.write_text(json.dumps(json.loads(path.read_bytes()), indent=2, sort_keys=True), encoding='utf-8')
        again = apply_result(self.db, path)
        self.assertFalse(again['applied'])
        self.assertEqual({**first, 'applied': False}, again)
        self.assertTrue(all(row['played'] == 1 for row in standings(self.db)))

    def test_same_match_id_with_different_content_is_rejected(self):
        path = self.log()
        apply_result(self.db, path)
        before = standings(self.db)
        self.change(path, lambda record: record['result'].update(winner='B'))
        with self.assertRaisesRegex(ValueError, 'different data'):
            apply_result(self.db, path)
        self.assertEqual(standings(self.db), before)

    def test_rating_survives_version_change_and_duplicate_returns_original_receipt(self):
        first_path = self.log()
        first = apply_result(self.db, first_path)
        second_path = self.log(2, 'B')
        self.change(second_path, lambda record: record['bot_sources']['A'].update(version='a'*64, sha256='a'*64))
        second = apply_result(self.db, second_path)
        self.assertEqual(second['players']['A']['before'], 1016)
        self.assertNotEqual(second['players']['A']['after'], 1016)
        self.assertEqual(apply_result(self.db, first_path), {**first, 'applied': False})
        self.assertTrue(all(row['played'] == 2 for row in standings(self.db)))

    def test_actual_engine_draw_log_is_scoreable_and_counted_for_both(self):
        receipt = apply_result(self.db, self.log(winner='draw'))
        self.assertEqual(receipt['winner'], 'draw')
        for row in standings(self.db):
            self.assertEqual((row['rating'], row['played'], row['draws']), (1000, 1, 1))

    def test_bot_fault_is_scoreable_but_system_or_incomplete_results_are_not(self):
        receipt = apply_result(self.db, self.log(winner='B', reason='response_timeout'))
        self.assertEqual(receipt['players']['B']['after'], 1016)
        before = standings(self.db)
        for index, (winner, reason) in enumerate(((None, 'system_error'), ('A', 'system_error'),
                                                  ('draw', 'goal'), ('A', 'threefold_repetition')), 2):
            path = self.log(index, winner, reason)
            # log() constructs a legal repetition for draw; override its reason here.
            self.change(path, lambda record, value=reason: record['result'].update(reason=value))
            with self.assertRaises(ValueError):
                apply_result(self.db, path)
        self.assertEqual(standings(self.db), before)

    def test_unpinned_self_play_invalid_identity_or_cleanup_rejected_before_database_creation(self):
        changes = [lambda r: r.update(cleanup_complete=False),
                   lambda r: r['bots'].update(B='file'),
                   lambda r: r['bot_sources']['B'].update(bot_id='student01'),
                   lambda r: r['bot_sources']['B'].update(bot_id='../bad'),
                   lambda r: r['bot_sources']['B'].update(sha256='f'*64)]
        for change in changes:
            path = self.log()
            self.change(path, change)
            with self.assertRaises(ValueError):
                apply_result(self.db, path)
            self.assertFalse(self.db.exists())
        with self.assertRaises(ValueError):
            apply_result(self.db, path.with_suffix('.txt'))
        self.assertFalse(self.db.exists())

    def test_concurrent_duplicate_results_apply_once(self):
        path = self.log()
        barrier = Barrier(6)
        def apply(_):
            barrier.wait(timeout=5)
            return apply_result(self.db, path)
        with ThreadPoolExecutor(max_workers=6) as pool:
            receipts = list(pool.map(apply, range(6)))
        self.assertEqual(sum(receipt['applied'] for receipt in receipts), 1)
        self.assertTrue(all(row['played'] == 1 for row in standings(self.db)))

    def test_concurrent_distinct_matches_do_not_lose_updates(self):
        paths = [self.log(number) for number in range(1, 7)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda path: apply_result(self.db, path), paths))
        rows = standings(self.db)
        self.assertTrue(all(row['played'] == 6 for row in rows))
        self.assertAlmostEqual(sum(row['rating'] for row in rows), 2000)

    def test_failure_updating_second_player_rolls_back_first_and_receipt(self):
        apply_result(self.db, self.log())
        before = standings(self.db)
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("""CREATE TRIGGER fail_second BEFORE UPDATE ON ratings
                WHEN NEW.bot_id = 'student02' BEGIN SELECT RAISE(ABORT, 'storage failure'); END""")
        with self.assertRaisesRegex(sqlite3.Error, 'storage failure'):
            apply_result(self.db, self.log(2))
        self.assertEqual(standings(self.db), before)
        with closing(sqlite3.connect(self.db)) as connection, connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM scored_matches').fetchone()[0], 1)
            connection.execute('DROP TRIGGER fail_second')
        self.assertTrue(apply_result(self.db, self.log(2))['applied'])

    def test_unrelated_database_is_not_modified(self):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute('CREATE TABLE unrelated(value TEXT)')
            connection.execute("INSERT INTO unrelated VALUES ('keep')")
        before = self.db.read_bytes()
        with self.assertRaises(ValueError):
            apply_result(self.db, self.log())
        with self.assertRaises(ValueError):
            standings(self.db)
        self.assertEqual(self.db.read_bytes(), before)

    def test_linked_database_is_rejected(self):
        if os.name == 'nt':
            self.skipTest('symlink privilege unavailable on Windows; checked on Linux')
        target = self.root / 'target.sqlite3'
        target.touch()
        self.db.symlink_to(target)
        with self.assertRaises(ValueError):
            apply_result(self.db, self.log())
        self.assertEqual(target.read_bytes(), b'')
