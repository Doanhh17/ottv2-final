"""Organizer samples speak the real protocol and produce legal moves."""

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from scripts.build_measurement_bots import build, VARIANTS
from ottv2.engine import sample_game
from ottv2.protocol import state_message, validate_ready


class MeasurementBotTests(unittest.TestCase):
    def test_standalone_protocol_legal_moves_and_zero_time_fallback(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'bots'
            build(directory)
            for name in VARIANTS:
                with self.subTest(name=name):
                    game = sample_game()
                    state = state_message(game, {
                        'cpu_budget_seconds': 5, 'cpu_remaining_seconds': {'A': 5, 'B': 5},
                        'response_limit_seconds': 1, 'response_remaining_seconds': 1})
                    if name == 'depth1_timed':
                        state['time']['response_remaining_seconds'] = 0
                    result = subprocess.run([sys.executable, '-I', str(directory/name/'main.py')],
                                            input=json.dumps(state)+'\n', text=True,
                                            capture_output=True, timeout=10, check=True)
                    lines = result.stdout.splitlines()
                    self.assertEqual(len(lines), 2)
                    validate_ready(json.loads(lines[0]))
                    game.validate_move(json.loads(lines[1]))
                    self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
