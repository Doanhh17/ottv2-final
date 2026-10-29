import json
import random
import subprocess
import sys
import unittest

from ottv2.bot_process import choose_move
from ottv2.communication_demo import ROOT, request_move
from ottv2.engine import IllegalMove, sample_game
from ottv2.protocol import state_message, validate_ready


class CommunicationTests(unittest.TestCase):
    def test_retry_demo_sends_one_illegal_attempt_then_a_legal_move(self):
        game = sample_game()
        message = json.dumps(state_message(game)) + "\n"
        result = subprocess.run(
            [sys.executable, "-m", "ottv2.bot_process", "--strategy", "retry-demo"],
            input=message * 2, capture_output=True, text=True, timeout=5, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        lines = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(lines), 3)
        validate_ready(lines[0])
        with self.assertRaises(IllegalMove):
            game.validate_move(lines[1])
        game.validate_move(lines[2])
        self.assertEqual(len(game.history), 0)

    def test_both_process_strategies_return_legal_move(self):
        game = sample_game()
        for strategy in ("random", "greedy"):
            game.play(request_move(game, strategy))
        self.assertEqual(len(game.history), 2)

    def test_bot_stays_alive_for_multiple_messages(self):
        game = sample_game()
        message = json.dumps(state_message(game)) + "\n"
        result = subprocess.run(
            [sys.executable, "-m", "ottv2.bot_process", "--strategy", "random"],
            input=message * 2, capture_output=True, text=True, timeout=5, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 3)
        validate_ready(json.loads(lines[0]))
        for line in lines[1:]:
            game.validate_move(json.loads(line))

    def test_invalid_input_goes_to_stderr(self):
        result = subprocess.run(
            [sys.executable, "-m", "ottv2.bot_process", "--strategy", "greedy"],
            input="not-json\n", capture_output=True, text=True, timeout=5, cwd=ROOT)
        self.assertNotEqual(result.returncode, 0)
        validate_ready(json.loads(result.stdout))
        self.assertIn("BOT INPUT ERROR", result.stderr)

    def test_client_candidates_match_engine_across_changing_positions(self):
        game = sample_game()
        rng = random.Random(3)
        for _ in range(150):
            if game.result is not None or not game.legal_moves():
                break
            message = state_message(game)
            for strategy in ("random", "greedy"):
                game.validate_move(choose_move(message, strategy, rng))
            game.play(rng.choice(game.legal_moves()))


if __name__ == "__main__":
    unittest.main()
