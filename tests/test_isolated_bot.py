from pathlib import Path
import sys
import unittest

from ottv2.isolated_bot_demo import BOX, run_demo
from ottv2.engine import sample_game


@unittest.skipUnless(sys.platform.startswith("linux") and Path("/usr/local/bin/isolate").is_file(),
                     "requires the configured Ubuntu Isolate environment")
class IsolatedBotTests(unittest.TestCase):
    def test_ready_then_legal_move_and_cleanup(self):
        game = run_demo(emit=lambda _: None)
        self.assertEqual(len(game.history), 1)
        event = game.history[0]
        sample_game().validate_move({"from": event["from"], "to": event["to"]})
        self.assertEqual(len(game.board), 18)
        self.assertEqual(game.turn, "B")
        self.assertFalse(BOX.exists())

    def test_cleanup_if_controller_fails_after_receiving_move(self):
        def emit(line):
            if line.startswith("RECEIVE:"):
                raise RuntimeError("test controller failure")

        with self.assertRaisesRegex(RuntimeError, "test controller failure"):
            run_demo(emit=emit)
        self.assertFalse(BOX.exists())


if __name__ == "__main__":
    unittest.main()
