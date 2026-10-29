from collections import Counter
import random
import unittest

from ottv2.advanced_bot import Position, Search, advanced_move, square
from ottv2.engine import Game, sample_game, small_sample_game
from ottv2.protocol import state_message


def timing(game, cpu=5):
    return state_message(game, {"cpu_budget_seconds": 5, "cpu_remaining_seconds": {"A": cpu, "B": cpu},
                               "response_limit_seconds": 1, "response_remaining_seconds": 1})


class AdvancedBotTests(unittest.TestCase):
    def test_compact_rules_and_undo_match_engine_across_random_games(self):
        for seed in range(12):
            rng = random.Random(seed)
            game = sample_game("A" if seed % 2 else "B")
            for _ in range(100):
                p = Position(state_message(game))
                moves = p.moves()
                self.assertEqual({(square(a), square(b)) for a, b in moves},
                                 {(m["from"], m["to"]) for m in game.legal_moves()})
                if not moves or game.result:
                    break
                move = rng.choice(moves)
                key, counts = p.key(), list(p.counts)
                undo, winner = p.push(move)
                expected_key = p.key()
                expected_counts = list(p.counts)
                p.pop(move, undo)
                self.assertEqual((p.key(), p.counts), (key, counts))
                game.play({"from": square(move[0]), "to": square(move[1])})
                self.assertEqual(winner, None if game.result in (None, "draw") else (0 if game.result == "A" else 1))
                if not game.result:
                    actual = Position(state_message(game))
                    self.assertEqual((expected_key, expected_counts), (actual.key(), actual.counts))

    def test_bomb_removes_last_kind_and_undo_restores_counts(self):
        game = small_sample_game()
        game.bombs.add("a2")
        p = Position(state_message(game))
        before = p.key(), list(p.counts)
        undo, winner = p.push((0, 9))
        self.assertEqual(winner, 1)
        self.assertEqual(p.board[9], 0)
        p.pop((0, 9), undo)
        self.assertEqual((p.key(), p.counts), before)

    def test_both_styles_take_immediate_capture_win(self):
        base = small_sample_game()
        board = dict(base.board)
        board["c2"] = board.pop("i9")
        for style in ("alpha", "guardian"):
            game = Game(board, base.goals)
            stats = {}
            move = advanced_move(timing(game), random.Random(1), style=style, stats=stats)
            game.play(move)
            self.assertEqual((game.result, game.reason), ("A", "missing_kind"))
            self.assertGreater(stats["completed_depth"], 0)

    def test_low_budget_returns_legal_fallback_and_state_is_unchanged(self):
        game = sample_game()
        for style in ("alpha", "guardian"):
            state = timing(game, 0.001)
            original = repr(state)
            stats = {}
            move = advanced_move(state, random.Random(1), style=style, stats=stats)
            game.validate_move(move)
            self.assertEqual(stats["completed_depth"], 0)
            self.assertEqual(repr(state), original)

    def test_deadline_unwinds_board_and_path(self):
        p = Position(state_message(sample_game()))
        ticks = iter((0, 0, 0, 2))
        s = Search(p, "alpha", 1, 1, lambda: next(ticks, 2), lambda: 0)
        before = p.key(), list(p.counts)
        with self.assertRaises(TimeoutError):
            s.negamax(4, -30001, 30001)
        self.assertEqual((p.key(), p.counts), before)
        self.assertEqual(s.path, Counter({p.key(): 1}))

    def test_both_styles_stop_opponent_goal_threat(self):
        base = sample_game()
        board = dict(base.board)
        board["b2"] = board.pop("h9")  # B paper threatens a1, occupied by A rock.
        game = Game(board, base.goals, base.bombs, base.obstacles)
        for style in ("alpha", "guardian"):
            search = Search(Position(state_message(game)), style, 1, 1, lambda: 0, lambda: 0)
            _, move = search.negamax(2, -30001, 30001)
            self.assertEqual(tuple(map(square, move)), ("c1", "b2"))


if __name__ == "__main__":
    unittest.main()
