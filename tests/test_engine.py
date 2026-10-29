import random
import unittest

from ottv2.bots import greedy_bot, random_bot
from ottv2.engine import BEATS, FormatError, Game, IllegalMove, Piece, sample_game


def fixture(extra=None, bombs=(), obstacles=(), goals=None):
    board = {"a1": Piece("A", "rock"), "b1": Piece("A", "paper"),
             "c1": Piece("A", "scissors"), "i9": Piece("B", "rock"),
             "h9": Piece("B", "paper"), "g9": Piece("B", "scissors")}
    board.update(extra or {})
    return Game(board, goals or {"A": "i9", "B": "a1"}, set(bombs), set(obstacles))


class RulesTests(unittest.TestCase):
    def test_eight_directions(self):
        game = fixture({"e4": Piece("A", "rock")})
        destinations = {m["to"] for m in game.legal_moves() if m["from"] == "e4"}
        self.assertEqual(destinations, {"d3", "e3", "f3", "d4", "f4", "d5", "e5", "f5"})

    def test_capture_matrix(self):
        for attacker in BEATS:
            for target in BEATS:
                with self.subTest(attacker=attacker, target=target):
                    game = fixture({"e4": Piece("A", attacker), "f5": Piece("B", target)})
                    if BEATS[attacker] == target:
                        event = game.play({"from": "e4", "to": "f5"})
                        self.assertEqual(event["capture"], target)
                        self.assertEqual(game.board["f5"], Piece("A", attacker))
                    else:
                        with self.assertRaises(IllegalMove):
                            game.play({"from": "e4", "to": "f5"})

    def test_illegal_move_changes_nothing(self):
        game = fixture(obstacles={"a2"})
        for move in ({"from": "a1", "to": "b1"}, {"from": "a1", "to": "a2"},
                     {"from": "a1", "to": "a3"}, {"from": "i9", "to": "i8"},
                     {"from": "a1", "to": "a1"}):
            before = (dict(game.board), game.position_key(), dict(game._seen))
            with self.assertRaises(IllegalMove):
                game.play(move)
            self.assertEqual(before, (dict(game.board), game.position_key(), dict(game._seen)))
            self.assertEqual(game.history, [])

    def test_format_is_separate_from_illegal_move(self):
        for move in (None, {}, {"from": 1, "to": "b2"}, {"from": "j1", "to": "b2"},
                     {"from": "a1", "to": "b2", "extra": 0}):
            with self.subTest(move=move), self.assertRaises(FormatError):
                fixture().play(move)

    def test_bomb_removes_only_stepping_piece_then_square_is_free(self):
        game = fixture({"b2": Piece("A", "rock")}, bombs={"a2"})
        game.play({"from": "a1", "to": "a2"})
        self.assertNotIn("a2", game.bombs)
        self.assertNotIn("a2", game.board)
        self.assertIn("b2", game.board)
        self.assertIsNone(game.result)
        game.play({"from": "i9", "to": "i8"})
        game.play({"from": "b2", "to": "a2"})
        self.assertEqual(game.board["a2"], Piece("A", "rock"))

    def test_losing_last_kind_to_bomb_is_loss(self):
        game = fixture(bombs={"a2"})
        game.play({"from": "a1", "to": "a2"})
        self.assertEqual((game.result, game.reason), ("B", "missing_kind"))

    def test_capturing_last_kind_is_win(self):
        game = fixture({"g8": Piece("A", "rock")})
        game.play({"from": "g8", "to": "g9"})
        self.assertEqual((game.result, game.reason), ("A", "missing_kind"))

    def test_any_piece_can_win_on_own_goal(self):
        for kind in BEATS:
            game = fixture({"e4": Piece("A", kind)}, goals={"A": "f5", "B": "a1"})
            game.play({"from": "e4", "to": "f5"})
            self.assertEqual((game.result, game.reason), ("A", "goal"))
            with self.assertRaises(IllegalMove):
                game.play({"from": "i9", "to": "i8"})

    def test_threefold_counts_initial_state_and_turn(self):
        game = fixture()
        cycle = [("a1", "a2"), ("i9", "i8"), ("a2", "a1"), ("i8", "i9")]
        for start, end in cycle:
            game.play({"from": start, "to": end})
        self.assertIsNone(game.result)
        for start, end in cycle:
            game.play({"from": start, "to": end})
        self.assertEqual((game.result, game.reason), ("draw", "threefold_repetition"))

    def test_no_legal_move_does_not_automatically_lose(self):
        game = fixture(obstacles={"a2", "b2", "c2", "d1", "d2"})
        self.assertEqual(game.legal_moves(), [])
        self.assertIsNone(game.result)
        self.assertIsNone(random_bot(game, random.Random(1)))

    def test_invalid_maps(self):
        with self.assertRaises(ValueError):
            fixture(bombs={"i9"})
        with self.assertRaises(ValueError):
            fixture(obstacles={"a1"})
        with self.assertRaises(ValueError):
            fixture({"a1": Piece("A", "paper")})

    def test_sample_bots_produce_legal_games(self):
        for seed in range(10):
            rng = random.Random(seed)
            game = sample_game(rng.choice(("A", "B")))
            for _ in range(1000):
                bot = random_bot if game.turn == "A" else greedy_bot
                move = bot(game, rng)
                if move is None or game.result is not None:
                    break
                game.play(move)
                self.assertFalse(set(game.board) & game.obstacles)
                self.assertFalse(set(game.board) & game.bombs)


if __name__ == "__main__":
    unittest.main()
