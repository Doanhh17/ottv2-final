import random
import unittest
from unittest.mock import patch

from ottv2.engine import Game, Piece, small_sample_game as sample_game
from ottv2.protocol import state_message
from ottv2.search_bot import _SearchLimit, allocate_search_time, search_move


def timed_state(cpu=1.5, response=1.0):
    return state_message(sample_game(), {
        "cpu_budget_seconds": 1.5, "cpu_remaining_seconds": {"A": cpu, "B": 1.5},
        "response_limit_seconds": 1.0, "response_remaining_seconds": response,
    })


class SearchBotTests(unittest.TestCase):
    def test_search_allocation_shrinks_with_remaining_cpu_and_wall(self):
        large = allocate_search_time(timed_state()["time"], "A")
        small = allocate_search_time(timed_state(cpu=0.2, response=0.1)["time"], "A")
        self.assertTrue(0 < small[0] < large[0] < 1.5)
        self.assertTrue(0 < small[1] < large[1] < 1)
        self.assertLessEqual(large[0], 0.08)
        self.assertLessEqual(large[1], 0.2)

    def test_low_cpu_or_response_time_keeps_legal_fallback_without_starting_search(self):
        for state in (timed_state(cpu=0.005), timed_state(response=0.02)):
            with self.subTest(time=state["time"]):
                stats = {}
                with patch("ottv2.search_bot._search_depth", side_effect=AssertionError("must keep reserve")):
                    move = search_move(state, random.Random(1), stats=stats)
                sample_game().validate_move(move)
                self.assertEqual((stats["completed_depth"], stats["cutoff"]), (0, "reserve"))

    def test_cpu_deadline_returns_legal_fallback(self):
        ticks = iter((0, 1))
        stats = {}
        move = search_move(timed_state(), random.Random(1), cpu_clock=lambda: next(ticks, 1),
                           wall_clock=lambda: 0, stats=stats)
        sample_game().validate_move(move)
        self.assertEqual((stats["completed_depth"], stats["cutoff"]), (0, "cpu"))

    def test_wall_deadline_returns_legal_fallback_even_with_cpu_left(self):
        ticks = iter((0, 1))
        stats = {}
        move = search_move(timed_state(), random.Random(1), cpu_clock=lambda: 0,
                           wall_clock=lambda: next(ticks, 1), stats=stats)
        sample_game().validate_move(move)
        self.assertEqual((stats["completed_depth"], stats["cutoff"]), (0, "wall"))

    def test_interrupted_deeper_search_keeps_last_completed_depth(self):
        chosen = sample_game().legal_moves()[-1]
        stats = {}
        with patch("ottv2.search_bot._search_depth", side_effect=[(chosen, 0), _SearchLimit("cpu")]):
            move = search_move(timed_state(), random.Random(1), cpu_clock=lambda: 0,
                               wall_clock=lambda: 0, stats=stats)
        self.assertEqual(move, chosen)
        self.assertEqual((stats["completed_depth"], stats["cutoff"]), (1, "cpu"))

    def test_timed_search_finds_an_immediate_capture_win(self):
        original = sample_game()
        board = dict(original.board)
        board["c2"] = board.pop("i9")  # B's last rock can be captured by A's paper.
        game = Game(board, original.goals)
        state = state_message(game, timed_state()["time"])
        stats = {}
        move = search_move(state, random.Random(1), cpu_clock=lambda: 0,
                           wall_clock=lambda: 0, stats=stats)
        game.play(move)
        self.assertEqual((game.result, game.reason), ("A", "missing_kind"))
        self.assertEqual(stats["cutoff"], "winning_line")

    def test_search_returns_a_legal_move(self):
        game = sample_game()
        game.validate_move(search_move(state_message(game), random.Random(1)))

    def test_search_takes_an_immediate_goal_win(self):
        game = sample_game()
        board = dict(game.board)
        board["e4"] = Piece("A", "rock")
        game = Game(board, {"A": "f5", "B": "a1"})
        move = search_move(state_message(game), random.Random(1))
        self.assertEqual(move, {"from": "e4", "to": "f5"})
