from collections import Counter
import unittest

from ottv2.engine import KINDS, Piece, sample_game


class NinePieceMapTests(unittest.TestCase):
    def test_each_side_starts_with_three_of_every_kind(self):
        for first in ("A", "B"):
            game = sample_game(first)
            self.assertEqual(game.turn, first)
            self.assertEqual(len(game.board), 18)
            for side in ("A", "B"):
                self.assertEqual(Counter(p.kind for p in game.board.values() if p.side == side),
                                 Counter({kind: 3 for kind in KINDS}))
            self.assertEqual(len(game.legal_moves()), 25)

    def test_setup_is_symmetric_under_half_turn_and_side_swap(self):
        game = sample_game()
        for square, piece in game.board.items():
            rotated = chr(ord("i") - (ord(square[0]) - ord("a"))) + str(10 - int(square[1]))
            self.assertEqual(game.board[rotated], Piece("B" if piece.side == "A" else "A", piece.kind))

    def test_losing_one_of_three_rocks_does_not_lose_a_kind(self):
        game = sample_game()
        game.bombs.add("a2")
        game.play({"from": "a1", "to": "a2"})
        self.assertIsNone(game.result)
        self.assertEqual(sum(p.side == "A" and p.kind == "rock" for p in game.board.values()), 2)


if __name__ == "__main__":
    unittest.main()
